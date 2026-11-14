# AI-assisted: written with AI pair-programming assistant.
"""End-to-end integration tests running the full pipeline on synthetic FITS fixtures."""

from __future__ import annotations

import io
from pathlib import Path

import numpy as np
import pytest
from astropy.coordinates import SkyCoord
from astropy.coordinates.name_resolve import NameResolveError
from astropy.io import fits
from astropy.table import Table
from astropy.wcs import WCS
from fastapi.testclient import TestClient
from PIL import Image

import app as backend
import coords
import data
import processing
from flags import FLAG_BITS

WISE_RA_HMS = "08h55m10.831s"
WISE_DEC_DMS = "-07d14m42.53s"


def _flag_bit(name: str) -> int:
    """Return the integer bit value for a named SPHEREx flag."""
    return 1 << FLAG_BITS[name]


def _build_celestial_wcs(
    *,
    ra: float,
    dec: float,
    ny: int,
    nx: int,
    angle_deg: float = 0.0,
    pixel_scale_arcsec: float = data.PIXEL_SCALE_ARCSEC,
) -> WCS:
    """Construct a 2D celestial TAN WCS centered on (ra, dec)."""
    scale_deg = pixel_scale_arcsec / 3600.0
    theta = np.deg2rad(angle_deg)
    cos_t = float(np.cos(theta))
    sin_t = float(np.sin(theta))

    wcs = WCS(naxis=2)
    wcs.wcs.ctype = ["RA---TAN", "DEC--TAN"]
    wcs.wcs.crval = [ra, dec]
    wcs.wcs.crpix = [(nx + 1) / 2.0, (ny + 1) / 2.0]
    wcs.wcs.cdelt = [-scale_deg, scale_deg]
    wcs.wcs.pc = [[cos_t, -sin_t], [sin_t, cos_t]]
    wcs.wcs.cunit = ["deg", "deg"]
    wcs.wcs.radesys = "ICRS"
    return wcs


def _gaussian_2d(
    ny: int,
    nx: int,
    x0: float,
    y0: float,
    amplitude: float,
    sigma_px: float = 1.1,
) -> np.ndarray:
    """Evaluate a 2D Gaussian profile on a (ny, nx) pixel grid."""
    yy, xx = np.mgrid[0:ny, 0:nx]
    return amplitude * np.exp(-0.5 * (((xx - x0) ** 2 + (yy - y0) ** 2) / (sigma_px**2)))


