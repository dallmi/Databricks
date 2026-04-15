"""
Flatten AppInsights pageViews export, join with HR snapshots, and produce
a CDM-based star schema.

Reads CSV or XLSX files exported from Azure AppInsights, flattens the nested
customDimensions JSON column, converts timestamps from UTC to CET, joins with
HR snapshot data (hr_history.parquet) using temporal GPN matching, computes
time-on-page and session-level engagement metrics, and persists everything in
a DuckDB database with SHA-256-based delta loading and upsert semantics.

Usage:
    # Process all files in input/ (default, no args needed)
    python scripts/flatten_appinsights.py

    # Process a specific file or directory
    python scripts/flatten_appinsights.py input/pageviews_march.csv
    python scripts/flatten_appinsights.py ./data/

    # Force reprocess everything
    python scripts/flatten_appinsights.py --full-refresh

    # Custom HR path
    python scripts/flatten_appinsights.py --hr path/to/hr_history.parquet

Output (written to <input_dir>/output/ by default, override with -o):
    pageviews.duckdb          -- persistent DuckDB with all tables + manifest
    fact_page_view.parquet    -- one row per page view
    agg_session.parquet       -- one row per session
    dim_page.parquet          -- page dimension (deduplicated)
    dim_date.parquet          -- date dimension
    pageviews_cdm.xlsx        -- all sheets in one Excel file for review

Delta loading:
    Each input file is SHA-256 hashed. On re-run, only new or changed files
    are processed. When a file changes (same name, different hash), its old
    rows are replaced with the new data (upsert via source_file tracking).
    Use --full-refresh to rebuild from scratch.
"""

import argparse
import hashlib
import json
import os
import sys
from pathlib import Path

import duckdb
import pandas as pd


# -- Column mappings: AppInsights raw -> CDM-inspired names --

FACT_COLUMNS = {
    "id": "view_id",
    "timestamp [UTC]": "timestamp_utc",
    "user_Id": "user_id",
    "session_Id": "session_id",
    "duration": "page_load_ms",
    "cp_refUri": "referrer_url",
    "client_OS": "client_os",
    "client_Browser": "client_browser",
    "client_CountryOrRegion": "client_country",
    "cp_PageId": "page_id",
    "cp_Email": "email",
    "cp_GPN": "gpn",
}

DIM_PAGE_COLUMNS = {
    "cp_PageId": "page_id",
    "cp_PageName": "page_name",
    "cp_PageURL": "page_url",
    "cp_SiteID": "site_id",
    "cp_SiteName": "site_name",
    "cp_ContentOwner": "content_owner",
    "cp_ContentType": "content_type",
    "cp_Theme": "theme",
    "cp_Topic": "topic",
    "cp_TargetRegion": "target_region",
    "cp_TargetOrganisation": "target_org",
    "cp_PageStatus": "page_status",
    "cp_PublishingDate": "publishing_date",
}

HR_FIELD_MAP = {
    "gcrs_division_desc": "hr_division",
    "gcrs_unit_desc": "hr_unit",
    "gcrs_area_desc": "hr_area",
    "gcrs_sector_desc": "hr_sector",
    "gcrs_segment_desc": "hr_segment",
    "work_location_country": "hr_country",
    "work_location_region": "hr_region",
    "job_title": "hr_job_title",
    "management_level": "hr_management_level",
}

DROP_COLUMNS = [
    "appId", "iKey", "sdkVersion", "itemCount", "itemType",
    "operation_Id", "operation_ParentId", "operation_Name",
    "client_IP", "client_Type", "client_Model",
    "performanceBucket", "name", "url",
    "client_City", "client_StateOrProvince",
    "cp_NewsCategory",
]

# CammsTrackingID join key — links page views with CPLAN packs/clusters/channels
# and with the iMEP email channel. See CPLAN/pipeline/docs/tracking-id.md.
# Source CSV may spell the field "CammsTrackingID" or "CommsTrackingID".
TRACKING_ID_SOURCE_COLS = ["cp_CammsTrackingID", "cp_CommsTrackingID"]

TIMEZONE = "Europe/Berlin"
TIME_ON_PAGE_CAP_SEC = 30 * 60


def log(msg):
    print(msg)


# ---------------------------------------------------------------------------
# File hashing & manifest (delta load with upsert)
# ---------------------------------------------------------------------------

