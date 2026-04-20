# FitNesse Integration — Proof of Concept

> **Purpose**: Mirror the Git-based knowledge base (`docs/tables/`, `docs/diagrams/`, `docs/joins/`) into FitNesse so the team can navigate it in the corporate wiki. This directory contains **copy-paste-ready wiki pages** for FitNesse.

---

## POC — What is here

## Local HTML preview (verify before upload)

Before pushing anything to the corporate wiki, render the pages locally to catch formatting issues, broken PlantUML, or bad cross-references:

```bash
python3 scripts/fitnesse_preview_build.py
open docs/fitnesse/preview/index.html
```

Generates one HTML file per page plus a sidebar navigation that mirrors the target FitNesse hierarchy. PlantUML diagrams render via the public PlantUML server (`plantuml.com/plantuml/svg/...`) so your local machine needs internet but no Java/plantuml.jar install. The corp machine never runs this — only your local verification workflow.

Output is gitignored under `docs/fitnesse/preview/`. Re-run the script after every edit to refresh.

---

## Automated upload (recommended)

A Python script uploads all pages + containers in one run:

```bash
# Set your FitNesse location once — the script never ships internal URLs
export FITNESSE_URL=http://<fitnesse-host>:<port>
export FITNESSE_PARENT_PATH=FrontPage.<Your.Parent.Path>

# Dry run — prints the plan without touching FitNesse
python3 scripts/fitnesse_upload.py --dry-run

# Live upload
python3 scripts/fitnesse_upload.py
```

Or pass everything inline (handy for one-off runs):

```bash
python3 scripts/fitnesse_upload.py \
  --base-url http://<fitnesse-host>:<port> \
  --parent-path FrontPage.<Your.Parent.Path> \
  --root-name MultiChannelDataModel
```

