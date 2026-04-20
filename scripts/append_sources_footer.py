#!/usr/bin/env python3
"""Append Quellen / Sources footer block to each doc file.

Reads a hand-curated mapping of files → relevant Q IDs and appends a standardized
footer pointing at the central sources index.
"""
from __future__ import annotations

from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent

# file → list of Q IDs (in chronological order)
MD_FILES: dict[str, list[str]] = {
    "docs/knowledge_base.md": ["Q1", "Q22", "Q24", "Q26", "Q27", "Q28", "Q29", "Q30"],
    "docs/tables/imep/tbl_email.md": ["Q1b", "Q2", "Q24", "Q26", "Q27", "Q28", "Q29", "Q30"],
    "docs/tables/imep/tbl_analytics_link.md": ["Q2", "Q26", "Q27", "Q28"],
    "docs/tables/imep/tbl_email_links.md": ["Q30"],
    "docs/tables/imep/tbl_email_receiver_status.md": ["Q2", "Q26", "Q27", "Q28"],
    "docs/tables/imep/tbl_event.md": ["Q1b", "Q26", "Q27"],
    "docs/tables/imep_gold/final.md": ["Q26", "Q27", "Q28", "Q29", "Q30"],
    "docs/tables/hr/tbl_hr_employee.md": ["Q3", "Q3a", "Q3b", "Q27", "Q28"],
    "docs/tables/hr/tbl_hr_costcenter.md": ["Q16", "Q21"],
    "docs/tables/hr/tbl_hr_user.md": ["Q3", "Q3b"],
    "docs/tables/sharepoint/pages.md": ["Q2", "Q17", "Q22", "Q25", "Q27", "Q28", "Q30"],
    "docs/tables/sharepoint_gold/pbi_db_interactions_metrics.md": ["Q17", "Q22", "Q26", "Q29", "Q30"],
    "docs/joins/cross_channel_via_tracking_id.md": ["Q23", "Q24", "Q25"],
    "docs/joins/hr_enrichment.md": ["Q3", "Q3a", "Q3b", "Q27"],
    "docs/joins/imep_bronze_email_events.md": ["Q2", "Q24", "Q27"],
    "docs/diagrams/er_imep_bronze.md": ["Q27", "Q30"],
    "docs/diagrams/er_cross_channel.md": ["Q21", "Q22", "Q24", "Q25", "Q27"],
    "docs/diagrams/er_sharepoint_bronze.md": ["Q17", "Q25", "Q28", "Q30"],
    "docs/diagrams/er_sharepoint_gold.md": ["Q17", "Q29", "Q30"],
    "docs/diagrams/er_imep_gold.md": ["Q21", "Q28", "Q29", "Q30"],
}

