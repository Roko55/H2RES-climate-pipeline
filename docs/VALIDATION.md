# Verification and reproduction

Run the regression checks and artifact audit from the repository root:

```bash
python -m unittest discover -s tests -v
python audit.py
```

The tests cover past-only features, chronological partitions, training-bounded
national calibration, UTC solar geometry, quantile ordering, native serialization,
wind sampling, scenario selection, spatial conservation, runtime keys and annual
thermal totals. GitHub Actions uses
Python 3.11 on Ubuntu; local checks also use Windows. The offline demo uses
illustrative inputs and exercises the adapter independently of the solver.
H2RES optimization requires the separate model, input database and licence.

## Evaluation records

Training covers 1980-2015, validation 2016-2019 and reporting 2020-2024.
Reporting years were inspected during development. National CF calibration
uses training years; early stopping, residual calibration and annual-rollout
selection use validation years. A single solar scale and temperature offset
match validation-period means and are frozen before reporting evaluation.
Source identities, split checks and replay records are under `provenance/`.

Central-profile errors evaluate the calendar-conditioned ensemble mean.
Individual-path statistics evaluate diversity and temporal behaviour.
Independent channels define designed sensitivity combinations. Distribution
results are recorded in `results/profile_validation/`, including empirical
interval coverage in `intervals_and_CRPS.csv`. These measured coverages, rather
than quantile labels alone, determine the interpretation of ensemble ranges.

Solar geometry uses source coordinates, UTC and the equation of time. Each
timestamp marks the start of an hourly averaging interval; daylight support
is checked across that interval. Both calibration and generation reorder
the three predicted quantiles before forming the median and noise width.
Initial autoregressive histories come from calendar-aligned training-year
endings. Independently seeded channel streams preserve separate generation.

Annual calibration screens 24 drift/noise settings per channel with 12 paths
and confirms three finalists with 64 fresh paths. Coverage, calendar spread,
annual-mean spread, ramps and mean bias must satisfy the declared checks in
`config/rollout_calibration.json`. The delivered bundle includes the selected
settings. Complete 100-member native-model CLI replay matches the frozen
research array bit for bit in the recorded runtime; its evidence is in
`provenance/model/saved_model_replay.json`.

The same 100-member array is also reproduced bit for bit after installing the
pinned requirements and package in a separate clean Python 3.12 environment.
That check includes the regression suite, the artifact audit, the offline
walkthrough and adapter import from an unrelated project with its own `scripts`
package. It verifies both the native-model entry point and installed namespace.

Country-scoped temperature integration additionally verifies that heat-demand
updates preserve all non-target zones exactly. The complete regression suite
contains 36 tests.

Spatial mapping applies historical ratios. Thermal normalization preserves
annual totals and reshapes their hourly distribution; COP follows the stated
bounded relationship. Data access and reuse conditions are in [RIGHTS.md](../RIGHTS.md).
