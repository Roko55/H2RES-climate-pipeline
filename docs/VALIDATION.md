# Validation coverage

The regression suite checks past-only features, chronological partitions,
training-bounded calibration, native serialization, wind sampling, scenario
selection, mapping conservation, runtime keys and thermal normalization.
Run `python -m unittest discover -s tests -v`, `python audit.py` and the offline
demo. The demo uses illustrative data. A small fitting fixture checks model
serialization; it is not a research refit or a performance benchmark.

GitHub Actions targets clean Ubuntu with Python 3.11. Local checks also use
Windows. The adapter is importable from a separate H2RES project, including
projects with another `scripts` package. The complete external solver model,
input database and licence are not included or exercised by these unit tests.

Research inputs use training-only national CF calibration. The reporting
period was previously inspected; it is not an untouched final test. Source,
model and data hashes, split checks and replay records are under `provenance/`.

Central-profile errors describe the calendar-conditioned ensemble mean, not
forecasts of specific historical events. Individual-profile diversity and
temporal behaviour answer different questions. Independent channel generation
defines sensitivity combinations, not a calibrated joint meteorological
distribution. No calibrated tail probabilities or universal method superiority
are established. The pipeline has narrow annual solar/temperature diversity.

The saved solar geometry gate is approximate; its consistency with source
timestamps is quantified in `results/profile_validation/solar_gate_consistency.json`.
Separate quantile trees may cross; archived counts are in
`provenance/model/saved_model_replay.json`. Physical CF bounds are imposed on
generated values, but the quantile levels are not calibrated uncertainty
coverage guarantees. Runtime differences can accumulate autoregressively;
exact solver-input reproduction uses the frozen ensemble.

Spatial mapping uses historical ratios, not newly simulated spatial weather.
Thermal demand normalization preserves annual totals; COP uses a bounded
model relationship rather than measured heat-pump efficiency. These modelling
assumptions must match the intended application.

Raw data and reuse rights remain separate from technical reproducibility.
See [RIGHTS.md](../RIGHTS.md).
