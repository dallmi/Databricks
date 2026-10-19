# iMEP Bronze Data Model and the Three Canonical Join Patterns

> Source: Genie Code notebook reviewed on 2026-04-16, before Genie question Q1. Status: confirmed for the patterns as transcribed from the notebook; the link between Pattern 2 and `imep_gold.final` is a hypothesis.

## Question

How do the iMEP Bronze tables fit together, and which join patterns does the existing Genie Code notebook use to produce KPI summaries, the denormalised fact table and link-level engagement detail?

## Findings

### Tables

| Table | Content | Grain |
|---|---|---|
| `TBL_EMAIL_RECEIVER_STATUS` | Recipient status per send | One row per recipient per mailing |
| `TBL_EMAIL` | Mailing master (title, overall status, creator) | One row per mailing |
| `TBL_ANALYTICS_LINK` | Runtime open and click events | One row per link interaction |
| `TBL_EMAIL_LINKS` | Links defined in e-mail templates (design time) | One row per template link |
| `TBL_EMAIL_COMPONENTS` | Top-level template components (name, order) | One row per component |
| `TBL_EMAIL_COMPONENT_ELEMENTS` | Elements inside a component (`KeyName`, `Name`) | One row per element |
| `TBL_ELEMENT_VALUES` | Link between a template link and its element | One row per element value |
| `TBL_HR_EMPLOYEE` | HR master data, keyed by `T_NUMBER` | One row per employee |
| `TBL_HR_COSTCENTER` | Org unit → division, area, region, country | One row per org unit |
| `TBL_HR_USER` | HR user record with town | One row per employee |

Cards for the main tables: see Related. The three template tables (`TBL_EMAIL_COMPONENTS`, `TBL_EMAIL_COMPONENT_ELEMENTS`, `TBL_ELEMENT_VALUES`) have no card yet.

### Keys and relationships

- `TBL_EMAIL_RECEIVER_STATUS.Id` (primary key) ← `TBL_ANALYTICS_LINK.EmailReceiverStatusId`
- `TBL_EMAIL_RECEIVER_STATUS.EmailId` → `TBL_EMAIL.Id`
- `TBL_EMAIL.CreatedBy` → `TBL_HR_EMPLOYEE.T_NUMBER` (creator)
- `TBL_ANALYTICS_LINK.TNumber` → `TBL_HR_EMPLOYEE.T_NUMBER` (recipient)
- `TBL_ANALYTICS_LINK.LinkTypeEnum`: `OPEN` versus click types — open versus click is a filter on this column, not a separate status field
- `TBL_ANALYTICS_LINK.Agent` ∈ {`desktop`, `mobile`} — device type
- `TBL_ANALYTICS_LINK.CurrentLanguage` — language per recipient interaction
- `IsActive = 1` is a mandatory filter on every table

### Pattern 1 — KPI summary

Recipient counts per mailing by delivery status, language and creator.

```sql
SELECT ...
FROM      TBL_EMAIL_RECEIVER_STATUS a
LEFT JOIN TBL_ANALYTICS_LINK c  ON a.Id        = c.EmailReceiverStatusId  -- CurrentLanguage
LEFT JOIN TBL_EMAIL          b  ON a.EmailId   = b.Id                     -- title, status
LEFT JOIN TBL_HR_EMPLOYEE    hr ON b.CreatedBy = hr.T_NUMBER              -- creator name
WHERE a.IsActive = 1
```

### Pattern 2 — denormalised fact table ("Final")

The central fact with geography, business organisation, device type and time-to-engagement bucket, including multi-device detection.

```sql
WITH mdclicks AS (
  SELECT Id, Agent, EmailId, TNumber AS RecipientID,
         'Desktop & Mobile' AS NewDeviceType,
         CASE WHEN LinkTypeEnum = 'OPEN' THEN 'Unique Opens'
              ELSE 'Unique Clicks' END AS Engagementtype
  FROM   TBL_ANALYTICS_LINK
  WHERE  IsActive = 1 AND Agent IN ('desktop', 'mobile') AND LinkTypeEnum != 'OPEN'
),
mdlinks AS (
  -- recipients who clicked from both desktop and mobile
  SELECT Id, RecipientID, EmailId, NewDeviceType, Engagementtype
  FROM   mdclicks
  WHERE  RecipientID IN (
           SELECT RecipientID FROM mdclicks
           GROUP BY EmailId, RecipientID
           HAVING COUNT(DISTINCT Agent) > 1)
)
SELECT ...
FROM      TBL_ANALYTICS_LINK a
LEFT JOIN TBL_EMAIL_RECEIVER_STATUS c  ON a.EmailReceiverStatusId = c.Id                    -- send time
LEFT JOIN TBL_HR_EMPLOYEE          hr ON a.TNumber = hr.T_NUMBER                           -- org unit
LEFT JOIN TBL_HR_COSTCENTER        cc ON hr.ORGANIZATIONAL_UNIT = cc.ORGANIZATIONAL_UNIT   -- division, area, region, country
LEFT JOIN TBL_HR_USER              u  ON hr.T_NUMBER = u.__ORG__Id                         -- town
LEFT JOIN mdlinks                  hh ON a.Id = hh.Id                                      -- multi-device label
WHERE c.EmailId IS NOT NULL AND a.EmailId IS NOT NULL AND a.IsActive = 1
```

