# Research results

The analyses share the accepted ensemble in `profiles/accepted.npy`, SHA256
`d86a855166c24d699d511010184db12d0baf1551f83254b41f661c602d7ae728`.

## Individual channels

The [single-channel multisector analyses](CHANNEL_RESULTS.md) contain 49 optimal
planning windows: 42 new windows and seven reused Average windows. Complete solar,
wind or temperature chronologies change independently against common Average.
All paths share initial 2020 stock and inherit their own later stock histories.
Their tables and figures are in `results/channel_investments/`.

## Combined resource and separate applications

The earlier completed package contains 63 optimal planning windows and 45 optimal
operational cases. The 63 windows cover combined-resource multisector paths,
the separate electricity application and the separate thermal application.
These earlier tables remain at `results/`:

| CSV | Definition |
|---|---|
| `planning_objectives.csv` | Configuration, case, anchor and objective in the configuration's native units |
| `objective_components.csv` | Native objective components and activity flags |
| `objective_deltas.csv` | Within-configuration differences at corresponding anchors |
| `operational_checks.csv` | Updated 2030 electricity portfolio, observed weather and grid-availability stress; energy in MWh |

The earlier **combined-resource** Low multisector path has a higher objective than
Average at every anchor. Its Average temperature/heat/COP inputs are shared within
each anchor. This result describes combined renewable changes; individual-channel
responses are reported separately in CHANNEL_RESULTS.md.

The 45 operational cases use their original updated electricity portfolios, with
15 cases at each grid-availability level (100%, 50%, 0%). Maximum HR CEEP is
0, 0.574591588 and 4.658303801 TWh respectively; HR unserved energy is zero.
These tests do not evaluate the seven new single-channel multisector portfolios.
The shared seven Average windows appear in both planning packages and must be
counted once when counting distinct solves.

## Climate profile diagnostics

`profile_metrics.csv` uses all 43800 reporting hours (2020-2024). Rows distinguish
the prior fixed H2RES profile, accepted ensemble mean, training calendar mean
and any additional declared central profile. Solar/wind MAE and RMSE are in CF;
temperature uses degrees Celsius. R2 is dimensionless and wMAPE is a percentage.

`solar_comparison.csv` retains the previous solar reference, accepted solar and
historical ensemble on validation and reporting diagnostics. Reporting 80% interval
coverage increases from about 16.9% to 79.6%; member MAE worsens by about 30.5%
and ensemble-mean MAE by about 0.12%. The historical ensemble has slightly better
fair CRPS. Central-profile error and variation between individual years describe
different properties. All reported regressions remain in the comparison.

Reporting years had previously been inspected and are retrospective. Native
objective comparisons retain their configuration-specific units; cross-configuration
currency and cost-per-MWh comparisons require common price-year and normalization
provenance. Overlapping window objectives are reported separately.

## Autoencoder study

The separate autoencoder repository retains one validation-selected CVAE and its
historical comparison corpus. Its annual wind-mean SD is 28.3% of that historical
reference, compared with 68.3% for its pipeline comparator. Other diagnostics,
including those favorable to the autoencoder, remain reported there. That study
is a historical benchmark and does not compare the CVAE with the final solar
release. The measured annual resource diversity supports the application choice
under the tested training protocol.
