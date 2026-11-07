# Getting started

This repository prepares hourly climate inputs for H2RES. Its included trained
models and profiles represent the Croatian case study. The input adapters can
be used with other unit names and countries when suitable local data are supplied.
Mapping Croatian profiles to another country does not calibrate its climate.

## Install a checkout

Install Git and Python 3.11, then clone the repository:

```bash
git clone https://github.com/Roko55/H2RES-climate-pipeline.git
cd H2RES-climate-pipeline
```

Create and activate a virtual environment using the command for your shell.
On Windows PowerShell:

```powershell
py -3.11 -m venv .venv
.venv\Scripts\Activate.ps1
```

On Linux/macOS:

```bash
python3.11 -m venv .venv
source .venv/bin/activate
```

Install into that environment:

```bash
python -m pip install -r requirements.txt
python -m pip install -e . --no-deps
```

Run CLI commands from the checkout root. Use the same environment for your H2RES
project when importing `h2res_interface`. The installed adapter can be imported
from another working directory; the CLI, frozen models and evidence use this
checkout's files. Installing the Python adapter alone does not copy the result
archive into another project.

## Choose the workflow

| Task | Entry point | Additional inputs |
|---|---|---|
| Inspect or select included profiles | `select-final` below | None |
| Try a complete input-preparation example | `python examples/demo.py --output exports/first_demo` | None; creates illustrative country ZZ data |
| Verify archived results | `python audit.py` | None; no optimizer invoked |
| Reproduce the manuscript summary tables | `python analysis/manuscript_results.py --output exports/first_tables` | None; no optimizer invoked |
| Map profiles to actual generation units | `export-units`; [Usage](USAGE.md) | Your unit capacities, regional CF profiles and transfer weights |
| Regenerate the accepted solar channel | `python pipeline.py replay --output exports/first_replay.npy` | None; retains stored wind and temperature |
| Regenerate all three channels | `generate`; [Usage](USAGE.md) | The original hash-bound weather corpus, excluded from the repository |
| Re-solve the energy experiments | Original H2RES runtime | Full original database and licensed optimizer, supplied separately |

Output paths must be new and located beneath `exports/`. Choose a different name
on subsequent runs; existing files and sidecars are not overwritten.

## Use the included profiles without regeneration

```bash
python pipeline.py select-final --design channel --year 2030 --output exports/first_channel_2030.npz
```

The NPZ contains seven arrays: `average`, `solar_low`, `solar_high`, `wind_low`,
`wind_high`, `temperature_cold`, `temperature_hot` (the manuscript's Warm case).
Each has shape `[8760,3]`: solar CF, wind CF, temperature in degrees C.
Rows are consecutive UTC non-leap calendar hours; row 0 is January 1 at 00:00.
Profiles are weather/CF inputs, not MW generation or optimized investments.
Future anchor years are 2025, 2030, 2035, 2040, 2045 and 2050; weather is stationary
across these anchors. The common historical 2020 calibration is separate.

Inspect the export in Python:

```python
import numpy as np

with np.load("exports/first_channel_2030.npz", allow_pickle=False) as archive:
    profile = archive["average"].copy()
solar_cf, wind_cf, temperature_c = profile.T
assert profile.shape == (8760, 3)
```

Alternatively, `np.load("profiles/accepted.npy", allow_pickle=False)` loads the
included `[100,8760,3]` generated ensemble. Neither operation needs retraining,
the raw weather corpus or a solver licence.

For the three separate electricity planning portfolios:

```bash
python pipeline.py select-final --design electricity --year 2030 --output exports/first_electricity_2030.npz
```

This exports `low`, `avg`, `high` frozen synthetic planning profiles. It does not
export the historical operating weather, solved portfolio capacities or the
seven national paths. The 45 operating checks are archived results under
`results/operational_checks.csv`; their planning year remains 2030 while their
weather years are 2020–2024. See [result columns](RESULT_COLUMNS.md).

## Integrate with your H2RES project

Follow [the mapping input contracts](DATA.md) and [integration example](INTEGRATION.md).
The mapper produces one 8760-element CF vector per generation unit. Availability
keys are `(unit,period,year)` with periods 1–8760; names must match your initialized
solver database. Temperature is the third profile column and is coupled separately
to explicit heat/cooling demand and COP baselines.

The demo's ZZ data demonstrate the interface only. For actual Croatian runs,
provide the required HR mapping inputs; the command does not download them.
For another country, provide appropriate local profiles/calibration and model
assumptions as well as mapping tables. The generic six-model `train` command is
documented separately from the frozen seven-quantile solar release protocol.
