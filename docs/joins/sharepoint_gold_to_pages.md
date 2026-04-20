# Join Recipe — SharePoint Gold Metrics → Pages (TrackingID)

> Der kanonische Weg, um SharePoint-Interaction-Metriken mit Cross-Channel-Attribution anzureichern. **Gold-Metric-Tables tragen keinen TrackingID** — Attribution läuft zwingend über `pages` als Dimension.

**Wann nutzen**: Jede Cross-Channel-Analyse auf SharePoint-Seite. Jedes Dashboard, das SP-Views per Pack aggregiert.

---

## Die Standard-Kette

```sql
SELECT
    -- TrackingID-Attribution aus pages
    p.UBSGICTrackingID  AS tracking_id,
    p.UBSArticleDate    AS article_date,
    p.PageURL,
    p.PageTitle,

    -- Site-Kontext
    s.SiteName,
    s.SiteUrl,

    -- Metriken aus Gold
    m.visitdatekey,
    SUM(m.views)                         AS views,
    SUM(m.visits)                        AS visits,
    SUM(m.durationsum)                   AS duration_sum_sec,
    AVG(m.durationavg)                   AS duration_avg_sec,
    COUNT(DISTINCT m.viewingcontactid)   AS unique_viewers,
    SUM(m.commentss)                     AS comments

FROM    sharepoint_gold.pbi_db_interactions_metrics m
JOIN    sharepoint_bronze.pages                     p ON p.pageUUID = m.marketingPageId
LEFT JOIN sharepoint_bronze.sites                   s ON s.SiteId   = p.SiteId

WHERE   p.UBSGICTrackingID IS NOT NULL          -- Pflicht-Filter für Attribution
  AND   m.visitdatekey >= '20250101'            -- Default-Zeitraum

GROUP BY p.UBSGICTrackingID, p.UBSArticleDate, p.PageURL, p.PageTitle,
         s.SiteName, s.SiteUrl, m.visitdatekey
```

---

## Varianten

### Variante A — Pack-Level-Aggregat (Dashboard-Grain)

Für "Wieviele Views hatte Pack X insgesamt?":

```sql
SELECT
    array_join(slice(split(UPPER(p.UBSGICTrackingID), '-'), 1, 2), '-') AS tracking_pack_id,
    SUM(m.views)                       AS total_views,
    SUM(m.visits)                      AS total_visits,
    COUNT(DISTINCT m.viewingcontactid) AS unique_viewers,
    SUM(m.durationsum)                 AS total_duration_sec
FROM   sharepoint_gold.pbi_db_interactions_metrics m
JOIN   sharepoint_bronze.pages                     p ON p.pageUUID = m.marketingPageId
WHERE  p.UBSGICTrackingID IS NOT NULL
  AND  m.visitdatekey >= '20250101'
GROUP BY 1
```

### Variante B — Date × Division (pre-aggregated)

Nutze `pbi_db_datewise_overview_fact_tbl` — viel kleiner (7.5M statt 84M), bereits nach Division gegroupt und mit Rolling Windows (7/14/21/28d):

```sql
SELECT
    p.UBSGICTrackingID,
    d.employeebusinessdivision,
    d.visitdatekey,
    d.page_div_date_views                 AS views_1d,
    d.page_div_date_views_7d              AS views_7d,
    d.page_div_date_views_14d             AS views_14d,
    d.page_div_date_uniquevisitors        AS unique_viewers_1d,
    d.page_div_date_uniquevisitors_7d     AS unique_viewers_7d
FROM   sharepoint_gold.pbi_db_datewise_overview_fact_tbl d
JOIN   sharepoint_bronze.pages                          p ON p.pageUUID = d.marketingPageId
WHERE  p.UBSGICTrackingID IS NOT NULL
  AND  d.visitdatekey >= '20250101'
```

### Variante C — Nur View-Counts (super-fast)

`pbi_db_pageviewed_metric` hat nur 5 Spalten, ist die schnellste Metric-Table für pure View-Counts:

```sql
SELECT
    p.UBSGICTrackingID,
    v.visitdatekey,
    SUM(v.views)                       AS views,
    COUNT(DISTINCT v.viewingcontactid) AS unique_viewers
FROM   sharepoint_gold.pbi_db_pageviewed_metric v
JOIN   sharepoint_bronze.pages                  p ON p.pageUUID = v.marketingPageId
WHERE  p.UBSGICTrackingID IS NOT NULL
GROUP BY 1, 2
```

