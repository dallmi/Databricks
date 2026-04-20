# Join Strategy Contract — Cross-Channel Analytics

> **Zweck**: Single-Pager mit den wichtigsten Regeln für Joins im Cross-Channel-Modell. Lies das, **bevor** du SQL schreibst. Jede Regel stammt aus einem konkreten Genie-Finding (Q1–Q30).

---

## Die 5 Regeln, an die du dich halten musst

### Regel 1 — Cross-Channel läuft dimensional, nie über Engagement

**DO**:
```sql
tbl_email.TrackingId  ↔  sharepoint_bronze.pages.UBSGICTrackingID
```

**DON'T**:
```sql
-- FALSCH: direkter Join von Engagement-Facts zwischen Domains
tbl_analytics_link  ↔  sharepoint_bronze.pageviews   -- NEIN
tbl_email_receiver_status  ↔  sharepoint_gold.pbi_db_interactions_metrics  -- NEIN
```

**Warum**: TrackingId existiert in **genau 4 Tabellen** (`tbl_email`, `tbl_event`, `tbl_pbi_platform_mailings`, `tbl_pbi_platform_events`) und koexistiert **nie** mit `EmailId` in derselben Tabelle. SharePoint hat kein Person-Key-Equivalent zu TNumber. Cross-Channel muss deshalb **immer** über die Dimensions-Tabellen `tbl_email` ↔ `pages` laufen. *(Q27)*

---

### Regel 2 — TNumber existiert nur in 2 Tabellen. Person-Level-Analytics sind Email-only.

**Person-Level-Joins nur möglich über**:
- `imep_bronze.tbl_email_receiver_status.TNumber`
- `imep_bronze.tbl_analytics_link.TNumber`

**Join zu HR**:
```sql
ON analytics_link.TNumber = tbl_hr_employee.T_NUMBER   -- beide lowercase t######
```

**Sonderfälle**:
- `tbl_hr_user.UbsId` ist **UPPERCASE** → `LOWER(UbsId) = LOWER(T_NUMBER)` joinen
- GPN kommt aus `pageviews.user_gpn` (8-digit) → `tbl_hr_employee.WORKER_ID` ist die Bridge (**keine** externe Quelle nötig)

**Warum**: TNumber erscheint nur in zwei Tabellen. Für SharePoint-Person-Analytics muss separat über `sharepoint_gold.pbi_db_employeecontact` (24M Rows) oder CDM resolved werden — das ist **nicht** TNumber-kompatibel ohne Bridge. *(Q3, Q27)*

---

### Regel 3 — TrackingId-Match: SEG1–4, niemals SEG5

TrackingId-Format: `CLUSTER-PACK-YYMMDD-ACTIVITY-CHANNEL` (32 Zeichen, 5 Segmente, UPPER).

**DO**:
```sql
-- Für Cross-Channel-Match: nur SEG1-4 vergleichen
ON  array_join(slice(split(email.TrackingId, '-'), 1, 4), '-')
  = array_join(slice(split(pages.UBSGICTrackingID, '-'), 1, 4), '-')
```

**DON'T**:
```sql
-- FALSCH: vollständiger String-Match
ON email.TrackingId = pages.UBSGICTrackingID   -- nur 6/1677 Treffer (Jaccard 0.004)
```

**Warum**: SEG5 encodiert **System-Ownership**, nicht Kanal. iMEP: `EMI`/`NLI`/`TWE`. SharePoint: `IAN`/`ITS`/`OPN`/`ANC`. Full-String-Match produziert fast keine Treffer, obwohl inhaltlich dieselbe Activity gemeint ist. *(Q23)*

---

### Regel 4 — Gold ist CTAS-Full-Rebuild. Respektiere Refresh-Fenster.

**Refresh-Zeiten (UTC)**:
| Layer | Zeit | Pattern |
|---|---|---|
| iMEP Bronze | 00:00 + 12:00 | MERGE Upsert |
| SharePoint Bronze | 02:00 | MERGE (pages) + Append (pageviews, 7 Bursts in 1 Min) |
| iMEP Gold | 00:23 + 12:25 | **CTAS Full Rebuild** ← kritisch |

**DO**:
- Dashboard-Queries zwischen `00:35–11:55` und `12:35–23:55` UTC (sicheres Fenster)
- Für Scheduled Reports: `00:40 UTC` oder `12:40 UTC` als frühester Slot

**DON'T**:
- Dashboard-Scheduled-Refresh um `00:20–00:30` oder `12:20–12:30` UTC
- Lange Transactions über diese Fenster hinweg öffnen

**Warum**: Gold-Tables werden komplett zerstört und neu aufgebaut (`CREATE OR REPLACE TABLE AS SELECT`). Queries mitten im Fenster können auf partiellen / inkonsistenten Stand treffen. *(Q28)*

---

### Regel 5 — Coverage-Realität: ~4% Pages, ~1.3% Mailings

**Pflicht-Caveats** in jedem Dashboard:

1. **SharePoint-Seite**: Nur 1,949 / 48,419 Pages (~4%) haben `UBSGICTrackingID`. Nur diese ~4% der 84M Interaction-Rows sind Pack-attribuierbar. Restliche 96% = "untracked intranet activity" — separate Sektion oder transparent gelabelt.

2. **iMEP-Seite**: Nur 986 / 73,930 Mailings (1.3%) haben TrackingId. Aber: starker Uptrend (2024: 99 → 2025: 637 → 2026 YTD: 250). **Default-Zeitraum des Dashboards: ab 2025.**

3. **Site-Konzentration**: 99.4% der getrackten Pages liegen auf **einer einzigen Site** ("News and events"). Falls Dashboard global filtert → Empfehlung, defaultmässig auf diese Site zu beschränken.