# FitNesse mirror (same Q-list)
TXT_FILES: dict[str, list[str]] = {
    "docs/fitnesse/pages/Overview.txt": ["Q1", "Q22", "Q24", "Q26", "Q27", "Q28", "Q29", "Q30"],
    "docs/fitnesse/pages/DataGlossary/ImepBronze/TblEmail.txt": ["Q1b", "Q2", "Q24", "Q26", "Q27", "Q28", "Q29", "Q30"],
    "docs/fitnesse/pages/DataGlossary/ImepBronze/TblAnalyticsLink.txt": ["Q2", "Q26", "Q27", "Q28"],
    "docs/fitnesse/pages/DataGlossary/ImepBronze/TblEmailLinks.txt": ["Q30"],
    "docs/fitnesse/pages/DataGlossary/ImepBronze/TblEmailReceiverStatus.txt": ["Q2", "Q26", "Q27", "Q28"],
    "docs/fitnesse/pages/DataGlossary/ImepBronze/TblEvent.txt": ["Q1b", "Q26", "Q27"],
    "docs/fitnesse/pages/DataGlossary/ImepGold/Final.txt": ["Q26", "Q27", "Q28", "Q29", "Q30"],
    "docs/fitnesse/pages/DataGlossary/Hr/TblHrEmployee.txt": ["Q3", "Q3a", "Q3b", "Q27", "Q28"],
    "docs/fitnesse/pages/DataGlossary/Hr/TblHrCostcenter.txt": ["Q16", "Q21"],
    "docs/fitnesse/pages/DataGlossary/Hr/TblHrUser.txt": ["Q3", "Q3b"],
    "docs/fitnesse/pages/DataGlossary/SharePointBronze/Pages.txt": ["Q2", "Q17", "Q22", "Q25", "Q27", "Q28", "Q30"],
    "docs/fitnesse/pages/DataGlossary/SharePointGold/PbiDbInteractionsMetrics.txt": ["Q17", "Q22", "Q26", "Q29", "Q30"],
    "docs/fitnesse/pages/JoinStrategy/CrossChannelViaTrackingId.txt": ["Q23", "Q24", "Q25"],
    "docs/fitnesse/pages/JoinStrategy/HrEnrichment.txt": ["Q3", "Q3a", "Q3b", "Q27"],
    "docs/fitnesse/pages/JoinStrategy/ImepBronzeEmailEvents.txt": ["Q2", "Q24", "Q27"],
    "docs/fitnesse/pages/Diagrams/ImepBronze.txt": ["Q27", "Q30"],
    "docs/fitnesse/pages/Diagrams/CrossChannel.txt": ["Q21", "Q22", "Q24", "Q25", "Q27"],
    "docs/fitnesse/pages/Diagrams/SharePointBronze.txt": ["Q17", "Q25", "Q28", "Q30"],
    "docs/fitnesse/pages/Diagrams/SharePointGold.txt": ["Q17", "Q29", "Q30"],
    "docs/fitnesse/pages/Diagrams/ImepGold.txt": ["Q21", "Q28", "Q29", "Q30"],
}

FITNESSE_SOURCES_PATH = ".EmployeeEngagement.CPlanGICTrackingCLARITYDashboard.MultiChannelDataModel.Sources"


def md_sources_link(file_path: str) -> str:
    depth = file_path.count("/") - 1
    if depth < 0:
        depth = 0
    prefix = "../" * depth if depth > 0 else ""
    return f"{prefix}sources.md"


def build_md_footer(qs: list[str], sources_link: str) -> str:
    q_links = ", ".join(f"[{q}]({sources_link}#{q.lower()})" for q in qs)
    return (
        "\n---\n\n"
        "## Quellen\n\n"
        f"Genie-Sessions, die die Aussagen auf dieser Seite stützen: {q_links}. "
        f"Siehe [sources.md]({sources_link}) für das vollständige Verzeichnis.\n"
    )


def build_txt_footer(qs: list[str]) -> str:
    q_list = ", ".join(qs)
    return (
        "\n----\n\n"
        "!2 Sources\n\n"
        f"Genie sessions supporting this page: {q_list}. "
        f"Full catalog: !see {FITNESSE_SOURCES_PATH}.\n"
    )


def already_has_footer(text: str, marker: str) -> bool:
    return marker in text


def process_md(rel_path: str, qs: list[str]) -> bool:
    path = REPO_ROOT / rel_path
    if not path.exists():
        print(f"  SKIP (missing): {rel_path}")
        return False
    text = path.read_text()
    if already_has_footer(text, "## Quellen"):
        print(f"  already has Quellen: {rel_path}")
        return False
    footer = build_md_footer(qs, md_sources_link(rel_path))
    if not text.endswith("\n"):
        text += "\n"
    path.write_text(text + footer)
    print(f"  appended: {rel_path}")
    return True


def process_txt(rel_path: str, qs: list[str]) -> bool:
    path = REPO_ROOT / rel_path
    if not path.exists():
        print(f"  SKIP (missing): {rel_path}")
        return False
    text = path.read_text()
    if already_has_footer(text, "!2 Sources"):
        print(f"  already has Sources: {rel_path}")
        return False
    footer = build_txt_footer(qs)
    if not text.endswith("\n"):
        text += "\n"
    path.write_text(text + footer)
    print(f"  appended: {rel_path}")
    return True


def main() -> int:
    print("Markdown files:")
    for p, qs in MD_FILES.items():
        process_md(p, qs)
    print("\nFitNesse mirrors:")
    for p, qs in TXT_FILES.items():
        process_txt(p, qs)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
