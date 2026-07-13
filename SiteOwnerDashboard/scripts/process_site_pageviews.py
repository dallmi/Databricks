"""
Build a single-site Parquet for the SiteOwnerDashboard.

Thin wrapper around ../../scripts/flatten_appinsights.py (repo pipeline):
reuses the exact same customDimensions flatten, UTC->CET, GPN-normalise,
CammsTrackingID-split and temporal HR-join logic, then

  1. joins the fact with its page dimension (denormalised — one wide table),
  2. derives `language` from the PageURL (/en/ /de/ /fr/ /it/ segment),
  3. filters to ONE site (SiteName or SiteID),

and writes SiteOwnerDashboard/output/site_pageviews.parquet — the single file
the standalone dashboard loads via DuckDB-WASM.

Incremental processing (same pattern as CampaignWe/process_campaignwe.py):
  - Only NEW or CHANGED files are processed, tracked via SHA-256 hash in a
    manifest (output/site_pageviews.manifest.json).
  - Overlapping exports are handled via UPSERT on a composite `event_key`
    (second-truncated timestamp + user_id + session_id + page_id — the
    CampaignWe primary-key approach): incoming rows replace existing rows with
    the same key, and a re-processed (changed) file first evicts all rows it
    contributed before (matched on source_file). No double counting.
    AppInsights `id` is deliberately NOT the key — real exports showed it is
    not unique per event.
  - --rebuild wipes the manifest and reprocesses everything from scratch.

Usage (from the SiteOwnerDashboard project root; raw exports go in input/):
    # Default: processes new/changed CSV/XLSX files in input/, upserts into
    # output/site_pageviews.parquet, and auto-detects the HR parquet
    # (input/*.parquet, then ../../SearchAnalytics/output/hr_history.parquet):
    python scripts/process_site_pageviews.py

    # Explicit file(s) skip the hash check (forced), but still upsert:
    python scripts/process_site_pageviews.py input/news_and_events.csv --site "News and events"

    # By SiteID instead of name:
    python scripts/process_site_pageviews.py --site-id 5313b145-...

    # Override / disable HR enrichment:
    python scripts/process_site_pageviews.py --hr /path/hr_history.parquet
    python scripts/process_site_pageviews.py --no-hr

    # Full rebuild (e.g. after changing --site/--url-contains):
    python scripts/process_site_pageviews.py --rebuild

    # No --site given: keeps every site present in the export (use when the KQL
    # already scoped to one site).

Output: output/site_pageviews.parquet
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd

# Reuse the canonical pipeline from flatten_appinsights.py. In the repo it
# lives in ../../scripts/ (Databricks/scripts/); when SiteOwnerDashboard is
# copied standalone, a copy placed next to this script (or in the project
# root) works too.
SCRIPT_DIR = Path(__file__).resolve().parent
PROJECT_DIR = SCRIPT_DIR.parents[0]
_PIPELINE_CANDIDATES = [
    PROJECT_DIR.parent / "scripts",  # repo layout: Databricks/scripts/
    SCRIPT_DIR,                      # standalone: next to this script
    PROJECT_DIR,                     # standalone: project root
]
_pipeline_dir = next((d for d in _PIPELINE_CANDIDATES
                      if (d / "flatten_appinsights.py").exists()), None)
if _pipeline_dir is None:
    sys.exit(
        "flatten_appinsights.py not found — this script reuses the shared pipeline.\n"
        "Looked in:\n"
        + "\n".join(f"  - {d}" for d in _PIPELINE_CANDIDATES)
        + "\nIn the repo, run from Databricks/SiteOwnerDashboard/ (the pipeline lives in "
        "Databricks/scripts/).\nStandalone: copy Databricks/scripts/flatten_appinsights.py "
        f"next to this script ({SCRIPT_DIR})."
    )
if str(_pipeline_dir) not in sys.path:
    sys.path.insert(0, str(_pipeline_dir))

from flatten_appinsights import (  # noqa: E402
    read_input,
    flatten_appinsights,
    build_clean_table,
    join_hr_data,
    build_fact_page_view,
    build_dim_page,
    strip_tz,
)


# --- Language derived from PageURL ------------------------------------------
# Intranet URLs carry non-English content as a path segment, e.g.
#   https://intranet/sites/news/de/cawb.aspx  -> DE
# English has NO language segment (…/cawb.aspx), so a valid URL with no /de|/fr|
# /it segment is English. Matched case-insensitively as a standalone segment.
LANG_SEGMENTS = {"en": "EN", "de": "DE", "fr": "FR", "it": "IT"}
_LANG_RE = re.compile(r"/(en|de|fr|it)(?=/|$|\?|#)", re.IGNORECASE)


def derive_language(url) -> str:
    if not isinstance(url, str) or not url.strip():
        return "Other"
    m = _LANG_RE.search(url)
    # No language segment on a real URL means English (the default language).
    return LANG_SEGMENTS[m.group(1).lower()] if m else "EN"


# --- Language-agnostic page key ---------------------------------------------
# The SAME logical page exists once per language (…/de/cawb.aspx, …/cawb.aspx,
# …/fr/cawb.aspx, …/it/cawb.aspx), each with its own PageId — so grouping the
# Pages table by page_id shows one page several times. page_key strips the
# language segment (and lowercases / trims a trailing slash) so all language
# variants collapse to ONE key; the dashboard groups pages by page_key and the
# language filter still splits them. Falls back to the raw URL, then page_id.
def canonical_page_key(url) -> str | None:
    if not isinstance(url, str) or not url.strip():
        return None
    u = _LANG_RE.sub("", url.strip().lower())  # drop the /xx language segment
    if len(u) > 1 and u.endswith("/"):
        u = u[:-1]
    return u


INPUT_DIR = PROJECT_DIR / "input"
DEFAULT_HR_PATH = PROJECT_DIR.parent.parent / "SearchAnalytics" / "output" / "hr_history.parquet"


def discover_inputs() -> list[Path]:
    """All CSV/XLSX exports in input/, skipping hidden and Excel lock files."""
    files = [p for p in sorted(INPUT_DIR.glob("*"))
             if p.suffix.lower() in (".csv", ".xlsx", ".xls")
             and not p.name.startswith((".", "~$"))]
    return files


# --- Manifest: SHA-256 change detection (mirrors CampaignWe processed_files) --
def manifest_path(out_path: Path) -> Path:
    return out_path.with_suffix(".manifest.json")


def compute_file_hash(filepath: Path) -> str:
    """SHA-256 hash of file contents for change detection."""
    h = hashlib.sha256()
    with open(filepath, "rb") as f:
        for chunk in iter(lambda: f.read(8192), b""):
            h.update(chunk)
    return h.hexdigest()


def load_manifest(path: Path) -> dict:
    if path.exists():
        try:
            return json.loads(path.read_text())
        except (json.JSONDecodeError, OSError):
            print(f"  WARNING: unreadable manifest {path.name} — reprocessing everything")
    return {}


def save_manifest(path: Path, manifest: dict) -> None:
    path.write_text(json.dumps(manifest, indent=2, sort_keys=True) + "\n")


def partition_files(files: list[Path], manifest: dict) -> tuple[list[tuple[Path, str, str]], list[str]]:
    """Split into ([(path, hash, reason)], [skipped names]) via manifest hashes."""
    to_process, skipped = [], []
    for filepath in files:
        file_hash = compute_file_hash(filepath)
        entry = manifest.get(filepath.name)
        if entry is None:
            to_process.append((filepath, file_hash, "new"))
        elif entry.get("sha256") != file_hash:
            to_process.append((filepath, file_hash, "changed"))
        else:
            skipped.append(filepath.name)
    return to_process, skipped


# --- Composite upsert key (CampaignWe primary-key approach) ------------------
# All components are STORED AppInsights columns, so the same stored row yields
# the same key in every export. The timestamp is truncated to whole seconds so
# CSV (sub-second) and Excel (second-truncated) exports of the same rows still
# match. AppInsights `id` (view_id) is deliberately NOT part of the key: real
# exports contain duplicated ids for distinct events, so it is kept as data
# only. Known accepted limitation (same as CampaignWe): two genuinely distinct
# views of the same page by the same user+session within the same second
# collapse into one.
KEY_COLS = ["user_id", "session_id", "page_id"]


def add_event_key(df: pd.DataFrame) -> pd.DataFrame:
    if "timestamp" in df.columns:
        key = (pd.to_datetime(df["timestamp"], errors="coerce")
               .dt.strftime("%Y-%m-%d %H:%M:%S").fillna(""))
    else:
        key = pd.Series("", index=df.index, dtype="string")
    for c in KEY_COLS:
        part = df[c].fillna("").astype(str) if c in df.columns else ""
        key = key + "|" + part
    df = df.copy()
    df["event_key"] = key
    return df


# --- Upsert into the existing output parquet ---------------------------------
def upsert_store(store: pd.DataFrame | None, new: pd.DataFrame,
                 replaced_files: list[str]) -> pd.DataFrame:
    """Delete-then-insert (CampaignWe upsert pattern), key = event_key.

    1. Evict every store row contributed by a re-processed file (source_file)
       so changed files fully replace their previous rows.
    2. Evict store rows whose event_key re-appears in the new batch
       (overlapping time windows across different exports) — incoming wins.
    3. Append the new rows. Missing columns on either side become NA
       (schema evolution, e.g. HR columns present in only one batch).
    """
    if store is None or store.empty:
        return new

    if "event_key" not in store.columns:  # store written before the key existed
        store = add_event_key(store)

    before = len(store)
    if replaced_files and "source_file" in store.columns:
        store = store[~store["source_file"].isin(replaced_files)]
        evicted = before - len(store)
        if evicted:
            print(f"  Upsert: evicted {evicted:,} rows from changed file(s)")

    overlap = store["event_key"].isin(new["event_key"])
    if overlap.any():
        print(f"  Upsert: replaced {int(overlap.sum()):,} overlapping rows (event_key)")
        store = store[~overlap]

    return pd.concat([store, new], ignore_index=True)


# --- Person / visit derivation (metrics only — source columns untouched) -----
# The dashboard counts VISITS on the official AppInsights `session_id` (the
# company standard, so our numbers reconcile with other company reports). We
# keep our OWN 30-min reconstruction as `visit_id` alongside it for comparison
# (see reconcile_visit_session.py) — the real session_id has a ~30-min
# inactivity timeout but also renews within a sitting (~48% persistence below
# 30 min), so visit_id is the cleaner grouping; both are available.
#
# The real person lives in CustomProps.GPN (the `gpn` column). Since user_Id is
# an anonymous per-device id, Unique Visitors is counted on `person_id`.
#
# Source columns user_id / session_id are kept AS-IS. AFTER the full store is
# assembled we ADD:
#   person_id  = GPN where present, else 'anon:<user_id>' (Unique Visitors).
#   visit_id   = our 30-min-gap reconstruction per person (secondary/QA).
# time_on_page_sec / is_last_in_session are computed over the OFFICIAL
# session_id, so Avg. Session is consistent with the session_id-based visits.
#
# This runs on the WHOLE store every run (deterministic, safe under the upsert).
SESSION_GAP_MIN = 30


def derive_person_visit(df: pd.DataFrame) -> pd.DataFrame:
    if "timestamp" not in df.columns:
        print("  Person/visit: no timestamp column — skipping derivation")
        return df
    df = df.copy()
    n = len(df)

    # 1. person_id: GPN, else anonymous device id (never collapse anon → 1)
    if "gpn" in df.columns:
        person = df["gpn"].astype("string")
    else:
        person = pd.Series(pd.NA, index=df.index, dtype="string")
    gpn_present = int(person.notna().sum())
    if "user_id" in df.columns:
        person = person.fillna("anon:" + df["user_id"].astype("string").fillna(""))
    else:
        person = person.fillna("anon:unknown")
    df["person_id"] = person

    # 2. visit_id: OUR reconstruction — new visit when the person changes or
    #    after an inactivity gap. Kept for comparison; not what the dashboard
    #    counts (that is the official session_id).
    ts = pd.to_datetime(df["timestamp"], errors="coerce")
    order = df[["person_id"]].assign(_ts=ts).sort_values(
        ["person_id", "_ts"], kind="mergesort")
    gap_min = order["_ts"].diff().dt.total_seconds().div(60)
    new_visit = (order["person_id"] != order["person_id"].shift(1)) | (gap_min > SESSION_GAP_MIN) | gap_min.isna()
    visit_no = new_visit.cumsum()
    df["visit_id"] = (order["person_id"].astype(str) + "#" + visit_no.astype(str)).reindex(df.index)

    # 3. time-on-page over the OFFICIAL session_id (the dashboard's visit unit)
    if "session_id" in df.columns:
        order2 = df[["session_id"]].assign(_ts=ts).sort_values(
            ["session_id", "_ts"], kind="mergesort")
        nxt = order2.groupby("session_id", sort=False)["_ts"].shift(-1)
        tos = (nxt - order2["_ts"]).dt.total_seconds()
        tos = tos.where(tos <= 30 * 60)  # cap runaway gaps (tab left open), keep NaN
        df["time_on_page_sec"] = tos.reindex(df.index)
        df["is_last_in_session"] = nxt.isna().reindex(df.index)

    persons = df["person_id"].nunique()
    off_visits = df["session_id"].nunique() if "session_id" in df.columns else -1
    our_visits = df["visit_id"].nunique()
    anon = n - gpn_present
    print(f"  Derived: {persons:,} persons ({gpn_present:,}/{n:,} rows have GPN, "
          f"{anon:,} anonymous)")
    print(f"  Visits — OFFICIAL session_id: {off_visits:,} ({n/max(off_visits,1):.1f} "
          f"views/visit)  |  our visit_id (gap {SESSION_GAP_MIN}m): {our_visits:,} "
          f"({n/max(our_visits,1):.1f} views/visit)")
    if anon / max(n, 1) > 0.3:
        print(f"  WARNING: {anon/n:.0%} of views have no GPN — each anonymous view "
              "counts as its own visitor. Check CustomProps.GPN coverage at the source.")
    return df


def resolve_hr_path(hr_arg: str | None, no_hr: bool) -> Path | None:
    """--hr wins; otherwise input/*.parquet, then the SearchAnalytics default."""
    if no_hr:
        return None
    if hr_arg:
        return Path(hr_arg)
    local = sorted(INPUT_DIR.glob("*.parquet"))
    if local:
        return local[0]
    if DEFAULT_HR_PATH.exists():
        return DEFAULT_HR_PATH
    return None


def build_one(input_path: Path, hr_path: Path | None) -> pd.DataFrame:
    print(f"  {input_path.name}:")
    raw = read_input(input_path)
    print(f"    {len(raw):,} raw rows")

    expanded = flatten_appinsights(raw)
    clean = build_clean_table(expanded)

    if hr_path and hr_path.exists():
        clean = join_hr_data(clean, hr_path)
    else:
        print("    HR: skipped — hr_* columns will be absent")

    fact = build_fact_page_view(clean, source_file=input_path.name)
    dim_page = build_dim_page(clean)

    # Denormalise: one wide table the dashboard can query directly.
    page_attr_cols = [c for c in dim_page.columns if c != "page_id"]
    if not dim_page.empty and "page_id" in fact.columns:
        wide = fact.merge(dim_page, on="page_id", how="left", suffixes=("", "_dim"))
    else:
        wide = fact.copy()
        for c in page_attr_cols:
            wide[c] = pd.NA

    return wide


def build(input_paths: list[Path], hr_path: Path | None, site_name: str | None,
          site_id: str | None, url_contains: str | None) -> pd.DataFrame:
    parts = [build_one(p, hr_path) for p in input_paths]
    wide = parts[0] if len(parts) == 1 else pd.concat(parts, ignore_index=True)

    # Dedup on the composite event_key — exports may overlap in time, and
    # session-derived fields (time_on_page_sec) can differ per file for the
    # same view, so a full-row comparison would miss those. keep="last": files
    # are processed in sorted name order, so the later export wins.
    wide = add_event_key(wide)
    before = len(wide)
    wide = wide.drop_duplicates(subset=["event_key"], keep="last")
    if len(wide) < before:
        print(f"  Dedup within batch (event_key): dropped {before - len(wide):,} duplicate rows")

    # Diagnostic: quantify how non-unique AppInsights `id` actually is
    # (it is data-only, the upsert never relies on it).
    if "view_id" in wide.columns:
        shared = int(wide["view_id"].dropna().duplicated().sum())
        if shared:
            print(f"  Note: {shared:,} rows share an AppInsights id with a distinct "
                  "event — expected, id is not event-unique; upsert uses event_key.")

    # Language from PageURL
    url_col = "page_url" if "page_url" in wide.columns else None
    wide["language"] = wide[url_col].map(derive_language) if url_col else "Other"

    # Language-agnostic page key: collapses de/en/fr/it variants of one logical
    # page into a single row in the dashboard's Pages table / ranking / lifecycle
    # (the language filter still splits them). Falls back to page_id.
    if url_col:
        wide["page_key"] = wide[url_col].map(canonical_page_key)
        if "page_id" in wide.columns:
            wide["page_key"] = wide["page_key"].fillna(wide["page_id"].astype("string"))
    elif "page_id" in wide.columns:
        wide["page_key"] = wide["page_id"].astype("string")

    # Site filter — usually the KQL already scoped by PageURL, so these are
    # optional. --url-contains mirrors the KQL PageUrlFilter for local re-filtering.
    before = len(wide)
    if url_contains and url_col:
        wide = wide[wide[url_col].astype("string").str.contains(url_contains, case=False, na=False)]
    elif site_name and "site_name" in wide.columns:
        wide = wide[wide["site_name"].astype("string").str.casefold() == site_name.casefold()]
    elif site_id and "site_id" in wide.columns:
        wide = wide[wide["site_id"].astype("string").str.casefold() == site_id.casefold()]
    if url_contains or site_name or site_id:
        print(f"  Site filter: {len(wide):,}/{before:,} rows kept")

    if wide.empty:
        print("  WARNING: 0 rows after site filter — check --site / --site-id value")

    sites = sorted(wide["site_name"].dropna().unique()) if "site_name" in wide.columns else []
    print(f"  Sites in output: {', '.join(map(str, sites)) or '(none)'}")
    print(f"  Languages: "
          + ", ".join(f"{k}={v}" for k, v in wide['language'].value_counts().items()))

    return strip_tz(wide.reset_index(drop=True))


def main():
    ap = argparse.ArgumentParser(description="Build single-site Parquet for the dashboard")
    ap.add_argument("input", nargs="*",
                    help="CSV/XLSX exported via export_site_pageviews.kql "
                    "(default: all CSV/XLSX files in input/)")
    ap.add_argument("--url-contains", help="Keep rows whose PageURL contains this substring "
                    "(mirrors the KQL PageUrlFilter; usually already applied at export)")
    ap.add_argument("--site", help="SiteName to keep (case-insensitive exact match)")
    ap.add_argument("--site-id", help="SiteID to keep (alternative to --site)")
    ap.add_argument("--hr", help="Path to hr_history.parquet (default: auto-detect "
                    "input/*.parquet, then ../SearchAnalytics/output/hr_history.parquet)")
    ap.add_argument("--no-hr", action="store_true", help="Skip HR enrichment even if a parquet is found")
    ap.add_argument("--rebuild", action="store_true",
                    help="Ignore the manifest and reprocess ALL input files from scratch "
                    "(use after changing --site / --url-contains)")
    ap.add_argument("-o", "--output", help="Output parquet (default: output/site_pageviews.parquet)")
    args = ap.parse_args()

    out_path = Path(args.output) if args.output else PROJECT_DIR / "output" / "site_pageviews.parquet"
    out_path.parent.mkdir(parents=True, exist_ok=True)
    mpath = manifest_path(out_path)
    manifest = {} if args.rebuild else load_manifest(mpath)
    if args.rebuild:
        print("Rebuild: manifest ignored, processing all files from scratch")
    # Manifest without a store parquet (deleted/moved) — start over.
    if manifest and not out_path.exists():
        print(f"  {out_path.name} missing — ignoring manifest, reprocessing everything")
        manifest = {}

    if args.input:
        input_paths = [Path(p) for p in args.input]
        missing = [p for p in input_paths if not p.exists()]
        if missing:
            sys.exit("Input not found: " + ", ".join(map(str, missing)))
        # Explicitly named files are always processed (no hash skip).
        to_process = [(p, compute_file_hash(p), "forced") for p in input_paths]
        skipped = []
    else:
        input_paths = discover_inputs()
        if not input_paths:
            sys.exit(f"No CSV/XLSX files found in {INPUT_DIR} — drop your "
                     "AppInsights export(s) there or pass a path explicitly.")
        to_process, skipped = partition_files(input_paths, manifest)

    if skipped:
        print(f"Skipping {len(skipped)} unchanged file(s): {', '.join(skipped)}")
    if not to_process:
        print(f"Up to date — nothing new to process ({out_path.name} unchanged).")
        return

    hr_path = resolve_hr_path(args.hr, args.no_hr)
    if hr_path:
        print(f"HR parquet: {hr_path}")

    print(f"Processing {len(to_process)} file(s): "
          + ", ".join(f"{p.name} ({reason})" for p, _, reason in to_process))
    new = build([p for p, _, _ in to_process], hr_path,
                args.site, args.site_id, args.url_contains)

    store = None
    if out_path.exists() and not args.rebuild:
        store = pd.read_parquet(out_path)
        print(f"  Existing store: {len(store):,} rows")
    wide = upsert_store(store, new, [p.name for p, _, _ in to_process])
    # Derive person_id / visit_id on the FULL store (cross-file visits) — source
    # user_id / session_id are left untouched.
    wide = derive_person_visit(wide)
    wide.to_parquet(out_path, index=False)

    now = datetime.now(timezone.utc).isoformat(timespec="seconds")
    rows_per_file = (new["source_file"].value_counts().to_dict()
                     if "source_file" in new.columns else {})
    for p, file_hash, _ in to_process:
        manifest[p.name] = {"sha256": file_hash,
                            "rows": int(rows_per_file.get(p.name, 0)),
                            "processed_at": now}
    save_manifest(mpath, manifest)

    print(f"\nWrote {len(wide):,} rows ({len(new):,} from this run) -> {out_path}")
    print("Open the dashboard:  python -m http.server 8000  ->  "
          "http://localhost:8000/dashboard/dashboard.html")


if __name__ == "__main__":
    main()
