# backend/auth/auth_config.py
"""
Authentication Configuration for Production

This module provides the MSAL configuration for:
1. Frontend: Token for backend API (not Power BI directly)
2. Backend: OBO flow to exchange user token for Power BI token

Security Pattern (BFF + OBO):
- Frontend requests token with audience = backend API
- Backend validates token, then uses OBO to get Power BI token
- Power BI token never exposed to frontend
"""

import os
from dataclasses import dataclass
from typing import List
from dotenv import load_dotenv


@dataclass
class AuthConfig:
    """
    Configuration for frontend MSAL.js authentication.
    
    Frontend requests token for BACKEND API, not Power BI.
    Backend will use OBO to get Power BI token.
    """
    client_id: str
    tenant_id: str
    authority: str
    redirect_uri: str
    scopes: List[str]  # Scopes for backend API, NOT Power BI
    
    @classmethod
    def from_env(cls, redirect_uri: str = None) -> "AuthConfig":
        """
        Create AuthConfig from environment variables.
        
        Args:
            redirect_uri: Override redirect URI (default: from env or localhost)
        """
        load_dotenv()
        
        tenant_id = os.getenv("TENANT_ID")
        client_id = os.getenv("CLIENT_ID_POWERBI")
        
        # API scope for backend - this is the audience the frontend will request
        # Format: api://<client_id>/access_as_user or api://<client_id>/.default
        api_scope = os.getenv("API_SCOPE", f"api://{client_id}/access_as_user")
        
        if not tenant_id or not client_id:
            raise ValueError("TENANT_ID and CLIENT_ID_POWERBI must be set in environment")
        
        # Redirect URI - can be overridden
        default_redirect = os.getenv("REDIRECT_URI", "http://localhost:8000")
        redirect = redirect_uri or default_redirect
        
        return cls(
            client_id=client_id,
            tenant_id=tenant_id,
            authority=f"https://login.microsoftonline.com/{tenant_id}",
            redirect_uri=redirect,
            # IMPORTANT: Scope is for backend API, NOT Power BI
            # Power BI token will be acquired via OBO in backend
            scopes=[api_scope],
        )
    
    def to_dict(self) -> dict:
        """Convert to dictionary for JSON serialization."""
        return {
            "clientId": self.client_id,
            "tenantId": self.tenant_id,
            "authority": self.authority,
            "redirectUri": self.redirect_uri,
            "scopes": self.scopes,
        }