- **Time-to-engagement bucket:** `(bigint(a.CreationDate) - bigint(c.CreationDate)) / 3600` — hours between send and interaction.
- **Final device type:** `COALESCE` of the multi-device label and the raw `Agent`; single-device rows get the suffix " Only" (for example `Desktop Only`).
- **HR join case:** `T_NUMBER` is lower case and `__ORG__Id` upper case; the transcribed join compares them directly. Normalise case when reproducing it ([hr-employee-identifiers.md](hr-employee-identifiers.md)).

Caveats in the logic as transcribed:

- Multi-device detection covers **clicks only** — opens are excluded in `mdclicks`, so the `CASE` always yields `Unique Clicks`.
- The `mdlinks` subquery filters on `RecipientID` alone, not on the pair (`EmailId`, `RecipientID`). A recipient who used both devices for one mailing would then be labelled "Desktop & Mobile" for clicks in every mailing. Whether Gold (`final`, `tbl_pbi_deviceTypeall`) inherits this is unverified.
- With the region join failing for most recipients ([q16-imep-gold-inventory.md](q16-imep-gold-inventory.md)), the `TBL_HR_COSTCENTER` join is where the NULL-region gap originates.

### Pattern 3 — engagement detail with template structure

Click data (branch 1, from the fact) is combined via `UNION` with the template structure (branch 2), so that links with zero clicks still appear in engagement reporting.

```sql
-- Branch 2: template metadata, UNIONed with branch 1 (the fact)
SELECT x.Url,
       b.EmailId  AS MailingId,
       x.LinkLabel,
       b.KeyName  AS SourceComponent,
       b.Name     AS SourceComponentName,
       a.KeyName  AS SourceElement,
       a.Name     AS SourceElementName,
       b.Order    AS ComponentOrder
FROM      TBL_EMAIL_LINKS              x
LEFT JOIN TBL_ELEMENT_VALUES           y ON y.Id = x.ElementValueId
LEFT JOIN TBL_EMAIL_COMPONENT_ELEMENTS a ON a.Id = y.ElementId
LEFT JOIN TBL_EMAIL_COMPONENTS         b ON b.Id = a.ComponentId
```

This explains the many zero counts in `imep_gold.tbl_pbi_engagement` ([q21-imep-gold-tier3-schemas.md](q21-imep-gold-tier3-schemas.md)).

## Consequences

1. **Pattern 2 is very likely the build logic of `imep_gold.final`** — same name, same three Bronze sources, same late HR enrichment ([q26-imep-silver.md](q26-imep-silver.md)). Not confirmed against the platform team's Gold notebook. The original plan to build our own `silver.fact_email` on Pattern 2 is dropped; consume `final` or the Gold KPI tables instead.
2. **Status comes from two places:** delivery status from `TBL_EMAIL_RECEIVER_STATUS`, open versus click from `TBL_ANALYTICS_LINK.LinkTypeEnum`.
3. **Recipient identity is the T-number** (`t100200`), not the e-mail address. The T-number is not the GPN (`00100200`); the bridge is `tbl_hr_employee.WORKER_ID` ([hr-employee-identifiers.md](hr-employee-identifiers.md)).
4. **Unique opens and clicks** are de-duplicated on `RecipientID` + `EmailId`, never on raw event counts.
5. **Multi-device logic is non-trivial**; if it is ever reproduced, fix the subquery to match on (`EmailId`, `RecipientID`).
6. The tracking-id location, open in this notebook, was answered by Q1: `tbl_email.TrackingId` ([q01-q03-imep-schemas-and-tracking-id.md](q01-q03-imep-schemas-and-tracking-id.md)).

## Related

- [q01-q03-imep-schemas-and-tracking-id.md](q01-q03-imep-schemas-and-tracking-id.md) · [q16-imep-gold-inventory.md](q16-imep-gold-inventory.md) · [q21-imep-gold-tier3-schemas.md](q21-imep-gold-tier3-schemas.md) · [q26-imep-silver.md](q26-imep-silver.md) · [q27-imep-join-graph.md](q27-imep-join-graph.md) · [hr-employee-identifiers.md](hr-employee-identifiers.md)
- [../tables/imep/tbl_email.md](../tables/imep/tbl_email.md) · [../tables/imep/tbl_email_receiver_status.md](../tables/imep/tbl_email_receiver_status.md) · [../tables/imep/tbl_analytics_link.md](../tables/imep/tbl_analytics_link.md) · [../tables/imep/tbl_email_links.md](../tables/imep/tbl_email_links.md) · [../tables/hr/tbl_hr_employee.md](../tables/hr/tbl_hr_employee.md) · [../tables/hr/tbl_hr_costcenter.md](../tables/hr/tbl_hr_costcenter.md) · [../tables/hr/tbl_hr_user.md](../tables/hr/tbl_hr_user.md)
- [../joins/imep_bronze_email_events.md](../joins/imep_bronze_email_events.md) · [../joins/hr_enrichment.md](../joins/hr_enrichment.md) · [../diagrams/er_imep_bronze.md](../diagrams/er_imep_bronze.md)
- [../BRD_cross_channel_analytics.md](../BRD_cross_channel_analytics.md) · [../genie_questions_imep.md](../genie_questions_imep.md)
