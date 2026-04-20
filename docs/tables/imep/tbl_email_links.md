# `imep_bronze.tbl_email_links`

> **Template-URL-Inventory.** Welche Links enthält ein Mailing-Template. **Nicht** für Funnel-Metriken — dafür `tbl_analytics_link`. Lean-Schema (10 Spalten), statische Template-Definition.

| | |
|---|---|
| **Layer** | Bronze |
| **Source system** | iMEP (SQL Server) → CDC → Delta |
| **Grain** | 1 row per URL pro Email-Template |
| **Primary key** | `Id` |
| **FK** | `EmailId` → `tbl_email.Id` |
| **Refresh** | 2×/Tag @ 00:00/12:00 UTC (MERGE) |
| **Approx row count** | ~(mittlere Grössenordnung — mehrere Links pro Mailing × 145K Mailings) |

---

## Key Columns

| Column | Type | Role | Notes |
|---|---|---|---|
| `Id` | string | **PK** | Eindeutig pro Template-Link |
| `EmailId` | string | **FK** → `tbl_email.Id` | Welches Mailing |
| `ElementValueId` | string | | Template-Element-Referenz |
| `Url` | string | | Klartext-URL |
| `LinkLabel` | string | | Anzeige-Label des Links |

---

## Unterscheidung: `tbl_email_links` vs `tbl_analytics_link`

| | `tbl_email_links` | `tbl_analytics_link` |
|---|---|---|
| **Was** | Template-Definition (statisch) | Event-Fact (dynamisch) |
| **Grain** | Pro Link pro Mailing-Template | Pro Click/Open-Event |
| **Rows** | Niedrig (~Mailings × ~5 Links) | **533M** |
| **Für** | "Welche URLs sind im Mailing X?" | "Wer hat welchen Link wann geklickt?" |

**Der häufigste Verwechslungsfehler im Projekt**. Merkregel: `_links` = Template, `analytics_` = Fact.

---

## Primary joins

### → `tbl_email` (N:1) — Mailing-Context

```sql
SELECT el.Url, el.LinkLabel, e.TrackingId, e.Title
FROM   imep_bronze.tbl_email_links el
JOIN   imep_bronze.tbl_email        e ON e.Id = el.EmailId
WHERE  e.TrackingId IS NOT NULL
```

### → `tbl_analytics_link` (N:1 via ElementValueId) — Click-Attribution pro Link

```sql
-- Hypothetical — exact join key via ElementValueId to be verified (Q30)
SELECT el.Url, COUNT(al.Id) AS clicks
FROM   imep_bronze.tbl_email_links     el
LEFT JOIN imep_bronze.tbl_analytics_link al ON al.EmailId = el.EmailId
                                             AND al.LinkTypeEnum = 'CLICK'
                                             /* AND al.<?> = el.ElementValueId -- TBD */
GROUP BY el.Url
```

---

## Quality caveats

- **Keine direkte FK zu `tbl_analytics_link`**: Die Click-Events referenzieren `EmailId` und `TNumber`, aber nicht direkt den `tbl_email_links.Id`. Link-spezifische Click-Attribution muss über `ElementValueId` oder URL-Match erfolgen — **noch nicht validiert** (Q30-Follow-up).
- **Statisches Schema** — URL ändert sich nicht nach Publikation. Redirect-Aufschlüsselung (z.B. Shortlinks) passiert **extern** (Newsletter-Tool) und ist in dieser Tabelle nicht sichtbar.

---

## Referenzen

- [tbl_email.md](tbl_email.md)
- [tbl_analytics_link.md](tbl_analytics_link.md) — Fact-Table für Click-Events
- [er_imep_bronze.md](../../diagrams/er_imep_bronze.md)
