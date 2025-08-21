from typing import Any, List, Optional

from sqlmodel import Session, select

from core.security import get_password_hash, verify_password
from models import User, UserCreate, UserUpdate, SystemPrompt


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