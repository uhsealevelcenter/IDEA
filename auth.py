import os
import hashlib
import secrets
from datetime import datetime, timedelta
from typing import Dict
from fastapi import HTTPException, Header

# Authentication configuration
AUTH_USERNAME = os.getenv("AUTH_USERNAME", "admin")  # Default username
AUTH_PASSWORD = os.getenv("AUTH_PASSWORD", "password123")  # Default password
AUTH_PASSWORD_HASH = hashlib.sha256(AUTH_PASSWORD.encode()).hexdigest()
SESSION_TIMEOUT = 24 * 60 * 60  # 24 hours in seconds

# Authentication session storage (in production, use Redis or database)
auth_sessions: Dict[str, datetime] = {}

def generate_auth_token() -> str:
    """Generate a secure random token for authentication"""
    return secrets.token_urlsafe(32)

def verify_password(username: str, password: str) -> bool:
    """Verify username and password"""
    password_hash = hashlib.sha256(password.encode()).hexdigest()
    return username == AUTH_USERNAME and password_hash == AUTH_PASSWORD_HASH

def is_authenticated(token: str) -> bool:
    """Check if authentication token is valid and not expired"""
    if token not in auth_sessions:
        return False
    
    # Check if token has expired
    if datetime.now() > auth_sessions[token]:
        del auth_sessions[token]
        return False
    
    return True

def get_auth_token(authorization: str = Header(None)) -> str:
    """Dependency to extract and validate auth token from headers"""
    if not authorization or not authorization.startswith("Bearer "):
        raise HTTPException(status_code=401, detail="Authentication required")
    
    token = authorization.replace("Bearer ", "")
    if not is_authenticated(token):
        raise HTTPException(status_code=401, detail="Invalid or expired token")
    
    return token

def add_auth_session(token: str, expiry_time: datetime):
    """Add a new authentication session"""
    auth_sessions[token] = expiry_time

def remove_auth_session(token: str):
    """Remove an authentication session"""
    if token in auth_sessions:
        del auth_sessions[token] 