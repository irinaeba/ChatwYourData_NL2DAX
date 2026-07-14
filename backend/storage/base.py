"""
Base storage interface and factory.
"""

import os
import logging
from abc import ABC, abstractmethod
from typing import Optional, Dict, List

logger = logging.getLogger(__name__)


class StorageBackend(ABC):
    """Abstract base class for storage backends."""

    @abstractmethod
    def get_schema(self, domain: str, version: Optional[str] = None) -> str:
        """
        Load schema text for a domain.

        Args:
            domain: Domain name (e.g., "work_orders", "inspections")
            version: Optional version identifier. If None, uses active version.

        Returns:
            Schema text content.

        Raises:
            FileNotFoundError: If schema not found.
        """
        ...

    @abstractmethod
    def get_prompt(self, prompt_path: str, version: Optional[str] = None) -> str:
        """
        Load prompt content.

        Args:
            prompt_path: Relative path within prompts storage
                         (e.g., "generator/work_orders.py", "validator/global_instructions.py")
            version: Optional version identifier. If None, uses active version.

        Returns:
            Prompt file content as text.

        Raises:
            FileNotFoundError: If prompt not found.
        """
        ...

    @abstractmethod
    def list_schema_versions(self) -> List[Dict]:
        """List all available schema versions with metadata."""
        ...

    @abstractmethod
    def list_prompt_versions(self) -> List[Dict]:
        """List all available prompt versions with metadata."""
        ...

    @abstractmethod
    def get_active_schema_version(self) -> str:
        """Get the currently active schema version identifier."""
        ...

    @abstractmethod
    def get_active_prompt_version(self) -> str:
        """Get the currently active prompt version identifier."""
        ...

    def invalidate_cache(self):
        """Clear any in-memory caches. Override if backend uses caching."""
        pass


def get_storage_backend() -> StorageBackend:
    """
    Factory function. Returns the appropriate storage backend based on
    STORAGE_BACKEND env var ("local" or "azure").
    """
    backend_type = os.environ.get("STORAGE_BACKEND", "local").lower()

    if backend_type == "azure":
        from backend.storage.azure_blob import AzureBlobStorageBackend
        logger.info("Using Azure Blob Storage backend")
        return AzureBlobStorageBackend()
    else:
        from backend.storage.local import LocalStorageBackend
        logger.info("Using local filesystem storage backend")
        return LocalStorageBackend()
