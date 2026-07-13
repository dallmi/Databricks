"""
Reconcile the reconstructed `visit_id` against the source AppInsights
`session_id` — answers "shouldn't session_id already group views into visits?".

Both columns PARTITION the page views into groups. This script compares the two
partitions on the store parquet and prints a verdict:

  1. COUNTS        distinct session_id vs visit_id vs person_id, views/group.
  2. SESSION SIZE  how many page views a source session actually spans. If (almost)
                   every session has exactly ONE view, the SDK is minting a fresh
                   session_id per navigation — it is NOT grouping anything.
  3. WITHIN A VISIT how many DISTINCT source session_ids appear inside one
                   reconstructed visit. ~= views/visit  => session_id resets every
                   view (useless); ~= 1 => session_id already captures the visit.
  4. SESSION HEALTH does a source session ever straddle >30 min or >1 person?
                   (a working session must be one person, one sitting.)
  5. AGREEMENT     on the sessions that DO span >1 view, do those views land in a
                   single visit_id and a single person_id? (high => the two agree
                   where session_id is usable at all.)

Read-only. Works on the corp store and on the demo store.

Usage (from the SiteOwnerDashboard project root):
    python scripts/reconcile_visit_session.py
    python scripts/reconcile_visit_session.py --store output/site_pageviews.parquet
"""

from __future__ import annotations

import argparse
from pathlib import Path

import pandas as pd

PROJECT_DIR = Path(__file__).resolve().parents[1]


def pct(n, d):
    return f"{(100.0 * n / d):.1f}%" if d else "n/a"


