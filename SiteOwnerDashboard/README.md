# Site Owner Dashboard

A standalone, corporate-branded analytics dashboard a **site owner** runs for a
**single SharePoint site**. Extract that site's pageViews from AppInsights,
build one Parquet, open one HTML file — DuckDB-WASM computes every KPI, chart
and table in the browser. No server-side backend, no Databricks connection.

```
export_site_pageviews.kql          AppInsights pageViews for ONE site (PageURL contains)
        │  Export → CSV → input/
        ▼
scripts/process_site_pageviews.py  flatten + HR-join + language-from-URL → output/site_pageviews.parquet
        │
        ▼
dashboard/dashboard.html           DuckDB-WASM + Chart.js — loads ../output/, renders the dashboard
        │
        ▼ (optional)
scripts/build_standalone_dashboard.py → output/site_dashboard_standalone.html (single file, runs from file://)
```

## Quick start (demo — runs out of the box)

```bash
pip install pandas duckdb pyarrow openpyxl numpy
python scripts/generate_demo_data.py  # writes output/site_pageviews.parquet (fake "News and events")
python -m http.server 8000
# open http://localhost:8000/dashboard/dashboard.html
```

The dashboard can't `fetch()` a Parquet over `file://`, so it must be served
over HTTP (any static server works) — or use the standalone build below.

## Real data — one site

1. **Extract.** Open `export_site_pageviews.kql`, set `PageUrlFilter` to a
   substring that identifies your site (e.g. `news-and-events`) and the time
   window (export **≥ 13 months** so the 12-month trend *and* the
   month-over-month deltas both have data). Run it in Azure Portal → Logs →
   **Export → CSV (all columns)**. Azure caps ~65k rows per export — narrow the
   window and export in chunks for large sites.
2. **Build.** Drop the export(s) into `input/` and run:
   ```bash
   python scripts/process_site_pageviews.py
   ```
   No path needed: the script picks up the CSV/XLSX files in `input/` and
   auto-detects the HR parquet (`input/*.parquet`, then
   `../../SearchAnalytics/output/hr_history.parquet`) for the Division/Region
   drilldowns (it carries the full GCRS hierarchy: division, unit, area, sector,
   region, country).

   **Incremental** (same pattern as CampaignWe): a SHA-256 manifest
   (`output/site_pageviews.manifest.json`) tracks processed files — unchanged
   files are skipped, only new/changed ones are processed and **upserted** into
   the existing parquet on a composite `event_key` (second-truncated timestamp
   + user + session + page; the AppInsights `id` proved **not** event-unique in
   real exports, so it is data-only). Overlapping/chunked exports therefore
   never double-count — even when mixing CSV (sub-second timestamps) and Excel
   (second-truncated) exports of the same rows. A re-exported (changed) file
   fully replaces the rows it contributed before. Deleting a file from `input/`
   does **not** remove its rows — use `--rebuild` for that. Known limitation
   (as in CampaignWe): two distinct views of the same page by the same
   user+session within the same second collapse into one. Overrides:
   ```bash
   python scripts/process_site_pageviews.py input/<export>.csv   # explicit file(s), skips hash check
   python scripts/process_site_pageviews.py --hr /path/to/hr_history.parquet
   python scripts/process_site_pageviews.py --no-hr              # skip HR join
   python scripts/process_site_pageviews.py --rebuild            # full reprocess (also after changing filters)
   # re-filter locally by URL if you exported broadly:
   python scripts/process_site_pageviews.py --url-contains news-and-events
   ```
3. **Open.** `python -m http.server 8000` → `http://localhost:8000/dashboard/dashboard.html`.

`scripts/process_site_pageviews.py` reuses the exact flatten / UTC→CET /
GPN-normalise / CammsTrackingID-split / temporal-HR-join logic from
[`../scripts/flatten_appinsights.py`](../scripts/flatten_appinsights.py) —
when running this folder **standalone** (outside the Databricks repo), copy
that file into `scripts/` next to `process_site_pageviews.py`, otherwise the
script exits with *"flatten_appinsights.py not found"*. It then
denormalises fact + page dimension into one wide table and derives `language`
from the PageURL (`/en/ /de/ /fr/ /it/` → EN/DE/FR/IT, else `Other`).

## Standalone build (SharePoint / offline distribution)

```bash
python scripts/build_standalone_dashboard.py
# → output/site_dashboard_standalone.html
```

One self-contained HTML that runs from `file://` — no server needed. The
parquet is ZSTD-recompressed and embedded as a base64 data island; Chart.js,
the date adapter and ExcelJS are inlined from `dashboard/vendor/` (no network
needed behind the corporate proxy). Only DuckDB-WASM still loads from the CDN
at open time. Same mechanism as the SearchAnalytics / CampaignWe standalones.
The standalone embeds the site data (may contain GPN/Email) and is gitignored.
To refresh the vendored libraries after a version bump:
`python scripts/vendor_libs.py`.

## What the dashboard shows

### Overview
- **KPIs** — Page Views, Page Visits (sessions), Unique Visitors, Avg. Session,
  Engagement (unique ÷ visits), each with a month-over-month delta vs. the prior
  equal-length period.
