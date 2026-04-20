# `imep_bronze.tbl_hr_user`

> Zusätzliche Employee-Metadaten — vor allem **Town**. Führt `UbsId` als **UPPERCASE**-Variante der T-Number. Nur joinen, wenn du Town o.ä. brauchst — für Region/Division reicht `tbl_hr_employee` + `tbl_hr_costcenter`.

| | |
|---|---|
| **Layer** | Bronze (HR-Domain) |
| **Source system** | HR-System → CDC → Delta |
| **Grain** | 1 row per Employee |
| **Primary key** | `UbsId` |
| **Refresh** | 2×/Tag (MERGE) |
| **PII** | Direkt identifizierend (UbsId + Town) |

---

## Key Columns

| Column | Type | Role | Notes |
|---|---|---|---|
| `UbsId` | string | PK | **UPPERCASE** `T######` (z.B. `T100200`) — Case-differenz zu `T_NUMBER`! |
| `Town` | string | | Office-Standort (z.B. `Zurich`, `Wollerau`) |

Weitere Spalten existieren (Emergency Contact, etc.) — für Analytics irrelevant.

---

## Sample row

```
UbsId = "T100200"
Town  = "Zurich"
```

---

## Primary joins

### → `tbl_hr_employee` (Case-Normalisierung!)

```sql
SELECT hr.T_NUMBER, u.UbsId, u.Town
FROM   imep_bronze.tbl_hr_employee hr
JOIN   imep_bronze.tbl_hr_user      u ON LOWER(u.UbsId) = LOWER(hr.T_NUMBER)
```

**Wichtig**: `LOWER()` auf beiden Seiten. `T_NUMBER` ist lowercase (`t100200`), `UbsId` ist UPPERCASE (`T100200`) — direkter Vergleich ergibt 0 Treffer.

---

## Quality caveats

- **Case-Inkonsistenz zwischen `UbsId` und `T_NUMBER`** — der häufigste HR-Footgun. Immer `LOWER()` anwenden.
- **Subset von `tbl_hr_employee`** — `tbl_hr_user` enthält typischerweise weniger oder gleich viele Rows als `tbl_hr_employee`. Wer in `tbl_hr_employee` steht, muss nicht unbedingt in `tbl_hr_user` sein.
- **Town ist freier String** — für Standort-Aggregation Dedup/Mapping-Tabelle pflegen.

---

## Referenzen

- [tbl_hr_employee.md](tbl_hr_employee.md) — Haupt-HR-Tabelle
- [hr_enrichment.md](../../joins/hr_enrichment.md)
