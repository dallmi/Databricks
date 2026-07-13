# Design: Global Filter Bar + GCRS / Region / Page Drilldowns

**Project:** SiteOwnerDashboard
**Date:** 2026-07-13
**Files touched:** `dashboard/dashboard.html`, `scripts/generate_demo_data.py`, rebuild of `output/site_dashboard_standalone.html`
**Reference implementation:** `Clicks/dashboard/dashboard.html` (click-tracking dashboard) — drill pattern ported 1:1.

## Goal

Bring the click-tracking dashboard's filter + drilldown concept to the SiteOwnerDashboard:

1. A global filter row (Site Name, Page Name, Page URL, Division, Region, Language) plus a collapsible **Advanced** section (Content Type, Content Owner, Channel, Topic, Theme).
2. Widget-local **bar-list drilldowns** through the GCRS org hierarchy (Division → Unit → Area → Sector) and geography (Region → Country), mirroring the reference dashboard.
3. Expandable **Page → URL / Language** drilldown in the Pages table.

## Data-Model Reality (verified)

The shared pipeline `scripts/flatten_appinsights.py` already maps the full hierarchy from `hr_history.parquet`:

```
gcrs_division_desc → hr_division    work_location_region  → hr_region
gcrs_unit_desc     → hr_unit        work_location_country → hr_country
gcrs_area_desc     → hr_area        cp_Theme → theme
gcrs_sector_desc   → hr_sector      cp_Topic → topic
gcrs_segment_desc  → hr_segment
```

- `build_fact_page_view` globs **all** `hr_*` columns into the fact, so real exports already carry `hr_unit`, `hr_area`, `hr_sector`, `hr_country`. `build_dim_page` keeps `theme` and `topic`.
- **Correction to the prior spec:** Topic and Theme are NOT absent — they exist as `theme` / `topic` (from `cp_Theme` / `cp_Topic`). They were merely missing from the demo dataset.
- The current **demo** `output/site_pageviews.parquet` (31,500 rows) carries only `hr_division`, `hr_region`, `client_country`, `content_type`, `language`, `page_name`, `page_url`, `page_key`, `site_name`, `content_owner`, `tracking_channel_abbr`. It lacks `hr_unit/area/sector/hr_country`, `theme`, `topic` → the demo generator must be extended (section 6).

| Intended filter / level | Column | Demo distinct | Notes |
|---|---|---|---|
| Site Name | `site_name` | 1 | auto-hidden when ≤1 |
| Page Name | `page_name` | 18 | searchable popover |
| Page URL | `page_url` | 18 | searchable popover |
| Division | `hr_division` | 5 | filter + drill L0 |
| Region | `hr_region` | 4 | filter + drill L0 |
| Language | `language` | 4 | already a filter today |
| Content Type | `content_type` | 3 | Advanced |
| Content Owner | `content_owner` | 1 | Advanced, auto-hidden when ≤1 |
| Channel | `tracking_channel_abbr` | 1 | Advanced, auto-hidden when ≤1 |
| Topic | `topic` | (added to demo) | Advanced |
| Theme | `theme` | (added to demo) | Advanced |
| Unit / Area / Sector | `hr_unit`/`hr_area`/`hr_sector` | (added to demo) | drill levels only |
| Country | `hr_country` | (added to demo) | region drill level; work-location, NOT geo `client_country` |

## Design

### 1. Global Filter Bar

New sticky row inside `.topbar`, **between header and tabs**, so header + filter bar + tabs stay one pinned unit (CLAUDE.md sticky rule). Two levels:

- **Primary row:** `Site Name` · `Page Name` · `Page URL` · `Division` · `Region` · `Language`
- **`Advanced ▾` (collapsed by default):** `Content Type` · `Content Owner` · `Channel` · `Topic` · `Theme`

Each control is a **multi-select popover**, reusing the existing `cp-*` popover component, extended once with an **optional search input** for the long lists (Page Name, Page URL). Select-all / clear are already in the component.

### 2. Central `filterClause()` (replaces `langClause()`)

- `activeFilters`: map `dimension -> Set<string>`. Dimensions: `site_name`, `page_name`, `page_url`, `hr_division`, `hr_region`, `language`, `content_type`, `content_owner`, `tracking_channel_abbr`, `topic`, `theme`.
- `filterClause()` emits `AND <col> IN (…)` for each non-empty set (SQL-escaped, single-quote doubling); returns `''` when nothing active.
- Swap `langClause()` → `filterClause()` at every call site (`win()`, `renderTraffic`, lifecycle/audience CTEs). `win(a,b)` keeps the time predicate and appends `filterClause()`.
- Remove the header `language` `<select>` and the standalone `language` variable; Language becomes a dimension in `activeFilters`.

