"""Build-time behavioral checks against the actual patched upstream middleware.

Execute its preprocessing blocks with mocked I/O, without importing the app or
connecting to its database. Set OPENWEBUI_MIDDLEWARE_PATH to test a local copy.
"""

import ast
import asyncio
import os
from pathlib import Path
from types import SimpleNamespace
import unittest
from unittest.mock import AsyncMock


MIDDLEWARE_PATH = Path(os.getenv(
    "OPENWEBUI_MIDDLEWARE_PATH", "/app/backend/open_webui/utils/middleware.py"
))


def preprocessing(source):
    tree = ast.parse(source)
    function = next(node for node in tree.body if isinstance(node, ast.AsyncFunctionDef) and node.name == "process_chat_payload")

    def assigns(node, name):
        return isinstance(node, ast.Assign) and any(isinstance(target, ast.Name) and target.id == name for target in node.targets)

    def condition(node, name):
        return isinstance(node, ast.If) and isinstance(node.test, ast.Name) and node.test.id == name

    start = next(i for i, node in enumerate(function.body) if assigns(node, "capabilities"))
    end = next(i for i, node in enumerate(function.body) if assigns(node, "variables"))
    body = function.body[start:end]
    server_resolution = next(node for node in function.body if isinstance(node, ast.If) and ast.unparse(node.test) == "payload_tools is None")
    start = next(i for i, node in enumerate(server_resolution.body) if assigns(node, "terminal_capability"))
    body += server_resolution.body[start:start + 3]
    body += [next(node for node in server_resolution.body if condition(node, "tools_dict"))]
    body += [next(node for node in function.body if condition(node, "file_context_enabled"))]
    body += [node for node in function.body if condition(node, "model_knowledge_search")][-1:]
    # Run the installed statements verbatim, with I/O supplied by each test.
    runner = ast.parse("async def run(form_data, tools_dict):\n    pass").body[0]
    runner.body = body + [ast.Return(value=ast.Call(func=ast.Name(id="locals", ctx=ast.Load()), args=[], keywords=[]))]
    return compile(ast.fix_missing_locations(ast.Module(body=[runner], type_ignores=[])), str(MIDDLEWARE_PATH), "exec")


