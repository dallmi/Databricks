"""
Diagnose the interactions store against the pageViews store — READ-ONLY.

Companion to diagnose_exports.py for Phase 2. Answers, before anyone trusts
the Content & Interactions tab:

  1. What is actually in site_interactions.parquet — event families, interaction
     classes, Link_Type distribution, GPN / PageURL coverage?
  2. How well do the two stores JOIN — page_key overlap (the dashboard's
     content grain), page_id overlap (the language-variant drill), and
     person_id overlap (clicker vs. viewer populations)?
  3. Is a session-level funnel (view -> click in the same visit) feasible —
     session_id / user_id overlap between the streams? (Not used by the
     dashboard yet; this is the go/no-go evidence for that idea.)

Usage (from the SiteOwnerDashboard project root):
    python scripts/diagnose_interactions.py
    python scripts/diagnose_interactions.py --ix output/site_interactions.parquet \
        --pv output/site_pageviews.parquet

Raw exports (CSV/XLSX from export_site_interactions.kql) can be inspected
before building, too:
    python scripts/diagnose_interactions.py --raw input/interactions/<export>.csv
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

import pandas as pd

SCRIPT_DIR = Path(__file__).resolve().parent
PROJECT_DIR = SCRIPT_DIR.parents[0]
sys.path.insert(0, str(SCRIPT_DIR))


def hdr(title: str) -> None:
    print(f"\n=== {title} " + "=" * max(0, 60 - len(title)))


def pct(n: int, d: int) -> str:
    return f"{n / d * 100:.1f}%" if d else "–"


def coverage(df: pd.DataFrame, col: str) -> str:
    if col not in df.columns:
        return "column absent"
    s = df[col].astype("string")
    present = int((s.notna() & ~s.str.strip().isin(["", "nan", "None", "null"])).sum())
    return f"{present:,}/{len(df):,} ({pct(present, len(df))})"


def value_counts(df: pd.DataFrame, col: str, top: int = 10) -> None:
    if col not in df.columns:
        print(f"  {col}: column absent")
        return
    vc = df[col].value_counts(dropna=False).head(top)
    for k, v in vc.items():
        print(f"  {str(k):40s} {v:>10,}")


def overlap(a: pd.Series, b: pd.Series, label: str) -> None:
    """How much of set(a) also appears in set(b) — and vice versa."""
    sa, sb = set(a.dropna()), set(b.dropna())
    inter = len(sa & sb)
    print(f"  {label}: ix {len(sa):,} distinct | pv {len(sb):,} distinct | "
          f"shared {inter:,} ({pct(inter, len(sa))} of ix, {pct(inter, len(sb))} of pv)")


def diagnose_store(ix_path: Path, pv_path: Path) -> None:
    if not ix_path.exists():
        sys.exit(f"Not found: {ix_path} — run process_site_interactions.py "
                 "(or generate_demo_data.py) first.")
    ix = pd.read_parquet(ix_path)
    hdr(f"Interactions store: {ix_path.name}")
    print(f"  Rows: {len(ix):,}  |  Columns: {len(ix.columns)}")
    if "timestamp" in ix.columns:
        ts = pd.to_datetime(ix["timestamp"], errors="coerce")
        print(f"  Time range: {ts.min()}  ->  {ts.max()}")

    hdr("Event families (event_name)")
    value_counts(ix, "event_name")

    hdr("Interaction classes (derived)")
    value_counts(ix, "interaction_class")

    hdr("Link_Type distribution (source column)")
    value_counts(ix, "link_type", top=15)

    hdr("Coverage")
    for col in ["gpn", "person_id", "page_url", "page_key", "page_id",
                "link_label", "link_address", "component_name",
                "file_name_label", "session_id", "user_id"]:
        print(f"  {col:18s} {coverage(ix, col)}")

    if not pv_path.exists():
        print(f"\npv store not found ({pv_path}) — skipping join diagnostics.")
        return
    pv = pd.read_parquet(pv_path)
    hdr(f"Join diagnostics vs. {pv_path.name} ({len(pv):,} views)")

    # 1. Content grain — page_key (dashboard ranking) and page_id (variant drill)
    if "page_key" in ix.columns and "page_key" in pv.columns:
        overlap(ix["page_key"], pv["page_key"], "page_key ")
        ix_keys = ix["page_key"].dropna()
        orphans = int((~ix_keys.isin(set(pv["page_key"].dropna()))).sum())
        print(f"    -> orphan clicks (page_key not in pv): {orphans:,} "
              f"({pct(orphans, len(ix_keys))}) — >10% means the export windows/scopes differ")
    if "page_id" in ix.columns and "page_id" in pv.columns:
        overlap(ix["page_id"], pv["page_id"], "page_id  ")

    # 2. Person grain — clicker vs. viewer populations
    for col in ["person_id", "gpn"]:
        if col in ix.columns and col in pv.columns:
            overlap(ix[col], pv[col], f"{col:9s}")

    # 3. Session grain — evidence for/against a same-visit view->click funnel
    hdr("Session-funnel feasibility (not used by the dashboard yet)")
    for col in ["session_id", "user_id"]:
        if col in ix.columns and col in pv.columns:
            overlap(ix[col], pv[col], col)
    print("  Interpretation: if session_id overlap is near zero, the official ids")
    print("  are minted per stream/request and a same-visit funnel must be")
    print("  reconstructed via person_id + time proximity instead.")


def diagnose_raw(paths: list[Path]) -> None:
    from flatten_appinsights import read_input, flatten_appinsights, build_clean_interactions_table
    for p in paths:
        if not p.exists():
            print(f"Not found: {p}")
            continue
        raw = read_input(p)
        hdr(f"Raw export: {p.name} ({len(raw):,} rows)")
        if "name" in raw.columns:
            value_counts(raw, "name")
        clean = build_clean_interactions_table(flatten_appinsights(raw))
        print("  After flatten:")
        for col in ["gpn", "page_url", "page_id", "link_label", "link_address",
                    "component_name", "file_name_label"]:
            print(f"    {col:16s} {coverage(clean, col)}")


def main():
    ap = argparse.ArgumentParser(description="Diagnose interactions store vs. pageViews store (read-only)")
    ap.add_argument("--ix", default=str(PROJECT_DIR / "output" / "site_interactions.parquet"),
                    help="interactions parquet (default: output/site_interactions.parquet)")
    ap.add_argument("--pv", default=str(PROJECT_DIR / "output" / "site_pageviews.parquet"),
                    help="pageviews parquet (default: output/site_pageviews.parquet)")
    ap.add_argument("--raw", nargs="*", default=[],
                    help="raw CSV/XLSX export(s) to inspect before building")
    args = ap.parse_args()

    if args.raw:
        diagnose_raw([Path(p) for p in args.raw])
    else:
        diagnose_store(Path(args.ix), Path(args.pv))


if __name__ == "__main__":
    main()
