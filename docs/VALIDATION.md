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

## Single-channel multisector analyses

The source archive SHA256 is
`34c87f8be8f8333f17e4331bb3cfe687e3c4b354246fec9aa7b9b227916c1d81`.
All 20 package-manifest member hashes and ZIP CRCs were verified before publication.
`provenance/channel_investments/source_records.json` links original records to the
delivered files. `original_package_manifest.json` identifies the original archive;
the root `artifact_manifest.json` verifies the actual publication inventory.

The completed records contain 49 optimal planning windows, 42 new and seven reused,
with 2009 stock, 273 emission and 49 balance checks passed. The portable analysis
reconciles objective components, realized investment differences and stock totals;
recomputes the donor selection; and verifies all 49 input profile hashes and means.
The reused Average objectives and source hashes also match the earlier completed
multisector Average. Full input/state reuse audits are retained in reference_decision.

Published numeric result tables are unchanged. Only the `source` path in the
objective table is made relative. Relative paths also replace machine paths in
preparation/reference records. Design metadata extracts the completed scientific
protocol from the original plan. Scientific execution notes preserve observer
compatibility and unchanged input/equation checks. The five frozen selection,
worker-building and collection sources are retained in `executed_source/`; their
hashes match preparation. They depend on the original model environment and serve
as execution provenance. The portable profile selector and summary command are
the public entry points.

The seven new paths have no new historical operational tests. Those tests remain
limited to the earlier electricity portfolios. No solver is invoked by these
publication checks; reproducing the energy optimizations requires the original
model, inputs and licensed solver.

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
