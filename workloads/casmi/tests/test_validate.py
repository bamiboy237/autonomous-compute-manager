from pathlib import Path

import pandas as pd
import pytest

from casmi.metric import ParticipantVisibleError, parse_guesses
from casmi.validate import IssueCode, Severity, validate_file, validate_submission


def frame(ids: list[object], smiles: list[object]) -> pd.DataFrame:
    return pd.DataFrame({"molecule_id": ids, "smiles": smiles})


# Every rejection documented on the competition overview, plus the metric's empty-row check.
REJECTED = {
    "missing molecule_id column": (pd.DataFrame({"smiles": ["CCO"]}), IssueCode.MISSING_COLUMNS),
    "missing smiles column": (pd.DataFrame({"molecule_id": ["m_1"]}), IssueCode.MISSING_COLUMNS),
    "empty": (frame([], []), IssueCode.EMPTY),
    "null molecule_id": (frame([None], ["CCO"]), IssueCode.NULLS),
    "null smiles": (frame(["m_1"], [None]), IssueCode.NULLS),
    "repeated molecule_id": (frame(["m_1", "m_1"], ["CCO", "CCC"]), IssueCode.DUPLICATE_ID),
    "26 guesses": (frame(["m_1"], [";".join(["C"] * 26)]), IssueCode.TOO_MANY_GUESSES),
    "no guesses": (frame(["m_1"], [";;"]), IssueCode.NO_GUESSES),
}


@pytest.mark.parametrize("name", sorted(REJECTED))
def test_documented_malformed_cases_are_rejected(name: str) -> None:
    submission, code = REJECTED[name]
    report = validate_submission(submission)
    assert not report.ok
    assert code in {i.code for i in report.errors}
    # The metric rejects the same file, with the validator's first message.
    with pytest.raises(ParticipantVisibleError) as err:
        parse_guesses(submission)
    assert str(err.value) == report.errors[0].message


def test_exactly_25_guesses_is_accepted() -> None:
    report = validate_submission(frame(["m_1"], [";".join(["C"] * 25)]))
    assert report.ok


def test_valid_submission_passes_metric_checks() -> None:
    submission = frame(["m_1", "m_2"], ["CCO;CC(=O)O", "c1ccccc1"])
    report = validate_submission(submission, expected_ids=["m_1", "m_2"])
    assert report.ok
    assert report.issues == []
    assert parse_guesses(submission) == {"m_1": ["CCO", "CC(=O)O"], "m_2": ["c1ccccc1"]}


def test_reports_all_problems_not_just_the_first() -> None:
    submission = frame(["m_1", "m_1", "m_2", "m_3"], ["CCO", "CCO", None, ";".join(["C"] * 30)])
    codes = [i.code for i in validate_submission(submission).errors]
    assert codes == [IssueCode.NULLS, IssueCode.DUPLICATE_ID, IssueCode.TOO_MANY_GUESSES]


def test_missing_and_unknown_molecules() -> None:
    report = validate_submission(frame(["m_1", "m_9"], ["CCO", "CCO"]), expected_ids=["m_1", "m_2"])
    assert [(i.code, i.severity, i.molecule_id) for i in report.issues] == [
        (IssueCode.MISSING_MOLECULE, Severity.ERROR, "m_2"),
        (IssueCode.UNKNOWN_MOLECULE, Severity.WARNING, "m_9"),
    ]


def test_unparseable_guesses_are_warnings_once() -> None:
    report = validate_submission(frame(["m_1", "m_2"], ["CCO;C1CC", "C1CC;nonsense"]))
    assert report.ok
    assert [(i.code, i.message) for i in report.warnings] == [
        (IssueCode.UNPARSEABLE_SMILES, "RDKit cannot parse guess 'C1CC'; it can never match"),
        (IssueCode.UNPARSEABLE_SMILES, "RDKit cannot parse guess 'nonsense'; it can never match"),
    ]
    assert validate_submission(frame(["m_1"], ["C1CC"]), check_smiles=False).issues == []


def test_file_name_must_be_submission_csv(tmp_path: Path) -> None:
    path = tmp_path / "preds.csv"
    frame(["m_1"], ["CCO"]).to_csv(path, index=False)
    report = validate_file(path)
    assert [i.code for i in report.errors] == [IssueCode.WRONG_FILE_NAME]


def test_file_checks(tmp_path: Path) -> None:
    path = tmp_path / "submission.csv"
    path.write_text("molecule_id,smiles\nm_1,CCO;CCC\nm_2,\n")
    report = validate_file(path, expected_ids=["m_1", "m_2"])
    assert [i.code for i in report.errors] == [IssueCode.NULLS]
    assert report.num_rows == 2

    path.write_text("molecule_id,smiles\n")
    assert [i.code for i in validate_file(path).errors] == [IssueCode.EMPTY]

    path.write_text("")
    assert [i.code for i in validate_file(path).errors] == [IssueCode.UNREADABLE]

    path.write_text("m_1,CCO\n")  # no header
    assert [i.code for i in validate_file(path).errors] == [IssueCode.MISSING_COLUMNS]


def test_sample_submission_is_valid(competition_dir: Path) -> None:
    test = pd.read_parquet(competition_dir / "test.parquet")
    sample = competition_dir / "sample_submission.csv"
    renamed = sample.with_name("submission.csv")
    sample.rename(renamed)
    report = validate_file(renamed, expected_ids=test["molecule_id"].unique())
    assert report.ok, report.issues
