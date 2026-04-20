# Join Recipe — Cross-Channel via TrackingId (Pack-Level / SEG1-2 Match)

> The full cross-channel chain between iMEP (Email) and SharePoint (Intranet). **Dimensional**, not factual — runs exclusively through `tbl_email.TrackingId` ↔ `sharepoint_bronze.pages.UBSGICTrackingID`. Match level is the **Pack** (SEG1-2) — neither full-string nor SEG1-4 works.

**When to use**: Any dashboard view that attributes mailings to page views. Any "email → landing page" funnel analysis.

---

## The rule in one line

```sql
array_join(slice(split(UPPER(email.TrackingId),        '-'), 1, 2), '-')
=
array_join(slice(split(UPPER(pages.UBSGICTrackingID), '-'), 1, 2), '-')
```

**Why SEG1-2 (Pack) and not the full TrackingId?**

TrackingId structure: `CLUSTER-PACK-YYMMDD-ACTIVITY-CHANNEL`

- **SEG1 (Cluster) + SEG2 (Pack number)** = campaign group, shared across all activities in the Pack
- SEG3 (date) + SEG4 (Activity-Seq) = specific to each individual activity
- SEG5 (Channel) = Email/Newsletter/Article/… — each system uses its own vocabulary (iMEP: `EMI`/`NLI`/`TWE`; SharePoint: `IAN`/`ITS`/`OPN`/`ANC`)

**Business reality**: A campaign kicks off, for example, with an email on 2024-07-09, and the corresponding intranet page only goes live on 2024-07-15. Different SEG3 (date), different SEG4 (Activity-Seq), different SEG5 (Channel) — **but the same Pack**. Only SEG1-2 matches the semantically related activities.

Full-string match: 6/1677 hits (Jaccard 0.004). Pack-level match: ~54 attributable Packs.

---

## The canonical cross-channel funnel (Pack-Level)

The full SQL you would run in a dashboard — returns per Pack: email sends/opens/clicks **and** SharePoint views/visits.

```sql
WITH
-- 1. Normalized email Pack key (SEG1-2)
email_packs AS (
  SELECT
      e.Id                                                          AS mailing_id,
      e.TrackingId,
      array_join(slice(split(UPPER(e.TrackingId), '-'), 1, 2), '-') AS tracking_pack_id
  FROM   imep_bronze.tbl_email e
  WHERE  e.TrackingId IS NOT NULL
),

-- 2. Email-side aggregates (per Pack)
email_side AS (
  SELECT
      ep.tracking_pack_id,
      COUNT(DISTINCT rs.Id)                                                            AS sends,
      COUNT(DISTINCT CASE WHEN al.LinkTypeEnum = 'OPEN'  THEN al.TNumber END)          AS unique_opens,
      COUNT(DISTINCT CASE WHEN al.LinkTypeEnum = 'CLICK' THEN al.TNumber END)          AS unique_clicks
  FROM   email_packs ep
  LEFT JOIN imep_bronze.tbl_email_receiver_status rs ON rs.EmailId = ep.mailing_id
  LEFT JOIN imep_bronze.tbl_analytics_link         al ON al.EmailReceiverStatusId = rs.Id
                                                      AND al.IsActive = 1
  GROUP BY ep.tracking_pack_id
),

-- 3. Normalized SP Pack key (SEG1-2)
sp_packs AS (
  SELECT
      p.pageUUID,
      p.UBSGICTrackingID,
      array_join(slice(split(UPPER(p.UBSGICTrackingID), '-'), 1, 2), '-') AS tracking_pack_id
  FROM   sharepoint_bronze.pages p
  WHERE  p.UBSGICTrackingID IS NOT NULL
),

-- 4. SharePoint-side aggregates (per Pack)
sp_side AS (
  SELECT
      sp.tracking_pack_id,
      SUM(m.views)                         AS total_views,
      SUM(m.visits)                        AS total_visits,
      SUM(m.durationsum)                   AS total_duration_sec,
      COUNT(DISTINCT m.viewingcontactid)   AS unique_viewers
  FROM   sp_packs sp
  JOIN   sharepoint_gold.pbi_db_interactions_metrics m ON m.marketingPageId = sp.pageUUID
  WHERE  m.visitdatekey >= '20250101'
  GROUP BY sp.tracking_pack_id
)

-- 5. FULL OUTER JOIN at Pack level — Packs can be email-only OR page-only
SELECT
    COALESCE(e.tracking_pack_id, s.tracking_pack_id) AS tracking_pack_id,
    e.sends, e.unique_opens, e.unique_clicks,
    s.total_views, s.total_visits, s.unique_viewers, s.total_duration_sec,
    CASE
      WHEN e.tracking_pack_id IS NOT NULL AND s.tracking_pack_id IS NOT NULL THEN 'both'
      WHEN e.tracking_pack_id IS NOT NULL                                    THEN 'email_only'
      ELSE                                                                        'sp_only'
    END                                              AS coverage_status

FROM        email_side e
FULL OUTER JOIN sp_side s ON s.tracking_pack_id = e.tracking_pack_id
ORDER BY tracking_pack_id;
```

