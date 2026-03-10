"""
Extract Metadata Schema Pack via XMLA Endpoint

Extracts Power BI semantic model metadata (tables, columns, measures,
relationships) using DAX INFO.VIEW.* functions and the MDSCHEMA_MEASURES
DMV through the XMLA endpoint (ADOMD.NET via pyadomd).

Produces a denormalized schema_pack dict compatible with
format_schema_for_prompt and split_schema.

Usage:
    from extract_schema import MetadataExtractor
    from execute_dax import PowerBIXmlaClient

    client = PowerBIXmlaClient(workspace_name, database_name,
                                client_id=..., tenant_id=..., client_secret=...)
    extractor = MetadataExtractor(client)
    schema_pack = extractor.extract_all()
"""

import re
from typing import Any, Dict, List

from execute_dax import PowerBIXmlaClient


# ============================================================
# EXTERNALMEASURE regex
# ============================================================
# Parses composite / DirectQuery measure wrappers:
#   EXTERNALMEASURE("Total Transactions", INTEGER, "DirectQuery to AS - service_model")
_EXTERNALMEASURE_RE = re.compile(
    r'EXTERNALMEASURE\("([^"]+)",\s*(\w+),\s*"([^"]+)"\)'
)


# ============================================================
# Data Type Helpers
# ============================================================

TYPE_MAP = {
    9: "string",
    10: "datetime",
    11: "date",
    2: "int64",
    3: "decimal",
    4: "double",
    6: "boolean",
}


def map_type(value: Any) -> str:
    """Convert Power BI data type code to human-readable string."""
    if value is None:
        return "unknown"
    if isinstance(value, (int, float)):
        int_val = int(value)
        if int_val in TYPE_MAP:
            return TYPE_MAP[int_val]
    return str(value).lower()


def as_bool(value: Any) -> bool:
    """Convert value to boolean."""
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return value != 0
    if isinstance(value, str):
        return value.lower() in ("true", "1", "yes")
    return False


# ============================================================
# Schema Extraction via DAX INFO Functions
# ============================================================

