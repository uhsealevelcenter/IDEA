# Multi-Agent Terminal Agent Integration Plan

## Executive Summary

**Goal:** Replace Open Interpreter with our custom terminal agent using a clean orchestrator architecture.

**Existing Infrastructure (No Changes Needed):**
- ✅ **User Model & Auth** - PostgreSQL users with JWT authentication
- ✅ **Conversation Model** - PostgreSQL conversations with messages
- ✅ **Message Model** - Stores conversation messages with metadata
- ✅ **Chat Endpoint** - `/chat` with SSE streaming
- ✅ **Session Management** - Redis-based active sessions
- ✅ **Frontend** - React app consuming SSE stream (expects Open Interpreter format)

**What We're Building:**

### 1. **Database Abstraction Layer** ✅ BUILT
- `ConversationCRUD` - Clean API for conversation operations (create, read, update, delete)
- `MessageCRUD` - Clean API for message operations (add, batch add, list)
- Complete separation between business logic and database operations

### 2. **Conversation Orchestrator** ✅ BUILT
- `ConversationOrchestrator` in `multi-agent.py`
- Manages conversation context and history
- Decides what context to provide to terminal agent
- Handles database persistence (registered users) vs Redis (guests)
- Formats responses for frontend SSE streaming (Open Interpreter format)
- Delegates task execution to `TerminalAgent`

### 3. **Terminal Agent** ✅ ALREADY EXISTS
- Focused purely on task execution
- No conversation management
- No database concerns
- Just executes commands and returns results

### 4. **Integration with app.py** 🔨 TODO
- Replace `OpenInterpreter` with `ConversationOrchestrator`
- Minimal changes to existing code

**Result:** Clean architecture with separation of concerns, complete Open Interpreter removal, automatic persistence for users, zero frontend changes.

---

## Architecture

```
┌─────────────────────────────────────────────────────────────────┐
│                    FastAPI /chat Endpoint                        │
│  (Handles: auth, rate limiting, session locking, SSE streaming) │
└────────────────────────────┬────────────────────────────────────┘
                             │
                             ▼
┌─────────────────────────────────────────────────────────────────┐
│              ConversationOrchestrator                            │
│  • Manages conversation context & history                        │
│  • Decides what context to give terminal agent                   │
│  • Coordinates persistence (DB for users, Redis for guests)      │
│  • Formats responses for frontend (OI format for SSE)            │
└──────────┬──────────────────────────────┬───────────────────────┘
           │                              │
           ▼                              ▼
┌──────────────────────────┐   ┌──────────────────────────────────┐
│   Database CRUD Layer    │   │       TerminalAgent              │
│  • ConversationCRUD      │   │  • Executes tasks                │
│  • MessageCRUD           │   │  • Persistent terminal           │
│  • Clean abstraction     │   │  • No conversation logic         │
└──────────┬───────────────┘   │  • Focused on execution          │
           │                   └──────────────────────────────────┘
           ▼
┌──────────────────────────┐
│   PostgreSQL Database    │
│  • Conversation table    │
│  • Message table         │
│  • User table            │
└──────────────────────────┘
```

---

## Why Open Interpreter Format? (Answering Your Question!)

**Question:** "Why do you need to do the OI to LangChain conversion? I thought we are removing open interpreter?"

**Answer:** The **frontend** expects Open Interpreter message format in SSE streaming:

```javascript
// Frontend expects this format (from Open Interpreter):
{
  role: "assistant",
  type: "message",  // or "code", "console", etc.
  content: "...",
  format: "python"  // optional
}
```

**Internal Flow:**
1. Frontend sends message → expects OI format back
2. Orchestrator receives → works in dict format
3. TerminalAgent executes → uses LangChain internally
4. Orchestrator formats → converts back to OI format for frontend
5. Frontend receives → understands and displays correctly

**We're NOT using Open Interpreter code** - just maintaining its message format for frontend compatibility (zero frontend changes).

---

## Overview
Replace Open Interpreter with the multi-agent terminal agent while maintaining seamless compatibility with the existing chat endpoint and SSE streaming infrastructure.