def compute_file_hash(filepath):
    """SHA-256 hash of file contents for change detection."""
    h = hashlib.sha256()
    with open(filepath, "rb") as f:
        for chunk in iter(lambda: f.read(8192), b""):
            h.update(chunk)
    return h.hexdigest()


def ensure_manifest_table(con):
    """Create processed_files manifest table if it doesn't exist."""
    con.execute("""
        CREATE TABLE IF NOT EXISTS processed_files (
            filename     TEXT PRIMARY KEY,
            file_hash    TEXT,
            row_count    INTEGER,
            processed_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """)


def get_unprocessed_files(con, input_files):
    """Return list of (filepath, hash, reason) for new or changed files."""
    ensure_manifest_table(con)

    to_process = []
    skipped = []

    for filepath in input_files:
        file_hash = compute_file_hash(filepath)
        filename = filepath.name

        existing = con.execute(
            "SELECT file_hash FROM processed_files WHERE filename = ?",
            [filename],
        ).fetchone()

        if existing is None:
            to_process.append((filepath, file_hash, "new"))
        elif existing[0] != file_hash:
            to_process.append((filepath, file_hash, "changed"))
        else:
            skipped.append(filename)

    if skipped:
        log(f"  Skipping {len(skipped)} unchanged file(s): {', '.join(skipped)}")
    if to_process:
        log(f"  Found {len(to_process)} file(s) to process")

    return to_process


def record_processed_file(con, filepath, file_hash, row_count):
    """Record a successfully processed file in the manifest."""
    filename = filepath.name
    con.execute("DELETE FROM processed_files WHERE filename = ?", [filename])
    con.execute("""
        INSERT INTO processed_files (filename, file_hash, row_count, processed_at)
        VALUES (?, ?, ?, CURRENT_TIMESTAMP)
    """, [filename, file_hash, int(row_count)])


def get_input_files(input_path: Path) -> list[Path]:
    """Get all CSV/XLSX files from a path (file or directory)."""
    if input_path.is_file():
        return [input_path]

    if input_path.is_dir():
        files = []
        for pattern in ["*.csv", "*.xlsx", "*.xls"]:
            files.extend(input_path.glob(pattern))
        files.sort(key=lambda f: os.path.getmtime(f))
        return files

    return []


# ---------------------------------------------------------------------------
# Data transformation
# ---------------------------------------------------------------------------

def parse_custom_dimensions(value):
    """Parse a customDimensions JSON string into a flat dict."""
    if pd.isna(value) or not isinstance(value, str) or not value.strip():
        return {}

    try:
        parsed = json.loads(value)
    except json.JSONDecodeError:
        try:
            parsed = json.loads(json.loads(value))
        except (json.JSONDecodeError, TypeError):
            return {}

    flat = {}
    if isinstance(parsed, dict):
        custom_props = parsed.pop("CustomProps", None)
        for key, val in parsed.items():
            flat[key] = val
        if isinstance(custom_props, dict):
            for key, val in custom_props.items():
                flat[f"cp_{key}"] = val
        elif isinstance(custom_props, str):
            try:
                cp_parsed = json.loads(custom_props)
                if isinstance(cp_parsed, dict):
                    for key, val in cp_parsed.items():
                        flat[f"cp_{key}"] = val
            except json.JSONDecodeError:
                flat["cp_raw"] = custom_props
    return flat


def read_input(file_path: Path) -> pd.DataFrame:
    """Read CSV or XLSX input file."""
    suffix = file_path.suffix.lower()
    if suffix == ".csv":
        return pd.read_csv(file_path)
    elif suffix in (".xlsx", ".xls"):
        return pd.read_excel(file_path)
    else:
        raise ValueError(f"Unsupported file format: {suffix}. Use .csv or .xlsx")


def flatten_appinsights(df: pd.DataFrame) -> pd.DataFrame:
    """Expand the customDimensions JSON column into separate cp_ columns."""
    cd_col = None
    for col in df.columns:
        if col.lower().replace(" ", "") == "customdimensions":
            cd_col = col
            break

    if cd_col is None:
        log("  Warning: No 'customDimensions' column found.")
        return df

    expanded = df[cd_col].apply(parse_custom_dimensions)
    expanded_df = pd.json_normalize(expanded)

    result = df.drop(columns=[cd_col]).reset_index(drop=True)
    result = pd.concat([result, expanded_df.reset_index(drop=True)], axis=1)
    return result


