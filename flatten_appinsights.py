"""
Flatten AppInsights pageViews export, join with HR snapshots, and produce
a CDM-based star schema.

Reads a CSV or XLSX file exported from Azure AppInsights, flattens the nested
customDimensions JSON column, converts timestamps from UTC to CET, joins with
HR snapshot data (hr_history.parquet) using temporal GPN matching, computes
time-on-page and session-level engagement metrics, and produces:
  1. A flat denormalized table (all relevant fields + HR dimensions)
  2. A star schema: fact_page_view, agg_session, dim_page, dim_date

Usage:
    python flatten_appinsights.py input_file.csv
    python flatten_appinsights.py input_file.xlsx
    python flatten_appinsights.py input_file.csv -o output.xlsx
    python flatten_appinsights.py input_file.csv --hr path/to/hr_history.parquet

The script expects hr_history.parquet in ../SearchAnalytics/output/ by default
(same convention as CampaignWe). Override with --hr flag.
"""

import argparse
import json
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

# HR fields to bring in from hr_history.parquet (src -> alias)
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

# Columns to drop entirely (no analytical value)
DROP_COLUMNS = [
    "appId", "iKey", "sdkVersion", "itemCount", "itemType",
    "operation_Id", "operation_ParentId", "operation_Name",
    "client_IP", "client_Type", "client_Model",
    "performanceBucket", "name", "url",
    "client_City", "client_StateOrProvince",
    "cp_CommsTrackingID", "cp_NewsCategory",
]

TIMEZONE = "Europe/Berlin"

# Cap for time-on-page: deltas above this are treated as inactive (user left)
TIME_ON_PAGE_CAP_SEC = 30 * 60  # 30 minutes


def log(msg):
    print(msg)


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
        log("Warning: No 'customDimensions' column found. Returning data as-is.")
        return df

    expanded = df[cd_col].apply(parse_custom_dimensions)
    expanded_df = pd.json_normalize(expanded)

    result = df.drop(columns=[cd_col]).reset_index(drop=True)
    result = pd.concat([result, expanded_df.reset_index(drop=True)], axis=1)
    return result


def build_clean_table(df: pd.DataFrame) -> pd.DataFrame:
    """Drop noise columns, rename to CDM-friendly snake_case, parse dates,
    convert UTC timestamps to CET."""
    flat = df.copy()

    cols_to_drop = [c for c in DROP_COLUMNS if c in flat.columns]
    flat = flat.drop(columns=cols_to_drop)

    rename_map = {}
    rename_map.update(FACT_COLUMNS)
    rename_map.update(DIM_PAGE_COLUMNS)
    existing_renames = {k: v for k, v in rename_map.items() if k in flat.columns}
    flat = flat.rename(columns=existing_renames)

    # Parse and convert timestamp from UTC to CET
    if "timestamp_utc" in flat.columns:
        flat["timestamp_utc"] = pd.to_datetime(flat["timestamp_utc"], errors="coerce")
        flat["timestamp_utc"] = flat["timestamp_utc"].dt.tz_localize("UTC")
        flat["timestamp"] = flat["timestamp_utc"].dt.tz_convert(TIMEZONE)
        flat = flat.drop(columns=["timestamp_utc"])

    if "publishing_date" in flat.columns:
        flat["publishing_date"] = pd.to_datetime(
            flat["publishing_date"], errors="coerce"
        )

    # Normalize GPN: strip .0 suffix from Excel floats, zero-pad to 8 digits
    if "gpn" in flat.columns:
        flat["gpn"] = (
            flat["gpn"]
            .astype(str)
            .str.replace(r"\.0$", "", regex=True)
            .str.strip()
            .str.zfill(8)
        )
        flat.loc[flat["gpn"].isin(["", "nan", "None", "00000000"]), "gpn"] = None

    return flat


