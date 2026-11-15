"""Synthetic SIA tables verify real-service parsing and safe cutout-only access."""
import io
import ssl
import pytest
from astropy.table import Table
import data


def payload(rows=None):
    rows = rows if rows is not None else [
        ('https://irsa.ipac.caltech.edu/ibe/data/spherex/dr1/level2/fixture.fits',61000.,'SPHEREx-D3','image',2)]
    table=Table(rows=rows,names=['access_url','t_min','energy_bandpassname','dataproduct_type','calib_level'],
                dtype=['U200','f8','U30','U20','i4'])
    stream=io.BytesIO();table.write(stream,format='votable');return stream.getvalue()


def test_sia_discovers_paths_without_downloading_mefs(monkeypatch):
    calls=[]
    def fetch(url,timeout):
        calls.append((url,timeout));return payload()
    monkeypatch.setattr(data,'_default_fetch_bytes',fetch)
    records=data.query_images(10,20)
    assert len(calls)==1 and '/SIA?' in calls[0][0]
    assert 'spherex_dr1_deep' in calls[0][0] and 'spherex_qr3' in calls[0][0]
    assert calls[0][1]==30
    assert records[0].uri=='ibe/data/spherex/dr1/level2/fixture.fits'
    assert records[0].mjd==61000 and records[0].discovery_service=='SIA'
    assert data.build_cutout_url(records[0].uri,10,20,120).find('?center=')>0


def test_sia_rejects_empty_invalid_and_truncated_responses(monkeypatch):
    monkeypatch.setattr(data,'_default_fetch_bytes',lambda *args,**kw:payload([]))
    with pytest.raises(data.NoCoverageError):data.query_sia_images(10,20)
    monkeypatch.setattr(data,'_default_fetch_bytes',lambda *args,**kw:payload([
        ('https://other.example/full.fits',61000.,'SPHEREx-D3','image',2)]))
    with pytest.raises(data.ArchiveUnreachableError,match='unsupported archive URL'):data.query_sia_images(10,20)
    monkeypatch.setattr(data,'_default_fetch_bytes',lambda *args,**kw:b'<VOTABLE><INFO name="QUERY_STATUS" value="OVERFLOW">truncated</INFO></VOTABLE>')
    with pytest.raises(data.ArchiveUnreachableError,match='truncated'):data.query_sia_images(10,20)


def test_https_fetch_keeps_certificate_verification_enabled(monkeypatch):
    def open_url(request,timeout,context):
        assert context.check_hostname and context.verify_mode==ssl.CERT_REQUIRED
        assert timeout==7
        return io.BytesIO(b'bytes')
    monkeypatch.setattr(data,'urlopen',open_url)
    assert data._default_fetch_bytes('https://irsa.ipac.caltech.edu/example',timeout=7)==b'bytes'


def test_sia_discovery_provenance_survives_cache_headers(tmp_path,monkeypatch):
    from tests.test_data import _make_cutout_hdul
    monkeypatch.setattr(data,'_default_fetch_bytes',lambda *args,**kw:b'')
    path=tmp_path/'sample.fits.gz'
    assert data.download_cutout('https://example.test/cutout',path,fetcher=lambda url:_make_cutout_hdul(),
                               metadata=dict(uri='fixture://cutout',mjd=61000.,bandpass='SPHEREx-D5',discovery_service='SIA'))
    from astropy.io import fits
    with fits.open(path) as hdul:
        assert hdul['PRIMARY'].header['SPHXSVC']=='SIA'
        assert 'PSF' not in hdul


def test_interrupted_download_stays_marked_incomplete(tmp_path,monkeypatch):
    record=data.ImageRecord('ibe/data/spherex/dr1/level2/fixture.fits',61000.,'SPHEREx-D3','SIA')
    monkeypatch.setattr(data,'query_images',lambda *args,**kwargs:[record])
    def interrupted(jobs,**kwargs):
        folder=jobs[0][1].parent
        assert data._read_incomplete_marker(folder)==1
        raise RuntimeError('interrupted')
    monkeypatch.setattr(data,'download_cutouts_parallel',interrupted)
    with pytest.raises(RuntimeError,match='interrupted'):
        data._fetch_live_frames(10,20,120,120,tmp_path,max_workers=1,retries=1,
                               backoff_base=0,tap_query=None,fetcher=None,
                               sleep_fn=lambda _:None,progress_callback=None)
    assert data._read_incomplete_marker(tmp_path/data.cache_key_for(10,20,120))==1


