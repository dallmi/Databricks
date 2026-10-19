# iMEP Core Schemas, Tracking ID Location and the First HR Identifier Search

> Source: Genie questions Q1, Q2 and Q3 (2026-04-16). Status: confirmed for Q1 and Q2; the Q3 conclusion was superseded by Q3b (2026-04-17).

## Question

The first three Genie questions closed the most basic open points (OP) of the Business Requirements Document (BRD):

- **Q1 (OP-04)** — In which table and column does the campaign tracking id live in iMEP? The BRD assumed a column called `CammsTrackingID`.
- **Q2 (OP-01)** — What are the full schemas of the four core iMEP Bronze tables `tbl_email`, `tbl_email_receiver_status`, `tbl_analytics_link` and `tbl_email_links`?
- **Q3 (OP-07d)** — Is there a human resources (HR) table that holds both the T-number (iMEP's person key) and the GPN (Global Personnel Number, 8 digits, used in the intranet telemetry)?

## Findings

### Q1 — The tracking id has a different name in every system

There is no column called `CammsTrackingID`. The same identifier appears under different names:

| System | Table | Column | Level |
|---|---|---|---|
| iMEP Bronze | `imep_bronze.tbl_email` | `TrackingId` | Mailing |
| iMEP Bronze | `imep_bronze.tbl_event` | `TrackingId` | Event |
| iMEP Gold | `imep_gold.tbl_pbi_platform_mailings` | `TrackingId` | Mailing, channel suffix `EMI` |
| iMEP Gold | `imep_gold.tbl_pbi_platform_events` | `TrackingId` | Event, channel suffix `EVT` |
| SharePoint Bronze | `sharepoint_bronze.pages` | `__ORG__GICTrackingID` | Page |
| SharePoint Bronze | `sharepoint_bronze.pageviews*` | `GICTrackingID` | Page view |
| SharePoint Silver | `sharepoint_silver.webpage` | `gicTrackingID` (lower-case variant; Q27 reported `gICTrackingID`) | Page |
| Campaign planning (CPLAN) | `sharepoint_cplan.internalcommunicationactivities_bronze` | `tracking_id` | Activity |
| Campaign planning (CPLAN) | `sharepoint_cplan.externalcommunicationactivities_bronze` | `tracking_id` | Activity, sample suffix `EVC` |

All variants carry the same 5-segment format `CLUSTER-PACK-YYMMDD-ACTIVITY-CHANNEL`, for example `ABCDE-0000123-240709-0000045-EMI` (synthetic). Format details: [q23-tracking-id-format.md](q23-tracking-id-format.md).

On the iMEP side the tracking id lives at **mailing level** on `tbl_email` — not per template link (`tbl_email_links`) and not per open/click event (`tbl_analytics_link`). Engagement rows reach it only through the mailing:

```
tbl_analytics_link.EmailId → tbl_email.Id → tbl_email.TrackingId
```

Q1 also surfaced the iMEP Gold master tables `tbl_pbi_platform_mailings` and `tbl_pbi_platform_events`, which led to Q1b ([q01b-imep-gold-layer.md](q01b-imep-gold-layer.md)).

Caveat (hypothesis): several sample values from iMEP Gold and CPLAN start with an all-placeholder cluster and pack (`CCCCC-0000000-…`). If that is a default for activities without an assigned pack, a pack-level match on segments 1–2 would merge unrelated activities under one pack. The share of such values has not been measured.

### Q2 — Core Bronze schemas

The four tables have 111 columns in total, all nullable. `OriginalId` and `CorrelationId` are consistently empty in every table. Column-level cards exist for each table (see Related); the points below are what Q2 added beyond them.

| Table | Columns | Role | Notes |
|---|---|---|---|
| `tbl_email` | 54 | Mailing master, one row per mailing | `CreatedBy` holds a T-number. Per-language content in `TemplateValuesDe/En/Fr/It` (JSON) and `SubjectDe/En/Fr`. Also `ShippingDate`, `TimeZone`, `EmailSendingStatus`, `EventId`, `QueryId`, `DistributionOrSharedList`, `TestsCount` |
| `tbl_email_receiver_status` | — | Recipient-centric delivery status, one row per mailing × recipient | `Receiver` (e-mail address), `FirstName`, `LastName`, `TNumber`, `Status` (integer), `LogStatus` (for example `Open`), `EmailLanguage`, `ExceptionJson`, test flags `IsTest`, `IsPerformance`, `IsStandard` |
| `tbl_analytics_link` | 22 (as reported) | Engagement fact: opens and clicks | `CreatedBy`/`ModifiedBy` are always `SYSTEM`. `Agent` (`desktop`/`mobile`), `LinkTypeEnum` (`OPEN`, …), `TNumber`, `EmailAddress`, `EmailReceiverStatusId`, `CurrentLanguage`, `OriginalLanguage`, `SourceComponent(Name)`, `SourceElement(Name)` |
| `tbl_email_links` | 10 | Template link definition, the leanest table | `ElementValueId`, `Url`, `LinkLabel` |

The column names Genie listed for `tbl_analytics_link` outnumber the reported count of 22; a `DESCRIBE` should settle the exact list.

`tbl_email_receiver_status` and `tbl_analytics_link` carry personal data in clear text (e-mail address, first and last name). These columns must not flow into analytical outputs.

### Q3 — First HR identifier search (superseded)

Genie inspected `tbl_hr_employees` (114 columns) and `tbl_hr_user` (22 columns) and reported:

| Table | Column | Type | Format | Nullability |
|---|---|---|---|---|
| `tbl_hr_employees` | `T_NUMBER` | string | lower-case T-number, `t100200` | NOT NULL |
| `tbl_hr_employees` | `__ORG___SOC_CONTRACT_ID` | string | contract reference | often NULL |
| `tbl_hr_employees` | `EXT_STAFF_VET_CAT` | string | external-staff category code | often NULL |
| `tbl_hr_user` | `__ORG__Id` | string | upper-case T-number, `T100200` | NOT NULL |

Further identifier columns: `WORKER_ID`, `ABACUS_ID`, `ALTERNATE_WORKER_ID`, `WEBSSO`, `UUNAME` (employees table); `PersonalNumber` and `Id` (a GUID) (user table).

The first answer was: no HR table has a GPN column, and no table holds T-number and GPN side by side. Within HR, `T_NUMBER` and `__ORG__Id` are the same T-number in different case, so any join between them needs `LOWER()` or `UPPER()`.

At the time we considered whether the GPN might simply be a rewritten T-number (`t` replaced by `0`, zero-padded to 8 digits).

**Superseded by Q3b (2026-04-17):** the GPN is `WORKER_ID` in `imep_bronze.tbl_hr_employee`, the same table that carries `T_NUMBER`. Q3 had listed `WORKER_ID` but not recognised it as the GPN — the search was too narrow. The bridge is a plain join; the string-rewrite idea is obsolete and must not be used. Details: [hr-employee-identifiers.md](hr-employee-identifiers.md).

The Q3 output names the table `tbl_hr_employees` (plural); every later answer uses `tbl_hr_employee`. We treat them as the same table.

## Consequences

1. **Email-side tracking source:** `tbl_email.TrackingId` at mailing level, carried to engagement rows via `EmailId`. Never look for it on `tbl_email_receiver_status` or `tbl_analytics_link`.
2. **One name in the harmonisation layer:** `TrackingId`, `__ORG__GICTrackingID`, `GICTrackingID`, `gicTrackingID` and `tracking_id` all map to a single `tracking_id` column.
3. **Case-normalise T-numbers in every HR join** (`T_NUMBER` lower case, `__ORG__Id` upper case).
4. **Person bridge:** T-number ↔ GPN through `tbl_hr_employee.T_NUMBER` / `WORKER_ID`, see [../joins/hr_enrichment.md](../joins/hr_enrichment.md).
5. **Personal data:** exclude `Receiver`, `EmailAddress`, `FirstName` and `LastName` from every output.
6. The plan at the time — build `silver.fact_email` from these Bronze tables — was dropped after Q16 and Q26; see [q16-imep-gold-inventory.md](q16-imep-gold-inventory.md) and [q26-imep-silver.md](q26-imep-silver.md).

## Related

- [q01b-imep-gold-layer.md](q01b-imep-gold-layer.md) · [q23-tracking-id-format.md](q23-tracking-id-format.md) · [camms-tracking-id.md](camms-tracking-id.md) · [hr-employee-identifiers.md](hr-employee-identifiers.md) · [imep-data-model.md](imep-data-model.md)
- [../tables/imep/tbl_email.md](../tables/imep/tbl_email.md) · [../tables/imep/tbl_email_receiver_status.md](../tables/imep/tbl_email_receiver_status.md) · [../tables/imep/tbl_analytics_link.md](../tables/imep/tbl_analytics_link.md) · [../tables/imep/tbl_email_links.md](../tables/imep/tbl_email_links.md) · [../tables/imep/tbl_event.md](../tables/imep/tbl_event.md)
- [../tables/hr/tbl_hr_employee.md](../tables/hr/tbl_hr_employee.md) · [../tables/hr/tbl_hr_user.md](../tables/hr/tbl_hr_user.md)
- [../joins/imep_bronze_email_events.md](../joins/imep_bronze_email_events.md) · [../joins/cross_channel_via_tracking_id.md](../joins/cross_channel_via_tracking_id.md)
- [../genie_questions_imep.md](../genie_questions_imep.md) — Q1, Q2, Q3 · [../sources.md](../sources.md#q1)
