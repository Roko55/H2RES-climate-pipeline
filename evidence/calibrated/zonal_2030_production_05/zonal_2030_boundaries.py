"""Extract and audit unchanged SI/BA exterior limits beside the Croatian cut."""
import csv
from hashlib import sha256
import io
import json
from math import fsum, isfinite
from pathlib import Path
from zipfile import ZipFile

from eraa_2030_built_exchange import HOURS, TOL


NEIGHBORS = {'SI':('AT00','HU00','ITN1'), 'BA':('ME00','RS00')}
DIRECTIONS = ('in','out')
COLUMNS = tuple(f'{s.lower()}_{d}_mw' for s in NEIGHBORS for d in DIRECTIONS)


def external_edges():
    return {s: {d: tuple(f'{n}-{s}00' if d=='in' else f'{s}00-{n}' for n in neighbors)
                for d in DIRECTIONS} for s,neighbors in NEIGHBORS.items()}


def extract_neighbor_envelope(root):
    import pandas as pd
    from prepare_eraa_2030_exchange import ARCHIVE_SHA, MEMBER, SOURCE, extract

    archive = (root/'inputs/entsoe_eraa_2025/ERAA_2025_NTCs.zip').read_bytes()
    if sha256(archive).hexdigest()!=ARCHIVE_SHA:
        raise ValueError('Unexpected official ERAA border archive')
    with ZipFile(io.BytesIO(archive)) as z:
        workbook = z.read(MEMBER)
    mapped = external_edges()
    labels = tuple(label for pair in mapped.values() for links in pair.values() for label in links)
    with pd.ExcelFile(io.BytesIO(workbook)) as book:
        header = book.parse('HVAC',header=None,nrows=8).iloc[7]
        relevant = {v for v in header if isinstance(v,str) and '-' in v
                    and any(country+'00' in v.split('-') for country in NEIGHBORS)}
        internal = {f'{s}00-HR00' for s in NEIGHBORS}|{f'HR00-{s}00' for s in NEIGHBORS}
        if relevant != set(labels)|internal:
            raise ValueError('ERAA SI or BA has an unmapped exterior corridor')
        hvdc = book.parse('HVDC',header=None,nrows=8).iloc[7]
        if any(isinstance(v,str) and any(s+'00' in v for s in NEIGHBORS) for v in hvdc):
            raise ValueError('Unsupported published SI/BA HVDC corridor')
        hourly = extract(book,'HVAC',labels,16)
    data = {f'{s.lower()}_{d}_mw':hourly[list(links)].sum(axis=1).tolist()
            for s,dirs in mapped.items() for d,links in dirs.items()}
    stream = io.StringIO(newline='')
    w = csv.writer(stream,lineterminator='\n')
    w.writerow(('period',*COLUMNS))
    w.writerows((p,*(data[key][p-1] for key in COLUMNS)) for p in HOURS)
    raw = stream.getvalue().encode('utf8')
    report = dict(status='published_neighbor_envelope_candidate',source=SOURCE,archive_sha256=ARCHIVE_SHA,
                  member=MEMBER,member_sha256=sha256(workbook).hexdigest(),candidate_sha256=sha256(raw).hexdigest(),
                  source_edges=mapped,hours=len(HOURS),stress_fraction_applies=False,
                  interpretation='Published directional NTCs aggregated by outer boundary; no independent PTDF/RAM model.',
                  summary_mw={k:{'min':min(v),'max':max(v)} for k,v in data.items()})
    return raw,report


def read_neighbor_envelope(raw, meta):
    if (meta.get('status')!='published_neighbor_envelope_candidate' or meta.get('hours')!=len(HOURS)
            or meta.get('stress_fraction_applies') is not False
            or sha256(raw).hexdigest()!=meta.get('candidate_sha256')):
        raise ValueError('Unverified SI/BA exterior input')
    reader = csv.DictReader(io.StringIO(raw.decode('utf8')))
    if reader.fieldnames != ['period',*COLUMNS]:
        raise ValueError('Unexpected SI/BA exterior data schema')
    rows = list(reader)
    if tuple(int(r['period']) for r in rows) != HOURS:
        raise ValueError('Incomplete SI/BA exterior chronology')
    result = {p:{c:float(r[c]) for c in COLUMNS} for p,r in zip(HOURS,rows)}
    if any(not isfinite(v) or v<0. for r in result.values() for v in r.values()):
        raise ValueError('Invalid SI/BA directional MW')
    return result


