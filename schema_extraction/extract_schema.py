"""
Extract Metadata Schema Pack

Builds an LLM-friendly denormalized schema_pack.json from XMLA DMVs.
Uses ONLY confirmed DMV fields (no guessing, no joins).

This module extracts metadata about Power BI datasets including tables, 
columns, measures, and relationships, then generates a structured JSON output.
"""

import json
import sys
from pathlib import Path
from typing import Any, Dict, List

# Add parent directory to path for imports
sys.path.insert(0, str(Path(__file__).parent.parent))

from backend.tools.auth import load_environment, AuthenticationManager
from execute_dax import DaxQueryExecutor


# ============================================================
# Type Mapping (ExplicitDataType)
# ============================================================
class DataTypeMapper:
    """Maps Power BI data type codes to human-readable type names."""

    # Power BI data type codes to string mapping
    TYPE_MAP = {
        9: "string",
        10: "datetime",
        11: "date",
        2: "int64",
        3: "decimal",
        4: "double",
        6: "boolean",
    }

    @staticmethod
    def map_type(value: Any) -> str:
        """Convert data type code to string name."""
        if value is None:
            return "unknown"
        if isinstance(value, int) and value in DataTypeMapper.TYPE_MAP:
            return DataTypeMapper.TYPE_MAP[value]
        return str(value).lower()

    @staticmethod
    def as_bool(value: Any) -> bool:
        """Convert value to boolean."""
        if isinstance(value, bool):
            return value
        if isinstance(value, int):
            return value != 0
        return False


# ============================================================
# DMV Query Helper
# ============================================================
class DMVReader:
    """Handles reading DMV results with ordinal-prefixed column names."""

    @staticmethod
    def process_reader(reader) -> List[Dict[str, Any]]:
        """
        Convert ADOMD reader to list of dictionaries with ordinal-prefixed column names.
        
        This ensures safe column name handling by prefixing with column index.
        Example: c0_ID, c1_Name, c2_IsHidden
        """
        # Create column names with ordinal prefix
        columns = [f"c{i}_{reader.GetName(i)}" for i in range(reader.FieldCount)]
        rows: List[Dict[str, Any]] = []

        # Read all rows from DMV result
        while reader.Read():
            row = {}
            for i, col_name in enumerate(columns):
                value = reader.GetValue(i)
                row[col_name] = None if value is None else value
            rows.append(row)

        return rows


