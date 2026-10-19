# SiteOwnerDashboard Pipeline: Page Grain, HR Hierarchy, CSV Timestamps and Interactions

> Source: pipeline work on the SiteOwnerDashboard with real intranet exports (2026-07-13 to 2026-07-15). Status: confirmed.

## Context

The SiteOwnerDashboard is a standalone, single-site HTML dashboard. It runs DuckDB-WASM (the WebAssembly build of DuckDB) on Parquet files. It sits on top of the shared flattener `scripts/flatten_appinsights.py` and produces two parquets at event grain:

| Parquet | View name in the dashboard | Source stream | Build script |
|---|---|---|---|
| `output/site_pageviews.parquet` | `pv` | `pageViews` | `SiteOwnerDashboard/scripts/process_site_pageviews.py` |
| `output/site_interactions.parquet` | `ix` | `customEvents`, `click_event` only | `SiteOwnerDashboard/scripts/process_site_interactions.py` |

This page collects what the pipeline work taught about the data. For design decisions and the user-facing description, see [DESIGN.md](../../SiteOwnerDashboard/DESIGN.md) and the [project README](../../SiteOwnerDashboard/README.md).

## Findings

### 1. Page grain: one logical page exists once per language

The same logical page exists once per language, and every variant has its **own `PageId`**:

| URL | Language |
|---|---|
| `…/de/<page>.aspx` | DE |
| `…/<page>.aspx` | **EN**: the URL has no language segment |
| `…/fr/<page>.aspx` | FR |
| `…/it/<page>.aspx` | IT |

Grouping the pages table by `page_id` listed one page up to four times. The fix:

- **`page_key`** = the URL, lowercased, with the `/xx` language segment (`en|de|fr|it`, case-insensitive, matched as a standalone path segment) stripped and a trailing slash trimmed. When the URL is missing it falls back to the raw URL, then to `page_id` (`canonical_page_key()` in `process_site_pageviews.py`).
- The dashboard groups pages by `page_key`. The language filter still splits them.
- **English = the URL without a language segment.** These pages used to be mislabelled "Other". "Other" now only means there is no usable URL (`derive_language()`).
- Diagnosis for duplicate page rows: `SiteOwnerDashboard/scripts/diagnose_page_grain.py`.

### 2. The pipeline carries the full HR hierarchy and theme/topic

`flatten_appinsights.py` maps the full organisational and content hierarchy. It reads from `CustomProps` and from the monthly HR history snapshots (`hr_history.parquet`, produced by the Search Analytics project). The HR join runs on the GPN and takes the latest snapshot at or before the month of the view. If there is none, it falls back to the earliest later snapshot.

| Source column | Pipeline column |
|---|---|
| `gcrs_division_desc` | `hr_division` |
| `gcrs_unit_desc` | `hr_unit` |
| `gcrs_area_desc` | `hr_area` |
| `gcrs_sector_desc` | `hr_sector` |
| `gcrs_segment_desc` | `hr_segment` |
| `work_location_region` | `hr_region` |
| `work_location_country` | `hr_country` |
| `cp_Theme` | `theme` |
| `cp_Topic` | `topic` |

`hr_region` and `hr_country` are the employee's **work location**. They are not the geo-IP field `client_country`. `build_fact_page_view` globs **all** `hr_*` columns into the fact, and `build_dim_page` keeps `theme` and `topic`, so real exports carry every level automatically.

**Correction to an earlier assumption:** theme and topic were once declared missing from the model. That conclusion came from the *demo* parquet, which only had `hr_division`, `hr_region` and `client_country`. Before you call a field missing, check the pipeline mapping and a real export. The demo generator (`generate_demo_data.py`) now emits the full hierarchy, several countries per region, theme/topic and genuinely multilingual pages that share a `page_key`.

The dashboard drills Division → Unit → Area → Sector and Region → Country. That pattern was ported from the Clicks project's dashboard.

### 3. Day-first timestamps in portal CSV exports

The 2026 corp exports from the App Insights portal are CSV files with the raw portal header (`timestamp [UTC],id,name,url,duration,…`). They write the timestamp **day-first**: `DD/MM/YYYY, HH:MM:SS.mmm`. To verify this, take a file whose range starts on 1 June: its first rows read `01/06/2026, 00:…`.

`_convert_timestamp_utc()` in `scripts/flatten_appinsights.py` calls `pd.to_datetime(..., errors="coerce", utc=True)` without `dayfirst` or `format`. dateutil then prefers month-first:

- Days 1–12 of every month silently swap day and month (`01/06` becomes 6 January).
- Days 13 and later cannot be a month, so they fall back to day-first and parse correctly.

Rows from the first twelve days of each month therefore land in the wrong month. This scrambles window filters, weekly trends and month-over-month (MoM) comparisons. The older exports were XLSX files with real datetime cells, so they never reached the string-parsing path. The problem was reproduced locally on 2026-07-15 with the exact CSV shape.

**Fix:** parse this export family with an explicit `format="%d/%m/%Y, %H:%M:%S.%f"` (or `dayfirst=True`). Detect the format per file and leave the XLSX handling unchanged.

Related export quirks:

- Earlier there was a different defect: an unquoted comma inside the timestamp shifted columns, so operation ids landed in the session and user columns. pandas sometimes absorbs this variant by moving the first field into the index instead. The columns then stay aligned, but the date part is lost, which produces year-1 timestamps. `diagnose_exports.py` tells this export defect apart from the per-row-unique session ids, which are a property of the source (see [appinsights-sessions-and-identity.md](appinsights-sessions-and-identity.md)).
- An export-renaming helper skipped these files with "no timestamp column", even though the header has `timestamp [UTC]`. Its header check failed on this export family.

### 4. Interactions (`customEvents`): the pv × ix join contract

Flow: `export_site_interactions.kql` → `input/interactions/` → `process_site_interactions.py` → `output/site_interactions.parquet` (view `ix`) → "Content & Interactions" tab, plus optional Clicks and click-through rate (CTR) columns in the Overview pages table. Only `click_event` is in scope. `SEARCH_*` events are out of scope.

- **No physical pv × ix join.** Both parquets stay at event grain, and both carry the same three page keys: `page_key`, `page_id` and `page_url`. Each dashboard table aggregates both sides and joins them in DuckDB-WASM **on the grain of that view**:
  - `page_key` for rankings, so that language variants do not compete
  - `page_id` (GUID: equal as a string across both streams, stable when a URL is renamed) for the language-variant drill
- **Unique Visitors and Unique Clickers are always a fresh `COUNT(DISTINCT person_id)` at the view's grain, never summed.** A reader of both the DE and the FR version counts once per logical page and once per variant. Clicker Rate = `ix` clickers ÷ `pv` unique visitors, joined as aggregates at the same grain (for example division).
- In the Interactions explorer, views, CTR and Clicker Rate join to `pv` only when every selected dimension also exists in `pv`. The guard is `dimKeys.every(k => pvCols.has(k))`.
- **Upsert key:** the pageViews `event_key` extended with the link identity (`component_name`, `link_address`, `link_label`). Two clicks on different links in the same second stay distinct.
- **`interaction_class`** is an *added* column. It is `download` when a file name or type label is present, else `video` when `video_action` is present, else `link`. Source columns stay untouched.
- **Filters know their columns per view.** `ix` has no `tracking_channel_abbr`, because `customEvents` carries no `CammsTrackingID`. An active Channel filter is skipped for `ix` queries and flagged on the table, never silently ignored.
- **Links nobody clicks do not show up.** The telemetry has no link inventory, so the "Last clicked" date serves as a proxy.
- `video_action` takes the values Play, Pause and Complete. It drives the completion rate for videos.
- The Top-domains card groups navigational clicks by destination host. It deliberately does not classify them as internal or external.
- XLSX exports carry the on-screen drill as Excel outline groups (`outlineLevel=1`, hidden, `summaryBelow: false`).
- QA: `diagnose_interactions.py`. If more than 10 % of clicks find no matching `pv` `page_key`, the export windows or scopes of the two streams differ.
- Rebuild order: `process_site_interactions.py` (add `--rebuild` after a scope change) → `diagnose_interactions.py` → `build_standalone_dashboard.py`.

### 5. DuckDB-WASM pitfalls

| Pitfall | Symptom | Remedy |
|---|---|---|
| Reserved words as aliases: `label`, `last`, `file`, `language` | "Parser Error: syntax error at or near …", and only at runtime in the browser | Quote the alias: `AS "label"` |
| `COUNT(DISTINCT …)` returns BIGINT, which Arrow hands to JavaScript as a `BigInt` | `r.n - 1` throws "Cannot mix BigInt and other types". It only fires on rows where the count is above 1, so demo data with one value per group ships green | Cast in SQL (`::INT`) or wrap the value in `Number()` |
| `any_value()` picks an arbitrary row | Results are not deterministic | `mode()` is supported (verified on DuckDB-WASM 1.28.0). As an aggregate it keeps the row grain: `mode(link_address)` per file is "the most-downloaded URL" |
| Top-N children per group | n/a | `row_number() OVER (PARTITION BY …)` on grouped results works in DuckDB-WASM |

## Consequences

- Rank pages on `page_key` and drill into language variants on `page_id`. Never sum unique counts across rows or grains.
- An English page is a URL without a language segment. Do not label it "Other".
- Treat theme, topic and the full HR hierarchy as available in real data. Check the pipeline mapping before you conclude a field is missing.
- Open item: before the shared flattener ingests day-first CSV exports, `_convert_timestamp_utc()` needs format detection or an explicit day-first format. When this page was written, the function still parsed without `format` or `dayfirst`.
- Quote reserved aliases and cast BIGINT results in every DuckDB-WASM query.

## Related

- [DESIGN.md](../../SiteOwnerDashboard/DESIGN.md): decisions 9–11 (join contract, `page_key` vs `page_id`, column-aware filters)
- [Interactions explorer design](../../SiteOwnerDashboard/docs/superpowers/specs/2026-07-15-interactions-explorer-design.md)
- [appinsights-source.md](appinsights-source.md): source schema of both streams
- [appinsights-sessions-and-identity.md](appinsights-sessions-and-identity.md): `person_id`, `visit_id` and the metric definitions
- [incremental-input-pipeline.md](incremental-input-pipeline.md): manifest and `event_key` upsert used by both build scripts
- [customevents.md](../tables/sharepoint/customevents.md): `customEvents` table card
