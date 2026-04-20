# Genie Questions for Clarifying Open iMEP Points

Targeted prompts for Databricks Genie, used to close open technical points from the [BRD](BRD_cross_channel_analytics.md) §9 **before** we implement `silver.fact_email` and the cross-channel joins.

Convention:
- **OP-XX** → reference to an Open Point in the BRD.
- Prompts are written in **English** (Genie delivers more reliable results against English table names).
- Every question comes with an expected output and a possible follow-up.

---

## A — Schema & Tracking-ID Location (highest priority)

> **Status 2026-04-16**: Q1, Q2, Q3 answered — see [memory/imep_genie_findings_q1_q2_q3.md](../../../.claude/projects/-Users-micha-Documents-Arbeit-Databricks/memory/imep_genie_findings_q1_q2_q3.md). Follow-ups Q1b and Q3a have been newly added.

### ~~Q1~~ ✅ Where does `CammsTrackingID` live in iMEP? **(OP-04 — resolved)**

> *Show me all columns in `TBL_EMAIL`, `TBL_EMAIL_LINKS`, `TBL_ANALYTICS_LINK` and `TBL_EMAIL_RECEIVER_STATUS` whose name contains "tracking", "camms", "cpid" or "cplan" (case-insensitive). Include the data type and a non-null sample value for each column.*

→ **Answer**: column is called `TrackingId` (not `CammsTrackingID`), lives on `imep_bronze.tbl_email` (mailing level), example `QRREP-0000058-240709-0000060-EMI`. Cross-channel join via `tbl_analytics_link.EmailId → tbl_email.Id → tbl_email.TrackingId`. SharePoint uses `GICTrackingID` (with case variants), CPLAN uses `tracking_id`.

### ~~Q1b~~ ✅ Evaluate iMEP Gold layer **(OP-07f — resolved)**

> *Show full schema and a 5-row sample of `imep_gold.tbl_pbi_platform_mailings` and `imep_gold.tbl_pbi_platform_events`. For each table, list which iMEP bronze tables / events / aggregations they appear to combine. Are these tables refreshed regularly (check max CreationDate / load timestamp)?*

→ **Answer**: Gold is a **mailing/event master with content and registration metrics**, **not** a send/open/click aggregate. Consequence: `silver.fact_email` still built from Bronze, but `silver.dim_pack` directly from Gold (`tbl_pbi_platform_mailings` + `tbl_pbi_platform_events` UNION). Event phase can be pulled forward (registration count available). Both tables refreshed daily. Sentinel value `2124` for open-ended events. See [memory/imep_gold_layer_analysis.md](../../../.claude/projects/-Users-micha-Documents-Arbeit-Databricks/memory/imep_gold_layer_analysis.md).

### ~~Q2~~ ✅ Full schema of core tables **(OP-01 — resolved)**

> *List every column with data type and nullability for these four tables: `TBL_EMAIL`, `TBL_EMAIL_RECEIVER_STATUS`, `TBL_ANALYTICS_LINK`, `TBL_EMAIL_LINKS`. For each column, also show one non-null sample value.*

→ **Answer**: 111 columns total, all nullable. `tbl_email` 54 cols (incl. `TrackingId`, `CreatedBy`, `Subject`, `EmailSendingStatus`); `tbl_email_receiver_status` with `Receiver`, `TNumber`, `Status`, `LogStatus`, `EmailLanguage`; `tbl_analytics_link` 22 cols with `Agent`, `LinkTypeEnum`, `CurrentLanguage`, `EmailReceiverStatusId`; `tbl_email_links` 10 cols (lean, template definitions).

### ~~Q3~~ ❌ HR bridge between TNumber and GPN **(OP-07d — hypothesis disproved)**

> *Inspect `TBL_HR_EMPLOYEE` and `TBL_HR_USER`: list all columns that look like employee identifiers (names containing `t_number`, `tnumber`, `gpn`, `personnel`, `staff`, `emp_id`, `worker`, `abacus`, `websso`, `id`). For each, show data type, sample value, and whether nulls exist. Also check whether any single table contains BOTH a T-number column and a GPN column.*

→ **Answer**: **GPN column does not exist in HR.** `tbl_hr_employees.T_NUMBER` (lowercase `t001108`) and `tbl_hr_user.UbsId` (uppercase `T594687`) are both T-Numbers. Other identifiers in HR: `WORKER_ID`, `ABACUS_ID`, `ALTERNATE_WORKER_ID`, `WEBSSO`, `UUNAME`, `PersonalNumber`. No bridge to GPN. → New question Q3a.

### ~~Q3a~~ ✅ What IS the GPN in AppInsights? **(OP-07e — resolved via Q3b finding)**

Hypothesis A: GPN is a string transformation of the T-Number (`t001108` → `00001108`). Hypothesis B: GPN comes from a different system (Active Directory, WebSSO).

> *Take 10 distinct non-null `customDimensions.GPN` values from our AppInsights `pageViews` (or `fact_page_view` if exposed). For each: try to find a matching employee in `tbl_hr_employees` by:
> a) replacing leading zeros with 't' (e.g. `00001108` → `t001108`)
> b) matching against `WORKER_ID`
> c) matching against `ABACUS_ID`
> d) matching against `ALTERNATE_WORKER_ID`
> e) matching against `WEBSSO`
> Report which strategy yields a hit (and how many of the 10 match).*

→ Decides whether we need a pure string transformation (cheap) or have to source an external bridge source (expensive).

### ~~Q3b~~ ✅ Extended HR search for GPN column **(OP-07e — resolved)**

> **Answer**: `imep_bronze.tbl_hr_employee.WORKER_ID` is the GPN. The same table also carries `T_NUMBER`. The bridge is a simple LEFT JOIN, no external source needed. Genie's initial search even listed `WORKER_ID` as "Primary worker identifier" — but did not connect it to GPN.

