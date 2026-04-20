# Join Recipe — HR Enrichment (TNumber ↔ GPN ↔ Region/Division)

> Die HR-Bridge. Für **drei Szenarien**: (1) TNumber aus iMEP-Engagement anreichern, (2) GPN aus SharePoint zu TNumber übersetzen, (3) Case-Normalisierung zwischen `T_NUMBER` (lowercase) und `UbsId` (UPPERCASE). Alle drei auf `imep_bronze.tbl_hr_employee` basiert — keine externen Quellen nötig.

---

## Die drei Identifier-Formate

| Name | Ort | Format | Beispiel |
|---|---|---|---|
| `TNumber` | iMEP Engagement | lowercase `t######` | `t100200` |
| `T_NUMBER` | HR | lowercase `t######` | `t100200` |
| `WORKER_ID` | HR | 8-digit numeric (= GPN) | `00100200` |
| `user_gpn` | SharePoint `pageviews` | 8-digit numeric | `00100200` |
| `UbsId` | `tbl_hr_user` | UPPERCASE `T######` | `T100200` |
| `viewingcontactid` | `sharepoint_gold.*` | GUID | `1254e21a-...` |

**Kernfakt**: `tbl_hr_employee` ist die einzige Tabelle, die **gleichzeitig** `T_NUMBER` und `WORKER_ID` führt → ein einfacher JOIN statt externer Bridge-Quelle (Q3b).

---

## Szenario 1: iMEP-Engagement → HR-Region/Division

```sql
SELECT
    al.TNumber,
    al.LinkTypeEnum,
    al.CreationDate,
    hr.WORKER_ID           AS gpn,
    cc.REGION,
    cc.DIVISION,
    cc.AREA,
    cc.COUNTRY
FROM   imep_bronze.tbl_analytics_link  al
LEFT JOIN imep_bronze.tbl_hr_employee   hr ON hr.T_NUMBER            = al.TNumber
LEFT JOIN imep_bronze.tbl_hr_costcenter cc ON cc.ORGANIZATIONAL_UNIT = hr.ORGANIZATIONAL_UNIT
WHERE  al.IsActive = 1
```

**Wichtig**: `LEFT JOIN` — sonst verlierst du Events von Ex-Mitarbeitern oder externen Empfängern, deren TNumber nicht (mehr) in HR ist.

---

## Szenario 2: SharePoint-GPN → iMEP-TNumber

```sql
SELECT
    pv.user_gpn,
    hr.T_NUMBER            AS t_number,
    pv.ViewTime,
    pv.pageId
FROM   sharepoint_bronze.pageviews  pv
LEFT JOIN imep_bronze.tbl_hr_employee hr ON hr.WORKER_ID = pv.user_gpn
```

**Die Bridge**: `WORKER_ID = user_gpn`. Beide sind 8-digit numeric.

**Anti-Pattern — NICHT machen**:
```sql
-- FALSCH: versucht GPN-String durch Prefix-Manipulation zu mappen
ON  CONCAT('t', LPAD(pv.user_gpn, 6, '0')) = hr.T_NUMBER   -- funktioniert NICHT
```

Q3a hat bestätigt: Die string-basierte Transformation `00100200 → t100200` funktioniert zwar **beispielweise**, aber nicht systematisch. Ausreichend GPNs passen **nicht** zu ihrer T-Number nach dieser Regel. Immer `WORKER_ID` benutzen.

---

## Szenario 3: `UbsId` (UPPERCASE) mit `T_NUMBER` (lowercase) matchen

```sql
SELECT
    hr.T_NUMBER,
    u.UbsId,
    u.Town
FROM   imep_bronze.tbl_hr_employee hr
JOIN   imep_bronze.tbl_hr_user      u ON LOWER(u.UbsId) = LOWER(hr.T_NUMBER)
```

**Case-Norm ist Pflicht** — ohne `LOWER()` ergibt sich 0 Treffer, weil `T_NUMBER` = `t100200` und `UbsId` = `T100200`.

`tbl_hr_user` bietet zusätzlich: `Town`, weitere Office-Metadaten. Nur joinen, wenn diese Felder gebraucht werden — sonst reicht `tbl_hr_employee` + `tbl_hr_costcenter`.

---

## Szenario 4: Full Cross-Channel Employee Enrichment

Wenn du in einer Query alle drei Welten brauchst — iMEP, SharePoint, HR:

