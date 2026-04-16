# Business Requirements Document — Cross-Channel Communication Analytics

**Projekt:** Multi-Channel Communication Analytics (Arbeitstitel: *CammsView*)
**Version:** 0.1 — Draft
**Status:** In Review
**Datum:** 2026-04-15
**Autoren:** Founder Office
**Zielgruppe:** Business Analysts, Data Engineers, Dashboard Developers

---

## 1. Executive Summary

Wir bauen eine integrierte analytische Sicht auf alle geplanten Kommunikations­aktivitäten (Emails, Intranet-News, Events, Banner, SMS), indem wir die in CPLAN geplanten Aktivitäten mit der tatsächlichen Delivery-Telemetrie aus AppInsights und iMEP verbinden. Der Join erfolgt über die system­generierte `CammsTrackingID`, die jede Aktivität in jedem Kanal trägt.

Output ist ein einheitliches Dashboard, das den Funnel *Planung → Zustellung → Konsum → Konversion* pro Kommunikations-Pack, Cluster und Zeitraum zeigt und Aussagen über die Wirksamkeit einzelner Kampagnen zulässt.

## 2. Business Goals

| # | Ziel | Messgrösse |
|---|---|---|
| G1 | End-to-End-Transparenz über alle Kanäle eines Packs hinweg | 100% der CPLAN-Packs im Dashboard sichtbar |
| G2 | Wirksamkeit pro Kampagne quantifizieren (Funnel) | Funnel-Raten Email→Page→Event pro Pack |
| G3 | Zielgruppen-Analyse über HR-Dimensionen | Views / UV pro Division, Region, Management Level |
| G4 | Content-Performance für Content Owner | Ranking Pages nach UV, Avg TOS, Bounce |
| G5 | Datenbasis für nachfolgende Kanal-Onboardings (BAN, SMS) | Wiederverwendbarkeit des Modells |

## 3. Scope

### 3.1 In Scope (MVP)

Drei Datenquellen, verbunden über `CammsTrackingID`:

1. **iMEP (Email)** — Send / Open / Click Events.
   Source: `imep_bronze.tbl_email_receiver_status` (Databricks / Unity Catalog).
   Channel-Abbr: `EMI`.

2. **AppInsights PageViews (Intranet)** — Bereits geflattened durch
   [`scripts/flatten_appinsights.py`](../scripts/flatten_appinsights.py) →
   `fact_page_view` / `agg_session`.
   Channel-Abbr: `INT`.

3. **CPLAN Pages / Activity-Metadaten** — Page-Inventory und Activity-Planung
   (`pbi_db_website_page_inventory`, `pbi_db_website_webpage_inventory`, CPLAN packs/clusters/activities).

Flankierend:
- **HR Snapshot Join** (GPN → Division / Region / Management Level) — bereits im PageView-Pipeline integriert.
- **Dashboard** — [`dashboard/multi_channel.html`](../dashboard/multi_channel.html), DuckDB-WASM + Chart.js, single-file.

### 3.2 Out of Scope (Phase 1)

- Event-Registrierungen (`EVT`, iMEP) — kommt in Phase 2, sobald MVP stabil.
- Banner (`BAN`), SMS — spätere Phasen.
- Real-Time-Streaming (Daily Batch reicht).
- Predictive Modelling / Next-Best-Action — explizit ausgeschlossen.

## 4. Datenmodell

### 4.1 Der zentrale Join Key — `CammsTrackingID`

32 Zeichen, 5 Segmente, `-`-separiert:

```
QRREP-0000058-240709-0000060-EMI
  │       │       │       │     └── tracking_channel_abbr  (EMI / INT / EVT / BAN)
  │       │       │       └──────── tracking_activity_number
  │       │       └──────────────── tracking_pub_date (YYMMDD)
  │       └──────────────────────── tracking_pack_number
  └──────────────────────────────── tracking_cluster_id
  └───────┬───────┘
   tracking_pack_id (Segment 1+2) — Grain für Dashboard-Aggregation
```

