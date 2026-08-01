import importlib.util
import json
import sys
import tempfile
import types
import unittest
from pathlib import Path
from unittest import mock


REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
SYNC_PATH = REPOSITORY_ROOT / "shared_data" / "sync_shared_data.py"
SYNC_SPEC = importlib.util.spec_from_file_location("sync_shared_data", SYNC_PATH)
sync = importlib.util.module_from_spec(SYNC_SPEC)
assert SYNC_SPEC.loader is not None
sys.modules[SYNC_SPEC.name] = sync
SYNC_SPEC.loader.exec_module(sync)

MSB_PATH = REPOSITORY_ROOT / "sandbox_service" / "msb_sandbox.py"
MSB_SPEC = importlib.util.spec_from_file_location("msb_sandbox_shared_data", MSB_PATH)
msb = importlib.util.module_from_spec(MSB_SPEC)
assert MSB_SPEC.loader is not None
MSB_SPEC.loader.exec_module(msb)


class SharedDataTests(unittest.TestCase):
    def setUp(self):
        self.datasets = sync.load_manifest(
            REPOSITORY_ROOT / "shared_data" / "manifest.toml"
        )

    def test_manifest_allowlists_only_requested_legacy_data(self):
        self.assertEqual(
            [dataset.name for dataset in self.datasets],
            ["metadata", "benchmarks", "altimetry", "insight"],
        )
        paths = {dataset.relative_path.parts[0] for dataset in self.datasets}
        self.assertEqual(paths, {"metadata", "benchmarks", "altimetry", "InSight"})
        self.assertTrue(
            {
                "papers",
                "HCDP",
                "SJW",
                ".pqa",
                "prompts",
            }.isdisjoint(paths)
        )

    def test_import_copies_only_manifest_paths(self):
        with tempfile.TemporaryDirectory() as source_dir, tempfile.TemporaryDirectory() as target_dir:
            source = Path(source_dir)
            target = Path(target_dir)
            (source / "metadata").mkdir()
            (source / "metadata" / "fd_metadata.geojson").write_text(
                json.dumps(
                    {
                        "type": "FeatureCollection",
                        "features": [{"type": "Feature", "properties": {}}],
                    }
                ),
                encoding="utf-8",
            )
            (source / "papers").mkdir()
            (source / "papers" / "private.pdf").write_bytes(b"not shared")

            metadata = sync.select_datasets(self.datasets, ["metadata"])
            sync.import_datasets(metadata, source, target)

            self.assertTrue((target / "metadata" / "fd_metadata.geojson").is_file())
            self.assertFalse((target / "papers").exists())

    def test_directory_import_uses_release_symlink(self):
        with tempfile.TemporaryDirectory() as source_dir, tempfile.TemporaryDirectory() as target_dir:
            source = Path(source_dir)
            target = Path(target_dir)
            (source / "InSight").mkdir()
            (source / "InSight" / "ps_calib_0001_01.csv").write_text(
                "UTC,PRESSURE\n2018-001T00:00:00.000Z,700\n",
                encoding="utf-8",
            )

            insight = sync.select_datasets(self.datasets, ["insight"])
            sync.import_datasets(insight, source, target)

            installed = target / "InSight"
            self.assertTrue(installed.is_symlink())
            self.assertTrue((installed / "ps_calib_0001_01.csv").is_file())

    def test_status_detects_checksum_mismatch(self):
        with tempfile.TemporaryDirectory() as source_dir, tempfile.TemporaryDirectory() as target_dir:
            source = Path(source_dir)
            target = Path(target_dir)
            (source / "metadata").mkdir()
            source_file = source / "metadata" / "fd_metadata.geojson"
            source_file.write_text(
                json.dumps(
                    {
                        "type": "FeatureCollection",
                        "features": [{"type": "Feature", "properties": {"version": 1}}],
                    }
                ),
                encoding="utf-8",
            )
            metadata = sync.select_datasets(self.datasets, ["metadata"])
            sync.import_datasets(metadata, source, target)
            (target / "metadata" / "fd_metadata.geojson").write_text(
                json.dumps(
                    {
                        "type": "FeatureCollection",
                        "features": [{"type": "Feature", "properties": {"version": 2}}],
                    }
                ),
                encoding="utf-8",
            )

            self.assertFalse(sync.status(metadata, target))

    def test_shared_mount_is_read_only_and_hardened(self):
        calls = []

        class FakeVolume:
            @staticmethod
            def bind(path, **kwargs):
                calls.append((path, kwargs))
                return {"path": path, **kwargs}

        with tempfile.TemporaryDirectory() as shared_dir:
            for relative_path in msb.SHARED_DATA_REQUIRED_PATHS:
                path = Path(shared_dir) / relative_path
                if relative_path == "InSight":
                    path.mkdir(parents=True)
                else:
                    path.parent.mkdir(parents=True, exist_ok=True)
                    path.touch()
            fake_module = types.SimpleNamespace(Volume=FakeVolume)
            with mock.patch.dict(sys.modules, {"microsandbox": fake_module}):
                volumes = msb.shared_data_volumes(shared_dir)

        self.assertEqual(set(volumes), {"/app/data"})
        self.assertEqual(calls[0][0], str(Path(shared_dir).resolve()))
        self.assertEqual(
            calls[0][1],
            {
                "readonly": True,
                "noexec": True,
                "nosuid": True,
                "nodev": True,
            },
        )

    def test_shared_mount_can_be_disabled_for_standalone_use(self):
        self.assertEqual(msb.shared_data_volumes(""), {})

    def test_shared_mount_rejects_an_uninitialized_volume(self):
        with tempfile.TemporaryDirectory() as shared_dir:
            with self.assertRaisesRegex(RuntimeError, "not initialized"):
                msb.shared_data_volumes(shared_dir)


if __name__ == "__main__":
    unittest.main()
