import importlib.util
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

    def test_resolves_function_qualified_pipe_model_id(self):
        client = FakeClient(
            responses={
                "/api/models": {
                    "data": [
                        {"id": "gpt-5.6-luna", "name": "gpt-5.6-luna"},
                        {
                            "id": "idea_terminal_agent.idea-terminal-agent",
                            "name": "IDEA Terminal Agent",
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
        self.assertIn("# Welcome Assistant", payload["params"]["system"])
        self.assertTrue(
            payload["meta"]["profile_image_url"].startswith("data:image/png;base64,")
        )
        self.assertIn(
            deploy.public_read_grants([])[0],
            payload["access_grants"],
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

    def test_base_model_remains_visible_for_chat_and_assistant_editor(self):
        client = FakeClient()

        action = deploy.configure_assistant_base_model(
            client,
            "idea-terminal-agent",
            {"id": "idea-terminal-agent", "name": "IDEA Terminal Agent"},
            dry_run=False,
        )

        self.assertEqual(action, "created")
        path, payload = client.posts[0]
        self.assertEqual(path, "/api/v1/models/create")
        self.assertFalse(payload["meta"]["hidden"])
        self.assertNotIn("assistant_base_model", payload["meta"])

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
