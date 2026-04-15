# KQL Queries — AppInsights pageViews

Standard queries against the AppInsights `pageViews` stream in the Azure
Portal (Application Insights → Logs).

## Workspace

- Resource: AppInsights instance hosting the intranet telemetry
- Table: `pageViews`

## Conventions

- `customDimensions` is a string column containing JSON. `CustomProps`
  inside it is itself a string, so use
  `parse_json(tostring(parse_json(...)))`.
- Always filter on `timestamp` first (performance + cost).
- `CammsTrackingID` is the cross-channel join key (see CPLAN
  `pipeline/docs/tracking-id.md`). Format:
  `CLUSTER-PACK-YYMMDD-ACTIVITY-CHANNEL`.

## Query Files

| File | Purpose |
|---|---|
| [`base_flatten.kql`](base_flatten.kql) | Base flatten: all top-level fields plus all CustomProps as own columns |
| [`by_pageurl.kql`](by_pageurl.kql) | Views for a single PageURL |
| [`by_site.kql`](by_site.kql) | Views for a site (SiteID or SiteName) |
| [`by_tracking_id.kql`](by_tracking_id.kql) | Views by CammsTrackingID / pack / cluster / channel |
| [`tracking_coverage.kql`](tracking_coverage.kql) | Share of pageViews carrying a tracking_id, broken down by site |
| [`export_for_pipeline.kql`](export_for_pipeline.kql) | Full export feeding `flatten_appinsights.py` |

## Export to the local pipeline

In Application Insights → Logs:
1. Run the query (max ~30 days / 65k rows per export).
2. Click **"Export → CSV (all columns)"** in the top right.
3. Drop the CSV into `Databricks/input/` and run
   `python scripts/flatten_appinsights.py`.

For larger volumes use Continuous Export or a Workbook trigger that
writes to Storage.
