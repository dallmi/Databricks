# SiteOwnerDashboard — Design

## Goal
Let a site owner see how their SharePoint site is consumed, from a single
standalone HTML file. Extract one site's pageViews, build one Parquet, open the
dashboard — DuckDB-WASM does all aggregation in the browser. Reusable per site
(the only parameter is which site to export).

## Decisions

| # | Decision | Rationale |
|---|----------|-----------|
| 1 | **Template per site**, not one fixed site | The KQL `PageUrlFilter` scopes the export; the same dashboard serves any site. |
| 2 | **DuckDB-WASM + Parquet** in-browser | True standalone, data swappable without touching HTML — the "DuckDB concept". |
| 3 | **Site filter via `PageURL contains`** | Consistent with the wider project's KQL; robust when SiteName is inconsistent. |
| 4 | **One denormalised `site_pageviews.parquet`** (fact + page dims) | Simpler than a star schema in WASM for single-site volumes; keeps `page_id` for the Phase-2 join. |
| 5 | **Language derived from PageURL** (`/en//de//fr//it/`) | pageViews has no language field; the URL segment carries it. Else `Other`. |
| 6 | **House design language** (CampaignWe/MURL), mockup 1a as rough direction only | Consistency across our dashboards beats pixel-matching a placeholder mockup. |
| 7 | **On-brand colour translation of 1a** | 1a highlights the selected range in **red**; the mandatory corporate palette forbids red chart fills. Selected range = dark grey `#404040`, context = light grey `#CCCABC`. Red stays a small accent (KPI top bar, active tab, negative MoM). Sparklines neutral grey; direction is carried by the MoM column. |
| 8 | **Phase 2 additive, dashboard works without it** | customEvents (`site_interactions.parquet`) is loaded only if present; its tab appears only then. Phase 1 is fully functional on pageViews alone. |

## Reused from the main pipeline
`build_site_parquet.py` imports `read_input`, `flatten_appinsights`,
`build_clean_table`, `join_hr_data`, `build_fact_page_view`, `build_dim_page`
from [`../scripts/flatten_appinsights.py`](../scripts/flatten_appinsights.py) —
same customDimensions flatten, UTC→CET, GPN normalise, CammsTrackingID split and
temporal HR join. It then denormalises, derives `language`, filters to one site.

## Overview tab (Phase 1)
- KPI row: Page Views · Page Visits (sessions) · Unique Visitors · Avg. Session
  (mean per-session engaged time) · Engagement (unique ÷ visits). Each with a
  MoM delta vs. the prior equal-length window.
- Traffic over time: monthly bars, selected window emphasised, Visits/Unique toggle.
- Audience by Division donut (`DIVISION_COLOR_MAP` greys).
- Content Type donut (`DONUT_PALETTE` grey + bronze).
- Pages table: per-page views/visits/unique/engagement/avg-time + weekly trend
  sparkline + MoM; sortable.

Reference date = `MAX(timestamp)` in the data. Timeframe presets
(30d/90d/YTD/12mo/All) and the previous period are computed relative to it.

## Content Lifecycle tab (pageViews only)

Uses `publishing_date` (from `CustomProps.PublishingDate`, normalised to
TIMESTAMP via `TRY_CAST` at load). If the column is missing or all-NULL the tab
shows an empty state; everything else is unaffected.

- KPI row: Pages Published (in window) · Median 1st-Week Reach (visits in first
  7 days; only pages whose first week lies fully inside the window) · Fresh
  Content Share (views on ≤ 30d-old content, of views with a known publish
  date) · Evergreen Share (> 90d).
- **Decay chart**: views per age bucket ÷ observable page-days in that bucket —
  exposure-normalised so a page published late in the window doesn't deflate
  old-age buckets it never reached.
- **Cadence chart**: monthly stacked views (fresh ≤ 30d dark grey, older light
  grey) + pages-published line (bronze, right axis).
- **Published pages table**: publish date, age, first-7d visits, 7d share
  (first week ÷ lifetime visits), window visits.

## Audience & Sessions tab (pageViews only)

- KPI row: New Visitors (first-ever visit ≥ window start) · Returning Share ·
  Bounce Rate (single-page sessions; **delta colour inverted**) · Pages/Session ·
  Visits/Visitor. Deltas vs. prior equal-length window.
- New vs. returning stacked monthly bars (full history; "new" = first ever
  visit in that month — computed within the current language scope).
- Visit-frequency and session-depth distribution bars.
- Entry-pages table: `arg_min(page_id, timestamp)` per session → entries,
  share, bounce rate, avg depth.

## Rendering model

Filter changes mark all tabs dirty and re-render only the active tab; hidden
tabs render lazily on switch (Chart.js canvases can't size inside
`display:none`). The XLSX export recomputes any still-dirty dataset first, so
the workbook always matches the current filters. Column presence is detected
via `DESCRIBE` at load — missing `hr_*` shows a hint in the Division donut,
missing `publishing_date` collapses the Lifecycle tab to its empty state.

## Phase 2 — customEvents (planned, additive)

customEvents scopes to a site the **same way** (SiteName/SiteID/PageId in
CustomProps) and shares `page_id` with pageViews → `dim_page` — **no
CammsTrackingID needed** (customEvents doesn't carry it). A second export
+ build produces `data/site_interactions.parquet`:

**`fact_interaction`** — one row per interaction, `event_name` discriminates the
family (as customEvents itself does):
`event_id, timestamp, page_id, user_id, session_id, gpn, event_name,
component_name, link_type, link_label, link_address, link_ancestors,
file_type_label, file_name_label, video_action, video_id, video_type,
video_duration`

Unlocks: downloads (top files, volume), link-type mix, top CTAs, **top/bottom
pages by engagement** (views × clicks × downloads, not views alone),
component performance, video engagement, on-site search. The Pages table becomes
drillable into a page's interactions.

The dashboard's data layer registers `site_interactions.parquet` only if it
loads; a **Content & Interactions** tab is added when present.