---

## Current Open Interpreter Interface Analysis

### Key Methods & Properties Used:
```python
# Initialization
interpreter = OpenInterpreter()
interpreter.system_message = sys_prompt + active_prompt
interpreter.llm.model = "gpt-4o"
interpreter.llm.supports_vision = True
interpreter.llm.supports_functions = True
interpreter.llm.temperature = 0.2
interpreter.llm.context_window = 128000
interpreter.llm.max_tokens = 16383
interpreter.max_output = 64000
interpreter.code_execution_timeout = 600
interpreter.auto_run = True

# Message Management
interpreter.messages = []  # List of message dicts
interpreter.custom_instructions = "..."  # Added via custom property

# Execution
for result in interpreter.chat(messages[-1], stream=True):
    # result is a dict with structure:
    # {"role": "assistant", "type": "message", "content": "...", "start": True/False, "end": True/False}
    # or tool calls: {"role": "assistant", "type": "code", "format": "python", "content": "..."}
    yield result

# Interruption
interpreter.stop_event = threading.Event()  # Custom property
interpreter.stop_event.clear()
interpreter.interrupt(timeout=1.5)

# State Repair (Custom utilities)
_ensure_all_tool_calls_have_outputs(interpreter, "...")
repair_interrupted_tool_state(interpreter, "...")
```

### Message Format:
```python
# User message
{"role": "user", "type": "message", "content": "..."}

# Assistant text response
{"role": "assistant", "type": "message", "content": "...", "start": True, "end": False}

# Code execution
{"role": "assistant", "type": "code", "format": "python", "content": "print('hello')"}

# Code output
{"role": "computer", "type": "console", "format": "output", "content": "hello"}

# Tool calls (LangChain format in our case)
{"role": "assistant", "tool_calls": [...]}
```

---

## Multi-Agent Terminal Agent Interface

### Current Implementation:
```python
# Initialization
agent = TerminalAgent(model="gpt-4o", temperature=0.2, max_iterations=20)

# Execution
result = agent.run(
    prompt="Task description",
    stream_callback=lambda content: print(content, end='')
)

# Returns
{
    'success': bool,
    'task_complete': bool,
    'iterations': int,
    'messages': list,  # LangChain message objects
    'token_summary': {...},
    'final_response': str
}
```

### Limitations:
1. ❌ No persistent message history between calls
2. ❌ Resets terminal session on each run
3. ❌ Streams only content, not structured chunks
4. ❌ No interruption support
5. ❌ Returns LangChain messages, not Open Interpreter format
6. ❌ Single-shot execution, not conversational

---

## Integration Strategy: Adapter Pattern

### Create `TerminalAgentAdapter` class that mimics Open Interpreter interface

```python
class TerminalAgentAdapter:
    """
    Adapter that makes TerminalAgent compatible with Open Interpreter interface.
    Maintains session state and converts between formats.
    """
    
    def __init__(self, session_key: str, model: str = "gpt-4o"):
        self.session_key = session_key
        self.model = model
        self.temperature = 0.2
        self.max_iterations = 20
        
        # Open Interpreter compatible properties
        self.messages = []  # Message history in OI format
        self.system_message = ""
        self.custom_instructions = ""
        self.stop_event = threading.Event()
        
        # Terminal agent (created lazily)
        self._agent = None
        
        # Streaming state
        self._streaming_queue = None
        self._stop_requested = False
        
        # LLM properties (for compatibility)
        self.llm = type('LLM', (), {
            'model': model,
            'temperature': 0.2,
            'supports_vision': True,
            'supports_functions': True,
            'context_window': 128000,
            'max_tokens': 16383,
        })()
        
        # Computer properties
        self.max_output = 64000
        self.code_execution_timeout = 1800  # 30 minutes
        self.auto_run = True
    
    def chat(self, message, stream=True):
        """
        Main chat method that mimics Open Interpreter's streaming interface.
        
        Args:
            message: User message dict or string
            stream: Whether to stream responses (always True for our case)
            
        Yields:
            Chunks in Open Interpreter format
        """
        # Implementation details below...
    
    def interrupt(self, timeout=1.5):
        """Stop the current execution."""
        # Set stop flag
        self._stop_requested = True
        self.stop_event.set()
```

