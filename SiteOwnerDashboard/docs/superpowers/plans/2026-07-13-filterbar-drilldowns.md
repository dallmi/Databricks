# Filter Bar + GCRS/Region/Page Drilldowns Implementation Plan

> **For agentic workers:** Steps use checkbox (`- [ ]`) syntax. This is a browser dashboard (single-file HTML + DuckDB-WASM + Chart.js) plus a Python demo generator. "Tests" are: Python column-assertions for the generator, and browser verification (serve + Playwright screenshot / console-error check) for the dashboard.

**Goal:** Add a global multi-dimension filter bar (Site/Page/URL/Division/Region/Language + Advanced: Content Type/Owner/Channel/Topic/Theme), widget-local GCRS drilldowns (Division→Unit→Area→Sector, Region→Country), and a page→URL/language expandable drill to the SiteOwnerDashboard.

**Architecture:** Extend the existing single-file `dashboard/dashboard.html`. Replace the single `language` filter mechanism (`langClause()`) with an `activeFilters` map + `filterClause()`. Reuse the existing `cp-*` popover component for filter controls (add a search box). Port the bar-list drill pattern from `Clicks/dashboard/dashboard.html`. Extend `scripts/generate_demo_data.py` so the demo carries the new columns.

**Tech Stack:** HTML/CSS/vanilla JS, DuckDB-WASM, Chart.js, Python/pandas/pyarrow (generator), `scripts/build_standalone_dashboard.py` (rebuild).

## Global Constraints

- Corporate palette only. Region-drill country level uses grey+bronze sequence; division greys via existing `DIVISION_COLOR_MAP`. No rainbow, no blue chart fills.
- Header + filter bar + tabs = one sticky unit.
- Border-radius 2px, no emojis, Lucide only, warm greys.
- English in code/docs. No brand names.
- Region drill uses `hr_country` (work location), NOT `client_country` (geo).
- Drill widgets are widget-local (own breadcrumb), they do NOT cross-filter the rest of the dashboard.
- Auto-hide any filter/level whose column is absent or ≤1 distinct value.

---

### Task 1: Extend demo generator

**Files:**
- Modify: `scripts/generate_demo_data.py`
- Output: `output/site_pageviews.parquet` (regenerated)

**Produces:** demo parquet gains columns `hr_unit`, `hr_area`, `hr_sector`, `hr_country`, `theme`, `topic`.

- [ ] **Step 1:** Add a small deterministic ORG dict `Division → {Unit → {Area → [Sector,…]}}` (2–3 children per level) and a `REGION_COUNTRIES` map (3–4 countries per region, weighted). Add a `THEMES = {theme: [topics]}` pool (≈4 themes, 2–3 topics each). Assign per synthetic user: walk the ORG tree seeded by user id → `hr_unit/hr_area/hr_sector`; pick `hr_country` from `REGION_COUNTRIES[user_region]`. Assign per page: a `(theme, topic)` pair from `THEMES`.
- [ ] **Step 2:** Emit the six new columns in the row dict (alongside existing `hr_division`, `hr_region`, `client_country`).
- [ ] **Step 3:** Run `python scripts/generate_demo_data.py`.
- [ ] **Step 4:** Verify columns + multi-value:
  `python -c "import pyarrow.parquet as pq,pandas as pd; d=pq.read_table('output/site_pageviews.parquet').to_pandas(); [print(c, d[c].nunique()) for c in ['hr_unit','hr_area','hr_sector','hr_country','theme','topic']]"`
  Expected: each ≥2 distinct; `hr_country` shows several countries.
- [ ] **Step 5:** Commit.

---

### Task 2: Filter-bar markup, styles, and FilterControl component

**Files:**
- Modify: `dashboard/dashboard.html` (topbar markup ~176–212; CSS near cp-* ~149–172; new JS FilterControl class near ColumnPicker ~760–843)

**Produces:** `FilterControl` class (multi-select popover with optional search, over an actual column), a `#filterBar` row with primary + Advanced sections, a `#filterChips` row, and a `#advancedToggle`.

