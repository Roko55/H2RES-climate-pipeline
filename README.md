# H2RES climate pipeline

Prepare annual hourly solar capacity factors, wind capacity factors and ambient
temperature for H2RES. This release contains the **accepted final ensemble used
in the completed planning and operational experiments**, its native models,
input adapters and numerical verification records.

## Install and check

Use Python 3.11 in a separate virtual environment. Run from this checkout:

```bash
python -m pip install -r requirements.txt
python -m pip install -e . --no-deps
python -m unittest discover -s tests -v
python audit.py
python examples/demo.py --output exports/demo_01
```

The demo uses fabricated inputs and calls no solver or training routine.
Use a new output name for each run. All CLI outputs stay under `exports/`.

## Reproduce the accepted profiles

```bash
python pipeline.py replay --output exports/accepted.npy
python pipeline.py select-final --design multisector --year 2030 --output exports/cases_2030.npz
```

`replay` regenerates solar from seven native quantile models and checks exact
array identity. Wind and temperature are retained from the included, hash-checked
accepted ensemble; this command does **not** claim to regenerate those channels.
It needs no external dataset or optimizer. Arrays have shape `[100, 8760, 3]`,
channel order **solar CF, wind CF, temperature in degrees Celsius**, dtype float32.
Leap days are removed; the hourly clock is UTC. Treat individual paths as
alternative weather years, not forecasts of particular observed years.

The ensemble SHA256 is
`d86a855166c24d699d511010184db12d0baf1551f83254b41f661c602d7ae728`.
This release supersedes the older fixed-drift bundle preserved at `v0.3.1`.

## Use with H2RES

Export selected cases to your explicitly named solver units, then use
`h2res_interface.py` for validated availability dictionaries or CSVs.
[Usage](docs/USAGE.md), [input schemas](docs/DATA.md) and
[integration examples](docs/INTEGRATION.md) explain the required files.
The H2RES optimizer, solver licence and solver database are separate.

The final multisector experiment holds Average temperature/heat/COP common
within each planning anchor. Electricity and thermal experiments use their own
executed selector and thermal adapter. These designs must not be interchanged.
Low/Average/High are designed sensitivities, not calibrated event probabilities.

## Scientific scope

[Results](docs/RESEARCH_RESULTS.md) identify the final 63 planning windows and
45 operational cases. [Verification](docs/VALIDATION.md) separates executable
replay from archived optimizer evidence. Reporting years 2020-2024 had previously
been inspected and are retrospective. There is no claim of an untouched test,
universal model superiority or calibrated joint-weather distribution.

The [autoencoder study](https://github.com/Roko55/H2RES-autoencoder/tree/ee89222)
is a separate, historical comparison; its comparator is not this final solar
release. Its variability results remain unchanged.

| Directory | Contents |
|---|---|
| `models/default/` | Seven solar and three temperature native XGBoost models |
| `profiles/accepted.npy` | Exact accepted 100-member final ensemble |
| `scripts/` | Portable generation, selection, spatial and thermal methods |
| `results/` | Final profile and energy-system result tables |
| `provenance/` | Frozen protocols, identities and executed adapter sources |
| `examples/`, `tests/` | Offline walkthrough and regression checks |

Raw weather and full solver inputs are not redistributed. No new licence is
granted by this release; consult [RIGHTS.md](RIGHTS.md) before reuse.
