# Verification

Run these commands from the repository root:

```bash
python -m unittest discover -s tests -v
python audit.py
python analysis/manuscript_results.py --output exports/current_results
```

The artifact manifest checks file sizes and SHA256 identities. The audit also
checks ten native models, the 100-member ensemble, selected annual profiles,
profile-score comparisons and the 17 manuscript figures. Training covers
1980-2015, validation 2016-2019 and reporting 2020-2024. The reporting period
had been inspected during development and is evaluated retrospectively.

## National planning

The compact extract in `evidence/final` reconciles with the preserved source
review of the full native stores. Checks cover 42 annual ledgers, component-cost
closure, gross/net electricity exchange, demand coverage, no new gas investment,
direct electricity/heating emissions limits and carried-capacity residuals.
The full-store audits were performed on the native database at execution time.
Public table reproduction checks the compact extract; it does not rerun H2RES.

## Fixed-portfolio operation

The separate zonal archive contains three 2030 planning portfolios and 45
full-year operating cases. Its auditor checks included hourly balances, stock,
exchange limits and storage cycles. The three 2021 zero-exchange cases have
small annual shortages. The constructive battery check is a feasible hourly
balance comparison, not a certificate of a global exclusive-battery optimum.
No additional exclusive-battery optimization results replace the 45 cases.

## Thermal coupling and source archives

Thermal tests cover weather-dependent annual service quantities, shared COP,
unclipped cooling, exact schedule application and unchanged caller dictionaries.
Fixed-total helpers are tested separately from the weather-dependent adapter.

`analysis/thermal_results.py` and `analysis/native_results.py` check supporting
archives against their own source receipts and schemas. These archives preserve
native execution evidence and do not supply the current national result tables.
Raw weather, full native state stores and the third-party database are external.
Re-solving the energy model requires those inputs and a licensed optimizer.
