# FitNesse Integration — Proof of Concept

> **Zweck**: Die Git-basierte Knowledge Base (`docs/tables/`, `docs/diagrams/`, `docs/joins/`) in FitNesse spiegeln, damit das Team im Corporate-Wiki navigieren kann. Dieses Verzeichnis enthält **Copy-paste-fertige Wiki-Pages** für FitNesse.

---

## POC — Was hier liegt

Zwei Beispiel-Pages, die direkt in FitNesse einfügbar sind:

| File | FitNesse-Page-Name | Zweck |
|---|---|---|
| [sample/TblEmail.txt](sample/TblEmail.txt) | `.CrossChannelAnalytics.DataGlossary.ImepBronze.TblEmail` | Data-Glossary-Eintrag für eine Tabelle |
| [sample/ErImepBronze.txt](sample/ErImepBronze.txt) | `.CrossChannelAnalytics.ErDiagrams.ErImepBronze` | ER-Diagramm via PlantUML |

**So copy-pastest du**:
1. In FitNesse eine neue Page anlegen (nach der Hierarchy unten)
2. Inhalt der `.txt`-Datei in den Edit-Mode kopieren
3. Save → FitNesse rendert das PlantUML + die Tabellen automatisch

---

## Voraussetzung — FitNesse PlantUML-Plugin

Damit `!startuml / !enduml`-Blöcke gerendert werden, braucht deine FitNesse-Instanz das **PlantUML-Plugin**. Prüfe mit dieser Test-Page:

```
!1 PlantUML Test
!startuml
entity Test {
  * id : int <<PK>>
}
!enduml
```

- **Rendert als Diagramm** → Plugin aktiv, wir können loslegen
- **Zeigt Rohtext** → Plugin fehlt, IT ansprechen (PlantUML ist bei FitNesse-Enterprise-Setups Standard)

---

## Vorgeschlagene FitNesse-Hierarchie

```
.CrossChannelAnalytics
├── .Overview                         [Landing page, analog zu knowledge_base.md]
├── .DataGlossary
│   ├── .ImepBronze
│   │   ├── .TblEmail                 [← unsere POC-Page]
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
│   ├── .ErImepBronze                 [← unsere POC-Page]
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

FitNesse-Convention: Page-Namen sind **CamelCase WikiWords** ohne Unterstriche — deshalb `TblEmail` statt `tbl_email`.

---

## Mapping MD → FitNesse-Markup

Die bestehenden MD-Docs übersetzen sich fast 1:1. Hier die wichtigsten Substitutionen:

| MD | FitNesse |
|---|---|
| `# H1` | `!1 H1` |
| `## H2` | `!2 H2` |
| `### H3` | `!3 H3` |
| `**bold**` | `'''bold'''` (drei Apostrophe) |
| `*italic*` | `''italic''` (zwei Apostrophe) |
| `` `code` `` | `!- code -!` oder `{{{code}}}` |
| `[Text](url)` | `[[Text][url]]` |
| `[[.Page]]`-Link | `.CrossChannelAnalytics.DataGlossary.ImepBronze.TblEmail` |
| \`\`\`mermaid / \`\`\` | `!startuml / !enduml` (Diagramm neu in PlantUML) |
| `- item` | `  * item` (mit führenden Spaces) |
| Markdown-Table | FitNesse-Table `\| col1 \| col2 \|` (ohne Separator-Zeile!) |

Cross-References im Corporate-Style: `!see .CrossChannelAnalytics.JoinStrategy.StrategyContract`.

---

## PlantUML ER-Syntax — Cheat Sheet

PlantUML unterstützt ER-Diagramme nativ. Hier die Mapping-Tabelle Mermaid → PlantUML:

| Mermaid | PlantUML | Bedeutung |
|---|---|---|
| `A \|\|--o{ B` | `A \|\|--o{ B` | 1:N (beide identisch) |
| `A \|\|--\|\| B` | `A \|\|--\|\| B` | 1:1 |
| `A }o--\|\| B` | `A }o--\|\| B` | N:1 |
| `A }o..o{ B` | `A }o..o{ B` | Unsicher/optional |
| `A { string col PK }` | `entity A { * col : string <<PK>> }` | Entity mit Spalten |

Cardinality-Marker sind zwischen Mermaid und PlantUML **identisch** — die Konvertierung der Entity-Blöcke ist der einzige Unterschied.

---

## Nächste Schritte nach POC-Verifikation

Wenn die POC-Pages sauber rendern, können wir:

1. **Option B**: Alle 5 ER-Diagramme + 25 Data-Glossary-Pages + 5 Join-Recipes als `.txt`-Files ausrollen (~35 Files)
2. **Optional**: Ein Python-Generator, der aus den MD-Docs automatisch die FitNesse-Pages erzeugt — synchronisiert den Git-Stand mit FitNesse bei jedem Update

Aktuell ist A komplett. Feedback bitte, ob PlantUML bei euch rendert und das Format trifft.
