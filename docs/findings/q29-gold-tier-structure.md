# Gold Tier Structure for iMEP and SharePoint

> Source: Genie question Q29 (2026-04-20). Status: confirmed for the tier model and the identity of `imep_gold.final`; several per-table sizes disagree with Q16/Q21 and are treated as indicative only.

## Question

Can the grain and role of every Gold table be derived from its shape (row count, key columns, dimension columns), and do the tables fall into a tier hierarchy? Which table is the large atomic fact that Q28 mislabelled?

The scope was 52 Gold tables in 2 schemas: 31 in `imep_gold`, 20 in `sharepoint_gold` (plus one shared employee reference).

## Findings

### 1. `imep_gold.final` is the atomic fact

| Property | Value |
|---|---|
| Table | `imep_gold.final` |
| Size | hundreds of millions of rows |
| Tier | **Tier 0 — atomic fact** |
| Grain | **mailing × recipient × event × hour** — events are bucketed by hour, not stored one by one |
| Role | Lowest-grain fact in iMEP; source of every downstream aggregate |
| Coverage | Fewer distinct mailings than the mailing master has rows (it starts in mid-2021, see [q27-imep-join-graph.md](q27-imep-join-graph.md)) |

This resolves the Q28 label confusion: `tbl_pbi_platform_mailings` is a small Tier-3 dimension, not the big table.

### 2. iMEP Gold — 31 tables in four tiers

| Tier | Tables | Pattern | Examples |
|---|---|---|---|
| **Tier 0** — atomic fact | 1 | mailing × recipient × event × hour | `final` |
| **Tier 1** — time window × dimension | 15 | per mailing × dimension × time window; windows as reported `24h` / `72h` / `1w` / `15w` / `15m+`; dimensions date, date + hour, division + area, region + country | `dateonly`, `datetime`, `divarea` (millions of rows each), `regcntry` (tens to hundreds of thousands) |
| **Tier 2** — per-mailing summaries | about 8 | `UniqueOpens` / `UniqueClicks` / `Count` per mailing × dimension | `mailingreceiver` (region + country), `mailingreceiver_region`, `mailingreceiver_division`, `engagement`, `log_mail` (count by device) |
| **Tier 3** — platform and reference dimensions | about 7 | master tables and lookups | `tbl_pbi_platform_mailings`, `tbl_pbi_platform_events`, `mailingid` (name lookup), mailing access permissions, language per mailing, content tagging, monthly employee counts |

Q29 abbreviates many names. The full names from the Q16 inventory are authoritative ([q16-imep-gold-inventory.md](q16-imep-gold-inventory.md)); the Q16 → Q29 tier mapping is listed there.

Q29's sizes for the Tier-2 summaries (`mailingreceiver_*`, `engagement`) differ markedly from the direct counts in Q16 and Q21. Use the Q21 sizes; the discrepancy is open.

### 3. The `engagement` table mixes mailings and events

`engagement` contains considerably more distinct mailing ids than `tbl_pbi_platform_mailings` has rows. The excess (tens of thousands of ids) matches event ids from `tbl_pbi_platform_events`. iMEP Gold treats **emails and events as the same engagement unit** in its Tier-2 aggregates.

For email-only analyses, inner-join `engagement` to `tbl_pbi_platform_mailings`. Whether the excess consists only of events, or whether other items leak in, is open.

### 4. Tracking ids in the Gold mailing master

Only a small minority (low single-digit percent) of the mailings in `tbl_pbi_platform_mailings` carry a `TrackingId`. Q24 counted *mailings* with a tracking id in Bronze, Q29 counted *distinct tracking ids* in Gold. Several mailings can share one tracking id, so the two figures are not directly comparable; the Gold count is slightly lower. Adoption is growing, so the count is as-of 2026-04-20 ([q24-tracking-id-volume.md](q24-tracking-id-volume.md)).

### 5. Tag tables fan out

A mailing-tag table has considerably more rows than the mailing master: it stores mailing × tag pairs, not one row per mailing. Joining it to mailing-level facts multiplies rows.

### 6. SharePoint Gold — 20 tables, also four tiers

