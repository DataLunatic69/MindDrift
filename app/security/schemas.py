"""
Pydantic schemas for authentication requests and responses.
"""
from datetime import datetime
from typing import Optional
from uuid import UUID

from pydantic import BaseModel, EmailStr, Field, ConfigDict


# ====================
# Request Schemas
# ====================

class ReferralSource(str):
    """How did you hear about us options."""
    GOOGLE_SEARCH = "google_search"
    LINKEDIN = "linkedin"
    REFERRAL = "referral"
    PROPERTY_PORTAL_AD = "property_portal_ad"
    OTHER = "other"


class RegisterRequest(BaseModel):
    """User registration request with agency creation."""
    # User fields
    email: EmailStr = Field(..., description="Work email address")
    password: str = Field(..., min_length=8, description="User password (min 8 characters)")
    full_name: str = Field(..., min_length=2, description="User full name")
    
    # Agency fields
    agency_name: str = Field(..., min_length=2, max_length=100, description="Agency/Company name")
    phone_number: str = Field(..., min_length=10, max_length=20, description="Phone number with country code")
    
    # Optional fields
    referral_source: Optional[str] = Field(
        None,
        description="How did you hear about us? (google_search, linkedin, referral, property_portal_ad, other)"
    )
    
    # Consent fields
    accept_terms: bool = Field(..., description="Must accept Terms of Service and Privacy Policy")
    marketing_opt_in: bool = Field(default=False, description="Opt-in for product updates and tips")
    
    model_config = ConfigDict(
        json_schema_extra={
            "example": {
                "email": "james@premierprops.co.uk",
                "password": "SecurePass123!",
                "full_name": "James Peterson",
                "agency_name": "Premier Properties London",
                "phone_number": "+447123456789",
                "referral_source": "google_search",
                "accept_terms": True,
                "marketing_opt_in": False
            }
        }
    )


class LoginRequest(BaseModel):
    """User login request."""
    email: EmailStr = Field(..., description="User email address")
    password: str = Field(..., description="User password")
    
    model_config = ConfigDict(
        json_schema_extra={
            "example": {
                "email": "user@example.com",
                "password": "SecurePass123!"
            }
        }
    )


class RefreshTokenRequest(BaseModel):
    """Refresh token request."""
    refresh_token: str = Field(..., description="Refresh token")
    
    model_config = ConfigDict(
        json_schema_extra={
            "example": {
                "refresh_token": "abc123def456..."
            }
        }
    )


class LogoutRequest(BaseModel):
    """Logout request."""
    refresh_token: str = Field(..., description="Refresh token to revoke")
    
    model_config = ConfigDict(
        json_schema_extra={
            "example": {
                "refresh_token": "abc123def456..."
            }
        }
    )

class OAuthLoginRequest(BaseModel):
    """OAuth login request parameter schema."""
    provider: str = Field(..., description="OAuth provider name (e.g., 'google')")

class OAuthCallbackRequest(BaseModel):
    """OAuth callback query parameter schema."""
    code: str = Field(..., description="Authorization code returned by provider")
    state: Optional[str] = Field(None, description="State parameter for CSRF protection")



class ForgotPasswordRequest(BaseModel):
    """Forgot password request."""
    email: EmailStr = Field(..., description="User email address")
    
    model_config = ConfigDict(
        json_schema_extra={
            "example": {
                "email": "user@example.com"
            }
        }
    )


class ResetPasswordRequest(BaseModel):
    """Reset password request."""
    token: str = Field(..., description="Password reset token")
    new_password: str = Field(..., min_length=8, description="New password (min 8 characters)")
    
    model_config = ConfigDict(
        json_schema_extra={
            "example": {
                "token": "reset_token_abc123",
                "new_password": "NewSecurePass123!"
            }
        }
    )


class VerifyEmailRequest(BaseModel):
    """Email verification request."""
    token: str = Field(..., description="Email verification token")
    
    model_config = ConfigDict(
        json_schema_extra={
            "example": {
                "token": "verification_token_abc123"
            }
        }
    )


