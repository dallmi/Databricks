# Telemetry Data Integrity Monitoring

Standalone branch. It carries only the data integrity work and nothing else from
the repository, so it can be read, reviewed or handed over on its own.

**Context.** Between 7 and 8 April 2026 the browser identifier in the intranet
telemetry stopped persisting. Page views and unique visitors stayed correct;
visits and every engagement metric derived from session grouping did not. The
defect was found by people weeks later rather than by the pipeline. This branch
is the monitoring that closes that gap.

## Reading order

| # | File | For whom |
|---|---|---|
| 1 | [`docs/data-integrity-checks.html`](docs/data-integrity-checks.html) | Executive one-pager. The incident and the ten signals that would have caught it. Open in a browser. |
| 2 | [`docs/integrity-checks-by-example.html`](docs/integrity-checks-by-example.html) | Every check on a worked example, healthy beside broken, with a plain-language explanation. Open in a browser. |
| 3 | [`docs/BRD_data_integrity_monitoring.md`](docs/BRD_data_integrity_monitoring.md) | The requirements: goals, scope, 28 numbered requirements, operating model, acceptance criteria, open points. |
| 4 | [`docs/data-integrity-checks.md`](docs/data-integrity-checks.md) | The full catalogue: every check with formula, fields, layer, threshold and the measured results. |
| 5 | [`docs/dq_blocks_engineering_notes.md`](docs/dq_blocks_engineering_notes.md) | Block by block: what each part of the SQL intends, what the query does, and which decisions are non-obvious. |
| 6 | [`dq_checks_prod_readonly.sql`](dq_checks_prod_readonly.sql) | The checks as a read-only notebook. Creates nothing, safe to run against production today. |
| 7 | [`dq_checks_draft.sql`](dq_checks_draft.sql) | The persistent edition, for Dev then pre-prod then production. |

A reviewer with ten minutes should read 1 and 2. A reviewer deciding whether to
build this should read 3. Whoever implements it lives in 5, 6 and 7.

## The two SQL editions

Identical logic and identical thresholds. They differ only in what they leave
behind.

| | Read-only | Persistent |
|---|---|---|
| Creates | Temporary views, session-scoped | A `dq` schema with result and definition tables |
| Safe on production today | Yes | No, promote through Dev and pre-prod first |
| Keeps history | No | Yes |
| Supports alerting and trending | No | Yes |
| Answers | Is the data sound right now | When did this start, and how often |

## Status

- Blocks 0, 0b and 0c have been executed against production. Their findings are
  recorded in the catalogue and the BRD, including the measured healthy
  baselines and the day-by-day shape of the incident.
- Blocks 1 to 9, the daily run, have not been executed anywhere yet.
- Blocks 0d to 0f are root-cause investigation and are not part of the daily run.

## Two principles worth knowing before reading the code

**Numbers are never held back.** No check result gates, delays, drops or filters
anything. A defect is almost always partial while a hold is total, there are many
downstream dependencies, and a stale report is silent about being stale. See BRD
§8.0.

**Labelling is scoped.** Each check declares which reported figures it casts
doubt on, so a banner names those and says which figures remain sound. In April
that means visits and the engagement metrics are flagged while page views and
unique visitors are explicitly declared unaffected.

## Not in this branch

Source photographs of notebook output used during the investigation. They are
gitignored, and they contain workspace identifiers that do not belong in a
repository.