def _make_synthetic_cutout_bytes(
    *,
    ra_center: float,
    dec_center: float,
    ny: int,
    nx: int,
    detector: int,
    mjd: float,
    wavelength_um: float,
    uri: str,
    mover_sky: tuple[float, float],
    star_sky: tuple[float, float],
    angle_deg: float = 0.0,
    zodi_base: float = 12.0,
    bg_level: float = 3.5,
    inject_hot_pixel: tuple[int, int] | None = None,
    inject_overflow_pixel: tuple[int, int] | None = None,
    seed: int = 0,
) -> bytes:
    """Build a serialized multi-extension SPHEREx cutout FITS file with PSF included."""
    cel_wcs = _build_celestial_wcs(
        ra=ra_center,
        dec=dec_center,
        ny=ny,
        nx=nx,
        angle_deg=angle_deg,
    )

    mx, my = cel_wcs.world_to_pixel_values(mover_sky[0], mover_sky[1])
    sx, sy = cel_wcs.world_to_pixel_values(star_sky[0], star_sky[1])
    mx_f, my_f = float(mx), float(my)
    sx_f, sy_f = float(sx), float(sy)

    yy, xx = np.mgrid[0:ny, 0:nx]
    zodi = (zodi_base + 0.08 * yy - 0.04 * xx).astype(np.float32)
    variance = np.full((ny, nx), 0.04, dtype=np.float32)

    rng = np.random.default_rng(seed)
    noise = rng.normal(0.0, 0.2, size=(ny, nx))
    star_flux = 60.0 * ((4.10 / wavelength_um) ** 2)
    mover_flux = 45.0
    sky_signal = (
        _gaussian_2d(ny, nx, mx_f, my_f, mover_flux)
        + _gaussian_2d(ny, nx, sx_f, sy_f, star_flux)
        + bg_level
        + noise
    )
    image = (zodi + sky_signal).astype(np.float32)

    flags_arr = np.zeros((ny, nx), dtype=np.int32)
    # Mark SOURCE bit (21) on pixels near both astronomical objects; must never be masked.
    near_mover = ((xx - mx_f) ** 2 + (yy - my_f) ** 2) <= 4.0
    near_star = ((xx - sx_f) ** 2 + (yy - sy_f) ** 2) <= 4.0
    flags_arr[near_mover | near_star] |= _flag_bit("SOURCE") | _flag_bit("FULLSAMPLE")

    if inject_hot_pixel is not None:
        hy, hx = inject_hot_pixel
        flags_arr[hy, hx] |= _flag_bit("HOT")
        image[hy, hx] = 50000.0

    if inject_overflow_pixel is not None:
        oy, ox = inject_overflow_pixel
        flags_arr[oy, ox] |= _flag_bit("OVERFLOW")

    pri_hdu = fits.PrimaryHDU()
    pri_hdu.header["MJD-OBS"] = mjd
    pri_hdu.header["DETECTOR"] = detector
    pri_hdu.header["BANDPASS"] = f"SPHEREx-D{detector}"
    with data.warnings_suppressed():
        pri_hdu.header["SPHXURI"] = uri

    img_hdr = cel_wcs.to_header()
    img_hdr["DETECTOR"] = detector
    img_hdr["MJD-OBS"] = mjd
    img_hdr["BUNIT"] = "MJy/sr"
    img_hdu = fits.ImageHDU(data=image, header=img_hdr, name="IMAGE")

    var_hdr = cel_wcs.to_header()
    var_hdr["BUNIT"] = "(MJy/sr)^2"
    var_hdu = fits.ImageHDU(data=variance, header=var_hdr, name="VARIANCE")

    flg_hdr = cel_wcs.to_header()
    for name, bit in FLAG_BITS.items():
        key = f"MP_{name}"
        flg_hdr[f"HIERARCH {key}" if len(key) > 8 else key] = bit
    flg_hdu = fits.ImageHDU(data=flags_arr, header=flg_hdr, name="FLAGS")

    zodi_hdr = cel_wcs.to_header()
    zodi_hdr["BUNIT"] = "MJy/sr"
    zodi_hdu = fits.ImageHDU(data=zodi, header=zodi_hdr, name="ZODI")

    cx, cy = (nx - 1) / 2.0, (ny - 1) / 2.0
    wave_grid = (wavelength_um + 0.002 * (xx - cx) + 0.001 * (yy - cy)).astype(np.float32)
    wave_hdr = fits.Header()
    wave_hdr["WCSAXESW"] = 2
    wave_hdr["CRPIX1W"] = (nx + 1) / 2.0
    wave_hdr["CRPIX2W"] = (ny + 1) / 2.0
    wave_hdr["CRVAL1W"] = wavelength_um
    wave_hdr["CRVAL2W"] = 0.0
    wave_hdr["CD1_1W"] = 0.002
    wave_hdr["CD1_2W"] = 0.001
    wave_hdr["CD2_1W"] = 0.0
    wave_hdr["CD2_2W"] = 1.0
    wave_hdr["CTYPE1W"] = "WAVE"
    wave_hdr["CTYPE2W"] = "LINEAR"
    wave_hdr["CUNIT1W"] = "um"
    wave_hdr["CUNIT2W"] = "pix"
    wave_hdu = fits.ImageHDU(data=wave_grid, header=wave_hdr, name="WCS-WAVE")

    psf_hdu = fits.ImageHDU(data=np.ones((9, 9), dtype=np.float32), name="PSF")

    # Place IMAGE after other extensions to confirm named-HDU reading (never index-based).
    hdul = fits.HDUList([pri_hdu, psf_hdu, flg_hdu, zodi_hdu, var_hdu, wave_hdu, img_hdu])
    buf = io.BytesIO()
    with data.warnings_suppressed():
        hdul.writeto(buf, output_verify="silentfix")
    return buf.getvalue()


@pytest.fixture(autouse=True)
def _clean_env_and_memory_cache(monkeypatch: pytest.MonkeyPatch) -> None:
    """Reset in-process data cache and SPHX_MODE around every integration test."""
    data.clear_memory_cache()
    monkeypatch.delenv("SPHX_MODE", raising=False)


