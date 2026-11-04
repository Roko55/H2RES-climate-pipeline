# H2RES integration

The package prepares inputs for a separately installed H2RES model and solver.
Run the offline walkthrough first: `python examples/demo.py --output exports/demo_01`.
The fixture contains illustrative weather, country and unit tables.

## Availability adapter

```python
import numpy as np
from h2res_interface import availability_frame, availability_parameters, inject_availability

with np.load("exports/units_2030.npz", allow_pickle=False) as archive:
    units = {name: archive[name] for name in archive.files}
frame = availability_frame(units, 2030, expected_units=set(units))
frame.to_csv("exports/availability_2030.csv", index=False)
updates = availability_parameters(units, 2030)
# In your initialized solver project:
# aval_factor_plant = inject_availability(aval_factor_plant, updates)
```

Availability keys are `(unit,period,year)`, periods 1..8760. Names must agree with
the initialized solver database; `inject_availability` rejects missing keys.
Use MW capacities consistently; CF itself is dimensionless. The installed
`h2res_climate` namespace avoids collision with an H2RES project's `scripts` folder.
The command-line workflow still runs from this repository checkout.

## Thermal coupling and experiment designs

For the individual-channel multisector experiments, use
`select-final --design channel --year 2030`. It exports all seven named cases
with complete Cold/Warm temperature chronologies. Each case retains the same
8760-hour weather profile at every planning anchor, without additional trends. Apply the existing multisector
thermal adapter with `scale_annual_totals=False`. Each path inherits its own
realized stock after the common 2020 start. See [CHANNEL_RESULTS.md](CHANNEL_RESULTS.md).

`h2res_interface.couple_temperature` is the explicit-baseline multisector adapter.
It returns new heat/cooling/COP dictionaries; callers supply temperature baselines,
unit lists and complete annual dictionaries. See `examples/demo.py` for a complete
call. It preserves annual thermal demand, changing hourly shape and COP.

The separate 2030 electricity portfolios use `select-final --design electricity`.
Their 45 operating checks retain the fixed capacities and the historical weather
years. They are separate from the seven multisector planning paths.

For explicit-baseline access to the prepared-demand utilities, the installed
`h2res_climate.prepared_thermal` module exposes
`_reshape_prepared_temperature_demand` and `_temperature_sensitive_cop`.
Demand uses 18/22 C drivers, bounded and smoothed multipliers,
annual-total restoration and optional peak caps. COP scales the existing baseline
by a Carnot-ratio response, restores its arithmetic mean **before** clipping to
1.2..5.5; clipping can alter the final mean. The caller supplies unit groups,
baseline demand and COP, and baseline/scenario temperatures.

Reproducing the archived energy optimizations requires the original solver
database, stock-updating controller and licensed optimizer. The climate demo
covers input preparation with illustrative data.
