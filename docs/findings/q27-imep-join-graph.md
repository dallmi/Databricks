# iMEP Join Graph from a Column-Fingerprint Scan

> Source: Genie question Q27 (2026-04-20). Status: confirmed for the join hubs and the tracking-id placement; the rule "T-number exists in only two tables" is partly validated and awaits a broader re-search.

## Question

Without Unity Catalog lineage, which tables share join columns? Which tables are the strong join hubs, which keys are primary versus contextual, and what constrains linking across domains?

The scan looked for the columns `Id`, `EmailId`, `mailingid`, `TrackingId`, `__ORG__GICTrackingID`, `GICTrackingID`, `TNumber`, `T_NUMBER` and `WORKER_ID`. **93 tables across 8 schemas** contain at least one of them.

## Findings

### 1. Two full-key fact hubs

Only two tables carry all three primary join keys `Id`, `EmailId` and `TNumber`. They are the only full-granularity, person-level engagement facts:

| Table | Keys | Size | Time span |
|---|---|---|---|
| `imep_bronze.tbl_analytics_link` | `Id`, `EmailId`, `TNumber` | hundreds of millions of rows | late 2020 – April 2026 |
| `imep_bronze.tbl_email_receiver_status` | `Id`, `EmailId`, `TNumber` | hundreds of millions of rows | late 2020 – April 2026 |

### 2. Secondary tables

| Table | Keys | Size | Time span |
|---|---|---|---|
| `imep_gold.final` | `Id`, `MailingId` | hundreds of millions of rows | mid-2021 – April 2026 |
| `sharepoint_bronze.pageviews` | `Id`, `GICTrackingID` | over a hundred million rows | 2023 – April 2026 |
| `imep_bronze.tbl_email_components` | `Id`, `EmailId` | millions of rows | late 2021 – April 2026 |
| `imep_bronze.tbl_email_template_images` | `Id`, `EmailId` | millions of rows | late 2021 – April 2026 |
| `imep_bronze.tbl_hr_employee` | `WORKER_ID`, `T_NUMBER` | hundreds of thousands of rows | 2020 – 2027 (future dates present) |
| `imep_bronze.tbl_email` | `Id`, `TrackingId` | over a hundred thousand rows | late 2020 – April 2026 |
| `imep_bronze.tbl_event` | `Id`, `TrackingId` | about a hundred thousand rows | 2009 – April 2026 |
| `imep_gold.tbl_pbi_platform_events` | `Id`, `TrackingId` | tens of thousands of rows | 2013 – April 2026 |
| `imep_gold.tbl_pbi_platform_mailings` | `Id`, `TrackingId` | tens of thousands of rows | late 2020 – April 2026 |
| `sharepoint_bronze.pages` | `__ORG__GICTrackingID`, `Id` | tens of thousands of rows | 1900 (placeholder date) – April 2026 |

Caveats visible in the time spans: `pages` contains a 1900 placeholder date; `tbl_hr_employee` contains dates in the future (presumably planned contract dates); `final` starts later than the Bronze mailing tables, so it does not cover the earliest mailings. The Bronze mailing table has noticeably more rows than the Gold mailing master.

### 3. `EmailId` is the backbone — present in 12 tables

It connects the email definition, components and assets, engagement facts and the Gold platform tables.

### 4. Person-level joins are narrow

`TNumber` was found in **only 2 tables**: `tbl_analytics_link` and `tbl_email_receiver_status`. These are the only tables that join to `tbl_hr_employee.T_NUMBER`. Read literally: person-level analytics exist only at email-engagement grain, and SharePoint has no person key comparable to the T-number.

This rule is **pending re-validation**. The scan looked for a fixed list of column names, and synonyms escape it: `imep_gold.final.RecipientID` is built from `TNumber` (see Pattern 2 in [imep-data-model.md](imep-data-model.md)), and SharePoint Gold has a visitor dimension (`employeecontact`) that is a candidate person bridge ([q29-gold-tier-structure.md](q29-gold-tier-structure.md)). Until a broader search over all schemas (including synonyms such as `EmployeeId`, `WorkerId`, `UserId`, `RecipientId`) is done, cite this rule as "per Q27, pending re-validation", not as a hard constraint.

### 5. `TrackingId` is a dimension key, not a fact key

`TrackingId` appears in **exactly 4 tables** — `tbl_email`, `tbl_event`, `tbl_pbi_platform_mailings`, `tbl_pbi_platform_events` — and **never together with `EmailId`**. It belongs to the mailing and event dimension: a campaign-level identifier, not a transactional one, and intentionally sparse.

### 6. SharePoint has its own parallel tracking key

`GICTrackingID` appears in `sharepoint_bronze.pageviews` and its visit tables and in `sharepoint_silver.webpage` (as `gICTrackingID`), again never with `EmailId`. The cross-channel link is therefore dimensional:

```
Email TrackingId  ↔  SharePoint __ORG__GICTrackingID
        └──── via pages (dimension table) ────┘
              not via engagement tables
```

The match itself runs on the pack (segments 1–2), see [q23-tracking-id-format.md](q23-tracking-id-format.md).

### 7. Gold aggregation topology

The 26 `imep_gold.tbl_pbi_*` tables all map back through the same chain:

```
MailingId = tbl_pbi_platform_mailings.Id = tbl_email.Id
```

### 8. New schema: `page_metadata_bronze`

Not seen in any earlier inventory:

| Table | Size | Purpose |
|---|---|---|
| `pagelikesview` | tens of thousands of rows | Page likes |
| `comments` | thousands of rows | Comments |
| `reportedcomments` | — | Moderation |
| `moderatedcomments` | — | Moderation |
| `pageviews` | hundreds of rows | Metadata-level views |

Comment and like metadata, separate from the main page-view facts — potentially useful for engagement enrichment in cross-channel views.

## Consequences

1. **Two join hubs:** `tbl_analytics_link` and `tbl_email_receiver_status` are the only full-key fact tables.
2. **`EmailId` is the backbone** from email definition to components, engagement and Gold.
3. **Cross-channel linkage is dimensional, not factual:** tracking id on the mailing ↔ tracking id on the page, never engagement row ↔ engagement row.
4. **Do not treat "person analytics are email-only" as settled** until the broader T-number search is done.
5. **Documentation:** cards exist for `tbl_email`, `tbl_analytics_link`, `tbl_email_receiver_status` and `imep_gold.final`; `page_metadata_bronze` has no card yet.

## Related

- [q23-tracking-id-format.md](q23-tracking-id-format.md) · [q26-imep-silver.md](q26-imep-silver.md) · [q29-gold-tier-structure.md](q29-gold-tier-structure.md) · [hr-employee-identifiers.md](hr-employee-identifiers.md) · [sharepoint-bronze-pages-and-pageviews.md](sharepoint-bronze-pages-and-pageviews.md) · [imep-data-model.md](imep-data-model.md)
- [../tables/imep/tbl_analytics_link.md](../tables/imep/tbl_analytics_link.md) · [../tables/imep/tbl_email_receiver_status.md](../tables/imep/tbl_email_receiver_status.md) · [../tables/imep/tbl_email.md](../tables/imep/tbl_email.md) · [../tables/sharepoint/pages.md](../tables/sharepoint/pages.md)
- [../diagrams/er_imep_bronze.md](../diagrams/er_imep_bronze.md) · [../diagrams/er_cross_channel.md](../diagrams/er_cross_channel.md) · [../joins/join_strategy_contract.md](../joins/join_strategy_contract.md)
- [../genie_questions_imep.md](../genie_questions_imep.md) — Q27 · [../sources.md](../sources.md#q27)
