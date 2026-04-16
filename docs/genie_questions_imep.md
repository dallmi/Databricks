# Genie-Fragen zur Klärung offener iMEP-Punkte

Gezielte Prompts für Databricks Genie, um offene technische Punkte aus dem [BRD](BRD_cross_channel_analytics.md) §9 zu schliessen, **bevor** wir `silver.fact_email` und die Cross-Channel-Joins implementieren.

Konvention:
- **OP-XX** → Referenz auf Open Point im BRD.
- Prompts sind in **Englisch** formuliert (Genie liefert zuverlässigere Ergebnisse gegen englische Tabellen­namen).
- Jede Frage kommt mit erwartetem Output und möglichem Follow-up.

---

## A — Schema & Tracking-ID-Lokation (höchste Priorität)

### Q1 — Wo lebt `CammsTrackingID` in iMEP? **(OP-04, kritisch)**

> *Show me all columns in `TBL_EMAIL`, `TBL_EMAIL_LINKS`, `TBL_ANALYTICS_LINK` and `TBL_EMAIL_RECEIVER_STATUS` whose name contains "tracking", "camms", "cpid" or "cplan" (case-insensitive). Include the data type and a non-null sample value for each column.*

→ Erwartet: klarer Treffer in **einer** der vier Tabellen. Ohne diese Antwort kann keine Join-Logik gebaut werden.

### Q2 — Vollständiges Schema der Kern-Tabellen **(OP-01)**

> *List every column with data type and nullability for these four tables: `TBL_EMAIL`, `TBL_EMAIL_RECEIVER_STATUS`, `TBL_ANALYTICS_LINK`, `TBL_EMAIL_LINKS`. For each column, also show one non-null sample value.*

→ Bestätigt unser Annahme-Set und deckt unbekannte Felder auf (z.B. BounceReason, Unsubscribe-Flag).

### Q3 — HR-Bridge zwischen TNumber und GPN **(OP-07d, blocker für Empfänger-Level-Join)**

> *Inspect `TBL_HR_EMPLOYEE` and `TBL_HR_USER`: list all columns that look like employee identifiers (names containing `t_number`, `tnumber`, `gpn`, `ubs`, `personnel`, `staff`, `emp_id`). For each, show data type, sample value, and whether nulls exist. Also check whether any single table contains BOTH a T-number column and a GPN column.*

→ Findet die Tabelle, die TNumber ↔ GPN auflöst (Voraussetzung für Empfänger-Level Cross-Channel-Aggregation).

---

## B — Status- und Event-Werte

### Q4 — Welche Status-Werte existieren? **(OP-02)**

> *For `TBL_EMAIL_RECEIVER_STATUS`: return the distinct values of every status-like or enum-like column (e.g. `Status`, `DeliveryStatus`, `BounceReason`), with row counts per value. Do the same for `TBL_ANALYTICS_LINK.linkTypeenum` and `TBL_ANALYTICS_LINK.Agent`.*

→ Liefert Funnel-Event-Mapping (sent/bounced/opened/clicked/unsubscribed).

### Q5 — Open vs. Click Dedup-Check **(OP-03)**

> *In `TBL_ANALYTICS_LINK`, for a sample of 10 recent mailings (top 10 `EmailId` by row count), compute:
> - total rows
> - distinct `(EmailId, TNumber)` pairs where `linkTypeenum = 'OPEN'`
> - distinct `(EmailId, TNumber)` pairs where `linkTypeenum != 'OPEN'`
> - max, min, avg number of rows per `(EmailId, TNumber, linkTypeenum)` triple.*

→ Bestätigt, dass unique-open/unique-click via `(EmailId, TNumber)`-Dedup korrekt ist und zeigt Multi-Event-Verhalten.

---

## C — Datenumfang & Qualität

### Q6 — Zeitliche Abdeckung **(OP-06)**

