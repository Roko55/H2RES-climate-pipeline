# Recorded application comparison

For annual H2RES resource cases, the primary comparison concerns interannual wind variability. The validation-selected autoencoder produced wind annual-mean SD equal to 14.0% of the reporting-year reference; the default hybrid produced 74.8%. The autoencoder performs better on several other statistics. Both methods have limitations, including annual solar/temperature diversity.

![Annual wind and renewable-yield variability](../figures/application_comparison.png)

The grey bars represent five historical reporting years, not an older H2RES model. Orange is one selected autoencoder; blue is this pipeline's default refit. Heights show SD across annual averages, not hourly error or training loss. The renewable-yield index is annual mean solar CF + wind CF, not actual system generation.

| Recorded measurement | Autoencoder | Current pipeline |
|---|---:|---:|
| Training / preparation | 3,600 s | 131 s |
| Peak process RAM during preparation | 1,477 MiB | 1,012 MiB |
| Generate 15 years on the common CPU, after load/warmup | 2.386 s | 52.285 s |

Preparation uses different fitting stages and hardware: Quadro P2000 CVAE training versus hybrid GPU fitting plus CPU calibration/preparation. These are measured workflows, not a matched-hardware time-to-equal-quality benchmark. Full tables remain in [results](../results/), including separate configurations and favourable autoencoder results.

**Evaluation scope:** upstream calibration used years overlapping the reporting period. The comparison is retrospective on a common pre-calibrated corpus, not an independent temporal holdout. The audit does not quantify the effect on model ranking. No downstream optimization was rerun for this comparison.
