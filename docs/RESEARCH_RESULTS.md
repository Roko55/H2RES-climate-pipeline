# Current manuscript results

The exact manuscript and 17 figures are bound in provenance/manuscript/scope.json.
The climate ensemble SHA256 is:
d86a855166c24d699d511010184db12d0baf1551f83254b41f661c602d7ae728.

## Profile evaluation
Figures 3-6 report six MAE comparisons on 43800 reporting hours and three
fair-CRPS comparisons of 100 generated and 36 historical members.

## National planning
Sections 4.2-4.5 use evidence/final: seven paths, 42 windows, common 2020 base
and separate carried capacity histories. Figures 7-13 use annual undiscounted
operation, payments on surviving additions and one-off charges. Two-year
optimization objectives are not summed or presented as annual bills.

In results/final, annual_cost_differences and annual_cost_components support
Figures 7-12; annual_costs, capacity_additions, average_energy_supply and
cumulative_capacity support Figures 13-15. Cold increases heat demand and
heat-pump additions. Higher wind yield lowers annual costs. Solar Low delays
a large PV addition from 2035 to 2040 and temporarily imports 33.4% of use.

## Separate operation
Section 4.6 reports 45 electricity-only checks: three fixed 2030
portfolios, five weather years and 100/50/0% HR exchange. Three 2021
zero-exchange cases have shortages. Annual unserved energy is supplied in
MWh and TWh in results/final/operating_results.csv. The fixed electricity
portfolios are distinct from the national multisector planning paths.

## Reproduction and artwork
Run python analysis/manuscript_results.py --output exports/current_results.
No solver is run. Native PDFs and exact embedded PNGs are in
results/manuscript/figures. Recomputing Figures 3-4 from weather requires the
original external corpus. No data are inferred from rendered images.

## Supporting archives
`results/channel_sensitivity`, `evidence/thermal` and `evidence/calibrated`
preserve source snapshots with separate schemas. Their analysis modules and
tests verify the corresponding archives. Manuscript national tables use
`evidence/final` and `results/final`.
