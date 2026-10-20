# Implementation and reproducibility audit

## Model and manuscript correspondence

The implementation matches the manuscript's method families: independent autoregressive XGBoost quantile models for solar and temperature, seasonal block resampling for wind, annual resource/thermal case selection, source-zone mapping to H2RES units and temperature-dependent demand/COP coupling. `provenance/manuscript_correspondence.json` identifies the document version and hash used for this review. This is a method correspondence check, not a new validation of planning outcomes.

The default six native model files are the recorded seed-42 comparison refit. Their source/data hashes, calibration and fitting measurements are retained. They are not certified original manuscript weights. Archived comparison CSVs retain all model cases, including those favourable to the autoencoder, and are not relabelled as new results.

## Causal-feature correction

In the original `_past_rolling`, warmup filling used the mean of the full supplied partition. After shifting and dropping the first 168 hours, the first retained 168-hour rolling feature still contained that full-partition mean. Later valid rolling windows used past values. This affects one retained row per partition: within validation/reporting, that feature included later observations of the same partition. Training and reporting partitions were not concatenated for fitting.

The maintained rolling feature averages only values strictly before the target hour. The exact original model source is preserved under `provenance/executed/`; its results and models remain original-version results. A separate 20261019 refit uses training-period national CF calibration and the maintained causal features. Its models, metrics and input identity are recorded separately under `models/training_20261019/`, `results/training_20261019/` and `provenance/training_20261019/`. Earlier measurements are not relabelled as results of that refit.

Rolling training features are not used in autoregressive generation from loaded trees; generation builds features from generated history. The second maintained change rejects a missing wind training pool rather than returning random noise. The validated entry point always reconstructs the wind pool from the exact input's training partition. Source compatibility is explicitly hash-bound in `provenance/generation_compatibility.json`; a controlled replay of five years (seed 101) gives bitwise identical original-versus-maintained generation on the same runtime. Replay against the archived faculty arrays on the local Python 3.9 / NumPy 2.0.2 runtime has maximum differences of approximately 6e-8 solar CF, zero wind CF and 0.00920 degrees C. The cause of this cross-runtime difference has not been isolated; exact archived replay is not claimed for this runtime. See `provenance/generation_replay.json`.

## Entry-point checks

The maintained `pipeline.py` rejects unknown CLI options, incomplete/duplicate/gapped source timestamps, nonfinite data, invalid capacity factors, missing or changed native models, changed model metadata, incompatible source code, output overwrites and output paths outside `exports/`. Native XGBoost files avoid loading pickled estimator objects. Training is an explicit command, CPU-only with one thread. Generation does not call the legacy GPU autodetection or automatic training path.

Scenario selection requires an existing validated ensemble and an explicit `orthogonal` or `joint_medoid` method. It cannot fall back to implicit generation. Orthogonal cases keep the renewable and thermal representative channels fixed across the appropriate axis. Spatial export requires explicit country unit inputs, solar spatial artifacts, valid unit arrays and a finite capacity-weighted mapping error within tolerance.

## Test coverage and limits

Synthetic tests cover future-weather perturbation of training climatology, feature causality, seeded wind bootstrap replay and bounds, chronological input rejection, invalid profile rejection, strict CLI parsing, model/data mismatch, output protection, orthogonal channel invariance, spatial mapping conservation on a controlled fixture and annual heat-demand total preservation. They do not replace raw-data evaluation or an H2RES solver run.

The shared source corpus has upstream calibration overlap with reporting years. All delivered comparison results remain retrospective. Annual wind diversity motivates the application comparison; physically coherent joint weather, calibrated tail probabilities, global model optimality and better investment outcomes are not established. Both generators have annual-variability limitations. Source spatial ratio clipping and missing-data legacy fallbacks require the explicit integrating-data checks described in the integration guide.

## Actual-data interface check

The standalone commands generated five annual profiles from the recorded native models and exact corpus, selected orthogonal cases and exported the average-yield/normal case to nine unit arrays (three solar, five wind, one hror). Solar and wind mean absolute capacity-weighted mapping errors were below the 1e-4 tolerance. The input hashes and numerical diagnostics are retained in `provenance/spatial_export_verification.json`. This is a five-member interface smoke test, not a regenerated manuscript ensemble or solver validation.

## Cross-project interface extensions

The reusable interface includes a complete offline demonstration on labelled illustrative signals, a wide availability CSV and a `(unit, period, year)` parameter adapter. Injection rejects absent runtime keys and preserves unrelated entries. The thermal adapter requires explicit baseline temperature and complete baseline demand/COP keys, returns copies and preserves annual demand totals. It does not depend on Croatian unit names or implicit country-file lookup.

The temperature module adds an optional explicit-baseline argument; the scenario manager adds optional hydro exclusion. Their original default behavior is retained and exact earlier versions are under `provenance/executed/`. These API extensions do not replace recorded research results. The strict exporter validates transfer rows, weights, zone coverage and input calendars before legacy loaders can apply fallbacks. Explicit NUTS3 requests cannot become macro exports silently. New training requires an explicit latitude.

Generator library import no longer performs country-specific configuration, creates output directories or invokes dependency installation. The maintained entry point supplies dataset paths and latitude. The installed adapter resolves its thermal code through the distinct `h2res_climate` package to avoid a collision with an integrating H2RES project's own `scripts` package.

Twenty regression tests and the executable offline walkthrough cover these contracts. The stricter exporter was also rerun with existing Croatian inputs. Full calibration after a corrected-model refit, arbitrary external H2RES variants and unrestricted reuse permissions remain outside that evidence; see [validation coverage](docs/VALIDATION.md).
