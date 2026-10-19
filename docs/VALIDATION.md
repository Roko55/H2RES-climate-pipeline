# Validation coverage

## Executable functionality

| Check | Evidence | Boundary |
|---|---|---|
| Twenty synthetic regression tests | `python -m unittest discover -s tests -v` | Includes causality, file contracts, mapping rejection, native serialization and thermal adapters |
| Complete offline interface path | `python examples/demo.py --output exports/demo_01` | Illustrative signals, no trained-model accuracy claim |
| Native model generation | `provenance/generation_replay.json` | Five paths; same-runtime original/maintained outputs identical; cross-runtime archived temperature difference documented |
| Actual Croatian spatial interface | `provenance/spatial_export_verification.json` and reuse-review record | Nine arrays, capacity-weighted mass error within tolerance; no solver invocation |
| File and model integrity | `python audit.py` | Hash consistency, not scientific quality certification |

GitHub Actions installs pinned dependencies in a clean Ubuntu / Python 3.11 environment, runs the tests, verifies artifacts and executes the full offline walkthrough. Local development checks also ran on Windows. macOS and every external H2RES version have not been tested. Native data/model generation requires the permitted source corpus; CI does not download it.

The adapter is installed as an editable package and imported from a separate directory containing an unrelated `scripts` package in a CI check. Its thermal module uses the `h2res_climate` namespace. A wheel build was also checked. The pinned dependency set targets Python 3.11–3.12; clean runtime CI currently covers 3.11.

The small native-training test fits actual two-tree quantile models and tests serialization. Calibration is stubbed in that fixture to avoid a long annual simulation. This is not a complete corrected-model refit or a performance benchmark. The warmup correction for new fitting does not reclassify archived old scores as corrected-model scores.

## Scientific limits

The research comparison is retrospective: upstream IRENA calibration overlaps reporting years, and the reporting period had already been inspected in the broader project. The supplied corpus is not an independent temporal holdout. Existing calibrated/clipped inputs cannot simply be divided by rounded factors to reconstruct original raw inputs.

Channels are generated independently and used as designed sensitivities. No calibrated joint weather distribution, tail-event probabilities, climate projection, global optimum or general superiority of one generator is established. Spatial mapping uses source climatological ratios and can distort aggregates under clipping; the strict wrapper rejects errors beyond the declared tolerance.

The autoencoder has limited interannual wind diversity, while the hybrid also has limited annual solar/temperature diversity. Neither a small calendar MAE nor a good composite alone validates scenario diversity. No new downstream H2RES optimization result is produced by this repository's test suite.

## External dependencies and release conditions

The recorded Croatian model needs the exact input corpus for wind resampling and climatology. Other users can fit their own bundles with correctly prepared, permitted data. Unit mapping and thermal integration require their actual solver unit names and baseline dictionaries. Raw source data are not redistributed here.

Technical reproducibility is separate from reuse permission. `RIGHTS.md` records that no new licence has been assigned to underlying H2RES code or third-party data. A public, freely reusable release needs a confirmed licence and data-access arrangement from the relevant rights holders; a working repository alone does not grant those rights.
