"""
Build the page_engagement Parquet stores for the SiteOwnerDashboard.

Reads customEvents exports containing the `page_engagement` family
(spec: docs/superpowers/specs/2026-07-15-page-engagement-heartbeat-design.md)
from input/engagement/, upserts the flush-grain store, and rebuilds the
view-instance aggregate from the FULL flush store each run:

    input/engagement/*.csv|xlsx
      -> output/engagement_flushes.parquet   (grain: 1 row per flush;
                                              PK view_instance_id x flush_seq)
      -> output/page_engagement.parquet      (grain: 1 row per view instance;
                                              engaged_ms = SUM of deltas,
                                              scroll_max_pct = MAX)

Delta semantics (spec §3.3): each flush carries milliseconds since the LAST
flush; double-fires arrive as 0-deltas and are harmless under SUM. flush_seq
gaps quantify beacon loss (has_seq_gap).

Usage:
    python scripts/process_page_engagement.py            # incremental
    python scripts/process_page_engagement.py --rebuild  # from scratch
"""
from __future__ import annotations

import argparse
import sys
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd

SCRIPT_DIR = Path(__file__).resolve().parent
PROJECT_DIR = SCRIPT_DIR.parents[0]
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
)
from flatten_appinsights import (  # noqa: E402
    read_input,
    flatten_appinsights,
    build_clean_interactions_table,
    strip_tz,
)

INPUT_DIR = PROJECT_DIR / "input" / "engagement"
FLUSH_KEY_COLS = ["view_instance_id", "flush_seq"]

FLUSH_COLUMNS = [
    "timestamp", "event_name", "view_instance_id",
    "engaged_ms_delta", "scroll_max_pct", "page_height_px",
    "viewport_height_px", "flush_reason", "flush_seq",
    "page_id", "page_key", "page_name", "page_url", "language",
    "site_id", "site_name",
    "user_id", "session_id", "person_id", "email", "gpn",
    "source_file",
]

NUMERIC_COLS = ["engaged_ms_delta", "scroll_max_pct", "page_height_px",
                "viewport_height_px", "flush_seq"]


def derive_person(df: pd.DataFrame) -> pd.DataFrame:
    """ADD person_id = GPN where present, else 'anon:<user_id>' (same as pv/ix)."""
    df = df.copy()
    if "gpn" in df.columns:
        person = df["gpn"].astype("string")
    else:
        person = pd.Series(pd.NA, index=df.index, dtype="string")
    if "user_id" in df.columns:
        person = person.fillna("anon:" + df["user_id"].astype("string").fillna(""))
    else:
        person = person.fillna("anon:unknown")
    df["person_id"] = person
    return df


def looks_like_engagement(raw: pd.DataFrame, name: str) -> bool:
    """Guard against a pageViews/clicks export dropped into input/engagement/."""
    if "name" in raw.columns and raw["name"].astype(str).eq("page_engagement").any():
        return True
    print(f"  WARNING: {name} has no page_engagement rows — wrong export for "
          "input/engagement/. Skipping it.")
    return False


def build_one(input_path: Path) -> pd.DataFrame | None:
    print(f"  {input_path.name}:")
    raw = read_input(input_path)
    print(f"    {len(raw):,} raw rows")
    if not looks_like_engagement(raw, input_path.name):
        return None

    clean = build_clean_interactions_table(flatten_appinsights(raw))

    # page_engagement only — never let another family leak into this store
    # (Global Constraint: the family and click aggregates stay disjoint).
    if "event_name" in clean.columns:
        before = len(clean)
        clean = clean[clean["event_name"] == "page_engagement"]
        if len(clean) < before:
            print(f"    Kept page_engagement only: {len(clean):,}/{before:,} rows")

    for col in NUMERIC_COLS:
        if col in clean.columns:
            clean[col] = pd.to_numeric(clean[col], errors="coerce")

    dropped = int(clean["view_instance_id"].isna().sum()) if "view_instance_id" in clean.columns else len(clean)
    if dropped:
        print(f"    WARNING: {dropped:,} rows without view_instance_id dropped "
              "(cannot be correlated)")
        clean = clean[clean["view_instance_id"].notna()]

    clean["source_file"] = input_path.name
    return clean


def aggregate_engagement(flushes: pd.DataFrame) -> pd.DataFrame:
    """Flush grain -> view-instance grain (spec §5).

    engaged_ms   = SUM(engaged_ms_delta)   — delta semantics, double-fire safe
    scroll_max   = MAX(scroll_max_pct)
    has_seq_gap  = flush_count != max(flush_seq)+1  — beacon-loss indicator
    Context columns: first non-null per view instance.
    """
    df = flushes.copy()
    df["engaged_ms_delta"] = pd.to_numeric(df["engaged_ms_delta"], errors="coerce").fillna(0)
    df["flush_seq"] = pd.to_numeric(df["flush_seq"], errors="coerce")
    df = df.sort_values(["view_instance_id", "flush_seq"], kind="mergesort")

    g = df.groupby("view_instance_id", sort=False)
    agg = g.agg(
        engaged_ms=("engaged_ms_delta", "sum"),
        scroll_max_pct=("scroll_max_pct", "max"),
        flush_count=("flush_seq", "size"),
        _max_seq=("flush_seq", "max"),
        first_ts=("timestamp", "min"),
        last_flush_reason=("flush_reason", "last"),
    )
    context_cols = [c for c in ("page_id", "page_key", "page_url", "page_name",
                                "language", "site_id", "site_name",
                                "person_id", "session_id",
                                # Read-Rate inputs (spec §5) must survive to view grain
                                "page_height_px", "viewport_height_px") if c in df.columns]
    if context_cols:
        agg = agg.join(g[context_cols].first())

    agg["engaged_ms"] = agg["engaged_ms"].round().astype("int64")
    agg["has_seq_gap"] = agg["flush_count"] != (agg["_max_seq"] + 1)
    agg = agg.drop(columns=["_max_seq"])
    return agg.reset_index()


