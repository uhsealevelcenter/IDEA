"""
Terminal Agent
A general-purpose AI agent with access to a persistent terminal session.
"""

import os
import time
import uuid
import base64
import hashlib
from typing import Dict, Any, Optional, Callable
from pathlib import Path
from langchain_openai import ChatOpenAI
from langchain_core.messages import HumanMessage, AIMessage, SystemMessage, ToolMessage

import sys
sys.path.append(str(Path(__file__).parent.parent))

from tools.persistent_terminal import run_terminal_tool, write_file_tool, show_image_tool, _persistent_terminals
from utils.tools import DATA_TOOLS

SYSTEM_PROMPT_PATH = Path(__file__).parent.parent / "utils" / "system_prompt.md"

ALL_TOOLS = [run_terminal_tool, write_file_tool, show_image_tool, *DATA_TOOLS]
TOOLS_BY_NAME = {t.name: t for t in ALL_TOOLS}


class TerminalAgent:
    """
    General-purpose terminal agent that gives the LLM access to a persistent terminal session.
    The LLM can write code to files, run scripts, install packages, and solve tasks iteratively.
    """
    
    def __init__(self, model: str = "gpt-4o-mini", temperature: Optional[float] = None, max_iterations: int = 20):
        self.model = model
        self.temperature = temperature
        self.max_iterations = max_iterations
        self._shown_image_hashes: set = set()  # Dedup identical images shown within a single run()
        
        # Initialize LLM with tools
        # Azure AI Foundry OpenAI-compatible endpoint: OPENAI_API_KEY and
        # OPENAI_BASE_URL (e.g. https://<resource>.services.ai.azure.com/openai/v1)
        # are read from the environment and passed explicitly to ChatOpenAI.
        # Reasoning models only support the provider default temperature -
        # omit the kwarg entirely when temperature is None.
        llm_kwargs: Dict[str, Any] = {
            "model": model,
            "streaming": True,
            "api_key": os.getenv("OPENAI_API_KEY"),
            "base_url": os.getenv("OPENAI_BASE_URL"),
        }
        if temperature is not None:
            llm_kwargs["temperature"] = temperature
        self.llm = ChatOpenAI(**llm_kwargs).bind_tools(ALL_TOOLS)
    
    @staticmethod
    def _encode_image_to_base64(filepath: str) -> tuple[str, str]:
        """
        Read an image file and encode it to base64.
        
        Returns:
            (base64_content, format) tuple, e.g., (base64_str, "png")
        """
        with open(filepath, 'rb') as f:
            image_data = f.read()
        
        b64_content = base64.b64encode(image_data).decode('utf-8')
        ext = Path(filepath).suffix.lower().lstrip('.')
        return b64_content, ext
    
    def reset_terminal(self):
        """Reset the persistent terminal session."""
        session_id = 'solver_session'
        if session_id in _persistent_terminals:
            _persistent_terminals[session_id].close()
            del _persistent_terminals[session_id]
            print("✓ Terminal session reset")
    
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
        
        # Reset terminal at the start of each task for clean state
        self.reset_terminal()
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
                for chunk in self.llm.stream(messages):
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
                        
                        result = run_terminal_tool.invoke(tool_call['args'])
                        
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
                        
                        result = write_file_tool.invoke(tool_call['args'])
                        
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

                        result = show_image_tool.invoke(tool_call['args'])

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

                    elif tool_name in TOOLS_BY_NAME:
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
                            result = TOOLS_BY_NAME[tool_name].invoke(tool_call['args'])
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
