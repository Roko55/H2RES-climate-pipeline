# Verification

Run the regression tests, `python audit.py`, the offline demo and `pipeline.py replay`
as listed in README. CI repeats them from a clean Python 3.11 checkout. It also
exports the seven single-channel profiles and reproduces their summary tables/plots.

Artifact checks cover the delivered code, native models, accepted profile array,
result tables and verification records. Regression tests cover causal features,
chronological split isolation, rejected malformed inputs, aggregate conservation
under saturation, availability keys and explicit thermal baselines. Solar replay
regenerates the accepted channel exactly. Wind/temperature are retained from the
verified ensemble; full generation needs the original hash-bound corpus.

## Stationary-weather multisector analyses

The source experiment is `channel_sensitivity_20261102`. Its archive SHA256 is
`3ba80608a1faf9ddeb2685498e06aa8f4f60d0ba343425b4cc716b3d08635b1d`.
All 20 package-manifest member hashes and ZIP CRCs were verified before publication.
`provenance/channel_sensitivity/source_records.json` links original records to
delivered files; `original_package_manifest.json` retains the archive inventory.
The root `artifact_manifest.json` verifies the publication inventory.

The completed records contain 49 newly solved optimal planning windows and no
reused windows. The portable analysis reconciles objective components, realized
investment differences and stock totals; recomputes donor selection; verifies all
49 profile hashes, means and degree hours; and rejects weather changes across
planning anchors. It also checks the archived 2009 stock, 273 emission and 49
balance records. `completion_audit.json` records independent server verification
of the frozen sources, numerical acceptance and complete state handoffs for all
49 windows. The audit did not start new optimizations.

Numeric source tables are unchanged. Only source paths in the objective table are
made relative, preserving numeric tokens. Preparation paths are normalized for
portability. Design metadata extracts the scientific protocol from the source
plan. The original execution overlay and independent audit sources are retained
under `executed_source/`; preparation records identify the frozen server sources.
These sources document execution in the original environment. Public entry points
are the portable profile selector and summary command.

The selected case uses one unique hourly profile hash across 2020-2050: solar and
wind factors 1.0, temperature offset 0.0 C. Prices, policies, asset aging and the
path's own inherited stock can still change between planning anchors.

The earlier trend experiment remains separately reproducible using
`python analysis/archive_trend_results.py --output exports/trend_summary` and its
[archived protocol](TREND_EXPERIMENT.md). Its original v0.5.1 tag is preserved.

The seven stationary-weather paths have no new historical operational tests.
The existing 45 tests retain their original electricity portfolios. Publication checks invoke no solver;
reproducing energy optimizations requires the original H2RES model, inputs and
licensed solver. Raw weather, asset databases and solver licence files remain
excluded; [reuse rights](../RIGHTS.md) are unchanged.

## Earlier analyses and profile models

`provenance/final/final_integrity.json` covers the earlier 63 planning and 45
operational cases. Its `publication_ready:false` field records manuscript and
price-year interpretation conditions. The original preparation status fields
precede execution; completed integrity records establish run completion.
Source path normalization is recorded in `provenance/final/source_records.json`.

Training/normalization uses 1980-2015; validation is 2016-2019. Accepted solar uses
inner 1980-2011 fitting and 2012-2015 tree-count tuning, followed by refitting on
the full training period. Its seven quantiles and postprocessing are frozen in
`models/default/solar/runtime_recipe.json`. The Temperature entry in the earlier
calibration record supplies the accepted temperature settings; solar uses its own
later recipe. Reporting 2020-2024 had previously been inspected and is retrospective.
Independent-channel generation and designed sensitivity selection determine the
scope of the conclusions. Model diagnostics and energy-system outcomes are reported
as separate evaluations.
