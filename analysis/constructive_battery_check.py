"""Construct an exclusive battery schedule from archived hourly flows, without solving.

This is a physical balance check, not an optimum certificate or a replacement
for the accepted operational results. No native result is changed.
"""
from pathlib import Path
from hashlib import sha256
from math import fsum
import csv, gzip, json

HERE=Path(__file__).resolve().parent
BASE=HERE.parent/'evidence/calibrated/zonal_2030_production_05/jobs'
OUT=HERE.parent/'exports/constructive_battery_check'; OUT.mkdir(parents=True,exist_ok=True)
TOL=1e-7
# The archived executed main sets charging efficiency to 0.9. The saved
# Liion_storage input has discharge efficiency 0.9; validate the annual cycle
# against each archived zone with nonzero throughput as an additional check.
EC=ED=.9; RT=EC*ED
checks=[]
for case in ('avg','low','high'):
    marker,=list((BASE/f'operation_{case}_2021_0').glob('attempt_*/accepted.json'))
    data=json.loads(marker.read_bytes()); source=marker.parent/'hourly.csv.gz'
    assert sha256(source.read_bytes()).hexdigest()==data['files']['hourly.csv.gz']
    assert data['croatia']['ceep_limit_active'] is False
    with gzip.open(source,'rt',encoding='utf8',newline='') as f: rows=list(csv.DictReader(f))
    assert len(rows)==26280
    metrics={z:dict(old_ceep=[],new_ceep=[],old_charge=[],old_discharge=[],new_charge=[],new_discharge=[],eens=[],changed_hours=0,max_soc_delta_error=0.,max_grid_residual=0.,max_power_increase=0.,max_overlap=0.) for z in ('HR','SI','BA')}
    for row in rows:
        z=row['system']; m=metrics[z]; C=float(row['battery_charge_grid_mwh']); D=float(row['battery_discharge_grid_mwh'])
        assert C>=-TOL and D>=-TOL
        C=max(0.,C); D=max(0.,D)
        removed=min(C,D/RT); a=max(0.,C-removed); b=max(0.,D-RT*removed)
        extra=removed*(1-RT)
        old=float(row['ceep_mwh']); new=old+extra
        soc_error=abs((EC*a-b/ED)-(EC*C-D/ED))
        residual=fsum(float(row[k]) for k in ('generation_mwh','eens_mwh','import_delivered_mwh'))+b-fsum([float(row[k]) for k in ('load_mwh','gross_export_sending_mwh','pumped_charge_grid_mwh')]+[new,a])
        assert soc_error<=TOL and abs(residual)<=TOL and a+b<=C+D+TOL and min(a,b)<=TOL
        m['old_ceep'].append(old); m['new_ceep'].append(new); m['old_charge'].append(C); m['old_discharge'].append(D)
        m['new_charge'].append(a); m['new_discharge'].append(b); m['eens'].append(float(row['eens_mwh']))
        m['changed_hours']+=int(removed>TOL)
        m['max_soc_delta_error']=max(m['max_soc_delta_error'],soc_error)
        m['max_grid_residual']=max(m['max_grid_residual'],abs(residual))
        m['max_power_increase']=max(m['max_power_increase'],a+b-C-D)
        m['max_overlap']=max(m['max_overlap'],min(a,b))
        row.update(battery_charge_grid_mwh=a,battery_discharge_grid_mwh=b,battery_overlap_grid_mwh=min(a,b),ceep_mwh=new)
    for z,m in metrics.items():
        assert abs(EC*fsum(m['old_charge'])-fsum(m['old_discharge'])/ED)<=2*TOL
        for k in ('old_ceep','new_ceep','old_charge','old_discharge','new_charge','new_discharge'):
            m[k+'_mwh']=fsum(m.pop(k))
        m['unserved_mwh']=fsum(m['eens']);m['unserved_hours']=sum(v>TOL for v in m.pop('eens'))
    target=OUT/f'{case}_2021_zero_exchange_exclusive_constructed.csv.gz'
    with gzip.open(target,'wt',encoding='utf8',newline='') as f:
        w=csv.DictWriter(f,fieldnames=list(rows[0]));w.writeheader();w.writerows(rows)
    checks.append(dict(case=case,source_marker=str(marker),source_marker_sha256=sha256(marker.read_bytes()).hexdigest(),
        original_hourly_sha256=data['files']['hourly.csv.gz'],zones=metrics,
        croatian_import_export_unchanged_and_zero=True,constructed_output=str(target)))
report=dict(status='constructive_hourly_balance_check_passed',optimization_calls=0,
    original_results_modified=False,global_optimum_claimed=False,full_native_model_reoptimized=False,
    interpretation='An exclusive hourly battery schedule exists with the same hourly SOC changes, generation, demand, exchange and unserved energy; removed battery cycling losses become extra curtailment. This does not determine the exclusive global optimum.',
    charging_efficiency=EC,discharge_efficiency=ED,cases=checks)
(OUT/'report.json').write_text(json.dumps(report,indent=2)+'\n',encoding='utf8')
print(json.dumps([dict(case=r['case'],unserved_mwh=r['zones']['HR']['unserved_mwh'],
    old_ceep_mwh=r['zones']['HR']['old_ceep_mwh'],new_ceep_mwh=r['zones']['HR']['new_ceep_mwh'],
    extra_ceep_mwh=r['zones']['HR']['new_ceep_mwh']-r['zones']['HR']['old_ceep_mwh']) for r in checks],indent=2))