def test_sia_ingestion_lag_is_disclosed_in_metadata(tmp_path,monkeypatch):
    import app
    from tests.test_tracking import frame
    from fastapi.testclient import TestClient
    exposure=frame().copy_with(uri='fixture://sia',meta={'discovery_service':'SIA','data_source':'cache'})
    monkeypatch.setattr(data,'get_frames',lambda *args,**kwargs:(exposure,))
    with TestClient(app.create_app(mode='cache',cache_root=tmp_path)) as client:
        response=client.get('/api/meta',params=dict(ra=359.999,dec=20,fov=120))
        assert response.status_code==200,response.text
        assert any('SIA' in w and 'one day' in w for w in response.json()['warnings'])


def test_partial_auto_cache_still_uses_irsa_concurrency_guard(tmp_path,monkeypatch):
    import app
    monkeypatch.setattr(data,'_find_cached_bucket',lambda *args:(tmp_path,120,()))
    assert not app._will_query_irsa('auto',tmp_path,10,20,120)
    data._write_incomplete_marker(tmp_path,1,2)
    assert app._will_query_irsa('auto',tmp_path,10,20,120)


def test_react_receives_clear_archive_errors(tmp_path,monkeypatch):
    import app
    from fastapi.testclient import TestClient
    def unavailable(*args,**kwargs):
        raise data.ArchiveUnreachableError('IRSA service unavailable during test')
    monkeypatch.setattr(data,'get_frames',unavailable)
    with TestClient(app.create_app(mode='auto',cache_root=tmp_path)) as client:
        result=client.get('/api/meta',params=dict(ra=10,dec=20,fov=120,mode='blink',clean=True))
        assert result.status_code==502
        assert 'IRSA service unavailable' in result.json()['detail']


def test_compat_uses_sia2_and_preserves_http_status_and_fuzzy_cache(tmp_path,monkeypatch):
    from pathlib import Path
    import app
    from fastapi.testclient import TestClient
    import frontend_compat
    import frontend_compat.data as fc_data

    # 1. frontend_compat.data.query_images uses SIA2 instead of legacy /TAP/sync
    sia_rec=data.ImageRecord('https://example.org/test.fits',60900.0,'SPHEREx-D2','SIA')
    monkeypatch.setattr(data,'query_sia_images',lambda ra,dec:[sia_rec])
    assert fc_data.query_images(346.62,-5.04)==[
        {'uri':'https://example.org/test.fits','mjd':60900.0,'bandpass':'SPHEREx-D2'}
    ]

    # 2. Compat practice middleware preserves 502 (archive) & 404 (no coverage) rather than 422
    monkeypatch.setattr(data,'get_detector_frames',lambda *a,**kw:(_ for _ in ()).throw(
        data.ArchiveUnreachableError('SIA unreachable during test')))
    with TestClient(app.create_app(mode='auto',cache_root=tmp_path)) as client:
        r502=client.get('/api/meta',params=dict(ra=10,dec=20,fov=280,mode='diff',clean=True,smooth=True,show_mask=False))
        assert r502.status_code==502

    monkeypatch.setattr(data,'get_detector_frames',lambda *a,**kw:(_ for _ in ()).throw(
        data.NoCoverageError('No SPHEREx coverage here')))
    with TestClient(app.create_app(mode='auto',cache_root=tmp_path)) as client:
        r404=client.get('/api/meta',params=dict(ra=10,dec=20,fov=280,mode='diff',clean=True,smooth=True,show_mask=False))
        assert r404.status_code==404

    # 3. Cached WISE0855 (folder ra133.7598 vs compat coord 133.7599) works offline in auto mode
    repo_cache=Path(__file__).resolve().parent.parent/'cache'
    if (repo_cache/'ra133.7598_dec-07.2422_s480').exists():
        monkeypatch.setattr(data,'get_detector_frames',lambda *a,**kw:pytest.fail('WISE0855 should hit local cache'))
        with TestClient(frontend_compat.create_practice_app(repo_cache,'auto')) as compat_client:
            ok=compat_client.get('/api/meta',params=dict(target='WISE0855',fov=240,mode='diff',clean=True,smooth=True,show_mask=False))
            assert ok.status_code==200,ok.text

    # 4. Mixed processing versions in selected frames do not crash /api/meta?mode=diff with 422
    synth=data._generate_synthetic_frames(111.85208,5.22579,280.0)
    mixed=tuple(
        f.copy_with(version=19 if i==0 else 20,meta={**(f.meta or {}),'synthetic':False,'data_source':'archive'})
        for i,f in enumerate(synth)
    )
    monkeypatch.setattr(data,'get_detector_frames',lambda *a,**kw:mixed)
    with TestClient(frontend_compat.create_practice_app(tmp_path,'auto')) as compat_client:
        mix_ok=compat_client.get('/api/meta',params=dict(ra=111.85208,dec=5.22579,fov=280,mode='diff',clean=True,smooth=True,show_mask=False))
        assert mix_ok.status_code==200,mix_ok.text
        assert mix_ok.json()['n_frames']>=2
