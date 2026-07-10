# Site Owner Dashboard

A standalone, corporate-branded analytics dashboard a **site owner** runs for a
**single SharePoint site**. Extract that site's pageViews from AppInsights,
build one Parquet, open one HTML file — DuckDB-WASM computes every KPI, chart
and table in the browser. No server-side backend, no Databricks connection.

```
export_site_pageviews.kql   AppInsights pageViews for ONE site (PageURL contains)
        │  Export → CSV
        ▼
build_site_parquet.py       flatten + HR-join + language-from-URL → data/site_pageviews.parquet
        │
        ▼
index.html                  DuckDB-WASM + Chart.js — loads the Parquet, renders the dashboard
```

## Quick start (demo — runs out of the box)

```bash
pip install pandas duckdb pyarrow openpyxl numpy
python generate_demo_data.py          # writes data/site_pageviews.parquet (fake "News and events")
python -m http.server 8000
# open http://localhost:8000/index.html
```

The dashboard can't `fetch()` a Parquet over `file://`, so it must be served
over HTTP (any static server works).

## Real data — one site

1. **Extract.** Open `export_site_pageviews.kql`, set `PageUrlFilter` to a
   substring that identifies your site (e.g. `news-and-events`) and the time
   window (export **≥ 13 months** so the 12-month trend *and* the
   month-over-month deltas both have data). Run it in Azure Portal → Logs →
   **Export → CSV (all columns)**. Azure caps ~65k rows per export — narrow the
   window and export in chunks for large sites.
2. **Build.** Drop the CSV into `data/` and run:
   ```bash
   python build_site_parquet.py data/<export>.csv
   # optional HR enrichment (enables the Audience-by-Division donut):
   python build_site_parquet.py data/<export>.csv --hr ../../SearchAnalytics/output/hr_history.parquet
   # re-filter locally by URL if you exported broadly:
   python build_site_parquet.py data/<export>.csv --url-contains news-and-events
   ```
3. **Open.** `python -m http.server 8000` → `http://localhost:8000/index.html`.

`build_site_parquet.py` reuses the exact flatten / UTC→CET / GPN-normalise /
CammsTrackingID-split / temporal-HR-join logic from
[`../scripts/flatten_appinsights.py`](../scripts/flatten_appinsights.py), then
denormalises fact + page dimension into one wide table and derives `language`
from the PageURL (`/en/ /de/ /fr/ /it/` → EN/DE/FR/IT, else `Other`).

## What the dashboard shows

### Overview
- **KPIs** — Page Views, Page Visits (sessions), Unique Visitors, Avg. Session,
  Engagement (unique ÷ visits), each with a month-over-month delta vs. the prior
  equal-length period.
- **Traffic over time** — 12+ months of monthly bars; the selected timeframe is
  emphasised (dark grey), the rest is context (light grey). Visits / Unique toggle.
- **Audience by Division** — share of visits by HR division (from the HR join;
  shows a hint instead when built without `--hr`).
- **Content Type** — share of page views by content type.
- **Top & Bottom Pages** — the 5 best and 5 weakest pages, ranked by a metric
  you choose (Visits / Page Views / Unique / Engagement / Avg time on page).
- **Pages — performance & trend** — every page on the site: views, visits,
  unique, engagement, avg time, a weekly trend sparkline and MoM delta. Sortable.

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

Filters: **Timeframe** (30d / 90d / YTD / 12mo / All), a **custom from–to date
range** picker, and **Language** (All / EN / DE / FR / IT) — all live in the
browser. Every KPI/chart/table reacts instantly; all deltas are vs. the prior
equal-length period.

## Phase 2 — customEvents (clicks, downloads, video, search)

The dashboard is built to grow. Drop a second Parquet
`data/site_interactions.parquet` (built from the customEvents stream — clicks,
downloads, video, on-site search for the same site) and a **Content &
Interactions** tab appears. Without that file the dashboard runs fully on
pageViews alone — nothing to configure. See [`DESIGN.md`](DESIGN.md) for the
`fact_interaction` schema and the analyses it unlocks (top/bottom pages by
engagement, downloads, link types, component performance, video, search).

## Files

| File | Purpose |
|---|---|
| `export_site_pageviews.kql` | AppInsights export for one site (PageURL contains), raw customDimensions |
| `build_site_parquet.py` | CSV/XLSX → `data/site_pageviews.parquet` (reuses the main pipeline) |
| `generate_demo_data.py` | Fake single-site Parquet so the dashboard runs with no export |
| `index.html` | The standalone dashboard (DuckDB-WASM + Chart.js) |
| `data/` | Parquets land here (gitignored — may contain GPN/Email) |
| `DESIGN.md` | Design decisions + Phase-2 architecture |
