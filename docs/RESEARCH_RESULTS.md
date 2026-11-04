# V6 results

This result package corresponds to the V6 manuscript with SHA256
`54313b834d431079adb30b61e9d4ff5c608a472f66df5651dac70fae208123dd`.
The accepted ensemble is `profiles/accepted.npy`, SHA256
`d86a855166c24d699d511010184db12d0baf1551f83254b41f661c602d7ae728`.

## Profile evaluation — section 4.1

The full 43800 reporting hours cover 2020-2024. Training is 1980-2015 and
validation is 2016-2019. Reporting was previously inspected and is retrospective.
`results/profile_metrics.csv` contains the nine mean-profile MAE rows used for
the generated mean, fixed H2RES reference and training-period hourly mean.
Solar/wind errors are in capacity-factor units; temperature errors are in degrees C.
`results/manuscript/fair_crps.csv` compares 100 generated and 36 historical members
on the same reporting calendar, with the finite-ensemble correction.

Figure 5 reports `100 * (generated MAE / reference MAE - 1)`. Against the fixed
reference, changes are -53.58% solar, -4.88% wind and -8.33% temperature.
Against the historical mean, they are -0.16%, +0.52% and -8.33% respectively.
The two temperature references are identical. Figure 6 fair-CRPS changes are
+2.01%, +1.60% and -7.48%. Negative changes mean a smaller error or score.
Mean-profile error and the distribution of annual members are distinct evaluations.

## Planning comparisons — sections 4.2-4.5

Seven [single-channel paths](CHANNEL_RESULTS.md) use stationary weather through
2020-2050. All 49 native windows are newly solved; none reuse an Average window.
Every case starts from the same 2020 stock and subsequently inherits its own
investments and retirements. Solar Low/High, Wind Low/High and Cold/Warm change
one complete hourly input against the common Average case.

The original supporting tables are under `results/channel_sensitivity/`.
They retain all assets and active components needed to reconcile the reported
49 windows. `results/manuscript/tables/` contains their manuscript summaries.
Costs use the model's EUR coefficients and discount weights. Window differences
are in million EUR; overlapping windows are reported separately. Realized annual
costs count each anchor once and remove its own discount weight. The cost per
demand MWh uses the same year's covered multisector demand. MW capacity additions,
MWh storage energy, TWh annual supply and Mt direct emissions remain distinct.

Solar yield preserves the cost ranking in these cases. Wind Low is more expensive
through 2040, then less expensive in the windows starting in 2045 and 2050.
Cold is initially less expensive and later more expensive. These results are
conditional on the selected hourly profiles, inherited stock and the executed
model's cost treatment. Annual heating/cooling totals are preserved while their
hourly distribution and heat-pump COP change.

## Fixed-portfolio operation — section 4.6

`results/operational_checks.csv` contains 45 full 8760-hour checks: three separately
optimized 2030 electricity portfolios, five historical years and exchange-capacity
fractions 1.0, 0.5 and 0.0. Portfolio design used 8736 hours; operation covers all
8760 hours with no capacity expansion and cyclic storage. All cases are optimal
with zero unserved Croatian electricity. Maximum CEEP is 0, 0.574591588 and
4.658303801 TWh for full, half and zero exchange respectively. These checks apply
to the three electricity portfolios, separately from the seven multisector paths.

## Manuscript figure index

The files below are exact copies of the corresponding V6 embedded images.
Their identities and captions are recorded in `provenance/manuscript/scope.json`.

| Figure | Reported content | File |
|---|---|---|
| 3 | Distributions; common solar daylight mask | [Figure 3](../results/manuscript/figures/figure_03.png) |
| 4 | One reporting week and selected Average | [Figure 4](../results/manuscript/figures/figure_04.png) |
| 5 | Six MAE reference comparisons | [Figure 5](../results/manuscript/figures/figure_05.png) |
| 6 | Fair CRPS against historical ensemble | [Figure 6](../results/manuscript/figures/figure_06.png) |
| 7-8 | Solar cost differences and components | [7](../results/manuscript/figures/figure_07.png), [8](../results/manuscript/figures/figure_08.png) |
| 9-10 | Wind cost differences and components | [9](../results/manuscript/figures/figure_09.png), [10](../results/manuscript/figures/figure_10.png) |
| 11-12 | Temperature cost differences and components | [11](../results/manuscript/figures/figure_11.png), [12](../results/manuscript/figures/figure_12.png) |
| 13 | Average realized annual costs and cost per demand MWh | [Figure 13](../results/manuscript/figures/figure_13.png) |
| 14 | Average new electricity/heating capacity | [Figure 14](../results/manuscript/figures/figure_14.png) |
| 15(a)-(b) | Average supply and direct emissions | [15(a)](../results/manuscript/figures/figure_15a.png), [15(b)](../results/manuscript/figures/figure_15b.png) |
| 16 | CEEP in the 45 operating cases | [Figure 16](../results/manuscript/figures/figure_16.png) |

## Reproduction

```bash
python analysis/manuscript_results.py --output exports/v6_tables
```

The command regenerates supporting tables from the included numerical records,
checks all figure hashes and draws the six MAE comparisons from their table.
Original embedded figure images are retained for exact manuscript correspondence.
Recomputing profile scores and historical-weather plots requires the excluded
source weather corpus. Re-solving H2RES requires its original model, inputs and
licensed optimizer. No solver is invoked by this command.
