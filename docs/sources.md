# Source index — Genie sessions Q1 … Q30

> Central traceability index. Every claim in `knowledge_base.md`, `tables/*.md`, `diagrams/*.md` and `joins/*.md` can be traced back to one of the Databricks Genie sessions listed here. Prose deliberately carries no inline `(Q##)` markers — instead use the `Sources` block at the bottom of each file, which points here.
>
> **Full raw transcripts** of the sessions: [genie_questions_imep.md](genie_questions_imep.md). **Deep-dive findings** with interpretation and consequences: memory at `~/.claude/projects/-Users-micha-Documents-Arbeit-Databricks/memory/`.

---

## How to read the sources

| Field | Meaning |
|---|---|
| **Date** | Day the session was run — facts may have changed since. |
| **Scope** | Which tables / which question was submitted against Databricks Genie. |
| **Key findings** | One-liner — the result as it flowed into the docs. |
| **Deep-Dive** | Link to the memory artefact with full analysis and SQL. |
| **Raw transcript** | Anchor in `genie_questions_imep.md` with the original prompt + answer. |

---

## A — Schema & TrackingId location

### Q1 — Where does TrackingId live?
- **Date**: 2026-04-16
- **Scope**: `tbl_email`, `tbl_email_links`, `tbl_analytics_link`, `tbl_email_receiver_status` — search for `tracking`/`camms`/`cpid`/`cplan` columns.
- **Finding**: TrackingId exists **only** on `imep_bronze.tbl_email` (mailing level), not on engagement tables. Format example: `QRREP-0000058-240709-0000060-EMI`. Cross-channel = dimension, not a fact key.
- **Deep-Dive**: [memory/imep_genie_findings_q1_q2_q3.md](../../../.claude/projects/-Users-micha-Documents-Arbeit-Databricks/memory/imep_genie_findings_q1_q2_q3.md)
- **Raw**: [genie_questions_imep.md §Q1](genie_questions_imep.md)

### Q1b — Evaluate iMEP gold layer
- **Date**: 2026-04-16
- **Scope**: Full schema + sample on `imep_gold.tbl_pbi_platform_mailings` and `tbl_pbi_platform_events`; identification as master vs. aggregate.
- **Finding**: Gold is a mailing/event master with content and registration metrics — **not** a send/open/click aggregate (tier-2 tables cover that). Sentinel `2124` for open-ended events. `silver.dim_pack` can be built directly from gold.
- **Deep-Dive**: [memory/imep_gold_layer_analysis.md](../../../.claude/projects/-Users-micha-Documents-Arbeit-Databricks/memory/imep_gold_layer_analysis.md)
- **Raw**: [genie_questions_imep.md §Q1b](genie_questions_imep.md)

### Q2 — Full core schemas
- **Date**: 2026-04-16
- **Scope**: DESCRIBE for `TBL_EMAIL`, `TBL_EMAIL_RECEIVER_STATUS`, `TBL_ANALYTICS_LINK`, `TBL_EMAIL_LINKS` with types + sample.
- **Finding**: 111 columns in total, all nullable. `tbl_email` 54 cols, `tbl_email_receiver_status` carries `LogStatus`/`TNumber`/`EmailLanguage`, `tbl_analytics_link` 22 cols incl. `Agent`/`LinkTypeEnum`/`EmailReceiverStatusId`, `tbl_email_links` 10 cols (lean).
- **Deep-Dive**: [memory/imep_genie_findings_q1_q2_q3.md](../../../.claude/projects/-Users-micha-Documents-Arbeit-Databricks/memory/imep_genie_findings_q1_q2_q3.md)
- **Raw**: [genie_questions_imep.md §Q2](genie_questions_imep.md)

### Q3 — HR bridge TNumber ↔ GPN (first pass)
- **Date**: 2026-04-16
- **Scope**: `tbl_hr_employee`, `tbl_hr_user` — identifier column inventory.
- **Finding**: **Hypothesis disproven** — no GPN column visible. `T_NUMBER` (lowercase) and `UbsId` (uppercase) are both T-numbers. Search was too narrow → opens Q3a/Q3b.
- **Deep-Dive**: [memory/hr_gpn_tnumber_relationship.md](../../../.claude/projects/-Users-micha-Documents-Arbeit-Databricks/memory/hr_gpn_tnumber_relationship.md)
- **Raw**: [genie_questions_imep.md §Q3](genie_questions_imep.md)

