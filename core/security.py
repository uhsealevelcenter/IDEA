import hashlib
import secrets
from datetime import datetime, timedelta
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


def generate_password_reset_token(email: str) -> str:
    """Generate password reset token"""
    return secrets.token_urlsafe(32)


def verify_password_reset_token(token: str) -> str | None:
    """Verify password reset token (simplified implementation)"""
    # This is a simplified implementation
    # In production, you'd want to store tokens with expiration in DB
    return None