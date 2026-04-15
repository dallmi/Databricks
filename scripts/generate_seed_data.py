"""Generate realistic multi-channel seed data for the cross-channel dashboard.

Produces one JSON file (``dashboard/seed.json``) with three arrays:

    page_views : AppInsights-flavoured pageView records (Intranet channel, INT)
    email_events : iMEP-flavoured per-recipient email events  (channel EMI)
    event_regs : iMEP-flavoured event registrations          (channel EVT)

All three arrays share the same CammsTrackingID space so dashboards can join
them per ``tracking_pack_id`` / ``tracking_id``.

Schema mirrors what ``flatten_appinsights.py`` and the iMEP Bronze Delta
tables produce so the dashboard can be swapped over to live data by
replacing the JSON with a Parquet/Delta export of the same shape.

Run:

    python scripts/generate_seed_data.py
"""

from __future__ import annotations

import json
import random
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path

random.seed(42)


# ---------------------------------------------------------------------------
# Campaign packs — the source of truth for all tracking ids
# ---------------------------------------------------------------------------

@dataclass
class Pack:
    cluster: str
    pack_no: str
    name: str
    publish_date: str          # YYYY-MM-DD
    channels: tuple             # subset of ("EMI", "INT", "EVT")
    audience: int               # target audience size
    theme: str
    topic: str
    target_region: str

PACKS: list[Pack] = [
    Pack("QRREP", "0000058", "Q1 2026 Earnings Call",         "2026-03-05", ("EMI", "INT", "EVT"), 48_000, "Financial Results", "Quarterly Reporting", "Global"),
    Pack("GSTRA", "0000102", "CEO Strategy Update",           "2026-03-10", ("EMI", "INT"),        120_000, "Strategy", "Leadership", "Global"),
    Pack("LEARN", "0000041", "Compliance Training 2026",      "2026-03-12", ("EMI", "INT"),         95_000, "Compliance", "Mandatory Training", "Global"),
    Pack("CSATS", "0000019", "Client Satisfaction Survey",    "2026-03-15", ("EMI",),               75_000, "Client Experience", "Surveys", "APAC"),
    Pack("DIVER", "0000033", "Diversity & Inclusion Week",    "2026-03-18", ("EMI", "INT", "EVT"),  62_000, "D&I", "Culture", "EMEA"),
    Pack("TECHS", "0000077", "Tech Summit 2026",              "2026-03-20", ("EMI", "INT", "EVT"),  18_000, "Innovation", "Tech", "Global"),
    Pack("WELLB", "0000025", "Mental Health Awareness",       "2026-03-22", ("EMI", "INT"),         110_000, "Wellbeing", "HR", "Global"),
    Pack("REGUL", "0000091", "Regulatory Update Bulletin",    "2026-03-25", ("EMI", "INT"),          38_000, "Regulation", "Legal", "Americas"),
    Pack("MRKTG", "0000044", "Brand Relaunch Announcement",   "2026-03-27", ("EMI", "INT"),         140_000, "Brand", "Marketing", "Global"),
    Pack("LEADR", "0000088", "Leadership Town Hall",          "2026-04-02", ("EMI", "INT", "EVT"),  95_000, "Leadership", "Town Hall", "Global"),
    Pack("PROJX", "0000012", "Project X Go-Live",             "2026-04-04", ("EMI", "INT"),          22_000, "Delivery", "Transformation", "EMEA"),
    Pack("SAFER", "0000007", "Cybersecurity Refresher",       "2026-04-06", ("EMI", "INT"),         140_000, "Security", "Mandatory Training", "Global"),
    Pack("INNOV", "0000029", "Innovation Challenge Winners",  "2026-04-08", ("EMI", "INT"),          65_000, "Innovation", "Awards", "Global"),
    Pack("CHARI", "0000014", "Charity Run Sign-up",           "2026-04-10", ("EMI", "EVT"),          28_000, "Community", "Events", "EMEA"),
    Pack("POLCY", "0000003", "New Travel Policy",             "2026-04-12", ("EMI", "INT"),         140_000, "HR", "Policies", "Global"),
]