- [ ] **Step 1:** Add a `.filter-bar`, `.filter-adv`, `.filter-chips`, `.chip`, and `.cp-search` CSS block (reuse cp-* popover styles; `.cp-search` = full-width input at top of `.cp-list`).
- [ ] **Step 2:** In `.topbar`, insert `<div class="filter-bar" id="filterBar">` between `.header` and `.tabs`, containing a primary `<div>` (mount points `id="flt_site_name"`, `flt_page_name`, `flt_page_url`, `flt_hr_division`, `flt_hr_region`, `flt_language`), an `Advanced ▾` button `#advancedToggle`, an Advanced `<div class="filter-adv hidden">` (mounts `flt_content_type`, `flt_content_owner`, `flt_tracking_channel_abbr`, `flt_topic`, `flt_theme`), and a `<div class="filter-chips" id="filterChips">`. Remove the header `Language` `<select>`.
- [ ] **Step 3:** Write `class FilterControl` modeled on `ColumnPicker`: constructor `(mountId, {label, values, onChange})`; renders a `cp-trigger` (label + selected-count) and a `cp-popover` with a `cp-search` input, Select-all/Clear actions, and one `cp-option` checkbox per value; maintains a `Set` of selected values; `getSelected()`, `setSelected(set)`, `refresh()`; the search input filters visible `cp-option`s by substring. Reuse `positionPopover`/`toggleOpen` behavior and the outside-click close.
- [ ] **Step 4:** Browser check: serve root, open dashboard, confirm the bar renders, popovers open/close, search filters options, no console errors (Playwright screenshot).
- [ ] **Step 5:** Commit.

---

### Task 3: activeFilters state, filterClause(), auto-hide, chips

**Files:**
- Modify: `dashboard/dashboard.html` (replace `langClause` ~515–516 and its call sites ~684,1105,1119,1152,1167,1253,1267; init in `loadData`/boot ~558–633; new wiring near setup)

**Consumes:** `FilterControl` (Task 2), `render()`, `windows()`, `win()`.
**Produces:** `activeFilters` (Map dim→Set), `filterClause()` returning `AND col IN (…)` chain, `FILTER_DIMS` config list, `initFilters()`, `renderChips()`.