Die ID wird in CPLAN pro Activity generiert und in jedem Kanal mit­gegeben. Sie ist damit der einzige zuverlässige Cross-Channel-Join-Key.

**Zu beachten:** CPLAN-Quell-CSVs enthalten sowohl `Tracking ID` als auch `Tacking ID` (Tippfehler). Beide Varianten müssen in der ETL gelesen werden.

### 4.2 Layered Architecture (Unity Catalog)

```
Bronze                 Silver                          Gold
──────────             ─────────────────────           ──────────────────────
imep_bronze.*     ──►  silver.fact_email         ──►  gold.fact_cross_channel
appinsights raw   ──►  silver.fact_page_view     ──►  gold.dim_pack
cplan packs/acts  ──►  silver.dim_pack / dim_activity
hr_history        ──►  silver.dim_employee_temporal
pbi_db_website_*  ──►  silver.dim_page
```

**Silver-Fakten führen alle 7 Tracking-Spalten** (`tracking_id`, `tracking_pack_id`, `tracking_cluster_id`, `tracking_pack_number`, `tracking_pub_date`, `tracking_activity_number`, `tracking_channel_abbr`) als first-class columns.

### 4.3 Silver Facts — Schemas

**`silver.fact_email`** (1 Row pro Link-Interaktion / Send-Event — folgt iMEP Genie-Pattern 2, siehe Anhang A)

| Column | Type | Quelle |
|---|---|---|
| email_event_id | STRING PK | `TBL_ANALYTICS_LINK.Id` bzw. `TBL_EMAIL_RECEIVER_STATUS.Id` (Send-Branch) |
| mailing_id | STRING | `TBL_EMAIL.Id` |
| mailing_title | STRING | `TBL_EMAIL.Title` |
| tracking_id, tracking_pack_id, … | STRING | Split aus `CammsTrackingID` (Spalte tbd, siehe OP-04) |
| t_number | STRING (`t######`) | `TBL_ANALYTICS_LINK.TNumber` / `TBL_EMAIL_RECEIVER_STATUS.TNumber` — **Recipient-ID in iMEP** |
| gpn | STRING (`########` 8-digit) | aus HR-Bridge resolved (TNumber → GPN) — Cross-Source-Schlüssel gegen PageView |
| event | STRING (`sent` / `opened` / `clicked` / `bounced` / …) | abgeleitet aus `TBL_EMAIL_RECEIVER_STATUS` + `TBL_ANALYTICS_LINK.linkTypeenum` |
| event_ts | TIMESTAMP | `CreationDate` (Send: STATUS, Click/Open: ANALYTICS_LINK) |
| device_type | STRING | `Agent` + Multi-Device-CTE → `Desktop & Mobile` / `Desktop Only` / `Mobile Only` |
| current_language | STRING | `TBL_ANALYTICS_LINK.CurrentLanguage` |
| timespan_h | INT | `(bigint(link.CreationDate) - bigint(status.CreationDate)) / 3600` |
| hr_org_unit, hr_division, hr_area, hr_region, hr_country, hr_town | STRING | `TBL_HR_EMPLOYEE` / `TBL_HR_COSTCENTER` / `TBL_HR_USER` |
| created_by | STRING | `TBL_EMAIL.CreatedBy` (resolved via `TBL_HR_EMPLOYEE.T_NUMBER`) |
| source_file | STRING | ETL |

**Wichtig**: `IsActive = 1` als Pflicht-Filter überall. Open vs Click ist `linkTypeenum`, nicht eigener Status.

**`silver.fact_page_view`** — bestehend, siehe [README.md §Data Model](../README.md).

**`silver.dim_pack`** (1 Row pro Communication Pack)

