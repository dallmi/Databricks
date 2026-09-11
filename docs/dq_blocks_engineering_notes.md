# Engineering Notes — `dq_checks_draft.sql`, block by block

**Audience:** Head Engineer and anyone maintaining the checks
**Companion to:** [`BRD_data_integrity_monitoring.md`](BRD_data_integrity_monitoring.md)
**Implements:** [`../dq_checks_draft.sql`](../dq_checks_draft.sql) — the persistent
edition, for Dev then pre-prod then PROD
**Read-only twin:** [`../dq_checks_prod_readonly.sql`](../dq_checks_prod_readonly.sql)
— identical logic using temporary views only, safe to run on PROD today. Same
conventions, same thresholds; it writes nothing and keeps no history, so it
answers "is the data sound right now" rather than "when did this start".
**Date:** 2026-09-11 · **Status:** Draft, blocks 0–0c executed, 1–9 not yet run

Each section answers three questions in order: what the block is trying to
achieve, what the query actually does to achieve it, and which decisions in it
are non-obvious enough to be worth defending. BRD references point at the
requirement the block satisfies.

---

## 0. Conventions that apply everywhere

These are decisions taken once and relied on throughout. Breaking one silently
breaks several blocks.

| # | Convention | Why |
|---|---|---|
| C1 | **`CAST(\`timestamp\` AS TIMESTAMP)`, never `to_timestamp()`** | The raw value is ISO-8601 UTC with a `T` separator, milliseconds and a `Z` suffix, e.g. `2026-03-02T00:00:00.229Z`. Neither a bare `to_timestamp()` nor the pattern `yyyy-MM-dd HH:mm:ss` matches it; the first run returned NULL for all 2.8M rows. `CAST` accepts ISO-8601. |
| C2 | **Range-filter on the raw string, not on the cast** | ISO-8601 sorts lexicographically in the same order as chronologically, so `` `timestamp` >= '2026-03-02' `` selects correctly *and* lets Delta skip files on min/max statistics. A predicate wrapped in `CAST` defeats file skipping. BRD NFR-02. |
| C3 | **Never window on `gmdp_date` or `gmdp_timestamp`** | Those record when the platform ingested a row. A late load would move an event into the wrong day. They have exactly one legitimate use: measuring load latency in A2. |
| C4 | **Identity ratios are daily, never pooled** | Pooling a fortnight reported 20.0 browser identities per person; the same state daily reads 3.9, because identities do not recur across days while people do. BRD §10.2, FR-DET-08. |
| C5 | **`DESCRIBE` / Spark schema, never `information_schema`** | The workspace resolves two-part names through the Hive Metastore. `information_schema` is a Unity Catalog feature and raises *Table or view not found*. BRD NFR-03. |
| C6 | **Bronze is already flat** | There is no `customDimensions` column to parse. The former CustomProps are first-class columns: `GPN`, `Email`, `PageURL`, `PageName`, `PublishingDate`, `SiteId`, `SiteName`, `GICTrackingID`. |
| C7 | **Absolute bounds sit below the healthy weekend** | Healthy weekday page views per session is 1.94–2.35 but healthy weekend is 1.59–1.71. A bound placed under the weekday would warn every Saturday. BRD FR-CAL-02. |
| C8 | **Writes only into `dq`** | Source tables are read-only. BRD NFR-04. |

### Column names, confirmed 2026-09-11

| Concept | Column | Note |
|---|---|---|
| Event time | `` `timestamp` `` | **STRING**, ISO-8601 UTC. Backtick it, it is a reserved word. |
| Person | `GPN` | Not `user_gpn`. 8-digit. Present on 100 % of rows. |
| Browser identity | `user_Id` | The SDK cookie. The column that broke. |
| Other identities | `user_AuthenticatedId`, `user_AccountId` | Application-supplied, not yet analysed. BRD OP-08. |
| Session | `session_Id` | |
| Page | `pageId` | **INT**, while the inventory keys on a GUID. BRD OP-04. |
| Ingestion | `gmdp_timestamp`, `ingestiontime` | Two stamps, relationship unconfirmed. BRD OP-05. |

### Bronze holds more rows than silver, by design

Confirmed 2026-09-11: unpublished pages, drafts and similar are filtered out on
the way into silver, so bronze is legitimately larger. The first run measured
202,887 against 169,910, a gap of 16.3 %. Comparing the counts for equality was
therefore wrong, and A6 now watches the *share that survives* against its own
28-day median instead. Silver against gold stays strict and is checked by G3,
which measured exactly 1.0.

