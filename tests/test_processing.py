# AI-assisted: written with AI pair-programming assistant.
"""Unit tests for SPHEREx frame processing (processing.py)."""

from __future__ import annotations

import io

import numpy as np
import pytest
from astropy.wcs import WCS
from PIL import Image

from frame import Frame
from processing import (
    DEFAULT_EPOCH_GAP_DAYS,
    DEFAULT_MAX_WIDTH_UM,
    DETECTOR_RANGES_UM,
    PIXEL_SCALE_ARCSEC,
    align_frames,
    clean_frame,
    compute_stretch,
    difference_images,
    group_frames,
    render_diff_png,
    render_png,
    split_epochs,
    stabilize_display_frames,
    stack_frames,
)


def _make_wcs(
    ra: float = 133.75,
    dec: float = -7.24,
    ny: int = 25,
    nx: int = 25,
    angle_deg: float = 0.0,
    scale_arcsec: float = PIXEL_SCALE_ARCSEC,
) -> WCS:
    """Construct a 2D TAN WCS centered at (ra, dec) rotated by angle_deg."""
    scale_deg = scale_arcsec / 3600.0
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


def _make_frame(
    *,
    ny: int = 25,
    nx: int = 25,
    image: np.ndarray | None = None,
    variance: np.ndarray | None = None,
    mask: np.ndarray | None = None,
    wavelength: float = 4.15,
    detector: int = 5,
    mjd: float = 60900.0,
    wcs: WCS | None = None,
    zodi: np.ndarray | None = None,
    version: int | None = 20,
    unreliable: np.ndarray | None = None,
    meta: dict | None = None,
) -> Frame:
    """Create a validated synthetic Frame for tests."""
    return Frame(
        image=np.zeros((ny, nx), dtype=np.float64) if image is None else image,
        variance=np.ones((ny, nx), dtype=np.float64) if variance is None else variance,
        mask=np.zeros((ny, nx), dtype=bool) if mask is None else mask,
        wavelength=wavelength,
        detector=detector,
        mjd=mjd,
        wcs=wcs,
        zodi=zodi,
        version=version,
        unreliable=unreliable,
        meta=meta,
    )


def _gaussian_source(
    ny: int,
    nx: int,
    x0: float,
    y0: float,
    flux: float = 100.0,
    sigma_px: float = 1.2,
) -> np.ndarray:
    """Render a 2D Gaussian point source onto a (ny, nx) array."""
    yy, xx = np.mgrid[0:ny, 0:nx]
    return flux * np.exp(-0.5 * (((xx - x0) ** 2 + (yy - y0) ** 2) / (sigma_px**2)))


def test_clean_frame_zodi_background_inpaint_and_immutability() -> None:
    """clean_frame subtracts zodi and background, inpaints mask, and never mutates input."""
    rng = np.random.default_rng(42)
    ny, nx = 31, 31
    yy, xx = np.mgrid[0:ny, 0:nx]
    zodi = 15.0 + 0.2 * yy - 0.1 * xx
    bg_const = 4.5
    noise = rng.normal(0.0, 0.2, size=(ny, nx))
    source = _gaussian_source(ny, nx, 15.0, 15.0, flux=120.0, sigma_px=1.0)
    raw_img = zodi + bg_const + noise + source

    mask = np.zeros((ny, nx), dtype=bool)
    mask[5, 5] = True
    mask[6, 5] = True
    raw_img[5, 5] = 9999.0
    raw_img[6, 5] = np.nan

    orig_img_copy = raw_img.copy()
    orig_zodi_copy = zodi.copy()
    orig_mask_copy = mask.copy()
    orig_meta = {"EXPOSURE": 1}

    frame = _make_frame(
        ny=ny,
        nx=nx,
        image=raw_img,
        mask=mask,
        zodi=zodi,
        meta=orig_meta,
    )
    cleaned = clean_frame(frame)

    # Original frame and its arrays/meta are untouched
    assert frame.zodi is not None
    assert np.array_equal(frame.image, orig_img_copy, equal_nan=True)
    assert np.array_equal(frame.zodi, orig_zodi_copy)
    assert np.array_equal(frame.mask, orig_mask_copy)
    assert frame.meta == {"EXPOSURE": 1}

    # Zodi removed, background near zero despite bright source, mask preserved, display inpainted
    assert cleaned.zodi is None
    assert np.array_equal(cleaned.mask, mask)
    assert cleaned.meta is not None
    assert cleaned.meta["zodi_subtracted"] is True
    assert abs(cleaned.meta["background_level"] - bg_const) < 0.1

    bg_region = cleaned.image[0:8, 20:30]
    assert abs(float(np.median(bg_region))) < 0.1
    assert np.all(np.isfinite(cleaned.image))
    assert abs(float(cleaned.image[5, 5])) < 1.0
    assert abs(float(cleaned.image[6, 5])) < 1.0
    # Bright source peak remains intact
    assert cleaned.image[15, 15] > 100.0


