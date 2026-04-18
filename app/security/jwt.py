"""
JWT token creation, validation, and refresh utilities.
"""
from datetime import datetime, timedelta, timezone
from typing import Optional, Dict, Any
from uuid import uuid4
import secrets

import jwt
from jwt import PyJWTError

from app.config import settings
from app.security.cache import AuthCache


def create_access_token(
    user_id: str,
    email: str,
    additional_claims: Optional[Dict[str, Any]] = None
) -> str:
    """
    Create a JWT access token.
    
    Args:
        user_id: User UUID as string
        email: User email
        additional_claims: Additional claims to include in token
        
    Returns:
        Encoded JWT access token
    """
    now = datetime.now(timezone.utc)
    expire = now + timedelta(minutes=settings.JWT_ACCESS_TOKEN_EXPIRE_MINUTES)
    
    payload = {
        "sub": user_id,  # Subject (user ID)
        "email": email,
        "iat": now,  # Issued at
        "exp": expire,  # Expiration
        "jti": str(uuid4()),  # JWT ID for blacklisting
        "type": "access"
    }
    
    if additional_claims:
        payload.update(additional_claims)
    
    token = jwt.encode(
        payload,
        settings.JWT_SECRET_KEY,
        algorithm=settings.JWT_ALGORITHM
    )
    
    return token


def create_refresh_token() -> tuple[str, str]:
    """
    Create a refresh token with prefix for efficient lookup.
    
    Returns:
        Tuple of (plain_token, prefix) where prefix is first 8 chars
    """
    from app.security.token_utils import create_token_with_prefix
    return create_token_with_prefix(32)


async def verify_token(token: str) -> Dict[str, Any]:
    """
    Verify and decode a JWT token.
    
    Args:
        token: JWT token string
        
    Returns:
        Decoded token payload
        
    Raises:
        TokenExpiredError: If token has expired
        InvalidTokenError: If token is invalid
    """
    try:
        payload = jwt.decode(
            token,
            settings.JWT_SECRET_KEY,
            algorithms=[settings.JWT_ALGORITHM]
        )
        
        # Check token blacklist
        jti = payload.get("jti")
        if jti and await AuthCache.is_token_blacklisted(jti):
            from .exceptions import InvalidTokenError
            raise InvalidTokenError("Token has been revoked")
        
        return payload
    except jwt.ExpiredSignatureError:
        from .exceptions import TokenExpiredError
        raise TokenExpiredError("Token has expired")
    except PyJWTError as e:
        from .exceptions import InvalidTokenError
        raise InvalidTokenError(f"Invalid token: {str(e)}")


def decode_token(token: str, verify: bool = True) -> Dict[str, Any]:
    """
    Decode a JWT token (optionally without verification).
    
    Note: This is a synchronous function. For full verification with blacklist check,
    use verify_token() instead (async).
    
    Args:
        token: JWT token string
        verify: Whether to verify the token signature and expiration
        
    Returns:
        Decoded token payload
        
    Raises:
        InvalidTokenError: If token is invalid
    """
    try:
        if verify:
            # For sync decode, we can't check blacklist
            # Use verify_token() for full verification with blacklist check
            return jwt.decode(
                token,
                settings.JWT_SECRET_KEY,
                algorithms=[settings.JWT_ALGORITHM]
            )
        else:
            # Decode without verification (for debugging only)
            return jwt.decode(
                token,
                options={"verify_signature": False}
            )
    except Exception as e:
        from .exceptions import InvalidTokenError
        raise InvalidTokenError(f"Failed to decode token: {str(e)}")


def get_token_expiry(token: str) -> Optional[datetime]:
    """
    Get the expiration time of a token without full verification.
    
    Args:
        token: JWT token string
        
    Returns:
        Expiration datetime or None if not found
    """
    try:
        payload = decode_token(token, verify=False)
        exp = payload.get("exp")
        if exp:
            return datetime.fromtimestamp(exp, tz=timezone.utc)
        return None
    except Exception:
        return None


def is_token_expired(token: str) -> bool:
    """
    Check if a token is expired.
    
    Args:
        token: JWT token string
        
    Returns:
        True if expired, False otherwise
    """
    expiry = get_token_expiry(token)
    if not expiry:
        return True
    
    return expiry < datetime.now(timezone.utc)

