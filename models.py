from pydantic import BaseModel
from typing import Optional

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