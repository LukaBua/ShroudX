"""Exercise the exact practice frontend's protocol using current real cached frames.

PASS requires non-mock metadata and valid Blink, Stack and Difference PNGs for
all five targets. Pixel inspection must return 200. Optional motion/spectrum/
catalogue/detection responses must return 200 or a clear 404/422 limitation;
limitations are reported explicitly, never treated as a scientific detection.
Any 500, invented data, missing cache or malformed image fails this check.
This is an interface compatibility check, not a scientific validation.
"""
from __future__ import annotations

import io
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from fastapi.testclient import TestClient
from PIL import Image

import app

TARGETS = ('WISE0855', 'Barnard', 'EpsilonIndiB', 'RWAurigae', 'RDraconis')


def main() -> int:
    rows = []
    with TestClient(app.create_app(mode='cache'), headers={'X-ShroudX-Frontend': 'spaceapps'}) as client:
        for name in TARGETS:
            row = dict(target=name, status='FAIL')
            try:
                query = dict(target=name, fov=240)
                response = client.get('/api/meta', params=query)
                assert response.status_code == 200, response.text
                meta = response.json()
                assert not meta['mock'] and not meta['synthetic'] and meta['n_frames'] > 0
                row.update(frames=meta['n_frames'], epochs=meta['n_epochs'], data_versions=meta['data_versions'])
                row['images'] = {}
                for mode in ('blink', 'stack', 'diff'):
                    response = client.get('/api/image.png', params={**query, 'mode': mode, 'index': 0,
                                                                  'clean': True, 'smooth': True})
                    assert response.status_code == 200, response.text
                    with Image.open(io.BytesIO(response.content)) as image:
                        image.load()
                        assert image.format == 'PNG' and image.size == (512, 512)
                        row['images'][mode] = dict(size=list(image.size), bytes=len(response.content))
                row['resources'] = {}
                for endpoint in ('pixel', 'motion', 'spectrum', 'catalog', 'detections'):
                    response = client.get('/api/' + endpoint, params={**query, 'x': 5, 'y': 5, 'index': 0})
                    expected = (200,) if endpoint == 'pixel' else (200, 404, 422)
                    assert response.status_code in expected, response.text
                    row['resources'][endpoint] = dict(status=response.status_code)
                    if response.status_code != 200:
                        row['resources'][endpoint]['limitation'] = response.text
                    else:
                        assert response.json()['synthetic'] is False
                row['status'] = 'PASS'
            except Exception as exc:
                row['error'] = str(exc)
            rows.append(row)
            print(json.dumps(row))
    report = dict(status='PASS' if all(r['status'] == 'PASS' for r in rows) else 'FAIL', targets=rows,
                  command=f'"{sys.executable}" scripts/check_frontend_compat.py', mode='cache')
    output = ROOT / 'results' / 'frontend-compat-api-check.json'
    output.parent.mkdir(exist_ok=True)
    output.write_text(json.dumps(report, indent=2), encoding='utf-8')
    return 0 if report['status'] == 'PASS' else 1


if __name__ == '__main__':
    raise SystemExit(main())
