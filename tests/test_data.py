# AI-assisted: written with AI pair-programming assistant.
"""Unit tests for SPHEREx IRSA data retrieval, caching, and Frame construction (data.py)."""

from __future__ import annotations

import io
from pathlib import Path
from typing import Any

import numpy as np
import pytest
from astropy.io import fits
from astropy.table import Table
from astropy.wcs import WCS

import data
from data import (
    ArchiveUnreachableError,
    ImageRecord,
    NoCoverageError,
    NotCachedError,
    build_cutout_url,
    cache_key_for,
    clear_memory_cache,
    download_cutout,
    download_cutouts_parallel,
    frame_from_hdul,
    get_data_mode,
    get_frames,
    parse_version,
    query_images,
    read_or_rebuild_index,
    rebuild_cache_index,
    select_size_bucket,
)
from flags import FLAG_BITS
from scripts import fetch_cutouts


def _bit(name: str) -> int:
    return 1 << FLAG_BITS[name]


def _make_cutout_hdul(
    *,
    ny: int = 20,
    nx: int = 20,
    ra: float = 133.76,
    dec: float = -7.24,
    pixel_scale_arcsec: float = 6.15,
    detector: int = 5,
    mjd: float = 60900.5,
    w0_um: float = 4.10,
    dw_dx_um: float = 0.002,
    dw_dy_um: float = 0.001,
    include_psf: bool = True,
    scramble_order: bool = False,
    image: np.ndarray | None = None,
    variance: np.ndarray | None = None,
    flags_arr: np.ndarray | None = None,
    zodi: np.ndarray | None = None,
    uri: str = "ibe/data/spherex/l2b-v20/2025/08/level2_20250813_det5_l2b-v20.fits",
) -> fits.HDUList:
    """Build an in-memory multi-extension FITS HDUList mimicking an IRSA SPHEREx cutout."""
    scale_deg = pixel_scale_arcsec / 3600.0
    cel_wcs = WCS(naxis=2)
    cel_wcs.wcs.ctype = ["RA---TAN", "DEC--TAN"]
    cel_wcs.wcs.crval = [ra, dec]
    cel_wcs.wcs.crpix = [(nx + 1) / 2.0, (ny + 1) / 2.0]
    cel_wcs.wcs.cdelt = [-scale_deg, scale_deg]
    cel_wcs.wcs.cunit = ["deg", "deg"]
    cel_wcs.wcs.radesys = "ICRS"

    pri_hdu = fits.PrimaryHDU()
    pri_hdu.header["MJD-OBS"] = mjd
    pri_hdu.header["DETECTOR"] = detector
    pri_hdu.header["BANDPASS"] = f"SPHEREx-D{detector}"
    with data.warnings_suppressed():
        pri_hdu.header["SPHXURI"] = uri

    img_data = (
        np.full((ny, nx), 25.0, dtype=np.float32)
        if image is None
        else np.asarray(image, dtype=np.float32)
    )
    img_hdr = cel_wcs.to_header()
    img_hdr["DETECTOR"] = detector
    img_hdr["MJD-OBS"] = mjd
    img_hdr["BUNIT"] = "MJy/sr"
    img_hdu = fits.ImageHDU(data=img_data, header=img_hdr, name="IMAGE")

    var_data = (
        np.full((ny, nx), 0.04, dtype=np.float32)
        if variance is None
        else np.asarray(variance, dtype=np.float32)
    )
    var_hdr = cel_wcs.to_header()
    var_hdr["BUNIT"] = "(MJy/sr)^2"
    var_hdu = fits.ImageHDU(data=var_data, header=var_hdr, name="VARIANCE")

    flg_data = (
        np.zeros((ny, nx), dtype=np.int32)
        if flags_arr is None
        else np.asarray(flags_arr, dtype=np.int32)
    )
    flg_hdr = cel_wcs.to_header()
    for name, b in FLAG_BITS.items():
        key = f"MP_{name}"
        flg_hdr[f"HIERARCH {key}" if len(key) > 8 else key] = b
    flg_hdu = fits.ImageHDU(data=flg_data, header=flg_hdr, name="FLAGS")

    zodi_data = (
        np.full((ny, nx), 10.0, dtype=np.float32)
        if zodi is None
        else np.asarray(zodi, dtype=np.float32)
    )
    zodi_hdr = cel_wcs.to_header()
    zodi_hdr["BUNIT"] = "MJy/sr"
    zodi_hdu = fits.ImageHDU(data=zodi_data, header=zodi_hdr, name="ZODI")

    # WCS-WAVE extension with alternate WCS key "W" (spectral linear variable filter model)
    yy, xx = np.mgrid[0:ny, 0:nx]
    cx = (nx - 1) / 2.0
    cy = (ny - 1) / 2.0
    wave_grid = (w0_um + dw_dx_um * (xx - cx) + dw_dy_um * (yy - cy)).astype(np.float32)

    wave_hdr = fits.Header()
    wave_hdr["WCSAXESW"] = 2
    wave_hdr["CRPIX1W"] = (nx + 1) / 2.0
    wave_hdr["CRPIX2W"] = (ny + 1) / 2.0
    wave_hdr["CRVAL1W"] = w0_um
    wave_hdr["CRVAL2W"] = 0.0
    wave_hdr["CD1_1W"] = dw_dx_um
    wave_hdr["CD1_2W"] = dw_dy_um
    wave_hdr["CD2_1W"] = 0.0
    wave_hdr["CD2_2W"] = 1.0
    wave_hdr["CTYPE1W"] = "WAVE"
    wave_hdr["CTYPE2W"] = "LINEAR"
    wave_hdr["CUNIT1W"] = "um"
    wave_hdr["CUNIT2W"] = "pix"
    wave_hdu = fits.ImageHDU(data=wave_grid, header=wave_hdr, name="WCS-WAVE")

    ext_hdus: list[fits.ImageHDU] = [img_hdu, flg_hdu, var_hdu, zodi_hdu, wave_hdu]
    if include_psf:
        psf_data = np.ones((11, 11), dtype=np.float32)
        psf_hdu = fits.ImageHDU(data=psf_data, name="PSF")
        ext_hdus.insert(2, psf_hdu)

    if scramble_order:
        # Reverse extension order so any code indexing by integer position (e.g. hdul[1]) breaks
        ext_hdus = list(reversed(ext_hdus))

    return fits.HDUList([pri_hdu, *ext_hdus])


