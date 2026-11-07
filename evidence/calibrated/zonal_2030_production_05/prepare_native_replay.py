"""Build and check the isolated 2020 replay ledger without running a solver."""
import csv
import hashlib
import io
import json
from collections import defaultdict
from datetime import datetime, timedelta, timezone
from math import fsum, isfinite
from pathlib import Path
import zipfile

HERE = Path(__file__).resolve().parent
PROJECT = HERE.parent
ARCHIVE_SHA = '2b564a9e9d2d7698d65ed4581dfbcb81b90e504757319dd09da0d2110cbf813f'
NATIVE_GENCO = '7c7e52eca5888c666bddc9fb19f8f232cb33c61aaf8f297d1190991544c3d34d'
TOL = 1e-7


def sha(data):
    return hashlib.sha256(data).hexdigest()


def csv_rows(data):
    return list(csv.DictReader(io.StringIO(data.decode('utf-8-sig'))))


def verified_inputs(path):
    raw = path.read_bytes()
    if sha(raw) != ARCHIVE_SHA:
        raise ValueError('Unexpected V3 archive hash')
    with zipfile.ZipFile(io.BytesIO(raw)) as z:
        manifest = json.loads(z.read('manifest.json'))
        if (len(manifest) != 13 or z.testzip() is not None
                or len(z.namelist()) != len(manifest) + 1
                or set(z.namelist()) != set(manifest) | {'manifest.json'}):
            raise ValueError('Invalid V3 archive or manifest')
        files = {name: z.read(name) for name in manifest}
    if any(sha(data) != manifest[name] for name, data in files.items()):
        raise ValueError('V3 member hash mismatch')
    return files


def inventory_plan(crosswalk, old):
    native = {r['unit_name']: r for r in old}
    if len(native) != len(old):
        raise ValueError('Duplicate native generator IDs')
    seen, plan = set(), []
    for entry in crosswalk:
        key = entry['native_id']
        target = float(entry['target_capacity_mw'])
        if not isfinite(target) or target < 0:
            raise ValueError('Invalid existing capacity')
        if key:
            if key in seen or key not in native:
                raise ValueError('Duplicate or unknown mapped native ID: ' + key)
            seen.add(key)
            if abs(float(entry['legacy_capacity_mw']) - float(native[key]['cap_mw'])) > TOL:
                raise ValueError('Legacy capacity mismatch: ' + key)
        action = 'configure_existing_stock' if key else 'needs_existing_stock_unit_parameters'
        if key and target == 0:
            action = 'zero_historical_stock'
        if entry['technology'] == 'hydro_reservoir' and key and native[key]['technology'] == 'HPHS':
            action = 'reclassify_existing_pumped_to_reservoir'
        plan.append({**entry, 'action': action, 'historical_investment_mw': 0.0})
    if seen != set(native):
        raise ValueError('Unreconciled legacy generator IDs')
    domestic = fsum(float(r['target_capacity_mw']) for r in plan if r['technology'] != 'krsko_hr')
    if abs(domestic - 4661.8) > TOL:
        raise ValueError('Domestic capacity does not reconcile to EIHP')
    return plan


