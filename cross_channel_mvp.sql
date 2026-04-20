-- =============================================================================
-- Cross-Channel MVP — tracking_pack_id grain
-- =============================================================================
-- Status: Q16 + Q17 + Q21 + Q22 + Q23 + Q24 + Q25 — ALL READINESS QUESTIONS ANSWERED.
-- =============================================================================
--
-- Q25 CONFIRMED (2026-04-20, IMG_7390..7393):
--   • Overall Page-Coverage: 1,959 / 48,419 (4.05%). 99.9% der tracked pages haben auch UBSArticleDate.
--   • 🎯 99.4% der tracked pages sitzen auf EINER Site: "News and events" (1,947 pages, 83 Packs).
--     Die restlichen 12 Pages sind Test-/Edge-Artefakte (Training playground, ORM APAC, etc.).
--   • Site-FK bestätigt: pages.SPT_Siteld = sites.SPT_Siteld (100% match, 48,419/48,419).
--   • Rollout-Zeitlinie: vor Jul 2024 praktisch 0% → Sep 2024 Inflection (33.9%)
--       → Okt-Dez 2024: 47-65% → 2025: 35-62% volatile → Peak Mar 2026 (70.5%), NIE 80%.
--   • 1:1 PageURL ↔ TrackingID Mapping: 0 URLs mit multiple TIDs → stabile Identity.
--   ⇒ Dashboard-Konsequenzen:
--     a) Site-Filter: WHERE pages.SiteName = 'News and events' (99.4% Coverage, 83 Packs)
--     b) Effective Date Floor: 2024-09-01 (Rollout-Inflection, dominant über iMEP-2025-Floor)
--     c) Coverage-KPI im Dashboard Pflicht ("tracked/total articles this month")
--
-- Q24 CONFIRMED (2026-04-20, IMG_7387..7389):
--   • TrackingId-Befüllungsgrad in platform_mailings: NUR 986 / 73,930 (1.30%).
--     Rest (98.7%) ist NULL → TrackingId-basierte Analyse deckt standardmässig 1.3% ab.
--   • Jahres-Adoption: 2024=99, 2025=637, 2026 YTD (3.5 Mo)=250 → Run-Rate ~860/Jahr.
--     ⇒ Default-Zeitfenster im Dashboard: ab 2025-01-01.
--   • Suffix-Verteilung: EMI=80.2%, TWE=8.9%, NLI=8.6%, Rest <2%.
--     ⇒ EMI-Filter ist unstrittig die richtige Wahl.
--   • 🎯 DASHBOARD-UNIVERSUM: 791 EMI-Rows → 696 distinct TIDs → nur 54 distinct Pack-IDs.
--     ⇒ Pack-Level-Aggregat liefert ≤54 Rows. Ø 13 Varianten pro Pack kollabiert durch SEG1-2-Cut.
--
-- Q23 CONFIRMED (2026-04-20, IMG_7384..7386):
--   • Format identisch zwischen TrackingId und UBSGICTrackingID: 32 chars, 5 segments (5-7-6-7-3),
--     alles UPPERCASE, keine Whitespace-Issues → UPPER/TRIM defensiv ok, funktional unnötig.
--   • ⚠️ Werte fast DISJOINT: Jaccard=0.004, nur 6 Exact-Matches aus 1,677 distinct IDs.
--     → TrackingId ist KEIN direkter FK. Es ist ein shared FORMAT, kein shared ID-SPACE.
--   • Channel-Suffix (SEG5) kodiert System-Ownership:
--       iMEP:     EMI (81%), NLI (9%), TWE (8%)
--       SharePoint: IAN (38%), ITS (34%), OPN (19%), ANC (5%)
--   • Korrekte Linking-Strategie: match SEG1-4, ignore SEG5.
--     Für unseren Pack-Grain (SEG1-SEG2) passt das automatisch.
--
-- Q21 CONFIRMED (2026-04-20, IMG_7375..7378):
--   tbl_pbi_mailingreciever_region   → MailingId, GCRS_REGION_NAME, GCRS_CNTRY_NAME, Count, UniqueOpens, UniqueClicks
--   tbl_pbi_mailingreciever_division → MailingId, GCRS_BUSN_DIV_NAME, Count, UniqueOpens, UniqueClicks
--   tbl_pbi_engagement               → mailingid (LOWERCASE!), senderlanguage, linklabel, sourcecomponentname, url, Count, componentorder, Linkorder, ComponentName
--   tbl_pbi_kpi                      → MailingId, MailingTitle, MailingCreatedBy, MailingReceiverStatus, OverallMailingStatus, StartsendDateTime, measure, RecipientLanguage (PIVOTED)
-- Match rates to platform_mailings.Id: region 97.7%, division 98.1%, engagement 72.2%, kpi 97.8%
-- NULL semantics: UniqueOpens NULL 28-35%, UniqueClicks NULL 66-81% → COALESCE(..., 0) vor SUM Pflicht.
--
-- Q22 CONFIRMED (2026-04-20, IMG_7379..7383):
--   SharePoint Gold Metriken (interactions_metrics, pageviewed_metric, pagevisited_metric, datewise_overview_fact_tbl)
--   teilen ALLE den FK `marketingPageId` (string GUID)
--   → sharepoint_bronze.pages.pageUUID
--       → pages.UBSGICTrackingID
--           → imep_gold.tbl_pbi_platform_mailings.TrackingId
--   KEIN direktes TrackingID auf Gold-Metriken.
--   ⚠️ COVERAGE-BLOCKER: nur 1,949 / 48,419 Pages (~4%) haben UBSGICTrackingID.
--   Dominante Gold-Metric-Cols: marketingPageId, visitdatekey, viewingcontactid, views, visits, durationsum.
--
-- Offen: Q23 (Format-Konsistenz), Q24 (Volumen/EMI-Share).


