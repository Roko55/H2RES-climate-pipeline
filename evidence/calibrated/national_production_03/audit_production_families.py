"""Verify that every declared production asset enters the native supply once."""
from collections import Counter, defaultdict
from math import fsum, isfinite


HOURS = tuple(range(1,8761))
TOL = 1e-7
DIRECT_FAMILIES = ('fossil_units','hphs_units','hdam_units','hror_units','nondisp_units')
TECHNOLOGY_GROUP = {
    'HDAM': ('reservoir_hydro','hdam_units'),
    'HPHS': ('pumped_hydro','hphs_units'),
    'HROR': ('run_of_river','hror_units'),
    'WTON': ('wind','nondisp_units'),
    'PHOT': ('solar','nondisp_units'),
}


def supply_inventory(rows):
    inventory = {}
    for row in rows:
        unit = row['unit_name']
        tech = row['technology']
        if tech == 'STUR':
            group, family = row['fuel_type'].lower(), 'fossil_units'
        else:
            group, family = TECHNOLOGY_GROUP[tech]
        mw = float(row['cap_mw'])
        if unit in inventory or not group or not isfinite(mw) or mw < 0.:
            raise ValueError('Invalid production generation inventory: '+unit)
        inventory[unit] = dict(initial_mw=mw,group=group,balance_family=family)
    if not inventory:
        raise ValueError('Empty production generation inventory')
    return inventory


def audit_built_supply(ctx, model, expected_inventory):
    """Compare native generation rows with an independently approved inventory."""
    years = tuple(ctx['years'])
    if (not years or len(set(years)) != len(years) or tuple(ctx['periods']) != HOURS
            or not expected_inventory or set(expected_inventory) != set(ctx['units'])):
        raise ValueError('Approved physical inventory does not match the model scope')
    memberships = defaultdict(list)
    for family in DIRECT_FAMILIES:
        counts = Counter(ctx[family])
        if any(n != 1 for n in counts.values()):
            raise ValueError('Duplicate generator in the electricity supply family '+family)
        for unit in counts:
            if unit not in expected_inventory:
                raise ValueError('Unexpected unit in native electricity supply: '+unit)
            memberships[unit].append(family)
    capacities = defaultdict(list)
    for unit,item in expected_inventory.items():
        capacity = item['initial_mw']
        group = item['group']
        if (not isfinite(capacity) or capacity < 0 or not group
                or memberships[unit] != [item['balance_family']]):
            raise ValueError(f'Asset is missing or counted twice in native electric supply: {unit}')
        if not isfinite(ctx['max_load'][unit]) or abs(ctx['max_load'][unit]-capacity) > TOL:
            raise ValueError('Unexpected loaded nameplate for '+unit)
        capacities[group].append(capacity)

    model.update()
    for year in years:
        for hour in HOURS:
            balance = ctx['meet_demand'][hour,year]
            if balance.Sense != '=':
                raise ValueError(f'Native supply balance is not an equality at {hour}/{year}')
            for unit in expected_inventory:
                variable = ctx['generation'][unit,hour,year]
                if model.getCoeff(balance,variable) != 1.:
                    raise ValueError(f'Wrong electricity supply coefficient for {unit}/{hour}/{year}')
    return dict(status='built_supply_membership_passed',years=list(years),hours=len(HOURS),
                units=len(expected_inventory),initial_capacity_by_group_mw={
                    group:fsum(values) for group,values in sorted(capacities.items())})
