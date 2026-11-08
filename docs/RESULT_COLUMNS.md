# Result columns and units

The checked V6.2 experiment definitions are in [Research results](RESEARCH_RESULTS.md).
This index describes the public CSVs. The V6.2 tables derive from the compact accepted thermal extract in `evidence/thermal`.
Original V6.1 receipts and unchanged zonal hourly files retain
their original schemas under [the evidence archive](../evidence/calibrated/README.md).
CSV files can be loaded with `pandas.read_csv`; `key_json` and `window_years` are
JSON strings, parsed with `json.loads`, not Python expressions.

## Case identifiers and calendars

| Column | Meaning |
|---|---|
| `case` | National path: `average`, `solar_low`, `solar_high`, `wind_low`, `wind_high`, `temperature_cold`, `temperature_hot` (Warm) |
| `portfolio` | Separate electricity-only portfolio: `avg` = Average, `low` = Low, `high` = High; these are not the seven national paths |
| `year` | Depends on the table: realized national anchor or native parameter anchor; see the specific sections below |
| `window_start` | First national anchor in a solved two-anchor window; 2050 has a single anchor |
| `window_years` | Explicit list of anchors in that solved window, e.g. `[2025,2030]` |
| `historical_year` | Operating weather year, 2020–2024; portfolio and exogenous load assumptions remain at 2030 |
| `availability_fraction` | Fraction of the specified HR exchange capacity: 1.0 = full, 0.5 = half, 0.0 = none; not renewable availability or a probability |

National tables have six realized future anchors per case (42 windows), following
the common calibrated 2020 base. Operating results contain 3 × 5 × 3 = 45 checks.
All electrical years contain 8760 hours, with February 29 removed.

## National input profiles

`results/channel_sensitivity/scenario_inputs.csv` has one row per case/anchor.

| Columns | Definition and unit |
|---|---|
| `solar_member`, `wind_member`, `temperature_member` | Zero-based donor indices in `profiles/accepted.npy` |
| `profile_sha256` | SHA256 of the assembled `[8760,3]` array's raw bytes; distinct from hashing an NPY/NPZ container file |
| `mean_solar_CF`, `mean_wind_CF` | Arithmetic mean hourly capacity factor, dimensionless |
| `mean_temperature_C` | Arithmetic mean hourly temperature, degrees C |
| `heating_degree_hours_18C` | Sum of `max(18-temperature,0)`, degree-hours |
| `cooling_degree_hours_22C` | Sum of `max(temperature-22,0)`, degree-hours |

## National window objectives and components

`results/channel_sensitivity/objective_comparison.csv`:

| Columns | Definition and unit |
|---|---|
| `objective` | Full solved-window objective with native EUR coefficients and discount weights |
| `delta_vs_average` | Case objective minus Average for the same window, EUR |
| `percent_vs_absolute_average` | `100*delta_vs_average/abs(Average objective)`, percent |
| `constraint_violation`, `bound_violation` | Solver-reported maximum violations in native model units; not an energy-service metric |

Windows overlap. Do not sum their objectives to obtain a transition cost.
Positive differences mean a higher cost than Average; negative differences a
lower cost. The fixed coefficient convention does not establish a common real
price year for every input.

`results/channel_sensitivity/objective_components.csv`:

| Columns | Definition and unit |
|---|---|
| `component_group` | Reporting group, e.g. `optimized_import_cost` or `heat_sector_investment_cost` |
| `value` | Component's full-window contribution, EUR |
| `active_in_objective` | 1 if included in objective reconciliation, 0 otherwise |

Sum `value` only where `active_in_objective == 1`, grouped by `case,window_start`.
Diagnostic groups have `active_in_objective=0` and must not be added.

## Realized investments and annual costs

`results/channel_sensitivity/investments_by_technology.csv`:

| Columns | Definition and unit |
|---|---|
| `family` | Native investment decision family |
| `key_json` | Aggregated unit and realized year encoded as a JSON list; the compact V6.2 extract groups nonzero physical additions |
| `value` | Realized decision quantity in `unit`; not total installed capacity |
| `unit` | MW for equipment capacity or MWh for storage energy capacity |
| `decision_year` | Anchor at which the decision is realized |
| `realized` | 1 in this table; next-anchor provisional decisions are excluded |
| `capacity_addition` | 0/1 physical-capacity inclusion flag; `extra_cap_inv` is an objective term excluded from physical-addition totals |

