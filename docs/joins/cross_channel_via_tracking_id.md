# Join Recipe — Cross-Channel via TrackingId (SEG1-4 Match)

> Die vollständige Cross-Channel-Kette zwischen iMEP (Email) und SharePoint (Intranet). **Dimensional**, nicht factual — läuft zwingend über `tbl_email.TrackingId` ↔ `sharepoint_bronze.pages.UBSGICTrackingID`. Full-String-Match produziert fast 0 Treffer; **SEG1-4-Match** ist Pflicht.

**Wann nutzen**: Jede Dashboard-Ansicht, die Mailings zu Page-Views attribuiert. Jede Funnel-Analyse "Email → Landing Page".

---

## Die Regel in einer Zeile

```sql
array_join(slice(split(UPPER(email.TrackingId),        '-'), 1, 4), '-')
=
array_join(slice(split(UPPER(pages.UBSGICTrackingID), '-'), 1, 4), '-')
```

**Warum SEG1-4 und nicht alle 5?** SEG5 encodiert System-Ownership:
- iMEP: `EMI` / `NLI` / `TWE`
- SharePoint: `IAN` / `ITS` / `OPN` / `ANC`

Full-String-Match: **6 von 1677 Treffer** (Jaccard 0.004). SEG1-4-Match: 54 attribuierbare Packs (Q23/Q24).

---

## Der kanonische Cross-Channel-Funnel (Pack-Level)

Die vollständige SQL, die du in einem Dashboard ausführen würdest — liefert pro Pack: Email-Sends/Opens/Clicks **und** SharePoint-Views/Visits.

```sql
WITH
-- 1. Normalisierter Email-Pack-Key
email_packs AS (
  SELECT
      e.Id                                                             AS mailing_id,
      e.TrackingId,
      array_join(slice(split(UPPER(e.TrackingId), '-'), 1, 2), '-')    AS tracking_pack_id,
      array_join(slice(split(UPPER(e.TrackingId), '-'), 1, 4), '-')    AS seg_1_4
  FROM   imep_bronze.tbl_email e
  WHERE  e.TrackingId IS NOT NULL
),

-- 2. Email-side aggregates
email_side AS (
  SELECT
      ep.tracking_pack_id,
      ep.seg_1_4,
      COUNT(DISTINCT rs.Id)                                                            AS sends,
      COUNT(DISTINCT CASE WHEN al.LinkTypeEnum = 'OPEN'  THEN al.TNumber END)          AS unique_opens,
      COUNT(DISTINCT CASE WHEN al.LinkTypeEnum = 'CLICK' THEN al.TNumber END)          AS unique_clicks
  FROM   email_packs ep
  LEFT JOIN imep_bronze.tbl_email_receiver_status rs ON rs.EmailId = ep.mailing_id
  LEFT JOIN imep_bronze.tbl_analytics_link         al ON al.EmailReceiverStatusId = rs.Id
                                                      AND al.IsActive = 1
  GROUP BY ep.tracking_pack_id, ep.seg_1_4
),

-- 3. Normalisierter SP-Pack-Key
sp_packs AS (
  SELECT
      p.pageUUID,
      p.UBSGICTrackingID,
      array_join(slice(split(UPPER(p.UBSGICTrackingID), '-'), 1, 2), '-') AS tracking_pack_id,
      array_join(slice(split(UPPER(p.UBSGICTrackingID), '-'), 1, 4), '-') AS seg_1_4
  FROM   sharepoint_bronze.pages p
  WHERE  p.UBSGICTrackingID IS NOT NULL
),

-- 4. SharePoint-side aggregates
sp_side AS (
  SELECT
      sp.tracking_pack_id,
      sp.seg_1_4,
      SUM(m.views)                         AS total_views,
      SUM(m.visits)                        AS total_visits,
      SUM(m.durationsum)                   AS total_duration_sec,
      COUNT(DISTINCT m.viewingcontactid)   AS unique_viewers
  FROM   sp_packs sp
  JOIN   sharepoint_gold.pbi_db_interactions_metrics m ON m.marketingPageId = sp.pageUUID
  WHERE  m.visitdatekey >= '20250101'
  GROUP BY sp.tracking_pack_id, sp.seg_1_4
)

-- 5. FULL OUTER JOIN — Packs can be email-only OR page-only
SELECT
    COALESCE(e.tracking_pack_id, s.tracking_pack_id) AS tracking_pack_id,
    e.sends, e.unique_opens, e.unique_clicks,
    s.total_views, s.total_visits, s.unique_viewers, s.total_duration_sec,
    CASE
      WHEN e.tracking_pack_id IS NOT NULL AND s.tracking_pack_id IS NOT NULL THEN 'both'
      WHEN e.tracking_pack_id IS NOT NULL                                    THEN 'email_only'
      ELSE                                                                        'sp_only'
    END                                              AS coverage_status

FROM        email_side e
FULL OUTER JOIN sp_side s ON s.seg_1_4 = e.seg_1_4
ORDER BY tracking_pack_id;
```