-- -----------------------------------------------------------------------------
-- PART A — Optional CPLAN/platform_mailings schema probes
-- -----------------------------------------------------------------------------

DESCRIBE TABLE imep_gold.tbl_pbi_platform_mailings;       -- confirm TrackingId column exists
DESCRIBE TABLE sharepoint_cplan.internalcommunicationactivities_bronze;
DESCRIBE TABLE sharepoint_cplan.communicationspacks_bronze;
DESCRIBE TABLE sharepoint_cplan.trackingcluster_bronze;


-- -----------------------------------------------------------------------------
-- PART B — Q23/Q24 in-SQL probes (Format-Konsistenz + TrackingId Coverage)
-- -----------------------------------------------------------------------------

-- Q23 — Format probe (struktur, KEIN Werte-Leak):
WITH imep AS (
  SELECT TrackingId AS tid FROM imep_gold.tbl_pbi_platform_mailings
  WHERE TrackingId IS NOT NULL
),
sp AS (
  SELECT UBSGICTrackingID AS tid FROM sharepoint_bronze.pages
  WHERE UBSGICTrackingID IS NOT NULL
)
SELECT 'imep' AS src,
       COUNT(*) AS n,
       AVG(LENGTH(tid))                                   AS avg_len,
       SUM(CASE WHEN LENGTH(tid) = 32 THEN 1 ELSE 0 END)  AS n_len32,
       SUM(CASE WHEN tid = UPPER(tid) THEN 1 ELSE 0 END)  AS n_upper,
       SUM(CASE WHEN tid <> TRIM(tid) THEN 1 ELSE 0 END)  AS n_whitespace,
       SUM(CASE WHEN SIZE(SPLIT(tid,'-')) = 5 THEN 1 ELSE 0 END) AS n_5seg
FROM imep
UNION ALL
SELECT 'sharepoint', COUNT(*),
       AVG(LENGTH(tid)),
       SUM(CASE WHEN LENGTH(tid) = 32 THEN 1 ELSE 0 END),
       SUM(CASE WHEN tid = UPPER(tid) THEN 1 ELSE 0 END),
       SUM(CASE WHEN tid <> TRIM(tid) THEN 1 ELSE 0 END),
       SUM(CASE WHEN SIZE(SPLIT(tid,'-')) = 5 THEN 1 ELSE 0 END)
