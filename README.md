# H2RES climate pipeline

Generate hourly solar and wind capacity factors and ambient temperature, select
annual sensitivity profiles and prepare climate inputs for H2RES.

Solar uses seven autoregressive XGBoost quantile models with fixed seasonal
postprocessing. Temperature uses three autoregressive quantile models. Wind uses
six-day seasonal historical blocks with four-hour blending. Channels are generated
independently. Profiles use UTC, 8760 hours per year with February 29 removed,
and channel order **solar CF, wind CF, temperature in degrees Celsius**.

## Installation

Use Python 3.11 in a virtual environment. From this checkout:

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
4. Apply temperature-dependent heat/cooling demand shapes and heat-pump COP,
   then export the availability and thermal parameter dictionaries for H2RES.

```bash
python pipeline.py replay --output exports/accepted.npy
python pipeline.py select-final --design channel --year 2030 --output exports/channel_cases_2030.npz
```

The included ensemble is `profiles/accepted.npy`, shape `[100,8760,3]`, float32.
`replay` regenerates solar from its native models and retains the stored wind
and temperature channels. Full generation additionally needs the original corpus,
including the wind resampling pool; see [Usage](docs/USAGE.md).

The `channel` design exports Average, Solar Low/High, Wind Low/High and Cold/Hot.
Each case changes one complete hourly channel from the same Average profile.
The multisector heat/cooling/COP adapter then applies the temperature chronology.
In the archived multisector runs, all seven paths start from common 2020 stock
and carry their own subsequent investments and retirements through 2050. Their
planning-year input trends are defined in [CHANNEL_RESULTS.md](docs/CHANNEL_RESULTS.md).

Map these profiles to your declared generation units with `export-units`, then use
`h2res_interface.py` for availability dictionaries or CSVs. See the
[data schemas](docs/DATA.md) and [integration examples](docs/INTEGRATION.md).
The H2RES model, solver licence and full input database are supplied separately.

## Results

| Analysis | Scope |
|---|---|
| [Individual solar, wind and temperature](docs/CHANNEL_RESULTS.md) | Trend-based multisector sensitivities; 49 optimal windows, including 7 reused Average windows |
| [Combined resource and separate applications](docs/RESEARCH_RESULTS.md) | Earlier 63 planning windows and 45 operational tests on their original electricity portfolios |
| [Climate profile diagnostics](results/profile_metrics.csv) | Complete 43800 reporting hours, 2020-2024 |

Single-channel objective and investment results can be summarized without a solver:

```bash
python analysis/channel_results.py --output exports/channel_summary
```

![Single-channel objective differences](results/channel_investments/figures/objective_changes.png)

Each panel shows `100 * (case objective - Average objective) / abs(Average objective)`
at each planning anchor. Vertical scales differ. Each objective covers its native
planning window; the summary reports windows separately.

## Reproducibility

[Verification](docs/VALIDATION.md) covers model and result identities, profile
selection, objective reconciliation, stock, emissions and balance records.
Training uses 1980-2015 and validation 2016-2019. Reporting years 2020-2024 had
previously been inspected and are retrospective. Low/High and Cold/Hot identify
designed sensitivities. Cluster fractions describe membership in the generated ensemble.

The [autoencoder study](https://github.com/Roko55/H2RES-autoencoder/tree/v0.1.0)
retains its own model and historical comparison corpus.

For a fixed reference, cite [version 0.5.1](https://github.com/Roko55/H2RES-climate-pipeline/tree/v0.5.1).
Raw weather and full solver inputs are excluded. Reuse rights are described in
[RIGHTS.md](RIGHTS.md).