```sql
WITH sp_views AS (
  SELECT user_gpn, COUNT(*) AS view_count
  FROM   sharepoint_bronze.pageviews
  WHERE  ViewTime >= '2025-01-01'
  GROUP BY user_gpn
),
imep_opens AS (
  SELECT TNumber, COUNT(*) AS open_count
  FROM   imep_bronze.tbl_analytics_link
  WHERE  LinkTypeEnum = 'OPEN'
    AND  IsActive = 1
    AND  CreationDate >= '2025-01-01'
  GROUP BY TNumber
)
SELECT
    hr.T_NUMBER,
    hr.WORKER_ID                       AS gpn,
    cc.REGION, cc.DIVISION,
    COALESCE(sp.view_count, 0)         AS sp_views,
    COALESCE(io.open_count, 0)         AS imep_opens
FROM   imep_bronze.tbl_hr_employee   hr
LEFT JOIN imep_bronze.tbl_hr_costcenter cc ON cc.ORGANIZATIONAL_UNIT = hr.ORGANIZATIONAL_UNIT
LEFT JOIN sp_views                     sp ON sp.user_gpn = hr.WORKER_ID
LEFT JOIN imep_opens                   io ON io.TNumber  = hr.T_NUMBER
WHERE  COALESCE(sp.view_count, 0) + COALESCE(io.open_count, 0) > 0
```

---

## Gotchas

### 1. `T_NUMBER` vs. `TNumber` vs. `UbsId` — drei Varianten desselben Keys

| Wo | Column-Name | Case |
|---|---|---|
| `tbl_hr_employee` | `T_NUMBER` | lowercase |
| `tbl_email_receiver_status`, `tbl_analytics_link` | `TNumber` | lowercase |
| `tbl_hr_user` | `UbsId` | UPPERCASE |

Join iMEP-Engagement ↔ HR = direct. Join HR ↔ `tbl_hr_user` = braucht `LOWER()`.

### 2. GPN kommt **nur** aus `tbl_hr_employee.WORKER_ID`

GPN existiert **nicht** auf `tbl_hr_employee` als separate Spalte mit dem Namen "GPN". Die 8-digit ID heisst `WORKER_ID`. Q3b-Finding: die zuerst weitestgehend durchsuchten HR-Spalten hatten sie zwar gelistet ("Primary worker identifier") — aber nicht als "= GPN" kenntlich gemacht.

### 3. Es gibt mehr HR-Identifier — aber nur diese sind für uns relevant

`tbl_hr_employee` führt auch: `ABACUS_ID`, `ALTERNATE_WORKER_ID`, `WEBSSO`, `UUNAME`, `PersonalNumber`. Für Cross-Channel-Analytics genügt `T_NUMBER` + `WORKER_ID`. Die anderen IDs sind für andere Use-Cases (WebSSO für Auth, Abacus für Finance-Joins).

### 4. HR wird 2×/Tag upserted — Snapshot, nicht Historie

`tbl_hr_employee` und `tbl_hr_costcenter` repräsentieren den **aktuellen** HR-Stand. Regional-Analyse "Person X war im Juli in EMEA, im August in APAC" funktioniert **nicht** out-of-the-box — dafür braucht's Snapshot-Tables (falls existent, noch zu klären).

### 5. LEFT JOIN, nie INNER JOIN

Nicht alle TNumbers in iMEP-Engagement sind in HR (Ex-Mitarbeiter, externe Adressen). `INNER JOIN` verliert diese.

### 6. `viewingcontactid` auf SP-Gold ist **kein** TNumber

Wenn du in `sharepoint_gold.pbi_db_interactions_metrics` arbeitest, nutze **nicht** `viewingcontactid` für HR-Joins. Das ist ein GUID, kein GPN. Bridge-Kandidaten:
- `sharepoint_gold.pbi_db_employeecontact` (24M Rows, potentielle Bridge — nicht validiert)
- oder zurück zu `sharepoint_bronze.pageviews.user_gpn`

---

## Referenzen

- HR-Cards: `tbl_hr_employee.md`, `tbl_hr_costcenter.md`, `tbl_hr_user.md` *(pending)*
- Employee-Identifier-Übersicht: `memory/employee_identifiers.md`
- Bridge-Resolution: `memory/hr_gpn_tnumber_bridge_resolved.md`
- ER-Diagramm Section 3: [architecture_diagram.md](../architecture_diagram.md)
