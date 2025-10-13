from datetime import datetime
from typing import Any, List, Optional
from uuid import UUID

from sqlmodel import Session, select

from core.security import get_password_hash, verify_password
from core.crypto import encrypt_secret
from models import (
    MCPConnection,
    MCPConnectionCreate,
    MCPConnectionPublic,
    MCPConnectionSummary,
    MCPConnectionUpdate,
    User,
    UserCreate,
    UserUpdate,
    SystemPrompt,
)


def create_user(*, session: Session, user_create: UserCreate) -> User:
    db_obj = User.model_validate(
        user_create, update={"hashed_password": get_password_hash(user_create.password)}
    )
    session.add(db_obj)
    session.commit()
    session.refresh(db_obj)
    return db_obj


def update_user(*, session: Session, db_user: User, user_in: UserUpdate) -> Any:
    user_data = user_in.model_dump(exclude_unset=True)
    extra_data = {}
    if "password" in user_data:
        password = user_data["password"]
        hashed_password = get_password_hash(password)
        extra_data["hashed_password"] = hashed_password
    db_user.sqlmodel_update(user_data, update=extra_data)
    session.add(db_user)
    session.commit()
    session.refresh(db_user)
    return db_user


def get_user_by_email(*, session: Session, email: str) -> User | None:
    statement = select(User).where(User.email == email)
    session_user = session.exec(statement).first()
    return session_user


def authenticate(*, session: Session, email: str, password: str) -> User | None:
    db_user = get_user_by_email(session=session, email=email)
    if not db_user:
        return None
    if not verify_password(password, db_user.hashed_password):
        return None
    return db_user


def get_user_by_id(*, session: Session, user_id: Any) -> User | None:
    return session.get(User, user_id)


def list_users(*, session: Session) -> List[User]:
    return session.exec(select(User).order_by(User.created_at.desc())).all()


def delete_user(*, session: Session, db_user: User) -> None:
    session.delete(db_user)
    session.commit()


# SystemPrompt helpers (optional service layer)

def list_system_prompts(*, session: Session, user_id: Any) -> List[SystemPrompt]:
    return session.exec(
        select(SystemPrompt).where(SystemPrompt.user_id == user_id).order_by(SystemPrompt.updated_at.desc())
    ).all()


def get_system_prompt(*, session: Session, prompt_id: Any) -> Optional[SystemPrompt]:
    return session.get(SystemPrompt, prompt_id)


def create_system_prompt(*, session: Session, user_id: Any, name: str, description: str, content: str) -> SystemPrompt:
    from datetime import datetime

    row = SystemPrompt(
        user_id=user_id,
        name=name,
        description=description or "",
        content=content,
        created_at=datetime.utcnow(),
        updated_at=datetime.utcnow(),
        is_active=False,
    )
    session.add(row)
    session.commit()
    session.refresh(row)
    return row


def update_system_prompt(*, session: Session, prompt: SystemPrompt, **fields) -> SystemPrompt:
    from datetime import datetime

    for k, v in fields.items():
        if v is not None and hasattr(prompt, k):
            setattr(prompt, k, v)
    prompt.updated_at = datetime.utcnow()
    session.add(prompt)
    session.commit()
    session.refresh(prompt)
    return prompt


def delete_system_prompt(*, session: Session, prompt: SystemPrompt) -> None:
    session.delete(prompt)
    session.commit()


# MCP connection helpers

def _normalise_connection_payload(data: dict[str, Any]) -> None:
    if "command_args" in data:
        data["command_args"] = data["command_args"] or []
    if "headers" in data:
        data["headers"] = data["headers"] or {}
    if "config" in data:
        data["config"] = data["config"] or {}


def list_mcp_connections(*, session: Session) -> List[MCPConnection]:
    statement = select(MCPConnection).order_by(MCPConnection.name)
    return session.exec(statement).all()


def list_active_mcp_connections(*, session: Session) -> List[MCPConnection]:
    statement = select(MCPConnection).where(MCPConnection.is_active.is_(True)).order_by(MCPConnection.name)
    return session.exec(statement).all()


def get_mcp_connection(*, session: Session, connection_id: UUID) -> MCPConnection | None:
    return session.get(MCPConnection, connection_id)


def create_mcp_connection(
    *,
    session: Session,
    connection_in: MCPConnectionCreate,
    created_by: UUID | None,
) -> MCPConnection:
    data = connection_in.model_dump(exclude_none=True)
    token = data.pop("auth_token", None)
    _normalise_connection_payload(data)

    connection = MCPConnection(**data, created_by=created_by)
    if token is not None:
        connection.auth_token = "" if token == "" else encrypt_secret(token)

    session.add(connection)
    session.commit()
    session.refresh(connection)
    return connection


def update_mcp_connection(
    *,
    session: Session,
    db_connection: MCPConnection,
    connection_in: MCPConnectionUpdate,
) -> MCPConnection:
    update_data = connection_in.model_dump(exclude_unset=True)
    _normalise_connection_payload(update_data)

    if "auth_token" in update_data:
        token = update_data.pop("auth_token")
        if token is None:
            db_connection.auth_token = None
        elif token == "":
            db_connection.auth_token = ""
        else:
            db_connection.auth_token = encrypt_secret(token)

    if update_data:
        db_connection.sqlmodel_update(update_data)

    db_connection.updated_at = datetime.utcnow()
    session.add(db_connection)
    session.commit()
    session.refresh(db_connection)
    return db_connection


def delete_mcp_connection(*, session: Session, db_connection: MCPConnection) -> None:
    session.delete(db_connection)
    session.commit()


def mcp_connection_to_public(connection: MCPConnection) -> MCPConnectionPublic:
    data = connection.model_dump(exclude={"auth_token"})
    data["has_auth_token"] = bool(connection.auth_token)
    return MCPConnectionPublic.model_validate(data)


def mcp_connection_to_summary(connection: MCPConnection) -> MCPConnectionSummary:
    return MCPConnectionSummary(
        id=connection.id,
        name=connection.name,
        description=connection.description,
        transport=connection.transport,
        is_active=connection.is_active,
        last_connected_at=connection.last_connected_at,
    )