---

## PostgreSQL Persistence Strategy

### Existing Infrastructure (Already Built)

**User Model** - ✅ Already exists in database:
```python
class User(UserBase, table=True):
    id: uuid.UUID (primary key)
    email: EmailStr (unique, indexed)
    hashed_password: str
    is_active: bool
    is_superuser: bool
    full_name: str | None
    created_at: datetime
```

**Authentication** - ✅ Already implemented:
- User registration and login
- JWT token-based auth
- Password reset functionality
- Guest user support (temporary accounts)
- Session management

**Existing Conversation Infrastructure** - ✅ Already exists:
- Conversation model with user relationship
- Message model with conversation relationship
- Conversation sharing functionality
- API endpoints for conversation management (`conversation_routes.py`)

**What We're Adding:**
- Link conversations to terminal agent sessions via `session_id`
- Track terminal agent metadata (model, tokens, cost)
- Persist messages during chat streaming (not just manual saves)
- Auto-create conversations for registered users during chat

### Current Database Schema (Already Exists)

**Conversation Model:**
```python
class Conversation(SQLModel, table=True):
    id: uuid.UUID (primary key)
    user_id: uuid.UUID (foreign key to user.id)
    title: str | None
    share_token: str | None
    is_shared: bool
    is_favorite: bool
    created_at: datetime
    updated_at: datetime
    
    # Relationships
    messages: list[Message]
    user: User
```

**Message Model:**
```python
class Message(SQLModel, table=True):
    id: uuid.UUID (primary key)
    conversation_id: uuid.UUID (foreign key to conversation.id)
    role: MessageRole  # USER, ASSISTANT, COMPUTER
    content: str
    message_type: MessageType  # MESSAGE, CODE, IMAGE, CONSOLE, CONFIRMATION
    message_format: MessageFormat | None  # OUTPUT, PATH, BASE64_PNG, PYTHON, SHELL, etc.
    recipient: MessageRecipient | None  # USER, ASSISTANT
    created_at: datetime
    
    # Relationships
    conversation: Conversation
```

### Persistence Rules

#### For Guest Users (Ephemeral)
- **Redis Only**: Messages stored in `messages:{session_key}`
- **No Database**: No Conversation or Message records created
- **TTL**: Expires with session (based on `LAST_ACTIVE_PREFIX`)
- **Session Key Format**: `guest_<user_id>_<session_id>`

#### For Registered Users (Persistent)
- **PostgreSQL**: Create Conversation and Message records
- **Redis Cache**: Still use Redis for active sessions (performance)
- **Sync Strategy**: 
  - On conversation start: Create Conversation record
  - After each message exchange: Save Message records
  - On session end: Ensure all messages persisted
- **Session Key Format**: `<user_id>_<session_id>`

### Metadata to Track

**Conversation Level:**
- ✅ `title` - Auto-generated from first user message (first 50 chars)
- ✅ `user_id` - Owner of the conversation
- ✅ `created_at` - When conversation started
- ✅ `updated_at` - Last message timestamp
- ✅ `is_favorite` - User can mark as favorite
- ✅ `is_shared` - Whether shared via link

**Message Level:**
- ✅ `role` - USER | ASSISTANT | COMPUTER
- ✅ `content` - Full message content (no truncation)
- ✅ `message_type` - MESSAGE | CODE | CONSOLE | etc.
- ✅ `message_format` - Language/format if applicable
- ✅ `created_at` - Message timestamp
- ✅ `conversation_id` - Parent conversation

**Additional Tracking (New Fields):**
```python
# Add to Conversation model via migration:
- session_id: str | None  # Link to runtime session
- model_name: str | None  # Which model was used
- total_tokens: int | None  # Token usage
- total_cost: float | None  # Estimated cost
- agent_type: str  # "open_interpreter" or "terminal_agent"
```

---

## Detailed Implementation Plan

### Phase 1: Create Adapter Class ✅

