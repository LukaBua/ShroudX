"""Check exact source parity and exercise the original UI's independent API."""
from pathlib import Path

from fastapi.testclient import TestClient

import app
from frontend_compat import is_practice_request
from scripts.verify_frontend_copy import verify


def test_frontend_design_matches_saved_baseline():
    root = Path(__file__).resolve().parents[1]
    report = verify(root / 'frontend', root / 'tests/frontend_baseline.json')
    assert report['status'] == 'PASS', report['unexpected_differences']


def test_protocol_selection():
    assert is_practice_request(dict(path='/api/meta', query_string=b'clean=true'))
    assert is_practice_request(dict(path='/api/motion', headers=[(b'referer', b'http://localhost/?clean=true')]))
    assert not is_practice_request(dict(path='/api/meta', query_string=b'target=WISE0855'))
    assert not is_practice_request(dict(path='/api/tracking', query_string=b'clean=true'))


def test_original_ui_features_and_modern_api_coexist(tmp_path):
    application = app.create_app(mode='synthetic', cache_root=tmp_path / 'cache', results_root=tmp_path / 'results')
    with TestClient(application) as client:
        base = dict(ra=133.79, dec=-7.245, fov=120)
        modern = client.get('/api/meta', params=base)
        assert modern.status_code == 200 and 'image_shape' in modern.json()
        legacy = client.get('/api/meta', params={**base, 'clean': 'true'})
        assert legacy.status_code == 200
        assert legacy.json()['synthetic'] is True and legacy.json()['mock'] is True
        assert {'frame_shape', 'sky_grid', 'recommended_epochs', 'n_epochs'} <= legacy.json().keys()
        headers = {'X-ShroudX-Frontend': 'spaceapps'}
        for mode in ('blink', 'stack', 'diff'):
            png = client.get('/api/image.png', params={**base, 'mode': mode, 'index': 0, 'clean': True,
                                                     'smooth': True, 'show_mask': False})
            assert png.status_code == 200 and png.content.startswith(b'\x89PNG')
        mask = client.get('/api/image.png', params={**base, 'clean': False, 'show_mask': True})
        assert mask.status_code == 200
        for endpoint, keys in [('motion', {'measurements', 'pm_total', 'pm_total_err'}),
                               ('spectrum', {'points', 'n_points'}), ('detections', {'lobes', 'threshold'}),
                               ('pixel', {'quality', 'flux', 'variance', 'zodi'})]:
            response = client.get('/api/' + endpoint, params={**base, 'x': 5, 'y': 5}, headers=headers)
            assert response.status_code == 200, response.text
            assert keys <= response.json().keys()
            assert response.json()['synthetic'] is True
        catalog = client.get('/api/catalog', params=base, headers=headers)
        assert catalog.status_code == 200 and catalog.json()['sources'] == []
        # A second application with no data must not reuse the synthetic application's caches.
        with TestClient(app.create_app(mode='cache', cache_root=tmp_path / 'empty')) as other:
            missing = other.get('/api/meta', params={**base, 'clean': True})
            assert missing.status_code == 404


def test_blink_uses_shared_legacy_stretch(monkeypatch):
    import io
    import numpy as np
    from PIL import Image
    import processing
    from frontend_compat import app as original
    from frontend_compat.frame import Frame
    arrays = [np.arange(100, dtype=float).reshape(10, 10), np.arange(100, dtype=float).reshape(10, 10) + 100]
    frames = [Frame(a, np.ones_like(a), np.zeros_like(a, dtype=bool), 4.1, 5, 61000+i) for i,a in enumerate(arrays)]
    monkeypatch.setattr(original, 'selected_frames', lambda p: (frames, 5, 'test'))
    monkeypatch.setattr(original, '_group_cache_key', lambda p: ())
    stretch = processing.compute_stretch(arrays)
    with TestClient(original.app) as client:
        for i in range(2):
            result = client.get('/api/image.png', params=dict(ra=10, dec=20, mode='blink', index=i, clean=False, smooth=False))
            expected = Image.open(io.BytesIO(processing.render_png(arrays[i], stretch))).resize((512,512), Image.Resampling.NEAREST)
            actual = Image.open(io.BytesIO(result.content))
            np.testing.assert_array_equal(np.array(actual), np.array(expected))


def test_react_blink_is_byte_identical_to_legacy_pipeline(tmp_path):
    application = app.create_app(mode='synthetic', cache_root=tmp_path / 'cache')
    with TestClient(application) as client:
        query = dict(ra=133.79, dec=-7.245, fov=120, mode='blink')
        legacy = client.get('/api/meta', params=query).json()
        react = client.get('/api/meta', params={**query, 'clean': True, 'smooth': True}).json()
        assert react['frame_shape'] == legacy['image_shape']
        assert [(f['index'],f['mjd'],f['wavelength']) for f in react['frames']] == [
            (f['index'],f['mjd'],f['wavelength_um']) for f in legacy['frames']]
        for frame in legacy['frames']:
            plain = client.get('/api/image.png', params={**query, 'index': frame['index']})
            displayed = client.get('/api/image.png', params={**query, 'index': frame['index'], 'clean': True, 'smooth': True})
            assert displayed.status_code == plain.status_code == 200
            assert displayed.content == plain.content