| Tier | Table | Size | Grain / content |
|---|---|---|---|
| **Tier 0** — atomic interaction facts | `pbi_db_interactions_metrics` | tens of millions | `marketingPageId` × date × application |
| | `pbi_db_pageviewed_metric` | tens of millions | same grain |
| | `pbi_db_pagevisited_metric` | tens of millions | visit-oriented |
| | `pbi_db_90_days_interactions_metric` | millions | rolling 90 days |
| **Tier 1** — pre-aggregated overviews | `datewise_overview_fact_tbl` | millions | page × date × division; 27 rolling-window statistics |
| | two further tables (unnamed in the output) | thousands / hundreds | region × division; page × date × division |
| **Tier 2** — social signals | `pageliked_metric` | hundreds of thousands | contact × page × date |
| | `pagecommented_metric` | hundreds of thousands | contact × page × date |
| | `page_like_int_fact` | tens of thousands | site × page (like clicks) |
| **Tier 3** — dimensions | `employeecontact` | tens of millions | visitor dimension; candidate T-number bridge |
| | `website_page_inventory` | tens of thousands | full page dimension (template, language, region, …) |
| | `site_page_inventory_for` | tens of thousands | simplified page lookup |
| | `website_page_view` | tens of thousands | a view |
| | `page_visitedkey_view` | tens of millions | key lookup only (index) |
| | `calendar` | tens of thousands | dates |
| | `referer_application` | a handful | referring applications |

Names as reported; in the catalog most of these tables carry the `pbi_db_` prefix. Details: [q17-sharepoint-gold-inventory.md](q17-sharepoint-gold-inventory.md), [q22-sharepoint-gold-schemas.md](q22-sharepoint-gold-schemas.md).

`employeecontact` is far larger than the employee population, so it is not one row per person. Its grain must be understood before it is used as a person bridge (the bridge hypothesis is tracked in [q17-sharepoint-gold-inventory.md](q17-sharepoint-gold-inventory.md)).

**Video sub-domain** (3 tables, the only ones built by a managed declarative pipeline, see [q30-storage-architecture.md](q30-storage-architecture.md)):

| Table | Size | Grain |
|---|---|---|
| `fact_video_engagement_datewise_gold` | tens of thousands | region × division × … (date-wise) |
| `fact_video_engagement_gold` | tens of thousands | asset × region × division |
| `tbl_video_inventory_gold` (Q30 lists it as `pbi_db_dim_video_inventory_gold`) | thousands | asset |

**Shared employee reference:** `tbl_emp_by_month_txt_xx` — hundreds of rows, monthly employee counts.

### 7. The only bridge between the two Gold schemas

There is no shared foreign key between iMEP Gold and SharePoint Gold. The single bridge is dimensional:

```
imep_gold.tbl_pbi_platform_mailings.TrackingId
  → sharepoint_bronze.pages.__ORG__GICTrackingID
  → sharepoint_bronze.pages.pageUUID
  → sharepoint_gold.pbi_db_*.marketingPageId
```

Q29 proposed matching after dropping the channel suffix (segment 5). The business refinement of Q23 on the same day narrowed this to the **pack (segments 1–2)**; see [q23-tracking-id-format.md](q23-tracking-id-format.md) and [../joins/cross_channel_via_tracking_id.md](../joins/cross_channel_via_tracking_id.md).

### 8. One-line mental model

- **iMEP Gold** is message-centric: per-recipient engagement with heavy time-window aggregation.
- **SharePoint Gold** is page-centric: interaction analytics with pre-rolled KPIs.
- **The tracking id is the only conceptual bridge**, and it lives on dimensions, not facts.

## Consequences

1. `imep_gold.final`: name confirmed, grain is `mailing × recipient × event × hour`. An hour-bucket column must therefore exist; the exact column list is still to be read with `DESCRIBE`.
2. The iMEP Gold entity-relationship diagram shows the four tiers with explicit labels; SharePoint Gold adds the video sub-domain as its own group ([../diagrams/er_imep_gold.md](../diagrams/er_imep_gold.md), [../diagrams/er_sharepoint_gold.md](../diagrams/er_sharepoint_gold.md)).
3. **Dashboard caveat:** `engagement` is a mail + event hybrid — filter to mailing ids for email KPIs.
4. **Fan-out caveat:** tag tables are mailing × tag.
5. **Cross-system joins go through `pages`**, never fact to fact.

## Related

- [q16-imep-gold-inventory.md](q16-imep-gold-inventory.md) · [q21-imep-gold-tier3-schemas.md](q21-imep-gold-tier3-schemas.md) · [q28-pipeline-operations.md](q28-pipeline-operations.md) · [q30-storage-architecture.md](q30-storage-architecture.md) · [q17-sharepoint-gold-inventory.md](q17-sharepoint-gold-inventory.md) · [q22-sharepoint-gold-schemas.md](q22-sharepoint-gold-schemas.md) · [q24-tracking-id-volume.md](q24-tracking-id-volume.md)
- [../tables/imep_gold/final.md](../tables/imep_gold/final.md) · [../tables/sharepoint_gold/pbi_db_interactions_metrics.md](../tables/sharepoint_gold/pbi_db_interactions_metrics.md) · [../joins/sharepoint_gold_to_pages.md](../joins/sharepoint_gold_to_pages.md)
- [../genie_questions_imep.md](../genie_questions_imep.md) — Q29 · [../sources.md](../sources.md#q29)
