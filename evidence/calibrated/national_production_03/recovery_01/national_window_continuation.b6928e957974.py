"""Keep the native input observer consistent across successive myopic windows."""

import ast


def model_local_names(source):
    tree = ast.parse(source)
    derived = next(node for node in tree.body if isinstance(node, ast.Assign)
                   and any(isinstance(t, ast.Name) and t.id == 'Fossil_hydro' for t in node.targets))
    if ast.unparse(derived.value) != 'list(dict.fromkeys(disp_units + hydro_units))':
        raise ValueError('The native hydro CAPEX derivation changed')
    reads = [n for n in ast.walk(tree) if isinstance(n, ast.Name) and isinstance(n.ctx, ast.Load)]
    if any(n.lineno <= derived.lineno for n in reads if n.id == 'Fossil_hydro'):
        raise ValueError('The model uses inherited Fossil_hydro before rebuilding it')

    fuel_reads = {id(n) for n in reads if n.id == 'fuel'}
    bound_fuel_reads = set()
    for comp in ast.walk(tree):
        if isinstance(comp, (ast.GeneratorExp, ast.ListComp, ast.SetComp, ast.DictComp)):
            for i, generator in enumerate(comp.generators):
                if isinstance(generator.target, ast.Name) and generator.target.id == 'fuel':
                    if any(id(n) in fuel_reads for g in comp.generators[:i+1] for n in ast.walk(g.iter)):
                        raise ValueError('Native fuel reads must be bound within the expression')
                    bound_fuel_reads.update(id(n) for n in ast.walk(comp) if id(n) in fuel_reads)
    if not fuel_reads or fuel_reads != bound_fuel_reads:
        raise ValueError('Found an externally supplied fuel symbol')

    containers = set()
    for node in ast.walk(tree):
        if (isinstance(node, ast.Assign) and isinstance(node.value, ast.Call)
                and isinstance(node.value.func, ast.Attribute)
                and isinstance(node.value.func.value, ast.Name)
                and node.value.func.value.id == 'ucp'
                and node.value.func.attr in ('addVars','addConstrs')):
            containers.update(t.id for t in node.targets if isinstance(t, ast.Name)
                              and all(r.lineno > node.lineno for r in reads if r.id == t.id))
    return containers


def signature_observer(original, containers, read_reference, record):
    def observe(ctx, digest):
        year = int(ctx['years'][0])
        expected = read_reference(year)['common_input_hashes']
        if set(expected) & (containers | {'Fossil_hydro'}):
            raise ValueError('A locally rebuilt symbol overlaps a reference input')
        observed = ctx.copy()
        removed = []
        restored = []
        for name in containers & ctx.keys():
            if not isinstance(ctx[name],dict):
                raise ValueError('Unexpected carried model container: '+name)
            del observed[name]
            removed.append(name)
        if 'Fossil_hydro' in ctx:
            if ctx['Fossil_hydro'] != list(dict.fromkeys(ctx['disp_units']+ctx['hydro_units'])):
                raise ValueError('Unexpected carried hydro cost aggregation')
            del observed['Fossil_hydro']
            removed.append('Fossil_hydro')
        if 'fuel' not in expected:
            raise ValueError('The frozen reference must include the inventory fuel list')
        if digest(ctx.get('fuel')) != expected['fuel']:
            inventory_fuel = list(ctx['df']['fuel_type'].unique())
            if digest(inventory_fuel) != expected['fuel']:
                raise ValueError('The fuel reference differs from the actual native inventory')
            observed['fuel'] = inventory_fuel
            restored.append('fuel')
        actual = original(observed,digest)
        changed = {name:dict(expected=expected[name],actual=actual[name])
                   for name in actual.keys() & expected.keys() if actual[name] != expected[name]}
        extra = sorted(actual.keys()-expected.keys())
        missing = sorted(expected.keys()-actual.keys())
        record(year,dict(exogenous_inputs_match=not (changed or extra or missing),
                         extra=extra,missing=missing,changed=changed,
                         removed_rebuilt_symbols=sorted(removed),
                         reference_hash_recomputed=restored))
        return actual
    return observe


def district_window(original):
    baseline = None

    def prepare(ctx, policy):
        nonlocal baseline
        before = ctx['Thermal_Decom_DH']
        if baseline is None:
            baseline = before.copy()
        ctx['Thermal_Decom_DH'] = baseline.copy()
        original(ctx,policy)
    return prepare