def test_clean_frame_without_zodi_and_all_masked() -> None:
    """clean_frame handles zodi=None and an all-masked frame gracefully."""
    img = np.full((8, 8), 3.0)
    mask = np.ones((8, 8), dtype=bool)
    frame = _make_frame(ny=8, nx=8, image=img, mask=mask, zodi=None)
    cleaned = clean_frame(frame)
    assert cleaned.meta is not None
    assert cleaned.meta["zodi_subtracted"] is False
    assert np.all(cleaned.mask)
    assert np.all(np.isfinite(cleaned.image))


def test_group_frames_picks_detector_and_narrowest_multi_epoch_window() -> None:
    """group_frames selects the best detector and narrowest window maximizing epochs then frames."""
    # Detector 2 has 2 frames; detector 5 has 6 frames across two wavelength clusters:
    # Cluster A: [4.10, 4.13, 4.15] spanning 2 epochs (MJD 60800 and 61000), 3 frames
    # Cluster B: [4.28, 4.29, 4.30, 4.31] all in 1 epoch (MJD 60801..60804), 4 frames
    # Plus an all-masked frame on detector 3 that must not inflate usable count.
    all_masked = np.ones((10, 10), dtype=bool)
    frames = [
        _make_frame(ny=10, nx=10, detector=2, wavelength=1.20, mjd=60800.0),
        _make_frame(ny=10, nx=10, detector=2, wavelength=1.22, mjd=60801.0),
        _make_frame(ny=10, nx=10, detector=3, wavelength=1.80, mjd=60800.0, mask=all_masked),
        # Detector 5 cluster A: 2 epochs, 3 frames, width 0.05 <= 0.10
        _make_frame(ny=10, nx=10, detector=5, wavelength=4.10, mjd=60800.0, version=20),
        _make_frame(ny=10, nx=10, detector=5, wavelength=4.13, mjd=60801.0, version=20),
        _make_frame(ny=10, nx=10, detector=5, wavelength=4.15, mjd=61000.0, version=20),
        # Detector 5 cluster B: 1 epoch, 4 frames, width 0.03 <= 0.10
        _make_frame(ny=10, nx=10, detector=5, wavelength=4.28, mjd=60802.0, version=20),
        _make_frame(ny=10, nx=10, detector=5, wavelength=4.29, mjd=60803.0, version=20),
        _make_frame(ny=10, nx=10, detector=5, wavelength=4.30, mjd=60804.0, version=20),
        _make_frame(ny=10, nx=10, detector=5, wavelength=4.31, mjd=60805.0, version=20),
    ]

    result = group_frames(frames, max_width=DEFAULT_MAX_WIDTH_UM)
    chosen, warnings = result
    assert result.detector == 5
    assert [f.wavelength for f in chosen] == [4.10, 4.13, 4.15]
    assert result.wavelength_window == pytest.approx((4.10, 4.15))
    assert warnings == []


def test_group_frames_tie_breaks_for_narrowest_then_most_frames() -> None:
    """When two windows share the same epoch count, most frames then narrowest width wins."""
    frames = [
        _make_frame(detector=4, wavelength=2.50, mjd=60800.0),
        _make_frame(detector=4, wavelength=2.51, mjd=60801.0),
        _make_frame(detector=4, wavelength=2.58, mjd=60802.0),
        _make_frame(detector=4, wavelength=3.00, mjd=60803.0),
        _make_frame(detector=4, wavelength=3.01, mjd=60804.0),
        _make_frame(detector=4, wavelength=3.02, mjd=60805.0),
        _make_frame(detector=4, wavelength=3.10, mjd=60806.0),
    ]
    # Window [3.00, 3.10] has 4 frames (1 epoch, width 0.10); [2.50, 2.58] has 3 frames.
    # If we drop 3.10, [3.00, 3.02] (width 0.02) beats [2.50, 2.58] (width 0.08) for 3 frames.
    result = group_frames(frames[:-1], detector=4)
    assert result.wavelength_window == pytest.approx((3.00, 3.02))
    assert len(result.frames) == 3


def test_group_frames_warnings_few_frames_wide_window_and_mixed_versions() -> None:
    """group_frames warns on <3 frames, forced wider window, and mixed processing versions."""
    frames = [
        _make_frame(detector=1, wavelength=0.80, mjd=60800.0, version=19),
        _make_frame(detector=1, wavelength=0.95, mjd=60900.0, version=20),
    ]
    result = group_frames(frames, max_width=0.10)
    chosen, warnings = result
    assert len(chosen) == 2
    assert result.wavelength_window == pytest.approx((0.80, 0.95))
    assert any("fewer than 3 frames" in w.lower() for w in warnings)
    assert any("wider than max_width" in w.lower() for w in warnings)
    assert any("mixed processing versions" in w.lower() for w in warnings)


