# `imep_bronze.tbl_email`

> Master table for iMEP mailings. Only place in Bronze where `TrackingId` is attached directly to the mailing — everything else (Sends, Opens, Clicks) links back to this row via `EmailId = Id`.

| | |
|---|---|
| **Layer** | Bronze |
| **Source system** | iMEP (SQL Server) → Change Data Capture (CDC) → Delta Bronze |
| **Grain** | 1 row per Mailing (mailing definition, not per recipient) |
| **Primary key** | `Id` |
| **Cross-channel key** | `TrackingId` (32-char, 5 segments) |
| **Write pattern** | MERGE full-table upsert on Id (Service Principal) |
| **Approx row count** | ~145K (as of 2026-04-20, timespan Nov 2020 – Apr 2026) |

---

## Neighborhood — direct joins with keys

```mermaid
erDiagram
    tbl_email ||--o{ tbl_email_receiver_status : "Id = EmailId"
    tbl_email ||--o{ tbl_analytics_link        : "Id = EmailId"
    tbl_email ||--o{ tbl_email_links           : "Id = EmailId (template)"
    tbl_email ||--|| tbl_pbi_platform_mailings : "Id = Id (gold 1:1)"
    tbl_email }o--|| tbl_hr_employee           : "CreatedBy = T_NUMBER"

    tbl_email {
        string Id PK
        string TrackingId "32-char cross-channel key"
        string Title
        string Subject
        string CreatedBy FK
        timestamp CreationDate "template creation, NOT send time"
    }
    tbl_email_receiver_status {
        string Id PK
        string EmailId FK
        string TNumber "recipient"
        timestamp DateTime "actual send time"
        string LogStatus
    }
    tbl_analytics_link {
        string Id PK
        string EmailId FK
        string EmailReceiverStatusId FK
        string TNumber
        string LinkTypeEnum "OPEN / CLICK"
        timestamp CreationDate "event time"
    }
    tbl_email_links {
        string Id PK
        string ElementValueId
        string Url
    }
    tbl_pbi_platform_mailings {
        string Id PK
        string TrackingId
        int ContentMetrics "pre-aggregated"
    }
    tbl_hr_employee {
        string T_NUMBER PK
        string WORKER_ID "= GPN"
    }
```

---

## Key Columns

| Column | Type | Role | Notes |
|---|---|---|---|
| `Id` | string | **PK** | GUID, join target for all iMEP children |
| `TrackingId` | string | **Cross-channel join** | `CLUSTER-PACK-YYMMDD-ACTIVITY-CHANNEL`. UPPER & clean. Exactly 32 characters when populated, otherwise NULL for untracked mailings. |
| `Title` | string | Description | Internal name, not the subject |
| `Subject` | string | Description | Email subject line (visible to recipient) |
| `CreatedBy` | string | FK → `tbl_hr_employee.T_NUMBER` | TNumber lowercase (`t100200`), **not** GPN |
| `CreationDate` | timestamp | Temporal | Creation time of the mailing template — **not** send time (that lives in `tbl_email_receiver_status.DateTime`) |

Full column list: `DESCRIBE imep_bronze.tbl_email` in Databricks.

---

## Sample row

```
Id            = "0a3f6c2e-..."
TrackingId    = "QRREP-0000058-240709-0000060-EMI"
Title         = "Q2 Investor Update — DE"
Subject       = "Your quarterly report for Q2 2024"
CreatedBy     = "t100200"
CreationDate  = 2024-07-08 14:22:31
```

---

## Primary joins

### → `tbl_email_receiver_status` (1:N) — Sends / Bounces
One mailing row, many recipients. This is where **who received it** and **when it was sent** live.

```sql
SELECT e.TrackingId, rs.TNumber, rs.DateTime, rs.LogStatus
FROM   imep_bronze.tbl_email e
JOIN   imep_bronze.tbl_email_receiver_status rs ON rs.EmailId = e.Id
WHERE  e.TrackingId IS NOT NULL
```

### → `tbl_analytics_link` (1:N) — Opens / Clicks
Interaction events (OPEN, CLICK). Each row = one event from a recipient.

```sql
SELECT e.TrackingId, al.TNumber, al.LinkTypeEnum, al.CreationDate AS event_time, al.Agent
FROM   imep_bronze.tbl_email e
JOIN   imep_bronze.tbl_analytics_link al ON al.EmailId = e.Id
WHERE  al.IsActive = 1
```

