# Verification

Run `python -m unittest discover -s tests -v`, `python audit.py` and
`python analysis/manuscript_results.py --output exports/v62_tables`.

The root manifests verify delivered bytes and code. The unchanged climate
models, 100-member ensemble, selection, profile scores and 17 exact manuscript
images retain their recorded identities. Training/validation/reporting boundaries
are unchanged; the retrospective reporting period had previously been inspected.

For V6.2, `analysis/thermal_results.py` verifies the compact accepted extract
against the original full-store audit and accepted-receipt hashes for all 42
national windows. It checks component closure, numerical solution quality,
net-import accounting, demand denominators and temperature-demand rankings.
The source audit checked native ledgers, complete state handoffs and frozen
inputs on the server. Large native stores are excluded from the public delivery;
local table reproduction does not re-solve the energy model.

The unchanged zonal archive retains all three planning portfolios and 45
operating checks. Its portable auditor rechecks the included hourly balances,
stock, exchange and storage-cycle records. Three 2021 zero-exchange controls with
exclusive battery operation are tracked separately and are not substituted
until their physical/optimization checks and comparison are accepted.

Thermal tests cover weather-dependent annual service quantities, shared COP,
unclipped cooling, exact selected-schedule application and unchanged caller data.
The older fixed-total helpers are tested as legacy methods only.
