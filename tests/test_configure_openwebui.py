import importlib.util
import unittest
from pathlib import Path


SCRIPT_PATH = (
    Path(__file__).resolve().parents[1]
    / "openwebui"
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
            payload["CONTEXT_COMPACTION_PROMPT_TEMPLATE"],
            "Keep this custom prompt",
        )

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


if __name__ == "__main__":
    unittest.main()