### Gold columns, corrected against the workspace

| Documented as | Actually | Found by |
|---|---|---|
| `referenceapplicationid` | **`referrerapplicationid`** | Cell 9 failing on G1, 2026-09-11 |
| `commentss` | **`comments`** | Block 0 probe |
| `marketingPageId` | **`marketingpageid`**, lowercase | Block 0 probe |

The April table cards were transcribed from photographs of Genie output, so a
column name taken from them and not from a live `DESCRIBE` is a guess. Block 0
probes the columns it was told to look for; it does not catch a name nobody
thought to ask about. When a check fails on an unknown column, correct it here
as well as in the SQL, because the table card is still wrong.

---

## Run order

```mermaid
flowchart TD
  subgraph once["Run once, read-only"]
    B0[Block 0<br/>column probe]
    B0b[Block 0b<br/>calibration]
    B0c[Block 0c<br/>date the incident]
  end
  subgraph rc["Root cause, read-only, ad hoc"]
    B0d[0d identity columns]
    B0e[0e which subset]
    B0f[0f hour · second stream]
  end
  subgraph daily["Daily run"]
    B1[Block 1<br/>tables]
    B2[Block 2<br/>slice]
    B3[Block 3<br/>metrics · baseline]
    B4[Block 4<br/>corridor engine]
    B5[Blocks 5-8<br/>explicit checks]
    B8b[8b silver · 8c gold]
    B9[Block 9<br/>alert · banner]
  end
  B0 --> B0b --> B0c --> B1
  B0c -.-> B0d --> B0e --> B0f
  B1 --> B2 --> B3 --> B4 --> B5 --> B8b --> B9

  classDef probe fill:#ECEBE4,stroke:#8E8D83,color:#404040;
  classDef run fill:#F5F0E1,stroke:#B98E2C,color:#404040;
  class B0,B0b,B0c,B0d,B0e,B0f probe;
  class B1,B2,B3,B4,B5,B8b,B9 run;
```

Blocks 0 to 0c have been executed. Blocks 0d to 0f are investigation, not
monitoring, and are not part of the daily job. Blocks 1 to 9 are the daily run
and have not yet been executed.

---

## Block 0 — Column probe

**Intent.** Establish the real schema before writing anything. Everything
downstream is named after what this block finds. BRD §11 phase 0.

**What the query does.** A single Python cell reads the Spark schema of six
tables, then prints per table which expected envelope columns are present and
which are missing, and groups every column into buckets for time, person,
identity, page, tracking and ingestion candidates.

**Non-obvious decisions.**
- Python rather than SQL, because five `DESCRIBE` statements in one `%sql` cell
  render only the last result. One cell has to answer everything in one screen.
- The magic must be the first line of its cell. An earlier version put `%python`
  in the middle of the block and could not run at all.

**What it found.** Bronze is flat (C6). `sdkVersion`, `itemCount`, `iKey` and
`appId` are all present, so checks A4, C3 and C4 run on bronze and the KQL
fallback is unnecessary. `pbi_db_employeecontact` carries `contactId` and no
employee number, so it is not the person bridge.

---

## Block 0b — Calibration probe

**Intent.** Replace guessed thresholds with measured ones, by reading the
identity ratios on a window that predates the incident. BRD FR-CAL-01, §10.1.

**What the query does.** Three fortnights in one grid: 2–15 March, 13–26 April,
and the most recent fortnight. Per window it reports page views per session,
browser identities per person, sessions per person, single-view session share,
the share of rows carrying an employee number, the distinct counts behind those
ratios, and the set of SDK versions seen.

**Non-obvious decisions.**
- The windows deliberately avoid the boundary. A window containing 8 April would
  average healthy and broken together and the baseline would be worthless.
- The single-view share needs a two-level aggregation: sessions first, then the
  share of sessions of size one. It cannot be expressed in one `GROUP BY`.

**Known limitation.** This block pools a fortnight, and pooling inflates
per-person identity counts (C4). Its 20.0 is not comparable with the 3.9 that
Block 0c measures daily. The daily figures govern.

---

## Block 0c — The transition, day by day

