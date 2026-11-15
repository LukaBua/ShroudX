# AI-assisted: implemented with Codex.
"""Core API integration using generated FITS cutouts, never live archive calls."""

from __future__ import annotations

import copy
import io
import threading
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import numpy as np
import pytest
from fastapi.testclient import TestClient
from PIL import Image

import app as backend
import coords
import data
import processing
from tests.test_data import _make_cutout_hdul

POSITION = {"ra": "133.76", "dec": "-7.24"}


def test_difference_api_delegates_processing_and_preserves_metadata(api, monkeypatch):
    from types import SimpleNamespace

    client, calls, _ = api
    selected = []
    rendered = []
    warning = "Mean wavelengths differ; a colour change can mimic or hide motion."
    diff = np.linspace(-4, 8, 400).reshape(20, 20)
    renderer = processing.render_diff_png

    def difference(frames, epoch_a=None, epoch_b=None):
        selected.append((frames, epoch_a, epoch_b))
        return SimpleNamespace(diff=diff, warnings=[warning])

    def render(array, limit=None):
        rendered.append((array, limit))
        return renderer(array, limit=limit)

    monkeypatch.setattr(processing, "difference_images", difference)
    monkeypatch.setattr(processing, "render_diff_png", render)
    original = client.get("/api/meta", params=POSITION).json()
    query = {**POSITION, "mode": "diff", "epoch_a": 1, "epoch_b": 0}
    response = client.get("/api/meta", params=query)
    assert response.status_code == 200, response.text
    assert warning in response.json()["warnings"]
    assert response.json()["stretch"] == original["stretch"]
    assert warning not in client.get("/api/meta", params=POSITION).json()["warnings"]
    response = client.get("/api/image.png", params=query)
    assert response.status_code == 200, response.text
    assert response.headers["content-type"] == "image/png"
    expected_limit = np.percentile(abs(diff), processing.DEFAULT_DIFF_PERCENTILE)
    assert float(response.headers["x-diff-limit"]) == pytest.approx(expected_limit)
    assert rendered[0][0] is diff
    assert rendered[0][1] == pytest.approx(expected_limit)
    with Image.open(io.BytesIO(response.content)) as image:
        assert list(reversed(image.size)) == original["image_shape"]
    assert all((a, b) == (1, 0) for _, a, b in selected)
    assert all(f.zodi is None and f.wcs is not None for f in selected[0][0])
    client.get("/api/image.png", params={**POSITION, "mode": "diff"})
    assert selected[-1][1:] == (0, 1)
    assert len(calls) == 1


@pytest.mark.parametrize("extra", [
    {"epoch_a": "abc"}, {"epoch_a": "1.5"}, {"epoch_a": -1},
    {"epoch_b": 20}, {"epoch_a": 0, "epoch_b": 0},
])
def test_difference_invalid_epochs(api, extra):
    client, _, _ = api
    for route in ("/api/meta", "/api/image.png"):
        response = client.get(route, params={**POSITION, "mode": "diff", **extra})
        assert response.status_code == 400
        assert response.json()["error"]["code"] == "bad_input"


def test_difference_insufficient_epochs_and_no_overlap(api, frames, monkeypatch):
    from types import SimpleNamespace

    client, _, application = api
    monkeypatch.setattr(data, "get_frames", lambda *a, **k: frames[:2])
    for route in ("/api/meta", "/api/image.png"):
        response = client.get(route, params={**POSITION, "mode": "diff"})
        assert response.status_code == 422
        assert response.json()["error"]["code"] == "insufficient_data"
    application.state.products.entries.clear()
    monkeypatch.setattr(data, "get_frames", lambda *a, **k: frames)
    monkeypatch.setattr(processing, "difference_images", lambda *a, **k:
                        SimpleNamespace(diff=np.full((20, 20), np.nan), warnings=[]))
    assert client.get("/api/image.png", params={**POSITION, "mode": "diff"}).status_code == 422


def test_difference_real_processing_warning_and_synthetic_label(tmp_path):
    with TestClient(backend.create_app(mode="synthetic", cache_root=tmp_path)) as client:
        query = {**POSITION, "mode": "diff", "wmin": 4.0, "wmax": 4.4}
        with pytest.warns(UserWarning, match="colour change"):
            response = client.get("/api/meta", params=query)
        assert response.status_code == 200, response.text
        assert any("colour change" in w for w in response.json()["warnings"])
        with pytest.warns(UserWarning, match="colour change"):
            image = client.get("/api/image.png", params=query)
        assert image.status_code == 200
        assert image.headers["x-synthetic"] == "true"