def test_full_pipeline_from_coordinates_to_api_png(tmp_path: Path) -> None:
    """Run coords -> get_frames -> group -> clean -> epochs -> align -> stack -> API PNG."""
    ra_deg, dec_deg = coords.parse_position(WISE_RA_HMS, WISE_DEC_DMS)
    assert ra_deg == pytest.approx(133.79512917, abs=1e-6)
    assert dec_deg == pytest.approx(-7.24514722, abs=1e-6)

    scale_deg = data.PIXEL_SCALE_ARCSEC / 3600.0
    cos_dec = float(np.cos(np.deg2rad(dec_deg)))

    # Stationary star placed 4 px East (+RA) and 4 px North (+Dec) of field center.
    star_sky = (ra_deg + (4.0 * scale_deg) / cos_dec, dec_deg + 4.0 * scale_deg)
    # Moving source shifts by ~3.5 px West (-RA) and ~3.0 px North (+Dec) between epochs.
    mover_ep0 = (ra_deg + (1.5 * scale_deg) / cos_dec, dec_deg - 1.5 * scale_deg)
    mover_ep1 = (ra_deg - (2.0 * scale_deg) / cos_dec, dec_deg + 1.5 * scale_deg)

    specs = [
        ("spherex/l2b-v20/d5_ep0_a.fits", 60900.2, "SPHEREx-D5", 5, 4.10, mover_ep0, 0.0, 1),
        ("spherex/l2b-v20/d5_ep0_b.fits", 60901.5, "SPHEREx-D5", 5, 4.13, mover_ep0, 8.0, 2),
        ("spherex/l2b-v20/d2_extra.fits", 60902.0, "SPHEREx-D2", 2, 1.35, mover_ep0, -5.0, 3),
        ("spherex/l2b-v20/d5_ep1_a.fits", 61082.4, "SPHEREx-D5", 5, 4.11, mover_ep1, -8.0, 4),
        ("spherex/l2b-v20/d5_ep1_b.fits", 61083.8, "SPHEREx-D5", 5, 4.14, mover_ep1, 4.0, 5),
        ("spherex/l2b-v20/d5_outwin.fits", 61084.5, "SPHEREx-D5", 5, 4.38, mover_ep1, 0.0, 6),
    ]

    tap_table = Table(
        rows=[(uri, mjd, band) for uri, mjd, band, *_ in specs],
        names=("uri", "mjd", "bandpass"),
    )
    cutout_blobs: dict[str, bytes] = {}
    for idx, (uri, mjd, _band, det, wl, mover_pos, angle, seed) in enumerate(specs):
        cutout_blobs[uri] = _make_synthetic_cutout_bytes(
            ra_center=ra_deg,
            dec_center=dec_deg,
            ny=24,
            nx=24,
            detector=det,
            mjd=mjd,
            wavelength_um=wl,
            uri=uri,
            mover_sky=mover_pos,
            star_sky=star_sky,
            angle_deg=angle,
            inject_hot_pixel=(3, 3) if idx == 0 else None,
            inject_overflow_pixel=(5, 5) if idx == 0 else None,
            seed=100 + seed,
        )

    def fake_fetcher(url: str) -> bytes:
        for uri, blob in cutout_blobs.items():
            if uri in url:
                return blob
        raise FileNotFoundError(f"Unexpected cutout URL: {url}")

    frames = data.get_frames(
        ra_deg,
        dec_deg,
        fov=120.0,
        mode="live",
        cache_root=tmp_path,
        tap_query=lambda _q: tap_table,
        fetcher=fake_fetcher,
    )
    assert len(frames) == 6
    assert [f.mjd for f in frames] == sorted(f.mjd for f in frames)
    assert all(f.version == 20 for f in frames)
    assert all(f.meta is not None and f.meta["synthetic"] is False for f in frames)
    assert all(f.meta is not None and f.meta["data_source"] == "archive" for f in frames)

    # Verify PSF was stripped on disk, HOT pixel (3, 3) masked, OVERFLOW (5, 5) unreliable.
    cached_fits = sorted(tmp_path.glob("ra*_dec*_s120/*.fits"))
    assert len(cached_fits) == 6
    with fits.open(cached_fits[0]) as saved_hdul:
        assert "PSF" not in [h.name for h in saved_hdul]
    assert frames[0].image.shape == (24, 24)
    assert bool(frames[0].mask[3, 3]) is True
    assert bool(frames[0].unreliable is not None and frames[0].unreliable[5, 5]) is True
    assert not np.all(frames[0].mask)

    # Group frames: selects detector 5 and the narrow multi-epoch window [4.10, 4.14] um.
    grouped = processing.group_frames(frames)
    assert grouped.detector == 5
    assert len(grouped.frames) == 4
    assert grouped.wavelength_window[1] - grouped.wavelength_window[0] <= 0.10 + 1e-6

    # Clean frames: subtract ZODI and background, inpaint hot pixel while keeping mask intact.
    cleaned = tuple(processing.clean_frame(f) for f in grouped.frames)
    assert all(f.zodi is None for f in cleaned)
    assert all(f.meta is not None and f.meta["zodi_subtracted"] is True for f in cleaned)
    assert bool(cleaned[0].mask[3, 3]) is True
    assert float(cleaned[0].image[3, 3]) < 20.0
    assert abs(float(cleaned[0].meta["background_level"]) - 3.5) < 0.25

    # Split epochs across the ~180 day revisit gap.
    epochs = processing.split_epochs(cleaned)
    assert len(epochs) == 2
    assert [ep.n_frames for ep in epochs] == [2, 2]

    # Align all cleaned frames onto a North-up, East-left TAN grid.
    aligned = processing.align_frames(
        cleaned,
        target_ra=ra_deg,
        target_dec=dec_deg,
        fov_arcsec=120.0,
    )
    assert len(aligned) == 4
    assert aligned[0].wcs is not None
    assert aligned[0].wcs.wcs.cdelt[0] < 0
    assert aligned[0].wcs.wcs.cdelt[1] > 0

    # Stack full sequence and verify per-epoch stacks reveal the mover shift.
    full_stack = processing.stack_frames(aligned)
    assert full_stack.image.shape == aligned[0].image.shape
    assert np.any(full_stack.n_frames == 4)

    aligned_epochs = processing.split_epochs(aligned)
    ep0_stack = processing.stack_frames(aligned_epochs[0].frames)
    ep1_stack = processing.stack_frames(aligned_epochs[1].frames)

    # Mask out the bright stationary star quadrant to isolate the moving source peak in each epoch.
    sx_out, sy_out = aligned[0].wcs.celestial.world_to_pixel_values(star_sky[0], star_sky[1])
    yy_o, xx_o = np.mgrid[0 : aligned[0].image.shape[0], 0 : aligned[0].image.shape[1]]
    not_star = ((xx_o - float(sx_out)) ** 2 + (yy_o - float(sy_out)) ** 2) > 9.0
    ep0_y, ep0_x = np.unravel_index(
        int(np.nanargmax(np.where(not_star, ep0_stack.image, -np.inf))),
        ep0_stack.image.shape,
    )
    ep1_y, ep1_x = np.unravel_index(
        int(np.nanargmax(np.where(not_star, ep1_stack.image, -np.inf))),
        ep1_stack.image.shape,
    )
    assert np.hypot(ep1_x - ep0_x, ep1_y - ep0_y) >= 3.0

    # Direct PNG rendering matches aligned dimensions.
    stretch = processing.compute_stretch(aligned)
    direct_png = processing.render_png(aligned[0], stretch)
    assert direct_png[:8] == b"\x89PNG\r\n\x1a\n"

    # Now serve the exact cached cutouts through the FastAPI app without mocking get_frames.
    data.clear_memory_cache()
    application = backend.create_app(mode="cache", cache_root=tmp_path)
    with TestClient(application) as client:
        health = client.get("/health")
        assert health.status_code == 200
        health_json = health.json()
        assert health_json["status"] == "ok"
        assert health_json["data_mode"] == "cache"
        assert health_json["cache_size_mb"] > 0.0
        assert "synthetic" not in health_json

        pos_params = {"ra": WISE_RA_HMS, "dec": WISE_DEC_DMS}
        res_resp = client.get("/api/resolve", params=pos_params)
        assert res_resp.status_code == 200
        res_json = res_resp.json()
        assert res_json["ra"] == pytest.approx(ra_deg, abs=1e-6)
        assert res_json["dec"] == pytest.approx(dec_deg, abs=1e-6)
        assert res_json["source"] == "coordinates"

        meta_resp = client.get("/api/meta", params={**pos_params, "fov": "120"})
        assert meta_resp.status_code == 200, meta_resp.text
        meta = meta_resp.json()
        assert meta["synthetic"] is False
        assert meta["data_source"] == "cache"
        assert meta["detector"] == 5
        assert meta["n_frames"] == 4
        assert [ep["n_frames"] for ep in meta["epochs"]] == [2, 2]
        assert meta["image_shape"] == list(aligned[0].image.shape)
        assert meta["stretch"] == pytest.approx(dict(stretch))

        for idx in range(meta["n_frames"]):
            img_resp = client.get(
                "/api/image.png",
                params={**pos_params, "fov": "120", "mode": "blink", "index": str(idx)},
            )
            assert img_resp.status_code == 200
            assert img_resp.headers["content-type"] == "image/png"
            assert img_resp.headers["x-data-source"] == "cache"
            assert img_resp.headers["x-synthetic"] == "false"
            with Image.open(io.BytesIO(img_resp.content)) as im:
                assert im.size == (meta["image_shape"][1], meta["image_shape"][0])

        stack_resp = client.get(
            "/api/image.png",
            params={**pos_params, "fov": "120", "mode": "stack"},
        )
        assert stack_resp.status_code == 200
        assert stack_resp.headers["content-type"] == "image/png"
        with Image.open(io.BytesIO(stack_resp.content)) as im:
            assert im.size == (meta["image_shape"][1], meta["image_shape"][0])