- **Traffic over time** — 12+ months of monthly bars; the selected timeframe is
  emphasised (dark grey), the rest is context (light grey). Visits / Unique toggle.
- **Audience by Division** — visits by HR division as a click-to-drill bar list
  that descends the GCRS org hierarchy **Division → Unit → Area → Sector**
  (breadcrumb to climb back). From the HR join; shows a hint instead when built
  without `--hr`, and auto-skips any level absent from the export.
- **Audience by Region** — visits by HR region, click-to-drill **Region →
  Country** (work-location `hr_country`).
- **Content Type** — share of page views by content type.
- **Top & Bottom Pages** — the 5 best and 5 weakest pages, ranked by a metric
  you choose (Visits / Page Views / Unique / Engagement / Avg time on page).
- **Pages — performance & trend** — every page on the site: views, visits,
  unique, engagement, avg time, a weekly trend sparkline and MoM delta. Sortable.
  When grouped by page name, a page with several language variants is
  **expandable** — click the chevron to reveal its per-URL / per-language rows
  (each with its own metrics).

### Content Lifecycle (requires `PublishingDate` in the export)
- **KPIs** — Pages Published in the window, Median 1st-Week Reach (visits in the
  first 7 days, pages fully observable only), Fresh Content Share (views on
  content ≤ 30 days old), Evergreen Share (views on content > 90 days old).
- **Content decay** — average daily views per page by content age (days since
  publication), normalised by how many page-days were observable at each age.
- **Publishing cadence vs. earned traffic** — monthly views split into fresh
  (≤ 30d) vs. older content, overlaid with the number of pages published.
- **Published pages table** — publish date, age, first-7-day visits, 7d share
  (first week ÷ lifetime) and window visits per page. Sortable.

If the export has no `PublishingDate`, the tab shows an explanatory empty state
— everything else keeps working.

### Audience & Sessions
- **KPIs** — New Visitors (first ever visit in window), Returning Share, Bounce
  Rate (single-page sessions; delta colour inverted — down is good), Pages /
  Session, Visits / Visitor. All with deltas vs. the prior equal-length period.
- **New vs. returning visitors** — monthly stacked bars over the full history
  (new = first ever visit in that month).
- **Visit frequency** — distribution of visits per visitor in the window
  (1 / 2 / 3–5 / 6–10 / 10+).
- **Session depth** — distribution of pages per session (1 / 2 / 3–5 / 6+).
- **Entry pages table** — where sessions start: entries, share, bounce rate and
  average session depth per entry page. Sortable.

### Export
- **Export XLSX** — KPIs, Pages, Audience (KPIs + entry pages) and Content
  Lifecycle (KPIs + published pages), corporate-styled, always reflecting the
  current timeframe/language filter.

Filters: **Timeframe** (30d / 90d / YTD / 12mo / All) and a **custom from–to date
range** picker, plus a **global filter bar** — a primary row (Site Name, Page
Name, Page URL, Division, Region, Language) and a collapsible **Advanced** row
(Content Type, Content Owner, Channel, Topic, Theme). Every control is a
searchable multi-select; selections combine (AND) and show as removable chips
with **Clear all**. Dimensions that are absent or single-valued in the export
auto-hide, so the bar stays clean. Everything lives in the browser; every
KPI/chart/table/drilldown reacts instantly, all deltas vs. the prior
equal-length period.

## Phase 2 — customEvents (clicks, downloads, video, search)

The dashboard is built to grow. Drop a second Parquet
`output/site_interactions.parquet` (built from the customEvents stream —
clicks, downloads, video, on-site search for the same site) and a **Content &
Interactions** tab appears. Without that file the dashboard runs fully on
pageViews alone — nothing to configure. See [`DESIGN.md`](DESIGN.md) for the
`fact_interaction` schema and the analyses it unlocks (top/bottom pages by
engagement, downloads, link types, component performance, video, search).

## Project layout

| Path | Purpose |
|---|---|
| `export_site_pageviews.kql` | AppInsights export for one site (PageURL contains), raw customDimensions |
| `input/` | Raw CSV/XLSX exports land here (gitignored — may contain GPN/Email) |
| `scripts/process_site_pageviews.py` | `input/` CSV/XLSX → `output/site_pageviews.parquet` (reuses the main pipeline) |
| `scripts/generate_demo_data.py` | Fake single-site Parquet so the dashboard runs with no export |
| `scripts/build_standalone_dashboard.py` | dashboard + parquet + vendored libs → `output/site_dashboard_standalone.html` |
| `scripts/vendor_libs.py` | Refresh `dashboard/vendor/` from the CDN (only on version bumps) |
| `dashboard/dashboard.html` | The dashboard (DuckDB-WASM + Chart.js), loads `../output/` |
| `dashboard/guide.html` | Illustrated user guide (steps + glossary, printable to PDF) — linked from the dashboard header |
| `dashboard/img/guide/` | Guide screenshots (demo data) |
| `dashboard/vendor/` | Vendored Chart.js / date adapter / ExcelJS for the standalone build |
| `output/` | Built parquets + standalone HTML (gitignored — may contain GPN/Email) |
| `DESIGN.md` | Design decisions + Phase-2 architecture |
