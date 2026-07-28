import importlib.util
import unittest
from pathlib import Path


MODULE_PATH = (
    Path(__file__).resolve().parents[1]
    / "langgraph"
    / "utils"
    / "output_sync.py"
)
SPEC = importlib.util.spec_from_file_location("output_sync", MODULE_PATH)
output_sync = importlib.util.module_from_spec(SPEC)
assert SPEC.loader is not None
SPEC.loader.exec_module(output_sync)


class OutputChangeTests(unittest.TestCase):
    def test_parses_file_metadata_snapshot(self):
        self.assertEqual(
            output_sync.parse_file_metadata_output(
                "/outputs/a.csv\t10\t100.0\n"
                "/outputs/nested/b.png\t20\t200.0\n"
            ),
            {
                "/outputs/a.csv": "10:100.0",
                "/outputs/nested/b.png": "20:200.0",
            },
        )

    def test_returns_only_new_or_modified_files(self):
        before = {
            "/outputs/unchanged.txt": "5:50.0",
            "/outputs/modified.csv": "9:90.0",
            "/outputs/deleted.txt": "7:70.0",
        }
        after = {
            "/outputs/unchanged.txt": "5:50.0",
            "/outputs/modified.csv": "10:100.0",
            "/outputs/new.png": "20:200.0",
        }

        self.assertEqual(
            output_sync.changed_output_paths(before, after),
            [
                "/outputs/modified.csv",
                "/outputs/new.png",
            ],
        )

    def test_extracts_and_normalizes_referenced_output_paths(self):
        content = (
            "[report](sandbox:/outputs/reports/final.html)\n"
            "[encoded](/outputs/reports/plot%20one.png)\n"
            "sandbox:/outputs/data.csv\n"
            "[escape](sandbox:/outputs/../../etc/passwd)"
        )

        self.assertEqual(
            output_sync.referenced_output_paths(content),
            {
                "/outputs/reports/final.html",
                "/outputs/reports/plot one.png",
                "/outputs/data.csv",
            },
        )

    def test_rejects_paths_outside_outputs(self):
        self.assertIsNone(
            output_sync.normalize_output_path("/outputs/../workspace/private")
        )
        self.assertIsNone(
            output_sync.normalize_output_path("/workspace/private")
        )


if __name__ == "__main__":
    unittest.main()
