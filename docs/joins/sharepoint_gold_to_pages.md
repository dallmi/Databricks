# Join Recipe — SharePoint Gold Metrics → Pages (TrackingID)

> The canonical way to enrich SharePoint interaction metrics with cross-channel attribution. **Gold metric tables carry no TrackingID** — attribution runs exclusively through `pages` as the dimension.

**When to use**: Any cross-channel analysis on the SharePoint side. Any dashboard that aggregates SP views per Pack.

---

## The standard chain

```sql
SELECT
    -- TrackingID attribution from pages
    p.UBSGICTrackingID  AS tracking_id,
    p.UBSArticleDate    AS article_date,
    p.PageURL,
    p.PageTitle,

    -- Site context
    s.SiteName,
    s.SiteUrl,

    -- Metrics from Gold
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

WHERE   p.UBSGICTrackingID IS NOT NULL          -- required filter for attribution
  AND   m.visitdatekey >= '20250101'            -- default time window

GROUP BY p.UBSGICTrackingID, p.UBSArticleDate, p.PageURL, p.PageTitle,
         s.SiteName, s.SiteUrl, m.visitdatekey
```

---

## Variants

### Variant A — Pack-level aggregate (dashboard grain)

For "How many views did Pack X get overall?":

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

### Variant B — Date × Division (pre-aggregated)

Use `pbi_db_datewise_overview_fact_tbl` — much smaller (7.5M instead of 84M), already grouped by Division and equipped with rolling windows (7/14/21/28d):

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

### Variant C — View counts only (super fast)

`pbi_db_pageviewed_metric` has only 5 columns and is the fastest metric table for pure view counts:

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

### Variant D — CTA clicks (alternative schema)

For call-to-action attribution instead of page views:

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

## Pitfalls

### 1. `LEFT JOIN` for unattributed pages

If you also want to count "untracked" pages (the 96%), use LEFT JOIN and `NVL`:

```sql
SELECT
    COALESCE(p.UBSGICTrackingID, '(untracked)') AS tid_or_untracked,
    SUM(m.views)
FROM   sharepoint_gold.pbi_db_interactions_metrics m
LEFT JOIN sharepoint_bronze.pages                  p ON p.pageUUID = m.marketingPageId
GROUP BY 1
```

→ This way you see the tracked/untracked ratio in the dashboard.

### 2. `visitdatekey` is a string, not a date

Format `YYYYMMDD`. For range filters, string compare is fine, but for parsing:

```sql
CAST(visitdatekey AS DATE)        -- works in Spark SQL
-- or more explicitly:
to_date(visitdatekey, 'yyyyMMdd')
```

### 3. Column typo: `commentss`

The double `s` in `commentss` is not your typo — the column is actually named that way. Do not correct it.

### 4. `viewingcontactid` vs. `user_gpn`

Gold facts carry `viewingcontactid` (GUID), not `user_gpn`. For person-level cross-channel:

```sql
-- Option A: bridge via pbi_db_employeecontact (not yet validated)
LEFT JOIN sharepoint_gold.pbi_db_employeecontact ec ON ec.contactId = m.viewingcontactid

-- Option B: fall back to bronze pageviews, which has user_gpn directly
-- (more expensive, but use when Option A doesn't work)
```

### 5. Grain consistency between metric tables

- `pbi_db_interactions_metrics` = page × date × contact × app
- `pbi_db_pageviewed_metric`    = page × date × contact × app
- `pbi_db_pagevisited_metric`   = page × date × visit

Do not aggregate blindly without understanding the grain. For comparably grouped numbers across the tables → first `GROUP BY page × date`, then join.

### 6. No `GICTrackingID` on the Gold metrics

If you find `GICTrackingID` filters on Gold in old dashboards / SQL files: it is **not** on Gold metrics. Only on `sharepoint_bronze.pageviews` and `sharepoint_silver.webpage` (with case variant `gICTrackingID`).

---

## Performance tuning

- **Pre-filter on `pages`**: If you only need tracked pages, materialize the 1,949 pages with `UBSGICTrackingID` first, then run the large join:

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

  This reduces the join partner to ~4% of the pages size.

- **`datewise_overview_fact_tbl` instead of `interactions_metrics`** wherever possible — 10× smaller.

- **Set time filters** — 84M rows × wide columns without filter = timeout risk.

---

## References

- [pbi_db_interactions_metrics.md](../tables/sharepoint_gold/pbi_db_interactions_metrics.md)
- [pages.md](../tables/sharepoint/pages.md)
- ER diagram: [er_sharepoint_gold.md](../diagrams/er_sharepoint_gold.md)
- Rules: [join_strategy_contract.md](join_strategy_contract.md) — Rule 1, 5