**File:** `/Users/rodericktabalba/Documents/Github/IDEA/langgraph/agents/terminal_agent_adapter.py`

**Key Components:**

1. **Message History Management**
   - Maintain `self.messages` list in Open Interpreter format
   - Convert between OI format and LangChain format
   - Persist to Redis after each interaction

2. **Streaming Adapter**
   - Convert TerminalAgent streaming to OI chunk format
   - Use threading.Queue for async chunk delivery
   - Run agent in background thread
   - Yield chunks as they arrive

3. **System Prompt Integration**
   - Combine `system_message` + `custom_instructions`
   - Pass to TerminalAgent's system prompt
   - Support dynamic updates

4. **Interruption Handling**
   - Monitor `stop_event` in background thread
   - Gracefully terminate agent execution
   - Mark incomplete tool calls

### Phase 2: Database Migration ✅

**Create Alembic Migration:**

```bash
cd /Users/rodericktabalba/Documents/Github/IDEA
alembic revision --autogenerate -m "Add terminal agent metadata to conversations"
```

**Migration Content:**

```python
# alembic/versions/xxx_add_terminal_agent_metadata.py

def upgrade() -> None:
    # Add new columns to conversation table
    op.add_column('conversation', sa.Column('session_id', sa.String(length=255), nullable=True))
    op.add_column('conversation', sa.Column('model_name', sa.String(length=100), nullable=True))
    op.add_column('conversation', sa.Column('total_tokens', sa.Integer(), nullable=True))
    op.add_column('conversation', sa.Column('total_cost', sa.Float(), nullable=True))
    op.add_column('conversation', sa.Column('agent_type', sa.String(length=50), nullable=True))
    
    # Create index on session_id for faster lookups
    op.create_index('ix_conversation_session_id', 'conversation', ['session_id'])

def downgrade() -> None:
    op.drop_index('ix_conversation_session_id', table_name='conversation')
    op.drop_column('conversation', 'agent_type')
    op.drop_column('conversation', 'total_cost')
    op.drop_column('conversation', 'total_tokens')
    op.drop_column('conversation', 'model_name')
    op.drop_column('conversation', 'session_id')
```

**Update models.py:**

```python
# models.py - Update Conversation class
class Conversation(ConversationBase, table=True):
    id: uuid.UUID = Field(default_factory=uuid.uuid4, primary_key=True)
    user_id: uuid.UUID = Field(foreign_key="user.id", index=True, nullable=False)
    share_token: str | None = Field(default=None, max_length=255, unique=True, index=True)
    is_shared: bool = Field(default=False)
    is_favorite: bool = Field(default=False)
    created_at: datetime = Field(default_factory=datetime.utcnow)
    updated_at: datetime = Field(default_factory=datetime.utcnow)
    
    # New fields for terminal agent
    session_id: str | None = Field(default=None, max_length=255, index=True)
    model_name: str | None = Field(default=None, max_length=100)
    total_tokens: int | None = Field(default=None)
    total_cost: float | None = Field(default=None)
    agent_type: str | None = Field(default=None, max_length=50)  # "open_interpreter" or "terminal_agent"
    
    # Relationships
    messages: list["Message"] = Relationship(back_populates="conversation", cascade_delete=True)
    user: User | None = Relationship()
```

**Run Migration:**

```bash
alembic upgrade head
```

### Phase 3: Update Session Management ✅

**Changes to `app.py`:**

```python
# Replace this:
from interpreter import OpenInterpreter

# With this:
from langgraph.agents.terminal_agent_adapter import TerminalAgentAdapter

# Replace this:
def get_or_create_interpreter(session_key: str, token: str | None = None, db: Session | None = None) -> OpenInterpreter:
    interpreter = OpenInterpreter()
    # ... setup ...

# With this:
def get_or_create_interpreter(session_key: str, token: str | None = None, db: Session | None = None) -> TerminalAgentAdapter:
    # Determine user_id and guest status
    user = None
    is_guest = False
    user_id = None
    
    if token and db:
        user = get_current_user(token)
        if user:
            user_id = str(user.id)
            is_guest = _is_guest_user(user.id)
    
    # Create adapter with DB support
    interpreter = TerminalAgentAdapter(
        session_key=session_key,
        user_id=user_id,
        is_guest=is_guest,
        db=db
    )
    
    # ... setup system message and other properties (works identically) ...
```

