"""Independent audit, channel comparisons and compact results package."""
from pathlib import Path
import ast
import csv
import gzip
import json
import os
import sys
import zipfile
from common import R, OLD, YEARS, CASES, load, save, sha, quality, accepted, remote_only, now


def folder(case, year):
    if case == 'average' and load(R / 'reference_decision.json')['reuse']:
        return OLD / 'multisector/jobs/avg_path' / str(year)
    return R / 'jobs' / case / str(year)


def write_csv(name, rows):
    assert rows, name
    target = R / 'delivery' / name
    with target.open('w', encoding='utf8', newline='') as f:
        writer = csv.DictWriter(f, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)


def independent_audit():
    source = (R / 'sources/post_audit.py').read_text(encoding='utf8')
    source = source.replace("P=R/'project'", "P=R/'projects/average'")
    source = source.replace('E:/Roko/remote_access/final_frozen_profiles_20261031/multisector', R.as_posix())
    tree = ast.parse(source)
    for node in ast.walk(tree):
        if isinstance(node, ast.For) and isinstance(node.target, ast.Name) and node.target.id == 'case':
            node.iter = ast.parse(repr(CASES), mode='eval').body
        if isinstance(node, ast.Assign) and any(isinstance(n, ast.Name) and n.id == 'folder' for n in node.targets):
            node.value = ast.parse("(OLD/'multisector/jobs/avg_path') if case=='average' and REUSE else (R/'jobs'/case)", mode='eval').body
        if isinstance(node, ast.FunctionDef) and node.name == 'write':
            node.body = ast.parse('save(R/name,v)').body
    tree.body.insert(0, ast.parse('from common import OLD, save\nREUSE=' +
                     repr(load(R / 'reference_decision.json')['reuse'])).body[0])
    tree.body.insert(1, ast.parse('REUSE=' + repr(load(R / 'reference_decision.json')['reuse'])).body[0])
    module = ast.fix_missing_locations(tree)
    namespace = {'__name__': 'independent_stock_audit', '__file__': str(R / 'post_audit_generated.py')}
    exec(compile(module, 'independent_post_audit', 'exec'), namespace)
    os.chdir(R)
    report = load(R / 'post_audit.json')
    assert all(report[k] for k in ('stock_all_passed', 'emissions_all_passed', 'balances_all_passed'))
    assert len(report['balance_checks']) == 49
    return report