def test_blink_gif_frames_stretch_captions_and_headers(api):
    client, calls, _ = api
    meta = client.get("/api/meta", params=POSITION).json()
    response = client.get("/api/blink.gif", params={**POSITION, "speed": 125, "mode": "stack"})
    assert response.status_code == 200, response.text
    assert response.headers["content-type"] == "image/gif"
    assert int(response.headers["x-gif-size-bytes"]) == len(response.content) < 10_000_000
    assert response.headers["x-synthetic"] == "false"
    assert "attachment" in response.headers["content-disposition"]
    with Image.open(io.BytesIO(response.content)) as gif:
        assert gif.n_frames == meta["n_frames"]
        assert gif.info["loop"] == 0
        captions = []
        for i in range(gif.n_frames):
            gif.seek(i)
            assert gif.info["duration"] == 130
            png = client.get("/api/image.png", params={**POSITION, "index": i})
            with Image.open(io.BytesIO(png.content)) as image:
                left = (gif.width - image.width) // 2
                np.testing.assert_array_equal(
                    np.asarray(gif.convert("L").crop((left, 0, left + image.width, image.height))),
                    np.asarray(image))
                captions.append(np.asarray(gif.convert("L"))[image.height:].copy())
        assert not np.array_equal(captions[0], captions[1])
    assert len(calls) == 1


@pytest.mark.parametrize("speed", ["49", "2001", "nan", "50.5", ""])
def test_blink_gif_invalid_speed(api, speed):
    client, calls, _ = api
    response = client.get("/api/blink.gif", params={**POSITION, "speed": speed})
    assert response.status_code == 400
    assert response.json()["error"]["code"] == "bad_input"
    assert not calls


def test_blink_gif_default_speed_and_identical_images(api, monkeypatch):
    client, _, _ = api
    product = backend.build_products(backend.Selection(
        backend.Position(None, "133.76", "-7.24"), 120, None, None, None),
        "auto", backend.ROOT / "cache")
    product.blink = (product.blink[0],) * len(product.blink)
    monkeypatch.setattr(backend, "build_products", lambda *a: product)
    response = client.get("/api/blink.gif", params=POSITION)
    with Image.open(io.BytesIO(response.content)) as gif:
        assert gif.n_frames == len(product.blink)
        assert gif.info["duration"] == 500


def test_blink_gif_downscales_without_dropping_frames(api, monkeypatch):
    client, _, _ = api
    rng = np.random.default_rng(2026)
    pngs = []
    for _ in range(4):
        buf = io.BytesIO()
        Image.fromarray(rng.integers(0, 256, (256, 256), dtype=np.uint8)).save(buf, format="PNG")
        pngs.append(buf.getvalue())
    product = backend.Products({"synthetic": False, "data_source": "cache", "frames": [
        {"date": "2026-01-01", "wavelength_um": 4.1} for _ in pngs]}, tuple(pngs), pngs[0])
    monkeypatch.setattr(backend, "build_products", lambda *a: product)
    monkeypatch.setattr(backend, "GIF_MAX_BYTES", 60_000)
    response = client.get("/api/blink.gif", params=POSITION)
    assert response.status_code == 200, response.text
    assert len(response.content) < 60_000
    assert int(response.headers["x-gif-size-bytes"]) == len(response.content)
    with Image.open(io.BytesIO(response.content)) as gif:
        assert gif.n_frames == 4
        assert gif.height < 256 + 30


def test_blink_gif_standard_error_and_synthetic_provenance(api, monkeypatch, tmp_path):
    client, _, _ = api
    def no_coverage(*a, **k):
        raise data.NoCoverageError("No SPHEREx coverage at this position.")
    original = data.get_frames
    monkeypatch.setattr(data, "get_frames", no_coverage)
    response = client.get("/api/blink.gif", params=POSITION)
    assert response.status_code == 404
    assert response.json()["error"]["code"] == "no_coverage"
    monkeypatch.setattr(data, "get_frames", original)
    # The API mode controls provenance independently of a mocked loader.
    with TestClient(backend.create_app(mode="synthetic", cache_root=tmp_path)) as synthetic:
        response = synthetic.get("/api/blink.gif", params=POSITION)
        assert response.status_code == 200
        assert response.headers["x-synthetic"] == "true"


