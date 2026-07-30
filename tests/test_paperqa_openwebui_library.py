import importlib.util
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch


REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
SCRIPT_PATH = (
    REPOSITORY_ROOT
    / "langgraph"
    / "utils"
    / "pqa"
    / "openwebui_library.py"
)
SPEC = importlib.util.spec_from_file_location(
    "openwebui_library", SCRIPT_PATH
)
library = importlib.util.module_from_spec(SPEC)
assert SPEC.loader is not None
sys.modules[SPEC.name] = library
SPEC.loader.exec_module(library)


def json_response(payload):
    response = Mock()
    response.raise_for_status.return_value = None
    response.json.return_value = payload
    return response


def content_response(content: bytes):
    response = Mock()
    response.raise_for_status.return_value = None
    response.iter_content.return_value = iter([content])
    return response


class PaperQAOpenWebUILibraryTests(unittest.TestCase):
    def setUp(self):
        self.temporary_directory = tempfile.TemporaryDirectory()
        root = Path(self.temporary_directory.name)
        self.papers_root = root / "papers"
        self.state_root = root / "state"
        self.patchers = [
            patch.object(library, "PAPERS_ROOT", self.papers_root),
            patch.object(
                library, "LIBRARY_STATE_ROOT", self.state_root
            ),
            patch.object(
                library, "OPENWEBUI_BASE_URL", "http://openwebui:8080"
            ),
        ]
        for patcher in self.patchers:
            patcher.start()

    def tearDown(self):
        for patcher in reversed(self.patchers):
            patcher.stop()
        self.temporary_directory.cleanup()

    @staticmethod
    def get_response(url, **kwargs):
        if "/knowledge/literature/files" in url:
            return json_response({
                "items": [{"id": "collection-pdf"}],
                "total": 1,
            })
        if url.endswith("/files/collection-pdf"):
            return json_response({
                "id": "collection-pdf",
                "hash": "collection-v1",
                "meta": {
                    "name": "paper.pdf",
                    "content_type": "application/pdf",
                    "size": 14,
                },
            })
        if url.endswith("/files/direct-pdf"):
            return json_response({
                "id": "direct-pdf",
                "hash": "direct-v1",
                "meta": {
                    "name": "supplement.pdf",
                    "content_type": "application/pdf",
                    "size": 10,
                },
            })
        if url.endswith("/files/collection-pdf/content"):
            return content_response(b"%PDF-collection")
        if url.endswith("/files/direct-pdf/content"):
            return content_response(b"%PDF-chat")
        raise AssertionError(f"Unexpected URL: {url}")

    def test_authorizes_collection_and_keeps_direct_pdf_chat_scoped(self):
        with patch.object(
            library.requests,
            "get",
            side_effect=self.get_response,
        ) as get:
            first = library.prepare_paperqa_library(
                user_id="user-1",
                assistant_id="sea",
                session_id="chat-1",
                resources=[
                    {"type": "collection", "id": "literature"},
                    {"type": "file", "id": "direct-pdf"},
                ],
                authorization="Bearer current-user",
            )
            second = library.prepare_paperqa_library(
                user_id="user-1",
                assistant_id="sea",
                session_id="chat-1",
                resources=[
                    {"type": "collection", "id": "literature"},
                ],
                authorization="Bearer current-user",
            )

        self.assertEqual(first.scope_id, second.scope_id)
        self.assertEqual(first.paper_count, 2)
        self.assertEqual(second.paper_count, 2)
        self.assertEqual(second.direct_file_ids, ("direct-pdf",))
        for call in get.call_args_list:
            self.assertEqual(
                call.kwargs["headers"],
                {"Authorization": "Bearer current-user"},
            )

        other_chat = library.prepare_paperqa_library
        with patch.object(
            library.requests,
            "get",
            side_effect=self.get_response,
        ):
            isolated = other_chat(
                user_id="user-1",
                assistant_id="sea",
                session_id="chat-2",
                resources=[
                    {"type": "collection", "id": "literature"},
                ],
                authorization="Bearer current-user",
            )
        self.assertNotEqual(first.scope_id, isolated.scope_id)
        self.assertEqual(isolated.paper_count, 1)
        self.assertEqual(isolated.direct_file_ids, ())

    def test_rejects_missing_user_authorization(self):
        with self.assertRaisesRegex(
            RuntimeError, "credential was not forwarded"
        ):
            library.prepare_paperqa_library(
                user_id="user-1",
                assistant_id="sea",
                session_id="chat-1",
                resources=[],
                authorization="",
            )


if __name__ == "__main__":
    unittest.main()