### Q3a — What IS the GPN in AppInsights?
- **Date**: 2026-04-17
- **Scope**: Match 10 GPN sample values from AppInsights `pageViews` against HR columns (with string transformations).
- **Finding**: Resolved by the Q3b finding — the GPN is `WORKER_ID` in HR, no string transformation needed.
- **Deep-Dive**: [memory/hr_gpn_tnumber_bridge_resolved.md](../../../.claude/projects/-Users-micha-Documents-Arbeit-Databricks/memory/hr_gpn_tnumber_bridge_resolved.md)
- **Raw**: [genie_questions_imep.md §Q3a](genie_questions_imep.md)

### Q3b — Extended HR search for GPN column
- **Date**: 2026-04-17
- **Scope**: Three-step deep search across all HR tables — name tokens, 8-digit pattern counts, schema scan.
- **Finding**: `imep_bronze.tbl_hr_employee.WORKER_ID` **is** the GPN. The same table also carries `T_NUMBER`. Bridge = simple LEFT JOIN, no external source.
- **Deep-Dive**: [memory/hr_gpn_tnumber_bridge_resolved.md](../../../.claude/projects/-Users-micha-Documents-Arbeit-Databricks/memory/hr_gpn_tnumber_bridge_resolved.md)
- **Raw**: [genie_questions_imep.md §Q3b](genie_questions_imep.md)

---

## B — Engagement events & coverage

### Q16 — iMEP gold inventory + attribution lag
- **Date**: 2026-04-18
- **Scope**: Which gold tables carry UniqueOpens/UniqueClicks, per mailing × Region/Division. Raw inventory of the 5-tier architecture.
- **Finding**: 22%/1.8% global open/click rates. 66% NULL-Region share on tier-3 aggregates → data-quality blocker. Tier-3 pivot of `tbl_pbi_kpi` documented.
- **Deep-Dive**: [memory/imep_gold_full_inventory.md](../../../.claude/projects/-Users-micha-Documents-Arbeit-Databricks/memory/imep_gold_full_inventory.md)

### Q17 — SharePoint gold + CPLAN inventory
- **Date**: 2026-04-18
- **Scope**: Which tables in `sharepoint_gold`, `sharepoint_clicks_gold`, `sharepoint_cplan` are accessible; which carry PageView/CTA metrics.
- **Finding**: `sharepoint_gold` (20 tables, 270M+ rows) + `sharepoint_clicks_gold` (CTA) + `sharepoint_cplan` (clusters/packs/activities) are all in Databricks. `pbi_gold` (60 tables) **not** accessible.
- **Deep-Dive**: [memory/sharepoint_gold_inventory.md](../../../.claude/projects/-Users-micha-Documents-Arbeit-Databricks/memory/sharepoint_gold_inventory.md)

### Q21 — iMEP gold tier-3 schemas
- **Date**: 2026-04-19
- **Scope**: DESCRIBE + sample for `tbl_pbi_mailings_region`, `_division`, `tbl_pbi_kpi`, `tbl_pbi_platform_mailings` — join keys and data types.
- **Finding**: Join key `MailingId` is lowercase `mailingid` on the engagement side! Match rates 72-98% (not every mailing has tier-3 entries). UniqueOpens NULL ~28-35%, UniqueClicks NULL ~66-81% — **semantic NULLs**, not defects. `tbl_pbi_kpi` is pivoted (measure + MailingReceiverStatus).
- **Deep-Dive**: [memory/imep_gold_tier3_schemas_q21.md](../../../.claude/projects/-Users-micha-Documents-Arbeit-Databricks/memory/imep_gold_tier3_schemas_q21.md)

### Q22 — SharePoint gold metric schemas
- **Date**: 2026-04-19
- **Scope**: Which gold table carries TrackingID? FK chain between `pbi_db_interactions_metrics` and `sharepoint_bronze.pages`.
- **Finding**: **No direct TrackingID** on gold metric tables. FK chain: `metrics.marketingPageId → pages.pageUUID → pages.UBSGICTrackingID`. ⚠️ Only 1,949/48,419 pages (~4%) carry UBSGICTrackingID → cross-channel coverage is sparse.
- **Deep-Dive**: [memory/sharepoint_gold_schemas_q22.md](../../../.claude/projects/-Users-micha-Documents-Arbeit-Databricks/memory/sharepoint_gold_schemas_q22.md)

---