**Intent.** Block 0b measures how far the ratios moved but says nothing about
when or how. This dates the incident and shows the shape of the change.
BRD §3.2.

**What the query does.** Thirty days centred on 8 April, one row per day, with
the same identity ratios plus distinct people as a control and the share of
traffic on the SDK version that disappeared.

**Non-obvious decisions.**
- Distinct people runs alongside as a control. If the population moved too, the
  cause is something other than the identifier.
- The weekday is printed next to the date. Weekend traffic is roughly 5 % of a
  weekday, so a naive reading mistakes every Sunday for an outage.

**What it found.** Onset on 7 April, completion on 8 April, then a flat plateau:
a staged rollout, not a switch. The SDK version share was already zero on 25
March, so the version change is **not** the same event, which overturned an
earlier conclusion. Easter distorts 3 and 6 April, which is why the holiday
calendar became a prerequisite (BRD §10.3, OP-03).

---

## Blocks 0d–0f — Root cause

Not monitoring. Investigation aids, run on demand, feeding BRD OP-07 and OP-08.

### Block 0d — Is there still a stable identity?

**Intent.** Bronze carries three identity columns and only one is known broken.
Establish whether the application-supplied identities survived 7 April.

**What the query does.** Compares a pre-window and a post-window on three things:
the fill rate of `user_AuthenticatedId` and `user_AccountId`, the number of
distinct values of each identity column per person, and the shape of the cookie
value (average length, number of distinct lengths, presence of the separator,
one sample). A sub-query then measures what share of identifiers is ever seen on
a second day.

**How to read it.** Authenticated identities per person staying near 1 means the
cookie broke while the instrumentation did not, which makes that column both the
diagnosis and a usable replacement key. All three rising together points at SDK
initialisation. A change in length or separator means the value is being
generated rather than read back.

### Block 0e — Which subset flipped first?

**Intent.** A two-day ramp almost always has a dimension it was rolled out
along. Find it.

**What the query does.** Unpivots four dimensions (site, browser, operating
system, client type, SDK version) into one long table, then reports browser
identities per person for each value on four specific days: Tuesday 31 March
against Tuesday 7 April for the onset, and Wednesday 1 April against Wednesday 8
April for the completed break.

**Non-obvious decisions.**
- Same weekday on both sides of every comparison. Traffic composition differs by
  weekday, so a Monday-to-Wednesday comparison would confound the effect.
- 3 and 6 April are excluded because they are Easter holidays.
- A `HAVING` floor of 500 views drops the long tail, where a handful of views
  produces a meaningless ratio.

**How to read it.** A value already elevated on 7 April was in the first wave.
A value that stays near 1 on all four days was never affected and is the most
informative row on the grid.

### Block 0f — The hour, and the second stream

**Intent.** Two questions. Exactly when on 7 April did it start, and did the
click stream break at the same time.

**What the query does.** Part 1 resolves 6 to 8 April to the hour. Part 2 runs
the same ratios over `customevents` filtered to click events, day by day.

**How to read it.** A sharp edge at one hour boundary is a deployment and gives a
timestamp to search a change record with. A gradual climb points at caching or
staged traffic. If `customevents` breaks on the same day, one SDK-level change
affected both streams; if it stays flat, page-view tracking alone changed and the
click stream becomes a control group.

**Note.** `customevents` has no `id` column, so it cannot be de-duplicated the
way `pageviews` is. Only the identity columns are comparable.

---

## Block 1 — Result table and check catalogue

**Intent.** Give every check one place to write to and one place to read its
thresholds from. BRD FR-REC-01, §9.

**What it does.** Creates `dq.dq_check_result`, one row per day and check, and
`dq.check_def`, a small table holding the thresholds for every check whose rule
is "stay inside a band".

**Why the thresholds live in a table.** Fourteen checks share one evaluation
query (Block 4). Putting the bounds in a table means adding a check is an
`INSERT`, not a new branch of SQL, and it makes every threshold auditable in one
place.

**The threshold columns.** `abs_*` are absolute bounds on the metric, `rel_*`
are relative deviations from the same-weekday median, `step_*` compare the 7-day
mean with the prior 28-day mean. `NULL` means the rule does not apply.

**Important.** The identity bounds describe healthy data, so B1, B4 and B5 fail
today by design and keep failing until the source is repaired. BRD FR-CAL-03
forbids retuning them to the current state.

---

## Block 2 — The working slice

