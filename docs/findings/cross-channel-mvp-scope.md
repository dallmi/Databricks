# Cross-Channel MVP Scope: iMEP, Page Views and Pages

> Source: project scoping decision, made before Genie question Q1 (2026-04-16). Status: confirmed as scope; the join details were refined by Q1, Q23, Q24 and Q25.

## Question

Which sources does the first cross-channel minimum viable product (MVP) combine, and how are they joined?

## Findings

### The three streams

The first integration combines three sources, linked through the campaign tracking id from the campaign planning system (CPLAN):

1. **iMEP — e-mail channel.** Mailings carry the channel code `EMI` in their tracking id. At scoping time the Databricks source was still unknown; it is now `imep_bronze.tbl_email.TrackingId` for the id and `imep_gold` for engagement ([q01-q03-imep-schemas-and-tracking-id.md](q01-q03-imep-schemas-and-tracking-id.md), [q16-imep-gold-inventory.md](q16-imep-gold-inventory.md)).
2. **Page views — intranet telemetry from Application Insights.** Flattened by `scripts/flatten_appinsights.py` into `fact_page_view` and `agg_session`. The tracking id sits inside `customDimensions.CustomProps` and has to be exposed as a first-class column ([appinsights-source.md](appinsights-source.md)). On the Databricks side the same id appears as `GICTrackingID` in `sharepoint_bronze.pageviews` ([sharepoint-bronze-pages-and-pageviews.md](sharepoint-bronze-pages-and-pageviews.md)).
3. **Pages — intranet page metadata** (`dim_page`, `pbi_db_website_page_inventory`, `pbi_db_website_webpage_inventory`): page name, site, content owner, theme, topic, publishing date and similar.

### Join model as scoped

- **E-mail → page view:** match on the tracking id, or on `tracking_pack_id` (segments 1–2), to relate e-mail campaigns to the intranet visits they cause.
- **Page view → page:** the existing `page_id` or URL join of the page-view pipeline.
- **Channel split:** `tracking_channel_abbr` (segment 5) tells whether a row is e-mail (`EMI`), event (`EVT`), page, and so on.

### Refinements since scoping

| Topic | As scoped | Current state |
|---|---|---|
| Column name | `CammsTrackingID` | Different name per system: `TrackingId`, `__ORG__GICTrackingID`, `GICTrackingID`, `tracking_id` (Q1) |
| Match level | full tracking id or pack | **Pack (segments 1–2) only.** Full-string and segment 1–4 matches fail because e-mail and page of the same pack differ in date, activity number and channel (Q23, business refinement 2026-04-20) |
| Where the link happens | e-mail send ↔ page view | Dimensional: mailing master ↔ `pages`, never engagement row ↔ engagement row (Q27, Q29) |
| Universe | all mailings | Only a small minority of mailings carry a tracking id, though adoption is rising; the e-mail side yields a few dozen packs (Q24) |
| Page coverage | all pages | Tracked pages sit almost entirely on one site ("News and events"), with tracking rolled out from September 2024. Restrict the page side to that site (Q25) |
| Time window | open | Effective dashboard floor 2024-09-01 (SharePoint rollout, Q25), replacing the e-mail-side default of 2025-01-01 (Q24) |

## Consequences

1. **Build these three streams first.** Do not onboard all channels at once; events (`EVT`) and banners follow in later phases.
2. **Prerequisite:** split the tracking id into its five segments and derive `tracking_pack_id` ([camms-tracking-id.md](camms-tracking-id.md), [q23-tracking-id-format.md](q23-tracking-id-format.md)).
3. **Pack-level funnel:** e-mail KPIs per pack from iMEP Gold, page metrics per pack through `pages` ([../joins/cross_channel_via_tracking_id.md](../joins/cross_channel_via_tracking_id.md)).
4. **Person-level linking** (T-number ↔ GPN) is a separate bridge through HR, not through the tracking id ([hr-employee-identifiers.md](hr-employee-identifiers.md)).
5. Once the trio works end to end, it is the template for adding further channels.

## Related

- [q01-q03-imep-schemas-and-tracking-id.md](q01-q03-imep-schemas-and-tracking-id.md) · [q23-tracking-id-format.md](q23-tracking-id-format.md) · [q24-tracking-id-volume.md](q24-tracking-id-volume.md) · [q25-sharepoint-pages-coverage.md](q25-sharepoint-pages-coverage.md) · [q27-imep-join-graph.md](q27-imep-join-graph.md) · [q29-gold-tier-structure.md](q29-gold-tier-structure.md)
- [camms-tracking-id.md](camms-tracking-id.md) · [appinsights-source.md](appinsights-source.md) · [sharepoint-bronze-pages-and-pageviews.md](sharepoint-bronze-pages-and-pageviews.md) · [hr-employee-identifiers.md](hr-employee-identifiers.md) · [siteowner-dashboard-pipeline.md](siteowner-dashboard-pipeline.md)
- [../BRD_cross_channel_analytics.md](../BRD_cross_channel_analytics.md) · [../joins/cross_channel_via_tracking_id.md](../joins/cross_channel_via_tracking_id.md) · [../diagrams/er_cross_channel.md](../diagrams/er_cross_channel.md)
