# KQL Queries — AppInsights pageViews

Standard-Queries für den AppInsights `pageViews`-Stream im Azure Portal
(Application Insights → Logs).

## Workspace

- Resource: AppInsights instance hosting the intranet telemetry
- Table: `pageViews`

## Conventions

- `customDimensions` ist eine String-Spalte mit JSON. `CustomProps` darin ist
  selbst nochmal ein String → daher `parse_json(tostring(parse_json(...)))`.
- Zeitfilter IMMER zuerst (Performance + Cost).
- `CammsTrackingID` ist der Cross-Channel-Join-Key (siehe CPLAN
  `pipeline/docs/tracking-id.md`). Format: `CLUSTER-PACK-YYMMDD-ACTIVITY-CHANNEL`.

## Query Files

| File | Zweck |
|---|---|
| [`base_flatten.kql`](base_flatten.kql) | Basis-Flatten: alle Top-Level + alle CustomProps als eigene Spalten |
| [`by_pageurl.kql`](by_pageurl.kql) | Views einer bestimmten PageURL |
| [`by_site.kql`](by_site.kql) | Views einer Site (SiteID oder SiteName) |
| [`by_tracking_id.kql`](by_tracking_id.kql) | Views per CammsTrackingID / Pack / Cluster / Channel |
| [`tracking_coverage.kql`](tracking_coverage.kql) | Wieviel % der PageViews tragen tracking_id, aufgeschlüsselt nach Site |
| [`export_for_pipeline.kql`](export_for_pipeline.kql) | Vollständiger Export für `flatten_appinsights.py` |

## Export für die lokale Pipeline

In Application Insights Logs:
1. Query laufen lassen (Zeitraum max. ~30 Tage / 65k rows pro Run).
2. **"Export → CSV (all columns)"** rechts oben.
3. CSV in `Databricks/input/` ablegen → `python scripts/flatten_appinsights.py`.

Für größere Volumina: per Continuous Export oder Workbook-Trigger nach Storage.
