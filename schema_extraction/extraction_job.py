"""
Schema Extraction Job — Azure Blob Storage Output

Wraps the existing automated_schema_extract.py to run as a Container Apps Job.
On completion, uploads the extracted schemas to Azure Blob Storage as a new version.

Usage (container):
    python schema_extraction/extraction_job.py

Environment variables required:
    AZURE_STORAGE_ACCOUNT_NAME  - Storage account name
    SCHEMAS_CONTAINER           - Container for schemas (default: "schemas")
    TENANT_ID                   - Azure AD tenant
    CLIENT_ID_POWERBI_SCHEMA_EXTRACTION    - SP client ID
    CLIENT_SECRET_POWERBI_SCHEMA_EXTRACTION - SP client secret
    WORKSPACE_NAME              - Power BI workspace
    DATABASE_NAME               - Semantic model name
"""

import os
import sys
import json
from pathlib import Path
from datetime import datetime, timezone

# Add project root to path
project_root = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(project_root))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from dotenv import load_dotenv
load_dotenv()


def run_extraction_to_blob():
    """Run schema extraction and upload results to Azure Blob Storage."""
    from azure.identity import DefaultAzureCredential
    from azure.storage.blob import BlobServiceClient, ContentSettings

    from execute_dax import PowerBIXmlaClient
    from extract_schema import MetadataExtractor
    from format_schema_for_prompt import SchemaPackFormatter
    from split_schema import SchemaSplitter, DOMAIN_CONFIGS, format_domain_schema

    # ─── Config ──────────────────────────────────────────
    account_name = os.environ["AZURE_STORAGE_ACCOUNT_NAME"]
    container_name = os.environ.get("SCHEMAS_CONTAINER", "schemas")
    tenant_id = os.environ["TENANT_ID"]
    client_id = os.environ["CLIENT_ID_POWERBI_SCHEMA_EXTRACTION"]
    client_secret = os.environ["CLIENT_SECRET_POWERBI_SCHEMA_EXTRACTION"]
    workspace_name = os.environ["WORKSPACE_NAME"]
    database_name = os.environ["DATABASE_NAME"]

    date_str = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    version = f"{date_str}_v1"

    # Check if version already exists, increment
    credential = DefaultAzureCredential()
    blob_service = BlobServiceClient(
        account_url=f"https://{account_name}.blob.core.windows.net",
        credential=credential,
    )
    container_client = blob_service.get_container_client(container_name)

    # Find next version number for today
    existing = set()
    for blob in container_client.list_blobs(name_starts_with=f"versions/{date_str}_v"):
        parts = blob.name.split("/")
        if len(parts) >= 2:
            existing.add(parts[1])

    v_num = 1
    while f"{date_str}_v{v_num}" in existing:
        v_num += 1
    version = f"{date_str}_v{v_num}"

    print(f"{'='*60}")
    print(f"🚀 Schema Extraction Job → Azure Blob Storage")
    print(f"   Version: {version}")
    print(f"   Storage: {account_name}/{container_name}")
    print(f"   Workspace: {workspace_name}")
    print(f"   Database: {database_name}")
    print(f"{'='*60}")

    # ─── Step 1: Extract via XMLA ────────────────────────
    print("\n📊 Step 1: Extracting schema via XMLA...")
    client = PowerBIXmlaClient(
        workspace_name=workspace_name,
        database_name=database_name,
        client_id=client_id,
        tenant_id=tenant_id,
        client_secret=client_secret,
    )
    extractor = MetadataExtractor(client)
    schema_pack = extractor.extract_all()

    tables = schema_pack["model"].get("tables", [])
    measures = schema_pack["model"].get("measures", [])
    print(f"   ✅ Extracted: {len(tables)} tables, {len(measures)} measures")

    # ─── Step 2: Upload full schema pack JSON ────────────
    print("\n📤 Step 2: Uploading schema pack JSON...")
    schema_json = json.dumps(schema_pack, indent=2, ensure_ascii=False)
    container_client.upload_blob(
        name=f"versions/{version}/schema_pack.json",
        data=schema_json.encode("utf-8"),
        overwrite=True,
        content_settings=ContentSettings(content_type="application/json"),
    )

    # ─── Step 3: Format full schema text ─────────────────
    formatter = SchemaPackFormatter(include_hidden=False)
    formatter.schema = schema_pack
    full_text = formatter.format()
    container_client.upload_blob(
        name=f"versions/{version}/schema_pack.txt",
        data=full_text.encode("utf-8"),
        overwrite=True,
        content_settings=ContentSettings(content_type="text/plain"),
    )

    # ─── Step 4: Split into domain schemas ───────────────
    print("\n📂 Step 3: Splitting into domain schemas...")
    splitter = SchemaSplitter(schema_pack)

    for config in DOMAIN_CONFIGS:
        domain_name = config["name"]
        sub_schema = splitter.build_domain_schema(config)
        text = format_domain_schema(sub_schema, config["label"], config["description"])

        blob_name = f"versions/{version}/schema_{domain_name}.txt"
        container_client.upload_blob(
            name=blob_name,
            data=text.encode("utf-8"),
            overwrite=True,
            content_settings=ContentSettings(content_type="text/plain"),
        )
        print(f"   ✅ {domain_name}: {len(text):,} chars")

    # ─── Step 5: Write manifest ──────────────────────────
    manifest = {
        "version": version,
        "created_at": datetime.now(timezone.utc).isoformat(),
        "source": "automated_extraction",
        "workspace": workspace_name,
        "database": database_name,
        "tables": len(tables),
        "measures": len(measures),
        "domains": [c["name"] for c in DOMAIN_CONFIGS],
    }
    container_client.upload_blob(
        name=f"versions/{version}/manifest.json",
        data=json.dumps(manifest, indent=2).encode("utf-8"),
        overwrite=True,
        content_settings=ContentSettings(content_type="application/json"),
    )

    print(f"\n{'='*60}")
    print(f"✅ Schema extraction complete!")
    print(f"   Version: {version}")
    print(f"   Files uploaded: {len(DOMAIN_CONFIGS) + 3}")
    print(f"{'='*60}")

    return version


if __name__ == "__main__":
    version = run_extraction_to_blob()
    print(f"\nDone. Version: {version}")