def _hdul_to_bytes(hdul: fits.HDUList) -> bytes:
    buf = io.BytesIO()
    with data.warnings_suppressed():
        hdul.writeto(buf, output_verify="silentfix")
    return buf.getvalue()


@pytest.fixture(autouse=True)
def _reset_cache_and_env(monkeypatch: pytest.MonkeyPatch) -> None:
    """Reset in-process cache and SPHX_MODE before each test."""
    clear_memory_cache()
    monkeypatch.delenv("SPHX_MODE", raising=False)


def test_frame_from_hdul_reads_by_name_and_evaluates_wcs_wave() -> None:
    """frame_from_hdul reads extensions by name even when extension order is scrambled."""
    ra, dec = 133.76, -7.24
    hdul = _make_cutout_hdul(
        ny=21,
        nx=21,
        ra=ra,
        dec=dec,
        detector=5,
        mjd=60901.25,
        w0_um=4.15,
        dw_dx_um=0.005,
        dw_dy_um=-0.002,
        scramble_order=True,
        uri="ibe/data/spherex/l2b-v20/exp_001_l2b-v20.fits",
    )
    # Verify that hdul[1] is NOT IMAGE when scrambled
    assert hdul[1].name != "IMAGE"

    frame = frame_from_hdul(hdul, ra=ra, dec=dec, mjd=60901.25, uri=hdul[0].header["SPHXURI"])
    assert frame.image.shape == (21, 21)
    assert frame.detector == 5
    assert frame.mjd == pytest.approx(60901.25)
    assert frame.version == 20
    assert frame.wavelength == pytest.approx(4.15, rel=1e-4)
    assert frame.zodi is not None
    assert np.allclose(frame.zodi, 10.0)

    # Shift target position by +4 pixels in X (+4 * -6.15 arcsec in RA) and check LVF gradient
    ra_shifted, dec_shifted = frame.wcs.celestial.pixel_to_world_values(14.0, 10.0)
    frame_shifted = frame_from_hdul(
        hdul,
        ra=float(ra_shifted),
        dec=float(dec_shifted),
        mjd=60901.25,
        uri="l2b-v21/file.fits",
    )
    assert frame_shifted.version == 21
    assert frame_shifted.wavelength == pytest.approx(4.15 + 4.0 * 0.005, rel=1e-4)


