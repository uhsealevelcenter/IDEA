"""
Terminal Agent
A general-purpose AI agent with access to a persistent terminal session.
"""

import os
import re
import time
import uuid
import base64
import hashlib
import requests
import threading
import queue
from concurrent.futures import ThreadPoolExecutor, TimeoutError, as_completed
from typing import Dict, Any, Optional, Callable, Iterable, Iterator
from pathlib import Path
from urllib.parse import quote
from langchain_openai import ChatOpenAI
from langchain_core.messages import HumanMessage, AIMessage, SystemMessage, ToolMessage

import sys
sys.path.append(str(Path(__file__).parent.parent))

from tools.persistent_terminal import (
    make_agent_tools,
    close_terminal,
    read_file_bytes,
    file_exists,
    list_file_metadata,
    run_python,
    write_file_stream,
)
from utils.output_sync import (
    changed_output_paths,
    discover_html_output_references,
    is_html_output,
    referenced_output_paths,
)
from utils.artifact_registry import ArtifactRegistry
from utils.skill_loader import (
    BuiltinSkillLoader,
    OpenWebUISkillLoader,
    make_view_skill_tool,
    summarize_skill_result,
)
from utils.tools import DATA_TOOLS
from config import LITELLM_PROXY_URL, LITELLM_VIRTUAL_KEY, LITELLM_END_USER_HEADER
from progress import (
    progress_chunk,
    tool_call_chunk_names,
    tool_status_description,
)

SYSTEM_PROMPT_PATH = Path(__file__).parent.parent / "utils" / "system_prompt.md"

# Files placed under this directory in the sandbox (see system_prompt.md's
# "Data/Analysis Output & File Operations" section) are auto-synced to Open
# WebUI's own Files storage at the end of each turn - see
# TerminalAgent._sync_outputs_to_openwebui.
OUTPUTS_DIR = "/outputs"
OPENWEBUI_BASE_URL = os.getenv("OPENWEBUI_BASE_URL", "http://openwebui:8080").rstrip("/")
OUTPUT_SYNC_TIMEOUT_SECONDS = float(os.getenv("OUTPUT_SYNC_TIMEOUT_SECONDS", "30"))
OUTPUT_SYNC_MAX_WORKERS = max(1, int(os.getenv("OUTPUT_SYNC_MAX_WORKERS", "4")))
INPUTS_DIR = "/workspace/uploads"
INPUT_SYNC_TIMEOUT_SECONDS = float(os.getenv("INPUT_SYNC_TIMEOUT_SECONDS", "120"))
INPUT_SYNC_MAX_FILE_BYTES = int(
    os.getenv("INPUT_SYNC_MAX_FILE_BYTES", str(1024 * 1024 * 1024))
)
INPUT_SYNC_CHUNK_BYTES = 1024 * 1024


def _safe_input_component(value: str, fallback: str, max_length: int = 180) -> str:
    """Produce one non-traversing, shell-friendly sandbox path component."""
    basename = str(value or "").replace("\\", "/").rsplit("/", 1)[-1]
    sanitized = re.sub(r"[^A-Za-z0-9._-]+", "_", basename).strip("._")
    if not sanitized:
        sanitized = fallback
    return sanitized[:max_length]


def _input_sandbox_path(file_id: str, filename: str) -> str:
    safe_id = _safe_input_component(file_id, "", max_length=96)
    if not safe_id or safe_id != file_id:
        safe_id = hashlib.sha256(file_id.encode("utf-8")).hexdigest()[:32]
    safe_name = _safe_input_component(filename, "upload")
    return f"{INPUTS_DIR}/{safe_id}/{safe_name}"


def _attached_files_context(synced_files: list[dict]) -> str:
    if not synced_files:
        return ""
    lines = [
        "Files attached by the user are available at these exact private "
        "sandbox paths:"
    ]
    lines.extend(
        f"- `{item['sandbox_path']}`"
        for item in synced_files
    )
    lines.append(
        "Use these exact paths for code and analysis. Keep derived working "
        "files under /workspace and user-facing deliverables under /outputs."
    )
    return "\n".join(lines)


