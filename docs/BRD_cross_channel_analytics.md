# Business Requirements Document — Cross-Channel Communication Analytics

**Project:** Multi-Channel Communication Analytics (working title: *CammsView*)
**Version:** 0.1 — Draft
**Status:** In Review
**Date:** 2026-04-15
**Authors:** Founder Office
**Audience:** Business Analysts, Data Engineers, Dashboard Developers

---

## 1. Executive Summary

We are building an integrated analytical view across all planned communication activities (emails, intranet news, events, banners, SMS) by joining the activities planned in CPLAN with actual delivery telemetry from AppInsights and iMEP. The join runs through the system-generated `CammsTrackingID`, which every activity in every channel carries.

The output is a unified dashboard showing the funnel *planning → delivery → consumption → conversion* per communication pack, cluster and time range, and enabling statements about the effectiveness of individual campaigns.

## 2. Business Goals

| # | Goal | Metric |
|---|---|---|
| G1 | End-to-end transparency across all channels of a pack | 100% of CPLAN packs visible in the dashboard |
| G2 | Quantify per-campaign effectiveness (funnel) | Funnel rates email→page→event per pack |
| G3 | Audience analysis across HR dimensions | Views / UV per division, region, management level |
| G4 | Content performance for content owners | Ranking of pages by UV, avg TOS, bounce |
| G5 | Data foundation for subsequent channel onboardings (BAN, SMS) | Reusability of the model |

## 3. Scope

### 3.1 In Scope (MVP)

Three data sources, joined via `CammsTrackingID`:

1. **iMEP (Email)** — send / open / click events.
   Source: `imep_bronze.tbl_email_receiver_status` (Databricks / Unity Catalog).
   Channel abbreviation: `EMI`.

2. **SharePoint PageViews (intranet interactions)** — `sharepoint_bronze.pageviews` (views, visits, engagement; column `GICTrackingID`). Complementary: the historical pipeline from AppInsights `pageViews` telemetry via [`scripts/flatten_appinsights.py`](../scripts/flatten_appinsights.py) → `fact_page_view` / `agg_session`. To be clarified which of the two becomes the canonical source. Channel abbreviation: `INT`.

3. **SharePoint Pages / Page Inventory** — `sharepoint_bronze.pages` (page master + article metadata + `UBSGICTrackingID` + `UBSArticleDate`). Plus CPLAN for overarching pack/cluster/activity planning.

Supporting:
- **HR Snapshot Join** (GPN → division / region / management level) — already integrated in the PageView pipeline.
- **Dashboard** — [`dashboard/multi_channel.html`](../dashboard/multi_channel.html), DuckDB-WASM + Chart.js, single-file.

### 3.2 Out of Scope (Phase 1)

- Event registrations (`EVT`, iMEP) — coming in Phase 2 once MVP is stable.
- Banner (`BAN`), SMS — later phases.
- Real-time streaming (daily batch is sufficient).
- Predictive modelling / next-best-action — explicitly excluded.

## 4. Data Model

### 4.1 The central join key — `CammsTrackingID`

32 characters, 5 segments, `-`-separated:

```
QRREP-0000058-240709-0000060-EMI
  │       │       │       │     └── tracking_channel_abbr  (EMI / INT / EVT / BAN)
  │       │       │       └──────── tracking_activity_number
  │       │       └──────────────── tracking_pub_date (YYMMDD)
  │       └──────────────────────── tracking_pack_number
  └──────────────────────────────── tracking_cluster_id
  └───────┬───────┘
   tracking_pack_id (segment 1+2) — grain for dashboard aggregation
```

The ID is generated in CPLAN per activity and passed through on every channel. It is therefore the only reliable cross-channel join key.

**Note:** CPLAN source CSVs contain both `Tracking ID` and `Tacking ID` (typo). Both variants must be read in the ETL.

### 4.2 Layered Architecture (Unity Catalog) — revised post-Q16