| Column | Quelle |
|---|---|
| tracking_pack_id (PK) | CPLAN |
| cluster_id, cluster_name | CPLAN |
| pack_name, pack_theme, pack_topic, target_region, target_org | CPLAN |
| planned_channels (ARRAY<STRING>) | CPLAN activities |
| audience_size | CPLAN / iMEP |
| publish_date | CPLAN |

### 4.4 Gold — Cross-Channel Fact

Eine Row pro `tracking_pack_id`, Spalten pro Kanal:

```sql
WITH email AS (
  SELECT tracking_pack_id,
         COUNT(*) FILTER (WHERE event='sent')    AS email_sent,
         COUNT(*) FILTER (WHERE event='opened')  AS email_opened,
         COUNT(*) FILTER (WHERE event='clicked') AS email_clicked
  FROM silver.fact_email GROUP BY 1
),
intranet AS (
  SELECT tracking_pack_id,
         COUNT(*)                AS page_views,
         COUNT(DISTINCT user_id) AS unique_readers,
         AVG(time_on_page_sec)   AS avg_time_on_page
  FROM silver.fact_page_view GROUP BY 1
)
SELECT p.*, e.*, i.*
FROM silver.dim_pack p
LEFT JOIN email e    USING (tracking_pack_id)
LEFT JOIN intranet i USING (tracking_pack_id);
```

## 5. Functional Requirements

### 5.1 ETL — `FR-ETL`

| # | Anforderung |
|---|---|
| FR-ETL-01 | iMEP Bronze → Silver: Split `CammsTrackingID` in 7 Spalten (Referenz-Logik: CPLAN `process_cplan.py` ~L559–579). |
| FR-ETL-02 | Beide Spalten-Varianten `Tracking ID` und `Tacking ID` lesen. |
| FR-ETL-03 | Delta/Upsert-Load basierend auf File-Hash (Vorbild: `flatten_appinsights.py`, `processed_files`-Manifest). |
| FR-ETL-04 | PageView-Pipeline: `CammsTrackingID` muss als first-class column in `fact_page_view` exponiert sein (heute in `customDimensions.CustomProps`). |
| FR-ETL-05 | HR Join: Temporal, monatlich, GPN 8-stellig zero-padded, Fallback nächst­jüngerer Snapshot (bestehend). |
| FR-ETL-06 | Gold-Materialisierung nightly (Batch). |
| FR-ETL-07 | Orphan-Handling: Page Views ohne `CammsTrackingID` bleiben erhalten (werden aggregiert in `tracking_pack_id = 'UNATTRIBUTED'`). |

### 5.2 Dashboard — `FR-DASH`

Single-File HTML (DuckDB-WASM + Chart.js), siehe Corporate Branding Guidelines in [CLAUDE.md](../../CLAUDE.md).

| # | View | Inhalt |
|---|---|---|
| FR-DASH-01 | **Overview** | KPI-Cards: # Packs, Emails Sent, Page Views, Unique Readers, Avg Funnel-Rate |
| FR-DASH-02 | **Pack Explorer** | Tabelle aller Packs mit Channel-Kennzahlen, filterbar nach Cluster / Zeitraum / Theme / Region |
| FR-DASH-03 | **Cross-Channel Funnel** | Pro Pack: Sent → Opened → Clicked → Page View → (später: Event Registered) mit Konversions­raten |
| FR-DASH-04 | **Audience Breakdown** | Page Views nach HR-Division, Region, Management Level (Bar + Heatmap) |
| FR-DASH-05 | **Content Performance** | Top Pages nach UV, Avg TOS, Bounce Rate; gefiltert nach Content Owner / Theme |
| FR-DASH-06 | **Time Series** | Wöchentlicher Trend über Channels hinweg, stacked |
| FR-DASH-07 | **Filters (global)** | Zeitraum, Cluster, Kanal, Region, Division |

### 5.3 Non-Functional — `NFR`