def replay_ledger(hourly, hydro, stocks, capacities):
    if len(hourly) != 8760 or len(hydro) != 8760:
        raise ValueError('Expected two 8760-hour series')
    start = datetime(2020, 1, 1, tzinfo=timezone.utc)
    calendar = [t for t in (start + timedelta(hours=i) for i in range(8784))
                if (t.month, t.day) != (2, 29)]
    prior = {k: stocks[k]['initial_and_terminal_mwh'] for k in ('reservoir', 'pumped')}
    max_stock_error, max_load_error = 0., 0.
    geothermal_excess = []
    ledger = []
    for period, (r, w, stamp) in enumerate(zip(hourly, hydro, calendar), 1):
        if (int(r['period']) != period or int(w['period']) != period or
                datetime.fromisoformat(r['time_utc']) != stamp or r['time_utc'] != w['time_utc']):
            raise ValueError('Mismatched historical calendar')
        values = {k: float(v) for k, v in r.items() if k not in ('time_utc', 'period')}
        if not all(isfinite(v) for v in values.values()):
            raise ValueError('Non-finite historical input')
        v = values
        pump = v['pumping_grid_mwh']
        if not 0 <= pump <= 246.9 + TOL:
            raise ValueError('Historical pump rating exceeded')
        for group in ('reservoir', 'pumped'):
            storage = float(w[group + '_stock_proxy_mwh'])
            natural = float(w[group + '_natural_inflow_proxy_mwh'])
            if natural < -TOL or not 0 <= storage <= stocks[group]['storage_proxy_mwh'] + TOL:
                raise ValueError('Hydro stock or natural inflow outside bounds')
            pumped_water = 0.75 * pump if group == 'pumped' else 0.
            expected = prior[group] + natural + pumped_water - v['hydro_' + group + '_mwh'] / 0.9
            max_stock_error = max(max_stock_error, abs(storage - expected))
            prior[group] = storage
        ev_charge, driving = v['ev_grid_mwh'], v['ev_driving_mwh']
        cooling_grid, cooling_service = v['cooling_grid_mwh'], v['cooling_delivered_mwh']
        if abs(0.9 * ev_charge - driving) > TOL or v['ev_v2g_mwh'] != 0.:
            raise ValueError('EV charge/drive or V2G mismatch')
        if abs(cooling_service / 3 - cooling_grid) > TOL:
            raise ValueError('Residential cooling COP mismatch')
        max_load_error = max(max_load_error, abs(v['gross_load_target_mwh'] -
                             v['other_load_mwh'] - pump - cooling_grid - ev_charge))
        for group, capacity in capacities.items():
            if group == 'legacy_only':
                continue
            key = 'geothermal_net_observed_mwh' if group == 'geothermal' else group + '_mwh'
            if group == 'geothermal' and v[key] > capacity + TOL:
                geothermal_excess.append(v[key] - capacity)
                continue
            if not -TOL <= v[key] <= capacity + TOL:
                raise ValueError(f'Historical {group} dispatch exceeds available capacity at {period}')
        trade = v['other_trade_net_proxy_mwh']
        ledger.append({
            'time_utc': r['time_utc'], 'period': period,
            'existing_household_cooling_grid_mwh': cooling_grid,
            'existing_household_cooling_service_mwh': cooling_service,
            'ev_grid_charge_mwh': ev_charge, 'ev_drive_mwh': driving, 'ev_grid_discharge_mwh': 0.,
            'pumped_hydro_grid_mwh': pump, 'pumped_hydro_water_input_mwh': .75 * pump,
            'pumped_hydro_natural_inflow_mwh': float(w['pumped_natural_inflow_proxy_mwh']),
            'reservoir_natural_inflow_mwh': float(w['reservoir_natural_inflow_proxy_mwh']),
            'other_load_including_unallocated_electric_heat_mwh': v['other_load_mwh'],
            'geothermal_net_dispatch_reference_mwh': v['geothermal_net_observed_mwh'],
            'geothermal_gross_accounting_mwh': v['geothermal_mwh'],
            'krsko_existing_share_reference_mwh': v['krsko_hr_mwh'],
            'other_import_proxy_mwh': max(trade, 0.), 'other_export_proxy_mwh': max(-trade, 0.),
            'other_trade_balancing_residual_mwh': v['other_trade_net_balancing_mwh'],
        })
    if max(max_stock_error, max_load_error) > TOL:
        raise ValueError('Historical stock or electric load does not close')
    if any(abs(prior[k] - stocks[k]['initial_and_terminal_mwh']) > TOL for k in prior):
        raise ValueError('Non-cyclic terminal hydro stock')
    return ledger, {'max_hourly_hydro_balance_mwh': max_stock_error,
                    'max_hourly_load_balance_mwh': max_load_error,
                    'geothermal_net_hours_above_reported_capacity': len(geothermal_excess),
                    'geothermal_net_max_excess_mw': max(geothermal_excess, default=0.),
                    'geothermal_net_total_excess_mwh': fsum(geothermal_excess)}


def main():
    files = verified_inputs(HERE / 'calibrated_inputs_v3.zip')
    old = PROJECT / 'H2RES_SingleZone_Myopic_v1.2/data/SC/genco_data_HR_sdewes.csv'
    raw_old = old.read_bytes()
    if sha(raw_old) != NATIVE_GENCO:
        raise ValueError('Unexpected accepted generator table')
    plan = inventory_plan(csv_rows(files['calibrated/h2res_capacity_crosswalk_2020.csv']), csv_rows(raw_old))
    capacities = defaultdict(float)
    for row in plan:
        capacities[row['technology']] += float(row['target_capacity_mw'])
    hourly = csv_rows(files['calibrated/national_hourly_2020.csv'])
    hydro = csv_rows(files['calibrated/hydro_historical_inflow_fit_2020.csv'])
    stocks = json.loads(files['calibrated/hydro_historical_inflow_fit_2020.json'])['diagnostics']
    ledger, residuals = replay_ledger(hourly, hydro, stocks, capacities)
    out = HERE / 'native_replay_preflight'
    out.mkdir(exist_ok=True)
    for name, data in [('inventory_actions.csv', plan), ('historical_replay_ledger.csv', ledger)]:
        with (out / name).open('w', newline='', encoding='utf-8') as f:
            writer = csv.DictWriter(f, fieldnames=list(data[0]))
            writer.writeheader()
            writer.writerows(data)
    report = dict(status='reference_ledger_prepared_native_launch_blocked', source_archive_sha256=ARCHIVE_SHA,
                  source_genco_sha256=NATIVE_GENCO, hours=len(ledger),
                  missing_native_unit_parameter_rows=sum(not r['native_id'] for r in plan),
                  historic_investment_target_mw=0., capacity_by_technology_mw=dict(capacities),
                  checks=residuals, geothermal_net_used_for_capacity_check=True,
                  geothermal_gross_above_net_capacity_hours=sum(float(r['geothermal_mwh']) > 10 + TOL for r in hourly),
                  solver_started=False, annual_future_inputs_approved=False,
                  unresolved=[
                      'Reconcile 16 metered geothermal hours at 10.1 MW with the 10 MW available rating; preserve observations without using them as hard dispatch bounds.',
                      'Complete existing-unit operational and retirement parameters and map fitted aggregate water inflows to native storage units.',
                      'Allocate separately modelled historical electric heating out of other load; keep fixed cooling and pumping distinct from optimized heat and curtailment.',
                      'Implement and build-check native export, pumping and EV replay constraints in a new solver project.',
                      'Freeze future cooling and EV assumptions and verify the resulting stock and no double counting before launching planning paths.',
                  ])
    (out / 'preflight.json').write_text(json.dumps(report, indent=2) + '\n', encoding='utf-8')
    print(json.dumps(report, indent=2))


if __name__ == '__main__':
    main()
