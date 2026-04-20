# `imep_bronze.tbl_hr_costcenter`

> **The org dimension lookup.** Maps `ORGANIZATIONAL_UNIT` to Region / Division / Area / Country. The single source for geographic-organizational aggregation in dashboards.

| | |
|---|---|
| **Layer** | Bronze (HR domain) |
| **Source system** | HR system -> Change Data Capture (CDC) -> Delta Bronze |
| **Grain** | 1 row per Organizational Unit |
| **Primary key** | `ORGANIZATIONAL_UNIT` |
| **Write pattern** | MERGE (Service Principal) |
| **Approx row count** | < 10K (cost center list) |

---

## Key Columns

| Column | Type | Role | Notes |
|---|---|---|---|
| `ORGANIZATIONAL_UNIT` | string | **PK** | Code such as `CH-ZH-0041` |
| `REGION` | string | | `EMEA`, `Americas`, `APAC`, `Switzerland`, ... |
| `DIVISION` | string | | Business area (e.g. `Wealth Management`, `Group Insurance Management`) |
| `AREA` | string | | Finer than Division, coarser than Country |
| `COUNTRY` | string | | ISO country or plain text |

---

## Sample row

```
ORGANIZATIONAL_UNIT = "CH-ZH-0041"
REGION              = "Switzerland"
DIVISION            = "Wealth Management"
AREA                = "Zurich"
COUNTRY             = "CH"
```

---

## Primary joins

### -> HR (standard lookup)

```sql
SELECT hr.T_NUMBER, cc.REGION, cc.DIVISION, cc.COUNTRY
FROM   imep_bronze.tbl_hr_employee   hr
LEFT JOIN imep_bronze.tbl_hr_costcenter cc ON cc.ORGANIZATIONAL_UNIT = hr.ORGANIZATIONAL_UNIT
```

-> See [hr_enrichment.md](../../joins/hr_enrichment.md) for complete patterns.

---

## Quality caveats

- **Current state, no history**: Cost centers can be reorganized. On division renames, that propagates into **all** existing engagement rows after the next rebuild run of `imep_gold.final`.
- **`DIVISION` values are not enumerated** — free-form string. Dashboards must maintain dedup + case-normalization + mapping tables themselves if, for example, "Group Insurance Management" should be treated as the same bucket as "GIM".
- **66% NULL-Region problem**: In the Tier-3 aggregates (`tbl_pbi_mailings_region`), up to 66% of rows have NULL Region. Cause: `ORGANIZATIONAL_UNIT` values on engagement events were **not** listed in `tbl_hr_costcenter` at build time. Dashboards must provide an "Unknown" bucket for this.

---

## References

- [tbl_hr_employee.md](tbl_hr_employee.md)
- [hr_enrichment.md](../../joins/hr_enrichment.md)

---

## Sources

Genie sessions backing the statements on this page: [Q16](../../sources.md#q16), [Q21](../../sources.md#q21). See [sources.md](../../sources.md) for the full directory.
