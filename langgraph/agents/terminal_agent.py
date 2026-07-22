"""
Terminal Agent
A general-purpose AI agent with access to a persistent terminal session.
"""

import os
import time
import uuid
import base64
import hashlib
import requests
import threading
import queue
from typing import Dict, Any, Optional, Callable, Iterable, Iterator
from pathlib import Path
from langchain_openai import ChatOpenAI
from langchain_core.messages import HumanMessage, AIMessage, SystemMessage, ToolMessage

import sys
sys.path.append(str(Path(__file__).parent.parent))

from tools.persistent_terminal import make_agent_tools, close_terminal, read_file_bytes, list_files, run_python
from utils.tools import DATA_TOOLS
from config import LITELLM_PROXY_URL, LITELLM_VIRTUAL_KEY, LITELLM_END_USER_HEADER

SYSTEM_PROMPT_PATH = Path(__file__).parent.parent / "utils" / "system_prompt.md"

# Files placed under this directory in the sandbox (see system_prompt.md's
# "Data/Analysis Output & File Operations" section) are auto-synced to Open
# WebUI's own Files storage at the end of each turn - see
# TerminalAgent._sync_outputs_to_openwebui.
OUTPUTS_DIR = "/outputs"
OPENWEBUI_BASE_URL = os.getenv("OPENWEBUI_BASE_URL", "http://openwebui:8080").rstrip("/")
OPENWEBUI_API_KEY = os.getenv("OPENWEBUI_API_KEY", "")


