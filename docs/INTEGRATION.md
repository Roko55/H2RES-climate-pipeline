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
with complete Cold/Hot temperature chronologies. Each case retains the same
8760-hour weather profile at every planning anchor, without additional trends. Apply the existing multisector
thermal adapter with `scale_annual_totals=False`. Each path inherits its own
realized stock after the common 2020 start. See [CHANNEL_RESULTS.md](CHANNEL_RESULTS.md).

`h2res_interface.couple_temperature` is the explicit-baseline multisector adapter.
It returns new heat/cooling/COP dictionaries; callers supply temperature baselines,
unit lists and complete annual dictionaries. See `examples/demo.py` for a complete
call. It preserves annual thermal demand, changing hourly shape and COP.

The combined-resource multisector study uses `select-final --design multisector` and holds
Average temperature, heat/cooling and COP common across Low/Average/High at each
planning anchor. All paths share the initial 2020 stock; subsequent investment
histories belong to their respective paths.

The separate electricity and thermal applications use the electricity/newshare
selector. Thermal-isolation cases hold the same Average renewable inputs while changing
temperature with training-derived annual-temperature P10/P50/P90 offsets.
`provenance/executed_final/climate_runtime_inputs.py` and
`climate_newshare_adapter.py` preserve those executed interfaces and key schemas.
They depend on the original solver project and are provenance, not standalone entry
points. The applications retain their respective thermal adapters and input schemas.

For explicit-baseline access to its numerical methods, the installed
`h2res_climate.prepared_thermal` module exposes
`_reshape_prepared_temperature_demand` and `_temperature_sensitive_cop` unchanged
from that runtime. Demand uses 18/22 C drivers, bounded and smoothed multipliers,
annual-total restoration and optional peak caps. COP scales the existing baseline
by a Carnot-ratio response, restores its arithmetic mean **before** clipping to
1.2..5.5; clipping can alter the final mean. The caller supplies unit groups,
baseline demand and COP, and baseline/scenario temperatures.

Reproducing the archived energy optimizations requires the original solver
database, stock-updating controller and licensed optimizer. The climate demo
covers input preparation with illustrative data.
