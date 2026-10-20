# H2RES climate pipeline

Generate annual hourly solar capacity factors, wind capacity factors and
temperature profiles, select representative sensitivity cases and prepare
generation-unit and temperature-dependent inputs for H2RES.

Solar and temperature use autoregressive XGBoost quantile models. Wind uses
seasonal historical block resampling. Channels are generated independently.
The H2RES optimizer, solver licence and solver database are separate.

## Install

Use Python 3.11 and run from this repository:

```bash
python -m pip install -r requirements.txt
python -m pip install -e . --no-deps
python -m unittest discover -s tests -v
python audit.py
python examples/demo.py --output exports/demo_01
```

The offline demo uses illustrative inputs, not research results. It exercises
selection, mapping, CSV export and thermal coupling without a solver or model
training. Use a new output directory for each run.

## Generate with the delivered model

The single delivered bundle is `models/default/`. National CF calibration
uses 1980-2015; model stopping and residual calibration use 2016-2019.
Generation needs the exact input corpus for the training climatology and
wind-resampling pool, in addition to the supplied native tree models:

```bash
python pipeline.py generate --data data/SC/HR_data_training_calibrated.parquet --count 100 --seed 42 --output exports/profiles.npy
python pipeline.py select --profiles exports/profiles.npy --method orthogonal --output exports/cases.npz
```

The corpus SHA256 is
`9a21558378d6359f308e3b7b7a3068d053195fa9123792c1d8aa9ac177101022`.
Raw source data are not redistributed. For other datasets, fit a new bundle
using [USAGE.md](docs/USAGE.md); data contracts are in [DATA.md](docs/DATA.md).

Arrays have shape `[number_of_profiles, 8760, 3]`, channel order
`solar, wind, temperature` and units CF, CF and degrees Celsius. Leap days
are removed. Wind speed and irradiance are not electrical capacity factors.

## H2RES integration

`h2res_interface.py` prepares availability dictionaries or CSVs and temperature
coupling. Unit names and baseline inputs are supplied explicitly. Annual
heat/cooling totals are preserved; temperature changes hourly shape and COP.
The interface does not change solver constraints or silently create units.
See [INTEGRATION.md](docs/INTEGRATION.md) for complete examples.

Scenario selection requires `orthogonal` or `joint_medoid` explicitly.
These select designed sensitivities; low/average/high labels and cluster
shares are not calibrated joint-weather probabilities. Spatial mapping
conserves the declared capacity-weighted aggregate within its tolerance.

## Results and verification

[Research results](docs/RESEARCH_RESULTS.md) distinguish central-profile error
from individual-profile diversity. [Validation coverage](docs/VALIDATION.md)
describes executable checks and interpretation. Reporting years 2020-2024
were previously inspected; model fitting and calibration use earlier years.
No claim of an untouched test, calibrated joint-weather distribution or
universal superiority is made.

| Path | Contents |
|---|---|
| `pipeline.py`, `h2res_interface.py` | CLI and integration adapter |
| `scripts/` | Generator, selection, mapping and thermal methods |
| `models/default/` | Six native quantile models and calibration metadata |
| `examples/`, `tests/` | Offline example and regression checks |
| `results/`, `provenance/` | Metrics, model identities and verification records |
| `docs/` | Usage, schemas, integration and evaluation scope |

Technical reproducibility does not assign reuse rights. See [RIGHTS.md](RIGHTS.md).
