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

Also emits output/site_interactions.parquet (Phase 2) with the SAME columns
process_site_interactions.py produces: click_event interactions CORRELATED
with the page views above — clicks are a subset of views (per-page CTR
~2–35 % depending on content type), Download pages and some articles carry
file downloads, Video pages get play actions, every page has a small link
catalogue with a dominant top link + long tail, and a few links stop being
clicked mid-window (the "declining link" story for Last clicked).

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
    # Genuinely multilingual pages: SAME name/slug across languages, so they
    # share one page_key and collapse to a single Pages-table row whose
    # Page → URL/Language drill reveals the per-language variants.
    ("Global Strategy Update",           "Article",  "EN", 0.64, 200),
    ("Global Strategy Update",           "Article",  "DE", 0.30, 200),
    ("Global Strategy Update",           "Article",  "FR", 0.22, 200),
    ("Global Strategy Update",           "Article",  "IT", 0.12, 200),
    ("Code of Conduct 2026 (PDF)",       "Download", "EN", 0.40, 140),
    ("Code of Conduct 2026 (PDF)",       "Download", "DE", 0.24, 140),
    ("Code of Conduct 2026 (PDF)",       "Download", "FR", 0.16, 140),
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

# --- GCRS org hierarchy: Division -> Unit -> Area -> Sector -----------------
# Mirrors the shared pipeline's hr_unit/hr_area/hr_sector columns so the
# dashboard's Division drilldown (Division -> Unit -> Area -> Sector) has data.
ORG = {
    "Investment Bank": {
        "Global Banking": {"M&A Advisory": ["EMEA Deals", "Americas Deals"],
                           "Capital Markets": ["Equity CM", "Debt CM"]},
        "Global Markets": {"Equities": ["Cash Equities", "Derivatives"],
                           "FX & Rates": ["FX Spot", "Rates Trading"]},
    },
    "Global Wealth Management": {
        "Client Advisory": {"UHNW": ["UHNW EMEA", "UHNW APAC"],
                            "HNW": ["HNW Europe", "HNW Americas"]},
        "Investment Products": {"Discretionary": ["Mandates", "Funds"],
                                "Advisory": ["Research", "Structured Products"]},
    },
    "Personal & Corporate Banking": {
        "Retail": {"Branch Network": ["Region North", "Region South"],
                   "Digital Banking": ["Mobile", "Online"]},
        "Corporate": {"SME Banking": ["DACH", "France"],
                      "Trade Finance": ["Import", "Export"]},
    },
    "Asset Management": {
        "Equities": {"Active": ["Global Equities", "Regional Equities"],
                     "Passive": ["Index Funds", "ETFs"]},
        "Fixed Income": {"Credit": ["Investment Grade", "High Yield"],
                         "Rates": ["Government", "Inflation"]},
    },
    "Group Functions": {
        "Technology": {"Engineering": ["Platform", "Data"],
                       "Cyber": ["SecOps", "Governance & Risk"]},
        "Finance & Risk": {"Controlling": ["FP&A", "Reporting"],
                           "Risk": ["Market Risk", "Credit Risk"]},
    },
}

# --- Region -> Country distribution (work location; several per region) -----
REGION_COUNTRIES = {
    "SWITZERLAND": [("Switzerland", 0.85), ("Liechtenstein", 0.15)],
    "EMEA": [("United Kingdom", 0.40), ("Germany", 0.25), ("France", 0.20), ("UAE", 0.15)],
    "AMERICAS": [("United States", 0.70), ("Brazil", 0.16), ("Canada", 0.14)],
    "APAC": [("Singapore", 0.40), ("Hong Kong", 0.35), ("Japan", 0.25)],
}

# --- Theme -> Topic pool (per page; mirrors cp_Theme/cp_Topic) --------------
THEMES = {
    "Corporate Strategy": ["Results", "Operating Model", "Leadership"],
    "Culture & People": ["Benefits", "Diversity", "Volunteering"],
    "Technology & Security": ["Cyber Security", "Innovation"],
    "Sustainability": ["ESG Report", "Climate"],
}

LANG_SEG = {"EN": "en", "DE": "de", "FR": "fr", "IT": "it"}

