"""Local electricity-only inventory candidate and future build checks."""
import csv
import hashlib
import io
import json
from math import isclose
from pathlib import Path

from prepare_native_replay import HERE, PROJECT


SOURCE = PROJECT/'validation_rebuild_20261019/energy_inputs_01/runs/electricity_avg/avg_yield__normal/data/genco_data_avg_yield__normal.csv'
SOURCE_SHA = '97e9f6c5eb3a549ea72a2433d61097c95bb4174d80c12b158df823f818d1a229'
UNIT = ('NE_Krsko', 'SI1', 'SI')
NET_MW = 696.
HR_SHARE_MW = 348.


def near(a, b):
    return isclose(float(a), b, rel_tol=0, abs_tol=1e-7)


def electricity_candidate(raw):
    if hashlib.sha256(raw).hexdigest() != SOURCE_SHA:
        raise ValueError('Wrong frozen electricity generator inventory')
    reader = csv.DictReader(io.StringIO(raw.decode('utf-8-sig')))
    rows = list(reader)
    matches = [r for r in rows if r['unit_name'] == UNIT[0]]
    if (len(matches) != 1 or (matches[0]['Zone'], matches[0]['System']) != UNIT[1:]
            or matches[0]['fuel_type'] != 'Nuclear'
            or any(r['fuel_type'].casefold() == 'nuclear' and r['System'].upper() == 'HR' for r in rows)
            or any(not near(matches[0][field],700) for field in ('cap_mw','max_cap'))):
        raise ValueError('Unexpected Krsko generator mapping or source rating')
    matches[0].update(cap_mw='696.0', max_cap='696.0')
    out = io.StringIO(newline='')
    writer = csv.DictWriter(out, fieldnames=reader.fieldnames, lineterminator='\n')
    writer.writeheader()
    writer.writerows(rows)
    return out.getvalue().encode('utf-8')


def audit_built_limits(ctx, model, years, *, row_labels=None):
    """Verify resolved dictionaries and the two distinct built constraints."""
    if not near(ctx['capacity_generator'][UNIT], NET_MW) or not near(
            ctx['capacity_generator_max'][UNIT], NET_MW):
        raise ValueError('Expected 696 MW existing and total SI1 capacity')
    if any(u.casefold().replace('_','').startswith(('nekrsko','krkso')) and s.upper() == 'HR'
           for u, z, s in ctx['capacity_generator']):
        raise ValueError('Duplicate Croatian Krsko generation')

    def row_terms(expr):
        terms = {}
        for i in range(expr.size()):
            idx = expr.getVar(i).index
            terms[idx] = terms.get(idx, 0.) + expr.getCoeff(i)
        return {k:v for k,v in terms.items() if not near(v,0)}

    def locate(family, key, var):
        if row_labels is None:
            return ctx[family][key]
        column = model.getCol(var)
        rows = [column.getConstr(i) for i in range(column.size())]
        matches = [row for row in rows if row.ConstrName.startswith(row_labels[family] + '[')]
        if len(matches) != 1:
            raise ValueError(f'Expected one distinctly labelled {family} row for {key}')
        return matches[0]

    for y in years:
        if not near(ctx['cap_inv_lim_long_lim'][UNIT[0],y,UNIT[1]], 0):
            raise ValueError(f'Nonzero nuclear investment allowance in {y}')
        key = (*UNIT,y)
        var = ctx['cap_inv'][key]
        add = locate('max_inv_unit', key, var)
        if (add.Sense != '<' or not near(add.RHS,0)
                or row_terms(model.getRow(add)) != {var.index:1.}):
            raise ValueError(f'Wrong built nuclear new-capacity constraint in {y}')
        total = locate('max_generator_capacity', key, var)
        expr = ctx['available_capacity_year'][key]
        if (total.Sense != '<' or not near(total.RHS, NET_MW - expr.getConstant())
                or row_terms(model.getRow(total)) != row_terms(expr)):
            raise ValueError(f'Wrong built nuclear total-capacity constraint in {y}')


def main():
    candidate = electricity_candidate(SOURCE.read_bytes())
    out = HERE/'electricity_krsko_candidate_preflight'
    out.mkdir(exist_ok=False)
    (out/'diagnostic_genco_electricity.csv').write_bytes(candidate)
    manifest = {
        'source_sha256': SOURCE_SHA,
        'candidate_sha256': hashlib.sha256(candidate).hexdigest(),
        'unit': UNIT,
        'physical_net_mw': NET_MW,
        'croatian_share_through_exchange_mw': HR_SHARE_MW,
        'croatian_nuclear_generator_added': False,
        'required_new_build_limit_mw_per_anchor': 0,
        'investment_limit_applied': False,
        'zero_exchange_expected': 'Zero gross physical HR imports and exports at each hour.',
        'retirement_overlay_applied': False,
        'built_model_verified': False,
        'planning_approved': False,
    }
    (out/'preflight.json').write_text(json.dumps(manifest, indent=2)+'\n', encoding='utf-8')
    print(json.dumps(manifest, indent=2))


if __name__ == '__main__':
    main()