```
Existing (read-only)                                  Our gold model
═══════════════════════════════════════════════       ═══════════════════════

iMEP Gold (5 tiers, confirmed):
  Tier 3 (Mailing × dim, incl. UniqueOpens/Clicks):
    imep_gold.tbl_pbi_engagement             ────►  gold.fact_cross_channel
    imep_gold.tbl_pbi_mailingreciever_region ────►        (pack-level aggregation)
    imep_gold.tbl_pbi_mailingreciever_division ──►
  Tier 4 (Master):
    imep_gold.tbl_pbi_platform_mailings      ────►  gold.dim_pack
    imep_gold.tbl_pbi_platform_events        ────►  gold.dim_pack
    imep_gold.tbl_pbi_deviceTypeall          ────►  (Device dimension)

SharePoint Gold (TBD — Q17 to find):
  sharepoint_gold.[pageview_aggregate?]      ────►  gold.fact_cross_channel
                                                    (intranet side)

Bronze only for enrichment we can't get from gold:
  imep_bronze.tbl_hr_employee                ────►  silver.dim_employee_temporal
  imep_bronze.tbl_hr_costcenter              ────►  (unless gold mapping fixes NULL-region)
  sharepoint_bronze.pages                    ────►  silver.dim_page (article metadata)
```

**Revised principle**: where gold already aggregates, **we do not re-aggregate** — we join gold directly. Silver stays minimal (only for data gold truly lacks). What gold lacks: employee bridge with temporal logic (`dim_employee_temporal`) and page-article metadata (`dim_page`).

### 4.3 Silver Facts — Schemas

**`silver.fact_email`** (1 row per link interaction / send event — follows iMEP Genie Pattern 2, see Appendix A)

| Column | Type | Source |
|---|---|---|
| email_event_id | STRING PK | `TBL_ANALYTICS_LINK.Id` or `TBL_EMAIL_RECEIVER_STATUS.Id` (send branch) |
| mailing_id | STRING | `TBL_EMAIL.Id` |
| mailing_title | STRING | `TBL_EMAIL.Title` |
| tracking_id, tracking_pack_id, … | STRING | split from `CammsTrackingID` (column tbd, see OP-04) |
| t_number | STRING (`t######`, lowercase) | `TBL_ANALYTICS_LINK.TNumber` / `TBL_EMAIL_RECEIVER_STATUS.TNumber` — **the only recipient key across the entire silver/gold layer**. The PageView side resolves GPN via `imep_bronze.tbl_hr_employee.WORKER_ID → T_NUMBER`; GPN is no longer carried after the silver build. |
| event | STRING (`sent` / `opened` / `clicked` / `bounced` / …) | derived from `TBL_EMAIL_RECEIVER_STATUS` + `TBL_ANALYTICS_LINK.linkTypeenum` |
| event_ts | TIMESTAMP | `CreationDate` (send: STATUS, click/open: ANALYTICS_LINK) |
| device_type | STRING | `Agent` + multi-device CTE → `Desktop & Mobile` / `Desktop Only` / `Mobile Only` |
| current_language | STRING | `TBL_ANALYTICS_LINK.CurrentLanguage` |
| timespan_h | INT | `(bigint(link.CreationDate) - bigint(status.CreationDate)) / 3600` |
| hr_org_unit, hr_division, hr_area, hr_region, hr_country, hr_town | STRING | `TBL_HR_EMPLOYEE` / `TBL_HR_COSTCENTER` / `TBL_HR_USER` |
| created_by | STRING | `TBL_EMAIL.CreatedBy` (resolved via `TBL_HR_EMPLOYEE.T_NUMBER`) |
| source_file | STRING | ETL |

**Important**: `IsActive = 1` is a mandatory filter everywhere. Open vs. click is `linkTypeenum`, not a separate status.

**`silver.fact_page_view`** — existing, see [README.md §Data Model](../README.md).

**`silver.dim_pack`** (1 row per communication pack)

| Column | Source |
|---|---|
| tracking_pack_id (PK) | CPLAN |
| cluster_id, cluster_name | CPLAN |
| pack_name, pack_theme, pack_topic, target_region, target_org | CPLAN |
| planned_channels (ARRAY<STRING>) | CPLAN activities |
| audience_size | CPLAN / iMEP |
| publish_date | CPLAN |

### 4.4 Gold — Cross-Channel Fact

One row per `tracking_pack_id`, columns per channel:

