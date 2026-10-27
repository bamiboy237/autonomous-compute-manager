import pandas as pd
import pytest
import rdkit

from casmi.metric import (
    EXPECTED_RDKIT_VERSION,
    HostVisibleError,
    ParticipantVisibleError,
    canonicalize_smiles_to_ik14,
    heavy_atom_composition,
    reciprocal_ranks,
    score,
)

GLUCOSE_STEREO = "OC[C@H]1OC(O)[C@H](O)[C@@H](O)[C@@H]1O"
GLUCOSE_FLAT = "OCC1OC(O)C(O)C(O)C1O"


def frame(ids: list[str], smiles: list[str]) -> pd.DataFrame:
    return pd.DataFrame({"molecule_id": ids, "smiles": smiles})


@pytest.fixture
def solution() -> pd.DataFrame:
    return pd.DataFrame(
        {"molecule_id": ["m_1", "m_2"], "smiles": ["CCO", "c1ccccc1"], "Usage": ["Public"] * 2}
    )


def test_rdkit_is_pinned_to_the_metric_version() -> None:
    assert rdkit.__version__ == EXPECTED_RDKIT_VERSION


# The next cases are the doctests from the metric notebook, version 14.


def test_rank_is_position_in_list(solution: pd.DataFrame) -> None:
    submission = frame(["m_1", "m_2"], ["CCO;CC(=O)O", "CC(=O)O;c1ccccc1"])
    assert score(solution, submission, "molecule_id") == 0.75


def test_only_first_correct_guess_counts(solution: pd.DataFrame) -> None:
    submission = frame(["m_1", "m_2"], ["CC(=O)O", "c1ccccc1;c1ccccc1;c1ccccc1"])
    assert score(solution, submission, "molecule_id") == 0.5


def test_stereochemistry_is_ignored() -> None:
    assert score(frame(["m_1"], ["C[C@H](N)C(=O)O"]), frame(["m_1"], ["CC(N)C(=O)O"])) == 1.0


def test_stereocentre_pinning_enone_regression() -> None:
    answer = "C=C(C)C1CCC2CCC(=O)C=C2C1"
    guess = "C=C(C)[C@@H]1CCC2CCC(=O)C=C2C1"
    assert score(frame(["m_1"], [answer]), frame(["m_1"], [guess])) == 1.0


def test_duplicate_ids_rejected(solution: pd.DataFrame) -> None:
    with pytest.raises(
        ParticipantVisibleError, match=r"^Each molecule_id must appear exactly once$"
    ):
        score(solution, frame(["m_1", "m_1"], ["CCO", "CCO"]))


def test_too_many_guesses_rejected(solution: pd.DataFrame) -> None:
    with pytest.raises(
        ParticipantVisibleError,
        match=r"^At most 2 semicolon-separated guesses per molecule_id, got 3 for m_1$",
    ):
        score(solution, frame(["m_1"], [";".join(["CCO"] * 3)]), max_rank=2)


def test_unparseable_answer_is_host_error() -> None:
    with pytest.raises(
        HostVisibleError,
        match=r"^Solution SMILES for molecule_id m_1 could not be parsed: 'not a molecule'$",
    ):
        score(frame(["m_1"], ["not a molecule"]), frame(["m_1"], ["CCO"]))


# Competition overview: glucose with and without stereo reduce to WQZGKKKJIJFFOK.


def test_glucose_example_from_competition_page() -> None:
    assert canonicalize_smiles_to_ik14(GLUCOSE_STEREO) == "WQZGKKKJIJFFOK"
    assert canonicalize_smiles_to_ik14(GLUCOSE_FLAT) == "WQZGKKKJIJFFOK"
    answer = frame(["m_1"], [GLUCOSE_STEREO])
    assert score(answer, frame(["m_1"], [GLUCOSE_FLAT])) == 1.0
    assert score(answer, frame(["m_1"], [GLUCOSE_STEREO])) == 1.0
    # Open-chain glucose is a different connectivity, not a stereo or tautomer variant.
    assert score(answer, frame(["m_1"], ["OCC(O)C(O)C(O)C(O)C=O"])) == 0.0


def test_tautomers_match() -> None:
    # Standard InChI already merges mobile-H tautomers; keto/enol is a case it keeps apart, so
    # this match comes from the RDKit tautomer canonicalization.
    keto, enol = "CC(=O)C", "CC(O)=C"
    assert canonicalize_smiles_to_ik14(keto) == canonicalize_smiles_to_ik14(enol)
    assert score(frame(["m_1"], [keto]), frame(["m_1"], [enol])) == 1.0


def test_protomers_pass_the_heavy_atom_gate() -> None:
    zwitterion, neutral = "[NH3+]CC(=O)[O-]", "NCC(=O)O"
    assert heavy_atom_composition(zwitterion) == heavy_atom_composition(neutral)
    assert score(frame(["m_1"], [neutral]), frame(["m_1"], [zwitterion])) == 1.0


def test_rank_25_and_beyond_wrong(solution: pd.DataFrame) -> None:
    guesses = ["C"] * 24 + ["CCO"]
    ranks = reciprocal_ranks(solution, frame(["m_1"], [";".join(guesses)]))
    assert ranks == {"m_1": pytest.approx(0.04), "m_2": 0.0}


def test_missing_molecule_scores_zero_and_unknown_is_ignored(solution: pd.DataFrame) -> None:
    submission = frame(["m_1", "m_999"], ["CCO", "c1ccccc1"])
    assert score(solution, submission) == 0.5


def test_garbage_guesses_never_crash(solution: pd.DataFrame) -> None:
    submission = frame(["m_1", "m_2"], ["not smiles;C1CC;CCO", "[Xx];c1ccccc1"])
    assert reciprocal_ranks(solution, submission) == {"m_1": 1 / 3, "m_2": 0.5}


def test_whitespace_and_empty_entries_are_dropped(solution: pd.DataFrame) -> None:
    submission = frame(["m_1", "m_2"], [" ; CC(=O)O ;; CCO ", "c1ccccc1;"])
    assert reciprocal_ranks(solution, submission) == {"m_1": 0.5, "m_2": 1.0}


def test_comma_separated_answers_accept_any() -> None:
    answer = frame(["m_1"], ["CCO,CCCO"])
    assert score(answer, frame(["m_1"], ["CCCO"])) == 1.0


def test_solution_without_answer_is_host_error() -> None:
    with pytest.raises(HostVisibleError, match="no answer SMILES for molecule_id m_1"):
        score(frame(["m_1"], [None]), frame(["m_1"], ["CCO"]))  # pyright: ignore[reportArgumentType]


def test_solution_without_smiles_column(solution: pd.DataFrame) -> None:
    with pytest.raises(ParticipantVisibleError, match=r"^Solution missing smiles column$"):
        score(solution.drop(columns="smiles"), frame(["m_1"], ["CCO"]))


@pytest.mark.parametrize("max_rank", [0, -1, 2.5])
def test_invalid_max_rank(solution: pd.DataFrame, max_rank: int) -> None:
    with pytest.raises(ValueError, match="max_rank must be a positive integer"):
        score(solution, frame(["m_1"], ["CCO"]), max_rank=max_rank)


def test_canonicalize_rejects_non_strings() -> None:
    assert canonicalize_smiles_to_ik14(None) is None
    assert canonicalize_smiles_to_ik14(float("nan")) is None
    assert canonicalize_smiles_to_ik14("C1CC") is None
