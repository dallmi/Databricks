# Genie-Fragen zur Klärung offener iMEP-Punkte

Gezielte Prompts für Databricks Genie, um offene technische Punkte aus dem [BRD](BRD_cross_channel_analytics.md) §9 zu schliessen, **bevor** wir `silver.fact_email` und die Cross-Channel-Joins implementieren.

Konvention:
- **OP-XX** → Referenz auf Open Point im BRD.
- Prompts sind in **Englisch** formuliert (Genie liefert zuverlässigere Ergebnisse gegen englische Tabellen­namen).
- Jede Frage kommt mit erwartetem Output und möglichem Follow-up.

---

## A — Schema & Tracking-ID-Lokation (höchste Priorität)

> **Status 2026-04-16**: Q1, Q2, Q3 beantwortet — siehe [memory/imep_genie_findings_q1_q2_q3.md](../../../.claude/projects/-Users-micha-Documents-Arbeit-Databricks/memory/imep_genie_findings_q1_q2_q3.md). Follow-ups Q1b und Q3a sind neu hinzugekommen.

### ~~Q1~~ ✅ Wo lebt `CammsTrackingID` in iMEP? **(OP-04 — gelöst)**

> *Show me all columns in `TBL_EMAIL`, `TBL_EMAIL_LINKS`, `TBL_ANALYTICS_LINK` and `TBL_EMAIL_RECEIVER_STATUS` whose name contains "tracking", "camms", "cpid" or "cplan" (case-insensitive). Include the data type and a non-null sample value for each column.*

→ **Antwort**: Spalte heisst `TrackingId` (nicht `CammsTrackingID`), liegt auf `imep_bronze.tbl_email` (Mailing-Ebene), Beispiel `QRREP-0000058-240709-0000060-EMI`. Cross-Channel-Join via `tbl_analytics_link.EmailId → tbl_email.Id → tbl_email.TrackingId`. SharePoint nutzt `GICTrackingID` (mit Case-Varianten), CPLAN nutzt `tracking_id`.

### ~~Q1b~~ ✅ iMEP Gold-Layer evaluieren **(OP-07f — gelöst)**

> *Show full schema and a 5-row sample of `imep_gold.tbl_pbi_platform_mailings` and `imep_gold.tbl_pbi_platform_events`. For each table, list which iMEP bronze tables / events / aggregations they appear to combine. Are these tables refreshed regularly (check max CreationDate / load timestamp)?*

→ **Antwort**: Gold ist **Mailing-/Event-Master mit Content- & Registration-Metriken**, **kein** Send/Open/Click-Aggregat. Konsequenz: `silver.fact_email` weiterhin aus Bronze, aber `silver.dim_pack` direkt aus Gold (`tbl_pbi_platform_mailings` + `tbl_pbi_platform_events` UNION). Event-Phase kann vorgezogen werden (Registration-Count vorhanden). Beide Tabellen täglich refreshed. Sentinel-Wert `2124` für open-ended Events. Siehe [memory/imep_gold_layer_analysis.md](../../../.claude/projects/-Users-micha-Documents-Arbeit-Databricks/memory/imep_gold_layer_analysis.md).

### ~~Q2~~ ✅ Vollständiges Schema der Kern-Tabellen **(OP-01 — gelöst)**

> *List every column with data type and nullability for these four tables: `TBL_EMAIL`, `TBL_EMAIL_RECEIVER_STATUS`, `TBL_ANALYTICS_LINK`, `TBL_EMAIL_LINKS`. For each column, also show one non-null sample value.*

→ **Antwort**: 111 Spalten gesamt, alle nullable. `tbl_email` 54 cols (inkl. `TrackingId`, `CreatedBy`, `Subject`, `EmailSendingStatus`); `tbl_email_receiver_status` mit `Receiver`, `TNumber`, `Status`, `LogStatus`, `EmailLanguage`; `tbl_analytics_link` 22 cols mit `Agent`, `LinkTypeEnum`, `CurrentLanguage`, `EmailReceiverStatusId`; `tbl_email_links` 10 cols (lean, Template-Definitionen).

### ~~Q3~~ ❌ HR-Bridge zwischen TNumber und GPN **(OP-07d — Hypothese widerlegt)**

> *Inspect `TBL_HR_EMPLOYEE` and `TBL_HR_USER`: list all columns that look like employee identifiers (names containing `t_number`, `tnumber`, `gpn`, `personnel`, `staff`, `emp_id`, `worker`, `abacus`, `websso`, `id`). For each, show data type, sample value, and whether nulls exist. Also check whether any single table contains BOTH a T-number column and a GPN column.*

→ **Antwort**: **GPN-Spalte existiert nicht in HR.** `tbl_hr_employees.T_NUMBER` (Lowercase `t001108`) und `tbl_hr_user.UbsId` (Uppercase `T594687`) sind beide T-Numbers. Andere Identifier in HR: `WORKER_ID`, `ABACUS_ID`, `ALTERNATE_WORKER_ID`, `WEBSSO`, `UUNAME`, `PersonalNumber`. Keine Bridge zu GPN. → Neue Frage Q3a.

### ~~Q3a~~ ✅ Was IST die GPN in AppInsights? **(OP-07e — gelöst durch Q3b-Befund)**

Hypothese A: GPN ist eine String-Transformation des T-Numbers (`t001108` → `00001108`). Hypothese B: GPN kommt aus einem anderen System (Active Directory, WebSSO).

> *Take 10 distinct non-null `customDimensions.GPN` values from our AppInsights `pageViews` (or `fact_page_view` if exposed). For each: try to find a matching employee in `tbl_hr_employees` by:
> a) replacing leading zeros with 't' (e.g. `00001108` → `t001108`)
> b) matching against `WORKER_ID`
> c) matching against `ABACUS_ID`
> d) matching against `ALTERNATE_WORKER_ID`
> e) matching against `WEBSSO`
> Report which strategy yields a hit (and how many of the 10 match).*

→ Entscheidet, ob wir eine reine String-Transformation brauchen (billig) oder eine externe Bridge-Quelle erschliessen müssen (teuer).

