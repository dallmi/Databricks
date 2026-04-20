#!/usr/bin/env python3
"""
Uploads the Cross-Channel Analytics knowledge base (22 pages) to FitNesse.

Reads .txt files from docs/fitnesse/pages/ and POSTs each as a FitNesse
Static Page under the configured parent path. Uses only Python stdlib — no
external dependencies. Ships a --dry-run mode that prints the plan without
touching FitNesse.

Usage:
    # Provide the FitNesse URL via --base-url or FITNESSE_URL env var
    export FITNESSE_URL=http://<fitnesse-host>:<port>
    python3 scripts/fitnesse_upload.py --dry-run
    python3 scripts/fitnesse_upload.py

Override other defaults as needed:
    python3 scripts/fitnesse_upload.py \\
        --base-url http://<fitnesse-host>:<port> \\
        --parent-path <FrontPage.Parent.Path> \\
        --root-name MultiChannelDataModel

The base URL is intentionally not baked into the source file to keep
internal infrastructure out of the public repo.
"""

from __future__ import annotations

import argparse
import os
import sys
import time
import urllib.parse
import urllib.request
import urllib.error
from pathlib import Path

DEFAULT_BASE_URL = os.environ.get("FITNESSE_URL", "")
DEFAULT_PARENT_PATH = os.environ.get("FITNESSE_PARENT_PATH", "")
DEFAULT_ROOT_NAME = os.environ.get("FITNESSE_ROOT_NAME", "MultiChannelDataModel")

# Order of upload — containers first (so !see links resolve as we add leaves).
# Empty-content items get a minimal description; leaves read from disk.
PLAN: list[tuple[str, str | None]] = [
    # path_relative_to_root              , source_file_relative_to_pages_dir
    ("",                                     "Overview.txt"),  # leaf content for root itself
    ("DataGlossary",                         None),
    ("ErDiagrams",                           None),
    ("JoinStrategy",                         None),
    ("DataGlossary.ImepBronze",              None),
    ("DataGlossary.ImepGold",                None),
    ("DataGlossary.SharePointBronze",        None),
    ("DataGlossary.SharePointGold",          None),
    ("DataGlossary.Hr",                      None),
    # Content pages
    ("ErDiagrams.ErImepBronze",              "ErDiagrams/ErImepBronze.txt"),
    ("ErDiagrams.ErSharePointBronze",        "ErDiagrams/ErSharePointBronze.txt"),
    ("ErDiagrams.ErImepGold",                "ErDiagrams/ErImepGold.txt"),
    ("ErDiagrams.ErSharePointGold",          "ErDiagrams/ErSharePointGold.txt"),
    ("ErDiagrams.ErCrossChannel",            "ErDiagrams/ErCrossChannel.txt"),
    ("JoinStrategy.StrategyContract",        "JoinStrategy/StrategyContract.txt"),
    ("JoinStrategy.ImepBronzeEmailEvents",   "JoinStrategy/ImepBronzeEmailEvents.txt"),
    ("JoinStrategy.SharePointGoldToPages",   "JoinStrategy/SharePointGoldToPages.txt"),
    ("JoinStrategy.HrEnrichment",            "JoinStrategy/HrEnrichment.txt"),
    ("JoinStrategy.CrossChannelViaTrackingId", "JoinStrategy/CrossChannelViaTrackingId.txt"),
    ("DataGlossary.Hr.TblHrEmployee",        "DataGlossary/Hr/TblHrEmployee.txt"),
    ("DataGlossary.Hr.TblHrCostcenter",      "DataGlossary/Hr/TblHrCostcenter.txt"),
    ("DataGlossary.Hr.TblHrUser",            "DataGlossary/Hr/TblHrUser.txt"),
    ("DataGlossary.ImepBronze.TblEmail",                "DataGlossary/ImepBronze/TblEmail.txt"),
    ("DataGlossary.ImepBronze.TblEmailReceiverStatus",  "DataGlossary/ImepBronze/TblEmailReceiverStatus.txt"),
    ("DataGlossary.ImepBronze.TblAnalyticsLink",        "DataGlossary/ImepBronze/TblAnalyticsLink.txt"),
    ("DataGlossary.ImepBronze.TblEmailLinks",           "DataGlossary/ImepBronze/TblEmailLinks.txt"),
    ("DataGlossary.ImepBronze.TblEvent",                "DataGlossary/ImepBronze/TblEvent.txt"),
    ("DataGlossary.ImepGold.Final",                     "DataGlossary/ImepGold/Final.txt"),
    ("DataGlossary.SharePointBronze.Pages",             "DataGlossary/SharePointBronze/Pages.txt"),
    ("DataGlossary.SharePointGold.PbiDbInteractionsMetrics", "DataGlossary/SharePointGold/PbiDbInteractionsMetrics.txt"),
]

CONTAINER_STUB = """!1 {name}

Container page grouping the {name} section of the MultiChannelDataModel knowledge base.

{subsection_hint}
"""

