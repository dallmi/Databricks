-- =============================================================================
-- Cross-Channel MVP — tracking_pack_id grain
-- Column names that carry the organisation's name are written as __ORG__
-- (e.g. __ORG__GICTrackingID), so this public repo stays free of it.
-- Replace __ORG__ with the real prefix (ORG_PREFIX in .env) before running.
-- =============================================================================
-- Status: Q16 + Q17 + Q21 + Q22 + Q23 + Q24 + Q25 — ALL READINESS QUESTIONS ANSWERED.
-- =============================================================================
--
-- Q25 CONFIRMED (2026-04-20):
--   • Overall Page-Coverage: nur ein kleiner Bruchteil der Pages (niedriger einstelliger %-Bereich).
--     Praktisch alle tracked pages haben auch __ORG__ArticleDate.
--   • 🎯 Fast alle tracked pages sitzen auf EINER Site: "News and events" (einige Dutzend Packs).
--     Die restlichen wenigen Pages sind Test-/Edge-Artefakte (Test-Sites u. ä.).
--   • Site-FK bestätigt: pages.SPT_Siteld = sites.SPT_Siteld (vollständiger Match, alle Pages).
--   • Rollout-Zeitlinie: vor Jul 2024 praktisch keine Abdeckung → Sep 2024 Inflection
--       → Okt-Dez 2024: rund die Hälfte → 2025: volatil → Peak Mar 2026 (gut zwei Drittel), NIE 80%.
--   • 1:1 PageURL ↔ TrackingID Mapping: keine URL mit multiple TIDs → stabile Identity.
--   ⇒ Dashboard-Konsequenzen:
--     a) Site-Filter: WHERE pages.SiteName = 'News and events' (fast vollständige Coverage, einige Dutzend Packs)
--     b) Effective Date Floor: 2024-09-01 (Rollout-Inflection, dominant über iMEP-2025-Floor)
--     c) Coverage-KPI im Dashboard Pflicht ("tracked/total articles this month")
--
-- Q24 CONFIRMED (2026-04-20):
--   • TrackingId-Befüllungsgrad in platform_mailings: NUR ein kleiner Bruchteil (um 1%).
--     Rest (fast alles) ist NULL → TrackingId-basierte Analyse deckt standardmässig nur rund 1% ab.
--   • Jahres-Adoption: 2024 noch wenige, 2025 deutlich mehr, 2026 YTD steigende Run-Rate.
--     ⇒ Default-Zeitfenster im Dashboard: ab 2025-01-01.
--   • Suffix-Verteilung: EMI dominiert (rund vier Fünftel), TWE und NLI je knapp ein Zehntel, Rest marginal.
--     ⇒ EMI-Filter ist unstrittig die richtige Wahl.
--   • 🎯 DASHBOARD-UNIVERSUM: einige hundert EMI-Rows → einige hundert distinct TIDs → nur einige Dutzend distinct Pack-IDs.
--     ⇒ Pack-Level-Aggregat liefert nur einige Dutzend Rows. Rund ein Dutzend Varianten pro Pack kollabiert durch SEG1-2-Cut.
--
-- Q23 CONFIRMED (2026-04-20):
--   • Format identisch zwischen TrackingId und __ORG__GICTrackingID: 32 chars, 5 segments (5-7-6-7-3),
--     alles UPPERCASE, keine Whitespace-Issues → UPPER/TRIM defensiv ok, funktional unnötig.
--   • ⚠️ Werte fast DISJOINT: Jaccard nahe null, nur eine Handvoll Exact-Matches unter über tausend distinct IDs.
--     → TrackingId ist KEIN direkter FK. Es ist ein shared FORMAT, kein shared ID-SPACE.
--   • Channel-Suffix (SEG5) kodiert System-Ownership:
--       iMEP:     EMI (dominant), NLI, TWE (je knapp ein Zehntel)
--       SharePoint: IAN und ITS (zusammen gut zwei Drittel), OPN, ANC (klein)
--   • Korrekte Linking-Strategie: match SEG1-4, ignore SEG5.
--     Für unseren Pack-Grain (SEG1-SEG2) passt das automatisch.
--
-- Q21 CONFIRMED (2026-04-20):
--   tbl_pbi_mailingreciever_region   → MailingId, GCRS_REGION_NAME, GCRS_CNTRY_NAME, Count, UniqueOpens, UniqueClicks
--   tbl_pbi_mailingreciever_division → MailingId, GCRS_BUSN_DIV_NAME, Count, UniqueOpens, UniqueClicks
--   tbl_pbi_engagement               → mailingid (LOWERCASE!), senderlanguage, linklabel, sourcecomponentname, url, Count, componentorder, Linkorder, ComponentName
--   tbl_pbi_kpi                      → MailingId, MailingTitle, MailingCreatedBy, MailingReceiverStatus, OverallMailingStatus, StartsendDateTime, measure, RecipientLanguage (PIVOTED)
-- Match rates to platform_mailings.Id: region, division and kpi near-complete (>95%); engagement clearly lower (roughly three quarters)
-- NULL semantics: UniqueOpens NULL in rund einem Drittel, UniqueClicks NULL in der Mehrheit der Rows → COALESCE(..., 0) vor SUM Pflicht.
--
-- Q22 CONFIRMED (2026-04-20):
--   SharePoint Gold Metriken (interactions_metrics, pageviewed_metric, pagevisited_metric, datewise_overview_fact_tbl)
--   teilen ALLE den FK `marketingPageId` (string GUID)
--   → sharepoint_bronze.pages.pageUUID
--       → pages.__ORG__GICTrackingID
--           → imep_gold.tbl_pbi_platform_mailings.TrackingId
--   KEIN direktes TrackingID auf Gold-Metriken.
--   ⚠️ COVERAGE-BLOCKER: nur ein kleiner Bruchteil der Pages (niedriger einstelliger %-Bereich) hat __ORG__GICTrackingID.
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
  SELECT __ORG__GICTrackingID AS tid FROM sharepoint_bronze.pages
  WHERE __ORG__GICTrackingID IS NOT NULL
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
           SPLIT_PART(UPPER(TRIM(__ORG__GICTrackingID)),'-',1),
           SPLIT_PART(UPPER(TRIM(__ORG__GICTrackingID)),'-',2)) AS pack_id
  FROM sharepoint_bronze.pages
  WHERE __ORG__GICTrackingID IS NOT NULL
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
--  A1 ✅ MailingId = platform_mailings.Id (Q21, near-complete match on region)
--  A2 ✅ Kein direktes TrackingID auf Gold-Metric — JOIN via
--        metrics.marketingPageId = pages.pageUUID, dann pages.__ORG__GICTrackingID (Q22)
--  A3 ✅ NULLs in UniqueOpens/UniqueClicks sind semantisch → COALESCE(..., 0) vor SUM (Q21)
--  A4 ✅ tracking_pack_id = SEG1-SEG2 — Format sauber, UPPER/TRIM defensiv (Q23)
--  A5 ✅ iMEP-Filter `RIGHT(TrackingId, 3) = 'EMI'` deckt den Grossteil der iMEP-TIDs ab (Q23)
--  A6 ✅ SharePoint-Filter `RIGHT(__ORG__GICTrackingID, 3) IN ('IAN','ITS','OPN','ANC')` (Q23)
--  A7 ⚠️ Nur ein kleiner Bruchteil der Pages (niedriger einstelliger %-Bereich) hat __ORG__GICTrackingID → sparse Coverage (Q22)
--  A8 ⚠️ Exact-TID-Intersection nur eine Handvoll (Jaccard nahe null)! Pack-Level-JOIN ist die EINZIGE
--        sinnvolle Brücke — direkter TrackingId-Match wäre leer (Q23)
-- =============================================================================

