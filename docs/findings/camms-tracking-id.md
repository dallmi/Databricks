# CammsTrackingID: the Cross-Channel Key from Communication Planning

> Source: working notes on the communication-planning (CPLAN) pipeline and the App Insights telemetry, written before the Genie sessions; the format was later confirmed by Genie question Q23 (2026-04-20). Status: confirmed.

## Question

What is `CammsTrackingID` in Application Insights (App Insights), how is it structured, and how does it connect planning data with delivery telemetry?

## Findings

`CammsTrackingID` in App Insights (`customDimensions.CustomProps`) is the same identifier as `tracking_id` in CPLAN, the communication-planning tool. CPLAN generates it for every communication activity — email, article, banner, event.

iMEP (`TrackingId`) and SharePoint (`__ORG__GICTrackingID`) use the same format, but each system carries the ids of its own activities, so the full values almost never match across systems (see [Q23](q23-tracking-id-format.md)).

### Format

32 characters, 5 segments separated by `-`. Synthetic example: `ABCDE-0000123-240709-0000045-EMI`.

| # | Name in CPLAN | Content | Example |
|---|---|---|---|
| 1 | `tracking_cluster_id` | Cluster abbreviation | `ABCDE` |
| 2 | `tracking_pack_number` | Pack sequence | `0000123` |
| 3 | `tracking_pub_date` | Date `YYMMDD` | `240709` |
| 4 | `tracking_activity_number` | Activity sequence | `0000045` |
| 5 | `tracking_channel_abbr` | Channel, e.g. `EMI` = email, `EVT` = event | `EMI` |

Derived: `tracking_pack_id` = segment 1 + `-` + segment 2 (e.g. `ABCDE-0000123`).

### Joins into the planning data

| Tracking-id part | Planning table and column | Note |
|---|---|---|
| `tracking_channel_abbr` | `channels.channel_abbr` | match with `source_type` |
| `tracking_cluster_id` | `clusters.cluster_abbreviation` | |
| `tracking_pack_id` | `packs.cpid` | |

The planning data flows SharePoint list → CSV export → CPLAN pipeline. It is also available in Databricks as `sharepoint_cplan` (see [Q17](q17-sharepoint-gold-inventory.md)).

## Consequences

1. **This is the key that combines planning and performance in one dashboard:** CPLAN planning data on one side, delivery telemetry from App Insights and Databricks on the other.
2. **When working with `pbi_db_*` page-view data, split `CammsTrackingID` into its five parts**, mirroring the split logic in the CPLAN processing script, so it joins to packs, clusters and channels.
3. **Watch for typo variants in the CPLAN source CSVs:** both `Tracking ID` and `Tacking ID` occur as column headers.
4. **Cross-channel matching uses the pack id only** (segments 1–2); see [Q23](q23-tracking-id-format.md) for why the finer segments diverge between channels.

## Related

- [q23-tracking-id-format.md](q23-tracking-id-format.md) — format check and the pack-level rule
- [q01-q03-imep-schemas-and-tracking-id.md](q01-q03-imep-schemas-and-tracking-id.md) — where the tracking id lives in iMEP
- [appinsights-source.md](appinsights-source.md) — `customDimensions` and `CustomProps` structure
- [q17-sharepoint-gold-inventory.md](q17-sharepoint-gold-inventory.md) — `sharepoint_cplan` in Databricks
- [cross-channel-mvp-scope.md](cross-channel-mvp-scope.md)
- [../joins/cross_channel_via_tracking_id.md](../joins/cross_channel_via_tracking_id.md)
