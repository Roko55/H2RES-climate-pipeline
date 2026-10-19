# H2RES integration

The standalone pipeline exports aggregate annual profiles, representative scenarios and generation-unit availability arrays. The NPZ unit keys are H2RES unit names. Arrays are ordered non-leap hours 0–8759; the H2RES runtime supplies its own period/year indexing and unit database.

For temperature integration, call `apply_temperature_coupling_to_runtime` from `scripts/temperature_coupling.py` with the selected temperature path and the runtime heat-demand, cooling-demand and heat-pump COP dictionaries. Supply explicit baseline dictionaries and the country source data. The default mode preserves annual heating/cooling totals while redistributing demand across hours. Annual-total scaling is a separate option, disabled by default. COP adjustment uses a baseline temperature climatology and documented bounds.

The original temperature module contains legacy missing-data fallbacks, including a zero baseline when the country source is absent. An integrating runtime must supply the actual baseline weather and demand inputs; missing baseline weather is not an evaluated physical scenario. Synthetic regression tests verify demand-total preservation, not the calibration of the thermal model.

The manuscript describes separately generated channels and designed sensitivity combinations. Use an explicit selection rule and preserve generated-case metadata with any planning run. Neither `pipeline.py` nor the tests invoke Gurobi. Full solver reproduction additionally needs the external H2RES version, solver licence, complete input database, scenario switches and archived run manifests. The native model bundle here is the documented comparison refit and must not be described as the exact original manuscript checkpoint.
