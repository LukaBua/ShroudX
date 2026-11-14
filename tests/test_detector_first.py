"""Known-answer tests ensure detector-first requests cannot hide missing bands."""
import numpy as np
import pytest
import data
from tests.test_tracking import frame


def records():
    return [data.ImageRecord(f'fixture://D{d}/{i}',t,f'SPHEREx-D{d}','SIA')
            for d,times in ((1,[60000,60001,60180]),(2,[60000,60001])) for i,t in enumerate(times)]


def test_manual_downloads_only_requested_detector_and_scopes_cache(monkeypatch,tmp_path):
    calls=[]
    def fetch(ra,dec,fov,**kwargs):
        if kwargs['mode']=='cache':raise data.NotCachedError('empty')
        calls.append(kwargs)
        return (frame().copy_with(detector=2),)
    monkeypatch.setattr(data,'get_frames',fetch)
    monkeypatch.setattr(data,'query_images',lambda *args:records())
    result=data.get_detector_frames(10,20,detector=2,mode='auto',cache_root=tmp_path)
    assert result[0].detector==2
    assert len(calls)==1 and calls[0]['cache_root']==tmp_path/'detector-2'
    assert {r.bandpass for r in calls[0]['image_records']}=={'SPHEREx-D2'}


def test_auto_ranks_metadata_and_explains_quality_fallback(monkeypatch,tmp_path):
    attempted=[]
    def fetch(ra,dec,fov,**kwargs):
        if kwargs['mode']=='cache':raise data.NotCachedError('empty')
        d=int(kwargs['cache_root'].name[-1]);attempted.append(d)
        f=frame().copy_with(detector=d)
        return (f.copy_with(mask=np.ones_like(f.mask)) if d==1 else f,)
    monkeypatch.setattr(data,'get_frames',fetch)
    monkeypatch.setattr(data,'query_images',lambda *args:records())
    result=data.get_detector_frames(10,20,mode='auto',cache_root=tmp_path)
    assert attempted==[1,2]
    assert result[0].detector==2
    assert 'no usable pixels' in result[0].meta['detector_selection_warning']


def test_all_band_cache_is_reused_without_discovery(monkeypatch,tmp_path):
    monkeypatch.setattr(data,'get_frames',lambda *args,**kw:(frame().copy_with(detector=1),frame().copy_with(detector=2)))
    monkeypatch.setattr(data,'query_images',lambda *args:pytest.fail('Should not query archive'))
    assert [f.detector for f in data.get_detector_frames(10,20,detector=2,mode='auto',cache_root=tmp_path)]==[2]


def test_subset_is_forwarded_to_download_jobs(monkeypatch,tmp_path):
    selected=[records()[0]]
    monkeypatch.setattr(data,'query_images',lambda *args,**kw:pytest.fail('Already discovered metadata'))
    def download(jobs,**kwargs):
        assert len(jobs)==1 and jobs[0][2] is selected[0]
        return [(jobs[0][1],selected[0])],[]
    monkeypatch.setattr(data,'download_cutouts_parallel',download)
    monkeypatch.setattr(data,'_load_frames_from_cache_dir',lambda *args,**kw:(frame(),))
    data._fetch_live_frames(10,20,120,120,tmp_path,max_workers=1,retries=1,backoff_base=0,
                           tap_query=None,fetcher=None,sleep_fn=lambda _:None,progress_callback=None,
                           image_records=selected)


def test_synthetic_never_queries_real_service(monkeypatch,tmp_path):
    monkeypatch.setattr(data,'query_images',lambda *args:pytest.fail('Real query in synthetic mode'))
    assert data.get_detector_frames(10,20,mode='synthetic',cache_root=tmp_path)


def test_completed_detector_cache_does_not_require_metadata_service(monkeypatch,tmp_path):
    folder=tmp_path/'detector-2'
    monkeypatch.setattr(data,'_find_cached_bucket',lambda root,*args:(folder,120,()) if root==folder else None)
    monkeypatch.setattr(data,'get_frames',lambda *args,**kwargs:(frame().copy_with(detector=2),))
    monkeypatch.setattr(data,'query_images',lambda *args:pytest.fail('Cached detector must work offline'))
    assert data.get_detector_frames(10,20,detector=2,mode='auto',cache_root=tmp_path)[0].detector==2