@pytest.fixture
def frames():
    yy, xx = np.mgrid[:20, :20]
    result = []
    for i, mjd in enumerate([60900.5, 60901.5, 61080.5, 61081.5]):
        image = 10 + (30 + 5 * i) * np.exp(-((xx - 10)**2 + (yy - 12)**2) / 3)
        with _make_cutout_hdul(mjd=mjd, image=image, w0_um=4.10 + i * .01) as hdul:
            frame = data.frame_from_hdul(hdul, 133.76, -7.24, mjd, uri="l2b-v20/test.fits")
        frame.meta.update(data_source="archive", synthetic=False)
        result.append(frame)
    return tuple(result)


@pytest.fixture
def api(monkeypatch, tmp_path, frames):
    calls = []

    def get_frames(ra, dec, fov, **kwargs):
        calls.append((ra, dec, fov, kwargs))
        return copy.deepcopy(frames)

    monkeypatch.setattr(data, "get_frames", get_frames)
    application = backend.create_app(mode="auto", cache_root=tmp_path)
    with TestClient(application, raise_server_exceptions=False) as client:
        yield client, calls, application


def test_metadata_images_shared_stretch_and_cache(api, monkeypatch):
    client, calls, _ = api
    stretches = []
    render = processing.render_png

    def capture(image, stretch, **kwargs):
        stretches.append(dict(stretch))
        return render(image, stretch, **kwargs)

    monkeypatch.setattr(processing, "render_png", capture)
    response = client.get("/api/meta", params=POSITION)
    assert response.status_code == 200, response.text
    meta = response.json()
    assert meta["n_frames"] == 4
    assert meta["image_shape"] == [20, 20]
    assert meta["detector"] == 5
    assert meta["data_source"] == "archive"
    assert meta["synthetic"] is False
    assert meta["target"] == {"name": None, "ra": 133.76, "dec": -7.24,
                              "source": "coordinates", "pm_known": False}
    assert [f["epoch"] for f in meta["frames"]] == [0, 0, 1, 1]
    assert all(f["version"] == 20 for f in meta["frames"])
    assert [ep["n_frames"] for ep in meta["epochs"]] == [2, 2]
    assert set(meta["detector_ranges_um"]) == set("123456")
    assert len(stretches) == 5  # Four frames and the stack, one shared stretch.
    assert all(s == meta["stretch"] for s in stretches)
    for i in range(4):
        response = client.get("/api/image.png", params={**POSITION, "index": i})
        assert response.status_code == 200
        assert response.headers["content-type"] == "image/png"
        with Image.open(io.BytesIO(response.content)) as image:
            assert image.size == (20, 20)
            # The source is north of centre in FITS and above centre in the PNG.
            assert np.unravel_index(np.argmax(np.asarray(image)), (20, 20))[0] < 10
    response = client.get(
        "/api/image.png",
        params={**POSITION, "mode": "stack", "index": "ignored"},
    )
    assert response.status_code == 200
    assert response.headers["x-synthetic"] == "false"
    assert len(calls) == 1
    assert client.get("/api/meta", params=POSITION).json()["data_source"] == "cache"
    assert len(stretches) == 5


@pytest.mark.parametrize("params", [
    {}, {"ra": "12"}, {"dec": "1"}, {"target": "x", **POSITION},
    {**POSITION, "fov": "29"}, {**POSITION, "fov": "601"},
    {**POSITION, "fov": "nan"}, {**POSITION, "fov": "inf"},
    {**POSITION, "detector": "7"}, {**POSITION, "detector": "1.5"},
    {**POSITION, "wmin": "4"}, {**POSITION, "wmax": "4"},
    {**POSITION, "wmin": "5", "wmax": "4"},
    {**POSITION, "wmin": "nan", "wmax": "4"},
    {**POSITION, "wmin": "0", "wmax": "4"},
    {**POSITION, "mode": "unknown"},
    {**POSITION, "ra": "360"}, {**POSITION, "dec": "91"},
    {**POSITION, "ra": ""}, {"target": " "},
])
def test_invalid_inputs_return_400(api, params):
    client, calls, _ = api
    response = client.get("/api/meta", params=params)
    assert response.status_code == 400, response.text
    assert response.json()["error"]["code"] == "bad_input"
    assert isinstance(response.json()["error"]["detail"], dict)
    assert not calls


def test_duplicate_parameters_and_image_indices(api):
    client, calls, _ = api
    assert client.get("/api/meta?ra=12&ra=13&dec=1").status_code == 400
    for extra in ({}, {"index": "-1"}, {"index": "1.5"}):
        response = client.get("/api/image.png", params={**POSITION, **extra})
        assert response.status_code == 400
    assert not calls
    response = client.get("/api/image.png", params={**POSITION, "index": "100"})
    assert response.status_code == 400
    assert response.headers["content-type"] == "application/json"