| # | Anforderung |
|---|---|
| NFR-01 | Refresh: Nightly Batch, Ziel < 30 Min End-to-End. |
| NFR-02 | Dashboard-Ladezeit < 3s bei Datenmenge von 12 Monaten. |
| NFR-03 | Keine Brand-Namen im Code (`--corp-*`, nicht `--ubs-*`). |
| NFR-04 | Corporate Color Palette verbindlich (siehe [CLAUDE.md](../../CLAUDE.md)). |
| NFR-05 | PII (GPN, Email) wird in Gold nicht exponiert — nur `user_id` und HR-Dimensionen (siehe [pii_cleanup_pending](../../../.claude/projects/-Users-micha-Documents-Arbeit-Databricks/memory/pii_cleanup_pending.md)). |
| NFR-06 | Alle Timestamps in CET; UTC-Konvertierung im ETL. |

## 6. Datenfluss (High-Level)

```
CPLAN (SharePoint CSV)  ──►  silver.dim_pack / dim_activity
iMEP Bronze             ──►  silver.fact_email
AppInsights pageViews   ──►  silver.fact_page_view (existing pipeline)
HR History              ──►  silver.dim_employee_temporal
pbi_db_website_*        ──►  silver.dim_page
                                         │
                                         ▼
                             gold.fact_cross_channel
                                         │
                                         ▼
                             dashboard/multi_channel.html
```

## 7. Rollen & Deliverables

| Rolle | Deliverable |
|---|---|
| BA | User Stories pro Dashboard-View, Acceptance Criteria, Validierung Kennzahlen­definitionen |
| Data Engineer | Silver/Gold DLT-Pipelines, iMEP Bronze→Silver ETL, Unit Tests |
| Dashboard Dev | `multi_channel.html` an Gold anbinden, Corporate Styling |
| QA | Reconciliation CPLAN ↔ iMEP ↔ AppInsights pro Pack (Row-Counts, Funnel-Plausibilität) |

## 8. Meilensteine (indikativ)

| Phase | Inhalt | Dauer |
|---|---|---|
| P0 | iMEP Schema-Discovery, CammsTrackingID im PageView first-class | 1 W |
| P1 | `silver.fact_email` + `silver.dim_pack` | 2 W |
| P2 | `gold.fact_cross_channel` + Dashboard MVP (Overview + Pack Explorer) | 2 W |
| P3 | Audience Breakdown + Content Performance + Funnel-View | 2 W |
| P4 | Event-Channel (`EVT`) onboarding | 2 W |

---

## 9. Open Points / Klärungs­bedarf

Die folgenden Punkte sind **vor** dem Start der Implementierung mit den jeweiligen Stakeholdern zu klären. Ohne diese Antworten kann keine vollständige End-to-End-Doku produziert werden.

### 9.1 iMEP (Email Channel)

> **Update 2026-04-16**: OP-01, OP-03, OP-07 weitgehend geklärt durch Genie-Code (siehe Anhang A & [memory/imep_data_model.md](../../../.claude/projects/-Users-micha-Documents-Arbeit-Databricks/memory/imep_data_model.md)). Resterklärung verbleibt unten.