### 3. Active-filter chips + Clear all

Chips row beneath the bar: `Dimension: value ✕`, collapsing to `Dimension: N selected` when >2 values. Removing a chip clears that value; **Clear all** empties `activeFilters`. The drill widgets do NOT cross-filter (see §5), so chips reflect only bar state.

### 4. Auto-hide absent / single-value dimensions

On load, compute presence + distinct count per dimension (`pvCols` + a `SELECT COUNT(DISTINCT …)`). A filter whose column is absent OR has ≤1 distinct value is hidden. If Advanced would be empty, hide the `Advanced ▾` toggle. Same rule gates drill levels (§5).

### 5. Drilldown widgets — ported 1:1 from `Clicks/dashboard/dashboard.html`

Two widget-local bar-list explorers replace the static Division donut in the Overview `charts-grid`. They respect the global `filterClause()` but drill **locally** (own breadcrumb stack, no cross-filtering of the rest of the dashboard).

- **Division drill:** `DIV_LEVELS = ['hr_division','hr_unit','hr_area','hr_sector']`, labels `Division → Unit → Area → Sector`. Each level drillable except the last; clicking a bar pushes onto the local drill stack and descends. Breadcrumb navigates back up. L0 coloured by the existing `DIVISION_COLOR_MAP` grey map; deeper levels reuse the parent division's grey.
- **Region drill:** `REG_LEVELS = ['hr_region','hr_country']`, labels `Region → Country`. Single-level drill. Region L0 by the region grey map; Country by a grey+bronze sequence (brand-compliant, no rainbow).
- Ported pieces (adapted to SiteOwner column names + `query()` helper): `bar-row` CSS, `breadcrumb` CSS, `renderBars`, `buildDrillBreadcrumb`, `renderDivDrill`, `renderRegionDrill`, drill-state objects, and the `pick(actual, source)` column-resolver so absent levels degrade gracefully (auto-skip a missing level, show "no data" message).
- Metric per bar: distinct sessions (visits) — matches the dashboard's primary audience metric; consistent with the existing donut.

### 6. Demo generator extension (`scripts/generate_demo_data.py`)

Add, per synthetic user (deterministic, seeded like the click-tracking `generate_hr_seed.py`):

- **GCRS path** under each division: `hr_unit`, `hr_area`, `hr_sector` from a small nested ORG dict (2–3 children per level) so drilldowns branch.
- **`hr_country`**: multi-country distribution per region (reuse a `REGION_COUNTRIES`-style map, several countries per region) so Region → Country shows more than one bar. Keep `client_country` untouched.
- Per page: `theme` and `topic` from a small pool (a few themes, a few topics per theme) so the Advanced filters and any theme/topic slicing have real values.

### 7. Rebuild

After editing `dashboard/dashboard.html` and regenerating the demo parquet, rebuild `output/site_dashboard_standalone.html` via `python scripts/build_standalone_dashboard.py`.

### 8. Page → URL / Language drill (Pages table)

The model already supports this: `page_key` (language-agnostic logical page) → `page_url` (per-language variant) → `language`. Make each page row in the Pages table **expandable**: an affordance (chevron) on the `page_name` cell reveals child rows, one per `(page_url, language)` variant under that `page_key`, each with the same metric columns (views/visits/uniques/engagement/avg ToS) scoped to that variant. Collapsed by default; expansion is per-row, respects the active global filters. If a page has a single variant, no chevron.

## Out of Scope (YAGNI)

- No cross-filtering from drill widgets into the rest of the dashboard (reference is widget-local).
- No `hr_segment` / `hr_function` drill levels (stop at Sector, matching the reference's 4 division levels).
- No new tab; drilldowns live in the Overview, page drill in the existing Pages table.

## Testing / Verification

- Serve project root (`python -m http.server`), open `dashboard/dashboard.html`:
  - Each visible filter narrows KPIs, traffic, drill widgets, content-type, and pages consistently; two dimensions AND correctly.
  - Chips reflect state; removing a chip / Clear all restores full data.
  - Division drill descends Division → Unit → Area → Sector; breadcrumb returns. Region drill descends Region → Country. Both respect active filters.
  - Absent levels (in a minimal export) auto-hide / show "no data" without breaking.
  - Page row expands to its URL/language variants; metrics per variant sum back to the parent within the active filter.
- Regenerate demo (`python scripts/generate_demo_data.py`) → confirm `hr_unit/area/sector/hr_country/theme/topic` present and multi-valued.
- Rebuild standalone → confirm identical behaviour from `file://`.
