"""Train/validation split that mimics the test set's three novelty classes.

The test set mixes (1) structures with public reference spectra, (2) known structures without
public spectra and (3) novel structures. Their shares are hidden. The split holds out whole
structures, grouped by InChIKey14 (the unit the metric scores), and simulates each class by what
it removes from training:

- class 1: only the query library's spectra of the structure are held out; spectra of the same
  structure from other libraries stay in training (a library hit is possible).
- class 2: every spectrum of the structure leaves training.
- class 3: as class 2, and every structure sharing its Bemis-Murcko scaffold leaves training too,
  so nothing structurally close remains. Acyclic structures have no scaffold and fall back to
  class 2 behaviour.

Assignment is a pure function of (seed, InChIKey14), so adding rows never reshuffles existing
structures between folds.
"""

import hashlib
from collections.abc import Sequence
from enum import StrEnum

import pandas as pd
from pydantic import BaseModel, Field, model_validator
from rdkit import Chem
from rdkit.Chem.Scaffolds import MurckoScaffold

VALID_MOLECULE_PREFIX = "val_"

# Class shares estimated from leaderboard probes in the public "Validation strategy" discussion
# (https://www.kaggle.com/competitions/enveda-CASMI26-molecule-id-mass-spectra/discussion/748524).
DEFAULT_CLASS_SHARES = (0.28, 0.20, 0.52)


class Fold(StrEnum):
    TRAIN = "train"
    VALID = "valid"
    EXCLUDED = "excluded"  # removed from training to simulate novelty, not scored


class SplitConfig(BaseModel):
    seed: int = 0
    valid_fraction: float = Field(default=0.02, gt=0, lt=1)
    class_shares: tuple[float, float, float] = DEFAULT_CLASS_SHARES
    # Only structures measured in these libraries are eligible for validation; their spectra in
    # these libraries become the query. None means every library. The closest match to the test
    # set is `enveda-np-examples` (same timsTOF pipeline).
    query_libs: tuple[str, ...] | None = None

    @model_validator(mode="after")
    def _shares_sum_to_one(self) -> "SplitConfig":
        if any(s < 0 for s in self.class_shares) or abs(sum(self.class_shares) - 1) > 1e-9:
            raise ValueError(
                f"class_shares must be non-negative and sum to 1, got {self.class_shares}"
            )
        return self


def _unit(seed: int, salt: str, key: str) -> float:
    digest = hashlib.sha256(f"{seed}:{salt}:{key}".encode()).digest()
    return int.from_bytes(digest[:8], "big") / 2**64


def murcko_scaffold(smiles: str) -> str | None:
    """Canonical Bemis-Murcko scaffold SMILES, or None for acyclic or unparseable input."""
    mol = Chem.MolFromSmiles(smiles)
    if mol is None:
        return None
    scaffold = MurckoScaffold.MurckoScaffoldSmiles(mol=mol, includeChirality=False)
    return scaffold or None


def assign_folds(frame: pd.DataFrame, config: SplitConfig) -> pd.DataFrame:
    """Assign every training spectrum a fold and every validation structure a novelty class.

    `frame` needs `spectrum_id`, `inchikey14`, `ingest_lib` and `normalized_smiles`. Returns one
    row per spectrum with columns spectrum_id, inchikey14, fold, novelty_class (0 outside the
    validation fold).
    """
    required = ("spectrum_id", "inchikey14", "ingest_lib", "normalized_smiles")
    missing = [c for c in required if c not in frame.columns]
    if missing:
        raise ValueError(f"split needs columns {missing}")

    libs_by_structure = frame.groupby("inchikey14")["ingest_lib"].agg(lambda s: sorted(set(s)))
    smiles_by_structure = frame.groupby("inchikey14")["normalized_smiles"].first()

    query_lib: dict[str, str] = {}
    novelty: dict[str, int] = {}
    shares = config.class_shares
    for ik14, libs in libs_by_structure.items():
        key = str(ik14)
        eligible = [lib for lib in libs if config.query_libs is None or lib in config.query_libs]
        if not eligible or _unit(config.seed, "valid", key) >= config.valid_fraction:
            continue
        query_lib[key] = eligible[0]
        u = _unit(config.seed, "class", key)
        cls = 1 if u < shares[0] else 2 if u < shares[0] + shares[1] else 3
        # A library hit needs spectra of the structure from another library.
        if cls == 1 and len(libs) < 2:
            cls = 2
        novelty[key] = cls

    scaffold_of = {str(k): murcko_scaffold(str(v)) for k, v in smiles_by_structure.items()}
    novel_scaffolds = {
        scaffold_of[k] for k, cls in novelty.items() if cls == 3 and scaffold_of[k] is not None
    }

    ik14 = frame["inchikey14"].astype(str)
    lib = frame["ingest_lib"].astype(str)
    held_out = ik14.isin(novelty.keys())
    is_query = held_out & (lib == ik14.map(query_lib))
    cls = ik14.map(novelty).fillna(0).astype(int)
    same_scaffold = ik14.map(scaffold_of).isin(novel_scaffolds)
    # A held-out structure that shares a scaffold with a class-3 structure loses its neighbours
    # from training too, so it is effectively class 3.
    cls = cls.where(~(held_out & same_scaffold), 3)

    fold = pd.Series(Fold.TRAIN.value, index=frame.index)
    fold[same_scaffold | (held_out & (cls >= 2))] = Fold.EXCLUDED.value
    fold[is_query] = Fold.VALID.value
    return pd.DataFrame(
        {
            "spectrum_id": frame["spectrum_id"],
            "inchikey14": ik14,
            "fold": fold,
            "novelty_class": cls.where(fold == Fold.VALID.value, 0),
        }
    )


def validation_set(
    frame: pd.DataFrame, folds: pd.DataFrame, *, test_columns: Sequence[str]
) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Build (queries, solution) for the validation fold in the test file's format.

    Each held-out structure becomes one molecule `val_<InChIKey14>`; the solution has the
    metric's `molecule_id,smiles` columns plus `novelty_class` for per-class reporting.
    """
    merged = frame.merge(folds[["spectrum_id", "fold", "novelty_class"]], on="spectrum_id")
    valid = merged[merged["fold"] == Fold.VALID.value].copy()
    valid["molecule_id"] = VALID_MOLECULE_PREFIX + valid["inchikey14"].astype(str)
    queries = valid[list(test_columns)].reset_index(drop=True)
    solution = (
        valid.groupby("molecule_id", sort=True)
        .agg(smiles=("normalized_smiles", "first"), novelty_class=("novelty_class", "first"))
        .reset_index()
    )
    return queries, solution
