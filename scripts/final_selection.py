"""Renewable-profile inputs underlying the V6 fixed 2030 electricity portfolios."""
from . import resource_selection
from .release_runtime import accepted_array


def select_cases(root, design, year):
    if design != 'electricity' or year != 2030:
        raise ValueError('The V6 operating portfolios use the electricity design at 2030')
    records = resource_selection.get_representative_scenarios(accepted_array(root).astype(float), year)
    return {name: records[name+'_yield__normal']['profile'].copy() for name in ('low', 'avg', 'high')}
