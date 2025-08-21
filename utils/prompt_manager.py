import logging
from datetime import datetime
from typing import Dict, List, Optional
from uuid import UUID

from sqlmodel import Session, select
from sqlalchemy import update as sa_update

from models import SystemPrompt

logger = logging.getLogger(__name__)


class PromptManager:
    """Manages CRUD operations for system prompts backed by the database (per-user)."""

    def __init__(self):
        pass

    # Utilities
    def _seed_default_if_missing(self, session: Session, user_id: UUID) -> None:
        existing = session.exec(
            select(SystemPrompt).where(SystemPrompt.user_id == user_id)
        ).first()
        if existing is None:
            now = datetime.utcnow()
            default = SystemPrompt(
                user_id=user_id,
                name="Default Assistant",
                description="A helpful AI assistant",
                content=(
                    "You are a helpful AI assistant. Please assist the user with their "
                    "questions and tasks to the best of your ability."
                ),
                created_at=now,
                updated_at=now,
                is_active=True,
            )
            session.add(default)
            session.commit()

    def get_active_prompt(self, session: Session, user_id: UUID) -> str:
        """Get the content of the active prompt for the user. Seed default if none."""
        self._seed_default_if_missing(session, user_id)
        active = session.exec(
            select(SystemPrompt).where(
                SystemPrompt.user_id == user_id, SystemPrompt.is_active == True
            )
        ).first()
        if active is not None:
            return active.content

        # If none marked active, pick most recently updated
        fallback = session.exec(
            select(SystemPrompt)
            .where(SystemPrompt.user_id == user_id)
            .order_by(SystemPrompt.updated_at.desc())
        ).first()
        return fallback.content if fallback else ""

    def list_prompts(self, session: Session, user_id: UUID) -> List[Dict]:
        self._seed_default_if_missing(session, user_id)
        rows = session.exec(
            select(SystemPrompt)
            .where(SystemPrompt.user_id == user_id)
            .order_by(SystemPrompt.updated_at.desc())
        ).all()
        prompt_list: List[Dict] = []
        for row in rows:
            prompt_list.append(
                {
                    "id": str(row.id),
                    "name": row.name,
                    "description": row.description or "",
                    "content": row.content,
                    "created_at": row.created_at.isoformat(),
                    "updated_at": row.updated_at.isoformat(),
                    "is_active": bool(row.is_active),
                }
            )
        return prompt_list

    def get_prompt(self, session: Session, user_id: UUID, prompt_id: str) -> Optional[Dict]:
        try:
            row = session.get(SystemPrompt, UUID(prompt_id))
        except Exception:
            return None
        if row is None or row.user_id != user_id:
            return None
        return {
            "id": str(row.id),
            "name": row.name,
            "description": row.description or "",
            "content": row.content,
            "created_at": row.created_at.isoformat(),
            "updated_at": row.updated_at.isoformat(),
            "is_active": bool(row.is_active),
        }

    def create_prompt(self, session: Session, user_id: UUID, name: str, description: str, content: str) -> Dict:
        now = datetime.utcnow()
        row = SystemPrompt(
            user_id=user_id,
            name=name,
            description=description or "",
            content=content,
            created_at=now,
            updated_at=now,
            is_active=False,
        )
        session.add(row)
        session.commit()
        session.refresh(row)
        logger.info(f"Created new prompt {row.id} for user {user_id}")
        return {
            "id": str(row.id),
            "name": row.name,
            "description": row.description or "",
            "content": row.content,
            "created_at": row.created_at.isoformat(),
            "updated_at": row.updated_at.isoformat(),
            "is_active": bool(row.is_active),
        }

    def update_prompt(
        self,
        session: Session,
        user_id: UUID,
        prompt_id: str,
        name: Optional[str] = None,
        description: Optional[str] = None,
        content: Optional[str] = None,
    ) -> Optional[Dict]:
        try:
            row = session.get(SystemPrompt, UUID(prompt_id))
        except Exception:
            return None
        if row is None or row.user_id != user_id:
            return None

        if name is not None:
            row.name = name
        if description is not None:
            row.description = description
        if content is not None:
            row.content = content
        row.updated_at = datetime.utcnow()
        session.add(row)
        session.commit()
        session.refresh(row)
        logger.info(f"Updated prompt {row.id} for user {user_id}")
        return {
            "id": str(row.id),
            "name": row.name,
            "description": row.description or "",
            "content": row.content,
            "created_at": row.created_at.isoformat(),
            "updated_at": row.updated_at.isoformat(),
            "is_active": bool(row.is_active),
        }

    def delete_prompt(self, session: Session, user_id: UUID, prompt_id: str) -> bool:
        try:
            row = session.get(SystemPrompt, UUID(prompt_id))
        except Exception:
            return False
        if row is None or row.user_id != user_id:
            return False

        was_active = bool(row.is_active)
        session.delete(row)
        session.commit()

        if was_active:
            # Set another prompt as active if any exist
            replacement = session.exec(
                select(SystemPrompt)
                .where(SystemPrompt.user_id == user_id)
                .order_by(SystemPrompt.updated_at.desc())
            ).first()
            if replacement is not None:
                replacement.is_active = True
                replacement.updated_at = datetime.utcnow()
                session.add(replacement)
                session.commit()
        logger.info(f"Deleted prompt {prompt_id} for user {user_id}")
        return True

    def set_active_prompt(self, session: Session, user_id: UUID, prompt_id: str) -> bool:
        try:
            target = session.get(SystemPrompt, UUID(prompt_id))
        except Exception:
            return False
        if target is None or target.user_id != user_id:
            return False

        # Deactivate all user's prompts, then activate target
        session.exec(
            sa_update(SystemPrompt)
            .where(SystemPrompt.user_id == user_id, SystemPrompt.is_active == True)
            .values(is_active=False)
        )
        target.is_active = True
        target.updated_at = datetime.utcnow()
        session.add(target)
        session.commit()
        logger.info(f"Set active prompt {prompt_id} for user {user_id}")
        return True


# Global instance (will be initialized in app.py)
prompt_manager: Optional[PromptManager] = None


def init_prompt_manager():
    """Initialize the global prompt manager instance (DB-backed)."""
    global prompt_manager
    prompt_manager = PromptManager()


def get_prompt_manager() -> PromptManager:
    """Get the global prompt manager instance"""
    if prompt_manager is None:
        raise RuntimeError("PromptManager not initialized. Call init_prompt_manager() first.")
    return prompt_manager 