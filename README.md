# H2RES climate pipeline

Generate hourly solar and wind capacity factors and ambient temperature, select
annual sensitivity profiles and prepare climate inputs for H2RES.

Solar uses seven autoregressive XGBoost quantile models with fixed seasonal
postprocessing. Temperature uses three autoregressive quantile models. Wind uses
six-day seasonal historical blocks with four-hour blending. Channels are generated
independently. Profiles use UTC, 8760 hours per year with February 29 removed,
and channel order **solar CF, wind CF, temperature in degrees Celsius**.
The included models and profiles represent the Croatian case study. Adapting
the software to another country requires suitable local climate and mapping data.

## Installation

Follow [Getting started](docs/GETTING_STARTED.md) for cloning, virtual-environment
setup and choosing a workflow. Use Python 3.11. From this checkout:

```bash
python -m pip install -r requirements.txt
python -m pip install -e . --no-deps
python -m unittest discover -s tests -v
python audit.py
python examples/demo.py --output exports/demo_01
```

The demo uses illustrative weather and unit tables. It runs selection, spatial
mapping, availability export and thermal coupling. Use a new output name on reruns.

## Profiles and H2RES inputs

The workflow has four steps:

1. Generate annual solar, wind and temperature profiles, or load the included ensemble.
2. Select complete annual profiles for the defined sensitivity cases.
3. Map aggregate solar/wind CF to generation units using training-period regional
   profiles, transfer weights and unit capacities. Capacity-weighted aggregation
   recovers the input CF within the checked tolerance.
4. Apply temperature-dependent annual and hourly heating/cooling needs and heat-pump COP,
   then export the availability and thermal parameter dictionaries for H2RES.

```bash
python pipeline.py select-final --design channel --year 2030 --output exports/channel_cases_2030.npz
```

The included ensemble is `profiles/accepted.npy`, shape `[100,8760,3]`, float32.
`replay` regenerates solar from its native models and retains the stored wind
and temperature channels. Full generation additionally needs the original corpus,
including the wind resampling pool; see [Usage](docs/USAGE.md).
Selecting or loading the included profiles needs no regeneration, retraining,
raw weather or optimizer.

The `channel` design exports Average, Solar Low/High, Wind Low/High and Cold/Warm.
Each case changes one complete hourly channel from the same Average profile.
The multisector heat/cooling/COP adapter then applies the temperature chronology.
In the archived multisector runs, all seven paths start from common 2020 stock
and carry their own subsequent investments and retirements through 2050. Their
stationary-weather inputs are defined in [CHANNEL_RESULTS.md](docs/CHANNEL_RESULTS.md).

Map these profiles to your declared generation units with `export-units`, then use
`h2res_interface.py` for availability dictionaries or CSVs. See the
[data schemas](docs/DATA.md) and [integration examples](docs/INTEGRATION.md).
The current national extract and source audit are under
[`evidence/final`](evidence/final/README.md). Earlier national cohorts and
unchanged zonal operating evidence remain under [`evidence/calibrated`](evidence/calibrated/README.md). Re-solving the energy
experiments additionally requires the original input database and licensed optimizer.

## Current manuscript results

Release 0.10.1 matches the final manuscript prepared for Energy and AI. Climate
models and the accepted ensemble are unchanged. The current national cohort is
national_production_luka_4h_20261113_01: seven planning paths, 42 accepted
windows in 2025-2050 and one common calibrated 2020 base.

- New gas plants and gas boilers are excluded from 2025; existing boilers remain.
- Electricity/heating have direct-emissions limits; industry follows fixed inputs.
- Exchange has hourly infrastructure limits and no annual net-import quota.
- Capital costs use annual payments at 5% over executed technology lifetimes.
- New stationary battery additions use four-hour grid duration.
- Temperature changes annual/hourly heating, cooling and heat-pump COP.

[Current results](docs/RESEARCH_RESULTS.md), [column definitions](docs/RESULT_COLUMNS.md)
and [model assumptions](evidence/final/model_assumptions.json) document the scope.

```bash
python analysis/manuscript_results.py --output exports/current_results
```

This checks the compact extract against the preserved source review and
recreates results/final CSVs without running H2RES.
Recreate the ten national-result plots with:
`python analysis/final_figures.py --output exports/current_figures`. The original native
database and full national state stores are external.

Average adds 10.71 GW solar PV, 4.41 GW wind and 3.62 GW heat pumps.
Cold requires 4.04 GW heat pumps and Warm 3.31 GW. Average net imports
are 2.37 TWh in 2025 and 4.35 TWh in 2050. All seven national paths cover
electricity and both forms of heating. Wind cost ranking follows yield;
solar ranking also depends on investment timing. Solar Low temporarily
reaches 33.4% net-import share in 2035; this result is retained.

The original 45 fixed-portfolio operating checks are a separate three-zone
electricity experiment. All 30 full/half-exchange cases cover HR demand.
Three zero-exchange cases in 2021 have annual unserved energy of
0.0000704 TWh (Average), 0.0001582 TWh (Low) and 0.0000652 TWh (High).
All 45 checks are retained; they do not test the seven new national portfolios.

## Figures and provenance

provenance/manuscript/scope.json records the manuscript SHA256, exact 17
embedded PNGs and separate native PDF exports. Current tables are in
results/final. Earlier results/channel_sensitivity, evidence/thermal and
evidence/calibrated remain archived snapshots with their original schemas.
Use the current reproduction command above for the final manuscript.

## Thermal coupling and reproducibility

couple_temperature_v62 remains the Croatian weather-dependent adapter;
the function name is retained for API compatibility. Use data/weather_energy.
Reference coefficients are calibrated once to 2020, then annual needs change
with temperature. The generic couple_temperature helper still supports
separate fixed-total experiments.

Training is 1980-2015, validation 2016-2019, reporting 2020-2024. Reporting
years had previously been inspected and are retrospective. The channel
cases are controlled sensitivities. [Verification](docs/VALIDATION.md)
describes the checks. Raw weather and complete native inputs are excluded;
a new energy solve requires those external inputs and a licensed optimizer.
Public result reproduction does not require an optimizer.

The [autoencoder archive](https://github.com/Roko55/H2RES-autoencoder/tree/v0.1.0)
is unchanged and remains an archived same-corpus comparison.
For a fixed reference use [v0.10.1](https://github.com/Roko55/H2RES-climate-pipeline/tree/v0.10.1).
Source/dependency rights remain in [RIGHTS.md](RIGHTS.md).
