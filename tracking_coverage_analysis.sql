-- =============================================================================
-- Tracking-ID Coverage Analysis — welche Sites/Pages sind pack-attribuiert?
-- =============================================================================
-- Kontext: Q22 hat gezeigt, dass nur 1,949 / 48,419 Pages (~4%) eine
-- UBSGICTrackingID haben. Dieser Query schlüsselt auf, WO die 4% sitzen:
-- welche Sites, welche Page-Typen, welche Zeiträume.
--
-- Ergebnis treibt zwei Entscheidungen:
--   1. Dashboard-Scope: Reicht 4% Coverage für einen glaubwürdigen Funnel?
--   2. Silver-Design: Welche Site/Page-Klassen fallen in `is_article=TRUE`?
-- =============================================================================


-- -----------------------------------------------------------------------------
-- 0) Schema-Probe — welche Spalten gibt es in pages und sites?
-- -----------------------------------------------------------------------------
DESCRIBE TABLE sharepoint_bronze.pages;
DESCRIBE TABLE sharepoint_bronze.sites;


-- -----------------------------------------------------------------------------
-- 1) Overall Coverage — die Eröffnungs-KPIs
-- -----------------------------------------------------------------------------
SELECT
  COUNT(*)                                                   AS total_pages,
  COUNT(UBSGICTrackingID)                                    AS with_trackingid,
  COUNT(UBSArticleDate)                                      AS with_article_date,
  SUM(CASE WHEN UBSGICTrackingID IS NOT NULL
            AND UBSArticleDate    IS NOT NULL THEN 1 ELSE 0 END) AS with_both,
  ROUND(100.0 * COUNT(UBSGICTrackingID) / COUNT(*), 2)       AS pct_with_tid,
  ROUND(100.0 * COUNT(UBSArticleDate)   / COUNT(*), 2)       AS pct_with_date
FROM sharepoint_bronze.pages;


-- -----------------------------------------------------------------------------
-- 2) Coverage pro Site — wo lebt die TrackingID überhaupt?
-- -----------------------------------------------------------------------------
-- Annahme: pages hat eine FK zu sites. Üblich: pages.siteId / site_id / webId.
-- DESCRIBE oben zeigt den echten Namen — im Zweifel per Spalten-Discovery:
--   SELECT column_name FROM information_schema.columns
--   WHERE table_schema='sharepoint_bronze' AND table_name='pages'
--     AND column_name ILIKE '%site%';

SELECT
  p.siteId                                       AS site_id,      -- <-- patchen wenn anders
  s.siteName                                     AS site_name,    -- <-- patchen wenn anders
  s.siteUrl                                      AS site_url,     -- <-- patchen wenn anders
  COUNT(*)                                       AS pages,
  COUNT(p.UBSGICTrackingID)                      AS pages_with_tid,
  COUNT(p.UBSArticleDate)                        AS pages_article_dated,
  ROUND(100.0 * COUNT(p.UBSGICTrackingID) / COUNT(*), 2) AS pct_tid,
  COUNT(DISTINCT CONCAT_WS('-',
    SPLIT_PART(UPPER(TRIM(p.UBSGICTrackingID)), '-', 1),
    SPLIT_PART(UPPER(TRIM(p.UBSGICTrackingID)), '-', 2)
  ))                                             AS distinct_packs
FROM sharepoint_bronze.pages p
LEFT JOIN sharepoint_bronze.sites s
       ON s.siteId = p.siteId                    -- <-- patchen nach DESCRIBE
GROUP BY 1, 2, 3
HAVING pages > 10                                 -- filter noise
ORDER BY pages_with_tid DESC, pct_tid DESC
LIMIT 100;


-- -----------------------------------------------------------------------------
-- 3) Top-Sites — wo konzentrieren sich die 1,949 Article-Pages?
-- -----------------------------------------------------------------------------
SELECT
  p.siteId,
  s.siteName,
  COUNT(*)                                       AS article_pages,
  MIN(p.UBSArticleDate)                          AS earliest_article,
  MAX(p.UBSArticleDate)                          AS latest_article,
  COUNT(DISTINCT CONCAT_WS('-',
    SPLIT_PART(UPPER(TRIM(p.UBSGICTrackingID)), '-', 1),
    SPLIT_PART(UPPER(TRIM(p.UBSGICTrackingID)), '-', 2)
  ))                                             AS distinct_packs,
  COUNT(DISTINCT RIGHT(p.UBSGICTrackingID, 3))   AS channel_suffix_variety,
  COLLECT_SET(RIGHT(p.UBSGICTrackingID, 3))      AS channel_suffixes
FROM sharepoint_bronze.pages p
LEFT JOIN sharepoint_bronze.sites s
       ON s.siteId = p.siteId