@unittest.skipUnless(MIDDLEWARE_PATH.is_file(), "requires the Open WebUI backend source")
class PreprocessingTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.program = preprocessing(MIDDLEWARE_PATH.read_text(encoding="utf-8"))

    def run_request(self, capabilities=None, terminal_id="idea-sandbox", mode="legacy", tools=None, knowledge=None):
        knowledge = knowledge if knowledge is not None else [
            {"type": "collection", "id": "papers", "name": "Literature"},
            {"collection_name": "old-collection", "name": "Legacy"},
        ]
        env = {
            "model": {"info": {"meta": {"knowledge": knowledge, "capabilities": capabilities or {}}}},
            "metadata": {"params": {"function_calling": mode}, "terminal_id": terminal_id},
            "form_data": {"messages": [{"role": "user", "content": "Hello"}], "files": [{"id": "upload", "type": "file"}]},
            "terminal_id": terminal_id,
            "get_last_user_message": lambda messages: messages[-1]["content"],
            "event_emitter": AsyncMock(),
            "Config": SimpleNamespace(get=AsyncMock(return_value=[{"id": "idea-sandbox"}, {"id": "external-terminal"}])),
            "get_terminal_tools": AsyncMock(return_value=({"run_python": {"spec": {"name": "run_python"}}}, None)),
            "chat_completion_tools_handler": AsyncMock(),
            "chat_completion_files_handler": AsyncMock(),
            "tools_dict": tools or {},
            "request": object(), "user": object(), "extra_params": {}, "models": {},
            "sources": [], "log": SimpleNamespace(exception=lambda exc: self.fail(str(exc))),
        }
        tool_sources = [{"source": {"name": "user-tool"}, "tool_result": True}]
        env["chat_completion_tools_handler"].return_value = (env["form_data"], {"sources": tool_sources})
        env["chat_completion_files_handler"].return_value = (env["form_data"], {"sources": []})
        exec(self.program, env)
        result = asyncio.run(env["run"](env["form_data"], env["tools_dict"]))
        return env, result

    def test_raw_files_skip_search_status_retrieval_and_internal_terminal_tools(self):
        env, result = self.run_request({"raw_file_access": True, "file_context": False, "terminal": True})
        env["event_emitter"].assert_not_awaited()
        env["chat_completion_files_handler"].assert_not_awaited()
        env["get_terminal_tools"].assert_not_awaited()
        env["chat_completion_tools_handler"].assert_not_awaited()
        self.assertEqual(env["sources"], [])
        self.assertFalse(result["terminal_capability"])
        # Files explorer selection and every attachment/collection survive.
        self.assertEqual(env["metadata"]["terminal_id"], "idea-sandbox")
        self.assertEqual(result["form_data"]["files"], [
            {"id": "upload", "type": "file"},
            {"type": "collection", "id": "papers", "name": "Literature"},
            {"id": "old-collection", "name": "Legacy", "legacy": True},
        ])

    def test_raw_access_overrides_stale_file_context_true(self):
        env, _ = self.run_request({"raw_file_access": True, "file_context": True})
        env["event_emitter"].assert_not_awaited()
        env["chat_completion_files_handler"].assert_not_awaited()

    def test_disabled_file_context_also_skips_status(self):
        env, _ = self.run_request({"file_context": False})
        env["event_emitter"].assert_not_awaited()
        env["chat_completion_files_handler"].assert_not_awaited()

    def test_normal_legacy_knowledge_still_searches_with_status(self):
        env, _ = self.run_request()
        env["chat_completion_files_handler"].assert_awaited_once()
        statuses = [call.args[0]["data"] for call in env["event_emitter"].await_args_list]
        self.assertEqual([status["done"] for status in statuses], [False, True])
        self.assertTrue(all(status["action"] == "knowledge_search" for status in statuses))

    def test_native_knowledge_does_not_emit_legacy_status(self):
        env, result = self.run_request(mode="native")
        env["event_emitter"].assert_not_awaited()
        self.assertEqual(result["form_data"]["files"], [{"id": "upload", "type": "file"}])

    def test_no_knowledge_does_not_emit_status(self):
        env, _ = self.run_request(knowledge=[])
        env["event_emitter"].assert_not_awaited()

    def test_other_terminals_keep_legacy_tools(self):
        env, result = self.run_request(terminal_id="external-terminal")
        self.assertTrue(result["terminal_capability"])
        env["get_terminal_tools"].assert_awaited_once()
        env["chat_completion_tools_handler"].assert_awaited_once()

    def test_internal_terminal_is_excluded_even_without_raw_file_metadata(self):
        env, _ = self.run_request()
        env["get_terminal_tools"].assert_not_awaited()
        env["chat_completion_tools_handler"].assert_not_awaited()

    def test_user_selected_tools_and_their_sources_remain_available(self):
        env, _ = self.run_request({"raw_file_access": True}, tools={"user_tool": {}})
        env["get_terminal_tools"].assert_not_awaited()
        env["chat_completion_tools_handler"].assert_awaited_once()
        self.assertEqual(env["sources"][0]["source"]["name"], "user-tool")

    def test_terminal_capability_false_still_disables_other_terminals(self):
        env, _ = self.run_request({"terminal": False}, terminal_id="external-terminal")
        env["get_terminal_tools"].assert_not_awaited()


if __name__ == "__main__":
    # A missing source is an image build failure, not a skipped build check.
    if not MIDDLEWARE_PATH.is_file():
        raise SystemExit(f"Missing middleware: {MIDDLEWARE_PATH}")
    unittest.main()
