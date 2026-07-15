"""
Tests for derive_person_visit() in process_site_pageviews.py — specifically the
visit-based time-on-page column `time_on_page_visit_sec`.

Background (2026-07-15): on the corp source the official AppInsights session_id
is minted fresh on most navigations (92% of sessions have exactly 1 view), so
`time_on_page_sec` (gap to the next view in the SAME session_id) is NULL for
~87% of views and the rest is biased toward same-instant double-fires — the
dashboard showed "0s" Avg time on 100k-view pages. `time_on_page_visit_sec`
computes the SAME gap-to-next-view, but grouped by the reconstructed `visit_id`
(person + 30-min inactivity rule), which is immune to session_id resets.

Plain-assert script (no test framework in this repo):
    python scripts/test_derive_person_visit.py
Exits non-zero on the first failing assertion.
"""

from __future__ import annotations

import math
import sys
from pathlib import Path

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent))
from process_site_pageviews import derive_person_visit  # noqa: E402


def ts(s: str) -> pd.Timestamp:
    return pd.Timestamp(s)


def build_input() -> pd.DataFrame:
    """Four persons exercising the session-reset, gap and boundary cases."""
    rows = [
        # Person A (GPN): one sitting, but session_id resets per navigation —
        # the corp pattern. Views: EN entry -> 0.3s redirect (same session,
        # the double-fire that produced the "0s" artifacts) -> article 39.7s
        # later (NEW session) -> back to start 140s later (NEW session again).
        dict(timestamp=ts("2026-06-01 09:00:00.000"), user_id="devA", session_id="S1", gpn="00123456"),
        dict(timestamp=ts("2026-06-01 09:00:00.300"), user_id="devA", session_id="S1", gpn="00123456"),
        dict(timestamp=ts("2026-06-01 09:00:40.000"), user_id="devA", session_id="S2", gpn="00123456"),
        dict(timestamp=ts("2026-06-01 09:03:00.000"), user_id="devA", session_id="S3", gpn="00123456"),
        # Person B (GPN): two views 31 min apart INSIDE one source session —
        # a new visit starts (gap > 30 min), so neither view gets a visit time.
        dict(timestamp=ts("2026-06-01 10:00:00.000"), user_id="devB", session_id="S9", gpn="00234567"),
        dict(timestamp=ts("2026-06-01 10:31:00.000"), user_id="devB", session_id="S9", gpn="00234567"),
        # Person C (no GPN -> anon:<user_id>): 60s apart, distinct sessions.
        dict(timestamp=ts("2026-06-01 11:00:00.000"), user_id="devC", session_id="S5", gpn=None),
        dict(timestamp=ts("2026-06-01 11:01:00.000"), user_id="devC", session_id="S6", gpn=None),
        # Person D (GPN): exactly 30 min apart — boundary stays ONE visit
        # (new visit only when the gap EXCEEDS 30 min), so the gap is measured.
        dict(timestamp=ts("2026-06-01 12:00:00.000"), user_id="devD", session_id="S7", gpn="00345678"),
        dict(timestamp=ts("2026-06-01 12:30:00.000"), user_id="devD", session_id="S8", gpn="00345678"),
    ]
    return pd.DataFrame(rows)


def check(label: str, actual, expected) -> None:
    if expected is None:
        ok = actual is None or (isinstance(actual, float) and math.isnan(actual)) or pd.isna(actual)
    else:
        ok = actual is not None and not pd.isna(actual) and math.isclose(actual, expected, abs_tol=1e-6)
    assert ok, f"{label}: expected {expected}, got {actual}"
    print(f"  ok  {label} = {expected}")


def main() -> None:
    df = derive_person_visit(build_input())

    assert "time_on_page_visit_sec" in df.columns, \
        "time_on_page_visit_sec column missing from derive_person_visit output"

    tos_v = df["time_on_page_visit_sec"].tolist()
    tos_s = df["time_on_page_sec"].tolist()

    print("visit-based time on page (time_on_page_visit_sec):")
    # Person A: one visit spanning the session resets.
    check("A view1 (0.3s to redirect)", tos_v[0], 0.3)
    check("A view2 (39.7s to article, ACROSS session reset)", tos_v[1], 39.7)
    check("A view3 (140s to next view, ACROSS session reset)", tos_v[2], 140.0)
    check("A view4 (last of visit)", tos_v[3], None)
    # Person B: 31-min gap splits the visit — no measurable time.
    check("B view1 (gap > 30 min -> new visit)", tos_v[4], None)
    check("B view2 (last of its visit)", tos_v[5], None)
    # Person C: anonymous person still gets visit times.
    check("C view1 (anon, 60s)", tos_v[6], 60.0)
    check("C view2 (anon, last of visit)", tos_v[7], None)
    # Person D: exactly-30-min boundary stays one visit.
    check("D view1 (exactly 1800s kept)", tos_v[8], 1800.0)
    check("D view2 (last of visit)", tos_v[9], None)

    print("session-based time_on_page_sec unchanged:")
    check("A view1 (0.3s within S1)", tos_s[0], 0.3)
    check("A view2 (last in S1)", tos_s[1], None)
    check("A view3 (only view of S2)", tos_s[2], None)
    check("A view4 (only view of S3)", tos_s[3], None)
    check("B view1 (1860s within S9 -> capped away)", tos_s[4], None)

    # Source columns untouched; existing derived columns still present.
    assert df["session_id"].tolist()[:4] == ["S1", "S1", "S2", "S3"], "session_id was modified"
    assert df["user_id"].tolist()[0] == "devA", "user_id was modified"
    for col in ("person_id", "visit_id", "is_last_in_session"):
        assert col in df.columns, f"existing derived column {col} missing"
    # Person A's four views must share ONE visit despite three session_ids.
    assert df["visit_id"].iloc[:4].nunique() == 1, "person A views did not land in one visit"

    print("\nAll assertions passed.")


if __name__ == "__main__":
    main()
