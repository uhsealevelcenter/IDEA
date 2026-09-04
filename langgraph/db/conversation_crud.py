"""
Database CRUD operations for conversations and messages.
Clean separation between business logic and database operations.
"""
import uuid
from datetime import datetime
from typing import Optional
from sqlmodel import Session, select
from sqlalchemy.orm import selectinload

# Import from parent models
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent.parent))

from models import Conversation, Message, MessageRole, MessageType, MessageFormat


class ConversationCRUD:
    """CRUD operations for conversations."""
    
    def __init__(self, db: Session):
        self.db = db
    
    def create_conversation(
        self,
        user_id: uuid.UUID,
        title: str
    ) -> Conversation:
        """Create a new conversation."""
        conversation = Conversation(
            user_id=user_id,
            title=title
        )
        self.db.add(conversation)
        self.db.commit()
        self.db.refresh(conversation)
        return conversation
    
    def get_conversation_by_id(
        self,
        conversation_id: uuid.UUID,
        load_messages: bool = False
    ) -> Optional[Conversation]:
        """Get conversation by ID."""
        if load_messages:
            query = select(Conversation).where(
                Conversation.id == conversation_id
            ).options(selectinload(Conversation.messages))
            return self.db.exec(query).first()
        return self.db.get(Conversation, conversation_id)
    
    def update_conversation(
        self,
        conversation_id: uuid.UUID,
        title: Optional[str] = None,
        is_favorite: Optional[bool] = None
    ) -> Optional[Conversation]:
        """Update conversation fields."""
        conversation = self.db.get(Conversation, conversation_id)
        if not conversation:
            return None
        
        if title is not None:
            conversation.title = title
        if is_favorite is not None:
            conversation.is_favorite = is_favorite
        
        conversation.updated_at = datetime.utcnow()
        
        self.db.add(conversation)
        self.db.commit()
        self.db.refresh(conversation)
        return conversation
    
    def delete_conversation(self, conversation_id: uuid.UUID) -> bool:
        """Delete a conversation (cascades to messages)."""
        conversation = self.db.get(Conversation, conversation_id)
        if not conversation:
            return False
        
        self.db.delete(conversation)
        self.db.commit()
        return True
    
    def list_user_conversations(
        self,
        user_id: uuid.UUID,
        skip: int = 0,
        limit: int = 100
    ) -> list[Conversation]:
        """List conversations for a user."""
        query = (
            select(Conversation)
            .where(Conversation.user_id == user_id)
            .order_by(Conversation.updated_at.desc())
            .offset(skip)
            .limit(limit)
        )
        return list(self.db.exec(query).all())


class MessageCRUD:
    """CRUD operations for messages."""
    
    def __init__(self, db: Session):
        self.db = db
    
    def add_message(
        self,
        conversation_id: uuid.UUID,
        role: MessageRole,
        content: str,
        message_type: MessageType = MessageType.MESSAGE,
        message_format: Optional[MessageFormat] = None
    ) -> Message:
        """Add a message to a conversation."""
        message = Message(
            conversation_id=conversation_id,
            role=role,
            content=content,
            message_type=message_type,
            message_format=message_format
        )
        self.db.add(message)
        self.db.commit()
        self.db.refresh(message)
        
        # Update conversation timestamp
        conversation = self.db.get(Conversation, conversation_id)
        if conversation:
            conversation.updated_at = datetime.utcnow()
            self.db.add(conversation)
            self.db.commit()
        
        return message
    
    def add_messages_batch(
        self,
        conversation_id: uuid.UUID,
        messages: list[dict]
    ) -> list[Message]:
        """Add multiple messages in a batch."""
        db_messages = []
        for msg_data in messages:
            message = Message(
                conversation_id=conversation_id,
                role=MessageRole(msg_data['role']),
                content=msg_data.get('content', ''),
                message_type=MessageType(msg_data.get('type', 'message').upper()),
                message_format=MessageFormat(msg_data['format'].upper()) if msg_data.get('format') else None
            )
            self.db.add(message)
            db_messages.append(message)
        
        self.db.commit()
        
        # Refresh all messages
        for msg in db_messages:
            self.db.refresh(msg)
        
        # Update conversation timestamp
        conversation = self.db.get(Conversation, conversation_id)
        if conversation:
            conversation.updated_at = datetime.utcnow()
            self.db.add(conversation)
            self.db.commit()
        
        return db_messages
    
    def get_messages_by_conversation(
        self,
        conversation_id: uuid.UUID,
        skip: int = 0,
        limit: int = 1000
    ) -> list[Message]:
        """Get messages for a conversation."""
        query = (
            select(Message)
            .where(Message.conversation_id == conversation_id)
            .order_by(Message.created_at)
            .offset(skip)
            .limit(limit)
        )
        return list(self.db.exec(query).all())
