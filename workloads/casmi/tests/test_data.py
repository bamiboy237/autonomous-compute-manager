from pathlib import Path

import pandas as pd
import pytest
from pydantic import ValidationError

from casmi.data import (
    TEST_ADDUCTS,
    TEST_COLUMNS,
    TRAIN_COLUMNS,
    SchemaError,
    iter_spectra,
    load_spectra,
    parse_row,
    spectra_by_molecule,
)


def test_documented_column_counts() -> None:
    assert len(TEST_COLUMNS) == 12
    assert len(TRAIN_COLUMNS) == 20  # the 12 shared columns plus 8 train-only ones
    assert len(TEST_ADDUCTS) == 10


def test_load_and_parse_train(competition_dir: Path) -> None:
    train = load_spectra(competition_dir / "train.parquet", "train")
    spectra = list(iter_spectra(train))
    assert len(spectra) == len(train)
    first = spectra[0]
    assert first.num_peaks == len(first.intensities) > 0
    assert max(first.intensities) == 1.0
    assert first.smiles is not None
    assert first.inchikey14 is not None
    assert len(first.inchikey14) == 14
    assert first.adduct in TEST_ADDUCTS
    assert first.collision_energy_ev in {(20.0,), (20.0, 40.0, 60.0)}


def test_load_test_has_no_labels(competition_dir: Path) -> None:
    test = load_spectra(competition_dir / "test.parquet", "test")
    assert list(test.columns) == list(TEST_COLUMNS)
    grouped = spectra_by_molecule(test)
    assert sum(len(v) for v in grouped.values()) == len(test)
    assert all(s.smiles is None for v in grouped.values() for s in v)
    assert all(s.instrument_type == "timsTOF" for v in grouped.values() for s in v)


def test_missing_column_is_schema_error(competition_dir: Path, tmp_path: Path) -> None:
    test = pd.read_parquet(competition_dir / "test.parquet").drop(columns="precursor_mz")
    test.to_parquet(tmp_path / "test.parquet")
    with pytest.raises(SchemaError, match="precursor_mz"):
        load_spectra(tmp_path / "test.parquet", "test")


def test_column_subset(competition_dir: Path) -> None:
    ids = load_spectra(competition_dir / "test.parquet", "test", columns=["molecule_id"])
    assert list(ids.columns) == ["molecule_id"]
    with pytest.raises(SchemaError, match="normalized_smiles"):
        load_spectra(competition_dir / "test.parquet", "test", columns=["normalized_smiles"])


BASE_ROW = {
    "molecule_id": "m_1",
    "spectrum_id": "s_1",
    "ms2_mzs": [50.0, 75.5],
    "ms2_normalized_intensities": [1.0, 0.3],
    "base_peak_intensity": None,
    "adduct": "[M+Na]+",
    "ionization_mode": "positive",
    "instrument_type": float("nan"),
    "precursor_mz": 203.05,
    "collision_energy_ev": None,
    "collision_energy_orig": "35HCD",
    "collision_energy_orig_units": "NCE",
}


def test_parse_row_nulls_and_scalars() -> None:
    spectrum = parse_row({**BASE_ROW, "collision_energy_ev": 30})
    assert spectrum.instrument_type is None
    assert spectrum.base_peak_intensity is None
    assert spectrum.collision_energy_ev == (30.0,)
    assert parse_row(BASE_ROW).collision_energy_ev is None


def test_misaligned_peaks_are_rejected() -> None:
    with pytest.raises(ValidationError, match="2 m/z values but 1 intensities"):
        parse_row({**BASE_ROW, "ms2_normalized_intensities": [1.0]})


def test_bad_ionization_mode_is_rejected() -> None:
    with pytest.raises(ValidationError):
        parse_row({**BASE_ROW, "ionization_mode": "neutral"})
