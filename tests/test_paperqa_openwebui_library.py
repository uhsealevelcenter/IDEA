import importlib.util
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
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
        self.assertIsNotNone(second.direct_scope_id)
        self.assertEqual(
            second.direct_file_names,
            ("supplement.pdf",),
        )
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
        self.assertIsNone(isolated.direct_scope_id)
        self.assertEqual(isolated.direct_file_names, ())

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

    def test_recognizes_supported_literature_formats(self):
        cases = {
            "paper.PDF": ("application/pdf", "pdf"),
            "paper.docx": (
                "application/vnd.openxmlformats-officedocument."
                "wordprocessingml.document",
                "docx",
            ),
            "paper.doc": ("application/msword", "doc"),
            "paper.odt": (
                "application/vnd.oasis.opendocument.text",
                "odt",
            ),
            "paper.rtf": ("text/rtf; charset=binary", "rtf"),
            "unknown-name": ("application/msword", "doc"),
        }
        for name, (content_type, expected) in cases.items():
            with self.subTest(name=name):
                self.assertEqual(
                    library._document_format({
                        "meta": {
                            "name": name,
                            "content_type": content_type,
                        },
                    }),
                    expected,
                )

        self.assertIsNone(library._document_format({
            "meta": {
                "name": "not-really.pdf",
                "content_type": "application/msword",
            },
        }))
        self.assertIsNone(library._document_format({
            "meta": {
                "name": "notes.txt",
                "content_type": "text/plain",
            },
        }))

    def test_converts_docx_to_validated_pdf_and_reuses_it(self):
        metadata = {
            "id": "word-file",
            "hash": "word-v1",
            "meta": {
                "name": "study.docx",
                "content_type": (
                    "application/vnd.openxmlformats-officedocument."
                    "wordprocessingml.document"
                ),
                "size": 12,
            },
        }

        def get_response(url, **kwargs):
            if url.endswith("/files/word-file"):
                return json_response(metadata)
            if url.endswith("/files/word-file/content"):
                return content_response(b"docx-content")
            raise AssertionError(f"Unexpected URL: {url}")

        def convert(command, **kwargs):
            output_dir = Path(command[command.index("--outdir") + 1])
            (output_dir / "source.pdf").write_bytes(b"%PDF-converted")
            return SimpleNamespace(returncode=0, stdout="converted")

        with (
            patch.object(library.requests, "get", side_effect=get_response),
            patch.object(
                library.subprocess,
                "run",
                side_effect=convert,
            ) as run,
        ):
            first = library.prepare_paperqa_library(
                user_id="user-1",
                assistant_id="sea",
                session_id="chat-1",
                resources=[{"type": "file", "id": "word-file"}],
                authorization="Bearer current-user",
            )
            second = library.prepare_paperqa_library(
                user_id="user-1",
                assistant_id="sea",
                session_id="chat-1",
                resources=[{"type": "file", "id": "word-file"}],
                authorization="Bearer current-user",
            )

        self.assertEqual(first.paper_count, 1)
        self.assertEqual(first.direct_file_names, ("study.docx",))
        self.assertEqual(first.warnings, ())
        self.assertEqual(second.paper_count, 1)
        run.assert_called_once()
        command = run.call_args.args[0]
        self.assertEqual(command[0], library.LIBREOFFICE_BINARY)
        self.assertIn("--headless", command)
        self.assertIn("--safe-mode", command)
        self.assertIn("pdf:writer_pdf_Export", command)
        self.assertNotIn("shell", run.call_args.kwargs)

        state = library._read_state(first.direct_scope_id)
        record = state["files"]["word-file"]
        self.assertEqual(record["name"], "study.docx")
        self.assertEqual(record["source_format"], "docx")
        self.assertEqual(
            record["representation_version"],
            library.PQA_DOCUMENT_REPRESENTATION_VERSION,
        )
        self.assertNotEqual(record["source_sha256"], record["sha256"])

    def test_reports_unsupported_document_without_hiding_valid_pdf(self):
        def get_response(url, **kwargs):
            if url.endswith("/files/paper"):
                return json_response({
                    "hash": "pdf-v1",
                    "meta": {
                        "name": "paper.pdf",
                        "content_type": "application/pdf",
                    },
                })
            if url.endswith("/files/table"):
                return json_response({
                    "hash": "csv-v1",
                    "meta": {
                        "name": "table.csv",
                        "content_type": "text/csv",
                    },
                })
            if url.endswith("/files/paper/content"):
                return content_response(b"%PDF-valid")
            raise AssertionError(f"Unexpected URL: {url}")

        with patch.object(
            library.requests,
            "get",
            side_effect=get_response,
        ):
            result = library.prepare_paperqa_library(
                user_id="user-1",
                assistant_id="sea",
                session_id="chat-1",
                resources=[
                    {"type": "file", "id": "paper"},
                    {"type": "file", "id": "table"},
                ],
                authorization="Bearer current-user",
            )

        self.assertEqual(result.paper_count, 1)
        self.assertEqual(result.direct_file_ids, ("paper",))
        self.assertEqual(len(result.warnings), 1)
        self.assertIn("table.csv", result.warnings[0])
        self.assertIn("Supported types", result.warnings[0])

    def test_failed_reconversion_does_not_restore_stale_direct_file(self):
        metadata = {
            "hash": "word-v1",
            "meta": {
                "name": "study.docx",
                "content_type": (
                    "application/vnd.openxmlformats-officedocument."
                    "wordprocessingml.document"
                ),
            },
        }

        def get_response(url, **kwargs):
            if url.endswith("/files/word-file"):
                return json_response(metadata)
            if url.endswith("/files/word-file/content"):
                return content_response(b"docx-content")
            raise AssertionError(f"Unexpected URL: {url}")

        def convert(command, **kwargs):
            output_dir = Path(command[command.index("--outdir") + 1])
            (output_dir / "source.pdf").write_bytes(b"%PDF-converted")
            return SimpleNamespace(returncode=0, stdout="converted")

        resources = [{"type": "file", "id": "word-file"}]
        with (
            patch.object(library.requests, "get", side_effect=get_response),
            patch.object(library.subprocess, "run", side_effect=convert),
        ):
            first = library.prepare_paperqa_library(
                user_id="user-1",
                assistant_id="sea",
                session_id="chat-1",
                resources=resources,
                authorization="Bearer current-user",
            )

        metadata["hash"] = "word-v2"
        with (
            patch.object(library.requests, "get", side_effect=get_response),
            patch.object(
                library.subprocess,
                "run",
                return_value=SimpleNamespace(returncode=1, stdout="bad"),
            ),
        ):
            second = library.prepare_paperqa_library(
                user_id="user-1",
                assistant_id="sea",
                session_id="chat-1",
                resources=resources,
                authorization="Bearer current-user",
            )

        self.assertEqual(second.paper_count, 0)
        self.assertEqual(second.direct_file_ids, ())
        self.assertIn("could not convert", second.warnings[0])
        self.assertEqual(
            library._read_state(first.direct_scope_id)["files"],
            {},
        )

    def test_rejects_converter_output_that_is_not_a_pdf(self):
        def convert(command, **kwargs):
            output_dir = Path(command[command.index("--outdir") + 1])
            (output_dir / "source.pdf").write_bytes(b"not a pdf")
            return SimpleNamespace(returncode=0, stdout="converted")

        with (
            patch.object(
                library.requests,
                "get",
                return_value=content_response(b"docx-content"),
            ),
            patch.object(library.subprocess, "run", side_effect=convert),
        ):
            with self.assertRaisesRegex(RuntimeError, "valid PDF"):
                library._materialize_pdf(
                    "word-file",
                    "docx",
                    self.papers_root / "scope" / "paper.pdf",
                    {"Authorization": "Bearer current-user"},
                    library.time.monotonic() + 10,
                )

    def test_converter_timeout_is_reported_safely(self):
        with patch.object(
            library.subprocess,
            "run",
            side_effect=library.subprocess.TimeoutExpired("soffice", 1),
        ):
            with self.assertRaisesRegex(RuntimeError, "conversion timed out"):
                with tempfile.TemporaryDirectory() as directory:
                    root = Path(directory)
                    source = root / "source.docx"
                    source.write_bytes(b"docx-content")
                    library._convert_to_pdf(
                        source,
                        root,
                        library.time.monotonic() + 10,
                    )

    def test_advertised_oversized_document_is_not_downloaded(self):
        metadata = {
            "hash": "word-v1",
            "meta": {
                "name": "large.docx",
                "content_type": (
                    "application/vnd.openxmlformats-officedocument."
                    "wordprocessingml.document"
                ),
                "size": 11,
            },
        }

        with (
            patch.object(
                library.requests,
                "get",
                return_value=json_response(metadata),
            ) as get,
            patch.object(library, "PQA_MAX_DOCUMENT_BYTES", 10),
        ):
            result = library.prepare_paperqa_library(
                user_id="user-1",
                assistant_id="sea",
                session_id="chat-1",
                resources=[{"type": "file", "id": "word-file"}],
                authorization="Bearer current-user",
            )

        self.assertEqual(result.paper_count, 0)
        self.assertIn("exceeds", result.warnings[0])
        self.assertEqual(get.call_count, 1)


if __name__ == "__main__":
    unittest.main()