def build_clean_table(df: pd.DataFrame) -> pd.DataFrame:
    """Drop noise columns, rename, parse dates, convert UTC to CET."""
    flat = df.copy()

    cols_to_drop = [c for c in DROP_COLUMNS if c in flat.columns]
    flat = flat.drop(columns=cols_to_drop)

    rename_map = {}
    rename_map.update(FACT_COLUMNS)
    rename_map.update(DIM_PAGE_COLUMNS)
    existing_renames = {k: v for k, v in rename_map.items() if k in flat.columns}
    flat = flat.rename(columns=existing_renames)

    if "timestamp_utc" in flat.columns:
        flat["timestamp_utc"] = pd.to_datetime(flat["timestamp_utc"], errors="coerce")
        flat["timestamp_utc"] = flat["timestamp_utc"].dt.tz_localize("UTC")
        flat["timestamp"] = flat["timestamp_utc"].dt.tz_convert(TIMEZONE)
        flat = flat.drop(columns=["timestamp_utc"])

    if "publishing_date" in flat.columns:
        flat["publishing_date"] = pd.to_datetime(flat["publishing_date"], errors="coerce")

    if "gpn" in flat.columns:
        flat["gpn"] = (
            flat["gpn"].astype(str)
            .str.replace(r"\.0$", "", regex=True)
            .str.strip().str.zfill(8)
        )
        flat.loc[flat["gpn"].isin(["", "nan", "None", "00000000"]), "gpn"] = None

    flat = parse_tracking_id(flat)

    return flat


def parse_tracking_id(flat: pd.DataFrame) -> pd.DataFrame:
    """Consolidate CammsTrackingID/CommsTrackingID and split into components.

    Format: <cluster>-<pack_number>-<YYMMDD>-<activity_number>-<channel_abbr>
    Example: QRREP-0000058-240709-0000060-EMI

    Adds columns: tracking_id, tracking_cluster_id, tracking_pack_number,
    tracking_pub_date, tracking_activity_number, tracking_channel_abbr,
    tracking_pack_id (cluster + pack_number).
    """
    sources = [c for c in TRACKING_ID_SOURCE_COLS if c in flat.columns]
    if not sources:
        return flat

    raw = flat[sources[0]].astype("string")
    for extra in sources[1:]:
        raw = raw.fillna(flat[extra].astype("string"))

    raw = raw.str.strip()
    raw = raw.where(~raw.isin(["", "nan", "None", "null"]), other=pd.NA)

    flat["tracking_id"] = raw

    parts = raw.str.split("-", n=4, expand=True)
    expected = ["tracking_cluster_id", "tracking_pack_number", "tracking_pub_date",
                "tracking_activity_number", "tracking_channel_abbr"]
    for i, name in enumerate(expected):
        flat[name] = parts[i] if i in parts.columns else pd.NA

    flat["tracking_pack_id"] = (
        flat["tracking_cluster_id"].astype("string")
        + "-" + flat["tracking_pack_number"].astype("string")
    )
    flat.loc[flat["tracking_cluster_id"].isna() | flat["tracking_pack_number"].isna(),
             "tracking_pack_id"] = pd.NA

    flat = flat.drop(columns=sources)

    matched = flat["tracking_id"].notna().sum()
    log(f"  CammsTrackingID: {matched:,}/{len(flat):,} rows have a tracking_id")

    return flat


