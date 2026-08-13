import sys
import io
import asyncio
import signal
import threading
import unittest
from pathlib import Path
from types import ModuleType, SimpleNamespace
from unittest.mock import Mock, call, patch


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "langgraph"))
sys.path.insert(0, str(ROOT / "sandbox_service"))

# The production image supplies langchain_core. These tests exercise the
# underlying functions directly, so keep them runnable in the repository's
# dependency-light host test environment with a minimal decorator stub.
try:
    import langchain_core.tools  # noqa: F401
except ModuleNotFoundError:
    langchain_core = ModuleType("langchain_core")
    langchain_tools = ModuleType("langchain_core.tools")
    langchain_tools.tool = lambda function: function
    langchain_core.tools = langchain_tools
    sys.modules["langchain_core"] = langchain_core
    sys.modules["langchain_core.tools"] = langchain_tools

from tools import persistent_terminal  # noqa: E402
from msb_sandbox import MicrosandboxTerminal  # noqa: E402


class TerminalOutputArchivingTests(unittest.TestCase):
    @staticmethod
    def _response(output):
        response = Mock()
        response.raise_for_status.return_value = None
        response.json.return_value = {
            "success": True,
            "output": output,
            "elapsed_time": 0,
        }
        return response

    def test_complete_inline_output_is_not_archived(self):
        output = "HTML parsed successfully\nLocal dependencies: []"

        with (
            patch.object(
                persistent_terminal._client,
                "post",
                return_value=self._response(output),
            ),
            patch.object(persistent_terminal, "write_file") as write_file,
        ):
            result = persistent_terminal.run_terminal(
                "python build_report.py", session_id="user-1"
            )

        self.assertIn(output, result)
        self.assertNotIn("Full output saved to:", result)
        self.assertNotIn("Failed to save full output", result)
        write_file.assert_not_called()

    def test_truncated_output_is_archived(self):
        output = "\n".join(f"line {index}" for index in range(25))

        with (
            patch.object(
                persistent_terminal._client,
                "post",
                return_value=self._response(output),
            ),
            patch.object(
                persistent_terminal,
                "write_file",
                return_value="✓ Wrote output",
            ) as write_file,
        ):
            result = persistent_terminal.run_terminal(
                "python noisy_script.py", session_id="user-1"
            )

        self.assertIn("Output truncated", result)
        self.assertIn("Full output saved to:", result)
        write_file.assert_called_once()
        args, kwargs = write_file.call_args
        self.assertEqual(args[1], output)
        self.assertEqual(kwargs, {"session_id": "user-1"})


class MicrosandboxWriteFileTests(unittest.TestCase):
    def make_terminal(self):
        sink = SimpleNamespace(write=Mock(), close=Mock())
        terminal = MicrosandboxTerminal.__new__(MicrosandboxTerminal)
        terminal._cwd = "/workspace"
        terminal._sandbox = SimpleNamespace(
            shell=Mock(return_value=SimpleNamespace(stdout_text="")),
            fs=SimpleNamespace(
                write=Mock(),
                write_stream=Mock(return_value=sink),
                rename=Mock(),
                remove=Mock(),
            ),
        )
        terminal._exec = Mock(side_effect=lambda operation: operation())
        return terminal, sink

    def test_write_creates_missing_parent_directory(self):
        terminal, _ = self.make_terminal()

        terminal.write_file("/outputs/report/index.html", "hello")

        self.assertEqual(
            terminal._sandbox.shell.call_args_list,
            [call("mkdir -p -- /outputs/report")],
        )
        terminal._sandbox.fs.write.assert_called_once_with(
            "/outputs/report/index.html", b"hello"
        )

    def test_relative_write_creates_resolved_parent_directory(self):
        terminal, _ = self.make_terminal()

        terminal.write_file("reports/daily.txt", "hello")

        self.assertEqual(
            terminal._sandbox.shell.call_args_list,
            [call("mkdir -p -- /workspace/reports")],
        )
        terminal._sandbox.fs.write.assert_called_once_with(
            "/workspace/reports/daily.txt", b"hello"
        )

    def test_streams_binary_bytes_then_atomically_renames(self):
        terminal, sink = self.make_terminal()
        data = b"CDF\x01\x00binary\xff"

        terminal.write_file_bytes(
            "/workspace/uploads/file-1/data.nc",
            io.BytesIO(data),
        )

        terminal._sandbox.fs.write_stream.assert_called_once()
        temporary_path = (
            terminal._sandbox.fs.write_stream.call_args.args[0]
        )
        self.assertTrue(
            temporary_path.startswith(
                "/workspace/uploads/file-1/data.nc.idea-upload-"
            )
        )
        sink.write.assert_called_once_with(data)
        sink.close.assert_called_once_with()
        terminal._sandbox.fs.rename.assert_called_once_with(
            temporary_path,
            "/workspace/uploads/file-1/data.nc",
        )