**Original question (for documentation):**

> **Domain knowledge**: GPN and TNumber have a confirmed 1:1 relationship in HR. If our initial Q3 hits did not show a GPN column, the search was too narrow.

> **Important**: return only metadata and pattern counts — NO sample values (avoid PII).

> *Three-step deep search — return only schema metadata and aggregate counts, no individual values:
>
> 1. List ALL tables in any schema starting with `tbl_hr_` or containing the word `employee`, `person`, `worker`, `staff`, `identity`, `directory`. Show table name, column count, row count.
>
> 2. For `tbl_hr_employees`: list all 114 column names and data types (DESCRIBE output). Then, for each STRING column, return only this aggregate: `count_matching_8digits` = number of rows where the column value matches regex `^[0-9]{8}$`. Do NOT return any actual values. Columns with a high `count_matching_8digits` are structurally GPN candidates.
>
> 3. Search column names across all HR tables for ANY of these tokens (case-insensitive): `gpn`, `global`, `g_p_n`, `personnel_no`, `personnel_number`, `pid`, `wmid`, `master_id`, `master_no`, `corp_id`, `emp_global`, `enterprise_id`. For each hit, return only `table.column`, data type, null count, total row count — no sample values.*

→ If this finds the GPN column: bridge is present in HR, OP-07e closed. If not: source externally (AD/WebSSO).

---

## B — Status and Event Values

### Q4 — Which status values exist? **(OP-02)**

> *For `TBL_EMAIL_RECEIVER_STATUS`: return the distinct values of every status-like or enum-like column (e.g. `Status`, `DeliveryStatus`, `BounceReason`), with row counts per value. Do the same for `TBL_ANALYTICS_LINK.linkTypeenum` and `TBL_ANALYTICS_LINK.Agent`.*

→ Provides funnel-event mapping (sent/bounced/opened/clicked/unsubscribed).

### Q5 — Open vs. Click dedup check **(OP-03)**

> *In `TBL_ANALYTICS_LINK`, for a sample of 10 recent mailings (top 10 `EmailId` by row count), compute:
> - total rows
> - distinct `(EmailId, TNumber)` pairs where `linkTypeenum = 'OPEN'`
> - distinct `(EmailId, TNumber)` pairs where `linkTypeenum != 'OPEN'`
> - max, min, avg number of rows per `(EmailId, TNumber, linkTypeenum)` triple.*

→ Confirms that unique-open/unique-click via `(EmailId, TNumber)` dedup is correct and reveals multi-event behaviour.

---

## C — Data Scope and Quality

### Q6 — Temporal coverage **(OP-06)**

> *For each of `TBL_EMAIL`, `TBL_EMAIL_RECEIVER_STATUS`, `TBL_ANALYTICS_LINK`: return `MIN(CreationDate)`, `MAX(CreationDate)`, total row count, and row count per calendar year.*

→ Retention / historisation limits.

### Q7 — Audience size per mailing **(OP-05)**

> *For the 20 most recent mailings (`TBL_EMAIL` by `CreationDate` desc), show:
> - `Id`, `Title`, `CreationDate`, `CreatedBy`
> - number of recipients in `TBL_EMAIL_RECEIVER_STATUS` (= audience size)
> - number of distinct `TNumber` values that had `linkTypeenum = 'OPEN'` (= unique opens)
> - number of distinct `TNumber` values that had any click event (= unique clicks).*

→ Confirms that audience size can be derived directly from iMEP (no CPLAN field needed).

### Q8 — Transactional mails without campaign tracking **(OP-08)**

> *Using the column identified in Q1 as the CammsTrackingID location, count how many rows in the relevant table have NULL / empty / malformed CammsTrackingID values. Sample 10 such rows and show what type of email they represent (show `Title` / `CreatedBy` / `CreationDate`).*

→ Filter rule for the Silver build (do transactional mails need to be excluded?).

### Q9 — Tracking-ID format conformance **(OP-09, OP-10)**

> *Using the column identified in Q1, return:
> - count of values matching the regex `^[A-Z0-9]{5}-[0-9]{7}-[0-9]{6}-[0-9]{7}-[A-Z]{3}$`
> - count of values NOT matching (non-conforming)
> - sample 10 non-conforming values
> - are any values stored in different cases (mix of upper/lower)?*

→ Validation of 32-char 5-segment format and case sensitivity.

---

## D — Org & HR

### Q10 — Org-unit mapping uniqueness **(OP-07b)**

> *In `TBL_HR_COSTCENTER`: group by `ORGANIZATIONAL_UNIT` and count rows. Flag any `ORGANIZATIONAL_UNIT` with more than 1 row. For those, show the conflicting values in the business-division / area / region / country columns.*

→ Checks whether the join `hr.ORGANIZATIONAL_UNIT = cc.ORGANIZATIONAL_UNIT` in Genie Pattern 2 is truly 1:1.

### Q11 — HR historisation in iMEP **(OP-07b)**

> *Does `TBL_HR_EMPLOYEE` have a validity window (columns like `ValidFrom`, `ValidTo`, `EffectiveDate`)? If yes, show for a sample of 5 employees how their `ORGANIZATIONAL_UNIT` has changed over time. If no, is there only a current snapshot?*

→ Decides whether we join temporally (as with PageView) or whether a current snapshot is sufficient.

### Q12 — Creator dimension **(OP-07c)**

> *For all distinct `CreatedBy` values in `TBL_EMAIL` from the last 12 months, resolve via `TBL_HR_EMPLOYEE.T_NUMBER` to a full name and show the top 20 creators by number of mailings.*

→ Qualifies the "Created By" filter in the dashboard as a meaningful dimension.

---

## E — Multi-Device & Engagement

### Q13 — Multi-device volume **(Design review Pattern 2)**

