# Profile comparison

The research evaluation distinguishes the repeated central generated profile
from individual annual paths. Against the fixed H2RES baseline, reporting-period
solar and wind MAE decrease by 47.5% and 44.6%. Temperature MAE changes from
3.177 to 3.168 degrees Celsius. These are central-profile comparisons, not
event forecasts. Full metrics are in `results/profile_validation/`.

For annual wind resource diversity, five historical years are compared with
15 individual autoencoder paths and 15 pipeline members. The autoencoder
checkpoint is validation-selected. Both generators use the same
training-calibrated corpus, sampling settings and reporting reference.

| Wind diagnostic | Historical | Autoencoder | Pipeline |
|---|---:|---:|---:|
| SD of annual mean CF | 0.007968 | 0.002258 | 0.005439 |
| Fraction of historical annual-mean SD | 100% | 28.3% | 68.3% |
| Mean 95th-percentile absolute hourly ramp | 0.090553 | 0.057743 | 0.085004 |

![Annual wind diversity](../figures/annual_wind_comparison.png)

The autoencoder is better on mean wind path score (0.302637 versus 0.345773)
and Wasserstein distance (0.022452 versus 0.025282). The pipeline better
represents annual wind diversity and ramps in this sample; no uniform
superiority or better H2RES investment outcome is inferred. Plotting values
and metrics are in `results/wind_comparison/`.

National calibration uses 1980-2015, validation uses 2016-2019 and reporting
uses 2020-2024. The reporting period was previously inspected. Descriptive
sample spreads are not calibrated rare-event probabilities.
