# H2RES integration

The interface prepares inputs before optimization. It does not import Gurobi or modify solver constraints. Use this full-year interface before any project-specific time aggregation or rolling-window slicing.

For zone-indexed heat-demand dictionaries, `scripts.temperature_scope` provides
`reshape_demand_for_zones(demand, zones, transform)`. Obtain `zones` from the model's
explicit country-to-zone mapping, using the same country as renewable input
mapping and heat-pump COP. The callback transforms a separate target-zone
subset; all other zones retain their configured values. The transformation
must preserve demand keys. Annual-total normalization belongs inside the
supplied temperature-demand transformation and uses the modeled hours.
After installation, import the helper from `h2res_climate.temperature_scope`
when working inside a separate H2RES checkout with its own `scripts` package.

In a myopic planning loop, select the corresponding climate year before each
planning subproblem and reload pristine heat-demand and COP baselines before
applying the temperature transformation. Preserve the inherited-capacity state
separately. This makes the prepared planning-year temperature inputs active in
each solve and prevents repeated transformations of an already modified profile.

The corresponding integration pattern inside the existing myopic loop is:

```python
for years in chunks:
    inherited_years = set(globals().get("previous_years_set", set()))
    os.environ["H2RES_CLIMATE_PLANNING_YEAR"] = str(min(years))
    _reload_data_for_current_mode()
    globals()["previous_years_set"] = inherited_years
    globals()["years"] = list(years)
    # Existing model construction, solution and state export follow here.
```

Here `_reload_data_for_current_mode` is the model's data loader, including the
climate-input hook, and the copied inherited-year set retains the original
investment handoff. Activate this pattern for prepared climate inputs; the
optimization formulation stays unchanged.

Install the adapter with `python -m pip install -e . --no-deps` after the requirements. You can then import `h2res_interface` from a separate H2RES project. The installed thermal implementation uses the distinct `h2res_climate` namespace to avoid conflicts with that project's own `scripts` directory. CLI/model files remain in this checkout; the Python adapter package does not install a complete optimization application.

## Availability arrays to parameters

After running the offline demo, this example is executable from the repository root:

```python
import numpy as np
from h2res_interface import availability_frame, availability_parameters, inject_availability

with np.load("exports/demo_01/units.npz", allow_pickle=False) as archive:
    units = {name: archive[name] for name in archive.files}

frame = availability_frame(units, year=2030)
# Columns: year, period, solar_west, solar_east, wind_inland, wind_coast, hror
# Row 0: year=2030, period=1. Last row: period=8760.
entries = availability_parameters(units, year=2030)
assert entries["wind_inland", 1, 2030] == float(units["wind_inland"][0])
```

`frame.to_csv(...)` writes the wide availability-table format. The demo has already written `availability_2030.csv`. `entries` uses `(unit_name, period, planning_year)` keys matching the H2RES `aval_factor_plant` convention. NumPy hour 0 maps to H2RES period 1; no modulo, daylight-saving or planning-year shift is applied. Weather and planning years are separate concepts: a generated annual chronology can be assigned to planning year 2030.

In an actual H2RES runtime, validate against its unit list and existing parameter keys:

```python
# Replace expected_units with the climate-dependent unit names in your model.
entries = availability_parameters(units, 2030, expected_units=list(units))
# In your initialized runtime:
# aval_factor_plant = inject_availability(aval_factor_plant, entries)
```

`inject_availability` returns a copy, preserves unrelated units/years and raises if any generated key is absent. It does not silently skip misspelled units or create units in the optimizer. Supply actual expected unit names rather than `list(units)` when connecting a project. Do not insert MW values here: capacity is a separate H2RES parameter; availability remains in [0, 1]. For a solar/wind-only project, export with `--exclude-hror`.

## Temperature, heat demand and COP

Use explicit unit names and an explicit 8,760-hour baseline temperature. This complete example uses simple illustrative baseline dictionaries:

```python
import numpy as np
from h2res_interface import couple_temperature

with np.load("exports/demo_01/cases.npz", allow_pickle=False) as cases:
    temperature = cases["avg_yield__normal"][:, 2]

year = 2030
heat = {("my_heat", p, year): 10.0 for p in range(1, 8761)}
cooling = {("my_cooling", p, year): 4.0 for p in range(1, 8761)}
cop = {("my_heat_pump", p, year): 3.0 for p in range(1, 8761)}
result = couple_temperature(
    temperature, baseline_temp_profile=np.full(8760, 12.0), year=year,
    heat_demand=heat, cooling_demand=cooling, hp_cop_period=cop,
    general_heat_units=["my_heat"], cooling_units=["my_cooling"],
    heat_pump_units=["my_heat_pump"],
)
assert np.isclose(sum(result["heat_demand"].values()), sum(heat.values()))
# In your runtime, replace the illustrative inputs with actual baseline data:
# heat_demand = result["heat_demand"]
# cooling_demand = result["cooling_demand"]
# hp_cop_period = result["hp_cop_period"]
```

The wrapper requires complete finite baseline keys for every selected unit. Demand must be nonnegative and COP positive. It rejects missing hours, partial temperature arrays and duplicate/overlapping heat-unit groups. Caller dictionaries remain unchanged. Empty groups can be omitted, but at least one group must be selected.

Annual heat/cooling totals are preserved. Temperature changes hourly demand shape through degree-day signals and adjusts COP with the source method's bounded theoretical relationship and baseline-mean calibration. Default heating/cooling bases are 18/22 degrees C; COP bounds are 1.2–5.5. These are model assumptions requiring application-specific validation, not measurements of a particular heat pump. The zero/constant baseline arrays in the example are illustrative; use your permitted baseline climatology and real runtime demand/COP data in an energy study.

The adapter passes baseline temperature directly and never loads a country file implicitly. The separate country-file API requires its corresponding country data.

## Scope of reuse

The interface supports complete annual arrays and the documented dictionary/CSV contract. Other H2RES versions may use different parameter names, time indexing, units or thermal assumptions; align those contracts explicitly. The bundle supplies no transmission topology, demand database, optimization constraints or solver licence. Runtime integration is tested with illustrative parameter dictionaries and the existing Croatian spatial artifacts; arbitrary external H2RES variants have not been exhaustively validated.