## C — TrackingId format & match strategy

### Q23 — TrackingId format consistency iMEP ↔ SharePoint
- **Date**: 2026-04-20
- **Scope**: Structural comparison `tbl_email.TrackingId` ↔ `sharepoint_bronze.pages.UBSGICTrackingID` — length, case, segments, value overlap.
- **Finding**: Format identical (32-char 5-seg UPPER clean) **but** values almost disjoint (Jaccard 0.004, 6/1677 overlap). SEG5 = channel (iMEP: EMI/NLI/TWE; SharePoint: IAN/ITS/OPN/ANC). **Business-refined 2026-04-20: cross-channel match is pack level (SEG1-2) ONLY** — SEG1-4 also fails because an email and a page of the same pack carry different dates (SEG3) and activity sequences (SEG4).
- **Deep-Dive**: [memory/tracking_id_format_q23.md](../../../.claude/projects/-Users-micha-Documents-Arbeit-Databricks/memory/tracking_id_format_q23.md)
- **Raw**: [genie_questions_imep.md §Q23](genie_questions_imep.md)

### Q24 — TrackingId volume & adoption
- **Date**: 2026-04-20
- **Scope**: How many mailings even carry TrackingId? Adoption over time + channel distribution.
- **Finding**: Only 986/73,930 mailings (1.3%) carry TrackingId. Adoption ramping: 2024 = 99, 2025 = 637, 2026 YTD = 250. 80% are EMI. **791 EMI rows → 696 distinct TIDs → 54 distinct pack IDs (SEG1-2)**. Dashboard universe = 54 packs, default time window from 2025.
- **Deep-Dive**: [memory/tracking_id_volume_q24.md](../../../.claude/projects/-Users-micha-Documents-Arbeit-Databricks/memory/tracking_id_volume_q24.md)
- **Raw**: [genie_questions_imep.md §Q24](genie_questions_imep.md)

### Q25 — UBSGICTrackingID distribution across pages
- **Date**: 2026-04-20
- **Scope**: Which SharePoint sites have tracking coverage, over which time ranges, with what URL mapping?
- **Finding**: 99.4% of tracked pages sit on **a single site** ("News and events", 83 packs). Coverage rollout Sep 2024 (33.9%), peak 70.5% March 2026, never 80%. 1:1 URL↔TID mapping. Dashboard → restrict to News-and-events + floor 2024-09-01.
- **Deep-Dive**: [memory/sharepoint_pages_coverage_q25.md](../../../.claude/projects/-Users-micha-Documents-Arbeit-Databricks/memory/sharepoint_pages_coverage_q25.md)
- **Raw**: [genie_questions_imep.md §Q25](genie_questions_imep.md)

---

## D — Pipeline lineage without Unity Catalog

### Q26 — Silver existence via schema naming
- **Date**: 2026-04-20
- **Scope**: `SHOW SCHEMAS` scan — which schemas end in `_silver`, which in `_bronze` / `_gold`?
- **Finding**: 17 `_silver` schemas exist. **SharePoint uses full Bronze→Silver→Gold** (`sharepoint_silver` ~262M `webpagevisited`). **`imep_silver` exists only for events**, email engagement skips silver → `imep_gold.final` ~520M rows is the direct consumption endpoint. Medallion asymmetric. Stop planning for `silver.fact_email`.
- **Deep-Dive**: [memory/imep_silver_q26_findings.md](../../../.claude/projects/-Users-micha-Documents-Arbeit-Databricks/memory/imep_silver_q26_findings.md)
- **Raw**: [genie_questions_imep.md §Q26](genie_questions_imep.md)

### Q27 — Join graph via column fingerprint
- **Date**: 2026-04-20
- **Scope**: Scan all table schemas × 8 schemas for EmailId/TrackingId/TNumber/GPN columns; reconstruct the join graph.
- **Finding**: 93 tables / 8 schemas mapped. Two full-key hubs: `tbl_analytics_link` (533M) + `tbl_email_receiver_status` (293M). `EmailId` in 12 tables, `TNumber` in just 2 (→ person analytics = email-grain only), `TrackingId` in exactly 4 and **never co-occurring with EmailId** → TrackingId is a dimension, not a fact. Cross-channel = `TrackingId ↔ GICTrackingID` via `pages`, **not** via engagement. New schema `page_metadata_bronze` surfaced.
- **Deep-Dive**: [memory/imep_join_graph_q27_findings.md](../../../.claude/projects/-Users-micha-Documents-Arbeit-Databricks/memory/imep_join_graph_q27_findings.md)
- **Raw**: [genie_questions_imep.md §Q27](genie_questions_imep.md)

