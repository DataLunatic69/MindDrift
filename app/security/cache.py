"""
Authentication-specific Redis caching utilities.
"""
import logging
from typing import Optional, List, Dict, Any, Set
from datetime import datetime
from uuid import UUID

from app.core.cache import redis_cache
from app.database.models import AuthUser, Role, Permission
import json

logger = logging.getLogger(__name__)


# Cache key patterns
class CacheKeys:
    """Cache key patterns for authentication."""
    
    USER = "auth:user:{user_id}"
    PERMISSIONS = "auth:permissions:{user_id}:{agency_id}"
    ROLES = "auth:roles:{user_id}:{agency_id}"
    TOKEN_BLACKLIST = "auth:token:blacklist:{jti}"
    REFRESH_TOKEN = "auth:refresh:{token_hash}"
    
    @staticmethod
    def user_key(user_id: UUID) -> str:
        """Get user cache key."""
        return CacheKeys.USER.format(user_id=str(user_id))
    
    @staticmethod
    def permissions_key(user_id: UUID, agency_id: UUID) -> str:
        """Get permissions cache key."""
        return CacheKeys.PERMISSIONS.format(
            user_id=str(user_id),
            agency_id=str(agency_id)
        )
    
    @staticmethod
    def roles_key(user_id: UUID, agency_id: UUID) -> str:
        """Get roles cache key."""
        return CacheKeys.ROLES.format(
            user_id=str(user_id),
            agency_id=str(agency_id)
        )
    
    @staticmethod
    def token_blacklist_key(jti: str) -> str:
        """Get token blacklist key."""
        return CacheKeys.TOKEN_BLACKLIST.format(jti=jti)
    
    @staticmethod
    def refresh_token_key(token_hash: str) -> str:
        """Get refresh token cache key."""
        return CacheKeys.REFRESH_TOKEN.format(token_hash=token_hash)


# Cache TTLs (in seconds)
class CacheTTL:
    """Cache TTL constants."""
    USER = 3600  # 1 hour
    PERMISSIONS = 3600  # 1 hour
    ROLES = 3600  # 1 hour
    REFRESH_TOKEN = 30 * 24 * 3600  # 30 days


