# V6.2 national thermal evidence

This compact archive supports all 42 accepted national planning windows in V6.2.
The exact export and manifest bind each case/year to the original accepted receipt
and native solver, investment, objective-ledger and runtime-input hashes.

- `national_plot_inputs.json`: exact source extract with annual outcomes, active and diagnostic cost groups, realized additions and first-anchor parameter statistics.
- `source_manifest.json`: accepted receipts and native source hashes, plus original export and source-audit identities.
- `preserved_national_audit.json`: original full-store audit, with its exact original SHA256 restored after text transport.
- `export_v62_remote.py`: exact read-only extractor used at source; it never imports an optimizer.
- `audit_preserved_national.py`, `audit_national_completion.py`: source-audit implementations; their original server dependencies are required to rerun the full-store audit.
- `manifest.json`: local byte identities for this compact delivery.

`analysis/thermal_results.py` derives the public CSVs from this extract and checks
them against the original source audit. Investment rows aggregate the saved
nonzero realized physical additions by family/unit; they do not reproduce every
original financial coefficient or provisional decision. Runtime parameter
statistics here cover each realized first anchor only. The large native hourly
variables, constraints, state snapshots and third-party database remain on the
server; local reproduction is a summary extraction, not a fresh solve.

The calibrated coefficients and selected synthetic thermal schedules are in
`data/weather_energy`. No raw reference-weather series is included.
The unchanged 45 zonal operating tests are in `evidence/calibrated`.
Additional exclusive-battery controls are not inserted until verified.