def main() -> None:
    ap = argparse.ArgumentParser(description="Build page_engagement Parquets for the dashboard")
    ap.add_argument("input", nargs="*",
                    help="customEvents export(s) with page_engagement rows "
                    "(default: all CSV/XLSX in input/engagement/)")
    ap.add_argument("--rebuild", action="store_true",
                    help="Ignore the manifest and reprocess ALL input files")
    ap.add_argument("-o", "--output",
                    help="Aggregate parquet (default: output/page_engagement.parquet)")
    args = ap.parse_args()

    agg_path = Path(args.output) if args.output else PROJECT_DIR / "output" / "page_engagement.parquet"
    flush_path = agg_path.with_name("engagement_flushes.parquet")
    agg_path.parent.mkdir(parents=True, exist_ok=True)

    mpath = manifest_path(flush_path)
    manifest = {} if args.rebuild else load_manifest(mpath)
    if manifest and not flush_path.exists():
        print(f"  {flush_path.name} missing — ignoring manifest, reprocessing everything")
        manifest = {}

    if args.input:
        input_paths = [Path(p) for p in args.input]
        missing = [p for p in input_paths if not p.exists()]
        if missing:
            sys.exit("Input not found: " + ", ".join(map(str, missing)))
        to_process = [(p, compute_file_hash(p), "forced") for p in input_paths]
        skipped = []
    else:
        input_paths = [p for p in sorted(INPUT_DIR.glob("*"))
                       if p.suffix.lower() in (".csv", ".xlsx", ".xls")
                       and not p.name.startswith((".", "~$"))] if INPUT_DIR.exists() else []
        if not input_paths:
            sys.exit(f"No CSV/XLSX files found in {INPUT_DIR} — drop the "
                     "page_engagement export(s) there or pass a path explicitly.")
        to_process, skipped = partition_files(input_paths, manifest)

    if skipped:
        print(f"Skipping {len(skipped)} unchanged file(s): {', '.join(skipped)}")
    if not to_process:
        print(f"Up to date — nothing new to process ({flush_path.name} unchanged).")
        return

    print(f"Processing {len(to_process)} file(s): "
          + ", ".join(f"{p.name} ({reason})" for p, _, reason in to_process))
    parts = [b for b in (build_one(p) for p, _, _ in to_process) if b is not None]
    if not parts:
        sys.exit("No processable page_engagement export found — nothing to do.")
    new = parts[0] if len(parts) == 1 else pd.concat(parts, ignore_index=True)

    # Language + language-agnostic page key — identical to the pv build.
    if "page_url" in new.columns:
        new["language"] = new["page_url"].map(derive_language)
        new["page_key"] = new["page_url"].map(canonical_page_key)
        if "page_id" in new.columns:
            new["page_key"] = new["page_key"].fillna(new["page_id"].astype("string"))
    new = derive_person(new)

    keep = [c for c in FLUSH_COLUMNS if c in new.columns]
    new = strip_tz(new[keep].reset_index(drop=True))

    new = add_event_key(new, FLUSH_KEY_COLS)
    new = new.drop_duplicates(subset=["event_key"], keep="last")

    store = None
    if flush_path.exists() and not args.rebuild:
        store = pd.read_parquet(flush_path)
        print(f"  Existing flush store: {len(store):,} rows")
    flushes = upsert_store(store, new, [p.name for p, _, _ in to_process], FLUSH_KEY_COLS)
    flushes.to_parquet(flush_path, index=False)

    # Aggregate is rebuilt from the FULL flush store — deterministic under upsert.
    agg = aggregate_engagement(flushes)
    agg.to_parquet(agg_path, index=False)

    gap_rate = float(agg["has_seq_gap"].mean()) if len(agg) else 0.0
    print(f"  Beacon loss (flush_seq gaps): {gap_rate:.1%} of view instances")

    now = datetime.now(timezone.utc).isoformat(timespec="seconds")
    rows_per_file = (new["source_file"].value_counts().to_dict()
                     if "source_file" in new.columns else {})
    for p, file_hash, _ in to_process:
        manifest[p.name] = {"sha256": file_hash,
                            "rows": int(rows_per_file.get(p.name, 0)),
                            "processed_at": now}
    save_manifest(mpath, manifest)

    print(f"\nWrote {len(flushes):,} flushes -> {flush_path}")
    print(f"Wrote {len(agg):,} view instances -> {agg_path}")


if __name__ == "__main__":
    main()