- [ ] **Step 1:** Add `const FILTER_DIMS = [{col:'site_name',label:'Site Name',adv:false}, … {col:'topic',label:'Topic',adv:true}, {col:'theme',label:'Theme',adv:true}]` and `const activeFilters = new Map()`.
- [ ] **Step 2:** Replace `langClause()` with `function filterClause(){ let s=''; for (const [col,set] of activeFilters){ if(set&&set.size){ const vals=[...set].map(v=>`'${String(v).replace(/'/g,"''")}'`).join(','); s+=` AND ${col} IN (${vals})`; } } return s; }`. Delete the `language` variable and `setLanguage`. Replace every `langClause()` call with `filterClause()`; `win(a,b)` appends `filterClause()`.
- [ ] **Step 3:** Add `async function initFilters()`: for each dim, if `pvCols.has(col)`, query `SELECT DISTINCT col v FROM pv WHERE col IS NOT NULL ORDER BY 1`; if ≥2 rows, instantiate a `FilterControl` on `flt_<col>` with `onChange: ()=>{ activeFilters.set(col, ctl.getSelected()); renderChips(); render(); }`; else hide its mount. Hide the `.filter-adv` block and `#advancedToggle` if no advanced control was created. Call `initFilters()` in boot after `pvCols` is known (near `setupColumnPickers()`).
- [ ] **Step 4:** Add `function renderChips()`: render `activeFilters` entries with values as `Label: value ✕` (collapse to `Label: N selected` when a set has >2), plus a `Clear all` chip when any active; wire ✕ to remove one value (and call the control's `setSelected`), Clear-all to empty all controls. Wire `#advancedToggle` to toggle `.filter-adv.hidden` + caret.
- [ ] **Step 5:** Browser check: select a Division value → KPIs/traffic/pages change; add a Content Type → ANDs; chips show; remove chip / Clear all restores. No console errors.
- [ ] **Step 6:** Commit.

---

### Task 4: Division & Region bar-list drilldowns (replace donut)

**Files:**
- Modify: `dashboard/dashboard.html` (Overview charts-grid ~237–244; replace `renderDivision` ~703–717; add region widget; render dispatch ~648; CSS for `.bar-row`/`.breadcrumb`)

**Consumes:** `filterClause()`, `win()`, `query()`, `DIVISION_COLOR_MAP`, `pvCols`.
**Produces:** `renderDivDrill(w)`, `renderRegionDrill(w)`, drill-state objects, ported `renderBars`/`buildDrillBreadcrumb`.

- [ ] **Step 1:** Port `.bar-row`, `.bar-row-label`, `.bar-row-track`, `.bar-row-fill`, `.bar-row-count`, `.breadcrumb*` CSS from `Clicks/dashboard/dashboard.html` (grey fills, corporate palette).
- [ ] **Step 2:** Replace the "Audience by Division" donut card markup with two cards: `id="divDrillBreadcrumb"`+`id="divDrillBars"` and `id="regionDrillBreadcrumb"`+`id="regionDrillBars"`. Remove `chartDivision` canvas + `renderDivision`/`destroy('division')` usage.
- [ ] **Step 3:** Add `const DIV_LEVELS=['hr_division','hr_unit','hr_area','hr_sector']`, `DIV_LABELS=['Division','Unit','Area','Sector']`, `REG_LEVELS=['hr_region','hr_country']`, `REG_LABELS=['Region','Country']`, `COUNTRY_PALETTE=['#404040','#B98E2C','#8E8D83','#CCCABC','#5A5D5C','#946F29','#B8B3A2','#6C5312']`, and drill-state `divDrill={level:0,filters:[]}`, `regDrill={level:0,filter:null}`.
- [ ] **Step 4:** Port `renderBars(elId, rows, colorFn, drillable, onClickFnName)`, `buildDrillBreadcrumb(...)`, `renderDivDrill(w)` (skips a level whose column is absent, "no data" fallback, uses `win()` + local `drillWhere`, metric = `COUNT(DISTINCT session_id)`), `renderRegionDrill(w)`, and window nav fns `divDrillInto/divNavigateTo/regionDrillInto/regionNavigateTo` — adapted to SiteOwner column names and `query()`.
- [ ] **Step 5:** In `renderActiveTab` overview branch, replace `renderDivision(w)` with `renderDivDrill(w)` and `renderRegionDrill(w)`.
- [ ] **Step 6:** Browser check: Division bars show; click descends Division→Unit→Area→Sector; breadcrumb returns; Region→Country works; both respect an active bar filter. No console errors.
- [ ] **Step 7:** Commit.

---

### Task 5: Page → URL/Language expandable rows

**Files:**
- Modify: `dashboard/dashboard.html` (`PAGES_COLDEFS` page_name cell ~870–871; `drawPagesTableUI` body render ~985; new expand handler)

**Consumes:** `query()`, `win()`, `filterClause()`, `lastPagesTable`.
**Produces:** `togglePageExpand(pageName, rowIdx)` + child-row rendering.

- [ ] **Step 1:** Only when the visible dims are the default single `page_name` group (no `page_url`/`language`/`page_key` already shown), prefix the `page_name` cell with a chevron button `‹expand›` carrying `data-pn`.
- [ ] **Step 2:** Add `async function togglePageExpand(pn, idx)`: if collapsed, query `SELECT page_url, COALESCE(language,'Other') language, COUNT(*) views, COUNT(DISTINCT session_id) visits, COUNT(DISTINCT person_id) uniques, AVG(time_on_page_sec) avg_tos FROM pv WHERE ${win} AND page_name = '<esc>' GROUP BY 1,2 ORDER BY visits DESC`; inject child `<tr class="pg-child">` rows beneath the parent (indented, one per URL/language variant, metric cells matching visible metric columns); toggle again removes them. Suppress the chevron when only one variant.
- [ ] **Step 3:** Add `.pg-child` CSS (indented first cell, subtle `Row Alt` background) and a caret state.
- [ ] **Step 4:** Browser check: expand a page → its language/URL variants appear with per-variant metrics that sum to the parent under the active filter; collapse works. No console errors.
- [ ] **Step 5:** Commit.

---

### Task 6: Rebuild standalone + full verification

**Files:**
- Output: `output/site_dashboard_standalone.html`

- [ ] **Step 1:** `python scripts/build_standalone_dashboard.py`.
- [ ] **Step 2:** Open the standalone from `file://` (Playwright): confirm bar, chips, drilldowns, and page expand all work with embedded data; no console errors.
- [ ] **Step 3:** Update `README.md` filter/drilldown section if it enumerates features.
- [ ] **Step 4:** Commit.