def test_group_frames_explicit_window_and_validation_errors() -> None:
    """group_frames validates inputs and respects explicit wmin/wmax."""
    frames = [
        _make_frame(detector=6, wavelength=4.50, mjd=60800.0),
        _make_frame(detector=6, wavelength=4.55, mjd=60801.0),
        _make_frame(detector=6, wavelength=4.70, mjd=60802.0),
    ]
    chosen, warnings = group_frames(frames, detector=6, wmin=4.48, wmax=4.56)
    assert len(chosen) == 2
    assert any("fewer than 3" in w.lower() for w in warnings)

    with pytest.raises(ValueError, match="empty"):
        group_frames([])
    with pytest.raises(ValueError, match="both be provided"):
        group_frames(frames, wmin=4.5)
    with pytest.raises(ValueError, match="wmin"):
        group_frames(frames, wmin=4.6, wmax=4.5)
    with pytest.raises(ValueError, match="detector"):
        group_frames(frames, detector=7)
    assert len(DETECTOR_RANGES_UM) == 6


def test_split_epochs_at_30_day_gap() -> None:
    """split_epochs clusters observations and splits whenever MJD gap > 30 days."""
    # Unsorted input MJDs:
    # Epoch 0: 60900.2, 60902.7 (gap 2.5d), 60932.0 (gap 29.3d <= 30d -> still Epoch 0)
    # Epoch 1: 60962.5 (gap 30.5d > 30d -> Epoch 1), 60963.1
    # Epoch 2: 61085.3 (gap 122.2d > 30d -> Epoch 2)
    mjds = [60962.5, 60900.2, 61085.3, 60932.0, 60902.7, 60963.1]
    frames = [_make_frame(mjd=m) for m in mjds]

    epochs = split_epochs(frames, gap_days=DEFAULT_EPOCH_GAP_DAYS)
    assert len(epochs) == 3
    assert epochs.epoch_indices == [0, 0, 0, 1, 1, 2]

    assert epochs[0]["epoch"] == 0
    assert epochs[0]["n_frames"] == 3
    assert epochs[0]["mjd_start"] == pytest.approx(60900.2)
    assert epochs[0]["mjd_end"] == pytest.approx(60932.0)
    assert epochs[0]["date_start"] == "2025-08-13"
    assert epochs[0]["date_end"] == "2025-09-14"

    assert epochs[1]["epoch"] == 1
    assert epochs[1]["n_frames"] == 2
    assert epochs[1]["mjd_start"] == pytest.approx(60962.5)
    assert epochs[1]["mjd_end"] == pytest.approx(60963.1)

    assert epochs[2]["epoch"] == 2
    assert epochs[2]["n_frames"] == 1
    assert epochs[2]["date_start"] == "2026-02-14"

    assert split_epochs([]) == []


def test_align_frames_preserves_source_position_under_rotated_wcs() -> None:
    """align_frames preserves a source's sky and aligned pixel position under rotated WCS."""
    ny, nx = 31, 31
    ra_target, dec_target = 133.75, -7.24
    star_ra = 133.75 + (4.0 * PIXEL_SCALE_ARCSEC / 3600.0) / np.cos(np.deg2rad(dec_target))
    star_dec = dec_target + (3.0 * PIXEL_SCALE_ARCSEC / 3600.0)

    angles = [0.0, 35.0, -72.0]
    frames = []
    for i, ang in enumerate(angles):
        wcs_i = _make_wcs(ra=ra_target, dec=dec_target, ny=ny, nx=nx, angle_deg=ang)
        x_star, y_star = wcs_i.world_to_pixel_values(star_ra, star_dec)
        img = _gaussian_source(ny, nx, float(x_star), float(y_star), flux=500.0, sigma_px=1.2)
        var = np.full((ny, nx), 2.5 + 0.5 * i, dtype=np.float64)
        mask = np.zeros((ny, nx), dtype=bool)
        mask[2, 2] = True
        frames.append(_make_frame(ny=ny, nx=nx, image=img, variance=var, mask=mask, wcs=wcs_i))

    aligned = align_frames(frames, target_ra=ra_target, target_dec=dec_target)
    assert len(aligned) == 3

    expected_x, expected_y = aligned[0].wcs.world_to_pixel_values(star_ra, star_dec)  # type: ignore[union-attr]
    # North up (CDELT2 > 0) and East left (CDELT1 < 0)
    assert aligned[0].wcs is not None
    assert aligned[0].wcs.wcs.cdelt[0] < 0
    assert aligned[0].wcs.wcs.cdelt[1] > 0
    # Source is East (smaller x) and North (larger y) of center
    assert expected_x < (nx - 1) / 2.0
    assert expected_y > (ny - 1) / 2.0

    for f in aligned:
        y_peak, x_peak = np.unravel_index(np.nanargmax(f.image), f.image.shape)
        assert abs(x_peak - float(expected_x)) < 0.6
        assert abs(y_peak - float(expected_y)) < 0.6
        # Propagated variance remains close to input level in covered region
        assert np.nanmedian(f.variance) == pytest.approx(np.median([2.5, 3.0, 3.5]), abs=0.6)


