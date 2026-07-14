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
| 9 | **No physical pv×ix join — aggregate joins at query time, per view grain** | Both parquets stay at event grain and carry the same three page keys (`page_key`, `page_id`, `page_url`). Every table joins per-view aggregates on ITS grain, so `COUNT(DISTINCT person_id)` is recomputed per grain — UV is never summed (a DE+FR reader counts once per logical page, once per variant). |
| 10 | **`page_key` for rankings, `page_id` for the variant drill** | Rankings answer a content question — language variants must not compete (the CAWB-4× fix). The drill joins on `page_id` (GUID): string-equality-safe across the two streams and stable across URL renames. |
| 11 | **Filters are column-aware per view** | ix carries the same filterable columns as pv EXCEPT `tracking_channel_abbr` (customEvents has no CammsTrackingID). A filter on a column ix lacks is skipped for ix queries and flagged on the table — never silently ignored. |

## Reused from the main pipeline
`scripts/process_site_pageviews.py` imports `read_input`, `flatten_appinsights`,
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

## Phase 2 — customEvents (shipped)

customEvents scopes to a site the **same way** (PageURL/PageId in CustomProps)
— **no CammsTrackingID needed** (customEvents doesn't carry it; the Channel
filter therefore never applies to interactions). `export_site_interactions.kql`
→ `input/interactions/` → `scripts/process_site_interactions.py` →
`output/site_interactions.parquet`. `click_event` only — search events
(`SEARCH_TRIGGERED`, `SEARCH_RESULT_CLICK`) have a 4-level nested schema and
stay out of scope.

**`site_interactions.parquet`** — one wide row per interaction:
`timestamp, event_name, page_id, page_key, page_name, page_url,
language, site_id, site_name, content_owner, content_type, theme, topic,
target_region, target_org, page_status, publishing_date, user_id, session_id,
person_id, email, gpn, client_os, client_browser, client_country, referrer_url,
component_name, link_type, link_label, link_address, link_ancestors,
file_type_label, file_name_label, video_action, video_id, video_type,
video_duration, interaction_class, hr_*, source_file, event_key`

Derivations are IDENTICAL to the pageViews build (same functions imported):
`language`/`page_key` from PageURL, `person_id` = GPN else `anon:<user_id>`,
temporal HR join via GPN. `interaction_class` is an ADDED column
(download → video → link; source columns untouched). The upsert `event_key`
includes the link identity (component + address + label) so two different-link
clicks in the same second stay distinct.

### Join contract (pv × ix)

| Grain | Key | Used for |
|---|---|---|
| Logical page (default reporting) | `page_key` | Pages interest×action table, rankings, CTR = clicks ÷ views |
| Language variant (drill) | `page_id` (GUID; URL + language are display) | per-variant UV/clicks/CTR under an expanded page |
| Person | `person_id` | Unique Clickers, Clicker Rate = clickers ÷ unique visitors |
| Session (NOT used yet) | `session_id` | a same-visit view→click funnel — run `diagnose_interactions.py` for the overlap evidence first |

All joins are per-view **aggregate joins in DuckDB-WASM at query time** (pv CTE
LEFT JOIN ix CTE on the grain key). Distinct counts are recomputed per grain —
never summed. Known limitation (documented in the UI): telemetry records only
**clicked** links; a link with zero clicks is invisible (no link inventory).
*Last clicked* is the observable proxy for "no longer clicked". Clicks whose
`page_key` has no pv rows in the window (mismatched export windows/scopes) are
counted in the KPIs and flagged as orphans under the table.

The tab adds: KPI row (Clicks / Unique Clickers / Downloads / CTR / Clicker
Rate, MoM), interactions over time, interaction mix, top components, the
interest×action pages table (expand → variants + link detail), top links and
top downloads. The Overview Pages table gains optional **Clicks** and **CTR**
measures (secondary ix aggregate mapped onto the same visible dimensions).

### Ported from the CampaignWe clicks dashboard (2026-07-14)

Four patterns from the sister project (`../CampaignWe/dashboard/dashboard.html`)
answer questions the tab previously didn't; story-submission specifics
(creation/invite funnels, deleted stories) were deliberately NOT ported:

| Pattern | What it answers | Notes |
|---|---|---|
| Adaptive daily line (clicks + unique clickers, dual axis) | traffic shape inside short windows | windows ≤ ~120 days; longer windows keep monthly bars |
| Clicks by hour / by weekday + **Weekday×Hour heatmap** (CET) | when the audience clicks → publish/push timing | heat ramp white→pastel→amber→red→dark red is data-driven *intensity* (RAG-toned), not a category palette; peak bar red = small accent |
| **Clicks by Division** table with Clicks/Clicker + **Clicker Rate** | who clicks, and how deep | Clicker Rate = ix clickers ÷ pv uniques per division — an aggregate join at the *Division* grain (decision #9 applied to a non-page grain) |
| **Division × Page heatmap** | which content resonates with which audience | top-15 pages by clicks × top-8 divisions |

All four honour the column-aware filter contract (decision #11) and degrade
gracefully: division views show a "rebuild with --hr" hint when `hr_division`
is absent from the interactions parquet.
