"""
Diagnose why the dashboard shows Page Views ~= Page Visits ~= Unique Visitors.

Checks three layers independently and prints a verdict:

  1. INPUT   — every CSV/XLSX in input/: column names (BOM/whitespace/shift),
               content fingerprints (is itemType really 'pageView'? is
               customDimensions really JSON?), uniqueness ratios of
               session_Id / user_Id (per-row-unique => column shift), and the
               simulated dashboard KPIs (views / visits / uniques) per file.
  2. STORE   — output/site_pageviews.parquet: rows per source_file, KPIs per
               source_file and overall (= what the dashboard computes), stale
               rows contributed by files that no longer exist in input/
               (the upsert only evicts rows when a file with the SAME NAME is
               re-processed — replaced/renamed files leave their rows behind).
  3. FRESHNESS — mtime chain input -> parquet -> standalone HTML, plus the
               manifest. The standalone HTML EMBEDS the parquet: if it was not
               rebuilt after reprocessing, the browser shows old data no matter
               how clean the new inputs are.

Read-only: this script never writes or modifies anything.

Usage (from the SiteOwnerDashboard project root, works in the corp env too):
    python scripts/diagnose_exports.py
    python scripts/diagnose_exports.py --input input --store output/site_pageviews.parquet

Requires: pandas, openpyxl (XLSX), pyarrow (parquet) — same as the pipeline.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from datetime import datetime
from pathlib import Path

import pandas as pd

SCRIPT_DIR = Path(__file__).resolve().parent
PROJECT_DIR = SCRIPT_DIR.parent

# KQL projection order of export_site_pageviews.kql — used to detect shifts.
EXPECTED_COLS = [
    "timestamp [UTC]", "id", "name", "url", "duration", "performanceBucket",
    "itemType", "customDimensions", "operation_Name", "operation_Id",
    "operation_ParentId", "session_Id", "user_Id", "client_Type",
    "client_Model", "client_OS", "client_IP", "client_City",
    "client_StateOrProvince", "client_CountryOrRegion", "client_Browser",
    "appId", "iKey", "sdkVersion", "itemCount",
]

# A session/user column whose distinct-ratio exceeds this is per-row-unique,
# i.e. it holds operation ids (column shift) — not real sessions/users.
UNIQUE_RATIO_THRESHOLD = 0.90

GUID_RE = re.compile(r"^[0-9a-fA-F]{32}$|^[0-9a-fA-F-]{36}$")

issues: list[str] = []   # collected [FAIL]/[WARN] lines for the verdict


def flag(level: str, msg: str) -> None:
    line = f"  [{level}] {msg}"
    print(line)
    if level in ("FAIL", "WARN"):
        issues.append(f"[{level}] {msg}")


def ok(msg: str) -> None:
    print(f"  [OK]   {msg}")


def fmt_ts(ts: float) -> str:
    return datetime.fromtimestamp(ts).strftime("%Y-%m-%d %H:%M")


def read_any(path: Path) -> pd.DataFrame:
    if path.suffix.lower() == ".csv":
        return pd.read_csv(path, encoding="utf-8-sig", low_memory=False)
    return pd.read_excel(path)


def uniq_ratio(s: pd.Series) -> tuple[int, float]:
    n = s.dropna()
    n = n[n.astype(str).str.strip() != ""]
    distinct = n.nunique()
    return distinct, (distinct / len(n)) if len(n) else 0.0


def looks_like_guid(s: pd.Series, sample: int = 200) -> float:
    """Share of sampled non-null values matching a 32/36-char GUID."""
    vals = s.dropna().astype(str).head(sample)
    if vals.empty:
        return 0.0
    return sum(bool(GUID_RE.match(v.strip())) for v in vals) / len(vals)


# ---------------------------------------------------------------------------
# 1. INPUT files
# ---------------------------------------------------------------------------

def check_input_file(path: Path) -> dict | None:
    print(f"\n--- {path.name} ({path.stat().st_size/1e6:.1f} MB, modified {fmt_ts(path.stat().st_mtime)}) ---")
    try:
        df = read_any(path)
    except Exception as e:
        flag("FAIL", f"cannot read: {type(e).__name__}: {e}")
        return None

    print(f"  shape: {df.shape[0]:,} rows x {df.shape[1]} columns")

    # --- column names: BOM, whitespace, unsplit single-column file ---
    if df.shape[1] <= 2:
        flag("FAIL", f"only {df.shape[1]} column(s) — file was saved WITHOUT being "
                     "split into columns (whole CSV line per cell). Re-export it.")
        print(f"  first column name: {df.columns[0]!r}")
        return None

    dirty = [c for c in df.columns if c != c.strip() or "﻿" in c]
    if dirty:
        flag("WARN", f"column names with BOM/whitespace: {dirty[:3]} — exact-name "
                     "mappings in the pipeline will miss these")
    cols_clean = [c.replace("﻿", "").strip() for c in df.columns]

    missing = [c for c in EXPECTED_COLS if c not in cols_clean and c != "timestamp [UTC]"]
    if "timestamp [UTC]" not in cols_clean and "timestamp" not in cols_clean:
        missing.insert(0, "timestamp [UTC]")
    extra = [c for c in cols_clean if c not in EXPECTED_COLS and c != "timestamp"]
    if missing:
        flag("WARN", f"expected columns missing: {missing}")
    if extra:
        flag("WARN", f"unexpected extra columns: {extra} — possible shift or "
                     "'all columns' export variant")
    if not missing and not extra:
        ok("column names match the KQL projection exactly")

    # normalise access by cleaned name
    df.columns = cols_clean

    # --- content fingerprints: is each column what its header claims? ---
    if "itemType" in df.columns:
        vals = df["itemType"].dropna().astype(str).unique()[:5]
        if set(vals) <= {"pageView"}:
            ok("itemType == 'pageView' for all rows (no shift at this position)")
        else:
            flag("FAIL", f"itemType contains {list(vals)} instead of 'pageView' — "
                         "COLUMN SHIFT: headers and values are misaligned")

    if "customDimensions" in df.columns:
        sample_cd = df["customDimensions"].dropna().astype(str).head(50)
        json_share = sum(v.strip().startswith("{") and "CustomProps" in v
                         for v in sample_cd) / max(len(sample_cd), 1)
        if json_share > 0.9:
            ok("customDimensions holds CustomProps JSON (no shift at this position)")
        else:
            flag("FAIL", f"customDimensions is NOT the expected JSON "
                         f"(only {json_share:.0%} look like JSON) — COLUMN SHIFT. "
                         f"sample: {sample_cd.iloc[0][:80]!r}" if len(sample_cd) else
                         "customDimensions is empty")

    # --- timestamp: parse rate, date range, time-of-day presence ---
    ts_col = "timestamp [UTC]" if "timestamp [UTC]" in df.columns else (
        "timestamp" if "timestamp" in df.columns else None)
    if ts_col is None:
        flag("FAIL", "no timestamp column found")
    else:
        ts = pd.to_datetime(df[ts_col], errors="coerce", utc=True, format="mixed")
        parse_rate = ts.notna().mean()
        if parse_rate < 0.99:
            flag("FAIL", f"timestamp parse rate only {parse_rate:.1%} "
                         f"(sample raw value: {df[ts_col].dropna().astype(str).iloc[0]!r})")
        else:
            ok(f"timestamp parses ({parse_rate:.1%}), "
               f"range {ts.min():%Y-%m-%d %H:%M} .. {ts.max():%Y-%m-%d %H:%M}")
        midnight_share = ((ts.dt.hour == 0) & (ts.dt.minute == 0) & (ts.dt.second == 0)).mean()
        if midnight_share > 0.9:
            flag("FAIL", f"{midnight_share:.0%} of timestamps are exactly 00:00:00 — "
                         "time-of-day was lost (date-only column). Avg-session/"
                         "time-on-page will be 0.")

    # --- the core check: identifier uniqueness ratios ---
    print("  identifier cardinality (distinct / non-null rows):")
    result = {"file": path.name, "rows": len(df)}
    for col, expect in [("id", "unique per view"),
                        ("operation_Id", "unique per view"),
                        ("operation_ParentId", "unique per view"),
                        ("session_Id", "REUSED across views (ratio well below 0.9)"),
                        ("user_Id", "REUSED across views/sessions (ratio well below 0.9)")]:
        if col not in df.columns:
            print(f"    {col:20s} MISSING")
            continue
        distinct, ratio = uniq_ratio(df[col])
        guid_share = looks_like_guid(df[col])
        sample_val = str(df[col].dropna().iloc[0])[:40] if df[col].notna().any() else "(all null)"
        print(f"    {col:20s} {distinct:>9,}  ratio={ratio:5.1%}  guid-like={guid_share:4.0%}  e.g. {sample_val!r}")
        result[col] = ratio
        if col in ("session_Id", "user_Id") and ratio > UNIQUE_RATIO_THRESHOLD:
            flag("FAIL", f"{col} is per-row-unique (ratio {ratio:.1%}) — expected: {expect}. "
                         "This file itself produces Views == Visits == Uniques.")

    # --- simulated dashboard KPIs for THIS file ---
    if "session_Id" in df.columns and "user_Id" in df.columns:
        views = len(df)
        visits, _ = uniq_ratio(df["session_Id"])
        uniques, _ = uniq_ratio(df["user_Id"])
        verdictish = "BROKEN (1:1:1)" if visits > views * 0.9 and uniques > views * 0.9 else "plausible"
        print(f"  simulated KPIs: views={views:,}  visits={visits:,}  uniques={uniques:,}  -> {verdictish}")
        result.update(views=views, visits=visits, uniques=uniques)
    return result


# ---------------------------------------------------------------------------
# 2. STORE (output parquet)
# ---------------------------------------------------------------------------

def check_store(store_path: Path, input_names: set[str]) -> None:
    print(f"\n{'='*70}\n2. STORE — {store_path}\n{'='*70}")
    if not store_path.exists():
        flag("WARN", "store parquet does not exist — nothing processed yet")
        return
    print(f"  modified {fmt_ts(store_path.stat().st_mtime)}")
    df = pd.read_parquet(store_path)
    print(f"  {len(df):,} rows, columns: {len(df.columns)}")

    def kpis(part: pd.DataFrame) -> tuple[int, int, int]:
        v = len(part)
        s = part["session_id"].dropna().nunique() if "session_id" in part.columns else -1
        u = part["user_id"].dropna().nunique() if "user_id" in part.columns else -1
        return v, s, u

    v, s, u = kpis(df)
    print(f"\n  OVERALL (= dashboard 'All'):  views={v:,}  visits={s:,}  uniques={u:,}")
    if s > v * 0.9 and u > v * 0.9:
        flag("FAIL", "store-level KPIs are 1:1:1 — the rows below show which "
                     "source file(s) contribute the broken identifiers")

    if "source_file" in df.columns:
        print(f"\n  per source_file:")
        print(f"    {'source_file':42s} {'views':>9s} {'visits':>9s} {'uniques':>9s}  {'ts range':22s} state")
        for name, part in df.groupby("source_file"):
            pv, ps, pu = kpis(part)
            broken = ps > pv * 0.9 and pu > pv * 0.9
            stale = name not in input_names
            if "timestamp" in part.columns:
                ts = pd.to_datetime(part["timestamp"], errors="coerce")
                rng = f"{ts.min():%Y-%m-%d}..{ts.max():%Y-%m-%d}" if ts.notna().any() else "(no ts)"
            else:
                rng = "(no ts col)"
            state = ("BROKEN " if broken else "ok ") + ("+STALE(no longer in input/)" if stale else "")
            print(f"    {str(name)[:42]:42s} {pv:>9,} {ps:>9,} {pu:>9,}  {rng:22s} {state}")
            if broken:
                flag("FAIL", f"store rows from '{name}' have per-row-unique session/user ids")
            if stale:
                flag("WARN", f"store still holds {pv:,} rows from '{name}', which is no "
                             "longer in input/ — the upsert only evicts rows when a file "
                             "with the SAME NAME is re-processed. Run with --rebuild.")
    else:
        flag("WARN", "store has no source_file column — cannot attribute rows to files")


# ---------------------------------------------------------------------------
# 3. FRESHNESS (manifest + mtime chain)
# ---------------------------------------------------------------------------

def check_freshness(input_files: list[Path], store_path: Path) -> None:
    print(f"\n{'='*70}\n3. FRESHNESS — manifest and rebuild chain\n{'='*70}")

    mpath = store_path.with_suffix(".manifest.json")
    manifest = {}
    if mpath.exists():
        try:
            manifest = json.loads(mpath.read_text())
        except Exception as e:
            flag("WARN", f"manifest unreadable: {e}")
        print(f"  manifest: {len(manifest)} entrie(s)")
        for name, entry in sorted(manifest.items()):
            in_input = "in input/" if any(p.name == name for p in input_files) else "NOT in input/ anymore"
            print(f"    {name:42s} rows={entry.get('rows', '?'):>8}  processed={entry.get('processed_at', '?')}  ({in_input})")
    else:
        flag("WARN", "no manifest found next to the store parquet")

    unprocessed = [p.name for p in input_files if p.name not in manifest]
    if unprocessed:
        flag("FAIL", f"input file(s) NEVER processed into the store: {unprocessed} — "
                     "run scripts/process_site_pageviews.py")
    elif input_files:
        ok("every current input file appears in the manifest")

    if input_files and store_path.exists():
        newest_input = max(p.stat().st_mtime for p in input_files)
        if newest_input > store_path.stat().st_mtime:
            flag("FAIL", "newest input file is NEWER than the store parquet — "
                         "the new exports were not processed yet")

    html = store_path.parent / "site_dashboard_standalone.html"
    if html.exists() and store_path.exists():
        h, s = html.stat().st_mtime, store_path.stat().st_mtime
        print(f"  parquet   modified {fmt_ts(s)}")
        print(f"  standalone HTML modified {fmt_ts(h)}")
        if h < s:
            flag("FAIL", "standalone HTML is OLDER than the parquet — it embeds the "
                         "parquet at build time, so the browser still shows the OLD "
                         "data. Rebuild: python scripts/build_standalone_dashboard.py")
        else:
            ok("standalone HTML is newer than the parquet (embedded data is current)")
    elif not html.exists():
        print("  (no standalone HTML in output/ — skipping)")


# ---------------------------------------------------------------------------
# main
# ---------------------------------------------------------------------------

def main() -> None:
    ap = argparse.ArgumentParser(description="Diagnose input exports, store and freshness")
    ap.add_argument("--input", default=str(PROJECT_DIR / "input"),
                    help="input directory with CSV/XLSX exports (default: input/)")
    ap.add_argument("--store", default=str(PROJECT_DIR / "output" / "site_pageviews.parquet"),
                    help="store parquet (default: output/site_pageviews.parquet)")
    args = ap.parse_args()

    input_dir = Path(args.input)
    store_path = Path(args.store)

    print(f"{'='*70}\n1. INPUT — {input_dir}\n{'='*70}")
    input_files = []
    if input_dir.is_dir():
        input_files = [p for p in sorted(input_dir.glob("*"))
                       if p.suffix.lower() in (".csv", ".xlsx", ".xls")
                       and not p.name.startswith((".", "~$"))]
    if not input_files:
        flag("WARN", f"no CSV/XLSX files found in {input_dir}")
    for p in input_files:
        check_input_file(p)

    check_store(store_path, {p.name for p in input_files})
    check_freshness(input_files, store_path)

    print(f"\n{'='*70}\nVERDICT\n{'='*70}")
    if not issues:
        print("  No problems found: inputs are clean, store matches inputs, HTML is")
        print("  current. If the dashboard still shows 1:1:1, export the KPIs again")
        print("  and re-check — the data layer is not the cause.")
    else:
        for line in issues:
            print(f"  {line}")
        print("\n  Typical fixes, in order:")
        print("   - input file FAILs (shift / per-row-unique ids): re-create that export;")
        print("     an XLSX saved from a broken CSV inherits the broken columns.")
        print("   - stale/broken store rows: python scripts/process_site_pageviews.py --rebuild")
        print("   - outdated HTML: python scripts/build_standalone_dashboard.py")
    sys.exit(1 if any(l.startswith("[FAIL]") for l in issues) else 0)


if __name__ == "__main__":
    main()