### ~~Q3b~~ ✅ Erweiterte HR-Suche nach GPN-Spalte **(OP-07e — gelöst)**

> **Antwort**: `imep_bronze.tbl_hr_employee.WORKER_ID` ist die GPN. Dieselbe Tabelle führt auch `T_NUMBER`. Bridge ist ein einfacher LEFT JOIN, keine externe Quelle nötig. Genie-initiale Suche hatte `WORKER_ID` als "Primary worker identifier" sogar gelistet — Verbindung zur GPN aber nicht hergestellt.

**Original-Frage (zur Doku):**

> **Domänen-Wissen**: GPN und TNumber haben in HR eine bestätigte 1:1-Beziehung. Wenn unsere ersten Q3-Treffer keine GPN-Spalte gezeigt haben, war die Suche zu eng.

> **Wichtig**: nur Metadaten und Pattern-Counts ausgeben — KEINE Beispielwerte (PII vermeiden).

> *Three-step deep search — return only schema metadata and aggregate counts, no individual values:
>
> 1. List ALL tables in any schema starting with `tbl_hr_` or containing the word `employee`, `person`, `worker`, `staff`, `identity`, `directory`. Show table name, column count, row count.
>
> 2. For `tbl_hr_employees`: list all 114 column names and data types (DESCRIBE output). Then, for each STRING column, return only this aggregate: `count_matching_8digits` = number of rows where the column value matches regex `^[0-9]{8}$`. Do NOT return any actual values. Columns with a high `count_matching_8digits` are structurally GPN candidates.
>
> 3. Search column names across all HR tables for ANY of these tokens (case-insensitive): `gpn`, `global`, `g_p_n`, `personnel_no`, `personnel_number`, `pid`, `wmid`, `master_id`, `master_no`, `corp_id`, `emp_global`, `enterprise_id`. For each hit, return only `table.column`, data type, null count, total row count — no sample values.*

→ Wenn das die GPN-Spalte findet: Bridge in HR vorhanden, OP-07e geschlossen. Wenn nicht: extern (AD/WebSSO) erschliessen.

---

## B — Status- und Event-Werte

### Q4 — Welche Status-Werte existieren? **(OP-02)**

> *For `TBL_EMAIL_RECEIVER_STATUS`: return the distinct values of every status-like or enum-like column (e.g. `Status`, `DeliveryStatus`, `BounceReason`), with row counts per value. Do the same for `TBL_ANALYTICS_LINK.linkTypeenum` and `TBL_ANALYTICS_LINK.Agent`.*

→ Liefert Funnel-Event-Mapping (sent/bounced/opened/clicked/unsubscribed).

### Q5 — Open vs. Click Dedup-Check **(OP-03)**

> *In `TBL_ANALYTICS_LINK`, for a sample of 10 recent mailings (top 10 `EmailId` by row count), compute:
> - total rows
> - distinct `(EmailId, TNumber)` pairs where `linkTypeenum = 'OPEN'`
> - distinct `(EmailId, TNumber)` pairs where `linkTypeenum != 'OPEN'`
> - max, min, avg number of rows per `(EmailId, TNumber, linkTypeenum)` triple.*

→ Bestätigt, dass unique-open/unique-click via `(EmailId, TNumber)`-Dedup korrekt ist und zeigt Multi-Event-Verhalten.

---

## C — Datenumfang & Qualität

### Q6 — Zeitliche Abdeckung **(OP-06)**

> *For each of `TBL_EMAIL`, `TBL_EMAIL_RECEIVER_STATUS`, `TBL_ANALYTICS_LINK`: return `MIN(CreationDate)`, `MAX(CreationDate)`, total row count, and row count per calendar year.*

→ Retention / Historisierungs-Grenzen.

### Q7 — Audience-Size pro Mailing **(OP-05)**

> *For the 20 most recent mailings (`TBL_EMAIL` by `CreationDate` desc), show:
> - `Id`, `Title`, `CreationDate`, `CreatedBy`
> - number of recipients in `TBL_EMAIL_RECEIVER_STATUS` (= audience size)
> - number of distinct `TNumber` values that had `linkTypeenum = 'OPEN'` (= unique opens)
> - number of distinct `TNumber` values that had any click event (= unique clicks).*

→ Bestätigt, dass Audience-Size direkt aus iMEP ableitbar ist (kein CPLAN-Feld nötig).

### Q8 — Transactional-Mails ohne Campaign-Tracking **(OP-08)**

> *Using the column identified in Q1 as the CammsTrackingID location, count how many rows in the relevant table have NULL / empty / malformed CammsTrackingID values. Sample 10 such rows and show what type of email they represent (show `Title` / `CreatedBy` / `CreationDate`).*

→ Filter-Regel für den Silver-Build (müssen transactional Mails ausgeschlossen werden?).

### Q9 — Tracking-ID Format-Konformität **(OP-09, OP-10)**

> *Using the column identified in Q1, return:
> - count of values matching the regex `^[A-Z0-9]{5}-[0-9]{7}-[0-9]{6}-[0-9]{7}-[A-Z]{3}$`
> - count of values NOT matching (non-conforming)
> - sample 10 non-conforming values
> - are any values stored in different cases (mix of upper/lower)?*

→ Validierung 32-Char-5-Segment-Format und Case-Sensitivity.

---

## D — Org & HR

### Q10 — Org-Unit-Mapping Eindeutigkeit **(OP-07b)**

> *In `TBL_HR_COSTCENTER`: group by `ORGANIZATIONAL_UNIT` and count rows. Flag any `ORGANIZATIONAL_UNIT` with more than 1 row. For those, show the conflicting values in the business-division / area / region / country columns.*

→ Prüft, ob der Join `hr.ORGANIZATIONAL_UNIT = cc.ORGANIZATIONAL_UNIT` im Genie-Pattern 2 wirklich 1:1 ist.

### Q11 — HR-Historisierung in iMEP **(OP-07b)**