def join_hr_data(clean_df: pd.DataFrame, hr_path: Path) -> pd.DataFrame:
    """Temporal join of pageViews with HR snapshots via GPN using DuckDB.

    For each pageView, finds the most recent HR snapshot at or before the
    event month. Falls back to the earliest snapshot after the event if
    no prior snapshot exists.
    """
    log(f"Loading HR data: {hr_path}")
    con = duckdb.connect()

    con.register("pageviews_df", clean_df)
    con.execute("CREATE TABLE pageviews AS SELECT * FROM pageviews_df")

    con.execute(f"""
        CREATE TABLE hr_history AS
        SELECT * FROM read_parquet('{hr_path}')
    """)

    row_count = con.execute("SELECT COUNT(*) FROM hr_history").fetchone()[0]
    gpn_count = con.execute("SELECT COUNT(DISTINCT gpn) FROM hr_history").fetchone()[0]
    snap_count = con.execute(
        "SELECT COUNT(DISTINCT (snapshot_year, snapshot_month)) FROM hr_history"
    ).fetchone()[0]
    log(f"  Loaded hr_history: {row_count:,} rows, {gpn_count:,} GPNs, {snap_count} snapshot(s)")

    hr_cols = con.execute("DESCRIBE hr_history").df()["column_name"].tolist()
    avail_hr = {src: alias for src, alias in HR_FIELD_MAP.items() if src in hr_cols}
    log(f"  HR fields to join: {list(avail_hr.values())}")

    if not avail_hr:
        log("  WARNING: No matching HR fields found. Skipping HR join.")
        con.close()
        return clean_df

    hr_select_parts = [f"h.{src} as {alias}" for src, alias in avail_hr.items()]
    hr_select_sql = ", ".join(hr_select_parts)

    hr_coalesce_parts = [
        f"COALESCE(hr_exact.{alias}, hr_fallback.{alias}) as {alias}"
        for _, alias in avail_hr.items()
    ]
    hr_coalesce_sql = ", ".join(hr_coalesce_parts)

    gpn_expr = "LPAD(REGEXP_REPLACE(CAST(p.gpn AS VARCHAR), '\\.0$', ''), 8, '0')"

    match_stats = con.execute(f"""
        SELECT
            COUNT(*) as total,
            COUNT(CASE WHEN p.gpn IS NOT NULL THEN 1 END) as has_gpn,
            COUNT(DISTINCT p.gpn) FILTER (WHERE p.gpn IS NOT NULL) as unique_gpns,
            COUNT(DISTINCT p.gpn) FILTER (
                WHERE p.gpn IS NOT NULL
                AND CAST(p.gpn AS VARCHAR) IN (SELECT DISTINCT CAST(gpn AS VARCHAR) FROM hr_history)
            ) as matched_gpns
        FROM pageviews p
    """).fetchone()
    log(f"  Match stats: {match_stats[1]:,}/{match_stats[0]:,} rows have GPN, "
        f"{match_stats[3]:,}/{match_stats[2]:,} unique GPNs found in HR data")

    result = con.execute(f"""
        SELECT
            p.*,
            {hr_coalesce_sql}
        FROM pageviews p
        LEFT JOIN LATERAL (
            SELECT {hr_select_sql}
            FROM hr_history h
            WHERE CAST(h.gpn AS VARCHAR) = {gpn_expr}
              AND (h.snapshot_year * 100 + h.snapshot_month)
                  <= (YEAR(p.timestamp) * 100 + MONTH(p.timestamp))
            ORDER BY h.snapshot_year DESC, h.snapshot_month DESC
            LIMIT 1
        ) hr_exact ON true
        LEFT JOIN LATERAL (
            SELECT {hr_select_sql}
            FROM hr_history h
            WHERE CAST(h.gpn AS VARCHAR) = {gpn_expr}
              AND (h.snapshot_year * 100 + h.snapshot_month)
                  > (YEAR(p.timestamp) * 100 + MONTH(p.timestamp))
            ORDER BY h.snapshot_year ASC, h.snapshot_month ASC
            LIMIT 1
        ) hr_fallback ON true
    """).df()

    hr_filled = result[list(avail_hr.values())].notna().any(axis=1).sum()
    log(f"  HR enrichment: {hr_filled:,}/{len(result):,} rows matched with HR data")

    con.close()
    return result


