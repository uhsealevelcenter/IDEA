import sys
import unittest
from pathlib import Path
from unittest.mock import Mock, patch


LANGGRAPH_DIR = Path(__file__).resolve().parents[1] / "langgraph"
sys.path.insert(0, str(LANGGRAPH_DIR))

from agents import terminal_agent  # noqa: E402


class OutputSyncTests(unittest.TestCase):
    def make_agent(self, authorization: str | None = "Bearer user-token"):
        agent = terminal_agent.TerminalAgent.__new__(
            terminal_agent.TerminalAgent
        )
        agent.sandbox_id = "user-1"
        agent.openwebui_authorization = authorization
        return agent

    def test_uploads_only_new_or_modified_outputs(self):
        response_ids = {
            "a.csv": "file-a",
            "b.png": "file-b",
        }

        def post(_url, *, headers, files, params, timeout):
            filename, data = files["file"]
            self.assertEqual(headers, {"Authorization": "Bearer user-token"})
            self.assertEqual(data, f"contents:{filename}".encode())
            self.assertEqual(params, {"process": "false"})
            self.assertEqual(timeout[0], 5)
            response = Mock()
            response.raise_for_status.return_value = None
            response.json.return_value = {"id": response_ids[filename]}
            return response

        with (
            patch.object(
                terminal_agent,
                "list_file_metadata",
                return_value={
                    "/outputs/b.png": "20:200.0",
                    "/outputs/nested/a.csv": "10:100.0",
                    "/outputs/unchanged.txt": "5:50.0",
                },
            ),
            patch.object(
                terminal_agent,
                "read_file_bytes",
                side_effect=lambda path, session_id, timeout: (
                    f"contents:{Path(path).name}".encode()
                ),
            ),
            patch.object(terminal_agent.requests, "post", side_effect=post),
        ):
            synced = self.make_agent()._sync_outputs_to_openwebui(
                {
                    "/outputs/nested/a.csv": "9:90.0",
                    "/outputs/unchanged.txt": "5:50.0",
                }
            )

        self.assertEqual(
            synced,
            [
                {
                    "filename": "/outputs/b.png",
                    "openwebui_file_id": "file-b",
                },
                {
                    "filename": "/outputs/nested/a.csv",
                    "openwebui_file_id": "file-a",
                },
            ],
        )

    def test_does_not_upload_without_current_user_credential(self):
        with (
            patch.object(
                terminal_agent,
                "list_file_metadata",
            ) as list_file_metadata,
            patch.object(terminal_agent.requests, "post") as post,
        ):
            synced = self.make_agent(None)._sync_outputs_to_openwebui({})

        self.assertEqual(synced, [])
        list_file_metadata.assert_not_called()
        post.assert_not_called()

    def test_does_not_upload_unchanged_outputs(self):
        snapshot = {
            "/outputs/existing.csv": "10:100.0",
        }
        with (
            patch.object(
                terminal_agent,
                "list_file_metadata",
                return_value=snapshot,
            ),
            patch.object(terminal_agent.requests, "post") as post,
        ):
            synced = self.make_agent()._sync_outputs_to_openwebui(snapshot)

        self.assertEqual(synced, [])
        post.assert_not_called()

    def test_skips_sync_if_pre_turn_snapshot_failed(self):
        with (
            patch.object(
                terminal_agent,
                "list_file_metadata",
            ) as list_file_metadata,
            patch.object(terminal_agent.requests, "post") as post,
        ):
            synced = self.make_agent()._sync_outputs_to_openwebui(None)

        self.assertEqual(synced, [])
        list_file_metadata.assert_not_called()
        post.assert_not_called()

    def test_warns_but_uploads_html_unchanged_without_dependencies(self):
        original_html = (
            b'<!doctype html><html><body>'
            b'<img src="numbers_plot.png?view=full">'
            b"</body></html>"
        )
        file_contents = {
            "/outputs/hello/index.html": original_html,
            "/outputs/hello/numbers_plot.png": b"png-bytes",
        }
        upload_order = []
        uploaded_data = {}

        def post(_url, *, headers, files, params, timeout):
            filename, data = files["file"]
            upload_order.append(filename)
            uploaded_data[filename] = data
            response = Mock()
            response.raise_for_status.return_value = None
            response.json.return_value = {"id": "html-id"}
            return response

        read_file_bytes = Mock(
            side_effect=lambda path, session_id, timeout: file_contents[path]
        )
        with (
            patch.object(
                terminal_agent,
                "list_file_metadata",
                return_value={
                    "/outputs/hello/index.html": "100:200.0",
                    "/outputs/hello/numbers_plot.png": "50:100.0",
                },
            ),
            patch.object(
                terminal_agent,
                "read_file_bytes",
                read_file_bytes,
            ),
            patch.object(terminal_agent.requests, "post", side_effect=post),
            patch("builtins.print") as print_mock,
        ):
            synced = self.make_agent()._sync_outputs_to_openwebui(
                {
                    # The image is unchanged, but the new HTML depends on it.
                    "/outputs/hello/numbers_plot.png": "50:100.0",
                }
            )

        self.assertEqual(upload_order, ["index.html"])
        self.assertEqual(uploaded_data["index.html"], original_html)
        read_file_bytes.assert_called_once()
        self.assertTrue(
            any(
                "is not self-contained" in str(call)
                and "/outputs/hello/numbers_plot.png" in str(call)
                for call in print_mock.call_args_list
            )
        )
        self.assertEqual(
            synced,
            [
                {
                    "filename": "/outputs/hello/index.html",
                    "openwebui_file_id": "html-id",
                }
            ],
        )


if __name__ == "__main__":
    unittest.main()