**Intent.** Read the 173M-row bronze table once per day, not once per check.
BRD NFR-01.

**What it does.** Materialises `dq.pv_window`, a 70-day slice, partitioned by
date, with every column the checks need renamed to a stable internal name.

**Non-obvious decisions.**
- The rename layer matters. If a source column is renamed upstream, exactly one
  line changes here rather than thirty lines across nine blocks.
- 70 days because the baseline needs 8 weeks of the same weekday plus headroom.
- The filter is a string range on the raw value (C2), so file skipping survives.
- `gmdp_timestamp` and `ingestiontime` are both carried through, so the
  comparison in OP-05 can be made without touching bronze again.

**Verification.** The block ends with a guard query that counts unparsed
timestamps and prints the first and last event. Run it after the first build.
A non-zero unparsed count means C1 has been violated somewhere.

---

## Block 3 — Daily metrics and the baseline

**Intent.** Turn the slice into one number per day per metric, and give every
number something to be compared against. BRD FR-DET-04, FR-DET-05.

**What it does.** Three stages. `dq.pv_daily` computes the per-day metrics,
including the two-level aggregations for single-view share and double-fire rate
and a rolling seven-day join for sessions per browser identity. `dq.gold_daily`
does the same for the gold fact. `dq.metric_daily` unpivots both into one long
table of day, metric and value. `dq.metric_baseline` then joins each day to the
same weekday over the trailing 8 weeks, takes the median and the median absolute
deviation, and computes the 7-day against prior-28-day means.

**Non-obvious decisions.**
- **Median and MAD, not mean and standard deviation.** One catastrophic day would
  drag a mean and inflate a standard deviation, widening the corridor exactly
  when it should be tightening. The median ignores it.
- **Same weekday, not the previous day.** Weekend traffic is a fraction of a
  weekday. Comparing Monday with Sunday produces a permanent false alarm.
- **`stack()` needs one type per position**, so every value is cast to `DOUBLE`
  before unpivoting. Mixed types fail at runtime, not at parse time.
- **The step comparison is separate from the corridor.** A corridor catches one
  bad day; a step catches a permanent shift that settles into a new normal, which
  is exactly what April was.

**Cost note.** The rolling seven-day self-join is the most expensive operation in
the daily run. If runtime becomes a problem (NFR-05), this is the first thing to
look at.

---

## Block 4 — The corridor engine

**Intent.** Evaluate every check whose rule is "stay inside a band" with one
piece of SQL. BRD FR-DET-01.

**What it does.** Joins `dq.metric_baseline` to `dq.check_def` on the metric
name, then a single `CASE` expression walks the rules in severity order: absolute
blocking bounds, relative blocking deviation, blocking step change, then the
warning equivalents, then the generic 3-MAD corridor. Produces A1, A4, B1, B2,
B4, B5, B8, C8, D1, D4 and D7.

**Non-obvious decisions.**
- **Order inside the `CASE` is the severity ladder.** The first match wins, so
  blocking conditions must precede warning conditions. Reordering them silently
  downgrades alerts.
- **`n_hist < 4` yields `info`, not `ok`.** A check with too little history is
  not passing, it is not yet able to judge. Recording that as `ok` would make a
  fresh deployment look healthy. BRD FR-DET-06.
- **The note carries the evidence**, including both means and the step
  percentage, so an alert is actionable without opening a notebook.

**To add a check.** Add its metric to `dq.pv_daily` and to the `stack()` in
`dq.metric_daily`, then insert one row into `dq.check_def`. No change here.

---

## Blocks 5–8 — The explicit checks

Checks whose rule is not a corridor. Each writes its own result row.

| Block | Checks | What it does | Worth knowing |
|---|---|---|---|
| 5 | A2, A5, A6, A7 | Freshness per layer, duplicate rate on the composite key, the three-layer row tie-out, and late-arrival growth | A5 uses the composite key (second-truncated time, browser, session, page) because the source `id` proved not to be event-unique in real exports. A7 uses Delta time travel, so it depends on the 7-day retention holding. |
| 6 | B3, B6, B7 | Recurring browser identities against the prior 28 days, session survival across short gaps, and official sessions against reconstructed visits | These compare a day against a period, which a corridor cannot express. B7 reconstructs visits in SQL with the same 30-minute rule the Python pipeline uses, so the two stay comparable. |
| 7 | C1–C7 | Schema contract diff, null rates, SDK version watch, instrumentation key, format validity, referential integrity, client mix | C1 materialises the current column set from the Spark schema because of C5. C6 joins pages on the URL, not the id, because of the INT-versus-GUID mismatch in OP-04. |
| 8 | D3, D5, D6, D8 | Top-page stability, gold against a fresh recount from bronze, funnel plausibility, and the report tie-out | D5 is a transformation tie-out, not an independent measurement: gold derives from the same bronze. Its value is the pattern, where visits diverge while people agree. D8 is not SQL; it needs a control measure in the semantic layer. |