> *Does `TBL_HR_EMPLOYEE` have a validity window (columns like `ValidFrom`, `ValidTo`, `EffectiveDate`)? If yes, show for a sample of 5 employees how their `ORGANIZATIONAL_UNIT` has changed over time. If no, is there only a current snapshot?*

→ Entscheidet, ob wir temporal joinen (wie bei PageView) oder Current-Snapshot reicht.

### Q12 — Creator-Dimension **(OP-07c)**

> *For all distinct `CreatedBy` values in `TBL_EMAIL` from the last 12 months, resolve via `TBL_HR_EMPLOYEE.T_NUMBER` to a full name and show the top 20 creators by number of mailings.*

→ Qualifiziert den "Created By"-Filter im Dashboard als sinnvolle Dimension.

---

## E — Multi-Device & Engagement

### Q13 — Multi-Device-Volumen **(Design-Review Pattern 2)**

> *Out of all recipients in `TBL_ANALYTICS_LINK` (filter `IsActive = 1`, `linkTypeenum != 'OPEN'`, `Agent IN ('desktop','mobile')`), what share has engaged via BOTH desktop AND mobile for the same `EmailId`? Break down by mailing for the 20 largest mailings.*

→ Zeigt, ob die Multi-Device-CTE für unser Dashboard wirklich relevant ist (Häufigkeit in Praxis).

### Q14 — Engagement-Template-Coverage **(Design-Review Pattern 3)**

> *For the 10 most recent mailings: what share of `TBL_EMAIL_LINKS` entries received at least one click at runtime (via `TBL_ANALYTICS_LINK`)? Show per mailing: total template links, clicked links, unclicked links, click rate.*

→ Rechtfertigt den Template-UNION-Step (Pattern 3): lohnt sich der Aufwand nur wenn "Dead-Links" überhaupt relevant sind.

---

## F — Integration mit PageView (AppInsights)

### Q15 — TrackingID-Coverage in SharePoint Pages Inventory **(OP-15, verfeinert)**

> **Domänen-Kontext**: `sharepoint_bronze.pages` ist die Page-Inventory-Tabelle. `UBSGICTrackingID` wird **nur für News- und Event-Pages (Articles)** abgefüllt. `UBSArticleDate` ist der temporale Anker.

> *Two-step coverage analysis on `sharepoint_bronze.pages`:*
>
> *1. **Overall coverage**: Show total row count, count where `UBSGICTrackingID IS NOT NULL`, count where `UBSArticleDate IS NOT NULL`, and count where both are populated. Express each as percentage of total.*
>
> *2. **Coverage trend over time**: Group rows by `DATE_TRUNC('month', UBSArticleDate)` for the last 36 months. For each month show: total articles (rows with non-null `UBSArticleDate`), articles with `UBSGICTrackingID`, and the coverage percentage. Also flag the first month where coverage exceeds 80% — that's the realistic start date for cross-channel funnel reporting.*

→ Definiert den **realistischen Wert-Anspruch** des Cross-Channel-Modells. Wenn Coverage erst ab z.B. 2024 verlässlich ist, sollte das Dashboard das transparent abbilden (Default-Zeitraum, Coverage-Note).

### Q15b — TrackingID-Coverage in `sharepoint_bronze.pageviews` **(Folge-Frage)**

> **Architektur-Hinweis**: `sharepoint_bronze.pageviews` ist die laufende Interactions-Tabelle (Views/Visits) und hat selbst eine Spalte `GICTrackingID` (ohne `UBS`-Präfix — Inkonsistenz zur Inventory `UBSGICTrackingID`). `sharepoint_bronze.pages` ist die Inventory.

> *In `sharepoint_bronze.pageviews`: joine zu `sharepoint_bronze.pages` über `page_id` (oder ähnlichem FK). Zeige für die letzten 12 Monate pro Monat:*
> - *Total page views*
> - *Page views auf Article-Pages (joined page hat `UBSArticleDate IS NOT NULL`)*
> - *Page views mit gefülltem `pageviews.GICTrackingID`*
> - *Page views, bei denen `pages.UBSGICTrackingID = pageviews.GICTrackingID` (Konsistenz-Check zwischen den beiden Tabellen)*
> - *Coverage-% (TrackingID-tracked Views / Article-Views)*

→ Drei Befunde auf einmal: (1) Traffic-gewichtete Coverage (entscheidender als Inventory-Coverage, weil das Dashboard Views aggregiert); (2) Konsistenz-Check, ob die TrackingID in `pageviews` mit der in `pages` matcht; (3) Validierung, dass die historischen `pageviews_*`-Varianten irrelevant sind.

### Q16 — Attribution-Lag zwischen Email und PageView **(OP-16)**

> *For the 20 largest recent mailings, compute the time between Email-Send (`TBL_EMAIL_RECEIVER_STATUS.CreationDate`) and the earliest matching PageView for the same `CammsTrackingID`. Return: p50, p90, p99 in hours. Only consider PageViews within the first 30 days after send.*

→ Liefert Default-Attribution-Window (wahrscheinlich 7 oder 14 Tage).

---

## G — Banner & Event (Phase 2 Preview)

### Q17 — Event-Channel (`EVT`) Schema **(Phase 2)**

> *List the iMEP bronze tables that relate to event registrations (names containing "event", "registration", "attendee"). Show full schema plus one sample row per table. Does the same CammsTrackingID mechanism apply?*

### Q18 — Banner-Telemetrie (`BAN`) **(Phase 3)**

> *Does AppInsights `pageViews` contain any rows where `customDimensions.CustomProps.CammsTrackingID` ends with `-BAN`? Show count per month for the last 6 months and sample 5 rows.*

---

## H — Sanity-Check (am Schluss)

### Q19 — End-to-End Join Test für 1 Pack

> *Pick the `tracking_pack_id` with the highest recipient count in the last 90 days. For that pack, produce one row with:
> - pack_id, cluster_id, publish_date
> - emails sent, opened, clicked (from iMEP)
> - page views, unique readers, avg time-on-page (from AppInsights / fact_page_view)
> - creator full name (from HR).*

→ Proof-of-Concept für die Gold-Cross-Channel-Query, gegen echte Daten.

