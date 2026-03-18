"""
Authentication and Authorization Module

This module handles all authentication and authorization logic for connecting to
Microsoft Power BI and Azure services. It manages token acquisition, caching,
and configuration loading.
"""

import os
from dotenv import load_dotenv  # Add this import
import json
import base64
import msal
import time
from typing import Optional, Callable, Union
from dataclasses import dataclass, field
from azure.identity import ClientSecretCredential, DefaultAzureCredential, get_bearer_token_provider


# -------------------------
# Environment Setup
# -------------------------
def load_environment():
    """
    Load environment variables from .env file.
    
    Returns:
        tuple: (TENANT_ID, CLIENT_ID_POWERBI, CLIENT_SECRET_POWERBI, WORKSPACE_NAME, 
                DATABASE_NAME, ADOMD_DLL)
        
        Note: CLIENT_SECRET_POWERBI is optional - it's only needed for non-interactive
        (client credentials) authentication. If not provided, interactive
        (device flow) authentication will be used.
    
    Raises:
        RuntimeError: If required environment variables are missing.
    """
    load_dotenv()
    
    TENANT_ID = os.getenv("TENANT_ID")
    CLIENT_ID_POWERBI = os.getenv("CLIENT_ID_POWERBI")
    CLIENT_SECRET_POWERBI = os.getenv("CLIENT_SECRET_POWERBI")  # Optional
    WORKSPACE_NAME = os.getenv("WORKSPACE_NAME")
    DATABASE_NAME = os.getenv("DATABASE_NAME")
    ADOMD_DLL = os.getenv("ADOMD_DLL")
    
    missing = [
        k for k, v in {
            "TENANT_ID": TENANT_ID,
            "CLIENT_ID_POWERBI": CLIENT_ID_POWERBI,
            "WORKSPACE_NAME": WORKSPACE_NAME,
            "DATABASE_NAME": DATABASE_NAME,
            "ADOMD_DLL": ADOMD_DLL,
        }.items() if not v
    ]
    
    if missing:
        raise RuntimeError(f"Missing values in .env: {', '.join(missing)}")
    
    return TENANT_ID, CLIENT_ID_POWERBI, CLIENT_SECRET_POWERBI, WORKSPACE_NAME, DATABASE_NAME, ADOMD_DLL


# -------------------------
# Token Utilities
# -------------------------
def decode_jwt_payload(token: str) -> dict:
    """
    Decode JWT payload WITHOUT validation.
    
    This function is safe for debugging purposes only. Do not use for 
    security decisions.
    
    Args:
        token (str): JWT token string.
    
    Returns:
        dict: Decoded payload or error information.
    """
    try:
        parts = token.split(".")
        if len(parts) < 2:
            return {}
        payload = parts[1]
        payload += "=" * (-len(payload) % 4)  # base64 padding
        return json.loads(base64.urlsafe_b64decode(payload.encode("utf-8")))
    except Exception as e:
        return {"error": str(e)}


