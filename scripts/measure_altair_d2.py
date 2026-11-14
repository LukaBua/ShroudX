"""PASS: auto-mode cold Altair D2 metadata and every blink PNG succeed, real D2 only.
Timings use perf_counter resolution as clock uncertainty; single-run network variability
is unmeasured. Measures HTTP transfer and PNG decode, excluding browser paint.
"""
import io,json,time,sys
from pathlib import Path
import requests
from PIL import Image
ROOT=Path(__file__).resolve().parents[1]
q=dict(target='Altair',detector=2,fov=120,mode='blink',clean='true',smooth='true',show_mask='false')
r=dict(command=f'"{sys.executable}" scripts/measure_altair_d2.py',status='FAIL',parameters=q,clock_resolution_seconds=time.get_clock_info('perf_counter').resolution,limitation='Single run; network variability unknown. HTTP plus PNG decode, not browser paint.')
def save():
 (ROOT/'results'/'altair-d2-timing.json').write_text(json.dumps(r,indent=2),encoding='utf-8')
t=time.perf_counter()
try:
 h=requests.get('http://127.0.0.1:8001/health').json();assert h['data_mode']=='auto'
 assert not list((ROOT/'cache').rglob('ra297.6958_dec+08.8683*')), 'Altair already cached; refusing cold measurement'
 t=time.perf_counter(); x=requests.get('http://127.0.0.1:8001/api/meta',params=q,timeout=1800);x.raise_for_status();m=x.json()
 r['metadata_ready_seconds']=time.perf_counter()-t
 r.update(n_frames=m['n_frames'],warnings=m.get('warnings',[]),data_versions=m.get('data_versions'),metadata=m)
 save();print('Metadata ready: '+str(r['metadata_ready_seconds'])+'s; '+str(m['n_frames'])+' frames',flush=True)
 assert not m.get('mock',m.get('synthetic',True)) and m['detector']==2
 for i in range(m['n_frames']):
  p=requests.get('http://127.0.0.1:8001/api/image.png',params={**q,'index':i},timeout=1800);p.raise_for_status()
  Image.open(io.BytesIO(p.content)).load()
  if i==0:r['first_png_decoded_seconds']=time.perf_counter()-t
 r.update(status='PASS',all_blink_pngs_decoded_seconds=time.perf_counter()-t)
except Exception as e:r['error']=str(e)
save();print(json.dumps({k:v for k,v in r.items() if k!='metadata'}),flush=True)