FROM sp;

-- Q23 — Intersection Count (echte Cross-Channel-Coverage)
SELECT COUNT(*) AS overlap_packs
FROM (
  SELECT DISTINCT CONCAT_WS('-',
           SPLIT_PART(UPPER(TRIM(TrackingId)),'-',1),
           SPLIT_PART(UPPER(TRIM(TrackingId)),'-',2)) AS pack_id
  FROM imep_gold.tbl_pbi_platform_mailings
  WHERE TrackingId IS NOT NULL
) e
INNER JOIN (
  SELECT DISTINCT CONCAT_WS('-',
           SPLIT_PART(UPPER(TRIM(UBSGICTrackingID)),'-',1),
           SPLIT_PART(UPPER(TRIM(UBSGICTrackingID)),'-',2)) AS pack_id
  FROM sharepoint_bronze.pages
  WHERE UBSGICTrackingID IS NOT NULL
) s USING (pack_id);

-- Q24 — Volumen & Channel-Suffix-Breakdown
SELECT
  COUNT(*)                                              AS total,
  SUM(CASE WHEN TrackingId IS NULL THEN 1 ELSE 0 END)   AS n_null,
  SUM(CASE WHEN TrackingId IS NOT NULL THEN 1 ELSE 0 END) AS n_not_null,
  SUM(CASE WHEN RIGHT(TrackingId,3) = 'EMI' THEN 1 ELSE 0 END) AS n_emi,
  SUM(CASE WHEN RIGHT(TrackingId,3) = 'INT' THEN 1 ELSE 0 END) AS n_int,
  SUM(CASE WHEN RIGHT(TrackingId,3) = 'EVT' THEN 1 ELSE 0 END) AS n_evt,
  SUM(CASE WHEN RIGHT(TrackingId,3) = 'BAN' THEN 1 ELSE 0 END) AS n_ban
FROM imep_gold.tbl_pbi_platform_mailings;


-- =============================================================================
-- PART C — Cross-Channel MVP Query (tracking_pack_id grain)
-- =============================================================================
-- Status:
--  A1 ✅ MailingId = platform_mailings.Id (Q21, 97.7% match on region)
--  A2 ✅ Kein direktes TrackingID auf Gold-Metric — JOIN via
--        metrics.marketingPageId = pages.pageUUID, dann pages.UBSGICTrackingID (Q22)
--  A3 ✅ NULLs in UniqueOpens/UniqueClicks sind semantisch → COALESCE(..., 0) vor SUM (Q21)
--  A4 ✅ tracking_pack_id = SEG1-SEG2 — Format sauber, UPPER/TRIM defensiv (Q23)
--  A5 ✅ iMEP-Filter `RIGHT(TrackingId, 3) = 'EMI'` deckt 81% der iMEP-TIDs ab (Q23)
--  A6 ✅ SharePoint-Filter `RIGHT(UBSGICTrackingID, 3) IN ('IAN','ITS','OPN','ANC')` (Q23)
--  A7 ⚠️ Nur ~4% der Pages (1,949/48,419) haben UBSGICTrackingID → sparse Coverage (Q22)
--  A8 ⚠️ Exact-TID-Intersection = 6 (Jaccard 0.004)! Pack-Level-JOIN ist die EINZIGE
--        sinnvolle Brücke — direkter TrackingId-Match wäre leer (Q23)
-- =============================================================================

