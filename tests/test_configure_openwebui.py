import importlib.util
import hmac
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch


SCRIPT_PATH = (
    Path(__file__).resolve().parents[1]
    / "deployment"
    / "post_deploy"
    / "configure_openwebui.py"
)
SPEC = importlib.util.spec_from_file_location("configure_openwebui", SCRIPT_PATH)
configure_openwebui = importlib.util.module_from_spec(SPEC)
assert SPEC.loader is not None
SPEC.loader.exec_module(configure_openwebui)


class FakeClient:
    def __init__(self, responses=None):
        self.responses = responses or {}
        self.posts = []

    def get(self, path):
        response = self.responses[path]
        return response() if callable(response) else response

    def post(self, path, payload):
        self.posts.append((path, payload))
        return payload


class MissingModelClient(FakeClient):
    def get(self, path):
        if path.startswith("/api/v1/models/model?"):
            raise configure_openwebui.ApiError("GET", path, 404, "Not found")
        return super().get(path)


class ConfigureOpenWebUITests(unittest.TestCase):
    def test_default_task_model_is_gpt_6_luna(self):
        self.assertEqual(configure_openwebui.DEFAULT_TASK_MODEL, "gpt-6-luna")

    def test_task_model_upgrades_legacy_default_inherited_from_root_env(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / ".env"
            path.write_text("TASK_MODEL_EXTERNAL=gpt-6-luna\n")
            with patch.dict(
                os.environ,
                {"TASK_MODEL_EXTERNAL": "gpt-5.6-luna"},
                clear=False,
            ):
                configure_openwebui.load_env_file(path)
                self.assertEqual(os.environ["TASK_MODEL_EXTERNAL"], "gpt-5.6-luna")
                self.assertEqual(configure_openwebui.resolve_task_model(None), "gpt-6-luna")

    def test_task_model_preserves_newer_configured_and_explicit_models(self):
        with patch.dict(os.environ, {"TASK_MODEL_EXTERNAL": "gpt-6.1-sol"}):
            self.assertEqual(configure_openwebui.resolve_task_model(None), "gpt-6.1-sol")
            self.assertEqual(configure_openwebui.resolve_task_model("gpt-6-astra"), "gpt-6-astra")

    def test_connection_update_preserves_existing_connections(self):
        client = FakeClient(
            {
                "/openai/config": {
                    "ENABLE_OPENAI_API": True,
                    "OPENAI_API_BASE_URLS": ["https://existing.example/v1"],
                    "OPENAI_API_KEYS": ["existing-secret"],
                    "OPENAI_API_CONFIGS": {"0": {"enable": True}},
                }
            }
        )

        configure_openwebui.configure_litellm_connection(
            client,
            "http://litellm:8080/v1",
            "litellm-secret",
            "gpt-5.6-luna",
        )

        path, payload = client.posts[0]
        self.assertEqual(path, "/openai/config/update")
        self.assertEqual(
            payload["OPENAI_API_BASE_URLS"],
            ["https://existing.example/v1", "http://litellm:8080/v1"],
        )
        self.assertEqual(
            payload["OPENAI_API_KEYS"],
            ["existing-secret", "litellm-secret"],
        )
        self.assertEqual(
            payload["OPENAI_API_CONFIGS"]["1"]["model_ids"],
            ["gpt-5.6-luna"],
        )

    def test_connection_update_is_idempotent(self):
        client = FakeClient(
            {
                "/openai/config": {
                    "ENABLE_OPENAI_API": True,
                    "OPENAI_API_BASE_URLS": ["http://litellm:8080/v1"],
                    "OPENAI_API_KEYS": ["old-secret"],
                    "OPENAI_API_CONFIGS": {
                        "0": {"enable": False, "prefix_id": "kept"}
                    },
                }
            }
        )

        configure_openwebui.configure_litellm_connection(
            client,
            "http://litellm:8080/v1/",
            "new-secret",
            "gpt-5.6-luna",
        )

        payload = client.posts[0][1]
        self.assertEqual(payload["OPENAI_API_BASE_URLS"], ["http://litellm:8080/v1"])
        self.assertEqual(payload["OPENAI_API_KEYS"], ["new-secret"])
        self.assertEqual(
            payload["OPENAI_API_CONFIGS"]["0"],
            {
                "enable": True,
                "prefix_id": "kept",
                "model_ids": ["gpt-5.6-luna"],
            },
        )

    def test_connection_update_migrates_incorrect_internal_port(self):
        client = FakeClient(
            {
                "/openai/config": {
                    "ENABLE_OPENAI_API": True,
                    "OPENAI_API_BASE_URLS": ["http://litellm:4000/v1"],
                    "OPENAI_API_KEYS": ["old-secret"],
                    "OPENAI_API_CONFIGS": {"0": {"enable": True}},
                }
            }
        )

        configure_openwebui.configure_litellm_connection(
            client,
            "http://litellm:8080/v1",
            "new-secret",
            "gpt-5.6-luna",
        )

        payload = client.posts[0][1]
        self.assertEqual(
            payload["OPENAI_API_BASE_URLS"],
            ["http://litellm:8080/v1"],
        )
        self.assertEqual(payload["OPENAI_API_KEYS"], ["new-secret"])

    def test_public_read_grant_is_added_once(self):
        existing = [
            {
                "id": "ignored",
                "resource_type": "model",
                "resource_id": "gpt-5.6-luna",
                "principal_type": "group",
                "principal_id": "researchers",
                "permission": "read",
            }
        ]

        grants = configure_openwebui.public_read_grants(existing)
        grants = configure_openwebui.public_read_grants(grants)

        self.assertEqual(
            grants.count(configure_openwebui.PUBLIC_READ_GRANT),
            1,
        )
        self.assertEqual(len(grants), 2)

    def test_task_update_sets_model_and_title_prompt_preserving_other_settings(self):
        current = {
            "TASK_MODEL": "",
            "TASK_MODEL_EXTERNAL": "",
            "ENABLE_TITLE_GENERATION": False,
            "TITLE_GENERATION_PROMPT_TEMPLATE": "",
        }
        client = FakeClient(
            {
                "/api/v1/tasks/config": current,
            }
        )

        configure_openwebui.configure_task_settings(
            client,
            "gpt-5.6-luna",
            configure_openwebui.TITLE_GENERATION_PROMPT,
        )

        path, payload = client.posts[0]
        self.assertEqual(path, "/api/v1/tasks/config/update")
        self.assertEqual(payload["TASK_MODEL_EXTERNAL"], "gpt-5.6-luna")
        self.assertEqual(
            payload["TITLE_GENERATION_PROMPT_TEMPLATE"],
            configure_openwebui.TITLE_GENERATION_PROMPT,
        )
        self.assertFalse(payload["ENABLE_TITLE_GENERATION"])

    def test_context_compaction_uses_legacy_threshold_and_preserves_prompt(self):
        current = {
            "ENABLE_CONTEXT_COMPACTION": False,
            "CONTEXT_COMPACTION_TOKEN_THRESHOLD": 80_000,
            "CONTEXT_COMPACTION_TOKEN_CAP": 80_000,
            "CONTEXT_COMPACTION_PROMPT_TEMPLATE": "Keep this custom prompt",
        }
        client = FakeClient({"/api/v1/chats/config": current})

        configure_openwebui.configure_context_compaction(
            client,
            True,
            configure_openwebui.DEFAULT_CONTEXT_COMPACTION_TOKEN_THRESHOLD,
        )

        path, payload = client.posts[0]
        self.assertEqual(path, "/api/v1/chats/config")
        self.assertTrue(payload["ENABLE_CONTEXT_COMPACTION"])
        self.assertEqual(
            payload["CONTEXT_COMPACTION_TOKEN_THRESHOLD"],
            136_000,
        )
        self.assertEqual(
            payload["CONTEXT_COMPACTION_TOKEN_CAP"],
            136_000,
        )
        self.assertEqual(
            payload["CONTEXT_COMPACTION_PROMPT_TEMPLATE"],
            "Keep this custom prompt",
        )

    def test_context_compaction_preserves_a_higher_token_cap(self):
        current = {
            "ENABLE_CONTEXT_COMPACTION": False,
            "CONTEXT_COMPACTION_TOKEN_THRESHOLD": 80_000,
            "CONTEXT_COMPACTION_TOKEN_CAP": 200_000,
            "CONTEXT_COMPACTION_PROMPT_TEMPLATE": "",
        }
        client = FakeClient({"/api/v1/chats/config": current})

        configure_openwebui.configure_context_compaction(
            client,
            True,
            configure_openwebui.DEFAULT_CONTEXT_COMPACTION_TOKEN_THRESHOLD,
        )

        payload = client.posts[0][1]
        self.assertEqual(
            payload["CONTEXT_COMPACTION_TOKEN_THRESHOLD"],
            136_000,
        )
        self.assertEqual(
            payload["CONTEXT_COMPACTION_TOKEN_CAP"],
            200_000,
        )

    def test_context_compaction_skips_token_cap_when_unsupported(self):
        # Pinned Open WebUI releases predating the Token Cap feature never
        # include the key in /api/v1/chats/config at all - the configurator
        # must not send or verify it in that case.
        current = {
            "ENABLE_CONTEXT_COMPACTION": False,
            "CONTEXT_COMPACTION_TOKEN_THRESHOLD": 80_000,
            "CONTEXT_COMPACTION_PROMPT_TEMPLATE": "",
        }
        client = FakeClient({"/api/v1/chats/config": current})

        configure_openwebui.configure_context_compaction(
            client,
            True,
            configure_openwebui.DEFAULT_CONTEXT_COMPACTION_TOKEN_THRESHOLD,
        )

        path, payload = client.posts[0]
        self.assertEqual(path, "/api/v1/chats/config")
        self.assertNotIn("CONTEXT_COMPACTION_TOKEN_CAP", payload)

    def test_native_code_execution_is_disabled_without_losing_other_settings(self):
        current = {
            "ENABLE_CODE_EXECUTION": True,
            "CODE_EXECUTION_ENGINE": "pyodide",
            "CODE_EXECUTION_JUPYTER_URL": "https://jupyter.example",
            "ENABLE_CODE_INTERPRETER": True,
            "CODE_INTERPRETER_ENGINE": "jupyter",
            "CODE_INTERPRETER_PROMPT_TEMPLATE": "Keep this prompt",
        }
        client = FakeClient({"/api/v1/configs/code_execution": current})

        configure_openwebui.configure_native_code_execution(
            client,
            False,
            False,
        )

        path, payload = client.posts[0]
        self.assertEqual(path, "/api/v1/configs/code_execution")
        self.assertFalse(payload["ENABLE_CODE_EXECUTION"])
        self.assertFalse(payload["ENABLE_CODE_INTERPRETER"])
        self.assertEqual(payload["CODE_EXECUTION_ENGINE"], "pyodide")
        self.assertEqual(
            payload["CODE_EXECUTION_JUPYTER_URL"],
            "https://jupyter.example",
        )
        self.assertEqual(payload["CODE_INTERPRETER_ENGINE"], "jupyter")
        self.assertEqual(
            payload["CODE_INTERPRETER_PROMPT_TEMPLATE"],
            "Keep this prompt",
        )

    def test_interface_default_preserves_other_admin_settings(self):
        path = "/api/v1/auths/admin/config"
        for defaults in (None, {"collapseCodeBlocks": False, "theme": "dark"}):
            with self.subTest(defaults=defaults):
                mfa_policy = {
                    "ENABLE_MFA": False,
                    "MFA_ALLOW_OAUTH_BYPASS": True,
                    "MFA_ALLOW_TRUSTED_HEADER_BYPASS": False,
                }
                current = {"DEFAULT_INTERFACE_SETTINGS": defaults, "ENABLE_SIGNUP": False}
                client = FakeClient()
                client.responses[path] = lambda: {
                    **(client.posts[-1][1] if client.posts else current),
                    **mfa_policy,
                }

                configure_openwebui.configure_interface_defaults(client)
                configure_openwebui.configure_interface_defaults(client)

                self.assertEqual(
                    client.posts,
                    [
                        (
                            path,
                            {
                                **current,
                                "DEFAULT_INTERFACE_SETTINGS": {
                                    **(defaults or {}),
                                    "collapseCodeBlocks": True,
                                },
                            },
                        ),
                    ],
                )

    def test_interface_default_requires_persistence(self):
        path = "/api/v1/auths/admin/config"
        client = FakeClient({path: {"DEFAULT_INTERFACE_SETTINGS": {}}})

        with self.assertRaisesRegex(RuntimeError, "default verification failed"):
            configure_openwebui.configure_interface_defaults(client)

    def test_title_prompt_matches_requested_template(self):
        self.assertEqual(
            configure_openwebui.TITLE_GENERATION_PROMPT,
            """### Task:
Generate a concise 3–5 word title summarizing the chat history.

### Guidelines:
- Do not include emoji, symbols, quotation marks, or special formatting.
- Clearly represent the main subject of the conversation.
- Write in the chat's primary language.
- Return only a raw JSON object.

### Output:
{ "title": "your concise title here" }

### Chat History:
<chat_history>
{{MESSAGES:END:2}}
</chat_history>""",
        )

    def test_missing_workspace_model_is_created_hidden_and_public(self):
        client = MissingModelClient()

        configure_openwebui.hide_task_model(
            client,
            "gpt-5.6-luna",
            {"id": "gpt-5.6-luna", "name": "gpt-5.6-luna"},
        )

        path, payload = client.posts[0]
        self.assertEqual(path, "/api/v1/models/create")
        self.assertTrue(payload["meta"]["hidden"])
        self.assertIn(
            configure_openwebui.PUBLIC_READ_GRANT,
            payload["access_grants"],
        )

    def test_configure_terminal_server_registers_sandbox_connection(self):
        path = "/api/v1/configs/terminal_servers"
        other = {"id": "another-terminal", "url": "http://example.test"}
        client = FakeClient({
            path: {
                "TERMINAL_SERVER_CONNECTIONS": [
                    other,
                    {"id": "idea-sandbox", "key": "test-token"},
                ],
            },
        })
        configure_openwebui.configure_terminal_server(
            client,
            "http://sandbox:8020",
            "test-token",
        )
        self.assertEqual(len(client.posts), 1)
        path, payload = client.posts[0]
        self.assertEqual(path, "/api/v1/configs/terminal_servers")
        connections = payload.get("TERMINAL_SERVER_CONNECTIONS") or []
        self.assertEqual(len(connections), 2)
        self.assertEqual(connections[0], other)
        connection = connections[1]
        self.assertEqual(connection["id"], "idea-sandbox")
        self.assertEqual(connection["url"], "http://sandbox:8020")
        self.assertEqual(
            connection["key"],
            hmac.digest(b"test-token", b"idea:file-explorer:v1", "sha256").hex(),
        )
        self.assertTrue(connection["enabled"])
        self.assertEqual(
            connection["config"]["access_grants"],
            [configure_openwebui.PUBLIC_READ_GRANT],
        )

    def test_configure_terminal_server_requires_configured_authentication(self):
        client = FakeClient()
        with self.assertRaisesRegex(RuntimeError, "INTERNAL_SERVICE_TOKEN is required"):
            configure_openwebui.configure_terminal_server(client, "http://sandbox:8020", "")
        self.assertEqual(client.posts, [])


if __name__ == "__main__":
    unittest.main()