---

## Block 8b — Silver

**Intent.** Silver resolves the person. That resolution is what the unique-visitor
number rests on, and a failure there is invisible to every bronze check.
BRD §5.3.

**What it does.** Materialises `dq.sv_daily` from `sharepoint_silver.pageviewed`,
then writes three results. S1 compares distinct employees in bronze with distinct
contacts in silver for the same day. S2 reports the fill rate of `contactId`,
`visitorId`, `sessionId` and `marketingPageId`. S3 tracks the returning-visitor
mix against a four-week baseline.

**Why S1 is the important one.** It is the only check that guards unique
visitors. Every bronze check can be green while people are collapsing into one
another one layer up. A ratio far below 1 means merging, far above means
splitting.

**A gift from the upstream team.** Silver already computes
`visitorReturningStatus`, so S3 reads a pre-computed flag instead of
reconstructing recurrence. Silver's `timestamp` is a real `TIMESTAMP`, unlike
bronze's string.

---

## Block 8c — Gold

**Intent.** Gold is what the report reads. Two failure modes matter here and are
invisible everywhere else. BRD §5.3.

**What it does.** G1 counts duplicates on the documented grain. G2 checks each
row against itself: visits never exceed views, no negative metrics, and
`durationavg` reconciles with `durationsum ÷ views`. G3 ties silver rows to gold
views and silver contacts to gold contacts.

**Why G1 earns its place.** A duplicated grain key multiplies every KPI on that
page while every corridor stays satisfied, because each individual number still
looks plausible. No other check in the catalogue would see it.

**Why G2 earns its place.** An impossible row passes every aggregate test,
because the aggregate is still in range. Only a row-level rule catches it.

---

## Two consumers, one computation

Everything up to cell 9d is a lazy view, so each cell that reads it recomputes
it. The first production run made the cost visible: cell 10 took 14m41s and cell
10b then spent another ten minutes on the same work. Cell 9e now materialises the
verdicts once and caches them, and both consumers read that.

They are not alternatives. Cell 10 is the full grid including every check that
passed, which is what proves coverage and what an engineer wants when tracing a
number. Cell 10b shows only what is failing, grouped and explained. Either can be
skipped; neither costs anything once 9e has run.

## The health board — who the output is written for

Cell 10 of the read-only edition is a grid of check ids and numbers. It is the
right artefact for whoever maintains the checks and the wrong one for everybody
else: *"S2 critical, value 0"* tells a first-line responder nothing about what
broke or whether it matters.

Cell 10b renders the same results as a board, and the reframing is the point. It
organises by **reported figure** rather than by check, because the only question
that person has is which published number they can still trust. Every check gets
a plain-language name and the question it answers, and no check id appears above
the fold.

Two design decisions are worth keeping if this is ever rebuilt in the report.

**One broad check must not condemn everything.** A single failing check that maps
to three figures turns three tiles red at once. That is the blanket-banner
problem from BRD §8.0 reappearing one level down, so the mapping in
`dq_check_affects` has to stay narrow and honest.

**Explaining something nobody has described yet.** The obvious trap is to write
the grouping and the narrative by hand, which works beautifully for the incident
you just spent a day understanding and produces nothing at all for the next one.
Three jobs have to be separated, and only the last needs a person.

| Job | How | Needs knowledge |
|---|---|---|
| Grouping | Checks that started failing on the same day are almost always one cause. `dq_onset` computes the first day each check left its corridor. | No |
| Description | A template filled with measurements: what moved, from what to what, since when, which published figures it touches, which control figures did **not** move, and which slice of the estate deviates (`dq_scope`). | No |
| Cause | Somebody works it out once and records it in `dq_known_causes`. | Yes, once |

