"""Verify the delivered model bundle, calibration boundary and source inventory."""
from pathlib import Path
import hashlib
import json

ROOT=Path(__file__).resolve().parent

def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()

def main():
    manifest=json.loads((ROOT/'artifact_manifest.json').read_text())
    for name,record in manifest['files'].items():
        path=(ROOT/name).resolve()
        path.relative_to(ROOT)
        if path.stat().st_size!=record['bytes'] or digest(path)!=record['sha256']:
            raise ValueError('Artifact mismatch: '+name)
    sources=json.loads((ROOT/'provenance/source_manifest.json').read_text())
    for name,record in sources.items():
        if digest(ROOT/name)!=record['sha256']:
            raise ValueError('Source mismatch: '+name)
    folder=ROOT/'models/default'
    metadata=json.loads((folder/'metadata.json').read_text())
    expected={(c,q) for c in ('Solar','Temperature') for q in (.16,.5,.84)}
    if {(r['channel'],r['quantile']) for r in metadata['models']}!=expected or len(metadata['models'])!=6:
        raise ValueError('Incomplete model bundle')
    for entry in metadata['models']:
        path=(folder/entry['file']).resolve()
        path.relative_to(folder.resolve())
        if digest(path)!=entry['sha256']:
            raise ValueError('Native model mismatch: '+entry['file'])
    preparation=json.loads((ROOT/'provenance/model/preprocessing_audit.json').read_text())
    if any(int(y)>2015 for values in preparation['calibration_targets'].values() for y in values):
        raise ValueError('National calibration crosses training boundary')
    if preparation['new_data_sha256']!=metadata['data_sha256'] or metadata['reporting_used_for_fit_or_calibration']:
        raise ValueError('Model and calibration corpus mismatch')
    print(json.dumps({'status':'passed','files_verified':len(manifest['files']),
                      'native_models_verified':6,'data_sha256':metadata['data_sha256'],
                      'reporting_used_for_fit_or_calibration':False,
                      'scope':'File, model and recorded calibration boundaries; reporting years previously inspected.'},indent=2))

if __name__=='__main__':main()
