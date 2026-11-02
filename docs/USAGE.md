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
The analysis command reproduces summary CSVs and PNG/PDF figures from the
delivered result tables. See [CHANNEL_RESULTS.md](CHANNEL_RESULTS.md) for units
and experiment definitions.

## Combined-resource and separate-application experiments

```bash
python pipeline.py replay --output exports/accepted.npy
python pipeline.py select-final --design multisector --year 2030 --output exports/multisector_2030.npz
python pipeline.py select-final --design electricity --year 2030 --output exports/electricity_2030.npz
```

The accepted array is also included directly in `profiles/accepted.npy`; `replay`
independently regenerates its solar channel. For these two designs, each selected NPZ contains `low`,
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
resampling pool. It is not bundled. Wrong hashes are rejected. Full three-channel
regeneration through this portable command requires a corpus-enabled verification;
the dataset-free release test verifies solar replay and retained cohort identity.
Other seeds/counts do not reproduce the published H2RES experiments.

## Training on another dataset

`train` fits a six-model reference bundle (three quantiles per autoregressive
channel). `--data`, `--latitude`, `--longitude` and `--output` are required;
`--solar-sites` accepts a CSV with `lat,lon` columns. This is a generic baseline,
**not** a reproduction of the accepted seven-quantile solar training/selection
protocol. Its calibration helper also does not recreate the final Nordex wind
conversion. Do not label custom outputs as the accepted study model.
The accepted solar protocol and selected configuration are recorded in
`provenance/solar/`; the executed base engine/calibration source is preserved in
`provenance/executed_final/`. Full retraining needs licensed source data and an
independent validation of the complete training/selection workflow.

`select` provides generic `orthogonal` and `joint_medoid` case selection on a
caller-provided ensemble. For the published final experiments use `select-final`.
