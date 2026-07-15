"""
Tests the flattener mappings for the page_engagement customEvents family
(spec: docs/superpowers/specs/2026-07-15-page-engagement-heartbeat-design.md §3.5).

Plain-assert script (no test framework in this repo):
    python SiteOwnerDashboard/scripts/test_flatten_page_engagement.py
Exits non-zero on the first failing assertion.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pandas as pd

SCRIPT_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(SCRIPT_DIR))
# process_site_pageviews puts the shared pipeline dir on sys.path
from process_site_pageviews import canonical_page_key  # noqa: E402,F401
from flatten_appinsights import (  # noqa: E402
    flatten_appinsights,
    build_clean_interactions_table,
    build_clean_table,
)


def make_custom_dimensions(props: dict) -> str:
    """AppInsights double-nesting: customDimensions.CustomProps is a JSON string."""
    return json.dumps({"CustomProps": json.dumps(props)})


def engagement_raw() -> pd.DataFrame:
    """One page_engagement flush as it arrives in a customEvents export."""
    props = {
        "GPN": "00100200", "Email": "a@corp.example",
        "SiteID": "s-1", "SiteName": "News and events",
        "PageId": "p-42", "PageName": "Article",
        "PageURL": "https://intranet.example/en/article.aspx",
        "View_Instance_Id": "vi-0001",
        "Engaged_Ms": "5000", "Scroll_Max_Pct": "40",
        "Page_Height_Px": "3200", "Viewport_Height_Px": "900",
        "Flush_Reason": "route", "Flush_Seq": "0",
    }
    return pd.DataFrame([{
        "timestamp [UTC]": "2026-07-15 08:00:00.000",
        "id": "ev-1", "name": "page_engagement",
        "user_Id": "u-1", "session_Id": "sess-1",
        "client_OS": "Windows", "client_Browser": "Edge",
        "client_CountryOrRegion": "Switzerland",
        "customDimensions": make_custom_dimensions(props),
    }])


def pageview_raw() -> pd.DataFrame:
    """One pageView carrying View_Instance_Id in its property bag (spec §3.5/§9)."""
    props = {
        "GPN": "00100200", "PageId": "p-42",
        "PageURL": "https://intranet.example/en/article.aspx",
        "View_Instance_Id": "vi-0001",
    }
    return pd.DataFrame([{
        "timestamp [UTC]": "2026-07-15 08:00:00.000",
        "id": "pv-1", "name": "https://intranet.example/en/article.aspx",
        "user_Id": "u-1", "session_Id": "sess-1", "duration": "123",
        "customDimensions": make_custom_dimensions(props),
    }])


def main() -> None:
    # customEvents side
    clean = build_clean_interactions_table(flatten_appinsights(engagement_raw()))
    for col in ("view_instance_id", "engaged_ms_delta", "scroll_max_pct",
                "page_height_px", "viewport_height_px", "flush_reason", "flush_seq"):
        assert col in clean.columns, f"missing engagement column: {col}"
    row = clean.iloc[0]
    assert row["event_name"] == "page_engagement"
    assert row["view_instance_id"] == "vi-0001"
    assert str(row["engaged_ms_delta"]) == "5000", row["engaged_ms_delta"]
    assert str(row["flush_reason"]) == "route"

    # pageViews side
    pv = build_clean_table(flatten_appinsights(pageview_raw()))
    assert "view_instance_id" in pv.columns, "pageViews flatten must map View_Instance_Id"
    assert pv.iloc[0]["view_instance_id"] == "vi-0001"

    # click_event untouched: a click without the new keys still flattens clean
    click = engagement_raw().assign(name="click_event")
    cc = build_clean_interactions_table(flatten_appinsights(click))
    assert cc.iloc[0]["event_name"] == "click_event"

    print("OK — flattener maps page_engagement payload on both streams")


if __name__ == "__main__":
    main()