> *For each of `TBL_EMAIL`, `TBL_EMAIL_RECEIVER_STATUS`, `TBL_ANALYTICS_LINK`: return `MIN(CreationDate)`, `MAX(CreationDate)`, total row count, and row count per calendar year.*

→ Retention / Historisierungs-Grenzen.

### Q7 — Audience-Size pro Mailing **(OP-05)**

> *For the 20 most recent mailings (`TBL_EMAIL` by `CreationDate` desc), show:
> - `Id`, `Title`, `CreationDate`, `CreatedBy`
> - number of recipients in `TBL_EMAIL_RECEIVER_STATUS` (= audience size)
> - number of distinct `TNumber` values that had `linkTypeenum = 'OPEN'` (= unique opens)
> - number of distinct `TNumber` values that had any click event (= unique clicks).*

→ Bestätigt, dass Audience-Size direkt aus iMEP ableitbar ist (kein CPLAN-Feld nötig).

### Q8 — Transactional-Mails ohne Campaign-Tracking **(OP-08)**

> *Using the column identified in Q1 as the CammsTrackingID location, count how many rows in the relevant table have NULL / empty / malformed CammsTrackingID values. Sample 10 such rows and show what type of email they represent (show `Title` / `CreatedBy` / `CreationDate`).*

→ Filter-Regel für den Silver-Build (müssen transactional Mails ausgeschlossen werden?).

### Q9 — Tracking-ID Format-Konformität **(OP-09, OP-10)**

> *Using the column identified in Q1, return:
> - count of values matching the regex `^[A-Z0-9]{5}-[0-9]{7}-[0-9]{6}-[0-9]{7}-[A-Z]{3}$`
> - count of values NOT matching (non-conforming)
> - sample 10 non-conforming values
> - are any values stored in different cases (mix of upper/lower)?*

→ Validierung 32-Char-5-Segment-Format und Case-Sensitivity.

---

## D — Org & HR

### Q10 — Org-Unit-Mapping Eindeutigkeit **(OP-07b)**

> *In `TBL_HR_COSTCENTER`: group by `ORGANIZATIONAL_UNIT` and count rows. Flag any `ORGANIZATIONAL_UNIT` with more than 1 row. For those, show the conflicting values in the business-division / area / region / country columns.*

→ Prüft, ob der Join `hr.ORGANIZATIONAL_UNIT = cc.ORGANIZATIONAL_UNIT` im Genie-Pattern 2 wirklich 1:1 ist.

### Q11 — HR-Historisierung in iMEP **(OP-07b)**

> *Does `TBL_HR_EMPLOYEE` have a validity window (columns like `ValidFrom`, `ValidTo`, `EffectiveDate`)? If yes, show for a sample of 5 employees how their `ORGANIZATIONAL_UNIT` has changed over time. If no, is there only a current snapshot?*

→ Entscheidet, ob wir temporal joinen (wie bei PageView) oder Current-Snapshot reicht.

### Q12 — Creator-Dimension **(OP-07c)**

> *For all distinct `CreatedBy` values in `TBL_EMAIL` from the last 12 months, resolve via `TBL_HR_EMPLOYEE.T_NUMBER` to a full name and show the top 20 creators by number of mailings.*

→ Qualifiziert den "Created By"-Filter im Dashboard als sinnvolle Dimension.

---

## E — Multi-Device & Engagement

### Q13 — Multi-Device-Volumen **(Design-Review Pattern 2)**

> *Out of all recipients in `TBL_ANALYTICS_LINK` (filter `IsActive = 1`, `linkTypeenum != 'OPEN'`, `Agent IN ('desktop','mobile')`), what share has engaged via BOTH desktop AND mobile for the same `EmailId`? Break down by mailing for the 20 largest mailings.*

→ Zeigt, ob die Multi-Device-CTE für unser Dashboard wirklich relevant ist (Häufigkeit in Praxis).

### Q14 — Engagement-Template-Coverage **(Design-Review Pattern 3)**

