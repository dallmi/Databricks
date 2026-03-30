"""
Flatten AppInsights pageViews export and transform to CDM-based star schema.

Reads a CSV or XLSX file exported from Azure AppInsights,
flattens the nested customDimensions JSON column, and produces:
  1. A flat denormalized table (all relevant fields)
  2. A star schema with fact_page_view, dim_page, and dim_date sheets

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


# -- Column mappings: AppInsights raw -> CDM-inspired names --

FACT_COLUMNS = {
    "id": "view_id",
    "timestamp [UTC]": "timestamp",
    "user_Id": "user_id",
    "session_Id": "session_id",
    "duration": "duration_ms",
    "cp_refUri": "referrer_url",
    "client_OS": "client_os",
    "client_Browser": "client_browser",
    "client_CountryOrRegion": "client_country",
    "cp_PageId": "page_id",
    "cp_Email": "email",
    "cp_GPN": "gpn",
}

DIM_PAGE_COLUMNS = {
    "cp_PageId": "page_id",
    "cp_PageName": "page_name",
    "cp_PageURL": "page_url",
    "cp_SiteID": "site_id",
    "cp_SiteName": "site_name",
    "cp_ContentOwner": "content_owner",
    "cp_ContentType": "content_type",
    "cp_Theme": "theme",
    "cp_Topic": "topic",
    "cp_TargetRegion": "target_region",
    "cp_TargetOrganisation": "target_org",
    "cp_PageStatus": "page_status",
    "cp_PublishingDate": "publishing_date",
}

# Columns to drop entirely (no analytical value)
DROP_COLUMNS = [
    "appId", "iKey", "sdkVersion", "itemCount", "itemType",
    "operation_Id", "operation_ParentId", "operation_Name",
    "client_IP", "client_Type", "client_Model",
    "performanceBucket", "name", "url",
    "client_City", "client_StateOrProvince",
    "cp_CommsTrackingID", "cp_NewsCategory",
]


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
        try:
            parsed = json.loads(json.loads(value))
        except (json.JSONDecodeError, TypeError):
            return {}

    flat = {}

    if isinstance(parsed, dict):
        custom_props = parsed.pop("CustomProps", None)
        for key, val in parsed.items():
            flat[key] = val
        if isinstance(custom_props, dict):
            for key, val in custom_props.items():
                flat[f"cp_{key}"] = val
        elif isinstance(custom_props, str):
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
    cd_col = None
    for col in df.columns:
        if col.lower().replace(" ", "") == "customdimensions":
            cd_col = col
            break

    if cd_col is None:
        print("Warning: No 'customDimensions' column found. Returning data as-is.")
        return df

    expanded = df[cd_col].apply(parse_custom_dimensions)
    expanded_df = pd.json_normalize(expanded)

    result = df.drop(columns=[cd_col]).reset_index(drop=True)
    result = pd.concat([result, expanded_df.reset_index(drop=True)], axis=1)

    return result


def build_flat_table(df: pd.DataFrame) -> pd.DataFrame:
    """Build a single denormalized table with CDM-friendly column names.

    Keeps all relevant fields, drops noise columns, renames to snake_case.
    """
    flat = df.copy()

    # Drop columns that exist in the dataframe
    cols_to_drop = [c for c in DROP_COLUMNS if c in flat.columns]
    flat = flat.drop(columns=cols_to_drop)

    # Rename columns that exist
    rename_map = {}
    rename_map.update(FACT_COLUMNS)
    rename_map.update(DIM_PAGE_COLUMNS)
    existing_renames = {k: v for k, v in rename_map.items() if k in flat.columns}
    flat = flat.rename(columns=existing_renames)

    # Parse timestamp
    if "timestamp" in flat.columns:
        flat["timestamp"] = pd.to_datetime(flat["timestamp"], errors="coerce")

    # Parse publishing_date
    if "publishing_date" in flat.columns:
        flat["publishing_date"] = pd.to_datetime(
            flat["publishing_date"], errors="coerce"
        )

    return flat


def build_fact_page_view(flat: pd.DataFrame) -> pd.DataFrame:
    """Extract the fact table from the flat denormalized table."""
    fact_cols = [
        "view_id", "timestamp", "page_id", "user_id", "session_id",
        "duration_ms", "referrer_url", "client_os", "client_browser",
        "client_country", "email", "gpn",
    ]
    available = [c for c in fact_cols if c in flat.columns]
    return flat[available].copy()


def build_dim_page(flat: pd.DataFrame) -> pd.DataFrame:
    """Extract the page dimension from the flat table (deduplicated)."""
    dim_cols = [
        "page_id", "page_name", "page_url", "site_id", "site_name",
        "content_owner", "content_type", "theme", "topic",
        "target_region", "target_org", "page_status", "publishing_date",
    ]
    available = [c for c in dim_cols if c in flat.columns]
    if not available or "page_id" not in flat.columns:
        return pd.DataFrame()

    dim = flat[available].drop_duplicates(subset=["page_id"]).reset_index(drop=True)
    return dim


def build_dim_date(flat: pd.DataFrame) -> pd.DataFrame:
    """Build a date dimension from the timestamps in the fact data."""
    if "timestamp" not in flat.columns:
        return pd.DataFrame()

    dates = flat["timestamp"].dropna().dt.normalize().drop_duplicates().sort_values()
    dim = pd.DataFrame({"date": dates}).reset_index(drop=True)
    dim["date_key"] = dim["date"].dt.strftime("%Y%m%d").astype(int)
    dim["year"] = dim["date"].dt.year
    dim["month"] = dim["date"].dt.month
    dim["month_name"] = dim["date"].dt.strftime("%B")
    dim["week"] = dim["date"].dt.isocalendar().week.astype(int)
    dim["day_of_week"] = dim["date"].dt.strftime("%A")
    dim["quarter"] = dim["date"].dt.quarter

    return dim[["date_key", "date", "year", "quarter", "month", "month_name",
                "week", "day_of_week"]]


def main():
    parser = argparse.ArgumentParser(
        description="Flatten AppInsights pageViews export to CDM star schema"
    )
    parser.add_argument("input", help="Path to CSV or XLSX file from AppInsights")
    parser.add_argument(
        "-o", "--output",
        help="Output Excel file path (default: <input>_cdm.xlsx)",
    )
    args = parser.parse_args()

    input_path = Path(args.input)
    if not input_path.exists():
        print(f"Error: File not found: {input_path}")
        sys.exit(1)

    output_path = Path(args.output) if args.output else input_path.with_name(
        f"{input_path.stem}_cdm.xlsx"
    )

    # Step 1: Read raw data
    print(f"Reading: {input_path}")
    raw_df = read_input(input_path)
    print(f"  Raw: {len(raw_df)} rows, {len(raw_df.columns)} columns")

    # Step 2: Flatten customDimensions JSON
    print("Flattening customDimensions...")
    expanded_df = flatten_appinsights(raw_df)
    cp_cols = [c for c in expanded_df.columns if c.startswith("cp_")]
    print(f"  Expanded: {len(expanded_df.columns)} columns ({len(cp_cols)} from CustomProps)")

    # Step 3: Build CDM-friendly flat table
    print("Building CDM flat table...")
    flat = build_flat_table(expanded_df)

    # Step 4: Build star schema
    print("Building star schema...")
    fact = build_fact_page_view(flat)
    dim_page = build_dim_page(flat)
    dim_date = build_dim_date(flat)

    print(f"  fact_page_view: {len(fact)} rows x {len(fact.columns)} cols")
    print(f"  dim_page:       {len(dim_page)} rows x {len(dim_page.columns)} cols")
    print(f"  dim_date:       {len(dim_date)} rows x {len(dim_date.columns)} cols")

    # Step 5: Write Excel with multiple sheets
    print(f"Writing: {output_path}")
    with pd.ExcelWriter(output_path, engine="openpyxl") as writer:
        flat.to_excel(writer, sheet_name="flat_all", index=False)
        fact.to_excel(writer, sheet_name="fact_page_view", index=False)
        if not dim_page.empty:
            dim_page.to_excel(writer, sheet_name="dim_page", index=False)
        if not dim_date.empty:
            dim_date.to_excel(writer, sheet_name="dim_date", index=False)

    print("Done. Sheets: flat_all, fact_page_view, dim_page, dim_date")


if __name__ == "__main__":
    main()