```sql
WITH email AS (
  SELECT tracking_pack_id,
         COUNT(*) FILTER (WHERE event='sent')    AS email_sent,
         COUNT(*) FILTER (WHERE event='opened')  AS email_opened,
         COUNT(*) FILTER (WHERE event='clicked') AS email_clicked
  FROM silver.fact_email GROUP BY 1
),
intranet AS (
  SELECT tracking_pack_id,
         COUNT(*)                AS page_views,
         COUNT(DISTINCT user_id) AS unique_readers,
         AVG(time_on_page_sec)   AS avg_time_on_page
  FROM silver.fact_page_view GROUP BY 1
)
SELECT p.*, e.*, i.*
FROM silver.dim_pack p
LEFT JOIN email e    USING (tracking_pack_id)
LEFT JOIN intranet i USING (tracking_pack_id);
```

## 5. Functional Requirements

### 5.1 ETL — `FR-ETL`

| # | Requirement |
|---|---|
| FR-ETL-01 | iMEP Bronze → Silver: split `CammsTrackingID` into 7 columns (reference logic: CPLAN `process_cplan.py` ~L559–579). |
| FR-ETL-02 | Read both column variants `Tracking ID` and `Tacking ID`. |
| FR-ETL-03 | Delta/upsert load based on file hash (model: `flatten_appinsights.py`, `processed_files` manifest). |
| FR-ETL-04 | PageView pipeline: `CammsTrackingID` must be exposed as a first-class column in `fact_page_view` (today inside `customDimensions.CustomProps`). |
| FR-ETL-05 | HR join: temporal, monthly, GPN 8-digit zero-padded, fallback to next-younger snapshot (existing). |
| FR-ETL-06 | Gold materialisation nightly (batch). |
| FR-ETL-07 | Orphan handling: page views without `CammsTrackingID` are kept (aggregated into `tracking_pack_id = 'UNATTRIBUTED'`). |

### 5.2 Dashboard — `FR-DASH`

Single-file HTML (DuckDB-WASM + Chart.js), see Corporate Branding Guidelines in [CLAUDE.md](../../CLAUDE.md).

| # | View | Content |
|---|---|---|
| FR-DASH-01 | **Overview** | KPI cards: # packs, emails sent, page views, unique readers, avg funnel rate |
| FR-DASH-02 | **Pack Explorer** | Table of all packs with channel KPIs, filterable by cluster / time range / theme / region |
| FR-DASH-03 | **Cross-Channel Funnel** | Per pack: sent → opened → clicked → page view → (later: event registered) with conversion rates |
| FR-DASH-04 | **Audience Breakdown** | Page views by HR division, region, management level (bar + heatmap) |
| FR-DASH-05 | **Content Performance** | Top pages by UV, avg TOS, bounce rate; filtered by content owner / theme |
| FR-DASH-06 | **Time Series** | Weekly trend across channels, stacked |
| FR-DASH-07 | **Filters (global)** | Time range, cluster, channel, region, division |

### 5.3 Non-Functional — `NFR`

| # | Requirement |
|---|---|
| NFR-01 | Refresh: nightly batch, target < 30 min end-to-end. |
| NFR-02 | Dashboard load time < 3s over 12 months of data. |
| NFR-03 | No brand- or company-specific abbreviations in code (prefix `--corp-*` etc., no corporate-specific abbreviations). |
| NFR-04 | Corporate color palette mandatory (see [CLAUDE.md](../../CLAUDE.md)). |
| NFR-05 | PII (GPN, email) is not exposed in gold — only `user_id` and HR dimensions (see [pii_cleanup_pending](../../../.claude/projects/-Users-micha-Documents-Arbeit-Databricks/memory/pii_cleanup_pending.md)). |
| NFR-06 | All timestamps in CET; UTC conversion in the ETL. |

## 6. Data Flow (High-Level)

```
CPLAN (SharePoint CSV)  ──►  silver.dim_pack / dim_activity
iMEP Bronze             ──►  silver.fact_email
AppInsights pageViews   ──►  silver.fact_page_view (existing pipeline)
HR History              ──►  silver.dim_employee_temporal
pbi_db_website_*        ──►  silver.dim_page
                                         │
                                         ▼
                             gold.fact_cross_channel
                                         │
                                         ▼
                             dashboard/multi_channel.html
```

