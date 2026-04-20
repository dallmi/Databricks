# `imep_bronze.tbl_hr_user`

> Additional employee metadata — primarily **Town**. Carries `UbsId` as the **UPPERCASE** variant of the T-Number. Only join when you need Town or similar — for Region/Division `tbl_hr_employee` + `tbl_hr_costcenter` is sufficient.

| | |
|---|---|
| **Layer** | Bronze (HR domain) |
| **Source system** | HR system -> Change Data Capture (CDC) -> Delta Bronze |
| **Grain** | 1 row per Employee |
| **Primary key** | `UbsId` |
| **Write pattern** | MERGE |

---

## Key Columns

| Column | Type | Role | Notes |
|---|---|---|---|
| `UbsId` | string | PK | **UPPERCASE** `T######` (e.g. `T100200`) — case difference vs. `T_NUMBER`! |
| `Town` | string | | Office location (e.g. `Zurich`, `Wollerau`) |

Further columns exist (Emergency Contact, etc.) — irrelevant for analytics.

---

## Sample row

```
UbsId = "T100200"
Town  = "Zurich"
```

---

## Primary joins

### -> `tbl_hr_employee` (case normalization!)

```sql
SELECT hr.T_NUMBER, u.UbsId, u.Town
FROM   imep_bronze.tbl_hr_employee hr
JOIN   imep_bronze.tbl_hr_user      u ON LOWER(u.UbsId) = LOWER(hr.T_NUMBER)
```

**Important**: `LOWER()` on both sides. `T_NUMBER` is lowercase (`t100200`), `UbsId` is UPPERCASE (`T100200`) — a direct comparison returns 0 matches.

---

## Quality caveats

- **Case inconsistency between `UbsId` and `T_NUMBER`** — the most common HR pitfall. Always apply `LOWER()`.
- **Subset of `tbl_hr_employee`** — `tbl_hr_user` typically contains fewer or equal rows compared to `tbl_hr_employee`. Anyone in `tbl_hr_employee` is not necessarily in `tbl_hr_user`.
- **Town is a free-form string** — maintain dedup/mapping table for location aggregation.

---

## References

- [tbl_hr_employee.md](tbl_hr_employee.md) — Main HR table
- [hr_enrichment.md](../../joins/hr_enrichment.md)

---

## Sources

Genie sessions backing the statements on this page: [Q3](../../sources.md#q3), [Q3b](../../sources.md#q3b). See [sources.md](../../sources.md) for the full directory.