# --- Phase 2: link catalogue building blocks (site_interactions.parquet) ----
# (label, link_type) pool the per-page catalogues sample from. Weights are
# Zipf-ish per page so one link dominates and the tail is rarely clicked.
LINK_POOL = [
    ("Read the full story",        "internal"),
    ("Related: strategy hub",      "internal"),
    ("All news",                   "internal"),
    ("Contact the editorial team", "mailto"),
    ("Share on Workplace",         "external"),
    ("Register for the event",     "external"),
    ("Previous coverage",          "internal"),
    ("Leadership bios",            "internal"),
    ("Media gallery",              "internal"),
]
COMPONENTS = ["HeroBanner", "RelatedLinks", "QuickLinks", "BodyCopy"]
# Base probability that a page view produces at least one interaction.
CTR_BASE = {"Article": 0.08, "Download": 0.35, "Video": 0.50}


def walk_org(division: str):
    """Pick a deterministic-ish (RNG-seeded) Unit -> Area -> Sector path."""
    units = list(ORG[division])
    unit = units[RNG.integers(0, len(units))]
    areas = list(ORG[division][unit])
    area = areas[RNG.integers(0, len(areas))]
    sectors = ORG[division][unit][area]
    sector = sectors[RNG.integers(0, len(sectors))]
    return unit, area, sector


def pick_country(region: str) -> str:
    pairs = REGION_COUNTRIES[region]
    labels = [c for c, _ in pairs]
    w = np.array([p for _, p in pairs], dtype=float)
    return labels[RNG.choice(len(labels), p=w / w.sum())]


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


# --- Phase 2: interactions (clicks / downloads / video) ---------------------
# All RNG draws for interactions happen AFTER the page-view generation, so the
# pageviews parquet stays identical to the Phase-1 demo output.

def build_link_catalogs():
    """Per-page link catalogue: dominant top link + long tail, plus a file
    download on Download pages (and some articles) and video actions on Video
    pages. A few links 'decline' — no clicks in the last ~75 days."""
    catalogs = {}
    decline_cutoff = REF_TODAY - timedelta(days=75)
    for i, (name, ct, lang, *_rest) in enumerate(PAGES):
        links = []
        n_links = 3 + int(RNG.integers(0, 3))
        idxs = RNG.choice(len(LINK_POOL), size=n_links, replace=False)
        for rank, j in enumerate(idxs):
            label, ltype = LINK_POOL[int(j)]
            links.append({
                "component_name": COMPONENTS[int(RNG.integers(0, len(COMPONENTS)))],
                "link_type": ltype,
                "link_label": label,
                "link_address": f"https://intranet.corp/{slug(label)}",
                "weight": 1.0 / (rank + 1) ** 1.3,
                "active_before": decline_cutoff if (rank == n_links - 1 and RNG.random() < 0.35) else None,
                "file_type_label": None, "file_name_label": None, "video": False,
            })
        has_file = ct == "Download" or (ct == "Article" and RNG.random() < 0.3)
        if has_file:
            ftype = "PDF" if RNG.random() < 0.75 else "XLSX"
            fname = f"{slug(name)}.{ftype.lower()}"
            links.append({
                "component_name": "DocumentList",
                "link_type": "download",
                "link_label": name if ct == "Download" else f"{name} — briefing",
                "link_address": f"https://intranet.corp/docs/{fname}",
                "weight": 3.0 if ct == "Download" else 0.8,
                "active_before": None,
                "file_type_label": ftype, "file_name_label": fname, "video": False,
            })
        if ct == "Video":
            links.append({
                "component_name": "VideoPlayer",
                "link_type": "video",
                "link_label": name,
                "link_address": f"https://video.corp/watch/{slug(name)}",
                "weight": 3.0,
                "active_before": None,
                "file_type_label": None, "file_name_label": None, "video": True,
            })
        catalogs[i] = links
    return catalogs