**Warum**: Ohne diese Caveats sehen Stakeholder massive Absolut-Zahlen und falsche Rate-Berechnungen. *(Q22, Q24, Q25)*

---

## Quick-Reference — Join-Patterns

### Pattern A: Per-Recipient-Event (Bronze-Kette, die 533M-Row-Wahrheit)

```sql
SELECT e.TrackingId, rs.TNumber, al.LinkTypeEnum, al.CreationDate AS event_time, al.Agent
FROM   imep_bronze.tbl_email e
JOIN   imep_bronze.tbl_email_receiver_status rs ON rs.EmailId              = e.Id
JOIN   imep_bronze.tbl_analytics_link       al ON al.EmailReceiverStatusId = rs.Id
                                              AND al.EmailId               = e.Id
WHERE  e.TrackingId IS NOT NULL
  AND  al.IsActive  = 1
```

→ Full-Detail-Grain, aber 533M Rows. Nur bei Ad-hoc-Analysen.

### Pattern B: Gold-Consumption (wenn es schnell sein muss)

```sql
SELECT *
FROM   imep_gold.final
WHERE  /* whatever filter */
```

→ 520M Rows, schon denormalisiert, HR-enriched. **Default für Dashboard-Backends.**

### Pattern C: Cross-Channel-Funnel (Pack-Level)

```sql
WITH pack AS (
  SELECT DISTINCT
         array_join(slice(split(UPPER(TrackingId), '-'), 1, 2), '-') AS tracking_pack_id,
         TrackingId
  FROM   imep_bronze.tbl_email
  WHERE  TrackingId IS NOT NULL
),
email_side AS (
  SELECT p.tracking_pack_id,
         COUNT(DISTINCT rs.Id)              AS sent,
         COUNT(DISTINCT al_open.Id)         AS opened,
         COUNT(DISTINCT al_click.Id)        AS clicked
  FROM   pack p
  JOIN   imep_bronze.tbl_email e             ON e.TrackingId = p.TrackingId
  LEFT JOIN imep_bronze.tbl_email_receiver_status rs ON rs.EmailId = e.Id
  LEFT JOIN imep_bronze.tbl_analytics_link al_open  ON al_open.EmailId = e.Id  AND al_open.LinkTypeEnum = 'OPEN'
  LEFT JOIN imep_bronze.tbl_analytics_link al_click ON al_click.EmailId = e.Id AND al_click.LinkTypeEnum = 'CLICK'
  GROUP BY 1
),
sp_side AS (
  SELECT array_join(slice(split(UPPER(p.UBSGICTrackingID), '-'), 1, 2), '-') AS tracking_pack_id,
         SUM(m.views)  AS page_views,
         SUM(m.visits) AS page_visits
  FROM   sharepoint_bronze.pages p
  JOIN   sharepoint_gold.pbi_db_interactions_metrics m ON m.marketingPageId = p.pageUUID
  WHERE  p.UBSGICTrackingID IS NOT NULL
  GROUP BY 1
)
SELECT COALESCE(e.tracking_pack_id, s.tracking_pack_id) AS tracking_pack_id,
       e.sent, e.opened, e.clicked,
       s.page_views, s.page_visits
FROM   email_side e
FULL OUTER JOIN sp_side s ON s.tracking_pack_id = e.tracking_pack_id;
```

→ Das ist der Funnel auf Pack-Ebene. `FULL OUTER JOIN`, weil viele Packs nur Email haben oder nur Intranet.

---

## Footgun-Alarm

| Footgun | Symptom | Fix |
|---|---|---|
| `TrackingId` auf Engagement-Tabellen suchen | `column not found` | TrackingId lebt nur auf `tbl_email`, `tbl_event`, `tbl_pbi_platform_mailings`, `tbl_pbi_platform_events` |
| Full-String-Match von TrackingIds | 0 oder fast 0 Treffer | SEG1-4 vergleichen (siehe Regel 3) |
| `UbsId = T_NUMBER` ohne Case-Norm | 0 Treffer | `LOWER(UbsId) = LOWER(T_NUMBER)` |
| Dashboard-Refresh um `00:25 UTC` | Teilweise leere Gold-Tables | Refresh-Fenster respektieren (Regel 4) |
| `SUM(views)` ohne TID-Filter | Aufgeblähte Zahlen | `WHERE UBSGICTrackingID IS NOT NULL` für attribuierten Funnel |
| `COUNT(*) FROM imep_gold.final` ohne Filter | ~520M, Timeout | Immer zeitlich einschränken oder mit PartitionKey filtern |
| Mailing-Send-Zeit aus `tbl_email.CreationDate` | Falsche Timestamps | Send-Zeit steht in `tbl_email_receiver_status.DateTime` |
| `GICTrackingID` vs `UBSGICTrackingID` verwechseln | Schema-inkonsistent | `pages` hat `UBSGICTrackingID`, `pageviews` hat `GICTrackingID` — beide meinen dasselbe, aber Case-Varianten kommen vor |

---

## Quellen-Index

Jede Regel in diesem Dokument ist rückverfolgbar zu einer Genie-Session:

- **Q1, Q27** → TrackingId-Location + Dimensional-vs-Factual
- **Q3, Q3a/b** → HR-Bridge (GPN↔TNumber via WORKER_ID)
- **Q22** → 4%-Coverage-Blocker + FK-Chain
- **Q23** → SEG5-Divergenz (Channel-Ownership)
- **Q24, Q25** → Adoption-Timeline + Site-Konzentration
- **Q26** → Silver-Asymmetrie
- **Q27** → Join-Graph-Topologie
- **Q28** → Refresh-Cadence + CTAS-Windows

Volldetails in [genie_questions_imep.md](../genie_questions_imep.md) und im Memory unter `memory/*.md`.
