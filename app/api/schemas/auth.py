from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, ConfigDict, EmailStr, Field


class AuthUserResponse(BaseModel):
    id: UUID
    email: str
    display_name: str | None
    avatar_url: str | None
    created_at: datetime

    model_config = ConfigDict(from_attributes=True)


class AuthSessionResponse(BaseModel):
    authenticated: bool
    user: AuthUserResponse | None = None


class LogoutResponse(BaseModel):
    message: str


class SignupRequest(BaseModel):
    email: EmailStr
    password: str = Field(min_length=8)
    display_name: str | None = None


class SignupResponse(BaseModel):
    message: str
    created: bool