def build_interactions(df, page_ids):
    """One click_event row per interaction, correlated with the views in df."""
    catalogs = build_link_catalogs()
    idx_by_page_id = {pid: i for i, pid in enumerate(page_ids)}
    # Per-page CTR multiplier 0.3–2.0: some high-interest pages get a LOW CTR
    # (the "unused potential" quadrant the dashboard should surface).
    ctr = {i: float(np.clip(CTR_BASE[PAGES[i][1]] * RNG.lognormal(0.0, 0.5), 0.01, 0.6))
           for i in range(len(PAGES))}
    video_ids = {i: f"v-{1000 + i}" for i in range(len(PAGES))}

    rows = []
    hr_cols = ["hr_division", "hr_unit", "hr_area", "hr_sector", "hr_region", "hr_country"]
    for row in df.itertuples(index=False):
        i = idx_by_page_id[row.page_id]
        if RNG.random() >= ctr[i]:
            continue
        n_clicks = 1 + (1 if RNG.random() < 0.25 else 0)
        links = catalogs[i]
        t = row.timestamp
        for _ in range(n_clicks):
            active = [l for l in links
                      if l["active_before"] is None or t < l["active_before"]]
            if not active:
                continue
            w = np.array([l["weight"] for l in active], dtype=float)
            link = active[int(RNG.choice(len(active), p=w / w.sum()))]
            t = t + timedelta(seconds=float(RNG.uniform(3, 90)))
            is_video = link["video"]
            r = {
                "event_id": str(uuid.uuid4()),
                "timestamp": t,
                "event_name": "click_event",
                "page_id": row.page_id,
                "page_key": row.page_key,
                "page_name": row.page_name,
                "page_url": row.page_url,
                "language": row.language,
                "site_id": row.site_id,
                "site_name": row.site_name,
                "content_owner": row.content_owner,
                "content_type": row.content_type,
                "theme": row.theme,
                "topic": row.topic,
                "publishing_date": row.publishing_date,
                "user_id": row.user_id,
                "session_id": row.session_id,
                "person_id": row.person_id,
                "gpn": row.gpn,
                "client_os": row.client_os,
                "client_browser": row.client_browser,
                "client_country": row.client_country,
                "component_name": link["component_name"],
                "link_type": link["link_type"],
                "link_label": link["link_label"],
                "link_address": link["link_address"],
                "link_ancestors": f"{row.page_name} > {link['component_name']}",
                "file_type_label": link["file_type_label"],
                "file_name_label": link["file_name_label"],
                "video_action": (["Play", "Play", "Play", "Pause", "Complete"]
                                 [int(RNG.integers(0, 5))] if is_video else None),
                "video_id": video_ids[i] if is_video else None,
                "video_type": "OnDemand" if is_video else None,
                "video_duration": str(int(RNG.integers(60, 2400))) if is_video else None,
                "interaction_class": ("download" if link["file_name_label"]
                                      else "video" if is_video else "link"),
            }
            for c in hr_cols:
                r[c] = getattr(row, c)
            rows.append(r)
    return pd.DataFrame(rows).sort_values("timestamp").reset_index(drop=True)


