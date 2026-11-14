"""PASS requires byte-identical core/React Stack PNGs and matching input frame metadata.

Checks all five cached targets at 180 and 240 arcseconds. No scientific accuracy
or performance claim follows from this software parity check. Results include
commands, versions and image hashes; no hand-edited scientific results are used.
"""
import hashlib
import json
import sys
from pathlib import Path
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from fastapi.testclient import TestClient
import app


def main():
    rows = []
    with TestClient(app.create_app(mode='cache')) as client:
        for target, alias in (("WISE 0855-0714", "WISE0855"), ("Barnard's Star", "Barnard"),
                              ("Epsilon Indi B", "EpsilonIndiB"), ("RW Aurigae", "RWAurigae"), ("R Draconis", "RDraconis")):
            for fov in (180,240):
                row = dict(target=target, fov=fov, status='FAIL')
                try:
                    query = dict(target=target, fov=fov, mode='stack')
                    react_query = {**query, 'target': alias, 'clean': True, 'smooth': True}
                    a = client.get('/api/meta', params=query)
                    b = client.get('/api/meta', params=react_query)
                    assert a.status_code == b.status_code == 200, (a.text,b.text)
                    core, react = a.json(), b.json()
                    assert react['frame_shape'] == core['image_shape']
                    fields = ('index','mjd','version','epoch')
                    assert [tuple(f[k] for k in fields) for f in core['frames']] == [tuple(f[k] for k in fields) for f in react['frames']]
                    a = client.get('/api/image.png', params=query)
                    b = client.get('/api/image.png', params=react_query)
                    assert a.status_code == b.status_code == 200 and a.content == b.content
                    row.update(status='PASS', frames=core['n_frames'], data_versions=sorted({f['version'] for f in core['frames']}),
                               png_sha256=hashlib.sha256(a.content).hexdigest())
                except Exception as exc:
                    row['error'] = str(exc)
                rows.append(row)
    report = dict(status='PASS' if all(r['status']=='PASS' for r in rows) else 'FAIL', cases=rows,
                  command=f'"{sys.executable}" scripts/verify_stack_parity.py', data_mode='cache')
    (ROOT/'results'/'stack-parity.json').write_text(json.dumps(report,indent=2),encoding='utf-8')
    print(json.dumps(report))
    return 0 if report['status']=='PASS' else 1

if __name__ == '__main__':
    raise SystemExit(main())
