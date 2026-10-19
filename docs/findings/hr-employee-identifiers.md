# HR Employee Identifiers: T-Number, GPN and the HR Bridge

> Source: Genie questions Q3, Q3a and Q3b, confirmed by the business owner (2026-04-16). Status: confirmed.

## Question

Which identifiers name an employee in the two channels, how do they relate, and where can one be translated into the other? iMEP (the internal e-mail platform) records recipients by T-number. The intranet telemetry from Application Insights (App Insights) records readers by GPN (Global Personnel Number).

## Findings

### Two identifiers, both unique per employee, not interchangeable

| Identifier | Format | Where it appears |
|---|---|---|
| T-number | `t` + 6 digits. Lowercase in iMEP and HR (`t100200`), uppercase in `tbl_hr_user.__ORG__Id` (`T100200`) | `tbl_analytics_link.TNumber`, `tbl_email_receiver_status.TNumber`, `tbl_email.CreatedBy`, `tbl_hr_employee.T_NUMBER`, `tbl_hr_user.__ORG__Id` |
| GPN | 8 digits, zero-padded, stored as a string (`00100200`) | App Insights `CustomProps.GPN`, `sharepoint_bronze.pageviews.user_gpn`, the pipeline's `fact_page_view.gpn`, the HR history snapshot (`hr_history.parquet`), `tbl_hr_employee.WORKER_ID` |

Each identifier is unique per employee. They are still different keys. Treating them as equivalent breaks every per-recipient cross-channel question, such as "how many recipients of a mailing later opened the linked page?".

The synthetic examples share their digits only for readability. No string rule maps a GPN to a T-number. Padding or prefixing works for some people and fails for others (see the anti-pattern in [hr_enrichment.md](../joins/hr_enrichment.md#scenario-2-sharepoint-gpn--imep-tnumber)).

These identifiers look like person keys but are not T-numbers or GPNs:

| Field | What it is |
|---|---|
| App Insights `user_Id` | Anonymous browser or device identifier. Not a person (see [appinsights-sessions-and-identity.md](appinsights-sessions-and-identity.md)) |
| `CustomProps.Email` | E-mail address from the telemetry. Used with the GPN for HR matching in the gold layer |
| `viewingcontactid` on `sharepoint_gold.*` | GUID contact ID, produced from GPN or e-mail matched to HR. Not a GPN or T-number |

### GPN and T-number are 1:1 in HR

The business owner confirmed that HR holds a 1:1 relationship between GPN and T-number.

### How the bridge was found

1. **First search (Q3):** Genie scanned the HR tables with a keyword list (`gpn`, `global`, `pid` and similar). The wide HR employee table (more than a hundred columns) and the HR user table showed no column with a GPN-like name. The conclusion was "GPN does not exist in HR". It opened a critical open point (OP-07e) in the cross-channel Business Requirements Document (BRD), which pointed towards opening up an external bridge source such as the directory or single sign-on.
2. **Correction:** The business owner stated that the 1:1 mapping exists in HR. A missing column named "GPN" shows that the search was too narrow. It does not show that the bridge is missing. There were three possible explanations: a non-obvious column name, HR tables nobody had inspected, or the value hidden inside a JSON or map column.
3. **Resolution (same day):** `imep_bronze.tbl_hr_employee.WORKER_ID` **is** the GPN. The same row carries `T_NUMBER`, so the bridge is a plain `LEFT JOIN` with no string transformation. Genie had already listed `WORKER_ID` as "Primary worker identifier" among the identifier-adjacent columns, but it did not connect that column to the GPN.

Naming detail: Genie returned the table as `tbl_hr_employees` (plural). The confirmed name is `imep_bronze.tbl_hr_employee` (singular). This is probably a catalog or schema path variation, so check both names when you build a pipeline.

This closed OP-07d and OP-07e in the cross-channel BRD. OP-07b (uniqueness and history of the organisational-unit join) and OP-07c (creator reporting) stay open.

### Lesson: the first search was too narrow, so broaden it before assuming an external bridge

Before an architecture decision about a bridge source:

- Print **all** columns of **every** `tbl_hr_*` table, not just the columns that match a keyword list.
- Search on broader names: `gpn`, `global`, `personnel`, `worker`, `pid`, `emp_id`, `master`, `corp_id`.
- Search on **value shape**, not only on names. For the GPN that means 8-digit zero-padded strings.
- Check JSON, map and struct columns in wide tables.
- Write "column not found yet, widen the search" rather than "column does not exist".

## Consequences

- **Bridge join.** Translate a GPN to a T-number through HR:

  ```sql
  SELECT pv.*, LOWER(hr.T_NUMBER) AS t_number
  FROM   <page_view_fact>               pv
  LEFT JOIN imep_bronze.tbl_hr_employee hr ON hr.WORKER_ID = pv.gpn
  ```

  The original plan was to resolve the GPN to a T-number straight away in the page-view silver step. That would make the T-number the only recipient key in silver and gold, and the cross-channel e-mail ↔ page join per recipient would then need no further translation.
- **Use `LEFT JOIN`, never `INNER JOIN`.** Leavers and external recipients are not in the current HR snapshot.
- **Keep the GPN as a string.** Casting `WORKER_ID` or `gpn` to an integer drops the leading zeros. The flattener normalises the telemetry GPN to 8 zero-padded digits (`normalize_gpn` in `scripts/flatten_appinsights.py`) and turns blanks and all-zero values into null.
- **Normalise case** when joining `T_NUMBER`/`TNumber` (lowercase) with `__ORG__Id` (uppercase): `LOWER(...) = LOWER(...)`.
- **Never conflate identifiers.** T-number, GPN, `__ORG__Id`, e-mail and contact ID each need an explicit mapping.
- **When the bridge is needed.** You only need it for per-recipient aggregation. Pack-level cross-channel analysis through the tracking ID works without it (see [q23-tracking-id-format.md](q23-tracking-id-format.md)).
- **Open: where else the T-number appears.** Q27 found the T-number in only two iMEP tables, which would make person-level analytics email-only. That claim still needs a broader re-search before anyone relies on it (see [open-questions.md](open-questions.md)).

## Related

- [hr_enrichment.md](../joins/hr_enrichment.md): canonical HR join recipes, including case normalisation and region/division lookup
- [tbl_hr_employee.md](../tables/hr/tbl_hr_employee.md), [tbl_hr_user.md](../tables/hr/tbl_hr_user.md), [tbl_hr_costcenter.md](../tables/hr/tbl_hr_costcenter.md)
- [q01-q03-imep-schemas-and-tracking-id.md](q01-q03-imep-schemas-and-tracking-id.md): the Genie round that first searched for the GPN
- [q27-imep-join-graph.md](q27-imep-join-graph.md): where the T-number occurs in the iMEP join graph
- [appinsights-source.md](appinsights-source.md): where the GPN sits in the telemetry
- [appinsights-sessions-and-identity.md](appinsights-sessions-and-identity.md): `person_id`, built from the GPN
- [open-questions.md](open-questions.md)