def test_align_frames_raises_without_wcs() -> None:
    """align_frames raises ValueError if any frame lacks a WCS."""
    f1 = _make_frame(wcs=_make_wcs())
    f2 = _make_frame(wcs=None)
    with pytest.raises(ValueError, match="no WCS"):
        align_frames([f1, f2])


def test_stack_frames_noise_scales_as_inv_sqrt_n() -> None:
    """Simulated Gaussian noise standard deviation in stack falls roughly as 1/sqrt(N)."""
    rng = np.random.default_rng(123)
    ny, nx = 64, 64
    sigma_in = 3.0
    var_in = sigma_in**2

    frames = [
        _make_frame(
            ny=ny,
            nx=nx,
            image=rng.normal(0.0, sigma_in, size=(ny, nx)),
            variance=np.full((ny, nx), var_in, dtype=np.float64),
        )
        for _ in range(16)
    ]

    stds = {}
    for n in (1, 4, 16):
        res = stack_frames(frames[:n])
        stds[n] = float(np.std(res.image))
        expected = sigma_in / np.sqrt(n)
        assert stds[n] == pytest.approx(expected, rel=0.08)
        assert np.allclose(res.variance, var_in / res.n_frames)
        assert float(np.median(res.variance)) == pytest.approx(var_in / n)
        assert float(np.mean(res.n_frames == n)) > 0.95

    assert stds[4] == pytest.approx(stds[1] / 2.0, rel=0.10)
    assert stds[16] == pytest.approx(stds[1] / 4.0, rel=0.10)


def test_align_and_stack_edge_cases() -> None:
    """align_frames handles fov_arcsec/unreliable/zodi; stack_frames handles all-masked pixels."""
    w = _make_wcs(ra=133.75, dec=-7.24, ny=20, nx=20, angle_deg=15.0)
    unrel = np.zeros((20, 20), dtype=bool)
    unrel[10, 10] = True
    zodi = np.full((20, 20), 2.0)
    f = _make_frame(
        ny=20,
        nx=20,
        image=np.ones((20, 20)),
        wcs=w,
        unreliable=unrel,
        zodi=zodi,
    )
    aligned = align_frames([f], fov_arcsec=61.5)
    assert aligned[0].image.shape == (10, 10)
    assert aligned[0].unreliable is not None
    assert aligned[0].zodi is not None

    # Shape mismatch in stack_frames raises ValueError
    with pytest.raises(ValueError, match="shape"):
        stack_frames([f, aligned[0]])

    # All-masked pixel yields NaN image/variance and n_frames == 0
    bad_mask = np.zeros((10, 10), dtype=bool)
    bad_mask[0, 0] = True
    bad_frames = [_make_frame(ny=10, nx=10, mask=bad_mask) for _ in range(2)]
    res = stack_frames(bad_frames)
    assert res.n_frames[0, 0] == 0
    assert np.isnan(res.image[0, 0])
    assert np.isnan(res.variance[0, 0])
    assert np.isnan(res.sigma[0, 0])

    # Constant image renders valid PNG without division-by-zero
    const_png = render_png(np.full((8, 8), 5.0))
    assert const_png[:8] == b"\x89PNG\r\n\x1a\n"


def test_stack_frames_inverse_variance_weighting_and_mad_clipping() -> None:
    """stack_frames applies inverse-variance weighting and rejects cosmic-ray outliers via MAD."""
    ny, nx = 10, 10
    frames = [
        _make_frame(
            ny=ny,
            nx=nx,
            image=np.full((ny, nx), 10.0),
            variance=np.full((ny, nx), 1.0),
        ),
        _make_frame(
            ny=ny,
            nx=nx,
            image=np.full((ny, nx), 10.2),
            variance=np.full((ny, nx), 1.0),
        ),
        _make_frame(
            ny=ny,
            nx=nx,
            image=np.full((ny, nx), 9.8),
            variance=np.full((ny, nx), 1.0),
        ),
        _make_frame(
            ny=ny,
            nx=nx,
            image=np.full((ny, nx), 10.0),
            variance=np.full((ny, nx), 1.0),
        ),
        _make_frame(
            ny=ny,
            nx=nx,
            image=np.full((ny, nx), 20.0),
            variance=np.full((ny, nx), 100.0),
        ),
    ]
    # Inject a cosmic ray at (3, 4) in frame 0
    frames[0].image[3, 4] = 500.0

    stacked = stack_frames(frames)
    # Outlier pixel (3, 4) was clipped -> 4 frames contribute, value near 10.0
    assert stacked.n_frames[3, 4] == 4
    assert stacked.image[3, 4] == pytest.approx(10.03, abs=0.15)

    # Uncontaminated pixel: 4 frames of var=1.0 (mean 10.0) + 1 frame of var=100.0 (val 20.0)
    # Inverse variance mean = (4 * 10 + 0.01 * 20) / 4.01 = 10.0249
    assert stacked.n_frames[0, 0] == 5
    assert stacked.image[0, 0] == pytest.approx((4 * 10.0 + 0.01 * 20.0) / 4.01, rel=1e-4)
    assert stacked.variance[0, 0] == pytest.approx(1.0 / 4.01, rel=1e-4)


