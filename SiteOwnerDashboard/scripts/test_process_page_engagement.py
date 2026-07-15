"""
Tests aggregate_engagement() — delta summation, scroll MAX, double-fire
tolerance and beacon-loss detection per spec §3.3/§5.

Plain-assert script:  python SiteOwnerDashboard/scripts/test_process_page_engagement.py
"""
from __future__ import annotations

import sys
from pathlib import Path

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent))
from process_page_engagement import aggregate_engagement  # noqa: E402


def flush(vi, seq, ms, scroll, reason, ts, page="p-42",
          url="https://intranet.example/en/a.aspx", person="00100200"):
    return {"view_instance_id": vi, "flush_seq": seq, "engaged_ms_delta": ms,
            "scroll_max_pct": scroll, "flush_reason": reason,
            "timestamp": pd.Timestamp(ts), "page_id": page, "page_url": url,
            "person_id": person, "session_id": "s-1"}


def main() -> None:
    rows = [
        # View A: route flush + hidden flush + zero-delta pagehide double-fire
        flush("vi-A", 0, 5000, 40, "route",    "2026-07-15 08:00:05"),
        flush("vi-A", 1, 2000, 80, "hidden",   "2026-07-15 08:00:30"),
        flush("vi-A", 2,    0, 80, "pagehide", "2026-07-15 08:00:30"),
        # View B: flush_seq 0 and 2 — seq 1 lost (beacon loss)
        flush("vi-B", 0, 1000, 25, "route",    "2026-07-15 09:00:01"),
        flush("vi-B", 2,  500, 60, "pagehide", "2026-07-15 09:01:00"),
        # View C: single-flush view (the 73% case the beacon exists for)
        flush("vi-C", 0, 42000, 100, "pagehide", "2026-07-15 10:00:42"),
    ]
    agg = aggregate_engagement(pd.DataFrame(rows))
    agg = agg.set_index("view_instance_id")
    assert len(agg) == 3, f"expected 3 view instances, got {len(agg)}"

    a = agg.loc["vi-A"]
    assert a["engaged_ms"] == 7000, f"A: deltas must SUM (5000+2000+0), got {a['engaged_ms']}"
    assert a["scroll_max_pct"] == 80
    assert a["flush_count"] == 3
    assert not a["has_seq_gap"], "A has contiguous seq 0..2 — no gap"
    assert a["last_flush_reason"] == "pagehide"

    b = agg.loc["vi-B"]
    assert b["engaged_ms"] == 1500
    assert b["has_seq_gap"], "B is missing seq 1 — gap must be flagged"

    c = agg.loc["vi-C"]
    assert c["engaged_ms"] == 42000 and c["flush_count"] == 1

    print("OK — aggregate_engagement sums deltas, flags gaps, tolerates double-fires")


if __name__ == "__main__":
    main()