SUBSECTION_HINT = {
    "DataGlossary":            "See subfolders !see .ImepBronze, !see .ImepGold, !see .SharePointBronze, !see .SharePointGold, !see .Hr for per-domain table cards.",
    "ErDiagrams":              "Five domain-scoped entity-relationship diagrams covering iMEP Bronze, iMEP Gold, SharePoint Bronze, SharePoint Gold, and the Cross-Channel bridge.",
    "JoinStrategy":            "Start with !see .StrategyContract for the five hard rules, then consult the canonical recipes for iMEP Bronze, SharePoint Gold, HR enrichment, and cross-channel joins.",
    "DataGlossary.ImepBronze": "iMEP bronze tables: TblEmail, TblEmailReceiverStatus, TblAnalyticsLink, TblEmailLinks, TblEvent.",
    "DataGlossary.ImepGold":   "iMEP gold tables: Final (520M consumption endpoint) plus tier-1/2/3 aggregates.",
    "DataGlossary.SharePointBronze": "SharePoint bronze tables: Pages (cross-channel dimension), Pageviews, Customevents.",
    "DataGlossary.SharePointGold":   "SharePoint gold metrics: PbiDbInteractionsMetrics (84M master fact) plus specialized grain-specific tables.",
    "DataGlossary.Hr":               "HR dimension tables: TblHrEmployee (master + GPN bridge), TblHrCostcenter (Region/Division), TblHrUser (UbsId).",
}


def build_fitnesse_path(parent_path: str, root_name: str, sub: str) -> str:
    parts = [parent_path, root_name]
    if sub:
        parts.append(sub)
    return ".".join(parts)


def build_url(base_url: str, page_path: str) -> str:
    return f"{base_url.rstrip('/')}/{page_path}"


def post_page(url: str, content: str, timeout: int = 30) -> tuple[int, str]:
    data = urllib.parse.urlencode({
        "responder": "saveData",
        "pageContent": content,
        "helpText": "",
        "save": "Save",
    }).encode("utf-8")
    req = urllib.request.Request(url, data=data, method="POST", headers={
        "Content-Type": "application/x-www-form-urlencoded",
        "User-Agent": "fitnesse-upload.py",
    })
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            body = resp.read(4096).decode("utf-8", errors="replace")
            return resp.status, body
    except urllib.error.HTTPError as e:
        body = e.read(4096).decode("utf-8", errors="replace") if e.fp else ""
        return e.code, body


def read_content(pages_dir: Path, source: str | None, sub_path: str) -> str:
    if source:
        full = pages_dir / source
        if not full.is_file():
            raise FileNotFoundError(f"Expected page content at {full}")
        return full.read_text(encoding="utf-8")
    # container stub
    name = sub_path.split(".")[-1] if sub_path else "MultiChannelDataModel"
    hint = SUBSECTION_HINT.get(sub_path, "See subpages for details.")
    return CONTAINER_STUB.format(name=name, subsection_hint=hint)


def main() -> int:
    parser = argparse.ArgumentParser(description="Upload Cross-Channel KB to FitNesse.")
    parser.add_argument("--base-url", default=DEFAULT_BASE_URL,
                        help="FitNesse base URL, e.g. http://host:port. Defaults to $FITNESSE_URL.")
    parser.add_argument("--parent-path", default=DEFAULT_PARENT_PATH,
                        help="Dotted FitNesse path of the existing parent page. Defaults to $FITNESSE_PARENT_PATH.")
    parser.add_argument("--root-name", default=DEFAULT_ROOT_NAME,
                        help="Name of the root page that will be created under parent-path.")
    parser.add_argument("--pages-dir", default="docs/fitnesse/pages",
                        help="Local directory holding the .txt source files.")
    parser.add_argument("--dry-run", action="store_true",
                        help="Print the plan without sending any POSTs.")
    parser.add_argument("--delay", type=float, default=0.3,
                        help="Seconds to sleep between requests (default: 0.3).")
    parser.add_argument("--stop-on-error", action="store_true",
                        help="Abort on the first HTTP error (default: continue).")
    args = parser.parse_args()

    if not args.base_url:
        print("ERROR: --base-url not provided (and FITNESSE_URL env var is empty).", file=sys.stderr)
        return 2
    if not args.parent_path:
        print("ERROR: --parent-path not provided (and FITNESSE_PARENT_PATH env var is empty).", file=sys.stderr)
        return 2

    pages_dir = Path(args.pages_dir)
    if not pages_dir.is_dir():
        print(f"ERROR: pages directory not found: {pages_dir}", file=sys.stderr)
        return 2

    total = len(PLAN)
    print(f"Plan: {total} pages under {args.parent_path}.{args.root_name}")
    print(f"Base URL: {args.base_url}")
    print(f"Source:   {pages_dir.resolve()}")
    print(f"Mode:     {'DRY RUN' if args.dry_run else 'LIVE UPLOAD'}")
    print()

    ok, failed = 0, 0
    for idx, (sub, source) in enumerate(PLAN, start=1):
        page_path = build_fitnesse_path(args.parent_path, args.root_name, sub)
        url = build_url(args.base_url, page_path)
        try:
            content = read_content(pages_dir, source, sub)
        except FileNotFoundError as e:
            print(f"[{idx:2}/{total}] SKIP  {page_path}  ({e})")
            failed += 1
            if args.stop_on_error:
                return 3
            continue

        label = f"[{idx:2}/{total}] {'DRY  ' if args.dry_run else 'POST '} {page_path}"
        if args.dry_run:
            print(f"{label}  ({len(content):>6} chars, from {source or 'stub'})")
            ok += 1
            continue

        status, body = post_page(url, content)
        if 200 <= status < 400:
            print(f"{label}  -> HTTP {status}  OK ({len(content)} chars)")
            ok += 1
        else:
            print(f"{label}  -> HTTP {status}  FAIL")
            print("    ", body[:500].replace("\n", " "))
            failed += 1
            if args.stop_on_error:
                return 4
        time.sleep(args.delay)

    print()
    print(f"Done: {ok} ok, {failed} failed (of {total}).")
    return 0 if failed == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
