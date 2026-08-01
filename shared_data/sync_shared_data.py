#!/usr/bin/env python3
"""Import, update, validate, and report on IDEA's shared scientific data."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
import tomllib
import urllib.request
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any


SCRIPT_DIR = Path(__file__).resolve().parent
DEFAULT_MANIFEST = SCRIPT_DIR / "manifest.toml"
DEFAULT_ROOT = Path(os.getenv("SHARED_DATA_ROOT", "/app/data"))
STATE_FILE = ".idea-shared-data-state.json"


class SharedDataError(RuntimeError):
    pass


@dataclass(frozen=True)
class Dataset:
    name: str
    relative_path: Path
    kind: str
    required: bool
    source_url: str | None
    required_variables: tuple[str, ...]
    required_globs: tuple[str, ...]


def load_manifest(path: Path) -> list[Dataset]:
    with path.open("rb") as handle:
        raw = tomllib.load(handle)
    if raw.get("schema_version") != 1:
        raise SharedDataError(f"Unsupported manifest schema in {path}")

    datasets: list[Dataset] = []
    seen: set[str] = set()
    for item in raw.get("datasets", []):
        name = str(item["name"])
        relative_path = Path(str(item["relative_path"]))
        if name in seen:
            raise SharedDataError(f"Duplicate dataset name: {name}")
        if relative_path.is_absolute() or ".." in relative_path.parts:
            raise SharedDataError(f"Unsafe relative_path for {name}: {relative_path}")
        seen.add(name)
        datasets.append(
            Dataset(
                name=name,
                relative_path=relative_path,
                kind=str(item["kind"]),
                required=bool(item.get("required", False)),
                source_url=item.get("source_url"),
                required_variables=tuple(item.get("required_variables", [])),
                required_globs=tuple(item.get("required_globs", [])),
            )
        )
    if not datasets:
        raise SharedDataError(f"No datasets defined in {path}")
    return datasets


def select_datasets(datasets: list[Dataset], names: list[str]) -> list[Dataset]:
    if not names or names == ["all"]:
        return datasets
    by_name = {dataset.name: dataset for dataset in datasets}
    unknown = sorted(set(names) - set(by_name))
    if unknown:
        raise SharedDataError(f"Unknown dataset(s): {', '.join(unknown)}")
    return [by_name[name] for name in names]


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def directory_fingerprint(path: Path) -> tuple[str, int, int]:
    digest = hashlib.sha256()
    total_bytes = 0
    count = 0
    for candidate in sorted(item for item in path.rglob("*") if item.is_file()):
        relative = candidate.relative_to(path).as_posix()
        digest.update(relative.encode("utf-8"))
        digest.update(b"\0")
        file_hash = sha256_file(candidate)
        digest.update(file_hash.encode("ascii"))
        total_bytes += candidate.stat().st_size
        count += 1
    return digest.hexdigest(), total_bytes, count


def validate_geojson(path: Path) -> None:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise SharedDataError(f"{path} is not valid JSON: {exc}") from exc
    if not isinstance(payload, dict) or payload.get("type") != "FeatureCollection":
        raise SharedDataError(f"{path} is not a GeoJSON FeatureCollection")
    features = payload.get("features")
    if not isinstance(features, list) or not features:
        raise SharedDataError(f"{path} has no GeoJSON features")


def validate_netcdf(path: Path, required_variables: tuple[str, ...]) -> None:
    ncdump = shutil.which("ncdump")
    if not ncdump:
        raise SharedDataError(
            "ncdump is required for NetCDF validation; use the shared-data "
            "maintenance container"
        )
    result = subprocess.run(
        [ncdump, "-h", str(path)],
        check=False,
        capture_output=True,
        text=True,
        timeout=120,
    )
    if result.returncode != 0:
        detail = (result.stderr or result.stdout).strip()
        raise SharedDataError(f"{path} is not a readable NetCDF file: {detail}")
    missing = [name for name in required_variables if name not in result.stdout]
    if missing:
        raise SharedDataError(
            f"{path} is missing required NetCDF variable(s): {', '.join(missing)}"
        )


def validate_directory(path: Path, required_globs: tuple[str, ...]) -> None:
    if not path.is_dir():
        raise SharedDataError(f"{path} is not a directory")
    files = [item for item in path.rglob("*") if item.is_file()]
    if not files:
        raise SharedDataError(f"{path} is empty")
    for pattern in required_globs:
        if not any(path.rglob(pattern)):
            raise SharedDataError(f"{path} contains no files matching {pattern}")
    empty = [item for item in files if item.stat().st_size == 0]
    if empty:
        raise SharedDataError(f"{path} contains empty file: {empty[0]}")


def validate_dataset(dataset: Dataset, path: Path) -> None:
    if not path.exists():
        raise SharedDataError(f"Missing {dataset.name}: {path}")
    if dataset.kind == "geojson":
        validate_geojson(path)
    elif dataset.kind == "netcdf":
        validate_netcdf(path, dataset.required_variables)
    elif dataset.kind == "directory":
        validate_directory(path, dataset.required_globs)
    else:
        raise SharedDataError(f"Unsupported dataset kind for {dataset.name}: {dataset.kind}")


def dataset_metadata(dataset: Dataset, path: Path, source: str) -> dict[str, Any]:
    if path.is_dir():
        digest, size, files = directory_fingerprint(path)
    else:
        digest, size, files = sha256_file(path), path.stat().st_size, 1
    return {
        "path": dataset.relative_path.as_posix(),
        "source": source,
        "sha256": digest,
        "bytes": size,
        "files": files,
        "updated_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
    }


def current_sha256(path: Path) -> str:
    if path.is_dir():
        digest, _, _ = directory_fingerprint(path)
        return digest
    return sha256_file(path)


def read_state(root: Path) -> dict[str, Any]:
    path = root / STATE_FILE
    if not path.exists():
        return {"schema_version": 1, "datasets": {}}
    try:
        state = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError):
        return {"schema_version": 1, "datasets": {}}
    if not isinstance(state, dict) or not isinstance(state.get("datasets"), dict):
        return {"schema_version": 1, "datasets": {}}
    return state


def write_state(root: Path, state: dict[str, Any]) -> None:
    root.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(prefix=".state-", dir=root)
    temporary_path = Path(temporary)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump(state, handle, indent=2, sort_keys=True)
            handle.write("\n")
        os.replace(temporary_path, root / STATE_FILE)
    finally:
        temporary_path.unlink(missing_ok=True)


def install_file(dataset: Dataset, source: Path, root: Path) -> Path:
    destination = root / dataset.relative_path
    destination.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(
        prefix=f".{destination.name}-", suffix=".part", dir=destination.parent
    )
    os.close(fd)
    temporary_path = Path(temporary)
    try:
        shutil.copyfile(source, temporary_path)
        validate_dataset(dataset, temporary_path)
        os.replace(temporary_path, destination)
    finally:
        temporary_path.unlink(missing_ok=True)
    return destination


def atomic_directory_link(destination: Path, release: Path) -> None:
    destination.parent.mkdir(parents=True, exist_ok=True)
    link = destination.parent / f".{destination.name}-{uuid.uuid4().hex}.link"
    link.symlink_to(os.path.relpath(release, destination.parent), target_is_directory=True)
    try:
        if destination.exists() and not destination.is_symlink():
            backup = destination.parent / f".{destination.name}-{uuid.uuid4().hex}.backup"
            destination.rename(backup)
            try:
                os.replace(link, destination)
            except Exception:
                backup.rename(destination)
                raise
            shutil.rmtree(backup)
        else:
            os.replace(link, destination)
    finally:
        link.unlink(missing_ok=True)


def prune_directory_releases(release_root: Path, keep: int = 2) -> None:
    releases = sorted(
        (item for item in release_root.iterdir() if item.is_dir()),
        key=lambda item: item.stat().st_mtime,
        reverse=True,
    )
    for old_release in releases[keep:]:
        shutil.rmtree(old_release)


def install_directory(dataset: Dataset, source: Path, root: Path) -> Path:
    release_root = root / ".releases" / dataset.name
    release_root.mkdir(parents=True, exist_ok=True)
    staging = release_root / f".staging-{uuid.uuid4().hex}"
    release = release_root / time.strftime("%Y%m%dT%H%M%SZ", time.gmtime())
    if release.exists():
        release = release_root / f"{release.name}-{uuid.uuid4().hex[:8]}"
    try:
        shutil.copytree(source, staging)
        validate_dataset(dataset, staging)
        os.replace(staging, release)
        atomic_directory_link(root / dataset.relative_path, release)
        prune_directory_releases(release_root)
    finally:
        if staging.exists():
            shutil.rmtree(staging)
    return root / dataset.relative_path


def install_from_path(dataset: Dataset, source: Path, root: Path) -> Path:
    if dataset.kind == "directory":
        return install_directory(dataset, source, root)
    return install_file(dataset, source, root)


def import_datasets(
    datasets: list[Dataset], source_root: Path, target_root: Path
) -> dict[str, Any]:
    source_root = source_root.resolve()
    state = read_state(target_root)
    for dataset in datasets:
        source = source_root / dataset.relative_path
        if not source.exists():
            if dataset.required:
                raise SharedDataError(f"Required source is missing: {source}")
            continue
        destination = install_from_path(dataset, source, target_root)
        state["datasets"][dataset.name] = dataset_metadata(
            dataset, destination, f"import:{source_root}"
        )
        write_state(target_root, state)
        print(f"Imported {dataset.name}: {destination}")
    return state


def download_dataset(dataset: Dataset, target_root: Path) -> dict[str, Any]:
    if not dataset.source_url:
        raise SharedDataError(f"{dataset.name} has no source_url in the manifest")
    with tempfile.TemporaryDirectory(prefix="idea-shared-data-") as temporary:
        download = Path(temporary) / Path(dataset.relative_path).name
        request = urllib.request.Request(
            dataset.source_url, headers={"User-Agent": "IDEA-shared-data/1.0"}
        )
        try:
            with urllib.request.urlopen(request, timeout=120) as response:
                with download.open("wb") as handle:
                    shutil.copyfileobj(response, handle, length=1024 * 1024)
        except Exception as exc:
            raise SharedDataError(
                f"Failed to download {dataset.name} from {dataset.source_url}: {exc}"
            ) from exc
        destination = install_from_path(dataset, download, target_root)

    state = read_state(target_root)
    state["datasets"][dataset.name] = dataset_metadata(
        dataset, destination, dataset.source_url
    )
    write_state(target_root, state)
    print(f"Updated {dataset.name}: {destination}")
    return state


def status(datasets: list[Dataset], root: Path) -> bool:
    state = read_state(root).get("datasets", {})
    valid = True
    for dataset in datasets:
        path = root / dataset.relative_path
        try:
            validate_dataset(dataset, path)
            metadata = state.get(dataset.name, {})
            expected_hash = metadata.get("sha256")
            if expected_hash:
                actual_hash = current_sha256(path)
                if actual_hash != expected_hash:
                    raise SharedDataError(
                        f"{path} checksum mismatch: expected {expected_hash}, "
                        f"got {actual_hash}"
                    )
            updated = metadata.get("updated_at", "unknown")
            print(f"OK      {dataset.name:<12} {path} (updated {updated})")
        except SharedDataError as exc:
            valid = False
            print(f"INVALID {dataset.name:<12} {exc}")
    return valid


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--manifest", type=Path, default=DEFAULT_MANIFEST)
    parser.add_argument("--root", type=Path, default=DEFAULT_ROOT)
    subparsers = parser.add_subparsers(dest="command", required=True)

    import_parser = subparsers.add_parser(
        "import", help="Import only manifest-allowlisted paths from a directory"
    )
    import_parser.add_argument("source", type=Path)
    import_parser.add_argument(
        "--dataset", action="append", default=[], help="Dataset name; repeat as needed"
    )

    update_parser = subparsers.add_parser(
        "update", help="Download a dataset from its manifest source_url"
    )
    update_parser.add_argument("dataset")

    status_parser = subparsers.add_parser("status", help="Validate installed datasets")
    status_parser.add_argument(
        "--dataset", action="append", default=[], help="Dataset name; repeat as needed"
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        datasets = load_manifest(args.manifest)
        root = args.root.resolve()
        if args.command == "import":
            selected = select_datasets(datasets, args.dataset)
            import_datasets(selected, args.source, root)
            return 0
        if args.command == "update":
            selected = select_datasets(datasets, [args.dataset])
            if len(selected) != 1:
                raise SharedDataError("update requires one dataset name")
            download_dataset(selected[0], root)
            return 0
        selected = select_datasets(datasets, args.dataset)
        return 0 if status(selected, root) else 1
    except SharedDataError as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