### Q20 — Row-Count-Reconciliation

> *For the last 30 days, compare per `tracking_pack_id`:
> - sum of sends in `TBL_EMAIL_RECEIVER_STATUS`
> - sum of clicks in `TBL_ANALYTICS_LINK`
> - sum of page views with matching `tracking_pack_id`.
> Flag any pack where page views exceed clicks by more than 2x, or where sends exist but no telemetry.*

→ Data-Quality-Gate vor Dashboard-Wiring.

---

## I — Gold-First Viability (Q16–Q20)

Hintergrund: Laut User enthält Gold **zwangsläufig** Engagement-Aggregate (Sends/Opens/Clicks in iMEP, PageViews in SharePoint) — sonst könnte das bestehende Power-BI Semantic Model sie gar nicht darstellen. Unsere Q1b-Suche auf `tbl_pbi_platform_mailings` war zu eng. Ziel dieser Sektion: **die tatsächlich konsumierten Gold-Tabellen finden**, um `gold.fact_cross_channel` als reinen Gold-JOIN zu bauen (kein Bronze-ETL).

### ~~Q16~~ ✅ Alle Tabellen in `imep_gold` **(gelöst 2026-04-17)**

> *List ALL tables AND views in the `imep_gold` schema …*

→ **Antwort**: 5-Tier-Architektur gefunden. Tier 1 = atomic `Final` (525M). Tier 2 = Rolling timespan aggs (`tbl_pbi_date*`, `_divarea*`, `_regcntry*`, `_deviceTypeall`) — pattern `EngagementType + Count`, keine separaten Open/Click-KPIs. **Tier 3 = Engagement-Summaries MIT UniqueOpens/UniqueClicks**: `tbl_pbi_engagement` (Mailing×Link, 1.38M), `tbl_pbi_mailingreciever_region` (697K), `tbl_pbi_mailingreciever_division` (290K). Tier 4 = Platform Metadata. Tier 5 = Reference. Daily refresh; Aggregationen ohne Historisierung. 66% NULL-Region als Data-Quality-Blocker. **Vollständige Tabellenliste**: siehe [memory/imep_gold_full_inventory.md](../../../.claude/projects/-Users-micha-Documents-Arbeit-Databricks/memory/imep_gold_full_inventory.md).

### Q16b — `Final`-Tabelle: Grain und Partitionierung **(Follow-up)**

> *For `imep_gold.Final` (525M rows, 30 columns): show complete column list with types. Is the table partitioned (by date, by MailingId, by EventType)? Show `MIN/MAX(send_datetime)` to understand temporal coverage. Show distinct values and row counts for `EventType` and `EngagementType` columns.*

→ Verstehen, ob wir `Final` direkt für Ad-hoc-Queries nutzen können oder ob Tier 2/3 immer vorzuziehen sind.

### Q16c — `tbl_pbi_kpi` Schema **(Follow-up)**

> *Full column list with types for `imep_gold.tbl_pbi_kpi` (244K rows). Which KPI columns exist (open rate, click rate, bounce rate, unsubscribe rate)? Is it per mailing or per (mailing, segment)?*

→ Könnte ein direkter Pack-Level-Summary sein, der uns die Silver-Aggregation erspart.

### Q16d — NULL-Region Root-Cause **(Data Quality, kritisch)**

> *In `imep_gold.tbl_pbi_mailingreciever_region`: for the 66% of recipients with NULL region — what is the distribution of their `MailingId` (is it concentrated in certain campaigns)? Join via MailingId to `tbl_pbi_platform_mailings` and show the distribution by CreationDate year. Also: for a sample of 100 affected TNumbers, check directly against `tbl_hr_employee` — do they exist there? If yes, what's their `ORGANIZATIONAL_UNIT`, and does that OU exist in `tbl_hr_costcenter`?*

→ Entscheidet die Fix-Strategie: veraltete Cost-Center-Snapshots? Ex-Employees? Oder ein echtes Reference-Data-Gap?

### ~~Q17~~ ✅ Existieren weitere Gold-Schemas **(gelöst 2026-04-17)**

→ **Antwort**: **SharePoint-Gold existiert** (`sharepoint_gold`, 20 Tabellen, ~270M+ rows). Schlüssel: `pbi_db_interactions_metrics` (84M, 11 cols), `pbi_db_pageviewed_metric` (84M, 5 cols), `pbi_db_pagevisited_metric` (81M, 9 cols), `pbi_db_employeecontact` (24M, 17 cols), `pbi_db_datewise_overview_fact_tbl` (7.5M, 31 cols). Separat: **`sharepoint_clicks_gold`** mit `pbi_db_ctalabel_intr_fact_gold` (3M, CTA-Labels). **CPLAN-Daten bereits in Databricks** (`sharepoint_cplan`): Clusters (17), Packs (280), Activities (4,349). **`pbi_gold` (60 Tabellen, inaccessible)** — Zugriff anfragen, vermutlich Semantic Model. Vollständig: siehe [memory/sharepoint_gold_inventory.md](../../../.claude/projects/-Users-micha-Documents-Arbeit-Databricks/memory/sharepoint_gold_inventory.md).

### Q17b — TrackingID-Spalten in `sharepoint_gold` **(Follow-up)**

> *For each table in `sharepoint_gold` (`pbi_db_interactions_metrics`, `pbi_db_pageviewed_metric`, `pbi_db_pagevisited_metric`, `pbi_db_page_visitedkey_view`, `pbi_db_employeecontact`, `pbi_db_90_days_interactions_metric`, `pbi_db_datewise_overview_fact_tbl`): list all column names with data types. For each table, identify: (a) which column joins back to `sharepoint_bronze.pages` (page_id or URL) to retrieve `UBSGICTrackingID`; (b) whether the metric table itself already carries a TrackingId column.*

→ Entscheidet, ob wir den Gold-Metric direkt per TrackingID joinen oder via page_id → pages.UBSGICTrackingID.

### Q17c — `pbi_db_datewise_overview_fact_tbl` als Pre-Joined Fact **(Follow-up)**

