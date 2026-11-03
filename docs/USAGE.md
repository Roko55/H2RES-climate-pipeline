# Usage

Commands are run from the repository root after installation (see README).
Run `python pipeline.py --help` for the complete interface. CLI files are written
only below `exports/`; existing outputs and JSON sidecars are never overwritten.

## Individual-channel experiment

```bash
python pipeline.py select-final --design channel --year 2030 --output exports/channel_cases_2030.npz
python analysis/channel_results.py --output exports/channel_summary
```

The channel NPZ contains `average`, `solar_low`, `solar_high`, `wind_low`,
`wind_high`, `temperature_cold`, `temperature_hot`. Each has shape `[8760,3]`.
All seven cases are checked against archived profile hashes for the requested anchor.
For this stationary-weather design, each case has identical hourly values at every
anchor from 2020 through 2050 (solar/wind multipliers 1.0/1.0, temperature offset 0 C).
The other two experiment designs below retain their original scaling conventions.
The analysis command reproduces summary CSVs and PNG/PDF figures from the
delivered result tables. See [CHANNEL_RESULTS.md](CHANNEL_RESULTS.md) for units
and experiment definitions.

## Combined-resource and separate-application experiments

```bash
python pipeline.py replay --output exports/accepted.npy
python pipeline.py select-final --design multisector --year 2030 --output exports/multisector_2030.npz
python pipeline.py select-final --design electricity --year 2030 --output exports/electricity_2030.npz
```

The array is included directly in `profiles/accepted.npy`; `replay`
regenerates its solar channel. For these two designs, each selected NPZ contains `low`,
`avg`, `high` arrays of shape `[8760,3]`. Multisector selection is checked against
all three archived input hashes at the requested anchor (2020, 2025, ..., 2050).
The electricity selector uses float64 and planning-year scaling before clustering;
the multisector selector retains the executed float32 path and scaling order.

## Your own H2RES mapping

```bash
python pipeline.py export-units --scenarios exports/multisector_2030.npz --scenario avg --mapping-dir data/SC --country HR --wind-resolution nuts3 --exclude-hror --output exports/units_2030.npz
```

Replace the mapping folder and country with your own declared inputs. Supply the
required schemas in DATA.md. Omit `--exclude-hror` only when an explicit valid
hydro availability column is present. The spatial reference ends in 2015.
Aggregate conservation and finite [0,1] bounds are verified before export.

## Full model regeneration with the original corpus

```bash
python pipeline.py generate --data data/SC/HR_data.parquet --count 100 --seed 42 --output exports/regenerated.npy
```

This requires the exact final corpus SHA256
`b700263a2868b20cc8f7fe95e0a7d22c48ea31c89cedbc0889a55ca870972d8a`.
The corpus supplies temperature climatology/initial histories and the wind
resampling pool. The release includes the derived profiles and model assets;
users provide this corpus separately. The command checks the corpus hash.
The data-free release check covers solar regeneration and complete stored-ensemble
identity. Full three-channel regeneration requires the original corpus.
It is outside the data-free verification checks.
The published experiments use 100 profiles and seed 42.

## Training on another dataset

`train` fits a six-model reference bundle (three quantiles per autoregressive
channel). `--data`, `--latitude`, `--longitude` and `--output` are required;
`--solar-sites` accepts a CSV with `lat,lon` columns. This command provides the
six-model reference configuration for custom datasets. The released solar model
uses the separate seven-quantile training/selection protocol in `provenance/solar/`.
Its executed base engine and calibration source are in `provenance/executed_final/`.
The `calibrate` command provides national CF scaling for the reference workflow;
the released wind corpus uses the Nordex speed-to-CF conversion described in
[DATA.md](DATA.md). Retraining requires the source data and validation of the
chosen configuration on the new dataset.

`select` provides generic `orthogonal` and `joint_medoid` case selection on a
caller-provided ensemble. For the published final experiments use `select-final`.