### → `tbl_email_links` (1:N) — Template URL inventory
Static URLs in the template. Not for funnel metrics — use `tbl_analytics_link` for that.

### → `imep_gold.tbl_pbi_platform_mailings` (1:1) — Pre-aggregated master
Same `Id`, but enriched with content metrics. **We consume Gold**, not Bronze (see lineage below).

---

## Quality caveats

- **TrackingId NULL rate**: Not every mailing is tracked. For cross-channel attribution, apply `WHERE TrackingId IS NOT NULL`. Only 986/73,930 mailings (1.3%) have a TrackingId, but strong uptrend (2024: 99 → 2025: 637 → 2026 YTD: 250).
- **TrackingId format**: Always 32 characters, 5 segments separated by `-`, UPPER. Structure: `CLUSTER-PACK-YYMMDD-ACTIVITY-CHANNEL`. SEG5 = channel (Email/Newsletter/Article/…). For cross-channel match with SharePoint compare only **SEG1-2 (Pack)** — email and intranet page of the same pack carry different dates/activities/channels. See [joins/cross_channel_via_tracking_id.md](../../joins/cross_channel_via_tracking_id.md).
- **Cross-channel path**: TrackingId is a **dimension**, not a fact key — it **never** co-exists with EmailId in the same table. Cross-channel therefore runs `tbl_email.TrackingId ↔ sharepoint_bronze.pages.UBSGICTrackingID`, **not** via engagement rows (`pageviews` / `tbl_analytics_link`).
- **`CreationDate` ≠ send time**: For "when did it go out" join `tbl_email_receiver_status.DateTime`.
- **`CreatedBy` format**: lowercase `t######`. Joins against `tbl_hr_user.UbsId` require `LOWER(UbsId)`.

---

## Lineage — Bronze → Gold (Silver is skipped!)

> **Confirmed 2026-04-20**: Email engagement **skips the Silver layer entirely**. iMEP deliberately chose not to build a dedicated `silver.fact_email` layer. `imep_silver` exists — but only for **Events** (`invitation`, `eventregistration`, `event`), not for Email.

```
imep_bronze.tbl_email
imep_bronze.tbl_email_receiver_status   ───►   imep_gold.final  (~520M rows)
imep_bronze.tbl_analytics_link                    │
                                                  ├─ denormalized join over Bronze
                                                  ├─ HR enrichment applied late
                                                  └─ extreme width & size by design

imep_bronze.tbl_email  ───►  imep_gold.tbl_pbi_platform_mailings  [Master, 1:1 to Bronze.Id]
                                       │
                                       └─► imep_gold.<tier-3 engagement>
                                              - Join via lowercase `mailingid`
                                              - UniqueOpens / UniqueClicks per Mailing × Region
```

**Consumption strategy for cross-channel MVP**:
- **Email events**: consume from `imep_gold.final` (it *is* the email fact), do not re-join Bronze.
- **Mailing master**: `imep_gold.tbl_pbi_platform_mailings` for Title/Subject/TrackingId per mailing.
- **Engagement KPIs**: Tier-3 tables (`UniqueOpens`/`UniqueClicks` per Mailing × Region/Division).

**Open**:
- Exact table name `imep_gold.final` + column grain (per-recipient-event?)
- Write pattern via `DESCRIBE HISTORY`
- Whether `imep_gold.final` carries all Bronze columns 1:1 or only a KPI subset

See [memory/imep_silver_q26_findings.md](../../../../.claude/projects/-Users-micha-Documents-Arbeit-Databricks/memory/imep_silver_q26_findings.md) for full findings.

---

## References

- ER diagram: Section 2 in [../../architecture_diagram.md](../../architecture_diagram.md)
- Canonical join chain: [../../joins/imep_bronze_join.md](../../joins/imep_bronze_join.md) *(tbd)*
- Cross-channel TrackingId match: [../../joins/cross_channel_trackingid.md](../../joins/cross_channel_trackingid.md) *(tbd)*
- Genie findings: `memory/imep_genie_findings_q1_q2_q3.md`

---

## Sources

Genie sessions backing the statements on this page: [Q1b](../../sources.md#q1b), [Q2](../../sources.md#q2), [Q24](../../sources.md#q24), [Q26](../../sources.md#q26), [Q27](../../sources.md#q27), [Q28](../../sources.md#q28), [Q29](../../sources.md#q29), [Q30](../../sources.md#q30). See [sources.md](../../sources.md) for the full index.
