"""Derive catalogue test expectations only from the team's cited benchmark CSV.

This does not measure motion or validate source identity. CSV uncertainties are
not supplied and are recorded as null, never invented. Availability is evidenced
by actual cached SPHEREx indexes, not fabricated archive entries.
"""
import csv,json,math,sys
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
import coords,data,processing
aliases={'WISE J085510.83-071442.5':'WISE 0855-0714','eps Ind B':'Epsilon Indi B'}
rows=[]
with (ROOT/'data/benchmarks.csv').open(newline='') as stream:
    for row in csv.DictReader(stream):
        name=aliases.get(row['name'],row['name'])
        target=coords.resolve_name(name)
        hit=data._find_cached_bucket(ROOT/'cache',target.ra,target.dec,120)
        assert hit is not None, 'Availability is not verified: '+name
        pm=math.hypot(float(row['pmra']),float(row['pmdec']))/1000
        rows.append(dict(name=name,pm_arcsec_per_year=pm,pixels_per_year=pm/processing.PIXEL_SCALE_ARCSEC,
                         catalogue_sigma=None,citation=row['citation'],archive_uri=hit[2][0][3]))
report=dict(command=f'"{sys.executable}" scripts/test_star_expectations.py',targets=rows,
            limitation='Catalogue uncertainties are absent from benchmarks.csv; no measured motion is claimed.')
(ROOT/'results'/'test-star-expectations.json').write_text(json.dumps(report,indent=2),encoding='utf-8')
for row in rows: print(row['name'],round(row['pm_arcsec_per_year'],3),round(row['pixels_per_year'],3),row['citation'])