**Key Change**: Pass `user_id`, `is_guest`, and `db` to adapter constructor.

### Phase 2.5: PostgreSQL Persistence Integration ✅

**File:** `/Users/rodericktabalba/Documents/Github/IDEA/langgraph/agents/terminal_agent_adapter.py`

**Add Database Persistence Methods:**

```python
class TerminalAgentAdapter:
    def __init__(self, session_key: str, user_id: str, is_guest: bool, db: Session = None):
        self.session_key = session_key
        self.user_id = user_id
        self.is_guest = is_guest
        self.db = db
        self.conversation_id = None  # Set when conversation is created/loaded
        
        # ... other initialization ...
    
    def _get_or_create_conversation(self) -> uuid.UUID | None:
        """
        Get existing conversation or create new one.
        Only for registered users.
        """
        if self.is_guest or not self.db:
            return None
        
        # Try to find existing conversation by session_id
        conversation = self.db.exec(
            select(Conversation)
            .where(Conversation.user_id == self.user_id)
            .where(Conversation.session_id == self.session_key)
        ).first()
        
        if conversation:
            logger.info(f"Loaded existing conversation {conversation.id}")
            return conversation.id
        
        # Create new conversation
        title = self._generate_title(self.messages)
        conversation = Conversation(
            user_id=uuid.UUID(self.user_id),
            title=title,
            session_id=self.session_key,
            model_name=self.model,
            agent_type="terminal_agent"
        )
        self.db.add(conversation)
        self.db.commit()
        self.db.refresh(conversation)
        
        logger.info(f"Created new conversation {conversation.id}")
        return conversation.id
    
    def _save_message_to_db(self, message: dict):
        """
        Save a single message to PostgreSQL.
        Only for registered users.
        """
        if self.is_guest or not self.db or not self.conversation_id:
            return
        
        try:
            # Map OI message to DB message
            db_message = Message(
                conversation_id=self.conversation_id,
                role=MessageRole(message['role']),
                content=message.get('content', ''),
                message_type=MessageType(message.get('type', 'message').upper()),
                message_format=MessageFormat(message['format'].upper()) if message.get('format') else None,
            )
            
            self.db.add(db_message)
            self.db.commit()
            
            # Update conversation timestamp
            conversation = self.db.get(Conversation, self.conversation_id)
            if conversation:
                conversation.updated_at = datetime.utcnow()
                self.db.commit()
            
        except Exception as e:
            logger.error(f"Failed to save message to DB: {e}")
            self.db.rollback()
    
    def _load_conversation_from_db(self) -> list[dict]:
        """
        Load conversation history from PostgreSQL.
        Only for registered users.
        """
        if self.is_guest or not self.db:
            return []
        
        try:
            # Find conversation by session_id
            conversation = self.db.exec(
                select(Conversation)
                .where(Conversation.user_id == uuid.UUID(self.user_id))
                .where(Conversation.session_id == self.session_key)
                .options(selectinload(Conversation.messages))
            ).first()
            
            if not conversation:
                return []
            
            self.conversation_id = conversation.id
            
            # Convert DB messages to OI format
            oi_messages = []
            for msg in sorted(conversation.messages, key=lambda m: m.created_at):
                oi_msg = {
                    'role': msg.role.value,
                    'type': msg.message_type.value.lower(),
                    'content': msg.content,
                }
                if msg.message_format:
                    oi_msg['format'] = msg.message_format.value.lower()
                oi_messages.append(oi_msg)
            
            logger.info(f"Loaded {len(oi_messages)} messages from DB for conversation {conversation.id}")
            return oi_messages
            
        except Exception as e:
            logger.error(f"Failed to load conversation from DB: {e}")
            return []
    
    def _update_conversation_metadata(self, token_summary: dict):
        """Update conversation with token usage and cost."""
        if self.is_guest or not self.db or not self.conversation_id:
            return
        
        try:
            conversation = self.db.get(Conversation, self.conversation_id)
            if conversation:
                conversation.total_tokens = token_summary.get('total_tokens')
                conversation.total_cost = token_summary.get('cost_estimate', {}).get('total_cost')
                conversation.updated_at = datetime.utcnow()
                self.db.commit()
        except Exception as e:
            logger.error(f"Failed to update conversation metadata: {e}")
            self.db.rollback()
    
    @staticmethod
    def _generate_title(messages: list[dict], max_length: int = 50) -> str:
        """Generate conversation title from first user message."""
        for msg in messages:
            if msg.get('role') == 'user' and msg.get('content'):
                content = msg['content'].strip()
                if len(content) > max_length:
                    return content[:max_length] + "..."
                return content
        return "New Conversation"
```