## 7. Roles & Deliverables

| Role | Deliverable |
|---|---|
| BA | User stories per dashboard view, acceptance criteria, validation of KPI definitions |
| Data Engineer | Silver/Gold DLT pipelines, iMEP Bronze→Silver ETL, unit tests |
| Dashboard Dev | Wire `multi_channel.html` to gold, corporate styling |
| QA | Reconciliation CPLAN ↔ iMEP ↔ AppInsights per pack (row counts, funnel plausibility) |

## 8. Milestones (indicative)

| Phase | Content | Duration |
|---|---|---|
| P0 | iMEP schema discovery, CammsTrackingID first-class in PageView | 1 W |
| P1 | `silver.fact_email` + `silver.dim_pack` | 2 W |
| P2 | `gold.fact_cross_channel` + dashboard MVP (Overview + Pack Explorer) | 2 W |
| P3 | Audience Breakdown + Content Performance + Funnel View | 2 W |
| P4 | Event channel (`EVT`) onboarding | 2 W |

---

## 9. Open Points / Items to Clarify

The following points are to be clarified **before** implementation starts, with the respective stakeholders. Without these answers, no complete end-to-end documentation can be produced.

### 9.1 iMEP (Email Channel)

> **Update 2026-04-16 (v2)**: Genie Q1/Q2/Q3 answered — see [memory/imep_genie_findings_q1_q2_q3.md](../../../.claude/projects/-Users-micha-Documents-Arbeit-Databricks/memory/imep_genie_findings_q1_q2_q3.md). **Column is called `TrackingId` (not `CammsTrackingID`)** and lives on `tbl_email` (mailing level). **GPN does NOT exist in the HR tables** — new critical OP-07e.

- ~~**OP-01**~~ ✅ Schema known — `TBL_EMAIL_RECEIVER_STATUS` (recipient status), `TBL_ANALYTICS_LINK` (open/click), `TBL_EMAIL` (mailing master, **incl. `TrackingId`**), `TBL_EMAIL_LINKS` (template). See Appendix A and Genie findings.
- **OP-02** Complete list of values in `TBL_EMAIL_RECEIVER_STATUS` (send/bounce/unsubscribe) and `TBL_ANALYTICS_LINK.linkTypeenum` (`OPEN`, `CLICK`, …) — confirmation with BA.
- ~~**OP-03**~~ ✅ Unique dedup: `RecipientID + EmailId`. Multi-device via CTE `HAVING COUNT(DISTINCT Agent) > 1`.
- ~~**OP-04**~~ ✅ Tracking ID lives on `imep_bronze.tbl_email.TrackingId` (mailing level). Cross-channel join via `tbl_analytics_link.EmailId → tbl_email.Id → tbl_email.TrackingId`. Naming mapping: `TrackingId` (iMEP) ↔ `GICTrackingID` (SharePoint, with case variants) ↔ `tracking_id` (CPLAN).
- **OP-05** Audience size per pack: does this come from iMEP (distribution list size) or CPLAN (`pack.target_audience_size`)?
- ~~**OP-06**~~ ✅ Historisation: `tbl_pbi_platform_mailings` from 2020-11-18 (5+ years, 73.8k rows), `tbl_pbi_platform_events` from 2013-10-10 (12+ years, 84k rows). Both refreshed daily. Bronze tables to be checked separately, but gold coverage shows: sufficiently long history.
- ~~**OP-07**~~ ✅ Recipient runs via `TNumber` (format `t100200`) — HR join in iMEP via `TBL_HR_EMPLOYEES.T_NUMBER` (**case warning**: `tbl_hr_user.UbsId` is uppercase `T594687`, `T_NUMBER` is lowercase — normalisation mandatory).
- **OP-07b** `TBL_HR_COSTCENTER.ORGANIZATIONAL_UNIT` join: uniqueness (1:1) and historisation (org changes)?
- **OP-07c** `TBL_EMAIL.CreatedBy` — is this required in the dashboard as a filter / dimension (creator reporting)?
- ~~**OP-07d**~~ ✅ **Bridge found**: `imep_bronze.tbl_hr_employee` contains **both** `T_NUMBER` (lowercase, `t######`) **and** `WORKER_ID` (= GPN, 8-digit). Pure LEFT JOIN, no string transformation required.
- ~~**OP-07e**~~ ✅ Resolved by the OP-07d finding. **Consequence**: in the silver ETL for `fact_page_view`, immediately resolve the GPN via `WORKER_ID` to `T_NUMBER`. After that, `t_number` is the only recipient key across the entire silver/gold layer.
- ~~**OP-07f**~~ ✅ **iMEP Gold fully evaluated** (Q16 finding, see [memory/imep_gold_full_inventory.md](../../../.claude/projects/-Users-micha-Documents-Arbeit-Databricks/memory/imep_gold_full_inventory.md)):
  - **Gold does contain engagement aggregates after all.** The Q1b search was too narrow — only `tbl_pbi_platform_mailings` was inspected, not the full schema.
  - **5-tier architecture**: atomic fact (`Final`, 525M), rolling-timespan aggs (Tier 2), **mailing-level engagement summaries** (Tier 3 — `tbl_pbi_engagement`, `tbl_pbi_mailingreciever_region`, `_division`), platform metadata (Tier 4), reference (Tier 5).
  - **`tbl_pbi_engagement`** (1.38M rows) — Mailing × Link/Component with `UniqueOpens`, `UniqueClicks`, `Count`.
  - **`tbl_pbi_mailingreciever_region`** (697K) — Mailing × Region/Country/Town.
  - **`tbl_pbi_mailingreciever_division`** (290K) — Mailing × Business Division.
  - **Consequence**: `silver.fact_email` is **not built from bronze**, but is a direct SELECT from Tier-3 gold. No multi-device CTE, no HR join — everything pre-aggregated.
  - **Aggregates have NO historisation** (rebuild on pipeline run). For long-term trend analysis we must persist snapshots.
