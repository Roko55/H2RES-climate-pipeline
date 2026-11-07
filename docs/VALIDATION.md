# Verification

Run the tests, `python audit.py`, offline demo, summary command and solar replay
listed in README. CI repeats the workflow from a clean Python 3.11 checkout.
The audit does not run optimization, training or modify solver inputs.

## Climate pipeline

Artifact/source manifests bind code, ten native models, accepted profiles,
results and figures. Tests cover causal features, split isolation, malformed
inputs, capacity-weighted conservation under saturation, availability keys,
thermal baselines and adapter installation outside this checkout.
Solar replay exactly regenerates the accepted solar channel. Wind/temperature
are retained; full generation needs the original hash-bound weather corpus.

Training/normalization uses 1980–2015; validation is 2016–2019. Accepted solar
uses inner 1980–2011 fitting and 2012–2015 tree-count tuning, then refits on the
training period. Seven quantiles and postprocessing are frozen in its runtime
recipe. Temperature uses its recorded three quantiles. Reporting 2020–2024
had previously been inspected and is retrospective. Independent-channel
generation does not establish coherent joint extreme-event probabilities.

## Calibrated energy evidence

The original read-only auditors were run on the server against the full native
national and zonal stores. Reports verify 42 national windows, complete state
handoffs, frozen inputs/model sources, three zonal planning portfolios and 45
operating checks. The export contains 1600 original files with per-file SHA256
and ZIP CRC verification, supplemented by hash-bound native demand statistics
and three synthetic 2030 profile extracts. No new simulation was run for this
publication. Original code, receipts and reports preserve their exact bytes.

Local national checks hash every delivered original file, bind accepted records
to the full-store audit, check the seven model signatures, common inputs,
42 native ledgers, component closures, capital residuals and all stationary
profile/mapping identities. Tables are regenerated from the delivered records
and compared with the committed summaries. Demand statistics are bound to the
original accepted parameter-file hashes and their exact extraction script.

Local zonal checks independently read all 48 hourly files: 8760 hours × three
zones per job. They verify electricity and water balances, annual demand,
service-hour counts, surplus, exchange, stock identity, planning-to-operation
handoffs, cyclic storage and objective closure. They do not assume zero
unserved energy. All three 2021 isolated shortfalls remain visible.

The full national variable/constraint gzip files, pickled states, large raw input
files and third-party databases are not redistributed. Their accepted hashes
and the server auditor's checks are retained. Thus local verification is not a
fresh independent re-solve or a replay of all national hourly variables.
See [archive scope](../evidence/calibrated/README.md).

## Manuscript identity

`provenance/manuscript/scope.json` binds the checked V6.1 manuscript SHA256,
17 exact embedded images, 42 new national windows and 45 separate operating
checks. Figures 3–6, nine profile-score rows, fair-CRPS inputs, fitted models and
accepted ensemble are unchanged. Energy cohorts replace the earlier V6 results;
prior tags retain their original snapshots. Source rights remain unchanged.

The national model boundary retains annual heat normalization, inherited
financial conventions and already inactive local RPS/aggregate CO2 targets.
A uniform real price year and an exclusive charge/discharge sensitivity are
not established by these checks. These limits constrain interpretation even
when numerical and artifact checks pass.