> *Out of all recipients in `TBL_ANALYTICS_LINK` (filter `IsActive = 1`, `linkTypeenum != 'OPEN'`, `Agent IN ('desktop','mobile')`), what share has engaged via BOTH desktop AND mobile for the same `EmailId`? Break down by mailing for the 20 largest mailings.*

→ Shows whether the multi-device CTE is really relevant for our dashboard (frequency in practice).

### Q14 — Engagement template coverage **(Design review Pattern 3)**

> *For the 10 most recent mailings: what share of `TBL_EMAIL_LINKS` entries received at least one click at runtime (via `TBL_ANALYTICS_LINK`)? Show per mailing: total template links, clicked links, unclicked links, click rate.*

→ Justifies the template UNION step (Pattern 3): the effort only pays off if "dead links" are actually relevant.

---

## F — Integration with PageView (AppInsights)

### Q15 — TrackingID coverage in SharePoint Pages Inventory **(OP-15, refined)**

> **Domain context**: `sharepoint_bronze.pages` is the page inventory table. `UBSGICTrackingID` is populated **only for news and event pages (articles)**. `UBSArticleDate` is the temporal anchor.

> *Two-step coverage analysis on `sharepoint_bronze.pages`:*
>
> *1. **Overall coverage**: Show total row count, count where `UBSGICTrackingID IS NOT NULL`, count where `UBSArticleDate IS NOT NULL`, and count where both are populated. Express each as percentage of total.*
>
> *2. **Coverage trend over time**: Group rows by `DATE_TRUNC('month', UBSArticleDate)` for the last 36 months. For each month show: total articles (rows with non-null `UBSArticleDate`), articles with `UBSGICTrackingID`, and the coverage percentage. Also flag the first month where coverage exceeds 80% — that's the realistic start date for cross-channel funnel reporting.*

→ Defines the **realistic value claim** of the cross-channel model. If coverage is only reliable from, say, 2024 onwards, the dashboard should reflect that transparently (default time range, coverage note).

### Q15b — TrackingID coverage in `sharepoint_bronze.pageviews` **(follow-up)**