class MicrosandboxOpenTerminalTests(unittest.TestCase):
    def make_terminal(self, exit_code=0, stderr_text=""):
        terminal = MicrosandboxTerminal.__new__(MicrosandboxTerminal)
        terminal.session_id = "sandbox-1"
        terminal._sandbox = SimpleNamespace(
            shell=Mock(
                return_value=SimpleNamespace(
                    exit_code=exit_code,
                    stderr_text=stderr_text,
                )
            ),
        )
        terminal._exec = Mock(side_effect=lambda operation, timeout=None: operation())
        return terminal

    def test_open_terminal_is_started_and_health_checked_lazily(self):
        terminal = self.make_terminal()

        terminal._ensure_open_terminal()

        command = terminal._sandbox.shell.call_args.args[0]
        self.assertIn("/app/entrypoint-slim.sh run", command)
        self.assertIn("OPEN_TERMINAL_API_KEY", command)
        self.assertIn("http://127.0.0.1:8000/health", command)
        self.assertEqual(terminal._exec.call_args.kwargs["timeout"], 20.0)

    def test_open_terminal_start_failure_is_explicit(self):
        terminal = self.make_terminal(exit_code=1, stderr_text="startup failed")

        with self.assertRaisesRegex(RuntimeError, "startup failed"):
            terminal._ensure_open_terminal()


class MicrosandboxPythonStreamingTests(unittest.TestCase):
    def test_ndjson_stdout_is_yielded_one_chunk_at_a_time(self):
        class Handle:
            def __aiter__(self):
                self.events = iter([
                    SimpleNamespace(event_type="stdout", data=b'{"event":"chunk","chunk":{"type":"console","format":"output","content":"first\\n"}}\n'),
                    SimpleNamespace(event_type="stdout", data=b'{"event":"chunk","chunk":{"type":"console","format":"output","content":"second\\n"}}\n'),
                    SimpleNamespace(event_type="exited", code=0),
                ])
                return self

            async def __anext__(self):
                try:
                    return next(self.events)
                except StopIteration:
                    raise StopAsyncIteration

        async def shell_stream(*args, **kwargs):
            return Handle()

        terminal = MicrosandboxTerminal.__new__(MicrosandboxTerminal)
        terminal._sandbox = SimpleNamespace(shell_stream=shell_stream)
        terminal._loop = asyncio.new_event_loop()
        terminal._thread = threading.Thread(
            target=terminal._loop.run_forever, daemon=True
        )
        terminal._thread.start()
        terminal._exec = Mock(return_value=None)
        terminal.interrupt_python = Mock(return_value=True)
        try:
            chunks = list(terminal.run_python_stream(
                "print('first')", kernel_id="kernel-1", run_id="run-1"
            ))
        finally:
            terminal._loop.call_soon_threadsafe(terminal._loop.stop)
            terminal._thread.join(timeout=2)
            terminal._loop.close()

        self.assertEqual(
            [chunk["content"] for chunk in chunks],
            ["first\n", "second\n"],
        )
        terminal.interrupt_python.assert_not_called()

    def test_cancellation_signals_existing_stream_process(self):
        class Handle:
            def __init__(self):
                self.signal_calls = []
                self.finished = asyncio.Event()
                self.exited = False

            def __aiter__(self):
                return self

            async def __anext__(self):
                if self.exited:
                    raise StopAsyncIteration
                await self.finished.wait()
                self.exited = True
                return SimpleNamespace(event_type="exited", code=0)

            async def signal(self, sig):
                self.signal_calls.append(sig)
                self.finished.set()

        handle = Handle()

        async def shell_stream(*args, **kwargs):
            return handle

        terminal = MicrosandboxTerminal.__new__(MicrosandboxTerminal)
        terminal._sandbox = SimpleNamespace(shell_stream=shell_stream)
        terminal._loop = asyncio.new_event_loop()
        terminal._thread = threading.Thread(
            target=terminal._loop.run_forever, daemon=True
        )
        terminal._thread.start()
        terminal._exec = Mock(return_value=None)
        terminal.interrupt_python = Mock(return_value=True)
        try:
            chunks = list(terminal.run_python_stream(
                "while True: pass",
                kernel_id="kernel-1",
                run_id="run-1",
                cancelled=lambda: True,
            ))
        finally:
            terminal._loop.call_soon_threadsafe(terminal._loop.stop)
            terminal._thread.join(timeout=2)
            terminal._loop.close()

        self.assertEqual(chunks, [])
        self.assertEqual(handle.signal_calls, [signal.SIGINT])
        terminal.interrupt_python.assert_not_called()