- **OP-07g** (new, **critical**) **66% NULL-region in gold** — `184M of 278M recipients` have no region assignment due to a failing `tbl_hr_employee.ORGANIZATIONAL_UNIT ↔ tbl_hr_costcenter` JOIN. Per Genie, this is *the* dominant data-quality blocker. Dashboard must communicate this transparently. Root-cause analysis needed: are the affected employees ex-staff with stale cost centres? Is there a cost-centre history we could use?

### 9.2 TrackingId (formerly CammsTrackingID)

- **OP-08** Are emails ever sent in iMEP *without* a `TrackingId` (transactional)? Filter rule? (`SELECT COUNT(*) WHERE TrackingId IS NULL`).
- **OP-09** Case sensitivity: SharePoint column names appear in several prefix and case variants of `GICTrackingID` — are the *values* also case-mixed? Normalisation rule for the join.
- **OP-10** Are there historical packs that do not match the 5-segment 32-char schema? The SharePoint sample `12345-12345123-12345-12345-1` hints at deviating formats in old data. Backward-compatibility strategy.

### 9.3 CPLAN Integration

- **OP-11** Is CPLAN mirrored into Databricks (Unity Catalog), or does it need to be loaded separately from the CPLAN repo (`/CPLAN/`)?
- **OP-12** Freshness of CPLAN data in Databricks — batch frequency?
- **OP-13** Definition of *pack name / theme / topic* — which CPLAN fields count as master?
- **OP-14** Cluster hierarchy: are there cluster groups / cluster owners needed in the dashboard?

### 9.4 AppInsights / PageViews

