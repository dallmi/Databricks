# iMEP Gold Inventory: Engagement Aggregates Exist

> Source: Genie question Q16 (2026-04-17). Status: confirmed; the five-tier numbering used here was replaced by the four-tier model of Q29.

## Question

Which tables exist in `imep_gold`, with size, grain and purpose — and does Gold contain send, open and click aggregates per mailing (the gap left by [Q1b](q01b-imep-gold-layer.md))?

## Findings

### Tier numbering: Q16 versus Q29

Q16 grouped the schema into five tiers. Q29 later re-classified all Gold tables into four tiers (Tier 0–3), and the entity-relationship diagram follows Q29. When a document speaks of "Tier-3 tables" in the sense of "the per-mailing KPI tables", it uses Q16 numbering.

| Q16 tier | Content | Q29 tier |
|---|---|---|
| Tier 1 | Atomic engagement fact | Tier 0 |
| Tier 2 | Rolling time-window aggregates | Tier 1 |
| Tier 3 | Per-mailing open/click KPI tables | Tier 2 |
| Tier 4 | Platform metadata and lookups | Tier 3 |
| Tier 5 | Small reference tables | Tier 3 |

### Tier 1 — atomic engagement fact

| Table | Size | Purpose |
|---|---|---|
| `final` | hundreds of millions of rows | The only event-level fact; every aggregate is derived from it. Too large for direct BI reporting. Key columns: `Id`, `MailingId`, `EventType`, `RecipientID`, `send_datetime`, `lang_origin_lang` |

Card: [../tables/imep_gold/final.md](../tables/imep_gold/final.md).

### Tier 2 — rolling time-window aggregates

All Tier-2 tables use the pattern `EngagementType` + `Count` — no separate open and click columns. They are rebuilt on each pipeline run, keep no history and carry no retention timestamps. The name suffixes appear to encode the time window after send (`24`, `5d`, `7d`, `15d`, `15dplus`, …).

| Group | Tables | Dimensions | Size per table |
|---|---|---|---|
| Time-based | `tbl_pbi_datesX4`, `tbl_pbi_date24pabs`, `tbl_pbi_dateId34`, `tbl_pbi_dateId44` | date / date and hour | hundreds of thousands to millions of rows |
| Business hierarchy | `tbl_pbi_divarea15dplus`, `tbl_pbi_divarea15d`, `tbl_pbi_divarea5d`, `tbl_pbi_divarea7d`, `tbl_pbi_divarea24` | `mailingId`, `Business_Division`, `Business_Area` | millions of rows |
| Regional | `tbl_pbi_regcntry15xplus`, `tbl_pbi_regcntry15d`, `tbl_pbi_regcntry5d`, `tbl_pbi_regcntry7d`, `tbl_pbi_regcntry24` | `mailingId`, `Region`, `Country`, `Town` | hundreds of thousands of rows |
| Device | `tbl_pbi_deviceTypeall` | `mailingId`, `Device_Type` | hundreds of thousands of rows |

### Tier 3 — per-mailing KPI tables

According to Genie these are **the only reliable sources for open and click KPIs**; every other table reports counts by engagement type only.

| Table | Grain | Engagement fields | Size |
|---|---|---|---|
| `tbl_pbi_mailingreciever_region` | mailing × region / country | `UniqueOpens`, `UniqueClicks`, `Count` | hundreds of thousands of rows |
| `tbl_pbi_mailingreciever_division` | mailing × business division | `UniqueOpens`, `UniqueClicks`, `Count` | hundreds of thousands of rows |
| `tbl_pbi_engagement` | mailing × link / component | `UniqueOpens`, `UniqueClicks`, `Count` | over a million rows |
| `tbl_pbi_kpi` | mailing (× status) | status, dates, recipient language | hundreds of thousands of rows |

Table names are spelled as Genie returned them (`reciever`). Schemas, join key and NULL semantics: [q21-imep-gold-tier3-schemas.md](q21-imep-gold-tier3-schemas.md).

Open and click rates are computable from these tables as unique opens (or clicks) divided by recipients. Genie's sample run did so for the whole population and per region; rates differ markedly between regions, so region is a meaningful slicer.

### Tier 4 — platform metadata and lookups

