# Verification scope

Run tests, `python audit.py`, the offline demo and `python pipeline.py replay`
as listed in README. CI repeats these checks from a clean Python 3.11 checkout.

Executable checks cover artifact hashes, native-model identities, accepted-array
shape/bounds, exact native solar replay, final multisector selection hashes,
causal features, split isolation, rejected malformed inputs, mapping conservation
under saturation, availability key contracts and thermal annual totals.
The illustrative demo exercises selection through unit/thermal export without
an optimizer. It is not evidence of meteorological quality or investment benefit.

`replay` regenerates solar and retains the frozen wind/temperature channels.
Full three-channel generation additionally requires the hash-bound raw corpus.
No dataset-free full native regeneration or full retraining claim is made.

The final archive was verified against its package manifest before extracting
publication tables. `provenance/final/source_records.json` records original record
hashes. Public tables normalize source path columns for portability; their hashes
therefore differ from the original delivery tables listed in final integrity.
No scientific values are changed by this normalization.

`provenance/final/final_integrity.json` reports 63 planning and 45 operational
cases. Its `publication_ready:false` field records outstanding **manuscript and
price-year interpretation** conditions, not a failed solver run. This repository
does not assert that the manuscript was updated. Jobspec/dependency records were
created before execution; completed integrity and delivery tables determine final
run status. Frozen executed adapter source is retained separately from portable
entry points, whose hashes are recorded in `source_manifest.json`.

Training/normalization uses 1980-2015; validation is 2016-2019. Accepted solar uses
inner 1980-2011 fitting and 2012-2015 tree-count tuning before refitting on the full
training period. The seven solar quantiles and frozen postprocessing are recorded
in `models/default/solar/runtime_recipe.json`. Temperature calibration is the
Temperature entry of `provenance/final/temperature_calibration.json`; its older Solar
entry is superseded by the accepted solar recipe. Reporting 2020-2024 had previously
been inspected and is retrospective, not a newly untouched test.

The channels are generated independently. Wind uses seasonal six-day blocks with
four-hour boundary blending. This is not a fitted spatial storm model or proof of
seam-free weather dynamics. No method is claimed best possible or uniformly superior.
