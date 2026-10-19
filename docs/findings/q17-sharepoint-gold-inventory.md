# SharePoint Gold and CPLAN Inventory

> Source: Genie question Q17 (2026-04-17). Status: confirmed (inventory); the bridge and pre-join hypotheses below are open.

## Question

Does SharePoint have a gold layer with page-view and click metrics, comparable to `imep_gold`? Which further schemas are accessible, and does communication-planning data (CPLAN: clusters, packs, activities) already exist in Databricks?

The scan covered 292 tables across 33 schemas.

## Findings

### SharePoint has a full bronze → silver → gold stack

| Schema | Tables | Volume | Role |
|---|---|---|---|
| `sharepoint_bronze` | 12 (+ snapshots) | hundreds of millions of rows | Raw ingestion: events, page views, page and site inventory |
| `sharepoint_silver` | 5 | hundreds of millions of rows | Refined interaction layer, strongly normalised |
| `sharepoint_gold` | 20 | hundreds of millions of rows in total | **Reporting and metrics layer** for Power BI |
| `sharepoint_clicks_gold` | 3 | millions of rows | Call-to-action (CTA) click layer |
| `sharepoint_cplan` | 11 | small | Communication planning: clusters, packs, activities |

Across the nine `sharepoint_*` schemas the accessible volume is on the order of a billion rows. Two tables dominate: `sharepoint_bronze.customevents` and `sharepoint_silver.webpagevisited`, both in the hundreds of millions.

### `sharepoint_bronze`

| Table | Columns | Volume | Purpose |
|---|---|---|---|
| `customevents` | 62 | hundreds of millions | Raw interaction events |
| `pageviews` | 57 | hundreds of millions | Raw page views |
| `master_employee_cdm_data` | **246** | tens of millions | Common Data Model (CDM) employee master — potential identity bridge |
| `pageviews_1_july_till_17_oct` | 56 | tens of millions | Historical snapshot |
| `pageviews_08022024` | 55 | tens of millions | Historical snapshot |
| `customevents_history` | 62 | tens of millions | Historical events |
| `pages` | 95 | tens of thousands | Page inventory (carries the tracking id) |
| `videos` | 54 | thousands | Video metadata |
| `sites` | 71 | hundreds | Site metadata |

### `sharepoint_silver`

| Table | Columns | Volume |
|---|---|---|
| `webpagevisited` | 111 | hundreds of millions |
| `pageviewed` | 36 | hundreds of millions |
| `pagevisited` | 35 | around a hundred million |
| `webpage` | 102 | tens of thousands |
| `website` | 43 | hundreds |

`webpagevisited` has exactly the row count of `bronze.customevents` → a 1:1 silver transformation of the raw events. `webpage` and `website` match `pages` and `sites` in row count.

### `sharepoint_gold` — the page-view side of the funnel

| Table | Columns | Volume | Purpose |
|---|---|---|---|
| `pbi_db_interactions_metrics` | 11 | tens of millions | **Main interaction fact** (views, visits, duration, comments) |
| `pbi_db_pageviewed_metric` | 5 | tens of millions | Lightweight view counts |
| `pbi_db_pagevisited_metric` | 9 | tens of millions | Visit-oriented metric |
| `pbi_db_page_visitedkey_view` | 1 | tens of millions | View with a single key column |
| `pbi_db_employeecontact` | 17 | tens of millions | Employee ↔ contact mapping — potential identity bridge |
| `pbi_db_90_days_interactions_metric` | 11 | millions | 90-day window aggregate |
| `pbi_db_datewise_overview_fact_tbl` | 31 | millions | **Widest fact**, pre-aggregated per date |

The tables are built for Power BI: narrow, metric-centric facts with dimension keys and counts only. `pbi_db_interactions_metrics` and `pbi_db_pageviewed_metric` have identical row counts → same grain. `pbi_db_employeecontact` has almost exactly the row count of `master_employee_cdm_data` → most likely derived from the CDM master (hypothesis).

### `sharepoint_clicks_gold` and `sharepoint_clicks`

