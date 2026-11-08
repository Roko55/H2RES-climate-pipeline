# Calibrated stationary-weather multisector paths

The V6.2 national experiment is `national_production_thermal_20261107_01`: seven paths share one
calibrated historical 2020 base and then solve six future windows starting in
2025, 2030, 2035, 2040, 2045 and 2050 (42 windows). A window contains its current
and next anchor, except 2050. Each path inherits its own realized capital and
retirements. The common 2020 calibration is not seven new synthetic cases.

## Profile selection

Indices below are zero-based members of the frozen 100-member ensemble.

| Case | Solar | Wind | Temperature |
|---|---:|---:|---:|
| Average | 65 | 65 | 65 |
| Solar Low | 2 | 65 | 65 |
| Solar High | 28 | 65 | 65 |
| Wind Low | 65 | 20 | 65 |
| Wind High | 65 | 72 | 65 |
| Cold | 65 | 65 | 41 |
| Warm (`temperature_hot`) | 65 | 65 | 48 |

Solar/wind donors are members nearest the outer-cluster means of independent
three-cluster K-means fits to annual CF (seed 42, n_init 10). Temperature uses
standardized cooling degree hours above 22 C minus standardized heating degree
hours below 18 C. Average member 65 belongs to each central cluster. Selection
uses the generated ensemble before the energy runs. Each case changes one
complete channel and retains its hourly profile at all six future anchors:
solar/wind multipliers 1.0, temperature offset 0 C. No trend is applied.

## Accounting and interpretation

The current cohort is `national_production_thermal_20261107_01`.
Its compact source extract and original full-store audit are included under
`evidence/thermal`. The previous national cohort remains a historical archive.
The configured RPS and aggregate CO2 targets are disabled; the fossil-electricity
carbon price and inherited financial convention are retained.

Window objectives use native EUR coefficients and include both anchors. Do not
sum overlapping windows. Realized-year costs use only the first anchor ledger
contribution divided by its saved NPV weight. One-off capacity terms are retained;
these costs are not a five-year integral or an annualized levelized cost.

`EUR_per_demand_MWh` divides that cost by the same year's native electricity,
space/district heat, cooling, high-temperature industrial heat, hydrogen and EV
transport-service demand. `Industry_HT` is excluded from `heat_demand` before its
separate industrial family is counted. This denominator mixes service demands;
it is not an electricity price. EV transport service differs from grid charging
because of conversion efficiency. The fixed-demand audits identify the added EV
and household-cooling electricity terms and excluded reference components.

National net imports include signed optimized exchange plus the existing HR
Krško entitlement exactly once. That entitlement is excluded from domestic
generation. It is not new nuclear investment. In the separate zonal model,
Krško remains physically in Slovenia.

Common coefficients calibrated once to 2020 convert degree hours below 18 C
to space heating and six-hour mean temperatures above 22 C to household cooling.
Hot-water and industrial heat follow shared trajectories. Household cooling is
unclipped exogenous electricity at COP 3 and replaces the old reference component
once. Air-to-water COP uses one common calibration multiplier (1.130404757464719),
bounded to 1.2–5.5 without per-case mean restoration. Annual heat and cooling
therefore change with the selected temperature chronology.

## Tables

Files are under [`results/channel_sensitivity`](../results/channel_sensitivity).
The [column dictionary](RESULT_COLUMNS.md) defines each field and its unit,
including the different meanings of `year`, `unit` and `value` between tables.

| File | Definition |
|---|---|
| `scenario_inputs.csv` | 42 case/anchor records; donor indices, CF/temperature means, degree hours, profile hash |
| `objective_comparison.csv` | Full-window EUR objective, solver violations, runtime, difference/percentage from same-window Average |
| `objective_components.csv` | Native components; sum rows with `active_in_objective=1` per case/window |
| `investments_by_technology.csv` | Realized first-anchor decisions only; nonzero physical additions aggregated by family/unit; financial coefficients remain in original native stores |
| `system_outcomes.csv` | Native annual output families at the realized anchor, keyed by saved asset/system indices |
| `thermal_inputs.csv` | Native parameter count, sum, minimum and maximum by case/window/realized-anchor/family/unit |
| `realized_annual_costs.csv` | First-anchor ledger/NPV, capacity terms, other active terms, same-year demand and ratio |

MW is equipment capacity; MWh is storage energy or annual energy as identified
by the family. `capacity_addition` is a 0/1 inclusion flag; `value` is capacity.
`extra_cap_inv` remains an objective term but is not a physical addition.
Future-anchor provisional investment decisions remain in the native evidence
and are excluded from realized summaries. A sum of stored hourly state values
(`*_soc`, `storage_level`) is not annual energy production.
For COP, sums/extrema are dimensionless; `sum/count` is the arithmetic mean.

Solar and wind costs follow Low > Average > High in every future window.
All national new electricity-generation decisions are zero in this snapshot.
Cumulative new heating capacity is 7788.839 MW for Average, 8204.488 MW for Cold
and 7373.401 MW for Warm. Cold requires more annual heat in all six decision years.
Window cost rankings also reflect when investments occur. Warm 2050 includes
49.64909 MWh unserved electricity in one hour at the 3.6 GW import limit.

```bash
python analysis/channel_results.py --output exports/v62_tables
```

This verifies the delivered native records and regenerates manuscript summaries.
[Verification](VALIDATION.md) distinguishes local checks from full-store audits.
The 45 operating tests concern three separate electricity portfolios.