> *Full column list for `sharepoint_gold.pbi_db_datewise_overview_fact_tbl` (7.5M rows, 31 cols). Is this already a pre-joined fact (date × page × employee × metrics)? Show 3 sample rows with all columns to understand what it aggregates.*

→ Wenn das ein Pre-Joined Fact ist, ist unser Gold-Cross-Channel-Build fast fertig.

### Q17d — `pbi_db_employeecontact` als alternative HR-Bridge **(Follow-up)**

> *Full schema of `sharepoint_gold.pbi_db_employeecontact` (24M rows, 17 cols). Does it contain both TNumber and GPN/WORKER_ID? Can we use it instead of `imep_bronze.tbl_hr_employee` for the identity bridge (might be faster / pre-cleaned)?*

→ Vereinfacht `silver.dim_employee_temporal`, wenn die Auflösung hier bereits fertig ist.

### Q17e — CPLAN-Daten in `sharepoint_cplan` direkt nutzbar? **(Follow-up)**

> *Full column list for `sharepoint_cplan.communicationspacks_bronze`, `_internalcommunicationactivities_bronze`, `_trackingcluster_bronze`. For each: are the relationships documented (FK columns)? Check that `trackingcluster_bronze.cluster_id` matches the first segment of `TrackingId` values in `imep_gold.tbl_pbi_platform_mailings`.*

→ Bestätigt, dass wir CPLAN direkt aus Databricks nutzen können (kein Import aus externem CPLAN-Repo nötig).

### Q17f — `pbi_gold` Access-Request **(Governance)**

> *We observe that `pbi_gold` schema exists with 60 tables but all return row/col count = -1 (access denied). This schema is likely central for the corporate Power BI semantic model. Question for admin: (a) who owns this schema? (b) what's the access-request process? (c) is read-only access possible for cross-channel reporting?*

→ Nicht direkt an Genie — sondern an den Catalog-Admin. Diese Frage gehört in die Governance-Sektion des BRD (OP-34 neu).

### Q18 — Pre-joined Cross-Channel-Views

> *Search the entire catalog for tables/views whose name contains `cross_channel`, `multi_channel`, `campaign_performance`, `communication_performance`, `funnel`, `journey`, `attribution`, `pack_performance`, `semantic`, `reporting_layer`. For each hit show schema.table, column count, row count, and 3 column names that reveal its purpose.*

→ Best Case: das Cross-Channel-Modell existiert schon.

### Q19 — Beziehung `tbl_pbi_platform_mailings.EventId` ↔ `tbl_pbi_platform_events.Id`

> *In `imep_gold.tbl_pbi_platform_mailings`: what percentage of rows have a non-null `EventId`? For those, verify that the `EventId` matches an existing `tbl_pbi_platform_events.Id` (JOIN hit rate). Also show: of mailings linked to an event, does the mailing's `tracking_pack_id` (first 2 segments of TrackingId) equal the event's `tracking_pack_id`?*

→ Bestätigt, ob die `EventId`-FK direkt nutzbar ist.

### Q20 — Power-BI / Semantic Model als authoritative Quelle (neu, kritisch)

> **Kontext**: Das bestehende Power-BI-Dashboard zeigt Opens/Clicks/Views — konsumiert also bestimmte Gold-Tabellen. Diese sind unser authoritative Starting-Point.

> *Can you inspect the Databricks Lakehouse Monitoring / Unity Catalog lineage for any downstream Power BI dataset consumers? Specifically:*
> *a) List all tables in any `*_gold` schema that are referenced by a Power BI / Lakeflow / semantic model downstream.*
> *b) If lineage is not available: list all tables in `imep_gold` and `sharepoint_*` that have been queried in the last 30 days (via query history / audit log). The frequently-read ones are the ones Power BI hits.*
> *c) For the top 5 most-queried tables: show column list so we can identify engagement metrics (count columns, *_sent, *_opened, *_clicked, *_views).*

→ Kürzester Weg zu den richtigen Gold-Tabellen: schauen, welche Power-BI bereits aktiv abfragt. Falls Genie keine Lineage hat: die Tabellen, die in Queries am häufigsten auftauchen, sind die "live" genutzten.

---

## J — Build Readiness für Cross-Channel-JOIN (Q21–Q24)

Ziel: Die letzten Schema-Details und Volume-Checks, die wir brauchen, um den konkreten SQL-Build von `gold.fact_cross_channel` zu starten. Plan: Email via `imep_gold.tbl_pbi_platform_mailings` + `tbl_pbi_mailingreciever_region` ↔ Intranet via `sharepoint_gold.pbi_db_*_metric` + `sharepoint_bronze.pages`, gejoint über abgeleiteten `tracking_pack_id`.

### Q21 — iMEP Gold Tier 3 Schemas (JOIN-kritisch)

> *For the following iMEP Gold tables, return full column list with types and nullability:*
> *- `imep_gold.tbl_pbi_mailingreciever_region`*
> *- `imep_gold.tbl_pbi_mailingreciever_division`*
> *- `imep_gold.tbl_pbi_engagement`*
> *- `imep_gold.tbl_pbi_kpi`*
>
> *For each, identify the exact column name that joins back to `imep_gold.tbl_pbi_platform_mailings.Id` (is it `MailingId`, `email_id`, `Id`, or something else?). Confirm the types of `UniqueOpens`, `UniqueClicks`, `Count` — are they nullable (per Q16-note "intentional NULLs for no tracked engagement")?*

→ Ohne diese Spalten-Details kann der `JOIN ON r.MailingId = m.Id` nicht konkret formuliert werden.

### Q22 — SharePoint Gold Metric-Schemas

> *For the following SharePoint Gold tables, return full column list with types and nullability:*
> *- `sharepoint_gold.pbi_db_interactions_metrics` (84M × 11 cols)*
> *- `sharepoint_gold.pbi_db_pageviewed_metric` (84M × 5 cols)*
> *- `sharepoint_gold.pbi_db_pagevisited_metric` (81M × 9 cols)*
> *- `sharepoint_gold.pbi_db_datewise_overview_fact_tbl` (7.5M × 31 cols)*
>
> *For each: (a) identify the page-identifying FK column (`page_id`, `pageId`, `url_hash`, etc.) that joins back to `sharepoint_bronze.pages`; (b) check whether any of these tables carries a direct `GICTrackingID` / `UBSGICTrackingID` column (so we can skip the pages-JOIN).*

