import os
import uuid
from pathlib import Path

import pytest
from obstore.store import LocalStore, MemoryStore, S3Store

from casmi.artifact import (
    blob_path,
    dataset_version,
    describe_files,
    fetch_dataset,
    load_manifest,
    open_store,
    publish_dataset,
)
from casmi.data import COMPETITION_FILES


def _files(directory: Path) -> list[Path]:
    return [directory / name for name in COMPETITION_FILES]


async def test_publish_and_fetch_roundtrip(competition_dir: Path, tmp_path: Path) -> None:
    store = MemoryStore()
    manifest = await publish_dataset(store, _files(competition_dir), source="test")
    assert [f.name for f in manifest.files] == sorted(COMPETITION_FILES)
    assert manifest.path() == f"manifests/datasets/casmi-2026/{manifest.version}.json"
    assert await load_manifest(store, "casmi-2026", manifest.version) == manifest

    fetched = await fetch_dataset(store, tmp_path / "copy", version=manifest.version)
    for path in fetched:
        assert path.read_bytes() == (competition_dir / path.name).read_bytes()


async def test_version_is_content_addressed(competition_dir: Path) -> None:
    store = MemoryStore()
    first = await publish_dataset(store, _files(competition_dir), source="a")
    again = await publish_dataset(store, _files(competition_dir), source="b")
    assert again.version == first.version
    # Unchanged data republished keeps the original manifest.
    assert (await load_manifest(store, "casmi-2026", first.version)).source == "a"

    (competition_dir / "sample_submission.csv").write_text("molecule_id,smiles\nm_1,CCO\n")
    changed = await publish_dataset(store, _files(competition_dir), source="c")
    assert changed.version != first.version
    assert changed.version == dataset_version(await describe_files(_files(competition_dir)))


async def test_fetch_detects_corruption(competition_dir: Path, tmp_path: Path) -> None:
    store = MemoryStore()
    manifest = await publish_dataset(store, _files(competition_dir), source="test")
    victim = next(f for f in manifest.files if f.name == "sample_submission.csv")
    await store.put_async(blob_path(victim.sha256), b"tampered")
    with pytest.raises(ValueError, match="expected sha256"):
        await fetch_dataset(store, tmp_path / "copy", version=manifest.version)
    assert not (tmp_path / "copy" / "sample_submission.csv").exists()


async def test_local_store_and_url(competition_dir: Path, tmp_path: Path) -> None:
    root = tmp_path / "store"
    root.mkdir()
    manifest = await publish_dataset(LocalStore(root), _files(competition_dir), source="test")
    assert (root / manifest.path()).is_file()
    store = open_store(root.as_uri())
    fetched = await fetch_dataset(store, tmp_path / "copy", version=manifest.version)
    assert len(fetched) == len(COMPETITION_FILES)


async def test_duplicate_names_rejected(competition_dir: Path, tmp_path: Path) -> None:
    other = tmp_path / "x" / "train.parquet"
    other.parent.mkdir()
    other.write_bytes(b"x")
    with pytest.raises(ValueError, match="unique"):
        await describe_files([competition_dir / "train.parquet", other])


def _setting(name: str) -> str:
    """Read a setting from the environment, else from .env.example (as the Compose stack does)."""
    if name in os.environ:
        return os.environ[name]
    env_example = Path(__file__).resolve().parents[3] / ".env.example"
    for line in env_example.read_text().splitlines():
        key, sep, value = line.partition("=")
        if sep and key.strip() == name:
            return value.strip()
    raise KeyError(f"{name} is not set and not documented in .env.example")


@pytest.mark.integration
async def test_publish_to_compose_minio(competition_dir: Path, tmp_path: Path) -> None:
    """Round-trip through the Compose stack's MinIO (`just up`), under a throwaway prefix."""
    store = S3Store(
        _setting("ACM_STORAGE__BUCKET"),
        prefix=f"test/casmi/{uuid.uuid4().hex}",
        endpoint=_setting("ACM_STORAGE__ENDPOINT_URL"),
        region=_setting("ACM_STORAGE__REGION"),
        access_key_id=_setting("ACM_STORAGE__ACCESS_KEY_ID"),
        secret_access_key=_setting("ACM_STORAGE__SECRET_ACCESS_KEY"),
        client_options={"allow_http": True},
    )
    manifest = await publish_dataset(store, _files(competition_dir), source="integration-test")
    again = await publish_dataset(store, _files(competition_dir), source="integration-test")
    assert again.version == manifest.version
    fetched = await fetch_dataset(store, tmp_path / "copy", version=manifest.version)
    for path in fetched:
        assert path.read_bytes() == (competition_dir / path.name).read_bytes()