class BinarySandboxClientTests(unittest.TestCase):
    def test_python_execution_routes_kernel_and_run_ids(self):
        response = Mock()
        response.raise_for_status.return_value = None
        response.json.return_value = {"chunks": [{"type": "console", "content": "ok"}]}

        with patch.object(persistent_terminal._client, "post", return_value=response) as post:
            chunks = persistent_terminal.run_python(
                "value = 1",
                session_id="user-1",
                kernel_id="kernel-1",
                run_id="run-1",
            )

        self.assertEqual(chunks[0]["content"], "ok")
        post.assert_called_once_with(
            "/sandboxes/user-1/run-python",
            json={
                "code": "value = 1",
                "kernel_id": "kernel-1",
                "run_id": "run-1",
            },
        )

    def test_python_chunks_are_consumed_from_ndjson_stream(self):
        response = Mock()
        response.raise_for_status.return_value = None
        response.iter_lines.return_value = iter([
            '{"type":"console","format":"output","content":"first\\n"}',
            '{"type":"console","format":"output","content":"second\\n"}',
        ])
        context = Mock()
        context.__enter__ = Mock(return_value=response)
        context.__exit__ = Mock(return_value=None)

        with patch.object(
            persistent_terminal._client, "stream", return_value=context
        ) as stream:
            chunks = list(persistent_terminal.run_python_stream(
                "print('first')",
                session_id="user-1",
                kernel_id="kernel-1",
                run_id="run-1",
            ))

        self.assertEqual(
            [chunk["content"] for chunk in chunks],
            ["first\n", "second\n"],
        )
        stream.assert_called_once_with(
            "POST",
            "/sandboxes/user-1/run-python/stream",
            json={
                "code": "print('first')",
                "kernel_id": "kernel-1",
                "run_id": "run-1",
            },
        )

    def test_legacy_python_traceback_is_normalized_as_an_error_chunk(self):
        response = Mock()
        response.raise_for_status.return_value = None
        response.json.return_value = {
            "chunks": [{
                "type": "console",
                "format": "output",
                "content": (
                    "---------------------------------------------------------------------------\n"
                    "NameError Traceback (most recent call last):\n"
                    "Cell In[1], line 1\n----> 1 missing\n"
                    "NameError: name 'missing' is not defined"
                ),
            }],
        }

        with patch.object(persistent_terminal._client, "post", return_value=response):
            chunks = persistent_terminal.run_python(
                "missing",
                session_id="user-1",
                kernel_id="kernel-1",
                run_id="run-1",
            )

        self.assertEqual(chunks[0]["format"], "error")

    def test_normal_console_output_is_not_reclassified(self):
        chunks = persistent_terminal._normalize_kernel_chunks([{
            "type": "console",
            "format": "output",
            "content": "NameError is a Python exception class",
        }])

        self.assertEqual(chunks[0]["format"], "output")

    def test_run_interrupt_is_sent_to_the_sandbox_service(self):
        response = Mock()
        response.raise_for_status.return_value = None
        response.json.return_value = {"interrupted": True}

        with patch.object(persistent_terminal._client, "post", return_value=response) as post:
            interrupted = persistent_terminal.interrupt_run("user-1", "run-1")

        self.assertTrue(interrupted)
        post.assert_called_once_with(
            "/sandboxes/user-1/runs/run-1/interrupt",
            timeout=35.0,
        )

    def test_streams_binary_request_with_expected_size(self):
        response = Mock()
        response.raise_for_status.return_value = None
        response.json.return_value = {"size": 5}
        chunks = [b"\x00\x01", b"\xfe\xffX"]

        with patch.object(
            persistent_terminal._client,
            "put",
            return_value=response,
        ) as put:
            written = persistent_terminal.write_file_stream(
                "/workspace/uploads/file-1/data.bin",
                chunks,
                session_id="user-1",
                expected_size=5,
                timeout=30,
            )

        self.assertEqual(written, 5)
        self.assertEqual(
            put.call_args.args[0],
            "/sandboxes/user-1/files/content",
        )
        self.assertEqual(
            put.call_args.kwargs["params"],
            {
                "filepath": "/workspace/uploads/file-1/data.bin",
                "expected_size": 5,
            },
        )
        self.assertIs(put.call_args.kwargs["content"], chunks)
if __name__ == "__main__":
    unittest.main()