def join_hr_data(clean_df: pd.DataFrame, hr_path: Path) -> pd.DataFrame:
    """Temporal join of pageViews with HR snapshots via GPN."""
    con = duckdb.connect()

    con.register("pageviews_df", clean_df)
    con.execute("CREATE TABLE pageviews AS SELECT * FROM pageviews_df")
    con.execute(f"CREATE TABLE hr_history AS SELECT * FROM read_parquet('{hr_path}')")

    row_count = con.execute("SELECT COUNT(*) FROM hr_history").fetchone()[0]
    gpn_count = con.execute("SELECT COUNT(DISTINCT gpn) FROM hr_history").fetchone()[0]
    snap_count = con.execute(
        "SELECT COUNT(DISTINCT (snapshot_year, snapshot_month)) FROM hr_history"
    ).fetchone()[0]
    log(f"  HR: {row_count:,} rows, {gpn_count:,} GPNs, {snap_count} snapshot(s)")

    hr_cols = con.execute("DESCRIBE hr_history").df()["column_name"].tolist()
    avail_hr = {src: alias for src, alias in HR_FIELD_MAP.items() if src in hr_cols}

    if not avail_hr:
        con.close()
        return clean_df

    hr_sel = ", ".join(f"h.{s} as {a}" for s, a in avail_hr.items())
    hr_coal = ", ".join(f"COALESCE(hr_exact.{a}, hr_fallback.{a}) as {a}"
                        for _, a in avail_hr.items())
    gpn_expr = "LPAD(REGEXP_REPLACE(CAST(p.gpn AS VARCHAR), '\\.0$', ''), 8, '0')"

    stats = con.execute(f"""
        SELECT COUNT(*), COUNT(CASE WHEN gpn IS NOT NULL THEN 1 END),
               COUNT(DISTINCT gpn) FILTER (WHERE gpn IS NOT NULL),
               COUNT(DISTINCT gpn) FILTER (WHERE gpn IS NOT NULL
                   AND CAST(gpn AS VARCHAR) IN (SELECT DISTINCT CAST(gpn AS VARCHAR) FROM hr_history))
        FROM pageviews
    """).fetchone()
    log(f"  GPN match: {stats[1]:,}/{stats[0]:,} have GPN, {stats[3]:,}/{stats[2]:,} in HR")

    result = con.execute(f"""
        SELECT p.*, {hr_coal}
        FROM pageviews p
        LEFT JOIN LATERAL (
            SELECT {hr_sel} FROM hr_history h
            WHERE CAST(h.gpn AS VARCHAR) = {gpn_expr}
              AND (h.snapshot_year * 100 + h.snapshot_month)
                  <= (YEAR(p.timestamp) * 100 + MONTH(p.timestamp))
            ORDER BY h.snapshot_year DESC, h.snapshot_month DESC LIMIT 1
        ) hr_exact ON true
        LEFT JOIN LATERAL (
            SELECT {hr_sel} FROM hr_history h
            WHERE CAST(h.gpn AS VARCHAR) = {gpn_expr}
              AND (h.snapshot_year * 100 + h.snapshot_month)
                  > (YEAR(p.timestamp) * 100 + MONTH(p.timestamp))
            ORDER BY h.snapshot_year ASC, h.snapshot_month ASC LIMIT 1
        ) hr_fallback ON true
    """).df()

    matched = result[list(avail_hr.values())].notna().any(axis=1).sum()
    log(f"  HR enriched: {matched:,}/{len(result):,} rows")

    con.close()
    return result


def compute_time_on_page(fact: pd.DataFrame) -> pd.DataFrame:
    """Compute time-on-page as delta to next pageView within session."""
    if "session_id" not in fact.columns or "timestamp" not in fact.columns:
        return fact

    df = fact.sort_values(["session_id", "timestamp"]).reset_index(drop=True)
    df["_next_ts"] = df.groupby("session_id")["timestamp"].shift(-1)
    df["time_on_page_sec"] = (df["_next_ts"] - df["timestamp"]).dt.total_seconds()
    df["is_last_in_session"] = df["_next_ts"].isna()
    df.loc[df["time_on_page_sec"] > TIME_ON_PAGE_CAP_SEC, "time_on_page_sec"] = None
    df = df.drop(columns=["_next_ts"])
    return df


def build_fact_page_view(flat: pd.DataFrame, source_file: str) -> pd.DataFrame:
    """Extract the fact table, compute time-on-page, tag with source file."""
    fact_cols = [
        "view_id", "timestamp", "page_id", "user_id", "session_id",
        "page_load_ms", "referrer_url", "client_os", "client_browser",
        "client_country", "email", "gpn",
        "tracking_id", "tracking_pack_id", "tracking_cluster_id",
        "tracking_pack_number", "tracking_pub_date",
        "tracking_activity_number", "tracking_channel_abbr",
    ]
    hr_cols = [c for c in flat.columns if c.startswith("hr_")]
    fact_cols.extend(hr_cols)

    available = [c for c in fact_cols if c in flat.columns]
    fact = flat[available].copy()
    fact = compute_time_on_page(fact)

    # Tag each row with its source file for upsert tracking
    fact["source_file"] = source_file

    return fact