def test_render_png_shared_stretch_and_orientation() -> None:
    """A single shared stretch produces identical pixel values for matching sky across frames."""
    ny, nx = 20, 20
    # Frame 0: quiet background gradient
    img0 = np.tile(np.linspace(0.0, 10.0, nx), (ny, 1))
    # Frame 1: same background plus a bright transient at top-left (North-East: high y, low x)
    img1 = img0.copy()
    img1[17, 2] = 500.0

    frames = [_make_frame(ny=ny, nx=nx, image=img0), _make_frame(ny=ny, nx=nx, image=img1)]
    stretch = compute_stretch(frames, mode="asinh", pmin=1.0, pmax=99.5)
    assert stretch["mode"] == "asinh"
    assert stretch["vmin"] < stretch["vmax"]

    png0 = render_png(frames[0], stretch)
    png1 = render_png(frames[1], stretch)
    assert png0[:8] == b"\x89PNG\r\n\x1a\n"

    arr0 = np.asarray(Image.open(io.BytesIO(png0)))
    arr1 = np.asarray(Image.open(io.BytesIO(png1)))
    assert arr0.shape == (ny, nx)
    assert arr1.shape == (ny, nx)

    # Every unaffected row/column has identical rendered brightness between the two frames
    assert np.array_equal(arr0[10:, :], arr1[10:, :])
    # Transient at FITS y=17, x=2 appears near TOP (row = ny - 1 - 17 = 2) and LEFT (col = 2)
    assert arr1[2, 2] == 255
    assert arr1[2, 2] > arr0[2, 2]

    # Linear stretch also works cleanly
    lin_stretch = compute_stretch(frames, mode="linear")
    lin_png = render_png(frames[0], lin_stretch)
    assert lin_png[:8] == b"\x89PNG\r\n\x1a\n"


def test_align_frames_half_pixel_shift_masks_hot_pixel_leak() -> None:
    """Uncleaned masked hot pixel shifted by (0.5, 0.5) must not leak into good pixels."""
    ny, nx = 21, 21
    ra0, dec0 = 133.75, -7.24
    # Offset the frame WCS crpix by 0.5 px in both x and y relative to target WCS
    in_wcs = _make_wcs(ra=ra0, dec=dec0, ny=ny, nx=nx, angle_deg=0.0)
    in_wcs.wcs.crpix = [(nx + 1) / 2.0 + 0.5, (ny + 1) / 2.0 + 0.5]

    img = np.zeros((ny, nx), dtype=np.float64)
    mask = np.zeros((ny, nx), dtype=bool)
    unreliable = np.zeros((ny, nx), dtype=bool)
    img[10, 10] = 1_000_000.0
    mask[10, 10] = True
    unreliable[10, 10] = True

    frame = _make_frame(
        ny=ny,
        nx=nx,
        image=img,
        mask=mask,
        wcs=in_wcs,
        unreliable=unreliable,
    )
    (aligned,) = align_frames([frame], target_ra=ra0, target_dec=dec0)

    # All four pixels receiving bilinear bleed from (10, 10) must be masked & marked unreliable
    good = ~aligned.mask
    assert np.any(good)
    assert np.allclose(aligned.image[good], 0.0, atol=1e-6)
    assert int(np.count_nonzero(aligned.mask[9:11, 9:11])) == 4
    assert aligned.unreliable is not None
    assert int(np.count_nonzero(aligned.unreliable[9:11, 9:11])) == 4


def test_group_frames_forced_widen_picks_narrowest_multi_epoch_window() -> None:
    """Forced widening picks the narrowest window spanning epochs rather than all wavelengths."""
    frames = [
        _make_frame(detector=2, wavelength=1.00, mjd=60800.0),
        _make_frame(detector=2, wavelength=1.12, mjd=60900.0),
        _make_frame(detector=2, wavelength=1.50, mjd=60801.0),
    ]
    res = group_frames(frames, detector=2, max_width=0.10)
    assert res.wavelength_window == pytest.approx((1.00, 1.12))
    assert len(res.frames) == 2
    assert any("wider than max_width" in w.lower() for w in res.warnings)

    # Also widens when a narrow single-epoch cluster exists but a second epoch is outside max_width
    frames_two_ep = [
        _make_frame(detector=5, wavelength=4.10, mjd=60800.0),
        _make_frame(detector=5, wavelength=4.12, mjd=60801.0),
        _make_frame(detector=5, wavelength=4.23, mjd=60900.0),
    ]
    res2 = group_frames(frames_two_ep, detector=5, max_width=0.10)
    assert res2.wavelength_window == pytest.approx((4.12, 4.23))
    assert len(res2.frames) == 2


