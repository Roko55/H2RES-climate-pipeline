"""Read-only extraction of the three frozen synthetic 2030 climate profiles."""
from pathlib import Path
import csv,json,hashlib
HERE=Path(__file__).resolve().parent
ARCHIVE=HERE.parent/'final_frozen_profiles_20261031'
WEATHER=HERE.parent/'zonal_2030_weather_03'
read=lambda p:json.loads(p.read_bytes())
sha=lambda p:hashlib.sha256(p.read_bytes()).hexdigest()
jobs=read(ARCHIVE/'jobspecs.json');weather=read(WEATHER/'weather_preflight.json')
sources=[]
for case in ('avg','low','high'):
    spec=read(Path(jobs['electricity_'+case]['specification']))
    summary=read(Path(spec['summary_path']))
    source=Path(summary['climate_profile_path']).resolve()
    source.relative_to(ARCHIVE.resolve())
    assert sha(source)==weather['cases'][case]['profile_sha256']
    with source.open(newline='',encoding='utf8') as stream:
        reader=csv.DictReader(stream)
        rows=[r for r in reader if int(r['year'])==2030]
    assert len(rows)==8760 and [int(r['period']) for r in rows]==list(range(1,8761))
    target=HERE/('electricity_'+case+'.csv')
    with target.open('w',encoding='utf8',newline='') as stream:
        writer=csv.DictWriter(stream,fieldnames=['year','period','solar_cf','wind_cf','temperature_c'])
        writer.writeheader();writer.writerows({k:r[k] for k in writer.fieldnames} for r in rows)
    sources.append(dict(case=case,source=source.relative_to(ARCHIVE).as_posix(),
        source_sha256=sha(source),file=target.name,sha256=sha(target),hours=8760,
        mapped_ncre_sha256=weather['cases'][case]['mapped_ncre_sha256']))
(HERE/'electricity_profiles_sources.json').write_text(json.dumps(dict(sources=sources,
    derivation_script_sha256=sha(Path(__file__)),new_optimization=False),indent=2)+'\n')
print('Exported three synthetic profiles; no optimization')
