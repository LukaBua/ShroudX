"""PASS only if all React blink frame PNGs equal legacy bytes for every cached target."""
import json
import sys
from pathlib import Path
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from fastapi.testclient import TestClient
import app
rows = []
with TestClient(app.create_app(mode='cache')) as client:
    for target in ('WISE 0855-0714', "Barnard's Star", 'Epsilon Indi B', 'RW Aurigae', 'R Draconis'):
        alias = dict(zip(('WISE 0855-0714', "Barnard's Star", 'Epsilon Indi B', 'RW Aurigae', 'R Draconis'), ('WISE0855','Barnard','EpsilonIndiB','RWAurigae','RDraconis')))[target]
        query = dict(target=target, fov=240, mode='blink')
        legacy = client.get('/api/meta', params=query)
        react = client.get('/api/meta', params={**query, 'target': alias, 'clean': True, 'smooth': True})
        assert legacy.status_code == react.status_code == 200, (target, legacy.text, react.text)
        old, new = legacy.json(), react.json()
        assert [(f['index'], f['mjd']) for f in old['frames']] == [(f['index'], f['mjd']) for f in new['frames']]
        for frame in old['frames']:
            p = {**query, 'index': frame['index']}
            a = client.get('/api/image.png', params=p)
            b = client.get('/api/image.png', params={**p, 'target': alias, 'clean': True, 'smooth': True})
            assert a.status_code == b.status_code == 200 and a.content == b.content, (target,frame['index'])
        rows.append(dict(target=target,frames=len(old['frames']),data_versions=sorted({f['version'] for f in old['frames']}),status='PASS'))
report = dict(status='PASS',targets=rows,command=f'"{sys.executable}" scripts/verify_blink_parity.py',note='Software image identity check, not scientific validation.')
(ROOT/'results'/'blink-parity.json').write_text(json.dumps(report,indent=2),encoding='utf-8')
print(json.dumps(report))
