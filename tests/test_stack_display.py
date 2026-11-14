"""Known-answer missing-data display tests; scientific stack values must stay unchanged."""
import io
import numpy as np
from PIL import Image
import processing


def test_display_fill_uses_known_neighbor_median_and_keeps_fits_orientation():
    image = np.array([[1., np.nan], [3., 4.]])
    variance = np.array([[1., np.nan], [1., 1.]])
    count = np.array([[2,0], [2,2]])
    stack = processing.StackResult(image.copy(), variance.copy(), count.copy())
    stretch = processing.compute_stretch(image)
    expected_image = np.array([[1., 3.], [3., 4.]])
    expected = np.array(Image.open(io.BytesIO(processing.render_png(expected_image, stretch))))
    actual = np.array(Image.open(io.BytesIO(processing.render_stack_png(stack, stretch))))
    np.testing.assert_array_equal(actual[0], expected[0])
    np.testing.assert_array_equal(actual[1,0], expected[1,0])
    np.testing.assert_array_equal(actual, expected)
    np.testing.assert_array_equal(stack.image, image)
    np.testing.assert_array_equal(stack.variance, variance)
    np.testing.assert_array_equal(stack.n_frames, count)


def test_complete_stack_is_byte_identical_to_normal_render():
    image = np.arange(9.).reshape(3,3)
    stack = processing.StackResult(image, np.ones_like(image), np.ones_like(image,dtype=int))
    stretch = processing.compute_stretch(image)
    assert processing.render_stack_png(stack,stretch) == processing.render_png(image,stretch)


def test_stack_quality_warnings_are_not_attached_to_blink(monkeypatch, tmp_path):
    import app as backend
    from fastapi.testclient import TestClient
    quality = dict(missing_pixels=1, target_position_missing=True, unreliable_exposures=True)
    product = backend.Products(dict(data_source='cache', warnings=[], stack_quality=quality), (), b'png')
    monkeypatch.setattr(backend, 'build_products', lambda *args, **kwargs: product)
    with TestClient(backend.create_app(mode='cache',cache_root=tmp_path)) as client:
        query = dict(ra=10,dec=20,fov=120)
        stack = client.get('/api/meta',params={**query,'mode':'stack'}).json()
        assert len(stack['warnings']) == 3
        assert 'display only' in stack['warnings'][0]
        assert 'cannot be recovered' in stack['warnings'][1]
        assert client.get('/api/meta',params={**query,'mode':'blink'}).json()['warnings'] == []


def test_zero_contributor_finite_value_is_not_used_as_a_measurement():
    image = np.array([[1., 999.], [3., 4.]])
    stack = processing.StackResult(image.copy(), np.ones_like(image), np.array([[1,0],[1,1]]))
    stretch = dict(mode='linear',vmin=0.,vmax=4.)
    expected = processing.render_png(np.array([[1.,3.],[3.,4.]]), stretch)
    assert processing.render_stack_png(stack,stretch) == expected
    assert stack.image[0,1] == 999.
