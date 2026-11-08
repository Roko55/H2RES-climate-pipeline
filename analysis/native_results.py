"""Portable extraction of the calibrated V6.1 receipts; no optimizer is imported.

National full variable/state stores were audited at their original location.
This module checks the delivered annual/asset records against those receipts.
The separate zonal auditor also rechecks every delivered electrical hour.
"""
from pathlib import Path
import hashlib
import json
import math
import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
YEARS = tuple(range(2025, 2051, 5))
CASES = ('average', 'solar_low', 'solar_high', 'wind_low', 'wind_high',
         'temperature_cold', 'temperature_hot')
MODEL_SHA = '4fc9605a52f29f956a5c5c28512436e365637ac7b2e6a6be256a89a7ebb9c9ee'


def read(path):
    return json.loads(path.read_bytes())


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def require(condition, message):
    if not condition:
        raise ValueError(message)


def evidence(root=ROOT):
    return root/'evidence/calibrated'


def national_job(root, case, year):
    return evidence(root)/'national_production_03/jobs'/case/str(year)


def annual_costs_v61(root=ROOT):
    """Realized first anchor only, removing that anchor's saved discount weight.

    The demand denominator follows the manuscript's native multisector service
    convention. Industry_HT is included once, not also inside heat_demand.
    It is not an electricity tariff or a five-year cost integral.
    """
    statistics = pd.read_csv(evidence(root)/'native_input_statistics.csv')
    output = []
    for case in CASES:
        for year in YEARS:
            folder = national_job(root, case, year)
            ledger = read(folder/'annual_objective_ledger.json')
            terms = pd.read_csv(folder/'investment_terms.csv')
            realized = terms[(terms.realized == 1) & (terms.decision_year == year)]
            weight = ledger['NPV'][str(year)]
            total = ledger['values_by_anchor'][str(year)]/weight
            capacity = realized.objective_contribution.sum()/weight
            selected = statistics[(statistics['case'] == case) &
                (statistics.window_start == year) & (statistics.year == year)]
            mask = (selected.family.isin(['demand', 'Industry_HT_demand', 'H2_demand', 'ev_Demand']) |
                (selected.family.eq('heat_demand') & selected.unit.isin(['general_demand', 'TE-TOZagreb'])) |
                selected.family.eq('cooling_demand'))
            demand = selected.loc[mask, 'sum'].sum()
            output.append(dict(case=case, year=year, discount_weight=weight,
                year_total_EUR=total, capacity_terms_EUR=capacity,
                other_active_terms_EUR=total-capacity, demand_MWh=demand,
                EUR_per_demand_MWh=total/demand))
    return pd.DataFrame(output)


