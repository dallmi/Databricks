#!/usr/bin/env python3
"""Strip inline Q-reference markers (like '(Q28)', '— Q30', '*(Q22, Q27)*') from docs.

Writes back in place. Run only against docs that should carry Q-references in a
dedicated Sources footer instead of inline.

Usage:
    python3 scripts/strip_q_refs.py <path> [<path> ...]
"""
from __future__ import annotations

import re
import sys
from pathlib import Path


# Patterns to strip, ordered from most specific to most general.
PATTERNS: list[tuple[re.Pattern[str], str]] = [
    # Markdown italic annotation at end of sentence: " *(Q28)*" / " *(Q22, Q24)*" / " *(Q28, scope-limited)*"
    (re.compile(r"\s*\*\(Q\d+[a-z]?(?:[,/]\s*Q\d+[a-z]?)*(,[^)]+)?\)\*"), r""),
    # FitNesse italic: " ''(Q28)''"
    (re.compile(r"\s*''\(Q\d+[a-z]?(?:[,/]\s*Q\d+[a-z]?)*\)''"), r""),
    # Table cell trailing "— Q28 |" or "— Q22 |"
    (re.compile(r"\s+—\s+Q\d+[a-z]?\s*(?=\|)"), r" "),
    # Sentence end with " (Q28)" or " (Q22, Q24)" or " (Q1b)"
    (re.compile(r"\s+\(Q\d+[a-z]?(?:[,/]\s*Q\d+[a-z]?)*\)(?=[\s.,:;]|$)"), r""),
    # " — Q28" at end of line / before punctuation
    (re.compile(r"\s+—\s+Q\d+[a-z]?(?=[\s.,:;]|$)"), r""),
    # "(siehe Q28)" / "(see Q28)" — drop whole parenthetical
    (re.compile(r"\s*\((?:siehe|see)\s+Q\d+[a-z]?(?:[,/]\s*Q\d+[a-z]?)*\)"), r""),
    # Trailing "— siehe Q28" at end of sentence
    (re.compile(r"\s*—\s*(?:siehe|see)\s+Q\d+[a-z]?\b"), r""),
    # Inline "(Q29-Stand 2026-04-20, Timespan …)" or "(Q27-Stand ..." — strip Q prefix
    (re.compile(r"\(Q\d+[a-z]?(?:/Q\d+[a-z]?)*-Stand(?=[\s,)])"), r"(Stand"),
    # Trailing "(Q27-Stand)" standalone
    (re.compile(r"\(Q\d+[a-z]?-Stand\)"), r"(Stand)"),
    # "(Q29-bestätigt)", "(Q29 bestätigt)" etc.
    (re.compile(r"\(Q\d+[a-z]?[\s-](bestätigt|confirmed|explizit bestätigt|confirms|hypothesis|hypothesiert)([^)]*)\)"), r"(\1\2)"),
    # "— Q23). " / " — Q23) " — trailing attribution at end of parenthetical
    (re.compile(r"\s+—\s+Q\d+[a-z]?\)"), r")"),
    # "(Q17/Q22 hypothesize …)" — drop Q prefix
    (re.compile(r"\(Q\d+[a-z]?(?:/Q\d+[a-z]?)+\s+"), r"("),
    # "Q23 bestätigt:" / "Q25 confirms:" at start of sentence — strip prefix
    (re.compile(r"\bQ\d+[a-z]?\s+(bestätigt|confirms?|confirmed|hypothesiert|hypothesizes)\b"), r"Dies \1"),
    # "Q3a hat bestätigt:" — German variant
    (re.compile(r"\bQ\d+[a-z]?\s+hat\s+bestätigt\b"), r"Bestätigt"),
    # "Q3b-Finding:" / "Q3b finding:" at start of sentence
    (re.compile(r"\bQ\d+[a-z]?-(Finding|finding)[\s:]"), r""),
    # "Q2-Schema-Check" → "Schema-Check"
    (re.compile(r"\bQ\d+[a-z]?-Schema-Check\b"), r"Schema-Check"),
    # "vom Q2-Schema-Check" → "vom Schema-Check"
    (re.compile(r"Q\d+[a-z]?\s+schema check"), r"schema check"),
    # "(Q24 adoption ramp)" / "(Q22 rule)" — drop the Q prefix
    (re.compile(r"\(Q\d+[a-z]?\s+"), r"("),
    # Q-Fund / Q-Findings in prose "(Q27-Fund)" → "(finding)"
    (re.compile(r"\*?\(Q\d+[a-z]?-Fund\)\*?"), r"(Finding)"),
    # "(Q29, 31 tables classified)" / "(Q29, 20 tables + 3 …)" — drop Q, keep rest
    (re.compile(r"\(Q\d+[a-z]?,\s+"), r"("),
    # "(Q30 to verify)" / "(Q30 zu klären)" — drop Q prefix
    (re.compile(r"\(Q\d+[a-z]?\s+(to verify|zu klären|zu verifizieren|follow-?up)\)", re.IGNORECASE), r"(\1)"),
    # "Siehe Q30" / "See Q30" — mid-sentence, strip
    (re.compile(r"\s+Siehe Q\d+[a-z]?\."), r"."),
    (re.compile(r"\s+See Q\d+[a-z]?\."), r"."),
    # "laut Q28" / "per Q28" / "according to Q28"
    (re.compile(r"\s+laut\s+Q\d+[a-z]?\b"), r""),
    (re.compile(r"\s+per\s+Q\d+[a-z]?\b"), r""),
    (re.compile(r"\s+according to\s+Q\d+[a-z]?\b"), r""),
    # "Q30-Follow-up" → "Follow-up"
    (re.compile(r"Q\d+[a-z]?-Follow-?up"), r"Follow-up"),
    # "Verifikations-Stand nach Q29/Q30" → "Verifikations-Stand"
    (re.compile(r"-Stand nach\s+Q\d+[a-z]?(?:/Q\d+[a-z]?)*"), r"-Stand"),
    # "nach Q29/Q30" → "nach späterer Verifikation"
    (re.compile(r"\s+nach\s+Q\d+[a-z]?(?:/Q\d+[a-z]?)*"), r" nach späterer Verifikation"),
    # "stammt aus … Genie-Sessions (Q1–Q30)" / "(Q1-Q30)" — drop the Q span
    (re.compile(r"\s*\(Q1\s*[–—-]\s*Q30\)"), r""),
    # "(Q27-Stand 2026-04-20, Timespan ...)" leftover — after earlier (Stand substitution
    (re.compile(r"\(Stand\s+(\d{4}-\d{2}-\d{2}),?"), r"(Stand \1,"),
]


def strip_file(path: Path) -> int:
    original = path.read_text()
    text = original
    for pattern, replacement in PATTERNS:
        text = pattern.sub(replacement, text)
    if text != original:
        path.write_text(text)
        # count rough number of Q-refs removed by comparing before/after
        return sum(1 for _ in re.finditer(r"Q\d+[a-z]?", original)) - sum(
            1 for _ in re.finditer(r"Q\d+[a-z]?", text)
        )
    return 0


def main(argv: list[str]) -> int:
    if len(argv) < 2:
        print("usage: strip_q_refs.py <file-or-dir> [<file-or-dir> ...]", file=sys.stderr)
        return 1
    total = 0
    files_changed = 0
    for arg in argv[1:]:
        root = Path(arg)
        if root.is_file():
            targets = [root]
        else:
            targets = [p for p in root.rglob("*") if p.suffix in {".md", ".txt"}]
        for p in targets:
            removed = strip_file(p)
            if removed:
                total += removed
                files_changed += 1
                print(f"  {p}: removed ~{removed} Q-ref tokens")
    print(f"\nStripped {total} Q-ref tokens across {files_changed} files.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
