# Checked V6.2 results

Manuscript SHA256:
`77c2073de4a49522036e19ff97abeaf8b665331807fa1a8b4c6a112c748c5565`.
This identifies a fixed checked snapshot, not later manuscript revisions.
See [Result columns and units](RESULT_COLUMNS.md) to interpret the CSV fields
and [Getting started](GETTING_STARTED.md) to use the pipeline.
The accepted climate ensemble is unchanged, SHA256
`d86a855166c24d699d511010184db12d0baf1551f83254b41f661c602d7ae728`.

## Profile evaluation — section 4.1

Training is 1980–2015, validation 2016–2019, reporting 2020–2024 (43800 hours).
Reporting had previously been inspected and is retrospective. Figures 3–6 and
their climate score tables are unchanged from v0.7.0. `profile_metrics.csv`
contains nine MAE rows: three channels × generated mean, fixed H2RES reference
and training-period hourly mean. Solar/wind use CF units; temperature uses C.

Figure 5 uses `100*(generated MAE/reference MAE-1)`. Changes versus the fixed
reference are −53.58% solar, −4.88% wind and −8.33% temperature; versus the
historical mean they are −0.16%, +0.52% and −8.33%. Both temperature references
are identical. Fair CRPS compares 100 generated and 36 historical members on
the same calendar, with finite-ensemble correction: +2.01%, +1.60%, −7.48%.
Negative changes indicate smaller error/score. These are different evaluations
from investment and operating outcomes.

## National planning — sections 4.2–4.5

The [seven stationary-weather paths](CHANNEL_RESULTS.md) share a calibrated 2020
base, followed by 42 newly solved windows in 2025–2050. Each inherits its own
complete realized state. Solar and wind costs follow Low > Average > High at
every future window. No new electricity-generation capacity is selected.
Average cumulative heating additions are 7788.839 MW; Cold requires 8204.488 MW
and Warm 7373.401 MW. Figure 14 shows heating additions only. Temperature now
changes annual heat/cooling needs through common 2020-calibrated coefficients.

The first Average window objective is 1551137209.109303 EUR. Its realized 2025
contribution is 635884950.3005005 EUR. These are different quantities.
Average net imports, including existing Krško rights once, are 5.26296 TWh in 2025,
6.67129 TWh in 2030 and 20.39274 TWh in 2050. Warm 2050 includes a one-hour
49.64909 MWh electricity shortfall, whose penalty affects the window cost.
Solar cost differences come from net purchases; wind non-purchase differences
are below 27 EUR. Figure 12 has two ordinary panels, Cold and Warm, covering all planning windows.

## Fixed-portfolio operation — section 4.6

`zonal_2030_production_05` contains three full 8760-hour 2030 planning portfolios
(Low, Average, High), then 45 fixed-stock operating checks: three portfolios ×
five historical years (2020–2024) × full/half/zero HR exchange. HR, SI and BA are
separate balanced zones; SI–BA exchange remains represented. These portfolios
are distinct from the seven national multisector paths. Historical weather
years do not change the exogenous 2030 zonal load totals.

All 30 full/half-exchange checks have zero HR unserved energy. At zero HR
exchange, 12 of 15 do; three checks in weather year 2021 have shortfalls:

| Fixed portfolio | HR unserved MWh | Hours |
|---|---:|---:|
| Average | 70.40645 | 1 |
| Low | 158.21978 | 3 |
| High | 65.22580 | 1 |

Zero-exchange gross HR imports and exports are zero; CEEP is approximately
1.52–3.42 TWh. All 45 cases, including these shortfalls, are published.
CEEP is surplus electricity; it is distinct from energy not served. Cyclic
storage and fixed solved stocks are verified. Some solutions show simultaneous
battery charge/discharge; the overlap is recorded. Three exclusive-operation controls for 2021/zero HR exchange are being checked
separately. Their latest completion has not been verified; the 45 original
operating results above are retained until that comparison is accepted.

## Manuscript figure index

The files below are exact copies of the corresponding V6.2 embedded images.
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
| 14 | Average new heating capacity (electricity additions are zero) | [Figure 14](../results/manuscript/figures/figure_14.png) |
| 15(a)-(b) | Average supply and direct emissions | [15(a)](../results/manuscript/figures/figure_15a.png), [15(b)](../results/manuscript/figures/figure_15b.png) |
| 16 | CEEP in the 45 operating cases | [Figure 16](../results/manuscript/figures/figure_16.png) |

Figures 1–2 (workflow/method) are also included. All 17 embedded image files and
captions are indexed in `provenance/manuscript/scope.json`.

## Reproduction

```bash
python audit.py
python analysis/manuscript_results.py --output exports/v62_tables
```

The commands verify evidence and reproduce the numerical summary tables and
MAE comparison plot without a solver. Embedded manuscript images are retained
exactly; the commands do not redraw every manuscript figure. Recomputing climate
scores/historical plots needs the excluded weather corpus. Re-solving H2RES
needs its original full database, runtime and licensed optimizer. See
[native evidence](../evidence/calibrated/README.md) for the precise archive scope.