class TerminalAgent:
    """
    General-purpose terminal agent that gives the LLM access to a persistent terminal session.
    The LLM can write code to files, run scripts, install packages, and solve tasks iteratively.
    """
    
    def __init__(self, session_id: str, user_id: Optional[str] = None, user_email: Optional[str] = None, model: str = "gpt-5.5", temperature: Optional[float] = None, max_iterations: int = 20):
        self.session_id = session_id
        self.user_id = user_id
        # Used only for LiteLLM per-end-user spend tracking (see
        # LITELLM_END_USER_HEADER below) - the sandbox/session identity
        # above is still keyed off user_id, not this.
        self.user_email = user_email
        self.model = model
        self.temperature = temperature
        self.max_iterations = max_iterations
        self._shown_image_hashes: set = set()  # Dedup identical images shown within a single run()
        
        # The sandbox/shell is keyed by user_id (stable across page reloads
        # and browser tabs) rather than session_id (a new random ID minted
        # by the frontend on every page load - see assistant.js generateId).
        # This is what makes the sandbox a genuinely *dedicated-per-user*
        # environment that a user reconnects to instead of getting a fresh
        # one every time. A missing user_id used to silently fall back to
        # session_id (e.g. for run_agent_task's one-off CLI usage) - now a
        # hard error instead, since a silent fallback here is exactly the
        # kind of gap that can cause multiple callers to collide on a
        # shared sandbox (see idea_pipe.py's own "anonymous" fallback for
        # a related, separate collision case on the caller side).
        if not user_id:
            raise ValueError(
                "TerminalAgent requires a non-empty user_id - refusing to "
                "fall back to session_id, which is not guaranteed unique "
                "per user and would risk multiple callers sharing a sandbox."
            )
        self.sandbox_id = str(user_id)
        
        # Terminal/filesystem tools bound to this user's own sandbox (or
        # local shell, if sandboxing is unavailable) - never shared with
        # other users. See tools/persistent_terminal.make_agent_tools.
        (
            self.run_terminal_tool,
            self.write_file_tool,
            self.show_image_tool,
            self.read_output_range_tool,
            # run_python_tool now requires the oi-kernel image
            # (SANDBOX_IMAGE=idea/oi-kernel:slim or similar) - it degrades
            # gracefully (a clear error chunk, not a crash) on sandboxes
            # still running the bare "python" image, so it's safe to
            # expose even before every user's sandbox has been recreated
            # on the new image. See sandbox_service/terminal_registry.py's
            # run_python() and msb_sandbox.py's run_python().
            self.run_python_tool,
            # grep_search_tool/glob_search_tool are unpacked but still left
            # out of self.all_tools below - unlike run_python_tool, they
            # raise (not a clean error chunk) on the local/bare-"python"
            # backend (see terminal_registry.grep_search/glob_search), so
            # enabling them needs every sandbox already on the oi-kernel
            # image first.
            _grep_search_tool,
            _glob_search_tool,
        ) = make_agent_tools(self.sandbox_id)
        self.all_tools = [
            self.run_terminal_tool,
            self.write_file_tool,
            self.show_image_tool,
            self.read_output_range_tool,
            self.run_python_tool,
            *DATA_TOOLS,
        ]
        self.tools_by_name = {t.name: t for t in self.all_tools}
        
        # Initialize LLM with tools
        # Routed through the LiteLLM proxy (see litellm/ and
        # docker-compose.yml's `litellm` service) rather than hitting the
        # Azure AI Foundry endpoint directly - LITELLM_VIRTUAL_KEY is one
        # key shared by every user (a $50 total budget, not per-user), and
        # LITELLM_END_USER_HEADER carries this user's email so LiteLLM can
        # still attribute spend/usage per end user despite the shared key.
        # Reasoning models (e.g., gpt-5.5) only support the provider default
        # temperature - omit the kwarg entirely when temperature is None.
        if not LITELLM_VIRTUAL_KEY:
            raise RuntimeError(
                "LITELLM_VIRTUAL_KEY is not set - see example.env for how to "
                "generate the shared virtual key from the litellm service."
            )
        end_user_id = (self.user_email or self.user_id or "anonymous").strip()
        llm_kwargs: Dict[str, Any] = {
            "model": model,
            "streaming": True,
            "api_key": LITELLM_VIRTUAL_KEY,
            "base_url": LITELLM_PROXY_URL,
            "default_headers": {LITELLM_END_USER_HEADER: end_user_id},
        }
        if temperature is not None:
            llm_kwargs["temperature"] = temperature
        self.llm = ChatOpenAI(**llm_kwargs).bind_tools(self.all_tools)
    
    def _encode_image_to_base64(self, filepath: str) -> tuple[str, str]:
        """
        Read an image file (from this session's sandbox/host) and encode it to base64.
        
        Returns:
            (base64_content, format) tuple, e.g., (base64_str, "png")
        """
        image_data = read_file_bytes(filepath, session_id=self.sandbox_id)
        
        b64_content = base64.b64encode(image_data).decode('utf-8')
        ext = Path(filepath).suffix.lower().lstrip('.')
        return b64_content, ext
    
    def _sync_outputs_to_openwebui(self) -> list[dict]:
        """
        Scan this sandbox's OUTPUTS_DIR (final state, after the model has
        finished any mid-turn reorganizing/renaming) and upload each file
        found to Open WebUI's own Files API, so it shows up as a
        downloadable attachment in chat. Best-effort: a failure syncing one
        file is logged and skipped, never fatal to the turn.

        Returns a list of {'filename', 'openwebui_file_id'} dicts for
        successfully synced files.
        """
        # TODO: re-enable once sync latency/timeouts (blocking `run()` for
        # up to 60s per file on a slow/unreachable Open WebUI, sometimes
        # stalling the whole turn) are addressed - see msb_sandbox.py sync
        # investigation. Disabled outright rather than gating on
        # OPENWEBUI_API_KEY so it's a one-line flip to restore.
        return []

        if not OPENWEBUI_API_KEY:
            return []

        synced = []
        for filepath in list_files(OUTPUTS_DIR, session_id=self.sandbox_id):
            try:
                data = read_file_bytes(filepath, session_id=self.sandbox_id)
                filename = Path(filepath).name
                response = requests.post(
                    f"{OPENWEBUI_BASE_URL}/api/v1/files/",
                    headers={"Authorization": f"Bearer {OPENWEBUI_API_KEY}"},
                    files={"file": (filename, data)},
                    params={"process": "false"},
                    timeout=60,
                )
                response.raise_for_status()
                file_id = response.json().get("id")
                if file_id:
                    synced.append({"filename": filepath, "openwebui_file_id": file_id})
                    print(f"✓ Synced {filepath} to Open WebUI (file_id={file_id})")
            except Exception as e:
                print(f"⚠️  Failed to sync {filepath} to Open WebUI: {e}")
                continue

        return synced

    @staticmethod
    def _invoke_with_heartbeat(
        fn: Callable[[], Any],
        stream_callback: Optional[Callable[[dict], None]],
        interval: float = 3.0,
    ) -> Any:
        """
        Run `fn()` (a zero-arg blocking call) on a background thread, and
        stream a harmless 'heartbeat' chunk every `interval` seconds while
        it's in flight.

        This exists because a single tool call (e.g. write_file_tool on a
        very large file) can legitimately block for a long time with no
        stream_callback activity in between - and that silence, not any one
        fixed numeric timeout, was what caused Open WebUI's frontend to
        report "reconnecting" and then hang (see nginx.conf / docker logs
        investigation - no single proxy/read timeout matched the observed
        <5-minute stall). Emitting *something* over the wire periodically
        defeats any idle-based disconnect detection anywhere in the chain
        (nginx, Open WebUI's own socket handling, the browser) regardless of
        exactly where it lives, without changing the substance of the
        response - idea_pipe.py's _translate_chunk turns 'heartbeat' chunks
        into an empty string.
        """
        result_box: Dict[str, Any] = {}
        error_box: Dict[str, Exception] = {}

        def _target():
            try:
                result_box['value'] = fn()
            except Exception as e:
                error_box['error'] = e

        thread = threading.Thread(target=_target, daemon=True)
        thread.start()
        while thread.is_alive():
            thread.join(timeout=interval)
            if thread.is_alive() and stream_callback:
                stream_callback({'role': 'computer', 'type': 'heartbeat'})

        if 'error' in error_box:
            raise error_box['error']
        return result_box.get('value')

    @staticmethod
    def _iter_with_heartbeat(
        iterable: Iterable,
        stream_callback: Optional[Callable[[dict], None]],
        interval: float = 3.0,
    ) -> Iterator:
        """
        Like _invoke_with_heartbeat, but for a blocking *iterable* (e.g.
        self.llm.stream(messages)) instead of a single blocking call.

        Drains `iterable` on a background thread into a queue; the calling
        thread yields each item as it arrives, or emits a heartbeat via
        stream_callback if `interval` seconds pass with no new item. This is
        needed in addition to _invoke_with_heartbeat because a streaming LLM
        response can go quiet for a long time between chunks that have
        actual text content (e.g. while it's emitting a large tool-call
        argument token by token with no visible .content per chunk - see the
        Iteration 3 stall this was added for, where 2690 chunks arrived with
        response_content length 0, so the existing per-chunk
        stream_callback(chunk.content) call in run() never fired even once).
        """
        _SENTINEL = object()
        q: "queue.Queue" = queue.Queue()
        error_box: Dict[str, Exception] = {}

        def _drain():
            try:
                for item in iterable:
                    q.put(item)
            except Exception as e:
                error_box['error'] = e
            finally:
                q.put(_SENTINEL)

        thread = threading.Thread(target=_drain, daemon=True)
        thread.start()
        while True:
            try:
                item = q.get(timeout=interval)
            except queue.Empty:
                if stream_callback:
                    stream_callback({'role': 'computer', 'type': 'heartbeat'})
                continue
            if item is _SENTINEL:
                break
            yield item

        if 'error' in error_box:
            raise error_box['error']

    def reset_terminal(self):
        """Gracefully stop (state-preserving) this user's sandbox/terminal."""
        close_terminal(self.sandbox_id)
        print(f"✓ Terminal session stopped ({self.sandbox_id})")
    
    def cleanup(self):
        """Clean up resources (close persistent terminal)."""
        self.reset_terminal()
    
    def run(self, prompt: str, stream_callback: Optional[Callable[[str], None]] = None) -> Dict[str, Any]:
        """
        Run the terminal agent with a natural language prompt.
        
        Args:
            prompt: Natural language task description
            stream_callback: Optional callback function for streaming responses
            
        Returns:
            Dictionary containing the result, messages, and metadata
        """
        
        self._shown_image_hashes.clear()
        
        # Load system prompt from the consolidated markdown file
        system_prompt = SYSTEM_PROMPT_PATH.read_text()

        user_prompt = prompt
        
        # Initialize conversation
        messages = [
            SystemMessage(content=system_prompt),
            HumanMessage(content=user_prompt)
        ]
        
        # Log initial messages
        print(f"\n{'='*80}")
        print("🚀 STARTING TERMINAL AGENT")
        print(f"{'='*80}")
        print(f"\n📋 SYSTEM PROMPT:")
        print(f"{'─'*80}")
        print(system_prompt)
        print(f"{'─'*80}")
        print(f"\n👤 USER PROMPT:")
        print(f"{'─'*80}")
        print(user_prompt)
        print(f"{'─'*80}")
        
        # Run conversation loop
        iterations = 0
        task_complete = False
        total_tokens = 0
        input_tokens = 0
        output_tokens = 0
        
        while iterations < self.max_iterations and not task_complete:
            iterations += 1
            print(f"\n{'='*60}")
            print(f"Iteration {iterations}")
            print(f"{'='*60}")
            
            # Get LLM response with streaming
            if stream_callback:
                response_content = ""
                aggregated_chunks = None
                chunk_count = 0
                for chunk in self._iter_with_heartbeat(self.llm.stream(messages), stream_callback):
                    chunk_count += 1
                    if hasattr(chunk, 'content') and chunk.content:
                        response_content += chunk.content
                        stream_callback(chunk.content)
                    # Accumulate chunks properly for tool calls
                    if aggregated_chunks is None:
                        aggregated_chunks = chunk
                    else:
                        aggregated_chunks = aggregated_chunks + chunk
                
                # Use the aggregated chunk which has properly accumulated tool calls
                tool_calls = []
                if aggregated_chunks and hasattr(aggregated_chunks, 'tool_calls'):
                    tool_calls = aggregated_chunks.tool_calls or []
                
                print(f"\n🔍 DEBUG: Received {chunk_count} chunks")
                print(f"🔍 DEBUG: response_content length: {len(response_content)}")
                print(f"🔍 DEBUG: tool_calls count: {len(tool_calls)}")
                
                if tool_calls:
                    print(f"🔍 DEBUG: tool_calls type: {type(tool_calls)}")
                    for i, tc in enumerate(tool_calls):
                        print(f"🔍 DEBUG: tool_call[{i}] type: {type(tc)}")
                        print(f"🔍 DEBUG: tool_call[{i}]: {tc}")
                        if isinstance(tc, dict):
                            print(f"  - name: {tc.get('name')}")
                            print(f"  - args: {tc.get('args')}")
                        elif hasattr(tc, 'name'):
                            print(f"  - name: {tc.name}")
                            print(f"  - args: {getattr(tc, 'args', 'NO ARGS ATTR')}")
                
                response = AIMessage(content=response_content)
                
                # Filter out invalid/empty tool calls
                valid_tool_calls = []
                if tool_calls:
                    for tc in tool_calls:
                        # Ensure tool call has required fields
                        if isinstance(tc, dict):
                            if tc.get('name') and tc.get('args') is not None:
                                valid_tool_calls.append(tc)
                                print(f"✅ Valid tool call (dict): {tc.get('name')}")
                            else:
                                print(f"❌ Invalid tool call (dict): name={tc.get('name')}, args={tc.get('args')}")
                        elif hasattr(tc, 'name') and hasattr(tc, 'args'):
                            if tc.name and tc.args is not None:
                                valid_tool_calls.append(tc)
                                print(f"✅ Valid tool call (obj): {tc.name}")
                            else:
                                print(f"❌ Invalid tool call (obj): name={getattr(tc, 'name', None)}, args={getattr(tc, 'args', None)}")
                
                if valid_tool_calls:
                    response.tool_calls = valid_tool_calls
                    
                # Preserve response metadata if available
                if aggregated_chunks and hasattr(aggregated_chunks, 'response_metadata'):
                    response.response_metadata = aggregated_chunks.response_metadata
            else:
                response = self.llm.invoke(messages)
            
            messages.append(response)
            
            # Track tokens
            if hasattr(response, 'response_metadata') and 'token_usage' in response.response_metadata:
                usage = response.response_metadata['token_usage']
                iter_input = usage.get('prompt_tokens', 0)
                iter_output = usage.get('completion_tokens', 0)
                iter_total = usage.get('total_tokens', 0)
                input_tokens += iter_input
                output_tokens += iter_output
                total_tokens += iter_total
                print(f"\n📊 Tokens this iteration: {iter_input} input + {iter_output} output = {iter_total} total")
                print(f"📊 Cumulative: {input_tokens} input + {output_tokens} output = {total_tokens} total")
            
            # Check if LLM wants to use tools
            if response.tool_calls:
                print(f"\n🔧 LLM wants to use {len(response.tool_calls)} tool(s)")
                for i, tool_call in enumerate(response.tool_calls, 1):
                    tool_name = tool_call['name']
                    print(f"\n→ Tool Call #{i}: {tool_name}")
                    
                    # Display tool arguments and stream to frontend
                    if tool_name == 'run_terminal_tool':
                        command = tool_call['args']['command']
                        print(f"\n📝 Command to execute:")
                        print(f"{'─'*60}")
                        print(command)
                        print(f"{'─'*60}")
                        
                        # Stream command to frontend
                        if stream_callback:
                            # Show the command being executed
                            stream_callback({
                                'role': 'computer',
                                'type': 'code',
                                'format': 'shell',
                                'content': command,
                                'start': True,
                                'end': True
                            })
                        
                        result = self.run_terminal_tool.invoke(tool_call['args'])
                        
                        # Stream command output to frontend
                        if stream_callback:
                            stream_callback({
                                'role': 'computer',
                                'type': 'console',
                                'format': 'output',
                                'content': result,
                                'start': True,
                                'end': True
                            })
                        
                    elif tool_name == 'write_file_tool':
                        filepath = tool_call['args']['filepath']
                        content = tool_call['args']['content']
                        append = tool_call['args'].get('append', False)
                        action = "Appending to" if append else "Writing to"
                        print(f"\n📝 {action}: {filepath}")
                        print(f"{'─'*60}")
                        # Show first 200 chars of content
                        preview = content[:200] + "..." if len(content) > 200 else content
                        print(preview)
                        print(f"{'─'*60}")
                        
                        # Stream file write to frontend
                        if stream_callback:
                            # Determine file extension for syntax highlighting
                            ext = Path(filepath).suffix.lstrip('.')
                            lang = ext if ext in ['python', 'py', 'js', 'html', 'css', 'json', 'yaml', 'sh'] else 'python'
                            if lang == 'py':
                                lang = 'python'
                            
                            # Show the file being written
                            stream_callback({
                                'role': 'computer',
                                'type': 'code',
                                'format': lang,
                                'content': content,
                                'start': True,
                                'end': True
                            })
                        
                        result = self._invoke_with_heartbeat(
                            lambda: self.write_file_tool.invoke(tool_call['args']),
                            stream_callback,
                        )
                        
                        # Stream result status
                        if stream_callback:
                            stream_callback({
                                'role': 'computer',
                                'type': 'console',
                                'format': 'output',
                                'content': result,
                                'start': True,
                                'end': True
                            })
                        
                    elif tool_name == 'show_image_tool':
                        image_path = tool_call['args']['filepath']
                        print(f"\n🖼️  LLM requested to show image: {image_path}")

                        result = self.show_image_tool.invoke(tool_call['args'])

                        if result.startswith("✓") and stream_callback:
                            try:
                                b64_content, img_format = self._encode_image_to_base64(image_path)
                                content_hash = hashlib.sha256(b64_content.encode('utf-8')).hexdigest()

                                if content_hash in self._shown_image_hashes:
                                    result = f"✓ Image already displayed to the user (identical content): {image_path}. Do not call show_image_tool again for this image."
                                    print(f"⏭️  Skipping duplicate image: {image_path}")
                                else:
                                    self._shown_image_hashes.add(content_hash)
                                    # Split into two chunks to avoid content duplication bug in frontend
                                    stream_callback({
                                        'role': 'assistant',
                                        'type': 'image',
                                        'format': f'base64.{img_format}',
                                        'content': '',
                                        'start': True
                                    })
                                    stream_callback({
                                        'role': 'assistant',
                                        'type': 'image',
                                        'format': f'base64.{img_format}',
                                        'content': b64_content,
                                        'end': True
                                    })
                                    print(f"✓ Image displayed: {image_path}")
                            except Exception as e:
                                result = f"✗ Failed to display image {image_path}: {e}"
                                print(result)
                        elif stream_callback:
                            stream_callback({
                                'role': 'computer',
                                'type': 'console',
                                'format': 'output',
                                'content': result,
                                'start': True,
                                'end': True
                            })

                    elif tool_name == 'run_python_tool':
                        code = tool_call['args']['code']
                        print(f"\n🐍 Python code to execute (persistent kernel):")
                        print(f"{'─'*60}")
                        print(code)
                        print(f"{'─'*60}")

                        if stream_callback:
                            stream_callback({
                                'role': 'computer',
                                'type': 'code',
                                'format': 'python',
                                'content': code,
                                'start': True,
                                'end': True
                            })

                        # Call tools.persistent_terminal.run_python() directly
                        # (not self.run_python_tool) to get the raw Open
                        # Interpreter chunk list, so console output and
                        # images can be streamed to the frontend as they're
                        # produced, instead of only the flattened text
                        # summary run_python_tool's own wrapper returns.
                        chunks = self._invoke_with_heartbeat(
                            lambda: run_python(code, session_id=self.sandbox_id),
                            stream_callback,
                        )

                        console_texts = []
                        image_count = 0
                        for chunk in chunks:
                            chunk_type = chunk.get('type')
                            if chunk_type == 'console' and chunk.get('format') != 'active_line':
                                content = chunk.get('content', '')
                                if content:
                                    console_texts.append(content)
                                    if stream_callback:
                                        stream_callback({
                                            'role': 'computer',
                                            'type': 'console',
                                            'format': 'output',
                                            'content': content,
                                            'start': True,
                                            'end': True
                                        })
                            elif chunk_type == 'image':
                                b64_content = chunk.get('content', '')
                                img_format = chunk.get('format', 'base64.png').split('.', 1)[-1]
                                content_hash = hashlib.sha256(b64_content.encode('utf-8')).hexdigest()
                                if content_hash in self._shown_image_hashes:
                                    continue
                                self._shown_image_hashes.add(content_hash)
                                image_count += 1
                                if stream_callback:
                                    stream_callback({
                                        'role': 'assistant',
                                        'type': 'image',
                                        'format': f'base64.{img_format}',
                                        'content': '',
                                        'start': True
                                    })
                                    stream_callback({
                                        'role': 'assistant',
                                        'type': 'image',
                                        'format': f'base64.{img_format}',
                                        'content': b64_content,
                                        'end': True
                                    })

                        result = "\n".join(console_texts).strip()
                        if image_count:
                            result = (result + f"\n[{image_count} image(s) generated and shown to the user]").strip()
                        result = result or "(no output)"

                    elif tool_name in self.tools_by_name:
                        # Generic dispatch for data tools (datetime, station, climate, web search, knowledge base)
                        print(f"\n📝 Args: {tool_call['args']}")

                        if stream_callback:
                            stream_callback({
                                'role': 'computer',
                                'type': 'console',
                                'format': 'output',
                                'content': f"Calling {tool_name}({tool_call['args']})",
                                'start': True,
                                'end': True
                            })

                        try:
                            result = self.tools_by_name[tool_name].invoke(tool_call['args'])
                        except Exception as e:
                            result = f"✗ {tool_name} failed: {e}"

                        if stream_callback:
                            stream_callback({
                                'role': 'computer',
                                'type': 'console',
                                'format': 'output',
                                'content': str(result),
                                'start': True,
                                'end': True
                            })

                    else:
                        # Unknown tool execution
                        result = f"Unknown tool: {tool_name}"
                    
                    print(f"\n✉️  Tool Result:")
                    print(f"{'─'*60}")
                    print(result)
                    print(f"{'─'*60}")
                    
                    # Add tool result to messages
                    # Ensure tool_call_id exists, generate one if missing
                    tool_call_id = tool_call.get('id')
                    if not tool_call_id:
                        tool_call_id = str(uuid.uuid4())
                        tool_call['id'] = tool_call_id
                    
                    messages.append(ToolMessage(
                        content=result,
                        tool_call_id=tool_call_id
                    ))
            else:
                # LLM responded without calling tools - task is complete
                print(f"\n💬 LLM Response (no tool calls):")
                print(f"{'─'*60}")
                print(response.content)  # Full content, no truncation
                print(f"{'─'*60}")
                
                # No tool calls means the agent is done
                # (Either finished the task or doesn't know how to proceed)
                task_complete = True
                print(f"\n✅ Agent stopped (no tool calls made)")
                break
        
        # Sync any deliverables the model placed under OUTPUTS_DIR to Open
        # WebUI's own Files storage, once per turn (not per write - see
        # system_prompt.md and _sync_outputs_to_openwebui docstring), and
        # let the user know they're available as downloads.
        synced_files = self._sync_outputs_to_openwebui()
        if stream_callback:
            for synced in synced_files:
                stream_callback({
                    'role': 'assistant',
                    'type': 'file',
                    'filename': synced['filename'],
                    'openwebui_file_id': synced['openwebui_file_id'],
                    'start': True,
                    'end': True
                })
        
        # Determine success based on completion
        success = task_complete or iterations < self.max_iterations
        
        # Calculate cost (GPT-4o pricing: $2.50/1M input, $10/1M output)
        input_cost = input_tokens / 1_000_000 * 2.50
        output_cost = output_tokens / 1_000_000 * 10.00
        total_cost = input_cost + output_cost
        
        # Log final summary (no truncation)
        print(f"\n{'='*80}")
        print("📊 FINAL SUMMARY")
        print(f"{'='*80}")
        print(f"✅ Success: {success}")
        print(f"✓ Task Complete: {task_complete}")
        print(f"🔄 Iterations: {iterations}")
        print(f"💰 Total Cost: ${total_cost:.6f}")
        print(f"📊 Total Tokens: {total_tokens} ({input_tokens} input + {output_tokens} output)")
        print(f"\n💬 Total Messages in Conversation: {len(messages)}")
        for i, msg in enumerate(messages, 1):
            msg_type = type(msg).__name__
            if hasattr(msg, 'content'):
                # Show FULL content, no truncation
                content = str(msg.content)
                print(f"  {i}. {msg_type}: {content}")
            else:
                print(f"  {i}. {msg_type}")
        print(f"{'='*80}\n")
        
        return {
            'success': success,
            'task_complete': task_complete,
            'iterations': iterations,
            'messages': messages,
            'token_summary': {
                'total_tokens': total_tokens,
                'input_tokens': input_tokens,
                'output_tokens': output_tokens,
                'api_calls': iterations,
                'cost_estimate': {
                    'input_cost': input_cost,
                    'output_cost': output_cost,
                    'total_cost': total_cost
                }
            },
            'final_response': messages[-1].content if messages else None
        }
    
    def reset(self):
        """Reset agent state between tasks"""
        self.reset_terminal()
