# `imep_bronze.tbl_hr_employee`

> **The HR master table.** Carries **both** `T_NUMBER` (iMEP-compatible) and `WORKER_ID` (= GPN, SharePoint-compatible) — **this** is the cross-channel employee bridge. No external source required.

| | |
|---|---|
| **Layer** | Bronze (lives in `imep_bronze`, but used cross-domain) |
| **Source system** | HR system (SQL Server) -> Change Data Capture (CDC) -> Delta Bronze |
| **Grain** | 1 row per Employee (current snapshot) |
| **Primary key** | `T_NUMBER` |
| **Bridge columns** | `T_NUMBER` (iMEP) + `WORKER_ID` (SharePoint/GPN) |
| **Write pattern** | MERGE (Service Principal) |
| **Approx row count** | ~265K (as of today) |

---

## Key Columns

| Column | Type | Role | Notes |
|---|---|---|---|
| `T_NUMBER` | string | **PK** | Lowercase `t######`. Joins to `TNumber` in iMEP engagement tables. |
| `WORKER_ID` | string | **= GPN** | 8-digit numeric (`00100200`). Bridge to `sharepoint_bronze.pageviews.user_gpn`. |
| `ORGANIZATIONAL_UNIT` | string | FK -> `tbl_hr_costcenter.ORGANIZATIONAL_UNIT` | Bridge to Region/Division/Area/Country |
| `ALTERNATE_WORKER_ID` | string | Alt. ID | Secondary identifier — for special cases |
| `ABACUS_ID` | string | Finance ID | Not for analytics joins |
| `WEBSSO` | string | Auth ID | Not for analytics joins |
| `UUNAME` | string | Username | Not for analytics joins |
| `PersonalNumber` | string | Legacy | Do not use |

Full schema via `DESCRIBE imep_bronze.tbl_hr_employee` (~30-40 columns).

---

## Sample row

```
T_NUMBER              = "t100200"
WORKER_ID             = "00100200"
ORGANIZATIONAL_UNIT   = "CH-ZH-0041"
ALTERNATE_WORKER_ID   = "..."
```

---

## Primary joins

### -> iMEP Engagement (TNumber bridge)

```sql
SELECT al.*, hr.WORKER_ID, hr.ORGANIZATIONAL_UNIT
FROM   imep_bronze.tbl_analytics_link al
LEFT JOIN imep_bronze.tbl_hr_employee  hr ON hr.T_NUMBER = al.TNumber
```

### -> SharePoint pageviews (GPN bridge)

```sql
SELECT pv.*, hr.T_NUMBER
FROM   sharepoint_bronze.pageviews   pv
LEFT JOIN imep_bronze.tbl_hr_employee hr ON hr.WORKER_ID = pv.user_gpn
```

### -> Region/Division/Area (via ORGANIZATIONAL_UNIT)

```sql
SELECT hr.T_NUMBER, cc.REGION, cc.DIVISION, cc.AREA, cc.COUNTRY
FROM   imep_bronze.tbl_hr_employee   hr
LEFT JOIN imep_bronze.tbl_hr_costcenter cc ON cc.ORGANIZATIONAL_UNIT = hr.ORGANIZATIONAL_UNIT
```

### -> `tbl_hr_user` (for Town / further office metadata)

```sql
SELECT hr.T_NUMBER, u.UbsId, u.Town
FROM   imep_bronze.tbl_hr_employee hr
LEFT JOIN imep_bronze.tbl_hr_user   u ON LOWER(u.UbsId) = LOWER(hr.T_NUMBER)
```

---

## Quality caveats

- **Snapshot, no history**: Current HR state. Employee moves (EMEA -> APAC) propagate at the latest after the next 12h MERGE — older engagement rows show the new value **afterwards**.
- **Ex-employees disappear**: Anyone who has left drops out of the table. Use `LEFT JOIN` for historical engagement data — otherwise you lose their events.
- **`WORKER_ID` is always an 8-digit string with leading zeros**. Watch out for type coercion: `INT(WORKER_ID)` loses the leading zeros.

---

## Lineage

```
HR system (SQL Server) --[CDC + MERGE]--> imep_bronze.tbl_hr_employee
                                                         |
                                                         +--> denormalized into imep_gold.final
                                                         +--> ad-hoc JOIN for HR enrichment
```

---

## References

- [hr_enrichment.md](../../joins/hr_enrichment.md) — Canonical join patterns
- [tbl_hr_costcenter.md](tbl_hr_costcenter.md) — Region/Division/Area lookup
- [tbl_hr_user.md](tbl_hr_user.md) — Town/UbsId lookup
- Memory: `employee_identifiers.md`, `hr_gpn_tnumber_bridge_resolved.md`

---

## Sources

Genie sessions backing the statements on this page: [Q3](../../sources.md#q3), [Q3a](../../sources.md#q3a), [Q3b](../../sources.md#q3b), [Q27](../../sources.md#q27), [Q28](../../sources.md#q28). See [sources.md](../../sources.md) for the full directory.
