"""Wire the pinned 2030 ERAA envelope into a new zonal native build."""
import os
from pathlib import Path

from electricity_exchange_preflight import audit_stress_exchange
from electricity_project_integration import ROKO, validate_planning_manifest
from eraa_2030_built_exchange import (
    HOURS, apply_fixed_native_transfer_inputs, audit_built_native_transfer_inputs,
    audit_exchange_envelope, install_exchange_envelope, load_envelope,
)


DATA = Path('data/eraa_2030')
CANDIDATE = DATA/'candidate_hourly_mw.csv'
SOURCE_MANIFEST = DATA/'preflight.json'


def render_eraa_2030_build(source, fraction):
    if type(fraction) not in (float,int) or fraction not in (0.,.5,1.):
        raise ValueError('Unsupported Croatian exchange fraction')
    anchor = 'current_year = min(years)'
    if (source.count(anchor) != 1 or 'ucp.optimize(' in source
            or 'audit_stress_exchange' in source or 'configure_eraa_2030' in source):
        raise ValueError('Expected a native build with no other border overlay or solve')
    hook = ('from eraa_electricity_integration import configure_eraa_2030, finish_eraa_2030\n'
            f'configure_eraa_2030(globals(), {float(fraction)!r})\n')
    code = source.replace(anchor,hook+anchor)+'\nfinish_eraa_2030(globals())\n'
    compile(code,'<isolated ERAA 2030 build>','exec')
    return code


def configure_eraa_2030(ctx, fraction):
    root = Path.cwd().resolve()
    if os.name != 'nt' or ROKO.resolve() not in root.parents:
        raise ValueError('ERAA build is restricted to an owned Roko project')
    if ('_eraa_2030_fixed' in ctx or tuple(ctx['years']) != (2030,) or tuple(ctx['periods']) != HOURS
            or any(ctx[flag] for flag in ('model_heating','model_cooling','model_transport_and_industry',
                                         'model_efuel','model_H2_transmission','include_secc_links'))):
        raise ValueError('Expected one unmodified zonal electricity window for 2030')
    manifest = Path(os.environ['H2RES_ELECTRICITY_PLANNING_MANIFEST']).resolve()
    if root not in manifest.parents:
        raise ValueError('ERAA manifest leaves the isolated project')
    spec,_ = validate_planning_manifest(manifest.read_bytes(),
                                      os.environ['H2RES_ELECTRICITY_PLANNING_MANIFEST_SHA256'],root)
    if spec['years'] != [2030] or any(path.as_posix() not in spec['inputs_sha256'] for path in (CANDIDATE,SOURCE_MANIFEST)):
        raise ValueError('ERAA files or anchor are not frozen in the electricity manifest')
    hourly = load_envelope((root/CANDIDATE).read_bytes(),(root/SOURCE_MANIFEST).read_bytes())
    fixed = apply_fixed_native_transfer_inputs(ctx,hourly)
    ctx['_eraa_2030_fixed'] = fixed,hourly,fraction


def verify_native_hourly_chain(ctx, primary):
    """The native intermediate cap must not silently derate the approved corridor."""
    model = ctx['ucp']
    checked = 0
    for key in primary:
        unit,*location = key
        stock = ctx['transfer_cap'][(unit,2030,*location)]
        hourly = ctx['transfer_cap_hour_prim'][(unit,2030,*location)]
        column = model.getCol(hourly)
        rows = [column.getConstr(i) for i in range(column.size())]
        rows = [row for row in rows if row.ConstrName.startswith('max_transfer_systexpimp_pos_all')]
        if not rows or any(row.Sense != '<' or row.RHS != 0.
                           or model.getCoeff(row, stock) != -1. or model.getCoeff(row, hourly) != 1.
                           or model.getRow(row).size() != 2 for row in rows):
            raise ValueError(f'Native intermediate transfer cap does not match its fixed stock: {key}')
        # The intermediate variable is shared; the direction audit checks each hourly flow.
        checked += len(rows)
    return checked


def finish_eraa_2030(ctx):
    if '_eraa_2030_installed' in ctx:
        raise ValueError('Refusing to apply the Croatian cut twice')
    fixed,hourly,fraction = ctx['_eraa_2030_fixed']
    model = ctx['ucp']
    native = audit_built_native_transfer_inputs(ctx,fixed)
    native['primary_hourly_chain_rows'] = verify_native_hourly_chain(ctx,fixed[0])
    native['direction_rows'] = audit_stress_exchange(ctx,model,1.)
    installed = install_exchange_envelope(ctx,hourly,fraction)
    envelope = audit_exchange_envelope(model,installed)
    ctx['_eraa_2030_installed'] = installed
    ctx['eraa_2030_build_audit'] = dict(fraction=fraction,native=native,envelope=envelope,
                                       interpretation='NTC envelope, not a full flow-based network')


def audit_solved_eraa_2030(ctx):
    if ctx['ucp'].Status != 2:
        raise ValueError('Expected an optimal electricity solve before checking realized flows')
    return audit_exchange_envelope(ctx['ucp'],ctx['_eraa_2030_installed'],solved=True)
