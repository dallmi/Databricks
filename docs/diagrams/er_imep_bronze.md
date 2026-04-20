# ER Diagram — `imep_bronze.*`

> Extended version of Section 2 of [architecture_diagram.md](../architecture_diagram.md). Shows the full Bronze topology for iMEP with every join key, row count, and cross-link to HR.

---

## Full Bronze topology

```mermaid
erDiagram
    tbl_email                 ||--o{ tbl_email_receiver_status : "Id = EmailId"
    tbl_email                 ||--o{ tbl_analytics_link        : "Id = EmailId"
    tbl_email                 ||--o{ tbl_email_links           : "Id = EmailId (template)"
    tbl_email                 ||--o{ tbl_email_components      : "Id = EmailId"
    tbl_email                 ||--o{ tbl_email_template_images : "Id = EmailId"
    tbl_email_receiver_status ||--o{ tbl_analytics_link        : "Id = EmailReceiverStatusId"
    tbl_email_receiver_status }o--|| tbl_hr_employee           : "TNumber = T_NUMBER"
    tbl_analytics_link        }o--|| tbl_hr_employee           : "TNumber = T_NUMBER"
    tbl_hr_employee           }o--|| tbl_hr_costcenter         : "ORGANIZATIONAL_UNIT"
    tbl_hr_employee           }o--|| tbl_hr_user               : "LOWER(T_NUMBER) = LOWER(UbsId)"
    tbl_event                 ||--o{ tbl_email_receiver_status : "? (to verify)"

    tbl_email {
        string Id PK "145K rows"
        string TrackingId "32-char, 1.3% populated"
        string Title
        string Subject
        string CreatedBy FK
        timestamp CreationDate "template created"
    }
    tbl_email_receiver_status {
        string Id PK "293M rows — HUB #1"
        string EmailId FK
        string TNumber FK
        string Receiver
        int Status
        string LogStatus "Sent/Open/Bounce"
        string EmailLanguage
        timestamp DateTime "actual send time"
    }
    tbl_analytics_link {
        string Id PK "533M rows — HUB #2"
        string EmailId FK
        string EmailReceiverStatusId FK
        string TNumber FK
        string Agent
        string LinkTypeEnum "OPEN / CLICK"
        string CurrentLanguage
        int IsActive "filter = 1"
        timestamp CreationDate "event time"
    }
    tbl_email_links {
        string Id PK
        string EmailId FK "(template)"
        string ElementValueId
        string Url
        string LinkLabel
    }
    tbl_email_components {
        string Id PK "3.3M rows"
        string EmailId FK
    }
    tbl_email_template_images {
        string Id PK "1.7M rows"
        string EmailId FK
    }
    tbl_event {
        string Id PK "100K rows"
        string TrackingId
        string Title
        timestamp EventDate
    }
    tbl_hr_employee {
        string T_NUMBER PK "265K rows, lowercase"
        string WORKER_ID "= GPN, 8-digit"
        string ALTERNATE_WORKER_ID
        string ORGANIZATIONAL_UNIT FK
        string ABACUS_ID
        string WEBSSO
    }
    tbl_hr_costcenter {
        string ORGANIZATIONAL_UNIT PK
        string DIVISION
        string AREA
        string REGION
        string COUNTRY
    }
    tbl_hr_user {
        string UbsId "T-number UPPERCASE"
        string Town
    }
```

---

## Volumes & Write Patterns

| Table | Rows | Pattern |
|---|---|---|
| `tbl_email_receiver_status` | **293M** | MERGE full-table upsert (27-72M/run) |
| `tbl_analytics_link` | **533M** | MERGE **incremental** (3.7-8.5K/run) |
| `tbl_email` | 145K | MERGE full-table upsert |
| `tbl_hr_employee` | 265K | MERGE full-table upsert |
| `tbl_email_components` | 3.3M | MERGE |
| `tbl_email_template_images` | 1.7M | MERGE |
| `tbl_event` | 100K | MERGE |

---

## Three join types in this domain

### 1. Mailing-centric (starts from `tbl_email`)

For "what happened to mailing X?". See [imep_bronze_email_events.md](../joins/imep_bronze_email_events.md).

### 2. Person-centric (starts from `tbl_hr_employee`)

For "which engagement events did person X have?". Only possible via the two hub tables `tbl_email_receiver_status` and `tbl_analytics_link` — **the SharePoint side has no equivalent person key**.

### 3. HR enrichment (from any TNumber-bearing table)

For "enrich my engagement fact with Region/Division". See [hr_enrichment.md](../joins/hr_enrichment.md).

---

## Key observations

- **Two full-key hubs**: only `tbl_email_receiver_status` and `tbl_analytics_link` carry `Id + EmailId + TNumber` simultaneously. These are the only tables with **person-level engagement granularity**.
- **`TrackingId` scope**: in Bronze only on `tbl_email` and `tbl_event`. **Never** on engagement tables. If you need a TrackingId in a join, you have to bring it in via `tbl_email.Id = EmailId`.
- **`tbl_email_links` vs. `tbl_analytics_link`**: do not confuse them. `tbl_email_links` is the template URL inventory (static); `tbl_analytics_link` is the event fact table (Open/Click).
- **HR link order**: engagement table → `tbl_hr_employee` → `tbl_hr_costcenter` (via `ORGANIZATIONAL_UNIT`). `tbl_hr_user` offers additional fields (Town); not required for Region/Division.

---

## References

- [Section 2 in architecture_diagram.md](../architecture_diagram.md) — original version
- [Join Strategy Contract](../joins/join_strategy_contract.md)
- Card per table: [tbl_email](../tables/imep/tbl_email.md), [tbl_email_receiver_status](../tables/imep/tbl_email_receiver_status.md), [tbl_analytics_link](../tables/imep/tbl_analytics_link.md)

---

## Sources

Genie sessions backing the statements on this page: [Q27](../sources.md#q27), [Q30](../sources.md#q30). See [sources.md](../sources.md) for the full index.