# ---------------------------------------------------------------------------
# Dimension data
# ---------------------------------------------------------------------------

SITES = [
    ("b96c5556-dc25-4f9a-a547-fec7418a7850", "Home"),
    ("c0d1e2f3-4a5b-6c7d-8e9f-0a1b2c3d4e5f", "News"),
    ("d2e3f4a5-b6c7-d8e9-f0a1-b2c3d4e5f6a7", "Compliance"),
    ("e4f5a6b7-c8d9-e0f1-a2b3-c4d5e6f7a8b9", "HR Central"),
    ("f6a7b8c9-d0e1-f2a3-b4c5-d6e7f8a9b0c1", "TechHub"),
]

DIVISIONS = [
    ("Division A",          "Americas"),
    ("Division A",          "EMEA"),
    ("Division A",          "APAC"),
    ("Division B",          "EMEA"),
    ("Division B",          "APAC"),
    ("Division C",          "Global"),
    ("Group Functions",     "Global"),
    ("Technology",          "Global"),
    ("Division D",          "EMEA"),
]

BROWSERS = ["Chrome 145.0", "Edge 145.0", "Safari 18.0", "Firefox 124.0"]
OS_LIST  = ["Windows 10", "Windows 11", "macOS 14", "iOS 18"]


def iso(dt: datetime) -> str:
    return dt.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%f")[:-3] + "Z"


def tracking_id(pack: Pack, activity_no: int, channel: str) -> str:
    yy = pack.publish_date[2:4] + pack.publish_date[5:7] + pack.publish_date[8:10]
    return f"{pack.cluster}-{pack.pack_no}-{yy}-{str(activity_no).zfill(7)}-{channel}"


# ---------------------------------------------------------------------------
# Channel generators
# ---------------------------------------------------------------------------

def gen_email_events(pack: Pack) -> list[dict]:
    """Mirror iMEP tbl_email_receiver_status: one row per (recipient, event)."""
    if "EMI" not in pack.channels:
        return []

    tid = tracking_id(pack, activity_no=100, channel="EMI")
    send_dt = datetime.fromisoformat(pack.publish_date + "T08:00:00+01:00")

    sent = pack.audience
    delivered = int(sent * random.uniform(0.96, 0.99))
    open_rate = random.uniform(0.35, 0.65)
    click_rate_of_opens = random.uniform(0.10, 0.30)
    opened = int(delivered * open_rate)
    clicked = int(opened * click_rate_of_opens)
    bounced = sent - delivered

    out = []
    out.append({"event": "sent",      "count": sent,      "timestamp": iso(send_dt),                         "tracking_id": tid})
    out.append({"event": "delivered", "count": delivered, "timestamp": iso(send_dt + timedelta(minutes=2)),  "tracking_id": tid})
    out.append({"event": "bounced",   "count": bounced,   "timestamp": iso(send_dt + timedelta(minutes=5)),  "tracking_id": tid})
    # Open/click times spread out over 7 days (heavy first 24h)
    out.append({"event": "opened",    "count": opened,    "timestamp": iso(send_dt + timedelta(hours=2)),    "tracking_id": tid})
    out.append({"event": "clicked",   "count": clicked,   "timestamp": iso(send_dt + timedelta(hours=3)),    "tracking_id": tid})
    return [dict(r, cluster=pack.cluster, pack_id=f"{pack.cluster}-{pack.pack_no}",
                 pack_name=pack.name, channel="EMI") for r in out]