def test_align_frames_nan_variance_and_none_meta_fallback() -> None:
    """align_frames masks NaN reprojected variance and handles meta ra/dec=None safely."""
    ny, nx = 15, 15
    ra0, dec0 = 133.75, -7.24
    in_wcs = _make_wcs(ra=ra0, dec=dec0, ny=ny, nx=nx, angle_deg=0.0)
    in_wcs.wcs.crpix = [(nx + 1) / 2.0 + 0.5, (ny + 1) / 2.0 + 0.5]

    var = np.ones((ny, nx), dtype=np.float64)
    var[7, 7] = np.nan
    frame = _make_frame(
        ny=ny,
        nx=nx,
        image=np.ones((ny, nx), dtype=np.float64),
        variance=var,
        wcs=in_wcs,
        meta={"ra": None, "dec": None},
    )
    # Without explicit target_ra/dec, None meta falls back to in_wcs center cleanly
    (aligned_auto,) = align_frames([frame])
    expected_ra, expected_dec = in_wcs.pixel_to_world_values((nx - 1) / 2.0, (ny - 1) / 2.0)
    assert aligned_auto.wcs is not None
    assert aligned_auto.wcs.wcs.crval[0] == pytest.approx(float(expected_ra), abs=1e-6)
    assert aligned_auto.wcs.wcs.crval[1] == pytest.approx(float(expected_dec), abs=1e-6)
    assert bool(aligned_auto.mask[7, 7]) is True

    # Under a half-pixel shift, all 4 pixels touching NaN variance are masked
    (aligned_shifted,) = align_frames([frame], target_ra=ra0, target_dec=dec0)
    assert int(np.count_nonzero(aligned_shifted.mask[6:8, 6:8])) == 4


def test_difference_images_moving_source_dipole_and_stationary_cancellation() -> None:
    """3-pixel mover appears as a positive/negative pair; stationary sources cancel in noise."""
    rng = np.random.default_rng(2026)
    ny, nx = 32, 32
    sigma_noise = 1.0
    var_in = sigma_noise**2

    # Two stationary sources
    stationary = _gaussian_source(ny, nx, 8.0, 8.0, flux=250.0, sigma_px=1.0) + _gaussian_source(
        ny, nx, 24.0, 22.0, flux=180.0, sigma_px=1.0
    )

    # Moving source shifts by 3 pixels in x between Epoch 0 (x=14, y=16) and Epoch 1 (x=17, y=16)
    x_ep0, y_ep0 = 14.0, 16.0
    x_ep1, y_ep1 = 17.0, 16.0
    mover_ep0 = _gaussian_source(ny, nx, x_ep0, y_ep0, flux=120.0, sigma_px=0.9)
    mover_ep1 = _gaussian_source(ny, nx, x_ep1, y_ep1, flux=120.0, sigma_px=0.9)

    frames: list[Frame] = []
    # Epoch 0: 4 frames around MJD 60900
    for i in range(4):
        noise = rng.normal(0.0, sigma_noise, size=(ny, nx))
        frames.append(
            _make_frame(
                ny=ny,
                nx=nx,
                image=stationary + mover_ep0 + noise,
                variance=np.full((ny, nx), var_in, dtype=np.float64),
                wavelength=4.12,
                mjd=60900.0 + 0.5 * i,
            )
        )
    # Epoch 1: 4 frames around MJD 61085 (>30 days later)
    for i in range(4):
        noise = rng.normal(0.0, sigma_noise, size=(ny, nx))
        frames.append(
            _make_frame(
                ny=ny,
                nx=nx,
                image=stationary + mover_ep1 + noise,
                variance=np.full((ny, nx), var_in, dtype=np.float64),
                wavelength=4.14,
                mjd=61085.0 + 0.5 * i,
            )
        )

    res = difference_images(frames)
    diff, stack_a, stack_b = res
    assert res.warnings == []
    assert res.epoch_a == 0
    assert res.epoch_b == 1
    assert stack_a.image.shape == (ny, nx)
    assert stack_b.image.shape == (ny, nx)

    # Propagated sigma matches sqrt(var_a + var_b) from the two epoch stacks
    expected_sigma = np.sqrt(stack_a.variance + stack_b.variance)
    assert np.allclose(res.sigma, expected_sigma)
    assert float(np.median(res.sigma)) == pytest.approx(np.sqrt(0.5))

    # Moving source: positive peak at Epoch 1 (later) position, negative trough at Epoch 0 (earlier)
    y_pos, x_pos = np.unravel_index(int(np.nanargmax(diff)), diff.shape)
    y_neg, x_neg = np.unravel_index(int(np.nanargmin(diff)), diff.shape)
    assert (x_pos, y_pos) == (int(x_ep1), int(y_ep1))
    assert (x_neg, y_neg) == (int(x_ep0), int(y_ep0))
    assert np.hypot(x_pos - x_neg, y_pos - y_neg) == pytest.approx(3.0)
    assert diff[int(y_ep1), int(x_ep1)] > 80.0
    assert diff[int(y_ep0), int(x_ep0)] < -80.0
    assert diff[int(y_ep1), int(x_ep1)] == pytest.approx(
        -diff[int(y_ep0), int(x_ep0)], rel=0.08
    )

    # Stationary sources cancel within the noise (|z| < 3 sigma, local std ~ 1)
    assert abs(float(diff[8, 8])) < 3.0
    assert abs(float(diff[22, 24])) < 3.0
    stat_patch_1 = diff[5:12, 5:12]
    stat_patch_2 = diff[19:26, 21:28]
    assert abs(float(np.mean(stat_patch_1))) < 0.6
    assert abs(float(np.mean(stat_patch_2))) < 0.6
    assert float(np.max(np.abs(stat_patch_1))) < 3.5
    assert float(np.max(np.abs(stat_patch_2))) < 3.5


