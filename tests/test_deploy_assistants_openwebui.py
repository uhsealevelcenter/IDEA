import hashlib
import importlib.util
import json
import tempfile
import unittest
from pathlib import Path


REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
SCRIPT_PATH = REPOSITORY_ROOT / "assistants" / "deploy_assistants_openwebui.py"
SPEC = importlib.util.spec_from_file_location("deploy_assistants_openwebui", SCRIPT_PATH)
deploy = importlib.util.module_from_spec(SPEC)
assert SPEC.loader is not None
SPEC.loader.exec_module(deploy)


class FakeClient:
    def __init__(self, models=None, responses=None):
        self.models = models or {}
        self.responses = responses or {}
        self.posts = []

    def get(self, path):
        if path.startswith("/api/v1/models/model?"):
            model_id = path.split("=", 1)[1]
            if model_id not in self.models:
                raise deploy.ApiError("GET", path, 404, "Not found")
            return self.models[model_id]
        return self.responses[path]

    def post(self, path, payload):
        self.posts.append((path, payload))
        return payload


class DeployAssistantsTests(unittest.TestCase):
    def setUp(self):
        self.manifest_path = REPOSITORY_ROOT / "assistants" / "manifest.json"
        self.manifest = deploy.load_manifest(self.manifest_path)

    def test_manifest_contains_the_three_official_assistants(self):
        self.assertEqual(
            [item["id"] for item in self.manifest["assistants"]],
            ["welcome-assistant", "sea", "mars-assistant"],
        )
        self.assertEqual(self.manifest["base_model_id"], "idea-terminal-agent")
        self.assertEqual(self.manifest["base_model_name"], "IDEA Agent")
        self.assertEqual(self.manifest["base_model_logo"], "assets/idea.png")
        self.assertEqual(
            self.manifest["welcome_suggestion_assistant_ids"],
            ["cindra"],
        )

    def test_every_official_assistant_has_six_suggested_prompts(self):
        for definition in self.manifest["assistants"]:
            suggestions = definition["suggestion_prompts"]
            self.assertEqual(len(suggestions), 6, definition["id"])
            self.assertTrue(all(item["content"] for item in suggestions))
            self.assertTrue(all(len(item["title"]) == 2 for item in suggestions))

        welcome = next(
            item
            for item in self.manifest["assistants"]
            if item["id"] == "welcome-assistant"
        )
        self.assertEqual(
            welcome["suggestion_prompts"],
            self.manifest["default_suggestion_prompts"],
        )

    def test_new_manifest_assistant_inherits_welcome_suggestions(self):
        raw_manifest = json.loads(self.manifest_path.read_text(encoding="utf-8"))
        raw_manifest["assistants"].append({
            "id": "future-assistant",
            "name": "Future Assistant",
            "description": "A future deployment-managed Assistant.",
            "prompt": "prompts/welcome.md",
            "logo": "assets/idea.png",
        })
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "manifest.json"
            path.write_text(json.dumps(raw_manifest), encoding="utf-8")
            loaded = deploy.load_manifest(path)

        future = next(
            item
            for item in loaded["assistants"]
            if item["id"] == "future-assistant"
        )
        self.assertEqual(
            future["suggestion_prompts"],
            loaded["default_suggestion_prompts"],
        )

    def test_official_prompts_match_pinned_repository_versions(self):
        expected = {
            "prompts/welcome.md": (
                2372,
                "24516eeb01a8c0f300545b1564c29c88b7facdcd18b94fe8902716d9b183b51d",
                False,
            ),
            "prompts/sea.md": (
                6657,
                "a5e562e4a02ac7b94c891eb5a2b2fdd810251cdef51c0a4e386274859b6804a0",
                True,
            ),
            "prompts/mars.md": (
                8859,
                "c5674535bfd2b2e067de2a6386841d94871386d30cb7e15905028b3fec5434ca",
                False,
            ),
        }

        for relative_path, (
            expected_length,
            expected_hash,
            ends_with_newline,
        ) in expected.items():
            prompt = deploy.read_relative_text(
                self.manifest_path,
                relative_path,
                ends_with_newline,
            )
            self.assertEqual(len(prompt), expected_length, relative_path)
            self.assertEqual(
                hashlib.sha256(prompt.encode("utf-8")).hexdigest(),
                expected_hash,
                relative_path,
            )

    def test_resolves_function_qualified_pipe_model_id(self):
        client = FakeClient(
            responses={
                "/api/models": {
                    "data": [
                        {"id": "gpt-5.6-luna", "name": "gpt-5.6-luna"},
                        {
                            "id": "idea_terminal_agent.idea-terminal-agent",
                            "name": "IDEA Agent",
                        },
                    ]
                }
            }
        )

        model = deploy.wait_for_base_model(
            client,
            "idea-terminal-agent",
            wait_seconds=0,
        )

        self.assertEqual(
            model["id"],
            "idea_terminal_agent.idea-terminal-agent",
        )

    def test_prefers_exact_base_model_id(self):
        client = FakeClient(
            responses={
                "/api/models": {
                    "data": [
                        {"id": "other.idea-terminal-agent"},
                        {"id": "idea-terminal-agent"},
                    ]
                }
            }
        )

        model = deploy.wait_for_base_model(
            client,
            "idea-terminal-agent",
            wait_seconds=0,
        )

        self.assertEqual(model["id"], "idea-terminal-agent")

    def test_payload_contains_prompt_png_and_public_read_access(self):
        definition = self.manifest["assistants"][0]
        payload = deploy.official_assistant_payload(
            self.manifest_path,
            self.manifest["base_model_id"],
            definition,
        )

        self.assertEqual(payload["base_model_id"], "idea-terminal-agent")
        self.assertTrue(
            payload["params"]["system"].startswith(
                "Welcome Assistant System Instructions for IDEA"
            )
        )
        self.assertTrue(
            payload["meta"]["profile_image_url"].startswith("data:image/png;base64,")
        )
        self.assertIn(
            deploy.public_read_grants([])[0],
            payload["access_grants"],
        )
        self.assertEqual(
            payload["meta"]["suggestion_prompts"],
            definition["suggestion_prompts"],
        )
        self.assertEqual(len(payload["meta"]["suggestion_prompts"]), 6)

    def test_sea_and_mars_use_private_workspace_for_new_downloads(self):
        definitions = {
            item["id"]: item for item in self.manifest["assistants"]
        }
        for assistant_id in ("sea", "mars-assistant"):
            payload = deploy.official_assistant_payload(
                self.manifest_path,
                self.manifest["base_model_id"],
                definitions[assistant_id],
            )
            prompt = payload["params"]["system"]
            self.assertIn("/workspace", prompt)
            self.assertIn("read-only", prompt)

    def test_official_assistants_use_paperqa_without_native_openwebui_rag(self):
        managed_capabilities = {
            **deploy.OFFICIAL_ASSISTANT_CAPABILITIES,
            "file_context": False,
        }
        for definition in self.manifest["assistants"]:
            payload = deploy.official_assistant_payload(
                self.manifest_path,
                self.manifest["base_model_id"],
                definition,
            )

            self.assertTrue(payload["meta"]["paperqa_enabled"])
            self.assertEqual(
                payload["meta"]["capabilities"],
                managed_capabilities,
            )
            self.assertEqual(
                payload["meta"]["defaultFeatureIds"],
                [],
            )
            self.assertEqual(
                payload["meta"]["builtinTools"],
                deploy.OFFICIAL_ASSISTANT_BUILTIN_TOOLS,
            )
            self.assertEqual(
                payload["params"]["function_calling"],
                "legacy",
            )

    def test_only_time_builtin_is_enabled(self):
        self.assertTrue(
            deploy.OFFICIAL_ASSISTANT_BUILTIN_TOOLS["time"]
        )
        self.assertFalse(
            any(
                enabled
                for name, enabled in (
                    deploy.OFFICIAL_ASSISTANT_BUILTIN_TOOLS.items()
                )
                if name != "time"
            )
        )

    def test_seed_mode_preserves_an_existing_assistant(self):
        existing = {
            "id": "sea",
            "name": "UI-edited SEA",
            "meta": {"description": "UI edit"},
            "params": {"system": "UI-edited prompt"},
        }
        client = FakeClient(models={"sea": existing})

        result = deploy.deploy_assistants(
            client,
            self.manifest_path,
            self.manifest,
            reconcile=False,
            dry_run=False,
            only={"sea"},
        )

        self.assertEqual(result, {"sea": "skipped"})
        self.assertEqual(client.posts, [])

    def test_reconcile_preserves_unmanaged_fields(self):
        existing = {
            "id": "sea",
            "name": "UI-edited SEA",
            "meta": {"custom": "kept", "official_assistant": True},
            "params": {"temperature": 0.25},
            "access_grants": [],
        }
        client = FakeClient(models={"sea": existing})

        result = deploy.deploy_assistants(
            client,
            self.manifest_path,
            self.manifest,
            reconcile=True,
            dry_run=False,
            only={"sea"},
        )

        self.assertEqual(result, {"sea": "updated"})
        path, payload = client.posts[0]
        self.assertEqual(path, "/api/v1/models/model/update")
        self.assertEqual(payload["name"], "SEA")
        self.assertEqual(payload["meta"]["custom"], "kept")
        self.assertEqual(payload["params"]["temperature"], 0.25)
        self.assertIn("# SEA", payload["params"]["system"])
        self.assertEqual(
            payload["meta"]["suggestion_prompts"],
            next(
                item["suggestion_prompts"]
                for item in self.manifest["assistants"]
                if item["id"] == "sea"
            ),
        )

    def test_reconcile_refuses_to_overwrite_a_user_owned_id_collision(self):
        client = FakeClient(
            models={
                "sea": {
                    "id": "sea",
                    "name": "A user's SEA",
                    "meta": {},
                    "params": {"system": "Personal instructions"},
                }
            }
        )

        with self.assertRaisesRegex(RuntimeError, "not marked"):
            deploy.deploy_assistants(
                client,
                self.manifest_path,
                self.manifest,
                reconcile=True,
                dry_run=False,
                only={"sea"},
            )

        self.assertEqual(client.posts, [])

    def test_cindra_receives_only_welcome_suggestion_metadata(self):
        existing = {
            "id": "cindra",
            "base_model_id": "idea-terminal-agent",
            "name": "CIndRA",
            "meta": {
                "description": "Custom CIndRA description",
                "capabilities": {"vision": True},
                "custom": "kept",
            },
            "params": {"system": "CIndRA instructions", "temperature": 0.1},
            "access_grants": [{"principal_id": "owner", "permission": "read"}],
            "is_active": False,
        }
        client = FakeClient(models={"cindra": existing})

        result = deploy.deploy_welcome_suggestions(
            client,
            self.manifest,
            dry_run=False,
            only={"cindra"},
        )

        self.assertEqual(result, {"cindra": "updated"})
        path, payload = client.posts[0]
        self.assertEqual(path, "/api/v1/models/model/update")
        self.assertEqual(
            payload["meta"]["suggestion_prompts"],
            self.manifest["default_suggestion_prompts"],
        )
        self.assertEqual(payload["meta"]["custom"], "kept")
        self.assertEqual(payload["meta"]["capabilities"], {"vision": True})
        self.assertEqual(payload["params"], existing["params"])
        self.assertEqual(payload["access_grants"], existing["access_grants"])
        self.assertFalse(payload["is_active"])

    def test_base_model_remains_visible_for_chat_and_assistant_editor(self):
        client = FakeClient()

        action = deploy.configure_assistant_base_model(
            client,
            "idea-terminal-agent",
            "IDEA Agent",
            "data:image/png;base64,aWRlYQ==",
            dry_run=False,
        )

        self.assertEqual(action, "created")
        path, payload = client.posts[0]
        self.assertEqual(path, "/api/v1/models/create")
        self.assertEqual(payload["name"], "IDEA Agent")
        self.assertFalse(payload["meta"]["hidden"])
        self.assertNotIn("assistant_base_model", payload["meta"])
        self.assertEqual(
            payload["meta"]["profile_image_url"],
            "data:image/png;base64,aWRlYQ==",
        )

    def test_base_model_reconciles_stale_display_name(self):
        client = FakeClient(
            models={
                "idea-terminal-agent": {
                    "id": "idea-terminal-agent",
                    "name": "IDEA Terminal Agent",
                    "meta": {},
                }
            }
        )

        action = deploy.configure_assistant_base_model(
            client,
            "idea-terminal-agent",
            "IDEA Agent",
            "data:image/png;base64,aWRlYQ==",
            dry_run=False,
        )

        self.assertEqual(action, "updated")
        path, payload = client.posts[0]
        self.assertEqual(path, "/api/v1/models/model/update")
        self.assertEqual(payload["name"], "IDEA Agent")

    def test_permissions_enable_private_assistant_creation_only(self):
        client = FakeClient(
            responses={
                "/api/v1/users/default/permissions": {
                    "workspace": {"models": False, "knowledge": True},
                    "sharing": {"models": True, "public_models": True},
                    "chat": {"share": True},
                }
            }
        )

        deploy.configure_user_assistant_permissions(client, dry_run=False)

        path, payload = client.posts[0]
        self.assertEqual(path, "/api/v1/users/default/permissions")
        self.assertTrue(payload["workspace"]["models"])
        self.assertTrue(payload["workspace"]["knowledge"])
        self.assertFalse(payload["sharing"]["models"])
        self.assertFalse(payload["sharing"]["public_models"])
        self.assertTrue(payload["chat"]["share"])

    def test_seed_mode_preserves_an_existing_default(self):
        client = FakeClient(
            responses={
                "/api/v1/configs/models": {
                    "DEFAULT_MODELS": "another-assistant",
                    "DEFAULT_PINNED_MODELS": "",
                    "MODEL_ORDER_LIST": [],
                }
            }
        )

        action = deploy.configure_default_assistant(
            client,
            "welcome-assistant",
            reconcile=False,
            dry_run=False,
        )

        self.assertEqual(action, "skipped")
        self.assertEqual(client.posts, [])


if __name__ == "__main__":
    unittest.main()
