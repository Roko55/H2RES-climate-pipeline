"""Owned batch paths, checked checkpoints and Windows-safe record writes."""
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
import hashlib
import json
import os
import pickle
import time
import uuid

R = Path(__file__).resolve().parent
OLD = Path('E:/Roko/remote_access/final_frozen_profiles_20261031')
YEARS = list(range(2020, 2051, 5))
CASES = ['average', 'solar_low', 'solar_high', 'wind_low', 'wind_high',
         'temperature_cold', 'temperature_hot']
DONORS = {'average': (65, 65, 65), 'solar_low': (2, 65, 65),
          'solar_high': (28, 65, 65), 'wind_low': (65, 20, 65),
          'wind_high': (65, 72, 65), 'temperature_cold': (65, 65, 41),
          'temperature_hot': (65, 65, 48)}


def remote_only():
    assert os.name == 'nt' and R == Path('E:/Roko/remote_access/national_production_03')
    assert R.resolve().is_relative_to(Path('E:/Roko').resolve())


def now():
    return datetime.now(timezone.utc).isoformat()


def sha(path):
    h = hashlib.sha256()
    with Path(path).open('rb') as f:
        for chunk in iter(lambda: f.read(4 * 1024 * 1024), b''):
            h.update(chunk)
    return h.hexdigest()


def load(path):
    return json.loads(Path(path).read_text(encoding='utf8'))


def replace(pending, target, retries=40):
    for attempt in range(retries):
        try:
            os.replace(pending, target)
            return
        except OSError as e:
            if getattr(e, 'winerror', None) not in (5, 32, 33) or attempt + 1 == retries:
                raise
            time.sleep(min(0.05 * (attempt + 1), 0.5))


def save(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    pending = path.with_name(path.name + '.' + uuid.uuid4().hex + '.pending')
    pending.write_text(json.dumps(value, indent=2, default=lambda v: v.item()
                       if hasattr(v, 'item') else str(v)), encoding='utf8')
    replace(pending, path)


def save_pickle(path, value):
    path = Path(path)
    pending = path.with_name(path.name + '.' + uuid.uuid4().hex + '.pending')
    with pending.open('wb') as f:
        pickle.dump(value, f, protocol=4)
    replace(pending, path)


def clean(value):
    if isinstance(value, dict):
        return {str(k): clean(v) for k, v in sorted(value.items(), key=lambda p: str(p[0]))}
    if isinstance(value, (tuple, list)):
        return [clean(v) for v in value]
    if hasattr(value, 'item'):
        return value.item()
    return value


def digest(value):
    return hashlib.sha256(json.dumps(clean(value), sort_keys=True).encode()).hexdigest()


@contextmanager
def lease(name):
    import msvcrt
    path = R / 'leases' / (name + '.lock')
    path.parent.mkdir(exist_ok=True)
    with path.open('a+b') as f:
        f.seek(0, 2)
        if f.tell() == 0:
            f.write(b'0')
            f.flush()
        f.seek(0)
        msvcrt.locking(f.fileno(), msvcrt.LK_NBLCK, 1)
        try:
            yield
        finally:
            f.seek(0)
            msvcrt.locking(f.fileno(), msvcrt.LK_UNLCK, 1)


def identity(record):
    import psutil
    try:
        p = psutil.Process(record['pid'])
        if abs(p.create_time() - record['created']) > .02:
            return None
        script = str(Path(record['script']).resolve()).replace('\\', '/').lower()
        if not any(str(a).replace('\\', '/').lower() == script for a in p.cmdline()):
            return None
        return p
    except (psutil.Error, KeyError):
        return None


def quality(value):
    return (value['status'] == 2 and value['constraint_violation'] <= 1e-7
            and value['bound_violation'] <= 1e-7)


def accepted(case, year, verify=True):
    out = R / 'jobs' / case / str(year)
    marker = out / 'accepted.json'
    if not marker.exists():
        return False
    v = load(marker)
    assert v['case'] == case and v['year'] == year
    assert v['fence'] == load(R / 'preparation.json')['execution_fence']
    if verify:
        for name, expected in v['files'].items():
            assert sha(out / name) == expected, str(out / name)
        assert quality(load(out / 'solver_result.json'))
    return True


def verify_sources():
    prep = load(R / 'preparation.json')
    for name, expected in prep['frozen_files'].items():
        assert sha(R / name) == expected, 'Changed frozen input/code: ' + name
    return prep