- ~~**OP-01**~~ ✅ Schema bekannt — `TBL_EMAIL_RECEIVER_STATUS` (Empfänger-Status), `TBL_ANALYTICS_LINK` (Open/Click), `TBL_EMAIL` (Mailing-Master). Siehe Anhang A.
- **OP-02** Vollständige Liste der Werte in `TBL_EMAIL_RECEIVER_STATUS` (Send/Bounce/Unsubscribe) und `TBL_ANALYTICS_LINK.linkTypeenum` (`OPEN`, `CLICK`, …) — Bestätigung mit BA.
- ~~**OP-03**~~ ✅ Unique-Dedup: `RecipientID + EmailId`. Multi-Device via CTE `HAVING COUNT(DISTINCT Agent) > 1`.
- **OP-04** **Wo lebt die `CammsTrackingID` in iMEP?** — auf `TBL_EMAIL` (1× pro Mailing) oder pro Link in `TBL_EMAIL_LINKS`? Genie-Code zeigt sie nicht. **Kritisch für Cross-Channel-Join.**
- **OP-05** Audience-Size pro Pack: Kommt diese aus iMEP (Distribution List Size) oder CPLAN (`pack.target_audience_size`)?
- **OP-06** Historisierung: Wie weit zurück reichen iMEP-Daten? Retention Policy?
- ~~**OP-07**~~ ✅ Recipient läuft über `TNumber` (Format `t100200`) — HR-Join in iMEP via `TBL_HR_EMPLOYEE.T_NUMBER`.
- **OP-07b** (neu) `TBL_HR_COSTCENTER.ORGANIZATIONAL_UNIT`-Join: Eindeutigkeit (1:1) und Historisierung (Org-Wechsel)?
- **OP-07c** (neu) `TBL_EMAIL.CreatedBy` — wird das im Dashboard als Filter / Dimension benötigt (Creator-Reporting)?
- **OP-07d** (neu) **TNumber ↔ GPN Bridge**: TNumber (`t100200`, iMEP) und GPN (`00100200`, AppInsights) sind unterschiedliche Identifier. In welcher HR-Tabelle / Spalte liegt das Mapping? `TBL_HR_EMPLOYEE` muss beide Spalten führen oder es braucht eine separate Bridge. **Voraussetzung für jede Cross-Source-Aggregation auf Empfänger-Ebene** (z.B. "wer hat Mail X erhalten und Page Y besucht").

### 9.2 CammsTrackingID

- **OP-08** Werden in iMEP jemals Emails *ohne* `CammsTrackingID` versendet (Transactional)? Filter-Regel?
- **OP-09** Sind `CammsTrackingID` case-sensitive? Normalisierungs­regel.
- **OP-10** Gibt es historische Packs, die das 5-Segment-Schema noch nicht erfüllen? Backward-Compatibility-Strategie.

### 9.3 CPLAN Integration

- **OP-11** Liegt CPLAN in Databricks gespiegelt vor (Unity Catalog), oder muss aus dem CPLAN-Repo (`/CPLAN/`) separat geladen werden?
- **OP-12** Aktualität der CPLAN-Daten in Databricks — Batch-Frequenz?
- **OP-13** Definition von *Pack-Name / Theme / Topic* — welche CPLAN-Felder gelten als Master?
- **OP-14** Cluster-Hierarchie: Gibt es Cluster-Gruppen / Cluster-Owner, die im Dashboard gebraucht werden?

### 9.4 AppInsights / PageViews

- **OP-15** `CammsTrackingID` in `customDimensions.CustomProps` — wird sie konsequent für *alle* Intranet-Pages gesetzt, oder nur bei verlinkten Campaign-Pages?
- **OP-16** Attribution-Window: Wie lange nach Email-Send darf ein PageView noch der Kampagne zugerechnet werden, wenn `CammsTrackingID` fehlt (Orphan)?
- **OP-17** Banner (`BAN`): Wie werden Banner-Impressions in AppInsights geliefert? Eigener EventType?

### 9.5 HR / Organisation

- **OP-18** HR Snapshot-Frequenz und Verfügbarkeit in Databricks (heute: Parquet aus SearchAnalytics).
- **OP-19** Management-Level-Taxonomie: Welche Werte sind im Dashboard-Filter erlaubt?
- **OP-20** Externals / Non-Employees: Wie werden PageViews ohne GPN behandelt?

### 9.6 Dashboard / UX

