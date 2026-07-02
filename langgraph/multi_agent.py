"""
Conversation Orchestrator for Terminal Agent
Manages conversation context, chat history, database persistence, and delegates 
task execution to the terminal agent.
"""

import os
import sys
import uuid
import logging
from typing import Dict, Any, Optional, Callable, Generator
from datetime import datetime

# Make database imports optional for microservice deployment
try:
    from sqlmodel import Session
    from db.conversation_crud import ConversationCRUD, MessageCRUD
    from models import MessageRole, MessageType, MessageFormat
    DB_AVAILABLE = True
except ImportError:
    Session = None
    ConversationCRUD = None
    MessageCRUD = None
    MessageRole = None
    MessageType = None
    MessageFormat = None
    DB_AVAILABLE = False

from agents.terminal_agent import TerminalAgent

logger = logging.getLogger(__name__)


class ConversationOrchestrator:
    """
    Orchestrates conversations with the terminal agent.
    
    Responsibilities:
    - Manage conversation context and history
    - Decide what context to provide to terminal agent
    - Handle database persistence (for registered users)
    - Format responses for frontend (SSE streaming)
    - Coordinate between user input, agent execution, and persistence
    """
    
    def __init__(
        self,
        user_id: Optional[str],
        session_id: str,
        is_guest: bool,
        db: Optional[Session] = None,
        model: str = "gpt-5.5",
        temperature: Optional[float] = None,
        max_iterations: int = 20
    ):
        self.user_id = user_id
        self.session_id = session_id
        self.is_guest = is_guest
        self.db = db
        self.model = model
        self.temperature = temperature
        self.max_iterations = max_iterations
        
        # Conversation management
        self.conversation_history: list[dict] = []
        
        # Terminal agent (created on demand)
        self._agent: Optional[TerminalAgent] = None
        
        # System prompt and instructions
        self.system_message = ""
        self.custom_instructions = ""
        
        # NOTE: Conversation persistence is handled via Redis (like current system)
        # Database conversations are created manually via API endpoints
    
    # Database persistence removed - using Redis only (matching current behavior)
    # Manual conversation saves happen via API endpoints in conversation_routes.py
    
    def _build_agent_context(self, user_message: str) -> str:
        """
        Build context for terminal agent.
        Decides what history/context to include for optimal task execution.
        """
        # Start with system instructions
        context_parts = []
        
        if self.system_message:
            context_parts.append(self.system_message)
        
        if self.custom_instructions:
            context_parts.append(f"\nAdditional Instructions:\n{self.custom_instructions}")
        
        # Include relevant conversation history (last N messages for context)
        # TODO: Smart context selection (not all history, just relevant parts)
        max_history_messages = 10
        recent_history = self.conversation_history[-max_history_messages:]
        
        if recent_history:
            context_parts.append("\nRecent conversation context:")
            for msg in recent_history:
                role = msg['role'].upper()
                content = msg['content']
                context_parts.append(f"{role}: {content}")
        
        # Current task
        context_parts.append(f"\nCurrent task:\n{user_message}")
        
        return "\n".join(context_parts)
    
    def chat(
        self,
        message: str,
        stream: bool = True
    ) -> Generator[dict, None, None]:
        """
        Process user message and stream agent responses.
        
        Yields SSE-compatible chunks in Open Interpreter format.
        """
        # Add user message to history
        user_msg = {
            'role': 'user',
            'type': 'message',
            'content': message
        }
        self.conversation_history.append(user_msg)
        
        # Build context for terminal agent
        agent_context = self._build_agent_context(message)
        
        # Create or reuse terminal agent
        if not self._agent:
            self._agent = TerminalAgent(
                model=self.model,
                temperature=self.temperature,
                max_iterations=self.max_iterations
            )
        
        # Stream agent execution
        assistant_messages = []
        chunk_index = [0]  # Use list to allow modification in nested function
        
        def capture_stream(content):
            """Capture streaming content and yield as SSE chunks. Accepts str or dict."""
            # Handle dict chunks (e.g., images) directly
            if isinstance(content, dict):
                assistant_messages.append(content)
                return
            
            # Handle string content (normal text streaming)
            is_first = chunk_index[0] == 0
            chunk = {
                'role': 'assistant',
                'type': 'message',
                'content': content
            }
            if is_first:
                chunk['start'] = True
            assistant_messages.append(chunk)
            chunk_index[0] += 1
        
        try:
            # Run agent with streaming
            result = self._agent.run(
                prompt=agent_context,
                stream_callback=capture_stream if stream else None
            )
            
            # Mark last message chunk with end flag (images already have start/end)
            if assistant_messages:
                # Find the last message chunk (not image)
                for i in range(len(assistant_messages) - 1, -1, -1):
                    if assistant_messages[i].get('type') == 'message':
                        assistant_messages[i]['end'] = True
                        break
            
            for chunk in assistant_messages:
                yield chunk
            
            # Consolidate into ONE history entry for the final text answer,
            # instead of one entry per streaming chunk (streaming can emit
            # hundreds of tiny deltas per reply) and instead of one entry per
            # intermediate tool-execution step (role 'computer': shell
            # commands, file writes, console output). Tool steps are working
            # memory for this single run() call only - they are NOT carried
            # into cross-turn conversation_history, since dumping raw/
            # truncated command output there would blow out the "last N
            # messages" context window used by _build_agent_context and crowd
            # out what actually matters (what the user asked, what the agent
            # concluded). The agent can always re-inspect its own working
            # directory (ls/cat/find) if it needs that detail again.
            consolidated_text = "".join(
                msg.get('content', '') for msg in assistant_messages
                if msg.get('type') == 'message' and isinstance(msg.get('content'), str)
            )
            if consolidated_text:
                self.conversation_history.append({
                    'role': 'assistant',
                    'type': 'message',
                    'content': consolidated_text
                })
            
            image_count = sum(1 for msg in assistant_messages if msg.get('type') == 'image')
            if image_count:
                self.conversation_history.append({
                    'role': 'assistant',
                    'type': 'message',
                    'content': f"[Generated {image_count} image(s) this turn]"
                })
            
        except Exception as e:
            logger.error(f"Agent execution failed: {e}")
            error_msg = {
                'role': 'assistant',
                'type': 'message',
                'content': f"Error: {str(e)}"
            }
            yield error_msg
    
    def cleanup(self):
        """Clean up terminal agent resources."""
        if self._agent:
            self._agent.cleanup()
            self._agent = None


