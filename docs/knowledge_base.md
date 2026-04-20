# Cross-Channel Analytics — Technical Knowledge Base

> **Scope**: Bronze → (Silver) → Gold data paths for iMEP (Email) and SharePoint (Intranet), plus cross-channel linking via TrackingId. This documentation targets new engineers/analysts — within 15 minutes it must be clear which table holds what and how to join them.

---

## Where to start

If you are **new to the project**, read in this order:

1. **[architecture_diagram.md](architecture_diagram.md)** — The 7-section overview with end-to-end data flow (Section 1), Bronze join pattern (Section 2), employee bridge (Section 3), cross-channel logic (Section 4).
2. **[joins/join_strategy_contract.md](joins/join_strategy_contract.md)** — Single-pager with the key dos and don'ts. **Read this before writing any join.**
3. **The five core tables** (see below) — 95% of the data you care about lives here.

---

## The 5 core tables

Once you have worked through these five cards, you can answer most cross-channel questions on your own:

| Table | Rows | Role |
|---|---|---|
| **[imep_bronze.tbl_email](tables/imep/tbl_email.md)** | 145K | Mailing master with `TrackingId` — the entry point for all email analyses |
| **[imep_bronze.tbl_email_receiver_status](tables/imep/tbl_email_receiver_status.md)** | 293M | Sends/bounces per recipient (full-key hub #1) |
| **[imep_bronze.tbl_analytics_link](tables/imep/tbl_analytics_link.md)** | 533M | Opens/clicks per recipient × link (full-key hub #2) |
| **[imep_gold.final](tables/imep_gold/final.md)** | 520M | **Denormalised consumption endpoint for email** — HR already joined |
| **[sharepoint_gold.pbi_db_interactions_metrics](tables/sharepoint_gold/pbi_db_interactions_metrics.md)** | 84M | Master interaction fact for SharePoint (views/visits/duration) |

Plus the bridge:

| Table | Rows | Role |
|---|---|---|
| **[sharepoint_bronze.pages](tables/sharepoint/pages.md)** | 48K | Page inventory with `UBSGICTrackingID` — **the only place** where cross-channel attribution happens |

---

## Full table overview

### iMEP Domain (Email + Events)

**Bronze** — `imep_bronze.*`
- [tbl_email](tables/imep/tbl_email.md) — Mailing master (145K)
- [tbl_email_receiver_status](tables/imep/tbl_email_receiver_status.md) — Sends/bounces (293M)
- [tbl_analytics_link](tables/imep/tbl_analytics_link.md) — Opens/clicks (533M)
- [tbl_email_links](tables/imep/tbl_email_links.md) — Template URL inventory
- tbl_email_components — 3.3M *(card pending)*
- tbl_email_template_images — 1.7M *(card pending)*
- [tbl_event](tables/imep/tbl_event.md) — Events (100K)

**Silver** — `imep_silver.*` *(events only! no email silver)*
- invitation, eventregistration (13.7M), event (84K) *(cards pending)*

**Gold** — `imep_gold.*`
- [final](tables/imep_gold/final.md) — Denormalised email endpoint (520M)
- tbl_pbi_platform_mailings — Mailing master with content metrics (73K) *(card pending)*
- tbl_pbi_mailings_region, tbl_pbi_mailings_division, tbl_pbi_kpi *(cards pending)*

### SharePoint Domain (Intranet)

**Bronze** — `sharepoint_bronze.*`
- [pages](tables/sharepoint/pages.md) — Page inventory with TrackingID (48K, **⚠️ only 4% coverage**)
- pageviews — Raw PageViews (173M) *(card pending)*
- customevents — Raw interactions (262M) *(card pending)*
- sites — Site metadata (805) *(card pending)*

**Silver** — `sharepoint_silver.*`
- webpagevisited (262M), pageviewed (136M), pagevisited (105M) *(cards pending)*
- webpage, website *(dims, cards pending)*

**Gold** — `sharepoint_gold.*`
- [pbi_db_interactions_metrics](tables/sharepoint_gold/pbi_db_interactions_metrics.md) — Master fact (84M)
- pbi_db_pageviewed_metric, pbi_db_pagevisited_metric, pbi_db_datewise_overview_fact_tbl *(cards pending)*
- pbi_db_employeecontact — ⚠️ potential person bridge (24M) *(card pending)*

**Clicks Gold** — `sharepoint_clicks_gold.*`
- pbi_db_ctalabel_intr_fact_gold — CTA click fact (3M) *(card pending)*

**CPLAN** — `sharepoint_cplan.*`
- internalcommunicationactivities_bronze (4,349 activities with tracking_id) *(card pending)*
- communicationspacks_bronze (280 packs), trackingcluster_bronze (17 clusters) *(cards pending)*

### HR Domain — `imep_bronze.*` (lives in iMEP, but used by everyone)

- [tbl_hr_employee](tables/hr/tbl_hr_employee.md) — T_NUMBER + WORKER_ID (= GPN), 265K
- [tbl_hr_costcenter](tables/hr/tbl_hr_costcenter.md) — Region/Division/Area/Country
- [tbl_hr_user](tables/hr/tbl_hr_user.md) — UbsId (uppercase variant)

### Page Metadata — `page_metadata_bronze.*` (Finding)
- pagelikesview (40K), comments (3.4K), reportedcomments, moderatedcomments *(cards pending)*

---

## Canonical Join Recipes

This is where the **ready-made SQL recipes** live for the most common join patterns:

| Recipe | Purpose |
|---|---|
| **[join_strategy_contract.md](joins/join_strategy_contract.md)** | ⭐ Read this first — dos and don'ts |
| [imep_bronze_email_events.md](joins/imep_bronze_email_events.md) | The 4-table Bronze chain: Mailing × Recipient × Event |
| [sharepoint_gold_to_pages.md](joins/sharepoint_gold_to_pages.md) | Gold metrics → TrackingID via pageUUID |
| [hr_enrichment.md](joins/hr_enrichment.md) | TNumber ↔ GPN ↔ Region/Division |
| [cross_channel_via_tracking_id.md](joins/cross_channel_via_tracking_id.md) | iMEP ↔ SharePoint via TrackingId SEG1-2 |

---

## ER diagrams per domain

| Diagram | Scope |
|---|---|
| [er_imep_bronze.md](diagrams/er_imep_bronze.md) | Extended version of Section 2 — 4 iMEP Bronze + 3 HR tables |
| [er_sharepoint_bronze.md](diagrams/er_sharepoint_bronze.md) | pages × pageviews × customevents × sites |
| [er_imep_gold.md](diagrams/er_imep_gold.md) | final + tbl_pbi_* tier structure |
| [er_sharepoint_gold.md](diagrams/er_sharepoint_gold.md) | marketingPageId FK chain (all metric tables) |
| [er_cross_channel.md](diagrams/er_cross_channel.md) | TrackingID bridge + employee bridge between domains |

---

## Core findings everyone must know

These five facts are the **hard constraints** that every cross-channel analysis must be built against:

### 1. Cross-channel link is dimensional, not factual

`tbl_email.TrackingId` ↔ `sharepoint_bronze.pages.UBSGICTrackingID` — **never** directly via engagement tables (`pageviews`, `tbl_analytics_link`). TrackingId is a dimension, not a fact key.

### 2. Only ~4% of pages have UBSGICTrackingID

1,949 out of 48,419 pages. That means: ~96% of SharePoint interactions **cannot** be attributed to a pack. The dashboard must make this explicit.

### 3. TrackingId adoption has only been ramping since 2024/25

Only 986/73,930 mailings (1.3%) carry a TrackingId. Default dashboard time window: **from 2025 onwards**.

### 4. Write patterns: incremental MERGE (Bronze) vs. full rebuild (Gold)

- **iMEP Bronze**: MERGE upsert. `tbl_analytics_link` is **truly incremental** (3-8K rows per run), `tbl_email` / `tbl_email_receiver_status` are full-table upserts (27-72M per run).
- **SharePoint Bronze**: `pages` via **MERGE daily snapshot replace**, `pageviews` via **Append-WRITE** (7 bursts per run, API pagination).
- **iMEP Gold**: **Full Rebuild** — tables (including the 520M-row `final`) are fully dropped and rewritten, not incremental.

**Caveat**: Refresh cadence deliberately not documented — we do **not have a complete job scheduler overview** (Delta history only shows write events, not the full schedule). Dashboards should be built resilient against the current snapshot version, rather than relying on specific time windows.

### 5. Email skips Silver entirely

`imep_silver` exists only for events. There is **no silver layer** for email engagement — `imep_gold.final` is the direct consumption endpoint (denormalised, HR-enriched).

### 6. Gold is classified into 4 tiers

Both gold schemas follow the same strict hierarchy:

- **Tier 0** — Atomic fact (largest tables): iMEP `final` 520M, SP `pbi_db_interactions_metrics` 84M
- **Tier 1** — Pre-aggregated timespans (rolling windows, date aggregates)
- **Tier 2** — Per-mailing / per-page summaries (UniqueOpens/Clicks, engagement)
- **Tier 3** — Platform and reference dimensions (`tbl_pbi_platform_mailings`, `employeecontact`, calendar etc.)

**Mental model**: iMEP Gold = message-centric per-recipient; SharePoint Gold = page-centric analytics. TrackingId is the only conceptual bridge, and it lives at dimension level (Tier 3).

### 7. Storage architecture: Gold co-located, zero partitioning

All 114 Delta tables are external, across **3 ADLS accounts** (iMEP Bronze, SP Bronze, **shared Gold**). The shared-Gold design enables cross-channel joins inside Fabric/Spark without cross-account auth. ⚠️ **No partitioning on any table** — the largest structural performance gap. Queries on `final` or `interactions_metrics` without a time filter = full-scan risk.

---

## Genie questions and findings

All insights come from structured Genie sessions. Full trail:

- **[genie_questions_imep.md](genie_questions_imep.md)** — All prompts with answers
- **[BRD_cross_channel_analytics.md](BRD_cross_channel_analytics.md)** — Business requirements document

---

## Not in this KB (deliberately)

- **Implementation SQL for our own model** — lives in `/cross_channel_mvp.sql` and `/tracking_coverage_analysis.sql`
- **Dashboard code** — lives in `/dashboard/`

---

## Sources

Genie sessions that back the statements on this page: [Q1](sources.md#q1), [Q22](sources.md#q22), [Q24](sources.md#q24), [Q26](sources.md#q26), [Q27](sources.md#q27), [Q28](sources.md#q28), [Q29](sources.md#q29), [Q30](sources.md#q30). See [sources.md](sources.md) for the full index.
