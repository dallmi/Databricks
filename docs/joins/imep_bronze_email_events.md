# Join Recipe — iMEP Bronze Email Events

> The canonical Bronze chain for all per-recipient event analyses. **4 tables**, grain = 1 row per event per recipient per mailing. This is the truth before Gold denormalizes it.

**When to use**: Ad-hoc analyses, debugging, cases where `imep_gold.final` lacks the right resolution (e.g. you need a Bronze column that doesn't land in Gold).

**Do not use** for: dashboard backends — there `imep_gold.final` is faster and already HR-enriched.

---

## The canonical 4-table chain

```sql
SELECT
    -- Mailing context
    e.TrackingId,
    e.Title,
    e.Subject,

    -- Recipient context (from receiver_status)
    rs.TNumber,
    rs.Receiver,
    rs.EmailLanguage,
    rs.DateTime         AS send_time,
    rs.LogStatus        AS send_status,

    -- Event context (from analytics_link)
    al.LinkTypeEnum     AS event_type,
    al.CreationDate     AS event_time,
    al.Agent            AS device_agent,
    al.CurrentLanguage,

    -- HR enrichment (separate join)
    hr.WORKER_ID        AS gpn,
    cc.REGION,
    cc.DIVISION,
    cc.AREA,
    cc.COUNTRY

FROM    imep_bronze.tbl_email                   e
JOIN    imep_bronze.tbl_email_receiver_status   rs ON rs.EmailId              = e.Id
LEFT JOIN imep_bronze.tbl_analytics_link        al ON al.EmailReceiverStatusId = rs.Id
                                                  AND al.EmailId               = e.Id
                                                  AND al.IsActive              = 1
LEFT JOIN imep_bronze.tbl_hr_employee           hr ON hr.T_NUMBER             = rs.TNumber
LEFT JOIN imep_bronze.tbl_hr_costcenter         cc ON cc.ORGANIZATIONAL_UNIT  = hr.ORGANIZATIONAL_UNIT

WHERE   e.TrackingId IS NOT NULL                   -- cross-channel filter
  AND   rs.DateTime >= '2025-01-01'                -- default window (adoption ramp)
  AND   rs.LogStatus IN ('Sent', 'Open')           -- tune per analysis
```

---

## Variants

### Variant A — Sends only (no Open/Click)

When you only want to know **who received which mailing** (bounce analysis etc.):

```sql
SELECT e.TrackingId, rs.TNumber, rs.LogStatus, rs.DateTime
FROM   imep_bronze.tbl_email                  e
JOIN   imep_bronze.tbl_email_receiver_status  rs ON rs.EmailId = e.Id
WHERE  e.TrackingId IS NOT NULL
```

→ 293M rows total. One row per mailing × recipient. `tbl_analytics_link` is not needed when only send events matter.

### Variant B — Opens/Clicks only (fact-only)

When send context doesn't matter:

```sql
SELECT al.EmailId, al.TNumber, al.LinkTypeEnum, al.CreationDate, al.Agent
FROM   imep_bronze.tbl_analytics_link al
WHERE  al.IsActive = 1
  AND  al.CreationDate >= '2025-01-01'
```

→ 533M rows. Most expensive — set a time filter.

### Variant C — Pack-level aggregates

For dashboard numbers (not per event):

```sql
WITH mail AS (
  SELECT Id, TrackingId,
         array_join(slice(split(UPPER(TrackingId), '-'), 1, 2), '-') AS tracking_pack_id
  FROM   imep_bronze.tbl_email
  WHERE  TrackingId IS NOT NULL
)
SELECT
    mail.tracking_pack_id,
    COUNT(DISTINCT rs.Id)                                                            AS sends,
    COUNT(DISTINCT CASE WHEN al.LinkTypeEnum = 'OPEN'  THEN al.TNumber END)          AS unique_opens,
    COUNT(DISTINCT CASE WHEN al.LinkTypeEnum = 'CLICK' THEN al.TNumber END)          AS unique_clicks
FROM    mail
JOIN    imep_bronze.tbl_email_receiver_status rs ON rs.EmailId = mail.Id
LEFT JOIN imep_bronze.tbl_analytics_link       al ON al.EmailReceiverStatusId = rs.Id
                                                 AND al.IsActive = 1
GROUP BY mail.tracking_pack_id
```

---

## Pitfalls

### 1. ALWAYS filter `al.IsActive = 1`

Without this filter you pull soft-deleted events into the numbers. Dashboards come out too high.

### 2. Dual FK between `analytics_link` and `receiver_status`

`tbl_analytics_link` carries **both** `EmailId` and `EmailReceiverStatusId` as FKs. Use both in the join condition — otherwise you run Cartesian risk if a recipient had multiple send events (rare, but possible on retries).

### 3. `tbl_analytics_link` is for OPEN + CLICK only, not Sends

Sends live in `tbl_email_receiver_status`, not in `tbl_analytics_link`. A common confusion.

### 4. `rs.DateTime` vs `al.CreationDate`

- `rs.DateTime` = when it was sent
- `al.CreationDate` = when it was opened / clicked

For time-to-open analyses: `DATEDIFF(second, rs.DateTime, al.CreationDate)`.

### 5. HR join is `N:1`, but can change

`tbl_hr_employee` is upserted via MERGE (snapshot replace). Region/Division are **current**, not historical. For "who was in which Division on day X", you need `imep_bronze.tbl_hr_employee_history` (if it exists) or a snapshot merge.

### 6. `TrackingId` only on `tbl_email`

Do not look for it on `receiver_status` or `analytics_link` — they don't carry it. Always fetch via `e.Id = EmailId`.

---

## Performance tuning

- **Time filter up front**: `rs.DateTime >= '...'` as the first WHERE clause → partition pruning where available
- **TrackingId pre-filter**: `WHERE e.TrackingId IS NOT NULL` cuts the starting set to ~1.3% of mailings
- **`LEFT JOIN` on `tbl_analytics_link`**, not `INNER JOIN`: otherwise you drop mailings that were never opened
- **Cache on Pack-level aggregates**: if you pull the same funnel 10× a day, materialize into a local view

---

## References

- Full field list per table: [tbl_email.md](../tables/imep/tbl_email.md), [tbl_email_receiver_status.md](../tables/imep/tbl_email_receiver_status.md), [tbl_analytics_link.md](../tables/imep/tbl_analytics_link.md)
- ER diagram: [er_imep_bronze.md](../diagrams/er_imep_bronze.md)
- Rules: [join_strategy_contract.md](join_strategy_contract.md)

---

## Sources

Genie sessions backing the statements on this page: [Q2](../sources.md#q2), [Q24](../sources.md#q24), [Q27](../sources.md#q27). See [sources.md](../sources.md) for the full index.