def main():
    page_ids = [str(uuid.UUID(bytes=bytes(RNG.integers(0, 256, size=16, dtype=np.uint8)))) for _ in PAGES]
    pop = np.array([p[3] for p in PAGES], dtype=float)

    # Per-page theme/topic (stable per page): pick a theme, then a topic in it.
    _theme_names = list(THEMES)
    page_theme, page_topic = {}, {}
    for i in range(len(PAGES)):
        th = _theme_names[RNG.integers(0, len(_theme_names))]
        tp = THEMES[th][RNG.integers(0, len(THEMES[th]))]
        page_theme[i], page_topic[i] = th, tp
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
    # GCRS org path + work-location country per user (deterministic per user).
    _org = {u: walk_org(user_div[u]) for u in users}
    user_unit = {u: _org[u][0] for u in users}
    user_area = {u: _org[u][1] for u in users}
    user_sector = {u: _org[u][2] for u in users}
    user_country = {u: pick_country(user_reg[u]) for u in users}

    n_sessions = 15000
    days_span = (REF_TODAY - START).days
    # Upward trend: later days more likely (linear ramp 0.6 -> 1.4), damped by a
    # working-week profile (weekdays dominate, weekends quiet) so the Phase-2
    # timing charts (hour/weekday/heatmap) show a realistic intranet pattern.
    weekday_factor = [1.0, 1.05, 1.0, 0.95, 0.85, 0.35, 0.25]  # Mon..Sun
    day_idx = np.arange(days_span)
    day_weight = 0.6 + 0.8 * (day_idx / days_span)
    day_weight = day_weight * np.array(
        [weekday_factor[(START + timedelta(days=int(d))).weekday()] for d in day_idx])
    day_weight = day_weight / day_weight.sum()
    sess_days = RNG.choice(day_idx, size=n_sessions, p=day_weight)
    # Intraday profile 6:00-19:00: mid-morning peak, lunch dip, afternoon bump.
    hour_choices = np.arange(6, 20)
    hour_weights = np.array([0.3, 0.7, 1.4, 2.2, 2.4, 2.0, 1.2, 0.9, 1.6, 1.8, 1.5, 1.1, 0.7, 0.4])
    hour_weights = hour_weights / hour_weights.sum()

    rows = []
    for s in range(n_sessions):
        u = users[RNG.integers(0, n_users)]
        day = START + timedelta(days=int(sess_days[s]))
        # working-hours start time following the intraday profile
        start = day + timedelta(hours=int(RNG.choice(hour_choices, p=hour_weights)),
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
                "theme": page_theme[int(pidx)],
                "topic": page_topic[int(pidx)],
                "publishing_date": pub_dt[int(pidx)],
                "language": lang,
                "gpn": user_gpn[u],
                "hr_division": user_div[u],
                "hr_unit": user_unit[u],
                "hr_area": user_area[u],
                "hr_sector": user_sector[u],
                "hr_region": user_reg[u],
                "hr_country": user_country[u],
                "client_os": OSES[RNG.integers(0, len(OSES))],
                "client_browser": BROWSERS[RNG.integers(0, len(BROWSERS))],
                "client_country": user_country[u],
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

    # Metric columns the dashboard counts on (mirrors process_site_pageviews.
    # derive_person_visit): person_id = the real person (GPN, else anonymous
    # device id), visit_id = a reconstructed visit. In the demo the synthetic
    # session_id is already a proper visit and gpn is present for everyone, so
    # these are direct maps — source columns stay untouched, as in the pipeline.
    df["person_id"] = df["gpn"].where(df["gpn"].notna(), "anon:" + df["user_id"])
    df["visit_id"] = df["session_id"]
    # visit_id == session_id in the demo, so the visit-based time-on-page (what
    # the dashboard's Avg time / Avg. Session read) equals the session-based one.
    df["time_on_page_visit_sec"] = df["time_on_page_sec"]

    # page_key: language-agnostic page (strip the /xx segment) so the Pages
    # table groups language variants of one page into a single row — same rule
    # as process_site_pageviews.canonical_page_key.
    df["page_key"] = (df["page_url"].str.lower()
                      .str.replace(r"/(en|de|fr|it)(?=/|$|\?|#)", "", regex=True)
                      .str.replace(r"/$", "", regex=True))

    out = Path(__file__).resolve().parents[1] / "output" / "site_pageviews.parquet"
    out.parent.mkdir(parents=True, exist_ok=True)
    df.to_parquet(out, index=False)

    print(f"Wrote {len(df):,} page views -> {out}")
    print(f"  visits:    {df['visit_id'].nunique():,}")
    print(f"  persons:   {df['person_id'].nunique():,}")
    print(f"  pages:     {df['page_id'].nunique()}")
    print(f"  range:     {df['timestamp'].min()}  ->  {df['timestamp'].max()}")
    print(f"  languages: {dict(df['language'].value_counts())}")

    # Phase 2: correlated interactions (clicks / downloads / video)
    ix = build_interactions(df, page_ids)
    ix_out = out.parent / "site_interactions.parquet"
    ix.to_parquet(ix_out, index=False)
    print(f"\nWrote {len(ix):,} interactions -> {ix_out}")
    print(f"  overall CTR: {len(ix) / len(df) * 100:.1f}% (clicks / views)")
    print(f"  classes:   {dict(ix['interaction_class'].value_counts())}")
    print(f"  clickers:  {ix['person_id'].nunique():,}")
    print(f"  top links: "
          + ", ".join(f"{k} ({v})" for k, v in ix["link_label"].value_counts().head(3).items()))


if __name__ == "__main__":
    main()