---

## Variants

### Variant A — Only "both" Packs (true cross-channel attribution)

When you only want Packs where **both** channels carry data (= real funnel possible):

```sql
-- Same CTEs as above, then:
SELECT e.tracking_pack_id, e.sends, e.unique_opens, e.unique_clicks,
       s.total_views, s.unique_viewers
FROM   email_side e
INNER JOIN sp_side s ON s.tracking_pack_id = e.tracking_pack_id
```

→ Expected result size: ~54 Packs.

### Variant B — Activity-within-Pack analysis (SEG1-3, within a single system only)

When you want to understand the time sequence of activities inside a Pack (e.g. "when did the emails go out, when did the pages launch") — but only within one system, not cross-channel:

```sql
-- SEG1-3 = Pack + activity date. Only meaningful within iMEP or within SP,
-- NOT for cross-channel match (pages run on different dates than emails).
array_join(slice(split(UPPER(TrackingId), '-'), 1, 3), '-')
```

### Variant C — Expose untracked SP activity as "noise"

```sql
-- Before the FULL OUTER JOIN, inside sp_side:
SELECT '(untracked)' AS tracking_pack_id, SUM(views), SUM(visits), ...
FROM   sharepoint_gold.pbi_db_interactions_metrics m
LEFT JOIN sharepoint_bronze.pages p ON p.pageUUID = m.marketingPageId
WHERE  p.UBSGICTrackingID IS NULL

UNION ALL

-- regular sp_side query
```

→ Shows "unattributed intranet activity" alongside the attributed funnel.

---

## Pitfalls

### 1. `UPPER()` on both sides is mandatory

Format is confirmed UPPER & clean on both sides. Apply `UPPER()` anyway as defensive code — you don't want to lose 90% of your hits over a single lowercase edge case.

### 2. `TRIM()` as insurance

Whitespace in tracking IDs is rare, but not ruled out in legacy data:

```sql
array_join(slice(split(UPPER(TRIM(TrackingId)), '-'), 1, 2), '-')
```

### 3. Pack-Level (SEG1-2), not Activity-Level

**The single most important pitfall of the whole project.** Email activity and intranet page of the same Pack have different SEG3 (date), SEG4 (Activity-Seq) and SEG5 (Channel). A match at any level finer than SEG1-2 would treat them as unrelated.

### 4. FULL OUTER JOIN, not INNER

Most Packs have **only** email data or **only** page data. Only ~54 Packs are "both". `INNER JOIN` loses 90%+ of Packs — then the dashboard has only a tiny subset as its basis.

### 5. Pack grain is Cluster + Pack number

The Pack key is `CLUSTER-PACKNUMBER` (e.g. `QRREP-0000058`). Dashboard default: **Pack level (SEG1-2)**. For within-a-single-system drilldown, optionally SEG1-3 (per date) — but not for cross-channel match.

### 6. TrackingId is not a time attribute

TrackingId contains SEG3 = date (YYMMDD), but that is the **activity creation time**, not the send/view time. For temporal filters use `rs.DateTime` / `m.visitdatekey`.

### 7. Pre-2025 coverage is thin

Default time filter from `2025-01-01` for iMEP, `20250101` for SP. Before 2025 TrackingId adoption is < 20% — funnel numbers become unreliable.

---

## Expected orders of magnitude

Based on the coverage findings:
- Email Packs with TrackingId (from 2025): ~54
- SP Packs with TrackingId (from 2024-09): ~83 (of which 83 sit on a single site "News and events")
- **Overlap**: ~54 Packs — this is the cross-channel dashboard universe.

---

## References

- [join_strategy_contract.md](join_strategy_contract.md) — Rule 1, 3, 5
- [er_cross_channel.md](../diagrams/er_cross_channel.md) — ER diagram with bridge
- [tbl_email.md](../tables/imep/tbl_email.md) · [pages.md](../tables/sharepoint/pages.md) — The two dimension tables
- Memory: `tracking_id_format_q23.md`, `tracking_id_volume_q24.md`, `sharepoint_pages_coverage_q25.md`

---

## Sources

Genie sessions backing the statements on this page: [Q23](../sources.md#q23), [Q24](../sources.md#q24), [Q25](../sources.md#q25). See [sources.md](../sources.md) for the full index.
