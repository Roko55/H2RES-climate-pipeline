"""Apply a temperature-demand transformation to explicit model zones only."""
from collections.abc import Callable, Iterable, MutableMapping
from typing import Any


def reshape_demand_for_zones(
    demand: MutableMapping,
    zones: Iterable[str],
    transform: Callable[[MutableMapping], Any],
) -> Any:
    """Preserve all non-target values while transforming a separate subset."""
    target_zones = {str(zone) for zone in zones}
    if not target_zones:
        raise ValueError('Explicit target zones are required')
    subset = {key:value for key,value in demand.items()
              if isinstance(key,tuple) and key and str(key[0]) in target_zones}
    keys = set(subset)
    diagnostics = transform(subset)
    if set(subset) != keys:
        raise ValueError('Temperature transformation must preserve demand keys')
    demand.update(subset)
    return diagnostics
