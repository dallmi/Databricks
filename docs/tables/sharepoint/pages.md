# `sharepoint_bronze.pages`

> **The cross-channel bridge.** Page inventory with `UBSGICTrackingID` — **the only place** where SharePoint-side interactions can be attributed to a Pack. 48K rows, but only **1,949 (~4%) have `UBSGICTrackingID` populated**. This is the most critical coverage blocker in the entire cross-channel model.

| | |
|---|---|
| **Layer** | Bronze |
| **Source system** | SharePoint -> Change Data Capture (CDC) or Snapshot -> Delta Bronze |
| **Grain** | 1 row per SharePoint page (snapshot) |
| **Primary key** | `pageUUID` (GUID) |
| **Cross-channel key** | `UBSGICTrackingID` — **only 4% populated** ⚠️ |
| **Write pattern** | MERGE Daily Snapshot Replace (Service Principal) |
| **Approx row count** | **~48K** (as of today, timespan 1900 – Apr 2026) |
| **Coverage** | 1,949 / 48,419 pages (~4%) have TrackingID -> **only article pages (News/Events)** |

---

## Neighborhood — dimension table for cross-channel

```mermaid
erDiagram
    pages ||--o{ pbi_db_interactions_metrics : "pageUUID = marketingPageId"
    pages ||--o{ pbi_db_pageviewed_metric    : "pageUUID = marketingPageId"
    pages ||--o{ pageviews                   : "pageUUID = ? (to verify)"
    pages }o--|| sites                       : "SiteId = SiteId"
    pages ||--o| tbl_email                   : "UBSGICTrackingID <-> TrackingId via SEG1-2"

    pages {
        string pageUUID PK "GUID"
        string UBSGICTrackingID "CROSS-CHANNEL KEY (only 4% populated)"
        string UBSArticleDate "publication date"
        string PageURL
        string PageTitle
        string SiteId FK
    }
    pbi_db_interactions_metrics {
        string marketingPageId FK
        bigint views
    }
    sites {
        string SiteId PK
        string SiteName
        string SiteUrl
    }
    tbl_email {
        string Id PK
        string TrackingId
    }
```

---

## Key Columns

| Column | Type | Role | Notes |
|---|---|---|---|
| `pageUUID` | string (GUID) | **PK** | Unique per page. Referenced as `marketingPageId` in all `sharepoint_gold.*` metric tables. |
| `UBSGICTrackingID` | string | **Cross-channel key** | Format: `CLUSTER-PACK-YYMMDD-ACTIVITY-CHANNEL` (32-char, 5-seg, UPPER). **Only 4% populated.** |
| `UBSArticleDate` | date | Publication date | When the article was published. Often NULL on non-article pages. |
| `PageURL` / `PageUrl` / `url` | string | URL | Column name varies — verify via `DESCRIBE`. 1:1 URL<->TID mapping. |
| `PageTitle` | string | Display | Page title |
| `SiteId` | string | FK -> `sites.SiteId` | Site assignment (99.4% of tracked pages = "News and events") |

Full list (95 columns in total) via `DESCRIBE sharepoint_bronze.pages`.

---

## Sample row

```
pageUUID          = "f94bc186-32a2-4155-aaec-42b22091cd22"
UBSGICTrackingID  = "QRREP-0000058-240709-0000060-IAN"     -- note: SEG5 = IAN (SharePoint)
UBSArticleDate    = 2024-07-09
PageURL           = "/sites/news/articles/q2-investor-update.aspx"
PageTitle         = "Q2 Investor Update 2024"
SiteId            = "..."
```

---

## Primary joins

### -> `pbi_db_interactions_metrics` (1:N) — the standard cross-channel path

```sql
SELECT p.UBSGICTrackingID, p.PageURL, p.UBSArticleDate,
       SUM(m.views)  AS total_views,
       SUM(m.visits) AS total_visits,
       COUNT(DISTINCT m.viewingcontactid) AS unique_viewers
FROM   sharepoint_bronze.pages p
JOIN   sharepoint_gold.pbi_db_interactions_metrics m ON m.marketingPageId = p.pageUUID
WHERE  p.UBSGICTrackingID IS NOT NULL            -- mandatory filter for attribution
  AND  m.visitdatekey   >= '20250101'            -- default time window from 2025
GROUP BY p.UBSGICTrackingID, p.PageURL, p.UBSArticleDate
```

### -> `sites` (N:1) — site context

