"""Real auto-mode gate: cold Vega metadata, real PNGs, persistent cache and offline reuse.

PASS requires no complete cache before the request, non-synthetic input
frames with source URIs and versions, PNG rendering, and successful cache-only
reload after clearing memory. Any exception, HTTP failure or synthetic response
fails. The website must not be switched on a failing report. No benchmark or
scientific accuracy claim is made. A fresh cache directory is used by default; --resume-cache explicitly resumes interrupted real downloads.
"""
import hashlib
import argparse
import json
import sys
import uuid
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from fastapi.testclient import TestClient
import app,coords,data


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--resume-cache',type=Path)
    args=parser.parse_args()
    cache=args.resume_cache.resolve() if args.resume_cache else ROOT/'scratch'/('auto-vega-'+uuid.uuid4().hex[:10])
    target=coords.resolve_name('Vega')
    reused=0
    if args.resume_cache:
        records=data.query_images(target.ra,target.dec)
        from astropy.io import fits
        expected={r.uri for r in records}
        found=set()
        for file in cache.glob('*/*.fits.gz'):
            with fits.open(file,memmap=False) as hdul:
                found.add(hdul['PRIMARY'].header.get('SPHXURI',''))
        reused=len(found & expected)
        folder=cache/data.cache_key_for(target.ra,target.dec,120)
        data._write_incomplete_marker(folder,len(expected-found),len(expected))
    report=dict(status='FAIL',target='Vega',cache_root=str(cache),initially_cached=bool(reused),initially_complete_cached=False,
                request_timeout_seconds=1800,reused_downloads=reused,command=f'"{sys.executable}" scripts/verify_auto_mode.py')
    print('Cold Vega test cache: '+str(cache),flush=True)
    try:
        hit=data._find_cached_bucket(cache,target.ra,target.dec,120)
        assert hit is None or data._read_incomplete_marker(hit[0])>0, 'Require a cold or incomplete cache'
        query=dict(target='Vega',fov=120,mode='blink')
        with TestClient(app.create_app(mode='auto',cache_root=cache,request_timeout=1800),raise_server_exceptions=False) as client:
            response=client.get('/api/meta',params=query)
            report['metadata_http_status']=response.status_code
            assert response.status_code==200, response.text
            meta=response.json()
            assert meta['synthetic'] is False and meta['data_source']=='archive'
            assert meta['frames'] and all(f['uri'] and f['version'] is not None for f in meta['frames'])
            report.update(n_selected_frames=meta['n_frames'],data_versions=sorted({f['version'] for f in meta['frames']}),
                          warnings=meta['warnings'],frames=meta['frames'])
            images={}
            for mode in ('blink','stack'):
                result=client.get('/api/image.png',params={**query,'mode':mode,'index':0})
                assert result.status_code==200 and result.content.startswith(b'\x89PNG'),result.text[:300]
                assert result.headers['X-Synthetic']=='false'
                images[mode]=hashlib.sha256(result.content).hexdigest()
            report['png_sha256']=images
        data.clear_memory_cache()
        frames=data.get_frames(target.ra,target.dec,120,mode='cache',cache_root=cache)
        assert frames and all(f.wcs is not None and not f.meta.get('synthetic',False) for f in frames)
        report.update(status='PASS',n_cached_frames=len(frames),offline_cache_reload=True,
                      all_data_versions=sorted({f.version for f in frames}))
    except Exception as exc:
        report['error']=str(exc)
    output=ROOT/'results'/'auto-mode-verification.json'
    output.write_text(json.dumps(report,indent=2),encoding='utf-8')
    print(json.dumps({k:v for k,v in report.items() if k!='frames'}),flush=True)
    return 0 if report['status']=='PASS' else 1

if __name__=='__main__':
    raise SystemExit(main())
