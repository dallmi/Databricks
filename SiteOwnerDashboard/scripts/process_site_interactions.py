"""
Build the interactions Parquet (Phase 2) for the SiteOwnerDashboard.

Pendant to process_site_pageviews.py for the customEvents stream
(name == 'click_event': link clicks, downloads, CTAs, video actions).
Reuses the shared pipeline from ../../scripts/flatten_appinsights.py
(customDimensions double-parse, UTC->CET, GPN normalise, temporal HR join)
and the site helpers from process_site_pageviews.py (language + page_key
derivation, SHA-256 manifest, event_key upsert), then writes
SiteOwnerDashboard/output/site_interactions.parquet — the optional second
Parquet the dashboard loads as view `ix`.

Join contract with site_pageviews.parquet (see DESIGN.md):
  - Both parquets stay at EVENT grain and carry the same three page keys:
    page_id (language variant, GUID), page_url (display), page_key
    (language-stripped canonical URL — the logical page).
  - The dashboard joins per-view aggregates at query time on the grain of the
    current view (page_key for rankings, page_id for the language drill), so
    COUNT(DISTINCT person_id) is always recomputed — never summed.

Incremental processing (same pattern as process_site_pageviews.py):
  - Raw exports go in input/interactions/ (kept separate from the pageViews
    exports in input/ so neither script picks up the other's files).
  - SHA-256 manifest (output/site_interactions.manifest.json) skips unchanged
    files; changed files fully replace the rows they contributed.
  - Upsert key `event_key` = second-truncated timestamp + user_id + session_id
    + page_id + component_name + link_address + link_label — the link identity
    is part of the key so two different-link clicks by the same person in the
    same second stay distinct. (A double-click on the SAME link within one
    second collapses — accepted, same class of limitation as CampaignWe.)
  - --rebuild wipes the manifest and reprocesses everything from scratch.

Derived columns (source columns stay untouched):
  - interaction_class: 'download' (file_name_label/file_type_label present)
    -> 'video' (video_action present) -> else 'link'.
  - language, page_key: identical derivation to the pageViews build.
  - person_id: GPN where present, else 'anon:<user_id>'.

Usage (from the SiteOwnerDashboard project root):
    # Default: processes new/changed CSV/XLSX files in input/interactions/:
    python scripts/process_site_interactions.py

    # Explicit file(s) skip the hash check (forced), but still upsert:
    python scripts/process_site_interactions.py input/interactions/<export>.csv

    # Same site/HR options as the pageViews build:
    python scripts/process_site_interactions.py --site "News and events"
    python scripts/process_site_interactions.py --hr /path/hr_history.parquet
    python scripts/process_site_interactions.py --no-hr
    python scripts/process_site_interactions.py --rebuild

Output: output/site_interactions.parquet
"""

from __future__ import annotations

import argparse
import sys
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd

SCRIPT_DIR = Path(__file__).resolve().parent
PROJECT_DIR = SCRIPT_DIR.parents[0]

# Shared site helpers (language, page_key, manifest, event_key, upsert) come
# from the pageViews build script next to this one — single implementation.
sys.path.insert(0, str(SCRIPT_DIR))
from process_site_pageviews import (  # noqa: E402
    derive_language,
    canonical_page_key,
    manifest_path,
    compute_file_hash,
    load_manifest,
    save_manifest,
    partition_files,
    add_event_key,
    upsert_store,
    resolve_hr_path,
)

# process_site_pageviews already resolved the shared pipeline directory
# (repo layout Databricks/scripts/, or a standalone copy) onto sys.path.
from flatten_appinsights import (  # noqa: E402
    read_input,
    flatten_appinsights,
    build_clean_interactions_table,
    join_hr_data,
    strip_tz,
)


INPUT_DIR = PROJECT_DIR / "input" / "interactions"

# Upsert key: link identity included (see module docstring).
IX_KEY_COLS = ["user_id", "session_id", "page_id",
               "component_name", "link_address", "link_label"]

# Denormalised output columns (kept when present). hr_* columns are appended
# dynamically. One wide table, page attributes inline per event row.
IX_COLUMNS = [
    "timestamp", "event_name",
    "page_id", "page_key", "page_name", "page_url", "language",
    "site_id", "site_name",
    "content_owner", "content_type", "theme", "topic",
    "target_region", "target_org", "page_status", "publishing_date",
    "user_id", "session_id", "person_id", "email", "gpn",
    "client_os", "client_browser", "client_country", "referrer_url",
    "component_name", "link_type", "link_label", "link_address", "link_ancestors",
    "file_type_label", "file_name_label",
    "video_action", "video_id", "video_title", "video_type", "video_duration",
    "video_played_time", "video_address",
    "interaction_class", "source_file",
]