class ResendVerificationRequest(BaseModel):
    """Resend email verification request."""
    email: EmailStr = Field(..., description="User email address")
    
    model_config = ConfigDict(
        json_schema_extra={
            "example": {
                "email": "user@example.com"
            }
        }
    )


# ====================
# Response Schemas
# ====================

class TokenResponse(BaseModel):
    """Token response."""
    access_token: str = Field(..., description="JWT access token")
    refresh_token: str = Field(..., description="Refresh token")
    token_type: str = Field(default="bearer", description="Token type")
    expires_in: int = Field(..., description="Access token expiration in seconds")
    
    model_config = ConfigDict(
        json_schema_extra={
            "example": {
                "access_token": "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9...",
                "refresh_token": "abc123def456...",
                "token_type": "bearer",
                "expires_in": 1800
            }
        }
    )


class UserResponse(BaseModel):
    """User response."""
    id: UUID = Field(..., description="User ID")
    email: str = Field(..., description="User email")
    full_name: Optional[str] = Field(None, description="User full name")
    is_active: bool = Field(..., description="Is user active")
    is_verified: bool = Field(..., description="Is email verified")
    email_verified_at: Optional[datetime] = Field(None, description="Email verification timestamp")
    last_login_at: Optional[datetime] = Field(None, description="Last login timestamp")
    created_at: datetime = Field(..., description="Account creation timestamp")
    
    model_config = ConfigDict(from_attributes=True)


class AgencyResponse(BaseModel):
    """Agency response for registration."""
    id: UUID = Field(..., description="Agency ID")
    name: str = Field(..., description="Agency name")
    slug: str = Field(..., description="Agency slug (URL-friendly name)")
    subscription_tier: str = Field(..., description="Subscription tier")
    subscription_status: str = Field(..., description="Subscription status")
    created_at: datetime = Field(..., description="Agency creation timestamp")
    
    model_config = ConfigDict(from_attributes=True)


class OnboardingResponse(BaseModel):
    """Onboarding status response."""
    current_step: str = Field(..., description="Current onboarding step")
    completed_steps: list[str] = Field(default_factory=list, description="List of completed steps")
    
    model_config = ConfigDict(from_attributes=True)


class AuthResponse(BaseModel):
    """Authentication response with user and tokens."""
    user: UserResponse = Field(..., description="User information")
    tokens: TokenResponse = Field(..., description="Access and refresh tokens")
    agency: Optional[AgencyResponse] = Field(None, description="Agency information (for registration)")
    onboarding: Optional[OnboardingResponse] = Field(None, description="Onboarding status (for registration)")
    
    model_config = ConfigDict(
        json_schema_extra={
            "example": {
                "user": {
                    "id": "123e4567-e89b-12d3-a456-426614174000",
                    "email": "james@premierprops.co.uk",
                    "full_name": "James Peterson",
                    "is_active": True,
                    "is_verified": False,
                    "created_at": "2025-01-01T00:00:00Z"
                },
                "tokens": {
                    "access_token": "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9...",
                    "refresh_token": "abc123def456...",
                    "token_type": "bearer",
                    "expires_in": 1800
                },
                "agency": {
                    "id": "agency-uuid-123",
                    "name": "Premier Properties London",
                    "slug": "premier-properties-london",
                    "subscription_tier": "professional",
                    "subscription_status": "trial",
                    "created_at": "2025-01-01T00:00:00Z"
                },
                "onboarding": {
                    "current_step": "account_created",
                    "completed_steps": []
                }
            }
        }
    )


class MessageResponse(BaseModel):
    """Generic message response."""
    message: str = Field(..., description="Response message")
    
    model_config = ConfigDict(
        json_schema_extra={
            "example": {
                "message": "Operation completed successfully"
            }
        }
    )


class ErrorResponse(BaseModel):
    """Error response."""
    error: str = Field(..., description="Error type")
    message: str = Field(..., description="Error message")
    detail: Optional[str] = Field(None, description="Additional error details")
    
    model_config = ConfigDict(
        json_schema_extra={
            "example": {
                "error": "InvalidCredentialsError",
                "message": "Invalid email or password",
                "detail": None
            }
        }
    )

