# acm-casmi

Workload for the first campaign: [Enveda CASMI 2026](https://www.kaggle.com/competitions/enveda-CASMI26-molecule-id-mass-spectra)
(ARCHITECTURE.md §15). Given the MS/MS spectra of a molecule, predict up to 25 ranked SMILES. The
score is MRR@25 on the tautomer-canonical InChIKey first block.

This package covers the data and the metric. Training and inference come later (THE-81).
Background research is in [RESEARCH.md](RESEARCH.md).

| Module | Purpose |
|---|---|
| `casmi.metric` | The Kaggle metric, ported from the metric notebook (version 14), with RDKit pinned to the same release. |
| `casmi.validate` | Submission validator: every rejection rule, plus warnings for things that lose score. |
| `casmi.data` | File names, column schema, `Spectrum` model, parquet loaders. |
| `casmi.split` | Train/validation split that simulates the three test novelty classes. |
| `casmi.kaggle` | Async downloader for the competition archive. |
| `casmi.artifact` | Publishes and fetches the data as a versioned, content-addressed dataset artifact. |
| `casmi.cli` | The `casmi` command. |

## Commands

```sh
casmi download data/                                  # needs KAGGLE_USERNAME / KAGGLE_KEY
casmi publish data/ --store s3://acm-artifacts        # prints: casmi-2026 <version> -> <manifest>
casmi fetch <version> data/ --store s3://acm-artifacts
casmi split data/ out/ --seed 0 --valid-fraction 0.02 --query-lib enveda-np-examples
casmi validate submission.csv --test data/test.parquet
casmi score out/valid_solution.csv submission.csv     # prints MRR@25 overall and per class
```

Run them with `uv run casmi ...` from the repository root.

## The metric

`casmi.metric.score(solution, submission)` gives the same number Kaggle does. It keeps the
notebook's checks, error messages and scoring loop:

1. The submission has one row per `molecule_id`, and `smiles` holds up to 25 guesses joined by `;`,
   best first.
2. Stereochemistry is stripped, RDKit's `TautomerEnumerator` canonicalizes the structure, and the
   first 14 characters of the InChIKey are compared.
3. A cheap heavy-atom-composition gate skips canonicalization of guesses that cannot match.
4. A molecule scores `1/rank` of its first correct guess, else 0. A molecule missing from the
   submission scores 0. Ids that are not in the solution are ignored.

RDKit is pinned to `2026.3.3`, the release the solution key was canonicalized with. Tautomer
canonicalization changes between RDKit releases, so do not bump the pin unless the metric does.
`tests/reference/kaggle_metric.py` is the notebook code itself. `tests/test_metric_parity.py`
checks that both implementations return the same score or the same error on randomized and
malformed inputs.

## Submission rules

`casmi.validate.validate_file` reports every problem in the file, not only the first.

| Check | Severity |
|---|---|
| file is not named `submission.csv` | error |
| `molecule_id` or `smiles` column missing (including a missing header) | error |
| no rows | error |
| a null in either column | error |
| a repeated `molecule_id` | error |
| more than 25 guesses for a molecule | error |
| a row whose guesses are all empty | error (the metric rejects it) |
| a test `molecule_id` is missing (needs `--test`) | error (the overview requires every id; Kaggle scores it 0) |
| a `molecule_id` that is not in the test set | warning (ignored by the metric) |
| a guess that RDKit cannot parse | warning (it can never match) |

## Data

`train.parquet` has about 2.5M spectra of 275k structures. `test.parquet` has the 12 shared
columns and no labels. The public copy of the test file is drawn from training; Kaggle swaps in
the hidden test set (~1,500 spectra of ~400 molecules) when it re-runs the notebook. Peaks are two
aligned arrays (`ms2_mzs`, `ms2_normalized_intensities`) with the base peak at 1.0. Use
`collision_energy_ev`, which is the column that matches the test convention. `casmi.data` lists
every column, and `Spectrum` is the typed view of one row.

### Download and storage

The downloader calls `GET https://www.kaggle.com/api/v1/competitions/data/download-all/<competition>`
with HTTP basic auth. This is the same endpoint the `kaggle` CLI uses. The account has to accept
the competition rules first, or Kaggle answers 403. Credentials come from `KAGGLE_USERNAME` and
`KAGGLE_KEY` (owner ticket THE-77). Until those exist, every test runs against a synthetic fixture
(`tests/conftest.py`) that follows the documented format. The live download test skips cleanly.

`casmi publish` stores each file at `cas/<sha256>`, then writes
`manifests/datasets/casmi-2026/<version>.json` (ARCHITECTURE.md §12). The version is a hash of the
file names and contents. Publishing the same data again therefore keeps the same version and
uploads nothing. `casmi fetch` checks every file's hash. The store URL can be `s3://`, `gs://`,
`az://`, `file://` or `memory://` (obstore). S3 credentials and the endpoint come from the standard
`AWS_*` variables. For the bundled MinIO, set `AWS_ENDPOINT_URL` and `AWS_ALLOW_HTTP=true`.

### Validation split

`casmi split` holds out whole structures, grouped by InChIKey14, which is the unit the metric
scores. Each held-out structure gets one of three simulated novelty classes:

| Class | Test definition | What the split removes from training |
|---|---|---|
| 1 | public spectra exist | only the query library's spectra of that structure |
| 2 | in PubChem/COCONUT, no public spectra | every spectrum of that structure |
| 3 | novel structure | every spectrum of every structure that shares its Bemis-Murcko scaffold |

The default class shares are 0.28 / 0.20 / 0.52. These are leaderboard-probe estimates from a
public discussion (see RESEARCH.md). Assignment depends only on `(seed, InChIKey14)`, so it does
not change when rows are added or removed. Outputs:

- `folds.parquet`: one row per spectrum, with `fold` set to `train`, `valid` or `excluded` and
  `novelty_class`.
- `valid_queries.parquet`: held-out spectra in the test-file format. Molecule ids are
  `val_<InChIKey14>`.
- `valid_solution.csv`: columns `molecule_id`, `smiles` and `novelty_class`. `casmi score` reports
  MRR overall and per class from it.

Restrict validation to the timsTOF libraries (`--query-lib enveda-np-examples`, `--query-lib
enveda-180`) for the closest match to the test instrument.

## Tests

```sh
uv run pytest workloads/casmi                      # unit tests, no network
just up && uv run pytest workloads/casmi -m integration
```

The artifact integration test round-trips a dataset through the Compose stack's MinIO, using the
`ACM_STORAGE__*` settings (from the environment, else `.env.example`) under a throwaway prefix.
The live Kaggle download test needs `KAGGLE_USERNAME` and `KAGGLE_KEY`; without them it skips
and says what is missing.