def test_frame_from_hdul_flag_masking_and_unreliable_core() -> None:
    """SOURCE bit (21) is never masked; DETECTOR_DEFECTS, NaN, and var<=0 are masked."""
    ny, nx = 8, 8
    img = np.full((ny, nx), 15.0, dtype=np.float32)
    var = np.full((ny, nx), 0.25, dtype=np.float32)
    flg = np.zeros((ny, nx), dtype=np.int32)

    # Set SOURCE (21) and FULLSAMPLE (12) on (0, 0): must NOT be masked or unreliable
    flg[0, 0] = _bit("SOURCE") | _bit("FULLSAMPLE")
    # Set HOT (10) on (1, 1): detector defect -> masked
    flg[1, 1] = _bit("HOT")
    # Set NONFUNC (6) on (1, 2): detector defect -> masked
    flg[1, 2] = _bit("NONFUNC")
    # Set OVERFLOW (1) and NONLINEAR (15) on (2, 2): unreliable core -> unreliable=True, mask=False
    flg[2, 2] = _bit("OVERFLOW") | _bit("NONLINEAR")
    # Set TRANSIENT (0) on (3, 3): transient -> neither masked by default nor unreliable
    flg[3, 3] = _bit("TRANSIENT")
    # Non-finite image pixel at (4, 4) -> masked
    img[4, 4] = np.nan
    # Non-positive variance at (5, 5) and (5, 6) -> masked
    var[5, 5] = 0.0
    var[5, 6] = -1.0

    hdul = _make_cutout_hdul(ny=ny, nx=nx, image=img, variance=var, flags_arr=flg)
    frame = frame_from_hdul(hdul, ra=133.76, dec=-7.24, mjd=60900.0, uri="l2b-v20/cut.fits")

    assert not frame.mask[0, 0]
    assert not frame.unreliable[0, 0]

    assert frame.mask[1, 1]
    assert frame.mask[1, 2]
    assert not frame.unreliable[1, 1]

    assert not frame.mask[2, 2]
    assert frame.unreliable[2, 2]

    assert not frame.mask[3, 3]
    assert not frame.unreliable[3, 3]

    assert frame.mask[4, 4]
    assert frame.mask[5, 5]
    assert frame.mask[5, 6]


def test_query_images_tap_success_and_no_coverage() -> None:
    """query_images parses TAP rows sorted by MJD and raises NoCoverageError when empty."""
    captured_queries: list[str] = []

    def fake_tap(adql: str) -> Table:
        captured_queries.append(adql)
        return Table(
            rows=[
                ("spherex/l2b-v20/late.fits", 61085.3, "SPHEREx-D5"),
                ("spherex/l2b-v20/early.fits", 60900.2, "SPHEREx-D5"),
            ],
            names=("uri", "mjd", "bandpass"),
        )

    rows = query_images(133.76, -7.24, tap_query=fake_tap)
    assert len(rows) == 2
    assert "CONTAINS(POINT('ICRS'" in captured_queries[0]
    assert "spherex.artifact" in captured_queries[0]
    assert "spherex.plane" in captured_queries[0]

    # Tuple unpacking works and rows are sorted by MJD
    uri0, mjd0, band0 = rows[0]
    assert uri0 == "spherex/l2b-v20/early.fits"
    assert mjd0 == pytest.approx(60900.2)
    assert band0 == "SPHEREx-D5"

    # Empty result raises NoCoverageError with clear message
    with pytest.raises(NoCoverageError, match="no SPHEREx coverage at this position"):
        query_images(133.76, -7.24, tap_query=lambda _q: Table(names=("uri", "mjd", "bandpass")))


