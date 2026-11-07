"""Audit the 2020 capital seed and every subsequent native window handoff."""
from itertools import product
from math import fsum, isclose, isfinite
from hashlib import sha256
import json

from seed_planning_investments import CAPITAL_FAMILIES, HORIZON, seed_no_build_investments


RETIREMENT = {
    'cap_inv': ('decom_rate', 'decom_start_new'),
    'heat_pump_ind_cap': ('HP_decom_rate', 'HP_decom_start_new'),
    'heat_pump_dh_cap': ('HP_decom_rate', 'HP_decom_start_new'),
    'trad_boiler_ind_cap_inv': ('Boiler_decom_rate', 'Boiler_decom_start_new'),
    'trad_boiler_dh_cap_inv': ('Boiler_decom_rate', 'Boiler_decom_start_new'),
    'stat_sto_cap_inv': ('Sta_sto_decom_rate', 'Sta_sto_decom_start_new'),
}
TOL = 1e-7
STATE_FAMILIES = {name for family in CAPITAL_FAMILIES
                  for name in ('values_' + family, family + '_cumulative')}


def branch_snapshot(context, base_result):
    """Create a first-window snapshot only from the accepted no-build base."""
    return dict(state=seed_no_build_investments(context, base_result), hor_yrs=list(HORIZON),
                next_index=1)


def resume_accepted_windows(job, case, fence, base_digest, read_snapshot):
    """Return the next native index and latest checked snapshot for one path."""
    if (not fence or len(base_digest) != 64
            or any(c not in '0123456789abcdef' for c in base_digest)):
        raise ValueError('Expected a frozen historical base and execution fence')
    if (job/'2020/accepted.json').exists():
        raise ValueError('A fresh production path must use the common historical base')
    previous = None
    for i, year in enumerate(HORIZON[1:], 1):
        directory = job/str(year)
        marker = directory/'accepted.json'
        if not marker.is_file():
            if any((job/str(y)/'accepted.json').exists() for y in HORIZON[i+1:]):
                raise ValueError('Accepted planning windows have a gap')
            return i, previous
        record = json.loads(marker.read_bytes())
        if (record['year'], record['case'], record['fence'], record['historical_base_sha256']) != (
                year, case, fence, base_digest):
            raise ValueError('Accepted planning window belongs to another path or base')
        inherited_file = f'inherited_{year}.pkl'
        required = {'solver_result.json', 'after_window.pkl', inherited_file}
        if not required <= record['files'].keys():
            raise ValueError('Accepted window lacks solver or handoff evidence')
        for relative, expected in record['files'].items():
            path = (directory/relative).resolve()
            if not path.is_relative_to(directory.resolve()):
                raise ValueError('Planning evidence path leaves its window')
            digest = sha256()
            with path.open('rb') as stream:
                for block in iter(lambda: stream.read(1024 * 1024), b''):
                    digest.update(block)
            if digest.hexdigest() != expected:
                raise ValueError(f'Changed accepted planning evidence {relative}')
        result = json.loads((directory/'solver_result.json').read_bytes())
        if (result['status'] != 2 or result['window_years'] != list(HORIZON[i:i+2])
                or not all(0 <= result[k] <= TOL for k in ('constraint_violation', 'bound_violation'))):
            raise ValueError('Accepted planning window lacks a strict optimal result')
        inherited = read_snapshot(directory/inherited_file)['state']
        if previous is None:
            if (inherited.keys() != STATE_FAMILIES
                    or any(v != 0. for group in inherited.values() for v in group.values())):
                raise ValueError('First window inherited nonzero or incomplete capital')
        elif inherited != previous['state']:
            raise ValueError('Planning window inherited a different previous state')
        previous = read_snapshot(directory/'after_window.pkl')
        if (previous['hor_yrs'] != list(HORIZON) or previous['next_index'] != i+1
                or previous['case'] != case or previous['fence'] != fence
                or previous['historical_base_sha256'] != base_digest
                or previous['state'].keys() != STATE_FAMILIES):
            raise ValueError('Accepted planning checkpoint has an unexpected identity')
    return len(HORIZON), previous


def audit_capital_handoff(context, before, after):
    """Recompute stock aging from actually realized native investments.

    The native two-year window stores both optimized decisions. Only an
    anchor strictly before the stock date may contribute to its inherited
    total. A previous look-ahead for the current anchor may be replaced.
    """
    i = context['i']
    if (tuple(context['hor_yrs']) != HORIZON or context['window'] != 2 or not 1 <= i < len(HORIZON)
            or tuple(context['years']) != HORIZON[i:i+2]):
        raise ValueError('Unexpected calibrated rolling window')
    if before.keys() != STATE_FAMILIES or after.keys() != STATE_FAMILIES:
        raise ValueError('Capital handoff is missing or adding a native family')
    prior_years = HORIZON[:1 if i == 1 else i+1]
    realized_years = HORIZON[:i+len(context['years'])]
    touched = HORIZON[i+1:i+3]
    report = {}
    for family, dims in CAPITAL_FAMILIES.items():
        keys = set(product(*(context[d] for d in dims)))
        old_values = before['values_' + family]
        new_values = after['values_' + family]
        old_stock = before[family + '_cumulative']
        new_stock = after[family + '_cumulative']
        if (old_values.keys() != {(*k, y) for k, y in product(keys, prior_years)}
                or new_values.keys() != {(*k, y) for k, y in product(keys, realized_years)}
                or old_stock.keys() != {(*k, y) for k, y in product(keys, HORIZON)}
                or new_stock.keys() != old_stock.keys()):
            raise ValueError(f'Unexpected state keys for {family}')
        if any(not isfinite(v) or v < -TOL for group in (old_values, new_values, old_stock, new_stock)
               for v in group.values()):
            raise ValueError(f'Invalid capacity in {family}')
        gap = 0.
        for key in keys:
            if old_values[(*key, 2020)] != 0. or new_values[(*key, 2020)] != 0.:
                raise ValueError(f'Historical additions changed for {family}/{key}')
            for y in HORIZON[:i]:
                if old_values[(*key, y)] != new_values[(*key, y)]:
                    raise ValueError(f'Realized decision overwritten for {family}/{key}/{y}')
            for y in context['years']:
                value = float(context[family][(*key, y)].X)
                if not isclose(new_values[(*key, y)], value, rel_tol=0., abs_tol=TOL):
                    raise ValueError(f'Solver decision missing from checkpoint for {family}/{key}/{y}')
            for y in HORIZON:
                actual = new_stock[(*key, y)]
                if y not in touched:
                    if actual != old_stock[(*key, y)]:
                        raise ValueError(f'Unexpected stock change for {family}/{key}/{y}')
                    continue
                contributions = []
                for vintage in (v for v in realized_years if v < y):
                    factor = 1.
                    if family in RETIREMENT:
                        rate_name, start_name = RETIREMENT[family]
                        rate = context[rate_name][key[0]]
                        start = context[start_name][key[0]] - 1
                        factor = round((1-rate)**(max(start, y-vintage)-start), 3)
                    contributions.append(new_values[(*key, vintage)] * factor)
                residual = abs(actual - fsum(contributions))
                if not isfinite(residual) or residual > TOL:
                    raise ValueError(f'Wrong surviving investment for {family}/{key}/{y}')
                gap = max(gap, residual)
        report[family] = dict(units=len(keys), max_surviving_capacity_residual=gap)
    return report
