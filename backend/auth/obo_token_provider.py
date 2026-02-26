# backend/auth/obo_token_provider.py
"""
On-Behalf-Of (OBO) Token Provider

This module exchanges user tokens (for backend API) to Power BI tokens
using the OAuth 2.0 On-Behalf-Of flow.

Security Pattern:
1. Frontend sends token with audience = backend API
2. Backend validates the token
3. Backend exchanges it for a Power BI token via OBO
4. Backend uses Power BI token to call XMLA endpoint
5. Power BI token NEVER sent to frontend

Requirements:
- App Registration must have "Power BI Service" API permission (Dataset.Read.All)
- Admin consent must be granted for the OBO permission
- CLIENT_SECRET must be set in environment (confidential client)
"""

import os
import time
import logging
from typing import Optional, Dict, Any
from dataclasses import dataclass
from dotenv import load_dotenv

try:
    import msal
except ImportError:
    msal = None
    
logger = logging.getLogger(__name__)


class OBOTokenError(Exception):
    """Raised when OBO token exchange fails."""
    pass


@dataclass
class OBOTokenResult:
    """Result of OBO token exchange."""
    access_token: str
    expires_at: int  # Unix timestamp
    token_type: str = "Bearer"
    
    @property
    def is_expired(self) -> bool:
        """Check if token is expired (with 5 min buffer)."""
        return time.time() > (self.expires_at - 300)


class OBOTokenProvider:
    """
    Exchanges user tokens for Power BI tokens using On-Behalf-Of flow.
    
    This is a confidential client - it requires CLIENT_SECRET.
    The OBO flow allows the backend to act on behalf of the user
    to access Power BI, without exposing the Power BI token to the frontend.
    """
    
    # Power BI API scope
    POWERBI_SCOPE = "https://analysis.windows.net/powerbi/api/.default"
    
    def __init__(
        self,
        tenant_id: str = None,
        client_id: str = None,
        client_secret: str = None,
    ):
        """
        Initialize the OBO token provider.
        
        Args:
            tenant_id: Azure tenant ID
            client_id: App registration client ID
            client_secret: App registration client secret (required for OBO)
        """
        if msal is None:
            raise ImportError("msal package is required. Install with: pip install msal")
        
        load_dotenv()
        
        self.tenant_id = tenant_id or os.getenv("TENANT_ID")
        self.client_id = client_id or os.getenv("CLIENT_ID_POWERBI")
        self.client_secret = client_secret or os.getenv("CLIENT_SECRET_POWERBI")
        
        if not all([self.tenant_id, self.client_id, self.client_secret]):
            raise ValueError(
                "TENANT_ID, CLIENT_ID_POWERBI, and CLIENT_SECRET_POWERBI must be configured for OBO flow"
            )
        
        self.authority = f"https://login.microsoftonline.com/{self.tenant_id}"
        
        # Create confidential client application
        self._app = msal.ConfidentialClientApplication(
            client_id=self.client_id,
            client_credential=self.client_secret,
            authority=self.authority,
        )
        
        # Token cache (per user)
        self._token_cache: Dict[str, OBOTokenResult] = {}
    
    def exchange_token_for_powerbi(
        self,
        user_assertion: str,
        user_id: str = None,
    ) -> OBOTokenResult:
        """
        Exchange a user token (for backend API) for a Power BI token.
        
        This uses the OAuth 2.0 On-Behalf-Of flow:
        - Input: Token with aud = api://<backend-client-id>
        - Output: Token with aud = https://analysis.windows.net/powerbi/api
        
        Args:
            user_assertion: The user's access token (for backend API)
            user_id: Optional user identifier for caching
            
        Returns:
            OBOTokenResult with Power BI access token
            
        Raises:
            OBOTokenError: If token exchange fails
        """
        # Check cache first
        cache_key = user_id or user_assertion[:50]
        cached = self._token_cache.get(cache_key)
        if cached and not cached.is_expired:
            logger.debug("Using cached Power BI token for user")
            return cached
        
        try:
            # Perform OBO token exchange
            result = self._app.acquire_token_on_behalf_of(
                user_assertion=user_assertion,
                scopes=[self.POWERBI_SCOPE],
            )
            
            if "error" in result:
                error_desc = result.get("error_description", result.get("error", "Unknown error"))
                logger.error(f"OBO token exchange failed: {error_desc}")
                
                # Check for specific errors
                if "AADSTS65001" in error_desc:
                    raise OBOTokenError(
                        "Admin consent required for Power BI API. "
                        "Please have an admin grant consent in Azure Portal."
                    )
                elif "AADSTS50013" in error_desc:
                    raise OBOTokenError(
                        "Invalid assertion. The user token may be expired or invalid."
                    )
                else:
                    raise OBOTokenError(f"Token exchange failed: {error_desc}")
            
            if "access_token" not in result:
                raise OBOTokenError("No access token in OBO response")
            
            # Calculate expiration
            expires_in = result.get("expires_in", 3600)
            expires_at = int(time.time()) + expires_in
            
            token_result = OBOTokenResult(
                access_token=result["access_token"],
                expires_at=expires_at,
                token_type=result.get("token_type", "Bearer"),
            )
            
            # Cache the token
            self._token_cache[cache_key] = token_result
            
            logger.info("Successfully exchanged user token for Power BI token via OBO")
            return token_result
            
        except OBOTokenError:
            raise
        except Exception as e:
            logger.error(f"OBO token exchange error: {str(e)}")
            raise OBOTokenError(f"Token exchange failed: {str(e)}")
    
    def clear_cache(self, user_id: str = None):
        """
        Clear token cache.
        
        Args:
            user_id: Clear cache for specific user, or all if None
        """
        if user_id:
            self._token_cache.pop(user_id, None)
        else:
            self._token_cache.clear()


# Global instance
_obo_provider: Optional[OBOTokenProvider] = None


def get_obo_provider() -> OBOTokenProvider:
    """Get or create the global OBO token provider."""
    global _obo_provider
    if _obo_provider is None:
        _obo_provider = OBOTokenProvider()
    return _obo_provider


def exchange_for_powerbi_token(user_token: str, user_id: str = None) -> str:
    """
    Exchange a user API token for a Power BI token.
    
    Convenience function that uses the global OBO provider.
    
    Args:
        user_token: User's token for backend API
        user_id: Optional user ID for caching
        
    Returns:
        Power BI access token
    """
    provider = get_obo_provider()
    result = provider.exchange_token_for_powerbi(user_token, user_id)
    return result.access_token