def build_agg_session(fact: pd.DataFrame) -> pd.DataFrame:
    """Aggregate pageViews into sessions."""
    if "session_id" not in fact.columns or "timestamp" not in fact.columns:
        return pd.DataFrame()

    sorted_df = fact.sort_values(["session_id", "timestamp"])

    entry = sorted_df.groupby("session_id").first()
    exit_ = sorted_df.groupby("session_id").last()

    agg = sorted_df.groupby("session_id").agg(
        user_id=("user_id", "first"),
        gpn=("gpn", "first"),
        email=("email", "first"),
        session_start=("timestamp", "min"),
        session_end=("timestamp", "max"),
        page_view_count=("view_id", "count"),
        client_country=("client_country", "first"),
        client_os=("client_os", "first"),
        client_browser=("client_browser", "first"),
    ).reset_index()

    agg["entry_page_id"] = entry["page_id"].values
    agg["exit_page_id"] = exit_["page_id"].values
    agg["duration_sec"] = (
        (agg["session_end"] - agg["session_start"]).dt.total_seconds().round(0)
    )

    if "time_on_page_sec" in sorted_df.columns:
        eng = (sorted_df.groupby("session_id")["time_on_page_sec"]
               .sum().reset_index()
               .rename(columns={"time_on_page_sec": "engagement_time_sec"}))
        agg = agg.merge(eng, on="session_id", how="left")

        avg_top = (sorted_df[sorted_df["is_last_in_session"] == False]
                   .groupby("session_id")["time_on_page_sec"]
                   .mean().round(1).reset_index()
                   .rename(columns={"time_on_page_sec": "avg_time_on_page_sec"}))
        agg = agg.merge(avg_top, on="session_id", how="left")

    agg["is_bounce"] = agg["page_view_count"] == 1
    agg["session_date"] = agg["session_start"].dt.normalize()

    hr_cols = [c for c in fact.columns if c.startswith("hr_")]
    if hr_cols:
        hr_first = sorted_df.groupby("session_id")[hr_cols].first().reset_index()
        agg = agg.merge(hr_first, on="session_id", how="left")

    col_order = [
        "session_id", "user_id", "gpn", "email",
        "session_date", "session_start", "session_end", "duration_sec",
        "engagement_time_sec", "avg_time_on_page_sec",
        "page_view_count", "entry_page_id", "exit_page_id", "is_bounce",
        "client_country", "client_os", "client_browser",
    ] + hr_cols
    return agg[[c for c in col_order if c in agg.columns]]


def build_dim_page(flat: pd.DataFrame) -> pd.DataFrame:
    dim_cols = [
        "page_id", "page_name", "page_url", "site_id", "site_name",
        "content_owner", "content_type", "theme", "topic",
        "target_region", "target_org", "page_status", "publishing_date",
    ]
    available = [c for c in dim_cols if c in flat.columns]
    if not available or "page_id" not in flat.columns:
        return pd.DataFrame()
    return flat[available].drop_duplicates(subset=["page_id"]).reset_index(drop=True)


def build_dim_date(flat: pd.DataFrame) -> pd.DataFrame:
    if "timestamp" not in flat.columns:
        return pd.DataFrame()
    dates = flat["timestamp"].dropna().dt.normalize().drop_duplicates().sort_values()
    dim = pd.DataFrame({"date": dates}).reset_index(drop=True)
    dim["date"] = dim["date"].dt.tz_localize(None)
    dim["date_key"] = dim["date"].dt.strftime("%Y%m%d").astype(int)
    dim["year"] = dim["date"].dt.year
    dim["month"] = dim["date"].dt.month
    dim["month_name"] = dim["date"].dt.strftime("%B")
    dim["week"] = dim["date"].dt.isocalendar().week.astype(int)
    dim["day_of_week"] = dim["date"].dt.strftime("%A")
    dim["quarter"] = dim["date"].dt.quarter
    return dim[["date_key", "date", "year", "quarter", "month", "month_name",
                "week", "day_of_week"]]


def strip_tz(df: pd.DataFrame) -> pd.DataFrame:
    for col in df.select_dtypes(include=["datetimetz"]).columns:
        df[col] = df[col].dt.tz_localize(None)
    return df


# ---------------------------------------------------------------------------
# DuckDB persistence with upsert
# ---------------------------------------------------------------------------

