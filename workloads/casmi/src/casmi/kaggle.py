"""Download the competition data through the Kaggle public API.

Uses `GET /api/v1/competitions/data/download-all/{competition}` with HTTP basic auth
(`KAGGLE_USERNAME` / `KAGGLE_KEY`), the endpoint the official `kaggle competitions download`
command calls. The account must have accepted the competition rules, or Kaggle answers 403.
"""

import asyncio
import os
import zipfile
from collections.abc import Mapping
from pathlib import Path

import httpx
from pydantic import BaseModel, SecretStr

from casmi.data import COMPETITION_FILES

COMPETITION = "enveda-CASMI26-molecule-id-mass-spectra"
KAGGLE_API = "https://www.kaggle.com/api/v1"
_CHUNK = 1 << 20


class KaggleError(RuntimeError):
    """The Kaggle API refused or failed the request."""


class KaggleCredentials(BaseModel):
    username: str
    key: SecretStr

    @classmethod
    def from_env(cls, env: Mapping[str, str] | None = None) -> "KaggleCredentials | None":
        """Read KAGGLE_USERNAME / KAGGLE_KEY; None when either is unset or empty."""
        source = os.environ if env is None else env
        username = source.get("KAGGLE_USERNAME", "")
        key = source.get("KAGGLE_KEY", "")
        if not username or not key:
            return None
        return cls(username=username, key=SecretStr(key))


def _explain(status: int, competition: str) -> str:
    if status == 401:
        return "Kaggle rejected the credentials (401); check KAGGLE_USERNAME and KAGGLE_KEY"
    if status == 403:
        return (
            f"Kaggle refused access to {competition} (403); accept the competition rules "
            "with this account first"
        )
    if status == 404:
        return f"Kaggle has no competition named {competition} (404)"
    return f"Kaggle API returned HTTP {status} for {competition}"


def _unpack(
    archive: Path, dest: Path, expected_files: tuple[str, ...], competition: str
) -> list[Path]:
    try:
        with zipfile.ZipFile(archive) as zf:
            names = zf.namelist()
            root = dest.resolve()
            for name in names:
                if not (dest / name).resolve().is_relative_to(root):
                    raise KaggleError(f"archive member {name!r} escapes {dest}")
            zf.extractall(dest)
    except zipfile.BadZipFile as err:
        raise KaggleError(f"{archive} is not a zip archive: {err}") from err
    finally:
        archive.unlink(missing_ok=True)

    missing = [f for f in expected_files if not (dest / f).is_file()]
    if missing:
        raise KaggleError(f"archive for {competition} lacks {missing}; got {sorted(names)}")
    return sorted(dest / name for name in names if not name.endswith("/"))


async def download_competition(
    dest: Path,
    credentials: KaggleCredentials,
    *,
    competition: str = COMPETITION,
    client: httpx.AsyncClient | None = None,
    expected_files: tuple[str, ...] = COMPETITION_FILES,
) -> list[Path]:
    """Download and unpack the competition archive into `dest`; return the extracted files.

    Streams the archive to disk (the data is ~3 GB), unpacks it, deletes the archive and checks
    that every expected file arrived. Raises KaggleError on any API failure.
    """
    await asyncio.to_thread(dest.mkdir, parents=True, exist_ok=True)
    archive = dest / f"{competition}.zip"
    partial = archive.with_suffix(".zip.part")
    url = f"{KAGGLE_API}/competitions/data/download-all/{competition}"
    auth = httpx.BasicAuth(credentials.username, credentials.key.get_secret_value())

    owned = client is None
    http = client or httpx.AsyncClient(timeout=httpx.Timeout(60.0, read=600.0))
    try:
        async with http.stream("GET", url, auth=auth, follow_redirects=True) as response:
            if response.status_code != 200:
                raise KaggleError(_explain(response.status_code, competition))
            with partial.open("wb") as out:
                async for chunk in response.aiter_bytes(_CHUNK):
                    await asyncio.to_thread(out.write, chunk)
    except httpx.HTTPError as err:
        await asyncio.to_thread(partial.unlink, missing_ok=True)
        raise KaggleError(f"downloading {competition} failed: {err}") from err
    finally:
        if owned:
            await http.aclose()
    await asyncio.to_thread(partial.replace, archive)
    return await asyncio.to_thread(_unpack, archive, dest, expected_files, competition)
