"""
Flatten AppInsights pageViews export data.

Reads a CSV or XLSX file exported from Azure AppInsights,
flattens the nested customDimensions JSON column, and writes
a clean Excel file with all fields as flat columns.

Usage:
    python flatten_appinsights.py input_file.csv
    python flatten_appinsights.py input_file.xlsx
    python flatten_appinsights.py input_file.csv -o output.xlsx
"""

import argparse
import json
import sys
from pathlib import Path

import pandas as pd


def parse_custom_dimensions(value):
    """Parse a customDimensions JSON string into a flat dict.

    Handles the nested CustomProps structure found in AppInsights pageViews.
    Returns an empty dict if parsing fails.
    """
    if pd.isna(value) or not isinstance(value, str) or not value.strip():
        return {}

    try:
        parsed = json.loads(value)
    except json.JSONDecodeError:
        # Try fixing common export quirks: double-encoded JSON
        try:
            parsed = json.loads(json.loads(value))
        except (json.JSONDecodeError, TypeError):
            return {}

    flat = {}

    # If there's a nested CustomProps object, extract its fields
    if isinstance(parsed, dict):
        custom_props = parsed.pop("CustomProps", None)
        # Remaining top-level keys in customDimensions
        for key, val in parsed.items():
            flat[key] = val
        # Flatten CustomProps
        if isinstance(custom_props, dict):
            for key, val in custom_props.items():
                flat[f"cp_{key}"] = val
        elif isinstance(custom_props, str):
            # CustomProps might itself be a JSON string
            try:
                cp_parsed = json.loads(custom_props)
                if isinstance(cp_parsed, dict):
                    for key, val in cp_parsed.items():
                        flat[f"cp_{key}"] = val
            except json.JSONDecodeError:
                flat["cp_raw"] = custom_props

    return flat


def read_input(file_path: Path) -> pd.DataFrame:
    """Read CSV or XLSX input file."""
    suffix = file_path.suffix.lower()
    if suffix == ".csv":
        return pd.read_csv(file_path)
    elif suffix in (".xlsx", ".xls"):
        return pd.read_excel(file_path)
    else:
        raise ValueError(f"Unsupported file format: {suffix}. Use .csv or .xlsx")


def flatten_appinsights(df: pd.DataFrame) -> pd.DataFrame:
    """Flatten an AppInsights pageViews DataFrame.

    Expands the customDimensions JSON column into separate columns
    prefixed with cp_ for CustomProps fields.
    """
    # Find the customDimensions column (case-insensitive)
    cd_col = None
    for col in df.columns:
        if col.lower().replace(" ", "") == "customdimensions":
            cd_col = col
            break

    if cd_col is None:
        print("Warning: No 'customDimensions' column found. Returning data as-is.")
        return df

    # Parse each row's customDimensions
    expanded = df[cd_col].apply(parse_custom_dimensions)
    expanded_df = pd.json_normalize(expanded)

    # Drop original customDimensions and join the expanded columns
    result = df.drop(columns=[cd_col]).reset_index(drop=True)
    result = pd.concat([result, expanded_df.reset_index(drop=True)], axis=1)

    return result


def main():
    parser = argparse.ArgumentParser(
        description="Flatten AppInsights pageViews export to Excel"
    )
    parser.add_argument("input", help="Path to CSV or XLSX file from AppInsights")
    parser.add_argument(
        "-o", "--output",
        help="Output Excel file path (default: <input>_flat.xlsx)",
    )
    args = parser.parse_args()

    input_path = Path(args.input)
    if not input_path.exists():
        print(f"Error: File not found: {input_path}")
        sys.exit(1)

    output_path = Path(args.output) if args.output else input_path.with_name(
        f"{input_path.stem}_flat.xlsx"
    )

    print(f"Reading: {input_path}")
    df = read_input(input_path)
    print(f"  Rows: {len(df)}, Columns: {df.columns.tolist()}")

    print("Flattening customDimensions...")
    flat_df = flatten_appinsights(df)
    print(f"  Result: {len(flat_df)} rows x {len(flat_df.columns)} columns")

    # Show a summary of the new columns from customDimensions
    cp_cols = [c for c in flat_df.columns if c.startswith("cp_")]
    if cp_cols:
        print(f"  CustomProps fields extracted ({len(cp_cols)}): {cp_cols}")

    print(f"Writing: {output_path}")
    flat_df.to_excel(output_path, index=False, engine="openpyxl")
    print("Done.")


if __name__ == "__main__":
    main()
