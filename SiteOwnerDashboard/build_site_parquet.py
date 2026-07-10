"""
Build a single-site Parquet for the SiteOwnerDashboard.

Thin wrapper around ../scripts/flatten_appinsights.py: reuses the exact same
customDimensions flatten, UTC->CET, GPN-normalise, CammsTrackingID-split and
temporal HR-join logic, then

  1. joins the fact with its page dimension (denormalised — one wide table),
  2. derives `language` from the PageURL (/en/ /de/ /fr/ /it/ segment),
  3. filters to ONE site (SiteName or SiteID),

and writes SiteOwnerDashboard/data/site_pageviews.parquet — the single file the
standalone dashboard loads via DuckDB-WASM.

Usage:
    # Site owner: export their site via export_site_pageviews.kql, then:
    python build_site_parquet.py data/news_and_events.csv --site "News and events"

    # By SiteID instead of name:
    python build_site_parquet.py data/export.csv --site-id 5313b145-...

    # HR enrichment (division/region donut). Optional — omit to skip:
    python build_site_parquet.py data/export.csv --site "..." --hr /path/hr_history.parquet

    # No --site given: keeps every site present in the export (use when the KQL
    # already scoped to one site).

Output: data/site_pageviews.parquet
"""

from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

import pandas as pd

# Reuse the canonical pipeline from ../scripts/flatten_appinsights.py
SCRIPT_DIR = Path(__file__).resolve().parent
PIPELINE_DIR = SCRIPT_DIR.parent / "scripts"
if str(PIPELINE_DIR) not in sys.path:
    sys.path.insert(0, str(PIPELINE_DIR))

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
# Intranet URLs carry the content language as a path segment, e.g.
#   https://intranet/sites/news/en/2026/q1-results  -> EN
# Matched case-insensitively as a standalone /xx/ segment. Anything else -> "Other".
LANG_SEGMENTS = {"en": "EN", "de": "DE", "fr": "FR", "it": "IT"}
_LANG_RE = re.compile(r"/(en|de|fr|it)(?=/|$|\?|#)", re.IGNORECASE)


def derive_language(url) -> str:
    if not isinstance(url, str) or not url:
        return "Other"
    m = _LANG_RE.search(url)
    return LANG_SEGMENTS[m.group(1).lower()] if m else "Other"


def build(input_path: Path, hr_path: Path | None, site_name: str | None,
          site_id: str | None, url_contains: str | None) -> pd.DataFrame:
    raw = read_input(input_path)
    print(f"  {len(raw):,} raw rows")

    expanded = flatten_appinsights(raw)
    clean = build_clean_table(expanded)

    if hr_path and hr_path.exists():
        clean = join_hr_data(clean, hr_path)
    else:
        print("  HR: skipped (no --hr / file missing) — hr_* columns will be absent")

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

    # Language from PageURL
    url_col = "page_url" if "page_url" in wide.columns else None
    wide["language"] = wide[url_col].map(derive_language) if url_col else "Other"

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
    ap.add_argument("input", help="CSV/XLSX exported via export_site_pageviews.kql")
    ap.add_argument("--url-contains", help="Keep rows whose PageURL contains this substring "
                    "(mirrors the KQL PageUrlFilter; usually already applied at export)")
    ap.add_argument("--site", help="SiteName to keep (case-insensitive exact match)")
    ap.add_argument("--site-id", help="SiteID to keep (alternative to --site)")
    ap.add_argument("--hr", help="Path to hr_history.parquet (optional, enables division donut)")
    ap.add_argument("-o", "--output", help="Output parquet (default: data/site_pageviews.parquet)")
    args = ap.parse_args()

    input_path = Path(args.input)
    if not input_path.exists():
        sys.exit(f"Input not found: {input_path}")

    hr_path = Path(args.hr) if args.hr else None
    out_path = Path(args.output) if args.output else SCRIPT_DIR / "data" / "site_pageviews.parquet"
    out_path.parent.mkdir(parents=True, exist_ok=True)

    print(f"Building from {input_path.name} ...")
    wide = build(input_path, hr_path, args.site, args.site_id, args.url_contains)
    wide.to_parquet(out_path, index=False)
    print(f"\nWrote {len(wide):,} rows -> {out_path}")
    print("Open the dashboard:  python -m http.server 8000  ->  http://localhost:8000/index.html")


if __name__ == "__main__":
    main()
