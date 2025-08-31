import uuid
from datetime import datetime
from enum import Enum
from typing import Optional

from pydantic import BaseModel, EmailStr
from sqlmodel import Field, Relationship, SQLModel
import sqlalchemy as sa

# Pydantic models for authentication
class LoginRequest(BaseModel):
    username: str
    password: str

class LoginResponse(BaseModel):
    success: bool
    token: Optional[str] = None
    message: Optional[str] = None

# Pydantic models for prompt management
class PromptCreateRequest(BaseModel):
    name: str
    description: str = ""
    content: str

class PromptUpdateRequest(BaseModel):
    name: Optional[str] = None
    description: Optional[str] = None
    content: Optional[str] = None

class PromptResponse(BaseModel):
    id: str
    name: str
    description: str
    content: str
    created_at: str
    updated_at: str
    is_active: bool

class PromptListResponse(BaseModel):
    id: str
    name: str
    description: str
    content: str
    created_at: str
    updated_at: str
    is_active: bool

class SetActivePromptRequest(BaseModel):
    prompt_id: str


# Database User Models (SQLModel)
# Shared properties
class UserBase(SQLModel):
    email: EmailStr = Field(unique=True, index=True, max_length=255)
    is_active: bool = True
    is_superuser: bool = False
    full_name: str | None = Field(default=None, max_length=255)


# Properties to receive via API on creation
class UserCreate(UserBase):
    password: str = Field(min_length=8, max_length=40)


# Properties to receive via API on update, all are optional
class UserUpdate(UserBase):
    email: EmailStr | None = Field(default=None, max_length=255)  # type: ignore
    password: str | None = Field(default=None, min_length=8, max_length=40)


class UserUpdateMe(SQLModel):
    full_name: str | None = Field(default=None, max_length=255)
    email: EmailStr | None = Field(default=None, max_length=255)


class UpdatePassword(SQLModel):
    current_password: str = Field(min_length=8, max_length=40)
    new_password: str = Field(min_length=8, max_length=40)


# Database model, database table inferred from class name
class User(UserBase, table=True):
    id: uuid.UUID = Field(default_factory=uuid.uuid4, primary_key=True)
    hashed_password: str
    created_at: datetime = Field(default_factory=datetime.utcnow)


# Properties to return via API, id is always required
class UserPublic(UserBase):
    id: uuid.UUID
    created_at: datetime


class UsersPublic(SQLModel):
    data: list[UserPublic]
    count: int


# Generic message
class GenericMessage(SQLModel):
    message: str


# JSON payload containing access token
class Token(SQLModel):
    access_token: str
    token_type: str = "bearer"


# Contents of JWT token
class TokenPayload(SQLModel):
    sub: str | None = None


class SystemPrompt(SQLModel, table=True):
    id: uuid.UUID = Field(default_factory=uuid.uuid4, primary_key=True)
    user_id: uuid.UUID = Field(foreign_key="user.id", index=True, nullable=False)
    name: str = Field(max_length=255)
    description: str = Field(default="", sa_column=sa.Column(sa.Text, nullable=False, default=""))
    content: str = Field(sa_column=sa.Column(sa.Text, nullable=False))
    created_at: datetime = Field(default_factory=datetime.utcnow)
    updated_at: datetime = Field(default_factory=datetime.utcnow)
    is_active: bool = Field(default=False, index=True)


# Enums for conversation and message models
class MessageRole(str, Enum):
    USER = "user"
    ASSISTANT = "assistant"
    COMPUTER = "computer"


class MessageType(str, Enum):
    MESSAGE = "message"
    CODE = "code"
    IMAGE = "image"
    CONSOLE = "console"
    FILE = "file"
    CONFIRMATION = "confirmation"


class MessageFormat(str, Enum):
    OUTPUT = "output"
    PATH = "path"
    BASE64_PNG = "base64.png"
    BASE64_JPEG = "base64.jpeg"
    PYTHON = "python"
    JAVASCRIPT = "javascript"
    SHELL = "shell"
    HTML = "html"
    ACTIVE_LINE = "active_line"
    EXECUTION = "execution"


class MessageRecipient(str, Enum):
    USER = "user"
    ASSISTANT = "assistant"


# Conversation Models
class ConversationBase(SQLModel):
    title: str | None = Field(default=None, max_length=255)


class ConversationCreate(ConversationBase):
    pass


class ConversationUpdate(ConversationBase):
    title: str | None = Field(default=None, max_length=255)
    is_favorite: bool | None = None


class Conversation(ConversationBase, table=True):
    id: uuid.UUID = Field(default_factory=uuid.uuid4, primary_key=True)
    user_id: uuid.UUID = Field(foreign_key="user.id", index=True, nullable=False)
    share_token: str | None = Field(default=None, max_length=255, unique=True, index=True)
    is_shared: bool = Field(default=False)
    is_favorite: bool = Field(default=False)
    created_at: datetime = Field(default_factory=datetime.utcnow)
    updated_at: datetime = Field(default_factory=datetime.utcnow)
    
    # Relationships
    messages: list["Message"] = Relationship(back_populates="conversation", cascade_delete=True)
    user: User | None = Relationship()


class ConversationPublic(ConversationBase):
    id: uuid.UUID
    user_id: uuid.UUID
    is_shared: bool
    is_favorite: bool
    created_at: datetime
    updated_at: datetime


class ConversationWithMessages(ConversationPublic):
    messages: list["MessagePublic"]


class ConversationShared(SQLModel):
    id: uuid.UUID
    title: str | None
    created_at: datetime
    updated_at: datetime
    messages: list["MessagePublic"]


class ConversationsPublic(SQLModel):
    data: list[ConversationPublic]
    count: int


# Sharing Models
class ConversationShareCreate(SQLModel):
    pass


class ConversationShareResponse(SQLModel):
    share_token: str
    share_url: str


# Message Models
class MessageBase(SQLModel):
    role: MessageRole
    content: str
    message_type: MessageType = Field(default=MessageType.MESSAGE)
    message_format: MessageFormat | None = Field(default=None)
    recipient: MessageRecipient | None = Field(default=None)


class MessageCreate(MessageBase):
    conversation_id: uuid.UUID


class MessageUpdate(SQLModel):
    content: str | None = None
    message_type: MessageType | None = None
    message_format: MessageFormat | None = None
    recipient: MessageRecipient | None = None


class Message(MessageBase, table=True):
    id: uuid.UUID = Field(default_factory=uuid.uuid4, primary_key=True)
    conversation_id: uuid.UUID = Field(foreign_key="conversation.id", nullable=False, ondelete="CASCADE")
    created_at: datetime = Field(default_factory=datetime.utcnow)
    
    # Relationships
    conversation: Conversation | None = Relationship(back_populates="messages")


class MessagePublic(MessageBase):
    id: uuid.UUID
    conversation_id: uuid.UUID
    created_at: datetime


class MessagesPublic(SQLModel):
    data: list[MessagePublic]
    count: int