def compute_time_on_page(fact: pd.DataFrame) -> pd.DataFrame:
    """Compute time-on-page as delta to the next pageView within the same session.

    - Sorted by session_id + timestamp
    - time_on_page_sec = next view's timestamp - current view's timestamp
    - Last page in session: time_on_page_sec = NULL (not measurable)
    - Capped at 30 minutes (deltas above this indicate user was inactive)
    - is_last_in_session flag marks rows where time_on_page is unknown
    """
    if "session_id" not in fact.columns or "timestamp" not in fact.columns:
        return fact

    df = fact.sort_values(["session_id", "timestamp"]).reset_index(drop=True)

    # Next timestamp within the same session
    df["_next_ts"] = df.groupby("session_id")["timestamp"].shift(-1)

    # Time-on-page in seconds
    df["time_on_page_sec"] = (df["_next_ts"] - df["timestamp"]).dt.total_seconds()

    # Flag last page in session (no next view → not measurable)
    df["is_last_in_session"] = df["_next_ts"].isna()

    # Cap at 30 minutes: treat longer gaps as inactive
    cap = TIME_ON_PAGE_CAP_SEC
    df.loc[df["time_on_page_sec"] > cap, "time_on_page_sec"] = None

    df = df.drop(columns=["_next_ts"])

    return df


def build_fact_page_view(flat: pd.DataFrame) -> pd.DataFrame:
    """Extract the fact table and compute time-on-page."""
    fact_cols = [
        "view_id", "timestamp", "page_id", "user_id", "session_id",
        "page_load_ms", "referrer_url", "client_os", "client_browser",
        "client_country", "email", "gpn",
    ]
    hr_cols = [c for c in flat.columns if c.startswith("hr_")]
    fact_cols.extend(hr_cols)

    available = [c for c in fact_cols if c in flat.columns]
    fact = flat[available].copy()

    # Compute time-on-page (engagement metric)
    fact = compute_time_on_page(fact)

    return fact


def build_agg_session(fact: pd.DataFrame) -> pd.DataFrame:
    """Aggregate pageViews into sessions.

    Derives session-level metrics: page count, duration, entry/exit page,
    bounce flag, engagement time. This is a CDM-compatible extension
    (derived aggregation, not a standard CDM entity).
    """
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

    # Session duration: last view - first view (0 for bounces)
    agg["duration_sec"] = (
        (agg["session_end"] - agg["session_start"]).dt.total_seconds().round(0)
    )

    # Engagement time: sum of measured time-on-page within the session
    # (excludes last page and capped values — gives a conservative estimate)
    if "time_on_page_sec" in sorted_df.columns:
        engagement = (
            sorted_df.groupby("session_id")["time_on_page_sec"]
            .sum()
            .reset_index()
            .rename(columns={"time_on_page_sec": "engagement_time_sec"})
        )
        agg = agg.merge(engagement, on="session_id", how="left")

    # Avg time on page within session (excluding last page)
    if "time_on_page_sec" in sorted_df.columns:
        avg_top = (
            sorted_df[sorted_df["is_last_in_session"] == False]
            .groupby("session_id")["time_on_page_sec"]
            .mean()
            .round(1)
            .reset_index()
            .rename(columns={"time_on_page_sec": "avg_time_on_page_sec"})
        )
        agg = agg.merge(avg_top, on="session_id", how="left")

    # Bounce = session with only 1 page view
    agg["is_bounce"] = agg["page_view_count"] == 1

    # Session date (date part of session_start, already in CET)
    agg["session_date"] = agg["session_start"].dt.normalize()

    # HR columns from first pageView
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
    available = [c for c in col_order if c in agg.columns]
    return agg[available]


def build_dim_page(flat: pd.DataFrame) -> pd.DataFrame:
    """Extract the page dimension (deduplicated)."""
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
    """Build a date dimension from timestamps (CET-based)."""
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