def test_resolve_coordinates_and_name_adapter(api, monkeypatch):
    client, _, _ = api
    result = client.get("/api/resolve", params={"ra": "08 00 00", "dec": "-07 30 00"})
    assert result.status_code == 200
    assert result.json()["ra"] == pytest.approx(120)
    assert result.json()["dec"] == pytest.approx(-7.5)
    monkeypatch.setattr(coords, "resolve_name", lambda name: coords.ResolvedTarget(
        133.76, -7.24, "offline", True, ["Position propagated to the observation epoch."]))
    monkeypatch.setitem(coords.OFFLINE_TARGETS, "WISE 0855-0714", coords.OfflineEntry(
        133.76, -7.24, 2000, 0, 2016, "cold brown dwarf"))
    resolved = client.get("/api/resolve", params={"target": "WISE 0855-0714"}).json()
    assert resolved["source"] == "offline_table"
    assert resolved["suggested_fov_arcsec"] == 300
    assert resolved["suggested_detector"] == 5
    assert resolved["pm_known"] is True
    metadata = client.get("/api/meta", params={"target": "WISE 0855-0714"}).json()
    assert metadata["fov_arcsec"] == 300
    override = client.get("/api/meta", params={"target": "WISE 0855-0714", "fov": 120}).json()
    assert override["fov_arcsec"] == 120
    assert any("too small" in warning for warning in override["warnings"])


@pytest.mark.parametrize("exception,status,code", [
    (data.NoCoverageError("No coverage."), 404, "no_coverage"),
    (data.NotCachedError("Position not cached."), 404, "not_cached"),
    (data.ArchiveUnreachableError("Archive unavailable."), 502, "archive_unreachable"),
    (RuntimeError("private credentials must not escape"), 500, "internal_error"),
])
def test_data_errors_are_safe_json_without_fallback(api, monkeypatch, exception, status, code):
    client, _, _ = api
    def fail(*args, **kwargs):
        raise exception
    monkeypatch.setattr(data, "get_frames", fail)
    for route in ("/api/meta", "/api/image.png"):
        response = client.get(route, params={**POSITION, "index": 0})
        assert response.status_code == status
        assert response.json()["error"]["code"] == code
        assert "private credentials" not in response.text


def test_unknown_target(api, monkeypatch):
    client, calls, _ = api
    def fail(name):
        raise ValueError("Target is unknown and has no offline coordinates.")
    monkeypatch.setattr(coords, "resolve_name", fail)
    response = client.get("/api/resolve", params={"target": "unknown"})
    assert response.status_code == 404
    assert response.json()["error"]["code"] == "unknown_target"
    assert not calls


def test_empty_selection_and_all_masked_pixels(api, monkeypatch, frames):
    client, _, _ = api
    response = client.get("/api/meta", params={**POSITION, "detector": 1})
    assert response.status_code == 422
    assert response.json()["error"]["code"] == "insufficient_data"
    for frame in frames:
        frame.mask[:] = True
    monkeypatch.setattr(data, "get_frames", lambda *a, **k: frames)
    assert client.get("/api/meta", params=POSITION).status_code == 422


def test_partial_downloads_and_cache_key_separation(api, frames):
    client, calls, _ = api
    frames[0].meta["failed_downloads"] = 2
    result = client.get("/api/meta", params=POSITION).json()
    assert any("2 cutout downloads failed" in w for w in result["warnings"])
    for extra in ({"fov": 60}, {"detector": 5}, {"wmin": 4.1, "wmax": 4.13}):
        assert client.get("/api/meta", params={**POSITION, **extra}).status_code == 200
    assert len(calls) == 4


def test_synthetic_mode_end_to_end_and_frozen_configuration(tmp_path, monkeypatch):
    data.clear_memory_cache()
    application = backend.create_app(mode="synthetic", cache_root=tmp_path)
    monkeypatch.setenv("SPHX_MODE", "live")
    with TestClient(application) as client:
        for route in ("/health", "/api/resolve", "/api/meta"):
            response = client.get(route, params=POSITION)
            assert response.status_code == 200, response.text
            assert response.json()["synthetic"] is True
        metadata = client.get("/api/meta", params=POSITION).json()
        assert metadata["data_source"] == "synthetic"
        assert any("SYNTHETIC DATA" in w for w in metadata["warnings"])
        assert client.get("/api/meta").json()["synthetic"] is True
        image = client.get("/api/image.png", params={**POSITION, "index": 0})
        assert image.status_code == 200
        assert image.headers["x-synthetic"] == "true"
    assert not list(tmp_path.iterdir())
    data.clear_memory_cache()