```sql
SELECT p.*, s.SiteName, s.SiteUrl
FROM   sharepoint_bronze.pages p
LEFT JOIN sharepoint_bronze.sites s ON s.SiteId = p.SiteId
```

### -> iMEP cross-channel link (SEG1-2)

```sql
-- Pack-level link: tbl_email x pages via SEG1-2
WITH email_packs AS (
  SELECT DISTINCT
         array_join(slice(split(UPPER(TrackingId), '-'), 1, 2), '-') AS seg1_4,
         TrackingId AS email_tid
  FROM   imep_bronze.tbl_email
  WHERE  TrackingId IS NOT NULL
),
page_packs AS (
  SELECT DISTINCT
         array_join(slice(split(UPPER(UBSGICTrackingID), '-'), 1, 2), '-') AS seg1_4,
         UBSGICTrackingID AS page_tid
  FROM   sharepoint_bronze.pages
  WHERE  UBSGICTrackingID IS NOT NULL
)
SELECT e.email_tid, p.page_tid, e.seg1_4 AS common_activity
FROM   email_packs e
JOIN   page_packs  p ON p.seg1_4 = e.seg1_4
```

---

## Quality caveats — critical

### ⚠️ 4% coverage blocker

Only **1,949 / 48,419 pages** have `UBSGICTrackingID`. Consequence:

- Only ~4% of the 84M SharePoint interaction rows are pack-attributable
- The remaining 96% = "untracked intranet activity" (internal tools, settings, collab pages, etc.)
- **Dashboard must make this explicit** — either (a) show only the 4% subset and label it as "Article Pages only", or (b) two separate sections (attributed vs. unattributed)

### Site concentration

**99.4% of tracked pages** (1,937 out of 1,949) live on **one single site**: "News and events".

- Dashboard default **should** be restricted to this site
- For "coverage reality check": 83 Pack IDs (SEG1-2) are accessible to this dashboard

### Coverage rollout

- **Start of TID population**: September 2024 (33.9% coverage for new pages)
- **Peak so far**: March 2026 (70.5%)
- **Never reached 80%** — even "current" pages have 25-30% missing TIDs

-> Default time filter: `UBSArticleDate >= '2024-09-01'`.

### Format inconsistencies

- `UBSGICTrackingID` should always be UPPER, clean, 32-char — but when comparing to iMEP always safeguard with `UPPER(TRIM(...))`
- SEG5 (channel) diverges: iMEP carries `EMI`/`NLI`/`TWE`, SharePoint `IAN`/`ITS`/`OPN`/`ANC` — **ignore SEG5** on cross-channel matching (see rule 3 in [join_strategy_contract.md](../../joins/join_strategy_contract.md))

### 1:1 URL <-> TID

This confirms: each URL has at most one TID, each TID at most one URL. That is, **URL-based aggregation** is equivalent to TID-based — use URL as a fallback key if needed.

---

## Lineage

```
SharePoint (CMS) --[Daily Snapshot MERGE]--> sharepoint_bronze.pages
                                                                |
                                                                +--> sharepoint_silver.webpage (normalized dimension)
                                                                         |
                                                                         +--> pbi_db_* gold metric tables reference pageUUID
```

**Refresh anomaly**: As Daily Snapshot Replace — the entire table is overwritten once per day. `MERGE on (id: PageUId)`. Page deletions therefore propagate within 24h at most.

---

## Related cards

- [pbi_db_interactions_metrics](../sharepoint_gold/pbi_db_interactions_metrics.md) — main consumer
- `sites.md` *(pending)* — site dimension
- `pageviews.md` *(pending)* — interaction bronze (without TrackingID attribution)
- `webpage.md` *(pending)* — silver variant of this table

---

## References

- [join_strategy_contract.md](../../joins/join_strategy_contract.md) — coverage rules (rule 5)
- [architecture_diagram.md](../../architecture_diagram.md) — Section 4 (cross-channel join) and Section 7 (coverage disclaimer)
- Memory: `sharepoint_gold_schemas_q22.md`, `sharepoint_pages_coverage_q25.md`, `sharepoint_pages_inventory.md`

---

## Sources

Genie sessions backing the statements on this page: [Q2](../../sources.md#q2), [Q17](../../sources.md#q17), [Q22](../../sources.md#q22), [Q25](../../sources.md#q25), [Q27](../../sources.md#q27), [Q28](../../sources.md#q28), [Q30](../../sources.md#q30). See [sources.md](../../sources.md) for the full directory.
