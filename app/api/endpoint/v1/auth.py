from fastapi import APIRouter, Depends, HTTPException, status

from app.api.schemas.auth import (
    AuthSessionResponse,
    AuthUserResponse,
    LogoutResponse,
    SignupRequest,
    SignupResponse,
)
from app.core.supabase import get_supabase
from app.models.user import User
from app.security import get_current_user, get_optional_current_user

router = APIRouter(prefix="/auth", tags=["auth"])


@router.post("/signup", response_model=SignupResponse, status_code=status.HTTP_201_CREATED)
async def signup(payload: SignupRequest):
    """Create a confirmed auth user without email confirmation flow."""
    supabase = get_supabase()

    try:
        existing_users = supabase.auth.admin.list_users(page=1, per_page=200)
    except Exception as exc:
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail=f"Unable to reach auth provider: {exc}",
        ) from exc

    existing = next((u for u in existing_users if (u.email or "").lower() == payload.email.lower()), None)
    if existing:
        if not existing.email_confirmed_at:
            try:
                supabase.auth.admin.update_user_by_id(
                    existing.id,
                    {
                        "email_confirm": True,
                    },
                )
            except Exception as exc:
                raise HTTPException(
                    status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                    detail=f"Failed to confirm existing user: {exc}",
                ) from exc

        return SignupResponse(
            message="Account already exists and is ready to log in.",
            created=False,
        )

    attributes: dict[str, object] = {
        "email": payload.email,
        "password": payload.password,
        "email_confirm": True,
    }
    if payload.display_name:
        attributes["user_metadata"] = {"full_name": payload.display_name}

    try:
        supabase.auth.admin.create_user(attributes)
    except Exception as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Signup failed: {exc}",
        ) from exc

    return SignupResponse(message="Account created.", created=True)


@router.get("/me", response_model=AuthUserResponse)
async def me(user: User = Depends(get_current_user)):
    """Return the currently authenticated user."""
    return AuthUserResponse.model_validate(user)


@router.get("/session", response_model=AuthSessionResponse)
async def session(user: User | None = Depends(get_optional_current_user)):
    """Return authentication state for the current bearer token."""
    if not user:
        return AuthSessionResponse(authenticated=False)
    return AuthSessionResponse(authenticated=True, user=AuthUserResponse.model_validate(user))


@router.post("/logout", response_model=LogoutResponse)
async def logout(_: User = Depends(get_current_user)):
    """Client-side tokens are managed by Supabase; this endpoint acknowledges sign-out."""
    return LogoutResponse(message="Signed out")
