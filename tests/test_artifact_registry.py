import sys
import unittest
from pathlib import Path
from unittest.mock import MagicMock, Mock


LANGGRAPH_DIR = Path(__file__).resolve().parents[1] / "langgraph"
sys.path.insert(0, str(LANGGRAPH_DIR))

from utils.artifact_registry import ArtifactRecord, ArtifactRegistry  # noqa: E402


class ArtifactRegistryTests(unittest.TestCase):
    def test_reads_valid_records_and_ignores_corrupt_entries(self):
        record = ArtifactRecord(
            sandbox_path="/outputs/page.html",
            signature="10:200.0",
            openwebui_file_id="file-id",
            display_name="page.html",
            uploaded_at="2026-07-27T00:00:00+00:00",
        )
        client = Mock()
        client.hmget.return_value = [record.to_json(), "not-json", None]
        registry = ArtifactRegistry("user-1", client=client)

        self.assertEqual(
            registry.get_many([
                "/outputs/page.html",
                "/outputs/corrupt.html",
                "/outputs/missing.html",
            ]),
            {"/outputs/page.html": record},
        )

    def test_upsert_does_not_replace_newer_mapping(self):
        newer = ArtifactRecord(
            sandbox_path="/outputs/page.html",
            signature="10:300.0",
            openwebui_file_id="newer-id",
            display_name="page.html",
            uploaded_at="2026-07-27T00:00:00+00:00",
        )
        pipeline = MagicMock()
        pipeline.__enter__.return_value = pipeline
        pipeline.hget.return_value = newer.to_json()
        client = Mock()
        client.pipeline.return_value = pipeline
        registry = ArtifactRegistry("user-1", client=client)

        updated = registry.upsert(
            "/outputs/page.html",
            "10:200.0",
            "older-id",
            "page.html",
        )

        self.assertFalse(updated)
        pipeline.unwatch.assert_called_once()
        pipeline.hset.assert_not_called()


if __name__ == "__main__":
    unittest.main()
