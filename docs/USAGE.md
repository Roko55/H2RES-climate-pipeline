# Usage

Run commands from the repository root. Outputs under `exports/` are ignored
by Git and protected against overwriting.

## Delivered model

`models/default/` requires `HR_data_training_calibrated.parquet`, SHA256
`9a21558378d6359f308e3b7b7a3068d053195fa9123792c1d8aa9ac177101022`.
National CF targets and reference weather use 1980-2015. Stopping and
residual and annual-rollout calibration use 2016-2019. Reporting years 2020-2024 were previously
inspected and do not enter fitting or calibration.

```bash
python pipeline.py generate --data data/SC/HR_data_training_calibrated.parquet --count 100 --seed 42 --output exports/profiles.npy
```

Trees require the training climatology and wind pool supplied by the exact
corpus. The frozen research ensemble SHA256 is
`1737c4704e3fa412f521d9e9059362be183d70e2539d891d5f5969de0f6f3aae`.
Native CLI generation reproduces this array bit for bit in the recorded runtime.
Autoregressive numerical differences can accumulate between runtimes. Use the
frozen array and corresponding solver configuration for exact reproduction of
reported energy inputs. Replay measurements are in `provenance/model/`.

To prepare data from the permitted Croatian raw cache and IRENA workbook:

```bash
python pipeline.py calibrate --source-dir data/SC --output exports/archive
python pipeline.py train --data exports/archive/HR_data_training_calibrated.parquet --seed 42 --latitude 44.52 --longitude 16.798 --solar-sites data/solar_sites.csv --output exports/fit
python pipeline.py generate --data exports/archive/HR_data_training_calibrated.parquet --model-dir exports/fit --count 100 --seed 42 --output exports/new_profiles.npy
```

`data/solar_sites.csv` must contain the actual source-site `lat,lon` columns;
the Croatian research coordinates are (43.788,16.001), (45.311,18.235) and
(45.865,16.206). Solar timestamps are UTC interval starts. Preparation requires
the original zone/site metadata, Parquet caches and
`IRENA_Statistics_Extract_2025H2.xlsx`. It refuses an existing output directory.
Serialization can change hashes: pair a newly serialized corpus with its own
fit instead of bypassing the recorded bundle's identity checks.

## Other datasets

Supply at least ten complete consecutive years according to [DATA.md](DATA.md)
and appropriate coordinates. The following coordinates are illustrative:

```bash
python pipeline.py train --data data/my_climate.parquet --seed 42 --latitude 45.0 --longitude 16.0 --output exports/my_fit
python pipeline.py generate --data data/my_climate.parquet --model-dir exports/my_fit --count 100 --seed 101 --output exports/my_profiles.npy
python pipeline.py select --profiles exports/my_profiles.npy --method orthogonal --output exports/my_cases.npz
```

The split is chronological: 80% training, 10% validation and the remaining
complete years for reporting, using integer year counts. Climatology and wind
blocks use training; stopping and residual/drift calibration use validation.
User-supplied upstream calibration requires its own temporal-boundary check.
Training and prediction use two CPU threads. Full annual calibration is part
of the default training command and can outlast tree fitting. A bundle created
with `--conditional-fit-only` is a fitting fixture and cannot generate until
annual calibration is performed. No universal runtime estimate is implied.

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