def test_download_cutout_drops_psf_and_retries_with_backoff(tmp_path: Path) -> None:
    """download_cutout retries on failure with backoff, drops PSF, and writes atomically."""
    raw_bytes = _hdul_to_bytes(_make_cutout_hdul(include_psf=True))
    attempts = 0
    sleeps: list[float] = []

    def flaky_fetcher(_url: str) -> bytes:
        nonlocal attempts
        attempts += 1
        if attempts < 3:
            raise ConnectionError("Simulated IRSA connection drop")
        return raw_bytes

    dest = tmp_path / "0001.fits"
    ok = download_cutout(
        "https://irsa.ipac.caltech.edu/fake?center=133.76,-7.24d&size=0.066667",
        dest,
        retries=4,
        backoff_base=0.25,
        fetcher=flaky_fetcher,
        sleep_fn=sleeps.append,
        metadata={"mjd": 60900.5, "bandpass": "SPHEREx-D5", "uri": "spherex/l2b-v20/f1.fits"},
    )
    assert ok is True
    assert attempts == 3
    assert sleeps == [0.25, 0.5]
    assert dest.is_file()
    # Ensure no leftover temporary files remain
    assert list(tmp_path.glob("*.tmp.fits")) == []

    with fits.open(dest) as saved:
        ext_names = [hdu.name for hdu in saved]
        assert "PSF" not in ext_names
        for expected in ("PRIMARY", "IMAGE", "FLAGS", "VARIANCE", "ZODI", "WCS-WAVE"):
            assert expected in ext_names


def test_download_cutout_returns_false_after_exhausting_retries(tmp_path: Path) -> None:
    """download_cutout returns False cleanly when all retry attempts fail."""
    sleeps: list[float] = []
    dest = tmp_path / "never.fits"

    def always_fail(_url: str) -> bytes:
        raise TimeoutError("IRSA timeout")

    ok = download_cutout(
        "https://irsa.ipac.caltech.edu/fail",
        dest,
        retries=3,
        backoff_base=0.1,
        fetcher=always_fail,
        sleep_fn=sleeps.append,
    )
    assert ok is False
    assert len(sleeps) == 2
    assert not dest.exists()


def test_parallel_download_partial_failure_and_progress(tmp_path: Path) -> None:
    """Parallel downloader caps workers at 5, reports progress, and tolerates single failures."""
    good_bytes = _hdul_to_bytes(_make_cutout_hdul())
    progress_events: list[tuple[int, int, str, bool]] = []

    def selective_fetcher(url: str) -> bytes:
        if "bad_uri" in url:
            raise OSError("Dropped cutout")
        return good_bytes

    records = [
        ImageRecord("spherex/l2b-v20/ok1.fits", 60900.1, "SPHEREx-D5"),
        ImageRecord("spherex/l2b-v20/bad_uri.fits", 60901.1, "SPHEREx-D5"),
        ImageRecord("spherex/l2b-v20/ok2.fits", 60902.1, "SPHEREx-D5"),
    ]
    jobs = [
        (
            build_cutout_url(rec.uri, 133.76, -7.24, 120),
            tmp_path / f"{i:04d}.fits",
            rec,
        )
        for i, rec in enumerate(records, start=1)
    ]

    succeeded, failed = download_cutouts_parallel(
        jobs,
        max_workers=8,  # Clamped internally to 5
        retries=2,
        backoff_base=0.0,
        fetcher=selective_fetcher,
        sleep_fn=lambda _s: None,
        progress_callback=lambda done, total, uri, ok: progress_events.append(
            (done, total, uri, ok)
        ),
    )

    assert len(succeeded) == 2
    assert failed == ["spherex/l2b-v20/bad_uri.fits"]
    assert len(progress_events) == 3
    assert sorted(ev[0] for ev in progress_events) == [1, 2, 3]


