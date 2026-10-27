"""Synthetic competition data that mirrors the documented file format.

The real files need Kaggle credentials (THE-77). These builders produce train.parquet,
test.parquet and sample_submission.csv with the documented columns and value conventions, small
enough to build per test.
"""

from pathlib import Path
from typing import Any

import pandas as pd
import pyarrow as pa
import pyarrow.parquet as pq
import pytest
from rdkit import Chem
from rdkit.Chem import rdMolDescriptors

from casmi.data import SAMPLE_SUBMISSION_FILE, TEST_COLUMNS, TEST_FILE, TRAIN_COLUMNS, TRAIN_FILE

# name -> (SMILES, InChIKey14 computed by RDKit at fixture build time)
MOLECULES = {
    "glucose": "OC[C@H]1OC(O)[C@H](O)[C@@H](O)[C@@H]1O",
    "caffeine": "Cn1cnc2c1c(=O)n(C)c(=O)n2C",
    "quercetin": "O=c1c(O)c(-c2ccc(O)c(O)c2)oc2cc(O)cc(O)c12",
    "kaempferol": "O=c1c(O)c(-c2ccc(O)cc2)oc2cc(O)cc(O)c12",
    "chrysin": "O=c1cc(-c2ccccc2)oc2cc(O)cc(O)c12",
    "tyramine": "NCCc1ccc(O)cc1",
    "citric_acid": "OC(=O)CC(O)(CC(=O)O)C(=O)O",
    "salicylic_acid": "OC(=O)c1ccccc1O",
    "theobromine": "Cn1cnc2c1c(=O)[nH]c(=O)n2C",
    "phenylalanine": "N[C@@H](Cc1ccccc1)C(=O)O",
}
LIBRARIES = ("enveda-np-examples", "gnps", "massbank")

SCHEMA = pa.schema(
    [
        ("molecule_id", pa.string()),
        ("spectrum_id", pa.string()),
        ("ms2_mzs", pa.list_(pa.float64())),
        ("ms2_normalized_intensities", pa.list_(pa.float64())),
        ("base_peak_intensity", pa.float64()),
        ("adduct", pa.string()),
        ("ionization_mode", pa.string()),
        ("instrument_type", pa.string()),
        ("precursor_mz", pa.float64()),
        ("collision_energy_ev", pa.list_(pa.float64())),
        ("collision_energy_orig", pa.string()),
        ("collision_energy_orig_units", pa.string()),
        ("normalized_smiles", pa.string()),
        ("inchikey", pa.string()),
        ("inchikey14", pa.string()),
        ("molecular_formula", pa.string()),
        ("ingest_lib", pa.string()),
        ("adduct_orig", pa.string()),
        ("precursor_error_ppm", pa.float64()),
        ("num_peaks", pa.int64()),
    ]
)


def _spectrum(name: str, smiles: str, lib: str, n: int, mid: str) -> dict[str, Any]:
    mol = Chem.MolFromSmiles(smiles)
    inchikey = Chem.MolToInchiKey(mol)
    mass = rdMolDescriptors.CalcExactMolWt(mol)
    positive = n % 2 == 0
    precursor = mass + (1.007276 if positive else -1.007276)
    mzs = [round(precursor * f, 4) for f in (0.31, 0.47, 0.62, 0.88)][: 2 + n % 3]
    intensities = [1.0, 0.42, 0.13, 0.05][: len(mzs)]
    energies = [20.0] if n % 3 else [20.0, 40.0, 60.0]
    return {
        "molecule_id": mid,
        "spectrum_id": f"s_{name}_{lib}_{n}",
        "ms2_mzs": mzs,
        "ms2_normalized_intensities": intensities,
        "base_peak_intensity": 25_000.0 + n,
        "adduct": "[M+H]+" if positive else "[M-H]-",
        "ionization_mode": "positive" if positive else "negative",
        "instrument_type": "timsTOF" if lib == "enveda-np-examples" else None,
        "precursor_mz": round(precursor, 4),
        "collision_energy_ev": energies,
        "collision_energy_orig": "20" if len(energies) == 1 else "[20 40 60]",
        "collision_energy_orig_units": "eV",
        "normalized_smiles": Chem.MolToSmiles(mol),
        "inchikey": inchikey,
        "inchikey14": inchikey[:14],
        "molecular_formula": rdMolDescriptors.CalcMolFormula(mol),
        "ingest_lib": lib,
        "adduct_orig": "M+H" if positive else "M-H",
        "precursor_error_ppm": 1.5,
        "num_peaks": len(mzs),
    }


def synthetic_train() -> pd.DataFrame:
    rows: list[dict[str, Any]] = []
    for i, (name, smiles) in enumerate(MOLECULES.items()):
        # Every structure in 1-3 libraries with 1-3 spectra each, so all novelty classes occur.
        for lib in LIBRARIES[: 1 + i % 3]:
            for n in range(1 + (i + len(lib)) % 3):
                rows.append(_spectrum(name, smiles, lib, n, f"t_{name}"))
    return pd.DataFrame(rows, columns=list(TRAIN_COLUMNS))


def synthetic_test() -> pd.DataFrame:
    # Like the published test.parquet: examples drawn from training, label columns dropped.
    train = synthetic_train()
    sample = train[train["ingest_lib"] == "enveda-np-examples"].copy()
    sample["molecule_id"] = "m_" + sample["inchikey14"].str[:6].str.lower()
    return sample[list(TEST_COLUMNS)].reset_index(drop=True)


def write_competition_dir(dest: Path) -> Path:
    dest.mkdir(parents=True, exist_ok=True)
    pq.write_table(
        pa.Table.from_pandas(synthetic_train(), schema=SCHEMA, preserve_index=False),
        dest / TRAIN_FILE,
    )
    test_schema = pa.schema([SCHEMA.field(c) for c in TEST_COLUMNS])
    pq.write_table(
        pa.Table.from_pandas(synthetic_test(), schema=test_schema, preserve_index=False),
        dest / TEST_FILE,
    )
    ids = synthetic_test()["molecule_id"].unique()
    pd.DataFrame({"molecule_id": ids, "smiles": ["CCO;CC(=O)O;c1ccccc1"] * len(ids)}).to_csv(
        dest / SAMPLE_SUBMISSION_FILE, index=False
    )
    return dest


@pytest.fixture
def competition_dir(tmp_path: Path) -> Path:
    return write_competition_dir(tmp_path / "data")


@pytest.fixture
def train_frame() -> pd.DataFrame:
    return synthetic_train()
