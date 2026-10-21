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
uses training years; early stopping and residual calibration use validation
years. Generation retains the fixed drift weight of 0.4 for both channels,
without an additional noise multiplier, solar scale or temperature offset.
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
endings. Solar and temperature consume consecutive reproducible random draws;
wind uses its own seeded bootstrap. Generation does not condition one channel
on another.

The generation policy and six native model identities are recorded under
`provenance/model/`. Complete 100-member native-model CLI replay is checked
against the frozen research array; the result, runtime versions and actual
source hashes are in `provenance/model/saved_model_replay.json`. GitHub Actions
checks the regression suite, artifact inventory, installed adapter and offline
walkthrough. It does not run the private weather corpus or H2RES solver.

The complete 100-member CLI replay also matches bit for bit in the isolated
Python 3.12 environment with the pinned requirements. That environment passes
the same 35 regression tests and an installation into a separate private target
directory; the installed adapter is checked outside the repository.

Country-scoped temperature integration additionally verifies that heat-demand
updates preserve all non-target zones exactly.

Spatial mapping applies historical ratios. Thermal normalization preserves
annual totals and reshapes their hourly distribution; COP follows the stated
bounded relationship. Data access and reuse conditions are in [RIGHTS.md](../RIGHTS.md).
