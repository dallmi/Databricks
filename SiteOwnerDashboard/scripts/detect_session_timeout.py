"""
Detect whether the source AppInsights `session_id` is renewed after a fixed
inactivity timeout — and if so, estimate it (scanning 15..120 min).

Idea: order page views by person (person_id) and time. For each pair of
CONSECUTIVE views by the SAME person, measure the inactivity gap and whether the
session_id stayed the same. If the SDK renews the session after T minutes of
inactivity, then:
    gap  < T  ->  same session_id   (still the same session)
    gap >= T  ->  new  session_id
So P(same session_id | gap) is high below the timeout and collapses above it —
the crossover IS the timeout. If session_id is minted per page view, P(same) is
~0 at every gap (no timeout — it renews on every navigation).

Read-only. Run on the corp store to see the real behaviour.

Usage (from the SiteOwnerDashboard project root):
    python scripts/detect_session_timeout.py
    python scripts/detect_session_timeout.py --store output/site_pageviews.parquet
    python scripts/detect_session_timeout.py --lo 15 --hi 120 --step 5
"""

from __future__ import annotations

import argparse
from pathlib import Path

import numpy as np
import pandas as pd

PROJECT_DIR = Path(__file__).resolve().parents[1]

# gap buckets in minutes (upper-exclusive); the last one catches everything else
BUCKETS = [0, 1, 2, 3, 5, 7, 10, 15, 20, 25, 30, 35, 40, 45, 50, 60, 75, 90, 120, 10**9]


