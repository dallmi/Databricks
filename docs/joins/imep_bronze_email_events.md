# Join Recipe — iMEP Bronze Email Events

> Die kanonische Bronze-Kette für alle per-Empfänger-Event-Analysen. **4 Tabellen**, Grain = 1 Row pro Event pro Empfänger pro Mailing. Das ist die Wahrheit bevor Gold sie denormalisiert.

**Wann nutzen**: Ad-hoc-Analysen, Debugging, Fälle wo `imep_gold.final` nicht die richtige Auflösung hat (z.B. brauchst du eine Bronze-Spalte, die nicht in Gold landet).

**Nicht nutzen** für: Dashboard-Backends — da ist `imep_gold.final` schneller und bereits HR-enriched.

---

## Die kanonische 4-Tabellen-Kette

```sql
SELECT
    -- Mailing context
    e.TrackingId,
    e.Title,
    e.Subject,

    -- Recipient context (von receiver_status)
    rs.TNumber,
    rs.Receiver,
    rs.EmailLanguage,
    rs.DateTime         AS send_time,
    rs.LogStatus        AS send_status,

    -- Event context (von analytics_link)
    al.LinkTypeEnum     AS event_type,
    al.CreationDate     AS event_time,
    al.Agent            AS device_agent,
    al.CurrentLanguage,

    -- HR-Enrichment (separat per Join)
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

WHERE   e.TrackingId IS NOT NULL                   -- Cross-Channel-Filter
  AND   rs.DateTime >= '2025-01-01'                -- Default-Zeitraum (Q24 adoption ramp)
  AND   rs.LogStatus IN ('Sent', 'Open')           -- je nach Analyse anpassen
```

---

## Varianten

### Variante A — Nur Sends (ohne Open/Click)

Wenn du nur wissen willst, **wer hat welches Mailing bekommen** (Bounce-Analyse etc.):

```sql
SELECT e.TrackingId, rs.TNumber, rs.LogStatus, rs.DateTime
FROM   imep_bronze.tbl_email                  e
JOIN   imep_bronze.tbl_email_receiver_status  rs ON rs.EmailId = e.Id
WHERE  e.TrackingId IS NOT NULL
```

→ 293M Rows total. Pro Mailing × Empfänger eine Row. `tbl_analytics_link` wird nicht benötigt, wenn nur Send-Events interessant sind.

### Variante B — Nur Opens/Clicks (Fact-only)

Wenn Send-Kontext nicht interessiert:

```sql
SELECT al.EmailId, al.TNumber, al.LinkTypeEnum, al.CreationDate, al.Agent
FROM   imep_bronze.tbl_analytics_link al
WHERE  al.IsActive = 1
  AND  al.CreationDate >= '2025-01-01'
```

→ 533M Rows. Am teuersten — unbedingt Zeit-Filter setzen.

### Variante C — Pack-Level-Aggregate

Für Dashboard-Numbers (nicht per-event):

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

## Gotchas

### 1. `al.IsActive = 1` IMMER filtern

Ohne diesen Filter ziehst du Soft-Deleted-Events in die Zahlen. Dashboards werden zu hoch.

### 2. Doppel-FK zwischen `analytics_link` und `receiver_status`

`tbl_analytics_link` hat **beide** `EmailId` und `EmailReceiverStatusId` als FK. Beide im Join-Condition verwenden — sonst fährst du Cartesian-Risk, falls ein Empfänger mehrere Send-Events hatte (selten, aber möglich bei Retries).

### 3. `tbl_analytics_link` nur für OPEN + CLICK, nicht für Sends

Sends leben in `tbl_email_receiver_status`, nicht in `tbl_analytics_link`. Eine häufige Verwechslung.

### 4. `rs.DateTime` vs `al.CreationDate`

- `rs.DateTime` = wann wurde gesendet
- `al.CreationDate` = wann wurde geöffnet/geklickt

Für Time-to-Open-Analysen: `DATEDIFF(second, rs.DateTime, al.CreationDate)`.

### 5. HR-Join ist `N:1`, aber kann sich ändern

`tbl_hr_employee` wird 2×/Tag upserted. Region/Division sind **aktuell**, nicht historisch. Für "Wer war am Tag X in welcher Division" braucht's `imep_bronze.tbl_hr_employee_history` (falls existent) oder Snapshot-Merge.

### 6. `TrackingId` nur auf `tbl_email`

Nicht auf `receiver_status` oder `analytics_link` suchen — die tragen's nicht. Immer via `e.Id = EmailId` herholen.

---

## Performance-Tuning

- **Zeitfilter vorne**: `rs.DateTime >= '...'` als erste WHERE-Clause → Partition-Pruning wenn vorhanden
- **TrackingId-Pre-Filter**: `WHERE e.TrackingId IS NOT NULL` reduziert die Start-Menge auf ~1.3% der Mailings
- **`LEFT JOIN` auf `tbl_analytics_link`**, nicht `INNER JOIN`: Sonst verlierst du Mailings, die nie geöffnet wurden
- **Cache auf Pack-Level-Aggregaten**: Wenn du den gleichen Funnel 10× am Tag pullst, materialisieren in eine lokale View

---

## Referenzen

- Volle Field-Liste pro Tabelle: [tbl_email.md](../tables/imep/tbl_email.md), [tbl_email_receiver_status.md](../tables/imep/tbl_email_receiver_status.md), [tbl_analytics_link.md](../tables/imep/tbl_analytics_link.md)
- ER-Diagramm: [er_imep_bronze.md](../diagrams/er_imep_bronze.md)
- Regeln: [join_strategy_contract.md](join_strategy_contract.md)
