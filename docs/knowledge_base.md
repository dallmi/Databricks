# MultiChannelDataModel

> Unified Bronze → (Silver) → Gold data paths for iMEP (Email) and SharePoint (Intranet), bridged through the TrackingId dimension. Target audience: engineers and analysts feeding the Clarity Dashboard. The goal of this page is that within 15 minutes of reading it is clear which table contains what, how domains connect, and where the hard constraints lie.

---

## 1. Revision History

| Reason for change | Name | Date | Version |
|---|---|---|---|
| Initial version | Michael Dall | 2026-04-20 | 0.1 |

---

## 2. Glossary

### 2.1 Data sections

Each domain has a dedicated container with per-table cards (schema, sample rows, joins, quality caveats).

| Section | Scope | Link |
|---|---|---|
| HR | Employee master + cost-center dimension (shared across iMEP and SharePoint) | [tables/hr/](tables/hr/) |
| iMEP Bronze | Email mailings, send events, open/click events, template inventory, event master | [tables/imep/](tables/imep/) |
| iMEP Gold | Denormalized email fact (`final`, 520M) plus tier-1/2/3 aggregates | [tables/imep_gold/](tables/imep_gold/) |
| SharePoint Bronze | Page inventory (with TrackingID) + raw page-view / custom-event interaction streams | [tables/sharepoint/](tables/sharepoint/) |
| SharePoint Gold | Master interaction fact (`pbi_db_interactions_metrics`, 84M) + specialized grain tables | [tables/sharepoint_gold/](tables/sharepoint_gold/) |

### 2.2 Key terms

| Term | Definition |
|---|---|
| **TrackingId** | 32-character dimension key shared between iMEP and SharePoint, format `CLUSTER-PACK-YYMMDD-ACTIVITY-CHANNEL`. Lives on `tbl_email` (iMEP, column `TrackingId`) and `sharepoint_bronze.pages` (SharePoint, column `UBSGICTrackingID`). The only cross-channel join key. |
| **Pack** | SEG1 + SEG2 of the TrackingId (Cluster + Pack-Number, e.g. `QRREP-0000058`). Identifies a campaign and bundles all its activities across channels and dates. **Cross-channel match lives at this level.** |
| **Cluster** | SEG1 of the TrackingId (5 characters, ALPHA, e.g. `QRREP`). Campaign prefix — groups all Packs of a campaign family. |
| **Activity** | A single instance within a Pack (one email, one intranet page, one newsletter). Identified by SEG3 (YYMMDD of creation) + SEG4 (sequence number). **Activity-level join does not work cross-channel** because the email and the matching intranet page carry different activity dates. |
| **Channel** | SEG5 of the TrackingId. System-specific vocabulary: iMEP uses `EMI` / `NLI` / `TWE`; SharePoint uses `IAN` / `ITS` / `OPN` / `ANC`. The reason a full-string match across domains fails. |
| **TNumber** | Lowercase `t######` identifier (e.g. `t100200`). iMEP's person key — lives on `tbl_email_receiver_status` and `tbl_analytics_link`. Primary key in `tbl_hr_employee` as `T_NUMBER`. |
| **GPN** | Global Personnel Number, 8-digit numeric with leading zeros (e.g. `00100200`). SharePoint's person key (`pageviews.user_gpn`). Same value stored on `tbl_hr_employee` as `WORKER_ID`. |
| **UbsId** | Uppercase `T######` variant of the T-Number, lives on `tbl_hr_user`. Joining to other tables requires `LOWER()` on both sides. |
| **MailingId** | Unique identifier of a specific email mailing (= `tbl_email.Id`, GUID). Foreign key referenced by `tbl_email_receiver_status.EmailId` and `tbl_analytics_link.EmailId`. Carried forward into `imep_gold.final` and `imep_gold.tbl_pbi_platform_mailings` as **lowercase `mailingid`** in the engagement aggregates. |
| **UBSGICTrackingID** | SharePoint's column name for the TrackingId. Lives on `sharepoint_bronze.pages` at page-inventory grain. Only ~4% of pages carry a value — coverage is the dominant cross-channel constraint. |

---

## 3. References

| Title | Type | Link |
|---|---|---|
| CPlan GIC Tracking Clarity Dashboard | Parent page (corp FitNesse) | link |
| Sources — Genie sessions Q1 … Q30 | Internal traceability index | [sources.md](sources.md) |
| BRD Cross-Channel Analytics | Business requirements document | [BRD_cross_channel_analytics.md](BRD_cross_channel_analytics.md) |
| iMEP Team Space | Upstream team (email engagement pipelines) | link |
| SharePoint Analytics Team Space | Upstream team (intranet interactions) | link |
| Databricks Workspace | Data platform hosting all tables | link |

