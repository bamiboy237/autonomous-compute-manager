import pandas as pd
import pytest
from pydantic import ValidationError

from casmi.data import TEST_COLUMNS
from casmi.metric import score
from casmi.split import Fold, SplitConfig, assign_folds, murcko_scaffold, validation_set


def _check_no_leakage(train: pd.DataFrame, folds: pd.DataFrame) -> None:
    merged = train.merge(folds[["spectrum_id", "fold", "novelty_class"]], on="spectrum_id")
    in_train = merged[merged["fold"] == Fold.TRAIN.value]
    valid = merged[merged["fold"] == Fold.VALID.value]
    train_structures = set(in_train["inchikey14"])
    train_scaffolds = {murcko_scaffold(s) for s in in_train["normalized_smiles"]}
    for (ik14, lib), group in valid.groupby(["inchikey14", "ingest_lib"]):
        cls = int(group["novelty_class"].iloc[0])
        assert cls in {1, 2, 3}
        # The query library's spectra of a held-out structure never stay in training.
        assert in_train[(in_train["inchikey14"] == ik14) & (in_train["ingest_lib"] == lib)].empty
        if cls == 1:
            assert ik14 in train_structures
        else:
            assert ik14 not in train_structures
        if cls == 3:
            scaffold = murcko_scaffold(str(group["normalized_smiles"].iloc[0]))
            assert scaffold is None or scaffold not in train_scaffolds


@pytest.mark.parametrize("seed", range(8))
def test_folds_cover_every_spectrum_without_leakage(train_frame: pd.DataFrame, seed: int) -> None:
    folds = assign_folds(train_frame, SplitConfig(seed=seed, valid_fraction=0.5))
    assert list(folds["spectrum_id"]) == list(train_frame["spectrum_id"])
    assert set(folds["fold"]) <= {f.value for f in Fold}
    _check_no_leakage(train_frame, folds)


def test_assignment_is_deterministic_and_stable(train_frame: pd.DataFrame) -> None:
    config = SplitConfig(seed=3, valid_fraction=0.5)
    first = assign_folds(train_frame, config)
    pd.testing.assert_frame_equal(first, assign_folds(train_frame, config))
    # Dropping rows must not move the remaining spectra between folds.
    subset = train_frame.iloc[::2]
    again = assign_folds(subset, config).set_index("spectrum_id")
    held = first.set_index("spectrum_id").loc[again.index]
    assert (held["fold"] == Fold.VALID.value).sum() > 0
    assert ((held["fold"] == Fold.VALID.value) <= (again["fold"] != Fold.TRAIN.value)).all()


def test_all_three_classes_occur(train_frame: pd.DataFrame) -> None:
    classes: set[int] = set()
    for seed in range(20):
        folds = assign_folds(train_frame, SplitConfig(seed=seed, valid_fraction=0.5))
        classes |= set(folds.loc[folds["fold"] == Fold.VALID.value, "novelty_class"])
    assert classes == {1, 2, 3}


def test_class3_removes_scaffold_neighbours(train_frame: pd.DataFrame) -> None:
    flavones = {"quercetin", "kaempferol", "chrysin"}
    assert (
        len(
            {
                murcko_scaffold(str(s))
                for s in train_frame.loc[
                    train_frame["molecule_id"].str[2:].isin(flavones), "normalized_smiles"
                ]
            }
        )
        == 1
    )
    checked = 0
    for seed in range(50):
        config = SplitConfig(seed=seed, valid_fraction=0.4, class_shares=(0.0, 0.0, 1.0))
        folds = assign_folds(train_frame, config).assign(name=train_frame["molecule_id"].str[2:])
        flav = folds[folds["name"].isin(flavones)]
        held = set(flav.loc[flav["fold"] == Fold.VALID.value, "name"])
        if held and held != flavones:
            # The flavones that were not held out lose their training spectra all the same.
            assert (flav["fold"] != Fold.TRAIN.value).all()
            checked += 1
    assert checked > 0


def test_query_libs_restrict_validation(train_frame: pd.DataFrame) -> None:
    config = SplitConfig(seed=1, valid_fraction=0.9, query_libs=("massbank",))
    folds = assign_folds(train_frame, config)
    merged = train_frame.merge(folds, on=["spectrum_id", "inchikey14"])
    assert set(merged.loc[merged["fold"] == Fold.VALID.value, "ingest_lib"]) == {"massbank"}


def test_validation_set_is_scoreable(train_frame: pd.DataFrame) -> None:
    folds = assign_folds(train_frame, SplitConfig(seed=2, valid_fraction=0.5))
    queries, solution = validation_set(train_frame, folds, test_columns=TEST_COLUMNS)
    assert list(queries.columns) == list(TEST_COLUMNS)
    assert set(queries["molecule_id"]) == set(solution["molecule_id"])
    assert all(mid.startswith("val_") for mid in solution["molecule_id"])
    assert score(solution, solution[["molecule_id", "smiles"]]) == 1.0


def test_config_validation() -> None:
    with pytest.raises(ValidationError, match="sum to 1"):
        SplitConfig(class_shares=(0.5, 0.5, 0.5))
    with pytest.raises(ValidationError):
        SplitConfig(valid_fraction=0)


def test_murcko_scaffold() -> None:
    assert murcko_scaffold("CCO") is None
    assert murcko_scaffold("C1CC") is None
    assert murcko_scaffold("Cc1ccccc1") == "c1ccccc1"