def main():
    parser = argparse.ArgumentParser(
        description="Flatten AppInsights pageViews, join HR, produce star schema"
    )
    parser.add_argument("input", help="Path to CSV or XLSX file from AppInsights")
    parser.add_argument(
        "-o", "--output",
        help="Output Excel file path (default: <input>_cdm.xlsx)",
    )
    parser.add_argument(
        "--hr",
        help="Path to hr_history.parquet (default: ../SearchAnalytics/output/hr_history.parquet)",
    )
    args = parser.parse_args()

    input_path = Path(args.input)
    if not input_path.exists():
        log(f"Error: File not found: {input_path}")
        sys.exit(1)

    output_path = Path(args.output) if args.output else input_path.with_name(
        f"{input_path.stem}_cdm.xlsx"
    )

    # Resolve HR parquet path (same convention as CampaignWe)
    if args.hr:
        hr_path = Path(args.hr)
    else:
        hr_path = input_path.parent.parent / "SearchAnalytics" / "output" / "hr_history.parquet"

    if not hr_path.exists():
        log(f"Error: HR history file not found: {hr_path}")
        log("  Run process_hr_history.py in SearchAnalytics first,")
        log("  or specify the path with --hr /path/to/hr_history.parquet")
        sys.exit(1)

    # Step 1: Read raw data
    log(f"Reading: {input_path}")
    raw_df = read_input(input_path)
    log(f"  Raw: {len(raw_df)} rows, {len(raw_df.columns)} columns")

    # Step 2: Flatten customDimensions JSON
    log("Flattening customDimensions...")
    expanded_df = flatten_appinsights(raw_df)
    cp_cols = [c for c in expanded_df.columns if c.startswith("cp_")]
    log(f"  Expanded: {len(expanded_df.columns)} columns ({len(cp_cols)} from CustomProps)")

    # Step 3: Clean up, convert UTC -> CET, normalize GPN
    log(f"Building clean table (timestamps converted to {TIMEZONE})...")
    clean = build_clean_table(expanded_df)

    # Step 4: Join with HR snapshots (temporal GPN match)
    log("Joining with HR data...")
    enriched = join_hr_data(clean, hr_path)

    # Step 5: Build star schema
    log("Building star schema...")
    fact = build_fact_page_view(enriched)
    agg_sess = build_agg_session(fact)
    dim_page = build_dim_page(enriched)
    dim_date = build_dim_date(enriched)

    log(f"  fact_page_view: {len(fact):,} rows x {len(fact.columns)} cols")
    log(f"  agg_session:    {len(agg_sess):,} rows x {len(agg_sess.columns)} cols")
    log(f"  dim_page:       {len(dim_page):,} rows x {len(dim_page.columns)} cols")
    log(f"  dim_date:       {len(dim_date):,} rows x {len(dim_date.columns)} cols")

    # Summary statistics
    if not fact.empty and "time_on_page_sec" in fact.columns:
        measurable = fact["time_on_page_sec"].notna()
        log(f"  Time-on-page: {measurable.sum():,}/{len(fact):,} views measurable "
            f"({measurable.sum()/len(fact)*100:.1f}%), "
            f"median {fact.loc[measurable, 'time_on_page_sec'].median():.0f}s, "
            f"mean {fact.loc[measurable, 'time_on_page_sec'].mean():.0f}s")

    if not agg_sess.empty:
        bounces = agg_sess["is_bounce"].sum()
        log(f"  Sessions: {len(agg_sess):,} total, "
            f"{bounces:,} bounces ({bounces/len(agg_sess)*100:.1f}%), "
            f"avg {agg_sess['page_view_count'].mean():.1f} pages/session")
        if "engagement_time_sec" in agg_sess.columns:
            non_bounce = agg_sess[~agg_sess["is_bounce"]]
            if not non_bounce.empty:
                log(f"  Engagement (non-bounce): "
                    f"median {non_bounce['engagement_time_sec'].median():.0f}s, "
                    f"mean {non_bounce['engagement_time_sec'].mean():.0f}s")

    # Step 6: Write Excel with multiple sheets
    log(f"Writing: {output_path}")
    with pd.ExcelWriter(output_path, engine="openpyxl") as writer:
        enriched.to_excel(writer, sheet_name="flat_all", index=False)
        fact.to_excel(writer, sheet_name="fact_page_view", index=False)
        if not agg_sess.empty:
            agg_sess.to_excel(writer, sheet_name="agg_session", index=False)
        if not dim_page.empty:
            dim_page.to_excel(writer, sheet_name="dim_page", index=False)
        if not dim_date.empty:
            dim_date.to_excel(writer, sheet_name="dim_date", index=False)

    log("Done. Sheets: flat_all, fact_page_view, agg_session, dim_page, dim_date")


if __name__ == "__main__":
    main()
