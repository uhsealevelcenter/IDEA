import json
import sys
import unittest
from datetime import datetime, timezone
from io import StringIO
from pathlib import Path

import pandas as pd


LANGGRAPH_DIR = Path(__file__).resolve().parents[1] / "langgraph"
sys.path.insert(0, str(LANGGRAPH_DIR))

from utils.tools import climate_tool  # noqa: E402


class ClimateToolTests(unittest.TestCase):
    def test_parses_cpc_seasons_to_middle_month_and_deduplicates(self):
        parsed = climate_tool._parse_cpc_oni_like(
            """SEAS YR TOTAL ANOM
DJF 2024 27.1 0.7
JFM 2024 27.2 -99.9
DJF 2024 27.3 0.8
"""
        )

        self.assertEqual(
            parsed["time"].dt.strftime("%Y-%m-%d").tolist(),
            ["2024-01-15", "2024-02-15"],
        )
        self.assertEqual(parsed["value"].iloc[0], 0.8)
        self.assertTrue(pd.isna(parsed["value"].iloc[1]))

    def test_normalizes_names_and_rejects_unsafe_output_paths(self):
        self.assertEqual(
            climate_tool.normalize_climate_index_names(
                ["roni", " ONI ", "RONI"]
            ),
            ["RONI", "ONI"],
        )
        self.assertEqual(
            climate_tool.normalize_climate_output_path(
                "/workspace/enso/indices.csv"
            ),
            (
                "/workspace/enso/indices.csv",
                "/workspace/enso/indices.provenance.json",
            ),
        )

        for path in (
            "/outputs/indices.csv",
            "/workspace/../outputs/indices.csv",
            "/workspace/uploads/file-123/input.csv",
            "/workspace/indices.json",
        ):
            with self.subTest(path=path), self.assertRaises(ValueError):
                climate_tool.normalize_climate_output_path(path)

        with self.assertRaises(ValueError):
            climate_tool.normalize_climate_index_names(["unsupported"])

    def test_builds_batched_long_csv_and_provenance(self):
        frames = {
            "RONI": pd.DataFrame({
                "time": pd.to_datetime(
                    ["2024-01-15", "2024-02-15", "2024-03-15"]
                ),
                "value": [0.4, None, 0.2],
            }),
            "ONI": pd.DataFrame({
                "time": pd.to_datetime(
                    ["2024-02-15", "2024-03-15", "2024-04-15"]
                ),
                "value": [0.7, 0.5, 0.3],
            }),
        }
        calls = []

        def fetch(name):
            calls.append(name)
            return frames[name]

        csv_bytes, provenance_bytes, metadata = (
            climate_tool.build_climate_indices_bundle(
                ["roni", "ONI"],
                "/workspace/enso/roni_oni.csv",
                fetch_index=fetch,
                retrieved_at=datetime(
                    2026, 7, 29, 12, 0, tzinfo=timezone.utc
                ),
            )
        )

        self.assertEqual(calls, ["RONI", "ONI"])
        data = pd.read_csv(StringIO(csv_bytes.decode("utf-8")))
        self.assertEqual(data.columns.tolist(), ["time", "index", "value"])
        self.assertEqual(len(data), 6)
        self.assertEqual(set(data["index"]), {"RONI", "ONI"})
        self.assertEqual(
            metadata["common_period"],
            {"start": "2024-02-15", "end": "2024-03-15"},
        )
        self.assertEqual(metadata["total_rows"], 6)
        self.assertEqual(metadata["indices"][0]["missing_values"], 1)
        self.assertEqual(
            metadata["dataset_path"],
            "/workspace/enso/roni_oni.csv",
        )
        self.assertEqual(
            json.loads(provenance_bytes),
            metadata,
        )
        self.assertNotIn("0.4", json.dumps(metadata))

    def test_tool_writes_bytes_and_returns_only_compact_metadata(self):
        writes = {}

        def write_bytes(path, data):
            writes[path] = data
            return len(data)

        tool = climate_tool.make_get_climate_indices_tool(write_bytes)
        self.assertEqual(tool.name, "get_climate_indices_tool")
        frame = pd.DataFrame({
            "time": pd.to_datetime(["2024-01-15", "2024-02-15"]),
            "value": [0.4, 0.3],
        })
        original_fetch = climate_tool._get_climate_index_dataframe
        climate_tool._get_climate_index_dataframe = lambda name: frame
        try:
            result = json.loads(tool.invoke({
                "climate_index_names": ["RONI", "ONI"],
                "output_path": "/workspace/enso/indices.csv",
            }))
        finally:
            climate_tool._get_climate_index_dataframe = original_fetch

        self.assertEqual(
            set(writes),
            {
                "/workspace/enso/indices.csv",
                "/workspace/enso/indices.provenance.json",
            },
        )
        self.assertEqual(result["total_rows"], 4)
        self.assertNotIn("Full CSV data", json.dumps(result))
        self.assertNotIn("2024-01-15,ONI,0.4", json.dumps(result))

    def test_rejects_empty_duplicate_or_malformed_source_data(self):
        cases = {
            "empty": pd.DataFrame(columns=["time", "value"]),
            "duplicate": pd.DataFrame({
                "time": pd.to_datetime(["2024-01-15", "2024-01-15"]),
                "value": [0.1, 0.2],
            }),
            "malformed": pd.DataFrame({"date": ["2024-01-15"]}),
        }
        for name, frame in cases.items():
            with self.subTest(name=name), self.assertRaises(ValueError):
                climate_tool.build_climate_indices_bundle(
                    ["RONI"],
                    "/workspace/roni.csv",
                    fetch_index=lambda _index, frame=frame: frame,
                )


if __name__ == "__main__":
    unittest.main()
