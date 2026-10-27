"""Submission-format validator.

Checks every rule from the competition overview ("A submission is rejected if it is missing the
molecule_id or smiles column, is empty, contains nulls in either column, repeats a molecule_id,
or gives more than 25 semicolon-separated guesses for any molecule"), the extra rejection the
metric applies (a row with no guesses), and pre-flight checks Kaggle does not reject but that
cost score: test molecules missing from the file, unknown ids and unparseable guesses.

Rejection messages are the metric's own, so a file that passes here also passes the metric.
"""

from collections.abc import Iterable
from enum import StrEnum
from pathlib import Path

import pandas as pd
from pydantic import BaseModel

from casmi.metric import MAX_RANK, heavy_atom_composition

SUBMISSION_FILE_NAME = "submission.csv"
REQUIRED_COLUMNS = ("molecule_id", "smiles")


class Severity(StrEnum):
    ERROR = "error"  # Kaggle rejects the submission
    WARNING = "warning"  # accepted, but scores worse than it could


class IssueCode(StrEnum):
    WRONG_FILE_NAME = "wrong_file_name"
    UNREADABLE = "unreadable"
    MISSING_COLUMNS = "missing_columns"
    EMPTY = "empty"
    NULLS = "nulls"
    DUPLICATE_ID = "duplicate_id"
    NO_GUESSES = "no_guesses"
    TOO_MANY_GUESSES = "too_many_guesses"
    MISSING_MOLECULE = "missing_molecule"
    UNKNOWN_MOLECULE = "unknown_molecule"
    UNPARSEABLE_SMILES = "unparseable_smiles"


class Issue(BaseModel):
    code: IssueCode
    severity: Severity
    message: str
    molecule_id: str | None = None


class ValidationReport(BaseModel):
    issues: list[Issue]
    num_rows: int

    @property
    def ok(self) -> bool:
        """True when Kaggle would accept the submission."""
        return not self.errors

    @property
    def errors(self) -> list[Issue]:
        return [i for i in self.issues if i.severity is Severity.ERROR]

    @property
    def warnings(self) -> list[Issue]:
        return [i for i in self.issues if i.severity is Severity.WARNING]


def _error(code: IssueCode, message: str, molecule_id: object = None) -> Issue:
    mid = None if molecule_id is None else str(molecule_id)
    return Issue(code=code, severity=Severity.ERROR, message=message, molecule_id=mid)


def _warning(code: IssueCode, message: str, molecule_id: object = None) -> Issue:
    mid = None if molecule_id is None else str(molecule_id)
    return Issue(code=code, severity=Severity.WARNING, message=message, molecule_id=mid)


def validate_submission(
    submission: pd.DataFrame,
    *,
    expected_ids: Iterable[str] | None = None,
    max_rank: int = MAX_RANK,
    check_smiles: bool = True,
) -> ValidationReport:
    """Validate a submission frame and report every problem found, not just the first.

    expected_ids: the test set's molecule_ids; enables the missing/unknown id checks.
    check_smiles: parse every guess with RDKit and warn on the ones that cannot score.
    """
    issues: list[Issue] = []
    num_rows = len(submission)

    missing = [c for c in REQUIRED_COLUMNS if c not in submission.columns]
    if missing:
        issues.append(
            _error(IssueCode.MISSING_COLUMNS, f"Missing columns: {list(REQUIRED_COLUMNS)}")
        )
        return ValidationReport(issues=issues, num_rows=num_rows)
    if num_rows == 0:
        issues.append(_error(IssueCode.EMPTY, "Submission is empty"))
        return ValidationReport(issues=issues, num_rows=num_rows)

    frame = submission[list(REQUIRED_COLUMNS)]
    null_rows = frame.isna().any(axis=1)
    if null_rows.any():
        issues.append(_error(IssueCode.NULLS, "NaN in required columns"))

    ids = frame["molecule_id"]
    for molecule_id in ids[ids.duplicated() & ids.notna()].unique():
        issues.append(
            _error(IssueCode.DUPLICATE_ID, "Each molecule_id must appear exactly once", molecule_id)
        )

    unparseable: set[str] = set()
    for molecule_id, raw in zip(frame["molecule_id"], frame["smiles"], strict=True):
        if pd.isna(molecule_id) or pd.isna(raw):
            continue
        guesses = [g for g in (c.strip() for c in str(raw).split(";")) if g]
        if not guesses:
            issues.append(
                _error(
                    IssueCode.NO_GUESSES, f"No guesses for molecule_id {molecule_id}", molecule_id
                )
            )
        elif len(guesses) > max_rank:
            issues.append(
                _error(
                    IssueCode.TOO_MANY_GUESSES,
                    f"At most {max_rank} semicolon-separated guesses per molecule_id, "
                    f"got {len(guesses)} for {molecule_id}",
                    molecule_id,
                )
            )
        if check_smiles:
            for guess in guesses:
                if guess not in unparseable and heavy_atom_composition(guess) is None:
                    unparseable.add(guess)
                    issues.append(
                        _warning(
                            IssueCode.UNPARSEABLE_SMILES,
                            f"RDKit cannot parse guess {guess!r}; it can never match",
                            molecule_id,
                        )
                    )

    if expected_ids is not None:
        expected = {str(i) for i in expected_ids}
        submitted = {str(i) for i in ids.dropna()}
        for molecule_id in sorted(expected - submitted):
            issues.append(
                _error(
                    IssueCode.MISSING_MOLECULE,
                    f"Every molecule_id must appear exactly once; {molecule_id} is missing "
                    "and would score 0",
                    molecule_id,
                )
            )
        for molecule_id in sorted(submitted - expected):
            issues.append(
                _warning(
                    IssueCode.UNKNOWN_MOLECULE,
                    f"molecule_id {molecule_id} is not in the test set and is ignored",
                    molecule_id,
                )
            )

    return ValidationReport(issues=issues, num_rows=num_rows)


def read_submission(path: Path) -> pd.DataFrame:
    """Read a submission CSV the way pandas defaults do (empty fields become NaN)."""
    return pd.read_csv(path, dtype={"molecule_id": str, "smiles": str})


def validate_file(
    path: Path,
    *,
    expected_ids: Iterable[str] | None = None,
    max_rank: int = MAX_RANK,
    check_smiles: bool = True,
) -> ValidationReport:
    """Validate a submission file, including the required `submission.csv` name."""
    issues: list[Issue] = []
    if path.name != SUBMISSION_FILE_NAME:
        issues.append(
            _error(
                IssueCode.WRONG_FILE_NAME,
                f"Submission file must be named {SUBMISSION_FILE_NAME}, got {path.name}",
            )
        )
    try:
        frame = read_submission(path)
    except (OSError, ValueError, pd.errors.ParserError) as err:
        issues.append(_error(IssueCode.UNREADABLE, f"Cannot read {path}: {err}"))
        return ValidationReport(issues=issues, num_rows=0)
    report = validate_submission(
        frame, expected_ids=expected_ids, max_rank=max_rank, check_smiles=check_smiles
    )
    return ValidationReport(issues=issues + report.issues, num_rows=report.num_rows)
