"""
Rename AppInsights CSV exports in input/ by their contained time window.

The AppInsights export has to be split into several time-window chunks
(row-count limits in the portal), so the downloaded files carry generic
names like "query_data.csv". This script inspects every CSV in the input
folder, determines the min and max timestamp inside the file, and renames
it to:

    Digital_<minDate>_<maxDate>.csv
    e.g. Digital_20250601_20250630.csv

Timestamps are taken from the "timestamp [UTC]" column (Azure Portal CSV
export) or "timestamp" as fallback, and formatted as YYYYMMDD in UTC.

Usage:
    # Rename all CSVs in input/ (default)
    python scripts/rename_exports_by_timerange.py

    # Custom folder
    python scripts/rename_exports_by_timerange.py path/to/folder

    # Dry run — show what would be renamed without touching anything
    python scripts/rename_exports_by_timerange.py --dry-run

Files already matching the Digital_<min>_<max>.csv pattern are skipped,
so the script is safe to re-run after dropping new chunks into input/.
"""

import argparse
import re
import sys
from pathlib import Path

import pandas as pd

TIMESTAMP_COLUMNS = ["timestamp [UTC]", "timestamp"]
RENAMED_PATTERN = re.compile(r"^Digital_\d{8}_\d{8}\.csv$")
FMT = "%Y%m%d"


def find_timestamp_column(path: Path) -> str | None:
    header = pd.read_csv(path, nrows=0)
    for col in TIMESTAMP_COLUMNS:
        if col in header.columns:
            return col
    return None


def rename_file(path: Path, dry_run: bool) -> bool:
    if RENAMED_PATTERN.match(path.name):
        print(f"  SKIP {path.name} (already renamed)")
        return False

    col = find_timestamp_column(path)
    if col is None:
        print(f"  SKIP {path.name} (no timestamp column, expected one of {TIMESTAMP_COLUMNS})")
        return False

    ts = pd.to_datetime(
        pd.read_csv(path, usecols=[col])[col], utc=True, format="mixed", errors="coerce"
    ).dropna()
    if ts.empty:
        print(f"  SKIP {path.name} (no parseable timestamps in '{col}')")
        return False

    new_name = f"Digital_{ts.min().strftime(FMT)}_{ts.max().strftime(FMT)}.csv"
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
        "folder", nargs="?", default="input",
        help="Folder containing the exported CSV chunks (default: input/)",
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

    csv_files = sorted(folder.glob("*.csv"))
    if not csv_files:
        print(f"No CSV files found in {folder}/")
        return 0

    print(f"Scanning {len(csv_files)} CSV file(s) in {folder}/")
    renamed = sum(rename_file(f, args.dry_run) for f in csv_files)
    print(f"Done: {renamed} file(s) {'would be ' if args.dry_run else ''}renamed.")
    return 0


if __name__ == "__main__":
    main()
