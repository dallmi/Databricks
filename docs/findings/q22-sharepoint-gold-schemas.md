# SharePoint Gold Metric Schemas and the Foreign-Key Chain to Pages

> Source: Genie question Q22 (2026-04-20). Status: confirmed.

## Question

For the four SharePoint gold metric tables: what are the columns and types, which column identifies the page and joins back to `sharepoint_bronze.pages`, and does any of them carry the tracking id directly (so the join through `pages` could be skipped)?

## Findings

### 1. Schemas of the gold facts

The full column card of the main fact is in [pbi_db_interactions_metrics.md](../tables/sharepoint_gold/pbi_db_interactions_metrics.md). Summary of all four:

| Table | Columns | Volume | Grain | Use |
|---|---|---|---|---|
| `pbi_db_interactions_metrics` | 11 | tens of millions | page × date × contact × referring application | Most granular: views, visits, duration, comments. Heaviest table. |
| `pbi_db_pageviewed_metric` | 5 | tens of millions | same as above | Fast view counts: `viewingcontactid`, `visitdatekey`, `referenceapplicationid`, `marketingPageId`, `views` |
| `pbi_db_pagevisited_metric` | 9 | tens of millions | page × date × visit | Visit-oriented, with deduplication: `marketingPageId`, `visits`, `durationsum`, `durationavg`, `visitedPageId`, … |
| `pbi_db_datewise_overview_fact_tbl` | 31 | millions | **page × date × division** | Pre-aggregated: `marketingPageId`, `websitename`, `visitdatekey`, `employeebusinessdivision`, rolling windows |

Schema details worth knowing:

- `visitdatekey` is a **string** in `YYYYMMDD` format.
- `marketingPageId` is a **string GUID** (globally unique identifier).
- `viewingcontactid` is a string GUID — a contact key, **not** a T-number or GPN (Global Personnel Number).
- `commentss` (double `s`) is the real column name.
- `marketingPageIdGuid` is typed `bigint` despite its name and was NULL in the sample — looks unused.
- `flag` is a string column with no documented meaning.
- `pbi_db_datewise_overview_fact_tbl` carries per-page-per-division-per-date columns `page_div_date_views`, `_visits`, `_comments`, `_likes`, `_uniquevisitors`, plus 7/14/21/28-day pre-aggregates. It is built to avoid scanning the large facts and suits daily, rolling and comparative dashboards.

### 2. One foreign-key chain for all gold metrics

```
sharepoint_gold.pbi_db_*.marketingPageId           (string GUID)
        ↓  = pageUUID
sharepoint_bronze.pages.pageUUID                   (string GUID)
        ↓  same row
sharepoint_bronze.pages.__ORG__GICTrackingID       (sparsely populated)
        ↓  pack-level match on segments 1–2, see Q23
imep_gold.tbl_pbi_platform_mailings.TrackingId     (cross-system link)
```

Common Data Model (CDM) metadata confirms the mapping: `marketingPageId` has `sourceName: mailync_marketingpageid`, `dataFormat: Guid`. The join works cleanly at row level.

### 3. No tracking id on any gold metric

None of the gold fact tables contains `GICTrackingID` or `__ORG__GICTrackingID`. The join through `pages` is mandatory; there is no shortcut.

### 4. Coverage blocker

`__ORG__GICTrackingID` is populated on only **a small minority of pages (low single-digit percent)**. Consequently only a small share of the interaction rows can be attributed to a pack at all; the large majority of page views are unattributable from a tracking perspective. This confirms the domain statement from Q15: only article pages (news and events) carry the tracking id. [Q25](q25-sharepoint-pages-coverage.md) breaks the coverage down by time and site.

## Consequences

1. **Join path:** `metrics.marketingPageId = pages.pageUUID` — not `page_id` or `id`.
2. **Take the tracking id from `pages.__ORG__GICTrackingID`**, with a `LEFT JOIN` when the untracked majority must stay visible.
3. **Pack-level aggregates:** `INNER JOIN pages` with `__ORG__GICTrackingID IS NOT NULL`. The pack-level `FULL OUTER JOIN` against the email side stays, because many packs exist only on the email side.
4. **Dashboard transparency:** either show only the tracked subset and label it "article pages only", or show a separate "non-article intranet activity" section without pack attribution.
5. **Viewer identity:** `viewingcontactid` must be resolved separately — through `sharepoint_gold.pbi_db_employeecontact` (candidate bridge from [Q17](q17-sharepoint-gold-inventory.md)) or through the CDM master. Not validated yet.

## Related

- [../joins/sharepoint_gold_to_pages.md](../joins/sharepoint_gold_to_pages.md) — join recipe built on this finding
- [../joins/cross_channel_via_tracking_id.md](../joins/cross_channel_via_tracking_id.md) — the pack-level cross-channel funnel
- [../tables/sharepoint/pages.md](../tables/sharepoint/pages.md) · [../tables/sharepoint_gold/pbi_db_interactions_metrics.md](../tables/sharepoint_gold/pbi_db_interactions_metrics.md)
- [../diagrams/er_sharepoint_gold.md](../diagrams/er_sharepoint_gold.md)
- [q17-sharepoint-gold-inventory.md](q17-sharepoint-gold-inventory.md) · [q23-tracking-id-format.md](q23-tracking-id-format.md) · [q25-sharepoint-pages-coverage.md](q25-sharepoint-pages-coverage.md)
- [sharepoint-bronze-pages-and-pageviews.md](sharepoint-bronze-pages-and-pageviews.md) · [hr-employee-identifiers.md](hr-employee-identifiers.md)
- [../genie_questions_imep.md](../genie_questions_imep.md) — Q22 · [../sources.md](../sources.md#q22)