For these rows, `decision_year == window_start`. Different families can refer
to the same asset: group by family and key before interpreting quantities.
Do not add MW and MWh. Original native files retain provisional decisions.

`realized_annual_costs.csv` exists in both `results/channel_sensitivity/` and
`results/manuscript/tables/`, with the same accounting:

| Columns | Definition and unit |
|---|---|
| `year` | Realized first anchor of the window |
| `year_total_EUR` | First-anchor objective-ledger contribution divided by that weight |
| `capacity_terms_EUR` | Realized first-anchor investment contributions divided by the same weight |
| `other_active_terms_EUR` | `year_total_EUR-capacity_terms_EUR` |
| `demand_MWh` | Same-year sum of covered multisector service-demand families, counting industrial high-temperature heat once |
| `EUR_per_demand_MWh` | `year_total_EUR/demand_MWh`; not an electricity tariff or levelized generation cost |

These annual-anchor costs retain one-off investments; they are not annualized
capital costs or five-year integrals. The denominator follows the manuscript's
service-demand convention, including EV transport service rather than grid
charging. See [the accounting notes](CHANNEL_RESULTS.md).

## Native annual outcomes and parameter statistics

`results/channel_sensitivity/system_outcomes.csv` uses `case,year,family,key_json,value`.
Only the realized anchor of each window is included. The family determines the unit:

| Families | Meaning/unit of `value` |
|---|---|
| `CO2_total`, `CO2_total_heat`, `CO2_total_industry` | Annual direct emissions, tonnes CO2, for electricity, heat and industry respectively |
| `generation`, `generation_FC`, `generation_H2`, `Fuel_ind`, `H2tInd`, `ResToHeat`, `boiler_dh_generation`, `boiler_ind_generation`, `heat_gen`, `heat_input`, `heat_pump_out`, `heat_pump_dh`, `heat_pump_ind`, `cooling_pump_dh`, `cooling_pump_ind`, `power_to_h2` | Annual sums of the named native energy-flow variables, MWh on their native electrical/heat/fuel basis; overlapping flow families must not be added as independent supply |
| `imports` | Annual signed optimized exchange, MWh; the existing HR Krško entitlement is added separately for manuscript net imports |
| `ceep` | Annual surplus electricity, MWh |
| `noload_elec`, `noload_heat_dh`, `noload_heat_ind` | Annual unserved electrical/heat energy, MWh |
| `ev_sto_in`, `ev_sto_out`, `h2_sto_out`, `stat_sto_in`, `stat_sto_out`, `sto_out_flow`, `hydro_grid_pumping` | Annual sums of named charging/discharging/pumping flow variables, MWh; use the native model's conversion convention |
| `ev_sto_soc`, `h2_sto_soc`, `heat_pump_sto_soc`, `heat_sto_level`, `stat_sto_soc`, `storage_level` | Sums of hourly storage states; neither annual production nor terminal state. Divide by 8760 for a mean state on the native energy basis |

`results/channel_sensitivity/thermal_inputs.csv` is a parameter-statistics table,
not solved dispatch. The current V6.2 extract includes realized first-anchor parameters only:

| Columns | Definition and unit |
|---|---|
| `year` | Realized parameter anchor; equal to `window_start` in V6.2 |
| `family` | `demand`, `heat_demand`, `cooling_demand`, `Industry_HT_demand`, `H2_demand`, `ev_Demand` or `HP_COP_period` |
| `unit` | Native asset identifier for heat/cooling/COP, or `all` for the other grouped families; here this column is not a physical measurement unit |
| `count` | Number of native parameter entries aggregated; may include several systems or an annual EV entry, so not always 8760 |
| `sum` | Sum of native parameter values; demand totals in MWh, or arithmetic sum of dimensionless COP values |
| `minimum`, `maximum` | Extrema of individual native entries; hourly demands in MW, COP dimensionless; `ev_Demand` is an annual service quantity in MWh |

`sum/count` gives a mean over the included entries, not necessarily the national
hourly load. `heat_demand` includes `Industry_HT`; exclude it before adding the
separate `Industry_HT_demand` family. Extrema over individual unit/system entries
are not the peak of the summed national load. Supplementary source receipts bind
these aggregates to the original accepted parameter files.

## Fixed-portfolio operating checks

