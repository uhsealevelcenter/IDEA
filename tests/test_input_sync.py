import sys
import unittest
from pathlib import Path
from unittest.mock import Mock, patch


LANGGRAPH_DIR = Path(__file__).resolve().parents[1] / "langgraph"
sys.path.insert(0, str(LANGGRAPH_DIR))

from agents import terminal_agent  # noqa: E402


class InputSyncTests(unittest.TestCase):
    def make_agent(
        self,
        files=None,
        authorization: str | None = "Bearer user-token",
    ):
        agent = terminal_agent.TerminalAgent.__new__(
            terminal_agent.TerminalAgent
        )
        agent.sandbox_id = "user-1"
        agent.attached_files = list(files or [])
        agent.openwebui_authorization = authorization
        return agent

    @staticmethod
    def response(*, json_data=None, chunks=None):
        response = Mock()
        response.raise_for_status.return_value = None
        response.json.return_value = json_data
        response.iter_content.return_value = iter(chunks or [])
        return response

    def test_authorizes_and_copies_binary_attachment(self):
        metadata_response = self.response(json_data={
            "id": "file-123",
            "filename": "stored-name",
            "meta": {
                "name": "../../observations.nc",
                "size": 8,
                "content_type": "application/x-netcdf",
            },
        })
        content_response = self.response(
            chunks=[b"CDF\x01", b"\x00\xffAB"],
        )
        written = {}

        def write(filepath, chunks, session_id, expected_size, timeout):
            written["filepath"] = filepath
            written["data"] = b"".join(chunks)
            written["session_id"] = session_id
            written["expected_size"] = expected_size
            return len(written["data"])

        with (
            patch.object(
                terminal_agent.requests,
                "get",
                side_effect=[metadata_response, content_response],
            ) as get,
            patch.object(terminal_agent, "file_exists", return_value=False),
            patch.object(
                terminal_agent,
                "write_file_stream",
                side_effect=write,
            ),
        ):
            synced = self.make_agent(
                [{"id": "file-123", "name": "untrusted-name"}]
            )._sync_inputs_from_openwebui()

        expected_path = (
            "/workspace/uploads/file-123/observations.nc"
        )
        self.assertEqual(written["filepath"], expected_path)
        self.assertEqual(written["data"], b"CDF\x01\x00\xffAB")
        self.assertEqual(written["session_id"], "user-1")
        self.assertEqual(written["expected_size"], 8)
        self.assertEqual(synced[0]["sandbox_path"], expected_path)
        self.assertEqual(
            get.call_args_list[0].kwargs["headers"],
            {"Authorization": "Bearer user-token"},
        )
        self.assertTrue(get.call_args_list[1].kwargs["stream"])
        content_response.close.assert_called_once_with()

    def test_reauthorizes_but_reuses_existing_sandbox_copy(self):
        metadata_response = self.response(json_data={
            "id": "file-123",
            "filename": "data.csv",
            "meta": {"name": "data.csv", "size": 4},
        })
        with (
            patch.object(
                terminal_agent.requests,
                "get",
                return_value=metadata_response,
            ) as get,
            patch.object(terminal_agent, "file_exists", return_value=True),
            patch.object(terminal_agent, "write_file_stream") as write,
        ):
            synced = self.make_agent(
                [{"id": "file-123"}]
            )._sync_inputs_from_openwebui()

        get.assert_called_once()
        write.assert_not_called()
        self.assertEqual(
            synced[0]["sandbox_path"],
            "/workspace/uploads/file-123/data.csv",
        )

    def test_refuses_attachments_without_current_user_credential(self):
        with (
            patch.object(terminal_agent.requests, "get") as get,
            patch.object(terminal_agent, "write_file_stream") as write,
        ):
            with self.assertRaisesRegex(
                RuntimeError,
                "credential was not forwarded",
            ):
                self.make_agent(
                    [{"id": "file-123"}],
                    authorization=None,
                )._sync_inputs_from_openwebui()

        get.assert_not_called()
        write.assert_not_called()

    def test_rejects_metadata_size_above_configured_limit(self):
        metadata_response = self.response(json_data={
            "id": "file-123",
            "filename": "large.bin",
            "meta": {"name": "large.bin", "size": 11},
        })
        with (
            patch.object(
                terminal_agent.requests,
                "get",
                return_value=metadata_response,
            ) as get,
            patch.object(
                terminal_agent,
                "INPUT_SYNC_MAX_FILE_BYTES",
                10,
            ),
            patch.object(terminal_agent, "file_exists") as exists,
        ):
            with self.assertRaisesRegex(RuntimeError, "input limit"):
                self.make_agent(
                    [{"id": "file-123"}]
                )._sync_inputs_from_openwebui()

        get.assert_called_once()
        exists.assert_not_called()

    def test_context_names_exact_private_sandbox_paths(self):
        context = terminal_agent._attached_files_context([
            {
                "sandbox_path":
                    "/workspace/uploads/file-123/observations.nc",
            }
        ])

        self.assertIn(
            "`/workspace/uploads/file-123/observations.nc`",
            context,
        )
        self.assertIn("Use these exact paths", context)


if __name__ == "__main__":
    unittest.main()