def gen_page_views(pack: Pack) -> list[dict]:
    """Mirror flatten_appinsights fact_page_view shape."""
    if "INT" not in pack.channels:
        return []

    tid_int = tracking_id(pack, activity_no=200, channel="INT")
    site_id, site_name = random.choice(SITES)
    base_dt = datetime.fromisoformat(pack.publish_date + "T06:00:00+01:00")

    # Keep seed file compact: cap raw views per pack and carry a `weight`
    # so aggregates still reflect true audience reach. The dashboard
    # multiplies metrics by weight when rendering.
    if "EMI" in pack.channels:
        true_views = int(pack.audience * random.uniform(0.04, 0.12))
    else:
        true_views = int(pack.audience * random.uniform(0.02, 0.06))
    views_total = min(true_views, 200)
    weight = true_views / views_total if views_total else 1

    out = []
    for i in range(views_total):
        division, region = random.choice(DIVISIONS)
        delta_min = int(abs(random.gauss(60 * 24, 60 * 48)))     # peak first 24-48h
        ts = base_dt + timedelta(minutes=delta_min)
        # 20% of views miss the tracking id (direct access without campaign link)
        tid = tid_int if random.random() > 0.2 else None
        out.append({
            "timestamp":         iso(ts),
            "weight":            round(weight, 3),
            "page_id":           f"{pack.cluster}-{pack.pack_no}-page",
            "page_name":         pack.name,
            "site_id":           site_id,
            "site_name":         site_name,
            "time_on_page_sec":  round(random.uniform(15, 240), 1),
            "client_country":    random.choice(["CH", "US", "GB", "SG", "DE", "HK", "IN"]),
            "hr_division":       division,
            "hr_region":         region,
            "theme":             pack.theme,
            "topic":             pack.topic,
            "tracking_pack_id":      f"{pack.cluster}-{pack.pack_no}" if tid else None,
            "tracking_cluster_id":   pack.cluster if tid else None,
            "tracking_channel_abbr": "INT" if tid else None,
            "channel":           "INT",
        })
    return out


def gen_event_regs(pack: Pack) -> list[dict]:
    """Mirror iMEP tbl_event_registration shape (aggregated by event status)."""
    if "EVT" not in pack.channels:
        return []

    tid = tracking_id(pack, activity_no=300, channel="EVT")
    event_dt = datetime.fromisoformat(pack.publish_date + "T14:00:00+01:00") + timedelta(days=14)

    registered = int(pack.audience * random.uniform(0.02, 0.08))
    attended   = int(registered * random.uniform(0.55, 0.80))
    no_show    = registered - attended

    out = [
        {"status": "registered", "count": registered, "event_datetime": iso(event_dt)},
        {"status": "attended",   "count": attended,   "event_datetime": iso(event_dt)},
        {"status": "no_show",    "count": no_show,    "event_datetime": iso(event_dt)},
    ]
    return [dict(r, cluster=pack.cluster, pack_id=f"{pack.cluster}-{pack.pack_no}",
                 pack_name=pack.name, channel="EVT", tracking_id=tid) for r in out]


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main():
    repo_root = Path(__file__).resolve().parent.parent
    out_dir = repo_root / "dashboard"
    out_dir.mkdir(parents=True, exist_ok=True)

    page_views: list[dict] = []
    email_events: list[dict] = []
    event_regs: list[dict] = []

    for p in PACKS:
        email_events.extend(gen_email_events(p))
        page_views.extend(gen_page_views(p))
        event_regs.extend(gen_event_regs(p))

    packs_meta = [
        {
            "pack_id":          f"{p.cluster}-{p.pack_no}",
            "cluster":          p.cluster,
            "name":             p.name,
            "publish_date":     p.publish_date,
            "channels":         list(p.channels),
            "audience":         p.audience,
            "theme":            p.theme,
            "topic":            p.topic,
            "target_region":    p.target_region,
        }
        for p in PACKS
    ]

    seed = {
        "generated_at": iso(datetime.now(timezone.utc)),
        "packs":        packs_meta,
        "email_events": email_events,
        "page_views":   page_views,
        "event_regs":   event_regs,
    }

    out_path = out_dir / "seed.json"
    out_path.write_text(json.dumps(seed, indent=1))

    print(f"Wrote {out_path}")
    print(f"  packs:        {len(packs_meta):,}")
    print(f"  email_events: {len(email_events):,}  rows")
    print(f"  page_views:   {len(page_views):,}  rows")
    print(f"  event_regs:   {len(event_regs):,}  rows")
    print(f"  total size:   {out_path.stat().st_size / 1024:.1f} KB")


if __name__ == "__main__":
    main()
