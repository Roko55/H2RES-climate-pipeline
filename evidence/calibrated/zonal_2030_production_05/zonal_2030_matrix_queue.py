"""Build one diagnostic matrix after the national solver releases its session."""

import argparse
import csv
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import socket
import time
import traceback

import electricity_matrix_2030 as matrix
from zonal_2030_hydro import audit_and_finalize_hydro, install_hydro_inputs, read_hydro
from zonal_2030_hourly import audit_generation_membership
from zonal_2030_matrix_input import make_hooks, validate_weather
from zonal_2030_stock import (audit_known_native_stock, audit_zonal_balance_roster,
                              configure_known_build_limits, install_zonal_balance_roster)
from zonal_2030_weather import digest


ROOT=Path(__file__).resolve().parent
TERMINAL={'original_completed','previous_completed','planning_completed','stopped'}


def owned():
    if (socket.gethostname().lower()!=matrix.EXPECTED_HOST or ROOT.parent!=matrix.REMOTE.resolve()
            or not ROOT.name.startswith('zonal_2030_matrix_')):
        raise ValueError('Only an isolated owned server matrix is permitted')


def read(path):
    return json.loads(path.read_bytes())


def status(stage,**details):
    final=ROOT/'queue_status.json'
    tmp=final.with_suffix('.pending')
    tmp.write_text(json.dumps(dict(utc=datetime.now(timezone.utc).isoformat(),stage=stage,pid=os.getpid(),
                                  no_optimization=True,planning_approved=False,**details),indent=2),encoding='utf8')
    tmp.replace(final)


def recovery_state(national,name):
    recovery=national/name
    path=recovery/'status.json'
    if path.is_file():
        return read(path)['status']
    return 'starting' if (recovery/'national_production_recovery.py').exists() else 'not_launched'


def wait_for_national():
    national=ROOT.parent/'national_production_03'
    previous=None
    while True:
        original=read(national/'coordination/status.json')['status']
        continuation=read(national/'recovery_01/status.json')['status']
        continuation_v2=recovery_state(national,'recovery_02')
        continuation_v3=recovery_state(national,'recovery_03')
        available=matrix.resource_check()
        state=(original,continuation,continuation_v2,continuation_v3,available['ready'])
        if state!=previous:
            status('waiting_for_national_and_resources',original=original,continuation=continuation,
                   continuation_v2=continuation_v2,continuation_v3=continuation_v3,resources=available)
            previous=state
        if (original in TERMINAL and continuation in TERMINAL
                and continuation_v2 in TERMINAL and continuation_v3 in TERMINAL|{'not_launched'}
                and available['ready']):
            return
        time.sleep(30)


def merge_weather(native,overlay):
    with overlay.open(encoding='utf8',newline='') as stream:
        reader=csv.DictReader(stream)
        fields=reader.fieldnames[2:]
        if reader.fieldnames[:2]!=['year','period'] or len(fields)!=8 or len(set(fields))!=8:
            raise ValueError('Unexpected Croatian overlay columns')
        values={}
        for r in reader:
            key=int(r['year']),int(r['period'])
            if key in values:
                raise ValueError('Duplicate Croatian overlay period')
            values[key]={k:r[k] for k in fields}
    expected={(2030,p) for p in matrix.HOURS}
    if set(values)!=expected:
        raise ValueError('Expected one complete 2030 overlay')
    target=native.with_suffix('.pending')
    matched=set()
    with native.open(encoding='utf-8-sig',newline='') as source, target.open('x',encoding='utf8',newline='') as out:
        reader=csv.DictReader(source)
        if not set(fields)<=set(reader.fieldnames):
            raise ValueError('Missing Croatian source columns')
        writer=csv.DictWriter(out,reader.fieldnames,lineterminator='\n')
        writer.writeheader()
        for row in reader:
            key=int(row['year']),int(row['period'])
            if key in values:
                if key in matched:
                    raise ValueError('Duplicate native 2030 period')
                matched.add(key)
                row.update(values[key])
            writer.writerow(row)
        if matched!=expected:
            raise ValueError('Incomplete Croatian overlay')
    target.replace(native)


