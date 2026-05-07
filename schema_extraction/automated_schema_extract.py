"""
Automated Schema Extraction Pipeline (XMLA Endpoint)

Runs 3 steps in sequence:
  1. extract_schema  – Extract metadata via DAX INFO functions (XMLA / ADOMD.NET)
  2. format_schema_for_prompt – Convert schema JSON to LLM-friendly text
  3. split_schema – Split into domain-specific schemas

Uses pyadomd + ADOMD.NET for XMLA access, supporting service principal auth.
No REST API limitations — works with client credentials (no user interaction).

Prerequisites:
  - XMLA read enabled in tenant (Admin Portal > Integration settings)
  - pyadomd + pythonnet: pip install pyadomd
  - ADOMD.NET DLL in lib/net45/
  - .env must have: TENANT_ID, CLIENT_ID_POWERBI_SCHEMA_EXTRACTION,
    CLIENT_SECRET_POWERBI_SCHEMA_EXTRACTION, WORKSPACE_NAME, DATABASE_NAME

Usage:
    python schema_extraction/automated_schema_extract.py
    python schema_extraction/automated_schema_extract.py --save-json
    python schema_extraction/automated_schema_extract.py --output cache/schema/my_schema.txt
"""

import os
import sys
import json
import argparse
from pathlib import Path
from datetime import datetime

# Add project root to path
project_root = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(project_root))

# Add schema_extraction folder to path for local imports
schema_dir = Path(__file__).resolve().parent
sys.path.insert(0, str(schema_dir))

from dotenv import load_dotenv
from execute_dax import PowerBIXmlaClient
from extract_schema import MetadataExtractor
from format_schema_for_prompt import SchemaPackFormatter
from split_schema import SchemaSplitter, DOMAIN_CONFIGS, format_domain_schema


# ============================================================
# Pipeline Steps
# ============================================================

def run_extract_schema(client: PowerBIXmlaClient, output_json_path: Path = None) -> dict:
    """
    Step 1: Extract schema metadata from Power BI via XMLA endpoint.

    Args:
        client: Authenticated PowerBIXmlaClient instance.
        output_json_path: Optional path to save the JSON schema pack.

    Returns:
        dict: The extracted schema pack.
    """
    print("\n" + "=" * 60)
    print("📊 STEP 1: Extracting Schema via XMLA (DAX INFO functions)")
    print("=" * 60)

    extractor = MetadataExtractor(client)
    schema_pack = extractor.extract_all()

    # Optionally save JSON
    if output_json_path:
        output_json_path.parent.mkdir(parents=True, exist_ok=True)
        output_json_path.write_text(
            json.dumps(schema_pack, indent=2, ensure_ascii=False),
            encoding="utf-8",
        )
        print(f"   ✅ Schema JSON saved to: {output_json_path}")

    # Print summary
    tables = schema_pack["model"].get("tables", [])
    measures = schema_pack["model"].get("measures", [])
    relationships = schema_pack["model"].get("relationships", [])

    print(f"   📋 Tables: {len(tables)}")
    print(f"   📏 Measures: {len(measures)}")
    print(f"   🔗 Relationships: {len(relationships)}")

    return schema_pack


def run_format_schema(schema_pack: dict, output_txt_path: Path) -> str:
    """
    Step 2: Format schema pack into LLM-friendly text.

    Args:
        schema_pack: The schema pack dictionary.
        output_txt_path: Path to save the formatted text.

    Returns:
        str: The formatted schema text.
    """
    print("\n" + "=" * 60)
    print("📝 STEP 2: Formatting Schema for LLM Prompt")
    print("=" * 60)

    formatter = SchemaPackFormatter(include_hidden=False)
    formatter.schema = schema_pack
    formatted_text = formatter.format()

    output_txt_path.parent.mkdir(parents=True, exist_ok=True)
    output_txt_path.write_text(formatted_text, encoding="utf-8")

    print(f"   ✅ Formatted schema saved to: {output_txt_path}")
    print(f"   📄 Size: {len(formatted_text):,} characters")

    return formatted_text


def run_split_schema(schema_pack: dict, output_dir: Path, date_str: str) -> None:
    """
    Step 3: Split full schema into domain-specific schemas.

    Args:
        schema_pack: The full schema pack dictionary.
        output_dir: Directory to save domain schema files.
        date_str: Date string for filenames.
    """
    print("\n" + "=" * 60)
    print("📂 STEP 3: Splitting into Domain Schemas")
    print("=" * 60)

    splitter = SchemaSplitter(schema_pack)

    for config in DOMAIN_CONFIGS:
        domain_name = config["name"]
        sub_schema = splitter.build_domain_schema(config)

        n_tables = len(sub_schema["model"]["tables"])
        n_rels = len(sub_schema["model"]["relationships"])
        n_measures = len(sub_schema["model"]["measures"])

        text = format_domain_schema(
            sub_schema, config["label"], config["description"]
        )

        out_file = output_dir / f"{config['output_prefix']}_{date_str}.txt"
        out_file.write_text(text, encoding="utf-8")

        # Overwrite the canonical schema file used by the app
        canonical_file = output_dir / f"{config['output_prefix']}.txt"
        canonical_file.write_text(text, encoding="utf-8")

        print(
            f"   ✅ {domain_name}: {out_file.name}  "
            f"({n_tables} tables, {n_rels} rels, {n_measures} measures, {len(text):,} chars)"
        )


