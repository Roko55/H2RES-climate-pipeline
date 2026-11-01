# Final experiment results

The final accepted input is `profiles/accepted.npy`, SHA256
`d86a855166c24d699d511010184db12d0baf1551f83254b41f661c602d7ae728`.
The archived completed cohort contains **63 optimal planning windows and 45
optimal operational cases**, with no verified infeasible case. This release
checks the archived evidence; it does not rerun those optimizations.

| CSV | Row interpretation and units |
|---|---|
| `planning_objectives.csv` | Configuration, resource/thermal case and five-year planning anchor; objective in that configuration's model units |
| `objective_components.csv` | Native objective decomposition; component units and configuration must be retained |
| `objective_deltas.csv` | Within-configuration case differences; compare corresponding anchors |
| `operational_checks.csv` | Fixed updated portfolio, observed weather year and grid-availability stress; energy fields in MWh |
| `profile_metrics.csv` | Channel/method point metrics over the **complete 43800 reporting hours** |
| `solar_comparison.csv` | Solar validation/reporting diagnostics for the previous reference, accepted solar and historical ensemble |

In `profile_metrics.csv`, `fixed_H2RES_profile` is the prior fixed solver profile;
`ensemble_mean` is the accepted ensemble's calendar-aligned mean, and
`training_calendar_mean` is the historical training climatology. A central-profile
MAE is not an individual-weather-path error or a diversity measure. Solar/wind
MAE and RMSE use CF units; temperature uses degrees Celsius. R2 is dimensionless;
wMAPE is a percentage. These are retrospective reporting comparisons.

The final Low multisector path costs more than Average at every planning anchor.
All paths begin from common 2020 stock and then inherit their own investments;
objective differences must not be interpreted as matched-state causal effects.
Cross-configuration currency comparisons and cost-per-MWh claims remain conditional
on shared price-year and normalization provenance.

Operational grid availability 100%, 50%, 0% has 15 cases per level. Maximum HR
CEEP is respectively 0, 0.574591588 and 4.658303801 TWh; HR unserved energy is zero.
Operational portfolios are the corresponding updated 2030 portfolios.

Solar spread improves substantially over the previous narrow reference: reporting
80% interval coverage increases from about 16.9% to 79.6%. This comes with worse
member MAE (about +30.5%) and slightly worse ensemble-mean MAE (about +0.12%).
The historical ensemble has slightly better fair CRPS than the accepted solar.
The complete comparison table retains these regressions; no method dominates all
metrics. Coverage is empirical and does not establish joint-weather calibration.

## Separate autoencoder study

The autoencoder repository retains one validation-selected CVAE and its original
comparison corpus. Its annual wind-mean SD is 28.3% of the historical reference,
versus 68.3% for that historical pipeline comparator. Limited annual resource
diversity is relevant for Low/Average/High resource sensitivities. Some other
autoencoder metrics are better and remain reported there.

That comparison is historical, not a fresh comparison with this final solar
cohort. It does not prove overfitting, global optimality, that RAM was the sole
limitation, or that longer/larger training would necessarily solve the problem.
The supported conclusion is limited variability under the tested training protocol.