# -------------------------
# Authentication Manager
# -------------------------
class AuthenticationManager:
    """
    Manages authentication and token acquisition for Power BI and Azure services.
    
    This class handles:
    - MSAL configuration and initialization (Public or Confidential client)
    - Token acquisition (silent, device flow, or client credentials)
    - Token caching for faster subsequent authentications
    
    Supports two authentication modes:
    1. Interactive (device flow): Requires user interaction, no secret needed
    2. Non-interactive (client credentials): Uses client secret, no user interaction
    
    Token caching:
    - For device flow: Enable persist_cache=True to avoid re-authentication
    - For client credentials: Caching is in-memory only (tokens are short-lived)
    """
    
    # Default cache file location
    DEFAULT_CACHE_FILE = ".msal_token_cache.bin"
    
    def __init__(self, tenant_id: str, client_id: str, 
                 client_secret: str = None, persist_cache: bool = False,
                 cache_file: str = None):
        """
        Initialize the AuthenticationManager.
        
        Args:
            tenant_id (str): Azure tenant ID.
            client_id (str): MSAL client/application ID.
            client_secret (str, optional): Client secret for non-interactive auth.
                                          If provided, uses Confidential Client flow.
                                          If not provided, uses Public Client (device flow).
            persist_cache (bool): If True, persist token cache to disk for device flow.
                                 This allows silent re-authentication without user login.
                                 Default is False (in-memory only).
            cache_file (str, optional): Path to cache file. Defaults to .msal_token_cache.bin
        
        Raises:
            ValueError: If tenant_id or client_id is empty.
        """
        if not tenant_id or not client_id:
            raise ValueError("tenant_id and client_id are required")
        
        self.tenant_id = tenant_id
        self.client_id = client_id
        self.client_secret = client_secret
        self.authority = f"https://login.microsoftonline.com/{tenant_id}"
        # Default scopes for Power BI
        self.default_scopes = ["https://analysis.windows.net/powerbi/api/.default"]
        self.scopes = self.default_scopes
        
        # Token cache settings
        self.persist_cache = persist_cache
        self.cache_file = cache_file or self.DEFAULT_CACHE_FILE
        
        # Initialize MSAL cache
        self.cache = msal.SerializableTokenCache()
        if self.persist_cache:
            self._load_cache()
        
        # Choose between Public and Confidential client based on secret availability
        if client_secret:
            # Confidential Client Application (non-interactive)
            self.app = msal.ConfidentialClientApplication(
                client_id,
                client_credential=client_secret,
                authority=self.authority,
                token_cache=self.cache
            )
            self.is_confidential = True
        else:
            # Public Client Application (interactive with device flow)
            self.app = msal.PublicClientApplication(
                client_id,
                authority=self.authority,
                token_cache=self.cache
            )
            self.is_confidential = False
        
        self.access_token = None
    
    def _load_cache(self):
        """Load token cache from disk if it exists."""
        if os.path.exists(self.cache_file):
            try:
                with open(self.cache_file, "r") as f:
                    self.cache.deserialize(f.read())
            except Exception as e:
                print(f"Warning: Could not load token cache: {e}")
    
    def _save_cache(self):
        """Save token cache to disk if persistence is enabled."""
        if self.persist_cache and self.cache.has_state_changed:
            try:
                with open(self.cache_file, "w") as f:
                    f.write(self.cache.serialize())
            except Exception as e:
                print(f"Warning: Could not save token cache: {e}")
    
    def acquire_token(self, resource: str = "powerbi", force_refresh: bool = False) -> str:
        """
        Acquire an access token using appropriate flow based on client type.
        
        For Confidential Client (with secret): Uses silent or client credentials flow
        For Public Client (no secret): Uses silent or device flow (user interaction)
        
        Args:
            resource (str): Resource for which to acquire a token.
                          Options: "powerbi" (default), "azure" (for Azure services like OpenAI)
            force_refresh (bool): If True, skip cache and acquire fresh token.
        
        Returns:
            str: Access token for API requests.
        
        Raises:
            RuntimeError: If token acquisition fails.
        """
        # Set scopes based on resource
        if resource == "azure":
            # Scope for Azure Cognitive Services (including Azure OpenAI)
            self.scopes = ["https://cognitiveservices.azure.com/.default"]
        elif resource == "powerbi":
            # Scope for Power BI
            self.scopes = self.default_scopes
        else:
            raise ValueError(f"Unknown resource: {resource}")
        
        result = None
        
        # Try silent first (works for both Public and Confidential clients) unless force_refresh
        if not force_refresh:
            accounts = self.app.get_accounts()
            if accounts:
                result = self.app.acquire_token_silent(self.scopes, account=accounts[0])
        
        # If silent fails, use appropriate flow based on client type
        if not result:
            if self.is_confidential:
                # Confidential Client: Use client credentials (no user interaction)
                result = self.app.acquire_token_for_client(scopes=self.scopes)
            else:
                # Public Client: Use device flow (requires user interaction)
                flow = self.app.initiate_device_flow(scopes=self.scopes)
                if "message" not in flow:
                    raise RuntimeError("Failed to create device flow")
                print(flow["message"])
                result = self.app.acquire_token_by_device_flow(flow)
        
        # Check for errors
        if "access_token" not in result:
            raise RuntimeError(f"Token acquisition failed: {result}")
        
        # Save cache for faster future authentication
        self._save_cache()
        
        self.access_token = result["access_token"]
        auth_type = "client credentials" if self.is_confidential else "device flow"
        print(f"[OK] Access token acquired ({auth_type})")
        return self.access_token
    
    def acquire_token_with_message(self, resource: str = "powerbi", force_refresh: bool = False) -> Optional[str]:
        """
        Acquire an access token, returning the device flow message if interactive login is needed.
        
        This method is designed for DevUI integration - it returns the device flow message
        so it can be displayed in the chat interface rather than just printed to console.
        
        Args:
            resource (str): Resource for which to acquire a token.
            force_refresh (bool): If True, skip cache and acquire fresh token.
        
        Returns:
            Optional[str]: Device flow message if interactive login was needed, None otherwise.
            The access token is stored in self.access_token.
        
        Raises:
            RuntimeError: If token acquisition fails.
        """
        # Set scopes based on resource
        if resource == "azure":
            self.scopes = ["https://cognitiveservices.azure.com/.default"]
        elif resource == "powerbi":
            self.scopes = self.default_scopes
        else:
            raise ValueError(f"Unknown resource: {resource}")
        
        result = None
        device_flow_message = None
        
        # Try silent first unless force_refresh
        if not force_refresh:
            accounts = self.app.get_accounts()
            if accounts:
                result = self.app.acquire_token_silent(self.scopes, account=accounts[0])
        
        # If silent fails, use appropriate flow
        if not result:
            if self.is_confidential:
                result = self.app.acquire_token_for_client(scopes=self.scopes)
            else:
                # Public Client: Use device flow
                flow = self.app.initiate_device_flow(scopes=self.scopes)
                if "message" not in flow:
                    raise RuntimeError("Failed to create device flow")
                
                # Capture the message for DevUI display
                device_flow_message = flow["message"]
                print(device_flow_message)  # Also print to console
                
                result = self.app.acquire_token_by_device_flow(flow)
        
        # Check for errors
        if "access_token" not in result:
            raise RuntimeError(f"Token acquisition failed: {result}")
        
        # Save cache for faster future authentication
        self._save_cache()
        
        self.access_token = result["access_token"]
        auth_type = "client credentials" if self.is_confidential else "device flow"
        print(f"[OK] Access token acquired ({auth_type})")
        
        return device_flow_message
    
    def get_token(self) -> str:
        """
        Get the current access token. Acquires one if not already obtained.
        
        Returns:
            str: Access token for API requests.
        """
        if not self.access_token:
            self.acquire_token()
        return self.access_token
    
    def get_decoded_token_info(self) -> dict:
        """
        Get decoded JWT payload information (for debugging only).
        
        Returns:
            dict: Decoded token claims.
        """
        if not self.access_token:
            self.acquire_token()
        return decode_jwt_payload(self.access_token)


