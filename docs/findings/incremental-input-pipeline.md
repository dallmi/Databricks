# Incremental Input Pipeline: SHA-256 Manifest and Composite-Key Upsert

> Source: pipeline work on the SiteOwnerDashboard with real intranet exports (2026-07-13). Status: confirmed.

## Context

Since 2026-07-13, every pipeline that reads an input folder has to process **incrementally**. A full reprocess on each run is not acceptable. The reference implementation is the CampaignWe project's processing script (`process_campaignwe.py`). `SiteOwnerDashboard/scripts/process_site_pageviews.py` ports it to pandas and Parquet, and `process_site_interactions.py` reuses the same helpers.

## Findings

### The house pattern

| Step | Mechanism |
|---|---|
| Change detection | A SHA-256 hash per input file, stored in a manifest of processed files. Unchanged files are skipped on every run |
| Write | **Upsert instead of append:** delete-then-insert on a primary key. Overlapping exports never double-count. The portal caps the number of rows per export, so long windows are exported in chunks that can overlap |
| Changed file | Before its new rows go in, all rows the file contributed earlier are evicted (matched on `source_file`) |
| Full reprocess | `--rebuild` wipes the manifest and processes everything again |

In the SiteOwnerDashboard, the manifest is JSON (`output/site_pageviews.manifest.json`). A run without arguments discovers `input/*.csv|xlsx` and the HR history parquet on its own. Files named explicitly on the command line skip the hash check (forced) but still go through the upsert.

### The App Insights `id` is not unique per event

On real exports, a per-file duplicate check fired on the App Insights `pageViews` `id`. **`id` cannot be the event key.** It is kept as data only (`id` / `view_id`).

The upsert key is therefore a composite **`event_key`**:

| Stream | `event_key` components |
|---|---|
| `pageViews` | timestamp truncated to the second + `user_id` + `session_id` + `page_id` |
| `customEvents` (`click_event`) | the above + `component_name` + `link_address` + `link_label` |

- All components are stored columns, so the key can be rebuilt on an existing store.
- Truncating to the second makes the same rows match whether they come from a CSV export (sub-second timestamps) or an Excel export (truncated to the second).
- The key uses the **source** `user_id` and `session_id`, not the derived `person_id` and `visit_id`.
- Accepted limitation: two views of the same page by the same user and session within the same second collapse into one.

### Derived columns are recomputed over the whole store

After each upsert, `derive_person_visit()` recomputes `person_id`, `visit_id` and the time-on-page columns over the **full** store, not only the new batch. Visits can span file boundaries, so a batch-only derivation would cut them. The result is deterministic and idempotent. The star-schema upsert in `scripts/flatten_appinsights.py` tolerates new columns (`ALTER` + `INSERT BY NAME`), so older stores pick up added columns.

## Consequences

- Reuse the manifest + composite-key upsert shape for every new export-folder pipeline.
- Never assume an App Insights `id` is an event key, and never assume any identifier is unique without checking it on real data.
- Build keys only from stored columns, and truncate timestamps to the precision of the coarsest export format.
- After a scope or key change, run once with `--rebuild`.

## Related

- [siteowner-dashboard-pipeline.md](siteowner-dashboard-pipeline.md): both build scripts and the interactions upsert key
- [appinsights-source.md](appinsights-source.md): the raw export format
- [appinsights-sessions-and-identity.md](appinsights-sessions-and-identity.md): `person_id`, `visit_id` and why the source session ids behave as they do
- [q28-pipeline-operations.md](q28-pipeline-operations.md): MERGE and write patterns in the Databricks layers
