# CASMI 2026 research notes

Notes for planning the first campaign. Every claim cites the source it came from. The sources were
read on 2026-10-27. Bracketed numbers refer to the source list at the end.

## 1. The competition in brief

- **Task.** Predict up to 25 ranked SMILES per molecule from its LC-MS/MS spectra. A test molecule
  has 1–16 spectra (median 3), so evidence has to be pooled per molecule [1][2].
- **Test set.** About 1,500 spectra of about 400 molecules, all from one Bruker timsTOF.
  Monoisotopic masses run from 157 to 1,159 Da (median 348). There are ten adducts, six positive
  and four negative [2].
- **Novelty classes** (shares hidden) [2]:
  1. the structure has public reference spectra;
  2. the structure is in PubChem or COCONUT, but no public spectra exist;
  3. the structure is not in PubChem at all and must be predicted de novo.
- **Metric.** MRR@25 on the InChIKey first block, after stereochemistry stripping and RDKit
  2026.03.3 tautomer canonicalization [1][3]. Stereochemistry and tautomer form never cost points.
  The correct 2D skeleton does.
- **Format.** A code competition: the notebook may run for at most 9 hours on CPU or GPU, with no
  internet. Freely and publicly available external data and pre-trained models are allowed [1].
  The limits are 5 submissions per day and 2 final selections. The winner's license is MIT [4].
- **Deadlines.** Entry and team merger close 2026-12-07. Final submissions close 2026-12-14 [1].

## 2. Public spectral data

`train.parquet` already aggregates most of the open libraries. It holds about 2.5M spectra of about
275k structures [2]:

| Library | Spectra | Structures | Notes [2] |
|---|---|---|---|
| enveda-180 | 1,153,785 | 182,941 | Same timsTOF as the test set, but synthetic drug-like chemistry |
| pluskal_ms2 (MSnLib) | 527,581 | 46,821 | Orbitrap, one protocol, multiple energies (NCE) |
| riken | 347,171 | 15,892 | Plant specialised metabolites |
| gnps | 220,849 | 45,750 | Largest public natural-product library; heterogeneous |
| massbank | 101,727 | 9,180 | Curated consortium library |
| mona | 92,416 | 11,681 | MassBank of North America |
| spectraverse | 50,933 | 9,631 | Harmonised aggregation, includes obscure libraries |
| msdial | 40,765 | 9,127 | MS-DIAL distributed libraries |
| drug_plus | 2,545 | 2,539 | Drugs, no collision-energy metadata |
| enveda-np-examples | 1,151 | 250 | **Closest to test**: same instruments and pipeline |
| masaryk | 652 | 416 | RECETOX chemical standards |

Other sources that are allowed and may add to this:

- **FragHub.** Merges and harmonises the major open MS/MS libraries. It overlaps heavily with
  `train.parquet` but has some extra experimental and predicted spectra [2].
- **MassIVE and GeMS.** MassIVE holds billions of unannotated MS/MS spectra. GeMS is a curated
  corpus mined from it, with up to 700M spectra. Without labels, it is useful for self-supervised
  pre-training [2][5].
- **GNPS propagated ("suspect") annotations.** These were left out of the training data because
  their labels are inferred, not measured. They are usable at our own risk [2].
- **NIST.** NIST is a commercial library. External data must be public and free for all
  participants, or meet the rules' reasonableness standard, so NIST is out unless the owner
  decides otherwise [4]. The free alternatives are the libraries above.

The public libraries differ in preprocessing, collision-energy units (eV, NCE, V, unknown),
free-text instrument names and m/z precision. Use `collision_energy_ev` to line up with the test
data. The common curation steps are: a relative intensity floor (2%, 1% or 0.1%), dropping peaks
above the precursor m/z plus 1–2 Da, a minimum of about 5–6 peaks, top-N peaks (N=128, or about 6
per 50 Da), deisotoping, and square-root or log intensity [2].

## 3. Candidate structure pools (classes 1 and 2)

- **Training structures.** The 275k labelled structures. Class 1 answers are likely here [2].
- **COCONUT 2.0.** 695,133 unique natural products from 63 collections, under CC BY 4.0, with
  SDF, CSV and database-dump downloads [6]. Class 2 is defined as "in PubChem or COCONUT" [2],
  which makes COCONUT the natural-product-focused retrieval pool.