**The script does not bake in the FitNesse URL** — internal infrastructure details stay out of the repo. Set `FITNESSE_URL` and `FITNESSE_PARENT_PATH` in your shell profile (e.g. `~/.zshrc` or a local `.env` that's gitignored) and the script picks them up automatically.

Uses only Python stdlib (no `pip install` needed). 30 pages upload in ~15 seconds. Anonymous HTTP POST — no auth required inside the corp network.

**Flags**:
- `--dry-run` — prints the plan and reads all local files, without HTTP calls
- `--stop-on-error` — abort on first failure (default: keep going, report at end)
- `--delay 0.3` — seconds between requests to avoid overwhelming FitNesse

---

## Manual copy-paste (fallback)

The entire KB as copy-paste-ready `.txt` files lives under `pages/`, mirroring the target FitNesse hierarchy:

```
pages/
├── Overview.txt                         -> .MultiChannelDataModel.Overview
├── DataGlossary/
│   ├── ImepBronze/
│   │   ├── TblEmail.txt                 -> …MultiChannelDataModel.DataGlossary.ImepBronze.TblEmail
│   │   └── …
│   ├── ImepGold/
│   ├── SharePointBronze/
│   ├── SharePointGold/
│   └── Hr/
├── Diagrams/
└── JoinStrategy/
```

**How to copy-paste**:
1. Create a new page in FitNesse (following the hierarchy below)
2. Copy the content of the `.txt` file into edit mode
3. Save -> FitNesse automatically renders the PlantUML and tables

---

## Prerequisite — FitNesse PlantUML plugin

For `!startuml / !enduml` blocks to render, your FitNesse instance needs the **PlantUML plugin**. Check with this test page:

```
!1 PlantUML Test
!startuml
entity Test {
  * id : int <<PK>>
}
!enduml
```

- **Renders as a diagram** -> plugin active, we can proceed
- **Shows raw text** -> plugin missing, contact IT (PlantUML is standard in FitNesse enterprise setups)

---

## Suggested FitNesse hierarchy

Root: **`.EmployeeEngagement.CPlanGICTrackingCLARITYDashboard.MultiChannelDataModel`** — a dedicated subsection under the existing `CPlanGICTrackingCLARITYDashboard` page. Sibling to `.CommunicationPack` and any other existing subpages. Keeps the entire Cross-Channel KB in one self-contained tree without diluting the dashboard's own Overview.

```
.EmployeeEngagement.CPlanGICTrackingCLARITYDashboard    [exists]
├── .CommunicationPack                                   [exists]
├── …other existing subpages
└── .MultiChannelDataModel                               [<- our KB root]
├── .Overview                         [Landing page, analogous to knowledge_base.md]
├── .DataGlossary
│   ├── .ImepBronze
│   │   ├── .TblEmail                 [<- our POC page]
│   │   ├── .TblEmailReceiverStatus
│   │   ├── .TblAnalyticsLink
│   │   ├── .TblEmailLinks
│   │   └── .TblEvent
│   ├── .ImepGold
│   │   ├── .Final
│   │   └── .TblPbiPlatformMailings
│   ├── .SharePointBronze
│   │   ├── .Pages
│   │   └── .Pageviews
│   ├── .SharePointGold
│   │   ├── .PbiDbInteractionsMetrics
│   │   └── …
│   └── .Hr
│       ├── .TblHrEmployee
│       ├── .TblHrCostcenter
│       └── .TblHrUser
├── .Diagrams
│   ├── .ImepBronze                 [<- our POC page]
│   ├── .SharePointBronze
│   ├── .ImepGold
│   ├── .SharePointGold
│   └── .CrossChannel
└── .JoinStrategy
    ├── .StrategyContract
    ├── .ImepBronzeEmailEvents
    ├── .SharePointGoldToPages
    ├── .HrEnrichment
    └── .CrossChannelViaTrackingId
```

FitNesse convention: page names are **CamelCase WikiWords** without underscores — hence `TblEmail` rather than `tbl_email`.

---

## Mapping MD -> FitNesse markup

The existing MD docs translate almost 1:1. The main substitutions:

| MD | FitNesse |
|---|---|
| `# H1` | `!1 H1` |
| `## H2` | `!2 H2` |
| `### H3` | `!3 H3` |
| `**bold**` | `'''bold'''` (three apostrophes) |
| `*italic*` | `''italic''` (two apostrophes) |
| `` `code` `` | `!- code -!` or `{{{code}}}` |
| `[Text](url)` | `[[Text][url]]` |
| `[[.Page]]` link | `.EmployeeEngagement.CPlanGICTrackingCLARITYDashboard.MultiChannelDataModel.DataGlossary.ImepBronze.TblEmail` |
| \`\`\`mermaid / \`\`\` | `!startuml / !enduml` (diagram re-authored in PlantUML) |
| `- item` | `  * item` (with leading spaces) |
| Markdown table | FitNesse table `\| col1 \| col2 \|` (no separator row!) |

Cross-references in corporate style: `!see .EmployeeEngagement.CPlanGICTrackingCLARITYDashboard.MultiChannelDataModel.JoinStrategy.StrategyContract`.

---

## PlantUML ER syntax — cheat sheet

PlantUML supports ER diagrams natively. Mapping table Mermaid -> PlantUML:

| Mermaid | PlantUML | Meaning |
|---|---|---|
| `A \|\|--o{ B` | `A \|\|--o{ B` | 1:N (both identical) |
| `A \|\|--\|\| B` | `A \|\|--\|\| B` | 1:1 |
| `A }o--\|\| B` | `A }o--\|\| B` | N:1 |
| `A }o..o{ B` | `A }o..o{ B` | Uncertain / optional |
| `A { string col PK }` | `entity A { * col : string <<PK>> }` | Entity with columns |

Cardinality markers are **identical** between Mermaid and PlantUML — the conversion of the entity blocks is the only difference.

---

## Next steps after POC verification

If the POC pages render cleanly, we can:

1. **Option B**: Roll out all 5 ER diagrams + 25 data-glossary pages + 5 join recipes as `.txt` files (~35 files)
2. **Optional**: A Python generator that automatically produces the FitNesse pages from the MD docs — syncs the Git state with FitNesse on every update

Option A is complete. Feedback welcome on whether PlantUML renders in your environment and whether the format hits the mark.
