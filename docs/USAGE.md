# Usage

Run commands from the repository root. Outputs under `exports/` are ignored
by Git and protected against overwriting.

## Delivered model

`models/default/` requires `HR_data_training_calibrated.parquet`, SHA256
`9a21558378d6359f308e3b7b7a3068d053195fa9123792c1d8aa9ac177101022`.
National CF targets and reference weather use 1980-2015. Stopping and
residual calibration use 2016-2019. Reporting years 2020-2024 were previously
inspected and do not enter fitting or calibration.

```bash
python pipeline.py generate --data data/SC/HR_data_training_calibrated.parquet --count 100 --seed 42 --output exports/profiles.npy
```

Trees require the training climatology and wind pool supplied by the exact
corpus. The frozen research ensemble SHA256 is
`0196ff09652440a1a5365a7d962f457dfab44839b2d3bc97c4dd83394b3b5fb9`.
Autoregressive numerical differences can accumulate between runtimes. Use the
frozen array and corresponding solver configuration for exact reproduction of
reported energy inputs. Replay measurements are in `provenance/model/`.

To prepare data from the permitted Croatian raw cache and IRENA workbook:

```bash
python pipeline.py calibrate --source-dir data/SC --output exports/archive
python pipeline.py train --data exports/archive/HR_data_training_calibrated.parquet --seed 42 --latitude 44.52 --output exports/fit
python pipeline.py generate --data exports/archive/HR_data_training_calibrated.parquet --model-dir exports/fit --count 100 --seed 42 --output exports/new_profiles.npy
```

Preparation requires the original zone/site metadata, Parquet caches and
`IRENA_Statistics_Extract_2025H2.xlsx`. It refuses an existing output directory.
Serialization can change hashes: pair a newly serialized corpus with its own
fit instead of bypassing the recorded bundle's identity checks.

## Other datasets

Supply at least ten complete consecutive years according to [DATA.md](DATA.md)
and an appropriate latitude. The following latitude is illustrative:

```bash
python pipeline.py train --data data/my_climate.parquet --seed 42 --latitude 45.0 --output exports/my_fit
python pipeline.py generate --data data/my_climate.parquet --model-dir exports/my_fit --count 100 --seed 101 --output exports/my_profiles.npy
python pipeline.py select --profiles exports/my_profiles.npy --method orthogonal --output exports/my_cases.npz
```

The split is chronological: 80% training, 10% validation and the remaining
complete years for reporting, using integer year counts. Climatology and wind
blocks use training; stopping and residual/drift calibration use validation.
User-supplied upstream calibration requires its own temporal-boundary check.
Training uses one CPU thread by default; annual calibration can outlast tree
fitting. No universal runtime estimate is implied.

## Selection and spatial export

Select `orthogonal` to combine separate renewable and thermal representatives,
or `joint_medoid` to retain whole generated annual profiles. Single-axis
arrays include `low_yield`, `avg_yield`, `high_yield`, `cold`, `normal`, `hot`;
combined arrays include `avg_yield__normal`. Each has shape `[8760,3]`.
The sidecar records source hashes, member indices and cluster shares. These
shares are descriptive, not calibrated event probabilities.

```bash
python pipeline.py export-units --scenarios exports/my_cases.npz --scenario avg_yield__normal --mapping-dir data/my_mapping --country CC --wind-resolution macro --exclude-hror --output exports/my_units.npz
```

`CC` is a project/country code. Supply actual capacities, zone profiles and
transfer weights. NUTS3 requires the corresponding artifacts; it never silently
becomes macro mapping when explicitly requested. Spatial climatologies default
to observations through 2015; use `--reference-end-year` for another training
cutoff. Default capacity-weighted mapping tolerance is 0.0001 CF, not a model
accuracy tolerance. Use [INTEGRATION.md](INTEGRATION.md) for solver dictionaries.

## Troubleshooting

Use the exact corpus matching model metadata or fit a new bundle. Missing or
unordered hours, inconsistent unit names, invalid weights and incomplete
thermal baselines are rejected. Existing outputs require a new path. Use
`python pipeline.py <command> --help` for precise arguments.
