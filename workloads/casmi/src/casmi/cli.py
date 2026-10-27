"""`casmi` command line: download, publish, fetch, split, validate, score."""

import argparse
import asyncio
import sys
from collections.abc import Sequence
from pathlib import Path

import pandas as pd

from casmi import artifact, kaggle, metric
from casmi.data import TEST_COLUMNS, TEST_FILE, TRAIN_FILE, load_spectra
from casmi.split import Fold, SplitConfig, assign_folds, validation_set
from casmi.validate import validate_file


def _download(args: argparse.Namespace) -> int:
    credentials = kaggle.KaggleCredentials.from_env()
    if credentials is None:
        print("KAGGLE_USERNAME and KAGGLE_KEY must be set", file=sys.stderr)
        return 2
    files = asyncio.run(kaggle.download_competition(args.dest, credentials))
    for f in files:
        print(f)
    return 0


def _publish(args: argparse.Namespace) -> int:
    paths = sorted(p for p in args.data_dir.iterdir() if p.is_file())
    store = artifact.open_store(args.store)
    manifest = asyncio.run(
        artifact.publish_dataset(store, paths, name=args.name, source=args.source)
    )
    print(f"{manifest.name} {manifest.version} -> {manifest.path()}")
    return 0


def _fetch(args: argparse.Namespace) -> int:
    store = artifact.open_store(args.store)
    for f in asyncio.run(
        artifact.fetch_dataset(store, args.dest, name=args.name, version=args.version)
    ):
        print(f)
    return 0


def _split(args: argparse.Namespace) -> int:
    config = SplitConfig(
        seed=args.seed,
        valid_fraction=args.valid_fraction,
        query_libs=tuple(args.query_lib) if args.query_lib else None,
    )
    train = load_spectra(args.data_dir / TRAIN_FILE, "train")
    folds = assign_folds(train, config)
    queries, solution = validation_set(train, folds, test_columns=TEST_COLUMNS)
    args.out.mkdir(parents=True, exist_ok=True)
    folds.to_parquet(args.out / "folds.parquet", index=False)
    queries.to_parquet(args.out / "valid_queries.parquet", index=False)
    solution.to_csv(args.out / "valid_solution.csv", index=False)
    counts = folds["fold"].value_counts().to_dict()
    print(
        f"spectra: train={counts.get(Fold.TRAIN.value, 0)} valid={counts.get(Fold.VALID.value, 0)}"
        f" excluded={counts.get(Fold.EXCLUDED.value, 0)}; molecules by class: "
        f"{solution['novelty_class'].value_counts().sort_index().to_dict()}"
    )
    return 0


def _validate(args: argparse.Namespace) -> int:
    expected = None
    if args.test is not None:
        expected = load_spectra(args.test, "test", columns=["molecule_id"])["molecule_id"].unique()
    report = validate_file(args.submission, expected_ids=expected, check_smiles=not args.fast)
    for issue in report.issues:
        print(f"{issue.severity.value}: {issue.code.value}: {issue.message}")
    print(f"{report.num_rows} rows, {len(report.errors)} errors, {len(report.warnings)} warnings")
    return 0 if report.ok else 1


def _score(args: argparse.Namespace) -> int:
    solution = pd.read_csv(args.solution)
    submission = pd.read_csv(args.submission)
    try:
        ranks = metric.reciprocal_ranks(solution, submission)
    except metric.ParticipantVisibleError as err:
        print(f"rejected: {err}", file=sys.stderr)
        return 1
    print(
        f"MRR@{metric.MAX_RANK}: {sum(ranks.values()) / len(ranks):.6f} over {len(ranks)} molecules"
    )
    if "novelty_class" in solution.columns:
        by_class = solution.set_index("molecule_id")["novelty_class"]
        for cls, ids in by_class.groupby(by_class).groups.items():
            values = [ranks[i] for i in ids]
            print(f"  class {cls}: {sum(values) / len(values):.6f} over {len(values)} molecules")
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="casmi", description=__doc__)
    sub = parser.add_subparsers(required=True)

    p = sub.add_parser("download", help="download the competition data from Kaggle")
    p.add_argument("dest", type=Path)
    p.set_defaults(func=_download)

    p = sub.add_parser("publish", help="publish a data directory as a dataset artifact")
    p.add_argument("data_dir", type=Path)
    p.add_argument("--store", required=True, help="object store URL, e.g. s3://acm-artifacts")
    p.add_argument("--name", default=artifact.DATASET_NAME)
    p.add_argument("--source", default=f"kaggle:{kaggle.COMPETITION}")
    p.set_defaults(func=_publish)

    p = sub.add_parser("fetch", help="download a dataset artifact version")
    p.add_argument("version")
    p.add_argument("dest", type=Path)
    p.add_argument("--store", required=True)
    p.add_argument("--name", default=artifact.DATASET_NAME)
    p.set_defaults(func=_fetch)

    p = sub.add_parser("split", help="write the novelty-class train/validation split")
    p.add_argument("data_dir", type=Path, help=f"directory holding {TRAIN_FILE}")
    p.add_argument("out", type=Path)
    p.add_argument("--seed", type=int, default=0)
    p.add_argument("--valid-fraction", type=float, default=SplitConfig().valid_fraction)
    p.add_argument("--query-lib", action="append", help="library eligible for validation")
    p.set_defaults(func=_split)

    p = sub.add_parser("validate", help="check a submission file against the rules")
    p.add_argument("submission", type=Path)
    p.add_argument("--test", type=Path, help=f"{TEST_FILE}, to check every molecule is present")
    p.add_argument("--fast", action="store_true", help="skip parsing every SMILES with RDKit")
    p.set_defaults(func=_validate)

    p = sub.add_parser("score", help="score a submission against a solution with MRR@25")
    p.add_argument("solution", type=Path, help="CSV with molecule_id,smiles[,novelty_class]")
    p.add_argument("submission", type=Path)
    p.set_defaults(func=_score)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
