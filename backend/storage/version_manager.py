"""
Version manager for schemas and prompts in Azure Blob Storage.

Used by the Admin Portal to create versions, set active version,
and upload content.
"""

import os
import json
import logging
from datetime import datetime, timezone
from typing import Dict, List, Optional

from azure.identity import DefaultAzureCredential
from azure.storage.blob import BlobServiceClient, ContentSettings

logger = logging.getLogger(__name__)


class VersionManager:
    """Manages versioned schemas and prompts in Azure Blob Storage."""

    def __init__(self):
        account_name = os.environ["AZURE_STORAGE_ACCOUNT_NAME"]
        self.schemas_container = os.environ.get("SCHEMAS_CONTAINER", "schemas")
        self.prompts_container = os.environ.get("PROMPTS_CONTAINER", "prompts")

        credential = DefaultAzureCredential()
        account_url = f"https://{account_name}.blob.core.windows.net"
        self.blob_service = BlobServiceClient(account_url=account_url, credential=credential)

    # ─── Schema Version Management ─────────────────────────────

    def create_schema_version(
        self,
        version: str,
        files: Dict[str, str],
        metadata: Optional[Dict] = None,
    ) -> Dict:
        """
        Create a new schema version by uploading files.

        Args:
            version: Version identifier (e.g., "2026-07-03_v1")
            files: Dict of {filename: content} (e.g., {"schema_work_orders.txt": "..."})
            metadata: Optional metadata dict (author, source, etc.)

        Returns:
            Dict with version info.
        """
        container_client = self.blob_service.get_container_client(self.schemas_container)

        # Upload each schema file
        for filename, content in files.items():
            blob_path = f"versions/{version}/{filename}"
            container_client.upload_blob(
                name=blob_path,
                data=content.encode("utf-8"),
                overwrite=True,
                content_settings=ContentSettings(content_type="text/plain"),
            )

        # Upload manifest
        manifest = {
            "version": version,
            "created_at": datetime.now(timezone.utc).isoformat(),
            "files": list(files.keys()),
            **(metadata or {}),
        }
        container_client.upload_blob(
            name=f"versions/{version}/manifest.json",
            data=json.dumps(manifest, indent=2).encode("utf-8"),
            overwrite=True,
            content_settings=ContentSettings(content_type="application/json"),
        )

        logger.info(f"Created schema version: {version} ({len(files)} files)")
        return manifest

    def set_active_schema_version(self, version: str) -> None:
        """Set the active schema version (updates active.json pointer)."""
        container_client = self.blob_service.get_container_client(self.schemas_container)

        # Verify version exists
        blobs = list(container_client.list_blobs(name_starts_with=f"versions/{version}/"))
        if not blobs:
            raise ValueError(f"Schema version '{version}' does not exist")

        active_data = {
            "version": version,
            "activated_at": datetime.now(timezone.utc).isoformat(),
        }
        container_client.upload_blob(
            name="active.json",
            data=json.dumps(active_data, indent=2).encode("utf-8"),
            overwrite=True,
            content_settings=ContentSettings(content_type="application/json"),
        )
        logger.info(f"Active schema version set to: {version}")

    def get_schema_version_files(self, version: str) -> Dict[str, str]:
        """Get all files for a schema version."""
        container_client = self.blob_service.get_container_client(self.schemas_container)
        files = {}

        prefix = f"versions/{version}/"
        for blob in container_client.list_blobs(name_starts_with=prefix):
            if blob.name.endswith(".txt"):
                blob_client = container_client.get_blob_client(blob.name)
                content = blob_client.download_blob().readall().decode("utf-8")
                filename = blob.name.replace(prefix, "")
                files[filename] = content

        return files

    # ─── Prompt Version Management ─────────────────────────────

    def create_prompt_version(
        self,
        version: str,
        files: Dict[str, str],
        metadata: Optional[Dict] = None,
    ) -> Dict:
        """
        Create a new prompt version by uploading files.

        Args:
            version: Version identifier (e.g., "v5")
            files: Dict of {relative_path: content}
                   e.g., {"generator/dax_generator_prompt_work_orders.py": "..."}
            metadata: Optional metadata dict (author, changelog, etc.)

        Returns:
            Dict with version info.
        """
        container_client = self.blob_service.get_container_client(self.prompts_container)

        for filepath, content in files.items():
            blob_path = f"versions/{version}/{filepath}"
            content_type = "text/x-python" if filepath.endswith(".py") else "text/plain"
            container_client.upload_blob(
                name=blob_path,
                data=content.encode("utf-8"),
                overwrite=True,
                content_settings=ContentSettings(content_type=content_type),
            )

        # Upload manifest
        manifest = {
            "version": version,
            "created_at": datetime.now(timezone.utc).isoformat(),
            "files": list(files.keys()),
            **(metadata or {}),
        }
        container_client.upload_blob(
            name=f"versions/{version}/manifest.json",
            data=json.dumps(manifest, indent=2).encode("utf-8"),
            overwrite=True,
            content_settings=ContentSettings(content_type="application/json"),
        )

        logger.info(f"Created prompt version: {version} ({len(files)} files)")
        return manifest

    def set_active_prompt_version(self, version: str) -> None:
        """Set the active prompt version (updates active.json pointer)."""
        container_client = self.blob_service.get_container_client(self.prompts_container)

        # Verify version exists
        blobs = list(container_client.list_blobs(name_starts_with=f"versions/{version}/"))
        if not blobs:
            raise ValueError(f"Prompt version '{version}' does not exist")

        active_data = {
            "version": version,
            "activated_at": datetime.now(timezone.utc).isoformat(),
        }
        container_client.upload_blob(
            name="active.json",
            data=json.dumps(active_data, indent=2).encode("utf-8"),
            overwrite=True,
            content_settings=ContentSettings(content_type="application/json"),
        )
        logger.info(f"Active prompt version set to: {version}")

    def get_prompt_version_files(self, version: str) -> Dict[str, str]:
        """Get all files for a prompt version."""
        container_client = self.blob_service.get_container_client(self.prompts_container)
        files = {}

        prefix = f"versions/{version}/"
        for blob in container_client.list_blobs(name_starts_with=prefix):
            if not blob.name.endswith("manifest.json"):
                blob_client = container_client.get_blob_client(blob.name)
                content = blob_client.download_blob().readall().decode("utf-8")
                filename = blob.name.replace(prefix, "")
                files[filename] = content

        return files
