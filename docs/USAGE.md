# Usage

Run commands from the repository root in the installed environment. Outputs under `exports/` are ignored by Git and protected against overwriting.

## Recorded model

`models/default/` is the seed-42 comparison refit, not certified original manuscript weights. Supply `data/SC/HR_data.parquet`:

```bash
python pipeline.py generate --data data/SC/HR_data.parquet --model-dir models/default --count 15 --seed 101 --output exports/profiles_101.npy
```

Expected SHA256: `d92748ca6625d0092b073b9bc16345da960a04c42da29926da5b125a63ad122d`. Historical data supply the training climatology and seasonal wind pool. Model metadata binds exact file bytes, not just equivalent column values. Updated or differently serialized data require a corresponding newly fitted bundle.

## Training-period calibrated model

The current six-model bundle is `models/training_20261019/`. Its archive is `HR_data_training_calibrated.parquet`, SHA256 `9a21558378d6359f308e3b7b7a3068d053195fa9123792c1d8aa9ac177101022`. National CF targets and reference weather use 1980-2015; model stopping and residual calibration use 2016-2019. Reporting years 2020-2024 were previously inspected. The raw archive is not redistributed.

```bash
python pipeline.py generate --data data/SC/HR_data_training_calibrated.parquet --count 100 --seed 42 --output exports/training_profiles_42.npy
```

The recorded frozen ensemble has SHA256 `0196ff09652440a1a5365a7d962f457dfab44839b2d3bc97c4dd83394b3b5fb9`. Saved-model replay can differ across environments; retain the frozen array for exact energy-input reproduction. Native model conversion is verified against the saved estimators on a fixed feature fixture. Profile diagnostics and replay measurements are under `results/training_20261019/` and `provenance/training_20261019/`.

To prepare the archive from the original Croatian raw-cache folder and IRENA workbook:

```bash
python pipeline.py calibrate --source-dir data/SC --output exports/training_archive
python pipeline.py train --data exports/training_archive/HR_data_training_calibrated.parquet --seed 42 --latitude 44.52 --output exports/training_fit
python pipeline.py generate --data exports/training_archive/HR_data_training_calibrated.parquet --model-dir exports/training_fit --count 100 --seed 42 --output exports/new_training_profiles.npy
```

The preparation command requires the original zone/site metadata, raw Parquet caches, calibration metadata and `IRENA_Statistics_Extract_2025H2.xlsx` in that source folder. It preserves the supplied archive and refuses an existing output directory. Parquet serialization can change file hashes; a newly serialized corpus must be paired with its own fit rather than bypassing a recorded bundle's hash checks.

## Own-data workflow

Prepare at least ten complete consecutive calendar years using [DATA.md](DATA.md). Supply a representative latitude for solar geometry; `45.0` below is an example:

```bash
python pipeline.py train --data data/my_climate.parquet --seed 42 --latitude 45.0 --output exports/my_fit
python pipeline.py generate --data data/my_climate.parquet --model-dir exports/my_fit --count 100 --seed 101 --output exports/my_profiles.npy
python pipeline.py select --profiles exports/my_profiles.npy --method orthogonal --output exports/my_cases.npz
```

Chronological partitions are 80% training, 10% validation and the remaining complete years for reporting, using integer year counts. Climatology and wind pools use training only; stopping and residual/drift calibration use validation. Upstream conversion/calibration requires a separate audit for user-supplied data.

Six quantile models use one CPU thread and the delivered default hyperparameters. Annual calibration simulations can remain busy after tree fitting. Runtime is dataset-dependent; archived GPU preparation time is not an estimate for this CPU command. Legacy search/diagnostic entry points are outside the maintained CLI.

## Selection output

`my_cases.npz` contains `[8760, 3]` arrays. Single-axis keys are `low_yield`, `avg_yield`, `high_yield`, `cold`, `normal`, `hot`; nine combined keys include `avg_yield__normal`:

```python
import numpy as np
with np.load("exports/my_cases.npz", allow_pickle=False) as cases:
    selected = cases["avg_yield__normal"]
    solar, wind, temperature = selected[:, 0], selected[:, 1], selected[:, 2]
```

The JSON sidecar records hashes, method, representative indices and cluster summaries. Orthogonal cases fix renewable representatives across temperature cases. Joint medoids use whole members and record empty-cluster fallback. Neither assigns calibrated event probabilities. The maintained command applies no future climate scaling.

## Spatial export

Supply capacities, zone histories and transfer tables. `CC` is a two-letter uppercase project/country code. Macro mapping supports arbitrary named regions:

```bash
python pipeline.py export-units --scenarios exports/my_cases.npz --scenario avg_yield__normal --mapping-dir data/my_mapping --country CC --wind-resolution macro --exclude-hror --output exports/my_units.npz
```

Without `--exclude-hror`, provide fixed hydro availability. Explicit `nuts3` requires NUTS3 artifacts. `auto` requires site metadata and records its selected mode; the source policy prefers NUTS3 at up to six active zones and sixty sites, with available-artifact fallbacks. Explicit `nuts3` never silently becomes macro.

Spatial climatologies use observations through `--reference-end-year` (default 2015). Set this explicitly to the training cutoff of another dataset. Every non-leap hour must be covered within the chosen reference period.

Transfer rows must cover every unit and reference real zone columns. Nonnegative relative weights are normalized per unit. Invalid weights and implicit replacements are rejected. Export fails when mean absolute capacity-weighted CF error exceeds `--mass-tolerance` (default `0.0001` CF). This is a mapping tolerance, not model accuracy.

## Troubleshooting

| Symptom | Action |
|---|---|
| Package import fails | Use the Python environment where requirements were installed. |
| Missing corpus | Run the offline demo or supply permitted data / fit a new bundle. |
| Data hash differs | Use the bundle's exact original input or fit a new bundle. |
| Source/artifact hash differs | Restore the intended commit before replay; development changes need a versioned provenance update. |
| Existing output | Choose a new path under `exports/`. |
| Timestamp error | Supply ordered hourly timestamps including leap days; missing hours are not interpolated. |
| Transfer unit / zone error | Match unit names and profile columns exactly; inspect demo input files. |
| Mapping mass error | Check capacities, mappings and clipping. A larger tolerance needs justification. |
| Missing runtime keys | Align units, planning year and one-based periods before injection. |

Use `python pipeline.py <command> --help` for exact arguments.