> **Architecture note**: `sharepoint_bronze.pageviews` is the live interactions table (views/visits) and itself has a column `GICTrackingID` (without `UBS` prefix — inconsistent with the inventory's `UBSGICTrackingID`). `sharepoint_bronze.pages` is the inventory.

> *In `sharepoint_bronze.pageviews`: join to `sharepoint_bronze.pages` via `page_id` (or similar FK). For the last 12 months, per month, show:*
> - *Total page views*
> - *Page views on article pages (joined page has `UBSArticleDate IS NOT NULL`)*
> - *Page views with populated `pageviews.GICTrackingID`*
> - *Page views where `pages.UBSGICTrackingID = pageviews.GICTrackingID` (consistency check between the two tables)*
> - *Coverage % (TrackingID-tracked views / article views)*

→ Three findings at once: (1) traffic-weighted coverage (more decisive than inventory coverage, because the dashboard aggregates views); (2) consistency check whether the TrackingID in `pageviews` matches the one in `pages`; (3) validation that the historical `pageviews_*` variants are irrelevant.

### Q16 — Attribution lag between email and PageView **(OP-16)**

> *For the 20 largest recent mailings, compute the time between Email-Send (`TBL_EMAIL_RECEIVER_STATUS.CreationDate`) and the earliest matching PageView for the same `CammsTrackingID`. Return: p50, p90, p99 in hours. Only consider PageViews within the first 30 days after send.*

→ Yields default attribution window (most likely 7 or 14 days).

---

## G — Banner & Event (Phase 2 Preview)

### Q17 — Event-channel (`EVT`) schema **(Phase 2)**

> *List the iMEP bronze tables that relate to event registrations (names containing "event", "registration", "attendee"). Show full schema plus one sample row per table. Does the same CammsTrackingID mechanism apply?*

### Q18 — Banner telemetry (`BAN`) **(Phase 3)**

> *Does AppInsights `pageViews` contain any rows where `customDimensions.CustomProps.CammsTrackingID` ends with `-BAN`? Show count per month for the last 6 months and sample 5 rows.*

---

## H — Sanity check (at the end)

### Q19 — End-to-end join test for 1 pack

> *Pick the `tracking_pack_id` with the highest recipient count in the last 90 days. For that pack, produce one row with:
> - pack_id, cluster_id, publish_date
> - emails sent, opened, clicked (from iMEP)
> - page views, unique readers, avg time-on-page (from AppInsights / fact_page_view)
> - creator full name (from HR).*

→ Proof-of-concept for the Gold cross-channel query, against real data.

### Q20 — Row-count reconciliation

> *For the last 30 days, compare per `tracking_pack_id`:
> - sum of sends in `TBL_EMAIL_RECEIVER_STATUS`
> - sum of clicks in `TBL_ANALYTICS_LINK`
> - sum of page views with matching `tracking_pack_id`.
> Flag any pack where page views exceed clicks by more than 2x, or where sends exist but no telemetry.*

→ Data-quality gate before dashboard wiring.

---

## I — Gold-First Viability (Q16–Q20)

Background: per the user, Gold **necessarily** contains engagement aggregates (sends/opens/clicks in iMEP, pageviews in SharePoint) — otherwise the existing Power BI semantic model could not display them at all. Our Q1b search against `tbl_pbi_platform_mailings` was too narrow. Goal of this section: **find the gold tables actually consumed**, in order to build `gold.fact_cross_channel` as a pure Gold JOIN (no Bronze ETL).

### ~~Q16~~ ✅ All tables in `imep_gold` **(resolved 2026-04-17)**

> *List ALL tables AND views in the `imep_gold` schema …*

→ **Answer**: 5-tier architecture found. Tier 1 = atomic `Final` (525M). Tier 2 = rolling-timespan aggs (`tbl_pbi_date*`, `_divarea*`, `_regcntry*`, `_deviceTypeall`) — pattern `EngagementType + Count`, no separate Open/Click KPIs. **Tier 3 = engagement summaries WITH UniqueOpens/UniqueClicks**: `tbl_pbi_engagement` (Mailing×Link, 1.38M), `tbl_pbi_mailingreciever_region` (697K), `tbl_pbi_mailingreciever_division` (290K). Tier 4 = platform metadata. Tier 5 = reference. Daily refresh; aggregations without historisation. 66% NULL-region as data-quality blocker. **Full table list**: see [memory/imep_gold_full_inventory.md](../../../.claude/projects/-Users-micha-Documents-Arbeit-Databricks/memory/imep_gold_full_inventory.md).

### Q16b — `Final` table: grain and partitioning **(follow-up)**

> *For `imep_gold.Final` (525M rows, 30 columns): show complete column list with types. Is the table partitioned (by date, by MailingId, by EventType)? Show `MIN/MAX(send_datetime)` to understand temporal coverage. Show distinct values and row counts for `EventType` and `EngagementType` columns.*

→ Understand whether we can use `Final` directly for ad-hoc queries or whether Tier 2/3 is always preferable.

### Q16c — `tbl_pbi_kpi` schema **(follow-up)**

> *Full column list with types for `imep_gold.tbl_pbi_kpi` (244K rows). Which KPI columns exist (open rate, click rate, bounce rate, unsubscribe rate)? Is it per mailing or per (mailing, segment)?*

→ Could be a direct pack-level summary that saves us the Silver aggregation.

### Q16d — NULL-region root cause **(data quality, critical)**

> *In `imep_gold.tbl_pbi_mailingreciever_region`: for the 66% of recipients with NULL region — what is the distribution of their `MailingId` (is it concentrated in certain campaigns)? Join via MailingId to `tbl_pbi_platform_mailings` and show the distribution by CreationDate year. Also: for a sample of 100 affected TNumbers, check directly against `tbl_hr_employee` — do they exist there? If yes, what's their `ORGANIZATIONAL_UNIT`, and does that OU exist in `tbl_hr_costcenter`?*

→ Decides the fix strategy: stale cost-center snapshots? Ex-employees? Or a genuine reference-data gap?

### ~~Q17~~ ✅ Do further Gold schemas exist **(resolved 2026-04-17)**

→ **Answer**: **SharePoint Gold exists** (`sharepoint_gold`, 20 tables, ~270M+ rows). Key: `pbi_db_interactions_metrics` (84M, 11 cols), `pbi_db_pageviewed_metric` (84M, 5 cols), `pbi_db_pagevisited_metric` (81M, 9 cols), `pbi_db_employeecontact` (24M, 17 cols), `pbi_db_datewise_overview_fact_tbl` (7.5M, 31 cols). Separately: **`sharepoint_clicks_gold`** with `pbi_db_ctalabel_intr_fact_gold` (3M, CTA labels). **CPLAN data already in Databricks** (`sharepoint_cplan`): Clusters (17), Packs (280), Activities (4,349). **`pbi_gold` (60 tables, inaccessible)** — request access, presumably the semantic model. Full list: see [memory/sharepoint_gold_inventory.md](../../../.claude/projects/-Users-micha-Documents-Arbeit-Databricks/memory/sharepoint_gold_inventory.md).

### Q17b — TrackingID columns in `sharepoint_gold` **(follow-up)**

> *For each table in `sharepoint_gold` (`pbi_db_interactions_metrics`, `pbi_db_pageviewed_metric`, `pbi_db_pagevisited_metric`, `pbi_db_page_visitedkey_view`, `pbi_db_employeecontact`, `pbi_db_90_days_interactions_metric`, `pbi_db_datewise_overview_fact_tbl`): list all column names with data types. For each table, identify: (a) which column joins back to `sharepoint_bronze.pages` (page_id or URL) to retrieve `UBSGICTrackingID`; (b) whether the metric table itself already carries a TrackingId column.*

→ Decides whether we join the gold metric directly by TrackingID or via page_id → pages.UBSGICTrackingID.

### Q17c — `pbi_db_datewise_overview_fact_tbl` as pre-joined fact **(follow-up)**

> *Full column list for `sharepoint_gold.pbi_db_datewise_overview_fact_tbl` (7.5M rows, 31 cols). Is this already a pre-joined fact (date × page × employee × metrics)? Show 3 sample rows with all columns to understand what it aggregates.*

→ If this is a pre-joined fact, our Gold cross-channel build is almost done.

### Q17d — `pbi_db_employeecontact` as alternative HR bridge **(follow-up)**

> *Full schema of `sharepoint_gold.pbi_db_employeecontact` (24M rows, 17 cols). Does it contain both TNumber and GPN/WORKER_ID? Can we use it instead of `imep_bronze.tbl_hr_employee` for the identity bridge (might be faster / pre-cleaned)?*

→ Simplifies `silver.dim_employee_temporal` if the resolution is already done here.

### Q17e — CPLAN data in `sharepoint_cplan` directly usable? **(follow-up)**

> *Full column list for `sharepoint_cplan.communicationspacks_bronze`, `_internalcommunicationactivities_bronze`, `_trackingcluster_bronze`. For each: are the relationships documented (FK columns)? Check that `trackingcluster_bronze.cluster_id` matches the first segment of `TrackingId` values in `imep_gold.tbl_pbi_platform_mailings`.*

→ Confirms that we can use CPLAN directly from Databricks (no import from external CPLAN repo needed).

### Q17f — `pbi_gold` access request **(governance)**

> *We observe that `pbi_gold` schema exists with 60 tables but all return row/col count = -1 (access denied). This schema is likely central for the corporate Power BI semantic model. Question for admin: (a) who owns this schema? (b) what's the access-request process? (c) is read-only access possible for cross-channel reporting?*

→ Not directly for Genie — rather for the catalog admin. This question belongs in the governance section of the BRD (new OP-34).

### Q18 — Pre-joined cross-channel views

> *Search the entire catalog for tables/views whose name contains `cross_channel`, `multi_channel`, `campaign_performance`, `communication_performance`, `funnel`, `journey`, `attribution`, `pack_performance`, `semantic`, `reporting_layer`. For each hit show schema.table, column count, row count, and 3 column names that reveal its purpose.*

→ Best case: the cross-channel model already exists.

### Q19 — Relationship `tbl_pbi_platform_mailings.EventId` ↔ `tbl_pbi_platform_events.Id`

> *In `imep_gold.tbl_pbi_platform_mailings`: what percentage of rows have a non-null `EventId`? For those, verify that the `EventId` matches an existing `tbl_pbi_platform_events.Id` (JOIN hit rate). Also show: of mailings linked to an event, does the mailing's `tracking_pack_id` (first 2 segments of TrackingId) equal the event's `tracking_pack_id`?*

→ Confirms whether the `EventId` FK is directly usable.

### Q20 — Power BI / semantic model as authoritative source (new, critical)

> **Context**: The existing Power BI dashboard shows opens/clicks/views — i.e. consumes certain Gold tables. These are our authoritative starting point.

> *Can you inspect the Databricks Lakehouse Monitoring / Unity Catalog lineage for any downstream Power BI dataset consumers? Specifically:*
> *a) List all tables in any `*_gold` schema that are referenced by a Power BI / Lakeflow / semantic model downstream.*
> *b) If lineage is not available: list all tables in `imep_gold` and `sharepoint_*` that have been queried in the last 30 days (via query history / audit log). The frequently-read ones are the ones Power BI hits.*
> *c) For the top 5 most-queried tables: show column list so we can identify engagement metrics (count columns, *_sent, *_opened, *_clicked, *_views).*

