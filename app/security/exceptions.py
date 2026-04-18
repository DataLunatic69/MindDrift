"""
Custom exceptions for authentication module.
"""
from typing import Optional


class AuthenticationError(Exception):
    """Base exception for authentication errors."""
    pass


class InvalidCredentialsError(AuthenticationError):
    """Raised when credentials are invalid."""
    pass


class UserNotFoundError(AuthenticationError):
    """Raised when user is not found."""
    pass


class UserAlreadyExistsError(AuthenticationError):
    """Raised when trying to create a user that already exists."""
    pass


class UserInactiveError(AuthenticationError):
    """Raised when user account is inactive."""
    pass


class UserNotVerifiedError(AuthenticationError):
    """Raised when user email is not verified."""
    pass


class AccountLockedError(AuthenticationError):
    """Raised when user account is locked."""
    def __init__(self, message: str = "Account is locked", locked_until: Optional[str] = None):
        super().__init__(message)
        self.locked_until = locked_until


class TokenExpiredError(AuthenticationError):
    """Raised when token has expired."""
    pass


class InvalidTokenError(AuthenticationError):
    """Raised when token is invalid."""
    pass


class RefreshTokenRevokedError(AuthenticationError):
    """Raised when refresh token has been revoked."""
    pass


class InvalidInputError(AuthenticationError):
    """Raised when input validation fails."""
    pass


class ResourceNotFoundError(AuthenticationError):
    """Raised when requested resource is not found."""
    pass

