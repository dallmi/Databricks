# Application Insights Source: pageViews and customEvents

> Source: export analysis and Kusto Query Language (KQL) key inventories from the Clicks and CampaignWe projects (2026-04-29). Status: confirmed.

## Context

Azure Application Insights (App Insights) collects the intranet (SharePoint) telemetry. Two streams land in Databricks Bronze:

| App Insights stream | Bronze table | Content |
|---|---|---|
| `pageViews` | `sharepoint_bronze.pageviews` | page loads |
| `customEvents` | `sharepoint_bronze.customevents` | clicks, searches, video actions |

This page describes the data **at the source**, before any flattening. You need it to build or review an extract, transform, load (ETL) step or a flattener for raw portal exports.

## Findings

### Shared envelope: double-nested `CustomProps`

Both streams carry top-level fields plus `customDimensions`. That is a JSON string with **exactly one key**, `CustomProps`, and the value of `CustomProps` is itself a JSON string. You have to parse it twice:

```kql
| extend CP = parse_json(tostring(parse_json(customDimensions).CustomProps))
```

```sql
-- Spark SQL
get_json_object(get_json_object(customDimensions, '$.CustomProps'), '$.GPN') AS gpn
```

In `scripts/flatten_appinsights.py`, only keys inside `CustomProps` get the `cp_` prefix (`cp_Theme`, `cp_Email`, …). Top-level fields keep their names. Test fixtures must reproduce the double nesting, or the prefixed columns will not appear.

### `pageViews`

**Top-level fields:** `timestamp`, `id`, `name`, `url`, `duration`, `performanceBucket`, `itemType`, `customDimensions`, `session_Id`, `user_Id`, `client_Type`, `client_OS`, `client_IP`, `client_City`, `client_StateOrProvince`, `client_CountryOrRegion`, `client_Browser`, `operation_Name`, `operation_Id`, `operation_ParentId`, `appId`, `iKey`, `sdkVersion`, `itemCount`.

**`CustomProps` keys:** `SiteID`, `SiteName`, `PageId`, `PageName`, `CammsTrackingID`, `ContentOwner`, `ContentType`, `Email`, `GPN`, `NewsCategory`, `PageStatus`, `PageURL`, `PublishingDate`, `TargetOrganisation`, `TargetRegion`, `Theme`, `Topic`, `refUri`.

`CammsTrackingID` (format `CLUSTER-PACK-YYMMDD-ACTIVITY-CHANNEL`) is the cross-channel join key. Only `pageViews` carries it. Keep it as its own column through every ETL or flattening step.

### `customEvents`

**Top-level fields:** `timestamp`, `id`, `name`, `user_Id`, `session_Id`, `client_CountryOrRegion`, `client_Type`, `client_OS`, `client_Browser`, `customDimensions`. This is a subset of the `pageViews` envelope.

**`name` tells you the event family, and the schema differs by family.** Always filter on `name` first:

| `name` | `CustomProps` shape |
|---|---|
| `click_event` | flat (level 2): link clicks, button clicks, downloads, video actions |
| `SEARCH_TRIGGERED`, `SEARCH_RESULT_CLICK` | deeply nested (levels 3–4). A flattener must walk all four levels |
| others | exist; run `summarize count() by name` for the inventory |

The `click_event` keys are listed by bucket (person, page context, click detail, download detail, targeting, video) in the table card [customevents.md](../tables/sharepoint/customevents.md#customprops--business-payload-for-name--click_event).

**`customEvents` has no `CammsTrackingID`.** To attribute clicks across channels, join through the page inventory: `CustomProps.PageId` → `sharepoint_bronze.pages.pageUUID` → `pages.__ORG__GICTrackingID`.

### Identity fields

| Field | Meaning |
|---|---|
| `user_Id` | App Insights anonymous browser or device identifier. **Not a person.** Its cardinality is clearly higher than that of the GPN, because one employee uses several devices and browsers |
| `session_Id` | App Insights session identifier. Its behaviour changed in April 2026, see [appinsights-sessions-and-identity.md](appinsights-sessions-and-identity.md) |
| `CustomProps.GPN` | The real person identifier (Global Personnel Number), an 8-digit string |
| `CustomProps.Email` | E-mail address, used together with the GPN for HR matching |

### Raw portal exports

Portal exports use the App Insights column names, with a header that starts `timestamp [UTC],id,name,url,duration,…`. Two properties of these exports matter for any ingestion code:

- CSV exports write the timestamp day-first (`DD/MM/YYYY, HH:MM:SS.mmm`). See [siteowner-dashboard-pipeline.md](siteowner-dashboard-pipeline.md#3-day-first-timestamps-in-portal-csv-exports).
- `id` is **not** unique per event. See [incremental-input-pipeline.md](incremental-input-pipeline.md).

## Consequences

- Every flattener has to parse `customDimensions` → `CustomProps` twice.
- `pageViews`: expose `CammsTrackingID` as a first-class column for cross-channel joins.
- `customEvents`: filter on `name` before parsing. Route cross-channel attribution through `pages.__ORG__GICTrackingID`.
- Never use `user_Id` as a person identifier. Extract `CustomProps.GPN` and bridge to HR through `imep_bronze.tbl_hr_employee.WORKER_ID` (see [hr-employee-identifiers.md](hr-employee-identifiers.md)).
- Canonical KQL in this repository: [`kql/base_flatten.kql`](../../kql/base_flatten.kql) for `pageViews` and [`kql/customevents_clicks.kql`](../../kql/customevents_clicks.kql) for `customEvents`. The Clicks project holds the master flattener for all intranet clicks and a set of profiling queries (event-name discovery, key inventory, link-type distribution). The CampaignWe project holds a page-filtered variant and the four-level nesting recipe for search events.

## Related

- [customevents.md](../tables/sharepoint/customevents.md), [pages.md](../tables/sharepoint/pages.md): Bronze table cards
- [sharepoint-bronze-pages-and-pageviews.md](sharepoint-bronze-pages-and-pageviews.md): Bronze architecture and the naming of the tracking-ID columns
- [camms-tracking-id.md](camms-tracking-id.md), [q23-tracking-id-format.md](q23-tracking-id-format.md): tracking-ID structure and cross-channel matching
- [appinsights-sessions-and-identity.md](appinsights-sessions-and-identity.md): `session_Id`, `user_Id` and `person_id`
- [hr-employee-identifiers.md](hr-employee-identifiers.md): GPN ↔ T-number
- [siteowner-dashboard-pipeline.md](siteowner-dashboard-pipeline.md), [incremental-input-pipeline.md](incremental-input-pipeline.md): how raw exports are processed
