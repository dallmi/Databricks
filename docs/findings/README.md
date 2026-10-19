# Findings

Deep-dive findings about the analytics platform: what each Genie question (Q1–Q30)
and each export analysis showed, how to read it, and what follows from it. They
complement the schema reference in [../tables/](../tables/), the join recipes in
[../joins/](../joins/) and the raw Genie transcripts in
[../genie_questions_imep.md](../genie_questions_imep.md).

The repository is public, so these pages are sanitized: object names that carry
the organisation's name are written as `__ORG__`, person ids are synthetic
(`t100200`, `00100200`), infrastructure ids are placeholders, and production
figures appear only as orders of magnitude or qualitative statements. Unverified
points are collected in [open-questions.md](open-questions.md).

## iMEP (email platform)

- [iMEP Core Schemas, Tracking ID Location and the First HR Identifier Search](q01-q03-imep-schemas-and-tracking-id.md) — Q1–Q3: `TrackingId` sits on `tbl_email` (mailing level) and is named differently in each system; the four core Bronze schemas; the first "no GPN in HR" result, superseded by Q3b.
- [iMEP Gold Mailing and Event Masters — and a Corrected Conclusion](q01b-imep-gold-layer.md) — Q1b: `tbl_pbi_platform_mailings` and `tbl_pbi_platform_events`, pack-dimension SQL from Gold, sentinel year 2124; "Gold has no engagement aggregates" was withdrawn.
- [iMEP Gold Inventory: Engagement Aggregates Exist](q16-imep-gold-inventory.md) — Q16: imep_gold tables by tier, per-mailing open/click KPI tables, Gold-first confirmed, the NULL-region blocker.
- [iMEP Gold KPI Tables: Schemas, Join Key and NULL Semantics](q21-imep-gold-tier3-schemas.md) — Q21: `MailingId` join (lower-case `mailingid` in engagement), semantic NULLs, COALESCE and pivot recipes, unique counts are not additive across mailings.
- [Silver Layer: SharePoint Has One, iMEP Email Does Not](q26-imep-silver.md) — Q26: email goes straight from Bronze to `imep_gold.final`; do not build `silver.fact_email`.
- [iMEP Join Graph from a Column-Fingerprint Scan](q27-imep-join-graph.md) — Q27: two full-key fact hubs, `EmailId` as backbone, `TrackingId` as a dimension key, cross-channel only through dimensions.
- [Pipeline Operations and Refresh Rhythm from Delta History](q28-pipeline-operations.md) — Q28: MERGE → OPTIMIZE → VACUUM, 7-day retention, Bronze once or twice a day, Gold fully rebuilt about twice a day.
- [Gold Tier Structure for iMEP and SharePoint](q29-gold-tier-structure.md) — Q29: four-tier model, `final` grain is mailing × recipient × event × hour, the only bridge is TrackingId → pages → marketingPageId.
- [iMEP Bronze Data Model and the Three Canonical Join Patterns](imep-data-model.md) — table roles and keys, SQL patterns for KPI summary, fact table and engagement detail, multi-device caveats.

## SharePoint and the cross-channel key

- [SharePoint Gold and CPLAN Inventory](q17-sharepoint-gold-inventory.md) — Q17: full bronze/silver/gold stack, page-view metrics in `sharepoint_gold`, clicks in `sharepoint_clicks_gold`, CPLAN already in Databricks as `sharepoint_cplan`.
- [SharePoint Gold Metric Schemas and the Foreign-Key Chain to Pages](q22-sharepoint-gold-schemas.md) — Q22: no gold metric carries a tracking id; the path is `marketingPageId → pages.pageUUID → pages.__ORG__GICTrackingID`.
- [SharePoint Bronze: `pages` (Inventory) and `pageviews` (Interactions)](sharepoint-bronze-pages-and-pageviews.md) — only article pages carry the tracking id; `pageviews` names the column `GICTrackingID`.
- [CammsTrackingID: the Cross-Channel Key from Communication Planning](camms-tracking-id.md) — the five segments, the derived `tracking_pack_id`, the joins to channels, clusters and packs.
- [Tracking ID Format: Same Format, Disjoint Values, Pack-Level Match](q23-tracking-id-format.md) — Q23: cross-channel matching works only at pack level (segments 1–2).
- [Tracking ID Volume and the Pack Universe (Email Side)](q24-tracking-id-volume.md) — Q24: a small minority of mailings carry a tracking id; the rows collapse to a few dozen packs.
- [SharePoint Pages: Tracking ID Coverage by Time and Site](q25-sharepoint-pages-coverage.md) — Q25: tracked pages sit on "News and events"; restrict to that site from 2024-09-01.
- [Cross-Channel MVP Scope: iMEP, Page Views and Pages](cross-channel-mvp-scope.md) — the first minimum viable product (MVP) scope and how later questions refined it.

## Platform

- [Storage Architecture and Physical Layout](q30-storage-architecture.md) — Q30: external Delta tables on three storage accounts, gold co-located, mostly notebook-based full rebuilds, no partitioning.

## Identities and App Insights

- [HR Employee Identifiers: T-Number, GPN and the HR Bridge](hr-employee-identifiers.md) — T-number and Global Personnel Number (GPN) are different keys; `imep_bronze.tbl_hr_employee.WORKER_ID` = GPN is the bridge.
- [Application Insights Source: pageViews and customEvents](appinsights-source.md) — envelopes of both streams, the double-nested `CustomProps` JSON, `CammsTrackingID` only on pageViews.
- [App Insights Sessions and Identity](appinsights-sessions-and-identity.md) — cookie ids near-unique per view since about 8 April 2026; `person_id` and `visit_id` derivation; metric definitions.

## SiteOwnerDashboard pipeline

- [SiteOwnerDashboard Pipeline: Page Grain, HR Hierarchy, CSV Timestamps and Interactions](siteowner-dashboard-pipeline.md) — `page_key` rule (English = URL without a language segment), day-first CSV pitfall, the page-view × interaction join contract, DuckDB-WASM pitfalls.
- [Incremental Input Pipeline: SHA-256 Manifest and Composite-Key Upsert](incremental-input-pipeline.md) — per-file manifest plus delete-then-insert upsert; the App Insights `id` is not event-unique.

## Adding a finding

Write one page per question or analysis, with the sections Question, Findings,
Consequences and Related, add it to this index, and log the session in
[../sources.md](../sources.md). The commit hooks check the page for the
organisation's name, person ids, infrastructure ids and local paths; exact
production figures they cannot detect, so write those as orders of magnitude.
