"""Publish and fetch the competition data as a versioned, content-addressed dataset artifact.

Layout follows docs/ARCHITECTURE.md §12: file contents live at `cas/<sha256>`, and each dataset
version is a manifest at `manifests/datasets/<name>/<version>.json`. The version is derived from
the file names and hashes, so publishing identical data twice yields the same version and uploads
nothing new.

Stores are opened with obstore from a URL: `s3://bucket/prefix` (credentials and endpoint from
the standard AWS_* variables, e.g. AWS_ENDPOINT_URL for MinIO), `gs://`, `az://`, `file:///path`
or `memory:///`.
"""

import asyncio
import hashlib
import json
from datetime import UTC, datetime
from pathlib import Path

import obstore
from obstore.exceptions import NotFoundError
from obstore.store import ObjectStore, from_url
from pydantic import BaseModel, Field

DATASET_NAME = "casmi-2026"
_CHUNK = 1 << 20


class DatasetFile(BaseModel):
    name: str
    sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    size: int = Field(ge=0)


class DatasetManifest(BaseModel):
    name: str
    version: str
    files: list[DatasetFile]
    source: str
    created_at: datetime

    def path(self) -> str:
        return manifest_path(self.name, self.version)


def blob_path(sha256: str) -> str:
    return f"cas/{sha256}"


def manifest_path(name: str, version: str) -> str:
    return f"manifests/datasets/{name}/{version}.json"


def open_store(url: str) -> ObjectStore:
    return from_url(url)


def _sha256_or_none(path: Path) -> str | None:
    return _sha256_file(path) if path.is_file() else None


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as f:
        while chunk := f.read(_CHUNK):
            digest.update(chunk)
    return digest.hexdigest()


def _describe(path: Path) -> DatasetFile:
    return DatasetFile(name=path.name, sha256=_sha256_file(path), size=path.stat().st_size)


async def describe_files(paths: list[Path]) -> list[DatasetFile]:
    """Hash files off the event loop; returns entries sorted by name."""
    names = [p.name for p in paths]
    if len(set(names)) != len(names):
        raise ValueError(f"dataset file names must be unique, got {names}")
    files = await asyncio.gather(*(asyncio.to_thread(_describe, p) for p in paths))
    return sorted(files, key=lambda f: f.name)


def dataset_version(files: list[DatasetFile]) -> str:
    """Content version: the first 16 hex chars of a hash over sorted (name, sha256) pairs."""
    canonical = json.dumps([[f.name, f.sha256] for f in sorted(files, key=lambda f: f.name)])
    return hashlib.sha256(canonical.encode()).hexdigest()[:16]


async def _exists(store: ObjectStore, path: str) -> bool:
    try:
        await obstore.head_async(store, path)
    except (NotFoundError, FileNotFoundError):
        return False
    return True


async def publish_dataset(
    store: ObjectStore, paths: list[Path], *, name: str = DATASET_NAME, source: str
) -> DatasetManifest:
    """Upload missing blobs, then write the version manifest; return the manifest.

    The manifest is written last, so a version is only visible once all its blobs exist.
    """
    files = await describe_files(paths)
    by_name = {p.name: p for p in paths}
    for f in files:
        if not await _exists(store, blob_path(f.sha256)):
            await obstore.put_async(store, blob_path(f.sha256), by_name[f.name])
    manifest = DatasetManifest(
        name=name,
        version=dataset_version(files),
        files=files,
        source=source,
        created_at=datetime.now(UTC),
    )
    if not await _exists(store, manifest.path()):
        await obstore.put_async(store, manifest.path(), manifest.model_dump_json(indent=2).encode())
    return manifest


async def load_manifest(store: ObjectStore, name: str, version: str) -> DatasetManifest:
    result = await obstore.get_async(store, manifest_path(name, version))
    return DatasetManifest.model_validate_json(bytes(await result.bytes_async()))


async def fetch_dataset(
    store: ObjectStore, dest: Path, *, name: str = DATASET_NAME, version: str
) -> list[Path]:
    """Download a dataset version into `dest`, verifying every file's hash."""
    manifest = await load_manifest(store, name, version)
    await asyncio.to_thread(dest.mkdir, parents=True, exist_ok=True)
    out: list[Path] = []
    for f in manifest.files:
        target = dest / f.name
        if await asyncio.to_thread(_sha256_or_none, target) != f.sha256:
            partial = target.with_name(target.name + ".part")
            result = await obstore.get_async(store, blob_path(f.sha256))
            with partial.open("wb") as fh:
                async for chunk in result.stream():
                    await asyncio.to_thread(fh.write, chunk)
            actual = await asyncio.to_thread(_sha256_file, partial)
            if actual != f.sha256:
                await asyncio.to_thread(partial.unlink)
                raise ValueError(f"{f.name}: expected sha256 {f.sha256}, got {actual}")
            await asyncio.to_thread(partial.replace, target)
        out.append(target)
    return out