def test_accidental_synthetic_frames_rejected(api, frames):
    client, _, _ = api
    frames[0].meta["synthetic"] = True
    response = client.get("/api/meta", params=POSITION)
    assert response.status_code == 500
    assert response.json()["error"]["code"] == "internal_error"


def test_static_health_and_no_cors(api, tmp_path):
    client, _, _ = api
    (tmp_path / "example.bin").write_bytes(b"1234")
    assert client.get("/health").json()["cache_size_mb"] == 4 / 1_000_000
    assert client.get("/").status_code == 200
    assert "SHROUDX" in client.get("/legacy").text
    assert client.get("/static/index.html").status_code == 200
    response = client.get("/health", headers={"Origin": "https://example.org"})
    assert "access-control-allow-origin" not in response.headers
    assert client.get("/api/missing").status_code == 404
    assert "error" in client.get("/api/missing").json()


def test_slow_pipeline_does_not_block_health(api, monkeypatch, frames):
    client, _, _ = api
    entered, release = threading.Event(), threading.Event()
    def slow_frames(*args, **kwargs):
        entered.set()
        assert release.wait(timeout=10)
        return frames
    monkeypatch.setattr(data, "get_frames", slow_frames)
    with ThreadPoolExecutor(max_workers=1) as pool:
        pending = pool.submit(client.get, "/api/meta", params=POSITION)
        try:
            assert entered.wait(timeout=5)
            assert client.get("/health").status_code == 200
            assert not pending.done()
        finally:
            release.set()
        assert pending.result(timeout=10).status_code == 200


def test_product_cache_expiry_and_eviction(monkeypatch):
    clock = [0.0]
    monkeypatch.setattr(backend.time, "monotonic", lambda: clock[0])
    cache = backend.ProductCache(max_entries=1, ttl=10, max_bytes=20)
    builds = []
    def build():
        builds.append(1)
        return backend.Products({}, (b"png",), b"stack")
    assert cache.get("a", build)[1] is False
    assert cache.get("a", build)[1] is True
    cache.get("b", build)
    assert cache.get("a", build)[1] is False
    clock[0] = 11
    assert cache.get("a", build)[1] is False
    assert len(builds) == 4


def test_product_cache_hits_are_not_blocked_by_a_slow_build():
    import threading
    import time

    cache = backend.ProductCache()
    cache.get("fast", lambda: backend.Products({}, (b"p",), b"s"))
    started, release = threading.Event(), threading.Event()

    def slow_build():
        started.set()
        release.wait(5)
        return backend.Products({}, (b"p",), b"s")

    worker = threading.Thread(target=lambda: cache.get("slow", slow_build))
    worker.start()
    try:
        assert started.wait(5)
        began = time.monotonic()
        assert cache.get("fast", lambda: 1 / 0)[1] is True  # served while "slow" is building
        assert time.monotonic() - began < 1.0
    finally:
        release.set()
        worker.join(5)


def test_product_cache_waiters_reuse_a_build_for_the_same_key():
    import threading

    cache = backend.ProductCache()
    builds, results = [], []
    started, release = threading.Event(), threading.Event()

    def build():
        builds.append(1)
        started.set()
        release.wait(5)
        return backend.Products({}, (b"p",), b"s")

    def request():
        results.append(cache.get("k", build)[1])

    threads = [threading.Thread(target=request) for _ in range(3)]
    threads[0].start()
    assert started.wait(5)
    for t in threads[1:]:
        t.start()
    release.set()
    for t in threads:
        t.join(5)
    assert len(builds) == 1
    assert sorted(results) == [False, True, True]


