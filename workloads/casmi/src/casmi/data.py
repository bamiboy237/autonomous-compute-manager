"""Competition file layout, column schema and spectrum parsing.

Column names and meanings follow the competition's data page
(https://www.kaggle.com/competitions/enveda-CASMI26-molecule-id-mass-spectra/data).
"""

from collections.abc import Iterator, Sequence
from pathlib import Path
from typing import Any, Literal

import numpy as np
import pandas as pd
from pydantic import BaseModel, ConfigDict, Field, model_validator

TRAIN_FILE = "train.parquet"
TEST_FILE = "test.parquet"
SAMPLE_SUBMISSION_FILE = "sample_submission.csv"
COMPETITION_FILES = (TRAIN_FILE, TEST_FILE, SAMPLE_SUBMISSION_FILE)

TEST_COLUMNS = (
    "molecule_id",
    "spectrum_id",
    "ms2_mzs",
    "ms2_normalized_intensities",
    "base_peak_intensity",
    "adduct",
    "ionization_mode",
    "instrument_type",
    "precursor_mz",
    "collision_energy_ev",
    "collision_energy_orig",
    "collision_energy_orig_units",
)
TRAIN_ONLY_COLUMNS = (
    "normalized_smiles",
    "inchikey",
    "inchikey14",
    "molecular_formula",
    "ingest_lib",
    "adduct_orig",
    "precursor_error_ppm",
    "num_peaks",
)
TRAIN_COLUMNS = TEST_COLUMNS + TRAIN_ONLY_COLUMNS

# The ten adducts that occur in the test set. The training set contains many more.
TEST_ADDUCTS = frozenset(
    {
        "[M+H]+",
        "[M+NH4]+",
        "[M-H2O+H]+",
        "[M-2H2O+H]+",
        "[M+Na]+",
        "[M+K]+",
        "[M-H]-",
        "[M-H2O-H]-",
        "[M+CH2O2-H]-",
        "[M+Cl]-",
    }
)

Split = Literal["train", "test"]


class SchemaError(ValueError):
    """A competition file does not have the documented columns."""


class Spectrum(BaseModel):
    """One MS/MS spectrum. Train-only fields are None for test spectra."""

    model_config = ConfigDict(frozen=True)

    molecule_id: str
    spectrum_id: str
    mzs: tuple[float, ...]
    intensities: tuple[float, ...]
    precursor_mz: float = Field(gt=0)
    adduct: str
    ionization_mode: Literal["positive", "negative"]
    collision_energy_ev: tuple[float, ...] | None
    collision_energy_orig: str | None
    collision_energy_orig_units: str | None
    instrument_type: str | None
    base_peak_intensity: float | None
    smiles: str | None = None
    inchikey14: str | None = None
    molecular_formula: str | None = None
    ingest_lib: str | None = None

    @model_validator(mode="after")
    def _aligned_peaks(self) -> "Spectrum":
        if len(self.mzs) != len(self.intensities):
            raise ValueError(
                f"spectrum {self.spectrum_id}: {len(self.mzs)} m/z values but "
                f"{len(self.intensities)} intensities"
            )
        return self

    @property
    def num_peaks(self) -> int:
        return len(self.mzs)


def check_columns(frame: pd.DataFrame, split: Split) -> None:
    expected = TRAIN_COLUMNS if split == "train" else TEST_COLUMNS
    missing = [c for c in expected if c not in frame.columns]
    if missing:
        raise SchemaError(f"{split} file is missing columns: {missing}")


def load_spectra(path: Path, split: Split, *, columns: Sequence[str] | None = None) -> pd.DataFrame:
    """Read train.parquet or test.parquet and check it has the documented columns.

    Pass `columns` to read a subset (the full train file is ~2.5M rows); the schema check then
    covers only the requested columns.
    """
    if columns is not None:
        expected = TRAIN_COLUMNS if split == "train" else TEST_COLUMNS
        unknown = [c for c in columns if c not in expected]
        if unknown:
            raise SchemaError(f"{split} file has no columns {unknown}")
    frame = pd.read_parquet(path, columns=list(columns) if columns is not None else None)
    if columns is None:
        check_columns(frame, split)
    return frame


def _floats(value: Any) -> tuple[float, ...] | None:
    if value is None:
        return None
    if isinstance(value, float) and np.isnan(value):
        return None
    if isinstance(value, int | float):
        return (float(value),)
    return tuple(float(v) for v in value)


def _optional_str(value: Any) -> str | None:
    if value is None or (isinstance(value, float) and np.isnan(value)):
        return None
    return str(value)


def _optional_float(value: Any) -> float | None:
    if value is None or (isinstance(value, float) and np.isnan(value)):
        return None
    return float(value)


def parse_row(row: dict[str, Any]) -> Spectrum:
    """Build a Spectrum from one parquet row (a dict of column -> value)."""
    return Spectrum(
        molecule_id=str(row["molecule_id"]),
        spectrum_id=str(row["spectrum_id"]),
        mzs=_floats(row["ms2_mzs"]) or (),
        intensities=_floats(row["ms2_normalized_intensities"]) or (),
        precursor_mz=float(row["precursor_mz"]),
        adduct=str(row["adduct"]),
        ionization_mode=row["ionization_mode"],
        collision_energy_ev=_floats(row.get("collision_energy_ev")),
        collision_energy_orig=_optional_str(row.get("collision_energy_orig")),
        collision_energy_orig_units=_optional_str(row.get("collision_energy_orig_units")),
        instrument_type=_optional_str(row.get("instrument_type")),
        base_peak_intensity=_optional_float(row.get("base_peak_intensity")),
        smiles=_optional_str(row.get("normalized_smiles")),
        inchikey14=_optional_str(row.get("inchikey14")),
        molecular_formula=_optional_str(row.get("molecular_formula")),
        ingest_lib=_optional_str(row.get("ingest_lib")),
    )


def iter_spectra(frame: pd.DataFrame) -> Iterator[Spectrum]:
    for row in frame.to_dict(orient="records"):
        yield parse_row({str(k): v for k, v in row.items()})


def spectra_by_molecule(frame: pd.DataFrame) -> dict[str, list[Spectrum]]:
    """Group spectra by molecule_id; predictions are made per molecule, not per spectrum."""
    grouped: dict[str, list[Spectrum]] = {}
    for spectrum in iter_spectra(frame):
        grouped.setdefault(spectrum.molecule_id, []).append(spectrum)
    return grouped