class AuthCache:
    """Authentication cache service."""
    
    @staticmethod
    async def get_user(user_id: UUID) -> Optional[Dict[str, Any]]:
        """
        Get cached user data.
        
        Args:
            user_id: User UUID
            
        Returns:
            Cached user data or None
        """
        key = CacheKeys.user_key(user_id)
        return await redis_cache.get(key)
    
    @staticmethod
    async def set_user(user: AuthUser, ttl: int = CacheTTL.USER) -> bool:
        """
        Cache user data.
        
        Args:
            user: AuthUser instance
            ttl: Time to live in seconds
            
        Returns:
            True if successful
        """
        key = CacheKeys.user_key(user.id)
        user_data = {
            "id": str(user.id),
            "email": user.email,
            "is_active": user.is_active,
            "is_verified": user.is_verified,
            "email_verified_at": user.email_verified_at.isoformat() if user.email_verified_at else None,
            "last_login_at": user.last_login_at.isoformat() if user.last_login_at else None,
            "failed_login_attempts": user.failed_login_attempts,
            "locked_until": user.locked_until.isoformat() if user.locked_until else None,
            "created_at": user.created_at.isoformat() if user.created_at else None,
            "updated_at": user.updated_at.isoformat() if user.updated_at else None,
        }
        return await redis_cache.set(key, user_data, ttl)
    
    @staticmethod
    async def invalidate_user(user_id: UUID) -> bool:
        """
        Invalidate cached user data.
        
        Args:
            user_id: User UUID
            
        Returns:
            True if successful
        """
        key = CacheKeys.user_key(user_id)
        return await redis_cache.delete(key)
    
    @staticmethod
    async def get_permissions(
        user_id: UUID,
        agency_id: UUID
    ) -> Optional[List[Dict[str, Any]]]:
        """
        Get cached user permissions.
        
        Args:
            user_id: User UUID
            agency_id: Agency UUID
            
        Returns:
            Cached permissions list or None
        """
        key = CacheKeys.permissions_key(user_id, agency_id)
        return await redis_cache.get(key)
    
    @staticmethod
    async def set_permissions(
        user_id: UUID,
        agency_id: UUID,
        permissions: List[Permission],
        ttl: int = CacheTTL.PERMISSIONS
    ) -> bool:
        """
        Cache user permissions.
        
        Args:
            user_id: User UUID
            agency_id: Agency UUID
            permissions: List of Permission instances
            ttl: Time to live in seconds
            
        Returns:
            True if successful
        """
        key = CacheKeys.permissions_key(user_id, agency_id)
        permissions_data = [
            {
                "id": str(p.id),
                "name": p.name,
                "resource": p.resource,
                "action": p.action,
                "description": p.description,
            }
            for p in permissions
        ]
        return await redis_cache.set(key, permissions_data, ttl)
    
    @staticmethod
    async def invalidate_permissions(user_id: Optional[UUID] = None, agency_id: Optional[UUID] = None) -> int:
        """
        Invalidate cached permissions.
        
        Args:
            user_id: User UUID (optional, if None invalidates all)
            agency_id: Agency UUID (optional, if None invalidates all)
            
        Returns:
            Number of keys deleted
        """
        if user_id and agency_id:
            # Invalidate specific user/agency
            key = CacheKeys.permissions_key(user_id, agency_id)
            deleted = await redis_cache.delete(key)
            return 1 if deleted else 0
        else:
            # Invalidate all permissions
            pattern = CacheKeys.PERMISSIONS.replace("{user_id}", "*").replace("{agency_id}", "*")
            return await redis_cache.delete_pattern(pattern)
    
    @staticmethod
    async def get_roles(
        user_id: UUID,
        agency_id: UUID
    ) -> Optional[List[Dict[str, Any]]]:
        """
        Get cached user roles.
        
        Args:
            user_id: User UUID
            agency_id: Agency UUID
            
        Returns:
            Cached roles list or None
        """
        key = CacheKeys.roles_key(user_id, agency_id)
        return await redis_cache.get(key)
    
    @staticmethod
    async def set_roles(
        user_id: UUID,
        agency_id: UUID,
        roles: List[Role],
        ttl: int = CacheTTL.ROLES
    ) -> bool:
        """
        Cache user roles.
        
        Args:
            user_id: User UUID
            agency_id: Agency UUID
            roles: List of Role instances
            ttl: Time to live in seconds
            
        Returns:
            True if successful
        """
        key = CacheKeys.roles_key(user_id, agency_id)
        roles_data = [
            {
                "id": str(r.id),
                "name": r.name,
                "description": r.description,
                "is_system_role": r.is_system_role,
            }
            for r in roles
        ]
        return await redis_cache.set(key, roles_data, ttl)
    
    @staticmethod
    async def invalidate_roles(user_id: Optional[UUID] = None, agency_id: Optional[UUID] = None) -> int:
        """
        Invalidate cached roles.
        
        Args:
            user_id: User UUID (optional, if None invalidates all)
            agency_id: Agency UUID (optional, if None invalidates all)
            
        Returns:
            Number of keys deleted
        """
        if user_id and agency_id:
            # Invalidate specific user/agency
            key = CacheKeys.roles_key(user_id, agency_id)
            deleted = await redis_cache.delete(key)
            return 1 if deleted else 0
        else:
            # Invalidate all roles
            pattern = CacheKeys.ROLES.replace("{user_id}", "*").replace("{agency_id}", "*")
            return await redis_cache.delete_pattern(pattern)
    
    @staticmethod
    async def is_token_blacklisted(jti: str) -> bool:
        """
        Check if token is blacklisted.
        
        Args:
            jti: JWT ID (token identifier)
            
        Returns:
            True if token is blacklisted
        """
        key = CacheKeys.token_blacklist_key(jti)
        return await redis_cache.exists(key)
    
    @staticmethod
    async def blacklist_token(jti: str, ttl: int) -> bool:
        """
        Add token to blacklist.
        
        Args:
            jti: JWT ID (token identifier)
            ttl: Time to live in seconds (should match token expiry)
            
        Returns:
            True if successful
        """
        key = CacheKeys.token_blacklist_key(jti)
        # Store timestamp of blacklist
        value = {"blacklisted_at": datetime.utcnow().isoformat()}
        return await redis_cache.set(key, value, ttl)
    
    @staticmethod
    async def cache_refresh_token(token_hash: str, user_id: UUID, ttl: int = CacheTTL.REFRESH_TOKEN) -> bool:
        """
        Cache refresh token validation.
        
        Args:
            token_hash: Hashed refresh token
            user_id: User UUID
            ttl: Time to live in seconds
            
        Returns:
            True if successful
        """
        key = CacheKeys.refresh_token_key(token_hash)
        value = {
            "user_id": str(user_id),
            "cached_at": datetime.utcnow().isoformat()
        }
        return await redis_cache.set(key, value, ttl)
    
    @staticmethod
    async def get_refresh_token(token_hash: str) -> Optional[Dict[str, Any]]:
        """
        Get cached refresh token data.
        
        Args:
            token_hash: Hashed refresh token
            
        Returns:
            Cached refresh token data or None
        """
        key = CacheKeys.refresh_token_key(token_hash)
        return await redis_cache.get(key)
    
    @staticmethod
    async def invalidate_refresh_token(token_hash: str) -> bool:
        """
        Invalidate cached refresh token.
        
        Args:
            token_hash: Hashed refresh token
            
        Returns:
            True if successful
        """
        key = CacheKeys.refresh_token_key(token_hash)
        return await redis_cache.delete(key)