→ Entscheidet, ob Intranet-Aggregat direkt per TrackingID joinbar ist oder via `page_id → pages.UBSGICTrackingID` resolved werden muss.

### Q23 — TrackingId-Format-Konsistenz zwischen iMEP und SharePoint

> *Take 100 sample values of `TrackingId` from `imep_gold.tbl_pbi_platform_mailings` and 100 samples of `UBSGICTrackingID` from `sharepoint_bronze.pages`. Do NOT return the sample values — return only a structural comparison:*
> *(a) Do both have the same length (32 chars)?*
> *(b) Do both use the same segment structure (5 segments, dash-separated)?*
> *(c) Is case consistent across both (all uppercase? mixed?)?*
> *(d) Any leading/trailing whitespace in either column?*
> *(e) Count how many TrackingIds appear in both tables (intersection size) vs only in one.*

→ Falls Formate divergieren, brauchen wir einen `UPPER(TRIM(...))` oder Regex-Normalize im JOIN. Intersection-Count ist die echte Coverage-Zahl für den Cross-Channel-Funnel.

### Q24 — Volumen-Check mit `TrackingId IS NOT NULL`-Filter (Performance-relevant)

> *In `imep_gold.tbl_pbi_platform_mailings`:*
> *(a) How many rows have `TrackingId IS NOT NULL` vs NULL?*
> *(b) Break down the NOT-NULL count by `YEAR(CreationDate)` for the last 5 years.*
> *(c) Of the rows with TrackingId, what percentage has the channel-suffix `-EMI` (= email)? And what's the distribution of the other suffixes (EVT, BAN, etc.)?*
> *(d) Assuming we filter `WHERE TrackingId IS NOT NULL AND RIGHT(TrackingId, 3) = 'EMI'` — how many distinct `tracking_pack_id` (first 2 segments) does that leave us?*

→ Zeigt wie viel vom 278M-Recipients-Universum nach dem Filter für unsere Email-Seite übrig bleibt, und wie viele distinct Packs wir im Dashboard zu erwarten haben.

### Q25 — UBSGICTrackingID-Verteilung in `pages` (Zeit + Site-Breakdown)

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

→ Verfeinert Q22: Die 4%-Coverage global wird aufgeschlüsselt nach Zeit und Site. Ziel: (1) realistischer Dashboard-Default-Zeitraum, (2) Pareto-Subset der Sites, auf dem Coverage tatsächlich hoch genug für einen glaubwürdigen Funnel ist.

---

## F — Pipeline-Lineage ohne Unity Catalog (Q26-Q30)

> **Kontext 2026-04-20**: Wir haben weder UC-Lineage-Graph noch `system.access.table_lineage`. Bronze → Gold Pipeline-Struktur muss rein aus SQL gegen Hive Metastore + Delta-Table-Metadaten rekonstruiert werden. Ziel: Data-Card-Lineage-Sektionen (siehe `docs/tables/`) mit echten Transformations-Jobs und Refresh-Cadence füllen — statt Annahmen.

### ~~Q26~~ ✅ Silver-Existenz über Schema-Naming **(OP-lineage-a — gelöst 2026-04-20)**

> *Run `SHOW DATABASES` (or `SHOW SCHEMAS`). List every schema whose name contains any of: "silver", "stage", "staging", "curated", "std", "standardized", "int", "intermediate", "harmonized", "conformed", "enriched". For each match, return schema name and `COUNT(*)` of tables via `SHOW TABLES IN <schema>`. If none exist, confirm that bronze writes directly to gold with no intermediate layer.*

→ **Antwort**: **17 Silver-Schemas existieren**, alle suffix `_silver`. **Zero hits** für alternative Namens-Pattern (stage/staging/curated/std/standardized/intermediate/harmonized/conformed/enriched). Medallion-Pattern ist **asymmetrisch**:
> - `bronze → silver → gold` für SharePoint, Adobe, Dynamics, Ads
> - `bronze → gold` (skip silver) für **Email-Engagement (iMEP)** → direkter Write in `imep_gold.final` (~520M rows, denormalisierter Join, HR-Enrichment spät)
>
> **`sharepoint_silver`** (5 Tabellen): `webpageviewed` ~262M · `pageviewed` ~136M · `pageposted` ~105M + Dims `webpage`, `website`. → für unsere Cross-Channel-SQL die SharePoint-Quelle der Wahl.
>
> **`imep_silver`** existiert nur für Events: `invitation`, `eventregistration` ~13.7M, `event` ~84K. **Kein Silver für Email.**
>
> Weitere relevante Silvers: `adobe_silver` (15), `dynamics_silver` (7), `email_campaign_forms_silver` (5), `adform_silver` (3), `profile_silver` (~6), `linkedin_silver`, `google_silver`, `facebook_silver`, `oii_silver`, `bid_silver`, `persona_data_silver`.
>
> **Konsequenz**: `silver.fact_email` NICHT bauen — iMEP hat sich bewusst dagegen entschieden. `imep_gold.final` direkt konsumieren. Volldetails: [memory/imep_silver_q26_findings.md](../../../.claude/projects/-Users-micha-Documents-Arbeit-Databricks/memory/imep_silver_q26_findings.md).

### ~~Q27~~ ✅ Lineage über Column-Fingerprint **(OP-lineage-b — gelöst 2026-04-20)**

> *Across ALL schemas in the workspace, find every table that contains at least one of these columns (case-insensitive match on column name): `Id`, `EmailId`, `mailingid`, `MailingId`, `TrackingId`, `UBSGICTrackingID`, `GICTrackingID`, `TNumber`, `T_NUMBER`, `WORKER_ID`.*
>
> *For each hit return: schema, table, matching_columns, row_count, MIN/MAX of the earliest timestamp column (`CreationDate`, `DateTime`, `ModifiedDate`, `_ingestion_ts`, or similar).*
>
> *Goal: reconstruct join-key relationships between bronze and gold purely from column overlap + row counts.*

