from typing import Any
from uuid import UUID
import secrets

from fastapi import APIRouter, Depends, HTTPException
from sqlmodel import Session, select

from auth import get_db
from models import (
    Conversation,
    ConversationCreate,
    ConversationPublic,
    ConversationsPublic,
    ConversationUpdate,
    ConversationWithMessages,
    ConversationShared,
    ConversationShareCreate,
    ConversationShareResponse,
    Message,
    MessageCreate,
    MessagePublic,
    MessagesPublic,
    MessageRole,
    GenericMessage,
)

router = APIRouter()


@router.get("/", response_model=ConversationsPublic)
def read_conversations(
    session_id: str,
    session: Session = Depends(get_db),
    skip: int = 0,
    limit: int = 100,
) -> Any:
    """
    Retrieve conversations for the current session.
    """
    if not session_id:
        raise HTTPException(status_code=400, detail="session_id is required")
        
    # For session-based access (no user authentication required)
    count_statement = select(Conversation).where(Conversation.session_id == session_id)
    statement = (
        select(Conversation)
        .where(Conversation.session_id == session_id)
        .offset(skip)
        .limit(limit)
        .order_by(Conversation.updated_at.desc())
    )

    count = len(session.exec(count_statement).all())
    conversations = session.exec(statement).all()

    return ConversationsPublic(data=conversations, count=count)


@router.get("/favorites", response_model=ConversationsPublic)
def read_favorite_conversations(
    session_id: str,
    session: Session = Depends(get_db),
    skip: int = 0,
    limit: int = 100,
) -> Any:
    """
    Retrieve favorite conversations for the current session.
    """
    if not session_id:
        raise HTTPException(status_code=400, detail="session_id is required")

    count_statement = select(Conversation).where(
        Conversation.session_id == session_id,
        Conversation.is_favorite == True
    )
    count = len(session.exec(count_statement).all())

    statement = (
        select(Conversation)
        .where(
            Conversation.session_id == session_id,
            Conversation.is_favorite == True
        )
        .offset(skip)
        .limit(limit)
        .order_by(Conversation.updated_at.desc())
    )
    conversations = session.exec(statement).all()

    return ConversationsPublic(data=conversations, count=count)


@router.get("/{conversation_id}", response_model=ConversationWithMessages)
def read_conversation(
    conversation_id: UUID,
    session: Session = Depends(get_db),
    session_id: str = None,
) -> Any:
    """
    Get conversation by ID with messages.
    """
    conversation = session.get(Conversation, conversation_id)
    if not conversation:
        raise HTTPException(status_code=404, detail="Conversation not found")
    
    if session_id and conversation.session_id != session_id:
        raise HTTPException(status_code=403, detail="Not enough permissions")
    
    # Get messages for this conversation
    messages_statement = (
        select(Message)
        .where(Message.conversation_id == conversation_id)
        .order_by(Message.created_at)
    )
    messages = session.exec(messages_statement).all()
    
    return ConversationWithMessages(
        id=conversation.id,
        title=conversation.title,
        session_id=conversation.session_id,
        is_shared=conversation.is_shared,
        is_favorite=conversation.is_favorite,
        created_at=conversation.created_at,
        updated_at=conversation.updated_at,
        messages=messages
    )


@router.post("/", response_model=ConversationPublic)
def create_conversation(
    *, 
    session: Session = Depends(get_db),
    conversation_in: ConversationCreate,
    session_id: str
) -> Any:
    """
    Create new conversation.
    """
    # Set a default title if none provided - will be updated when first message is added
    title = conversation_in.title if conversation_in.title and conversation_in.title.strip() else "New conversation"
    
    conversation = Conversation(
        title=title,
        session_id=session_id
    )
    session.add(conversation)
    session.commit()
    session.refresh(conversation)
    return conversation


@router.delete("/{conversation_id}")
def delete_conversation(
    conversation_id: UUID,
    session: Session = Depends(get_db),
    session_id: str = None,
) -> GenericMessage:
    """
    Delete a conversation.
    """
    conversation = session.get(Conversation, conversation_id)
    if not conversation:
        raise HTTPException(status_code=404, detail="Conversation not found")
    
    if session_id and conversation.session_id != session_id:
        raise HTTPException(status_code=403, detail="Not enough permissions")
    
    session.delete(conversation)
    session.commit()
    return GenericMessage(message="Conversation deleted successfully")


@router.post("/{conversation_id}/messages", response_model=MessagePublic)
def add_message(
    *,
    session: Session = Depends(get_db),
    conversation_id: UUID,
    message_in: MessageCreate,
    session_id: str = None,
) -> Any:
    """
    Add a message to a conversation.
    """
    conversation = session.get(Conversation, conversation_id)
    if not conversation:
        raise HTTPException(status_code=404, detail="Conversation not found")
    
    if session_id and conversation.session_id != session_id:
        raise HTTPException(status_code=403, detail="Not enough permissions")
    
    # Check if this is the first user message and conversation needs a better title
    if (message_in.role.value == "user" and 
        (not conversation.title or conversation.title.strip() in ["New conversation", ""])):
        # Set title to first 50 characters of the user's message
        title_content = message_in.content.strip()
        new_title = title_content[:50] + ("..." if len(title_content) > 50 else "")
        conversation.title = new_title
    
    # Create user message
    message = Message(
        role=message_in.role,
        content=message_in.content,
        message_type=message_in.message_type,
        message_format=message_in.message_format,
        recipient=message_in.recipient,
        conversation_id=conversation_id,
    )
    session.add(message)
    # Update conversation timestamp for ordering / recency tracking
    from datetime import datetime as _dt
    conversation.updated_at = _dt.utcnow()

    session.add(conversation)
    session.commit()
    session.refresh(message)
    
    return message