- **OP-15** **Partially clarified**: `UBSGICTrackingID` in `sharepoint_bronze.pages` only for news/event articles. Per Q17 confirmed: `sharepoint_gold` provides PageView aggregates (20 tables, 270M rows) — whether dimensioned per TrackingId or only per page is clarified by Q17b. Coverage analysis Q15/Q15b remains relevant.
- **OP-15b** (new) **Use `sharepoint_gold` contents**: primary PageView fact tables are `pbi_db_interactions_metrics` (84M), `pbi_db_pageviewed_metric` (84M), `pbi_db_pagevisited_metric` (81M). To be clarified via Q17b: join gold metric directly by TrackingId or via page_id → `sharepoint_bronze.pages.UBSGICTrackingID`.
- **OP-15c** (new) **CPLAN directly in Databricks**: `sharepoint_cplan` contains `trackingcluster_bronze` (17 clusters), `communicationspacks_bronze` (280 packs), `internalcommunicationactivities_bronze` (4,349 activities). This replaces the external CPLAN repo as the source for `silver.dim_pack`. To be clarified via Q17e: FK relationships between these tables and the link to iMEP gold.
- **OP-15d** (new, governance) **Request `pbi_gold` access**: 60 tables, presumably the central semantic model. Currently inaccessible. File an access request with the catalog admin (Q17f).
- **OP-16** Attribution window: how long after email send can a PageView still be attributed to the campaign when `CammsTrackingID` is missing (orphan)?
- **OP-17** Banner (`BAN`): how are banner impressions delivered in AppInsights? Own event type?

### 9.5 HR / Organisation

- **OP-18** HR snapshot frequency and availability in Databricks (today: parquet from SearchAnalytics).
- **OP-19** Management-level taxonomy: which values are permitted in the dashboard filter?
- **OP-20** Externals / non-employees: how are PageViews without a GPN handled?

### 9.6 Dashboard / UX

- **OP-21** Primary user persona: content owner, campaign manager, communications lead? Influences default views and filters.
- **OP-22** Permissions: do all users see all packs, or row-level security by content owner / division?
- **OP-23** Drill-down depth: should a detail view with session listing exist per pack?
- **OP-24** Export requirements: XLSX export per view needed? (If yes: corporate XLSX format per [CLAUDE.md](../../CLAUDE.md).)
- **OP-25** Is mobile support required?

### 9.7 Governance / Compliance

- **OP-26** PII clearance: is `user_id` (anonymous browser ID) compliant, or are additional aggregation thresholds needed (k-anonymity, min 5 users per bucket)?
- **OP-27** Retention in the gold layer: how long may aggregated campaign KPIs be retained?
- **OP-28** External recipients: may their interactions be analysed?
- **OP-29** Is an audit log for dashboard access required?

### 9.8 Operations

- **OP-30** Monitoring & alerting for the ETL jobs — tooling (Databricks Workflows, external tools)?
- **OP-31** On-call and incident-response process.
- **OP-32** CI/CD for DLT pipelines (Bundles / Repos).
- **OP-33** Cost cap for Databricks compute.

---

**Next step:** walk through open points OP-01 to OP-33 in a kickoff with BA, Data Engineer and Dashboard Dev. Then re-issue as BRD v1.0.

---

## Appendix A — iMEP Genie Code Reference Patterns

Source: Databricks Genie notebook (cells 6/7/24/25). Screenshots: `Bilder/16. April 2026/IMG_7331..7334.jpeg`.

### Table overview

| Table | Content | Grain |
|---|---|---|
| `TBL_EMAIL_RECEIVER_STATUS` | Recipient status per email | 1 row per recipient per mailing |
| `TBL_EMAIL` | Mailing master (title, status, CreatedBy) | 1 row per mailing |
| `TBL_ANALYTICS_LINK` | Runtime click/open events (agent, linkTypeenum, CurrentLanguage) | 1 row per interaction |
| `TBL_EMAIL_LINKS` | Template links (design-time) | 1 row per link in the template |
| `TBL_EMAIL_COMPONENTS` | Top-level components (name, order) | |
| `TBL_EMAIL_COMPONENT_ELEMENTS` | Elements (KeyName, Name) | |
| `TBL_ELEMENT_VALUES` | Link ↔ element bridge | |
| `TBL_HR_EMPLOYEE` | HR master data via T_NUMBER | |
| `TBL_HR_COSTCENTER` | Org-unit → division/area/region/country | |
| `TBL_HR_USER` | Town | |

### Pattern 1 — KPI Summary (Cells 6, 24)

Aggregates recipient counts per mailing by delivery status, language, creator.