PV_STORE = PROJECT_DIR / "output" / "site_pageviews.parquet"


def _present(series: pd.Series) -> pd.Series:
    """True where a string column carries a real value (not NA/blank/'nan')."""
    s = series.astype("string")
    return s.notna() & ~s.str.strip().isin(["", "nan", "None", "null"])


def classify_interactions(df: pd.DataFrame) -> pd.DataFrame:
    """ADD interaction_class — download > video > link (source cols untouched)."""
    df = df.copy()
    cls = pd.Series("link", index=df.index, dtype="string")
    if "video_action" in df.columns:
        cls = cls.mask(_present(df["video_action"]), "video")
    is_download = pd.Series(False, index=df.index)
    for c in ("file_name_label", "file_type_label"):
        if c in df.columns:
            is_download |= _present(df[c])
    cls = cls.mask(is_download, "download")
    df["interaction_class"] = cls
    return df


def derive_person(df: pd.DataFrame) -> pd.DataFrame:
    """ADD person_id = GPN where present, else 'anon:<user_id>' (same as pv)."""
    df = df.copy()
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
    n = len(df)
    print(f"  Persons: {df['person_id'].nunique():,} distinct "
          f"({gpn_present:,}/{n:,} rows have GPN)")
    if n and (n - gpn_present) / n > 0.3:
        print(f"  WARNING: {(n - gpn_present)/n:.0%} of interactions have no GPN — "
              "each anonymous click counts as its own clicker.")
    return df


def discover_inputs() -> list[Path]:
    """All CSV/XLSX exports in input/interactions/, skipping hidden/lock files."""
    if not INPUT_DIR.exists():
        return []
    return [p for p in sorted(INPUT_DIR.glob("*"))
            if p.suffix.lower() in (".csv", ".xlsx", ".xls")
            and not p.name.startswith((".", "~$"))]


def looks_like_interactions(raw: pd.DataFrame, name: str) -> bool:
    """Guard against a pageViews export dropped into input/interactions/."""
    if "name" in raw.columns and raw["name"].astype(str).eq("click_event").any():
        return True
    print(f"  WARNING: {name} has no click_event rows — this looks like a "
          "pageViews export. Skipping it (pageViews exports go in input/, "
          "not input/interactions/).")
    return False


def build_one(input_path: Path, hr_path: Path | None) -> pd.DataFrame | None:
    print(f"  {input_path.name}:")
    raw = read_input(input_path)
    print(f"    {len(raw):,} raw rows")
    if not looks_like_interactions(raw, input_path.name):
        return None

    expanded = flatten_appinsights(raw)
    clean = build_clean_interactions_table(expanded)

    # click_event only — other families (SEARCH_*) have a different schema and
    # are out of scope for this dashboard phase.
    if "event_name" in clean.columns:
        counts = clean["event_name"].value_counts()
        others = int(counts.drop("click_event", errors="ignore").sum())
        if others:
            print(f"    Event families: "
                  + ", ".join(f"{k}={v:,}" for k, v in counts.items())
                  + f" — keeping click_event only ({others:,} rows dropped)")
            clean = clean[clean["event_name"] == "click_event"]

    if hr_path and hr_path.exists():
        clean = join_hr_data(clean, hr_path)
    else:
        print("    HR: skipped — hr_* columns will be absent")

    clean["source_file"] = input_path.name
    return clean