def test_difference_images_wavelength_warning_and_single_epoch_error() -> None:
    """Wavelength caution fires when |dw| > max_width / 2; fewer than 2 epochs raises ValueError."""
    # Epoch 0 mean wavelength = 4.10 um, Epoch 1 mean wavelength = 4.18 um
    # Difference = 0.08 um > 0.05 um (half of default max_width = 0.10 um)
    frames_warn = [
        _make_frame(ny=12, nx=12, wavelength=4.10, mjd=60900.0),
        _make_frame(ny=12, nx=12, wavelength=4.10, mjd=60901.0),
        _make_frame(ny=12, nx=12, wavelength=4.18, mjd=61000.0),
        _make_frame(ny=12, nx=12, wavelength=4.18, mjd=61001.0),
    ]
    with pytest.warns(UserWarning, match=r"colour|wavelength"):
        res = difference_images(frames_warn, max_width=0.10)
    assert len(res.warnings) == 1
    assert "colour" in res.warnings[0].lower()

    # One epoch raises a clear ValueError
    single_epoch_frames = [
        _make_frame(ny=12, nx=12, wavelength=4.10, mjd=60900.0),
        _make_frame(ny=12, nx=12, wavelength=4.10, mjd=60905.0),
        _make_frame(ny=12, nx=12, wavelength=4.10, mjd=60915.0),
    ]
    with pytest.raises(ValueError, match=r"2 epochs"):
        difference_images(single_epoch_frames)

    with pytest.raises(ValueError, match=r"2 epochs"):
        difference_images([])


def test_difference_images_epoch_selection_and_render_diff_png() -> None:
    """difference_images uses first/last epoch and render_diff_png applies symmetric stretch."""
    ny, nx = 16, 16
    img_ep0 = _gaussian_source(ny, nx, 4.0, 8.0, flux=50.0, sigma_px=0.8)
    img_ep1 = _gaussian_source(ny, nx, 7.0, 8.0, flux=50.0, sigma_px=0.8)
    img_ep2 = _gaussian_source(ny, nx, 10.0, 8.0, flux=50.0, sigma_px=0.8)

    frames = [
        _make_frame(ny=ny, nx=nx, image=img_ep0, wavelength=4.12, mjd=60800.0),
        _make_frame(ny=ny, nx=nx, image=img_ep1, wavelength=4.12, mjd=60900.0),
        _make_frame(ny=ny, nx=nx, image=img_ep2, wavelength=4.12, mjd=61000.0),
    ]

    # Default compares first (0) and last (2) epoch
    diff_default, _, _ = difference_images(frames)
    assert diff_default[8, 10] > 20.0
    assert diff_default[8, 4] < -20.0
    assert abs(float(diff_default[8, 7])) < 1e-3

    # Explicit epoch_a=0, epoch_b=1 compares epoch 0 and epoch 1
    diff_01, _, _ = difference_images(frames, epoch_a=0, epoch_b=1)
    assert diff_01[8, 7] > 20.0
    assert diff_01[8, 4] < -20.0
    assert abs(float(diff_01[8, 10])) < 0.05

    # Invalid epoch indices raise ValueError
    with pytest.raises(ValueError, match="distinct"):
        difference_images(frames, epoch_a=1, epoch_b=1)
    with pytest.raises(ValueError, match="out of range"):
        difference_images(frames, epoch_a=0, epoch_b=5)

    # render_diff_png symmetric stretch: positive -> 255, negative -> 0, zero -> 128
    png_bytes = render_diff_png(diff_default, limit=10.0)
    assert png_bytes[:8] == b"\x89PNG\r\n\x1a\n"
    arr = np.asarray(Image.open(io.BytesIO(png_bytes)))
    assert arr.shape == (ny, nx)
    row_png = ny - 1 - 8
    assert int(arr[row_png, 10]) == 255
    assert int(arr[row_png, 4]) == 0
    assert int(arr[0, 0]) == 128

    # Percentile-based default limit also renders positive bright, negative dark, zero mid-grey
    png_auto = render_diff_png(diff_default)
    arr_auto = np.asarray(Image.open(io.BytesIO(png_auto)))
    assert int(arr_auto[row_png, 10]) == 255
    assert int(arr_auto[row_png, 4]) == 0
    assert int(arr_auto[0, 0]) == 128


