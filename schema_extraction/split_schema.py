"""
Split Schema Pack into Specialized Domain Schemas

Takes the full schema pack JSON and produces two focused schema files:
  1. schema_transactions_<date>.txt  – FactTransactions + connected dims + Transaction measures
  2. schema_feedback_<date>.txt      – FactADFeedback + connected dims + tempdata + Feedback measures

Connected tables are discovered automatically from model relationships.

Usage:
    python schema_extraction/split_schema.py
    python schema_extraction/split_schema.py --json cache/schema/schema_pack_2026-02-23.json
"""

import json
import argparse
from copy import deepcopy
from datetime import datetime
from pathlib import Path
from typing import Dict, Any, List, Set

# Resolve project root so we can import the formatter
import sys
project_root = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(project_root))

from schema_extraction.format_schema_for_prompt import SchemaPackFormatter


# ============================================================
# Domain definitions
# ============================================================

DOMAIN_CONFIGS: List[Dict[str, Any]] = [
    {
        "name": "transactions",
        "label": "TRANSACTIONS SCHEMA",
        "description": (
            "This schema contains only tables, relationships, and measures\n"
            "related to FactTransactions (TAMM platform transactions)."
        ),
        "fact_tables": ["facttransactions"],
        "extra_tables": [],              # additional tables to always include
        "measure_folders": ["Transactions"],
        "output_prefix": "schema_transactions",
    },
    {
        "name": "feedback",
        "label": "FEEDBACK SCHEMA",
        "description": (
            "This schema contains only tables, relationships, and measures\n"
            "related to FactADFeedback (customer feedback / surveys)."
        ),
        "fact_tables": ["factadfeedback"],
        "extra_tables": ["tempdata"],     # user requested tempdata in feedback
        "measure_folders": ["Feedback"],
        "output_prefix": "schema_feedback",
    },
]


# ============================================================
# Schema splitter
# ============================================================

class SchemaSplitter:
    """Split a full schema pack JSON into domain-specific sub-schemas."""

    def __init__(self, schema: Dict[str, Any]):
        self.schema = schema
        self.model = schema["model"]

    # ----------------------------------------------------------
    # Discover connected tables via relationships
    # ----------------------------------------------------------
    def _find_connected_tables(self, fact_tables: List[str]) -> Set[str]:
        """
        Walk relationships to find all tables connected to the given fact tables.

        Includes both active and inactive relationships.
        """
        fact_set = {t.lower() for t in fact_tables}
        connected: Set[str] = set(fact_set)

        for rel in self.model.get("relationships", []):
            from_tbl = rel["fromTable"].lower()
            to_tbl = rel["toTable"].lower()

            if from_tbl in fact_set or to_tbl in fact_set:
                connected.add(from_tbl)
                connected.add(to_tbl)

        return connected

    # ----------------------------------------------------------
    # Build a filtered sub-schema
    # ----------------------------------------------------------
    def build_domain_schema(self, config: Dict[str, Any]) -> Dict[str, Any]:
        """
        Build a sub-schema containing only the tables, relationships,
        and measures relevant to a specific domain.
        """
        fact_tables = config["fact_tables"]
        extra_tables = config.get("extra_tables", [])
        measure_folders = [f.lower() for f in config["measure_folders"]]

        # 1. Discover all tables to include
        included = self._find_connected_tables(fact_tables)
        for t in extra_tables:
            included.add(t.lower())

        # 2. Filter tables
        tables = [
            deepcopy(t) for t in self.model["tables"]
            if t["name"].lower() in included
        ]

        # 3. Filter relationships to only those between included tables
        relationships = [
            deepcopy(r) for r in self.model.get("relationships", [])
            if r["fromTable"].lower() in included
            and r["toTable"].lower() in included
        ]

        # 4. Filter measures by folder
        measures = [
            deepcopy(m) for m in self.model.get("measures", [])
            if (m.get("folder") or "").strip().lower() in measure_folders
        ]

        # 5. Keep linguistic metadata for included tables
        linguistic = [
            deepcopy(lm) for lm in self.model.get("linguistic_metadata", [])
            if lm.get("tableName") is None  # model-level
            or (lm.get("tableName") or "").lower() in included
        ]

        return {
            "model": {
                "tables": tables,
                "relationships": relationships,
                "measures": measures,
                "linguistic_metadata": linguistic,
            }
        }


# ============================================================
# Format a domain schema to text
# ============================================================

def format_domain_schema(
    sub_schema: Dict[str, Any],
    label: str,
    description: str,
) -> str:
    """Format a domain sub-schema using SchemaPackFormatter, with a header."""
    formatter = SchemaPackFormatter(include_hidden=False)
    formatter.schema = sub_schema
    body = formatter.format()

    border = "=" * 60
    header = f"{border}\n{label}\n{border}\n{description}\n"

    return f"{header}\n{body}\n"


# ============================================================
# Main
# ============================================================

def main():
    parser = argparse.ArgumentParser(
        description="Split full schema pack into domain-specific schemas."
    )
    parser.add_argument(
        "--json",
        type=str,
        default=None,
        help="Path to schema_pack JSON file. Defaults to latest in cache/schema/.",
    )
    parser.add_argument(
        "--output-dir",
        type=str,
        default=None,
        help="Output directory. Defaults to cache/schema/.",
    )
    args = parser.parse_args()

    # Resolve paths
    cache_dir = project_root / "cache" / "schema"

    if args.json:
        json_path = Path(args.json)
    else:
        # Find the latest schema_pack_*.json in cache/schema
        candidates = sorted(cache_dir.glob("schema_pack_*.json"), reverse=True)
        if not candidates:
            print("❌ No schema_pack_*.json found in cache/schema/.")
            print("   Run automated_schema_extract.py --save-json first.")
            sys.exit(1)
        json_path = candidates[0]

    output_dir = Path(args.output_dir) if args.output_dir else cache_dir

    # Load full schema
    print(f"📂 Loading schema: {json_path.name}")
    full_schema = json.loads(json_path.read_text(encoding="utf-8"))

    splitter = SchemaSplitter(full_schema)
    today = datetime.now().strftime("%Y-%m-%d")

    print()
    for config in DOMAIN_CONFIGS:
        domain_name = config["name"]
        print(f"📝 Building {domain_name} schema...")

        # Build filtered sub-schema
        sub_schema = splitter.build_domain_schema(config)

        # Count what we got
        n_tables = len(sub_schema["model"]["tables"])
        n_rels = len(sub_schema["model"]["relationships"])
        n_measures = len(sub_schema["model"]["measures"])
        print(f"   Tables: {n_tables}, Relationships: {n_rels}, Measures: {n_measures}")

        # Format to text
        text = format_domain_schema(
            sub_schema, config["label"], config["description"]
        )

        # Save dated copy
        out_file = output_dir / f"{config['output_prefix']}_{today}.txt"
        out_file.write_text(text, encoding="utf-8")
        print(f"   ✅ Saved: {out_file.name}  ({len(text):,} chars)")

        # Overwrite the canonical schema file used by the app
        canonical_file = output_dir / f"{config['output_prefix']}.txt"
        canonical_file.write_text(text, encoding="utf-8")
        print(f"   ✅ Updated: {canonical_file.name}")
        print()

    print("✅ Done – domain schemas saved to:", output_dir)


if __name__ == "__main__":
    main()
