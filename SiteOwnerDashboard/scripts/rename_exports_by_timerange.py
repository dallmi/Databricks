"""
Rename AppInsights exports (CSV or XLSX) in input/ by their contained time window.

The AppInsights export has to be split into several time-window chunks
(row-count limits in the portal), so the downloaded files carry generic
names like "query_data.csv". This script inspects every CSV and XLSX file
directly in the input folder (subfolders are not crawled), determines the
min and max timestamp inside the file, and renames it to:

    Digital_<minDate>_<maxDate>.<original extension>
    e.g. Digital_20250601_20250630.csv / Digital_20250601_20250630.xlsx

Timestamps are taken from the "timestamp [UTC]" column (Azure Portal CSV
export) or "timestamp" as fallback, and formatted as YYYYMMDD in UTC.
For XLSX files only the first worksheet is read.

Usage (from SiteOwnerDashboard/):
    # Rename all CSV/XLSX files in input/ (default)
    python scripts/rename_exports_by_timerange.py

    # Custom folder
    python scripts/rename_exports_by_timerange.py path/to/folder

    # Dry run — show what would be renamed without touching anything
    python scripts/rename_exports_by_timerange.py --dry-run

Without an explicit folder argument the script always targets
SiteOwnerDashboard/input/, regardless of the current working directory.

Files already matching the Digital_<min>_<max> pattern are skipped,
so the script is safe to re-run after dropping new chunks into input/.
"""

import argparse
import csv
import re
import sys
from pathlib import Path

import pandas as pd

TIMESTAMP_COLUMNS = ["timestamp [utc]", "timestamp"]  # matched case-insensitively
SUPPORTED_SUFFIXES = (".csv", ".xlsx")
RENAMED_PATTERN = re.compile(r"^Digital_\d{8}_\d{8}\.(csv|xlsx)$", re.IGNORECASE)
FMT = "%Y%m%d"
DEFAULT_INPUT_DIR = Path(__file__).resolve().parent.parent / "input"


def sniff_delimiter(path: Path) -> str:
    # Azure/Excel exports vary between comma and semicolon; sniff instead of assuming.
    with open(path, encoding="utf-8-sig", newline="") as f:
        sample = f.read(64 * 1024)
    try:
        return csv.Sniffer().sniff(sample, delimiters=",;\t|").delimiter
    except csv.Error:
        return ","


def read_header(path: Path, sep: str | None) -> pd.DataFrame:
    if path.suffix.lower() == ".xlsx":
        return pd.read_excel(path, nrows=0)
    # encoding="utf-8-sig" strips the BOM that Azure exports prepend
    # (otherwise the first column is named "﻿timestamp" and never matches).
    return pd.read_csv(path, nrows=0, sep=sep, encoding="utf-8-sig")


def read_timestamp_values(path: Path, col: str, sep: str | None) -> pd.Series:
    if path.suffix.lower() == ".xlsx":
        return pd.read_excel(path, usecols=[col])[col]
    return pd.read_csv(path, usecols=[col], sep=sep, encoding="utf-8-sig")[col]


def find_timestamp_column(path: Path, sep: str | None) -> str | None:
    for col in read_header(path, sep).columns:
        if str(col).strip().lower() in TIMESTAMP_COLUMNS:
            return col
    return None


def rename_file(path: Path, dry_run: bool) -> bool:
    if RENAMED_PATTERN.match(path.name):
        print(f"  SKIP {path.name} (already renamed)")
        return False

    sep = sniff_delimiter(path) if path.suffix.lower() == ".csv" else None
    col = find_timestamp_column(path, sep)
    if col is None:
        print(f"  SKIP {path.name} (no timestamp column, expected one of {TIMESTAMP_COLUMNS})")
        return False

    ts = pd.to_datetime(
        read_timestamp_values(path, col, sep),
        utc=True, format="mixed", errors="coerce",
    ).dropna()
    if ts.empty:
        print(f"  SKIP {path.name} (no parseable timestamps in '{col}')")
        return False

    new_name = f"Digital_{ts.min().strftime(FMT)}_{ts.max().strftime(FMT)}{path.suffix.lower()}"
    if new_name == path.name:
        print(f"  OK   {path.name} (name already correct)")
        return False

    target = path.with_name(new_name)
    if target.exists():
        print(f"  SKIP {path.name} -> {new_name} (target exists — same window exported twice?)")
        return False

    print(f"  {'WOULD RENAME' if dry_run else 'RENAME'} {path.name} -> {new_name}")
    if not dry_run:
        path.rename(target)
    return True


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.strip().splitlines()[0])
    parser.add_argument(
        "folder", nargs="?", default=DEFAULT_INPUT_DIR,
        help="Folder containing the exported CSV/XLSX chunks (default: SiteOwnerDashboard/input/)",
    )
    parser.add_argument(
        "--dry-run", action="store_true",
        help="Show planned renames without changing any files",
    )
    args = parser.parse_args()

    folder = Path(args.folder)
    if not folder.is_dir():
        print(f"Error: {folder} is not a directory", file=sys.stderr)
        return 1

    # Top-level files only — subfolders are deliberately not crawled.
    files = sorted(
        p for p in folder.iterdir()
        if p.is_file() and p.suffix.lower() in SUPPORTED_SUFFIXES
    )
    if not files:
        print(f"No CSV/XLSX files found in {folder}/")
        return 0

    print(f"Scanning {len(files)} file(s) in {folder}/")
    renamed = sum(rename_file(f, args.dry_run) for f in files)
    print(f"Done: {renamed} file(s) {'would be ' if args.dry_run else ''}renamed.")
    return 0


if __name__ == "__main__":
    main()