WITH
-- ---------- Email side: iMEP Gold Tier-3 region aggregate (97.7% match, most complete) ----------
email_pack AS (
  SELECT
    CONCAT_WS('-',
      SPLIT_PART(m.TrackingId, '-', 1),
      SPLIT_PART(m.TrackingId, '-', 2)
    ) AS tracking_pack_id,
    COUNT(DISTINCT m.Id)                         AS email_mailings,
    SUM(COALESCE(r.Count, 0))                    AS email_recipients,
    SUM(COALESCE(r.UniqueOpens, 0))              AS email_unique_opens,
    SUM(COALESCE(r.UniqueClicks, 0))             AS email_unique_clicks
  FROM imep_gold.tbl_pbi_platform_mailings m
  LEFT JOIN imep_gold.tbl_pbi_mailingreciever_region r
         ON r.MailingId = m.Id                   -- Q21 confirmed
  WHERE m.TrackingId IS NOT NULL
    AND RIGHT(m.TrackingId, 3) = 'EMI'           -- Q23/Q24: 80% der iMEP-TIDs = Dashboard-Scope
    AND m.CreationDate >= '2024-09-01'           -- Q25: SharePoint-Rollout Sep 2024 = effective floor           -- Q24: Adoption ramp ab 2025 → realistischer Default
  GROUP BY 1
),

-- ---------- SharePoint side: page-view aggregates per tracking_pack_id ----------
-- Q22: JOIN-Pfad ist metrics.marketingPageId → pages.pageUUID → pages.UBSGICTrackingID
-- Only 4% of pages have UBSGICTrackingID → INNER JOIN auf pages mit WHERE-Filter.
-- Wir nehmen `pbi_db_interactions_metrics` (master fact) statt _pageviewed_metric:
--   gleiche Grain, mehr Messgrössen (views, visits, durationsum), selbe marketingPageId.
pages_pack AS (
  SELECT
    p.pageUUID,
    RIGHT(p.UBSGICTrackingID, 3) AS channel_suffix,
    CONCAT_WS('-',
      SPLIT_PART(p.UBSGICTrackingID, '-', 1),
      SPLIT_PART(p.UBSGICTrackingID, '-', 2)
    ) AS tracking_pack_id
  FROM sharepoint_bronze.pages p
  INNER JOIN sharepoint_bronze.sites s
          ON s.SPT_Siteld = p.SPT_Siteld                         -- Q25: 100% match
  WHERE p.UBSGICTrackingID IS NOT NULL                           -- ~1,959 / 48,419 pages
    AND RIGHT(p.UBSGICTrackingID, 3) IN ('IAN','ITS','OPN','ANC') -- Q23: 96% der tracked pages
    AND s.SiteName = 'News and events'                           -- Q25: 99.4% Pareto auf einer Site
    AND p.UBSArticleDate >= '2024-09-01'                         -- Q25: Rollout-Inflection
),
intranet_pack AS (
  SELECT
    pp.tracking_pack_id,
    COUNT(DISTINCT pp.pageUUID)                    AS pages,
    SUM(COALESCE(m.views, 0))                      AS page_views,
    SUM(COALESCE(m.visits, 0))                     AS page_visits,
    SUM(COALESCE(m.durationsum, 0))                AS total_duration_sec,
    COUNT(DISTINCT m.viewingcontactid)             AS unique_readers
  FROM pages_pack pp
  INNER JOIN sharepoint_gold.pbi_db_interactions_metrics m
          ON m.marketingPageId = pp.pageUUID       -- Q22 confirmed
  GROUP BY 1
),

-- ---------- CPLAN pack metadata ----------
pack_dim AS (
  SELECT
    UPPER(TRIM(a.tracking_id))          AS tracking_id,
    CONCAT_WS('-',
      SPLIT_PART(UPPER(TRIM(a.tracking_id)),'-',1),
      SPLIT_PART(UPPER(TRIM(a.tracking_id)),'-',2)
    )                                   AS tracking_pack_id,
    p.pack_name,
    p.pack_theme,
    p.target_region,
    c.cluster_name,
    a.publish_date
  FROM sharepoint_cplan.internalcommunicationactivities_bronze a
  LEFT JOIN sharepoint_cplan.communicationspacks_bronze p ON p.pack_id = a.pack_id
  LEFT JOIN sharepoint_cplan.trackingcluster_bronze    c ON c.cluster_id = a.cluster_id
),
pack_dim_distinct AS (
  SELECT tracking_pack_id,
         ANY_VALUE(pack_name)    AS pack_name,
         ANY_VALUE(pack_theme)   AS pack_theme,
         ANY_VALUE(target_region) AS target_region,
         ANY_VALUE(cluster_name) AS cluster_name,
         MIN(publish_date)       AS publish_date
  FROM pack_dim
  GROUP BY 1
)