def main() -> None:
    ap = argparse.ArgumentParser(description="Reconcile visit_id vs source session_id")
    ap.add_argument("--store", default=str(PROJECT_DIR / "output" / "site_pageviews.parquet"))
    ap.add_argument("--examples", type=int, default=5, help="example visits to print")
    args = ap.parse_args()

    path = Path(args.store)
    if not path.exists():
        raise SystemExit(f"store not found: {path}")
    df = pd.read_parquet(path)
    n = len(df)

    need = ["session_id", "visit_id", "person_id"]
    missing = [c for c in need if c not in df.columns]
    if missing:
        raise SystemExit(f"store is missing {missing} — run process_site_pageviews.py "
                         "(the derived columns are added there). Rebuild with --rebuild.")

    print(f"store: {path}  ({n:,} page views)\n")

    # 1. COUNTS -------------------------------------------------------------
    n_sess = df["session_id"].nunique()
    n_visit = df["visit_id"].nunique()
    n_person = df["person_id"].nunique()
    print("1. COUNTS")
    print(f"   distinct source session_id : {n_sess:,}   ({n/max(n_sess,1):.2f} views/session)")
    print(f"   distinct visit_id (recon.) : {n_visit:,}   ({n/max(n_visit,1):.2f} views/visit)")
    print(f"   distinct person_id         : {n_person:,}   ({n/max(n_person,1):.2f} views/person)")

    # 2. SESSION SIZE -------------------------------------------------------
    sess_sizes = df.groupby("session_id").size()
    one_view_sessions = int((sess_sizes == 1).sum())
    views_in_one = int(sess_sizes[sess_sizes == 1].sum())
    print("\n2. SOURCE SESSION SIZE (page views per source session_id)")
    print(f"   sessions with exactly 1 view : {one_view_sessions:,}/{n_sess:,} "
          f"({pct(one_view_sessions, n_sess)} of sessions)")
    print(f"   page views in 1-view sessions: {views_in_one:,}/{n:,} "
          f"({pct(views_in_one, n)} of views)")
    dist = sess_sizes.value_counts().sort_index()
    head = ", ".join(f"{k}v×{v:,}" for k, v in dist.head(6).items())
    print(f"   size histogram (views×count) : {head}"
          + (" …" if len(dist) > 6 else ""))

    # 3. WITHIN A RECONSTRUCTED VISIT --------------------------------------
    per_visit = df.groupby("visit_id").agg(
        views=("session_id", "size"),
        sess=("session_id", "nunique"),
        persons=("person_id", "nunique"),
    )
    multi = per_visit[per_visit["views"] > 1]
    one_session_share = 0.0
    print("\n3. WITHIN ONE RECONSTRUCTED VISIT")
    print(f"   multi-view visits            : {len(multi):,}/{len(per_visit):,}")
    if len(multi):
        ratio = (multi["sess"] / multi["views"]).mean()
        print(f"   mean distinct session_id / views in those visits: {ratio:.2f}")
        print(f"     ~1.0 => a NEW session_id per page view (session_id useless);")
        print(f"     ~1/views (low) => session_id already captures the visit.")
        one_session = int((multi["sess"] == 1).sum())
        one_session_share = one_session / len(multi)
        print(f"   visits whose views ALL share ONE session_id: {one_session:,}/{len(multi):,} "
              f"({pct(one_session, len(multi))})")

    # 4. SOURCE SESSION HEALTH ---------------------------------------------
    print("\n4. SOURCE SESSION HEALTH (only sessions with >1 view can be judged)")
    multi_sess_ids = sess_sizes[sess_sizes > 1].index
    if len(multi_sess_ids) == 0:
        print("   none — every source session has a single view, so session_id")
        print("   cannot be validated at all. It is effectively per-page-view.")
    else:
        ms = df[df["session_id"].isin(multi_sess_ids)]
        g = ms.groupby("session_id")
        span_min = (g["timestamp"].max() - g["timestamp"].min()).dt.total_seconds() / 60
        over_30 = int((span_min > 30).sum())
        multi_person = int((g["person_id"].nunique() > 1).sum())
        print(f"   multi-view sessions          : {len(multi_sess_ids):,}")
        print(f"   spanning >30 min             : {over_30:,} ({pct(over_30, len(multi_sess_ids))})")
        print(f"   spanning >1 person_id        : {multi_person:,} ({pct(multi_person, len(multi_sess_ids))})")

        # 5. AGREEMENT ------------------------------------------------------
        same_visit = int((g["visit_id"].nunique() == 1).sum())
        same_person = int((g["person_id"].nunique() == 1).sum())
        print("\n5. AGREEMENT (do multi-view sessions land in ONE visit / ONE person?)")
        print(f"   all views in a single visit_id : {same_visit:,}/{len(multi_sess_ids):,} "
              f"({pct(same_visit, len(multi_sess_ids))})")
        print(f"   all views for a single person  : {same_person:,}/{len(multi_sess_ids):,} "
              f"({pct(same_person, len(multi_sess_ids))})")

    # examples --------------------------------------------------------------
    if args.examples and len(multi):
        print(f"\nEXAMPLES ({args.examples} multi-view visits: views | distinct session_id)")
        for vid, r in multi.head(args.examples).iterrows():
            print(f"   {str(vid)[:32]:32s}  views={int(r['views'])}  distinct_session_id={int(r['sess'])}")

    # VERDICT ---------------------------------------------------------------
    per_view_share = views_in_one / max(n, 1)
    print("\nVERDICT")
    if per_view_share > 0.9:
        print("   session_id is minting a fresh id per page view (>90% of views sit in")
        print("   1-view sessions). It does NOT group a visit — the reconstructed")
        print("   visit_id is the correct grouping. Your intuition is right in theory;")
        print("   this AppInsights instance just doesn't persist the session cookie.")
    elif one_session_share > 0.8:
        print("   session_id DOES group views into visits: in "
              f"{pct(int(one_session_share*len(multi)), len(multi))} of multi-view")
        print("   visits every view shares one session_id, and multi-view sessions")
        print("   stay within one person and one sitting. Here session_id and visit_id")
        print("   agree — reconstruction is a safety net, not a correction.")
    else:
        print("   Mixed: session_id groups some visits but resets within others.")
        print("   Reconstruction (visit_id) normalises both cases — keep counting on it.")


if __name__ == "__main__":
    main()
