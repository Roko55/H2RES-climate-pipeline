# Stationary-weather solar, wind and temperature analyses

The same full multisector H2RES model is evaluated with one climate channel
changed at a time. Seven paths cover anchors 2020, 2025, ..., 2050. Each native
window contains the current and next anchor, except the terminal 2050 window.
The completed V6 experiment has 49 newly solved optimal windows and no reused
planning windows. Complete state handoffs are verified for each path.

## Profile selection

Donors are zero-based member indices in the included 100-member ensemble:

| Case | Solar donor | Wind donor | Temperature donor |
|---|---:|---:|---:|
| Average | 65 | 65 | 65 |
| Solar Low | 2 | 65 | 65 |
| Solar High | 28 | 65 | 65 |
| Wind Low | 65 | 20 | 65 |
| Wind High | 65 | 72 | 65 |
| Cold | 65 | 65 | 41 |
| Warm | 65 | 65 | 48 |

Solar and wind donors are the members nearest the outer-cluster means of separate
three-cluster K-means fits to annual CF (seed 42, n_init 10). Cold/Warm use the outer
thermal-index cluster representatives: standardized cooling degree hours above
22 C minus standardized heating degree hours below 18 C. Average member 65 belongs
to each central cluster and is kept as the common reference. Selection uses the
generated ensemble before the energy runs. Full Cold/Warm chronologies are retained.

## Stationary weather inputs

Each case uses the same selected 8760-hour profile at all seven planning anchors.
Solar and wind factors remain 1.0; the temperature offset remains 0.0 C. All 49
input hashes are verified against `scenario_inputs.csv`, with one unique hash
per case across the seven anchors. Spatial mapping checks cover both resource
channels after mapping, including channels held fixed.

At 2020, Solar Low/Average/High mean CF is 0.105242/0.110304/0.114586;
Wind Low/Average/High is 0.226350/0.244826/0.261186. Cold/Average/Warm mean temperature
is 11.3931/12.3375/12.8434 C. The complete input ranges and degree hours are retained
in the tables. Effects across channels depend on these different selected ranges.

## Model and accounting

Each path starts from the same complete 2020 stock. Later anchors inherit that
path's own realized investments and retirements. Prices, policies, technology
assumptions, hydro, exchange capacities, equations and solver tolerances retain
the accepted multisector configuration. Temperature uses the existing hourly
heat/cooling/COP adapter, with `scale_annual_totals=False`. Inspect `thermal_inputs.csv`
for the executed annual demand totals and COP statistics.

`investments_by_technology.csv` contains only decisions realized at the current
anchor (`realized=1`, `decision_year=window_start`). Decisions for the next anchor
are provisional and excluded. `installed_stock.csv` separates remaining original
stock, inherited new stock, current additions and resulting installed stock.
Retirements after the first anchor are derived from successive native stock records.

MW measures equipment capacity. MWh measures stationary-battery and hydrogen-store
energy capacity. The `capacity_addition` column is a **0/1 inclusion flag**;
`value` holds the capacity. `extra_cap_inv` is retained as a native objective
term but excluded from sums of physical additions. Native objective contributions
use each row's saved coefficient and discounting. Per-window objectives retain
their model monetary convention; summing overlapping windows would double-count.
Imports are endogenous; input-side exports retain the model's accounting convention.

## Tables

All paths below are under [`results/channel_sensitivity`](../results/channel_sensitivity):

| File | Contents |
|---|---|
| `scenario_inputs.csv` | Donor indices, calendar means, degree hours and profile hashes |
| `objective_comparison.csv` | Window objective, numerical violations, difference and percent relative to Average |
| `objective_components.csv` | Native components; use `active_in_objective=1` to reconcile each window |
| `investments_by_technology.csv` | Realized native decision values, units, coefficients and contributions |
| `installed_stock.csv` | Inherited and resulting stock plus derived retirements |
| `channel_comparison.csv` | Asset-level capacity and investment-contribution differences from Average |
| `system_outcomes.csv` | Native annual generation, heat, imports, CEEP and emissions by saved family/key |
| `thermal_inputs.csv` | Annual demand sums and hourly mean/min/max, or dimensionless COP statistics |

Demand sums are MWh over the hourly year. COP mean/min/max are dimensionless;
the COP `sum` column is an arithmetic sum of hourly ratios. `key_json`
preserves the native asset/system/year indices. The stored `source` paths identify
the original result locations, made relative for portability.

## Reported results

V6 Figures 7-12 report window cost differences and their active components.
Figures 13-15 report realized annual costs, new capacity, supply and direct
emissions for Average. The exact images and section mapping are in the
[V6 results index](RESEARCH_RESULTS.md).

Solar Low has a higher objective than Average at every anchor. Wind Low is higher
through 2040, then lower in the windows starting in 2045 and 2050. Cold is lower
in 2020/2025 and higher later. Each result includes that path's inherited stock.
Window objectives overlap and are not added as a transition-cost total.

```bash
python analysis/channel_results.py --output exports/v6_tables
```

This entry point runs the manuscript table reproduction. It verifies 49 profile
hashes, donor selection, stationary weather, fresh solution records, complete
state handoffs, numerical tolerances and objective/investment reconciliation,
plus the archived 2009 stock, 273 emission and 49 balance checks.
The 45 operating tests retain their separate fixed electricity portfolios.

The executable case ID `temperature_hot` denotes the manuscript's Warm case.
