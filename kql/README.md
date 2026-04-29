# KQL Queries — AppInsights `pageViews` + `customEvents`

Standard queries against the two AppInsights streams that feed the Bronze
layer in Azure Application Insights → Logs.

## Workspace

- Resource: AppInsights instance hosting the intranet telemetry
- Tables: `pageViews` (page loads, ~173M Bronze rows), `customEvents`
  (clicks + searches + video, ~262M Bronze rows)

## Conventions

- `customDimensions` is a string column containing JSON. `CustomProps`
  inside it is itself a string, so use
  `parse_json(tostring(parse_json(...)))`.
- Always filter on `timestamp` first (performance + cost).
- `CammsTrackingID` (the cross-channel join key, format
  `CLUSTER-PACK-YYMMDD-ACTIVITY-CHANNEL`) lives **only on `pageViews`**.
  For `customEvents` (clicks), recover the TrackingID via
  `pageId → sharepoint_bronze.pages.UBSGICTrackingID`.

## Query Files

### `pageViews` stream

| File | Purpose |
|---|---|
| [`base_flatten.kql`](base_flatten.kql) | Base flatten: all top-level fields plus all `pageViews` CustomProps as own columns |
| [`by_pageurl.kql`](by_pageurl.kql) | Views for a single PageURL |
| [`by_site.kql`](by_site.kql) | Views for a site (SiteID or SiteName) |
| [`by_tracking_id.kql`](by_tracking_id.kql) | Views by CammsTrackingID / pack / cluster / channel |
| [`tracking_coverage.kql`](tracking_coverage.kql) | Share of pageViews carrying a tracking_id, broken down by site |
| [`export_for_pipeline.kql`](export_for_pipeline.kql) | Full export feeding `flatten_appinsights.py` |

### `customEvents` stream

| File | Purpose |
|---|---|
| [`customevents_clicks.kql`](customevents_clicks.kql) | Flatten click_event interactions + 4 alternative queries (event-name inventory, key inventory, anonymized export, Link_Type sanity check). Ported from the [Clicks](../../Clicks) project. |

For the full profiling suite (13 queries — daily trend, ContentType cross-tab,
domain analysis etc.) see
[`/Users/micha/Documents/Arbeit/Clicks/clicks_explorer.kql`](../../Clicks/clicks_explorer.kql).

## Export to the local pipeline

In Application Insights → Logs:
1. Run the query (max ~30 days / 65k rows per export).
2. Click **"Export → CSV (all columns)"** in the top right.
3. Drop the CSV into `Databricks/input/` and run
   `python scripts/flatten_appinsights.py`.

For larger volumes use Continuous Export or a Workbook trigger that
writes to Storage.