def upsert_fact(con, fact: pd.DataFrame, source_filename: str):
    """Upsert fact rows: delete old rows from this source file, insert new ones.

    This ensures that when a file is re-exported with updated data for the
    same time period, the new data replaces the old.
    """
    con.register("fact_df", fact)

    # Ensure table exists
    con.execute("""
        CREATE TABLE IF NOT EXISTS fact_page_view AS
        SELECT * FROM fact_df WHERE 1=0
    """)

    # Delete old rows from this source file (upsert)
    deleted = con.execute(f"""
        SELECT COUNT(*) FROM fact_page_view WHERE source_file = '{source_filename}'
    """).fetchone()[0]

    if deleted > 0:
        con.execute(f"DELETE FROM fact_page_view WHERE source_file = '{source_filename}'")
        log(f"  Upsert: removed {deleted:,} old rows from {source_filename}")

    # Insert new rows
    con.execute("INSERT INTO fact_page_view SELECT * FROM fact_df")
    total = con.execute("SELECT COUNT(*) FROM fact_page_view").fetchone()[0]
    log(f"  fact_page_view: +{len(fact):,} rows ({total:,} total)")


def upsert_dims(con, dim_page: pd.DataFrame, dim_date: pd.DataFrame):
    """Merge new dimension entries (insert only, dims are append-only)."""
    if not dim_page.empty:
        con.register("dim_page_df", dim_page)
        con.execute("CREATE TABLE IF NOT EXISTS dim_page AS SELECT * FROM dim_page_df WHERE 1=0")
        con.execute("""
            INSERT INTO dim_page SELECT d.* FROM dim_page_df d
            WHERE d.page_id NOT IN (SELECT page_id FROM dim_page)
        """)

    if not dim_date.empty:
        con.register("dim_date_df", dim_date)
        con.execute("CREATE TABLE IF NOT EXISTS dim_date AS SELECT * FROM dim_date_df WHERE 1=0")
        con.execute("""
            INSERT INTO dim_date SELECT d.* FROM dim_date_df d
            WHERE d.date_key NOT IN (SELECT date_key FROM dim_date)
        """)


def rebuild_agg_session(con):
    """Rebuild agg_session from the full fact_page_view in DuckDB."""
    log("Rebuilding agg_session from all data...")
    full_fact = con.execute("SELECT * FROM fact_page_view").df()
    if full_fact.empty:
        return pd.DataFrame()

    agg = build_agg_session(full_fact)
    agg = strip_tz(agg)

    con.execute("DROP TABLE IF EXISTS agg_session")
    con.register("full_agg_df", agg)
    con.execute("CREATE TABLE agg_session AS SELECT * FROM full_agg_df")

    count = con.execute("SELECT COUNT(*) FROM agg_session").fetchone()[0]
    log(f"  agg_session: {count:,} sessions")
    return agg