The third is the one that cannot be automated. No computation gets from
"identifiers stopped repeating" to "the hosting layer re-initialises the SDK".
What matters is that the cause lives in **data rather than code**, so recording
one costs a row, and a cluster with nothing recorded renders as *cause not yet
identified* instead of fragmenting into unrelated-looking findings.

The description is deliberately a template rather than generated prose, because a
filled template cannot be wrong. A language model could rephrase it more fluently
and that is a fair use. It must not be asked to supply a cause: an invented
explanation in a data-quality tool is worse than no explanation, because people
act on it.

Two limitations worth stating. Onset detection only covers checks driven by the
corridor engine, since those are evaluated for every day in the window; the
explicit checks judge a single day and have no history here. The persistent
edition does not share that limitation, because it stores a row per day and per
check, which is one of the concrete things the read-only edition cannot do. And
`dq_scope` currently derives the site from the URL path, which avoids rebuilding
the slice but depends on the path convention holding.

**Several checks usually mean one problem.** The first production run produced
twelve findings that traced back to four causes, and eight of the twelve were the
one incident already under investigation. Twelve red rows read as twelve
problems; a responder either panics or stops looking. The board therefore groups
findings by cause, states what the cause is in two sentences, and lists the
checks underneath. A check with no cause assigned appears on its own, which is
the correct treatment for something genuinely new.

**New matters more than bad.** A check failing at the same level for weeks is a
known condition; one that moved this week is today's news. The board separates
them with an *ongoing* or *changed recently* chip, derived from the seven-day
mean against the prior twenty-eight. Without that distinction first-line support
escalates the same chronic finding every morning and stops reading the board.

## Block 9 — Alerting, labelling and consumption

**Intent.** Turn stored verdicts into action. BRD §8.

**What it contains.** The alert query that fires on any warning or critical result for
the previous day, ordered so critical results appear first. The view
`dq.v_affected_dates`, which the report reads to drive its banner. The view
`dq.v_data_health`, which de-duplicates to the latest computation per day and
check and feeds the report page. The DLT expectations for row-level rules, as a
commented Python cell. The daily job order.

**Idempotency.** Re-running a day must not duplicate results (BRD FR-REC-03).
Either delete the day before re-inserting, or merge on day and check. The block
documents both; pick one and make the job do it unconditionally.

**Policy: numbers are never held back.** No check result gates, delays, drops or
filters anything. BRD FR-RSP-02 and §8.0.

Three reasons, worth knowing before anyone is tempted to add a gate. A defect is
almost always partial while a hold is total: in April page views and unique
visitors were correct the whole time, and a hold would have removed them too.
There are many downstream dependencies, so stopping a load stalls a chain rather
than pausing one report. And a stale report is silent about being stale, whereas
a labelled figure tells the reader exactly what is wrong.

**Scoped labelling is what makes that safe.** `dq.check_affects` maps each check
to the reported figures it casts doubt on, and `dq.v_affected_dates` returns both
the affected figures and the unaffected ones. The banner must be built from both
columns. Naming what is still sound is the half that keeps the report usable
during an incident, and it is the reason the identity family is deliberately
mapped to visits and the engagement metrics but **not** to page views or unique
visitors. BRD FR-RSP-08, FR-RSP-09.

The same rule applies at row level, which is why only `@dlt.expect` appears in
the commented expectations and never `expect_or_drop` or `expect_or_fail`. Both
withhold rows. BRD FR-RSP-07.

**Two surfaces, two audiences.** `dq.v_health_overview` is the operational one:
every check, every layer, every day, with the figures each failing check puts at
risk, for the technical team to monitor and work from. `dq.v_affected_dates`
drives the consumer-facing banner and says as little as the situation allows.
Both are Must, not Should. Without them the policy degrades into publishing
defects silently, which is worse than either alternative.

Creation order matters: `v_health_overview` reads `v_data_health`, so it is
defined after it in the block.

---

## Open items carried in the code

| Marker | Where | BRD |
|---|---|---|
| `<gpn_column>` removed, C6 now joins on URL | Block 7 | OP-04 |
| `LinkTypeEnum = 'CLICK'` value unconfirmed | Block 8, D6 | — |
| Silver timestamp column confirmed, silver table names confirmed | Blocks 5, 8b | — |
| D8 needs a control measure in the semantic layer | Block 8 | — |
| Two ingestion stamps carried, relationship unconfirmed | Block 2 | OP-05 |
