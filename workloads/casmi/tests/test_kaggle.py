import io
import os
import zipfile
from pathlib import Path

import httpx
import pytest

from casmi.data import COMPETITION_FILES
from casmi.kaggle import COMPETITION, KaggleCredentials, KaggleError, download_competition

CREDS = KaggleCredentials.model_validate({"username": "alice", "key": "s3cret"})


def _zip(files: dict[str, bytes]) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        for name, data in files.items():
            zf.writestr(name, data)
    return buf.getvalue()


def _client(status: int, body: bytes, seen: list[httpx.Request] | None = None) -> httpx.AsyncClient:
    def handler(request: httpx.Request) -> httpx.Response:
        if seen is not None:
            seen.append(request)
        return httpx.Response(status, content=body)

    return httpx.AsyncClient(transport=httpx.MockTransport(handler))


async def test_downloads_and_unpacks(competition_dir: Path, tmp_path: Path) -> None:
    archive = _zip({name: (competition_dir / name).read_bytes() for name in COMPETITION_FILES})
    seen: list[httpx.Request] = []
    dest = tmp_path / "out"
    async with _client(200, archive, seen) as client:
        files = await download_competition(dest, CREDS, client=client)
    assert [f.name for f in files] == sorted(COMPETITION_FILES)
    assert (dest / "train.parquet").read_bytes() == (competition_dir / "train.parquet").read_bytes()
    assert not list(dest.glob("*.zip*"))
    request = seen[0]
    assert request.url.path == f"/api/v1/competitions/data/download-all/{COMPETITION}"
    assert request.headers["authorization"] == httpx.BasicAuth("alice", "s3cret")._auth_header


@pytest.mark.parametrize(
    ("status", "message"),
    [
        (401, "credentials"),
        (403, "accept the competition rules"),
        (404, "no competition"),
        (500, "HTTP 500"),
    ],
)
async def test_api_errors_are_explained(tmp_path: Path, status: int, message: str) -> None:
    async with _client(status, b"{}") as client:
        with pytest.raises(KaggleError, match=message):
            await download_competition(tmp_path, CREDS, client=client)
    assert not os.listdir(tmp_path)


async def test_missing_files_are_reported(tmp_path: Path) -> None:
    async with _client(200, _zip({"train.parquet": b"x"})) as client:
        with pytest.raises(KaggleError, match="lacks"):
            await download_competition(tmp_path, CREDS, client=client)


async def test_rejects_non_zip(tmp_path: Path) -> None:
    async with _client(200, b"<html>login</html>") as client:
        with pytest.raises(KaggleError, match="not a zip"):
            await download_competition(tmp_path, CREDS, client=client)


async def test_rejects_path_traversal(tmp_path: Path) -> None:
    async with _client(200, _zip({"../evil.txt": b"x"})) as client:
        with pytest.raises(KaggleError, match="escapes"):
            await download_competition(tmp_path / "d", CREDS, client=client)
    assert not (tmp_path / "evil.txt").exists()


def test_credentials_from_env() -> None:
    assert KaggleCredentials.from_env({}) is None
    assert KaggleCredentials.from_env({"KAGGLE_USERNAME": "a", "KAGGLE_KEY": ""}) is None
    creds = KaggleCredentials.from_env({"KAGGLE_USERNAME": "a", "KAGGLE_KEY": "zz-hidden-zz"})
    assert creds is not None
    assert creds.key.get_secret_value() == "zz-hidden-zz"
    assert "zz-hidden-zz" not in repr(creds)


@pytest.mark.integration
@pytest.mark.skipif(
    KaggleCredentials.from_env() is None,
    reason="needs KAGGLE_USERNAME/KAGGLE_KEY with the CASMI rules accepted (THE-77)",
)
async def test_live_download(tmp_path: Path) -> None:
    creds = KaggleCredentials.from_env(os.environ)
    assert creds is not None
    files = await download_competition(tmp_path, creds)
    assert {f.name for f in files} >= set(COMPETITION_FILES)