WITH
-- ---------- Email side: iMEP Gold Tier-3 region aggregate (near-complete match, most complete) ----------
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
    AND RIGHT(m.TrackingId, 3) = 'EMI'           -- Q23/Q24: Grossteil der iMEP-TIDs = Dashboard-Scope
    AND m.CreationDate >= '2024-09-01'           -- Q25: SharePoint-Rollout Sep 2024 = effective floor           -- Q24: Adoption ramp ab 2025 → realistischer Default
  GROUP BY 1
),

-- ---------- SharePoint side: page-view aggregates per tracking_pack_id ----------
-- Q22: JOIN-Pfad ist metrics.marketingPageId → pages.pageUUID → pages.__ORG__GICTrackingID
-- Only a small minority of pages (low single-digit %) have __ORG__GICTrackingID → INNER JOIN auf pages mit WHERE-Filter.
-- Wir nehmen `pbi_db_interactions_metrics` (master fact) statt _pageviewed_metric:
--   gleiche Grain, mehr Messgrössen (views, visits, durationsum), selbe marketingPageId.
pages_pack AS (
  SELECT
    p.pageUUID,
    RIGHT(p.__ORG__GICTrackingID, 3) AS channel_suffix,
    CONCAT_WS('-',
      SPLIT_PART(p.__ORG__GICTrackingID, '-', 1),
      SPLIT_PART(p.__ORG__GICTrackingID, '-', 2)
    ) AS tracking_pack_id
  FROM sharepoint_bronze.pages p
  INNER JOIN sharepoint_bronze.sites s
          ON s.SPT_Siteld = p.SPT_Siteld                         -- Q25: 100% match
  WHERE p.__ORG__GICTrackingID IS NOT NULL                           -- small minority of pages
    AND RIGHT(p.__ORG__GICTrackingID, 3) IN ('IAN','ITS','OPN','ANC') -- Q23: fast alle tracked pages
    AND s.SiteName = 'News and events'                           -- Q25: Pareto, fast alles auf einer Site
    AND p.__ORG__ArticleDate >= '2024-09-01'                         -- Q25: Rollout-Inflection
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
  -- Intranet KPIs (low-coverage caveat per Q22)
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
-- Erwartung: einige Dutzend Rows Email-Side (Q24), davon eine Teilmenge mit Intranet-Match.


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
  DATE_TRUNC('month', p.__ORG__ArticleDate)                                 AS article_month,
  COUNT(*)                                                              AS total_articles,
  SUM(CASE WHEN p.__ORG__GICTrackingID IS NOT NULL THEN 1 ELSE 0 END)       AS tracked_articles,
  ROUND(100.0 * SUM(CASE WHEN p.__ORG__GICTrackingID IS NOT NULL THEN 1 ELSE 0 END)
       / COUNT(*), 2)                                                   AS coverage_pct
FROM sharepoint_bronze.pages p
INNER JOIN sharepoint_bronze.sites s ON s.SPT_Siteld = p.SPT_Siteld
WHERE p.__ORG__ArticleDate IS NOT NULL
  AND p.__ORG__ArticleDate >= '2024-09-01'
  AND s.SiteName = 'News and events'
GROUP BY 1
ORDER BY 1 DESC;


-- =============================================================================
-- PART F — 🎯 Funnel-Intersection: wie viele der EMI-Packs haben Intranet-Pendant?
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
    CONCAT_WS('-', SPLIT_PART(p.__ORG__GICTrackingID,'-',1), SPLIT_PART(p.__ORG__GICTrackingID,'-',2)) AS pack_id
  FROM sharepoint_bronze.pages p
  INNER JOIN sharepoint_bronze.sites s ON s.SPT_Siteld = p.SPT_Siteld
  WHERE p.__ORG__GICTrackingID IS NOT NULL
    AND s.SiteName = 'News and events'
    AND p.__ORG__ArticleDate >= '2024-09-01'
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
--   Einige Dutzend Pack-Rows (Q24 EMI-Universe); nach 2024-09-01-Floor etwas weniger
--   Open-Rate global rund ein Fünftel, Click-Rate im niedrigen einstelligen %-Bereich (Q16)
--
-- Intranet-Side (PART C):
--   Einige Dutzend Pack-Rows (Q25: Packs auf News-and-events)
--   Alle auf News-and-events konzentriert (Coverage-Pareto)
--
-- Intersection (PART F) — die Narrativ-Zahl:
--   Worst Case: nur eine Handvoll Packs gemeinsam (unterschiedliche Kampagnen-Klammern in Sys.)
--   Best Case:  die Mehrheit der EMI-Packs (wenn EMI-Packs systematisch auch News-Pages bekommen)
--   Median-Erwartung: rund ein Drittel der EMI-Packs mit vollem Funnel
--
-- Coverage-Pflicht-KPI (PART E):
--   Monatliche Abdeckung stark schwankend, im Schnitt rund die Hälfte, nie 80% — transparent kommunizieren.
--
-- Funnel-Sanity (pro Pack mit Full-Match):
--   Sent → Open: 15-30% (Q16: globaler Wert liegt in diesem Band)
--   Open → Click: 5-10%
--   Click → PageView: >100% möglich (multi-view pro User)
--   Reach (unique_readers / recipients): 1-5%
-- =============================================================================