def validate_staged():
    weather=ROOT.parent/'zonal_2030_weather_03'
    validate_weather(weather)
    updated=ROOT/'zonal_2030_stock_dispatchable_preflight'
    stock=updated if updated.is_dir() else ROOT/'zonal_2030_stock_with_hydro_preflight'
    stock_report=read(stock/'preflight.json')
    candidate=stock/'candidate_genco.csv'
    if (stock_report['planning_approved'] is not False or not stock_report['future_stock_pending']
            or digest(candidate)!=stock_report['candidate_sha256']):
        raise ValueError('Unexpected diagnostic stock status')
    if stock==updated and (stock_report['thermal_2030']['ba_existing_coal_mw']!=1518.
                           or stock_report['thermal_2030']['si_ppe_tol_mw']!=132.
                           or set(stock_report['annual_power_ceiling_mwh'])!=
                              {'HR_Biomass_Existing','HR_Biogas_Existing'}):
        raise ValueError('The updated thermal inventory is incomplete')
    hydro=ROOT/'zonal_2030_eraa_hydro_preflight'
    streams=read_hydro((hydro/'hydro_hourly_2030.csv').read_bytes(),read(hydro/'preflight.json'))
    return weather,candidate,stock_report,streams


def physical_hooks(root,stock,streams,ledger):
    def install_stock(ctx):
        configure_known_build_limits(ctx,stock)
        install_zonal_balance_roster(ctx,stock)
    def install_water(ctx):
        ledger.update(install_hydro_inputs(ctx,streams))
    def audit_stock(ctx):
        report=audit_known_native_stock(ctx,stock,matrix.CAPACITY_ROW_LABELS)
        report['power_balance_roster']=audit_zonal_balance_roster(ctx)
        report['dated_units_in_grid_balance']=audit_generation_membership(ctx,stock)
        return report
    return make_hooks(root,install_stock,install_water,audit_stock,lambda c:audit_and_finalize_hydro(c,ledger))


def run():
    weather,candidate,stock_report,streams=validate_staged()
    with (ROOT/'controller.lock').open('x',encoding='utf8') as lock:
        lock.write(str(os.getpid()))
    wait_for_national()
    status('staging_native_diagnostic',stock_rows=len(stock_report['rows']))
    paths, inputs, scenario, year_specific=matrix.stage(physical_handoff=True)
    paths['genco_path'].write_bytes(candidate.read_bytes())
    merge_weather(paths['ncre_path'],weather/'avg_ncre.csv')
    for key in ('genco_path','ncre_path'):
        path=paths[key]
        inputs[path.relative_to(matrix.PROJECT).as_posix()]['staged_sha256']=digest(path)
    (ROOT/'input_manifest.json').write_text(json.dumps(inputs,indent=2),encoding='utf8')
    ledger={}
    hooks=physical_hooks(ROOT,stock_report,streams,ledger)
    status('building_native_diagnostic')
    ctx=matrix.assemble(paths,scenario,year_specific,matrix_only=True,physical_handoff=True,**hooks)
    if ctx is None:
        raise ValueError('Resource admission changed before model creation; no optimization started')
    try:
        report=matrix.check_matrix(ctx,paths,inputs)
        with (ROOT/'matrix_report.json').open('x',encoding='utf8') as f:
            json.dump(dict(native=report,physical=ctx['physical_zonal_audit'],no_optimization=True,
                           production_approved=False,remaining_stock=stock_report['future_stock_pending']),f,
                      indent=2,allow_nan=False)
        status('native_diagnostic_completed',fingerprint=report['fingerprint'])
    finally:
        ctx['ucp'].dispose()


if __name__=='__main__':
    owned()
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('mode',choices=('verify','controller'))
    if p.parse_args().mode=='verify':
        _,_,receipt,_=validate_staged()
        status('staged_sources_verified',stock_rows=len(receipt['rows']))
    else:
        try:
            run()
        except BaseException:
            status('stopped',error=traceback.format_exc())
            raise
