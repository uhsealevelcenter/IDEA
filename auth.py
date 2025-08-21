import os
import secrets
from datetime import datetime, timedelta
from typing import Dict, Any
from fastapi import HTTPException, Header, Depends
from sqlmodel import Session

from core.db import engine
from core.security import create_access_token
import crud
from models import User

# Session timeout configuration
SESSION_TIMEOUT = 24 * 60 * 60  # 24 hours in seconds

# Authentication session storage (in production, use Redis or database)
auth_sessions: Dict[str, Dict[str, Any]] = {}


def get_db():
    """Database dependency"""
    with Session(engine) as session:
        yield session


def generate_auth_token() -> str:
    """Generate a secure random token for authentication"""
    return create_access_token(subject=secrets.token_urlsafe(16), expires_delta=timedelta(seconds=SESSION_TIMEOUT))


def verify_password(email: str, password: str, session: Session) -> User | None:
    """Verify user credentials and return user if valid"""
    return crud.authenticate(session=session, email=email, password=password)


def is_authenticated(token: str) -> bool:
    """Check if authentication token is valid and not expired"""
    if token not in auth_sessions:
        return False
    session_data = auth_sessions[token]
    # Check if token has expired
    if datetime.now() > session_data["expires"]:
        del auth_sessions[token]
        return False
    return True


def get_current_user(token: str) -> User | None:
    """Get current user from token"""
    if token not in auth_sessions:
        return None
    session_data = auth_sessions[token]
    # Check if token has expired
    if datetime.now() > session_data["expires"]:
        del auth_sessions[token]
        return None
    with Session(engine) as db_session:
        return crud.get_user_by_id(session=db_session, user_id=session_data["user_id"])


def get_auth_token(authorization: str = Header(None)) -> str:
    """Dependency to extract and validate auth token from headers"""
    if not authorization or not authorization.startswith("Bearer "):
        raise HTTPException(status_code=401, detail="Authentication required")
    token = authorization.replace("Bearer ", "")
    if not is_authenticated(token):
        raise HTTPException(status_code=401, detail="Invalid or expired token")
    return token


def add_auth_session(token: str, user_id: Any, expiry_time: datetime):
    """Add a new authentication session"""
    auth_sessions[token] = {"user_id": user_id, "expires": expiry_time}


def remove_auth_session(token: str):
    """Remove an authentication session"""
    if token in auth_sessions:
        del auth_sessions[token] 