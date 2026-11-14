"""PASS requires real Vega auto selection to download only detector-scoped cutouts.

The all-band cache must remain absent, manual reuse of the selected detector must
work without adding another detector, and the resulting core Stack must render.
A fresh cache directory is used. This checks transport/cache behavior, not science.
"""
import json,sys,uuid
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
import app,coords,data
from fastapi.testclient import TestClient

root=ROOT/'scratch'/('detector-first-'+uuid.uuid4().hex[:10])
report=dict(status='FAIL',cache_root=str(root),command=f'"{sys.executable}" scripts/verify_detector_first.py')
print('Real detector-first test: '+str(root),flush=True)
try:
    with TestClient(app.create_app(mode='auto',cache_root=root,request_timeout=1800)) as client:
        q=dict(target='Vega',fov=120,mode='stack')
        response=client.get('/api/meta',params=q)
        assert response.status_code==200,response.text
        m=response.json();det=m['detector']
        assert not m['synthetic']
        assert len(list(root.glob('detector-*')))==1
        assert data._find_cached_bucket(root,m['target']['ra'],m['target']['dec'],120) is None
        frames=data.get_detector_frames(m['target']['ra'],m['target']['dec'],120,detector=det,mode='cache',cache_root=root)
        assert frames and {f.detector for f in frames}=={det}
        png=client.get('/api/image.png',params={**q,'detector':det})
        assert png.status_code==200 and png.content.startswith(b'\x89PNG')
        report.update(status='PASS',detector=det,n_selected_frames=m['n_frames'],n_downloaded_frames=len(frames),
                      data_versions=sorted({f.version for f in frames}),warnings=m['warnings'])
except Exception as e:report['error']=str(e)
(ROOT/'results'/'detector-first-verification.json').write_text(json.dumps(report,indent=2),encoding='utf-8')
print(json.dumps(report),flush=True)
raise SystemExit(0 if report['status']=='PASS' else 1)