def test_build_products_pads_cutout_fetch_fov_to_preserve_rotated_corners(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    import math

    from astropy.wcs import WCS

    angle = math.radians(45.0)
    cos_a, sin_a = math.cos(angle), math.sin(angle)
    ny, nx = 78, 78  # 480" bucket at 6.15"/px
    scale_deg = 6.15 / 3600.0
    rot_wcs = WCS(naxis=2)
    rot_wcs.wcs.ctype = ["RA---TAN", "DEC--TAN"]
    rot_wcs.wcs.crval = [133.76, -7.24]
    rot_wcs.wcs.crpix = [(nx + 1) / 2.0, (ny + 1) / 2.0]
    rot_wcs.wcs.cdelt = [-scale_deg, scale_deg]
    rot_wcs.wcs.pc = [[cos_a, -sin_a], [sin_a, cos_a]]
    rot_wcs.wcs.cunit = ["deg", "deg"]
    rot_wcs.wcs.radesys = "ICRS"
    rot_hdr = rot_wcs.to_header()

    seen_fovs = []

    def fake_bucket(cache_root, ra, dec, fov):
        return (tmp_path, 480, ())

    def fake_get_frames(ra, dec, fov, **kwargs):
        seen_fovs.append(fov)
        out = []
        for idx, mjd in enumerate((60900.0, 61080.0)):
            hdul = _make_cutout_hdul(ny=ny, nx=nx, ra=ra, dec=dec, mjd=mjd, uri=f"u{idx}.fits")
            hdul["IMAGE"].header.update(rot_hdr)
            frame = data.frame_from_hdul(
                hdul,
                ra=ra,
                dec=dec,
                mjd=mjd,
                uri=f"u{idx}.fits",
                bandpass="SPHEREx-D5",
            )
            frame = data._crop_frame_to_fov(frame, ra=ra, dec=dec, fov_arcsec=fov)
            out.append(frame)
        return tuple(out)

    monkeypatch.setattr(data, "_find_cached_bucket", fake_bucket)
    monkeypatch.setattr(data, "get_frames", fake_get_frames)

    sel = backend.Selection(
        backend.Position(None, "133.76", "-7.24"),
        fov=300,
        detector=5,
        wmin=None,
        wmax=None,
    )
    prod = backend.build_products(sel, "cache", tmp_path)

    assert len(seen_fovs) == 1
    assert seen_fovs[0] == pytest.approx(450.0)
    assert prod.metadata["fov_arcsec"] == 300
    assert prod.metadata["image_shape"] == [49, 49]
    # Without rotation padding, all four corners would be masked; with 450" fetch, none are masked
    assert int(np.sum(prod.aligned[0].mask)) == 0


def test_build_products_stabilizes_blink_pedestal_keeps_scientific_aligned(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    ny, nx = 48, 48  # 300" padded bucket for 120" output FOV (20x20 crop)
    yy, xx = np.mgrid[0:ny, 0:nx]
    star = 120.0 * np.exp(-0.5 * (((xx - 23.5) ** 2 + (yy - 23.5) ** 2) / 1.2**2))
    inner = (np.abs(xx - 23.5) <= 10.5) & (np.abs(yy - 23.5) <= 10.5)

    def fake_bucket(cache_root, ra, dec, fov):
        return (tmp_path, 300, ())

    def fake_get_frames(ra, dec, fov, **kwargs):
        out = []
        for idx, (mjd, inner_pedestal) in enumerate(((60900.0, 0.0), (61080.0, 8.0))):
            hdul = _make_cutout_hdul(
                ny=ny, nx=nx, ra=ra, dec=dec, mjd=mjd, uri=f"ped{idx}.fits"
            )
            img = 10.0 + star + np.where(inner, inner_pedestal, 0.0)
            hdul["IMAGE"].data = img.astype(np.float32)
            hdul["ZODI"].data = np.zeros((ny, nx), dtype=np.float32)
            frame = data.frame_from_hdul(
                hdul,
                ra=ra,
                dec=dec,
                mjd=mjd,
                uri=f"ped{idx}.fits",
                bandpass="SPHEREx-D5",
            )
            out.append(frame)
        return tuple(out)

    monkeypatch.setattr(data, "_find_cached_bucket", fake_bucket)
    monkeypatch.setattr(data, "get_frames", fake_get_frames)

    sel = backend.Selection(
        backend.Position(None, "133.76", "-7.24"),
        fov=120,
        detector=5,
        wmin=None,
        wmax=None,
    )
    prod = backend.build_products(sel, "cache", tmp_path)

    # Scientific aligned frames keep outer-calibrated flux offsets (>6 MJy/sr difference).
    sci_meds = [float(np.median(f.image[~f.mask])) for f in prod.aligned]
    assert sci_meds[1] - sci_meds[0] > 6.0

    # Rendered blink PNGs harmonize sky pedestal so frames do not flicker.
    pngs = [np.array(Image.open(io.BytesIO(b)), dtype=np.float64) for b in prod.blink]
    assert abs(float(np.median(pngs[1])) - float(np.median(pngs[0]))) < 3.0