def get_authenticated_token(tenant_id: str, client_id: str) -> str:
    """
    Convenience function to quickly get an authenticated token.
    
    Args:
        tenant_id (str): Azure tenant ID.
        client_id (str): MSAL client/application ID.
    
    Returns:
        str: Access token for API requests.
    """
    auth_manager = AuthenticationManager(tenant_id, client_id)
    return auth_manager.acquire_token()


# ============================================================
# Azure OpenAI Configuration and Authentication
# ============================================================

class AzureOpenAIConfig:
    """
    Centralized configuration for Azure OpenAI services.
    
    This class loads and validates all Azure OpenAI configuration from
    environment variables, providing a single source of truth for
    endpoint, deployment, and authentication settings.
    """
    
    def __init__(self):
        """Load configuration from environment variables."""
        load_dotenv()
        
        # Azure OpenAI endpoint and deployment
        self.endpoint = os.getenv("AZURE_OPENAI_ENDPOINT")
        self.deployment_name = os.getenv("AZURE_OPENAI_DEPLOYMENT")
        self.api_version = os.getenv("AZURE_OPENAI_API_VERSION", "2024-02-15-preview")
        
        # Service principal credentials for Azure OpenAI
        self.tenant_id = os.getenv("TENANT_ID")
        self.client_id = os.getenv("CLIENT_ID_OPENAI")
        self.client_secret = os.getenv("CLIENT_SECRET_OPENAI")
        
        # API Key - only use if service principal credentials are NOT available
        self._has_service_principal = bool(
            self.tenant_id and self.client_id and self.client_secret
        )
        self.api_key = None if self._has_service_principal else os.getenv("AZURE_OPENAI_API_KEY")
        
        # Agent behavior defaults
        self.temperature = 0.2  # Lower temperature for deterministic generation
        self.max_tokens = 4000  # Increased for gpt-5-mini with large prompts
        self.top_p = 0.95
    
    @property
    def has_service_principal(self) -> bool:
        """Check if service principal authentication is configured."""
        return self._has_service_principal
    
    @property
    def has_api_key(self) -> bool:
        """Check if API key authentication is configured."""
        return bool(self.api_key)
    
    def validate(self) -> None:
        """
        Validate that required configuration is available.

        Raises:
            RuntimeError: If required environment variables are missing.
        """
        missing = []
        if not self.endpoint:
            missing.append("AZURE_OPENAI_ENDPOINT")
        if not self.deployment_name:
            missing.append("AZURE_OPENAI_DEPLOYMENT")
        
        # Check for either API key OR service principal credentials
        if not self.has_api_key and not self.has_service_principal:
            missing.append("Either AZURE_OPENAI_API_KEY or (TENANT_ID, CLIENT_ID_OPENAI, CLIENT_SECRET_OPENAI)")

        if missing:
            raise RuntimeError(f"Missing required .env values: {', '.join(missing)}")


