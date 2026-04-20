# Join Recipe — HR Enrichment (TNumber ↔ GPN ↔ Region/Division)

> The HR bridge. For **three scenarios**: (1) enrich TNumber from iMEP engagement, (2) translate GPN from SharePoint to TNumber, (3) case normalization between `T_NUMBER` (lowercase) and `UbsId` (UPPERCASE). All three are based on `imep_bronze.tbl_hr_employee` — no external sources required.

---

## The three identifier formats

| Name | Location | Format | Example |
|---|---|---|---|
| `TNumber` | iMEP engagement | lowercase `t######` | `t100200` |
| `T_NUMBER` | HR | lowercase `t######` | `t100200` |
| `WORKER_ID` | HR | 8-digit numeric (= GPN) | `00100200` |
| `user_gpn` | SharePoint `pageviews` | 8-digit numeric | `00100200` |
| `UbsId` | `tbl_hr_user` | UPPERCASE `T######` | `T100200` |
| `viewingcontactid` | `sharepoint_gold.*` | GUID | `1254e21a-...` |

**Core fact**: `tbl_hr_employee` is the only table that carries **both** `T_NUMBER` and `WORKER_ID` simultaneously → a simple JOIN instead of an external bridge source.

---

## Scenario 1: iMEP engagement → HR Region/Division

```sql
SELECT
    al.TNumber,
    al.LinkTypeEnum,
    al.CreationDate,
    hr.WORKER_ID           AS gpn,
    cc.REGION,
    cc.DIVISION,
    cc.AREA,
    cc.COUNTRY
FROM   imep_bronze.tbl_analytics_link  al
LEFT JOIN imep_bronze.tbl_hr_employee   hr ON hr.T_NUMBER            = al.TNumber
LEFT JOIN imep_bronze.tbl_hr_costcenter cc ON cc.ORGANIZATIONAL_UNIT = hr.ORGANIZATIONAL_UNIT
WHERE  al.IsActive = 1
```

**Important**: `LEFT JOIN` — otherwise you drop events from former employees or external recipients whose TNumber is no longer in HR.

---

## Scenario 2: SharePoint GPN → iMEP TNumber

```sql
SELECT
    pv.user_gpn,
    hr.T_NUMBER            AS t_number,
    pv.ViewTime,
    pv.pageId
FROM   sharepoint_bronze.pageviews  pv
LEFT JOIN imep_bronze.tbl_hr_employee hr ON hr.WORKER_ID = pv.user_gpn
```

**The bridge**: `WORKER_ID = user_gpn`. Both are 8-digit numeric.

**Anti-pattern — do NOT do this**:
```sql
-- WRONG: tries to map GPN string via prefix manipulation
ON  CONCAT('t', LPAD(pv.user_gpn, 6, '0')) = hr.T_NUMBER   -- does NOT work
```

Confirmed: the string-based transformation `00100200 → t100200` works **by example**, but not systematically. A significant share of GPNs do **not** match their T-Number under this rule. Always use `WORKER_ID`.

---

## Scenario 3: match `UbsId` (UPPERCASE) with `T_NUMBER` (lowercase)

```sql
SELECT
    hr.T_NUMBER,
    u.UbsId,
    u.Town
FROM   imep_bronze.tbl_hr_employee hr
JOIN   imep_bronze.tbl_hr_user      u ON LOWER(u.UbsId) = LOWER(hr.T_NUMBER)
```

**Case normalization is mandatory** — without `LOWER()` you get 0 hits, because `T_NUMBER` = `t100200` and `UbsId` = `T100200`.

`tbl_hr_user` additionally offers: `Town`, further office metadata. Only join when those fields are needed — otherwise `tbl_hr_employee` + `tbl_hr_costcenter` is enough.

---

## Scenario 4: Full Cross-Channel Employee Enrichment

When a single query needs all three worlds — iMEP, SharePoint, HR:

```sql
WITH sp_views AS (
  SELECT user_gpn, COUNT(*) AS view_count
  FROM   sharepoint_bronze.pageviews
  WHERE  ViewTime >= '2025-01-01'
  GROUP BY user_gpn
),
imep_opens AS (
  SELECT TNumber, COUNT(*) AS open_count
  FROM   imep_bronze.tbl_analytics_link
  WHERE  LinkTypeEnum = 'OPEN'
    AND  IsActive = 1
    AND  CreationDate >= '2025-01-01'
  GROUP BY TNumber
)
SELECT
    hr.T_NUMBER,
    hr.WORKER_ID                       AS gpn,
    cc.REGION, cc.DIVISION,
    COALESCE(sp.view_count, 0)         AS sp_views,
    COALESCE(io.open_count, 0)         AS imep_opens
FROM   imep_bronze.tbl_hr_employee   hr
LEFT JOIN imep_bronze.tbl_hr_costcenter cc ON cc.ORGANIZATIONAL_UNIT = hr.ORGANIZATIONAL_UNIT
LEFT JOIN sp_views                     sp ON sp.user_gpn = hr.WORKER_ID
LEFT JOIN imep_opens                   io ON io.TNumber  = hr.T_NUMBER
WHERE  COALESCE(sp.view_count, 0) + COALESCE(io.open_count, 0) > 0
```

---

## Pitfalls

### 1. `T_NUMBER` vs. `TNumber` vs. `UbsId` — three variants of the same key

| Where | Column name | Case |
|---|---|---|
| `tbl_hr_employee` | `T_NUMBER` | lowercase |
| `tbl_email_receiver_status`, `tbl_analytics_link` | `TNumber` | lowercase |
| `tbl_hr_user` | `UbsId` | UPPERCASE |

Join iMEP engagement ↔ HR = direct. Join HR ↔ `tbl_hr_user` = needs `LOWER()`.

### 2. GPN comes **only** from `tbl_hr_employee.WORKER_ID`

GPN does **not** exist on `tbl_hr_employee` as a separate column called "GPN". The 8-digit ID is called `WORKER_ID`. The HR columns scanned earlier did list it ("Primary worker identifier") — but not flagged as "= GPN".

### 3. There are more HR identifiers — but only these matter for us

`tbl_hr_employee` also carries: `ABACUS_ID`, `ALTERNATE_WORKER_ID`, `WEBSSO`, `UUNAME`, `PersonalNumber`. For cross-channel analytics `T_NUMBER` + `WORKER_ID` is enough. The other IDs serve different use cases (WebSSO for auth, Abacus for finance joins).

### 4. HR is a snapshot, not history

`tbl_hr_employee` and `tbl_hr_costcenter` represent the **current** HR state. Regional analysis "Person X was in EMEA in July, in APAC in August" does **not** work out of the box — you need snapshot tables for that (if they exist, still to be clarified).

### 5. LEFT JOIN, never INNER JOIN

Not all TNumbers in iMEP engagement are in HR (former employees, external addresses). `INNER JOIN` drops them.

### 6. `viewingcontactid` on SP Gold is **not** a TNumber

When working in `sharepoint_gold.pbi_db_interactions_metrics`, do **not** use `viewingcontactid` for HR joins. It is a GUID, not a GPN. Bridge candidates:
- `sharepoint_gold.pbi_db_employeecontact` (24M rows, potential bridge — not validated)
- or fall back to `sharepoint_bronze.pageviews.user_gpn`

---

## References

- HR cards: `tbl_hr_employee.md`, `tbl_hr_costcenter.md`, `tbl_hr_user.md` *(pending)*
- Employee identifier overview: `memory/employee_identifiers.md`
- Bridge resolution: `memory/hr_gpn_tnumber_bridge_resolved.md`
- ER diagram Section 3: [architecture_diagram.md](../architecture_diagram.md)

---

## Sources

Genie sessions backing the statements on this page: [Q3](../sources.md#q3), [Q3a](../sources.md#q3a), [Q3b](../sources.md#q3b), [Q27](../sources.md#q27). See [sources.md](../sources.md) for the full index.