def build(input_paths: list[Path], hr_path: Path | None, site_name: str | None,
          site_id: str | None, url_contains: str | None) -> pd.DataFrame:
    parts = [b for b in (build_one(p, hr_path) for p in input_paths) if b is not None]
    if not parts:
        sys.exit("No processable interactions export found — nothing to do.")
    wide = parts[0] if len(parts) == 1 else pd.concat(parts, ignore_index=True)

    # Dedup on the composite event_key (exports may overlap); keep="last":
    # files are processed in sorted name order, the later export wins.
    wide = add_event_key(wide, IX_KEY_COLS)
    before = len(wide)
    wide = wide.drop_duplicates(subset=["event_key"], keep="last")
    if len(wide) < before:
        print(f"  Dedup within batch (event_key): dropped {before - len(wide):,} duplicate rows")

    # Language + language-agnostic page key — identical to the pageViews build,
    # so the two parquets join cleanly on page_key / language filters.
    url_col = "page_url" if "page_url" in wide.columns else None
    wide["language"] = wide[url_col].map(derive_language) if url_col else "Other"
    if url_col:
        wide["page_key"] = wide[url_col].map(canonical_page_key)
        if "page_id" in wide.columns:
            wide["page_key"] = wide["page_key"].fillna(wide["page_id"].astype("string"))
    elif "page_id" in wide.columns:
        wide["page_key"] = wide["page_id"].astype("string")

    wide = classify_interactions(wide)

    # Site filter — usually the KQL already scoped by PageURL; optional here.
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

    # Trim to the documented schema (keeps hr_* and event_key).
    keep = [c for c in IX_COLUMNS if c in wide.columns]
    keep += [c for c in wide.columns if c.startswith("hr_")]
    keep.append("event_key")
    seen = set()
    keep = [c for c in keep if not (c in seen or seen.add(c))]
    wide = wide[keep]

    if "interaction_class" in wide.columns:
        print("  Interaction classes: "
              + ", ".join(f"{k}={v:,}" for k, v in wide["interaction_class"].value_counts().items()))
    if "link_type" in wide.columns and "interaction_class" in wide.columns:
        xt = (wide.groupby(["interaction_class", "link_type"], dropna=False)
              .size().sort_values(ascending=False).head(12))
        print("  Link_Type x class (top 12):")
        for (cls, lt), cnt in xt.items():
            print(f"    {str(cls):10s} {str(lt):30s} {cnt:>10,}")

    return strip_tz(wide.reset_index(drop=True))


def report_pv_overlap(ix: pd.DataFrame) -> None:
    """QA: how many clicks land on pages the pv store knows about?"""
    if not PV_STORE.exists() or "page_key" not in ix.columns:
        return
    try:
        pv_keys = set(pd.read_parquet(PV_STORE, columns=["page_key"])["page_key"].dropna())
    except (KeyError, OSError, ValueError):
        print("  QA: pv store has no page_key column — skipping overlap check")
        return
    ix_keys = ix["page_key"].dropna()
    orphan_mask = ~ix_keys.isin(pv_keys)
    orphans = int(orphan_mask.sum())
    pct = orphans / max(len(ix_keys), 1) * 100
    print(f"  QA: {len(ix_keys) - orphans:,}/{len(ix_keys):,} clicks match a pv page_key "
          f"({orphans:,} orphans, {pct:.1f}%)")
    if pct > 10:
        print("  WARNING: >10% orphan clicks — the interactions export likely covers "
              "a different time window or site scope than the pageViews export.")


def main():
    ap = argparse.ArgumentParser(description="Build interactions Parquet (Phase 2) for the dashboard")
    ap.add_argument("input", nargs="*",
                    help="CSV/XLSX exported via export_site_interactions.kql "
                    "(default: all CSV/XLSX files in input/interactions/)")
    ap.add_argument("--url-contains", help="Keep rows whose PageURL contains this substring")
    ap.add_argument("--site", help="SiteName to keep (case-insensitive exact match)")
    ap.add_argument("--site-id", help="SiteID to keep (alternative to --site)")
    ap.add_argument("--hr", help="Path to hr_history.parquet (default: auto-detect like the pv build)")
    ap.add_argument("--no-hr", action="store_true", help="Skip HR enrichment even if a parquet is found")
    ap.add_argument("--rebuild", action="store_true",
                    help="Ignore the manifest and reprocess ALL input files from scratch")
    ap.add_argument("-o", "--output", help="Output parquet (default: output/site_interactions.parquet)")
    args = ap.parse_args()

    out_path = Path(args.output) if args.output else PROJECT_DIR / "output" / "site_interactions.parquet"
    out_path.parent.mkdir(parents=True, exist_ok=True)
    mpath = manifest_path(out_path)
    manifest = {} if args.rebuild else load_manifest(mpath)
    if args.rebuild:
        print("Rebuild: manifest ignored, processing all files from scratch")
    if manifest and not out_path.exists():
        print(f"  {out_path.name} missing — ignoring manifest, reprocessing everything")
        manifest = {}

    if args.input:
        input_paths = [Path(p) for p in args.input]
        missing = [p for p in input_paths if not p.exists()]
        if missing:
            sys.exit("Input not found: " + ", ".join(map(str, missing)))
        to_process = [(p, compute_file_hash(p), "forced") for p in input_paths]
        skipped = []
    else:
        input_paths = discover_inputs()
        if not input_paths:
            sys.exit(f"No CSV/XLSX files found in {INPUT_DIR} — drop your "
                     "customEvents export(s) there (export_site_interactions.kql) "
                     "or pass a path explicitly.")
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
    wide = upsert_store(store, new, [p.name for p, _, _ in to_process], IX_KEY_COLS)
    # person_id on the FULL store (deterministic, safe under the upsert).
    wide = derive_person(wide)
    report_pv_overlap(wide)
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