class AzureOpenAIAuthProvider:
    """
    Provides authentication for Azure OpenAI with token caching.
    """
    
    # Class-level token cache (shared across instances)
    _cached_credential: Optional[ClientSecretCredential] = None
    _cached_token_provider: Optional[Callable] = None
    _cache_config_hash: Optional[str] = None
    
    def __init__(self, config: AzureOpenAIConfig = None):
        self.config = config or AzureOpenAIConfig()
        self._init_cached_credential()
    
    def _get_config_hash(self) -> str:
        """Generate hash of config to detect changes."""
        return f"{self.config.tenant_id}:{self.config.client_id}:{self.config.client_secret}"
    
    def _init_cached_credential(self):
        """Initialize or reuse cached credential."""
        config_hash = self._get_config_hash()
        
        # Reuse cached credential if config unchanged
        if (AzureOpenAIAuthProvider._cached_credential is not None and 
            AzureOpenAIAuthProvider._cache_config_hash == config_hash):
            self._credential = AzureOpenAIAuthProvider._cached_credential
            self._token_provider = AzureOpenAIAuthProvider._cached_token_provider
            return
        
        # Create new credential and cache it
        self._credential = ClientSecretCredential(
            tenant_id=self.config.tenant_id,
            client_id=self.config.client_id,
            client_secret=self.config.client_secret,
        )
        self._token_provider = get_bearer_token_provider(
            self._credential,
            "https://cognitiveservices.azure.com/.default"
        )
        
        # Cache at class level
        AzureOpenAIAuthProvider._cached_credential = self._credential
        AzureOpenAIAuthProvider._cached_token_provider = self._token_provider
        AzureOpenAIAuthProvider._cache_config_hash = config_hash
    
    @property
    def credential(self) -> ClientSecretCredential:
        return self._credential
    
    @property
    def token_provider(self) -> Callable:
        return self._token_provider
    
    @classmethod
    def clear_cache(cls):
        """Clear the cached credentials (useful for testing or credential rotation)."""
        cls._cached_credential = None
        cls._cached_token_provider = None
        cls._cache_config_hash = None
    
    @classmethod
    def prewarm_token(cls, config: 'AzureOpenAIConfig' = None):
        """
        Pre-warm the token cache by acquiring a token at startup.
        
        This eliminates the ~2-3s OIDC discovery delay on the first request.
        Call this during application initialization.
        
        Args:
            config: Optional config, uses default if not provided
        """
        provider = cls(config=config)
        try:
            # Force token acquisition to warm the cache
            _ = provider.token_provider()
            print("[OK] Azure OpenAI token pre-warmed")
        except Exception as e:
            print(f"[WARN] Token pre-warm failed: {e}")

    def get_token(self) -> str:
        """
        Get an access token for Azure OpenAI.
        
        Returns:
            str: Access token string
        """
        token = self._credential.get_token(self._scope)
        return token.token


# ============================================================
# Core42 Compass API Configuration
# ============================================================