def compose_system_prompt(
    base_prompt: str,
    assistant_system_prompt: Optional[str] = None,
    builtin_skills_manifest: Optional[str] = None,
) -> str:
    """Append an Assistant specialization without replacing IDEA's base rules."""
    sections = [base_prompt.rstrip()]
    manifest = (builtin_skills_manifest or "").strip()
    if manifest:
        sections.append(
            "# Available built-in IDEA skills\n\n"
            f"{manifest}"
        )
    specialization = (assistant_system_prompt or "").strip()
    if specialization:
        sections.append(
            "# Selected Assistant specialization and Open WebUI context\n\n"
            "Apply the following role, domain, and skill instructions when "
            "they are compatible with the shared IDEA execution, security, "
            "sandbox, and artifact rules above. The shared rules take "
            "precedence if they conflict.\n\n"
            f"{specialization}"
        )
    return "\n\n".join(sections) + "\n"


class TerminalAgent:
    """
    General-purpose terminal agent that gives the LLM access to a persistent terminal session.
    The LLM can write code to files, run scripts, install packages, and solve tasks iteratively.
    """
    
    def __init__(
        self,
        session_id: str,
        user_id: Optional[str] = None,
        user_email: Optional[str] = None,
        model: str = "gpt-5.6-sol",
        temperature: Optional[float] = None,
        max_iterations: int = 20,
        assistant_id: Optional[str] = None,
        assistant_system_prompt: Optional[str] = None,
        attached_files: Optional[list[dict[str, Any]]] = None,
        openwebui_authorization: Optional[str] = None,
    ):
        self.session_id = session_id
        self.user_id = user_id
        # Used only for LiteLLM per-end-user spend tracking (see
        # LITELLM_END_USER_HEADER below) - the sandbox/session identity
        # above is still keyed off user_id, not this.
        self.user_email = user_email
        self.model = model
        self.temperature = temperature
        self.max_iterations = max_iterations
        self.assistant_id = assistant_id
        self.assistant_system_prompt = assistant_system_prompt
        self.attached_files = list(attached_files or [])
        self.openwebui_authorization = openwebui_authorization
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
        self.artifact_registry = ArtifactRegistry(self.sandbox_id)
        self.builtin_skill_loader = BuiltinSkillLoader()
        self.workspace_skill_loader = OpenWebUISkillLoader(
            OPENWEBUI_BASE_URL,
            self.openwebui_authorization,
        )
        self.view_skill_tool = make_view_skill_tool(
            self.builtin_skill_loader,
            self.workspace_skill_loader,
        )
        
        # Terminal/filesystem tools bound to this user's own sandbox (or
        # local shell, if sandboxing is unavailable) - never shared with
        # other users. See tools/persistent_terminal.make_agent_tools.
        (
            self.run_terminal_tool,
            self.write_file_tool,
            self.publish_artifact_tool,
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
            self.publish_artifact_tool,
            self.show_image_tool,
            self.read_output_range_tool,
            self.run_python_tool,
            self.view_skill_tool,
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
        # Reasoning models (e.g., gpt-5.6-sol) only support the provider default
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

    def _sync_inputs_from_openwebui(self) -> list[dict]:
        """
        Authorize and copy this turn's Open WebUI attachments into the
        user's private sandbox before the model sees the prompt.

        Input sync is intentionally fail-closed. Running without a requested
        attachment would invite the model to guess about data it cannot read.
        """
        attached_files = list(getattr(self, "attached_files", []) or [])
        if not attached_files:
            return []
        if not self.openwebui_authorization:
            raise RuntimeError(
                "Cannot prepare attached files because the current Open "
                "WebUI user credential was not forwarded."
            )

        sync_deadline = time.monotonic() + INPUT_SYNC_TIMEOUT_SECONDS
        headers = {"Authorization": self.openwebui_authorization}
        synced: list[dict] = []
        seen: set[str] = set()

        for descriptor in attached_files:
            file_id = descriptor.get("id") if isinstance(descriptor, dict) else None
            if not isinstance(file_id, str) or not file_id or file_id in seen:
                continue
            seen.add(file_id)

            remaining = sync_deadline - time.monotonic()
            if remaining <= 0:
                raise RuntimeError(
                    "Timed out while preparing attached files for the sandbox."
                )

            encoded_id = quote(file_id, safe="")
            try:
                metadata_response = requests.get(
                    f"{OPENWEBUI_BASE_URL}/api/v1/files/{encoded_id}",
                    headers=headers,
                    timeout=(min(5, remaining), remaining),
                )
                metadata_response.raise_for_status()
                metadata = metadata_response.json()
                if not isinstance(metadata, dict):
                    raise ValueError("Open WebUI returned invalid file metadata")
            except Exception as exc:
                raise RuntimeError(
                    f"Could not authorize attached file {file_id!r}: {exc}"
                ) from exc

            meta = metadata.get("meta") or {}
            if not isinstance(meta, dict):
                meta = {}
            filename = meta.get("name") or metadata.get("filename") or "upload"
            size = meta.get("size")
            if not isinstance(size, int) or size < 0:
                size = None
            if size is not None and size > INPUT_SYNC_MAX_FILE_BYTES:
                raise RuntimeError(
                    f"Attached file {filename!r} is {size} bytes; the sandbox "
                    f"input limit is {INPUT_SYNC_MAX_FILE_BYTES} bytes."
                )

            sandbox_path = _input_sandbox_path(file_id, str(filename))
            # Re-authorize above on every turn, even when this immutable file
            # ID has already been copied into the persistent sandbox.
            remaining = sync_deadline - time.monotonic()
            if remaining <= 0:
                raise RuntimeError(
                    "Timed out while preparing attached files for the sandbox."
                )
            if file_exists(
                sandbox_path,
                session_id=self.sandbox_id,
                timeout=remaining,
            ):
                synced.append({
                    "id": file_id,
                    "name": str(filename),
                    "size": size,
                    "sandbox_path": sandbox_path,
                })
                continue

            remaining = sync_deadline - time.monotonic()
            if remaining <= 0:
                raise RuntimeError(
                    "Timed out while preparing attached files for the sandbox."
                )

            try:
                content_response = requests.get(
                    f"{OPENWEBUI_BASE_URL}/api/v1/files/{encoded_id}/content",
                    headers=headers,
                    stream=True,
                    timeout=(min(5, remaining), remaining),
                )
                content_response.raise_for_status()
                transferred = 0

                def chunks() -> Iterator[bytes]:
                    nonlocal transferred
                    for chunk in content_response.iter_content(
                        chunk_size=INPUT_SYNC_CHUNK_BYTES
                    ):
                        if time.monotonic() >= sync_deadline:
                            raise RuntimeError(
                                "Timed out while copying attached files "
                                "into the sandbox."
                            )
                        if not chunk:
                            continue
                        transferred += len(chunk)
                        if transferred > INPUT_SYNC_MAX_FILE_BYTES:
                            raise RuntimeError(
                                f"Attached file {filename!r} exceeds the "
                                f"{INPUT_SYNC_MAX_FILE_BYTES}-byte sandbox "
                                "input limit."
                            )
                        yield chunk

                remaining = sync_deadline - time.monotonic()
                if remaining <= 0:
                    raise RuntimeError(
                        "Timed out while preparing attached files for the sandbox."
                    )
                written = write_file_stream(
                    sandbox_path,
                    chunks(),
                    session_id=self.sandbox_id,
                    expected_size=size,
                    timeout=remaining,
                )
                if written != transferred or (
                    size is not None and transferred != size
                ):
                    raise RuntimeError(
                        f"Attached file {filename!r} was not copied intact "
                        f"(expected {size}, received {transferred}, wrote {written})."
                    )
            except Exception as exc:
                raise RuntimeError(
                    f"Could not copy attached file {filename!r} into the "
                    f"sandbox: {exc}"
                ) from exc
            finally:
                if "content_response" in locals():
                    content_response.close()
                    del content_response

            synced.append({
                "id": file_id,
                "name": str(filename),
                "size": transferred,
                "sandbox_path": sandbox_path,
            })
            print(f"✓ Prepared Open WebUI attachment at {sandbox_path}")

        return synced
    
    def _sync_outputs_to_openwebui(
        self,
        outputs_before_turn: dict[str, str] | None,
        referenced_paths: set[str] | None = None,
    ) -> list[dict]:
        """
        Scan this sandbox's OUTPUTS_DIR (final state, after the model has
        finished any mid-turn reorganizing/renaming) and upload each file
        found to Open WebUI's own Files API, so it shows up as a
        downloadable attachment in chat. Best-effort: a failure syncing one
        file is logged and skipped, never fatal to the turn.

        Returns a list of {'filename', 'openwebui_file_id'} dicts for
        successfully synced files.
        """
        if not self.openwebui_authorization:
            print("⚠️  Skipping output sync: no Open WebUI user credential")
            return []

        if outputs_before_turn is None:
            print("⚠️  Skipping output sync: pre-turn output snapshot failed")
            return []

        outputs_after_turn = list_file_metadata(
            OUTPUTS_DIR,
            session_id=self.sandbox_id,
        )
        if outputs_after_turn is None:
            print("⚠️  Skipping output sync: post-turn output snapshot failed")
            return []

        changed_paths = set(changed_output_paths(
            outputs_before_turn,
            outputs_after_turn,
        ))
        registry = getattr(self, "artifact_registry", None)
        deleted_paths = set(outputs_before_turn) - set(outputs_after_turn)
        if registry and deleted_paths:
            try:
                registry.remove_many(deleted_paths)
            except Exception as e:
                print(f"⚠️  Artifact registry cleanup failed: {e}")

        referenced_paths = {
            path
            for path in (referenced_paths or set())
            if path in outputs_after_turn
        }

        reused: list[dict] = []
        unresolved_references = set(referenced_paths)
        if registry and referenced_paths:
            try:
                registry_records = registry.get_many(referenced_paths)
                for filepath, record in registry_records.items():
                    if (
                        filepath not in changed_paths
                        and record.signature == outputs_after_turn[filepath]
                    ):
                        reused.append({
                            "filename": filepath,
                            "openwebui_file_id": record.openwebui_file_id,
                        })
                        unresolved_references.discard(filepath)
            except Exception as e:
                print(f"⚠️  Artifact registry lookup failed: {e}")

        # Preserve the existing behavior of attaching every new/changed
        # output, and additionally recover a mapping for any unchanged file
        # the final response explicitly references.
        filepaths = sorted(changed_paths | unresolved_references)
        if not filepaths:
            return reused

        sync_deadline = time.monotonic() + OUTPUT_SYNC_TIMEOUT_SECONDS
        html_data: dict[str, bytes] = {}

        # Self-contained HTML is required by system_prompt.md because a
        # sandboxed preview cannot authenticate subresource requests. Check
        # for accidental local dependencies, but never mutate the page.
        for filepath in filepaths:
            if not is_html_output(filepath):
                continue
            try:
                remaining = sync_deadline - time.monotonic()
                if remaining <= 0:
                    break
                data = read_file_bytes(
                    filepath,
                    session_id=self.sandbox_id,
                    timeout=remaining,
                )
                html_data[filepath] = data
                local_references = discover_html_output_references(
                    data,
                    filepath,
                )
                if local_references:
                    print(
                        f"⚠️  {filepath} is not self-contained; sandboxed "
                        f"browser previews cannot load local output "
                        f"resource(s): {', '.join(sorted(local_references))}"
                    )
            except Exception as e:
                print(
                    f"⚠️  Could not validate self-contained HTML for "
                    f"{filepath}: {e}"
                )

        def upload(filepath: str, data: bytes | None = None) -> dict | None:
            try:
                remaining = sync_deadline - time.monotonic()
                if remaining <= 0:
                    return None
                if data is None:
                    data = read_file_bytes(
                        filepath,
                        session_id=self.sandbox_id,
                        timeout=remaining,
                    )
                remaining = sync_deadline - time.monotonic()
                if remaining <= 0:
                    return None
                filename = Path(filepath).name
                response = requests.post(
                    f"{OPENWEBUI_BASE_URL}/api/v1/files/",
                    headers={"Authorization": self.openwebui_authorization},
                    files={"file": (filename, data)},
                    params={"process": "false"},
                    timeout=(min(5, remaining), remaining),
                )
                response.raise_for_status()
                file_id = response.json().get("id")
                if file_id:
                    print(f"✓ Synced {filepath} to Open WebUI (file_id={file_id})")
                    return {
                        "filename": filepath,
                        "openwebui_file_id": file_id,
                    }
            except Exception as e:
                print(f"⚠️  Failed to sync {filepath} to Open WebUI: {e}")
            return None

        executor = ThreadPoolExecutor(
            max_workers=min(OUTPUT_SYNC_MAX_WORKERS, len(filepaths)),
            thread_name_prefix="idea-output-sync",
        )
        futures = [
            executor.submit(upload, filepath, html_data.get(filepath))
            for filepath in filepaths
        ]
        synced: list[dict] = []
        try:
            remaining = max(0, sync_deadline - time.monotonic())
            for future in as_completed(futures, timeout=remaining):
                result = future.result()
                if result:
                    synced.append(result)
        except TimeoutError:
            unfinished = sum(not future.done() for future in futures)
            print(
                "⚠️  Output sync reached its "
                f"{OUTPUT_SYNC_TIMEOUT_SECONDS:g}s batch timeout; "
                f"skipping {unfinished} unfinished file(s)"
            )
        finally:
            for future in futures:
                future.cancel()
            executor.shutdown(wait=False, cancel_futures=True)

        if registry:
            for item in synced:
                filepath = item["filename"]
                try:
                    registry.upsert(
                        filepath,
                        outputs_after_turn[filepath],
                        item["openwebui_file_id"],
                        Path(filepath).name,
                    )
                except Exception as e:
                    print(
                        f"⚠️  Artifact registry update failed for "
                        f"{filepath}: {e}"
                    )

        return sorted(reused + synced, key=lambda item: item["filename"])

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

        TODO: Base heartbeat timing on time since the last user-visible/SSE
        event, rather than only on queue.get() timeouts. A model can emit
        continuous tool-argument chunks with empty visible content, which
        keeps this queue busy and suppresses heartbeats. Native progress
        statuses now prevent the UI from appearing stalled, but a run that
        stays in this state beyond a proxy read timeout could still lose its
        transport connection.
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
        
        def emit_progress(
            phase: str,
            description: str,
            *,
            done: bool = False,
            tool_name: Optional[str] = None,
        ) -> None:
            if stream_callback:
                stream_callback(
                    progress_chunk(
                        phase,
                        description,
                        done=done,
                        tool_name=tool_name,
                    )
                )

        self._shown_image_hashes.clear()
        if getattr(self, "attached_files", None):
            emit_progress("syncing_inputs", "Preparing attached files…")
            synced_inputs = self._sync_inputs_from_openwebui()
            emit_progress(
                "syncing_inputs",
                "Attached files are ready",
            )
        else:
            synced_inputs = []
        outputs_before_turn = list_file_metadata(
            OUTPUTS_DIR,
            session_id=self.sandbox_id,
        )
        
        # Load system prompt from the consolidated markdown file
        system_prompt = compose_system_prompt(
            SYSTEM_PROMPT_PATH.read_text(),
            self.assistant_system_prompt,
            self.builtin_skill_loader.render_manifest(),
        )

        user_prompt = prompt
        attached_context = _attached_files_context(synced_inputs)
        if attached_context:
            user_prompt = f"{prompt.rstrip()}\n\n{attached_context}"
        
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
        if self.assistant_id:
            print(f"Selected Assistant: {self.assistant_id}")
        print(f"{'─'*80}")
        print(
            f"System prompt prepared ({len(system_prompt)} characters). "
            "Full content is not logged because it may contain private "
            "Open WebUI Workspace skill instructions."
        )
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
        sensitive_tool_call_ids: set[str] = set()

        while iterations < self.max_iterations and not task_complete:
            iterations += 1
            print(f"\n{'='*60}")
            print(f"Iteration {iterations}")
            print(f"{'='*60}")
            emit_progress("thinking", "Thinking…")
            
            # Get LLM response with streaming
            if stream_callback:
                response_content = ""
                aggregated_chunks = None
                chunk_count = 0
                announced_tool_names: set[str] = set()
                for chunk in self._iter_with_heartbeat(self.llm.stream(messages), stream_callback):
                    chunk_count += 1
                    if hasattr(chunk, 'content') and chunk.content:
                        response_content += chunk.content
                        stream_callback(chunk.content)
                    for tool_name in tool_call_chunk_names(chunk):
                        if tool_name in announced_tool_names:
                            continue
                        announced_tool_names.add(tool_name)
                        emit_progress(
                            "preparing_tool",
                            tool_status_description(
                                tool_name,
                                preparing=True,
                            ),
                            tool_name=tool_name,
                        )
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
                    emit_progress(
                        "running_tool",
                        tool_status_description(
                            tool_name,
                            preparing=False,
                        ),
                        tool_name=tool_name,
                    )
                    
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

                    elif tool_name == 'view_skill':
                        print(f"\n📝 Args: {tool_call['args']}")
                        try:
                            result = self.view_skill_tool.invoke(
                                tool_call['args']
                            )
                            displayed_result = summarize_skill_result(result)
                        except Exception as e:
                            result = f"✗ view_skill failed: {e}"
                            displayed_result = result

                        if stream_callback:
                            stream_callback({
                                'role': 'computer',
                                'type': 'console',
                                'format': 'output',
                                'content': displayed_result,
                                'start': True,
                                'end': True
                            })

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

                    displayed_result = (
                        summarize_skill_result(result)
                        if tool_name == 'view_skill'
                        and not str(result).startswith("✗")
                        else result
                    )
                    
                    print(f"\n✉️  Tool Result:")
                    print(f"{'─'*60}")
                    print(displayed_result)
                    print(f"{'─'*60}")
                    
                    # Add tool result to messages
                    # Ensure tool_call_id exists, generate one if missing
                    tool_call_id = tool_call.get('id')
                    if not tool_call_id:
                        tool_call_id = str(uuid.uuid4())
                        tool_call['id'] = tool_call_id
                    if tool_name == 'view_skill':
                        sensitive_tool_call_ids.add(tool_call_id)
                    
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
        emit_progress("syncing_outputs", "Finalizing outputs…")
        final_response = messages[-1].content if messages else ""
        synced_files = self._sync_outputs_to_openwebui(
            outputs_before_turn,
            referenced_paths=referenced_output_paths(str(final_response)),
        )
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
        emit_progress("completed", "Finished", done=True)
        
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
                if isinstance(msg, SystemMessage):
                    content = (
                        f"[system prompt omitted from logs; "
                        f"{len(str(msg.content))} characters]"
                    )
                elif (
                    isinstance(msg, ToolMessage)
                    and msg.tool_call_id in sensitive_tool_call_ids
                    and not str(msg.content).startswith("✗")
                ):
                    content = summarize_skill_result(str(msg.content))
                else:
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