# ============================================================
# Metadata Extractor
# ============================================================
class MetadataExtractor:
    """Extracts and structures Power BI metadata from XMLA DMVs."""

    def __init__(self, executor: DaxQueryExecutor):
        """Initialize with a DAX query executor."""
        self.executor = executor
        self.tables = {}
        self.columns_by_table = {}
        self.column_name_by_id = {}
        self.measures = []
        self.relationships = []
        self.linguistic_metadata = []

    def fetch_all_metadata(self) -> Dict[str, List[Dict[str, Any]]]:
        """
        Execute all DMV queries to fetch metadata.
        
        Returns:
            Dictionary with keys: tables, columns, measures, relationships
        """
        print("Fetching tables...")
        tables = self._execute_dmv_query("""
            SELECT
              [ID],
              [Name],
              [IsHidden],
              [Description]
            FROM $SYSTEM.TMSCHEMA_TABLES
        """)

        print("Fetching columns...")
        columns = self._execute_dmv_query("""
            SELECT
              [ID],
              [TableID],
              [ExplicitName],
              [ExplicitDataType],
              [IsHidden],
              [IsKey],
              [IsNullable],
              [FormatString],
              [SourceColumn],
              [Description]
            FROM $SYSTEM.TMSCHEMA_COLUMNS
        """)

        print("Fetching measures...")
        measures = self._execute_dmv_query("""
            SELECT
              [ID],
              [Name],
              [Expression],
              [FormatString],
              [IsHidden],
              [TableID],
              [DisplayFolder],
              [Description]
            FROM $SYSTEM.TMSCHEMA_MEASURES
        """)

        print("Fetching relationships...")
        relationships = self._execute_dmv_query("""
            SELECT
              [ID],
              [Name],
              [IsActive],
              [Type],
              [CrossFilteringBehavior],
              [JoinOnDateBehavior],
              [FromTableID],
              [FromColumnID],
              [ToTableID],
              [ToColumnID],
              [FromCardinality],
              [ToCardinality]
            FROM $SYSTEM.TMSCHEMA_RELATIONSHIPS
        """)

        # Try to fetch linguistic metadata (Q&A synonyms) - may not be available in all models
        print("Fetching linguistic metadata (Q&A synonyms)...")
        linguistic_metadata = []
        try:
            # First try with SELECT * to discover available columns
            linguistic_metadata = self._execute_dmv_query("""
                SELECT *
                FROM $SYSTEM.TMSCHEMA_LINGUISTIC_METADATA
            """)
            print(f"  Found {len(linguistic_metadata)} linguistic metadata entries")
        except Exception as e:
            print(f"  Warning: Could not fetch linguistic metadata: {e}")
            print("  Continuing without Q&A synonyms...")

        return {
            "tables": tables,
            "columns": columns,
            "measures": measures,
            "relationships": relationships,
            "linguistic_metadata": linguistic_metadata,
        }

    def _execute_dmv_query(self, query: str) -> List[Dict[str, Any]]:
        """Execute a DMV query and return processed rows."""
        result = self.executor.execute_with_metadata(query)
        # Reconstruct the ordinal-prefixed column format for compatibility
        return self._convert_to_ordinal_format(result)

    @staticmethod
    def _convert_to_ordinal_format(result: Dict[str, Any]) -> List[Dict[str, Any]]:
        """Convert standard result format to ordinal-prefixed column names."""
        columns = result["columns"]
        rows = []
        for data_row in result["data"]:
            row = {}
            for i, col_name in enumerate(columns):
                row[f"c{i}_{col_name}"] = data_row[i]
            rows.append(row)
        return rows

    def build_schema_pack(self, metadata: Dict[str, List[Dict[str, Any]]]) -> Dict[str, Any]:
        """
        Build denormalized schema pack from metadata.
        
        Organizes tables, columns, measures, relationships, and linguistic metadata
        into a single structured format suitable for LLM consumption.
        """
        tables_rows = metadata["tables"]
        columns_rows = metadata["columns"]
        measures_rows = metadata["measures"]
        relationships_rows = metadata["relationships"]
        linguistic_rows = metadata.get("linguistic_metadata", [])

        # Process tables
        self._process_tables(tables_rows)

        # Process columns
        self._process_columns(columns_rows)

        # Process measures
        self._process_measures(measures_rows)

        # Process relationships
        self._process_relationships(relationships_rows)

        # Process linguistic metadata (Q&A synonyms)
        self._process_linguistic_metadata(linguistic_rows)

        # Return final schema pack structure
        return {
            "model": {
                "tables": list(self.tables.values()),
                "measures": self.measures,
                "relationships": self.relationships,
                "linguistic_metadata": self.linguistic_metadata,
            }
        }

    def _process_tables(self, tables_rows: List[Dict[str, Any]]):
        """Extract table definitions from DMV results."""
        for row in tables_rows:
            table_id = int(row["c0_ID"])
            self.tables[table_id] = {
                "name": row["c1_Name"],
                "hidden": DataTypeMapper.as_bool(row["c2_IsHidden"]),
                "description": row.get("c3_Description") or "",
                "columns": [],
            }
            self.columns_by_table[table_id] = []

    def _process_columns(self, columns_rows: List[Dict[str, Any]]):
        """Extract column definitions and associate with tables."""
        for row in columns_rows:
            column_id = int(row["c0_ID"])
            table_id = int(row["c1_TableID"])

            # Build column definition
            column_def = {
                "name": row["c2_ExplicitName"],
                "type": DataTypeMapper.map_type(row["c3_ExplicitDataType"]),
                "hidden": DataTypeMapper.as_bool(row["c4_IsHidden"]),
                "isKey": DataTypeMapper.as_bool(row["c5_IsKey"]),
                "isNullable": DataTypeMapper.as_bool(row["c6_IsNullable"]),
                "formatString": row["c7_FormatString"],
                "sourceColumn": row["c8_SourceColumn"],
                "description": row.get("c9_Description") or "",
            }

            # Track column ID for relationship building
            self.column_name_by_id[column_id] = column_def["name"]

            # Add column to its table
            if table_id in self.columns_by_table:
                self.columns_by_table[table_id].append(column_def)

        # Attach columns to their parent tables
        for table_id, columns in self.columns_by_table.items():
            if table_id in self.tables:
                self.tables[table_id]["columns"] = columns

    def _process_measures(self, measures_rows: List[Dict[str, Any]]):
        """Extract measure definitions."""
        for row in measures_rows:
            table_id = int(row["c5_TableID"])
            table_name = self.tables.get(table_id, {}).get("name")

            measure_def = {
                "name": row["c1_Name"],
                "table": table_name,
                "expression": row["c2_Expression"],
                "formatString": row["c3_FormatString"],
                "hidden": DataTypeMapper.as_bool(row["c4_IsHidden"]),
                "folder": row.get("c6_DisplayFolder") or "",
                "description": row.get("c7_Description") or "",
            }
            self.measures.append(measure_def)

    def _process_relationships(self, relationships_rows: List[Dict[str, Any]]):
        """Extract relationship definitions."""
        for row in relationships_rows:
            from_table_id = int(row["c6_FromTableID"])
            from_column_id = int(row["c7_FromColumnID"])
            to_table_id = int(row["c8_ToTableID"])
            to_column_id = int(row["c9_ToColumnID"])

            # Validate that all referenced entities exist
            if from_table_id not in self.tables or to_table_id not in self.tables:
                continue
            if from_column_id not in self.column_name_by_id or to_column_id not in self.column_name_by_id:
                continue

            # Build relationship definition
            relationship_def = {
                "name": row["c1_Name"],
                "fromTable": self.tables[from_table_id]["name"],
                "fromColumn": self.column_name_by_id[from_column_id],
                "toTable": self.tables[to_table_id]["name"],
                "toColumn": self.column_name_by_id[to_column_id],
                "active": DataTypeMapper.as_bool(row["c2_IsActive"]),
                "crossFilter": row["c4_CrossFilteringBehavior"],
                "fromCardinality": row["c10_FromCardinality"],
                "toCardinality": row["c11_ToCardinality"],
            }
            self.relationships.append(relationship_def)

    def _process_linguistic_metadata(self, linguistic_rows: List[Dict[str, Any]]):
        """
        Extract Q&A linguistic metadata (synonyms/terms) from DMV results.
        
        The Content field contains XML or JSON with synonym definitions for
        tables and columns used by Power BI Q&A feature.
        
        Note: Column names vary by model, so we search for common patterns.
        """
        for row in linguistic_rows:
            # Find TableID column (might be c0_, c1_, etc.)
            table_id = None
            column_id = None
            content = None
            
            for key, value in row.items():
                key_lower = key.lower()
                if "tableid" in key_lower and value is not None:
                    table_id = int(value)
                elif "columnid" in key_lower and value is not None:
                    column_id = int(value)
                elif "content" in key_lower and value is not None:
                    content = str(value)
            
            # Determine which object this metadata applies to
            table_name = None
            column_name = None
            
            if table_id is not None:
                table_name = self.tables.get(table_id, {}).get("name")
            
            if column_id is not None:
                column_name = self.column_name_by_id.get(column_id)
            
            # Only add if we have meaningful content
            if content:
                linguistic_def = {
                    "tableName": table_name,
                    "columnName": column_name,
                    "content": content,
                }
                self.linguistic_metadata.append(linguistic_def)


