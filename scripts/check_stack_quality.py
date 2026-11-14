"""Record real Stack missing-data quality without reconstructing missing flux.

PASS requires zero-contributor pixels to remain non-finite in the scientific
coadd and to be identified in metadata. Reports missing data as a limitation,
not as a scientific detection or failed measurement. No parameters are tuned.
"""
import json
import sys
from pathlib import Path
import numpy as np
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
import app

rows=[]
for target in ('R Draconis', "Barnard's Star"):
    selection=app.Selection(app.Position(target,None,None),210,None,None,None)
    product=app.build_products(selection,'cache',ROOT/'cache')
    stack=app.processing.stack_frames(product.aligned)
    missing=stack.n_frames==0
    assert np.all(~np.isfinite(stack.image[missing]))
    assert int(missing.sum()) == product.metadata['stack_quality']['missing_pixels']
    resolved,_,raw=app.fetch_selection_frames(selection,'cache',ROOT/'cache')
    detectors={}
    for frame in raw:
        x,y=frame.wcs.celestial.world_to_pixel_values(resolved['ra'],resolved['dec'])
        x,y=int(round(float(x))),int(round(float(y)))
        if not (0<=x<frame.image.shape[1] and 0<=y<frame.image.shape[0]):continue
        row=detectors.setdefault(str(frame.detector),dict(exposures=0,usable_target_pixels=0,reliable_target_pixels=0))
        row['exposures']+=1
        valid=not frame.mask[y,x] and np.isfinite(frame.image[y,x]) and np.isfinite(frame.variance[y,x]) and frame.variance[y,x]>0
        row['usable_target_pixels']+=int(valid)
        row['reliable_target_pixels']+=int(valid and (frame.unreliable is None or not frame.unreliable[y,x]))
    rows.append(dict(target=target,fov_arcsec=210,detector=product.metadata['detector'],
                     data_versions=sorted({f.version for f in raw}),stack_quality=product.metadata['stack_quality'],
                     detectors=detectors,inputs=[dict(uri=f.uri,mjd=f.mjd,version=f.version) for f in raw]))
report=dict(status='PASS',command=f'"{sys.executable}" scripts/check_stack_quality.py',data_mode='cache',targets=rows,
            limitation='Missing or unreliable target-core measurements cannot be recovered from the cached selection.')
(ROOT/'results'/'stack-quality.json').write_text(json.dumps(report,indent=2),encoding='utf-8')
print(json.dumps({**report,'targets':[{k:v for k,v in r.items() if k!='inputs'} for r in rows]}))
