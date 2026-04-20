# `imep_bronze.tbl_hr_costcenter`

> **Das Org-Dimension-Lookup.** Mappt `ORGANIZATIONAL_UNIT` auf Region / Division / Area / Country. Die einzige Quelle für geografisch-organisatorische Aggregation in Dashboards.

| | |
|---|---|
| **Layer** | Bronze (HR-Domain) |
| **Source system** | HR-System → CDC → Delta |
| **Grain** | 1 row per Organizational Unit |
| **Primary key** | `ORGANIZATIONAL_UNIT` |
| **Refresh** | 2×/Tag (MERGE, Service Principal) |
| **Approx row count** | < 10K (Cost-Center-Liste) |
| **PII** | Keine direkte PII — organisatorische Dimension |

---

## Key Columns

| Column | Type | Role | Notes |
|---|---|---|---|
| `ORGANIZATIONAL_UNIT` | string | **PK** | Code wie `CH-ZH-0041` |
| `REGION` | string | | `EMEA`, `Americas`, `APAC`, `Switzerland`, ... |
| `DIVISION` | string | | Business-Bereich (z.B. `Wealth Management`, `Group Insurance Management`) |
| `AREA` | string | | Finer als Division, coarser als Country |
| `COUNTRY` | string | | ISO-Country oder Klartext |

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

### → HR (Standard-Lookup)

```sql
SELECT hr.T_NUMBER, cc.REGION, cc.DIVISION, cc.COUNTRY
FROM   imep_bronze.tbl_hr_employee   hr
LEFT JOIN imep_bronze.tbl_hr_costcenter cc ON cc.ORGANIZATIONAL_UNIT = hr.ORGANIZATIONAL_UNIT
```

→ Siehe [hr_enrichment.md](../../joins/hr_enrichment.md) für vollständige Patterns.

---

## Quality caveats

- **Aktueller Stand, keine Historie**: Cost-Center können reorganisiert werden. Bei Divisions-Umbenennungen propagiert das in **alle** existierenden Engagement-Rows nach dem nächsten CTAS-Run von `imep_gold.final`.
- **`DIVISION`-Werte sind nicht enumeriert** — freier String. Dashboards müssen Dedup + Case-Norm + Mapping-Tabelle selber pflegen, wenn z.B. "Group Insurance Management" mit "GIM" als derselbe Bucket laufen soll.
- **66% NULL-Region-Problem** (Q16/Q21): In den Tier-3-Aggregaten (`tbl_pbi_mailings_region`) haben bis zu 66% der Rows NULL-Region. Ursache: `ORGANIZATIONAL_UNIT` in Engagement-Events war zur Build-Zeit **nicht** in `tbl_hr_costcenter` gelistet. Dashboards müssen dafür einen "Unknown"-Bucket vorsehen.

---

## Referenzen

- [tbl_hr_employee.md](tbl_hr_employee.md)
- [hr_enrichment.md](../../joins/hr_enrichment.md)