### Variante D — CTA-Clicks (alternatives Schema)

Für Call-to-Action-Attribution statt Page-Views:

```sql
SELECT
    p.UBSGICTrackingID,
    c.ctalabel,
    SUM(c.clicks) AS total_clicks
FROM   sharepoint_clicks_gold.pbi_db_ctalabel_intr_fact_gold c
JOIN   sharepoint_bronze.pages                              p ON p.pageUUID = c.marketingPageId
WHERE  p.UBSGICTrackingID IS NOT NULL
GROUP BY 1, 2
```

---

## Gotchas

### 1. `LEFT JOIN` für unattribuierte Pages

Wenn du auch "untracked" Pages zählen willst (die 96%), LEFT JOIN und `NVL`:

```sql
SELECT
    COALESCE(p.UBSGICTrackingID, '(untracked)') AS tid_or_untracked,
    SUM(m.views)
FROM   sharepoint_gold.pbi_db_interactions_metrics m
LEFT JOIN sharepoint_bronze.pages                  p ON p.pageUUID = m.marketingPageId
GROUP BY 1
```

→ So siehst du die Relation tracked/untracked im Dashboard.

### 2. `visitdatekey` ist ein String, kein Date

Format `YYYYMMDD`. Für Range-Filter String-Compare ist OK, aber für Parsing:

```sql
CAST(visitdatekey AS DATE)        -- funktioniert im Spark SQL
-- oder expliziter:
to_date(visitdatekey, 'yyyyMMdd')
```

### 3. Spalten-Typo: `commentss`

Der doppelte `s` in `commentss` ist kein Tippfehler von dir — so heisst die Spalte. Nicht korrigieren.

### 4. `viewingcontactid` vs. `user_gpn`

Gold-Facts tragen `viewingcontactid` (GUID), nicht `user_gpn`. Für Person-Level-Cross-Channel:

```sql
-- Option A: Bridge via pbi_db_employeecontact (noch nicht validiert)
LEFT JOIN sharepoint_gold.pbi_db_employeecontact ec ON ec.contactId = m.viewingcontactid

-- Option B: Zurück zu Bronze-pageviews wo user_gpn direkt steht
-- (teurer, aber wenn Option A nicht funktioniert)
```

### 5. Grain-Konsistenz zwischen Metric-Tables

- `pbi_db_interactions_metrics` = page × date × contact × app
- `pbi_db_pageviewed_metric`    = page × date × contact × app
- `pbi_db_pagevisited_metric`   = page × date × visit

Nicht blind aggregieren ohne Grain zu verstehen. Für gleich-gegroupte Zahlen zwischen den Tables → erst `GROUP BY page × date` dann joinen.

### 6. Kein `GICTrackingID` auf den Gold-Metriken

Falls du in alten Dashboards / SQL-Files `GICTrackingID`-Filter auf Gold findest: ist **nicht** in Gold-Metriken. Nur auf `sharepoint_bronze.pageviews` und `sharepoint_silver.webpage` (mit Case-Variant `gICTrackingID`).

---

## Performance-Tuning

- **Pre-Filter auf `pages`**: Wenn du nur getrackte Pages brauchst, erst die 1,949 Pages mit `UBSGICTrackingID` materialisieren, dann das grosse Join:

  ```sql
  WITH tracked_pages AS (
    SELECT pageUUID, UBSGICTrackingID FROM sharepoint_bronze.pages
    WHERE  UBSGICTrackingID IS NOT NULL
  )
  SELECT tp.UBSGICTrackingID, SUM(m.views)
  FROM   sharepoint_gold.pbi_db_interactions_metrics m
  JOIN   tracked_pages                              tp ON tp.pageUUID = m.marketingPageId
  GROUP BY 1;
  ```

  Das reduziert den Join-Partner auf ~4% der Pages-Größe.

- **`datewise_overview_fact_tbl` statt `interactions_metrics`** wo möglich — 10× kleiner.

- **Zeitfilter setzen** — 84M Rows × breite Spalten ohne Filter = Timeout-Risk.

---

## Referenzen

- [pbi_db_interactions_metrics.md](../tables/sharepoint_gold/pbi_db_interactions_metrics.md)
- [pages.md](../tables/sharepoint/pages.md)
- ER-Diagramm: [er_sharepoint_gold.md](../diagrams/er_sharepoint_gold.md)
- Regeln: [join_strategy_contract.md](join_strategy_contract.md) — Regel 1, 5
