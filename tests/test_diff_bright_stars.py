"""Tests for Difference-mode bright/saturated star handling in frontend_compat."""

from __future__ import annotations

import io
from pathlib import Path

import numpy as np
from fastapi.testclient import TestClient
from PIL import Image

from frontend_compat import app as compat_app
from frontend_compat import create_practice_app
from frontend_compat import sphx_stats as ss
from frontend_compat.frame import Frame


def _gaussian_2d(shape: tuple[int, int], cx: float, cy: float, amp: float, sigma: float = 1.4) -> np.ndarray:
    yy, xx = np.mgrid[0 : shape[0], 0 : shape[1]]
    return amp * np.exp(-0.5 * (((xx - cx) ** 2 + (yy - cy) ** 2) / (sigma**2)))


def _make_frame(
    img: np.ndarray,
    mjd: float,
    *,
    var: np.ndarray | None = None,
    unreliable: np.ndarray | None = None,
    detector: int = 2,
    wavelength: float = 1.5,
) -> Frame:
    ny, nx = img.shape
    return Frame(
        image=img.astype(float),
        variance=(np.full((ny, nx), 0.02, dtype=float) if var is None else var.astype(float)),
        mask=np.zeros((ny, nx), dtype=bool),
        wavelength=wavelength,
        detector=detector,
        mjd=mjd,
        wcs=None,
        meta={},
        unreliable=(np.zeros((ny, nx), dtype=bool) if unreliable is None else unreliable.astype(bool)),
    )


def test_stationary_reprojection_variance_suppresses_bowtie_preserves_mover_and_variable():
    shape = (41, 41)
    rng = np.random.default_rng(7)
    sky_bg = 1.0 + rng.normal(0.0, 0.01, size=shape)

    # 1) Stationary bright star whose total flux is conserved (~50 MJy/sr peak)
    #    but sub-pixel reprojection slightly shifts PSF peak (+18 -> +15 and rings adjacent pixel)
    earlier = sky_bg.copy() + _gaussian_2d(shape, 10.0, 10.0, 40.0, sigma=1.1)
    later = sky_bg.copy() + _gaussian_2d(shape, 10.22, 10.0, 40.0, sigma=1.1)

    # 2) Genuine stationary variable star (brightens dramatically by >2x at (30, 10))
    earlier += _gaussian_2d(shape, 30.0, 10.0, 8.0, sigma=1.3)
    later += _gaussian_2d(shape, 30.0, 10.0, 22.0, sigma=1.3)

    # 3) Genuine fast-moving star (displaces from (20, 28) to (20, 31) — non-overlapping cores)
    earlier += _gaussian_2d(shape, 20.0, 28.0, 18.0, sigma=1.2)
    later += _gaussian_2d(shape, 20.0, 31.0, 18.0, sigma=1.2)

    valid = np.ones(shape, dtype=bool)
    v_rep = ss._stationary_reprojection_variance(earlier, later, valid)

    # Stationary flux-conserving star gets a non-trivial undersampling floor
    stat_slice = v_rep[8:13, 8:13]
    assert float(stat_slice.max()) > 1.0

    # Real brightening variable star & real displaced mover stay near-zero in v_rep
    var_peak_v = float(v_rep[10, 30])
    mover_north_v = float(v_rep[31, 20])
    assert var_peak_v < 0.05 * float(stat_slice.max())
    assert mover_north_v < 0.05 * float(stat_slice.max())

    # Verify epoch_diff_stats end-to-end preserves mover & variable while taming stationary star
    ep_a = [_make_frame(earlier, 61000.0 + i * 0.01) for i in range(3)]
    ep_b = [_make_frame(later, 61365.0 + i * 0.01) for i in range(3)]
    res = ss.epoch_diff_stats(ep_a, ep_b)
    z = res["z"]
    assert abs(float(z[10, 10])) < 6.0  # suppressed well below catastrophic >30 sigma spike
    assert float(z[10, 30]) > 20.0      # genuine variable brightening preserved
    assert float(z[31, 20]) > 20.0      # genuine mover North lobe preserved
    assert float(z[28, 20]) < -20.0     # genuine mover South negative hole preserved


