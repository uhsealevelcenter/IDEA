import hashlib
import io
import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

from langchain_core.messages import AIMessage, ToolMessage


LANGGRAPH_DIR = Path(__file__).resolve().parents[1] / "langgraph"
sys.path.insert(0, str(LANGGRAPH_DIR))

from utils import skill_loader  # noqa: E402
from agents import terminal_agent  # noqa: E402


class BuiltinSkillLoaderTests(unittest.TestCase):
    def test_reads_every_builtin_skill_exactly(self):
        loader = skill_loader.BuiltinSkillLoader()
        documents = loader.catalog()

        self.assertEqual(len(documents), 10)
        for document in documents:
            source = (
                loader.root / document.skill_id / "SKILL.md"
            ).read_text(encoding="utf-8")
            self.assertEqual(document.content, source)
            self.assertEqual(
                document.sha256,
                hashlib.sha256(source.encode("utf-8")).hexdigest(),
            )
            self.assertNotIn("line(s) omitted", document.content)

    def test_frontend_skill_contains_middle_section_omitted_by_terminal_cat(self):
        document = skill_loader.BuiltinSkillLoader().load(
            "frontend-design"
        )

        self.assertIn("## Review Tooling", document.content)
        self.assertEqual(document.byte_count, 4944)
        self.assertEqual(
            document.sha256,
            "414bce8cd0aff62cd41335edfa4bbbed1dbe2942ca86f6deb6d25ab3896fe44b",
        )

    def test_manifest_is_generated_from_frontmatter(self):
        manifest = skill_loader.BuiltinSkillLoader().render_manifest()

        self.assertIn("<available_builtin_skills>", manifest)
        self.assertIn("<source>builtin</source>", manifest)
        self.assertIn("<id>frontend-design</id>", manifest)
        self.assertIn(
            "Create distinctive, production-grade frontend interfaces",
            manifest,
        )
        self.assertIn("<id>cindra</id>", manifest)
        self.assertIn("<kind>package</kind>", manifest)
        self.assertIn("<id>flood-frequency</id>", manifest)

    def test_rejects_traversal_unknown_and_symlinked_skills(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            valid_dir = root / "valid"
            valid_dir.mkdir()
            valid_skill = (
                "---\n"
                "name: valid\n"
                "description: Valid test skill\n"
                "---\n"
                "Complete instructions\n"
            )
            (valid_dir / "SKILL.md").write_text(
                valid_skill,
                encoding="utf-8",
            )
            (root / "linked").symlink_to(valid_dir, target_is_directory=True)
            loader = skill_loader.BuiltinSkillLoader(root)

            self.assertEqual(loader.load("valid").content, valid_skill)
            for invalid_id in ("../valid", "unknown", "linked"):
                with self.subTest(skill_id=invalid_id):
                    with self.assertRaises(skill_loader.SkillLoadError):
                        loader.load(invalid_id)

    def test_rejects_malformed_or_mismatched_frontmatter(self):
        cases = {
            "missing": "No frontmatter",
            "mismatch": (
                "---\nname: another\ndescription: Test\n---\nBody\n"
            ),
            "description": "---\nname: description\n---\nBody\n",
        }
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            for skill_id, content in cases.items():
                path = root / skill_id
                path.mkdir()
                (path / "SKILL.md").write_text(content, encoding="utf-8")
            loader = skill_loader.BuiltinSkillLoader(root)

            for skill_id in cases:
                with self.subTest(skill_id=skill_id):
                    with self.assertRaises(skill_loader.SkillLoadError):
                        loader.load(skill_id)


class BuiltinSkillPackageTests(unittest.TestCase):
    @staticmethod
    def write_package(root: Path, manifest: str) -> Path:
        package = root / "example"
        (package / "shared").mkdir(parents=True)
        (package / "skills" / "base").mkdir(parents=True)
        (package / "skills" / "workflow").mkdir(parents=True)
        (package / "SKILL.md").write_text(
            "---\n"
            "name: example\n"
            "description: Example package router\n"
            "---\n"
            "ROOT-POLICY\n",
            encoding="utf-8",
        )
        (package / "shared" / "rules.md").write_text(
            "SHARED-RULES\n",
            encoding="utf-8",
        )
        (package / "skills" / "base" / "SKILL.md").write_text(
            "---\n"
            "name: base\n"
            "description: Base policy\n"
            "---\n"
            "BASE-POLICY\n",
            encoding="utf-8",
        )
        (package / "skills" / "workflow" / "SKILL.md").write_text(
            "---\n"
            "name: workflow\n"
            "description: Workflow instructions\n"
            "---\n"
            "WORKFLOW-INSTRUCTIONS\n",
            encoding="utf-8",
        )
        (package / "manifest.yaml").write_text(manifest, encoding="utf-8")
        return package

    @staticmethod
    def valid_manifest(extra: str = "") -> str:
        return (
            "schema_version: 1\n"
            "id: example\n"
            "description: Example hierarchical package\n"
            "entrypoint: SKILL.md\n"
            "references:\n"
            "  shared-rules:\n"
            "    path: shared/rules.md\n"
            "components:\n"
            "  base:\n"
            "    path: skills/base/SKILL.md\n"
            "    requires: [shared-rules]\n"
            "  workflow:\n"
            "    path: skills/workflow/SKILL.md\n"
            "    requires: [base, shared-rules]\n"
            "routes:\n"
            "  standard:\n"
            "    description: Run the standard workflow.\n"
            "    components: [workflow]\n"
            f"{extra}"
        )

    def test_resolves_route_dependencies_once_in_deterministic_order(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            self.write_package(root, self.valid_manifest())
            loader = skill_loader.BuiltinSkillLoader(root)

            bundle = loader.load_bundle("example", route="standard")

            self.assertEqual(
                [document.component_id for document in bundle.documents],
                ["root", "shared-rules", "base", "workflow"],
            )
            self.assertEqual(bundle.route, "standard")
            self.assertEqual(bundle.requested_components, ("workflow",))
            self.assertEqual(
                sum(
                    "SHARED-RULES" in document.content
                    for document in bundle.documents
                ),
                1,
            )
            payload = json.loads(bundle.to_tool_result())
            self.assertEqual(payload["load_order"], [
                "root",
                "shared-rules",
                "base",
                "workflow",
            ])
            self.assertTrue(all("sha256" in item for item in payload["documents"]))

    def test_component_selection_still_loads_policy_dependencies(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            self.write_package(root, self.valid_manifest())
            loader = skill_loader.BuiltinSkillLoader(root)

            bundle = loader.load_bundle(
                "example",
                components=["workflow"],
            )

            self.assertEqual(
                [document.component_id for document in bundle.documents],
                ["root", "shared-rules", "base", "workflow"],
            )

    def test_rejects_cycles_traversal_and_unknown_routes(self):
        cycle_manifest = self.valid_manifest().replace(
            "requires: [shared-rules]",
            "requires: [workflow]",
            1,
        )
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            self.write_package(root, cycle_manifest)
            with self.assertRaisesRegex(
                skill_loader.SkillLoadError,
                "dependency cycle",
            ):
                skill_loader.BuiltinSkillLoader(root).package("example")

        traversal_manifest = self.valid_manifest().replace(
            "path: shared/rules.md",
            "path: ../outside.md",
        )
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            self.write_package(root, traversal_manifest)
            with self.assertRaisesRegex(
                skill_loader.SkillLoadError,
                "unsafe path",
            ):
                skill_loader.BuiltinSkillLoader(root).package("example")

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            self.write_package(root, self.valid_manifest())
            with self.assertRaisesRegex(
                skill_loader.SkillLoadError,
                "Unknown route",
            ):
                skill_loader.BuiltinSkillLoader(root).load_bundle(
                    "example",
                    route="missing",
                )

    def test_bundle_limit_fails_without_partial_result(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            self.write_package(root, self.valid_manifest())
            loader = skill_loader.BuiltinSkillLoader(root)

            with patch.object(skill_loader, "MAX_SKILL_BUNDLE_BYTES", 10):
                with self.assertRaisesRegex(
                    skill_loader.SkillLoadError,
                    "No package component was partially loaded",
                ):
                    loader.load_bundle("example", route="standard")

    def test_cindra_routes_load_complete_expected_source_documents(self):
        loader = skill_loader.BuiltinSkillLoader()
        bundle = loader.load_bundle("cindra", route="trend")
        component_ids = [
            document.component_id for document in bundle.documents
        ]

        self.assertEqual(
            component_ids,
            [
                "root",
                "cindra-terminology",
                "cindra-data-sources",
                "cindra-conventions",
                "cindra-validation-rules",
                "cindra-sea-level-governance",
                "cindra-site-setup",
                "cindra-quality-control",
                "cindra-sea-level-trend",
            ],
        )
        package = loader.package("cindra")
        self.assertIsNotNone(package)
        for document in bundle.documents[1:]:
            source = package.components[document.component_id].path.read_text(
                encoding="utf-8"
            )
            self.assertEqual(document.content, source)
            self.assertEqual(
                document.sha256,
                hashlib.sha256(source.encode("utf-8")).hexdigest(),
            )
        self.assertLess(bundle.byte_count, skill_loader.MAX_SKILL_BUNDLE_BYTES)
        self.assertTrue(
            all(
                requirement["status"] == "unresolved"
                for requirement in bundle.external_requirements
            )
        )

        package = loader.package("cindra")
        self.assertIsNotNone(package)
        for route_id, route in package.routes.items():
            with self.subTest(route=route_id):
                route_bundle = loader.load_bundle("cindra", route=route_id)
                load_order = [
                    document.component_id
                    for document in route_bundle.documents
                ]
                self.assertEqual(load_order[0], "root")
                self.assertEqual(len(load_order), len(set(load_order)))
                for requested in route.components:
                    self.assertIn(requested, load_order)
                for component_id in load_order[1:]:
                    component_index = load_order.index(component_id)
                    for dependency in package.components[
                        component_id
                    ].requires:
                        self.assertLess(
                            load_order.index(dependency),
                            component_index,
                        )


class OpenWebUISkillLoaderTests(unittest.TestCase):
    @staticmethod
    def response(status_code=200, payload=None):
        response = Mock()
        response.status_code = status_code
        response.json.return_value = payload or {}
        if status_code >= 400:
            response.raise_for_status.side_effect = (
                skill_loader.requests.HTTPError("request failed")
            )
        return response

    def test_loads_complete_authorized_workspace_skill(self):
        content = "# Workspace skill\n\nSTART\nMIDDLE\nEND\n"
        http_get = Mock(return_value=self.response(payload={
            "id": "my-skill",
            "name": "My Skill",
            "description": "A private workflow",
            "content": content,
            "is_active": True,
        }))
        loader = skill_loader.OpenWebUISkillLoader(
            "http://openwebui:8080",
            "Bearer user-token",
            http_get=http_get,
        )

        document = loader.load("my-skill")

        self.assertEqual(document.content, content)
        self.assertEqual(document.source, "workspace")
        self.assertIn("MIDDLE", document.content)
        http_get.assert_called_once_with(
            "http://openwebui:8080/api/v1/skills/id/my-skill",
            headers={"Authorization": "Bearer user-token"},
            timeout=(5.0, 15.0),
        )

    def test_url_encodes_workspace_skill_id(self):
        http_get = Mock(return_value=self.response(payload={
            "id": "skill/name",
            "name": "Skill",
            "description": "",
            "content": "Complete",
            "is_active": True,
        }))
        loader = skill_loader.OpenWebUISkillLoader(
            "http://openwebui:8080",
            "Bearer token",
            http_get=http_get,
        )

        loader.load("skill/name")

        self.assertIn(
            "/api/v1/skills/id/skill%2Fname",
            http_get.call_args.args[0],
        )

    def test_rejects_missing_auth_access_denial_missing_and_inactive(self):
        no_auth_get = Mock()
        with self.assertRaises(skill_loader.SkillLoadError):
            skill_loader.OpenWebUISkillLoader(
                "http://openwebui:8080",
                None,
                http_get=no_auth_get,
            ).load("private")
        no_auth_get.assert_not_called()

        cases = [
            (403, {}, "Access denied"),
            (404, {}, "was not found"),
            (
                200,
                {
                    "id": "private",
                    "name": "Private",
                    "content": "Hidden",
                    "is_active": False,
                },
                "inactive",
            ),
        ]
        for status, payload, expected in cases:
            with self.subTest(status=status, expected=expected):
                loader = skill_loader.OpenWebUISkillLoader(
                    "http://openwebui:8080",
                    "Bearer secret-token",
                    http_get=Mock(
                        return_value=self.response(status, payload)
                    ),
                )
                with self.assertRaisesRegex(
                    skill_loader.SkillLoadError,
                    expected,
                ) as raised:
                    loader.load("private")
                self.assertNotIn("secret-token", str(raised.exception))

    def test_does_not_silently_truncate_oversized_skill(self):
        http_get = Mock(return_value=self.response(payload={
            "id": "large",
            "name": "Large",
            "description": "",
            "content": "x" * 101,
            "is_active": True,
        }))
        loader = skill_loader.OpenWebUISkillLoader(
            "http://openwebui:8080",
            "Bearer token",
            http_get=http_get,
        )

        with patch.object(skill_loader, "MAX_SKILL_BYTES", 100):
            with self.assertRaisesRegex(
                skill_loader.SkillLoadError,
                "not partially loaded",
            ):
                loader.load("large")

    def test_rejects_hierarchical_workspace_selection_explicitly(self):
        loader = skill_loader.OpenWebUISkillLoader(
            "http://openwebui:8080",
            "Bearer token",
            http_get=Mock(),
        )

        with self.assertRaisesRegex(
            skill_loader.SkillLoadError,
            "do not yet support hierarchical",
        ):
            loader.load_bundle("workspace-package", route="workflow")


class ViewSkillToolTests(unittest.TestCase):
    def test_unified_tool_returns_full_content_for_both_sources(self):
        builtin = Mock()
        workspace = Mock()
        builtin.load.return_value = skill_loader.SkillDocument(
            "builtin",
            "built",
            "Built",
            "Built-in",
            "BEGIN\nBUILTIN-MIDDLE\nEND",
        )
        workspace.load.return_value = skill_loader.SkillDocument(
            "workspace",
            "custom",
            "Custom",
            "Workspace",
            "BEGIN\nWORKSPACE-MIDDLE\nEND",
        )
        view_skill = skill_loader.make_view_skill_tool(
            builtin,
            workspace,
        )

        built_result = json.loads(view_skill.invoke({
            "source": "builtin",
            "id": "built",
        }))
        workspace_result = json.loads(view_skill.invoke({
            "source": "workspace",
            "id": "custom",
        }))

        self.assertIn("BUILTIN-MIDDLE", built_result["content"])
        self.assertIn("WORKSPACE-MIDDLE", workspace_result["content"])
        builtin.load.assert_called_once_with("built")
        workspace.load.assert_called_once_with("custom")

    def test_package_route_returns_complete_bundle(self):
        root = skill_loader.SkillBundleDocument(
            "root",
            "root",
            "Router",
            "ROOT SECRET",
        )
        module = skill_loader.SkillBundleDocument(
            "workflow",
            "skill",
            "Workflow",
            "WORKFLOW SECRET",
        )
        bundle = skill_loader.SkillBundle(
            source="builtin",
            skill_id="package",
            name="Package",
            description="Package description",
            status="draft",
            route="standard",
            requested_components=("workflow",),
            documents=(root, module),
        )
        builtin = Mock()
        builtin.load_bundle.return_value = bundle
        workspace = Mock()
        view_skill = skill_loader.make_view_skill_tool(builtin, workspace)

        result = json.loads(view_skill.invoke({
            "source": "builtin",
            "id": "package",
            "route": "standard",
        }))

        self.assertEqual(result["load_order"], ["root", "workflow"])
        self.assertEqual(
            [document["content"] for document in result["documents"]],
            ["ROOT SECRET", "WORKFLOW SECRET"],
        )
        builtin.load_bundle.assert_called_once_with(
            "package",
            route="standard",
            components=None,
        )

    def test_log_summary_does_not_include_skill_content(self):
        document = skill_loader.SkillDocument(
            "workspace",
            "private",
            "Private",
            "Private workflow",
            "DO NOT LOG THIS PRIVATE CONTENT",
        )

        summary = skill_loader.summarize_skill_result(
            document.to_tool_result()
        )

        self.assertIn("workspace skill 'private'", summary)
        self.assertIn(document.sha256, summary)
        self.assertNotIn("DO NOT LOG", summary)

    def test_package_log_summary_does_not_include_component_content(self):
        bundle = skill_loader.SkillBundle(
            source="builtin",
            skill_id="package",
            name="Package",
            description="Package",
            status="draft",
            route="standard",
            requested_components=("secret",),
            documents=(
                skill_loader.SkillBundleDocument(
                    "root",
                    "root",
                    "Root",
                    "ROOT PRIVATE CONTENT",
                ),
                skill_loader.SkillBundleDocument(
                    "secret",
                    "skill",
                    "Secret",
                    "MODULE PRIVATE CONTENT",
                ),
            ),
        )

        summary = skill_loader.summarize_skill_result(
            bundle.to_tool_result()
        )

        self.assertIn("skill package 'package' route 'standard'", summary)
        self.assertIn(bundle.sha256, summary)
        self.assertNotIn("PRIVATE CONTENT", summary)


class TerminalAgentSkillIntegrationTests(unittest.TestCase):
    def test_prompt_contains_generated_manifest_and_no_cat_workflow(self):
        base_prompt = (
            Path(terminal_agent.SYSTEM_PROMPT_PATH)
            .read_text(encoding="utf-8")
        )
        manifest = skill_loader.BuiltinSkillLoader().render_manifest()

        prompt = terminal_agent.compose_system_prompt(
            base_prompt,
            "<available_skills><skill><id>custom</id></skill></available_skills>",
            manifest,
        )

        self.assertIn("<available_builtin_skills>", prompt)
        self.assertIn("<id>frontend-design</id>", prompt)
        self.assertIn("<id>custom</id>", prompt)
        self.assertNotIn(
            "cat langgraph/utils/skills/<skill-name>/SKILL.md",
            prompt,
        )

    def test_agent_loop_gives_full_skill_to_model_but_redacts_logs(self):
        private_content = (
            "BEGIN PRIVATE SKILL\n"
            "PRIVATE-MIDDLE-INSTRUCTION\n"
            "END PRIVATE SKILL"
        )
        document = skill_loader.SkillDocument(
            "workspace",
            "private",
            "Private",
            "Private integration test",
            private_content,
        )
        builtin_loader = Mock()
        builtin_loader.render_manifest.return_value = (
            "<available_builtin_skills></available_builtin_skills>"
        )
        workspace_loader = Mock()
        workspace_loader.load.return_value = document
        view_skill = skill_loader.make_view_skill_tool(
            builtin_loader,
            workspace_loader,
        )

        first_response = AIMessage(
            content="",
            tool_calls=[{
                "name": "view_skill",
                "args": {"source": "workspace", "id": "private"},
                "id": "skill-call",
                "type": "tool_call",
            }],
        )
        second_response = AIMessage(content="Skill applied")
        llm = Mock()
        llm.invoke.side_effect = [first_response, second_response]

        agent = terminal_agent.TerminalAgent.__new__(
            terminal_agent.TerminalAgent
        )
        agent.sandbox_id = "test-user"
        agent._shown_image_hashes = set()
        agent.assistant_id = None
        agent.assistant_system_prompt = (
            "<available_skills><skill><id>private</id></skill></available_skills>"
        )
        agent.builtin_skill_loader = builtin_loader
        agent.view_skill_tool = view_skill
        agent.tools_by_name = {"view_skill": view_skill}
        agent.llm = llm
        agent.max_iterations = 3
        agent._sync_outputs_to_openwebui = Mock(return_value=[])

        output = io.StringIO()
        with (
            patch.object(
                terminal_agent,
                "list_file_metadata",
                return_value={},
            ),
            patch("sys.stdout", output),
        ):
            result = agent.run("Use the private skill")

        second_messages = llm.invoke.call_args_list[1].args[0]
        tool_messages = [
            message
            for message in second_messages
            if isinstance(message, ToolMessage)
        ]
        self.assertEqual(len(tool_messages), 1)
        self.assertIn(
            "PRIVATE-MIDDLE-INSTRUCTION",
            tool_messages[0].content,
        )
        self.assertNotIn(private_content, output.getvalue())
        self.assertIn("Loaded workspace skill 'private'", output.getvalue())
        self.assertEqual(result["final_response"], "Skill applied")


if __name__ == "__main__":
    unittest.main()