`results/operational_checks.csv` has one row per portfolio/weather/exchange setting.

| Columns | Definition and unit |
|---|---|
| `hours` | Electrical hours per zone, 8760 |
| `status` | `optimal_operation`: accepted optimal dispatch; it does not imply zero unserved energy |
| `solver_status` | Native Gurobi status code; 2 means optimal |
| `runtime_seconds`, `constraint_violation`, `bound_violation` | Solver runtime and numerical-quality fields, as above |
| `HR_unserved_MWh`, `HR_unserved_hours` | Croatian energy not served and count of hours above the native audit tolerance |
| `HR_CEEP_MWh`, `HR_CEEP_hours` | Croatian surplus electricity and its positive-hour count |
| `HR_import_MWh`, `HR_export_MWh` | Separate gross Croatian exchange totals, MWh; not a signed net flow or the national Krško-adjusted measure |
| `stationary_simultaneous_hours` | Hours with simultaneous stationary-battery charging/discharging under the saved meter; not overlap energy |
| `portfolio_sha256` | Hash of the originating planning portfolio file, linking all 15 checks of that portfolio |
| `verified_fixed_stock` | Boolean: solved stock agrees with the retained planning stock within tolerance |
| `storage_terminal_cycle_active` | Boolean: the accepted operation has cyclic storage conditions |
| `max_storage_cycle_error` | Largest saved storage-cycle residual on the native energy basis |
| `source` | Repository-relative accepted receipt path |

Unserved energy and CEEP measure different conditions and can both occur within
one year. All 45 checks are included, including the three 2021 zero-exchange
shortfalls. HR/SI/BA denote Croatia, Slovenia and Bosnia and Herzegovina.

## Climate scores and manuscript summary tables

`results/profile_metrics.csv`:

| Columns | Definition and unit |
|---|---|
| `channel` | `solar`, `wind` or `temperature` |
| `method` | `ensemble_mean` = generated ensemble's hourly mean; `fixed_H2RES_profile` = default reference; `training_calendar_mean` = historical hourly mean |
| `mask` | `complete_reporting`, the common full reporting calendar |
| `n_hours` | Number of reporting observations, 43800 |
| `MAE` | Mean absolute error: dimensionless CF for solar/wind, degrees C for temperature |

`results/manuscript/fair_crps.csv` and its copy in `results/manuscript/tables/`:

| Columns | Definition and unit |
|---|---|
| `n_reporting_hours` | Common reporting calendar length, 43800 |
| `historical_members`, `synthetic_members` | Ensemble sizes, 36 and 100 |
| `historical_fair_CRPS`, `synthetic_fair_CRPS` | Fair CRPS of each ensemble; channel units match MAE |
| `fair_CRPS_change_percent` | `100*(synthetic/historical-1)`, percent; negative means smaller score |

The other files under `results/manuscript/tables/` are reporting summaries:

| File | Additional columns and interpretation |
|---|---|
| `mean_profile_MAE.csv` | `channel`, `reference`, `reference_MAE`, `generated_MAE`, `change_percent` = `100*(generated/reference-1)`; negative means smaller MAE |
| `window_cost_differences.csv` | `case`, `year`, `window_years`, `difference_million_EUR` = full-window cost difference from same-window Average divided by 1e6 |
| `cost_component_differences.csv` | `case`, `window_start`, `component_group`, `difference_million_EUR` = grouped active-component difference from Average divided by 1e6 |
| `average_capacity.csv` | `year`, `technology`, `unit`, `new_capacity`: summed realized Average additions; nonzero physical additions; not total installed stock |
| `average_energy_supply.csv` | `year`, `scope` (Electricity/Heat), `technology`, `TWh`: annual grouped supply. Net imports include existing Krško rights once and exclude them from domestic generation |
| `average_direct_emissions.csv` | `year`, `electricity_Mt`, `heat_Mt`, `industry_Mt`: direct domestic emissions in million tonnes CO2; foreign emissions associated with imports excluded |
| `operational_surplus.csv` | `portfolio`, `historical_year`, `availability_fraction`, `HR_unserved_MWh`, `HR_CEEP_TWh`: abbreviated view of all 45 operating checks |

The two temperature reference labels share the same MAE because the fixed
temperature reference is the training-period hourly mean. Profile-score changes,
cost differences and service shortfalls are separate comparisons with different
units; one must not be substituted for another.
