# Source index — Genie sessions Q1 … Q30

> Central traceability index. Every claim in `knowledge_base.md`, `tables/*.md`, `diagrams/*.md` and `joins/*.md` can be traced back to one of the Databricks Genie sessions listed here. Prose deliberately carries no inline `(Q##)` markers — instead use the `Sources` block at the bottom of each file, which points here.
>
> **Full raw transcripts** of the sessions: [genie_questions_imep.md](genie_questions_imep.md). **Deep-dive findings** with interpretation and consequences: [findings/](findings/README.md) (sanitized: no organisation name, no person ids, no infrastructure ids, no exact production figures).

---

## How to read the sources

| Field | Meaning |
|---|---|
| **Date** | Day the session was run — facts may have changed since. |
| **Scope** | Which tables / which question was submitted against Databricks Genie. |
| **Key findings** | One-liner — the result as it flowed into the docs. |
| **Deep-Dive** | Link to the sanitized finding in `docs/findings/`. |
| **Raw transcript** | Anchor in `genie_questions_imep.md` with the original prompt + answer. |

---

## A — Schema & TrackingId location

### Q1 — Where does TrackingId live?
- **Date**: 2026-04-16
- **Scope**: `tbl_email`, `tbl_email_links`, `tbl_analytics_link`, `tbl_email_receiver_status` — search for `tracking`/`camms`/`cpid`/`cplan` columns.
- **Finding**: TrackingId exists **only** on `imep_bronze.tbl_email` (mailing level), not on engagement tables. Format example: `ABCDE-0000123-240709-0000045-EMI`. Cross-channel = dimension, not a fact key.
- **Deep-Dive**: [findings/q01-q03-imep-schemas-and-tracking-id.md](findings/q01-q03-imep-schemas-and-tracking-id.md)
- **Raw**: [genie_questions_imep.md §Q1](genie_questions_imep.md)

### Q1b — Evaluate iMEP gold layer
- **Date**: 2026-04-16
- **Scope**: Full schema + sample on `imep_gold.tbl_pbi_platform_mailings` and `tbl_pbi_platform_events`; identification as master vs. aggregate.
- **Finding**: Gold is a mailing/event master with content and registration metrics — **not** a send/open/click aggregate (tier-2 tables cover that). Sentinel `2124` for open-ended events. `silver.dim_pack` can be built directly from gold.
- **Deep-Dive**: [findings/q01b-imep-gold-layer.md](findings/q01b-imep-gold-layer.md)
- **Raw**: [genie_questions_imep.md §Q1b](genie_questions_imep.md)

### Q2 — Full core schemas
- **Date**: 2026-04-16
- **Scope**: DESCRIBE for `TBL_EMAIL`, `TBL_EMAIL_RECEIVER_STATUS`, `TBL_ANALYTICS_LINK`, `TBL_EMAIL_LINKS` with types + sample.
- **Finding**: 111 columns in total, all nullable. `tbl_email` 54 cols, `tbl_email_receiver_status` carries `LogStatus`/`TNumber`/`EmailLanguage`, `tbl_analytics_link` 22 cols incl. `Agent`/`LinkTypeEnum`/`EmailReceiverStatusId`, `tbl_email_links` 10 cols (lean).
- **Deep-Dive**: [findings/q01-q03-imep-schemas-and-tracking-id.md](findings/q01-q03-imep-schemas-and-tracking-id.md)
- **Raw**: [genie_questions_imep.md §Q2](genie_questions_imep.md)

### Q3 — HR bridge TNumber ↔ GPN (first pass)
- **Date**: 2026-04-16
- **Scope**: `tbl_hr_employee`, `tbl_hr_user` — identifier column inventory.
- **Finding**: **Hypothesis disproven** — no GPN column visible. `T_NUMBER` (lowercase) and `__ORG__Id` (uppercase) are both T-numbers. Search was too narrow → opens Q3a/Q3b.
- **Deep-Dive**: [findings/hr-employee-identifiers.md](findings/hr-employee-identifiers.md)
- **Raw**: [genie_questions_imep.md §Q3](genie_questions_imep.md)