def national_tables_v61(root=ROOT):
    """Extract native annual output families and realized asset decisions."""
    from scripts.channel_profiles import DONORS
    from scripts.release_runtime import accepted_array
    ensemble = accepted_array(root)
    objectives, components, investments, outcomes, inputs = [], [], [], [], []
    for case in CASES:
        donors = DONORS[case]
        profile = np.column_stack([ensemble[m, :, c] for c, m in enumerate(donors)])
        for year in YEARS:
            folder = national_job(root, case, year)
            solver = read(folder/'solver_result.json')
            objectives.append(dict(case=case, year=year, window_years=json.dumps(solver['window_years']),
                objective=solver['objective'], constraint_violation=solver['constraint_violation'],
                bound_violation=solver['bound_violation'], runtime_seconds=solver['runtime_seconds']))
            for name, target in [('objective_breakdown_outputs.csv', components),
                                 ('investment_terms.csv', investments)]:
                frame = pd.read_csv(folder/name)
                if name == 'objective_breakdown_outputs.csv':
                    frame.insert(0, 'window_start', year)
                if name == 'investment_terms.csv':
                    frame = frame[(frame.realized == 1) & (frame.decision_year == year)]
                frame.insert(0, 'case', case)
                target.append(frame)
            for key, value in solver['annual_sums'].items():
                family, indices = key.split('|', 1)
                if json.loads(indices)[-1] == year:
                    outcomes.append(dict(case=case, year=year, family=family, key_json=indices, value=value))
            inputs.append(dict(case=case, year=year, solar_member=donors[0], wind_member=donors[1],
                temperature_member=donors[2], profile_sha256=hashlib.sha256(profile.tobytes()).hexdigest(),
                mean_solar_CF=profile[:, 0].astype(float).mean(),
                mean_wind_CF=profile[:, 1].astype(float).mean(),
                mean_temperature_C=profile[:, 2].astype(float).mean(),
                heating_degree_hours_18C=np.maximum(18-profile[:, 2].astype(float), 0).sum(),
                cooling_degree_hours_22C=np.maximum(profile[:, 2].astype(float)-22, 0).sum()))
    objectives = pd.DataFrame(objectives)
    reference = objectives[objectives['case'] == 'average'].set_index('year').objective
    objectives['delta_vs_average'] = objectives.objective-objectives.year.map(reference)
    objectives['percent_vs_absolute_average'] = 100*objectives.delta_vs_average/objectives.year.map(reference).abs()
    return dict(scenario_inputs=pd.DataFrame(inputs), objective_comparison=objectives,
        objective_components=pd.concat(components, ignore_index=True),
        investments_by_technology=pd.concat(investments, ignore_index=True),
        system_outcomes=pd.DataFrame(outcomes),
        thermal_inputs=pd.read_csv(evidence(root)/'native_input_statistics.csv'))


def operational_table(root=ROOT):
    """45 fixed-portfolio checks, kept separate from national planning paths."""
    folder = evidence(root)/'zonal_2030_production_05'
    rows = []
    for marker in sorted((folder/'jobs').glob('operation_*/attempt_*/accepted.json')):
        data = read(marker)
        solver, hr, native = data['solver'], data['service']['HR'], data['croatia']
        rows.append(dict(portfolio=data['case'], historical_year=data['weather'],
            availability_fraction=data['fraction'], hours=data['hours'], status=data['status'],
            solver_status=solver['solver_status'], runtime_seconds=solver['runtime_seconds'],
            constraint_violation=solver['constraint_violation'], bound_violation=solver['bound_violation'],
            HR_unserved_MWh=hr['eens_mwh'], HR_unserved_hours=hr['eens_hours'],
            HR_CEEP_MWh=hr['ceep_mwh'], HR_CEEP_hours=hr['ceep_hours'],
            HR_import_MWh=native['gross_import_mwh'], HR_export_MWh=native['gross_export_mwh'],
            stationary_simultaneous_hours=native['stationary_simultaneous_hours'],
            portfolio_sha256=data['parent_portfolio_sha256'],
            verified_fixed_stock=data['max_fixed_stock_delta'] <= 1e-7,
            storage_terminal_cycle_active=data['storage_cycles'] > 0,
            max_storage_cycle_error=data['max_storage_cycle_error'],
            source=marker.relative_to(root).as_posix()))
    return pd.DataFrame(rows)


