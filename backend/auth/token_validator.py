# backend/auth/token_validator.py
"""
Token Validation for Production

This module validates JWT access tokens from Entra ID.

Security Pattern (BFF + OBO):
- Frontend sends token with audience = backend API (api://<client_id>)
- This validator checks that the token is for OUR API, not Power BI
- After validation, backend uses OBO to get Power BI token

Tokens are passed by the frontend and validated on each request.
No tokens are stored on the backend.
"""

import os
import json
import base64
import time
import logging
from typing import Optional, Dict, Any
from dataclasses import dataclass
from dotenv import load_dotenv

logger = logging.getLogger(__name__)


class TokenValidationError(Exception):
    """Raised when token validation fails."""
    pass


@dataclass
class TokenClaims:
    """Validated token claims."""
    subject: str  # User ID (sub or oid)
    email: Optional[str]
    name: Optional[str]
    preferred_username: Optional[str]
    tenant_id: str
    audience: str
    expires_at: int
    issued_at: int
    
    @property
    def is_expired(self) -> bool:
        """Check if token is expired."""
        return time.time() > self.expires_at
    
    @property
    def user_id(self) -> str:
        """Get user identifier for caching."""
        return self.subject


class TokenValidator:
    """
    Validates Entra ID access tokens for the backend API.
    
    Security Features:
    - Validates audience (aud) matches backend API (api://<client_id>)
    - Validates issuer (iss) matches expected tenant
    - Validates expiration time (exp)
    - Does NOT accept Power BI tokens (wrong audience)
    - Does NOT store tokens - validates on each request
    
    For production, consider using PyJWT with azure-identity for
    cryptographic signature verification.
    """
    
    def __init__(self, tenant_id: str = None, client_id: str = None):
        """
        Initialize the token validator.
        
        Args:
            tenant_id: Azure tenant ID (loads from env if not provided)
            client_id: App registration client ID (loads from env if not provided)
        """
        load_dotenv()
        
        self.tenant_id = tenant_id or os.getenv("TENANT_ID")
        self.client_id = client_id or os.getenv("CLIENT_ID_POWERBI")
        
        if not self.tenant_id or not self.client_id:
            raise ValueError("TENANT_ID and CLIENT_ID_POWERBI must be configured")
        
        # Valid audiences - ONLY our API, NOT Power BI
        self.valid_audiences = [
            self.client_id,
            f"api://{self.client_id}",
            f"api://{self.client_id}/access_as_user",
        ]
        
        # Valid issuers
        self.valid_issuers = [
            f"https://sts.windows.net/{self.tenant_id}/",
            f"https://login.microsoftonline.com/{self.tenant_id}/v2.0",
        ]
    
    def _decode_jwt_payload(self, token: str) -> Dict[str, Any]:
        """
        Decode JWT payload without cryptographic verification.
        
        WARNING: This only decodes the payload. For production, you should
        verify the signature using the JWKS from Azure AD.
        
        Args:
            token: JWT access token
            
        Returns:
            Decoded payload as dictionary
        """
        try:
            parts = token.split(".")
            if len(parts) != 3:
                raise TokenValidationError("Invalid JWT format")
            
            # Decode payload (second part)
            payload = parts[1]
            # Add padding if needed
            payload += "=" * (-len(payload) % 4)
            decoded = base64.urlsafe_b64decode(payload.encode("utf-8"))
            return json.loads(decoded)
            
        except TokenValidationError:
            raise
        except Exception as e:
            raise TokenValidationError(f"Failed to decode token: {str(e)}")
    
    def validate_token(self, token: str) -> TokenClaims:
        """
        Validate an access token for the backend API.
        
        Validation checks:
        1. Token format is valid JWT
        2. Token is not expired
        3. Audience matches backend API (NOT Power BI)
        4. Issuer matches expected tenant
        
        Args:
            token: Bearer token from Authorization header
            
        Returns:
            TokenClaims with validated user information
            
        Raises:
            TokenValidationError: If validation fails
        """
        if not token:
            raise TokenValidationError("No token provided")
        
        # Remove "Bearer " prefix if present
        if token.startswith("Bearer "):
            token = token[7:]
        
        # Decode payload
        claims = self._decode_jwt_payload(token)
        
        # Check expiration
        exp = claims.get("exp")
        if not exp:
            raise TokenValidationError("Token has no expiration claim")
        
        if time.time() > exp:
            raise TokenValidationError("Token has expired")
        
        # Check audience - MUST be our API, NOT Power BI
        aud = claims.get("aud")
        
        if not aud:
            raise TokenValidationError("Token has no audience claim")
        
        if aud not in self.valid_audiences:
            # Reject Power BI tokens - they should NOT be sent to backend
            if "analysis.windows.net" in str(aud) or "powerbi" in str(aud).lower():
                raise TokenValidationError(
                    "Invalid token: Power BI tokens should not be sent to the API. "
                    "Request a token for the backend API scope instead."
                )
            raise TokenValidationError(f"Invalid audience: {aud}")
        
        # Check issuer
        iss = claims.get("iss")
        if iss not in self.valid_issuers:
            raise TokenValidationError(f"Invalid issuer: {iss}")
        
        # Extract user info
        return TokenClaims(
            subject=claims.get("oid") or claims.get("sub") or "unknown",
            email=claims.get("email") or claims.get("upn"),
            name=claims.get("name"),
            preferred_username=claims.get("preferred_username"),
            tenant_id=claims.get("tid", self.tenant_id),
            audience=aud,
            expires_at=exp,
            issued_at=claims.get("iat", 0),
        )
    
    def get_raw_token(self, token: str) -> str:
        """
        Get the raw token string (for OBO exchange).
        
        Validates the token first, then returns the raw token
        that can be used for On-Behalf-Of exchange.
        
        Args:
            token: Bearer token from frontend
            
        Returns:
            Raw token string (without "Bearer " prefix)
            
        Raises:
            TokenValidationError: If validation fails
        """
        # Validate first
        self.validate_token(token)
        
        # Return raw token
        if token.startswith("Bearer "):
            return token[7:]
        return token