def main() -> None:
    ap = argparse.ArgumentParser(description="Estimate the session_id inactivity timeout")
    ap.add_argument("--store", default=str(PROJECT_DIR / "output" / "site_pageviews.parquet"))
    ap.add_argument("--lo", type=int, default=15, help="scan lower bound (min)")
    ap.add_argument("--hi", type=int, default=120, help="scan upper bound (min)")
    ap.add_argument("--step", type=int, default=5, help="scan step (min)")
    args = ap.parse_args()

    path = Path(args.store)
    if not path.exists():
        raise SystemExit(f"store not found: {path}")
    df = pd.read_parquet(path)

    for c in ("person_id", "session_id", "timestamp"):
        if c not in df.columns:
            raise SystemExit(f"store is missing '{c}' — rebuild via process_site_pageviews.py")

    df = df[["person_id", "session_id", "timestamp"]].copy()
    df["timestamp"] = pd.to_datetime(df["timestamp"], errors="coerce")
    df = df.dropna(subset=["timestamp"]).sort_values(
        ["person_id", "timestamp"], kind="mergesort").reset_index(drop=True)

    # consecutive-pair features within the same person
    prev_person = df["person_id"].shift(1)
    prev_sess = df["session_id"].shift(1)
    prev_ts = df["timestamp"].shift(1)
    same_person = df["person_id"] == prev_person
    gap_min = (df["timestamp"] - prev_ts).dt.total_seconds() / 60

    pairs = pd.DataFrame({
        "gap_min": gap_min[same_person],
        "same_session": (df["session_id"] == prev_sess)[same_person],
    }).dropna(subset=["gap_min"])

    n_pairs = len(pairs)
    if n_pairs == 0:
        raise SystemExit("no same-person consecutive pairs — need >1 view per person")

    overall_same = pairs["same_session"].mean()
    print(f"store: {path}")
    print(f"same-person consecutive view pairs: {n_pairs:,}")
    print(f"overall P(same session_id across a pair): {overall_same:.1%}\n")

    # per-gap-bucket P(same session_id)
    print("P(same session_id) by inactivity gap:")
    print(f"   {'gap (min)':>14s} {'pairs':>10s} {'same-session':>13s}")
    pairs["bucket"] = pd.cut(pairs["gap_min"], bins=BUCKETS, right=False)
    grp = pairs.groupby("bucket", observed=True)["same_session"].agg(["size", "mean"])
    labels = {}
    for iv, row in grp.iterrows():
        lo, hi = iv.left, iv.right
        lab = f"{int(lo)}–{'∞' if hi >= 10**8 else int(hi)}"
        labels[iv] = (lo, hi)
        bar = "█" * int(round(row["mean"] * 20))
        print(f"   {lab:>14s} {int(row['size']):>10,} {row['mean']:>12.1%} {bar}")

    # threshold scan 15..120: contrast below vs at/above each candidate T
    print(f"\nthreshold scan ({args.lo}..{args.hi} min): does 'same session' hold below T"
          " and break at/above T?")
    print(f"   {'T (min)':>8s} {'P(same|gap<T)':>15s} {'P(same|gap>=T)':>16s} {'drop':>8s}")
    best = None
    for T in range(args.lo, args.hi + 1, args.step):
        below = pairs[pairs["gap_min"] < T]["same_session"]
        above = pairs[pairs["gap_min"] >= T]["same_session"]
        if len(below) == 0 or len(above) == 0:
            continue
        pb, pa = below.mean(), above.mean()
        drop = pb - pa
        star = ""
        if best is None or drop > best[1]:
            best = (T, drop, pb, pa); star = ""
        print(f"   {T:>8d} {pb:>14.1%} {pa:>15.1%} {drop:>+8.1%}")

    # Cliff detection: the timeout is the gap boundary where the per-bucket
    # 'same session' rate DROPS the most (relative to the level just below it).
    # Real session_id can also be renewed WITHIN a session (reloads, tabs), so
    # the level below the timeout need not be ~100% — we look for a sharp
    # relative fall, not an absolute one.
    rates = grp["mean"]
    ivs = list(grp.index)
    cliff = None  # (est_min, lo_edge, hi_edge, rate_below, rate_above, drop)
    for i in range(1, len(ivs)):
        boundary = ivs[i].left
        if not (args.lo <= boundary <= args.hi):
            continue
        rb, ra = rates.iloc[i - 1], rates.iloc[i]
        drop = rb - ra
        # timeout sits between the high bucket's right edge and the low bucket's
        # left edge; the midpoint is the best point estimate (handles a gap in
        # observed buckets, e.g. no data between 40 and 50 min).
        lo_edge, hi_edge = int(ivs[i - 1].right), int(boundary)
        est = (lo_edge + hi_edge) // 2
        if cliff is None or drop > cliff[5]:
            cliff = (est, lo_edge, hi_edge, rb, ra, drop)

    # verdict
    print("\nVERDICT")
    is_cliff = cliff is not None and cliff[5] >= 0.15 and cliff[3] >= 2 * max(cliff[4], 1e-9)
    if overall_same < 0.02:
        print("   session_id is renewed on essentially EVERY page view "
              f"(P(same)={overall_same:.1%}).")
        print("   There is NO inactivity timeout — it does not persist across "
              "navigations at all.")
        print("   The reconstructed visit_id is what defines a visit here.")
    elif is_cliff:
        T, lo_edge, hi_edge, rb, ra, drop = cliff
        window = f"~{T} min" if lo_edge == hi_edge else f"~{T} min (between {lo_edge} and {hi_edge})"
        print(f"   session_id has an inactivity timeout of {window}: the 'same session'")
        print(f"   rate falls sharply there ({rb:.0%} just below -> {ra:.0%} just above).")
        if rb < 0.7:
            print(f"   BUT below the timeout session_id already persists only ~{rb:.0%} of")
            print("   the time — it is ALSO renewed within a session (reloads / new tabs),")
            print("   so it over-fragments and stays unusable for counting raw.")
        gap_note = "matches" if abs(T - 30) <= 5 else "differs from"
        print(f"   The visit_id 30-min reconstruction {gap_note} this ~{T}-min timeout"
              + ("." if gap_note == "matches" else f"; set SESSION_GAP_MIN to ~{T} to align."))
    else:
        print("   No sharp timeout between "
              f"{args.lo} and {args.hi} min — session_id renewal is not driven by a")
        print("   single inactivity threshold (mixed triggers: reloads, tabs, per-view).")
        print("   Keep counting on the reconstructed visit_id.")


if __name__ == "__main__":
    main()
