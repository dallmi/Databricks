"""
Tests the flattener mappings for the Video sub-domain of click_event.

Regression guard: Video_Title / Video_Address / Video_PlayedTime were present in
the export's raw CustomProps from day one but never mapped, so "Top videos" fell
back to Video_Id and rendered opaque asset ids (ASSETID-550417190-2606).

Plain-assert script (no test framework in this repo):
    python SiteOwnerDashboard/scripts/test_flatten_video.py
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
)
from process_site_interactions import IX_COLUMNS  # noqa: E402


def make_custom_dimensions(props: dict) -> str:
    """AppInsights double-nesting: customDimensions.CustomProps is a JSON string."""
    return json.dumps({"CustomProps": json.dumps(props)})


def video_raw() -> pd.DataFrame:
    """One video click_event as it arrives in a customEvents export.

    Link_label is absent — embedded players emit the readable name only as
    Video_Title, which is exactly the case that produced the asset-id labels.
    """
    props = {
        "GPN": "00100200",
        "SiteID": "s-1", "SiteName": "News and events",
        "PageId": "p-42", "PageName": "Town Hall",
        "PageURL": "https://intranet.example/en/townhall.aspx",
        "ComponentName": "VideoPlayer", "Link_Type": "video",
        "Video_Action": "play",
        "Video_Id": "ASSETID-550417190-2606",
        "Video_Title": "CEO Town Hall Q3",
        "Video_Type": "OnDemand",
        "Video_Duration": "254",
        "Video_PlayedTime": "88",
        "Video_Address": "https://video.corp/watch/townhall",
    }
    return pd.DataFrame([{
        "timestamp [UTC]": "2026-07-01 10:00:00.000",
        "name": "click_event",
        "user_Id": "u-1", "session_Id": "sess-1",
        "client_OS": "Windows", "client_Browser": "Edge",
        "client_CountryOrRegion": "Switzerland",
        "customDimensions": make_custom_dimensions(props),
    }])


def main() -> None:
    clean = build_clean_interactions_table(flatten_appinsights(video_raw()))

    for col in ("video_action", "video_id", "video_title", "video_type",
                "video_duration", "video_played_time", "video_address"):
        assert col in clean.columns, f"missing video column: {col}"

    row = clean.iloc[0]
    assert row["video_title"] == "CEO Town Hall Q3", row["video_title"]
    assert row["video_id"] == "ASSETID-550417190-2606", row["video_id"]
    assert str(row["video_played_time"]) == "88", row["video_played_time"]
    assert row["video_address"] == "https://video.corp/watch/townhall"

    # The dashboard only sees columns the writer keeps — a mapping that never
    # reaches IX_COLUMNS is invisible, which is how the original gap survived.
    for col in ("video_title", "video_played_time", "video_address"):
        assert col in IX_COLUMNS, f"{col} mapped but dropped before the parquet"

    # Label resolution: title wins over the opaque asset id, and a video without
    # a title still degrades to the id rather than vanishing.
    untitled = video_raw()
    props = json.loads(json.loads(untitled.at[0, "customDimensions"])["CustomProps"])
    del props["Video_Title"]
    untitled.at[0, "customDimensions"] = make_custom_dimensions(props)
    u = build_clean_interactions_table(flatten_appinsights(untitled))
    assert "video_title" not in u.columns or pd.isna(u.iloc[0].get("video_title")), \
        "absent Video_Title must not fabricate a value"
    assert u.iloc[0]["video_id"] == "ASSETID-550417190-2606"

    print("OK — flattener maps the Video sub-domain incl. Video_Title")


if __name__ == "__main__":
    main()
