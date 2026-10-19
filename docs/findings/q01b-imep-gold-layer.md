# iMEP Gold Mailing and Event Masters — and a Corrected Conclusion

> Source: Genie question Q1b (2026-04-16), corrected on 2026-04-17 by business-owner input and Genie question Q16. Status: confirmed for the two tables described here; the original conclusion "iMEP Gold has no engagement aggregates" is withdrawn.

## Question

What do `imep_gold.tbl_pbi_platform_mailings` and `imep_gold.tbl_pbi_platform_events` contain — schema, source tables, refresh — and can they replace a self-built email fact table? (BRD open point OP-07f)

## Findings

### `tbl_pbi_platform_mailings` — mailing master with content metrics

- **Grain:** one row per mailing (`tbl_email.Id`). Tens of thousands of rows.
- **History:** creation dates from late 2020 up to the query date; actively refreshed.

| Column group | Columns | Origin |
|---|---|---|
| Core metadata | `Id` (PK), `CreationDate`, `CreatedBy`, `IsActive`, `Title`, `Description`, `FolderId`, `Template`, `TemplateName` (resolved), `SourceTemplateId`, `Subject`, `Status`, `EmailSendingStatus`, `IsScheduled`, `TimeZone`, `SendLanguageCopies`, `TrackingId` (suffix `EMI`) | `tbl_email`, `tbl_templates` |
| Event link | `EventId` → `tbl_event` | `tbl_email` |
| Send timing | `StartsentTime` = earliest send timestamp per mailing | `tbl_email_receiver_status` |
| Content complexity | `elementcounts`, `uniqueelements`, `uniquecomponents` | `tbl_email_components`, `tbl_email_component_elements` |
| Languages | `CountLanguages` | `tbl_email_languages` |

It has **no** engagement columns: no recipient, send, open, click or bounce counts.

### `tbl_pbi_platform_events` — event master with registrations

- **Grain:** one row per event (`tbl_event.Id`). Tens of thousands of rows.
- **History:** creation dates from 2013 up to the query date; actively refreshed.

| Column group | Columns |
|---|---|
| Audit and lifecycle | `Id`, `CreationDate`, `CreatedBy`, `ModifyDate`, `ModifiedBy`, `IsActive`, `IsLegacy`, `TrackingId` (suffix `EVT`) |
| Core metadata | `Title`, `FolderId`, `AdministratorId`, `Location`, `Timezone`, `Mailbox`, `MessageId` |
| Scheduling | `StartDate`, `EndDate`, `EventFrom`, `EventTo` — open-ended events use the sentinel year **2124** |
| Registration settings | `AvailableSeats`, `IsLimitedSeats`, `IsOpen`, `IsMultiRegistrations`, `RequiresAuthentication`, `DefaultLanguage`, `DefaultRegistrationStatus`, `WhoCanRegisterId`, `WhoCanReg` (resolved label) |
| E-mail settings | `AttachCalendarInEmail`, `SalutationInEmail`, `HasEmailSentToAdmin`, `HasEmailSentToUser`, `HasCancellationLink` |
| Aggregate | `registration` (bigint) — total registrations per event |

Sources: `tbl_event`, `tbl_event_registration` (registration count), `tbl_query` (`WhoCanReg` label).

### Correction (2026-04-17): Gold does hold engagement aggregates

The first reading of Q1b was "iMEP Gold is a master with content metrics, not a send/open/click aggregate — build the email fact from Bronze". That was wrong, because only these two tables had been inspected.

The business owner pointed out that the existing Power BI semantic model shows opens, clicks and views per mailing. Those numbers cannot be computed live from Bronze at reporting time, so pre-built aggregates had to exist in Gold under other names. Q16 confirmed this the same day: `imep_gold.final` is the atomic engagement fact, and `tbl_pbi_mailingreciever_region`, `tbl_pbi_mailingreciever_division`, `tbl_pbi_engagement` and `tbl_pbi_kpi` carry unique opens and clicks per mailing. See [q16-imep-gold-inventory.md](q16-imep-gold-inventory.md).

Lesson kept as a working rule: if Genie finds no Gold table for a metric that a report already shows, the search was too narrow. The Power BI semantic model is the authoritative list of Gold tables actually consumed — see [../semantic-models/imep-mailings.md](../semantic-models/imep-mailings.md).

## Consequences

1. **Pack dimension from Gold.** `tbl_pbi_platform_mailings` and `tbl_pbi_platform_events` together provide the pack metadata (tracking id, title, subject, creator, creation date, first send time, content metrics) without Bronze joins and without depending on CPLAN for the mailing side:

   ```sql
   SELECT
       array_join(slice(split(UPPER(m.TrackingId), '-'), 1, 2), '-') AS tracking_pack_id,
       m.TrackingId     AS tracking_id,
       'EMI'            AS source_channel,
       m.Id             AS source_id,
       m.Title          AS title,
       m.Subject        AS subject,
       m.CreatedBy      AS created_by,
       m.CreationDate   AS created_at,
       m.StartsentTime  AS first_activity_at
   FROM   imep_gold.tbl_pbi_platform_mailings m
   WHERE  m.IsActive = TRUE
     AND  m.TrackingId IS NOT NULL

   UNION ALL

   SELECT
       array_join(slice(split(UPPER(e.TrackingId), '-'), 1, 2), '-'),
       e.TrackingId,
       'EVT',
       e.Id,
       e.Title,
       NULL,
       e.CreatedBy,
       e.CreationDate,
       e.StartDate
   FROM   imep_gold.tbl_pbi_platform_events e
   WHERE  e.IsActive = TRUE
     AND  e.IsLegacy = FALSE
     AND  e.EventFrom < '2100-01-01'      -- drops open-ended events (sentinel year 2124)
     AND  e.TrackingId IS NOT NULL
   ```

   `CreatedBy` holds a T-number on the mailing side; resolve it through HR only where a creator dimension is really needed (BRD OP-07c).

2. **Events can move forward.** `registration` gives the registered-attendee metric of the events phase immediately. Event-level registrations also exist in `imep_silver.eventregistration` ([q26-imep-silver.md](q26-imep-silver.md)).
3. **History depth answers BRD OP-06:** mailings from late 2020, events from 2013.
4. **Sentinel year 2124:** filter it out (`EventFrom < '2100-01-01'`) before any date arithmetic on events.
5. **Withdrawn:** "build `silver.fact_email` from Bronze with the multi-device pattern". Email engagement is consumed from Gold instead — Q16 for the aggregates, Q26 for the atomic fact `imep_gold.final`.
6. **Freshness:** both masters showed the query date as maximum creation date. Q28 later observed a full Gold rebuild about twice a day ([q28-pipeline-operations.md](q28-pipeline-operations.md)).

## Related

- [q01-q03-imep-schemas-and-tracking-id.md](q01-q03-imep-schemas-and-tracking-id.md) · [q16-imep-gold-inventory.md](q16-imep-gold-inventory.md) · [q21-imep-gold-tier3-schemas.md](q21-imep-gold-tier3-schemas.md) · [q29-gold-tier-structure.md](q29-gold-tier-structure.md)
- [../tables/imep/tbl_event.md](../tables/imep/tbl_event.md) · [../tables/imep_gold/final.md](../tables/imep_gold/final.md) · [../diagrams/er_imep_gold.md](../diagrams/er_imep_gold.md)
- [../semantic-models/imep-mailings.md](../semantic-models/imep-mailings.md)
- [../genie_questions_imep.md](../genie_questions_imep.md) — Q1b · [../sources.md](../sources.md#q1b)