→ **Antwort**: **93 Tabellen / 8 Schemas** haben Treffer. Kern-Findings:
>
> **Zwei einzige Full-Key-Fact-Hubs** (Id + EmailId + TNumber):
> - `imep_bronze.tbl_analytics_link` — **533M** rows
> - `imep_bronze.tbl_email_receiver_status` — **293M** rows
>
> **Row-Counts bestätigt**: `imep_gold.final` 520M · `sharepoint_bronze.pageviews` 173M · `tbl_email` 145K · `tbl_pbi_platform_mailings` 73K · `sharepoint_bronze.pages` 48K · `tbl_hr_employee` 265K.
>
> **Join-Graph-Topologie**:
> - `EmailId` → **12 Tabellen** (Backbone für Email-Domain)
> - `TNumber` → **nur 2 Tabellen** → Person-Level-Analytics existieren ausschliesslich auf Email-Engagement-Grain
> - `TrackingId` → **exakt 4 Tabellen** (tbl_email, tbl_event, tbl_pbi_platform_mailings, tbl_pbi_platform_events), **nie** zusammen mit EmailId → TrackingId ist **Dimension**, nicht Fact-Key
> - 26 `imep_gold.tbl_pbi_*` joinen alle zurück: `MailingId = tbl_pbi_platform_mailings.Id = tbl_email.Id`
>
> **Cross-Channel**: Email ↔ SharePoint geht **dimensional** (TrackingId ↔ GICTrackingID auf `pages`), **nicht** über Engagement-Rows. SharePoint hat kein Person-Key-Equivalent zu TNumber.
>
> **Neues Schema gesichtet**: `page_metadata_bronze` (pagelikesview 40K, comments 3.4K, reportedcomments, moderatedcomments, metadata-pageviews 293) — separat vom main pageview-Fact.
>
> Volldetails: [memory/imep_join_graph_q27_findings.md](../../../.claude/projects/-Users-micha-Documents-Arbeit-Databricks/memory/imep_join_graph_q27_findings.md).

### ~~Q28~~ ✅ Pipeline-Hinweise aus Delta-History **(OP-lineage-c — gelöst 2026-04-20)**

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