-- ---------- Final cross-channel fact ----------
SELECT
  COALESCE(d.tracking_pack_id, e.tracking_pack_id, i.tracking_pack_id) AS tracking_pack_id,
  d.pack_name,
  d.pack_theme,
  d.cluster_name,
  d.target_region,
  d.publish_date,
  -- Email KPIs
  e.email_mailings,
  e.email_recipients,
  e.email_unique_opens,
  e.email_unique_clicks,
  ROUND(e.email_unique_opens  / NULLIF(e.email_recipients,0) * 100, 2) AS open_rate_pct,
  ROUND(e.email_unique_clicks / NULLIF(e.email_recipients,0) * 100, 2) AS click_rate_pct,
  -- Intranet KPIs (~4% coverage caveat per Q22)
  i.pages,
  i.page_views,
  i.page_visits,
  i.total_duration_sec,
  i.unique_readers,
  ROUND(i.total_duration_sec / NULLIF(i.page_visits, 0), 1) AS avg_duration_sec,
  -- Funnel ratios
  ROUND(i.page_views      / NULLIF(e.email_unique_clicks,0) * 100, 2) AS click_to_view_pct,
  ROUND(i.unique_readers  / NULLIF(e.email_recipients,0)    * 100, 2) AS reach_pct
FROM pack_dim_distinct d
FULL OUTER JOIN email_pack    e USING (tracking_pack_id)
FULL OUTER JOIN intranet_pack i USING (tracking_pack_id)
WHERE COALESCE(d.tracking_pack_id, e.tracking_pack_id, i.tracking_pack_id) IS NOT NULL
ORDER BY COALESCE(e.email_recipients, i.page_views, 0) DESC;
-- Erwartung: ~54 Rows Email-Side (Q24), davon eine Teilmenge mit Intranet-Match.


-- =============================================================================
-- PART D — Optional: Region/Division Breakdown per Pack (nach Q21-Struktur)
-- =============================================================================
-- Q21 bestätigt: Region- und Division-Aggregate sind Mailing-Level.
-- Für Pack-Level-Breakdowns nach Region/Division summieren wir über Mailings:

-- Pack × Region Breakdown
WITH email_region_pack AS (
  SELECT
    CONCAT_WS('-',
      SPLIT_PART(m.TrackingId, '-', 1),
      SPLIT_PART(m.TrackingId, '-', 2)
    )                                            AS tracking_pack_id,
    r.GCRS_REGION_NAME                           AS region,
    r.GCRS_CNTRY_NAME                            AS country,
    SUM(COALESCE(r.Count, 0))                    AS recipients,
    SUM(COALESCE(r.UniqueOpens, 0))              AS unique_opens,
    SUM(COALESCE(r.UniqueClicks, 0))             AS unique_clicks
  FROM imep_gold.tbl_pbi_platform_mailings m
  INNER JOIN imep_gold.tbl_pbi_mailingreciever_region r
          ON r.MailingId = m.Id
  WHERE m.TrackingId IS NOT NULL
    AND RIGHT(m.TrackingId, 3) = 'EMI'
    AND m.CreationDate >= '2024-09-01'           -- Q25: SharePoint-Rollout Sep 2024 = effective floor
  GROUP BY 1, 2, 3
)
SELECT * FROM email_region_pack
WHERE recipients > 0
ORDER BY tracking_pack_id, recipients DESC;


-- =============================================================================
-- PART E — Coverage-KPI (Dashboard-Pflicht-Widget nach Q25-Empfehlung)
-- =============================================================================
-- Zeigt pro Artikel-Monat: tracked / total articles → verstehen, wann der Funnel
-- verlässlich ist. Ausgabe taucht als "Coverage by Month"-Panel im Dashboard auf.

