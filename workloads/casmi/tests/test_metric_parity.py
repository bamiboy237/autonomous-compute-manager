"""Differential test: casmi.metric must agree with the Kaggle notebook on every input."""

import importlib.util
import random
from pathlib import Path
from types import ModuleType

import pandas as pd
import pytest

from casmi import metric

REFERENCE = Path(__file__).parent / "reference" / "kaggle_metric.py"

# Answers and guesses chosen to hit every branch: stereo variants, tautomers, protomers, salts,
# isomers with the same heavy atoms, unparseable strings and padding.
ANSWERS = [
    "OC[C@H]1OC(O)[C@H](O)[C@@H](O)[C@@H]1O",
    "C=C(C)C1CCC2CCC(=O)C=C2C1",
    "CC(=O)C",
    "NCC(=O)O",
    "Oc1ccccn1",
    "Cn1cnc2c1c(=O)n(C)c(=O)n2C",
    "N[C@@H](Cc1ccccc1)C(=O)O",
    "CCO,CCCO",
]
GUESSES = [
    "OCC1OC(O)C(O)C(O)C1O",
    "OCC(O)C(O)C(O)C(O)C=O",
    "C=C(C)[C@@H]1CCC2CCC(=O)C=C2C1",
    "CC(O)=C",
    "CCC=O",
    "[NH3+]CC(=O)[O-]",
    "O=c1cccc[nH]1",
    "Cn1c(=O)c2c(ncn2C)n(C)c1=O",
    "NC(Cc1ccccc1)C(=O)O",
    "NC(Cc1ccccc1)C(=O)[O-].[Na+]",
    "CCCO",
    "OCC",
    "c1ccccc1",
    "not a smiles",
    "C1CC",
    "[Xx]",
    " CCO ",
]


def _load_reference() -> ModuleType:
    spec = importlib.util.spec_from_file_location("casmi_reference_metric", REFERENCE)
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture(scope="module")
def reference() -> ModuleType:
    return _load_reference()


def _outcome(fn: object, *args: object, **kwargs: object) -> tuple[str, object]:
    try:
        return ("ok", fn(*args, **kwargs))  # pyright: ignore[reportCallIssue]
    except Exception as err:  # the class lives in two modules; compare by name and message
        return (type(err).__name__, str(err))


def _random_case(rng: random.Random) -> tuple[pd.DataFrame, pd.DataFrame, int]:
    ids = [f"m_{i}" for i in range(rng.randint(1, 6))]
    solution = pd.DataFrame({"molecule_id": ids, "smiles": [rng.choice(ANSWERS) for _ in ids]})
    rows = []
    for mid in rng.sample(ids, rng.randint(1, len(ids))) + (["m_x"] if rng.random() < 0.2 else []):
        k = rng.randint(1, 8)
        rows.append((mid, ";".join(rng.choice(GUESSES + ANSWERS[:-1]) for _ in range(k))))
    submission = pd.DataFrame(rows, columns=["molecule_id", "smiles"])
    max_rank = rng.choice([25, 25, 25, 4])
    return solution, submission, max_rank


def test_random_submissions_score_identically(reference: ModuleType) -> None:
    rng = random.Random(20261027)
    for _ in range(300):
        solution, submission, max_rank = _random_case(rng)
        ours = _outcome(metric.score, solution, submission, "molecule_id", max_rank=max_rank)
        theirs = _outcome(reference.score, solution, submission, "molecule_id", max_rank=max_rank)
        assert ours == theirs, (solution.to_dict(), submission.to_dict(), max_rank)


MALFORMED = {
    "missing column": pd.DataFrame({"molecule_id": ["m_1"]}),
    "empty": pd.DataFrame({"molecule_id": [], "smiles": []}),
    "null id": pd.DataFrame({"molecule_id": [None], "smiles": ["CCO"]}),
    "null smiles": pd.DataFrame({"molecule_id": ["m_1"], "smiles": [None]}),
    "duplicate": pd.DataFrame({"molecule_id": ["m_1", "m_1"], "smiles": ["CCO", "CCO"]}),
    "too many": pd.DataFrame({"molecule_id": ["m_1"], "smiles": [";".join(["C"] * 26)]}),
    "no guesses": pd.DataFrame({"molecule_id": ["m_1"], "smiles": [" ; ;"]}),
}


@pytest.mark.parametrize("name", sorted(MALFORMED))
def test_malformed_submissions_fail_identically(reference: ModuleType, name: str) -> None:
    solution = pd.DataFrame({"molecule_id": ["m_1"], "smiles": ["CCO"]})
    ours = _outcome(metric.score, solution, MALFORMED[name], "molecule_id")
    theirs = _outcome(reference.score, solution, MALFORMED[name], "molecule_id")
    assert ours[0] == "ParticipantVisibleError"
    assert ours == theirs


def test_broken_solution_fails_identically(reference: ModuleType) -> None:
    submission = pd.DataFrame({"molecule_id": ["m_1"], "smiles": ["CCO"]})
    for solution in (
        pd.DataFrame({"molecule_id": ["m_1"], "smiles": ["garbage"]}),
        pd.DataFrame({"molecule_id": ["m_1"], "smiles": [None]}),
        pd.DataFrame({"molecule_id": ["m_1"], "answer": ["CCO"]}),
    ):
        ours = _outcome(metric.score, solution, submission, "molecule_id")
        theirs = _outcome(reference.score, solution, submission, "molecule_id")
        assert ours == theirs
