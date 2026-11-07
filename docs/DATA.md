# Input contracts

## Annual profiles

NPY arrays: `[members,8760,3]`, finite values, solar/wind CF in [0,1],
temperature in degrees Celsius. Calendar: UTC, January 1 through December 31,
February 29 removed. Scenario NPZ values have shape `[8760,3]`.

The final raw parquet corpus has columns `time,solar,wind,temperature`, complete
ordered consecutive hourly timestamps including leap days, years 1980-2024.
Solar and wind are already electrical capacity factors, not irradiance or m/s.
The generator removes leap days after validating the calendar. The accepted
models use 1980-2015 for training/normalization and 2016-2019 for validation.
Solar additionally uses inner 1980-2011 fitting and 2012-2015 tree-count tuning,
then refits on the training period. Reporting covers 2020-2024.
Reporting had been inspected before this release and is retrospective.
These dates describe the frozen release, not an implicit split for arbitrary
custom datasets. See [Verification](VALIDATION.md) and [Usage](USAGE.md).

## Spatial mapping directory

Replace `CC` with the two-letter uppercase country code:

| File | Required content |
|---|---|
| `genco_data_CC_sdewes.csv` | `unit_name,technology,fuel_type,cap_mw`; solar technology `PHOT`, wind fuel `Wind` |
| `CC_solar_macro_profiles.parquet` | `time` or DatetimeIndex and zone CF columns |
| `CC_solar_transfer_macro_to_units.csv` | `unit_name,source_zone,weight` |
| `CC_wind_macro_profiles.parquet` | Same time/zone CF schema |
| `CC_wind_transfer_macro_to_units.csv` | Same transfer schema |
| `CC_wind_nuts_profiles.parquet` | Used for explicit `nuts3` wind resolution |
| `CC_wind_transfer_nuts_to_units.csv` | Transfer for NUTS3 resolution |
| `ncre_aval_factor_CC_2020_2050_sdewes.csv` | `period,hror` if hydro is included |

Transfer weights must be finite, nonnegative, positive in total for every unit,
and reference existing source zones. Unit/zone pairs must be unique and the unit
set must match the solver table exactly. Spatial inputs must cover all 8760
non-leap hour-of-year values by 2015. Bounds and timestamps are validated.
Use explicit `macro` or `nuts3` resolution; `auto` additionally requires site
metadata understood by the scenario manager (see the illustrative fixture).

The balancing method maintains the target capacity-weighted national CF while
respecting [0,1] bounds, including hours when initial regional allocations saturate.
Mapping ratios use training-period observations only.

## Wind conversion in the released corpus

ERA5 100 m speed is in m/s. The resource proxy uses the Nordex
N131/3600 Mode 0 power curve at density 1.1 kg/m3. Multiply speed by
1.3247997658871304 **before** evaluating the curve. This coefficient uses
January-July 2015 Pometeno Brdo output and a 17.5 MW capacity assumption.
The plant-calibrated conversion is applied as a national resource proxy.
`scripts/wind_conversion.py` requires explicit curve units, rated power, source,
and operating range.
Raw weather, the full bootstrap pool and third-party curve material are excluded.
