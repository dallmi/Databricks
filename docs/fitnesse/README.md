# FitNesse Integration — Proof of Concept

> **Purpose**: Mirror the Git-based knowledge base (`docs/tables/`, `docs/diagrams/`, `docs/joins/`) into FitNesse so the team can navigate it in the corporate wiki. This directory contains **copy-paste-ready wiki pages** for FitNesse.

---

## POC — What is here

Two sample pages that can be pasted directly into FitNesse:

| File | FitNesse page name | Purpose |
|---|---|---|
| [sample/TblEmail.txt](sample/TblEmail.txt) | `.ClarityDashboard.DataGlossary.ImepBronze.TblEmail` | Data-glossary entry for a table |
| [sample/ErImepBronze.txt](sample/ErImepBronze.txt) | `.ClarityDashboard.ErDiagrams.ErImepBronze` | ER diagram via PlantUML |

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

Root: **`.ClarityDashboard`** — a new top-level WikiWord under which the entire Cross-Channel KB lives. Sits parallel to the existing `.EmployeeEngagement` branch; keeps our tree clean and navigable without mixing into the broader Marketing-Ecosystem hierarchy.

```
.ClarityDashboard
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
├── .ErDiagrams
│   ├── .ErImepBronze                 [<- our POC page]
│   ├── .ErSharePointBronze
│   ├── .ErImepGold
│   ├── .ErSharePointGold
│   └── .ErCrossChannel
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
| `[[.Page]]` link | `.ClarityDashboard.DataGlossary.ImepBronze.TblEmail` |
| \`\`\`mermaid / \`\`\` | `!startuml / !enduml` (diagram re-authored in PlantUML) |
| `- item` | `  * item` (with leading spaces) |
| Markdown table | FitNesse table `\| col1 \| col2 \|` (no separator row!) |

Cross-references in corporate style: `!see .ClarityDashboard.JoinStrategy.StrategyContract`.

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