def test_clean_frame_heavily_masked_bright_core_avoids_over_subtraction() -> None:
    """clean_frame does not mistake a sparse inner PSF ring for flat sky."""
    ny, nx = 20, 20
    yy, xx = np.mgrid[0:ny, 0:nx]
    radius = np.hypot(xx - 9.5, yy - 9.5)
    # Steep inner wing from ~2000 down to ~270 across a sparse annulus (<25% unmasked).
    wing = 100.0 + 3000.0 * np.exp(-1.5 * np.maximum(radius - 7.5, 0.0))
    mask = (radius < 7.8) | (radius > 9.4)
    assert float(np.count_nonzero(~mask)) / float(mask.size) < 0.25

    frame = _make_frame(
        ny=ny,
        nx=nx,
        image=wing.copy(),
        mask=mask.copy(),
        zodi=np.zeros((ny, nx), dtype=np.float64),
    )
    cleaned = clean_frame(frame)

    bg = float(cleaned.meta["background_level"])
    assert bg < 400.0
    assert float(np.min(cleaned.image[~cleaned.mask])) >= -25.0


def test_compute_stretch_ignores_all_masked_frames_when_unmasked_exist() -> None:
    """An all-masked frame must not hijack the stretch of a sequence with good frames."""
    good = _make_frame(
        ny=10,
        nx=10,
        image=np.tile(np.linspace(0.0, 10.0, 10), (10, 1)),
    )
    all_masked = _make_frame(
        ny=10,
        nx=10,
        image=np.full((10, 10), -5000.0),
        mask=np.ones((10, 10), dtype=bool),
    )

    stretch = compute_stretch([good, all_masked])
    assert 0.0 <= stretch["vmin"] <= 1.0
    assert 9.0 <= stretch["vmax"] <= 10.0

    # A lone all-masked frame still falls back to finite pixels gracefully.
    fallback = compute_stretch(all_masked)
    assert fallback["vmin"] == pytest.approx(-5000.0)


def test_stabilize_display_frames_harmonizes_pedestal_and_masks() -> None:
    """stabilize_display_frames aligns residual sky pedestals without mutating inputs."""
    ny, nx = 16, 16
    star = _gaussian_source(ny, nx, 8.0, 8.0, flux=80.0, sigma_px=1.0)
    mask = np.zeros((ny, nx), dtype=bool)
    mask[2, 2] = True

    f0 = _make_frame(ny=ny, nx=nx, image=star + 0.0, mask=mask.copy())
    f1 = _make_frame(ny=ny, nx=nx, image=star + 8.0, mask=mask.copy())
    # Inject bright outlier inside dilated mask pixel as alignment edges can hold.
    f0.image[2, 2] = 500.0
    f1.image[2, 2] = 500.0
    f2_masked = _make_frame(
        ny=ny,
        nx=nx,
        image=np.full((ny, nx), -999.0),
        mask=np.ones((ny, nx), dtype=bool),
    )

    f0_orig = f0.image.copy()
    f1_orig = f1.image.copy()
    stabilized = stabilize_display_frames((f0, f1, f2_masked))

    # Original inputs are unmodified.
    assert np.array_equal(f0.image, f0_orig)
    assert np.array_equal(f1.image, f1_orig)

    # Display copies converge to shared median pedestal (4.0) and fill mask cleanly.
    assert abs(float(np.median(stabilized[0].image[~mask])) - 4.0) < 0.25
    assert abs(float(np.median(stabilized[1].image[~mask])) - 4.0) < 0.25
    assert abs(float(stabilized[0].image[2, 2]) - 4.0) < 1.0
    assert abs(float(stabilized[1].image[2, 2]) - 4.0) < 1.0
    assert np.allclose(stabilized[2].image, 4.0)