### Q3a — What IS the GPN in AppInsights?
- **Date**: 2026-04-17
- **Scope**: Match 10 GPN sample values from AppInsights `pageViews` against HR columns (with string transformations).
- **Finding**: Resolved by the Q3b finding — the GPN is `WORKER_ID` in HR, no string transformation needed.
- **Deep-Dive**: [findings/hr-employee-identifiers.md](findings/hr-employee-identifiers.md)
- **Raw**: [genie_questions_imep.md §Q3a](genie_questions_imep.md)

### Q3b — Extended HR search for GPN column
- **Date**: 2026-04-17
- **Scope**: Three-step deep search across all HR tables — name tokens, 8-digit pattern counts, schema scan.
- **Finding**: `imep_bronze.tbl_hr_employee.WORKER_ID` **is** the GPN. The same table also carries `T_NUMBER`. Bridge = simple LEFT JOIN, no external source.
- **Deep-Dive**: [findings/hr-employee-identifiers.md](findings/hr-employee-identifiers.md)
- **Raw**: [genie_questions_imep.md §Q3b](genie_questions_imep.md)

---

## B — Engagement events & coverage

### Q16 — iMEP gold inventory + attribution lag
- **Date**: 2026-04-18
- **Scope**: Which gold tables carry UniqueOpens/UniqueClicks, per mailing × Region/Division. Raw inventory of the 5-tier architecture.
- **Finding**: Global open/click rates computable (clicks far rarer than opens; rates differ markedly by region). NULL region for the majority of recipients on tier-3 aggregates → data-quality blocker. Tier-3 pivot of `tbl_pbi_kpi` documented.
- **Deep-Dive**: [findings/q16-imep-gold-inventory.md](findings/q16-imep-gold-inventory.md)

### Q17 — SharePoint gold + CPLAN inventory
- **Date**: 2026-04-18
- **Scope**: Which tables in `sharepoint_gold`, `sharepoint_clicks_gold`, `sharepoint_cplan` are accessible; which carry PageView/CTA metrics.
- **Finding**: `sharepoint_gold` (20 tables, hundreds of millions of rows) + `sharepoint_clicks_gold` (CTA) + `sharepoint_cplan` (clusters/packs/activities) are all in Databricks. `pbi_gold` (60 tables) **not** accessible.
- **Deep-Dive**: [findings/q17-sharepoint-gold-inventory.md](findings/q17-sharepoint-gold-inventory.md)

### Q21 — iMEP gold tier-3 schemas
- **Date**: 2026-04-19
- **Scope**: DESCRIBE + sample for `tbl_pbi_mailings_region`, `_division`, `tbl_pbi_kpi`, `tbl_pbi_platform_mailings` — join keys and data types.
- **Finding**: Join key `MailingId` is lowercase `mailingid` on the engagement side! Match rates vary widely by table (not every mailing has tier-3 entries). UniqueOpens NULL for a substantial minority of rows, UniqueClicks NULL for the majority — **semantic NULLs**, not defects. `tbl_pbi_kpi` is pivoted (measure + MailingReceiverStatus).
- **Deep-Dive**: [findings/q21-imep-gold-tier3-schemas.md](findings/q21-imep-gold-tier3-schemas.md)

### Q22 — SharePoint gold metric schemas
- **Date**: 2026-04-19
- **Scope**: Which gold table carries TrackingID? FK chain between `pbi_db_interactions_metrics` and `sharepoint_bronze.pages`.
- **Finding**: **No direct TrackingID** on gold metric tables. FK chain: `metrics.marketingPageId → pages.pageUUID → pages.__ORG__GICTrackingID`. ⚠️ Only a small minority of pages (low single-digit percent) carry __ORG__GICTrackingID → cross-channel coverage is sparse.
- **Deep-Dive**: [findings/q22-sharepoint-gold-schemas.md](findings/q22-sharepoint-gold-schemas.md)

---

## C — TrackingId format & match strategy