def print_summary(con):
    """Print summary statistics from DuckDB."""
    log("\n" + "=" * 60)
    log("SUMMARY")
    log("=" * 60)

    tables = con.execute("SHOW TABLES").df()["name"].tolist()

    for table in ["fact_page_view", "agg_session", "dim_page", "dim_date"]:
        if table in tables:
            count = con.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
            log(f"  {table}: {count:,} rows")

    if "processed_files" in tables:
        manifest = con.execute("""
            SELECT filename, row_count, processed_at
            FROM processed_files ORDER BY processed_at
        """).df()
        if not manifest.empty:
            log(f"\n  Processed files ({len(manifest)}):")
            for _, row in manifest.iterrows():
                log(f"    {row['filename']}: {row['row_count']:,} rows "
                    f"({row['processed_at']})")

    if "fact_page_view" in tables:
        stats = con.execute("""
            SELECT COUNT(*), COUNT(time_on_page_sec),
                   ROUND(MEDIAN(time_on_page_sec), 0),
                   ROUND(AVG(time_on_page_sec), 0)
            FROM fact_page_view
        """).fetchone()
        if stats[0] > 0 and stats[1] > 0:
            log(f"\n  Time-on-page: {stats[1]:,}/{stats[0]:,} measurable "
                f"({stats[1]/stats[0]*100:.1f}%), "
                f"median {stats[2]}s, mean {stats[3]}s")

    if "agg_session" in tables:
        sess = con.execute("""
            SELECT COUNT(*),
                   SUM(CASE WHEN is_bounce THEN 1 ELSE 0 END),
                   ROUND(AVG(page_view_count), 1)
            FROM agg_session
        """).fetchone()
        if sess[0] > 0:
            log(f"  Sessions: {sess[0]:,}, {sess[1]:,} bounces "
                f"({sess[1]/sess[0]*100:.1f}%), avg {sess[2]} pages/session")

    if "fact_page_view" in tables:
        fact_cols = con.execute("DESCRIBE fact_page_view").df()["column_name"].tolist()
        if "tracking_id" in fact_cols:
            tot, with_tid, with_pack = con.execute("""
                SELECT COUNT(*),
                       COUNT(tracking_id),
                       COUNT(DISTINCT tracking_pack_id)
                FROM fact_page_view
            """).fetchone()
            pct = (with_tid / tot * 100) if tot else 0
            log(f"\n  Tracking ID coverage: {with_tid:,}/{tot:,} views ({pct:.1f}%), "
                f"{with_pack:,} distinct packs")

            channels = con.execute("""
                SELECT COALESCE(tracking_channel_abbr, '(none)') AS channel,
                       COUNT(*) AS views,
                       COUNT(DISTINCT tracking_pack_id) AS packs
                FROM fact_page_view
                GROUP BY 1 ORDER BY views DESC
            """).fetchall()
            log("  Views by tracking_channel_abbr:")
            for ch, views, packs in channels:
                log(f"    {ch:10s}  views={views:>10,}  packs={packs:>6,}")

            if "dim_page" in tables:
                log("\n  Tracking ID coverage by site (top 20 by views):")
                site_cov = con.execute("""
                    SELECT COALESCE(p.site_name, '(unknown)') AS site,
                           COUNT(*) AS views,
                           COUNT(f.tracking_id) AS with_tid,
                           ROUND(COUNT(f.tracking_id) * 100.0 / COUNT(*), 1) AS pct
                    FROM fact_page_view f
                    LEFT JOIN dim_page p ON f.page_id = p.page_id
                    GROUP BY 1
                    ORDER BY views DESC
                    LIMIT 20
                """).fetchall()
                log(f"    {'site':40s} {'views':>10s} {'with_tid':>10s} {'cov%':>7s}")
                for site, views, with_tid, pct in site_cov:
                    site_disp = (site[:37] + "...") if len(site) > 40 else site
                    log(f"    {site_disp:40s} {views:>10,} {with_tid:>10,} {pct:>6.1f}%")

                log("\n  Top 15 pages WITHOUT tracking_id (by views):")
                pages_missing = con.execute("""
                    SELECT COALESCE(p.site_name, '(unknown)') AS site,
                           COALESCE(p.page_name, f.page_id, '(unknown)') AS page,
                           COUNT(*) AS views
                    FROM fact_page_view f
                    LEFT JOIN dim_page p ON f.page_id = p.page_id
                    WHERE f.tracking_id IS NULL
                    GROUP BY 1, 2
                    ORDER BY views DESC
                    LIMIT 15
                """).fetchall()
                for site, page, views in pages_missing:
                    site_disp = (site[:25] + "...") if len(site) > 28 else site
                    page_disp = (page[:47] + "...") if len(page) > 50 else page
                    log(f"    {site_disp:28s} | {page_disp:50s} {views:>8,}")

                log("\n  Top 15 pages WITH tracking_id (by views):")
                pages_with = con.execute("""
                    SELECT COALESCE(p.site_name, '(unknown)') AS site,
                           COALESCE(p.page_name, f.page_id, '(unknown)') AS page,
                           COUNT(*) AS views,
                           COUNT(DISTINCT f.tracking_id) AS distinct_tids
                    FROM fact_page_view f
                    LEFT JOIN dim_page p ON f.page_id = p.page_id
                    WHERE f.tracking_id IS NOT NULL
                    GROUP BY 1, 2
                    ORDER BY views DESC
                    LIMIT 15
                """).fetchall()
                for site, page, views, n_tids in pages_with:
                    site_disp = (site[:25] + "...") if len(site) > 28 else site
                    page_disp = (page[:47] + "...") if len(page) > 50 else page
                    log(f"    {site_disp:28s} | {page_disp:50s} {views:>8,}  tids={n_tids}")


# ---------------------------------------------------------------------------
# Pipeline: process one file
# ---------------------------------------------------------------------------