class MetadataExtractor:
    """
    Extracts Power BI semantic model metadata using DAX INFO.VIEW functions
    via the Execute Queries REST API.

    Uses INFO.VIEW.* (not INFO.*) because:
      - INFO.VIEW.* work via the REST Execute Queries API on all compat levels
      - INFO.VIEW.* pre-resolve IDs to friendly names (table name, column name)
      - INFO.* (without VIEW) require XMLA admin permissions / higher compat level

    Supplements with MDSCHEMA_MEASURES DMV for expression/folder/format data
    that INFO.VIEW.MEASURES may return as null on composite/DirectQuery models.
    """

    # Map INFO.VIEW.COLUMNS DataType strings to our standard type names
    DATATYPE_MAP = {
        "String": "string",
        "Int64": "int64",
        "Double": "double",
        "Decimal": "decimal",
        "DateTime": "datetime",
        "Date": "date",
        "Boolean": "boolean",
        "Binary": "binary",
    }

    def __init__(self, client: PowerBIXmlaClient):
        self.client = client

    # ----------------------------------------------------------
    # Individual fetch methods
    # ----------------------------------------------------------

    def fetch_tables(self) -> List[Dict[str, Any]]:
        """Fetch table metadata via INFO.VIEW.TABLES()."""
        print("   Fetching tables (INFO.VIEW.TABLES)...")
        return self.client.execute_dax("EVALUATE INFO.VIEW.TABLES()")

    def fetch_columns(self) -> List[Dict[str, Any]]:
        """Fetch column metadata via INFO.VIEW.COLUMNS()."""
        print("   Fetching columns (INFO.VIEW.COLUMNS)...")
        return self.client.execute_dax("EVALUATE INFO.VIEW.COLUMNS()")

    def fetch_measures(self) -> List[Dict[str, Any]]:
        """Fetch measure metadata via INFO.VIEW.MEASURES()."""
        print("   Fetching measures (INFO.VIEW.MEASURES)...")
        return self.client.execute_dax("EVALUATE INFO.VIEW.MEASURES()")

    def fetch_measures_dmv(self) -> Dict[str, Dict[str, str]]:
        """
        Fetch measure expressions, folders, and format strings from MDSCHEMA_MEASURES DMV.

        INFO.VIEW.MEASURES returns null for Expression and FormatString on
        composite/DirectQuery models. The MDSCHEMA_MEASURES DMV exposes
        the expression text, display folders, and format strings.

        Returns:
            dict: measure_name -> {"expression": ..., "folder": ..., "formatString": ...}
        """
        print("   Supplementing measures (MDSCHEMA_MEASURES DMV)...")
        try:
            rows = self.client.execute_dax(
                "SELECT MEASURE_NAME, EXPRESSION, MEASURE_DISPLAY_FOLDER, "
                "DEFAULT_FORMAT_STRING "
                "FROM $SYSTEM.MDSCHEMA_MEASURES"
            )
            dmv_map = {}
            for row in rows:
                name = row.get("MEASURE_NAME", "")
                if name:
                    dmv_map[name] = {
                        "expression": row.get("EXPRESSION") or "",
                        "folder": row.get("MEASURE_DISPLAY_FOLDER") or "",
                        "formatString": row.get("DEFAULT_FORMAT_STRING") or "",
                    }
            return dmv_map
        except RuntimeError as e:
            print(f"   ⚠️  DMV query failed (non-fatal): {e}")
            return {}

    def fetch_relationships(self) -> List[Dict[str, Any]]:
        """Fetch relationship metadata via INFO.VIEW.RELATIONSHIPS()."""
        print("   Fetching relationships (INFO.VIEW.RELATIONSHIPS)...")
        return self.client.execute_dax("EVALUATE INFO.VIEW.RELATIONSHIPS()")

    # ----------------------------------------------------------
    # Main extraction method
    # ----------------------------------------------------------

    def extract_all(self) -> Dict[str, Any]:
        """
        Extract all metadata and build a schema pack compatible with
        the existing format_schema_for_prompt and split_schema tools.

        INFO.VIEW.* functions return friendly names directly (e.g., Table name
        instead of TableID), so no ID-based lookups are needed.

        Returns:
            dict: Schema pack with model.tables, model.measures,
                  model.relationships, model.linguistic_metadata
        """
        # Fetch raw metadata via INFO.VIEW functions
        tables_raw = self.fetch_tables()
        columns_raw = self.fetch_columns()
        measures_raw = self.fetch_measures()
        relationships_raw = self.fetch_relationships()

        # Supplement measures from MDSCHEMA_MEASURES DMV
        # (INFO.VIEW.MEASURES returns null Expression on composite/DQ models)
        measures_dmv = self.fetch_measures_dmv()

        # --- Process tables ---
        table_map = {}  # table_name -> table dict
        for row in tables_raw:
            name = row.get("Name", "")
            table_map[name] = {
                "name": name,
                "hidden": as_bool(row.get("IsHidden", False)),
                "description": row.get("Description") or "",
                "columns": [],
            }

        # --- Process columns ---
        for row in columns_raw:
            table_name = row.get("Table", "")
            col_type_str = row.get("Type", "")

            # Skip RowNumber columns (internal)
            if col_type_str == "RowNumber":
                continue

            # Map the DataType string to our standard types
            data_type_raw = row.get("DataType", "")
            data_type = self.DATATYPE_MAP.get(
                data_type_raw, data_type_raw.lower() if data_type_raw else "unknown"
            )

            col_def = {
                "name": row.get("Name", ""),
                "type": data_type,
                "hidden": as_bool(row.get("IsHidden", False)),
                "isKey": as_bool(row.get("IsKey", False)),
                "isNullable": as_bool(row.get("IsNullable", True)),
                "formatString": row.get("FormatString"),
                "sourceColumn": row.get("SourceColumn"),
                "description": row.get("Description") or "",
            }

            if table_name in table_map:
                table_map[table_name]["columns"].append(col_def)

        # --- Process measures ---
        measures = []
        for row in measures_raw:
            name = row.get("Name", "")
            dmv_data = measures_dmv.get(name, {})

            # Prefer INFO.VIEW values; fall back to DMV
            expression = row.get("Expression") or dmv_data.get("expression", "")
            folder = row.get("DisplayFolder") or dmv_data.get("folder", "")
            format_string = row.get("FormatString") or dmv_data.get("formatString")

            measure_def = {
                "name": name,
                "table": row.get("Table", ""),
                "expression": expression,
                "formatString": format_string,
                "hidden": as_bool(row.get("IsHidden", False)),
                "folder": folder,
                "description": row.get("Description") or "",
            }

            # Parse EXTERNALMEASURE wrapper (composite / DirectQuery models)
            em_match = _EXTERNALMEASURE_RE.match(expression)
            if em_match:
                _, data_type, source = em_match.groups()
                measure_def["dataType"] = data_type
                measure_def["expression"] = f'"{source}"'

            measures.append(measure_def)

        # --- Process relationships ---
        # Cardinality comes as strings ("Many", "One") — map to ints (2, 1)
        # so the formatter's CARDINALITY_SHORT dict works correctly.
        CARD_STR_TO_INT = {"Many": 2, "One": 1, "None": 0}

        relationships = []
        for row in relationships_raw:
            from_card_raw = row.get("FromCardinality")
            to_card_raw = row.get("ToCardinality")

            from_card = (
                CARD_STR_TO_INT.get(from_card_raw, from_card_raw)
                if isinstance(from_card_raw, str) else from_card_raw
            )
            to_card = (
                CARD_STR_TO_INT.get(to_card_raw, to_card_raw)
                if isinstance(to_card_raw, str) else to_card_raw
            )

            rel_def = {
                "name": row.get("Name") or "",
                "fromTable": row.get("FromTable", ""),
                "fromColumn": row.get("FromColumn", ""),
                "toTable": row.get("ToTable", ""),
                "toColumn": row.get("ToColumn", ""),
                "active": as_bool(row.get("IsActive", True)),
                "crossFilter": row.get("CrossFilteringBehavior"),
                "fromCardinality": from_card,
                "toCardinality": to_card,
            }
            relationships.append(rel_def)

        # Build final schema pack
        schema_pack = {
            "model": {
                "tables": list(table_map.values()),
                "measures": measures,
                "relationships": relationships,
                "linguistic_metadata": [],  # Not available via INFO.VIEW; empty is fine
            }
        }

        return schema_pack