@router.get("/{conversation_id}/messages", response_model=MessagesPublic)
def read_conversation_messages(
    conversation_id: UUID,
    session: Session = Depends(get_db),
    session_id: str = None,
    skip: int = 0,
    limit: int = 100,
) -> Any:
    """
    Retrieve messages for a conversation.
    """
    conversation = session.get(Conversation, conversation_id)
    if not conversation:
        raise HTTPException(status_code=404, detail="Conversation not found")
    
    if session_id and conversation.session_id != session_id:
        raise HTTPException(status_code=403, detail="Not enough permissions")
    
    count_statement = select(Message).where(Message.conversation_id == conversation_id)
    count = len(session.exec(count_statement).all())

    statement = (
        select(Message)
        .where(Message.conversation_id == conversation_id)
        .offset(skip)
        .limit(limit)
        .order_by(Message.created_at)
    )
    messages = session.exec(statement).all()

    return MessagesPublic(data=messages, count=count)


@router.put("/{conversation_id}", response_model=ConversationPublic)
def update_conversation(
    *,
    session: Session = Depends(get_db),
    conversation_id: UUID,
    conversation_in: ConversationUpdate,
    session_id: str = None,
) -> Any:
    """
    Update conversation title and favorite status.
    """
    conversation = session.get(Conversation, conversation_id)
    if not conversation:
        raise HTTPException(status_code=404, detail="Conversation not found")
    
    if session_id and conversation.session_id != session_id:
        raise HTTPException(status_code=403, detail="Not enough permissions")
    
    if conversation_in.title is not None:
        conversation.title = conversation_in.title
    
    if conversation_in.is_favorite is not None:
        conversation.is_favorite = conversation_in.is_favorite
    
    session.add(conversation)
    session.commit()
    session.refresh(conversation)
    return conversation


@router.post("/{conversation_id}/favorite", response_model=ConversationPublic)
def toggle_favorite_conversation(
    *,
    session: Session = Depends(get_db),
    conversation_id: UUID,
    session_id: str = None,
) -> Any:
    """
    Toggle favorite status of a conversation.
    """
    conversation = session.get(Conversation, conversation_id)
    if not conversation:
        raise HTTPException(status_code=404, detail="Conversation not found")
    
    if session_id and conversation.session_id != session_id:
        raise HTTPException(status_code=403, detail="Not enough permissions")
    
    conversation.is_favorite = not conversation.is_favorite
    session.add(conversation)
    session.commit()
    session.refresh(conversation)
    return conversation


@router.post("/{conversation_id}/share", response_model=ConversationShareResponse)
def create_share_link(
    *,
    session: Session = Depends(get_db),
    conversation_id: UUID,
    share_in: ConversationShareCreate,
    session_id: str = None,
) -> Any:
    """
    Create a shareable link for a conversation.
    """
    conversation = session.get(Conversation, conversation_id)
    if not conversation:
        raise HTTPException(status_code=404, detail="Conversation not found")
    
    if session_id and conversation.session_id != session_id:
        raise HTTPException(status_code=403, detail="Not enough permissions")
    
    # Generate a unique share token if one doesn't exist
    if not conversation.share_token:
        conversation.share_token = secrets.token_urlsafe(32)
        conversation.is_shared = True
        session.add(conversation)
        session.commit()
        session.refresh(conversation)
    
    return ConversationShareResponse(
        share_token=conversation.share_token,
        share_url=f"/share/{conversation.share_token}"
    )


@router.delete("/{conversation_id}/share")
def remove_share_link(
    *,
    session: Session = Depends(get_db),
    conversation_id: UUID,
    session_id: str = None,
) -> GenericMessage:
    """
    Remove the shareable link for a conversation.
    """
    conversation = session.get(Conversation, conversation_id)
    if not conversation:
        raise HTTPException(status_code=404, detail="Conversation not found")
    
    if session_id and conversation.session_id != session_id:
        raise HTTPException(status_code=403, detail="Not enough permissions")
    
    conversation.share_token = None
    conversation.is_shared = False
    session.add(conversation)
    session.commit()
    
    return GenericMessage(message="Share link removed successfully")


@router.get("/shared/{share_token}", response_model=ConversationShared)
def get_shared_conversation(
    *,
    session: Session = Depends(get_db),
    share_token: str,
) -> Any:
    """
    Get a shared conversation by its share token (public access).
    """
    conversation = session.exec(
        select(Conversation).where(
            Conversation.share_token == share_token,
            Conversation.is_shared == True
        )
    ).first()
    
    if not conversation:
        raise HTTPException(status_code=404, detail="Shared conversation not found")
    
    # Get messages for this conversation
    messages_statement = (
        select(Message)
        .where(Message.conversation_id == conversation.id)
        .order_by(Message.created_at)
    )
    messages = session.exec(messages_statement).all()
    
    return ConversationShared(
        id=conversation.id,
        title=conversation.title,
        created_at=conversation.created_at,
        updated_at=conversation.updated_at,
        messages=messages
    )