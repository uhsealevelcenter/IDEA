import sys
import unittest
from pathlib import Path


LANGGRAPH_DIR = Path(__file__).resolve().parents[1] / "langgraph"
sys.path.insert(0, str(LANGGRAPH_DIR))

from tools import persistent_terminal  # noqa: E402


class PublishArtifactTests(unittest.TestCase):
    def test_preserves_workspace_relative_path_by_default(self):
        self.assertEqual(
            persistent_terminal.normalize_publish_paths(
                "/workspace/project/report.html"
            ),
            (
                "/workspace/project/report.html",
                "/outputs/project/report.html",
            ),
        )

    def test_accepts_explicit_output_destination(self):
        self.assertEqual(
            persistent_terminal.normalize_publish_paths(
                "/workspace/project/report.html",
                "/outputs/final/page.html",
            ),
            (
                "/workspace/project/report.html",
                "/outputs/final/page.html",
            ),
        )

    def test_rejects_source_outside_workspace(self):
        with self.assertRaises(ValueError):
            persistent_terminal.normalize_publish_paths(
                "/workspace/../etc/passwd"
            )

    def test_rejects_destination_outside_outputs(self):
        with self.assertRaises(ValueError):
            persistent_terminal.normalize_publish_paths(
                "/workspace/report.html",
                "/outputs/../workspace/report.html",
            )


if __name__ == "__main__":
    unittest.main()
