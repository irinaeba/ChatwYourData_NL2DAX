"""
Azure Blob Storage backend (production).

Reads schemas and prompts from Azure Blob containers with versioning.

Storage layout:
  schemas/
    active.json          → {"version": "2026-07-03_v1"}
    versions/<version>/schema_work_orders.txt
    versions/<version>/schema_inspections.txt
    ...

  prompts/
    active.json          → {"version": "v5"}
    versions/<version>/generator/dax_generator_prompt_work_orders.py
    versions/<version>/validator/dax_validator_global_instructions.py
    versions/<version>/query_planner_prompt.py
    versions/<version>/answer_formatter_prompt.py
    ...
"""

import os
import json
import time
import logging
from typing import Optional, Dict, List

from azure.identity import DefaultAzureCredential
from azure.storage.blob import BlobServiceClient

from backend.storage.base import StorageBackend

logger = logging.getLogger(__name__)

# Cache TTL in seconds
_CACHE_TTL = int(os.environ.get("STORAGE_CACHE_TTL", "300"))


class AzureBlobStorageBackend(StorageBackend):
    """Reads schemas and prompts from Azure Blob Storage with in-memory caching."""

    def __init__(self):
        account_name = os.environ["AZURE_STORAGE_ACCOUNT_NAME"]
        self.schemas_container = os.environ.get("SCHEMAS_CONTAINER", "schemas")
        self.prompts_container = os.environ.get("PROMPTS_CONTAINER", "prompts")

        credential = DefaultAzureCredential()
        account_url = f"https://{account_name}.blob.core.windows.net"
        self.blob_service = BlobServiceClient(account_url=account_url, credential=credential)

        # In-memory cache: {key: (content, timestamp)}
        self._cache: Dict[str, tuple] = {}

    def _read_blob(self, container: str, blob_path: str) -> str:
        """Read a blob as UTF-8 text with caching."""
        cache_key = f"{container}/{blob_path}"

        # Check cache
        if cache_key in self._cache:
            content, ts = self._cache[cache_key]
            if time.time() - ts < _CACHE_TTL:
                return content

        # Fetch from blob storage
        container_client = self.blob_service.get_container_client(container)
        blob_client = container_client.get_blob_client(blob_path)

        try:
            data = blob_client.download_blob().readall()
            content = data.decode("utf-8")
        except Exception as e:
            raise FileNotFoundError(
                f"Blob not found: {container}/{blob_path} — {e}"
            )

        # Update cache
        self._cache[cache_key] = (content, time.time())
        return content

    def _get_active_version(self, container: str) -> str:
        """Read active.json from a container to get the current version."""
        try:
            active_json = self._read_blob(container, "active.json")
            return json.loads(active_json)["version"]
        except (FileNotFoundError, KeyError, json.JSONDecodeError) as e:
            logger.warning(f"Could not read active version from {container}: {e}")
            raise

    def get_schema(self, domain: str, version: Optional[str] = None) -> str:
        """Load schema from schemas/versions/<version>/schema_<domain>.txt"""
        if version is None:
            version = self._get_active_version(self.schemas_container)

        blob_path = f"versions/{version}/schema_{domain}.txt"
        return self._read_blob(self.schemas_container, blob_path)

    def get_prompt(self, prompt_path: str, version: Optional[str] = None) -> str:
        """Load prompt from prompts/versions/<version>/<prompt_path>"""
        if version is None:
            version = self._get_active_version(self.prompts_container)

        blob_path = f"versions/{version}/{prompt_path}"
        return self._read_blob(self.prompts_container, blob_path)

    def list_schema_versions(self) -> List[Dict]:
        """List all schema versions by enumerating version prefixes."""
        container_client = self.blob_service.get_container_client(self.schemas_container)
        versions = set()

        for blob in container_client.list_blobs(name_starts_with="versions/"):
            # versions/2026-07-03_v1/schema_work_orders.txt → "2026-07-03_v1"
            parts = blob.name.split("/")
            if len(parts) >= 2:
                versions.add(parts[1])

        active = self.get_active_schema_version()
        return sorted(
            [
                {"version": v, "is_active": v == active, "source": "azure"}
                for v in versions
            ],
            key=lambda x: x["version"],
            reverse=True,
        )

    def list_prompt_versions(self) -> List[Dict]:
        """List all prompt versions by enumerating version prefixes."""
        container_client = self.blob_service.get_container_client(self.prompts_container)
        versions = set()

        for blob in container_client.list_blobs(name_starts_with="versions/"):
            parts = blob.name.split("/")
            if len(parts) >= 2:
                versions.add(parts[1])

        active = self.get_active_prompt_version()
        return sorted(
            [
                {"version": v, "is_active": v == active, "source": "azure"}
                for v in versions
            ],
            key=lambda x: x["version"],
            reverse=True,
        )

    def get_active_schema_version(self) -> str:
        return self._get_active_version(self.schemas_container)

    def get_active_prompt_version(self) -> str:
        return self._get_active_version(self.prompts_container)

    def invalidate_cache(self):
        """Clear all cached blobs."""
        self._cache.clear()
        logger.info("Storage cache invalidated")
