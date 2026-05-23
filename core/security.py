import hashlib
import secrets
from datetime import timedelta
from typing import Any

from passlib.context import CryptContext

pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")


def create_access_token(subject: str | Any, expires_delta: timedelta) -> str:
    """Create access token for authentication"""
    # For simplicity, we'll use a basic token system
    # In production, consider using JWT
    return secrets.token_urlsafe(32)


def verify_password(plain_password: str, hashed_password: str) -> bool:
    """Verify a password against its hash"""
    return pwd_context.verify(plain_password, hashed_password)


def get_password_hash(password: str) -> str:
    """Generate password hash"""
    return pwd_context.hash(password)


def generate_password_reset_token() -> str:
    """Generate password reset token"""
    return secrets.token_urlsafe(32)


def hash_password_reset_token(token: str) -> str:
    """Hash password reset tokens before storing them."""
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def verify_password_reset_token(token: str, token_hash: str) -> bool:
    """Verify a password reset token against its stored hash."""
    return secrets.compare_digest(hash_password_reset_token(token), token_hash)