"""
Generate a realistic demo Parquet for the SiteOwnerDashboard so it runs out of
the box without any AppInsights export.

Emits output/site_pageviews.parquet with the SAME columns
process_site_pageviews.py produces from a real export, for one site
("News and events"): ~13 months of page views grouped into sessions, with HR
division/region, content type, language (from URL), time-on-page,
CammsTrackingID on the News articles, and staggered publishing dates with
age-weighted views (burst + decay) so the Content Lifecycle tab shows
realistic curves.

Run (from the SiteOwnerDashboard project root):
    python scripts/generate_demo_data.py
"""

from __future__ import annotations

import uuid
from datetime import datetime, timedelta
from pathlib import Path

import numpy as np
import pandas as pd

RNG = np.random.default_rng(42)
SITE_NAME = "News and events"
SITE_ID = "5313b145-5ead-43ba-b317-6955e804881a"
REF_TODAY = datetime(2026, 7, 1)
START = REF_TODAY - timedelta(days=395)  # ~13 months so 12mo trend + prior-period MoM both have data

# --- Page catalogue: (name, content_type, language, base_popularity, publish_offset) ---
# publish_offset = days from START; negative = published before the data window
# (evergreen). Views are weighted by content age so news burst after publish and
# decay — this is what the Content Lifecycle tab visualises.
PAGES = [
    ("Q1 2026 Results Announced",        "Article",  "EN", 1.00, 318),
    ("Q1 2026 Ergebnisse",               "Article",  "DE", 0.62, 318),
    ("CEO Town Hall Recording",          "Video",    "EN", 0.78, 254),
    ("New Operating Model Explained",    "Article",  "EN", 0.71,  30),
    ("Sustainability Report 2025 (PDF)", "Download", "EN", 0.55, 273),
    ("Nachhaltigkeitsbericht 2025",      "Download", "DE", 0.34, 273),
    ("Leadership Changes Q2",            "Article",  "EN", 0.66, 353),
    ("Rapport annuel 2025",              "Download", "FR", 0.22, 277),
    ("Employee Benefits Update",         "Article",  "EN", 0.58, -30),
    ("Cyber Security Awareness Week",    "Article",  "EN", 0.49, 122),
    ("Relazione trimestrale",            "Article",  "IT", 0.15, 323),
    ("Office Reopening Guidelines",      "Article",  "EN", 0.44, -90),
    ("Innovation Awards 2026",           "Article",  "EN", 0.52, 365),
    ("Diversity & Inclusion Panel",      "Video",    "EN", 0.41, 233),
    ("Town Hall — Slides (PDF)",         "Download", "EN", 0.38, 254),
    ("Year in Review 2025",              "Video",    "EN", 0.47, 197),
    ("Volunteering Day Highlights",      "Article",  "EN", 0.29,   9),
    ("Nouveau modèle opérationnel",      "Article",  "FR", 0.19,  30),
]


def age_factor(age_days: float, content_type: str) -> float:
    """Interest in a page as a function of its age: burst after publish,
    fast decay over ~2 weeks, then a slow tail (Downloads stay referenced)."""
    if age_days < 0:
        return 0.0
    tail = 0.30 if content_type == "Download" else 0.12
    return 6.0 * np.exp(-age_days / 4.0) + 1.2 * np.exp(-age_days / 45.0) + tail

DIVISIONS = [
    ("Investment Bank",              0.27),
    ("Global Wealth Management",     0.31),
    ("Personal & Corporate Banking", 0.21),
    ("Asset Management",             0.12),
    ("Group Functions",              0.09),
]
REGIONS = [("SWITZERLAND", 0.44), ("EMEA", 0.30), ("AMERICAS", 0.16), ("APAC", 0.10)]
OSES = ["Windows 10", "Windows 11", "Mac OS X", "iOS", "Android"]
BROWSERS = ["Edge", "Chrome", "Safari", "Firefox"]
COUNTRY_BY_REGION = {"SWITZERLAND": "Switzerland", "EMEA": "United Kingdom",
                     "AMERICAS": "United States", "APAC": "Singapore"}

LANG_SEG = {"EN": "en", "DE": "de", "FR": "fr", "IT": "it"}


def slug(name: str) -> str:
    keep = [c.lower() if c.isalnum() else "-" for c in name]
    s = "".join(keep)
    while "--" in s:
        s = s.replace("--", "-")
    return s.strip("-")


def page_url(name: str, lang: str) -> str:
    return f"https://intranet.corp/sites/news-and-events/{LANG_SEG[lang]}/2026/{slug(name)}"


def weighted_choice(pairs):
    labels = [p[0] for p in pairs]
    weights = np.array([p[1] for p in pairs], dtype=float)
    return labels, weights / weights.sum()