# ============================================================
# Main Orchestration
# ============================================================
def main():
    """Main execution flow for metadata extraction."""
    # Load environment variables and validate required configs
    tenant_id, client_id, _, workspace_name, database_name, adomd_dll = load_environment()

    # Authenticate using device flow (no persistent cache)
    # This is a development utility script - production uses OBO flow
    auth_manager = AuthenticationManager(
        tenant_id, 
        client_id, 
        persist_cache=False,  # Don't persist tokens to disk
    )
    access_token = auth_manager.acquire_token()

    # Create XMLA endpoint URL for Power BI workspace
    xmla_endpoint = f"powerbi://api.powerbi.com/v1.0/myorg/{workspace_name}"

    # Initialize DAX executor for running DMV queries
    executor = DaxQueryExecutor(adomd_dll, workspace_name, database_name, access_token)

    # Use context manager to ensure connection cleanup
    with executor:
        # Extract all metadata from DMVs
        metadata_extractor = MetadataExtractor(executor)
        metadata = metadata_extractor.fetch_all_metadata()

        # Build denormalized schema pack suitable for LLM consumption
        schema_pack = metadata_extractor.build_schema_pack(metadata)

        # Write results to JSON file in output directory
        output_dir = Path(__file__).with_name("out")
        output_dir.mkdir(exist_ok=True)
        output_path = output_dir / "schema_pack_new.json"

        output_path.write_text(
            json.dumps(schema_pack, indent=2, ensure_ascii=False),
            encoding="utf-8"
        )

        print("✅ schema_pack.json generated")
        print(output_path)


# if __name__ == "__main__":
#     main()
