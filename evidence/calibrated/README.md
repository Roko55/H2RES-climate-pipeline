# Native calibrated V6.1 evidence

This directory preserves original execution records beneath their source-relative
paths. It is an evidence archive. Public profile export and summary entry points
are at the repository root; these frozen server scripts require their original
input database/runtime and should not be launched as a standalone installation.

| Path | Contents |
|---|---|
| `national_production_03/` | 42 accepted future-window receipts, annual results, investment terms, objective components/ledgers, build/solution audits, frozen executed Python sources, common base and recovery receipts |
| `zonal_2030_production_05/` | Three planning and 45 operating jobs, portfolios, solver/matrix/input receipts and all 48 `hourly.csv.gz` files |
| `zonal_2030_matrix_06/` | Original matrix diagnostic source context |
| `publication_v61_20261107/` | Exact read-only collection/auditor sources and reports from the full native stores |
| `source_manifest.json` | Per-file SHA256 and byte count for all 1600 original exported files |
| `native_input_statistics.csv` | Counts, sums and extrema from 42 accepted native parameter files, including next-anchor parameters |
| `native_input_statistics_sources.json` | Original parameter and accepted-receipt hashes, statistics/script identity |
| `summarize_native_inputs_remote.py` | Exact read-only statistics extractor used at source |
| `electricity_{avg,low,high}.csv` | Synthetic 2030 hourly profiles used by the three zonal planning cases |
| `electricity_profiles_sources.json` | Original full profile hashes, subset hashes and executed mapped-weather identities |
| `export_electricity_profiles_remote.py` | Exact subset extraction script |
| `weather_preflight.json` | Profile-to-mapped-weather source identity and spatial conservation checks |

The original export ZIP SHA256 is
`2e9bf82d9653fa8f5a7c1944279977a8dd1d862ca44d37c55c5b7f13f4d3fdbe`
(61150671 bytes). The ZIP is not duplicated here; all its original members are
included. Supplementary extracts and this index are covered by the root artifact
manifest. No optimizer was invoked by either extraction script.

National pickled states, large hourly variable/constraint stores, raw parameter
gzip files and original third-party input databases remain excluded. Their
hashes are preserved in accepted receipts; the original auditors checked the
full stores on the server. The national auditor cannot be replayed against only
this reduced archive. The portable zonal auditor can replay all included hourly
balances without a solver. Original absolute paths are provenance, not required
public installation locations. Historical matrix/preparation records retain
their original statuses; final accepted results and completion audits establish
which runs were completed.

See [verification](../../docs/VALIDATION.md) and [rights](../../RIGHTS.md).