### Q28 — Pipeline hints from Delta history
- **Date**: 2026-04-20
- **Scope**: `DESCRIBE HISTORY` on the largest tables — write operations, service principal, operation types (MERGE/CTAS/Append).
- **Finding**: Unified MERGE → OPTIMIZE → VACUUM rhythm, 7-day retention. iMEP Bronze MERGE upsert (`tbl_analytics_link` truly incremental 3-8K/run; `tbl_email` / `tbl_email_receiver_status` full-replace 27-72M/run). SharePoint Bronze: `pages` MERGE snapshot, `pageviews` Append WRITE (7 bursts = API pagination). iMEP Gold CTAS Full Rebuild — 520M-row `final` is fully rewritten = largest compute cost. All writes via service principal (SPN `a71734ea-...`). **Scope limit**: concrete UTC refresh slots visible only in history, no complete scheduler overview.
- **Deep-Dive**: [memory/imep_pipeline_ops_q28_findings.md](../../../.claude/projects/-Users-micha-Documents-Arbeit-Databricks/memory/imep_pipeline_ops_q28_findings.md)
- **Raw**: [genie_questions_imep.md §Q28](genie_questions_imep.md)

### Q29 — Gold tier structure from table shape
- **Date**: 2026-04-20
- **Scope**: Scan 52 gold tables (iMEP + SharePoint) for row count, distinct key count, column count; derive the 4-tier hierarchy.
- **Finding**: Strict 4-tier hierarchy. **Tier 0** = atomic fact (`imep_gold.final` 520M grain `mailing × recipient × event × hour`; `sharepoint_gold.pbi_db_interactions_metrics` 84M). **Tier 1** = timespan × dim (15 tables). **Tier 2** = per-mailing summaries (incl. anomaly: `engagement` mixes emails+events with 117K vs 73K mailingIds). **Tier 3** = platform and reference dims (`tbl_pbi_platform_mailings` 73,920 rows, 927 TrackingIds). Cross-system bridge = dimensional via `tbl_pbi_platform_mailings.TrackingId → pages → marketingPageId`. SharePoint Gold has a video sub-domain (3 tables, managed Declarative Pipeline).
- **Deep-Dive**: [memory/imep_sp_gold_tiers_q29_findings.md](../../../.claude/projects/-Users-micha-Documents-Arbeit-Databricks/memory/imep_sp_gold_tiers_q29_findings.md)
- **Raw**: [genie_questions_imep.md §Q29](genie_questions_imep.md)

### Q30 — Table properties & storage location
- **Date**: 2026-04-20
- **Scope**: `DESCRIBE DETAIL` / `SHOW CREATE TABLE` across 114 tables — ADLS paths, partitioning, formats, pipeline owner.
- **Finding**: 114 external Delta tables across 3 ADLS accounts (iMEP bronze, SP bronze, shared Gold). **Gold is co-located** → cross-channel joins possible inside Fabric/Spark. **111/114 via notebook CTAS**, only 3 video tables via Declarative Pipeline. **⚠️ Zero partitioning across all tables** — the largest structural performance gap; impacts 520M `final` + 84M `interactions_metrics`. SharePoint Bronze has ad-hoc historical snapshots (`pageviews_09_27_2023` etc.). iMEP Gold paths: `/final` root + `/tbl_pbi/*` + outlier `/imep/tbl_active_employee_month`.
- **Deep-Dive**: [memory/storage_architecture_q30_findings.md](../../../.claude/projects/-Users-micha-Documents-Arbeit-Databricks/memory/storage_architecture_q30_findings.md)
- **Raw**: [genie_questions_imep.md §Q30](genie_questions_imep.md)

---

## Usage

- **In doc files**: add a `## Sources` block at the bottom of each file with the relevant Q-links (e.g. `- [Q22](../sources.md#q22) — SharePoint gold metric schemas`).
- **New sessions**: add an entry here + write a memory artefact + update the MEMORY.md index.
- **Contradictions**: when a later Genie session overturns an earlier assumption, do **not** delete the old Q entry — overwrite the finding with the date and make the change visible (e.g. Q23 has a business refinement from 2026-04-20 after the original Q23 answer on the same day).