class CompassConfig:
    """
    Configuration for Core42 Compass GPT-5.1 API.
    
    OpenAI-compatible endpoint that can be used as a drop-in
    alternative to Azure OpenAI for DAX generation and validation.
    """
    
    def __init__(self):
        """Load configuration from environment variables."""
        load_dotenv()
        
        self.api_key = os.getenv("COMPASS_API_KEY")
        self.base_url = os.getenv("COMPASS_BASE_URL", "https://api.core42.ai/v1")
        self.model = os.getenv("COMPASS_MODEL", "gpt-5.1")
        
        # Compass GPT-5.1 specs
        self.context_window = 200000
        self.max_tokens = 8192
    
    def validate(self) -> None:
        """Validate required configuration."""
        if not self.api_key:
            raise RuntimeError("Missing COMPASS_API_KEY in .env")
    
    def __repr__(self) -> str:
        return f"CompassConfig(model={self.model}, base_url={self.base_url})"


# ============================================================
# LLM Provider Factory
# ============================================================

def get_llm_provider() -> str:
    """
    Get the configured LLM provider.
    
    Returns:
        str: "azure" or "compass"
    """
    load_dotenv()
    return os.getenv("LLM_PROVIDER", "azure").lower()


def create_chat_service(service_id: str = "default"):
    """
    Factory function to create the appropriate Semantic Kernel chat service
    based on the LLM_PROVIDER environment variable.
    
    Args:
        service_id: Semantic Kernel service ID (for multiple services)
        
    Returns:
        Tuple of (chat_service, settings_class, provider_name)
        - chat_service: AzureChatCompletion or OpenAIChatCompletion instance
        - settings_class: The matching prompt execution settings class
        - provider_name: "azure" or "compass" (for logging)
    """
    provider = get_llm_provider()
    
    if provider == "compass":
        return _create_compass_service(service_id)
    else:
        return _create_azure_service(service_id)


def _create_azure_service(service_id: str):
    """Create Azure OpenAI chat service with service principal auth."""
    from semantic_kernel.connectors.ai.open_ai import AzureChatCompletion
    from semantic_kernel.connectors.ai.open_ai.prompt_execution_settings.azure_chat_prompt_execution_settings import (
        AzureChatPromptExecutionSettings,
    )
    
    config = AzureOpenAIConfig()
    auth_provider = AzureOpenAIAuthProvider(config=config)
    
    chat_service = AzureChatCompletion(
        ad_token_provider=auth_provider.token_provider,
        deployment_name=config.deployment_name,
        endpoint=config.endpoint,
        api_version=config.api_version,
        service_id=service_id,
    )
    
    return chat_service, AzureChatPromptExecutionSettings, "azure"


def _create_compass_service(service_id: str):
    """Create Core42 Compass chat service via OpenAI-compatible API.
    
    Reuses a module-level AsyncOpenAI client so that all services
    (planner, generator, validator, formatter) share one HTTP
    connection pool — avoids per-tool TCP/TLS cold-starts and
    reduces 429 retries from parallel connection setup.
    """
    from openai import AsyncOpenAI
    from semantic_kernel.connectors.ai.open_ai import OpenAIChatCompletion
    from semantic_kernel.connectors.ai.open_ai.prompt_execution_settings.open_ai_prompt_execution_settings import (
        OpenAIChatPromptExecutionSettings,
    )
    
    config = CompassConfig()
    config.validate()
    
    # Shared AsyncOpenAI client (module-level singleton)
    global _shared_compass_client
    if _shared_compass_client is None:
        import httpx
        _shared_compass_client = AsyncOpenAI(
            api_key=config.api_key,
            base_url=config.base_url,
            max_retries=2,
            timeout=httpx.Timeout(60.0, connect=10.0),
            http_client=httpx.AsyncClient(
                limits=httpx.Limits(
                    max_connections=20,
                    max_keepalive_connections=10,
                    keepalive_expiry=120,
                ),
                timeout=httpx.Timeout(60.0, connect=10.0),
            ),
        )
        print(f"[PERF] Shared Compass HTTP client created (pool=20, keepalive=10)")
    
    chat_service = OpenAIChatCompletion(
        ai_model_id=config.model,
        async_client=_shared_compass_client,
        service_id=service_id,
    )
    
    return chat_service, OpenAIChatPromptExecutionSettings, "compass"


# Module-level singleton for the shared AsyncOpenAI client
_shared_compass_client = None
