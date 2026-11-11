import asyncio
import importlib.util
import sys
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, patch

from fastapi import HTTPException

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "langgraph"))

import langgraph_service  # noqa: E402
from integration_sessions import (  # noqa: E402
    IntegrationLimits,
    IntegrationSessionStore,
    IntegrationSettings,
)

try:
    import fakeredis
except ImportError:  # pragma: no cover - optional test dependency
    fakeredis = None

SCRIPT_PATH = ROOT / "openwebui" / "functions" / "idea_pipe.py"
SPEC = importlib.util.spec_from_file_location("idea_pipe_integration", SCRIPT_PATH)
idea_pipe = importlib.util.module_from_spec(SPEC)
assert SPEC.loader is not None
SPEC.loader.exec_module(idea_pipe)


def _request(**overrides):
    values = {
        "session_id": "chat-1",
        "user_id": "owner-1",
        "is_guest": False,
        "messages": [{"role": "user", "content": "plot sea level"}],
        "workspace_scope": "integration_session",
        "integration_id": "n8n-test",
        "integration_session_id": "visitor-session-1",
    }
    values.update(overrides)
    return langgraph_service.ChatRunRequest(**values)


@unittest.skipIf(fakeredis is None, "fakeredis[lua] is required")
class ServiceRoutingTests(unittest.TestCase):
    def setUp(self):
        settings = IntegrationSettings(
            enabled=True,
            integration_ids=frozenset({"n8n-test"}),
            limits=IntegrationLimits(daily_token_budget=10_000_000),
        )
        self.store = IntegrationSessionStore(
            fakeredis.FakeRedis(decode_responses=True), settings,
        )
        for patcher in (
            patch.object(langgraph_service, "IDEA_AGENT_RUNTIME", "langgraph"),
            patch.object(langgraph_service, "INTEGRATION_SETTINGS", settings),
            patch.object(langgraph_service, "integration_store", self.store),
            patch.object(langgraph_service, "_set_chat_run_status"),
            patch.object(langgraph_service.redis_client, "delete"),
        ):
            patcher.start()
            self.addCleanup(patcher.stop)
        thread_patch = patch.object(langgraph_service.threading, "Thread")
        self.thread = thread_patch.start()
        self.addCleanup(thread_patch.stop)

    def _start(self, request):
        return asyncio.run(langgraph_service.start_chat_run(request))

    def test_integration_run_is_routed_to_its_session_vm(self):
        self._start(_request())
        resolved = self.thread.call_args.kwargs["kwargs"]["resolved"]
        status = langgraph_service._set_chat_run_status.call_args.args[1]

        self.assertTrue(resolved.sandbox_id.startswith("sb_n8n_"))
        self.assertEqual(resolved.user_id, "owner-1")
        self.assertEqual(status["sandbox_id"], resolved.sandbox_id)
        self.assertEqual(status["user_id"], "owner-1")
        self.assertNotIn("visitor-session-1", str(status))
        self.assertFalse(self.thread.call_args.kwargs["kwargs"]["paperqa_enabled"])

    def test_browser_run_keeps_the_personal_vm(self):
        self._start(_request(
            workspace_scope="browser", integration_id=None, integration_session_id=None,
        ))
        resolved = self.thread.call_args.kwargs["kwargs"]["resolved"]
        self.assertEqual(resolved.sandbox_id, "owner-1")

    def test_invalid_context_fails_before_any_job_starts(self):
        cases = [
            _request(integration_session_id=None),
            _request(integration_session_id="a\nb"),
            _request(integration_id="unknown"),
            _request(workspace_scope="browser"),
            _request(user_id="anonymous"),
        ]
        for request in cases:
            with self.assertRaises(HTTPException):
                self._start(request)
        self.thread.assert_not_called()
        langgraph_service._set_chat_run_status.assert_not_called()

    def test_disabled_feature_or_legacy_runtime_rejects_integration(self):
        with patch.object(langgraph_service, "IDEA_AGENT_RUNTIME", "manual"):
            with self.assertRaises(HTTPException) as raised:
                self._start(_request())
        self.assertEqual(raised.exception.status_code, 400)
        with patch.object(
            langgraph_service, "INTEGRATION_SETTINGS", IntegrationSettings(enabled=False),
        ):
            with self.assertRaises(HTTPException) as raised:
                self._start(_request())
        self.assertEqual(raised.exception.status_code, 503)
        self.thread.assert_not_called()

    def test_concurrent_turn_in_same_session_is_rejected(self):
        self._start(_request())
        with self.assertRaises(HTTPException) as raised:
            self._start(_request())
        self.assertEqual(raised.exception.status_code, 409)
        self.assertIn("Retry-After", raised.exception.headers)

    def test_legacy_chat_endpoint_rejects_integration_requests(self):
        request = langgraph_service.ChatRequest(
            session_key="k", user_id="owner-1", is_guest=False, message="hi",
            workspace_scope="integration_session", integration_session_id="s",
        )
        with self.assertRaises(HTTPException) as raised:
            asyncio.run(langgraph_service.chat_endpoint(request))
        self.assertEqual(raised.exception.status_code, 400)


