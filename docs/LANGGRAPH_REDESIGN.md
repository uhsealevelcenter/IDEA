# Open Interpreter Redesign — LangGraph Architecture

A complete redesign of the Open Interpreter backend using LangGraph framework while maintaining feature parity with the current implementation.

---

## Table of Contents

1. [Design Philosophy](#design-philosophy)
2. [High-Level Architecture](#high-level-architecture)
3. [State Schema](#state-schema)
4. [Graph Structure](#graph-structure)
5. [Node Definitions](#node-definitions)
6. [Edge Logic & Routing](#edge-logic--routing)
7. [Tool Definitions](#tool-definitions)
8. [Streaming Implementation](#streaming-implementation)
9. [Session Management & Persistence](#session-management--persistence)
10. [Context Window Management](#context-window-management)
11. [Loop/Autonomous Mode](#loopautonomous-mode)
12. [IDEA Integration Points](#idea-integration-points)
13. [Migration Path](#migration-path)
14. [Code Structure](#code-structure)

---

## Design Philosophy

### Why LangGraph?

1. **Explicit state management** — Current implementation scatters state across `self.messages`, `self.llm`, and `self.computer`. LangGraph centralizes this in a typed state object.
2. **Declarative flow control** — Replace the imperative `while True` loop in `respond()` with a graph of nodes and conditional edges.
3. **Built-in persistence** — LangGraph checkpointing replaces manual Redis serialization.
4. **Better debuggability** — Graph visualization and step-by-step inspection vs. tracing through generators.
5. **Composability** — Sub-graphs for OS mode, MCP tool planning, custom instructions can be plugged in cleanly.

### Core Principles

- **Feature parity first** — Match all current functionality before adding new features
- **Streaming-first** — Maintain real-time token streaming to the UI
- **Backward compatible** — Same REST API contract for IDEA frontend
- **Testable** — Each node is a pure function that can be unit tested

---

## High-Level Architecture

```
┌────────────────────────────────────────────────────────────────┐
│                    InterpreterGraph                            │
│                                                                │
│  ┌──────────┐      ┌──────────┐      ┌──────────┐            │
│  │  Entry   │─────▶│   LLM    │─────▶│  Router  │            │
│  │  Node    │      │  Call    │      │   Node   │            │
│  └──────────┘      └──────────┘      └────┬─────┘            │
│                                            │                   │
│                          ┌─────────────────┼─────────────┐     │
│                          ▼                 ▼             ▼     │
│                   ┌──────────┐      ┌──────────┐  ┌─────────┐ │
│                   │   Code   │      │   Loop   │  │   End   │ │
│                   │ Executor │      │ Injector │  │  Node   │ │
│                   └────┬─────┘      └────┬─────┘  └─────────┘ │
│                        │                 │                     │
│                        └────────┬────────┘                     │
│                                 ▼                              │
│                          ┌──────────┐                          │
│                          │   LLM    │ (loop back)              │
│                          │  Call    │                          │
│                          └──────────┘                          │
│                                                                │
└────────────────────────────────────────────────────────────────┘

Checkpointer (Redis/PostgreSQL)
     ▲                    │
     │                    ▼
  Save State         Load State
```

---

## State Schema

```python
from typing import TypedDict, Literal, Optional, List, Dict, Any
from langchain_core.messages import BaseMessage

class InterpreterState(TypedDict):
    """
    The single source of truth for the conversation state.
    Replaces scattered state in current OpenInterpreter class.
    """
    
    # Message history (LangChain format, not LMC)
    messages: List[BaseMessage]
    
    # Configuration (mirrors current interpreter settings)
    system_message: str
    custom_instructions: str
    auto_run: bool
    loop_mode: bool
    loop_message: str
    loop_breakers: List[str]
    max_output: int
    os_mode: bool
    
    # LLM configuration
    model: str
    api_key: Optional[str]
    api_base: Optional[str]
    context_window: int
    max_tokens: int
    temperature: float
    
    # Computer/execution configuration
    available_languages: List[str]
    import_computer_api: bool
    safe_mode: bool  # If True, require confirmation before execution
    
    # Runtime state
    last_code_block: Optional[Dict[str, str]]  # {"language": "python", "code": "..."}
    execution_outputs: List[Dict[str, Any]]
    active_language_states: Dict[str, Any]  # Persistent REPL states
    
    # Session metadata (for IDEA integration)
    session_id: str
    user_id: str
    host_config: Dict[str, Any]  # Upload paths, custom functions, etc.
    
    # Control flags
    should_execute: bool  # Router decision
    should_loop: bool     # Loop injection decision
    should_end: bool      # Termination flag
    
    # Streaming control
    stream_buffer: List[Dict[str, Any]]  # Chunks to yield
```

---

## Graph Structure

```python
from langgraph.graph import StateGraph, END
from langgraph.checkpoint.redis import RedisSaver

def create_interpreter_graph():
    """
    Builds the core execution graph.
    """
    
    # Initialize graph with state schema
    graph = StateGraph(InterpreterState)
    
    # Add nodes
    graph.add_node("entry", entry_node)
    graph.add_node("build_system_message", build_system_message_node)
    graph.add_node("llm_call", llm_call_node)
    graph.add_node("router", router_node)
    graph.add_node("code_executor", code_executor_node)
    graph.add_node("loop_injector", loop_injector_node)
    graph.add_node("end", end_node)
    
    # Define edges
    graph.set_entry_point("entry")
    
    graph.add_edge("entry", "build_system_message")
    graph.add_edge("build_system_message", "llm_call")
    graph.add_edge("llm_call", "router")
    
    # Conditional routing from router
    graph.add_conditional_edges(
        "router",
        route_decision,
        {
            "execute": "code_executor",
            "loop": "loop_injector",
            "end": "end"
        }
    )
    
    # Code execution loops back to LLM
    graph.add_edge("code_executor", "build_system_message")
    
    # Loop injection loops back to LLM
    graph.add_edge("loop_injector", "build_system_message")
    
    graph.add_edge("end", END)
    
    # Compile with checkpointer for persistence
    checkpointer = RedisSaver(redis_conn)
    return graph.compile(checkpointer=checkpointer)
```

---

## Node Definitions

### 1. Entry Node

```python
def entry_node(state: InterpreterState) -> InterpreterState:
    """
    Initial setup and validation.
    Equivalent to chat() entry point in current implementation.
    """
    # Validate session exists
    # Load any persisted language runtime states
    # Initialize stream buffer
    state["stream_buffer"] = []
    state["should_end"] = False
    
    return state
```

### 2. Build System Message Node

```python
def build_system_message_node(state: InterpreterState) -> InterpreterState:
    """
    Constructs the system message from:
    - default_system_message
    - language-specific hints
    - custom_instructions
    - computer API docs (if import_computer_api=True)
    - dynamic rendering ({{ python_code }} blocks)
    
    Equivalent to system message assembly in respond().
    """
    from langchain_core.messages import SystemMessage
    
    system_parts = [
        state["system_message"],  # Base prompt
    ]
    
    # Add language-specific instructions
    # (e.g., Python REPL behavior, shell tips)
    
    if state["custom_instructions"]:
        system_parts.append(state["custom_instructions"])
    
    if state["import_computer_api"]:
        system_parts.append(get_computer_api_docs())
    
    # Dynamic rendering: execute {{ code }} blocks
    final_system = "\n\n".join(system_parts)
    final_system = render_dynamic_blocks(final_system)
    
    # Prepend to messages if not already there, or replace existing system message
    if not state["messages"] or not isinstance(state["messages"][0], SystemMessage):
        state["messages"].insert(0, SystemMessage(content=final_system))
    else:
        state["messages"][0] = SystemMessage(content=final_system)
    
    return state
```

### 3. LLM Call Node

```python
from langchain_openai import ChatOpenAI
from langchain_core.messages import AIMessage, ToolMessage

def llm_call_node(state: InterpreterState) -> InterpreterState:
    """
    Calls the LLM with:
    - Context window trimming (via tokentrim or LangChain's trimming)
    - Image pruning (OS mode)
    - Tool binding (execute function)
    - Streaming
    
    Equivalent to llm.run() and run_tool_calling_llm.py.
    """
    
    # Initialize LLM with tool binding
    llm = ChatOpenAI(
        model=state["model"],
        api_key=state["api_key"],
        base_url=state["api_base"],
        max_tokens=state["max_tokens"],
        temperature=state["temperature"],
        streaming=True
    )
    
    # Bind the execute tool
    execute_tool = {
        "type": "function",
        "function": {
            "name": "execute",
            "description": "Executes code on the user's machine and returns output",
            "parameters": {
                "type": "object",
                "properties": {
                    "language": {
                        "type": "string",
                        "enum": state["available_languages"]
                    },
                    "code": {"type": "string"}
                },
                "required": ["language", "code"]
            }
        }
    }
    llm_with_tools = llm.bind_tools([execute_tool])
    
    # Trim context to fit window
    messages_for_llm = trim_messages(
        state["messages"],
        max_tokens=state["context_window"] - state["max_tokens"] - 25
    )
    
    # Prune images if in OS mode
    if state["os_mode"]:
        messages_for_llm = prune_images(messages_for_llm, keep_last=2)
    
    # Stream response
    chunks = []
    for chunk in llm_with_tools.stream(messages_for_llm):
        # Add to stream buffer for yielding to client
        state["stream_buffer"].append({
            "role": "assistant",
            "type": "message" if not chunk.tool_calls else "code",
            "content": chunk.content or ""
        })
        chunks.append(chunk)
    
    # Combine chunks into final AI message
    final_message = merge_chunks(chunks)
    state["messages"].append(final_message)
    
    # Extract code block if tool was called
    if final_message.tool_calls:
        tool_call = final_message.tool_calls[0]
        state["last_code_block"] = {
            "language": tool_call["args"]["language"],
            "code": tool_call["args"]["code"],
            "call_id": tool_call["id"]
        }
    else:
        state["last_code_block"] = None
    
    return state
```

### 4. Router Node

```python
def router_node(state: InterpreterState) -> InterpreterState:
    """
    Decides next action based on last message.
    
    Routes to:
    - "execute" if last message has code block
    - "loop" if loop_mode=True and no code block and no loop_breaker
    - "end" otherwise
    
    Equivalent to the branching logic in respond().
    """
    
    if state["last_code_block"]:
        state["should_execute"] = True
        state["should_loop"] = False
        state["should_end"] = False
    else:
        # Check for loop breakers
        last_message_content = state["messages"][-1].content
        
        has_loop_breaker = any(
            breaker.lower() in last_message_content.lower()
            for breaker in state["loop_breakers"]
        )
        
        if state["loop_mode"] and not has_loop_breaker:
            state["should_execute"] = False
            state["should_loop"] = True
            state["should_end"] = False
        else:
            state["should_execute"] = False
            state["should_loop"] = False
            state["should_end"] = True
    
    return state

def route_decision(state: InterpreterState) -> str:
    """
    Conditional edge function.
    """
    if state["should_execute"]:
        return "execute"
    elif state["should_loop"]:
        return "loop"
    else:
        return "end"
```

### 5. Code Executor Node

```python
from langchain_core.messages import ToolMessage

def code_executor_node(state: InterpreterState) -> InterpreterState:
    """
    Executes the code block via Computer/Terminal.
    Yields confirmation chunk if safe_mode=True.
    Streams execution output.
    
    Equivalent to computer.run() in current implementation.
    """
    
    code_block = state["last_code_block"]
    
    # Yield confirmation chunk (if not auto_run)
    if not state["auto_run"]:
        state["stream_buffer"].append({
            "role": "computer",
            "type": "confirmation",
            "format": "execution",
            "content": f"Run {code_block['language']} code?"
        })
        # In real implementation, wait for user approval here
        # For now, assume approved
    
    # Execute via terminal
    terminal = get_terminal_instance(state["session_id"])
    
    execution_output = []
    for chunk in terminal.run(
        language=code_block["language"],
        code=code_block["code"],
        stream=True
    ):
        # Stream to client
        state["stream_buffer"].append({
            "role": "computer",
            **chunk
        })
        execution_output.append(chunk)
    
    # Combine output into single string
    combined_output = "".join(
        chunk["content"] for chunk in execution_output
        if chunk.get("format") == "output"
    )
    
    # Truncate if exceeds max_output
    if len(combined_output) > state["max_output"]:
        combined_output = combined_output[:state["max_output"]] + "\n... (truncated)"
    
    # Add ToolMessage to conversation
    tool_message = ToolMessage(
        content=combined_output,
        tool_call_id=code_block["call_id"]
    )
    state["messages"].append(tool_message)
    
    # Clear last_code_block
    state["last_code_block"] = None
    
    return state
```

### 6. Loop Injector Node

```python
from langchain_core.messages import HumanMessage

def loop_injector_node(state: InterpreterState) -> InterpreterState:
    """
    Injects the loop_message to push the LLM to continue.
    
    Equivalent to loop message injection in respond().
    """
    
    # Combine adjacent assistant messages (current implementation does this)
    state["messages"] = combine_adjacent_assistant_messages(state["messages"])
    
    # Inject loop message
    loop_msg = HumanMessage(content=state["loop_message"])
    state["messages"].append(loop_msg)
    
    # Add to stream buffer for transparency
    state["stream_buffer"].append({
        "role": "user",
        "type": "message",
        "content": state["loop_message"],
        "injected": True
    })
    
    return state
```

### 7. End Node

```python
def end_node(state: InterpreterState) -> InterpreterState:
    """
    Final cleanup before returning.
    """
    state["should_end"] = True
    return state
```

---

## Edge Logic & Routing

### Conditional Routing Function

```python
def route_decision(state: InterpreterState) -> Literal["execute", "loop", "end"]:
    """
    Maps state flags to edge labels.
    Called by add_conditional_edges.
    """
    if state["should_execute"]:
        return "execute"
    elif state["should_loop"]:
        return "loop"
    else:
        return "end"
```

---

## Tool Definitions

### Execute Tool

```python
from langchain_core.tools import tool

@tool
def execute(language: str, code: str) -> str:
    """
    Executes code on the user's machine in the specified language.
    
    Args:
        language: Programming language (python, shell, javascript, etc.)
        code: Code to execute
        
    Returns:
        Execution output or error message
    """
    # This is bound to the LLM but not actually called directly
    # The actual execution happens in code_executor_node
    # This is just the schema
    pass
```

---

## Streaming Implementation

### Stream Processor

```python
from typing import AsyncIterator
import json

async def stream_graph_execution(
    graph: CompiledGraph,
    initial_state: InterpreterState,
    session_id: str
) -> AsyncIterator[str]:
    """
    Executes the graph and yields SSE-formatted chunks.
    Replaces _respond_and_store() generator.
    """
    
    config = {
        "configurable": {
            "thread_id": session_id
        }
    }
    
    async for event in graph.astream(initial_state, config):
        # Extract node output
        node_name = list(event.keys())[0]
        node_output = event[node_name]
        
        # Yield buffered stream chunks
        if "stream_buffer" in node_output and node_output["stream_buffer"]:
            for chunk in node_output["stream_buffer"]:
                # Format as SSE
                yield f"data: {json.dumps(chunk)}\n\n"
            
            # Clear buffer after yielding
            node_output["stream_buffer"] = []
        
        # Check for end condition
        if node_output.get("should_end"):
            break
```

### FastAPI Integration

```python
from fastapi import FastAPI
from fastapi.responses import StreamingResponse

app = FastAPI()

@app.post("/idea-api/chat")
async def chat_endpoint(
    request: ChatRequest,
    session_id: str = Header(..., alias="x-session-id"),
    user: User = Depends(get_current_user)
):
    # Build initial state from request
    initial_state = build_initial_state(
        messages=request.messages,
        session_id=session_id,
        user_id=user.id,
        config=request.config
    )
    
    # Stream graph execution
    return StreamingResponse(
        stream_graph_execution(interpreter_graph, initial_state, session_id),
        media_type="text/event-stream"
    )
```

---

## Session Management & Persistence

### Redis Checkpointing

```python
from langgraph.checkpoint.redis import RedisSaver
import redis

# Initialize Redis connection
redis_conn = redis.Redis(
    host=os.getenv("REDIS_HOST", "localhost"),
    port=int(os.getenv("REDIS_PORT", 6379)),
    decode_responses=False  # LangGraph manages serialization
)

# Create checkpointer
checkpointer = RedisSaver(redis_conn)

# Compile graph with checkpointing
interpreter_graph = graph.compile(checkpointer=checkpointer)
```

### State Persistence

```python
def build_initial_state(
    messages: List[Dict],
    session_id: str,
    user_id: str,
    config: Dict
) -> InterpreterState:
    """
    Builds initial state, loading from checkpoint if exists.
    """
    
    # Try to load existing checkpoint
    checkpoint_config = {"configurable": {"thread_id": f"{user_id}:{session_id}"}}
    
    try:
        # LangGraph automatically loads from checkpoint if available
        # We just need to merge with new user message
        existing_state = interpreter_graph.get_state(checkpoint_config)
        
        if existing_state:
            # Add new message to existing conversation
            new_message = messages[-1]  # Last message from request
            existing_state["messages"].append(
                convert_to_langchain_message(new_message)
            )
            return existing_state
    except:
        pass
    
    # No checkpoint found, create fresh state
    return {
        "messages": [convert_to_langchain_message(m) for m in messages],
        "session_id": session_id,
        "user_id": user_id,
        "system_message": DEFAULT_SYSTEM_MESSAGE,
        "custom_instructions": config.get("custom_instructions", ""),
        "auto_run": config.get("auto_run", True),
        "loop_mode": config.get("loop_mode", False),
        # ... (all other config)
    }
```

---

## Context Window Management

### Message Trimming

```python
from langchain_core.messages import trim_messages as lc_trim_messages

def trim_messages(
    messages: List[BaseMessage],
    max_tokens: int
) -> List[BaseMessage]:
    """
    Trims conversation to fit context window.
    Preserves:
    - System message (always)
    - Tool calls and tool messages (execution state)
    - Recent messages
    
    Drops:
    - Middle conversational text messages
    
    Equivalent to tokentrim in current implementation.
    """
    
    # Separate system message
    system_msg = messages[0] if messages and messages[0].type == "system" else None
    other_messages = messages[1:] if system_msg else messages
    
    # Separate tool-related messages from text messages
    tool_messages = [
        m for m in other_messages 
        if m.type in ("tool", "ai") and hasattr(m, "tool_calls") and m.tool_calls
    ]
    text_messages = [
        m for m in other_messages 
        if m not in tool_messages
    ]
    
    # Use LangChain's built-in trimmer for text messages
    trimmed_text = lc_trim_messages(
        text_messages,
        max_tokens=max_tokens,
        strategy="last",  # Keep most recent
        token_counter=count_tokens,
        start_on="human"
    )
    
    # Reconstruct message list
    result = []
    if system_msg:
        result.append(system_msg)
    
    # Merge tool messages back in at their original positions
    # (This is simplified; real implementation needs position tracking)
    result.extend(trimmed_text)
    result.extend(tool_messages)
    
    return result
```

---

## Loop/Autonomous Mode

### Loop Configuration

```python
DEFAULT_LOOP_MESSAGE = """
Proceed. You CAN run code on my machine. If the entire task I asked for is done, 
say exactly 'The task is done.' If you need specific information (like username 
or password) say EXACTLY 'Please provide more information.' If it's impossible, 
say 'The task is impossible.' Otherwise keep going.
""".strip()

DEFAULT_LOOP_BREAKERS = [
    "The task is done.",
    "The task is impossible.",
    "Let me know what you'd like to do next.",
    "Please provide more information."
]
```

### Loop Injection Logic

The `loop_injector_node` handles this automatically when routed via the conditional edge.

---

## IDEA Integration Points

### Custom Instructions Injection

```python
def get_custom_instructions(session_id: str, user_id: str, host_config: Dict) -> str:
    """
    Builds IDEA-specific custom instructions.
    Includes:
    - Host/session metadata
    - Available custom functions (get_datetime, get_station_info, etc.)
    - MCP tool descriptions
    """
    
    instructions_parts = [
        f"Host: {host_config['host']}",
        f"User ID: {user_id}",
        f"Session ID: {session_id}",
        f"Upload path: {host_config['upload_path']}",
        "",
        "Available custom functions:",
    ]
    
    # Add custom function descriptions
    for func in host_config.get("custom_functions", []):
        instructions_parts.append(f"- {func['signature']}: {func['description']}")
    
    # Add MCP tools if available
    mcp_tools = get_mcp_tool_descriptions()
    if mcp_tools:
        instructions_parts.append("\nMCP Tools:")
        instructions_parts.extend(mcp_tools)
    
    return "\n".join(instructions_parts)
```

### MCP Tool Pre-flight

```python
def create_mcp_subgraph() -> CompiledGraph:
    """
    Optional sub-graph for MCP tool planning and execution.
    Can be invoked before main interpreter loop.
    """
    
    mcp_graph = StateGraph(MCPState)
    
    mcp_graph.add_node("plan_tools", plan_mcp_tools_node)
    mcp_graph.add_node("execute_tools", execute_mcp_tools_node)
    
    mcp_graph.set_entry_point("plan_tools")
    mcp_graph.add_edge("plan_tools", "execute_tools")
    mcp_graph.add_edge("execute_tools", END)
    
    return mcp_graph.compile()
```

---

## Migration Path

### Phase 1: Parallel Implementation (2-4 weeks)

1. **Week 1**: Core graph structure
   - Implement state schema
   - Build basic nodes (entry, llm_call, router, end)
   - Add execute tool binding
   - Wire up basic flow without execution

2. **Week 2**: Code execution integration
   - Port Terminal/Computer classes (no changes needed)
   - Implement code_executor_node
   - Add streaming buffer logic
   - Test Python/Shell execution

3. **Week 3**: Advanced features
   - Loop mode (loop_injector_node)
   - Context trimming
   - Image handling (OS mode)
   - Session persistence (Redis checkpointer)

4. **Week 4**: IDEA integration
   - FastAPI endpoint wrapper
   - Custom instructions injection
   - MCP tool integration
   - Message format conversion (LMC ↔ LangChain)

### Phase 2: Testing & Validation (1-2 weeks)

- Side-by-side testing with current implementation
- Performance benchmarking (latency, memory)
- Conversation history migration scripts
- A/B testing in production

### Phase 3: Cutover (1 week)

- Feature flag to switch between implementations
- Monitor error rates and user feedback
- Gradual rollout (10% → 50% → 100%)
- Deprecate old implementation

---

## Code Structure

```
backend/
├── langgraph_interpreter/
│   ├── __init__.py
│   ├── graph.py                 # Graph construction
│   ├── state.py                 # State schema definition
│   ├── nodes/
│   │   ├── __init__.py
│   │   ├── entry.py
│   │   ├── system_message.py
│   │   ├── llm_call.py
│   │   ├── router.py
│   │   ├── executor.py
│   │   ├── loop_injector.py
│   │   └── end.py
│   ├── tools/
│   │   ├── __init__.py
│   │   └── execute.py           # Execute tool definition
│   ├── utils/
│   │   ├── __init__.py
│   │   ├── trimming.py          # Context window management
│   │   ├── streaming.py         # Stream processors
│   │   └── conversion.py        # LMC ↔ LangChain message conversion
│   └── config.py                # Default configurations
├── routes/
│   └── chat_langgraph.py        # FastAPI endpoint (LangGraph version)
└── tests/
    └── langgraph_interpreter/
        ├── test_graph.py
        ├── test_nodes.py
        └── test_streaming.py
```

---

## Advantages Over Current Implementation

| Aspect | Current (Generators) | LangGraph Redesign |
|--------|---------------------|-------------------|
| **State management** | Scattered across attributes | Centralized in typed state |
| **Flow control** | Imperative `while True` loop | Declarative graph |
| **Persistence** | Manual Redis serialization | Built-in checkpointing |
| **Debuggability** | Trace through generators | Graph visualization + step debugging |
| **Testability** | Integration tests only | Unit test each node |
| **Resumability** | Restart from scratch | Resume from any checkpoint |
| **Composability** | Hard to extend | Sub-graphs for features |
| **Error handling** | Try/catch in loop | Node-level error boundaries |

---

## Disadvantages & Tradeoffs

| Consideration | Impact |
|--------------|--------|
| **Learning curve** | Team needs to learn LangGraph concepts |
| **Dependency** | Adds LangGraph as core dependency (vs. lightweight LiteLLM wrapper) |
| **Migration effort** | 4-6 weeks of development + testing |
| **Message format** | Need conversion layer between LMC and LangChain messages |
| **Streaming complexity** | Stream buffer management adds indirection |

---

## Open Questions

1. **Checkpointing granularity**: Checkpoint after every node or only after complete turns?
2. **Streaming latency**: Does LangGraph's state updates add measurable latency compared to direct generators?
3. **Memory footprint**: How does state serialization affect memory with 400k token contexts?
4. **Tool calling models**: Can we cleanly support both tool-calling and text-only models in the same graph?
5. **Interrupt handling**: How to handle user interrupts (Ctrl+C) mid-execution in graph execution?

---

## Next Steps

1. **Prototype** the core graph with entry → llm_call → router → end flow
2. **Benchmark** streaming latency vs. current implementation
3. **Design** the LMC ↔ LangChain message conversion layer
4. **Validate** checkpointing with Redis (memory usage, serialization speed)
5. **Build** parallel FastAPI endpoint for A/B testing

---

## References

- [LangGraph Documentation](https://langchain-ai.github.io/langgraph/)
- [Current Open Interpreter Internals](./OPEN_INTERPRETER_INTERNALS.md)
- [LangGraph Checkpointing Guide](https://langchain-ai.github.io/langgraph/how-tos/persistence/)
- [LangGraph Streaming Guide](https://langchain-ai.github.io/langgraph/how-tos/streaming/)