```sql
SELECT ...
FROM TBL_EMAIL_RECEIVER_STATUS a
LEFT JOIN TBL_ANALYTICS_LINK c ON a.Id = c.EmailReceiverStatusId   -- CurrentLanguage
LEFT JOIN TBL_EMAIL b          ON a.EmailId = b.Id                 -- Title, Status
LEFT JOIN TBL_HR_EMPLOYEE hr   ON b.CreatedBy = hr.T_NUMBER        -- Creator
WHERE a.IsActive = 1
```

### Pattern 2 — Final Fact Table (Cell 7)

Denormalised fact table with geo, org, device, timespan. **Template for `silver.fact_email`.**

```sql
WITH mdclicks AS (
  SELECT Id, Agent, EmailId, TNumber AS RecipientID,
         'Desktop & Mobile' AS NewDeviceType,
         CASE WHEN linkTypeenum = 'OPEN' THEN 'Unique Opens'
              ELSE 'Unique Clicks' END AS Engagementtype
  FROM TBL_ANALYTICS_LINK
  WHERE IsActive = 1 AND Agent IN ('desktop','mobile') AND linkTypeenum != 'OPEN'
),
mdlinks AS (
  SELECT Id, RecipientID, EmailId, NewDeviceType, Engagementtype
  FROM mdclicks
  WHERE RecipientID IN (
    SELECT RecipientID FROM mdclicks
    GROUP BY EmailId, RecipientID
    HAVING COUNT(DISTINCT Agent) > 1
  )
)
SELECT ...
FROM TBL_ANALYTICS_LINK a
LEFT JOIN TBL_EMAIL_RECEIVER_STATUS c ON a.EmailReceiverStatusId = c.Id      -- SentDateTime
LEFT JOIN TBL_HR_EMPLOYEE hr          ON a.TNumber = hr.T_NUMBER             -- org unit
LEFT JOIN TBL_HR_COSTCENTER cc        ON hr.ORGANIZATIONAL_UNIT = cc.ORGANIZATIONAL_UNIT
LEFT JOIN TBL_HR_USER u               ON hr.T_NUMBER = u.UbsId               -- town
LEFT JOIN mdlinks hh                  ON a.Id = hh.Id                        -- multi-device
WHERE c.EmailId IS NOT NULL AND a.EmailId IS NOT NULL AND a.IsActive = 1
```

- **Timespan bucket**: `(bigint(a.CreationDate) - bigint(c.CreationDate)) / 3600` (hours send → click).
- **FinalDeviceType**: COALESCE of the CTE result with raw `Agent`, suffix `" Only"` for single-device.

### Pattern 3 — Engagement Detail (Cell 25)

UNION with template metadata so that links with 0 clicks appear in the reporting.

```sql
SELECT x.Url, b.EmailId AS MailingId,
       x.linkLabel,
       b.KeyName AS SourceComponent,
       b.Name    AS SourceComponentName,
       a.KeyName AS SourceElement,
       a.Name    AS SourceElementName,
       b.Order   AS ComponentOrder
FROM TBL_EMAIL_LINKS x
LEFT JOIN TBL_ELEMENT_VALUES y           ON y.Id = x.ElementValueId
LEFT JOIN TBL_EMAIL_COMPONENT_ELEMENTS a ON a.Id = y.ElementId
LEFT JOIN TBL_EMAIL_COMPONENTS b         ON b.Id = a.ComponentId
```

### Consequences for our model

- `silver.fact_email` is based on **Pattern 2** — grain = 1 row per link interaction (+ a separate send branch from `TBL_EMAIL_RECEIVER_STATUS` for `event=sent`).
- `event` is **not** read from a single status field, but combined from two sources:
  - `TBL_EMAIL_RECEIVER_STATUS` → `sent`, `bounced`, `unsubscribed`
  - `TBL_ANALYTICS_LINK.linkTypeenum` → `opened` (`OPEN`) vs. `clicked` (everything else)
- HR join in iMEP via `TNumber` (format `t100200`). **TNumber ≠ GPN** — GPN (`00100200`) is used in AppInsights/PageView. For cross-source joins at recipient level an HR bridge is required (see OP-07d).
- Take the multi-device detection (CTE) along — relevant for engagement analysis.
- `CurrentLanguage` is available — language dimension possible in the dashboard.
