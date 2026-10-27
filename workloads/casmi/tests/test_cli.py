from pathlib import Path

import pandas as pd
import pytest

from casmi.cli import main


def test_split_validate_score(
    competition_dir: Path, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    out = tmp_path / "split"
    assert (
        main(["split", str(competition_dir), str(out), "--valid-fraction", "0.5", "--seed", "2"])
        == 0
    )
    assert "molecules by class" in capsys.readouterr().out

    solution = pd.read_csv(out / "valid_solution.csv")
    queries = pd.read_parquet(out / "valid_queries.parquet")
    assert set(queries["molecule_id"]) == set(solution["molecule_id"])

    submission = tmp_path / "submission.csv"
    perfect = solution[["molecule_id", "smiles"]].assign(smiles=lambda d: "CCO;" + d["smiles"])
    perfect.to_csv(submission, index=False)
    assert main(["validate", str(submission)]) == 0
    assert main(["score", str(out / "valid_solution.csv"), str(submission)]) == 0
    printed = capsys.readouterr().out
    assert "MRR@25: 0.500000" in printed
    assert "class " in printed


def test_validate_against_test_file(
    competition_dir: Path, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    submission = tmp_path / "submission.csv"
    pd.DataFrame({"molecule_id": ["m_unknown"], "smiles": ["CCO"]}).to_csv(submission, index=False)
    assert main(["validate", str(submission), "--test", str(competition_dir / "test.parquet")]) == 1
    assert "missing_molecule" in capsys.readouterr().out


def test_score_rejects_malformed(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    (tmp_path / "solution.csv").write_text("molecule_id,smiles\nm_1,CCO\n")
    (tmp_path / "submission.csv").write_text("molecule_id,smiles\nm_1,CCO\nm_1,CCO\n")
    assert main(["score", str(tmp_path / "solution.csv"), str(tmp_path / "submission.csv")]) == 1
    assert "exactly once" in capsys.readouterr().err


def test_publish_and_fetch(
    competition_dir: Path, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    store = tmp_path / "store"
    store.mkdir()
    assert main(["publish", str(competition_dir), "--store", store.as_uri()]) == 0
    version = capsys.readouterr().out.split()[1]
    assert main(["fetch", version, str(tmp_path / "copy"), "--store", store.as_uri()]) == 0
    assert (tmp_path / "copy" / "train.parquet").is_file()


def test_download_needs_credentials(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("KAGGLE_USERNAME", raising=False)
    monkeypatch.delenv("KAGGLE_KEY", raising=False)
    assert main(["download", str(tmp_path)]) == 2
