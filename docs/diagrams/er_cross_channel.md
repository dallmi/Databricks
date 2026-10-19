# ER Diagram — Cross-Channel Bridge

> The **only bridge** between iMEP (Email) and SharePoint (Intranet). Runs via **TrackingId ↔ __ORG__GICTrackingID** at the dimensional level (`tbl_email` ↔ `pages`), **never** through engagement facts directly. Plus the Employee bridge via `tbl_hr_employee.WORKER_ID`.

---

## The entire cross-channel architecture in one picture

```mermaid
erDiagram
    tbl_email                 ||..|| pages                         : "TrackingId ↔ __ORG__GICTrackingID (Pack / SEG1-2)"
    tbl_email                 ||--o{ tbl_email_receiver_status     : "Id = EmailId"
    tbl_email_receiver_status ||--o{ tbl_analytics_link            : "Id = EmailReceiverStatusId"
    tbl_email_receiver_status }o--|| tbl_hr_employee               : "TNumber = T_NUMBER"
    tbl_hr_employee           }o..|| pageviews                     : "WORKER_ID = user_gpn (GPN bridge)"
    pages                     ||--o{ pbi_db_interactions_metrics   : "pageUUID = marketingPageId"
    pages                     }o--|| sites                         : "SiteId = SiteId"

    tbl_email {
        string Id PK "over a hundred thousand"
        string TrackingId "rarely populated"
        string Title
    }
    tbl_email_receiver_status {
        string Id PK "hundreds of millions"
        string EmailId FK
        string TNumber
        string LogStatus
        timestamp DateTime
    }
    tbl_analytics_link {
        string Id PK "hundreds of millions"
        string EmailId FK
        string TNumber
        string LinkTypeEnum
    }
    tbl_hr_employee {
        string T_NUMBER PK "hundreds of thousands"
        string WORKER_ID "= GPN"
        string ORGANIZATIONAL_UNIT
    }
    pages {
        string pageUUID PK "tens of thousands"
        string __ORG__GICTrackingID "rarely populated ⚠️"
        string SiteId FK
    }
    pageviews {
        string Id PK "hundreds of millions"
        string user_gpn "8-digit"
        string pageId FK
    }
    pbi_db_interactions_metrics {
        string marketingPageId FK "tens of millions"
        bigint views
        string viewingcontactid
    }
    sites {
        string SiteId PK "hundreds"
        string SiteName
    }
```

---

## The two bridges

### Bridge 1: TrackingID (campaign-level)

**Where?**: `tbl_email.TrackingId` ↔ `sharepoint_bronze.pages.__ORG__GICTrackingID`

**Grain**: **Pack (SEG1-2 = Cluster + Pack number)**. The pack bundles all activities of a campaign — email, intranet page, newsletter, etc. — regardless of their individual dates and channel codes.

**Match logic**:
```sql
-- Match on pack level (SEG1-2); ignore everything after (date/activity/channel)
ON  array_join(slice(split(UPPER(email.TrackingId),         '-'), 1, 2), '-')
  = array_join(slice(split(UPPER(pages.__ORG__GICTrackingID),  '-'), 1, 2), '-')
```

**Why not match more narrowly?** Email and intranet page of the same pack typically run on different days (SEG3), with different activity sequencing (SEG4), and on a different channel (SEG5). Only pack-level finds the activities that belong together semantically.

**Coverage reality**:
- iMEP side: a small minority of mailings (low single-digit percent) have a TrackingId
- SP side: a small minority of pages (low single-digit percent) have a TrackingID
- **Realistic pack intersection**: a few dozen packs — the dashboard universe

### Bridge 2: Employee identity (person-level)

**Where?**: `tbl_hr_employee.WORKER_ID` (iMEP) ↔ `sharepoint_bronze.pageviews.user_gpn` (SharePoint)

**Both are GPN** in `00100200` format (8-digit). `WORKER_ID` in HR, `user_gpn` in SP Bronze.

**Alternative** (not yet validated): `sharepoint_gold.pbi_db_employeecontact` carries `T_NUMBER` directly — could be a shorter path than the GPN detour.

---

## Why **no** direct engagement join?

A common misconception: "Can I join `tbl_analytics_link.TNumber` directly against something in SharePoint to see whether the same person opened the email AND read the page?"

**Answer**: not directly, because:

1. **SharePoint carries no TNumber** — only `user_gpn` or `viewingcontactid`
2. **Engagement tables carry no TrackingID** in either domain

The correct path:

```
tbl_analytics_link.TNumber
        │ JOIN tbl_hr_employee.T_NUMBER
        ▼
tbl_hr_employee.WORKER_ID  (= GPN)
        │ JOIN sharepoint_bronze.pageviews.user_gpn
        ▼
pageviews.pageId
        │ JOIN sharepoint_bronze.pages.pageUUID
        ▼
pages.__ORG__GICTrackingID  (if present)
        │ SEG1-2 (pack) compare
        ▼
tbl_email.TrackingId
```

This is a **5-hop chain**. In practice you do not run it row-by-row but aggregated at pack level (see [cross_channel_via_tracking_id.md](../joins/cross_channel_via_tracking_id.md)).

---

## The canonical cross-channel funnel

```mermaid
flowchart LR
    A["Email sent<br/>(tbl_email_receiver_status)"] --> B["Email opened<br/>(tbl_analytics_link)"]
    B --> C["Email link clicked<br/>(tbl_analytics_link)"]
    C -.TrackingId bridge.-> D["Page view<br/>(pbi_db_interactions_metrics)"]
    D --> E["Time on page<br/>(durationsum)"]
    E --> F["Event registration<br/>(imep_silver.eventregistration)"]

    style C stroke:#E60000,stroke-width:3px
    style D stroke:#E60000,stroke-width:3px

    classDef email fill:#ECEBE4,stroke:#8E8D83;
    classDef sp    fill:#F5F0E1,stroke:#B98E2C;
    classDef event fill:#F7F7F5,stroke:#7A7870;
    class A,B,C email;
    class D,E sp;
    class F event;
```

**The transition marked in red** is the only cross-channel moment — and it is **dimensional** (via TrackingID on `pages`), not factual.

---

## Typical funnel numbers

```
N mailings sent          (from the small tracked universe, from 2025)
  │
  │ open rate: a minority of sends
  ▼
  opens
  │
  │ click rate: low single-digit percent of sends
  ▼
  clicks
  │ ↓ cross-channel break (only a small minority attributable on the SP side)
  ▼
    X page views  (highly variable, depends on site coverage)
```

---

## References

- [join_strategy_contract.md](../joins/join_strategy_contract.md) — rules 1 + 5
- [cross_channel_via_tracking_id.md](../joins/cross_channel_via_tracking_id.md) — full SQL recipe
- [hr_enrichment.md](../joins/hr_enrichment.md) — GPN bridge

---

## Sources

Genie sessions backing the statements on this page: [Q21](../sources.md#q21), [Q22](../sources.md#q22), [Q24](../sources.md#q24), [Q25](../sources.md#q25), [Q27](../sources.md#q27). See [sources.md](../sources.md) for the full index.
