# Cross-Channel Analytics — Technical Knowledge Base

> **Scope**: Bronze → (Silver) → Gold Datenpfade für iMEP (Email) und SharePoint (Intranet), plus Cross-Channel-Linking über TrackingId. Diese Dokumentation richtet sich an Engineer/Analyst-Neuzugänge — in 15 Minuten muss klar sein, welche Tabelle was enthält und wie sie zu joinen ist.

---

## Wo anfangen?

Wenn du **neu im Projekt** bist, in dieser Reihenfolge lesen:

1. **[architecture_diagram.md](architecture_diagram.md)** — Die 7-Sektion-Übersicht mit End-to-End-Datenfluss (Section 1), Bronze-Join-Pattern (Section 2), Employee-Bridge (Section 3), Cross-Channel-Logik (Section 4).
2. **[joins/join_strategy_contract.md](joins/join_strategy_contract.md)** — Single-Pager mit den wichtigsten Dos & Don'ts. **Lies das bevor du irgendeinen Join schreibst.**
3. **Die fünf Kern-Tabellen** (siehe unten) — hier liegen 95% der Daten, die dich interessieren.

---

## Die 5 Kern-Tabellen

Wenn du diese fünf Cards durchhast, kannst du die meisten Cross-Channel-Fragen selbständig beantworten:

