# Verification and reproduction

Run the regression checks and artifact audit from the repository root:

```bash
python -m unittest discover -s tests -v
python audit.py
```

The tests cover past-only features, chronological partitions, training-bounded
calibration, native serialization, wind sampling, scenario selection, spatial
conservation, runtime keys and annual thermal totals. GitHub Actions uses
Python 3.11 on Ubuntu; local checks also use Windows. The offline demo uses
illustrative inputs and exercises the adapter independently of the solver.
H2RES optimization requires the separate model, input database and licence.

## Evaluation records

Training covers 1980-2015, validation 2016-2019 and reporting 2020-2024.
Reporting years were inspected during development. National CF calibration
uses training years; stopping and residual calibration use validation years.
Source identities, split checks and replay records are under `provenance/`.

Central-profile errors evaluate the calendar-conditioned ensemble mean.
Individual-path statistics evaluate diversity and temporal behaviour.
Independent channels define designed sensitivity combinations. Distribution
results are recorded in `results/profile_validation/`, including empirical
interval coverage in `intervals_and_CRPS.csv`. These measured coverages, rather
than quantile labels alone, determine the interpretation of ensemble ranges.

The archived solar gate uses a latitude-based clock approximation. Its
agreement with input timestamps is measured in `solar_gate_consistency.json`.
Separate quantile trees can cross; generation clips negative half-widths to
zero. Counts are in `provenance/model/saved_model_replay.json`. Exact replay
of the reported solver inputs uses the frozen ensemble identified in
`provenance/model/ensemble_freeze.json`.

Spatial mapping applies historical ratios. Thermal normalization preserves
annual totals and reshapes their hourly distribution; COP follows the stated
bounded relationship. Data access and reuse conditions are in [RIGHTS.md](../RIGHTS.md).