### Q23 — TrackingId format consistency iMEP ↔ SharePoint
- **Date**: 2026-04-20
- **Scope**: Structural comparison `tbl_email.TrackingId` ↔ `sharepoint_bronze.pages.__ORG__GICTrackingID` — length, case, segments, value overlap.
- **Finding**: Format identical (32-char 5-seg UPPER clean) **but** values almost disjoint (only a handful of values appear on both sides). SEG5 = channel (iMEP: EMI/NLI/TWE; SharePoint: IAN/ITS/OPN/ANC). **Business-refined 2026-04-20: cross-channel match is pack level (SEG1-2) ONLY** — SEG1-4 also fails because an email and a page of the same pack carry different dates (SEG3) and activity sequences (SEG4).
- **Deep-Dive**: [findings/q23-tracking-id-format.md](findings/q23-tracking-id-format.md)
- **Raw**: [genie_questions_imep.md §Q23](genie_questions_imep.md)

### Q24 — TrackingId volume & adoption
- **Date**: 2026-04-20
- **Scope**: How many mailings even carry TrackingId? Adoption over time + channel distribution.
- **Finding**: Only a small minority of mailings (low single-digit percent) carry TrackingId. Adoption starts in 2024 and ramps up steeply (2025 several times 2024, 2026 YTD on track to exceed 2025). The large majority are EMI. **Hundreds of EMI rows → slightly fewer distinct TIDs → a few dozen distinct pack IDs (SEG1-2)**. Dashboard universe = a few dozen packs, default time window from 2025.
- **Deep-Dive**: [findings/q24-tracking-id-volume.md](findings/q24-tracking-id-volume.md)
- **Raw**: [genie_questions_imep.md §Q24](genie_questions_imep.md)

### Q25 — __ORG__GICTrackingID distribution across pages
- **Date**: 2026-04-20
- **Scope**: Which SharePoint sites have tracking coverage, over which time ranges, with what URL mapping?
- **Finding**: Virtually all tracked pages sit on **a single site** ("News and events", several dozen packs). Coverage rollout Sep 2024 (jump from near zero to a substantial share), peak March 2026, never 80%. 1:1 URL↔TID mapping. Dashboard → restrict to News-and-events + floor 2024-09-01.
- **Deep-Dive**: [findings/q25-sharepoint-pages-coverage.md](findings/q25-sharepoint-pages-coverage.md)
- **Raw**: [genie_questions_imep.md §Q25](genie_questions_imep.md)

---

## D — Pipeline lineage without Unity Catalog

### Q26 — Silver existence via schema naming
- **Date**: 2026-04-20
- **Scope**: `SHOW SCHEMAS` scan — which schemas end in `_silver`, which in `_bronze` / `_gold`?
- **Finding**: 17 `_silver` schemas exist. **SharePoint uses full Bronze→Silver→Gold** (`sharepoint_silver`, `webpagevisited` with hundreds of millions of rows). **`imep_silver` exists only for events**, email engagement skips silver → `imep_gold.final` (hundreds of millions of rows) is the direct consumption endpoint. Medallion asymmetric. Stop planning for `silver.fact_email`.
- **Deep-Dive**: [findings/q26-imep-silver.md](findings/q26-imep-silver.md)
- **Raw**: [genie_questions_imep.md §Q26](genie_questions_imep.md)

### Q27 — Join graph via column fingerprint
- **Date**: 2026-04-20
- **Scope**: Scan all table schemas × 8 schemas for EmailId/TrackingId/TNumber/GPN columns; reconstruct the join graph.
- **Finding**: 93 tables / 8 schemas mapped. Two full-key hubs: `tbl_analytics_link` + `tbl_email_receiver_status` (hundreds of millions of rows each). `EmailId` in 12 tables, `TNumber` in just 2 (→ person analytics = email-grain only), `TrackingId` in exactly 4 and **never co-occurring with EmailId** → TrackingId is a dimension, not a fact. Cross-channel = `TrackingId ↔ GICTrackingID` via `pages`, **not** via engagement. New schema `page_metadata_bronze` surfaced.
- **Deep-Dive**: [findings/q27-imep-join-graph.md](findings/q27-imep-join-graph.md)
- **Raw**: [genie_questions_imep.md §Q27](genie_questions_imep.md)

