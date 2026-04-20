# `imep_bronze.tbl_hr_employee`

> **Die HR-Master-Tabelle.** Führt **gleichzeitig** `T_NUMBER` (iMEP-kompatibel) und `WORKER_ID` (= GPN, SharePoint-kompatibel) — **das** ist die Cross-Channel-Employee-Bridge. Keine externe Quelle nötig (Q3b).

| | |
|---|---|
| **Layer** | Bronze (liegt in `imep_bronze`, wird aber domänenübergreifend genutzt) |
| **Source system** | HR-System (SQL Server) → CDC → Delta |
| **Grain** | 1 row per Employee (aktueller Snapshot) |
| **Primary key** | `T_NUMBER` |
| **Bridge columns** | `T_NUMBER` (iMEP) + `WORKER_ID` (SharePoint/GPN) |
| **Refresh** | 2×/Tag (MERGE, Service Principal) — Q28 |
| **Approx row count** | ~265K (Q27-Stand) |
| **PII** | **Hoch** — alle Employee-Daten. Vorsicht bei Dashboard-Konsumption. |

---

## Key Columns

| Column | Type | Role | Notes |
|---|---|---|---|
| `T_NUMBER` | string | **PK** | Lowercase `t######`. Joint zu `TNumber` in iMEP-Engagement-Tabellen. |
| `WORKER_ID` | string | **= GPN** | 8-digit numeric (`00100200`). Bridge zu `sharepoint_bronze.pageviews.user_gpn`. |
| `ORGANIZATIONAL_UNIT` | string | FK → `tbl_hr_costcenter.ORGANIZATIONAL_UNIT` | Bridge zu Region/Division/Area/Country |
| `ALTERNATE_WORKER_ID` | string | Alt. ID | Secondary identifier — für spezielle Fälle |
| `ABACUS_ID` | string | Finance-ID | Nicht für Analytics-Joins |
| `WEBSSO` | string | Auth-ID | Nicht für Analytics-Joins |
| `UUNAME` | string | Username | Nicht für Analytics-Joins |
| `PersonalNumber` | string | Legacy | Nicht nutzen |

Volles Schema via `DESCRIBE imep_bronze.tbl_hr_employee` (~30-40 Spalten inkl. PII wie Name, Geburtsdatum etc. — für Cross-Channel-Analytics nicht relevant, weil wir auf Region/Division aggregieren).

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

### → iMEP Engagement (TNumber-Bridge)

```sql
SELECT al.*, hr.WORKER_ID, hr.ORGANIZATIONAL_UNIT
FROM   imep_bronze.tbl_analytics_link al
LEFT JOIN imep_bronze.tbl_hr_employee  hr ON hr.T_NUMBER = al.TNumber
```

### → SharePoint pageviews (GPN-Bridge)

```sql
SELECT pv.*, hr.T_NUMBER
FROM   sharepoint_bronze.pageviews   pv
LEFT JOIN imep_bronze.tbl_hr_employee hr ON hr.WORKER_ID = pv.user_gpn
```

### → Region/Division/Area (via ORGANIZATIONAL_UNIT)

```sql
SELECT hr.T_NUMBER, cc.REGION, cc.DIVISION, cc.AREA, cc.COUNTRY
FROM   imep_bronze.tbl_hr_employee   hr
LEFT JOIN imep_bronze.tbl_hr_costcenter cc ON cc.ORGANIZATIONAL_UNIT = hr.ORGANIZATIONAL_UNIT
```

### → `tbl_hr_user` (für Town / weitere Office-Metadaten)

```sql
SELECT hr.T_NUMBER, u.UbsId, u.Town
FROM   imep_bronze.tbl_hr_employee hr
LEFT JOIN imep_bronze.tbl_hr_user   u ON LOWER(u.UbsId) = LOWER(hr.T_NUMBER)
```

---

## Quality caveats

- **Snapshot, kein History**: Aktueller HR-Stand. Mitarbeiter-Wechsel (EMEA → APAC) propagiert spätestens nach dem nächsten 12h-MERGE — ältere Engagement-Rows zeigen **danach** den neuen Wert.
- **Ex-Mitarbeiter verschwinden**: Wer gekündigt hat, fällt aus der Tabelle raus. `LEFT JOIN` für historische Engagement-Daten verwenden — sonst verlierst du deren Events.
- **PII-Vollständigkeit**: Tabelle enthält Name, Geburtsdatum, E-Mail etc. Für Cross-Channel-Dashboards nur `T_NUMBER`/`WORKER_ID`/`ORGANIZATIONAL_UNIT` ziehen — Klartext-PII nicht in Dashboards leaken.
- **`WORKER_ID` ist immer 8-digit string mit führenden Nullen**. Vorsicht bei Type-Coercion: `INT(WORKER_ID)` verliert die Leading-Zeroes.

---

## Lineage

```
HR-System (SQL Server) ──[CDC + MERGE 2×/Tag]──► imep_bronze.tbl_hr_employee
                                                         │
                                                         ├──► denormalized into imep_gold.final
                                                         └──► ad-hoc JOIN für HR-Enrichment
```

---

## Referenzen

- [hr_enrichment.md](../../joins/hr_enrichment.md) — Canonical Join-Patterns
- [tbl_hr_costcenter.md](tbl_hr_costcenter.md) — Region/Division/Area Lookup
- [tbl_hr_user.md](tbl_hr_user.md) — Town/UbsId Lookup
- Memory: `employee_identifiers.md`, `hr_gpn_tnumber_bridge_resolved.md`