WHERE p.UBSGICTrackingID IS NOT NULL
GROUP BY 1, 2
ORDER BY article_pages DESC
LIMIT 50;


-- -----------------------------------------------------------------------------
-- 4) Coverage-Trend über Zeit — ab wann ist der Funnel glaubwürdig?
-- -----------------------------------------------------------------------------
-- Monatlicher Anteil der Articles (= UBSArticleDate NOT NULL), die eine
-- TrackingID haben. "Funnel-ready" definieren wir als ≥80% Coverage.
SELECT
  DATE_TRUNC('month', UBSArticleDate)            AS article_month,
  COUNT(*)                                       AS articles_total,
  COUNT(UBSGICTrackingID)                        AS articles_with_tid,
  ROUND(100.0 * COUNT(UBSGICTrackingID) / COUNT(*), 2) AS pct_tid,
  CASE WHEN 100.0 * COUNT(UBSGICTrackingID) / COUNT(*) >= 80
       THEN '✓ funnel-ready' ELSE '—' END         AS status
FROM sharepoint_bronze.pages
WHERE UBSArticleDate IS NOT NULL
  AND UBSArticleDate >= ADD_MONTHS(CURRENT_DATE(), -36)
GROUP BY 1
ORDER BY 1;


-- -----------------------------------------------------------------------------
-- 5) Page-Typen-Breakdown — welche Klassen kriegen TrackingIDs?
-- -----------------------------------------------------------------------------
-- pages hat 95 Spalten; typische Kandidaten für "Page-Typ" (patchen nach DESCRIBE):
--   pageType, layoutType, contentType, templateName, articleType, category, ...

SELECT
  COALESCE(pageType, '(null)')                   AS page_type,     -- <-- patchen
  COUNT(*)                                       AS total,
  COUNT(UBSGICTrackingID)                        AS with_tid,
  ROUND(100.0 * COUNT(UBSGICTrackingID) / COUNT(*), 2) AS pct_tid
FROM sharepoint_bronze.pages
GROUP BY 1
ORDER BY total DESC
LIMIT 50;


-- -----------------------------------------------------------------------------
-- 6) Traffic-Gewichtung — wie gross ist die Coverage NACH Views?
-- -----------------------------------------------------------------------------
-- Inventory-Coverage (4%) ist die eine Metrik, Traffic-Coverage (% der 84M
-- Interaction-Rows auf tracked Pages) ist die wirklich relevante:
--   wenn die 4% tracked Pages 40% der Views kriegen, ist der Funnel brauchbar.

WITH pages_classified AS (
  SELECT
    p.pageUUID,
    p.siteId,
    CASE WHEN p.UBSGICTrackingID IS NOT NULL THEN 'tracked'
         WHEN p.UBSArticleDate   IS NOT NULL THEN 'article_no_tid'
         ELSE 'non_article' END                  AS page_class
  FROM sharepoint_bronze.pages p
)
SELECT
  pc.page_class,
  COUNT(DISTINCT pc.pageUUID)                    AS distinct_pages,
  COUNT(m.marketingPageId)                       AS interaction_rows,
  SUM(COALESCE(m.views, 0))                      AS total_views,
  SUM(COALESCE(m.visits, 0))                     AS total_visits,
  ROUND(100.0 * SUM(COALESCE(m.views, 0))
       OVER (PARTITION BY 1)
       / NULLIF(SUM(SUM(COALESCE(m.views, 0))) OVER (), 0), 2) AS pct_of_all_views
FROM pages_classified pc
LEFT JOIN sharepoint_gold.pbi_db_interactions_metrics m
       ON m.marketingPageId = pc.pageUUID
GROUP BY 1
ORDER BY total_views DESC;


-- =============================================================================
-- ERWARTETES ERGEBNIS (Hypothesen, durch Run zu validieren):
--
--  • Cell 1: ~4% pct_with_tid (Q22 bestätigt). UBSArticleDate wahrscheinlich
--    höher (~10–20%?) — viele Articles haben Datum, aber keine TID.
--
--  • Cell 2+3: TrackingIDs konzentriert auf wenige "News"/"GIC-Intranet"-Sites.
--    Erwartung: 5–10 Sites decken 80% der tracked Pages ab (Pareto).
--
--  • Cell 4: Coverage vor 2023 wahrscheinlich <50% → realistischer Default-
--    Zeitraum fürs Dashboard ist "letzte 24 Monate" oder "ab Monat X".
--
--  • Cell 6: KRITISCHE Metrik. Wenn die 4% tracked Pages >20% der Views
--    kriegen, ist der Funnel erzählbar. Wenn <5%, müssen wir das Narrativ
--    ändern ("Dashboard zeigt pack-attribuierte Article-Views — die Gesamt-
--    Intranet-Aktivität ist grösser").
-- =============================================================================
