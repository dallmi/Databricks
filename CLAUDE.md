# Working in this repository

This repository is public. It documents an internal analytics platform without
naming the organisation or exposing its data.

## Public-repo rules

- Never write the organisation's name, also not inside identifiers. Object names
  that carry it are written with the placeholder `__ORG__` (`__ORG__GICTrackingID`,
  `__ORG__Id`, `__ORG__ArticleDate`). `ORG_PREFIX` in `.env` restores the real
  names where content leaves the repo (FitNesse upload); SQL files say at the top
  where to substitute.
- No real person identifiers (T-numbers, GPNs, names, e-mail addresses). Describe
  formats and use the synthetic examples `t100200` and `00100200`.
- No infrastructure identifiers: workspace URL, storage accounts, service
  principal ids, tenant ids, secret scopes, internal host names, real GUIDs. Use
  placeholders such as `<workspace-url>`, `<gold-acc>`, `<storage-account>`.
- No exact figures from production data in new docs. Write orders of magnitude or
  qualitative statements ("hundreds of millions of rows", "a small minority").
  Schema metadata is fine: counts of tables, columns and tiers, grains, keys.
- No local paths.

`scripts/check_public_safe.sh` enforces the generic part, and the hooks in
`.githooks/` run it on every commit and commit message. Activate them once per
clone:

```bash
git config core.hooksPath .githooks
```

Values specific to the real environment go into the untracked
`.local/denylist.txt`, one string per line, so the check blocks them as well.

## Where the knowledge lives

- `docs/findings/` holds the deep-dive findings: Genie questions Q1–Q30, employee
  identifiers, App Insights, the SiteOwnerDashboard pipeline. Start at
  `docs/findings/README.md` and read the relevant finding before asking about
  tables, columns or joins.
- `docs/findings/open-questions.md` lists hypotheses that still need validation.
  Do not treat them as facts.
- `docs/tables/`, `docs/joins/` and `docs/diagrams/` are the schema reference,
  `docs/genie_questions_imep.md` has the raw Genie transcripts, `docs/sources.md`
  the session log.
- Neither the catalog name nor a staging schema is documented. Do not assume them.
- New findings go into `docs/findings/` and its index, so every clone has them.
  A local assistant memory only keeps what cannot be public.

## No data access from development machines

Development machines cannot reach Databricks: no Spark, no cluster, no real data.
Results from real data arrive as photos of the corporate screen.

- Never imply a SQL cell was tested. Say "not executed" and name what was checked
  instead (review, reading, a local harness).
- Python and HTML cells can be exercised locally with a mocked `spark` and
  synthetic data. Label such results as synthetic.
- End every change with the exact cells to re-run, in order, and which results to
  send back.

## Conventions

- English in all technical docs and FitNesse pages, one language per page. Spell
  out abbreviations on first use, e.g. Change Data Capture (CDC). Several older
  docs are partly German: ask before translating one wholesale.
- Employee identifiers (T-number, GPN, `__ORG__Id`, e-mail) are each unique per
  person but not interchangeable. Confirm the identifier type and the bridge
  table before joining; never infer the type from the value format. See
  `docs/findings/hr-employee-identifiers.md`.
- Keep source columns intact. When a source column is unusable for a metric, add
  a clearly named derived column (such as `person_id` or `visit_id`) instead of
  renaming or overwriting the source column.
- Dashboard tables with a totals row: style `tfoot td` generically, with the same
  padding as `tbody td` and `.num` cells right-aligned with tabular numbers, never
  per table id. Compute the label cell's `colspan` from the leading non-metric
  columns and verify the alignment per column index.
