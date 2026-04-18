"""Authentication-only FastAPI dependencies.

This module intentionally excludes RBAC and permission checks.
"""

from typing import Any
from uuid import UUID

from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from jose import ExpiredSignatureError, JWTError, jwt
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import get_settings
from app.core.supabase import get_supabase
from app.core.database import get_db
from app.models.user import User

settings = get_settings()
security = HTTPBearer(auto_error=False)


def _decode_supabase_token(token: str) -> dict[str, Any]:
    """Decode and verify a Supabase access token."""
    if not settings.supabase_jwt_secret:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Missing SUPABASE_JWT_SECRET configuration",
        )

    try:
        payload = jwt.decode(
            token,
            settings.supabase_jwt_secret,
            algorithms=["HS256"],
            options={"verify_aud": False},
        )
        return payload
    except ExpiredSignatureError as exc:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Access token expired",
            headers={"WWW-Authenticate": "Bearer"},
        ) from exc
    except JWTError as exc:
        # Fallback path for tokens that cannot be validated locally (for example
        # non-HS256 projects): ask Supabase Auth to resolve the user identity.
        try:
            response = get_supabase().auth.get_user(jwt=token)
            user = response.user if response else None
            if not user or not user.id:
                raise HTTPException(
                    status_code=status.HTTP_401_UNAUTHORIZED,
                    detail="Invalid access token",
                    headers={"WWW-Authenticate": "Bearer"},
                )

            metadata = user.user_metadata if isinstance(user.user_metadata, dict) else {}
            return {
                "sub": str(user.id),
                "email": user.email,
                "name": metadata.get("full_name"),
                "user_metadata": metadata,
            }
        except HTTPException:
            raise
        except Exception as fallback_exc:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Invalid access token",
                headers={"WWW-Authenticate": "Bearer"},
            ) from fallback_exc


async def decode_token(token: str) -> dict[str, Any]:
    """Async wrapper to decode JWT payload from a bearer token string."""
    return _decode_supabase_token(token)


async def get_current_user(
    credentials: HTTPAuthorizationCredentials | None = Depends(security),
    db: AsyncSession = Depends(get_db),
) -> User:
    """Resolve the authenticated local user from a Supabase JWT."""
    if not credentials or not credentials.credentials:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Not authenticated",
            headers={"WWW-Authenticate": "Bearer"},
        )

    payload = _decode_supabase_token(credentials.credentials)
    sub = payload.get("sub")
    email = payload.get("email")

    if not sub:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Token is missing subject",
        )

    try:
        user_id = UUID(str(sub))
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Token subject is not a valid user id",
        ) from exc

    result = await db.execute(select(User).where(User.id == user_id))
    user = result.scalar_one_or_none()
    if user:
        return user

    metadata = payload.get("user_metadata")
    display_name = metadata.get("full_name") if isinstance(metadata, dict) else payload.get("name")

    user = User(
        id=user_id,
        email=str(email or f"{user_id}@supabase.local"),
        display_name=display_name,
    )
    db.add(user)
    await db.flush()
    return user


async def get_optional_current_user(
    credentials: HTTPAuthorizationCredentials | None = Depends(security),
    db: AsyncSession = Depends(get_db),
) -> User | None:
    """Return the authenticated user when a valid token is present."""
    if not credentials or not credentials.credentials:
        return None
    try:
        return await get_current_user(credentials, db)
    except HTTPException:
        return None


async def get_current_active_user(user: User = Depends(get_current_user)) -> User:
    """Compatibility alias for auth-only mode."""
    return user


async def get_current_verified_user(user: User = Depends(get_current_user)) -> User:
    """Compatibility alias for auth-only mode."""
    return user


async def get_optional_verified_user(
    user: User | None = Depends(get_optional_current_user),
) -> User | None:
    """Compatibility alias for auth-only mode."""
    return user