**Update chat() method to integrate persistence:**

```python
def chat(self, message, stream=True):
    """Stream responses and save to DB."""
    # Add user message
    user_msg = self._normalize_message(message)
    self.messages.append(user_msg)
    
    # For registered users: ensure conversation exists and save user message
    if not self.is_guest and self.db:
        if not self.conversation_id:
            self.conversation_id = self._get_or_create_conversation()
        self._save_message_to_db(user_msg)
    
    # ... streaming logic ...
    
    # After agent completes, save assistant messages to DB
    if not self.is_guest and self.db:
        for msg in self.messages[len(self.messages) - assistant_msg_count:]:
            self._save_message_to_db(msg)
        
        # Update metadata
        if hasattr(self, '_last_result'):
            self._update_conversation_metadata(
                self._last_result.get('token_summary', {})
            )
    
    # Always save to Redis for active session
    redis_client.set(f"messages:{self.session_key}", json.dumps(self.messages))
```

**Update initialization to load from DB:**

```python
def __init__(self, session_key: str, user_id: str, is_guest: bool, db: Session = None):
    # ... initialization ...
    
    # Load conversation history
    if not is_guest and db:
        db_messages = self._load_conversation_from_db()
        if db_messages:
            self.messages = db_messages
            logger.info(f"Initialized with {len(self.messages)} messages from DB")
    else:
        # Try Redis for active session (guest or registered)
        stored_messages = redis_client.get(f"messages:{session_key}")
        if stored_messages:
            self.messages = json.loads(stored_messages)
            logger.info(f"Initialized with {len(self.messages)} messages from Redis")
```

### Phase 3: Message Format Conversion ✅

**Convert between formats:**

```python
def langchain_to_oi_message(lc_msg) -> dict:
    """Convert LangChain message to Open Interpreter format."""
    if isinstance(lc_msg, HumanMessage):
        return {"role": "user", "type": "message", "content": lc_msg.content}
    elif isinstance(lc_msg, AIMessage):
        if hasattr(lc_msg, 'tool_calls') and lc_msg.tool_calls:
            return {
                "role": "assistant",
                "type": "code",
                "format": "shell",
                "content": lc_msg.tool_calls[0]['args']['command']
            }
        return {"role": "assistant", "type": "message", "content": lc_msg.content}
    elif isinstance(lc_msg, ToolMessage):
        return {"role": "computer", "type": "console", "format": "output", "content": lc_msg.content}
    return {"role": "assistant", "type": "message", "content": str(lc_msg.content)}

def oi_to_langchain_messages(oi_messages) -> list:
    """Convert Open Interpreter messages to LangChain format."""
    messages = []
    for msg in oi_messages:
        if msg['role'] == 'user':
            messages.append(HumanMessage(content=msg['content']))
        elif msg['role'] == 'assistant':
            messages.append(AIMessage(content=msg['content']))
        elif msg['role'] == 'computer':
            # Skip - these are tool outputs already captured
            pass
    return messages
```

### Phase 4: Streaming Implementation ✅

**Threading-based streaming:**