→ Shortest path to the right Gold tables: look at what Power BI already actively queries. If Genie has no lineage: the tables that appear most often in queries are the ones used "live".

---

## J — Build Readiness for Cross-Channel JOIN (Q21–Q24)

Goal: the final schema details and volume checks we need to start the concrete SQL build of `gold.fact_cross_channel`. Plan: email via `imep_gold.tbl_pbi_platform_mailings` + `tbl_pbi_mailingreciever_region` ↔ intranet via `sharepoint_gold.pbi_db_*_metric` + `sharepoint_bronze.pages`, joined via derived `tracking_pack_id`.

### Q21 — iMEP Gold Tier 3 schemas (JOIN-critical)

> *For the following iMEP Gold tables, return full column list with types and nullability:*
> *- `imep_gold.tbl_pbi_mailingreciever_region`*
> *- `imep_gold.tbl_pbi_mailingreciever_division`*
> *- `imep_gold.tbl_pbi_engagement`*
> *- `imep_gold.tbl_pbi_kpi`*
>
> *For each, identify the exact column name that joins back to `imep_gold.tbl_pbi_platform_mailings.Id` (is it `MailingId`, `email_id`, `Id`, or something else?). Confirm the types of `UniqueOpens`, `UniqueClicks`, `Count` — are they nullable (per Q16-note "intentional NULLs for no tracked engagement")?*

→ Without these column details the `JOIN ON r.MailingId = m.Id` cannot be concretely formulated.

### Q22 — SharePoint Gold metric schemas

> *For the following SharePoint Gold tables, return full column list with types and nullability:*
> *- `sharepoint_gold.pbi_db_interactions_metrics` (84M × 11 cols)*
> *- `sharepoint_gold.pbi_db_pageviewed_metric` (84M × 5 cols)*
> *- `sharepoint_gold.pbi_db_pagevisited_metric` (81M × 9 cols)*
> *- `sharepoint_gold.pbi_db_datewise_overview_fact_tbl` (7.5M × 31 cols)*
>
> *For each: (a) identify the page-identifying FK column (`page_id`, `pageId`, `url_hash`, etc.) that joins back to `sharepoint_bronze.pages`; (b) check whether any of these tables carries a direct `GICTrackingID` / `UBSGICTrackingID` column (so we can skip the pages-JOIN).*

→ Decides whether the intranet aggregate can be joined directly by TrackingID or has to be resolved via `page_id → pages.UBSGICTrackingID`.

### Q23 — TrackingId format consistency between iMEP and SharePoint

> *Take 100 sample values of `TrackingId` from `imep_gold.tbl_pbi_platform_mailings` and 100 samples of `UBSGICTrackingID` from `sharepoint_bronze.pages`. Do NOT return the sample values — return only a structural comparison:*
> *(a) Do both have the same length (32 chars)?*
> *(b) Do both use the same segment structure (5 segments, dash-separated)?*
> *(c) Is case consistent across both (all uppercase? mixed?)?*
> *(d) Any leading/trailing whitespace in either column?*
> *(e) Count how many TrackingIds appear in both tables (intersection size) vs only in one.*

→ If the formats diverge, we need an `UPPER(TRIM(...))` or regex normalize in the JOIN. Intersection count is the true coverage figure for the cross-channel funnel.

### Q24 — Volume check with `TrackingId IS NOT NULL` filter (performance-relevant)

> *In `imep_gold.tbl_pbi_platform_mailings`:*
> *(a) How many rows have `TrackingId IS NOT NULL` vs NULL?*
> *(b) Break down the NOT-NULL count by `YEAR(CreationDate)` for the last 5 years.*
> *(c) Of the rows with TrackingId, what percentage has the channel-suffix `-EMI` (= email)? And what's the distribution of the other suffixes (EVT, BAN, etc.)?*
> *(d) Assuming we filter `WHERE TrackingId IS NOT NULL AND RIGHT(TrackingId, 3) = 'EMI'` — how many distinct `tracking_pack_id` (first 2 segments) does that leave us?*

