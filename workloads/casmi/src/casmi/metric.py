"""Local copy of the Kaggle CASMI 2026 metric: mean reciprocal rank at 25 (MRR@25).

Ported from the competition's metric notebook, version 14
(https://www.kaggle.com/code/metric/casmi-mean-reciprocal-rank, Apache 2.0). The checks,
error messages, canonicalization and scoring loop match that notebook line for line, so a local
score equals the leaderboard score for the same solution and submission. Differences from the
notebook:

- RDKit is a pinned dependency of this package, so the notebook's offline wheel installer is gone.
- `reciprocal_ranks` exposes the per-molecule scores that the notebook averages.

Do not "improve" the logic here. If Kaggle publishes a new metric version, port it as a whole and
update the version above.
"""

import warnings
from collections.abc import Hashable

import numpy as np
import pandas as pd
import rdkit
from rdkit import Chem, rdBase
from rdkit.Chem.MolStandardize import rdMolStandardize

# The solution key was canonicalized with RDKit 2026.03.3. TautomerEnumerator output changes
# between releases, so this pin decides which submissions match.
EXPECTED_RDKIT_VERSION = "2026.03.3"
MAX_RANK = 25

rdBase.DisableLog("rdApp.*")
if rdkit.__version__ != EXPECTED_RDKIT_VERSION:
    warnings.warn(
        f"RDKit version mismatch: solution={EXPECTED_RDKIT_VERSION}, "
        f"scoring={rdkit.__version__}. Canonicalization may differ.",
        UserWarning,
        stacklevel=1,
    )
_tautomer_canonicalizer = rdMolStandardize.TautomerEnumerator()

type HeavyAtomKey = tuple[tuple[str, int], ...]


class ParticipantVisibleError(Exception):
    """The submission is malformed; Kaggle shows this message to the participant."""


class HostVisibleError(Exception):
    """The solution file is broken; Kaggle shows this message only to the host."""


def canonicalize_smiles_to_ik14(smiles: object) -> str | None:
    """Return the tautomer-canonical InChIKey14 of a SMILES, or None if it cannot be parsed.

    Stereochemistry is removed before tautomer canonicalization: TautomerEnumerator will not move
    a double bond that a neighbouring stereocentre constrains, so the same skeleton with and
    without that stereocentre could otherwise land on different tautomers (different connectivity,
    different InChIKey14). Both the answer and the guesses go through this function.
    """
    if not isinstance(smiles, str):
        return None
    try:
        mol = Chem.MolFromSmiles(smiles)
        if mol is None:
            return None
        Chem.RemoveStereochemistry(mol)
        canonical_mol = _tautomer_canonicalizer.Canonicalize(mol)
        inchikey = Chem.MolToInchiKey(canonical_mol)
        return inchikey[:14]
    # MolSanitizeException subclasses ValueError; RuntimeError covers the C++ layer and TypeError
    # covers Boost.Python argument errors. A malformed guess is a non-match, never a crash.
    except (ValueError, RuntimeError, TypeError):
        return None


def heavy_atom_composition(smiles: object) -> HeavyAtomKey | None:
    """Heavy-atom element counts (no H, charge ignored) as a hashable key, or None on failure.

    A cheap gate in front of canonicalization: structures sharing an InChIKey14 share a heavy-atom
    skeleton. It deliberately ignores H count and charge because protomers share an InChIKey14.
    """
    mol = Chem.MolFromSmiles(smiles) if isinstance(smiles, str) else None
    if mol is None:
        return None
    counts: dict[str, int] = {}
    for atom in mol.GetAtoms():
        sym = atom.GetSymbol()
        if sym != "H":
            counts[sym] = counts.get(sym, 0) + 1
    return tuple(sorted(counts.items()))


def parse_guesses(submission: pd.DataFrame, max_rank: int = MAX_RANK) -> dict[Hashable, list[str]]:
    """Apply the metric's submission checks and return the ranked guesses per molecule_id.

    Raises ParticipantVisibleError with the metric's exact message on the first failed check.
    """
    _check_frame(submission, max_rank)
    return _split_guesses(submission, max_rank)


def _check_frame(submission: pd.DataFrame, max_rank: int) -> None:
    if not isinstance(max_rank, int | np.integer) or max_rank < 1:
        raise ValueError(f"max_rank must be a positive integer, got {max_rank!r}")
    required = ["molecule_id", "smiles"]
    if not all(c in submission.columns for c in required):
        raise ParticipantVisibleError(f"Missing columns: {required}")
    if len(submission) == 0:
        raise ParticipantVisibleError("Submission is empty")
    if submission[required].isna().any().any():
        raise ParticipantVisibleError("NaN in required columns")