- **OP-21** Primäre User-Persona: Content Owner, Campaign Manager, Communications-Lead? Einfluss auf Default-Views und Filter.
- **OP-22** Berechtigungen: Sehen alle Nutzer alle Packs, oder Row-Level-Security nach Content-Owner / Division?
- **OP-23** Drill-Down-Tiefe: Soll pro Pack ein Detail-View mit Session-Listing existieren?
- **OP-24** Export-Anforderungen: XLSX-Export pro View nötig? (Wenn ja: Corporate XLSX-Format nach [CLAUDE.md](../../CLAUDE.md).)
- **OP-25** Mobile-Support notwendig?

### 9.7 Governance / Compliance

- **OP-26** PII-Freigabe: Ist `user_id` (anonymer Browser-ID) datenschutzkonform, oder braucht es zusätzliche Aggregationsschwellen (k-Anonymität, min 5 User je Bucket)?
- **OP-27** Retention im Gold-Layer: Wie lange dürfen aggregierte Kampagnen-KPIs gehalten werden?
- **OP-28** Externe Empfänger: Dürfen deren Interaktionen analysiert werden?
- **OP-29** Audit-Log für Dashboard-Zugriffe erforderlich?

### 9.8 Betrieb

- **OP-30** Monitoring & Alerting für die ETL-Jobs — Tooling (Databricks Workflows, externe Tools)?
- **OP-31** On-Call und Incident-Response-Prozess.
- **OP-32** CI/CD für DLT-Pipelines (Bundles / Repos).
- **OP-33** Kostendeckel für Databricks-Compute.

---

**Nächster Schritt:** Durchgehen der Open Points OP-01 bis OP-33 in einem Kickoff mit BA, Data Engineer und Dashboard Dev. Danach Re-Issue als BRD v1.0.

---

## Anhang A — iMEP Genie-Code Referenz-Patterns

Quelle: Databricks Genie Notebook (Cells 6/7/24/25). Screenshots: `Bilder/16. April 2026/IMG_7331..7334.jpeg`.

### Tabellen-Übersicht

| Tabelle | Inhalt | Grain |
|---|---|---|
| `TBL_EMAIL_RECEIVER_STATUS` | Empfänger-Status pro Email | 1 Row pro Empfänger pro Mailing |
| `TBL_EMAIL` | Mailing-Master (Title, Status, CreatedBy) | 1 Row pro Mailing |
| `TBL_ANALYTICS_LINK` | Runtime Click/Open Events (Agent, linkTypeenum, CurrentLanguage) | 1 Row pro Interaktion |
| `TBL_EMAIL_LINKS` | Template-Links (Design-Time) | 1 Row pro Link im Template |
| `TBL_EMAIL_COMPONENTS` | Top-Level-Komponenten (Name, Order) | |
| `TBL_EMAIL_COMPONENT_ELEMENTS` | Elemente (KeyName, Name) | |
| `TBL_ELEMENT_VALUES` | Bindeglied Link ↔ Element | |
| `TBL_HR_EMPLOYEE` | HR-Stammdaten via T_NUMBER | |
| `TBL_HR_COSTCENTER` | Org-Unit → Division/Area/Region/Country | |
| `TBL_HR_USER` | Town | |

### Pattern 1 — KPI Summary (Cells 6, 24)

Aggregiert Empfängerzahlen pro Mailing nach Delivery-Status, Sprache, Creator.

```sql
SELECT ...
FROM TBL_EMAIL_RECEIVER_STATUS a
LEFT JOIN TBL_ANALYTICS_LINK c ON a.Id = c.EmailReceiverStatusId   -- CurrentLanguage
LEFT JOIN TBL_EMAIL b          ON a.EmailId = b.Id                 -- Title, Status
LEFT JOIN TBL_HR_EMPLOYEE hr   ON b.CreatedBy = hr.T_NUMBER        -- Creator
WHERE a.IsActive = 1
```

### Pattern 2 — Final Fact Table (Cell 7)

Denormalisierte Fact-Tabelle mit Geo, Org, Device, Timespan. **Vorlage für `silver.fact_email`.**