class PermissionCache:
    """Redis cache for permission system."""
    
    # TTL values (in seconds)
    PERMISSION_TTL = 3600  # 1 hour
    ROLE_TTL = 3600  # 1 hour
    CHECK_TTL = 600  # 10 minutes (for complex checks)
    AGENCY_TTL = 3600  # 1 hour
    
    # Key prefixes
    PREFIX_PERMISSIONS = "auth:permissions"
    PREFIX_ROLES = "auth:roles"
    PREFIX_CAN_ACCESS = "auth:can_access"
    PREFIX_AGENCY = "auth:agency"
    
    @classmethod
    def _make_key(cls, *parts: str) -> str:
        """Create cache key from parts."""
        return ":".join(str(p) for p in parts)
    
    # ============================================================
    # User Permissions Cache
    # ============================================================
    
    @classmethod
    async def get_user_permissions(
        cls,
        user_id: UUID,
        agency_id: UUID
    ) -> Optional[Set[str]]:
        """
        Get cached user permissions.
        
        Args:
            user_id: User ID
            agency_id: Agency ID
            
        Returns:
            Set of permission names or None if not cached
        """
        key = cls._make_key(cls.PREFIX_PERMISSIONS, user_id, agency_id)
        
        try:
            value = await redis_cache.get(key)
            if value:
                permissions_list = json.loads(value)
                return set(permissions_list)
        except Exception as e:
            logger.error(f"Error getting cached permissions: {e}")
        
        return None
    
    @classmethod
    async def set_user_permissions(
        cls,
        user_id: UUID,
        agency_id: UUID,
        permissions: Set[str]
    ) -> bool:
        """
        Cache user permissions.
        
        Args:
            user_id: User ID
            agency_id: Agency ID
            permissions: Set of permission names
            
        Returns:
            True if cached successfully
        """
        key = cls._make_key(cls.PREFIX_PERMISSIONS, user_id, agency_id)
        
        try:
            # Convert set to list for JSON serialization
            permissions_list = list(permissions)
            value = json.dumps(permissions_list)
            
            await redis_cache.set(key, value, ttl=cls.PERMISSION_TTL)
            logger.debug(f"Cached {len(permissions)} permissions for user {user_id}")
            return True
        except Exception as e:
            logger.error(f"Error caching permissions: {e}")
            return False
    
    @classmethod
    async def invalidate_user_permissions(
        cls,
        user_id: UUID,
        agency_id: Optional[UUID] = None
    ) -> None:
        """
        Invalidate cached permissions for user.
        
        Args:
            user_id: User ID
            agency_id: Agency ID (if None, invalidate all agencies)
        """
        try:
            if agency_id:
                # Invalidate specific agency
                key = cls._make_key(cls.PREFIX_PERMISSIONS, user_id, agency_id)
                await redis_cache.delete(key)
                logger.debug(f"Invalidated permissions for user {user_id} in agency {agency_id}")
            else:
                # Invalidate all agencies for user
                pattern = cls._make_key(cls.PREFIX_PERMISSIONS, user_id, "*")
                await redis_cache.delete_pattern(pattern)
                logger.debug(f"Invalidated all permissions for user {user_id}")
        except Exception as e:
            logger.error(f"Error invalidating permissions cache: {e}")
    
    # ============================================================
    # User Roles Cache
    # ============================================================
    
    @classmethod
    async def get_user_roles(
        cls,
        user_id: UUID,
        agency_id: UUID
    ) -> Optional[List[str]]:
        """
        Get cached user role names.
        
        Args:
            user_id: User ID
            agency_id: Agency ID
            
        Returns:
            List of role names or None if not cached
        """
        key = cls._make_key(cls.PREFIX_ROLES, user_id, agency_id)
        
        try:
            value = await redis_cache.get(key)
            if value:
                return json.loads(value)
        except Exception as e:
            logger.error(f"Error getting cached roles: {e}")
        
        return None
    
    @classmethod
    async def set_user_roles(
        cls,
        user_id: UUID,
        agency_id: UUID,
        roles: List[Role]
    ) -> bool:
        """
        Cache user roles.
        
        Args:
            user_id: User ID
            agency_id: Agency ID
            roles: List of Role objects
            
        Returns:
            True if cached successfully
        """
        key = cls._make_key(cls.PREFIX_ROLES, user_id, agency_id)
        
        try:
            # Store just the role names
            role_names = [role.name for role in roles]
            value = json.dumps(role_names)
            
            await redis_cache.set(key, value, ttl=cls.ROLE_TTL)
            logger.debug(f"Cached {len(role_names)} roles for user {user_id}")
            return True
        except Exception as e:
            logger.error(f"Error caching roles: {e}")
            return False
    
    @classmethod
    async def invalidate_user_roles(
        cls,
        user_id: UUID,
        agency_id: Optional[UUID] = None
    ) -> None:
        """
        Invalidate cached roles for user.
        
        Args:
            user_id: User ID
            agency_id: Agency ID (if None, invalidate all agencies)
        """
        try:
            if agency_id:
                key = cls._make_key(cls.PREFIX_ROLES, user_id, agency_id)
                await redis_cache.delete(key)
            else:
                pattern = cls._make_key(cls.PREFIX_ROLES, user_id, "*")
                await redis_cache.delete_pattern(pattern)
            logger.debug(f"Invalidated roles cache for user {user_id}")
        except Exception as e:
            logger.error(f"Error invalidating roles cache: {e}")
    
    # ============================================================
    # Complex Permission Checks Cache
    # ============================================================
    
    @classmethod
    async def get_can_access(
        cls,
        user_id: UUID,
        resource_type: str,
        resource_id: UUID
    ) -> Optional[bool]:
        """
        Get cached complex permission check result.
        
        Example: "Can user 123 edit contact 456?"
        
        Args:
            user_id: User ID
            resource_type: Resource type (e.g., "contact", "property")
            resource_id: Resource ID
            
        Returns:
            True/False if cached, None if not cached
        """
        key = cls._make_key(cls.PREFIX_CAN_ACCESS, user_id, resource_type, resource_id)
        
        try:
            value = await redis_cache.get(key)
            if value is not None:
                return value == "1"
        except Exception as e:
            logger.error(f"Error getting cached access check: {e}")
        
        return None
    
    @classmethod
    async def set_can_access(
        cls,
        user_id: UUID,
        resource_type: str,
        resource_id: UUID,
        can_access: bool
    ) -> bool:
        """
        Cache complex permission check result.
        
        Args:
            user_id: User ID
            resource_type: Resource type
            resource_id: Resource ID
            can_access: Whether user can access
            
        Returns:
            True if cached successfully
        """
        key = cls._make_key(cls.PREFIX_CAN_ACCESS, user_id, resource_type, resource_id)
        
        try:
            value = "1" if can_access else "0"
            await redis_cache.set(key, value, ttl=cls.CHECK_TTL)
            return True
        except Exception as e:
            logger.error(f"Error caching access check: {e}")
            return False
    
    @classmethod
    async def invalidate_resource_access(
        cls,
        resource_type: str,
        resource_id: UUID
    ) -> None:
        """
        Invalidate all access checks for a resource.
        
        Called when resource ownership or permissions change.
        
        Args:
            resource_type: Resource type
            resource_id: Resource ID
        """
        try:
            pattern = cls._make_key(cls.PREFIX_CAN_ACCESS, "*", resource_type, resource_id)
            await redis_cache.delete_pattern(pattern)
            logger.debug(f"Invalidated access cache for {resource_type} {resource_id}")
        except Exception as e:
            logger.error(f"Error invalidating resource access cache: {e}")
    
    # ============================================================
    # Agency Context Cache
    # ============================================================
    
    @classmethod
    async def get_agency_context(cls, agency_id: UUID) -> Optional[Dict]:
        """
        Get cached agency context.
        
        Includes: settings, subscription tier, business hours, AI config
        
        Args:
            agency_id: Agency ID
            
        Returns:
            Agency context dict or None if not cached
        """
        key = cls._make_key(cls.PREFIX_AGENCY, agency_id)
        
        try:
            value = await redis_cache.get(key)
            if value:
                return json.loads(value)
        except Exception as e:
            logger.error(f"Error getting cached agency context: {e}")
        
        return None
    
    @classmethod
    async def set_agency_context(
        cls,
        agency_id: UUID,
        context: Dict
    ) -> bool:
        """
        Cache agency context.
        
        Args:
            agency_id: Agency ID
            context: Agency context dictionary
            
        Returns:
            True if cached successfully
        """
        key = cls._make_key(cls.PREFIX_AGENCY, agency_id)
        
        try:
            value = json.dumps(context)
            await redis_cache.set(key, value, ttl=cls.AGENCY_TTL)
            return True
        except Exception as e:
            logger.error(f"Error caching agency context: {e}")
            return False
    
    @classmethod
    async def invalidate_agency_context(cls, agency_id: UUID) -> None:
        """
        Invalidate cached agency context.
        
        Args:
            agency_id: Agency ID
        """
        try:
            key = cls._make_key(cls.PREFIX_AGENCY, agency_id)
            await redis_cache.delete(key)
            logger.debug(f"Invalidated agency context for {agency_id}")
        except Exception as e:
            logger.error(f"Error invalidating agency context: {e}")
    
    # ============================================================
    # Bulk Invalidation
    # ============================================================
    
    @classmethod
    async def invalidate_all_for_user(
        cls,
        user_id: UUID,
        agency_id: Optional[UUID] = None
    ) -> None:
        """
        Invalidate all caches related to a user.
        
        Called when user roles change or user is deleted.
        
        Args:
            user_id: User ID
            agency_id: Agency ID (if None, invalidate across all agencies)
        """
        await cls.invalidate_user_permissions(user_id, agency_id)
        await cls.invalidate_user_roles(user_id, agency_id)
        
        # Invalidate all access checks for this user
        try:
            pattern = cls._make_key(cls.PREFIX_CAN_ACCESS, user_id, "*")
            await redis_cache.delete_pattern(pattern)
        except Exception as e:
            logger.error(f"Error invalidating user access checks: {e}")
    
    @classmethod
    async def invalidate_all_for_role(cls, role_id: UUID) -> None:
        """
        Invalidate all caches when role permissions change.
        
        This is expensive but necessary for correctness.
        
        Args:
            role_id: Role ID
        """
        try:
            # Invalidate all permissions (brute force)
            pattern = cls._make_key(cls.PREFIX_PERMISSIONS, "*")
            await redis_cache.delete_pattern(pattern)
            
            # Invalidate all access checks
            pattern = cls._make_key(cls.PREFIX_CAN_ACCESS, "*")
            await redis_cache.delete_pattern(pattern)
            
            logger.warning(f"Invalidated all permission caches due to role {role_id} change")
        except Exception as e:
            logger.error(f"Error invalidating role caches: {e}")
    
    @classmethod
    async def invalidate_all_for_agency(cls, agency_id: UUID) -> None:
        """
        Invalidate all caches for an agency.
        
        Called when agency is deleted or permissions restructured.
        
        Args:
            agency_id: Agency ID
        """
        try:
            # Invalidate all permissions for agency
            pattern = cls._make_key(cls.PREFIX_PERMISSIONS, "*", agency_id)
            await redis_cache.delete_pattern(pattern)
            
            # Invalidate all roles for agency
            pattern = cls._make_key(cls.PREFIX_ROLES, "*", agency_id)
            await redis_cache.delete_pattern(pattern)
            
            # Invalidate agency context
            await cls.invalidate_agency_context(agency_id)
            
            logger.info(f"Invalidated all caches for agency {agency_id}")
        except Exception as e:
            logger.error(f"Error invalidating agency caches: {e}")


# Convenience singleton instance
permission_cache = PermissionCache()