def test_damaged_or_missing_index_recovery(tmp_path: Path) -> None:
    """Missing or corrupted index.csv is automatically rebuilt from the FITS headers."""
    cache_dir = tmp_path / cache_key_for(133.76, -7.24, 240)
    cache_dir.mkdir(parents=True)

    hdul1 = _make_cutout_hdul(
        mjd=60905.25,
        detector=5,
        uri="spherex/l2b-v20/frame_b.fits",
    )
    hdul2 = _make_cutout_hdul(
        mjd=60900.75,
        detector=5,
        uri="spherex/l2b-v20/frame_a.fits",
    )
    download_cutout(
        "http://example.invalid/1",
        cache_dir / "0001.fits",
        fetcher=lambda _u: hdul1,
        metadata={"mjd": 60905.25, "bandpass": "SPHEREx-D5", "uri": "spherex/l2b-v20/frame_b.fits"},
    )
    download_cutout(
        "http://example.invalid/2",
        cache_dir / "0002.fits",
        fetcher=lambda _u: hdul2,
        metadata={"mjd": 60900.75, "bandpass": "SPHEREx-D5", "uri": "spherex/l2b-v20/frame_a.fits"},
    )

    # Case 1: index.csv is completely missing
    index_path = cache_dir / "index.csv"
    assert not index_path.exists()
    rows = read_or_rebuild_index(cache_dir)
    assert index_path.is_file()
    assert len(rows) == 2
    assert [r[1] for r in rows] == [pytest.approx(60900.75), pytest.approx(60905.25)]

    # Case 2: index.csv is corrupted with garbage text
    index_path.write_text("corrupted,header\nnot_a_file.fits,not_a_float\n", encoding="utf-8")
    rebuilt = read_or_rebuild_index(cache_dir)
    assert len(rebuilt) == 2
    assert rebuilt[0][0] == "0002.fits"
    assert rebuilt[0][3] == "spherex/l2b-v20/frame_a.fits"


def test_cropping_cached_larger_cutout_geometry(tmp_path: Path) -> None:
    """A cached 480-arcsec cutout serves a 120-arcsec request by cropping with Cutout2D."""
    ra, dec = 133.76, -7.24
    # 480 arcsec at 6.15 arcsec/pix is 78x78 pixels
    ny, nx = 78, 78
    img = np.zeros((ny, nx), dtype=np.float32)
    # Place a bright marker at the exact center pixel (39, 39)
    img[39, 39] = 500.0
    sky_center_hdul = _make_cutout_hdul(
        ny=ny,
        nx=nx,
        ra=ra,
        dec=dec,
        pixel_scale_arcsec=6.15,
        image=img,
        mjd=60900.0,
    )
    center_sky = WCS(sky_center_hdul["IMAGE"].header).celestial.pixel_to_world(39, 39)

    cache_dir_480 = tmp_path / cache_key_for(ra, dec, 480)
    download_cutout(
        "http://example.invalid/cut480",
        cache_dir_480 / "0001.fits",
        fetcher=lambda _u: sky_center_hdul,
        metadata={"mjd": 60900.0, "bandpass": "SPHEREx-D5", "uri": "spherex/l2b-v20/cut480.fits"},
    )
    rebuild_cache_index(cache_dir_480)

    # Request a 120-arcsec FOV in cache mode (no network): ~20x20 pixels cropped from 480" cache
    frames = get_frames(ra, dec, fov=120.0, mode="cache", cache_root=tmp_path)
    assert len(frames) == 1
    cropped = frames[0]
    expected_npix = int(np.round(120.0 / 6.15))
    assert cropped.image.shape == (expected_npix, expected_npix)
    assert cropped.meta is not None
    assert cropped.meta["cache_bucket_arcsec"] == 480
    assert cropped.meta["cropped_from_shape"] == (78, 78)

    # Verify that the updated WCS in the cropped frame still maps the peak pixel to center_sky
    peak_y, peak_x = np.unravel_index(int(np.argmax(cropped.image)), cropped.image.shape)
    assert cropped.image[peak_y, peak_x] == pytest.approx(500.0)
    recovered_sky = cropped.wcs.celestial.pixel_to_world(peak_x, peak_y)
    assert center_sky.separation(recovered_sky).arcsec < 0.05