### Q28 — Pipeline hints from Delta history
- **Date**: 2026-04-20
- **Scope**: `DESCRIBE HISTORY` on the largest tables — write operations, service principal, operation types (MERGE/CTAS/Append).
- **Finding**: Unified MERGE → OPTIMIZE → VACUUM rhythm, 7-day retention. iMEP Bronze MERGE upsert (`tbl_analytics_link` truly incremental, thousands/run; `tbl_email` / `tbl_email_receiver_status` full-replace, up to tens of millions/run). SharePoint Bronze: `pages` MERGE snapshot, `pageviews` Append WRITE (a handful of bursts = API pagination). iMEP Gold CTAS Full Rebuild — `final` (hundreds of millions of rows) is fully rewritten = largest compute cost. All writes via service principal (identity not recorded here). **Scope limit**: concrete UTC refresh slots visible only in history, no complete scheduler overview.
- **Deep-Dive**: [findings/q28-pipeline-operations.md](findings/q28-pipeline-operations.md)
- **Raw**: [genie_questions_imep.md §Q28](genie_questions_imep.md)

### Q29 — Gold tier structure from table shape
- **Date**: 2026-04-20
- **Scope**: Scan 52 gold tables (iMEP + SharePoint) for row count, distinct key count, column count; derive the 4-tier hierarchy.
- **Finding**: Strict 4-tier hierarchy. **Tier 0** = atomic fact (`imep_gold.final`, hundreds of millions of rows, grain `mailing × recipient × event × hour`; `sharepoint_gold.pbi_db_interactions_metrics`, tens of millions). **Tier 1** = timespan × dim (15 tables). **Tier 2** = per-mailing summaries (incl. anomaly: `engagement` mixes emails+events, with more distinct mailingIds than the mailing master has rows). **Tier 3** = platform and reference dims (`tbl_pbi_platform_mailings`, tens of thousands of rows, hundreds of distinct TrackingIds). Cross-system bridge = dimensional via `tbl_pbi_platform_mailings.TrackingId → pages → marketingPageId`. SharePoint Gold has a video sub-domain (3 tables, managed Declarative Pipeline).
- **Deep-Dive**: [findings/q29-gold-tier-structure.md](findings/q29-gold-tier-structure.md)
- **Raw**: [genie_questions_imep.md §Q29](genie_questions_imep.md)

### Q30 — Table properties & storage location
- **Date**: 2026-04-20
- **Scope**: `DESCRIBE DETAIL` / `SHOW CREATE TABLE` across 114 tables — ADLS paths, partitioning, formats, pipeline owner.
- **Finding**: 114 external Delta tables across 3 ADLS accounts (iMEP bronze, SP bronze, shared Gold). **Gold is co-located** → cross-channel joins possible inside Fabric/Spark. **111/114 via notebook CTAS**, only 3 video tables via Declarative Pipeline. **⚠️ Zero partitioning across all tables** — the largest structural performance gap; impacts `final` (hundreds of millions of rows) + `interactions_metrics` (tens of millions). SharePoint Bronze has ad-hoc historical snapshots (`pageviews_09_27_2023` etc.). iMEP Gold paths: `/final` root + `/tbl_pbi/*` + outlier `/imep/tbl_active_employee_month`.
- **Deep-Dive**: [findings/q30-storage-architecture.md](findings/q30-storage-architecture.md)
- **Raw**: [genie_questions_imep.md §Q30](genie_questions_imep.md)

---

## Usage

- **In doc files**: add a `## Sources` block at the bottom of each file with the relevant Q-links (e.g. `- [Q22](../sources.md#q22) — SharePoint gold metric schemas`).
- **New sessions**: add an entry here + write a finding in `docs/findings/` + add it to `docs/findings/README.md`.
- **Contradictions**: when a later Genie session overturns an earlier assumption, do **not** delete the old Q entry — overwrite the finding with the date and make the change visible (e.g. Q23 has a business refinement from 2026-04-20 after the original Q23 answer on the same day).