| Tabelle | Rows | Rolle |
|---|---|---|
| **[imep_bronze.tbl_email](tables/imep/tbl_email.md)** | 145K | Mailing-Master mit `TrackingId` — der Einstieg für alle Email-Analysen |
| **[imep_bronze.tbl_email_receiver_status](tables/imep/tbl_email_receiver_status.md)** | 293M | Sends/Bounces pro Empfänger (full-key hub #1) |
| **[imep_bronze.tbl_analytics_link](tables/imep/tbl_analytics_link.md)** | 533M | Opens/Clicks pro Empfänger × Link (full-key hub #2) |
| **[imep_gold.final](tables/imep_gold/final.md)** | 520M | **Denormalisierter Consumption-Endpoint für Email** — HR bereits gejoint |
| **[sharepoint_gold.pbi_db_interactions_metrics](tables/sharepoint_gold/pbi_db_interactions_metrics.md)** | 84M | Master Interaction Fact für SharePoint (views/visits/duration) |

Plus die Brücke:

| Tabelle | Rows | Rolle |
|---|---|---|
| **[sharepoint_bronze.pages](tables/sharepoint/pages.md)** | 48K | Page-Inventory mit `UBSGICTrackingID` — **der einzige Ort**, wo Cross-Channel-Attribution stattfindet |

---

## Vollständige Tabellen-Übersicht

### iMEP Domain (Email + Events)

**Bronze** — `imep_bronze.*`
- [tbl_email](tables/imep/tbl_email.md) — Mailing-Master (145K)
- [tbl_email_receiver_status](tables/imep/tbl_email_receiver_status.md) — Sends/Bounces (293M)
- [tbl_analytics_link](tables/imep/tbl_analytics_link.md) — Opens/Clicks (533M)
- [tbl_email_links](tables/imep/tbl_email_links.md) — Template-URL-Inventory
- tbl_email_components — 3.3M *(card pending)*
- tbl_email_template_images — 1.7M *(card pending)*
- [tbl_event](tables/imep/tbl_event.md) — Events (100K)

**Silver** — `imep_silver.*` *(nur Events! Kein Email-Silver — siehe Q26)*
- invitation, eventregistration (13.7M), event (84K) *(cards pending)*

**Gold** — `imep_gold.*`
- [final](tables/imep_gold/final.md) — Denormalisierter Email-Endpoint (520M)
- tbl_pbi_platform_mailings — Mailing-Master mit Content-Metriken (73K) *(card pending)*
- tbl_pbi_mailings_region, tbl_pbi_mailings_division, tbl_pbi_kpi *(cards pending)*

### SharePoint Domain (Intranet)

**Bronze** — `sharepoint_bronze.*`
- [pages](tables/sharepoint/pages.md) — Page-Inventory mit TrackingID (48K, **⚠️ nur 4% Coverage**)
- pageviews — Raw PageViews (173M) *(card pending)*
- customevents — Raw Interactions (262M) *(card pending)*
- sites — Site-Metadaten (805) *(card pending)*

**Silver** — `sharepoint_silver.*`
- webpagevisited (262M), pageviewed (136M), pagevisited (105M) *(cards pending)*
- webpage, website *(dims, cards pending)*

**Gold** — `sharepoint_gold.*`
- [pbi_db_interactions_metrics](tables/sharepoint_gold/pbi_db_interactions_metrics.md) — Master Fact (84M)
- pbi_db_pageviewed_metric, pbi_db_pagevisited_metric, pbi_db_datewise_overview_fact_tbl *(cards pending)*
- pbi_db_employeecontact — ⚠️ potentielle Person-Bridge (24M) *(card pending)*

**Clicks Gold** — `sharepoint_clicks_gold.*`
- pbi_db_ctalabel_intr_fact_gold — CTA-Click-Fact (3M) *(card pending)*

**CPLAN** — `sharepoint_cplan.*`
- internalcommunicationactivities_bronze (4,349 Activities mit tracking_id) *(card pending)*
- communicationspacks_bronze (280 Packs), trackingcluster_bronze (17 Clusters) *(cards pending)*

### HR Domain — `imep_bronze.*` (lebt in iMEP, wird aber von allen genutzt)

- [tbl_hr_employee](tables/hr/tbl_hr_employee.md) — T_NUMBER + WORKER_ID (= GPN), 265K
- [tbl_hr_costcenter](tables/hr/tbl_hr_costcenter.md) — Region/Division/Area/Country
- [tbl_hr_user](tables/hr/tbl_hr_user.md) — UbsId (Uppercase-Variante)

### Page Metadata — `page_metadata_bronze.*` *(Q27-Fund)*
- pagelikesview (40K), comments (3.4K), reportedcomments, moderatedcomments *(cards pending)*

---

## Canonical Join Recipes

Hier liegen die **fertigen SQL-Kochrezepte** für die am häufigsten gebrauchten Join-Pattern:

| Recipe | Zweck |
|---|---|
| **[join_strategy_contract.md](joins/join_strategy_contract.md)** | ⭐ Lies das zuerst — Dos & Don'ts |
| [imep_bronze_email_events.md](joins/imep_bronze_email_events.md) | Die 4-Tabellen-Bronze-Kette: Mailing × Empfänger × Event |
| [sharepoint_gold_to_pages.md](joins/sharepoint_gold_to_pages.md) | Gold-Metriken → TrackingID via pageUUID |
| [hr_enrichment.md](joins/hr_enrichment.md) | TNumber ↔ GPN ↔ Region/Division |
| [cross_channel_via_tracking_id.md](joins/cross_channel_via_tracking_id.md) | iMEP ↔ SharePoint über TrackingId SEG1-4 |

---

## ER-Diagramme pro Domain

| Diagramm | Umfang |
|---|---|
| [er_imep_bronze.md](diagrams/er_imep_bronze.md) | Erweiterte Version von Section 2 — 4 iMEP-Bronze + 3 HR-Tabellen |
| [er_sharepoint_bronze.md](diagrams/er_sharepoint_bronze.md) | pages × pageviews × customevents × sites |
| [er_imep_gold.md](diagrams/er_imep_gold.md) | final + tbl_pbi_* Tier-Struktur |
| [er_sharepoint_gold.md](diagrams/er_sharepoint_gold.md) | marketingPageId-FK-Chain (alle Metric-Tables) |
| [er_cross_channel.md](diagrams/er_cross_channel.md) | TrackingID-Bridge + Employee-Bridge zwischen Domains |

---

## Kernbefunde, die jeder kennen muss

Diese fünf Fakten sind die **harten Constraints**, gegen die alle Cross-Channel-Analysen gebaut werden müssen:

### 1. Cross-Channel-Link ist dimensional, nicht factual

`tbl_email.TrackingId` ↔ `sharepoint_bronze.pages.UBSGICTrackingID` — **niemals** über Engagement-Tabellen (`pageviews`, `tbl_analytics_link`) direkt. TrackingId ist eine Dimension, kein Fact-Key. *(Q27)*

### 2. Nur ~4% der Pages haben UBSGICTrackingID

1,949 von 48,419 Pages. Das heisst: ~96% der SharePoint-Interaktionen sind **nicht** einem Pack zuordenbar. Dashboard muss das explizit machen. *(Q22)*

### 3. TrackingId-Adoption rampt erst seit 2024/25

Nur 986/73,930 Mailings (1.3%) haben TrackingId. Default-Dashboard-Zeitraum: **ab 2025**. *(Q24)*

### 4. Refresh-Cadence ist 2×/Tag iMEP, 1×/Tag SharePoint

- iMEP Bronze MERGE: 00:00 + 12:00 UTC
- SharePoint Bronze: 02:00 UTC (pages MERGE, pageviews Append-WRITE)
- iMEP Gold CTAS: 00:23 + 12:25 UTC — **während dieser Fenster keine Dashboard-Queries** *(Q28)*

### 5. Email skipped Silver komplett

`imep_silver` existiert nur für Events. Für Email-Engagement gibt es **keine Silver-Schicht** — `imep_gold.final` ist direkter Consumption-Endpoint (denormalisiert, HR-enriched). *(Q26)*

---

## Genie-Fragen und Findings

Alle Erkenntnisse stammen aus strukturierten Genie-Sessions (Q1–Q30). Vollständiger Verlauf:

- **[genie_questions_imep.md](genie_questions_imep.md)** — Alle Prompts mit Antworten (Q1–Q30)
- **[BRD_cross_channel_analytics.md](BRD_cross_channel_analytics.md)** — Business-Requirements-Dokument

---

## Nicht in diesem KB (bewusst)

- **Implementation-SQL für unser eigenes Modell** — lebt in `/cross_channel_mvp.sql` und `/tracking_coverage_analysis.sql`
- **Dashboard-Code** — lebt in `/dashboard/`
- **Personal Data Handling / PII** — separater Compliance-Prozess (siehe `memory/pii_cleanup_pending.md`)