```sql
WITH mdclicks AS (
  SELECT Id, Agent, EmailId, TNumber AS RecipientID,
         'Desktop & Mobile' AS NewDeviceType,
         CASE WHEN linkTypeenum = 'OPEN' THEN 'Unique Opens'
              ELSE 'Unique Clicks' END AS Engagementtype
  FROM TBL_ANALYTICS_LINK
  WHERE IsActive = 1 AND Agent IN ('desktop','mobile') AND linkTypeenum != 'OPEN'
),
mdlinks AS (
  SELECT Id, RecipientID, EmailId, NewDeviceType, Engagementtype
  FROM mdclicks
  WHERE RecipientID IN (
    SELECT RecipientID FROM mdclicks
    GROUP BY EmailId, RecipientID
    HAVING COUNT(DISTINCT Agent) > 1
  )
)
SELECT ...
FROM TBL_ANALYTICS_LINK a
LEFT JOIN TBL_EMAIL_RECEIVER_STATUS c ON a.EmailReceiverStatusId = c.Id      -- SentDateTime
LEFT JOIN TBL_HR_EMPLOYEE hr          ON a.TNumber = hr.T_NUMBER             -- Org-Unit
LEFT JOIN TBL_HR_COSTCENTER cc        ON hr.ORGANIZATIONAL_UNIT = cc.ORGANIZATIONAL_UNIT
LEFT JOIN TBL_HR_USER u               ON hr.T_NUMBER = u.UbsId               -- Town
LEFT JOIN mdlinks hh                  ON a.Id = hh.Id                        -- Multi-Device
WHERE c.EmailId IS NOT NULL AND a.EmailId IS NOT NULL AND a.IsActive = 1
```

- **Timespan-Bucket**: `(bigint(a.CreationDate) - bigint(c.CreationDate)) / 3600` (Stunden Send → Click).
- **FinalDeviceType**: COALESCE des CTE-Resultats mit raw `Agent`, Suffix `" Only"` für Single-Device.

### Pattern 3 — Engagement Detail (Cell 25)

UNION mit Template-Metadata, damit Links mit 0 Klicks im Reporting auftauchen.

```sql
SELECT x.Url, b.EmailId AS MailingId,
       x.linkLabel,
       b.KeyName AS SourceComponent,
       b.Name    AS SourceComponentName,
       a.KeyName AS SourceElement,
       a.Name    AS SourceElementName,
       b.Order   AS ComponentOrder
FROM TBL_EMAIL_LINKS x
LEFT JOIN TBL_ELEMENT_VALUES y           ON y.Id = x.ElementValueId
LEFT JOIN TBL_EMAIL_COMPONENT_ELEMENTS a ON a.Id = y.ElementId
LEFT JOIN TBL_EMAIL_COMPONENTS b         ON b.Id = a.ComponentId
```

### Konsequenzen für unser Modell

- `silver.fact_email` baut auf **Pattern 2** auf — Grain = 1 Row pro Link-Interaktion (+ separater Send-Branch aus `TBL_EMAIL_RECEIVER_STATUS` für `event=sent`).
- `event` wird **nicht** aus einem einzigen Status-Feld gelesen, sondern aus zwei Quellen kombiniert:
  - `TBL_EMAIL_RECEIVER_STATUS` → `sent`, `bounced`, `unsubscribed`
  - `TBL_ANALYTICS_LINK.linkTypeenum` → `opened` (`OPEN`) vs `clicked` (alles andere)
- HR-Join in iMEP über `TNumber` (Format `t100200`). **TNumber ≠ GPN** — GPN (`00100200`) wird in AppInsights/PageView verwendet. Für Cross-Source-Joins auf Empfänger-Ebene wird eine HR-Bridge benötigt (siehe OP-07d).
- Multi-Device-Detection (CTE) mitnehmen — relevant für Engagement-Analyse.
- `CurrentLanguage` ist verfügbar — Sprach-Dimension im Dashboard möglich.