> *For the 10 most recent mailings: what share of `TBL_EMAIL_LINKS` entries received at least one click at runtime (via `TBL_ANALYTICS_LINK`)? Show per mailing: total template links, clicked links, unclicked links, click rate.*

→ Rechtfertigt den Template-UNION-Step (Pattern 3): lohnt sich der Aufwand nur wenn "Dead-Links" überhaupt relevant sind.

---

## F — Integration mit PageView (AppInsights)

### Q15 — CammsTrackingID-Coverage in PageView **(OP-15)**

> *In our `fact_page_view` table: what percentage of rows have a non-null `tracking_pack_id`? Show the distribution by month for the last 12 months. Also: what's the percentage of rows where `tracking_channel_abbr = 'INT'` specifically?*

> *Hinweis: falls `fact_page_view` noch nicht als Tabelle existiert, stattdessen gegen `pbi_db_website_page_view` und die `customDimensions`-JSON-Spalte direkt abfragen.*

→ Quantifiziert Attribution-Gap (Orphan-Handling-Dringlichkeit).

### Q16 — Attribution-Lag zwischen Email und PageView **(OP-16)**

> *For the 20 largest recent mailings, compute the time between Email-Send (`TBL_EMAIL_RECEIVER_STATUS.CreationDate`) and the earliest matching PageView for the same `CammsTrackingID`. Return: p50, p90, p99 in hours. Only consider PageViews within the first 30 days after send.*

→ Liefert Default-Attribution-Window (wahrscheinlich 7 oder 14 Tage).

---

## G — Banner & Event (Phase 2 Preview)

### Q17 — Event-Channel (`EVT`) Schema **(Phase 2)**

> *List the iMEP bronze tables that relate to event registrations (names containing "event", "registration", "attendee"). Show full schema plus one sample row per table. Does the same CammsTrackingID mechanism apply?*

### Q18 — Banner-Telemetrie (`BAN`) **(Phase 3)**

> *Does AppInsights `pageViews` contain any rows where `customDimensions.CustomProps.CammsTrackingID` ends with `-BAN`? Show count per month for the last 6 months and sample 5 rows.*

---

## H — Sanity-Check (am Schluss)

### Q19 — End-to-End Join Test für 1 Pack

> *Pick the `tracking_pack_id` with the highest recipient count in the last 90 days. For that pack, produce one row with:
> - pack_id, cluster_id, publish_date
> - emails sent, opened, clicked (from iMEP)
> - page views, unique readers, avg time-on-page (from AppInsights / fact_page_view)
> - creator full name (from HR).*

→ Proof-of-Concept für die Gold-Cross-Channel-Query, gegen echte Daten.

### Q20 — Row-Count-Reconciliation

> *For the last 30 days, compare per `tracking_pack_id`:
> - sum of sends in `TBL_EMAIL_RECEIVER_STATUS`
> - sum of clicks in `TBL_ANALYTICS_LINK`
> - sum of page views with matching `tracking_pack_id`.
> Flag any pack where page views exceed clicks by more than 2x, or where sends exist but no telemetry.*

→ Data-Quality-Gate vor Dashboard-Wiring.

---

## Nutzungshinweise

1. **Reihenfolge**: Q1 und Q3 zuerst — sie entscheiden, ob das Modell überhaupt baubar ist. Q19/Q20 am Schluss als Validierung.
2. **Genie-Session**: Fragen in einer Session stellen — Genie merkt sich Kontext (Tabellen, Filter) und verkettet sauber.
3. **Output sichern**: Jede Antwort als Notebook-Cell oder Screenshot sichern, dann im BRD §9 als "✅ gelöst" markieren.
4. **Fragen-Follow-up**: Wenn Genie nicht liefert (z.B. "column not found"), direkt fragen: *"Which tables in this catalog contain a column named like 'tracking_id' or 'camms_tracking_id'?"* — Genie kann das Information-Schema durchsuchen.
