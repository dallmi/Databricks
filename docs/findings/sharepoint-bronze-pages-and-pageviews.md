# SharePoint Bronze: `pages` (Inventory) and `pageviews` (Interactions)

> Source: domain statement recorded 2026-04-16, which framed Genie questions Q15 and Q15b; later confirmed by Q22 (2026-04-20) and Q25 (2026-04-20). Status: partly validated.

## Question

Which SharePoint bronze tables carry the page inventory and the interactions, where does the tracking id sit, and what does that mean for the cross-channel model?

## Findings

### Two core tables, mirroring iMEP (the internal mailing and event platform)

| iMEP bronze | SharePoint bronze | Role |
|---|---|---|
| `tbl_email` (master) | `sharepoint_bronze.pages` | **Inventory / metadata** |
| `tbl_analytics_link` (engagement) | `sharepoint_bronze.pageviews` | **Interactions** (views, visits, engagement) |

Both sides follow the same pattern: a master table plus an interaction fact.

### `sharepoint_bronze.pages` — inventory

The master table for SharePoint pages and their sites: page tags, site assignment, article-specific fields.

- `__ORG__GICTrackingID` is **filled only for news and event pages ("articles")**. Ordinary pages — landing pages, static content, internal apps — have no tracking id. Confirmed by [Q22](q22-sharepoint-gold-schemas.md): only a small minority of pages carry it.
- `__ORG__ArticleDate` is the temporal anchor for coverage analysis. [Q25](q25-sharepoint-pages-coverage.md) used it to find the rollout in September 2024.

### `sharepoint_bronze.pageviews` — interactions

Page-view telemetry: views, visits, engagement events. The tracking-id column here is called **`GICTrackingID`**, without the organisation prefix (`__ORG__`) — a naming inconsistency with `pages`.

Several historical variants exist: `pageviews_1_july_till_17_oct`, `pageviews_historic`, `pageviews_history`, `pageviews_test` — most likely ETL (extract, transform, load) snapshots or migration artefacts. The live source is `pageviews` without a suffix. [Q30](q30-storage-architecture.md) confirms these are ad-hoc snapshot folders.

### The cross-channel funnel is limited to articles

Email or event → intranet view can only be attributed through the tracking id for **article pages**. For non-article pages (for example general research visits) attribution stays empty. That is by design, not a bug: the tracking id exists for campaign attribution of news and events.

## Consequences

1. **The dashboard must say so honestly:**
   - "Attributed article views per pack" — the funnel metric.
   - "Total page views" — context only, without a pack split.
   - A clear note that the cross-channel funnel covers articles only.
2. **Proposed silver mapping** (if a SharePoint silver layer of our own is built):

   | Silver table | Source |
   |---|---|
   | `silver.dim_page` | `sharepoint_bronze.pages` (inventory + `__ORG__GICTrackingID` + `__ORG__ArticleDate`), with the flag `is_article = (__ORG__GICTrackingID IS NOT NULL)` |
   | `silver.fact_page_view` | `sharepoint_bronze.pageviews` (interactions + `GICTrackingID`), joined to `dim_page` by page id |

3. **Harmonise the column names in silver:** `__ORG__GICTrackingID` (from `pages`) and `GICTrackingID` (from `pageviews`) both become `tracking_id`.
4. **The pack join filters by itself:** `JOIN ON tracking_pack_id` covers articles only; non-articles drop out through the inner/left-join pattern.
5. **Coverage analysis per month of `__ORG__ArticleDate`** — total articles, articles with a tracking id, trend — defines the realistic value of the cross-channel model. Run as Q25.

## Related

- [q22-sharepoint-gold-schemas.md](q22-sharepoint-gold-schemas.md) · [q25-sharepoint-pages-coverage.md](q25-sharepoint-pages-coverage.md) · [q30-storage-architecture.md](q30-storage-architecture.md)
- [q17-sharepoint-gold-inventory.md](q17-sharepoint-gold-inventory.md) — sizes of the bronze tables
- [cross-channel-mvp-scope.md](cross-channel-mvp-scope.md) · [imep-data-model.md](imep-data-model.md)
- [../tables/sharepoint/pages.md](../tables/sharepoint/pages.md) · [../tables/sharepoint/customevents.md](../tables/sharepoint/customevents.md)
- [../diagrams/er_sharepoint_bronze.md](../diagrams/er_sharepoint_bronze.md)
- [../genie_questions_imep.md](../genie_questions_imep.md) — Q15, Q15b