def test_saturated_core_centroid_avoids_core_spikes():
    shape = (39, 39)
    cy, cx = 19, 19
    yy, xx = np.mgrid[0 : shape[0], 0 : shape[1]]

    # True center is at (cx=19.2, cy=20.4), with a wide bright PSF and heavily spiked saturated core
    true_x, true_y = 19.2, 20.4
    img = 0.2 + _gaussian_2d(shape, true_x, true_y, 800.0, sigma=2.4)
    # Inject off-center saturation artifacts/spikes deep inside the inner 3x3 core
    img[cy - 1, cx - 1] = 2500.0
    img[cy, cx - 1] = 2400.0
    var = np.full(shape, 0.5, dtype=float)

    unrel = np.zeros(shape, dtype=bool)
    unrel[cy - 2 : cy + 3, cx - 2 : cx + 3] = True  # 25 >= 20 unreliable pixels in 7x7 core

    # With saturated_core=True and wider box, top-clipping anchors on the symmetric PSF wings
    box = (cy - 5, cy + 6, cx - 5, cx + 6)
    x_sat, y_sat, ex, ey = ss.centroid_with_err(img, var, box=box, saturated_core=True)
    x_raw, y_raw, _, _ = ss.centroid_with_err(img, var, box=(cy - 2, cy + 3, cx - 2, cx + 3), saturated_core=False)

    assert abs(x_sat - true_x) < 0.35
    assert abs(y_sat - true_y) < 0.35
    # Raw 5x5 peak-chasing gets pulled left/down toward the injected artifact spike at (cx-1, cy-1)
    assert abs(x_sat - true_x) < abs(x_raw - true_x)
    assert abs(y_sat - true_y) < abs(y_raw - true_y)
    assert ex > 0.0 and ey > 0.0

    # Verify measure_motion switches to saturated_core path when >=20 unreliable pixels exist in core
    f0 = _make_frame(0.2 + _gaussian_2d(shape, 19.0, 18.8, 800.0, sigma=2.4), 60900.0, var=var, unreliable=unrel)
    f0.image[18, 17] = 2500.0
    f1 = _make_frame(0.2 + _gaussian_2d(shape, 19.0, 20.6, 800.0, sigma=2.4), 61265.0, var=var, unreliable=unrel)
    f1.image[21, 18] = 2500.0
    mot = ss.measure_motion([f0, f1])
    assert len(mot["measurements"]) == 2
    dy_px = mot["measurements"][1]["y"] - mot["measurements"][0]["y"]
    dx_px = mot["measurements"][1]["x"] - mot["measurements"][0]["x"]
    assert dy_px > 1.2  # predominantly northward motion preserved despite intra-core spikes
    assert abs(dx_px) < 0.4


def test_to_png_bridges_bright_saturated_cores_and_ignores_dark_sky_flags():
    shape = (41, 41)
    yy, xx = np.mgrid[0 : shape[0], 0 : shape[1]]

    # Build a synthetic bright dipole with a masked central octagon (where raw z=0)
    z = np.zeros(shape, dtype=float)
    z[13:20, 16:25] = 12.0   # green northern lobe
    z[21:28, 16:25] = -12.0  # red southern lobe

    u_mask = np.zeros(shape, dtype=bool)
    core_rect = (yy >= 17) & (yy <= 23) & (xx >= 18) & (xx <= 22)
    u_mask[core_rect] = True
    z[core_rect] = 0.0  # zeroed out like epoch_diff_stats unreliable pixels

    # Also add a dark-sky SUR_ERROR block far from any star
    sky_defect = (yy >= 4) & (yy <= 7) & (xx >= 4) & (xx <= 7)
    u_mask[sky_defect] = True

    ref_flux = np.zeros(shape, dtype=float)
    ref_flux[14:27, 15:26] = 200.0  # bright saturated star only at center

    bright_cores = compat_app._select_saturated_diff_cores(
        ss.inpaint_bad_pixels(z, u_mask), u_mask, reference_flux=ref_flux
    )
    assert bright_cores[20, 20] or bright_cores[18, 20]  # bright central core kept
    assert not bright_cores[sky_defect].any()             # dark-sky defect excluded

    png_bytes = compat_app.to_png(
        z, lo=-8.0, hi=8.0, cmap=compat_app.ASTRO_DIFF_CMAP, smooth=True,
        unreliable_mask=u_mask, reference_flux=ref_flux,
    )
    im = Image.open(io.BytesIO(png_bytes))
    assert im.size == (compat_app.DISPLAY_SIZE, compat_app.DISPLAY_SIZE)
    rgb = np.asarray(im)

    # Note FLIP_TOP_BOTTOM flips y, so center near y=18 (high z=+12, south-flipped in image) shows green
    # Check that core pixels in center are NOT solid pitch-black (#000000 / zero luminosity)
    center_crop = rgb[220:290, 220:290]
    assert int(center_crop.max()) > 80

    # Check empty-sky defect corner stays uniformly dark sky without bright hatch spikes
    # Row index near y=5..7 in raw array maps to bottom of PNG after FLIP_TOP_BOTTOM
    sky_corner = rgb[420:460, 50:90]
    assert int(sky_corner.std()) < 8.0

    # Edge cases: all-True and all-False unreliable masks must never raise
    assert len(compat_app.to_png(z, lo=-8.0, hi=8.0, cmap=compat_app.ASTRO_DIFF_CMAP, unreliable_mask=np.ones(shape, dtype=bool))) > 100
    assert len(compat_app.to_png(z, lo=-8.0, hi=8.0, cmap=compat_app.ASTRO_DIFF_CMAP, unreliable_mask=np.zeros(shape, dtype=bool))) > 100
    assert len(compat_app.to_png(z, lo=-8.0, hi=8.0, cmap=compat_app.ASTRO_DIFF_CMAP, smooth=False, unreliable_mask=u_mask, reference_flux=ref_flux)) > 100


def test_compat_diff_endpoint_works_cleanly_in_mock_mode(tmp_path):
    app = create_practice_app(tmp_path, "mock")
    with TestClient(app) as client:
        img = client.get("/api/image.png", params={"ra": 269.44, "dec": 4.77, "fov": 240, "mode": "diff"})
        assert img.status_code == 200
        assert img.headers["content-type"].startswith("image/png")
        det = client.get("/api/detections", params={"ra": 269.44, "dec": 4.77, "fov": 240, "mode": "diff"})
        assert det.status_code == 200
        mot = client.get("/api/motion", params={"ra": 269.44, "dec": 4.77, "fov": 240, "mode": "diff"})
        assert mot.status_code == 200
