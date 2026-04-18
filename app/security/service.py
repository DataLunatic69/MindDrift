"""
Authentication service for user registration, login, token management, etc.
"""
from datetime import datetime, timedelta, timezone
from typing import Optional, Dict, Any
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.database.models import (
    AuthUser,
    RefreshToken,
    EmailVerificationToken,
    PasswordResetToken,
    AgencyUser,
    Agency,
    AgencyOnboarding,
    Role,
    UserRoleAssignment,
)
from app.security.password import hash_password, verify_password, check_password_strength
from app.security.jwt import create_access_token, create_refresh_token, verify_token
from app.security.token_utils import hash_token, verify_token_hash, create_token_with_prefix, extract_token_prefix
from app.security.cache import AuthCache
from app.security.exceptions import (
    InvalidCredentialsError,
    UserNotFoundError,
    UserAlreadyExistsError,
    UserInactiveError,
    UserNotVerifiedError,
    AccountLockedError,
    TokenExpiredError,
    InvalidTokenError,
    RefreshTokenRevokedError,
)


class AuthService:
    """Service for authentication operations."""
    
    def __init__(self, db: AsyncSession):
        """Initialize AuthService with database session and config."""
        self.db = db
        # Load settings from config (no hardcoded magic numbers)
        from app.config import settings
        self.MAX_FAILED_ATTEMPTS = settings.AUTH_MAX_FAILED_ATTEMPTS
        self.LOCKOUT_DURATION_MINUTES = settings.AUTH_LOCKOUT_DURATION_MINUTES
        self.REFRESH_TOKEN_EXPIRE_DAYS = settings.JWT_REFRESH_TOKEN_EXPIRE_DAYS
        self.EMAIL_VERIFICATION_EXPIRE_HOURS = settings.AUTH_EMAIL_VERIFICATION_EXPIRE_HOURS
        self.PASSWORD_RESET_EXPIRE_HOURS = settings.AUTH_PASSWORD_RESET_EXPIRE_HOURS
    
    async def register(
        self,
        email: str,
        password: str,
        full_name: str,
        agency_name: str,
        phone_number: str,
        referral_source: Optional[str] = None,
        marketing_opt_in: bool = False
    ) -> tuple[AuthUser, str, str, Agency, AgencyOnboarding]:
        """
        Register a new user with agency creation.
        
        Creates:
        1. auth_users record
        2. agencies record (status: 'trial')
        3. agency_users record (role: 'agency_admin')
        4. user_roles record (agency_admin role)
        5. agency_onboarding record (step: 'account_created')
        
        Args:
            email: User work email
            password: Plain text password
            full_name: User full name
            agency_name: Agency/company name
            phone_number: Phone number with country code
            referral_source: How they heard about us
            marketing_opt_in: Whether they opted in for marketing
            
        Returns:
            Tuple of (user, access_token, refresh_token, agency, onboarding)
            
        Raises:
            UserAlreadyExistsError: If user already exists
            ValueError: If password doesn't meet requirements
        """
        import re
        
        # Check if user already exists (fresh DB query, not cached)
        existing_user = await self.db.execute(
            select(AuthUser).where(AuthUser.email == email.lower())
        )
        existing = existing_user.scalar_one_or_none()
        if existing:
            # User exists in DB, ensure cache is in sync
            await AuthCache.set_user(existing)
            raise UserAlreadyExistsError(f"User with email {email} already exists")
        
        # Validate password strength
        is_valid, error_msg = check_password_strength(password)
        if not is_valid:
            raise ValueError(error_msg or "Password does not meet requirements")
        
        # Hash password
        password_hash = hash_password(password)
        
        # Generate agency slug from name
        slug = self._generate_slug(agency_name)
        
        # Check if agency slug already exists, make it unique if needed
        slug = await self._ensure_unique_slug(slug)
        
        # 1. Create agency first (so we have agency.id for auth_users.agency_id)
        agency = Agency(
            name=agency_name,
            slug=slug,
            email=email.lower(),
            phone=phone_number,
            business_type=None,  # Set during onboarding
            subscription_tier="professional",  # Default tier for trial
            subscription_status="trial"
        )
        self.db.add(agency)
        await self.db.flush()  # Get agency.id
        
        # 2. Create auth_users record (with agency_id for DBs that require it)
        user = AuthUser(
            agency_id=agency.id,
            email=email.lower(),
            password_hash=password_hash,
            is_active=True,
            is_verified=False,  # Email verification required
            failed_login_attempts=0
        )
        self.db.add(user)
        await self.db.flush()  # Get user.id without committing
        
        # 3. Create agency_users record (link user to agency as admin)
        agency_user = AgencyUser(
            agency_id=agency.id,
            auth_user_id=user.id,
            user_id=user.id,  # For backward compatibility
            role="agency_admin",
            full_name=full_name,
            email=email.lower(),
            phone=phone_number,
            is_active=True
        )
        self.db.add(agency_user)
        
        # 4. Find and assign agency_admin role
        role_result = await self.db.execute(
            select(Role).where(Role.name == "agency_admin")
        )
        agency_admin_role = role_result.scalar_one_or_none()
        
        if agency_admin_role:
            user_role = UserRoleAssignment(
                user_id=user.id,
                role_id=agency_admin_role.id,
                agency_id=agency.id,
                granted_by=user.id  # Self-granted on signup
            )
            self.db.add(user_role)
        
        # 5. Create agency_onboarding record
        onboarding = AgencyOnboarding(
            agency_id=agency.id,
            current_step="account_created",
            completed_steps=[],
            onboarding_started_at=datetime.now(timezone.utc)
        )
        self.db.add(onboarding)
        
        # Commit all records
        await self.db.commit()
        await self.db.refresh(user)
        await self.db.refresh(agency)
        await self.db.refresh(onboarding)
        
        # Cache user
        await AuthCache.set_user(user)
        
        # Generate tokens
        access_token = create_access_token(
            user_id=str(user.id),
            email=user.email,
            additional_claims={"agency_id": str(agency.id)}  # Include agency_id in token
        )
        
        plain_refresh_token, token_prefix = create_refresh_token()
        refresh_token_hash = hash_token(plain_refresh_token)  # Fast SHA-256 hashing
        
        # Store refresh token
        refresh_token = RefreshToken(
            user_id=user.id,
            token_hash=refresh_token_hash,
            token_prefix=token_prefix,  # For O(1) lookup
            expires_at=datetime.now(timezone.utc) + timedelta(days=self.REFRESH_TOKEN_EXPIRE_DAYS),
            device_info=None
        )
        self.db.add(refresh_token)
        await self.db.commit()
        
        return user, access_token, plain_refresh_token, agency, onboarding
    
    def _generate_slug(self, name: str) -> str:
        """Generate URL-friendly slug from agency name."""
        import re
        # Convert to lowercase, replace spaces and special chars with hyphens
        slug = name.lower().strip()
        slug = re.sub(r'[^\w\s-]', '', slug)  # Remove special chars except hyphen
        slug = re.sub(r'[\s_]+', '-', slug)  # Replace spaces/underscores with hyphen
        slug = re.sub(r'-+', '-', slug)  # Remove consecutive hyphens
        slug = slug.strip('-')  # Remove leading/trailing hyphens
        return slug
    
    async def _ensure_unique_slug(self, slug: str) -> str:
        """Ensure slug is unique, append number if needed."""
        base_slug = slug
        counter = 1
        
        while True:
            result = await self.db.execute(
                select(Agency).where(Agency.slug == slug)
            )
            if not result.scalar_one_or_none():
                return slug
            slug = f"{base_slug}-{counter}"
            counter += 1
    
    async def login(
        self,
        email: str,
        password: str,
        device_info: Optional[Dict[str, Any]] = None
    ) -> tuple[AuthUser, str, str]:
        """
        Authenticate user and generate tokens.
        
        Args:
            email: User email
            password: Plain text password
            device_info: Optional device information (user agent, IP, etc.)
            
        Returns:
            Tuple of (user, access_token, refresh_token)
            
        Raises:
            UserNotFoundError: If user doesn't exist
            InvalidCredentialsError: If password is incorrect
            UserInactiveError: If user account is inactive
            AccountLockedError: If account is locked
        """
        # Find user
        result = await self.db.execute(
            select(AuthUser).where(AuthUser.email == email.lower())
        )
        user = result.scalar_one_or_none()
        
        if not user:
            raise UserNotFoundError(f"User with email {email} not found")
        
        # Check if account is locked
        if user.locked_until and user.locked_until > datetime.now(timezone.utc):
            raise AccountLockedError(
                f"Account is locked until {user.locked_until}",
                locked_until=user.locked_until.isoformat()
            )
        
        # Check if account is active
        if not user.is_active:
            raise UserInactiveError("User account is inactive")
        
        # Verify password
        if not verify_password(password, user.password_hash):
            # Increment failed attempts
            user.failed_login_attempts += 1
            
            # Lock account if max attempts reached
            if user.failed_login_attempts >= self.MAX_FAILED_ATTEMPTS:
                user.locked_until = datetime.now(timezone.utc) + timedelta(
                    minutes=self.LOCKOUT_DURATION_MINUTES
                )
            
            await self.db.commit()
            raise InvalidCredentialsError("Invalid email or password")
        
        # Reset failed attempts on successful login
        user.failed_login_attempts = 0
        user.locked_until = None
        user.last_login_at = datetime.now(timezone.utc)
        await self.db.commit()
        
        # Invalidate and refresh cache
        await AuthCache.invalidate_user(user.id)
        await AuthCache.set_user(user)
        
        # Generate tokens
        access_token = create_access_token(
            user_id=str(user.id),
            email=user.email
        )
        
        plain_refresh_token, token_prefix = create_refresh_token()
        refresh_token_hash = hash_token(plain_refresh_token)  # Fast SHA-256 hashing
        
        # Store refresh token
        refresh_token = RefreshToken(
            user_id=user.id,
            token_hash=refresh_token_hash,
            token_prefix=token_prefix,  # For O(1) lookup
            expires_at=datetime.now(timezone.utc) + timedelta(days=self.REFRESH_TOKEN_EXPIRE_DAYS),
            device_info=device_info
        )
        self.db.add(refresh_token)
        await self.db.commit()
        
        return user, access_token, plain_refresh_token
    
    async def oauth_login(
        self,
        provider: str,
        user_info: Dict[str, Any],
        device_info: Optional[Dict[str, Any]] = None
    ) -> tuple[AuthUser, str, str]:
        """
        Authenticate user via OAuth provider and generate tokens.
        If the user doesn't exist, they are registered automatically.
        
        Args:
            provider: OAuth provider name (e.g., 'google')
            user_info: Dictionary containing user profile from provider
            device_info: Optional device information
            
        Returns:
            Tuple of (user, access_token, refresh_token)
        """
        from app.database.models import OAuthAccount
        
        provider_account_id = user_info.get("provider_account_id")
        email = user_info.get("email", "").lower()
        
        if not provider_account_id or not email:
            raise InvalidCredentialsError("OAuth profile missing required fields (id or email)")
            
        # 1. Check if OAuth account is already linked
        result = await self.db.execute(
            select(OAuthAccount).where(
                OAuthAccount.provider == provider,
                OAuthAccount.provider_account_id == provider_account_id
            )
        )
        oauth_account = result.scalar_one_or_none()
        
        if oauth_account:
            # User already logged in before via this provider
            user_result = await self.db.execute(
                select(AuthUser).where(AuthUser.id == oauth_account.user_id)
            )
            user = user_result.scalar_one()
            
            if not user.is_active:
                raise UserInactiveError("User account is inactive")
                
            if user.locked_until and user.locked_until > datetime.now(timezone.utc):
                raise AccountLockedError(
                    f"Account is locked until {user.locked_until}",
                    locked_until=user.locked_until.isoformat()
                )
                
            # Update OAuth tokens if provided
            access_token_hash = hash_token(user_info["access_token"]) if user_info.get("access_token") else None
            if access_token_hash:
                oauth_account.access_token_hash = access_token_hash
                
            user.last_login_at = datetime.now(timezone.utc)
            user.failed_login_attempts = 0
            
        else:
            # 2. Check if user exists by email but not linked to this provider
            user_result = await self.db.execute(
                select(AuthUser).where(AuthUser.email == email)
            )
            user = user_result.scalar_one_or_none()
            
            if user:
                # Link new provider to existing user
                oauth_account = OAuthAccount(
                    user_id=user.id,
                    provider=provider,
                    provider_account_id=provider_account_id,
                    access_token_hash=hash_token(user_info["access_token"]) if user_info.get("access_token") else None
                )
                self.db.add(oauth_account)
                
                user.last_login_at = datetime.now(timezone.utc)
                
                # Auto-verify email if provider considers it verified
                if user_info.get("email_verified") and not user.is_verified:
                    user.is_verified = True
                    user.email_verified_at = datetime.now(timezone.utc)
            else:
                # 3. Create a totally new user (without agency - agencies can be created post-signup)
                user = AuthUser(
                    email=email,
                    password_hash=None, # No password for OAuth users
                    is_active=True,
                    is_verified=user_info.get("email_verified", False),
                    email_verified_at=datetime.now(timezone.utc) if user_info.get("email_verified") else None,
                    failed_login_attempts=0,
                    last_login_at=datetime.now(timezone.utc)
                )
                self.db.add(user)
                await self.db.flush() # Get user.id
                
                oauth_account = OAuthAccount(
                    user_id=user.id,
                    provider=provider,
                    provider_account_id=provider_account_id,
                    access_token_hash=hash_token(user_info["access_token"]) if user_info.get("access_token") else None
                )
                self.db.add(oauth_account)
                
        await self.db.commit()
        
        # Invalidate and refresh cache
        await AuthCache.invalidate_user(user.id)
        await AuthCache.set_user(user)
        
        # Generate our platform tokens
        access_token = create_access_token(
            user_id=str(user.id),
            email=user.email,
            additional_claims={"agency_id": str(user.agency_id)} if user.agency_id else {}
        )
        
        plain_refresh_token, token_prefix = create_refresh_token()
        refresh_token_hash = hash_token(plain_refresh_token)
        
        # Store refresh token
        refresh_token = RefreshToken(
            user_id=user.id,
            token_hash=refresh_token_hash,
            token_prefix=token_prefix,
            expires_at=datetime.now(timezone.utc) + timedelta(days=self.REFRESH_TOKEN_EXPIRE_DAYS),
            device_info=device_info
        )
        self.db.add(refresh_token)
        await self.db.commit()
        
        return user, access_token, plain_refresh_token
    
    async def refresh_access_token(self, refresh_token: str) -> str:
        """
        Generate a new access token using a refresh token (optimized with prefix lookup).
        
        Args:
            refresh_token: Plain text refresh token
            
        Returns:
            New access token
            
        Raises:
            InvalidTokenError: If refresh token is invalid
            RefreshTokenRevokedError: If refresh token has been revoked
            TokenExpiredError: If refresh token has expired
        """
        # OPTIMIZED: Extract prefix for O(1) lookup
        token_prefix = extract_token_prefix(refresh_token)
        
        # Query tokens with matching prefix (or NULL prefix for old tokens)
        # This allows graceful migration - old tokens still work but slower
        result = await self.db.execute(
            select(RefreshToken).where(
                (RefreshToken.token_prefix == token_prefix) | (RefreshToken.token_prefix.is_(None)),
                RefreshToken.is_revoked == False,
                RefreshToken.expires_at > datetime.now(timezone.utc)
            )
        )
        candidate_tokens = result.scalars().all()
        
        # Verify hash on small set (usually just 1 token)
        matching_token = None
        for token in candidate_tokens:
            if verify_token_hash(refresh_token, token.token_hash):
                matching_token = token
                break
        
        if not matching_token:
            raise InvalidTokenError("Invalid refresh token")
        
        if matching_token.is_revoked or matching_token.revoked_at:
            raise RefreshTokenRevokedError("Refresh token has been revoked")
        
        if matching_token.expires_at < datetime.now(timezone.utc):
            raise TokenExpiredError("Refresh token has expired")
        
        # Get user
        user_result = await self.db.execute(
            select(AuthUser).where(AuthUser.id == matching_token.user_id)
        )
        user = user_result.scalar_one()
        
        if not user.is_active:
            raise UserInactiveError("User account is inactive")
        
        # Generate new access token
        access_token = create_access_token(
            user_id=str(user.id),
            email=user.email
        )
        
        return access_token
    
    async def logout(self, refresh_token: str) -> None:
        """
        Revoke a refresh token (logout).
        
        Args:
            refresh_token: Plain text refresh token to revoke
        """
        # Find and revoke refresh token
        result = await self.db.execute(
            select(RefreshToken).where(
                RefreshToken.is_revoked == False,
                RefreshToken.expires_at > datetime.now(timezone.utc)
            )
        )
        all_tokens = result.scalars().all()
        
        for token in all_tokens:
            if verify_password(refresh_token, token.token_hash):
                token.is_revoked = True
                token.revoked_at = datetime.now(timezone.utc)
                await self.db.commit()
                return
        
        # Token not found - silently succeed (idempotent)
    
    async def logout_all_devices(self, user_id: UUID) -> None:
        """
        Revoke all refresh tokens for a user (logout from all devices).
        
        Args:
            user_id: User UUID
        """
        result = await self.db.execute(
            select(RefreshToken).where(
                RefreshToken.user_id == user_id,
                RefreshToken.is_revoked == False
            )
        )
        tokens = result.scalars().all()
        
        now = datetime.now(timezone.utc)
        for token in tokens:
            token.is_revoked = True
            token.revoked_at = now
            # Invalidate refresh token cache
            await AuthCache.invalidate_refresh_token(token.token_hash)
        
        await self.db.commit()
        
        # Invalidate user cache to force refresh
        await AuthCache.invalidate_user(user_id)
    
    async def request_password_reset(self, email: str) -> str:
        """
        Request a password reset token.
        
        Args:
            email: User email
            
        Returns:
            Password reset token (plain text)
            
        Raises:
            UserNotFoundError: If user doesn't exist
        """
        # Find user
        result = await self.db.execute(
            select(AuthUser).where(AuthUser.email == email.lower())
        )
        user = result.scalar_one_or_none()
        
        if not user:
            # Don't reveal if user exists (security best practice)
            # Return a token anyway, but it won't work
            raise UserNotFoundError("If this email exists, a password reset link has been sent")
        
        # Generate reset token
        reset_token, token_prefix = create_token_with_prefix(32)
        reset_token_hash = hash_token(reset_token)  # Fast SHA-256 hashing
        
        # Store reset token
        reset_token_record = PasswordResetToken(
            user_id=user.id,
            token_hash=reset_token_hash,
            token_prefix=token_prefix,  # For O(1) lookup
            expires_at=datetime.now(timezone.utc) + timedelta(
                hours=self.PASSWORD_RESET_EXPIRE_HOURS
            )
        )
        self.db.add(reset_token_record)
        await self.db.commit()
        
        return reset_token
    
    async def reset_password(
        self,
        reset_token: str,
        new_password: str
    ) -> AuthUser:
        """
        Reset password using a reset token.
        
        Args:
            reset_token: Password reset token
            new_password: New plain text password
            
        Returns:
            Updated user
            
        Raises:
            InvalidTokenError: If reset token is invalid or expired
            ValueError: If password doesn't meet requirements
        """
        # Validate password strength
        is_valid, error_msg = check_password_strength(new_password)
        if not is_valid:
            raise ValueError(error_msg or "Password does not meet requirements")
        
        # OPTIMIZED: Find valid reset token using prefix lookup
        token_prefix = extract_token_prefix(reset_token)
        
        result = await self.db.execute(
            select(PasswordResetToken).where(
                (PasswordResetToken.token_prefix == token_prefix) | (PasswordResetToken.token_prefix.is_(None)),
                PasswordResetToken.used_at.is_(None),
                PasswordResetToken.expires_at > datetime.now(timezone.utc)
            )
        )
        candidate_tokens = result.scalars().all()
        
        matching_token = None
        for token in candidate_tokens:
            if verify_token_hash(reset_token, token.token_hash):
                matching_token = token
                break
        
        if not matching_token:
            raise InvalidTokenError("Invalid or expired password reset token")
        
        # Get user
        user_result = await self.db.execute(
            select(AuthUser).where(AuthUser.id == matching_token.user_id)
        )
        user = user_result.scalar_one()
        
        # Update password
        user.password_hash = hash_password(new_password)
        user.failed_login_attempts = 0
        user.locked_until = None
        
        # Mark token as used
        matching_token.used_at = datetime.now(timezone.utc)
        
        # Revoke all refresh tokens (force re-login)
        await self.logout_all_devices(user.id)
        
        await self.db.commit()
        await self.db.refresh(user)
        
        return user
    
    async def request_email_verification(self, user_id: UUID) -> str:
        """
        Request an email verification token.
        
        Args:
            user_id: User UUID
            
        Returns:
            Email verification token (plain text)
        """
        # Get user
        result = await self.db.execute(
            select(AuthUser).where(AuthUser.id == user_id)
        )
        user = result.scalar_one_or_none()
        
        if not user:
            raise UserNotFoundError("User not found")
        
        if user.is_verified:
            raise ValueError("Email is already verified")
        
        # Generate verification token
        verification_token, token_prefix = create_token_with_prefix(32)
        verification_token_hash = hash_token(verification_token)  # Fast SHA-256 hashing
        
        # Store verification token
        verify_token_record = EmailVerificationToken(
            user_id=user.id,
            token_hash=verification_token_hash,
            token_prefix=token_prefix,  # For O(1) lookup
            expires_at=datetime.now(timezone.utc) + timedelta(
                hours=self.EMAIL_VERIFICATION_EXPIRE_HOURS
            )
        )
        self.db.add(verify_token_record)
        await self.db.commit()
        
        return verification_token
    
    async def verify_email(self, verification_token: str) -> AuthUser:
        """
        Verify user email using verification token.
        
        Args:
            verification_token: Email verification token
            
        Returns:
            Updated user
            
        Raises:
            InvalidTokenError: If verification token is invalid or expired
        """
        # OPTIMIZED: Find valid verification token using prefix lookup
        token_prefix = extract_token_prefix(verification_token)
        
        result = await self.db.execute(
            select(EmailVerificationToken).where(
                (EmailVerificationToken.token_prefix == token_prefix) | (EmailVerificationToken.token_prefix.is_(None)),
                EmailVerificationToken.verified_at.is_(None),
                EmailVerificationToken.expires_at > datetime.now(timezone.utc)
            )
        )
        candidate_tokens = result.scalars().all()
        
        matching_token = None
        for token in candidate_tokens:
            if verify_token_hash(verification_token, token.token_hash):
                matching_token = token
                break
        
        if not matching_token:
            raise InvalidTokenError("Invalid or expired email verification token")
        
        # Get user
        user_result = await self.db.execute(
            select(AuthUser).where(AuthUser.id == matching_token.user_id)
        )
        user = user_result.scalar_one()
        
        # Mark email as verified
        user.is_verified = True
        user.email_verified_at = datetime.now(timezone.utc)
        
        # Mark token as used
        matching_token.verified_at = datetime.now(timezone.utc)
        
        await self.db.commit()
        await self.db.refresh(user)
        
        # Invalidate and refresh cache
        await AuthCache.invalidate_user(user.id)
        await AuthCache.set_user(user)
        
        return user
    
    async def get_user_by_id(self, user_id: UUID) -> Optional[AuthUser]:
        """
        Get user by ID.
        
        Args:
            user_id: User UUID
            
        Returns:
            User or None if not found
        """
        result = await self.db.execute(
            select(AuthUser).where(AuthUser.id == user_id)
        )
        return result.scalar_one_or_none()
    
    async def get_user_by_email(self, email: str) -> Optional[AuthUser]:
        """
        Get user by email.
        
        Args:
            email: User email
            
        Returns:
            User or None if not found
        """
        result = await self.db.execute(
            select(AuthUser).where(AuthUser.email == email.lower())
        )
        return result.scalar_one_or_none()