def collect():
    remote_only()
    (R / 'delivery').mkdir(exist_ok=True)
    decision = load(R / 'reference_decision.json')
    reuse = decision['reuse']
    objectives, components, investments, outcomes, thermal = [], [], [], [], []
    proofs = []
    for case in CASES:
        for year in YEARS:
            out = folder(case, year)
            if not (case == 'average' and reuse):
                assert accepted(case, year)
            marker = load(out / 'accepted.json')
            for name, expected in marker['files'].items():
                assert sha(out / name) == expected
            result = load(out / 'solver_result.json')
            assert quality(result)
            ledger = load(out / 'annual_objective_ledger.json')
            gap = sum(ledger['values_by_anchor'].values()) + ledger['constant'] + ledger['unassigned'] - result['objective']
            assert abs(gap) < .01
            rows = list(csv.DictReader((out / 'objective_breakdown_outputs.csv').open(encoding='utf8')))
            component_sum = sum(float(r['value']) for r in rows if int(r['active_in_objective']) == 1)
            assert abs(component_sum - result['objective']) < .01
            objectives.append({'case': case, 'year': year, 'objective': result['objective'],
                               'constraint_violation': result['constraint_violation'],
                               'bound_violation': result['bound_violation'],
                               'window_years': json.dumps(result['window_years']),
                               'source': str(out), 'source_sha256': sha(out / 'solver_result.json')})
            for row in rows:
                components.append(dict(case=case, window_start=year, **row))
            term_path = R / 'reference' / str(year) / 'investment_terms.csv' if case == 'average' and reuse else out / 'investment_terms.csv'
            for row in csv.DictReader(term_path.open(encoding='utf8')):
                if int(row['realized']):
                    investments.append(dict(case=case, **row))
            for label, value in result['annual_sums'].items():
                family, key = label.split('|', 1)
                keys = json.loads(key)
                if keys[-1] == year:
                    outcomes.append({'case': case, 'year': year, 'family': family,
                                     'key_json': key, 'value': value,
                                     'unit': 'tCO2' if family.startswith('CO2_') else 'MWh'})
            sums = {}
            with gzip.open(out / 'runtime_accounting_inputs.csv.gz', 'rt', encoding='utf8') as f:
                for row in csv.DictReader(f):
                    key = json.loads(row['key_json'])
                    if key[-1] == year and row['family'] in ('heat_demand', 'cooling_demand', 'HP_COP_period'):
                        group = (row['family'], key[0])
                        sums.setdefault(group, []).append(float(row['value']))
            for (family, unit), values in sums.items():
                thermal.append({'case': case, 'year': year, 'family': family, 'unit_name': unit,
                                'sum': sum(values), 'mean': sum(values)/len(values),
                                'min': min(values), 'max': max(values), 'hours': len(values),
                                'value_unit': 'dimensionless' if family == 'HP_COP_period' else 'MWh'})
            proofs.append({'case': case, 'year': year, 'accepted_sha256': sha(out / 'accepted.json'),
                           'terms_sha256': sha(term_path), 'reused': case == 'average' and reuse})
    ref = {r['year']: r['objective'] for r in objectives if r['case'] == 'average'}
    for row in objectives:
        row['delta_vs_average'] = row['objective'] - ref[row['year']]
        row['percent_vs_absolute_average'] = 100 * row['delta_vs_average'] / abs(ref[row['year']]) if ref[row['year']] else None
    report = independent_audit()
    stock = report['installed_capacity']
    previous = {}
    for row in stock:
        key = row['case'], row['family'], json.dumps(row['key'][:-1])
        prior = previous.get(key)
        row['native_retirements_since_previous_anchor'] = (prior + row['new_addition'] - row['installed_native_capacity']) if prior is not None else None
        previous[key] = row['installed_native_capacity']
        row['unit'] = 'MWh' if row['family'] in ('stat_sto_cap_inv', 'h2_sto_max_cap') else 'MW'
    write_csv('objective_comparison.csv', objectives)
    write_csv('objective_components.csv', components)
    write_csv('investments_by_technology.csv', investments)
    write_csv('installed_stock.csv', stock)
    write_csv('system_outcomes.csv', outcomes)
    write_csv('thermal_inputs.csv', thermal)
    changes = []
    reference_terms = {(r['family'], r['key_json']): r for r in investments if r['case'] == 'average'}
    for row in investments:
        base = reference_terms[row['family'], row['key_json']]
        changes.append({'case': row['case'], 'year': row['decision_year'], 'family': row['family'],
                        'key_json': row['key_json'], 'unit': row['unit'],
                        'capacity_addition': row['capacity_addition'],
                        'capacity_delta_vs_average': float(row['value']) - float(base['value']),
                        'investment_objective_delta': float(row['objective_contribution']) - float(base['objective_contribution'])})
    write_csv('channel_comparison.csv', changes)
    # Physical capacity and storage energy are kept on separate axes/files.
    import matplotlib
    matplotlib.use('Agg')
    import matplotlib.pyplot as plt
    for unit in ('MW', 'MWh'):
        fig, axes = plt.subplots(1, 3, figsize=(13.5, 4.4), constrained_layout=True)
        labels = [('solar', 'Solar availability', ['solar_low', 'solar_high']),
                  ('wind', 'Wind availability', ['wind_low', 'wind_high']),
                  ('temperature', 'Temperature', ['temperature_cold', 'temperature_hot'])]
        for ax, (_, title, cases) in zip(axes, labels):
            ax.axhline(0, color='#555555', lw=.8)
            for case, color, label in zip(cases, ['#167D9A', '#D47A39'],
                                          ['Cold' if title == 'Temperature' else 'Low',
                                           'Hot' if title == 'Temperature' else 'High']):
                values = [sum(r['capacity_delta_vs_average'] for r in changes if r['case'] == case
                              and int(r['year']) == y and r['unit'] == unit and int(r['capacity_addition'])) for y in YEARS]
                ax.plot(YEARS, values, marker='o', ms=4, lw=1.8, color=color, label=label)
            ax.set_title(title, fontsize=11)
            ax.set_xlabel('Planning year')
            ax.set_xticks(YEARS[::2])
            ax.spines[['top', 'right']].set_visible(False)
            ax.grid(axis='y', alpha=.18)
            ax.legend(frameon=False)
        axes[0].set_ylabel('New capacity minus Average (' + unit + ')')
        fig.savefig(R / 'delivery' / ('investment_changes_' + unit + '.png'), dpi=220)
        fig.savefig(R / 'delivery' / ('investment_changes_' + unit + '.pdf'))
        plt.close(fig)
    save(R / 'delivery/interpretation.json', {'scope': 'Same full multisector model; one climate channel changed at a time',
         'common_reference': 'average', 'reused_average': reuse, 'planning_years': YEARS,
         'temperature_application': 'Native hourly heat/cooling/COP adapter; annual-total scaling flag remains false',
         'cost_scope': 'Native per-window objective and realized investment objective contributions; overlapping objectives are not a transition-cost total',
         'trade_scope': 'Imports endogenous; do not call input-side exports optimized export revenue',
         'comparison_scope': 'Responses to the selected input ranges, not a universal ranking of channel importance',
         'figures': 'Physical MW and storage MWh separated; technology-level values retained in CSV'})
    save(R / 'final_integrity.json', {'utc': now(), 'status': 'completed_independently_verified',
         'planning_windows': 49, 'new_planning_windows': 42 if reuse else 49,
         'reused_windows': 7 if reuse else 0, 'optimal': 49, 'proofs': proofs,
         'stock_checks': len(report['stock_checks']), 'emission_checks': len(report['emission_checks']),
         'balance_checks': len(report['balance_checks']), 'all_passed': True,
         'manuscript_updated': False, 'new_historical_stress_tests': 0})
    members = [p for p in (R / 'delivery').glob('*') if p.is_file()]
    members += [R / n for n in ('preparation.json', 'plan.json', 'mapping_checks.json', 'scenario_inputs.csv',
                               'reference_decision.json', 'post_audit.json', 'final_integrity.json')]
    manifest = {p.relative_to(R).as_posix(): sha(p) for p in members}
    save(R / 'package_manifest.json', manifest)
    with zipfile.ZipFile(R / 'results.zip', 'w', zipfile.ZIP_DEFLATED) as z:
        for p in members + [R / 'package_manifest.json']:
            z.write(p, p.relative_to(R).as_posix())
    save(R / 'delivery_ready.json', {'utc': now(), 'archive_sha256': sha(R / 'results.zip'),
                                   'members': len(members)+1, 'archive_bytes': (R / 'results.zip').stat().st_size})


if __name__ == '__main__':
    collect()