def test_offline_target_name_end_to_end_through_api(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Resolve WISE 0855 offline with PM propagation, crop 480->300 arcsec, and render PNG."""

    def _no_network(_name: str) -> SkyCoord:
        raise NameResolveError("Online name resolution disabled for integration test")

    monkeypatch.setattr(SkyCoord, "from_name", _no_network)

    resolved = coords.resolve_name("WISE 0855-0714", target_epoch=2026.0)
    assert resolved.source == "offline"
    assert resolved.pm_known is True

    # Populate a 480" cache bucket (~78x78 px) at the propagated 2026.0 position.
    specs = [
        ("spherex/l2b-v20/wise_ep0_a.fits", 60900.1, 4.12),
        ("spherex/l2b-v20/wise_ep0_b.fits", 60901.3, 4.14),
        ("spherex/l2b-v20/wise_ep1_a.fits", 61085.2, 4.13),
    ]
    tap_table = Table(
        rows=[(uri, mjd, "SPHEREx-D5") for uri, mjd, _ in specs],
        names=("uri", "mjd", "bandpass"),
    )
    blobs = {
        uri: _make_synthetic_cutout_bytes(
            ra_center=resolved.ra,
            dec_center=resolved.dec,
            ny=78,
            nx=78,
            detector=5,
            mjd=mjd,
            wavelength_um=wl,
            uri=uri,
            mover_sky=(resolved.ra, resolved.dec),
            star_sky=(resolved.ra + 0.01, resolved.dec + 0.01),
            seed=50 + i,
        )
        for i, (uri, mjd, wl) in enumerate(specs)
    }

    def fake_fetcher(url: str) -> bytes:
        for uri, blob in blobs.items():
            if uri in url:
                return blob
        raise FileNotFoundError(url)

    # Fetch at 300" (stores 480" cache bucket on disk).
    data.get_frames(
        resolved.ra,
        resolved.dec,
        fov=300.0,
        mode="live",
        cache_root=tmp_path,
        tap_query=lambda _q: tap_table,
        fetcher=fake_fetcher,
    )
    assert len(list(tmp_path.glob("ra*_dec*_s480/*.fits"))) == 3

    data.clear_memory_cache()
    application = backend.create_app(mode="cache", cache_root=tmp_path)
    with TestClient(application) as client:
        res_json = client.get("/api/resolve", params={"target": "WISE 0855-0714"}).json()
        assert res_json["source"] == "offline_table"
        assert res_json["pm_known"] is True
        assert res_json["suggested_fov_arcsec"] == 300
        assert res_json["suggested_detector"] == 5

        meta_resp = client.get("/api/meta", params={"target": "WISE 0855-0714"})
        assert meta_resp.status_code == 200, meta_resp.text
        meta = meta_resp.json()
        assert meta["synthetic"] is False
        assert meta["data_source"] == "cache"
        assert meta["fov_arcsec"] == 300
        assert meta["detector"] == 5
        assert meta["n_frames"] == 3
        assert any("proper-motion" in w.lower() for w in meta["warnings"])

        expected_npix = int(np.round(300.0 / data.PIXEL_SCALE_ARCSEC))
        assert meta["image_shape"] == [expected_npix, expected_npix]

        stack_resp = client.get(
            "/api/image.png",
            params={"target": "WISE 0855-0714", "mode": "stack"},
        )
        assert stack_resp.status_code == 200
        with Image.open(io.BytesIO(stack_resp.content)) as im:
            assert im.size == (expected_npix, expected_npix)
