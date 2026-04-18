"""
OAuth 2.0 Service for handling third-party authentication.
"""
from typing import Dict, Any, Optional
import httpx
from fastapi import HTTPException, status
import logging

from app.config import settings

logger = logging.getLogger(__name__)

class OAuthService:
    """Service for handling OAuth 2.0 flows."""
    
    # Provider configurations
    PROVIDERS = {
        "google": {
            "auth_url": "https://accounts.google.com/o/oauth2/v2/auth",
            "token_url": "https://oauth2.googleapis.com/token",
            "userinfo_url": "https://www.googleapis.com/oauth2/v3/userinfo",
            "scopes": ["openid", "email", "profile"],
        }
    }
    
    @classmethod
    def get_provider_config(cls, provider: str) -> Dict[str, Any]:
        """Get configuration for a specific provider."""
        provider = provider.lower()
        if provider not in cls.PROVIDERS:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"Unsupported OAuth provider: {provider}"
            )
        return cls.PROVIDERS[provider]

    @classmethod
    def get_authorization_url(cls, provider: str, state: str = "random_state_string") -> str:
        """
        Generate the authorization URL to redirect the user to.
        """
        config = cls.get_provider_config(provider)
        
        # Currently only supporting Google
        if provider == "google":
            if not settings.GOOGLE_CLIENT_ID:
                raise HTTPException(
                    status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                    detail="Google OAuth is not configured (missing CLIENT_ID)"
                )
                
            redirect_uri = settings.GOOGLE_REDIRECT_URI
            scope = " ".join(config["scopes"])
            
            auth_url = (
                f"{config['auth_url']}?"
                f"client_id={settings.GOOGLE_CLIENT_ID}&"
                f"redirect_uri={redirect_uri}&"
                f"response_type=code&"
                f"scope={scope}&"
                f"state={state}&"
                f"access_type=offline&"
                f"prompt=consent"
            )
            return auth_url
            
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Authorization URL generation not implemented for {provider}"
        )

    @classmethod
    async def get_tokens_and_user_info(cls, provider: str, code: str) -> Dict[str, Any]:
        """
        Exchange the authorization code for tokens and fetch user profile.
        
        Returns:
            Dict containing user profile and tokens.
        """
        config = cls.get_provider_config(provider)
        
        if provider == "google":
            if not settings.GOOGLE_CLIENT_ID or not settings.GOOGLE_CLIENT_SECRET:
                raise HTTPException(
                    status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                    detail="Google OAuth is not properly configured"
                )
            
            # 1. Exchange code for tokens
            token_data = {
                "client_id": settings.GOOGLE_CLIENT_ID,
                "client_secret": settings.GOOGLE_CLIENT_SECRET,
                "code": code,
                "grant_type": "authorization_code",
                "redirect_uri": settings.GOOGLE_REDIRECT_URI,
            }
            
            async with httpx.AsyncClient() as client:
                token_response = await client.post(
                    config["token_url"],
                    data=token_data,
                    headers={"Accept": "application/json"}
                )
                
                if token_response.status_code != 200:
                    logger.error(f"Failed to fetch {provider} token: {token_response.text}")
                    raise HTTPException(
                        status_code=status.HTTP_400_BAD_REQUEST,
                        detail="Failed to authenticate with provider"
                    )
                    
                tokens = token_response.json()
                access_token = tokens.get("access_token")
                refresh_token = tokens.get("refresh_token")
                
                # 2. Fetch user info
                userinfo_response = await client.get(
                    config["userinfo_url"],
                    headers={"Authorization": f"Bearer {access_token}"}
                )
                
                if userinfo_response.status_code != 200:
                    logger.error(f"Failed to fetch {provider} user info: {userinfo_response.text}")
                    raise HTTPException(
                        status_code=status.HTTP_400_BAD_REQUEST,
                        detail="Failed to fetch user profile from provider"
                    )
                    
                user_info = userinfo_response.json()
                
                return {
                    "provider": provider,
                    "provider_account_id": user_info.get("sub") or str(user_info.get("id")),
                    "email": user_info.get("email"),
                    "full_name": user_info.get("name"),
                    "given_name": user_info.get("given_name"),
                    "family_name": user_info.get("family_name"),
                    "picture": user_info.get("picture"),
                    "email_verified": user_info.get("email_verified", False),
                    "access_token": access_token,
                    "refresh_token": refresh_token,
                }
                
        raise NotImplementedError(f"Token exchange not implemented for {provider}")