- `sharepoint_clicks_gold.pbi_db_ctalabel_intr_fact_gold` (11 columns, millions of rows) — the largest of three tables; most likely CTA clicks per page element.
- `sharepoint_clicks.webpagevisited_silver` (122 columns, tens of millions of rows) — the only table in that schema.

### `sharepoint_cplan` — communication planning is already in Databricks

| Table | Volume | Content |
|---|---|---|
| `internalcommunicationactivities_bronze` | thousands | All internal activities, with `tracking_id` |
| `externalcommunicationactivities_bronze` | not reported | External activities |
| `communicationspacks_bronze` | hundreds | All packs |
| `trackingcluster_bronze` | a short list | Complete cluster list |

Activities ↔ packs ↔ clusters can be read directly from the catalogue; no import from the external CPLAN pipeline is needed.

### Other schemas

- `imep_gold` (31 tables, gold layer of iMEP, the internal mailing and event platform): `final` holds the vast majority of the schema's rows (hundreds of millions). See [q16-imep-gold-inventory.md](q16-imep-gold-inventory.md).
- `imep_silver` (3 tables): `invitation` (39 columns, over ten million rows), `eventregistration` (48 columns, millions), `event` (164 columns, tens of thousands). Event-centric, wide; most likely feeds the event aggregates in `imep_gold`. See [q26-imep-silver.md](q26-imep-silver.md).
- Small or test schemas: `sharepoint_apps`, `sharepoint_lead`, `sharepoint_metadata`, `sharepointtest_bronze`.

### Inaccessible schemas

About a dozen schemas return row and column count `-1` (permission, external table or restricted view): external marketing and advertising sources, customer relationship management (CRM) sources, person and profile data, and **`pbi_gold` (60 tables)**. `pbi_gold` looks central to branding and to the corporate Power BI semantic model.

## Consequences

1. **Scenario A — fully gold-based — is plausible for both channels.** The page-view side of the funnel can be built from `sharepoint_gold` just as the email side from `imep_gold`.
2. **Key tables for a cross-channel fact:**
   - Email: `imep_gold.tbl_pbi_engagement`, `tbl_pbi_mailingreciever_region`, `tbl_pbi_mailingreciever_division`
   - Page view: `sharepoint_gold.pbi_db_interactions_metrics`, `pbi_db_pageviewed_metric`, `pbi_db_pagevisited_metric`
   - CTA click: `sharepoint_clicks_gold.pbi_db_ctalabel_intr_fact_gold`
   - Pack metadata: `sharepoint_cplan.*_bronze` — replaces the external CPLAN source
3. **`sharepoint_gold.pbi_db_employeecontact`** may be an alternative identity bridge to `imep_bronze.tbl_hr_employee`. Not yet validated.
4. **Request access to `pbi_gold`.**

Follow-ups raised at the time and their status:

- Q17b — which tracking-id column the gold metrics carry → answered by [Q22](q22-sharepoint-gold-schemas.md): none; the chain runs via `pages`.
- Q17c — how `pbi_db_interactions_metrics` joins to `pages` → answered by Q22: `marketingPageId = pages.pageUUID`.
- Q17d — what `pbi_db_datewise_overview_fact_tbl` holds → answered by Q22: page × date × division with rolling windows.
- Q17e/f — CPLAN usability and `pbi_gold` access → open.

## Related

- [q22-sharepoint-gold-schemas.md](q22-sharepoint-gold-schemas.md) — schemas and foreign-key chain of the gold metrics
- [q16-imep-gold-inventory.md](q16-imep-gold-inventory.md) — the email-side counterpart
- [q29-gold-tier-structure.md](q29-gold-tier-structure.md) — tier structure of both gold schemas
- [q30-storage-architecture.md](q30-storage-architecture.md) — physical layout and pipeline families
- [hr-employee-identifiers.md](hr-employee-identifiers.md) — identity bridge options
- [camms-tracking-id.md](camms-tracking-id.md) — tracking id format used by CPLAN
- [../tables/sharepoint_gold/pbi_db_interactions_metrics.md](../tables/sharepoint_gold/pbi_db_interactions_metrics.md)
- [../genie_questions_imep.md](../genie_questions_imep.md) — Q17 to Q17f
- [../sources.md](../sources.md#q17)