SELECT
  DATE_TRUNC('month', p.UBSArticleDate)                                 AS article_month,
  COUNT(*)                                                              AS total_articles,
  SUM(CASE WHEN p.UBSGICTrackingID IS NOT NULL THEN 1 ELSE 0 END)       AS tracked_articles,
  ROUND(100.0 * SUM(CASE WHEN p.UBSGICTrackingID IS NOT NULL THEN 1 ELSE 0 END)
       / COUNT(*), 2)                                                   AS coverage_pct
FROM sharepoint_bronze.pages p
INNER JOIN sharepoint_bronze.sites s ON s.SPT_Siteld = p.SPT_Siteld
WHERE p.UBSArticleDate IS NOT NULL
  AND p.UBSArticleDate >= '2024-09-01'
  AND s.SiteName = 'News and events'
GROUP BY 1
ORDER BY 1 DESC;


-- =============================================================================
-- PART F — 🎯 Funnel-Intersection: wie viele der 54 EMI-Packs haben Intranet-Pendant?
-- =============================================================================
-- Die KRITISCHE Zahl für Stakeholder-Kommunikation.

WITH imep_packs AS (
  SELECT DISTINCT
    CONCAT_WS('-', SPLIT_PART(TrackingId,'-',1), SPLIT_PART(TrackingId,'-',2)) AS pack_id
  FROM imep_gold.tbl_pbi_platform_mailings
  WHERE TrackingId IS NOT NULL
    AND RIGHT(TrackingId, 3) = 'EMI'
    AND CreationDate >= '2024-09-01'
),
sp_packs AS (
  SELECT DISTINCT
    CONCAT_WS('-', SPLIT_PART(p.UBSGICTrackingID,'-',1), SPLIT_PART(p.UBSGICTrackingID,'-',2)) AS pack_id
  FROM sharepoint_bronze.pages p
  INNER JOIN sharepoint_bronze.sites s ON s.SPT_Siteld = p.SPT_Siteld
  WHERE p.UBSGICTrackingID IS NOT NULL
    AND s.SiteName = 'News and events'
    AND p.UBSArticleDate >= '2024-09-01'
)
SELECT
  (SELECT COUNT(*) FROM imep_packs)                                  AS imep_packs_total,
  (SELECT COUNT(*) FROM sp_packs)                                    AS sp_packs_total,
  (SELECT COUNT(*) FROM imep_packs INNER JOIN sp_packs USING(pack_id)) AS packs_in_both,
  ROUND(100.0 *
    (SELECT COUNT(*) FROM imep_packs INNER JOIN sp_packs USING(pack_id))
    / (SELECT COUNT(*) FROM imep_packs), 2)                          AS email_pack_cross_channel_coverage_pct;


-- =============================================================================
-- ERWARTUNGS-BANDBREITE NACH Q21-Q25
-- =============================================================================
-- Email-Side (PART C):
--   ≤54 Pack-Rows (Q24 EMI-Universe); nach 2024-09-01-Floor erwartet ~40-50
--   Open-Rate global ~22%, Click-Rate ~1.8% (Q16)
--
-- Intranet-Side (PART C):
--   ≤83 Pack-Rows (Q25: News-and-events hat 83 Packs)
--   Alle auf News-and-events konzentriert (99.4% Coverage-Pareto)
--
-- Intersection (PART F) — die Narrativ-Zahl:
--   Worst Case: 5-10 Packs gemeinsam (unterschiedliche Kampagnen-Klammern in Sys.)
--   Best Case:  30-40 Packs (wenn EMI-Packs systematisch auch News-Pages bekommen)
--   Median-Erwartung: 15-25 gemeinsame Packs für vollen Funnel
--
-- Coverage-Pflicht-KPI (PART E):
--   Monatliche Abdeckung 35-70%, Schnitt ~50%, nie 80% — transparent kommunizieren.
--
-- Funnel-Sanity (pro Pack mit Full-Match):
--   Sent → Open: 15-30% (Q16: 22% global)
--   Open → Click: 5-10%
--   Click → PageView: >100% möglich (multi-view pro User)
--   Reach (unique_readers / recipients): 1-5%
-- =============================================================================
