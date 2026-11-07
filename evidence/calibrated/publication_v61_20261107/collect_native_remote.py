"""Read-only audits and a source-preserving export of completed native results.

Writes only into its own publication export directory. Never constructs or solves a model.
Large native variable/constraint stores and source inputs remain on the server;
their accepted SHA256 identities are retained and checked by the completion audits.
"""
from pathlib import Path
from hashlib import sha256
import json,os,subprocess,sys,zipfile

HERE=Path(__file__).resolve().parent
BASE=Path('E:/Roko/remote_access')
NATIONAL=BASE/'national_production_03'
ZONAL=BASE/'zonal_2030_production_05'
assert HERE==BASE/'publication_v61_20261107'
os.environ.update(OMP_NUM_THREADS='1',OPENBLAS_NUM_THREADS='1',MKL_NUM_THREADS='1',PYTHONDONTWRITEBYTECODE='1')
sha=lambda path:sha256(path.read_bytes()).hexdigest()

reports={}
for kind,root in [('national',NATIONAL),('zonal',ZONAL)]:
    script=HERE/f'audit_{kind}_completion.py'
    env=dict(os.environ,PYTHONPATH=str(root))
    with (HERE/f'{kind}_audit.stdout.json').open('wb') as out,(HERE/f'{kind}_audit.stderr.txt').open('wb') as err:
        result=subprocess.run([sys.executable,'-B',str(script),str(root)],cwd=root,env=env,stdout=out,stderr=err)
    reports[kind]={'exit_code':result.returncode,'auditor_sha256':sha(script)}
    if result.returncode:
        (HERE/'collection_status.json').write_text(json.dumps({'status':'audit_failed','audits':reports},indent=2))
        sys.exit(result.returncode)
    value=json.loads((HERE/f'{kind}_audit.stdout.json').read_text())
    assert value['status']=='verified'
    reports[kind]['report_sha256']=sha(HERE/f'{kind}_audit.stdout.json')

selected={}
def add(path):
    path=path.resolve();path.relative_to(BASE.resolve())
    if path.is_file():selected[path.relative_to(BASE).as_posix()]=path

for kind in ('national','zonal'):
    add(HERE/f'audit_{kind}_completion.py');add(HERE/f'{kind}_audit.stdout.json')
add(Path(__file__))
for name in ('status.json','preparation.json','plan.json','historical_base.json','production_stock_reconciliation.json',
             'production_inventory.csv','production_supply_inventory.csv'):
    add(NATIONAL/name)
for folder in ('coordination','recovery_01','recovery_02','recovery_03','national_seed_evidence'):
    for p in (NATIONAL/folder).glob('*'):
        if p.suffix in ('.json','.py'):add(p)
for marker in NATIONAL.glob('jobs/*/*/accepted.json'):
    add(marker)
    data=json.loads(marker.read_text())
    for name,expected in data['files'].items():
        p=marker.parent/name
        # Complete native annual/asset tables and all small audit/receipt records.
        if p.suffix in ('.json','.csv'):
            assert sha(p)==expected
            add(p)
prep=json.loads((NATIONAL/'preparation.json').read_text())
for name,expected in prep['frozen_files'].items():
    p=NATIONAL/name
    if p.suffix=='.py':
        assert sha(p)==expected
        add(p)
for name in ('status.json','results.json','staging_verified.json'):
    add(ZONAL/name)
for marker in ZONAL.glob('jobs/*/attempt_*/accepted.json'):
    add(marker)
    data=json.loads(marker.read_text())
    for name,expected in data['files'].items():
        p=marker.parent/name
        if p.suffix in ('.json','.csv') or p.name=='hourly.csv.gz':
            assert sha(p)==expected
            add(p)
for p in ZONAL.glob('*.py'):add(p)
for p in (BASE/'zonal_2030_matrix_06').glob('*.json'):add(p)
manifest={name:{'sha256':sha(path),'bytes':path.stat().st_size} for name,path in sorted(selected.items())}
(HERE/'source_manifest.json').write_text(json.dumps(manifest,indent=2)+'\n')
archive=HERE/'native_results.zip'
with zipfile.ZipFile(archive,'w',zipfile.ZIP_DEFLATED,compresslevel=6) as z:
    for name,path in sorted(selected.items()):z.write(path,name)
    z.write(HERE/'source_manifest.json','source_manifest.json')
with archive.open('rb') as stream:
    part=0
    while data:=stream.read(1024*1024):
        (HERE/f'native_results.part{part:04}.bin').write_bytes(data);part+=1
status={'status':'audited_and_exported','audits':reports,'archive_sha256':sha(archive),
    'archive_bytes':archive.stat().st_size,'parts':part,'files':len(manifest),
    'scope':'Native annual/asset tables, accepted receipts, audited hourly zonal outputs, executed sources and source signatures',
    'raw_variable_constraint_stores':'Remain in their original server folders; hashes and capital-chain evidence verified by the full auditors',
    'new_optimization':False}
(HERE/'collection_status.json').write_text(json.dumps(status,indent=2)+'\n')
print(json.dumps(status),flush=True)