def process_file(filepath: Path, hr_path: Path) -> tuple:
    """Process one input file end-to-end. Returns (fact, dim_page, dim_date)."""
    raw_df = read_input(filepath)
    log(f"  {len(raw_df):,} raw rows")

    expanded = flatten_appinsights(raw_df)
    clean = build_clean_table(expanded)
    enriched = join_hr_data(clean, hr_path)

    fact = build_fact_page_view(enriched, source_file=filepath.name)
    dim_page = build_dim_page(enriched)
    dim_date = build_dim_date(enriched)

    fact = strip_tz(fact)

    return fact, dim_page, dim_date


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(
        description="Flatten AppInsights pageViews, join HR, produce star schema"
    )
    parser.add_argument("input", nargs="?", default=None,
                        help="CSV/XLSX file or directory (default: input/ next to scripts/)")
    parser.add_argument("-o", "--output-dir", help="Output directory")
    parser.add_argument("--hr", help="Path to hr_history.parquet")
    parser.add_argument("--db", help="DuckDB file path")
    parser.add_argument("--full-refresh", action="store_true",
                        help="Reprocess all files, rebuild DB from scratch")
    args = parser.parse_args()

    # Default input: input/ directory next to scripts/
    script_dir = Path(__file__).resolve().parent
    if args.input:
        input_path = Path(args.input)
    else:
        input_path = script_dir.parent / "input"

    if not input_path.exists():
        log(f"Error: Path not found: {input_path}")
        sys.exit(1)

    project_root = script_dir.parent
    input_dir = input_path if input_path.is_dir() else input_path.parent
    output_dir = Path(args.output_dir) if args.output_dir else project_root / "output"
    output_dir.mkdir(parents=True, exist_ok=True)
    db_path = Path(args.db) if args.db else output_dir / "pageviews.duckdb"

    hr_path = Path(args.hr) if args.hr else (
        project_root.parent / "SearchAnalytics" / "output" / "hr_history.parquet"
    )
    if not hr_path.exists():
        log(f"Error: HR history not found: {hr_path}")
        log("  Run process_hr_history.py first, or use --hr /path/to/hr_history.parquet")
        sys.exit(1)

    # Full refresh: delete existing DB
    if args.full_refresh and db_path.exists():
        log(f"Full refresh: deleting {db_path}")
        db_path.unlink()

    # Get input files
    all_files = get_input_files(input_path)
    if not all_files:
        log(f"No CSV/XLSX files found in {input_path}")
        sys.exit(1)
    log(f"Found {len(all_files)} input file(s)")

    # Open persistent DuckDB
    con = duckdb.connect(str(db_path))
    ensure_manifest_table(con)

    # Delta detection via SHA-256 file hash
    if args.full_refresh:
        files_to_process = [(f, compute_file_hash(f), "full-refresh") for f in all_files]
    else:
        files_to_process = get_unprocessed_files(con, all_files)

    if not files_to_process:
        log("All files already processed. Nothing new to do.")
        log("Use --full-refresh to reprocess everything.")
        print_summary(con)
        con.close()
        return

    # Process each file
    for filepath, file_hash, reason in files_to_process:
        log(f"\nProcessing ({reason}): {filepath.name}")

        fact, dim_page, dim_date = process_file(filepath, hr_path)

        upsert_fact(con, fact, filepath.name)
        upsert_dims(con, dim_page, dim_date)
        record_processed_file(con, filepath, file_hash, len(fact))

    # Rebuild agg_session from full dataset
    agg_sess = rebuild_agg_session(con)

    # Summary
    print_summary(con)

    # Export Parquet from DuckDB
    log(f"\nExporting Parquet to: {output_dir}/")
    for table in ["fact_page_view", "agg_session", "dim_page", "dim_date"]:
        tables = con.execute("SHOW TABLES").df()["name"].tolist()
        if table in tables:
            con.execute(f"COPY {table} TO '{output_dir / f'{table}.parquet'}' (FORMAT PARQUET)")
    log("  Parquet files written")

    # Excel summary
    xlsx_path = output_dir / "pageviews_cdm.xlsx"
    log(f"Writing Excel: {xlsx_path}")
    with pd.ExcelWriter(xlsx_path, engine="openpyxl") as writer:
        for table in ["fact_page_view", "agg_session", "dim_page", "dim_date"]:
            tables = con.execute("SHOW TABLES").df()["name"].tolist()
            if table in tables:
                df = strip_tz(con.execute(f"SELECT * FROM {table}").df())
                if not df.empty:
                    df.to_excel(writer, sheet_name=table, index=False)

    con.close()

    log(f"\nDone. Output in {output_dir}/")
    log(f"  DuckDB:  {db_path}")
    log(f"  Parquet: {output_dir}/*.parquet")
    log(f"  Excel:   {xlsx_path}")


if __name__ == "__main__":
    main()
