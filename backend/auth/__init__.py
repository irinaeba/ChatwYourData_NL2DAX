# backend/auth/__init__.py
"""
Authentication module for production-ready Entra ID integration.

Security Pattern (BFF + OBO):
- Frontend requests token with audience = backend API
- TokenValidator validates that token is for our API (not Power BI)
- OBOTokenProvider exchanges user token for Power BI token
- Power BI token never exposed to frontend
"""

from .token_validator import TokenValidator, TokenValidationError, TokenClaims
from .auth_config import AuthConfig
from .obo_token_provider import (
    OBOTokenProvider,
    OBOTokenError,
    OBOTokenResult,
    get_obo_provider,
    exchange_for_powerbi_token,
)

__all__ = [
    # Token validation
    "TokenValidator",
    "TokenValidationError",
    "TokenClaims",
    # Auth config
    "AuthConfig",
    # OBO token exchange
    "OBOTokenProvider",
    "OBOTokenError",
    "OBOTokenResult",
    "get_obo_provider",
    "exchange_for_powerbi_token",
]