def configure_neighbor_inventory(ctx,hourly,fixed):
    """Raise the native stock ceiling only to the published directional maximum."""
    if tuple(ctx['years'])!=(2030,) or tuple(ctx['previous_years_list']) or set(hourly)!=set(HOURS):
        raise ValueError('Expected a clean 2030 physical inventory')
    links = {}
    for system in NEIGHBORS:
        matches = [k for k in ctx['outside_links'] if k[2]==system and k[0] in ctx['transmission_outside_units_el']]
        if len(matches)!=1:
            raise ValueError('Expected one native exterior electric corridor in '+system)
        key, = matches
        for name in ('transmission_outside_cap','transmission_outside_max_cap','transmission_outside_max_cap_period'):
            if key not in ctx[name]:
                raise ValueError('Missing native exterior infrastructure input: '+name)
        limit = max(row[f'{system.lower()}_{d}_mw'] for row in hourly.values() for d in DIRECTIONS)
        ctx['transmission_outside_cap'][key] = ctx['transmission_outside_max_cap'][key] = limit
        ctx['transmission_outside_max_cap_period'][key] = 0.
        ctx['transmission_outside_ramp'][key] = 1.
        fixed[1][key] = limit
        links[system] = key
    return links


def install_neighbor_rows(ctx,hourly,links):
    """Install two SI/BA directional rows per hour without a Croatian stress multiplier."""
    model = ctx['ucp']
    model.update()
    rows = []
    for s,(unit,z,_) in links.items():
        for p in HOURS:
            balance = ctx['energy_balance'][z,s,p,2030]
            for direction,name in (('in','import_outside'),('out','export_outside')):
                v = ctx[name][unit,2030,p,z,s]
                coefficient = model.getCoeff(balance,v)
                if (v.LB<0. or (direction=='in' and not 0.<coefficient<=1.+TOL)
                        or (direction=='out' and abs(coefficient+1.)>TOL)):
                    raise ValueError('Exterior flow missing from the correct zonal balance')
                bound = hourly[p][f'{s.lower()}_{direction}_mw']
                row = model.addConstr(v<=bound,name=f'eraa2030_{s}_external_{direction}[{p}]')
                rows.append((row,v,bound))
    model.update()
    audit_neighbor_rows(model,rows)
    return rows


def audit_neighbor_rows(model,rows,*,solved=False):
    if len(rows)!=len(NEIGHBORS)*len(HOURS)*2:
        raise ValueError('Incomplete SI/BA exterior rows')
    excess = []
    for row,v,bound in rows:
        if row.Sense!='<' or abs(row.RHS-bound)>TOL or model.getCoeff(row,v)!=1. or model.getRow(row).size()!=1:
            raise ValueError('Unexpected built SI/BA exterior row')
        if solved:
            if not isfinite(v.X) or not -TOL<=v.X<=bound+TOL:
                raise ValueError('Solved neighbor exchange violates its unchanged directional bound')
            excess.append(max(0.,v.X-bound))
    return dict(rows=len(rows),worst_excess_mw=max(excess,default=0.) if solved else None)


if __name__=='__main__':
    here = Path(__file__).resolve().parent
    payload,evidence = extract_neighbor_envelope(here)
    out = here/'zonal_2030_boundary_preflight'
    out.mkdir(exist_ok=False)
    (out/'neighbor_hourly.csv').write_bytes(payload)
    (out/'neighbor_preflight.json').write_text(json.dumps(evidence,indent=2)+'\n',encoding='utf8')