def verify_national_v61(root=ROOT):
    """Verify included bytes and locally check annual accounting and input identities."""
    base = evidence(root)
    inventory = read(base/'source_manifest.json')
    for name, record in inventory.items():
        path = (base/name).resolve()
        path.relative_to(base.resolve())
        require(path.stat().st_size == record['bytes'] and digest(path) == record['sha256'],
                'Native source mismatch: '+name)
    report = read(base/'publication_v61_20261107/national_audit.stdout.json')
    require(report['status'] == 'verified' and report['windows'] == 42 and report['cases'] == 7,
            'Original national audit failed')
    audit_index = {(r['case'], r['year']): r for r in report['evidence']}
    require(set(audit_index) == {(c, y) for c in CASES for y in YEARS}, 'Missing national audit evidence')
    statistics = read(base/'native_input_statistics_sources.json')
    require(statistics['status'] == 'verified' and statistics['windows'] == 42 and
        digest(base/'native_input_statistics.csv') == statistics['statistics_sha256'] and
        digest(base/'summarize_native_inputs_remote.py') == statistics['derivation_script_sha256'],
        'Native input statistics identity differs')
    for source in statistics['sources']:
        accepted_path = national_job(root, source['case'], source['window_start'])/'accepted.json'
        require(digest(accepted_path) == source['accepted_sha256'] and
            read(accepted_path)['files']['runtime_accounting_inputs.csv.gz'] == source['source_sha256'],
            'Input denominator source differs')
    tables = national_tables(root)
    for case in CASES:
        require(digest(base/'national_production_03/projects'/case/'scripts/Build_model_RH.py') == MODEL_SHA,
                'Executed national model differs')
        for year in YEARS:
            folder = national_job(root, case, year)
            accepted = read(folder/'accepted.json')
            require(digest(folder/'accepted.json') == audit_index[case, year]['accepted_sha256'],
                    'Accepted window differs from original full-store audit')
            # Omitted large state/variable files retain their original hashes.
            for name, sha in accepted['files'].items():
                path = folder/name
                if path.exists():
                    require(digest(path) == sha, 'Changed native result: '+str(path))
            solver = read(folder/'solver_result.json')
            ledger = read(folder/'annual_objective_ledger.json')
            require(solver['status'] == 2 and all(0 <= solver[k] <= 1e-7 for k in
                ('constraint_violation', 'bound_violation')), 'National solution acceptance failed')
            require(solver['window_years'] == ([year, year+5] if year < 2050 else [2050]),
                    'Native planning window differs')
            require(abs(math.fsum([*ledger['values_by_anchor'].values(), ledger['constant'],
                ledger['unassigned'], -solver['objective']])) < .01, 'National ledger does not close')
            components = pd.read_csv(folder/'objective_breakdown_outputs.csv')
            require(abs(components.loc[components.active_in_objective == 1, 'value'].sum()-solver['objective']) < .01,
                    'Native active objective components do not close')
            handoff = read(folder/'capital_handoff_audit.json')
            require(all(0 <= r['max_surviving_capacity_residual'] <= 1e-7 for r in handoff.values()),
                    'Capital handoff residual differs')
            pre = read(folder/'prebuild_validation.json')
            reference = read(national_job(root, 'average', year)/'prebuild_validation.json')
            require(pre['common_input_hashes'] == reference['common_input_hashes'] and
                pre['other_availability_sha256'] == reference['other_availability_sha256'],
                'Non-climate inputs differ across paths')
            if not case.startswith('temperature_'):
                require(pre['thermal_hashes'] == reference['thermal_hashes'], 'Unchanged thermal inputs differ')
            profile_hash = tables['scenario_inputs'].query('case == @case and year == @year').iloc[0].profile_sha256
            require(all(r['input_profile_sha256'] == profile_hash and
                set(r['max_errors']) == {'solar', 'wind'} and
                all(0 <= v <= 1e-12 for v in r['max_errors'].values()) for r in pre['mapping']),
                'Stationary input or spatial mapping differs')
    return dict(planning_windows=42, new_windows=42, common_calibrated_base_year=2020,
        input_profile_hashes=42, complete_state_handoffs_audited_on_server=42,
        native_annual_ledgers_verified=42, new_operational_tests=45,
        scope='Included annual/asset records verified locally; full national state stores audited at source')


def _thermal_snapshot(root):
    scope = read(root/'provenance/manuscript/scope.json')
    return (scope.get('result_cohort') == 'national_production_thermal_20261107_01'
            or scope['manuscript_version'] == 'V6.2')

def annual_costs(root=ROOT):
    if _thermal_snapshot(root):
        from analysis.thermal_results import annual_costs as current
        return current(root)
    return annual_costs_v61(root)

def national_tables(root=ROOT):
    if _thermal_snapshot(root):
        from analysis.thermal_results import national_tables as current
        return current(root)
    return national_tables_v61(root)

def verify_national(root=ROOT):
    if _thermal_snapshot(root):
        from analysis.thermal_results import verify_national as current
        return current(root)
    return verify_national_v61(root)