→ **Antwort** (110 History-Rows über 11 Tabellen):
>
> **Einheitliches Rhythmus-Muster**: `MERGE/WRITE → OPTIMIZE (Z-Order) → VACUUM`, 7-Tage-Retention.
>
> **iMEP Bronze — MERGE Upsert 2×/day @ 00:00/12:00 UTC**:
> - `tbl_email`: ~145K full-table upsert
> - `tbl_email_receiver_status`: 27–72M per run (full upsert)
> - `tbl_analytics_link`: **3.7–8.5K per run → echt inkrementell** (neue Click-Events)
>
> **SharePoint Bronze — 1×/day @ 02:00 UTC**:
> - `pages`: MERGE Daily Snapshot (48K)
> - `pageviews`: **Append WRITE, 7 Bursts innerhalb 1 Minute** (API-Pagination-Pattern, micro-batched, **kein** Streaming)
>
> **iMEP Gold — CTAS Full Rebuild 2×/day @ ~00:23/12:25 UTC**:
> - **Keine Inkrementalität** — jede Gold-Tabelle komplett zerstört und neu aufgebaut
> - **520M-Row-Tabelle** (Label `tbl_pbi_platform_mailings` im Image — ⚠️ widerspricht Q27's 73K, wahrscheinlich `imep_gold.final`, durch Q30 zu klären) wird 2× täglich voll neu geschrieben → **grösster Compute-Kostenpunkt**
> - Weitere Gold-CTAS: `tbl_pbi_mailings_region` (73,530), `tbl_pbi_mailings_division` (697,787), `tbl_pbi_analytics` (290,723), `tbl_pbi_kpi` (245,040), +1 (1,384,800)
>
> **Writer**: `userName` überall leer → **Service Principal** (SPN-Präfix `a71734ea-...`). Vollautomatisierte Pipeline, kein menschlicher Writer.
>
> **Dashboard-Implikation**: Refresh-Window-Risk um 00:23/12:25 UTC (Gold-CTAS-Phase) — Queries in diesem Fenster können auf partiellen/inkonsistenten Stand treffen. Cache oder Schedule drum herum.
>
> **Optimierungs-Hinweis** (nicht unsere Baustelle): Gold CTAS → Incremental MERGE würde Compute deutlich reduzieren.
>
> Volldetails: [memory/imep_pipeline_ops_q28_findings.md](../../../.claude/projects/-Users-micha-Documents-Arbeit-Databricks/memory/imep_pipeline_ops_q28_findings.md).

### ~~Q29~~ ✅ Tier-Struktur aus Tabellen-Shape ableiten **(OP-lineage-d — gelöst 2026-04-20)**

> *For every table in `imep_gold` AND `sharepoint_gold`, return: table name, all column names, row count, `COUNT(DISTINCT <id-like column>)`, presence of dimension/metric columns as boolean flags. Goal: bucket tables by grain.*

→ **Antwort**: 52 Gold-Tabellen in strikter 4-Tier-Hierarchie. **iMEP Gold (31 Tabellen)**:
> - **Tier 0** — Atomic Fact (1 Tabelle): `final` (520M, grain `mailing × recipient × event × hour`) — **löst die Q28-Label-Verwirrung endgültig** ✅
> - **Tier 1** — Timespan × Dimension Aggregates (15 Tabellen): 24h/72h/1w/15w/15m+, Date/DateHour/DivArea/RegCntry
> - **Tier 2** — Per-Mailing Summaries (~8 Tabellen): `mailingreceiver_*`, `engagement` (1.8M, **117K distinct mailingIds → emails+events gemixt**), `log_mail`
> - **Tier 3** — Platform & Reference Dimensions (~7): `tbl_pbi_platform_mailings` (73,920 Rows, **927 TrackingIds**), `tbl_pbi_platform_events` (84,052), Mailing-Access, Logins etc.
>
> **SharePoint Gold (20 Tabellen)**:
> - Tier 0 — Atomic Interaction Facts (4): `interactions_metrics` 84M, `pageviewed_metric`, `pagevisited_metric`, `90_days`
> - Tier 1 — Pre-Aggregated Overview (3): `datewise_overview_fact_tbl` (7.5M, 27 rolling-window stats)
> - Tier 2 — Engagement/Social (3): `pageliked_metric`, `pagecommented_metric`, `page_like_int_fact`
> - Tier 3 — Dimensions (7): `employeecontact` (24.3M, Person-Bridge-Kandidat), `website_page_inventory`, Calendar, Referer-App
> - **Video Sub-Domain (3 Tabellen)**: `fact_video_engagement_gold` etc. — die einzigen managed-Declarative-Pipeline-Tabellen im ganzen System
>
> **Cross-System-Join-Mechanismus**: iMEP Gold + SP Gold haben **keine shared FK** — einzige Brücke ist `tbl_pbi_platform_mailings.TrackingId → pages.UBSGICTrackingID → pages.pageUUID → marketingPageId`, und sie funktioniert nur nach SEG1-4-Match.
>
> **Mental Model**: iMEP Gold = message-centric per-recipient; SP Gold = page-centric analytics; TrackingId = einzige konzeptuelle Brücke auf Dimensions-Ebene.
>
> Volldetails: [memory/imep_sp_gold_tiers_q29_findings.md](../../../.claude/projects/-Users-micha-Documents-Arbeit-Databricks/memory/imep_sp_gold_tiers_q29_findings.md).

### ~~Q30~~ ✅ Tabellen-Properties & Storage-Location **(OP-lineage-e — gelöst 2026-04-20)**

> *For every table in `imep_bronze`, `imep_gold`, `sharepoint_bronze`, `sharepoint_gold`, run `DESCRIBE EXTENDED` and return Detailed Table Information. The ADLS path often encodes the producing pipeline name.*

→ **Antwort**: 114 External Delta Tables in **3 ADLS-Accounts** (iMEP-Bronze, SP-Bronze, Gold gemeinsam):
>
> **Pipeline-Fingerprints**:
> - iMEP Bronze: 49 Tabellen, uniform path `abfss://bronze@<iMEP-bronze-acc>/.../imep/<TABLE>` → **1 Ingestion-Job**
> - iMEP Gold: 31 Tabellen via CTAS in `/final` + `/tbl_pbi/*` (+ 1 Outlier `/imep/tbl_active_employee_month`) → **1 Orchestration-Notebook**
> - SP Bronze: 12 Tabellen + Historical-Snapshots (`pageviews_09_27_2023`, `pageviews_08022024` etc.) → **1 Ingestion-Pipeline mit ad-hoc Snapshot-Folders**
> - SP Gold: **zwei Pipeline-Familien** — Family 1 "Employee Analytics" (16 Notebooks-CTAS-Tabellen in `/employee_analytics/pbi_db_*`, Spark 3.2.1) + Family 2 "Video Analytics" (3 Tabellen unter **managed Declarative Pipeline**, Spark 3.5.2/4.0.0, pipelines.pipelineId, ChangeDataFeed enabled, proper table comments)
>
> **3 strukturelle Befunde**:
> 1. **Pipeline-Reality-Check**: Von 114 Tabellen sind **nur 3 (Video) managed Declarative Pipelines** — die anderen 111 sind Notebook-basierte Jobs ohne Pipeline-Metadata. Folder-Layout suggeriert 2–3 grosse Orchestrierungs-Notebooks statt layered framework.
> 2. **⚠️ Zero Partitioning across all 114 tables** — besonders impactful für `imep_gold.final` (520M, CTAS-rebuilt 2×/Tag) und `sharepoint_gold.interactions_metrics` (84M). Partitionierung by date würde CTAS-Kosten massiv reduzieren und Power-BI-Latency verbessern. **Biggest structural performance gap.**
> 3. **Gold Co-Location ist intentional**: Beide Gold-Schemas in **einem** ADLS-Gold-Account → Cross-System-Joins inside Fabric/Spark direkt möglich, keine Cross-Account-Auth nötig. Deliberate architectural alignment für Analytics.
>
> **One-Page-Summary**: Physical layout zeigt die Architektur-Wahrheit — alles ist notebook-driven (ausser Video), Gold wird wholesale rebuilt (nicht incremental), Keine Partitionierung ist der grösste Performance-Gap, Gold-Co-Location ist korrektes Design für Cross-Channel.
>
> Volldetails: [memory/storage_architecture_q30_findings.md](../../../.claude/projects/-Users-micha-Documents-Arbeit-Databricks/memory/storage_architecture_q30_findings.md).

**Was Genie NICHT beantworten kann** (per Workspace-UI oder REST-API zu klären):
- Job-Definitionen & Scheduler → Jobs-UI bzw. `/api/2.1/jobs/list`
- Notebook-Inhalte (die eigentliche Transformations-SQL) → Workspace-Browser
- Service-Principal → Job-Mapping → Workspace-Admin-Bereich

---

## Nutzungshinweise

1. **Reihenfolge**: Q1 und Q3 zuerst — sie entscheiden, ob das Modell überhaupt baubar ist. Q19/Q20 am Schluss als Validierung.
2. **Genie-Session**: Fragen in einer Session stellen — Genie merkt sich Kontext (Tabellen, Filter) und verkettet sauber.
3. **Output sichern**: Jede Antwort als Notebook-Cell oder Screenshot sichern, dann im BRD §9 als "✅ gelöst" markieren.
4. **Fragen-Follow-up**: Wenn Genie nicht liefert (z.B. "column not found"), direkt fragen: *"Which tables in this catalog contain a column named like 'tracking_id' or 'camms_tracking_id'?"* — Genie kann das Information-Schema durchsuchen.