class StopRoutingTests(unittest.TestCase):
    def _stop(self, status):
        captured = {}

        async def offload(function, sandbox_id, run_id):
            captured["sandbox_id"] = sandbox_id
            return True

        async def exercise():
            with (
                patch.object(langgraph_service, "_get_chat_run_status", return_value=status),
                patch.object(langgraph_service.redis_client, "set"),
                patch.object(langgraph_service, "_update_chat_run_status"),
                patch.object(langgraph_service, "_append_chat_run_event"),
                patch.object(
                    langgraph_service.asyncio, "to_thread",
                    new_callable=AsyncMock, side_effect=offload,
                ),
            ):
                await langgraph_service.stop_chat_run("run-1")

        asyncio.run(exercise())
        return captured.get("sandbox_id")

    def test_stop_interrupts_the_stored_session_vm(self):
        self.assertEqual(
            self._stop({
                "run_id": "run-1", "user_id": "owner-1", "status": "running",
                "workspace_scope": "integration_session", "sandbox_id": "sb_n8n_abc",
            }),
            "sb_n8n_abc",
        )

    def test_legacy_browser_records_fall_back_to_the_owner_vm(self):
        self.assertEqual(
            self._stop({"run_id": "run-1", "user_id": "owner-1", "status": "running"}),
            "owner-1",
        )

    def test_integration_record_without_sandbox_never_targets_owner_vm(self):
        self.assertIsNone(self._stop({
            "run_id": "run-1", "user_id": "owner-1", "status": "running",
            "workspace_scope": "integration_session",
        }))


class CheckpointBindingTests(unittest.TestCase):
    def test_foreign_thread_hints_are_ignored(self):
        with patch.object(langgraph_service, "_load_message_checkpoint", return_value=None):
            thread, checkpoint, source = langgraph_service._resolve_graph_checkpoint(
                base_thread_id="th_mine",
                messages=[],
                response_message_id="m1",
                run_id="run-1",
                explicit_checkpoint_id="cp-other",
                explicit_thread_id="th_other_session",
            )
        self.assertTrue(thread.startswith("th_mine"))
        self.assertIsNone(checkpoint)
        self.assertEqual(source, "new_branch")

    def test_own_branch_hints_are_honored(self):
        thread, checkpoint, source = langgraph_service._resolve_graph_checkpoint(
            base_thread_id="th_mine",
            messages=[],
            response_message_id=None,
            run_id="run-1",
            explicit_checkpoint_id="cp-1",
            explicit_thread_id="th_mine:branch:abc",
        )
        self.assertEqual((thread, checkpoint, source), ("th_mine:branch:abc", "cp-1", "message_metadata"))


class RuntimeSandboxTests(unittest.TestCase):
    def test_agent_uses_explicit_sandbox_and_keeps_owner_identity(self):
        from agents import terminal_agent

        with (
            patch.object(terminal_agent, "LITELLM_VIRTUAL_KEY", "test-key"),
            patch.object(
                terminal_agent, "make_agent_tools",
                wraps=terminal_agent.make_agent_tools,
            ) as make_tools,
            patch.object(terminal_agent, "ArtifactRegistry") as registry,
        ):
            agent = terminal_agent.TerminalAgent(
                session_id="owner-1:ws", user_id="owner-1", sandbox_id="sb_n8n_abc",
            )
            with self.assertRaises(ValueError):
                terminal_agent.TerminalAgent(
                    session_id="s", user_id="owner-1", sandbox_id=" ",
                )
            browser = terminal_agent.TerminalAgent(session_id="s", user_id="owner-1")
        self.assertEqual(agent.sandbox_id, "sb_n8n_abc")
        self.assertEqual(agent.user_id, "owner-1")
        self.assertEqual(browser.sandbox_id, "owner-1")
        self.assertEqual(registry.call_args_list[0].args, ("sb_n8n_abc",))
        self.assertEqual(make_tools.call_args_list[0].args, ("sb_n8n_abc",))

    def test_runtime_forwards_resolved_sandbox_to_agent(self):
        from agents import terminal_agent
        from idea_graph.runtime import TerminalGraphRuntime

        with patch.object(terminal_agent, "TerminalAgent") as agent_class:
            TerminalGraphRuntime(
                user_id="owner-1", user_email=None, session_id="owner-1:ws",
                model="m", reasoning_effort=None, use_responses_api=True,
                assistant_id=None, assistant_system_prompt=None, attached_files=[],
                openwebui_authorization=None, is_guest=False, paperqa_enabled=False,
                sandbox_id="sb_n8n_abc",
            )
        self.assertEqual(agent_class.call_args.kwargs["sandbox_id"], "sb_n8n_abc")
        self.assertEqual(agent_class.call_args.kwargs["user_id"], "owner-1")