def main():
    page_ids = [str(uuid.UUID(bytes=bytes(RNG.integers(0, 256, size=16, dtype=np.uint8)))) for _ in PAGES]
    pop = np.array([p[3] for p in PAGES], dtype=float)
    pub_dt = {i: START + timedelta(days=p[4], hours=9) for i, p in enumerate(PAGES)}

    # Per-day page weights: popularity x age_factor(day - publish_day).
    days_total = (REF_TODAY - START).days
    day_page_w = np.zeros((days_total, len(PAGES)))
    for d in range(days_total):
        for i, p in enumerate(PAGES):
            day_page_w[d, i] = pop[i] * age_factor(d - p[4], p[1])
    day_page_w = day_page_w / day_page_w.sum(axis=1, keepdims=True)

    div_labels, div_p = weighted_choice(DIVISIONS)
    reg_labels, reg_p = weighted_choice(REGIONS)

    # User pool — reused across sessions so Unique Visitors < Page Visits (engagement ~0.35)
    n_users = 5200
    users = [f"u{ i:05d}" for i in range(n_users)]
    user_gpn = {u: str(RNG.integers(1_000_000, 9_999_999)).zfill(8) for u in users}
    user_div = {u: div_labels[RNG.choice(len(div_labels), p=div_p)] for u in users}
    user_reg = {u: reg_labels[RNG.choice(len(reg_labels), p=reg_p)] for u in users}

    n_sessions = 15000
    days_span = (REF_TODAY - START).days
    # Upward trend: later days more likely (linear ramp 0.6 -> 1.4)
    day_idx = np.arange(days_span)
    day_weight = 0.6 + 0.8 * (day_idx / days_span)
    day_weight = day_weight / day_weight.sum()
    sess_days = RNG.choice(day_idx, size=n_sessions, p=day_weight)

    rows = []
    for s in range(n_sessions):
        u = users[RNG.integers(0, n_users)]
        day = START + timedelta(days=int(sess_days[s]))
        # working-hours-ish start time
        start = day + timedelta(hours=int(RNG.integers(6, 19)),
                                minutes=int(RNG.integers(0, 60)),
                                seconds=int(RNG.integers(0, 60)))
        session_id = f"s-{s:06d}"
        n_views = 1 + int(RNG.poisson(1.1))  # ~bounce-heavy: many 1-2 page sessions
        n_views = min(n_views, 6)
        t = start
        chosen = RNG.choice(len(PAGES), size=n_views, p=day_page_w[int(sess_days[s])])
        for vi, pidx in enumerate(chosen):
            name, ct, lang = PAGES[pidx][:3]
            has_tid = ct == "Article"  # only News articles carry a CammsTrackingID
            rows.append({
                "view_id": str(uuid.uuid4()),
                "timestamp": t,
                "session_id": session_id,
                "user_id": u,
                "page_id": page_ids[pidx],
                "page_name": name,
                "page_url": page_url(name, lang),
                "site_id": SITE_ID,
                "site_name": SITE_NAME,
                "content_type": ct,
                "content_owner": "Group Internal Communications",
                "publishing_date": pub_dt[int(pidx)],
                "language": lang,
                "gpn": user_gpn[u],
                "hr_division": user_div[u],
                "hr_region": user_reg[u],
                "client_os": OSES[RNG.integers(0, len(OSES))],
                "client_browser": BROWSERS[RNG.integers(0, len(BROWSERS))],
                "client_country": COUNTRY_BY_REGION[user_reg[u]],
                "page_load_ms": int(RNG.integers(180, 2600)),
                "tracking_id": f"QRREP-{1000+pidx:07d}-260215-{2000+pidx:07d}-EMI" if has_tid else None,
                "tracking_pack_id": f"QRREP-{1000+pidx:07d}" if has_tid else None,
                "tracking_channel_abbr": "EMI" if has_tid else None,
                "_seq": vi,
            })
            # dwell before next view: log-normal seconds
            dwell = float(np.clip(RNG.lognormal(mean=4.0, sigma=0.9), 5, 1800))
            t = t + timedelta(seconds=dwell)

    df = pd.DataFrame(rows).sort_values(["session_id", "timestamp"]).reset_index(drop=True)

    # time-on-page = delta to next view in session, capped 30 min; last view = NULL
    nxt = df.groupby("session_id")["timestamp"].shift(-1)
    tos = (nxt - df["timestamp"]).dt.total_seconds()
    df["is_last_in_session"] = nxt.isna()
    tos = tos.where(tos <= 1800, other=np.nan)
    df["time_on_page_sec"] = tos
    df = df.drop(columns=["_seq"])

    out = Path(__file__).resolve().parents[1] / "output" / "site_pageviews.parquet"
    out.parent.mkdir(parents=True, exist_ok=True)
    df.to_parquet(out, index=False)

    print(f"Wrote {len(df):,} page views -> {out}")
    print(f"  sessions:  {df['session_id'].nunique():,}")
    print(f"  users:     {df['user_id'].nunique():,}")
    print(f"  pages:     {df['page_id'].nunique()}")
    print(f"  range:     {df['timestamp'].min()}  ->  {df['timestamp'].max()}")
    print(f"  languages: {dict(df['language'].value_counts())}")


if __name__ == "__main__":
    main()