def test_modes_and_no_silent_synthetic_fallback(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Verify cache, live, auto, and synthetic modes, and forbid silent synthetic fallback."""
    ra, dec = 210.8023, 54.3490

    # 1. cache mode on an uncached position raises NotCachedError
    monkeypatch.setenv("SPHX_MODE", "cache")
    with pytest.raises(NotCachedError, match="not in the local cutout cache"):
        get_frames(ra, dec, fov=120.0, cache_root=tmp_path)

    # 2. auto mode when IRSA is unreachable raises ArchiveUnreachableError (NEVER synthetic)
    monkeypatch.setenv("SPHX_MODE", "auto")

    def unreachable_tap(_q: str) -> Any:
        raise ConnectionError("DNS resolution failed for irsa.ipac.caltech.edu")

    with pytest.raises(ArchiveUnreachableError, match="IRSA archive could not be reached"):
        get_frames(ra, dec, fov=120.0, cache_root=tmp_path, tap_query=unreachable_tap)

    # 3. live mode queries IRSA, caches to disk, and returns sorted Frames with synthetic=False
    monkeypatch.setenv("SPHX_MODE", "live")
    tap_rows = Table(
        rows=[
            ("spherex/l2b-v20/f2.fits", 61080.0, "SPHEREx-D5"),
            ("spherex/l2b-v20/f1.fits", 60900.0, "SPHEREx-D5"),
        ],
        names=("uri", "mjd", "bandpass"),
    )

    def fake_fetcher(url: str) -> bytes:
        mjd = 60900.0 if "f1.fits" in url else 61080.0
        return _hdul_to_bytes(_make_cutout_hdul(ra=ra, dec=dec, mjd=mjd))

    live_frames = get_frames(
        ra,
        dec,
        fov=120.0,
        cache_root=tmp_path,
        tap_query=lambda _q: tap_rows,
        fetcher=fake_fetcher,
    )
    assert len(live_frames) == 2
    assert [f.mjd for f in live_frames] == [60900.0, 61080.0]
    assert all(f.meta is not None and f.meta["synthetic"] is False for f in live_frames)
    assert all(f.meta is not None and f.meta["data_source"] == "archive" for f in live_frames)

    # 4. Subsequent auto mode call uses the disk cache without touching the network
    clear_memory_cache()
    monkeypatch.setenv("SPHX_MODE", "auto")
    cached_frames = get_frames(
        ra,
        dec,
        fov=120.0,
        cache_root=tmp_path,
        tap_query=unreachable_tap,  # Would raise if called
    )
    assert len(cached_frames) == 2
    assert all(f.meta is not None and f.meta["data_source"] == "cache" for f in cached_frames)
    assert all(f.meta is not None and f.meta["synthetic"] is False for f in cached_frames)

    # 5. Explicit synthetic mode sets meta["synthetic"] = True on every frame
    monkeypatch.setenv("SPHX_MODE", "synthetic")
    synth_frames = get_frames(ra, dec, fov=120.0, cache_root=tmp_path)
    assert len(synth_frames) >= 4
    assert all(f.meta is not None and f.meta.get("synthetic") is True for f in synth_frames)
    assert all(
        f.meta is not None and f.meta.get("data_source") == "synthetic"
        for f in synth_frames
    )


def test_bucket_and_version_helpers() -> None:
    """Verify cutout bucket selection, invalid mode errors, and version parsing."""
    assert select_size_bucket(60.0) == 120
    assert select_size_bucket(120.0) == 120
    assert select_size_bucket(180.0) == 240
    assert select_size_bucket(300.0) == 480
    assert select_size_bucket(600.0) == 600
    with pytest.raises(ValueError, match="exceeds maximum cutout bucket"):
        select_size_bucket(720.0)
    with pytest.raises(ValueError, match="Invalid SPHX_MODE"):
        get_data_mode("bogus")

    assert parse_version("ibe/data/spherex/l2b-v20/file.fits") == 20
    assert parse_version("level2_v19_cutout.fits") == 19
    assert parse_version("no_version_here.fits") is None


def test_fetch_cutouts_cli(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Verify scripts/fetch_cutouts.py parses coordinates and caches cutouts."""
    tap_rows = Table(
        rows=[("spherex/l2b-v20/cli_1.fits", 60900.0, "SPHEREx-D5")],
        names=("uri", "mjd", "bandpass"),
    )
    monkeypatch.setattr(
        data,
        "query_images",
        lambda ra, dec, **kw: [ImageRecord("spherex/l2b-v20/cli_1.fits", 60900.0, "SPHEREx-D5")],
    )
    orig_get_frames = data.get_frames

    def patched_get_frames(ra: float, dec: float, fov: float = 120.0, **kwargs: Any):
        kwargs["tap_query"] = lambda _q: tap_rows
        kwargs["fetcher"] = lambda _u: _hdul_to_bytes(_make_cutout_hdul(ra=ra, dec=dec))
        return orig_get_frames(ra, dec, fov, **kwargs)

    monkeypatch.setattr(data, "get_frames", patched_get_frames)

    rc = fetch_cutouts.main(
        [
            "--ra",
            "08h55m10.831s",
            "--dec=-07d14m42.53s",
            "--fov",
            "240",
            "--cache-dir",
            str(tmp_path),
        ]
    )
    assert rc == 0
    cached_dirs = list(tmp_path.glob("ra*_dec*_s240"))
    assert len(cached_dirs) == 1
    assert (cached_dirs[0] / "0001.fits").is_file()
    assert (cached_dirs[0] / "index.csv").is_file()


def _partial_failure_setup(tmp_path: Path):
    """Four records where the third cutout can be made to fail on demand."""
    records = [
        (f"spherex/l2b-v20/f{i}.fits", 60900.0 + 40.0 * i, "SPHEREx-D5") for i in range(4)
    ]
    state = {"fail_f2": True}

    def tap(_q: str) -> Table:
        return Table(rows=records, names=("uri", "mjd", "bandpass"))

    def fetcher(url: str) -> bytes:
        if "f2.fits" in url and state["fail_f2"]:
            raise OSError("cutout dropped")
        return _hdul_to_bytes(_make_cutout_hdul(mjd=60900.0))

    kwargs = dict(
        cache_root=tmp_path,
        tap_query=tap,
        fetcher=fetcher,
        retries=1,
        sleep_fn=lambda _s: None,
        use_memory_cache=False,
    )
    return state, kwargs


def test_partial_download_is_not_served_as_complete(tmp_path: Path) -> None:
    """A cache left incomplete by failed downloads is flagged, retried, and then healed."""
    state, kwargs = _partial_failure_setup(tmp_path)
    ra, dec = 133.76, -7.24

    first = get_frames(ra, dec, 120.0, mode="auto", **kwargs)
    assert len(first) == 3
    assert all(f.meta["failed_downloads"] == 1 for f in first)

    # Later request, IRSA unreachable: serve the partial cache but keep the warning.
    def unreachable(_q: str) -> Any:
        raise ConnectionError("down")

    offline = {**kwargs, "tap_query": unreachable}
    served = get_frames(ra, dec, 120.0, mode="auto", **offline)
    assert len(served) == 3
    assert all(f.meta["failed_downloads"] == 1 for f in served)
    assert all(f.meta["data_source"] == "cache" for f in served)

    # Offline cache mode never touches the network but still reports the gap.
    cached_only = get_frames(ra, dec, 120.0, mode="cache", cache_root=tmp_path)
    assert all(f.meta["failed_downloads"] == 1 for f in cached_only)

    # IRSA healthy again: auto mode retries the missing cutout and clears the marker.
    state["fail_f2"] = False
    healed = get_frames(ra, dec, 120.0, mode="auto", **kwargs)
    assert len(healed) == 4
    assert all("failed_downloads" not in f.meta for f in healed)
    key_dir = next(p for p in tmp_path.iterdir() if p.is_dir())
    assert not (key_dir / data.INCOMPLETE_MARKER).exists()

    # And a complete cache is now served without retrying anything.
    final = get_frames(ra, dec, 120.0, mode="auto", **offline)
    assert len(final) == 4


def test_live_mode_bypasses_memory_cache_and_cache_is_lru(tmp_path: Path) -> None:
    calls: list[str] = []

    def tap(_q: str) -> Table:
        calls.append("tap")
        return Table(rows=[("spherex/l2b-v20/f0.fits", 60900.0, "SPHEREx-D5")],
                     names=("uri", "mjd", "bandpass"))

    def fetcher(_url: str) -> bytes:
        return _hdul_to_bytes(_make_cutout_hdul())

    kw = dict(cache_root=tmp_path, tap_query=tap, fetcher=fetcher, retries=1,
              sleep_fn=lambda _s: None)
    get_frames(133.76, -7.24, 120.0, mode="live", **kw)
    get_frames(133.76, -7.24, 120.0, mode="live", **kw)
    assert calls == ["tap", "tap"]  # second live call asked the archive again

    clear_memory_cache()
    get_frames(133.76, -7.24, 120.0, mode="auto", **kw)
    for i in range(data._MEMORY_CACHE_MAX - 1):
        get_frames(10.0 + i, 5.0, 120.0, mode="synthetic", cache_root=tmp_path)
    first_key = next(iter(data._MEMORY_CACHE))
    get_frames(133.76, -7.24, 120.0, mode="auto", **kw)  # touch the oldest entry
    get_frames(200.0, 5.0, 120.0, mode="synthetic", cache_root=tmp_path)  # forces one eviction
    assert first_key in data._MEMORY_CACHE


def _hdul_with_scalar_wavelength(**header: Any) -> fits.HDUList:
    """A cutout whose WCS-WAVE has no usable spectral WCS, only the given scalar keywords."""
    hdul = _make_cutout_hdul()
    wave_hdr = fits.Header()
    wave_hdr["CRVAL1W"] = 4.1  # reference value only; must never be read as the wavelength
    for key, value in header.items():
        wave_hdr[key] = value
    index = hdul.index_of("WCS-WAVE")
    hdul[index] = fits.ImageHDU(header=wave_hdr, name="WCS-WAVE")
    return hdul


def test_wavelength_never_falls_back_to_crval_or_implausible_values() -> None:
    with pytest.raises(ValueError, match="wavelength"):
        frame_from_hdul(_hdul_with_scalar_wavelength(), ra=133.76, dec=-7.24, mjd=60900.0)
    with pytest.raises(ValueError, match="wavelength"):
        frame_from_hdul(
            _hdul_with_scalar_wavelength(WAVELEN=4200.0), ra=133.76, dec=-7.24, mjd=60900.0
        )
    with pytest.raises(ValueError, match="wavelength"):
        frame_from_hdul(
            _hdul_with_scalar_wavelength(WAVELEN=4.2, CUNIT1W="parsec"),
            ra=133.76, dec=-7.24, mjd=60900.0,
        )
    ok = frame_from_hdul(
        _hdul_with_scalar_wavelength(WAVELEN=4200.0, CUNIT1W="nm"),
        ra=133.76, dec=-7.24, mjd=60900.0,
    )
    assert ok.wavelength == pytest.approx(4.2)
