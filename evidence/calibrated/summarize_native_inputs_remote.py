"""Summarize hash-verified captured native parameters; run no solver."""
from pathlib import Path
from hashlib import sha256
import csv,gzip,json,math

HERE=Path(__file__).resolve().parent
ROOT=Path('E:/Roko/remote_access/national_production_03')
assert HERE==Path('E:/Roko/remote_access/publication_v61_20261107')
records=[];sources=[]
for marker in sorted(ROOT.glob('jobs/*/*/accepted.json')):
    accepted=json.loads(marker.read_text());folder=marker.parent
    case=folder.parent.name;window=int(folder.name)
    path=folder/'runtime_accounting_inputs.csv.gz'
    digest=sha256(path.read_bytes()).hexdigest()
    assert digest==accepted['files'][path.name]
    groups={}
    with gzip.open(path,'rt',encoding='utf8',newline='') as stream:
        for row in csv.DictReader(stream):
            key=json.loads(row['key_json']);family=row['family'];year=int(key[-1]);value=float(row['value'])
            unit=str(key[0]) if family in ('heat_demand','cooling_demand','HP_COP_period') else 'all'
            assert math.isfinite(value)
            group=groups.setdefault((year,family,unit),[]);group.append(value)
    for (year,family,unit),values in sorted(groups.items()):
        records.append(dict(case=case,window_start=window,year=year,family=family,unit=unit,
                            count=len(values),sum=math.fsum(values),minimum=min(values),maximum=max(values)))
    sources.append(dict(case=case,window_start=window,source='national_production_03/'+path.relative_to(ROOT).as_posix(),
                        source_sha256=digest,accepted_sha256=sha256(marker.read_bytes()).hexdigest()))
assert len(sources)==42
target=HERE/'native_input_statistics.csv'
with target.open('w',encoding='utf8',newline='') as stream:
    writer=csv.DictWriter(stream,fieldnames=list(records[0]));writer.writeheader();writer.writerows(records)
report=dict(status='verified',windows=42,sources=sources,statistics_sha256=sha256(target.read_bytes()).hexdigest(),
            derivation_script_sha256=sha256(Path(__file__).read_bytes()).hexdigest(),new_optimization=False)
(HERE/'native_input_statistics_sources.json').write_text(json.dumps(report,indent=2)+'\n')
print(json.dumps({'status':'verified','windows':42,'rows':len(records)}))