→ Shows how much of the 278M-recipients universe survives the filter for our email page, and how many distinct packs we should expect in the dashboard.

### Q25 — UBSGICTrackingID distribution in `pages` (time + site breakdown)

> **Domain context**: `sharepoint_bronze.pages` is the SharePoint page inventory (~48k rows). `UBSGICTrackingID` is populated only for News- and Event-Articles (~4% per Q22). `UBSArticleDate` is the temporal anchor. Each page belongs to a Site — FK likely `siteId` / `webId` / `siteUrl`, use whichever column joins to `sharepoint_bronze.sites`.

> *Analyze `UBSGICTrackingID` coverage in `sharepoint_bronze.pages` in four steps. Return only aggregates — do NOT return sample TrackingID values.*
>
> *1. **Overall counts**:*
>    - *Total rows*
>    - *Count where `UBSGICTrackingID IS NOT NULL`*
>    - *Count where `UBSArticleDate IS NOT NULL`*
>    - *Count where both are populated*
>    - *Each as raw count + percentage of total*
>
> *2. **TrackingID distribution over `UBSArticleDate`**:*
>    - *Group by `DATE_TRUNC('month', UBSArticleDate)` for the last 60 months.*
>    - *Per month show: total articles (`UBSArticleDate IS NOT NULL`), articles with `UBSGICTrackingID`, coverage % (with-TID / articles that month).*
>    - *Flag the first month where coverage ≥ 80% — that's the realistic start date for cross-channel funnel reporting.*
>
> *3. **Distinct PageURLs with TrackingID**:*
>    - *How many distinct page URLs (column `pageUrl` / `webUrl` / `url`) have `UBSGICTrackingID IS NOT NULL`?*
>    - *How many distinct page URLs exist in total?*
>    - *Ratio as percentage.*
>    - *Also: are any page URLs associated with multiple `UBSGICTrackingID`s (historical re-use)? Report the count of URLs with >1 distinct TID.*
>
> *4. **Coverage per Site**:*
>    - *Join `pages` to `sharepoint_bronze.sites` via the site FK.*
>    - *Per Site (top 30 by total pages) return: site name / URL, total pages, pages with `UBSGICTrackingID`, coverage % within that site, distinct `tracking_pack_id` count (first 2 dash-segments of `UPPER(TRIM(UBSGICTrackingID))`).*
>    - *Order by `pages_with_tid` DESC.*
>    - *Highlight the top 5 sites that together cover ≥ 80% of all tracked pages (Pareto).*
>
> *Final summary — one paragraph, answer the two build-decisions this analysis should drive:*
> *(a) Is tracking-ID coverage dense enough on any specific subset of sites that we can restrict the cross-channel funnel to that subset?*
> *(b) From which date onward does coverage become reliable enough that the dashboard's default time window should start there?*
>
> *If the FK between `pages` and `sites` is not obvious, first run `DESCRIBE TABLE sharepoint_bronze.pages` and `DESCRIBE TABLE sharepoint_bronze.sites`, identify matching GUID/ID columns, and explain your join choice before running step 4.*

→ Refines Q22: the global 4% coverage is broken down by time and site. Goal: (1) realistic dashboard default time range, (2) Pareto subset of sites where coverage is actually high enough for a credible funnel.

---

## F — Pipeline Lineage without Unity Catalog (Q26–Q30)

> **Context 2026-04-20**: We have neither a UC lineage graph nor `system.access.table_lineage`. The Bronze → Gold pipeline structure has to be reconstructed purely from SQL against Hive Metastore + Delta table metadata. Goal: populate data-card lineage sections (see `docs/tables/`) with real transformation jobs and refresh cadence — instead of assumptions.

### ~~Q26~~ ✅ Silver existence via schema naming **(OP-lineage-a — resolved 2026-04-20)**

> *Run `SHOW DATABASES` (or `SHOW SCHEMAS`). List every schema whose name contains any of: "silver", "stage", "staging", "curated", "std", "standardized", "int", "intermediate", "harmonized", "conformed", "enriched". For each match, return schema name and `COUNT(*)` of tables via `SHOW TABLES IN <schema>`. If none exist, confirm that bronze writes directly to gold with no intermediate layer.*

→ **Answer**: **17 Silver schemas exist**, all with the suffix `_silver`. **Zero hits** for alternative naming patterns (stage/staging/curated/std/standardized/intermediate/harmonized/conformed/enriched). The medallion pattern is **asymmetric**:
> - `bronze → silver → gold` for SharePoint, Adobe, Dynamics, Ads
> - `bronze → gold` (skipping silver) for **email engagement (iMEP)** → direct write to `imep_gold.final` (~520M rows, denormalised join, HR enrichment late)
>
> **`sharepoint_silver`** (5 tables): `webpageviewed` ~262M · `pageviewed` ~136M · `pageposted` ~105M + dims `webpage`, `website`. → the SharePoint source of choice for our cross-channel SQL.
>
> **`imep_silver`** exists only for events: `invitation`, `eventregistration` ~13.7M, `event` ~84K. **No Silver for email.**
>
> Other relevant silvers: `adobe_silver` (15), `dynamics_silver` (7), `email_campaign_forms_silver` (5), `adform_silver` (3), `profile_silver` (~6), `linkedin_silver`, `google_silver`, `facebook_silver`, `oii_silver`, `bid_silver`, `persona_data_silver`.
>
> **Consequence**: do NOT build `silver.fact_email` — iMEP deliberately chose against it. Consume `imep_gold.final` directly. Full details: [memory/imep_silver_q26_findings.md](../../../.claude/projects/-Users-micha-Documents-Arbeit-Databricks/memory/imep_silver_q26_findings.md).

