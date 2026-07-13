# Design: Global Filter Bar + Division/Region Drilldown

**Project:** SiteOwnerDashboard
**Date:** 2026-07-13
**File touched:** `dashboard/dashboard.html` (single-file dashboard) + rebuild of `output/site_dashboard_standalone.html`

## Goal

Bring the click-tracking dashboard's filter concept to the SiteOwnerDashboard:

1. A global filter row (Site Name, Page Name, Page URL) plus an "Advanced" section, modelled on the click-tracking dashboard.
2. Interactive drill-down from Division into its Regions, driven from the existing Division donut.

## Data-Model Reality (verified against `output/site_pageviews.parquet`, 31,500 rows)

| Intended filter | Column present | Notes |
|---|---|---|
| Site Name | `site_name` | 1 distinct in current export (single-site owner view) |
| Page Name | `page_name` | 18 distinct |
| Page URL | `page_url` | 18 distinct (≈1:1 with page_name) |
| Division | `hr_division` | 5 distinct |
| Region | `hr_region` | 4 distinct |
| Language | `language` | 4 distinct — already a filter today |
| Content Type | `content_type` | 3 distinct (Article/Video/Download) |
| Content Owner | `content_owner` | 1 distinct |
| Channel | `tracking_channel_abbr` | 1 distinct |
| **Topic / Theme** | **absent** | Not in the model. Decision: do NOT fake it — Advanced uses the real fields above instead. |

## Current State

- No global filter bar exists. The header holds only the timeframe segment, a custom date range, and a single `Language` `<select>`.
- The one dimensional filter is `language`, applied through `langClause()` which is appended to `win()` and ~10 query sites.
- Division appears only as a static, non-interactive donut (`renderDivision`). Region is never surfaced.
- Existing reusable UI: a multi-select popover component (`cp-trigger` / `cp-popover` / `cp-option` with select-all/clear actions) used today by the column picker. No search box in it yet.

## Design

### 1. Global Filter Bar

A new sticky row inside `.topbar`, placed **between the header and the tabs**, so header + filter bar + tabs remain a single pinned unit (per CLAUDE.md sticky-header rule).

Two levels:

- **Primary row:** `Site Name` · `Page Name` · `Page URL` · `Division` · `Region` · `Language`
- **`Advanced ▾` (collapsible, collapsed by default):** `Content Type` · `Content Owner` · `Channel`

Each control is a **multi-select popover**, reusing the existing `cp-*` popover component. The component is extended once with an **optional search input** (filters the option list client-side) for the long lists (Page Name, Page URL). Select-all / clear actions are already part of the component.

### 2. Central `filterClause()` (replaces `langClause()`)

- Introduce `activeFilters`: a map `dimension -> Set<string>` of selected values. Dimensions: `site_name`, `page_name`, `page_url`, `hr_division`, `hr_region`, `language`, `content_type`, `content_owner`, `tracking_channel_abbr`.
- `langClause()` is replaced by `filterClause()`, which emits `AND <col> IN ('v1','v2',…)` for every dimension with a non-empty set, values SQL-escaped (single-quote doubling), and returns `''` when nothing is active.
- Swap `langClause()` → `filterClause()` at every existing call site (`win()`, `renderTraffic`, lifecycle CTEs, audience CTEs, etc.). `win(a,b)` keeps the time predicate and appends `filterClause()`.
- Remove the standalone `language` variable / header `<select>`; Language is now just another dimension in `activeFilters`, driven by its popover in the primary row.

### 3. Active-filter chips + Clear all

- A chips row beneath the bar lists every active selection as `Dimension: value ✕` (values from the same set collapse to `Dimension: 3 selected` when >2). Removing a chip clears that value; **Clear all** empties `activeFilters`.
- The Division selected via donut drill-down appears here as a chip too, so donut state and bar state stay unified and clearable from one place.

### 4. Auto-hide single-value dimensions

On load, compute distinct counts per dimension. Any dimension with ≤1 distinct value has its filter control hidden (keeps the bar clean on real single-site exports). With the current export this hides `Site Name`, `Content Owner`, and `Channel`; `Advanced` therefore shows only `Content Type` until richer data arrives (if Advanced would be empty, hide the `Advanced ▾` toggle entirely).

### 5. Division donut drill-down (`renderDivision`)

- **Level 1 (default):** divisions by distinct-session count, coloured via the existing `DIVISION_COLOR_MAP` grey map.
- **Click a slice:** (a) set `activeFilters.hr_division = {clickedDivision}` → whole dashboard re-renders filtered, chip appears; (b) the donut redraws as the **Region breakdown of that division**, titled with a `‹ Divisions` breadcrumb.
- **Level 2 (region) colours:** grey + bronze sequence (`#404040, #B98E2C, #8E8D83, #CCCABC, #5A5D5C, #946F29, …`) — brand-compliant, no rainbow.
- **Breadcrumb back-click:** clears the `hr_division` filter and restores the Level-1 division view.
- If the user sets an `hr_division` filter from the bar popover (single value), the donut mirrors it and shows the Region breakdown; multi-value division selection keeps the Level-1 view. Drill-down state derives from `activeFilters.hr_division`, not a separate variable, so bar and donut never diverge.

### 6. Rebuild

After editing `dashboard/dashboard.html`, rebuild the embedded standalone via `python scripts/build_standalone_dashboard.py` so `output/site_dashboard_standalone.html` reflects the changes.

## Out of Scope (YAGNI)

- No Division›Region tree table (donut drill-down only).
- No derived Topic/Theme field.
- No page-level tier in the drill-down.
- No new tab; everything lives in the existing header/Overview.

## Testing / Verification

- Serve project root (`python -m http.server`), open `dashboard/dashboard.html`:
  - Each visible filter narrows KPIs, traffic, division, content-type, and pages consistently.
  - Combining two dimensions ANDs correctly (e.g. Division + Content Type).
  - Chips reflect state; removing a chip and Clear all restore full data.
  - Donut click filters the dashboard, swaps to Region view, breadcrumb returns.
  - Single-value dimensions are hidden.
- Confirm the standalone build opens from `file://` with identical behaviour.
