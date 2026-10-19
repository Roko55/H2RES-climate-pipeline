# Data contract

## Annual climate source

`HR_data.parquet` must contain `time`, `solar`, `wind`, `temperature`, with consecutive hourly timestamps, complete calendar years and finite values. Solar and wind are capacity factors in [0, 1]; temperature is in degrees Celsius. Leap days are removed after chronology validation. At least ten complete years are needed for chronological fitting/validation/reporting partitions.

The delivered model bundle requires SHA256 `d92748ca6625d0092b073b9bc16345da960a04c42da29926da5b125a63ad122d`: 1980–2024, 45 complete non-leap years after removal. Training anchors, temperature bounds and the wind pool are reconstructed from 1980–2015 only. Validation 2016–2019 was used for stopping/calibration. Reporting 2020–2024 was used for the recorded comparison.

Upstream wind calibration uses IRENA 2004–2023 and solar calibration 2019–2023, including reporting years. This prevents a strict independent temporal-holdout interpretation of the supplied corpus. A strict new study needs calibration rebuilt from permitted training information before new training/selection/evaluation. Existing clipped/rounded calibrated inputs cannot be justified as original raw inputs by dividing by rounded factors.

## Spatial mapping inputs

The mapping directory requires the existing H2RES/source artifacts, with `CC` replaced by the country code:

- `genco_data_CC_sdewes.csv`: actual solver unit names, technology/fuel and capacities.
- `ncre_aval_factor_CC_2020_2050_sdewes.csv`: hourly availability with `period` and explicit `hror` columns.
- `CC_solar_macro_profiles.parquet` and `CC_solar_transfer_macro_to_units.csv`.
- `CC_wind_sites_meta.csv`, the selected `CC_wind_nuts_profiles.parquet` or `CC_wind_macro_profiles.parquet`, and its corresponding `CC_wind_transfer_nuts_to_units.csv` or `CC_wind_transfer_macro_to_units.csv`.

The original transfer schema is implemented in `scripts/scenario_manager.py::_load_transfer_matrix`. Profile tables contain source zones and timestamps; transfer weights connect those zones to the units. Unit capacities define the aggregate mass check. Hour-of-year source ratios are climatologies, not new spatial weather realizations. Clipping can prevent exact mass conservation; the wrapper rejects exports exceeding the declared tolerance instead of concealing that discrepancy.

Obtain source inputs under their applicable access and reuse conditions. No credentials or automated data-download configuration is included.
