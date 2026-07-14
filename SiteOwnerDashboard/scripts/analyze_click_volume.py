"""
Analyze raw customEvents (click_event) exports in input/interactions/ to find
which clicks are volume noise rather than a real content-engagement signal —
evidence for narrowing export_site_interactions.kql before the next export.

Reads every CSV/XLSX chunk in input/interactions/ (read-only — does not
touch output/site_interactions.parquet or its manifest), flattens them the
same way process_site_interactions.py does, and answers two questions per
ComponentName / Link_Type / (ComponentName, Link_label) combination:

  1. Row share  — what fraction of your exported rows does this account for?
  2. Page coverage — what fraction of distinct pages (page_key) does it show
     up on? A component clicked from ~every page (header nav, footer, cookie
     banner, social-share widget, language switcher, ...) is site-wide chrome,
     not a content signal — exactly the kind of row this dashboard has no use
     for, and the kind of row that's cheap to filter out at the KQL source
     with `| where ComponentName !in (...)`.

High row share + high page coverage = strong candidate to exclude. The
script prints a ready-to-paste KQL snippet for whatever crosses the
--coverage-threshold, plus the row-count savings that filter would have
bought on the data you already downloaded.

Usage (from SiteOwnerDashboard/):
    # All CSV/XLSX files in input/interactions/ (default)
    python scripts/analyze_click_volume.py

    # Explicit file(s)
    python scripts/analyze_click_volume.py input/interactions/Digital_ce_*.csv

    # Show more/fewer rows per table, tune the "site-wide chrome" cutoff
    python scripts/analyze_click_volume.py --top 40 --coverage-threshold 0.5

    # Also write the full component breakdown to CSV for a closer look
    python scripts/analyze_click_volume.py --csv-out output/click_volume_by_component.csv
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

import pandas as pd

# Force line-buffered stdout: on some Windows consoles a fully-buffered pipe
# means a crash before the buffer fills shows neither output nor a traceback.
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(line_buffering=True)

SCRIPT_DIR = Path(__file__).resolve().parent
PROJECT_DIR = SCRIPT_DIR.parents[0]
sys.path.insert(0, str(SCRIPT_DIR))
from process_site_pageviews import canonical_page_key  # noqa: E402
from process_site_interactions import classify_interactions  # noqa: E402
from flatten_appinsights import (  # noqa: E402
    read_input,
    flatten_appinsights,
    build_clean_interactions_table,
)

INPUT_DIR = PROJECT_DIR / "input" / "interactions"
DEFAULT_TOP = 25
DEFAULT_COVERAGE_THRESHOLD = 0.6


def hdr(title: str) -> None:
    print(f"\n=== {title} " + "=" * max(0, 70 - len(title)))


def discover_inputs() -> list[Path]:
    if not INPUT_DIR.exists():
        return []
    return [p for p in sorted(INPUT_DIR.glob("*"))
            if p.suffix.lower() in (".csv", ".xlsx", ".xls")
            and not p.name.startswith((".", "~$"))]


def load_all(paths: list[Path]) -> pd.DataFrame:
    parts = []
    for p in paths:
        raw = read_input(p)
        clean = build_clean_interactions_table(flatten_appinsights(raw))
        if "event_name" in clean.columns:
            others = clean[clean["event_name"] != "click_event"]
            if not others.empty:
                print(f"  {p.name}: dropping {len(others):,} non-click_event rows "
                      f"({', '.join(f'{k}={v}' for k, v in others['event_name'].value_counts().items())})")
                clean = clean[clean["event_name"] == "click_event"]
        clean["source_file"] = p.name
        print(f"  {p.name}: {len(clean):,} click_event rows")
        parts.append(clean)
    if not parts:
        sys.exit(f"No processable exports found — nothing to analyze.")
    return pd.concat(parts, ignore_index=True)


def add_page_key(df: pd.DataFrame) -> pd.DataFrame:
    df = df.copy()
    if "page_url" in df.columns:
        df["page_key"] = df["page_url"].map(canonical_page_key)
        if "page_id" in df.columns:
            df["page_key"] = df["page_key"].fillna(df["page_id"].astype("string"))
    elif "page_id" in df.columns:
        df["page_key"] = df["page_id"].astype("string")
    else:
        df["page_key"] = pd.NA
    return df


def pct(n, d) -> str:
    return f"{n / d * 100:5.1f}%" if d else "    –"


def coverage_table(df: pd.DataFrame, group_col: str | list[str], total_rows: int,
                    total_pages: int, top: int) -> pd.DataFrame:
    """Row share + page coverage per value (or value-combo) of group_col."""
    grp = df.groupby(group_col, dropna=False)
    out = grp.size().rename("rows").reset_index()
    out["row_share"] = out["rows"] / total_rows
    if total_pages:
        pages_per = grp["page_key"].nunique()
        out = out.merge(pages_per.rename("distinct_pages").reset_index(), on=group_col)
        out["page_coverage"] = out["distinct_pages"] / total_pages
    out = out.sort_values("rows", ascending=False).reset_index(drop=True)
    out["cum_row_share"] = out["row_share"].cumsum()
    return out.head(top)


def print_crosstab(df: pd.DataFrame, row_col: str, col_col: str,
                    row_order: list | None = None, as_share: bool = False) -> None:
    """Print a row x col count (or row-normalised %) matrix, columns by total volume."""
    ct = pd.crosstab(df[row_col], df[col_col])
    col_order = ct.sum(axis=0).sort_values(ascending=False).index
    ct = ct[col_order]
    if row_order is not None:
        ct = ct.reindex([r for r in row_order if r in ct.index])
    if as_share:
        ct = ct.div(ct.sum(axis=1), axis=0) * 100

    col_w = 9
    print(f"{row_col:32s}" + "".join(f"{str(c)[:col_w-1]:>{col_w}s}" for c in ct.columns))
    for idx, row in ct.iterrows():
        label = str(idx)
        label = (label[:29] + "...") if len(label) > 32 else label
        if as_share:
            cells = "".join(f"{v:>{col_w-1}.0f}%" for v in row)
        else:
            cells = "".join(f"{int(v):>{col_w},}" for v in row)
        print(f"{label:32s}{cells}")


def print_component_table(table: pd.DataFrame, label_col: str) -> None:
    has_cov = "page_coverage" in table.columns
    head = f"{label_col:32s} {'rows':>10s} {'share':>7s} {'cum':>7s}"
    if has_cov:
        head += f" {'pages':>7s} {'coverage':>9s}"
    print(head)
    for _, r in table.iterrows():
        label = str(r[label_col])
        label = (label[:29] + "...") if len(label) > 32 else label
        line = f"{label:32s} {r['rows']:>10,} {r['row_share']*100:>6.1f}% {r['cum_row_share']*100:>6.1f}%"
        if has_cov:
            line += f" {int(r['distinct_pages']):>7,} {r['page_coverage']*100:>8.1f}%"
        print(line)


def main() -> int:
    ap = argparse.ArgumentParser(description="Profile click_event export volume — what's noise vs. content signal")
    ap.add_argument("input", nargs="*", help="CSV/XLSX file(s) to analyze (default: all files in input/interactions/)")
    ap.add_argument("--top", type=int, default=DEFAULT_TOP, help=f"Rows per table (default {DEFAULT_TOP})")
    ap.add_argument("--coverage-threshold", type=float, default=DEFAULT_COVERAGE_THRESHOLD,
                     help=f"Page-coverage ratio above which a component is flagged as site-wide chrome "
                          f"(default {DEFAULT_COVERAGE_THRESHOLD})")
    ap.add_argument("--csv-out", help="Optional path to write the full component breakdown as CSV")
    args = ap.parse_args()

    paths = [Path(p) for p in args.input] if args.input else discover_inputs()
    if not paths:
        sys.exit(f"No CSV/XLSX files found in {INPUT_DIR} — nothing to analyze.")
    missing = [p for p in paths if not p.exists()]
    if missing:
        sys.exit("Input not found: " + ", ".join(map(str, missing)))

    hdr(f"Loading {len(paths)} file(s)")
    df = add_page_key(load_all(paths))
    total_rows = len(df)
    total_pages = df["page_key"].nunique()

    hdr("Overview")
    print(f"  Rows (click_event): {total_rows:,}")
    print(f"  Distinct pages (page_key): {total_pages:,}")
    for col in ["component_name", "link_type", "session_id", "user_id"]:
        if col in df.columns:
            print(f"  Distinct {col}: {df[col].nunique():,}")

    if "component_name" not in df.columns:
        print("\nNo component_name column — cannot compute chrome candidates. Stopping here.")
        return 0

    hdr(f"Top {args.top} ComponentName by row volume")
    comp_table = coverage_table(df, "component_name", total_rows, total_pages, args.top)
    print_component_table(comp_table, "component_name")

    if "link_type" in df.columns:
        hdr("Link_Type breakdown")
        lt_table = coverage_table(df, "link_type", total_rows, total_pages, args.top)
        print_component_table(lt_table, "link_type")

        df = classify_interactions(df)  # adds interaction_class: download/video/link
        hdr("interaction_class x Link_Type — does span/div carry downloads or video?")
        print("  (row counts — a nonzero cell outside 'link' means that Link_Type")
        print("   tags real download/video interactions, not just chrome wrappers)")
        print_crosstab(df, "interaction_class", "link_type")

        top_components = comp_table["component_name"].head(min(args.top, 15)).tolist()
        hdr(f"Link_Type mix inside the top {len(top_components)} ComponentName (row %)")
        print("  If span/div sit almost entirely inside nav/header/footer-looking rows")
        print("  here, filtering them is low-risk. If they also dominate a content")
        print("  component (e.g. your rich-text editor), filtering them is NOT.")
        print_crosstab(df[df["component_name"].isin(top_components)],
                       "component_name", "link_type", row_order=top_components, as_share=True)

    if "link_label" in df.columns:
        hdr(f"Top {args.top} (ComponentName, Link_label) combos by row volume")
        combo_table = coverage_table(df, ["component_name", "link_label"], total_rows, total_pages, args.top)
        for _, r in combo_table.iterrows():
            comp = str(r["component_name"])[:22]
            label = str(r["link_label"])[:32]
            cov = f"{r['page_coverage']*100:6.1f}%" if "page_coverage" in combo_table.columns else "     –"
            print(f"  {comp:22s} | {label:32s} {r['rows']:>10,} {r['row_share']*100:>6.1f}%  cov={cov}")

    hdr(f"Page-coverage >= {args.coverage_threshold*100:.0f}% (REVIEW, not auto-exclude)")
    full_comp = coverage_table(df, "component_name", total_rows, total_pages, top=len(df["component_name"].unique()))
    candidates = full_comp[full_comp.get("page_coverage", 0) >= args.coverage_threshold]
    if candidates.empty:
        print("  None — no component shows up on that large a share of pages. "
              "Either your site is small, or nothing here looks like pure navigation chrome.")
    else:
        print("  High page coverage is AMBIGUOUS, not a verdict: it also matches a universal")
        print("  content widget (a rich-text/body editor instantiated on nearly every page) —")
        print("  excluding one of those silently deletes real content clicks, not noise.")
        print("  Check every name below against your site's component list before excluding")
        print("  anything. Names like '...Header...', '...Footer...', '...Nav...', '...Chrome...',")
        print("  '...CookieBanner...' are safe bets; '...Editor...', '...Text...', '...Content...' are not.\n")
        print_component_table(candidates, "component_name")
        savings_rows = int(candidates["rows"].sum())
        print(f"\n  If ALL of these were navigation/chrome, excluding them would drop "
              f"{savings_rows:,}/{total_rows:,} rows ({savings_rows/total_rows*100:.1f}%) "
              "from this export — treat that number as an upper bound, not a target.")
        names = "\", \"".join(str(v) for v in candidates["component_name"])
        print("\n  Once you've pruned the list above to genuine chrome, paste into")
        print("  export_site_interactions.kql right after the click_event filter:")
        print(f'    | where ComponentName !in ("{names}")')
        print("  (extend cp = parse_json(...) first if ComponentName isn't already in scope there —")
        print("   see kql/customevents_clicks.kql QUERY 0 for the full extend/project pattern.)")

    if args.csv_out:
        out_path = Path(args.csv_out)
        out_path.parent.mkdir(parents=True, exist_ok=True)
        full_comp.to_csv(out_path, index=False)
        print(f"\nFull component breakdown written to {out_path}")

    print("\nDone.")
    return 0


if __name__ == "__main__":
    # Belt-and-braces: guarantee SOME output even on an exception class that
    # would otherwise print nothing on this console (seen once on Windows —
    # command returned to the prompt with neither a result nor a traceback).
    try:
        sys.exit(main())
    except SystemExit:
        raise
    except BaseException:
        import traceback
        traceback.print_exc()
        sys.exit(1)
