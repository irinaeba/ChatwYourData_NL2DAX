"""
Format Schema Pack to LLM Grounding Context

Converts a Power BI schema pack JSON file into a compact, LLM-friendly 
text format that provides context for DAX generation. The output format
clearly defines tables, columns, relationships, and measures.

Output sections (separated by ============ borders):
  1. TABLES & COLUMNS       – table names with columns, types, descriptions, synonyms
  2. COLUMN SYNONYMS        – business-friendly aliases for key columns
  3. TABLE RELATIONSHIPS    – active & inactive joins with cardinality
  4. MEASURES               – DAX definitions grouped by table/folder
"""

import json
import re
import xml.etree.ElementTree as ET
from pathlib import Path
from typing import Dict, Any, List, Optional, Tuple


# ============================================================
# Configuration
# ============================================================
SECTION_BORDER = "=" * 60

class SchemaPackConfig:
    """Configuration for schema pack formatting."""

    # Default input/output paths (relative to script directory)
    DEFAULT_INPUT_JSON = "out/schema_pack.json"
    DEFAULT_OUTPUT_TXT = "out/schema_pack.compact.txt"

    # Hide hidden objects (columns, tables marked as hidden)
    # Set to False for best LLM grounding
    INCLUDE_HIDDEN = False

    # Relationship cardinality mapping for human-readable labels
    CARDINALITY_MAP = {
        (1, 2): "one-to-many",
        (2, 1): "many-to-one",
        (1, 1): "one-to-one",
        (2, 2): "many-to-many",
    }

    # Cardinality short labels for relationship arrows
    CARDINALITY_SHORT = {
        (1, 2): "Many-to-One (*:1)",
        (2, 1): "One-to-Many (1:*)",
        (1, 1): "One-to-One (1:1)",
        (2, 2): "Many-to-Many (*:*)",
    }


