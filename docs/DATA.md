# Data contract

## Annual climate source

The hourly source Parquet must contain `time`, `solar`, `wind`, `temperature`, with consecutive hourly timestamps, complete calendar years and finite values. Use UTC timestamps (timezone-aware UTC or naive values explicitly representing UTC). Include leap days; removal happens after validation. Local clock time with daylight-saving gaps is not the input calendar. At least ten complete consecutive years are required.

| Column | Type / units | Example |
|---|---|---|
| `time` | Timestamp at a whole UTC hour | `2001-01-01 00:00:00` |
| `solar` | Electrical capacity factor [0, 1] | `0.0` at night |
| `wind` | Electrical capacity factor [0, 1] | `0.27` |
| `temperature` | Degrees Celsius | `4.5` |

Supply generation capacity factors, not irradiance in W/m2, wind speed in m/s or temperature in Kelvin. There is no automatic weather-to-power conversion in this repository. For your own data the filename is arbitrary; pass it with `--data`. Upstream conversions, regional aggregation and calibration are your data-preparation responsibility.

The delivered model bundle requires SHA256 `9a21558378d6359f308e3b7b7a3068d053195fa9123792c1d8aa9ac177101022`: 1980–2024, 45 complete non-leap years after removal. Training anchors, temperature bounds and the wind pool use 1980–2015 only. Validation 2016–2019 is used for stopping, residual calibration and annual-rollout selection, including a common channel scale or offset. Reporting 2020–2024 supplies the profile comparison and was previously inspected during development.

Solar observations represent mean power over an hour, labelled by its UTC start.
The generator checks daylight support over that interval using the actual source
coordinates. See the [Renewables.ninja dataset documentation](https://www.renewables.ninja/documentation/datasets)
for the clock convention. The delivered model metadata records all three solar
source coordinates. The training-derived daylight mask used for evaluating
solar coverage is separate from this astronomical generation gate.

National CF calibration uses training-period IRENA targets: solar 2013–2015 and wind 2004–2015. The preparation command reconstructs profiles from the original raw caches and training-period targets. Source identities and the selected targets are recorded in `provenance/model/preprocessing_audit.json`. New datasets require their own upstream temporal-boundary checks.

## Spatial mapping inputs

The mapping directory requires the existing H2RES/source artifacts, with `CC` replaced by the country code:

- `genco_data_CC_sdewes.csv`: actual solver unit names, technology/fuel and capacities.
- `ncre_aval_factor_CC_2020_2050_sdewes.csv`: hourly availability with `period` and explicit `hror` columns. Omit this only when using `--exclude-hror`.
- `CC_solar_macro_profiles.parquet` and `CC_solar_transfer_macro_to_units.csv`.
- `CC_wind_sites_meta.csv`, the selected `CC_wind_nuts_profiles.parquet` or `CC_wind_macro_profiles.parquet`, and its corresponding `CC_wind_transfer_nuts_to_units.csv` or `CC_wind_transfer_macro_to_units.csv`.

Profile tables use `time` or a DatetimeIndex in UTC, with numeric columns named exactly like source-zone identifiers. Each referenced zone must contain finite CFs in [0, 1], and the data must cover every non-leap hour of year. They may contain multiple historical years; the source method averages each hour of year to construct spatial ratios. Missing coverage is rejected rather than interpolated. Unit capacities define the aggregate mass check. Ratios are climatologies, not new spatial weather realizations. Clipping can prevent exact conservation; the wrapper rejects exports beyond tolerance.

## Concrete CSV schemas

The minimal unit table columns are:

```csv
unit_name,technology,fuel_type,cap_mw
solar_west,PHOT,Solar,30.0
solar_east,PHOT,Solar,70.0
wind_inland,WTON,Wind,60.0
wind_coast,WTON,Wind,40.0
```

Solar identification uses `technology=PHOT`; wind identification uses `fuel_type=Wind`. Capacities are MW and must be finite/nonnegative, with positive group totals. Names must match the optimizer. Other project columns may be retained. The current spatial exporter expects both solar and wind groups; single-resource mapping is not implemented.

A solar transfer table for that unit table is:

```csv
unit_name,source_zone,weight
solar_west,SOLAR_WEST,1.0
solar_east,SOLAR_EAST,1.0
```

Weights are nonnegative relative contributions, normalized within each unit. A unit can have several different source-zone rows; each unit/zone pair occurs once. Every selected unit needs a positive total weight and every referenced zone must exist in its profile Parquet. Unknown units, omitted units, duplicate pairs, negative/NaN weights and absent zones are rejected. Wind uses the same schema with wind unit/zone names.

The associated solar Parquet has `time,SOLAR_WEST,SOLAR_EAST`; wind has `time,WIND_INLAND,WIND_COAST`. `examples/demo.py` writes complete working versions of every required schema under its output `inputs/` directory.

Optional hydro input uses `year,period,hror`, with periods covering 1–8760 and CFs in [0, 1]. The source method averages repeated hour-of-year rows if several years are supplied. It retains fixed hydro availability; hydro is not generated by this model.

For wind `auto` resolution, the site metadata CSV needs one row per site and a `nuts_id` column identifying active source zones. Explicit `macro` or `nuts3` does not require site metadata. Explicit NUTS3 requests require actual NUTS3 files and do not fall back silently.

Obtain source inputs under their applicable access and reuse conditions. No credentials or automated data-download configuration is included.
