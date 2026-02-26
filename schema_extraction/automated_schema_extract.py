"""
Automated Schema Extraction Pipeline

This script runs 3 tools in sequence:
1. extract_schema - Extract metadata from Power BI via XMLA DMVs
2. format_schema_for_prompt - Convert schema JSON to LLM-friendly text format
3. split_schema - Split into domain-specific schemas (transactions & feedback)

Usage:
    python development/tools/automated_schema_extract.py
    
    # Or with custom output path:
    python development/tools/automated_schema_extract.py --output cache/schema/my_schema.txt
"""

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

# Import the tools
from extract_schema import MetadataExtractor, DataTypeMapper
from format_schema_for_prompt import SchemaPackFormatter
from backend.tools.auth import load_environment, AuthenticationManager
from execute_dax import DaxQueryExecutor
from split_schema import SchemaSplitter, DOMAIN_CONFIGS, format_domain_schema


def run_extract_schema(executor: DaxQueryExecutor, output_json_path: Path = None) -> dict:
    """
    Step 1: Extract schema metadata from Power BI.
    
    Args:
        executor: DAX query executor with active connection
        output_json_path: Optional path to save the JSON schema pack
        
    Returns:
        dict: The extracted schema pack
    """
    print("\n" + "=" * 60)
    print("📊 STEP 1: Extracting Schema from Power BI")
    print("=" * 60)
    
    # Extract all metadata from DMVs
    metadata_extractor = MetadataExtractor(executor)
    metadata = metadata_extractor.fetch_all_metadata()
    
    # Build denormalized schema pack
    schema_pack = metadata_extractor.build_schema_pack(metadata)
    
    # Optionally save to JSON file
    if output_json_path:
        output_json_path.parent.mkdir(parents=True, exist_ok=True)
        output_json_path.write_text(
            json.dumps(schema_pack, indent=2, ensure_ascii=False),
            encoding="utf-8"
        )
        print(f"   ✅ Schema JSON saved to: {output_json_path}")
    
    # Print summary
    tables = schema_pack.get("tables", [])
    measures = schema_pack.get("measures", [])
    relationships = schema_pack.get("relationships", [])
    
    print(f"   📋 Tables: {len(tables)}")
    print(f"   📏 Measures: {len(measures)}")
    print(f"   🔗 Relationships: {len(relationships)}")
    
    return schema_pack


def run_format_schema(schema_pack: dict, output_txt_path: Path) -> str:
    """
    Step 2: Format schema pack into LLM-friendly text.
    
    Args:
        schema_pack: The schema pack dictionary
        output_txt_path: Path to save the formatted text
        
    Returns:
        str: The formatted schema text
    """
    print("\n" + "=" * 60)
    print("📝 STEP 2: Formatting Schema for LLM Prompt")
    print("=" * 60)
    
    # Create formatter (exclude hidden objects for cleaner output)
    formatter = SchemaPackFormatter(include_hidden=False)
    
    # Load schema directly from dict (not from file)
    formatter.schema = schema_pack
    
    # Generate formatted text
    formatted_text = formatter.format()
    
    # Save to file
    output_txt_path.parent.mkdir(parents=True, exist_ok=True)
    output_txt_path.write_text(formatted_text, encoding="utf-8")
    
    print(f"   ✅ Formatted schema saved to: {output_txt_path}")
    print(f"   📄 Size: {len(formatted_text):,} characters")
    
    return formatted_text


def run_split_schema(schema_pack: dict, output_dir: Path, date_str: str) -> None:
    """
    Step 3: Split full schema into domain-specific schemas.
    
    Args:
        schema_pack: The full schema pack dictionary
        output_dir: Directory to save domain schema files
        date_str: Date string for filenames
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
        
        print(f"   ✅ {domain_name}: {out_file.name}  "
              f"({n_tables} tables, {n_rels} rels, {n_measures} measures, {len(text):,} chars)")


def main():
    """Main execution: run extract_schema, format_schema_for_prompt, then split_schema."""
    parser = argparse.ArgumentParser(
        description="Extract Power BI schema and format for LLM prompts"
    )
    parser.add_argument(
        "--output", "-o",
        type=str,
        default=None,
        help="Output path for formatted schema text (default: cache/schema/schema_<date>.txt)"
    )
    parser.add_argument(
        "--save-json",
        action="store_true",
        help="Also save the intermediate JSON schema pack"
    )
    
    args = parser.parse_args()
    
    print("=" * 60)
    print("🚀 Automated Schema Extraction Pipeline")
    print("=" * 60)
    print()
    print("This script runs 3 tools in sequence:")
    print("  1. extract_schema - Extract metadata from Power BI")
    print("  2. format_schema_for_prompt - Format for LLM consumption")
    print("  3. split_schema - Split into domain schemas (transactions & feedback)")
    print()
    
    # Load environment and authenticate
    print("🔐 Authenticating with Power BI...")
    tenant_id, client_id, _, workspace_name, database_name, adomd_dll = load_environment()
    
    # Use device flow for interactive authentication (no persistent cache)
    # This is a development utility script - production uses OBO flow
    auth_manager = AuthenticationManager(
        tenant_id, 
        client_id, 
        persist_cache=False,  # Don't persist tokens to disk
    )
    access_token = auth_manager.acquire_token()
    
    print(f"   ✅ Authenticated")
    print(f"   📁 Workspace: {workspace_name}")
    print(f"   📊 Database: {database_name}")
    
    # Setup output paths
    date_str = datetime.now().strftime("%Y-%m-%d")
    
    if args.output:
        output_txt_path = Path(args.output)
    else:
        output_txt_path = project_root / "cache" / "schema" / f"schema_pack_{date_str}.txt"
    
    output_json_path = None
    if args.save_json:
        output_json_path = output_txt_path.with_suffix(".json")
    
    # Initialize DAX executor
    executor = DaxQueryExecutor(adomd_dll, workspace_name, database_name, access_token)
    
    # Run pipeline
    with executor:
        # Step 1: Extract schema
        schema_pack = run_extract_schema(executor, output_json_path)
        
        # Step 2: Format for LLM
        formatted_text = run_format_schema(schema_pack, output_txt_path)
    
    # Step 3: Split into domain schemas (runs outside executor context)
    run_split_schema(schema_pack, output_txt_path.parent, date_str)
    
    # Summary
    print("\n" + "=" * 60)
    print("✅ PIPELINE COMPLETE")
    print("=" * 60)
    print(f"\n📄 Full schema: {output_txt_path}")
    if output_json_path:
        print(f"📋 JSON file: {output_json_path}")
    print(f"📂 Domain schemas: {output_txt_path.parent}")
    print()
    
    return output_txt_path


if __name__ == "__main__":
    main()