### ~~Q27~~ ✅ Lineage via column fingerprint **(OP-lineage-b — resolved 2026-04-20)**

> *Across ALL schemas in the workspace, find every table that contains at least one of these columns (case-insensitive match on column name): `Id`, `EmailId`, `mailingid`, `MailingId`, `TrackingId`, `UBSGICTrackingID`, `GICTrackingID`, `TNumber`, `T_NUMBER`, `WORKER_ID`.*
>
> *For each hit return: schema, table, matching_columns, row_count, MIN/MAX of the earliest timestamp column (`CreationDate`, `DateTime`, `ModifiedDate`, `_ingestion_ts`, or similar).*
>
> *Goal: reconstruct join-key relationships between bronze and gold purely from column overlap + row counts.*

→ **Answer**: **93 tables / 8 schemas** have hits. Core findings:
>
> **Two sole full-key fact hubs** (Id + EmailId + TNumber):
> - `imep_bronze.tbl_analytics_link` — **533M** rows
> - `imep_bronze.tbl_email_receiver_status` — **293M** rows
>
> **Row counts confirmed**: `imep_gold.final` 520M · `sharepoint_bronze.pageviews` 173M · `tbl_email` 145K · `tbl_pbi_platform_mailings` 73K · `sharepoint_bronze.pages` 48K · `tbl_hr_employee` 265K.
>
> **Join-graph topology**:
> - `EmailId` → **12 tables** (backbone for email domain)
> - `TNumber` → **only 2 tables** → person-level analytics exist exclusively on email-engagement grain
> - `TrackingId` → **exactly 4 tables** (tbl_email, tbl_event, tbl_pbi_platform_mailings, tbl_pbi_platform_events), **never** co-occurring with EmailId → TrackingId is a **dimension**, not a fact key
> - 26 `imep_gold.tbl_pbi_*` all join back: `MailingId = tbl_pbi_platform_mailings.Id = tbl_email.Id`
>
> **Cross-channel**: email ↔ SharePoint goes **dimensionally** (TrackingId ↔ GICTrackingID via `pages`), **not** via engagement rows. SharePoint has no person-key equivalent to TNumber.
>
> **New schema sighted**: `page_metadata_bronze` (pagelikesview 40K, comments 3.4K, reportedcomments, moderatedcomments, metadata-pageviews 293) — separate from the main pageview fact.
>
> Full details: [memory/imep_join_graph_q27_findings.md](../../../.claude/projects/-Users-micha-Documents-Arbeit-Databricks/memory/imep_join_graph_q27_findings.md).

### ~~Q28~~ ✅ Pipeline hints from Delta history **(OP-lineage-c — resolved 2026-04-20)**

> *For each of these tables run `DESCRIBE HISTORY` and return the last 10 operations (timestamp, operation, userName, operationParameters, operationMetrics.numOutputRows):*
>
> - `imep_bronze.tbl_email`
> - `imep_bronze.tbl_email_receiver_status`
> - `imep_bronze.tbl_analytics_link`
> - `imep_gold.tbl_pbi_platform_mailings`
> - `sharepoint_bronze.pages`
> - `sharepoint_bronze.pageviews`
> - *plus any Tier-3 engagement table identified in `imep_gold`*
>
> *From this I need: refresh cadence (how often), operation type (MERGE / INSERT / COPY INTO / WRITE), the service-principal or user that writes, and whether the write pattern looks like batch or streaming.*