| Group | Tables | Size per table |
|---|---|---|
| Configuration and access | `tbl_pbi_platform_components`, `tbl_pbi_platform_access`, `tbl_pbi_mailing_access`, `tbl_pbi_platform_lang` | hundreds of thousands to millions of rows |
| Taxonomy and tagging | `tbl_pbi_platform_mailing_tags`, `tbl_pbi_platform_event_tags` | over a hundred thousand rows |
| Platform master data | `tbl_pbi_platform_events`, `tbl_pbi_platform_mailings`, `tbl_pbi_mailingid` | tens of thousands of rows |

The master tables showed the query date as maximum date — refreshed at least daily.

### Tier 5 — reference

`tbl_active_employee_month` — hundreds of rows, employee counts per region and division per month. A candidate denominator for reach metrics.

### Freshness and rebuild

- Platform tables (Tier 4): current-day freshness.
- Aggregate tables (Tiers 2 and 3): no maximum date; rebuilt end to end on every pipeline run, no history.

### Data-quality blocker: region is NULL for most recipients

For the **majority of recipients** the region is NULL. Genie called this "the dominant data quality issue in the dataset".

Root cause per Genie: the Gold build joins `tbl_hr_employee.ORGANIZATIONAL_UNIT` to `tbl_hr_costcenter.ORGANIZATIONAL_UNIT` with a LEFT JOIN, and for most recipients that join finds no match. Suspected reasons: different org-unit codes in the two tables, or missing history (employees on cost centres that are no longer in the current cost-centre snapshot). Neither is verified.

A second hypothesis is open: iMEP may maintain its own person-to-hierarchy table (similar to a contact table on the SharePoint side), which would make the HR path a fallback. See [open-questions.md](open-questions.md).

### Semantic NULLs

`UniqueOpens` and `UniqueClicks` are intentionally NULL where no engagement was tracked. NULL here means "none", not "unknown" — handle it before aggregating ([q21-imep-gold-tier3-schemas.md](q21-imep-gold-tier3-schemas.md)).

## Consequences

1. **Gold-first is confirmed** (Scenario A in [../architecture.html](../architecture.html), section 8). iMEP Gold delivers fully aggregated engagement KPIs; no Bronze ETL (extract, transform, load) is needed for an email fact.
2. **No `silver.fact_email` built from Bronze.** Read the KPI tables directly: `tbl_pbi_engagement` for mailing × link, `tbl_pbi_mailingreciever_region` / `_division` for mailing × geography or organisation.
3. **Multi-device handling is already done** in `tbl_pbi_deviceTypeall` (count per mailing × device type).
4. **HR audience attributes are pre-joined** in the Tier-2 and Tier-3 aggregates — with the NULL-region caveat. The dashboard must state openly that most recipients cannot be attributed to a region.
5. **Always `COALESCE(…, 0)`** on `UniqueOpens` / `UniqueClicks` before summing.
6. Follow-ups raised here and where they went:
   - `tbl_pbi_kpi` columns → answered by Q21.
   - Partitioning of `final` → Q30: no table is partitioned ([q30-storage-architecture.md](q30-storage-architecture.md)).
   - SharePoint Gold page-view aggregates → Q17 ([q17-sharepoint-gold-inventory.md](q17-sharepoint-gold-inventory.md)).
   - Relationship `tbl_pbi_mailingid` ↔ `tbl_pbi_platform_mailings` (identical or 1:1?) → still open.

## Related

- [q01b-imep-gold-layer.md](q01b-imep-gold-layer.md) · [q21-imep-gold-tier3-schemas.md](q21-imep-gold-tier3-schemas.md) · [q29-gold-tier-structure.md](q29-gold-tier-structure.md) · [q28-pipeline-operations.md](q28-pipeline-operations.md) · [q30-storage-architecture.md](q30-storage-architecture.md)
- [../tables/imep_gold/final.md](../tables/imep_gold/final.md) · [../diagrams/er_imep_gold.md](../diagrams/er_imep_gold.md)
- [../tables/hr/tbl_hr_employee.md](../tables/hr/tbl_hr_employee.md) · [../tables/hr/tbl_hr_costcenter.md](../tables/hr/tbl_hr_costcenter.md) · [../joins/hr_enrichment.md](../joins/hr_enrichment.md)
- [../genie_questions_imep.md](../genie_questions_imep.md) — Q16, Q16b–Q16d · [../sources.md](../sources.md#q16)