- **PubChem.** PubChem defines both class 2 and class 3 [2]. It is much larger and mostly
  non-natural. One public validation study found that inserting PubChem candidates helped locally
  (+0.01 to +0.02) but hurt on the leaderboard (0.295 → 0.288). Its conclusion was that PubChem-only
  molecules are probably rare in the test set [7].
- Formula filtering from the precursor m/z (exact mass plus the known adduct) narrows any pool
  sharply. SIRIUS and MIST-CF predict formulas from MS/MS [2][9].

## 4. Pre-trained models and tools

The rules allow any freely and publicly available pre-trained model [1][4]. Inference has to run
offline, inside the 9-hour notebook, so a web service (for example CSI:FingerID's online search)
cannot be called at scoring time.

| Model or tool | What it does | Source |
|---|---|---|
| DreaMS | 116M-parameter transformer, self-supervised on GeMS (masked peaks and retention order). Fine-tunes to fingerprints, similarity and properties. Weights and GeMS are public. | [5] |
| MIST | Spectrum transformer that predicts molecular fingerprints. With contrastive training, it can do database lookup. Also does formula annotation (MIST-CF). | [8][2] |
| SIRIUS / CSI:FingerID / CANOPUS | Formula identification, fingerprint prediction plus structure-database search, and compound classes. | [9] |
| MSNovelist | De novo SMILES from the formula and fingerprint that SIRIUS/CSI:FingerID predict. It needs no structure database. Positive mode only. | [10] |
| DiffMS | Formula-constrained graph diffusion (DiGress decoder) with a MIST-style encoder. | [11] |
| MADGEN | Two stages: contrastive scaffold retrieval, then spectrum-guided generation from the scaffold. | [12] |
| ICEBERG | Forward model that simulates fragmentation spectra for candidate structures. Used for re-ranking. | [13] |
| matchms | Spectrum cleaning, curation and similarity scores. | [2] |
| MassSpecGym | Benchmark with de novo, retrieval and simulation tasks, plus "bonus" formula-given variants. Several of the models above were trained on it. | [14] |

## 5. Approaches, by novelty class

1. **Spectral library search (class 1).** Match test spectra against training spectra with cosine,
   modified-cosine or learned similarity, such as DreaMS embeddings, pooled over a molecule's
   spectra. The hit's structure is the guess. Cheap and strong when the structure is in the
   library [2][5].
2. **Fingerprint prediction plus candidate retrieval (class 2).** Predict a molecular fingerprint
   from the spectra (MIST, a fine-tuned DreaMS, or CSI:FingerID-style). Filter the candidate pool
   (COCONUT plus training structures) by formula or exact mass. Rank by fingerprint similarity,
   optionally re-ranked with a forward model such as ICEBERG [5][8][9][13].
3. **De novo generation (class 3).** Generate structures conditioned on the spectrum, usually with
   the formula as a hard constraint: MSNovelist (fingerprint to SMILES), DiffMS (graph diffusion),
   or MADGEN (scaffold then molecule). Unconstrained de novo is still very hard. On MassSpecGym,
   the SMILES/SELFIES-transformer and random-generation baselines reach 0.00 top-1 and top-10
   exact-match accuracy, even when given the formula [14]. Conditioning on the formula helps the
   most [11][15].
4. **Formula-constrained search** cuts across all three. The precursor m/z and adduct pin down the
   neutral mass. SIRIUS or MIST-CF rank candidate formulas. Every later stage then searches or
   generates only within those formulas [2][9][10].

A ranked list of 25 can mix these sources: library hits first, then retrieval hits, then de novo
proposals. Each class is worth points, and the class shares are unknown.

## 6. Validation strategy (feeds `casmi.split`)

One public write-up [7] held out 1,250 structures with all their spectra and simulated the classes
by what it removed. Class 1 kept other libraries' spectra. Class 2 removed all spectra of the
structure. Class 3 also removed similar structures (Tanimoto ≥ 0.7) from training and from the
candidate pools. Their class-share estimates from leaderboard probes were about 28% / 20% / 52%.
Weighted by those shares, the local estimate was 0.334 against a leaderboard score of 0.322.

Lessons we adopt:

- Compare two settings on the same validation molecules, using bootstrap confidence intervals over
  structures. Treat differences below about 0.02 from a single run as noise, and average over
  seeds when a change involves retraining.
- Evaluate models trained on MassSpecGym only on structures outside MassSpecGym, to avoid
  memorization.
- Hold out structures from `enveda-np-examples` (same instrument and pipeline) as well as from the
  other natural-product libraries.

`casmi.split` puts this into practice with InChIKey14 grouping, per-class removal and those
default shares. For class 3 it uses Bemis-Murcko scaffold grouping in place of the Tanimoto
threshold, as the ticket specifies. A Tanimoto-based variant is a candidate follow-up.

## 7. Implications for the campaign

- The 9-hour offline notebook bounds inference. Training, pool building and model search happen on
  our providers. The notebook loads the weights and the candidate pool as a Kaggle dataset
  (ARCHITECTURE.md §15).
- The cheapest early win is library search plus formula-filtered COCONUT retrieval. Class 3 holds
  about half the molecules (by estimate), so the planner should budget for a de novo branch.
- Track MRR per simulated class (`casmi score` prints it). The class mix is the main unknown when
  turning local scores into leaderboard predictions.

## Sources

1. Kaggle, *Enveda CASMI 2026: Overview* (evaluation, submission format, code requirements, timeline). https://www.kaggle.com/competitions/enveda-CASMI26-molecule-id-mass-spectra/overview
2. Kaggle, *Enveda CASMI 2026: Data*. https://www.kaggle.com/competitions/enveda-CASMI26-molecule-id-mass-spectra/data
3. Kaggle, *CASMI: Mean Reciprocal Rank* metric notebook, version 14. https://www.kaggle.com/code/metric/casmi-mean-reciprocal-rank
4. Kaggle, *Enveda CASMI 2026: Rules* (external data, submission limits, winner license). https://www.kaggle.com/competitions/enveda-CASMI26-molecule-id-mass-spectra/rules
5. Bushuiev et al., "Self-supervised learning of molecular representations from millions of tandem mass spectra using DreaMS", *Nature Biotechnology* (2025). https://www.nature.com/articles/s41587-025-02663-3 and https://github.com/pluskal-lab/DreaMS
6. Chandrasekhar et al., "COCONUT 2.0: a comprehensive overhaul and curation of the collection of open natural products database", *Nucleic Acids Research* 53(D1) (2025). https://pmc.ncbi.nlm.nih.gov/articles/PMC11701633/
7. Kaggle discussion, "Validation strategy" (CASMI 2026). https://www.kaggle.com/competitions/enveda-CASMI26-molecule-id-mass-spectra/discussion/748524
8. Goldman et al. (Coley lab), MIST: Metabolite Inference with Spectrum Transformers. https://github.com/samgoldman97/mist
9. SIRIUS documentation (SIRIUS, CSI:FingerID, CANOPUS, MSNovelist). https://v6.docs.sirius-ms.io/
10. Stravs et al., "MSNovelist: de novo structure generation from mass spectra", *Nature Methods* (2022). https://pmc.ncbi.nlm.nih.gov/articles/PMC9262714/
11. "DiffMS: Diffusion Generation of Molecules Conditioned on Mass Spectra", ICML 2025. https://arxiv.org/abs/2502.09571
12. Wang et al., "MADGEN: Mass-Spec attends to De Novo Molecular generation", OpenReview. https://openreview.net/forum?id=78tc3EiUrN
13. "Neural Spectral Prediction for Structure Elucidation with Tandem Mass Spectrometry" (ICEBERG), bioRxiv (2025). https://www.biorxiv.org/content/10.1101/2025.05.28.656653v1.full
14. Bushuiev et al., "MassSpecGym: A benchmark for the discovery and identification of molecules", NeurIPS 2024 Datasets and Benchmarks. https://arxiv.org/abs/2410.23326
15. "De Novo Structure Prediction from Tandem Mass Spectra" (formula conditioning improves exact match). https://pmc.ncbi.nlm.nih.gov/articles/PMC12985711/