---

## Varianten

### Variante A — Nur "both"-Packs (echte Cross-Channel-Attribution)

Wenn du nur die Packs willst, wo **beide** Kanäle Daten haben (= echter Funnel möglich):

```sql
-- Same CTEs as above, then:
SELECT e.tracking_pack_id, e.sends, e.unique_opens, e.unique_clicks,
       s.total_views, s.unique_viewers
FROM   email_side e
INNER JOIN sp_side s ON s.seg_1_4 = e.seg_1_4
```

→ Erwartete Ergebnis-Grösse: ~54 Packs (Q24).

### Variante B — Activity-Level statt Pack-Level (SEG1-3)

Falls du noch feiner auflösen willst (pro einzelne Activity, nicht per Pack):

```sql
-- Key wird SEG1-3 statt SEG1-2
array_join(slice(split(UPPER(TrackingId), '-'), 1, 3), '-')
```

### Variante C — Untracked SP-Activity als "noise" sichtbar machen

```sql
-- Vor dem FULL OUTER JOIN, in sp_side:
SELECT '(untracked)' AS seg_1_4, SUM(views), SUM(visits), ...
FROM   sharepoint_gold.pbi_db_interactions_metrics m
LEFT JOIN sharepoint_bronze.pages p ON p.pageUUID = m.marketingPageId
WHERE  p.UBSGICTrackingID IS NULL

UNION ALL

-- regular sp_side query
```

→ Zeigt "unattribuierte Intranet-Aktivität" neben attribuiertem Funnel.

---

## Gotchas

### 1. `UPPER()` auf beiden Seiten Pflicht

Q23 bestätigt: Format ist auf beiden Seiten UPPER & clean. Trotzdem `UPPER()` anwenden für Defensivcode — du willst nicht bei einem einzigen lowercase-Edge-Case 90% deiner Treffer verlieren.

### 2. `TRIM()` als Versicherung

Whitespaces in Tracking-IDs sind selten, aber in Legacy-Daten nicht ausgeschlossen:

```sql
array_join(slice(split(UPPER(TRIM(TrackingId)), '-'), 1, 4), '-')
```

### 3. SEG1-4, **nicht** SEG1-5

Der wichtigste Footgun des ganzen Projekts. SEG5 gehört dem System (iMEP vs SP), nicht der Activity. Nie full-string matchen.

### 4. FULL OUTER JOIN, nicht INNER

Die meisten Packs haben **nur** Email-Daten oder **nur** Page-Daten. Nur ~54 Packs sind "both". `INNER JOIN` verliert 90%+ der Packs — dann hat das Dashboard nur eine winzige Teilmenge als Basis.

### 5. Pack-Grain ist SEG1-2 (Cluster + Pack-Number)

Wenn du auf Pack-Level aggregierst, ist der Key `CLUSTER-PACKNUMBER` (z.B. `QRREP-0000058`). Activity-Level ist SEG1-3 (mit Datum). Dashboard-Default: **Pack-Level**.

### 6. TrackingId ist kein Zeit-Attribut

TrackingId enthält zwar SEG3 = Datum (YYMMDD), aber das ist die **Activity-Erstellungszeit**, nicht die Send-/View-Zeit. Für temporale Filter `rs.DateTime` / `m.visitdatekey` nutzen.

### 7. Pre-2025-Coverage ist dünn

Default-Zeitfilter ab `2025-01-01` für iMEP, `20250101` für SP. Vor 2025 ist TrackingId-Adoption < 20% — Funnel-Zahlen werden unzuverlässig.

---

## Erwartete Grössenordnungen (Q24/Q25)

Nach den Coverage-Findings:
- Email-Packs mit TrackingId (ab 2025): ~54
- SP-Packs mit TrackingId (ab 2024-09): ~83 (davon 83 auf einer einzigen Site "News and events")
- **Überlappung**: ~54 Packs — das ist das Cross-Channel-Dashboard-Universum.

---

## Referenzen

- [join_strategy_contract.md](join_strategy_contract.md) — Regel 1, 3, 5
- [er_cross_channel.md](../diagrams/er_cross_channel.md) — ER-Diagramm mit Bridge
- [tbl_email.md](../tables/imep/tbl_email.md) · [pages.md](../tables/sharepoint/pages.md) — Die beiden Dimensions-Tabellen
- Memory: `tracking_id_format_q23.md`, `tracking_id_volume_q24.md`, `sharepoint_pages_coverage_q25.md`