→ **Answer** (110 history rows across 11 tables):
>
> **Uniform rhythm pattern**: `MERGE/WRITE → OPTIMIZE (Z-Order) → VACUUM`, 7-day retention.
>
> **iMEP Bronze — MERGE upsert 2×/day @ 00:00/12:00 UTC**:
> - `tbl_email`: ~145K full-table upsert
> - `tbl_email_receiver_status`: 27–72M per run (full upsert)
> - `tbl_analytics_link`: **3.7–8.5K per run → truly incremental** (new click events)
>
> **SharePoint Bronze — 1×/day @ 02:00 UTC**:
> - `pages`: MERGE daily snapshot (48K)
> - `pageviews`: **append WRITE, 7 bursts within 1 minute** (API pagination pattern, micro-batched, **not** streaming)
>
> **iMEP Gold — full rebuild 2×/day @ ~00:23/12:25 UTC**:
> - **No incrementality** — every gold table completely destroyed and rebuilt
> - **520M-row table** (labelled `tbl_pbi_platform_mailings` in the image — ⚠️ contradicts Q27's 73K, most likely `imep_gold.final`; to be clarified via Q30) rewritten twice daily → **largest compute cost item**
> - Further gold full rebuilds: `tbl_pbi_mailings_region` (73,530), `tbl_pbi_mailings_division` (697,787), `tbl_pbi_analytics` (290,723), `tbl_pbi_kpi` (245,040), +1 (1,384,800)
>
> **Writer**: `userName` empty everywhere → **service principal** (SPN prefix `a71734ea-...`). Fully automated pipeline, no human writer.
>
> **Dashboard implication**: refresh-window risk around 00:23/12:25 UTC (gold rebuild phase) — queries in this window may hit a partial/inconsistent state. Cache or schedule around it.
>
> **Optimisation hint** (not our remit): gold full rebuild → incremental MERGE would significantly reduce compute.
>
> Full details: [memory/imep_pipeline_ops_q28_findings.md](../../../.claude/projects/-Users-micha-Documents-Arbeit-Databricks/memory/imep_pipeline_ops_q28_findings.md).

### ~~Q29~~ ✅ Derive tier structure from table shape **(OP-lineage-d — resolved 2026-04-20)**

> *For every table in `imep_gold` AND `sharepoint_gold`, return: table name, all column names, row count, `COUNT(DISTINCT <id-like column>)`, presence of dimension/metric columns as boolean flags. Goal: bucket tables by grain.*

→ **Answer**: 52 gold tables in a strict 4-tier hierarchy. **iMEP Gold (31 tables)**:
> - **Tier 0** — atomic fact (1 table): `final` (520M, grain `mailing × recipient × event × hour`) — **definitively resolves the Q28 label confusion** ✅
> - **Tier 1** — timespan × dimension aggregates (15 tables): 24h/72h/1w/15w/15m+, Date/DateHour/DivArea/RegCntry
> - **Tier 2** — per-mailing summaries (~8 tables): `mailingreceiver_*`, `engagement` (1.8M, **117K distinct mailingIds → emails+events mixed**), `log_mail`
> - **Tier 3** — platform & reference dimensions (~7): `tbl_pbi_platform_mailings` (73,920 rows, **927 TrackingIds**), `tbl_pbi_platform_events` (84,052), mailing access, logins, etc.
>
> **SharePoint Gold (20 tables)**:
> - Tier 0 — atomic interaction facts (4): `interactions_metrics` 84M, `pageviewed_metric`, `pagevisited_metric`, `90_days`
> - Tier 1 — pre-aggregated overview (3): `datewise_overview_fact_tbl` (7.5M, 27 rolling-window stats)
> - Tier 2 — engagement/social (3): `pageliked_metric`, `pagecommented_metric`, `page_like_int_fact`
> - Tier 3 — dimensions (7): `employeecontact` (24.3M, person-bridge candidate), `website_page_inventory`, calendar, referer-app
> - **Video sub-domain (3 tables)**: `fact_video_engagement_gold` etc. — the only managed Declarative Pipeline tables in the whole system
>
> **Cross-system join mechanism**: iMEP Gold + SP Gold share **no FK** — the only bridge is `tbl_pbi_platform_mailings.TrackingId → pages.UBSGICTrackingID → pages.pageUUID → marketingPageId`, and it only works after a SEG1–4 match.
>
> **Mental model**: iMEP Gold = message-centric per-recipient; SP Gold = page-centric analytics; TrackingId = the only conceptual bridge at the dimension level.
>
> Full details: [memory/imep_sp_gold_tiers_q29_findings.md](../../../.claude/projects/-Users-micha-Documents-Arbeit-Databricks/memory/imep_sp_gold_tiers_q29_findings.md).

### ~~Q30~~ ✅ Table properties & storage location **(OP-lineage-e — resolved 2026-04-20)**

> *For every table in `imep_bronze`, `imep_gold`, `sharepoint_bronze`, `sharepoint_gold`, run `DESCRIBE EXTENDED` and return Detailed Table Information. The ADLS path often encodes the producing pipeline name.*

→ **Answer**: 114 external Delta tables across **3 ADLS accounts** (iMEP bronze, SP bronze, shared Gold):
>
> **Pipeline fingerprints**:
> - iMEP Bronze: 49 tables, uniform path `abfss://bronze@<iMEP-bronze-acc>/.../imep/<TABLE>` → **1 ingestion job**
> - iMEP Gold: 31 tables via full rebuild in `/final` + `/tbl_pbi/*` (+ 1 outlier `/imep/tbl_active_employee_month`) → **1 orchestration notebook**
> - SP Bronze: 12 tables + historical snapshots (`pageviews_09_27_2023`, `pageviews_08022024`, etc.) → **1 ingestion pipeline with ad-hoc snapshot folders**
> - SP Gold: **two pipeline families** — family 1 "Employee Analytics" (16 notebook-based full-rebuild tables under `/employee_analytics/pbi_db_*`, Spark 3.2.1) + family 2 "Video Analytics" (3 tables under **managed Declarative Pipeline**, Spark 3.5.2/4.0.0, pipelines.pipelineId, ChangeDataFeed enabled, proper table comments)
>
> **3 structural findings**:
> 1. **Pipeline reality check**: of 114 tables, **only 3 (video) are managed Declarative Pipelines** — the other 111 are notebook-based jobs without pipeline metadata. The folder layout suggests 2–3 large orchestration notebooks instead of a layered framework.
> 2. **⚠️ Zero partitioning across all 114 tables** — particularly impactful for `imep_gold.final` (520M, full rebuild 2×/day) and `sharepoint_gold.interactions_metrics` (84M). Partitioning by date would massively reduce rebuild cost and improve Power BI latency. **Biggest structural performance gap.**
> 3. **Gold co-location is intentional**: both gold schemas sit in **one** ADLS gold account → cross-system joins possible directly inside Fabric/Spark, no cross-account auth required. Deliberate architectural alignment for analytics.
>
> **One-page summary**: the physical layout reveals the architectural truth — everything is notebook-driven (except video), gold is rebuilt wholesale (not incrementally), no partitioning is the biggest performance gap, gold co-location is the correct design for cross-channel.
>
> Full details: [memory/storage_architecture_q30_findings.md](../../../.claude/projects/-Users-micha-Documents-Arbeit-Databricks/memory/storage_architecture_q30_findings.md).

**What Genie CANNOT answer** (to be clarified via workspace UI or REST API):
- Job definitions & scheduler → Jobs UI or `/api/2.1/jobs/list`
- Notebook contents (the actual transformation SQL) → Workspace browser
- Service principal → job mapping → workspace admin area

---

## Usage Notes

1. **Order**: Q1 and Q3 first — they decide whether the model is buildable at all. Q19/Q20 at the end as validation.
2. **Genie session**: ask questions within one session — Genie remembers context (tables, filters) and chains cleanly.
3. **Capture output**: save each answer as a notebook cell or screenshot, then mark in BRD §9 as "✅ resolved".
4. **Question follow-up**: if Genie does not deliver (e.g. "column not found"), ask directly: *"Which tables in this catalog contain a column named like 'tracking_id' or 'camms_tracking_id'?"* — Genie can search the information schema.