def _split_guesses(submission: pd.DataFrame, max_rank: int) -> dict[Hashable, list[str]]:
    if submission["molecule_id"].duplicated().any():
        raise ParticipantVisibleError("Each molecule_id must appear exactly once")
    # A semicolon cannot occur inside a valid SMILES, so splitting on it never cuts a guess.
    guesses_by_molecule: dict[Hashable, list[str]] = {}
    for molecule_id, raw_guesses in zip(
        submission["molecule_id"], submission["smiles"], strict=True
    ):
        parsed = [candidate.strip() for candidate in str(raw_guesses).split(";")]
        parsed = [candidate for candidate in parsed if candidate]
        if not parsed:
            raise ParticipantVisibleError(f"No guesses for molecule_id {molecule_id}")
        if len(parsed) > max_rank:
            raise ParticipantVisibleError(
                f"At most {max_rank} semicolon-separated guesses per molecule_id, "
                f"got {len(parsed)} for {molecule_id}"
            )
        guesses_by_molecule[molecule_id] = parsed
    return guesses_by_molecule


def _accepted_keys(solution: pd.DataFrame) -> dict[Hashable, tuple[set[str], set[HeavyAtomKey]]]:
    sol_dict: dict[Hashable, tuple[set[str], set[HeavyAtomKey]]] = {}
    for _, row in solution.iterrows():
        molecule_id = row["molecule_id"]
        accept_smiles = row["smiles"] if isinstance(row["smiles"], str) else ""
        # The answer column may hold several comma-separated acceptable structures.
        candidates = [s for s in accept_smiles.split(",") if s]
        if not candidates:
            raise HostVisibleError(f"Solution has no answer SMILES for molecule_id {molecule_id}")
        accept_ik14s = {k for k in map(canonicalize_smiles_to_ik14, candidates) if k}
        accept_keys = {k for k in map(heavy_atom_composition, candidates) if k}
        if not accept_ik14s or not accept_keys:
            raise HostVisibleError(
                f"Solution SMILES for molecule_id {molecule_id} could not be parsed: "
                f"{accept_smiles!r}"
            )
        sol_dict[molecule_id] = (accept_ik14s, accept_keys)
    return sol_dict


def reciprocal_ranks(
    solution: pd.DataFrame, submission: pd.DataFrame, max_rank: int = MAX_RANK
) -> dict[Hashable, float]:
    """Reciprocal rank of the first correct guess for every molecule in the solution.

    Molecules missing from the submission score 0. Submission ids absent from the solution are
    ignored, as on Kaggle.
    """
    # Same check order as the notebook, so a doubly broken input reports the same error.
    _check_frame(submission, max_rank)
    if "smiles" not in solution.columns:
        raise ParticipantVisibleError("Solution missing smiles column")
    guesses_by_molecule = _split_guesses(submission, max_rank)

    gate_keys = {
        s: heavy_atom_composition(s) for guesses in guesses_by_molecule.values() for s in guesses
    }
    sol_dict = _accepted_keys(solution)

    canon_cache: dict[str, str | None] = {}

    def canon(smiles: str) -> str | None:
        if smiles not in canon_cache:
            canon_cache[smiles] = canonicalize_smiles_to_ik14(smiles)
        return canon_cache[smiles]

    scores: dict[Hashable, float] = {}
    for mol_id, (acceptable, accept_keys) in sol_dict.items():
        rr = 0.0
        for rank, guess in enumerate(guesses_by_molecule.get(mol_id, []), 1):
            if gate_keys[guess] not in accept_keys:
                continue
            if canon(guess) in acceptable:
                rr = 1.0 / rank
                break
        scores[mol_id] = rr
    return scores


def score(
    solution: pd.DataFrame,
    submission: pd.DataFrame,
    row_id_column_name: str = "molecule_id",
    max_rank: int = MAX_RANK,
) -> float:
    """Mean reciprocal rank over the solution's molecules, exactly as Kaggle scores it.

    `row_id_column_name` is accepted for signature parity with Kaggle metrics; like the
    notebook, the id column is always `molecule_id`.
    """
    del row_id_column_name
    return float(np.mean(list(reciprocal_ranks(solution, submission, max_rank).values())))