# ========== SIMPLE API ==========

def run_agent_task(
    prompt: str,
    model: str = "gpt-5.5",
    temperature: Optional[float] = None,
    max_iterations: int = 20,
    stream_callback: Optional[Callable[[str], None]] = None
) -> Dict[str, Any]:
    """
    Simple API for running a one-off terminal agent task.
    For integration with conversation orchestrator, use ConversationOrchestrator.chat()
    """
    agent = TerminalAgent(
        model=model,
        temperature=temperature,
        max_iterations=max_iterations
    )
    
    try:
        result = agent.run(prompt=prompt, stream_callback=stream_callback)
        return result
    finally:
        agent.cleanup()


# ========== TEST ==========

if __name__ == "__main__":
    if not os.getenv("OPENAI_API_KEY"):
        print("ERROR: OPENAI_API_KEY not set")
        sys.exit(1)
    
    # Stream callback for real-time output
    def stream_output(content: str):
        print(content, end='', flush=True)
    
    # Run a simple test task using the orchestrator
    result = run_agent_task(
        prompt="Create a Python script that prints 'Hello from Terminal Agent!' and run it.",
        max_iterations=10,
        stream_callback=stream_output,
        model="gpt-5.5"
    )
    
    print(f"\n{'='*80}")
    print("RESULT")
    print(f"{'='*80}")
    print(f"Success: {result['success']}")
    print(f"Task Complete: {result['task_complete']}")
    print(f"Iterations: {result['iterations']}")
    print(f"\nFinal Response:\n{result['final_response']}")