# ============================================================
# Main
# ============================================================

def main():
    parser = argparse.ArgumentParser(
        description="Extract Power BI schema via XMLA endpoint (ADOMD.NET / pyadomd)"
    )
    parser.add_argument(
        "--output", "-o",
        type=str,
        default=None,
        help="Output path for formatted schema text (default: cache/schema/schema_pack_<date>.txt)",
    )
    parser.add_argument(
        "--save-json",
        action="store_true",
        help="Also save the intermediate JSON schema pack",
    )
    args = parser.parse_args()

    print("=" * 60)
    print("🚀 Automated Schema Extraction Pipeline")
    print("   (XMLA Endpoint — pyadomd / ADOMD.NET)")
    print("=" * 60)
    print()
    print("This script runs 3 steps:")
    print("  1. Extract metadata via DAX INFO functions (XMLA endpoint)")
    print("  2. Format for LLM consumption")
    domain_names = ", ".join(c["name"] for c in DOMAIN_CONFIGS)
    print(f"  3. Split into domain schemas: {domain_names}")
    print()

    # Load .env
    load_dotenv()

    tenant_id = os.getenv("TENANT_ID")
    client_id = os.getenv("CLIENT_ID_POWERBI_SCHEMA_EXTRACTION")
    client_secret = os.getenv("CLIENT_SECRET_POWERBI_SCHEMA_EXTRACTION")
    workspace_name = os.getenv("WORKSPACE_NAME")
    database_name = os.getenv("DATABASE_NAME")

    # Validate required variables
    missing = []
    if not tenant_id:
        missing.append("TENANT_ID")
    if not client_id:
        missing.append("CLIENT_ID_POWERBI_SCHEMA_EXTRACTION")
    if not client_secret:
        missing.append("CLIENT_SECRET_POWERBI_SCHEMA_EXTRACTION")
    if not workspace_name:
        missing.append("WORKSPACE_NAME")
    if not database_name:
        missing.append("DATABASE_NAME")

    if missing:
        print(f"❌ Missing required .env variables: {', '.join(missing)}")
        print()
        print("Required variables:")
        print("  TENANT_ID                               - Azure AD tenant ID")
        print("  CLIENT_ID_POWERBI_SCHEMA_EXTRACTION     - Service principal client ID")
        print("  CLIENT_SECRET_POWERBI_SCHEMA_EXTRACTION - Service principal secret")
        print("  WORKSPACE_NAME                          - Power BI workspace name")
        print("  DATABASE_NAME                           - Semantic model / database name")
        sys.exit(1)

    print(f"🔐 Using service principal auth (XMLA endpoint)")
    print(f"   📁 Workspace: {workspace_name}")
    print(f"   📊 Database:  {database_name}")

    # Setup output paths
    date_str = datetime.now().strftime("%Y-%m-%d")

    if args.output:
        output_txt_path = Path(args.output)
    else:
        output_txt_path = project_root / "cache" / "schema" / f"schema_pack_{date_str}.txt"

    output_json_path = None
    if args.save_json:
        output_json_path = output_txt_path.with_suffix(".json")

    # Initialize XMLA client with service principal credentials
    # ADOMD.NET handles the OAuth flow internally via the powerbi:// endpoint
    client = PowerBIXmlaClient(
        workspace_name=workspace_name,
        database_name=database_name,
        client_id=client_id,
        tenant_id=tenant_id,
        client_secret=client_secret,
    )

    # Run pipeline
    # Step 1: Extract schema via XMLA
    schema_pack = run_extract_schema(client, output_json_path)

    # Step 2: Format for LLM
    formatted_text = run_format_schema(schema_pack, output_txt_path)

    # Step 3: Split into domain schemas
    run_split_schema(schema_pack, output_txt_path.parent, date_str)

    # Summary
    print("\n" + "=" * 60)
    print("✅ PIPELINE COMPLETE (XMLA Endpoint)")
    print("=" * 60)
    print(f"\n📄 Full schema: {output_txt_path}")
    if output_json_path:
        print(f"📋 JSON file: {output_json_path}")
    print(f"📂 Domain schemas: {output_txt_path.parent}")
    print()

    return output_txt_path


if __name__ == "__main__":
    main()