---

## 4. Business Justification

The **Clarity Dashboard** is the single consumption surface for InternalCommunication MultiChannel campaign performance. Today, campaign metrics are fragmented across multiple dashboards — email engagement in one place, intranet analytics in another, event registrations in a third — which makes end-to-end funnel attribution impossible and forces analysts to stitch sources manually.

This data model unifies iMEP (email), SharePoint (intranet) and HR through the TrackingId dimension. The result is a single source for the full funnel — **mailing sent → opened → clicked → page viewed → event registered** — broken down by Region and Division.

---

## 5. Data Model at a Glance

Medallion structure per domain. Note the asymmetry: SharePoint uses the full Bronze → Silver → Gold pattern; iMEP email engagement **skips Silver** and ships straight from Bronze into `imep_gold.final` as a denormalized, HR-enriched consumption endpoint. Event data (invitation / registration / event) does have a Silver layer.

| Layer | iMEP (`imep_bronze` / `imep_silver` / `imep_gold`) | SharePoint (`sharepoint_bronze` / `_silver` / `_gold`) | HR (`imep_bronze`, shared) |
|---|---|---|---|
| **Bronze** | `tbl_email`, `tbl_email_receiver_status`, `tbl_analytics_link`, `tbl_email_links`, `tbl_event` | `pages`, `pageviews`, `customevents`, `sites` | `tbl_hr_employee`, `tbl_hr_costcenter`, `tbl_hr_user` |
| **Silver** | Events only — `invitation`, `eventregistration`, `event` | `webpagevisited`, `pageviewed`, `pagevisited` | — |
| **Gold** | **`final`** (520M atomic fact), `tbl_pbi_platform_mailings` / `_events`, tier-1/2/3 aggregates | **`pbi_db_interactions_metrics`** (84M atomic fact), tier-1/2/3 grain-specific tables | — |

See [er_cross_channel.md](diagrams/er_cross_channel.md) for the end-to-end visual.

---

## 6. The 5 Core Tables

After these five cards you can answer most cross-channel questions on your own:

| Table | Rows | Role |
|---|---|---|
| [`imep_bronze.tbl_email`](tables/imep/tbl_email.md) | 145K | Mailing master with `TrackingId` — entry point for all email analysis |
| [`imep_bronze.tbl_email_receiver_status`](tables/imep/tbl_email_receiver_status.md) | 293M | Sends/Bounces per recipient (full-key hub #1) |
| [`imep_bronze.tbl_analytics_link`](tables/imep/tbl_analytics_link.md) | 533M | Opens/Clicks per recipient × link (full-key hub #2) |
| [`imep_gold.final`](tables/imep_gold/final.md) | 520M | **Denormalized consumption endpoint for email** — HR already joined |
| [`sharepoint_gold.pbi_db_interactions_metrics`](tables/sharepoint_gold/pbi_db_interactions_metrics.md) | 84M | SharePoint master interaction fact (views/visits/duration) |

Plus the cross-channel bridge:

| Table | Rows | Role |
|---|---|---|
| [`sharepoint_bronze.pages`](tables/sharepoint/pages.md) | 48K | Page inventory with `UBSGICTrackingID` — **the only place** cross-channel attribution happens |

---

## 7. ER Diagrams per Domain

| Diagram | Scope |
|---|---|
| [er_imep_bronze.md](diagrams/er_imep_bronze.md) | iMEP Bronze + HR neighborhood |
| [er_sharepoint_bronze.md](diagrams/er_sharepoint_bronze.md) | pages × pageviews × customevents × sites |
| [er_imep_gold.md](diagrams/er_imep_gold.md) | `final` + `tbl_pbi_*` tier structure |
| [er_sharepoint_gold.md](diagrams/er_sharepoint_gold.md) | `marketingPageId` FK chain (all metric tables) |
| [er_cross_channel.md](diagrams/er_cross_channel.md) | TrackingID bridge + employee bridge between domains |

---

## 8. Canonical Join Recipes

Ready-made SQL for the most common join patterns:

| Recipe | Purpose |
|---|---|
| **[join_strategy_contract.md](joins/join_strategy_contract.md)** | ⭐ Read this first — dos and don'ts |
| [imep_bronze_email_events.md](joins/imep_bronze_email_events.md) | 4-table Bronze chain: Mailing × Recipient × Event |
| [sharepoint_gold_to_pages.md](joins/sharepoint_gold_to_pages.md) | Gold metrics → TrackingID via `pageUUID` |
| [hr_enrichment.md](joins/hr_enrichment.md) | TNumber ↔ GPN ↔ Region/Division |
| [cross_channel_via_tracking_id.md](joins/cross_channel_via_tracking_id.md) | iMEP ↔ SharePoint via TrackingId Pack-Level (SEG1-2) |

---

## 9. Key Findings Everyone Must Know

These are the **hard constraints** against which every cross-channel analysis must be built.

### 9.1 The cross-channel link is dimensional, not factual

`tbl_email.TrackingId` ↔ `sharepoint_bronze.pages.UBSGICTrackingID` — **never** directly via engagement tables (`pageviews`, `tbl_analytics_link`). TrackingId is a dimension, not a fact key.

### 9.2 Only ~4% of pages have UBSGICTrackingID

1,949 out of 48,419 pages. That means ~96% of SharePoint interactions **cannot** be attributed to a pack. Dashboards must make this explicit.

### 9.3 TrackingId adoption has only been ramping since 2024/25

Only 986/73,930 mailings (1.3%) carry a TrackingId. Default dashboard time window: **from 2025 onwards**.

### 9.4 Cross-channel match lives at Pack level (SEG1-2), not per activity

An email and the matching intranet page of the same Pack typically launch on different dates (different SEG3), with different activity sequences (SEG4) and on different channels (SEG5). Matching on anything finer than SEG1-2 treats semantically related activities as unrelated. Full-string match yields 6/1677 hits (Jaccard 0.004); Pack-level match yields ~54 attributable Packs — the dashboard universe.

### 9.5 Write patterns: MERGE (Bronze) vs. Full Rebuild (Gold)

- **iMEP Bronze**: MERGE upsert. `tbl_analytics_link` is **truly incremental** (3-8K rows per run); `tbl_email` / `tbl_email_receiver_status` are full-table upserts (27-72M per run).
- **SharePoint Bronze**: `pages` via **MERGE daily snapshot replace**, `pageviews` via **Append WRITE** (7 bursts per run, API pagination).
- **iMEP Gold**: **Full Rebuild** — tables (including the 520M-row `final`) are completely destroyed and rewritten, not incrementally merged.

Refresh cadence is deliberately not documented on this page — we only see writes via `DESCRIBE HISTORY`, not the full job scheduler. Dashboards should be built resilient against whichever snapshot is current, rather than relying on specific time windows.

### 9.6 Email engagement skips Silver entirely

`imep_silver` exists only for events. For email engagement there is **no silver layer** — `imep_gold.final` is the direct Bronze → Gold consumption endpoint (denormalized, HR-enriched). Event data (`invitation`, `eventregistration`, `event`) does have a Silver layer.

### 9.7 Gold is classified into 4 tiers

Both gold schemas follow the same strict hierarchy:

- **Tier 0** — Atomic fact (largest tables): iMEP `final` 520M, SP `pbi_db_interactions_metrics` 84M
- **Tier 1** — Pre-aggregated timespans (rolling windows, date aggregates)
- **Tier 2** — Per-mailing / per-page summaries (`UniqueOpens`, `UniqueClicks`, engagement)
- **Tier 3** — Platform and reference dimensions (`tbl_pbi_platform_mailings`, `employeecontact`, Calendar, etc.)

Mental model: iMEP Gold is message-centric per-recipient; SharePoint Gold is page-centric analytics. TrackingId is the only conceptual bridge, and it lives at the dimension level (Tier 3).

### 9.8 Storage: Gold co-located, zero partitioning

All 114 Delta tables are External, spread across **3 ADLS accounts** (iMEP Bronze, SP Bronze, **shared Gold**). The shared-Gold design enables cross-channel joins inside Fabric/Spark without cross-account auth. **⚠️ No partitioning on any table** — the largest structural performance gap. Queries on `final` or `interactions_metrics` without a time filter are a full-scan risk.

---

## Sources

Genie sessions supporting this page: [Q1](sources.md#q1), [Q22](sources.md#q22), [Q24](sources.md#q24), [Q26](sources.md#q26), [Q27](sources.md#q27), [Q28](sources.md#q28), [Q29](sources.md#q29), [Q30](sources.md#q30). See [sources.md](sources.md) for the full index.
