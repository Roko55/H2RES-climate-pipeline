# H2RES climate pipeline

A variable-specific generator and scenario interface for annual hourly H2RES inputs. Solar and temperature use autoregressive XGBoost quantile models; wind uses season-aware historical block resampling. Representative annual cases are selected by renewable yield and thermal stress, then mapped to generation-unit availability profiles. Temperature-coupling utilities redistribute heat demand and adjust heat-pump COP.

Companion experiment: [H2RES autoencoder](https://github.com/Roko55/H2RES-autoencoder).

## Repository scope

| Component | Implementation | Output |
|---|---|---|
| Solar and temperature | Three quantile regressors per channel, autoregressive generation, calibrated correlated noise | Hourly solar CF and temperature |
| Wind | Season-aware 144-hour historical block resampling with boundary blending | Hourly wind CF |
| Representative cases | Renewable-yield and thermal-stress clustering, explicit selection method | Low/average/high resource and cold/normal/hot cases |
| Spatial interface | Source-zone ratio profiles and transfer matrices | H2RES unit availability arrays |
| Temperature coupling | Demand-shape normalization and temperature-dependent COP | Runtime demand/COP dictionaries in an H2RES application |

The profile array has shape `[annual_profiles, 8760, 3]`, with channels `[solar, wind, temperature]`, in capacity factor, capacity factor and degrees Celsius. Leap days are excluded. Channels are generated independently; combined cases are designed sensitivities, not physically coherent multivariate weather samples.

This repository covers climate generation, case selection and interface preparation. The H2RES optimization model, Gurobi licence and complete solver input database are separate dependencies. Running these commands does not reproduce or revalidate the manuscript's investment or operational results.

## Installation and verification

Use Python 3.11 in a dedicated environment:

```bash
python -m pip install -r requirements.txt
python -m unittest discover -s tests -v
python audit.py
```

No dataset download, fitting or solver execution occurs during installation or the synthetic tests. Native XGBoost model files are included; raw training weather and spatial source datasets are not redistributed. See [data requirements](docs/DATA.md) and [rights](RIGHTS.md).

## Generate annual profiles

Supply the exact `HR_data.parquet` identified in [data provenance](provenance/data_provenance.json). The default model bundle is the recorded seed-42 comparison refit, not a certified copy of the original manuscript checkpoint.

```bash
python pipeline.py generate --data data/SC/HR_data.parquet --count 15 --seed 101 --output exports/profiles_101.npy
```

The entry point checks complete hourly chronology, source-data identity, all six model hashes, calibration metadata and source compatibility. Wind is resampled from the training partition only. Generation uses one CPU thread. Changed input data, missing models and existing outputs are rejected; missing wind data are not replaced by noise.

## Select annual sensitivity cases

Choose the combination rule explicitly:

```bash
python pipeline.py select --profiles exports/profiles_101.npy --method orthogonal --output exports/cases_101.npz
```

`orthogonal` combines the renewable representative with the temperature representative from separate axes, allowing controlled channel comparisons. `joint_medoid` retains complete generated annual profiles, selecting within joint clusters or the nearest available member when a cluster is empty. These are different experimental designs. Cluster fractions are ensemble summaries, not calibrated probabilities. Selection here applies no future climate scaling. The legacy module's optional future-year scaling is separate from baseline case selection.

## Export to H2RES units

Provide the spatial source profiles, transfer matrices and existing H2RES unit definitions described in [DATA.md](docs/DATA.md):

```bash
python pipeline.py export-units --scenarios exports/cases_101.npz --scenario avg_yield__normal --mapping-dir data/SC --country HR --wind-resolution auto --output exports/units_101.npz
```

The output contains one 8,760-hour capacity-factor array per unit and a JSON record of input hashes and capacity-weighted mapping errors. Missing spatial artifacts, invalid unit arrays and mapping error beyond the requested tolerance cause failure. Temperature is passed separately to `scripts/temperature_coupling.py` by the external H2RES runtime. See [integration](docs/INTEGRATION.md).

## Fit a new model bundle

```bash
python pipeline.py train --data data/SC/HR_data.parquet --seed 42 --latitude 44.52 --output exports/new_fit
python pipeline.py generate --data data/SC/HR_data.parquet --model-dir exports/new_fit --count 15 --seed 101 --output exports/new_profiles.npy
```

New CPU fitting uses the maintained causal-feature implementation, chronological 80%/10%/remainder splits by complete years, training-only climatology and validation calibration. For the 45-year source corpus these correspond to 1980–2015, 2016–2019 and 2020–2024. New training results are separate from the delivered results. The recorded default bundle used the original implementation; [AUDIT.md](AUDIT.md) identifies the warmup correction and generation compatibility evidence.

## Application comparison

For annual H2RES resource cases, the primary comparison concerns interannual wind variability. The validation-selected autoencoder produced wind annual-mean SD equal to 14.0% of the reporting-year reference; the default hybrid produced 74.8%. The autoencoder performs better on several other statistics. Both methods have limitations, including annual solar/temperature diversity.

![Annual wind and renewable-yield variability](figures/application_comparison.png)

The grey bars represent five historical reporting years, not an older H2RES model. Orange is one selected autoencoder; blue is this pipeline's default refit. Heights show SD across annual averages, not hourly error or training loss. The renewable-yield index is annual mean solar CF + wind CF, not actual system generation.

| Recorded measurement | Autoencoder | Current pipeline |
|---|---:|---:|
| Training / preparation | 3,600 s | 131 s |
| Peak process RAM during preparation | 1,477 MiB | 1,012 MiB |
| Generate 15 years on the common CPU, after load/warmup | 2.386 s | 52.285 s |

Preparation uses different fitting stages and hardware: Quadro P2000 CVAE training versus hybrid GPU fitting plus CPU calibration/preparation. These are measured workflows, not a matched-hardware time-to-equal-quality benchmark. Full tables remain in [results](results/), including separate configurations and favourable autoencoder results.

**Evaluation scope:** upstream calibration used years overlapping the reporting period. The comparison is retrospective on a common pre-calibrated corpus, not an independent temporal holdout. The audit does not quantify the effect on model ranking. No downstream optimization was rerun for this comparison.

## Source and evidence

`scripts/` contains the generator, scenario manager and temperature coupling. `pipeline.py` supplies validated commands. `tests/` contains synthetic regression checks. `models/default/` contains native quantile models and calibration metadata. `provenance/` records measured execution, source hashes, source compatibility and manuscript correspondence. [AUDIT.md](AUDIT.md) defines validation coverage and limits.
