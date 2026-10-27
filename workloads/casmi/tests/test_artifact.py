import os
from pathlib import Path

import pytest
from obstore.store import LocalStore, MemoryStore

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


@pytest.mark.integration
@pytest.mark.skipif(
    not os.environ.get("CASMI_TEST_STORE_URL"),
    reason="set CASMI_TEST_STORE_URL (e.g. s3://acm-artifacts with AWS_* for MinIO) to run",
)
async def test_publish_to_object_store(competition_dir: Path, tmp_path: Path) -> None:
    store = open_store(os.environ["CASMI_TEST_STORE_URL"])
    manifest = await publish_dataset(store, _files(competition_dir), source="integration-test")
    fetched = await fetch_dataset(store, tmp_path / "copy", version=manifest.version)
    assert sorted(p.name for p in fetched) == sorted(COMPETITION_FILES)