class PipeTransportTests(unittest.TestCase):
    @staticmethod
    def _client():
        client = Mock()
        client.__aenter__ = AsyncMock(return_value=client)
        client.__aexit__ = AsyncMock(return_value=None)
        started = Mock()
        started.raise_for_status.return_value = None
        started.json.return_value = {"run_id": "run-1", "status": "queued"}
        client.post = AsyncMock(return_value=started)
        finished = Mock()
        finished.raise_for_status.return_value = None
        finished.json.return_value = {
            "run_id": "run-1", "status": "completed", "events": [], "next_after": 0,
        }
        client.get = AsyncMock(return_value=finished)
        return client

    def _run(
        self, *, model, body_extra=None, headers=None, metadata=None,
        enabled=True, integration_accounts="",
    ):
        pipe = idea_pipe.Pipe()
        pipe.valves.INTEGRATION_MODEL_ENABLED = enabled
        pipe.valves.INTEGRATION_ID = "n8n-test"
        pipe.valves.INTEGRATION_ACCOUNT_IDS = integration_accounts
        client = self._client()
        body = {"model": model, "messages": [{"role": "user", "content": "hi"}]}
        body.update(body_extra or {})

        async def collect():
            return [
                chunk async for chunk in pipe.pipe(
                    body,
                    __user__={"id": "owner-1", "role": "user"},
                    __metadata__=metadata or {},
                    __request__=SimpleNamespace(headers=headers or {}, cookies={}),
                )
            ]

        with patch.object(idea_pipe.httpx, "AsyncClient", return_value=client):
            output = asyncio.run(collect())
        return output, client

    def _chat_payload(self, client):
        for call in client.post.call_args_list:
            if str(call.args[0]).endswith("/chat-runs"):
                return call.kwargs["json"]
        self.fail("chat run was not started")

    def test_registers_integration_model_only_when_enabled(self):
        pipe = idea_pipe.Pipe()
        self.assertNotIn(
            idea_pipe.INTEGRATION_MODEL_ID, [item["id"] for item in pipe.pipes()]
        )
        pipe.valves.INTEGRATION_MODEL_ENABLED = True
        self.assertIn(
            idea_pipe.INTEGRATION_MODEL_ID, [item["id"] for item in pipe.pipes()]
        )

    def test_header_transport_reaches_chat_run_payload(self):
        _, client = self._run(
            model="idea_terminal_agent.idea-terminal-agent-integration",
            headers={"x-idea-integration-session": "visitor-1"},
        )
        payload = self._chat_payload(client)
        self.assertEqual(payload["workspace_scope"], "integration_session")
        self.assertEqual(payload["integration_id"], "n8n-test")
        self.assertEqual(payload["integration_session_id"], "visitor-1")
        self.assertEqual(payload["user_id"], "owner-1")

    def test_body_and_metadata_transport_are_supported(self):
        for extra, metadata in (
            ({"idea_integration": {"integration_session_id": "v-2"}}, None),
            ({"metadata": {"integration_session_id": "v-2"}}, None),
            ({}, {"idea_integration": {"integration_session_id": "v-2"}}),
        ):
            _, client = self._run(
                model="idea-terminal-agent-integration", body_extra=extra, metadata=metadata,
            )
            self.assertEqual(
                self._chat_payload(client)["integration_session_id"], "v-2",
            )

    def test_missing_conflicting_or_misrouted_context_never_starts_a_run(self):
        cases = [
            {"model": "idea-terminal-agent-integration"},
            {
                "model": "idea-terminal-agent-integration",
                "headers": {"x-idea-integration-session": "a"},
                "body_extra": {"idea_integration": {"integration_session_id": "b"}},
            },
            {
                "model": "idea-terminal-agent-integration",
                "headers": {
                    "x-idea-integration-session": "a",
                    "x-idea-integration-id": "someone-else",
                },
            },
            {"model": "idea-terminal-agent", "headers": {"x-idea-integration-session": "a"}},
            {
                "model": "idea-terminal-agent-integration",
                "headers": {"x-idea-integration-session": "a"},
                "enabled": False,
            },
        ]
        for case in cases:
            output, client = self._run(**case)
            self.assertTrue(output and output[0].startswith("**Error:**"), case)
            client.post.assert_not_called()

    def test_integration_replies_omit_hidden_tool_chrome(self):
        hidden = list(idea_pipe.Pipe._translate_chunk(
            {"type": "console", "format": "error", "content": "Command failed\nsecret"},
            plain_tools=True,
        ))
        self.assertEqual(hidden, [])
        shown = "".join(idea_pipe.Pipe._translate_chunk(
            {"type": "console", "format": "error", "content": "Command failed"},
        ))
        self.assertIn("Tool error", shown)
        self.assertIn(idea_pipe.TOOL_OUTPUT_START, shown)

        code = "".join(idea_pipe.Pipe._translate_chunk(
            {"type": "code", "format": "python", "content": "print(1)"},
            plain_tools=True,
        ))
        self.assertIn("```python\nprint(1)\n```", code)
        self.assertNotIn(idea_pipe.RAW_TOOL_OUTPUT_START, code)
        self.assertNotIn(idea_pipe.RAW_TOOL_OUTPUT_END, code)

    def test_integration_file_links_use_the_public_export(self):
        rendered, referenced = idea_pipe._resolve_output_links(
            "[plot](/outputs/plot.png)",
            [{"filename": "/outputs/plot.png", "openwebui_file_id": "private-file"}],
            "https://idea.example",
            {"/outputs/plot.png": "https://idea.example/integration-exports/abc/plot.png"},
        )
        self.assertEqual(
            rendered, "[plot.png](https://idea.example/integration-exports/abc/plot.png)",
        )
        self.assertNotIn("private-file", rendered)
        self.assertEqual(referenced, {"/outputs/plot.png"})

    def test_integration_images_use_the_public_export(self):
        image_path = "/outputs/.idea/kernel-images/run-exec-1.png"
        pipe = idea_pipe.Pipe()
        pipe.valves.INTEGRATION_MODEL_ENABLED = True
        pipe.valves.INTEGRATION_ID = "n8n-test"
        client = self._client()
        client.get.return_value.json.return_value = {
            "run_id": "run-1", "status": "completed", "next_after": 2,
            "events": [
                {"seq": 1, "chunk": {
                    "type": "image", "format": "png", "filename": image_path,
                    "openwebui_file_id": "private-file",
                }},
                {"seq": 2, "chunk": {
                    "type": "file", "filename": image_path,
                    "openwebui_file_id": "private-file",
                }},
            ],
        }
        started = client.post.return_value
        status = Mock()
        status.raise_for_status.return_value = None
        status.json.return_value = {
            "exports": [{"path": image_path, "object_id": "obj-1"}],
        }
        client.post = AsyncMock(
            side_effect=lambda url, **_: (
                status if str(url).endswith("/integration-sessions/status") else started
            ),
        )

        async def collect():
            return [
                chunk async for chunk in pipe.pipe(
                    {
                        "model": "idea-terminal-agent-integration",
                        "messages": [{"role": "user", "content": "plot"}],
                    },
                    __user__={"id": "owner-1", "role": "user"},
                    __metadata__={},
                    __request__=SimpleNamespace(
                        headers={"x-idea-integration-session": "visitor-1"},
                        cookies={},
                    ),
                )
            ]

        with patch.object(idea_pipe.httpx, "AsyncClient", return_value=client):
            output = "".join(asyncio.run(collect()))
        self.assertIn(
            "![generated image](/integration-exports/obj-1/run-exec-1.png)", output,
        )
        self.assertNotIn("private-file", output)
        self.assertEqual(output.count("run-exec-1.png"), 1)

    def test_integration_accounts_always_need_session_context(self):
        for model in ("idea-terminal-agent", "idea_terminal_agent.idea-terminal-agent-advanced"):
            output, client = self._run(model=model, integration_accounts="other, owner-1")
            self.assertEqual(
                output, ["**Error:** this model requires an integration session ID."],
            )
            client.post.assert_not_called()

    def test_integration_account_specialists_share_the_session(self):
        _, client = self._run(
            model="idea_terminal_agent.idea-terminal-agent",
            headers={"x-idea-integration-session": "visitor-1"},
            metadata={"model": {"id": "sea"}},
            integration_accounts="owner-1",
        )
        payload = self._chat_payload(client)
        self.assertEqual(payload["workspace_scope"], "integration_session")
        self.assertEqual(payload["integration_session_id"], "visitor-1")
        self.assertEqual(payload["assistant_id"], "sea")

    def test_end_action_closes_the_session_without_running_the_agent(self):
        output, client = self._run(
            model="idea-terminal-agent-integration",
            headers={
                "x-idea-integration-session": "visitor-1",
                "x-idea-integration-action": "end",
            },
        )
        self.assertEqual(output, ["Conversation ended."])
        self.assertTrue(client.post.call_args.args[0].endswith("/integration-sessions/end"))
        self.assertEqual(
            client.post.call_args.kwargs["json"],
            {
                "user_id": "owner-1",
                "integration_id": "n8n-test",
                "integration_session_id": "visitor-1",
            },
        )


if __name__ == "__main__":
    unittest.main()
