#!/usr/bin/env python3
"""
Back up a FitNesse subtree as a timestamped ZIP snapshot.

Calls FitNesse's built-in ?responder=zip endpoint, which returns the whole
subtree (target page + all descendants) as a ZIP archive containing raw wiki
source plus per-page version history. The archive is written to
docs/fitnesse/backup/<YYYYMMDD-HHMM>-<last-path-segment>.zip.

Defaults for --base-url and --parent-path come from fitnesse_upload.py, so
this script reuses whatever you already configured there (env vars or
hard-coded values). The parent-path is expected to already point at the
subtree you want to back up (e.g. FrontPage.EmployeeEngagement.CPlanGICTrackingCLARITYDashboard).

No external dependencies — pure Python stdlib. Works on Windows (Anaconda
Prompt), macOS and Linux. Runs from any working directory.

Usage:
    # Uses defaults from fitnesse_upload.py (env vars / hard-coded)
    python scripts/fitnesse_backup_zip.py

    # Override for a one-off backup of a different subtree
    python scripts/fitnesse_backup_zip.py ^
        --base-url http://<host>:<port> ^
        --parent-path FrontPage.Some.Other.Subtree

    # Skip the pre-flight HEAD check (go straight to download)
    python scripts/fitnesse_backup_zip.py --skip-head-check
"""

from __future__ import annotations

import argparse
import datetime
import sys
import urllib.error
import urllib.request
from pathlib import Path

SCRIPT_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(SCRIPT_DIR))
from fitnesse_upload import (  # noqa: E402
    DEFAULT_BASE_URL,
    DEFAULT_PARENT_PATH,
    DEFAULT_PAGES_DIR,
)

# Backup lives as a sibling of the pages directory configured in fitnesse_upload.py.
DEFAULT_BACKUP_ROOT = Path(DEFAULT_PAGES_DIR).parent / "backup"


def head_check(url: str, timeout: int = 15) -> tuple[int, str]:
    """HEAD the URL; return (status_code, content_type). Status 0 means network error."""
    req = urllib.request.Request(url, method="HEAD",
                                 headers={"User-Agent": "fitnesse-backup-zip.py"})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return resp.status, resp.headers.get("Content-Type", "")
    except urllib.error.HTTPError as e:
        return e.code, ""
    except urllib.error.URLError as e:
        print(f"ERROR: cannot reach {url}", file=sys.stderr)
        print(f"       {e}", file=sys.stderr)
        return 0, ""


def download(url: str, target: Path, timeout: int = 180) -> int:
    """Stream-download URL to target file. Return bytes written."""
    req = urllib.request.Request(url, headers={"User-Agent": "fitnesse-backup-zip.py"})
    written = 0
    with urllib.request.urlopen(req, timeout=timeout) as resp, target.open("wb") as out:
        while True:
            chunk = resp.read(65536)
            if not chunk:
                break
            out.write(chunk)
            written += len(chunk)
    return written


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Back up a FitNesse subtree as a timestamped ZIP.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("--base-url", default=DEFAULT_BASE_URL,
                        help="FitNesse base URL. Default: value from fitnesse_upload.py "
                             "(env var FITNESSE_URL).")
    parser.add_argument("--parent-path", default=DEFAULT_PARENT_PATH,
                        help="Dotted path of the subtree to back up. Default: value from "
                             "fitnesse_upload.py (env var FITNESSE_PARENT_PATH).")
    parser.add_argument("--backup-root", default=str(DEFAULT_BACKUP_ROOT),
                        help=f"Folder where the ZIP is written (default: {DEFAULT_BACKUP_ROOT}).")
    parser.add_argument("--skip-head-check", action="store_true",
                        help="Skip the pre-flight HEAD request and go straight to download.")
    args = parser.parse_args()

    if not args.base_url:
        print("ERROR: --base-url not provided and fitnesse_upload.DEFAULT_BASE_URL is empty "
              "(set $FITNESSE_URL).", file=sys.stderr)
        return 2
    if not args.parent_path:
        print("ERROR: --parent-path not provided and fitnesse_upload.DEFAULT_PARENT_PATH is empty "
              "(set $FITNESSE_PARENT_PATH).", file=sys.stderr)
        return 2

    url = f"{args.base_url.rstrip('/')}/{args.parent_path}?responder=zip"
    last_segment = args.parent_path.rsplit(".", 1)[-1]

    ts = datetime.datetime.now().strftime("%Y%m%d-%H%M")
    backup_root = Path(args.backup_root)
    backup_root.mkdir(parents=True, exist_ok=True)
    out_file = backup_root / f"{ts}-{last_segment}.zip"

    print(f"URL:    {url}")
    print(f"Target: {out_file.resolve()}")
    print()

    if not args.skip_head_check:
        print("Pre-flight HEAD check...")
        status, ctype = head_check(url)
        if status == 0:
            return 3  # network error already reported by head_check()
        if status != 200:
            print(f"ERROR: HEAD returned HTTP {status}.", file=sys.stderr)
            print("       Double-check --parent-path / --page, or run with --skip-head-check "
                  "to try anyway.", file=sys.stderr)
            return 3
        if "zip" not in ctype.lower() and "octet-stream" not in ctype.lower():
            print(f"WARNING: unexpected Content-Type '{ctype}' (expected zip/octet-stream).",
                  file=sys.stderr)
        print(f"  OK  HTTP 200  Content-Type: {ctype}")
        print()

    print("Downloading...")
    try:
        size = download(url, out_file)
    except urllib.error.HTTPError as e:
        print(f"ERROR: HTTP {e.code}: {e.reason}", file=sys.stderr)
        if e.fp is not None:
            try:
                body = e.read().decode("utf-8", errors="replace")
            except Exception:  # noqa: BLE001
                body = ""
            if body.strip():
                print("Response body (first 1500 chars):", file=sys.stderr)
                print(body[:1500], file=sys.stderr)
        # Diagnostic: try the page itself without ?responder=zip to prove the path exists
        plain_url = f"{args.base_url.rstrip('/')}/{args.parent_path}"
        print(f"\nDiagnostic: try the plain page URL to confirm the path is reachable:",
              file=sys.stderr)
        print(f"  curl -I \"{plain_url}\"", file=sys.stderr)
        print(f"  If that returns 200, the ZIP responder is disabled or named differently "
              "on this FitNesse instance.", file=sys.stderr)
        # Remove the empty/partial file
        if out_file.exists():
            try:
                out_file.unlink()
            except OSError:
                pass
        return 4
    except urllib.error.URLError as e:
        print(f"ERROR: {e}", file=sys.stderr)
        return 4

    print(f"  OK  {size:,} bytes written")
    print()
    print(f"Done: {out_file}")
    print()
    print("Inspect the archive contents:")
    print(f'  python -m zipfile -l "{out_file}"')
    print("Extract (if you want the raw files):")
    print(f'  python -m zipfile -e "{out_file}" "{out_file.with_suffix("")}/"')
    return 0


if __name__ == "__main__":
    sys.exit(main())