# ============================================================
# Schema Pack Formatter
# ============================================================
class SchemaPackFormatter:
    """
    Converts a Power BI schema pack into LLM grounding context.

    Transforms JSON metadata into a structured text format with clear
    sections for tables, columns, relationships, and measures to aid
    DAX generation and understanding.
    """

    def __init__(self, include_hidden: bool = False):
        self.include_hidden = include_hidden
        self.schema = None
        self.lines: List[str] = []
        # Synonym lookup: (tableName, columnName) -> [synonym1, synonym2, ...]
        self.column_synonyms: Dict[Tuple[str, str], List[str]] = {}
        # Table-level synonyms: tableName -> [synonym1, ...]
        self.table_synonyms: Dict[str, List[str]] = {}

    # ----------------------------------------------------------
    # Schema loading
    # ----------------------------------------------------------
    def load_schema(self, path: Path) -> Dict[str, Any]:
        """Load schema pack from JSON file."""
        if not path.exists():
            raise FileNotFoundError(f"Schema pack not found at {path}")
        self.schema = json.loads(path.read_text(encoding="utf-8"))
        return self.schema

    # ----------------------------------------------------------
    # Linguistic metadata / synonym parsing
    # ----------------------------------------------------------
    def _parse_linguistic_metadata(self) -> None:
        """
        Parse linguistic metadata content to extract column and table synonyms.

        Power BI stores Q&A synonyms in the LinguisticSchema, which may be:
          - XML format (LinguisticSchema XML)
          - JSON format
          - Plain version number (e.g. "1") meaning no synonyms defined
        """
        linguistic_data = self.schema["model"].get("linguistic_metadata", [])
        if not linguistic_data:
            return

        for item in linguistic_data:
            content = (item.get("content") or "").strip()
            table_name = item.get("tableName")
            column_name = item.get("columnName")

            if not content or content in ("0", "1", "2"):
                # Version number only – no actual synonym data
                continue

            # Try XML parse (Power BI LinguisticSchema format)
            if content.startswith("<"):
                self._parse_xml_synonyms(content)
            # Try JSON parse
            elif content.startswith("{") or content.startswith("["):
                self._parse_json_synonyms(content, table_name, column_name)
            else:
                # Plain text – treat comma-separated values as synonyms
                synonyms = [s.strip() for s in content.split(",") if s.strip()]
                if synonyms and table_name and column_name:
                    key = (table_name, column_name)
                    self.column_synonyms.setdefault(key, []).extend(synonyms)

    def _parse_xml_synonyms(self, xml_content: str) -> None:
        """
        Parse Power BI LinguisticSchema XML to extract synonyms.

        Expected structure:
        <LinguisticSchema Version="...">
          <Entities>
            <Entity Name="TableName">
              <Words><Word>synonym</Word></Words>
              <Attributes>
                <Attribute Name="ColumnName">
                  <Words><Word>synonym</Word></Words>
                </Attribute>
              </Attributes>
            </Entity>
          </Entities>
        </LinguisticSchema>
        """
        try:
            root = ET.fromstring(xml_content)
        except ET.ParseError:
            return

        # Handle namespace if present
        ns = ""
        match = re.match(r"\{(.+?)\}", root.tag)
        if match:
            ns = f"{{{match.group(1)}}}"

        for entity in root.iter(f"{ns}Entity"):
            table_name = entity.get("Name", "")
            if not table_name:
                continue

            # Table-level synonyms
            for word in entity.iter(f"{ns}Word"):
                if word.text and word.text.strip():
                    self.table_synonyms.setdefault(table_name, []).append(word.text.strip())

            # Column-level synonyms from Attributes
            for attr in entity.iter(f"{ns}Attribute"):
                col_name = attr.get("Name", "")
                if not col_name:
                    continue
                for word in attr.iter(f"{ns}Word"):
                    if word.text and word.text.strip():
                        key = (table_name, col_name)
                        self.column_synonyms.setdefault(key, []).append(word.text.strip())

    def _parse_json_synonyms(self, json_content: str,
                             table_name: Optional[str],
                             column_name: Optional[str]) -> None:
        """Parse JSON-format synonym definitions."""
        try:
            data = json.loads(json_content)
        except json.JSONDecodeError:
            return

        if isinstance(data, dict):
            synonyms = data.get("synonyms", [])
            if isinstance(synonyms, list) and table_name and column_name:
                key = (table_name, column_name)
                self.column_synonyms.setdefault(key, []).extend(
                    [s for s in synonyms if isinstance(s, str)]
                )

    # ----------------------------------------------------------
    # Helpers – name splitting
    # ----------------------------------------------------------

    # Dictionary for segmenting all-lowercase concatenated column names
    _WORD_DICT: Optional[set] = None

    @classmethod
    def _get_word_dict(cls) -> set:
        """Lazily build a word dictionary for lowercase name segmentation."""
        if cls._WORD_DICT is not None:
            return cls._WORD_DICT
        cls._WORD_DICT = {
            # 2-letter
            "id", "at", "by", "in", "is", "it", "of", "on", "or", "to",
            "up", "no", "ai", "yy", "dd", "ad",
            # 3-letter
            "key", "url", "ref", "sla", "day", "mon", "ces", "cms", "nps",
            "adl", "adf", "kpi", "dwh", "trx", "min", "gsp", "qrt", "net",
            "sad", "tag", "row", "one", "the", "for", "all", "new", "old",
            "top", "end", "max", "sum", "avg", "sql", "dax", "src", "spa",
            # 4-letter
            "date", "time", "name", "type", "code", "flag", "rank", "paid",
            "item", "path", "live", "full", "week", "year", "adge", "tamm",
            "care", "page", "days", "conv", "exec", "shot", "data", "with",
            "from", "auto", "last", "mode", "city", "unit", "true", "hash",
            "like", "temp",
            # 5-letter
            "short", "index", "space", "today", "month", "start", "title",
            "scale", "score", "happy", "total", "above", "query", "first",
            "count", "order", "group", "table", "value", "point", "pilot",
            "trans", "taken", "cycle", "email",
            # 6-letter
            "status", "entity", "sector", "hidden", "search", "global",
            "record", "parent", "locker", "column", "number", "bucket",
            "access", "mobile", "effort", "action", "source", "master",
            "slicer", "arabic", "create", "survey", "common", "filter",
            "format", "string", "enable", "prompt", "smiley", "detail",
            "lookup",
            # 7-letter
            "contact", "service", "channel", "details", "browser",
            "android", "closure", "comment", "version", "matched",
            "instant", "neutral", "english", "working", "current",
            "created", "utility", "enabled", "minutes", "powered",
            "promote", "payment", "private", "general", "boolean",
            "integer", "primary",
            # 8+ letter
            "combined", "activity", "feedback", "comments", "category",
            "passives", "included", "provider", "creation", "platform",
            "analytics", "customer", "promoter", "onboarded", "modified",
            "execution", "autopilot", "reference", "detractor",
            "authority", "promotors", "detractors", "completion",
            "satisfaction", "description", "intervention", "conversation",
            "modification", "recommendation", "application", "transaction",
            "transactions", "percentage",
        }
        return cls._WORD_DICT

    @classmethod
    def _segment_words(cls, name: str) -> Optional[str]:
        """
        Segment an all-lowercase concatenated name into words using DP.

        Uses a domain-specific dictionary.  Minimises segment count
        (i.e. prefers fewer, longer words).

        Returns the segmented string, or None if no valid split is found.
        """
        words = cls._get_word_dict()
        n = len(name)
        if n <= 2:
            return name

        INF = float("inf")
        MAX_WORD = 20
        # best[i] = (segment_count, prev_pos)  for name[:i]
        best = [(INF, -1)] * (n + 1)
        best[0] = (0, -1)

        for i in range(1, n + 1):
            for j in range(max(0, i - MAX_WORD), i):
                piece = name[j:i]
                if piece in words and best[j][0] + 1 < best[i][0]:
                    best[i] = (best[j][0] + 1, j)

        if best[n][0] == INF:
            return None  # could not fully segment

        # Reconstruct
        parts: List[str] = []
        pos = n
        while pos > 0:
            parts.append(name[best[pos][1]:pos])
            pos = best[pos][1]
        parts.reverse()
        return " ".join(parts)

    @staticmethod
    def _split_camel_case(name: str) -> str:
        """
        Insert spaces into camelCase / PascalCase names.

        Examples:
            SmileyType      -> Smiley Type
            TAMMCareFlag    -> TAMM Care Flag
            ApplicationID   -> Application ID
            ADGEKey         -> ADGE Key
            KPIs            -> KPIs  (plural acronym kept intact)
            contactkey      -> contactkey  (all-lowercase unchanged)
        """
        # Step 1: boundary between a run of UPPERCASE and an Uppercase+lowercase word
        #         Require 2+ lowercase chars to avoid splitting plural acronyms (KPIs)
        #         e.g. "TAMMCare" -> "TAMM Care", "ADGEKey" -> "ADGE Key"
        s = re.sub(r'([A-Z]+)([A-Z][a-z]{2,})', r'\1 \2', name)
        # Step 2: boundary between a lowercase/digit and an uppercase letter
        #         e.g. "smileyType" -> "smiley Type"
        s = re.sub(r'([a-z0-9])([A-Z])', r'\1 \2', s)
        return s

    @classmethod
    def _make_friendly_name(cls, name: str) -> str:
        """
        Generate a human-readable version of a column name.

        Strategy:
          - Already has spaces → return as-is (already readable)
          - Mixed case (camelCase / PascalCase) → split at case boundaries
          - All-lowercase → dictionary-based DP word segmentation
          - Fallback → return original unchanged
        """
        if " " in name:
            return name

        has_upper = any(c.isupper() for c in name)
        has_lower = any(c.islower() for c in name)

        if has_upper and has_lower:
            return cls._split_camel_case(name)

        if name.islower():
            segmented = cls._segment_words(name)
            if segmented is not None:
                return segmented

        return name

    def _should_include(self, is_hidden: bool) -> bool:
        """Whether to include an object based on its hidden flag."""
        return not is_hidden or self.include_hidden

    def _add_section_header(self, title: str) -> None:
        """Add a bordered section header:  ====\\n TITLE \\n===="""
        self.lines.append(SECTION_BORDER)
        self.lines.append(title)
        self.lines.append(SECTION_BORDER)
        self.lines.append("")

    def _format_cardinality_short(self, from_card: Any, to_card: Any) -> str:
        """Return short cardinality label like 'Many-to-One (*:1)'."""
        key = (from_card, to_card)
        return SchemaPackConfig.CARDINALITY_SHORT.get(key, "Unknown")

    # ----------------------------------------------------------
    # Section 1 – Tables & Columns
    # ----------------------------------------------------------
    def _format_tables_and_columns(self) -> None:
        """
        Format tables and columns section.

        Output format per table:
            TableName  description
                columnName  type  -- column description  (synonyms: a, b, c)
        """
        self._add_section_header("TABLES & COLUMNS")

        for table in self.schema["model"]["tables"]:
            if not self._should_include(table.get("hidden", False)):
                continue

            # Table line: "TableName  description"
            table_name = table["name"]
            table_desc = (table.get("description") or "").strip()
            if table_desc:
                self.lines.append(f"{table_name}  {table_desc}")
            else:
                self.lines.append(table_name)

            # Columns
            for column in table.get("columns", []):
                if not self._should_include(column.get("hidden", False)):
                    continue

                col_name = column["name"]          # preserve original
                col_type = column.get("type", "")
                col_desc = (column.get("description") or "").strip()

                # Generate human-readable split name
                friendly = self._make_friendly_name(col_name)

                # Build annotation after "--"
                annotations: List[str] = []
                if friendly.lower() != col_name.lower():
                    annotations.append(friendly)
                if col_desc:
                    annotations.append(col_desc)

                # Inline synonyms
                synonym_key = (table_name, col_name)
                synonyms = self.column_synonyms.get(synonym_key, [])
                if synonyms:
                    annotations.append(f"synonyms: {', '.join(synonyms)}")

                # Build column line:  "    colName  type  -- Friendly Name. description"
                parts = [f"    {col_name}"]
                if col_type:
                    parts.append(f"  {col_type}")
                if annotations:
                    parts.append(f"  -- {'. '.join(annotations)}")

                self.lines.append("".join(parts))

            self.lines.append("")  # blank line between tables

    # ----------------------------------------------------------
    # Section 2 – Column Synonyms & Business Terms
    # ----------------------------------------------------------
    def _format_synonyms_section(self) -> None:
        """
        Format a dedicated synonyms section.

        Groups synonyms by table:
            TableName columns:
                ColumnName: synonym1, synonym2, ...
        """
        if not self.column_synonyms and not self.table_synonyms:
            return

        self._add_section_header("COLUMN SYNONYMS & BUSINESS TERMS")

        # Collect all table names that have synonyms
        tables_with_synonyms: Dict[str, Dict[str, List[str]]] = {}

        for (tbl, col), syns in self.column_synonyms.items():
            tables_with_synonyms.setdefault(tbl, {})[col] = syns

        for table_name in sorted(tables_with_synonyms.keys()):
            self.lines.append(f"{table_name} columns:")
            for col_name, syns in sorted(tables_with_synonyms[table_name].items()):
                self.lines.append(f"    {col_name}: {', '.join(syns)}")
            self.lines.append("")

    # ----------------------------------------------------------
    # Section 3 – Table Relationships
    # ----------------------------------------------------------
    def _format_relationships(self) -> None:
        """
        Format relationships section, separated into active and inactive.

        Output format:
            FromTable[FromCol] --> ToTable[ToCol]
                Cardinality: Many-to-One (*:1)
        """
        relationships = self.schema["model"].get("relationships", [])
        if not relationships:
            return

        self._add_section_header("TABLE RELATIONSHIPS")

        active_rels = [r for r in relationships if r.get("active", True)]
        inactive_rels = [r for r in relationships if not r.get("active", True)]

        if active_rels:
            self.lines.append("ACTIVE RELATIONSHIPS:")
            self.lines.append("")
            for rel in active_rels:
                self._format_single_relationship(rel)

        if inactive_rels:
            self.lines.append("INACTIVE RELATIONSHIPS:")
            self.lines.append("")
            for rel in inactive_rels:
                self._format_single_relationship(rel)

    def _format_single_relationship(self, rel: Dict[str, Any]) -> None:
        """Format a single relationship entry."""
        arrow = (
            f"{rel['fromTable']}[{rel['fromColumn']}] --> "
            f"{rel['toTable']}[{rel['toColumn']}]"
        )
        self.lines.append(arrow)

        # Cardinality
        if rel.get("fromCardinality") is not None and rel.get("toCardinality") is not None:
            label = self._format_cardinality_short(
                rel["fromCardinality"], rel["toCardinality"]
            )
            self.lines.append(f"    Cardinality: {label}")

        self.lines.append("")

    # ----------------------------------------------------------
    # Section 4 – Measures
    # ----------------------------------------------------------
    def _format_measures(self) -> None:
        """
        Format measures section, grouped by table.

        Output format:
            Measure: MeasureName
              Folder: FolderName
              Format: FormatString
              Expression:
                DAX expression
        """
        measures = self.schema["model"].get("measures", [])
        if not measures:
            return

        # Group measures by table
        measures_by_table: Dict[str, List[Dict[str, Any]]] = {}
        for m in measures:
            if m.get("hidden", False) and not self.include_hidden:
                continue
            tbl = m.get("table", "_Measures")
            measures_by_table.setdefault(tbl, []).append(m)

        # Build header – include table name if all measures are in one table
        if len(measures_by_table) == 1:
            tbl_name = next(iter(measures_by_table))
            self._add_section_header(f"MEASURES (DAX DEFINITIONS) - Table: {tbl_name}")
        else:
            self._add_section_header("MEASURES (DAX DEFINITIONS)")

        for table_name, table_measures in sorted(measures_by_table.items()):
            # Only show sub-header when multiple tables
            if len(measures_by_table) > 1:
                self.lines.append(f"Table: {table_name}")
                self.lines.append("")

            for measure in table_measures:
                self.lines.append(f"Measure: {measure['name']}")

                desc = (measure.get("description") or "").strip()
                if desc:
                    self.lines.append(f"  Description: {desc}")

                folder = (measure.get("folder") or "").strip()
                if folder:
                    self.lines.append(f"  Folder: {folder}")

                fmt = (measure.get("formatString") or "").strip()
                if fmt:
                    self.lines.append(f"  Format: {fmt}")

                expr = measure.get("expression", "")
                if expr:
                    self.lines.append("  Expression:")
                    for line in expr.split("\n"):
                        self.lines.append(f"    {line}")

                self.lines.append("")

    # ----------------------------------------------------------
    # Public API
    # ----------------------------------------------------------
    def format(self) -> str:
        """
        Generate the complete LLM grounding context.

        Returns:
            str: Formatted context text ready for LLM consumption.
        """
        if not self.schema:
            raise ValueError("Schema not loaded. Call load_schema() first.")

        # Reset state
        self.lines = []
        self.column_synonyms = {}
        self.table_synonyms = {}

        # Parse linguistic metadata for synonyms first
        self._parse_linguistic_metadata()

        # Build output in logical order
        self._format_tables_and_columns()
        self._format_synonyms_section()
        self._format_relationships()
        self._format_measures()

        return "\n".join(self.lines).strip()


# ============================================================
# Main Orchestration
# ============================================================
def main():
    """Main execution flow for schema pack formatting."""
    base_path = Path(__file__).parent
    schema_path = base_path / SchemaPackConfig.DEFAULT_INPUT_JSON
    output_path = base_path / SchemaPackConfig.DEFAULT_OUTPUT_TXT

    formatter = SchemaPackFormatter(include_hidden=SchemaPackConfig.INCLUDE_HIDDEN)
    formatter.load_schema(schema_path)

    context_text = formatter.format()
    output_path.write_text(context_text, encoding="utf-8")

    print("✅ DAX context written to:")
    print(output_path.resolve())


# if __name__ == "__main__":
#     main()