```python
def chat(self, message, stream=True):
    """Stream responses in OI format."""
    # Add user message to history
    user_msg = self._normalize_message(message)
    self.messages.append(user_msg)
    
    # Start streaming
    self._streaming_queue = queue.Queue()
    self._stop_requested = False
    
    # Build full conversation context
    conversation = self._build_conversation_prompt()
    
    # Stream callback that converts to OI format
    def stream_callback(content: str):
        if not self._stop_requested:
            chunk = {
                "role": "assistant",
                "type": "message",
                "content": content,
            }
            self._streaming_queue.put(chunk)
    
    # Run agent in background thread
    def run_agent():
        try:
            result = self._get_or_create_agent().run(
                prompt=conversation,
                stream_callback=stream_callback
            )
            # Store result
            self._last_result = result
            # Convert messages to OI format and append
            for lc_msg in result['messages']:
                oi_msg = langchain_to_oi_message(lc_msg)
                if oi_msg not in self.messages:
                    self.messages.append(oi_msg)
            # Signal completion
            self._streaming_queue.put(None)
        except Exception as e:
            self._streaming_queue.put({"error": str(e)})
            self._streaming_queue.put(None)
    
    thread = threading.Thread(target=run_agent, daemon=True)
    thread.start()
    
    # Yield chunks from queue
    while True:
        chunk = self._streaming_queue.get()
        if chunk is None:
            break
        if 'error' in chunk:
            yield chunk
            break
        yield chunk
    
    thread.join(timeout=5)
```

### Phase 5: Testing & Validation ✅

**Test cases:**

1. ✅ Basic message exchange
2. ✅ Tool call execution (terminal commands)
3. ✅ Multi-turn conversation
4. ✅ Interruption handling
5. ✅ Message persistence to Redis
6. ✅ SSE streaming to frontend
7. ✅ Error handling and recovery
8. ✅ Long-running commands (30 min timeout)

---

## Implementation Checklist

### Database Changes:
- [ ] Update `models.py` - Add new fields to Conversation model
- [ ] Create Alembic migration for new fields
- [ ] Run migration: `alembic upgrade head`
- [ ] Verify migration in staging database

### Code Files to Create:
- [ ] `/langgraph/agents/terminal_agent_adapter.py` - Main adapter class with DB persistence
- [ ] `/langgraph/agents/message_converter.py` - Format conversion utilities

### Code Files to Modify:
- [ ] `/app.py` - Update imports and `get_or_create_interpreter()` to pass DB params
- [ ] `/models.py` - Add new fields to Conversation model
- [ ] No other changes needed! (Adapter maintains compatibility)

### Configuration Updates:
- [ ] Update timeout to 30 minutes (already done in PersistentTerminal)
- [ ] Verify model configuration matches
- [ ] Test rate limiting still works
- [ ] Verify PostgreSQL connection pool size adequate

### Testing:
- [ ] Unit tests for adapter (with and without DB)
- [ ] Test guest user (ephemeral, Redis-only)
- [ ] Test registered user (PostgreSQL persistence)
- [ ] Test conversation loading from DB
- [ ] Test message saving to DB
- [ ] Integration test with chat endpoint
- [ ] Frontend SSE streaming test
- [ ] Load test for multiple sessions
- [ ] Interruption test
- [ ] DB rollback/error handling test

---

## Benefits of This Approach

### ✅ Minimal Code Changes
- Only 2 files to modify in main app
- Adapter encapsulates all complexity
- Existing utilities (`_ensure_all_tool_calls_have_outputs`, etc.) still work

### ✅ Preserves Existing Functionality
- Message persistence unchanged
- SSE streaming format unchanged
- Session management unchanged
- Tool call repair logic unchanged

### ✅ Maintains Compatibility
- Frontend requires no changes
- Redis schema unchanged
- API interface unchanged
- Error handling patterns preserved

### ✅ Adds New Capabilities
- 30-minute timeout for long operations
- More reliable terminal session management
- Better command execution tracking
- Improved state isolation

---

## Rollback Strategy

