# H2RES integration

The package prepares inputs for a separately installed H2RES model and solver.
Run the offline walkthrough first: `python examples/demo.py --output exports/demo_01`.
The fixture contains illustrative weather, country and unit tables.

## Availability adapter

After running the demo command above, this example can be executed from the
checkout root. It uses illustrative ZZ units, including the demo's `hror` vector.

```python
import numpy as np
from h2res_interface import availability_frame, availability_parameters, inject_availability

with np.load("exports/demo_01/units.npz", allow_pickle=False) as archive:
    units = {name: archive[name] for name in archive.files}
# Illustrative names: replace with the climate-mapped units in your H2RES database.
expected_climate_units = {"solar_west", "solar_east", "wind_inland", "wind_coast", "hror"}
frame = availability_frame(units, 2030, expected_units=expected_climate_units)
frame.to_csv("exports/demo_01/availability_checked_2030.csv", index=False)
updates = availability_parameters(units, 2030, expected_units=expected_climate_units)
# In your initialized solver project:
# aval_factor_plant = inject_availability(aval_factor_plant, updates)
```

Availability keys are `(unit,period,year)`, periods 1..8760. Names must agree with
the initialized solver database; `inject_availability` rejects missing keys.
Use MW capacities consistently; CF itself is dimensionless. The installed
`h2res_climate` namespace avoids collision with an H2RES project's `scripts` folder.
The command-line workflow still runs from this repository checkout.
The explicit expected set is the units receiving climate updates, not every
plant in the solver database. Other units and years keep their existing values.
For a real project, replace the NPZ path, planning year and expected unit set
with your mapped export and actual solver names. Use the matching set without
`hror` when exporting with `--exclude-hror`. The API returns new dictionaries: assign the returned
value to the solver parameter mapping before building the model.

## Thermal coupling used in V6.2

Select the seven cases with `select-final --design channel --year 2030`.
Use `h2res_interface.couple_temperature_v62` with the complete selected temperature
year and the initialized Croatian heat, cooling and COP dictionaries. Pass
`data/weather_energy` explicitly. The returned dictionaries are copies.
Run `python examples/v62_thermal.py` for a complete no-solver example.

The adapter uses the archived 2020-calibrated schedules, permits annual heat and
cooling totals to vary and applies one shared COP curve without per-case mean
normalization. Returned `household_cooling_electric_mwh` belongs in the explicit
power-demand ledger: subtract the old common cooling reference once and add
this vector once. Do not add household cooling a second time as endogenous
district cooling. The full executed power-meter implementation and source audit
remain part of the original H2RES project; the public example prepares inputs.

For other sites, `h2res_climate.weather_energy` exposes `signals`, `heat_service`,
`cooling_service`, `calibrate_cop` and `cop_curve`. Supply suitable reference-year
service quantities and weather; the Croatian coefficients are case-study inputs.

The older `couple_temperature` and `prepared_thermal` helpers preserve annual
totals and remain for legacy/generic experiments. They are not the V6.2 method.

The separate `select-final --design electricity` exports the three fixed 2030
portfolios' source profiles. Their 45 operating checks are distinct from the
seven multisector planning paths. Full energy re-solving requires the original
database, myopic state controller and licensed optimizer.
