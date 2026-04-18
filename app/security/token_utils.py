"""
Token utilities - Fast hashing and prefix generation for tokens.

Uses SHA-256 for token hashing (fast) instead of bcrypt (slow).
Implements prefix indexing for O(1) token lookup.
"""
import hashlib
import secrets
from typing import Tuple


def hash_token(token: str) -> str:
    """
    Fast hashing for tokens using SHA-256.
    
    Unlike bcrypt (designed for passwords), SHA-256 is:
    - Fast (millions of hashes per second)
    - Deterministic (same input → same output)
    - Suitable for tokens (tokens are high-entropy, randomly generated)
    
    Args:
        token: Plain token string
        
    Returns:
        SHA-256 hex digest
    """
    return hashlib.sha256(token.encode()).hexdigest()


def verify_token_hash(plain_token: str, token_hash: str) -> bool:
    """
    Verify token against hash.
    
    Args:
        plain_token: Plain token
        token_hash: SHA-256 hash to compare against
        
    Returns:
        True if token matches hash
    """
    return hash_token(plain_token) == token_hash


def create_token_with_prefix(length: int = 32) -> Tuple[str, str]:
    """
    Create a random token with prefix for efficient lookup.
    
    Args:
        length: Token length in bytes (default 32 = 256 bits)
        
    Returns:
        Tuple of (full_token, prefix) where prefix is first 8 chars
    """
    token = secrets.token_urlsafe(length)
    prefix = token[:8]
    return token, prefix


def extract_token_prefix(token: str) -> str:
    """
    Extract prefix from token.
    
    Args:
        token: Full token string
        
    Returns:
        First 8 characters as prefix
    """
    return token[:8] if len(token) >= 8 else token