If issues arise:
1. Keep Open Interpreter import available
2. Add feature flag: `USE_TERMINAL_AGENT = os.getenv("USE_TERMINAL_AGENT", "false") == "true"`
3. Switch implementation based on flag:
   ```python
   if USE_TERMINAL_AGENT:
       from langgraph.agents.terminal_agent_adapter import TerminalAgentAdapter as InterpreterClass
   else:
       from interpreter import OpenInterpreter as InterpreterClass
   ```

---

## Timeline Estimate

- **Phase 1 (Adapter Class):** 4-6 hours
- **Phase 2 (Database Migration):** 1-2 hours
- **Phase 2.5 (PostgreSQL Persistence):** 3-4 hours
- **Phase 3 (Session Management Update):** 1-2 hours
- **Phase 4 (Message Conversion):** 2-3 hours
- **Phase 5 (Streaming):** 3-4 hours
- **Phase 6 (Testing):** 6-8 hours

**Total:** ~20-29 hours of development time

**Breakdown by Component:**
- Core adapter implementation: 4-6h
- Database persistence logic: 3-4h
- Message format conversion: 2-3h
- Streaming with threading: 3-4h
- Database migration: 1-2h
- Integration updates: 1-2h
- Testing (guest + registered users): 6-8h

---

## Complete Flow Summary

### For Guest Users (Ephemeral)
```
1. User sends message → /chat endpoint
2. get_or_create_interpreter(session_key, token=None, db)
   → Creates TerminalAgentAdapter(user_id=None, is_guest=True, db=None)
3. Adapter checks Redis for active session messages
4. Runs TerminalAgent with conversation context
5. Streams responses back via SSE
6. Saves messages to Redis ONLY (no PostgreSQL)
7. Messages expire with session TTL
```

### For Registered Users (Persistent)
```
1. User sends message → /chat endpoint
2. get_or_create_interpreter(session_key, token, db)
   → Creates TerminalAgentAdapter(user_id=<uuid>, is_guest=False, db=<Session>)
3. Adapter initialization:
   a. Check PostgreSQL for existing conversation (by session_id)
   b. If found: Load messages from DB
   c. If not found: Check Redis for active session
   d. Store messages in self.messages
4. User message arrives:
   a. Append to self.messages
   b. Create Conversation record if doesn't exist
   c. Save user Message to PostgreSQL immediately
5. Run TerminalAgent with conversation context
6. Stream responses via SSE
7. After completion:
   a. Save all assistant/computer Messages to PostgreSQL
   b. Update Conversation.updated_at, total_tokens, total_cost
   c. Save full message list to Redis (for active session cache)
8. Next message in same session:
   → Loads from PostgreSQL (already has conversation_id)
   → Appends new messages
   → Full history maintained in DB
```

### Data Flow Diagram
```
                    ┌──────────────┐
                    │  /chat POST  │
                    └──────┬───────┘
                           │
                    ┌──────▼───────────┐
                    │ TerminalAgent    │
                    │    Adapter       │
                    └──────┬───────────┘
                           │
           ┌───────────────┼───────────────┐
           │               │               │
    ┌──────▼─────┐  ┌─────▼──────┐ ┌─────▼──────┐
    │   Redis    │  │ PostgreSQL │ │ Terminal   │
    │  (Cache)   │  │   (DB)     │ │   Agent    │
    └────────────┘  └────────────┘ └────────────┘
    
    Guest:          Registered:     Execution:
    ✓ Messages      ✓ Conversation  ✓ Persistent
    ✓ Ephemeral     ✓ Messages        Terminal
    ✗ No DB         ✓ Metadata      ✓ 30min timeout
```

---

## Next Steps

1. **Review and approve** this plan
2. **Create database migration** for new Conversation fields
3. **Run migration** in development: `alembic upgrade head`
4. **Create feature branch**: `feature/terminal-agent-integration`
5. **Implement adapter class** with PostgreSQL persistence
6. **Test locally**:
   - Guest user (Redis only)
   - Registered user (PostgreSQL + Redis)
   - Conversation loading
   - Message persistence
7. **Deploy to staging** for validation
8. **Monitor** metrics, logs, and database performance
9. **Production rollout** with feature flag
10. **Remove** Open Interpreter dependency after successful rollout
