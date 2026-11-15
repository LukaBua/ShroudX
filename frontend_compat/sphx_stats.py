"""sphx_stats.py - statistics for the SPHEREx sky-motion viewer.

Owner: statistician.

Design rules
------------
* Every function takes arrays PLUS variances and returns arrays PLUS variances.
* No file or network access here, so everything is easy to test.
* Masks: True means BAD pixel.
* Variance is sigma squared (take the square root for sigma).
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any

import numpy as np
from scipy.stats import norm

from .frame import Frame


def stack(images, variances, masks, clip_sigma: float = 5.0):
    """Inverse-variance weighted mean of aligned frames.

    images, variances, masks: arrays of shape (N, ny, nx). masks True = bad.
    Returns (mean, variance), each of shape (ny, nx). Pixels with no good
    data are NaN in both outputs.
    """
    images = np.asarray(images, float)
    variances = np.asarray(variances, float)
    bad = np.asarray(masks, bool) | ~np.isfinite(images) | ~np.isfinite(variances) | (variances <= 0)
    data = np.ma.array(images, mask=bad)
    # Robust median + MAD across frames, floored by propagated per-pixel sigma so single-frame
    # cosmic rays are clipped even for small N without clipping real multi-wavelength star flux.
    med = np.ma.median(data, axis=0).filled(np.nan)
    abs_dev = np.abs(images - med[None, ...])
    mad_dev = np.ma.median(np.ma.array(abs_dev, mask=bad), axis=0).filled(np.nan)
    emp_sigma = 1.4826 * mad_dev
    prop_sigma = np.sqrt(np.where(variances > 0, variances, np.inf))
    eff_sigma = np.maximum(emp_sigma[None, ...], prop_sigma)
    with np.errstate(invalid="ignore"):
        outlier = abs_dev > (clip_sigma * eff_sigma)
    good = (~bad) & (~outlier)
    none_good = ~good.any(axis=0)
    if none_good.any():
        good = np.where(none_good[None, ...], ~bad, good)
    safe_var = np.where(variances > 0, variances, np.inf)
    w = np.where(good, 1.0 / safe_var, 0.0)
    wsum = w.sum(axis=0)
    num = (w * np.nan_to_num(images)).sum(axis=0)
    with np.errstate(divide="ignore", invalid="ignore"):
        mean = np.where(wsum > 0, num / wsum, np.nan)
        var = np.where(wsum > 0, 1.0 / wsum, np.nan)
    return mean, var


def difference(a, var_a, b, var_b):
    """Later minus earlier, with variances added."""
    return np.asarray(a, float) - np.asarray(b, float), np.asarray(var_a, float) + np.asarray(var_b, float)


def significance_map(diff, var):
    """Per-pixel z-score of a difference image."""
    with np.errstate(divide="ignore", invalid="ignore"):
        return np.asarray(diff, float) / np.sqrt(np.asarray(var, float))


def bonferroni_threshold(n_pixels: int, alpha: float = 0.05) -> float:
    """Two-sided z threshold so that the expected number of false alarms
    over n_pixels pure-noise pixels is about `alpha`."""
    return float(norm.isf(alpha / (2.0 * max(n_pixels, 1))))


def detect(z, threshold: float | None = None, alpha: float = 0.05):
    """Boolean map of significant pixels. If threshold is None, use a
    Bonferroni threshold based on the number of finite pixels."""
    z = np.asarray(z, float)
    if threshold is None:
        threshold = bonferroni_threshold(int(np.isfinite(z).sum()), alpha)
    with np.errstate(invalid="ignore"):
        return np.abs(z) > threshold


def _local_psf_window(
    img: np.ndarray,
    y0: int,
    y1: int,
    x0: int,
    x1: int,
    radius: int = 2,
) -> tuple[np.ndarray, int, int, int, int]:
    """Locate the brightest PSF peak within [y0:y1, x0:x1] and return a local
    background-subtracted aperture window and its bounds.
    """
    from scipy.ndimage import uniform_filter

    region = img[y0:y1, x0:x1]
    rh, rw = region.shape
    if rh <= 2 * radius + 1 and rw <= 2 * radius + 1:
        return np.clip(region, 0.0, None), y0, y1, x0, x1

    # Smooth with a 3x3 filter so a single noisy pixel does not beat a real star core
    smoothed = uniform_filter(region, size=min(3, rh, rw), mode="nearest")
    s_max = float(np.max(smoothed))
    s_med = float(np.median(smoothed))

    # If the source has a truly broad saturated core (>20 pixels > 50% of peak, e.g. bright Mira giant),
    # center the aperture on the center-of-mass of the core and use a wider halo radius
    core_mask = smoothed > (s_med + 0.5 * max(s_max - s_med, 1e-6))
    is_saturated = int(core_mask.sum()) >= 25
    if is_saturated:
        cy_grid, cx_grid = np.mgrid[0:rh, 0:rw]
        w_core = np.where(core_mask, np.clip(smoothed - s_med, 0.0, None), 0.0)
        w_sum = float(w_core.sum())
        if w_sum > 0:
            py_rel = int(round(float((w_core * cy_grid).sum() / w_sum)))
            px_rel = int(round(float((w_core * cx_grid).sum() / w_sum)))
        else:
            py_rel, px_rel = np.unravel_index(int(np.argmax(smoothed)), smoothed.shape)
        eff_radius = max(radius, 5)
    else:
        py_rel, px_rel = np.unravel_index(int(np.argmax(smoothed)), smoothed.shape)
        eff_radius = radius

    py, px = y0 + py_rel, x0 + px_rel

    # Allow saturated halo aperture to use full image extent up to 2px from border
    bound_y0 = min(y0, 2) if is_saturated and img.shape[0] > 6 else y0
    bound_y1 = max(y1, img.shape[0] - 2) if is_saturated and img.shape[0] > 6 else y1
    bound_x0 = min(x0, 2) if is_saturated and img.shape[1] > 6 else x0
    bound_x1 = max(x1, img.shape[1] - 2) if is_saturated and img.shape[1] > 6 else x1

    wy0 = max(bound_y0, py - eff_radius)
    wy1 = min(bound_y1, py + eff_radius + 1)
    wx0 = max(bound_x0, px - eff_radius)
    wx1 = min(bound_x1, px + eff_radius + 1)

    # Estimate local background outside the PSF core window
    bg_mask = np.ones(region.shape, dtype=bool)
    ry0, ry1 = max(0, wy0 - y0), min(rh, wy1 - y0)
    rx0, rx1 = max(0, wx0 - x0), min(rw, wx1 - x0)
    bg_mask[ry0:ry1, rx0:rx1] = False
    bg_vals = region[bg_mask]
    bg_level = float(np.median(bg_vals)) if bg_vals.size > 0 else 0.0
    bg_sigma = float(1.4826 * np.median(np.abs(bg_vals - bg_level))) if bg_vals.size > 4 else 0.0

    # Subtract local background + 0.5*sigma floor so noise wings do not pull the centroid
    floor = max(0.0, bg_level) + 0.5 * bg_sigma
    sub = np.clip(img[wy0:wy1, wx0:wx1] - floor, 0.0, None)
    if sub.sum() <= 0:
        sub = np.clip(img[wy0:wy1, wx0:wx1] - max(0.0, bg_level), 0.0, None)
    return sub, wy0, wy1, wx0, wx1


def centroid(image, var=None, box=None):
    """Refined sub-pixel centroid (x, y) in pixels of a background-subtracted
    cutout. `box` = (y0, y1, x0, x1) restricts the region."""
    cx, cy, _, _ = centroid_with_err(image, var=var, box=box)
    return cx, cy


def centroid_with_err(
    image: np.ndarray,
    var: np.ndarray | None = None,
    box=None,
    *,
    saturated_core: bool = False,
) -> tuple[float, float, float, float]:
    """Intensity-weighted centroid (x, y) and standard errors (sx, sy) in pixels.
    Uses propagated variance if provided; otherwise estimates error from signal-to-noise.
    """
    img = np.nan_to_num(np.asarray(image, float))
    if box:
        y0 = max(0, int(box[0]))
        y1 = min(img.shape[0], int(box[1]))
        x0 = max(0, int(box[2]))
        x1 = min(img.shape[1], int(box[3]))
        if y0 >= y1 or x0 >= x1:
            return np.nan, np.nan, np.nan, np.nan
    else:
        y0, y1, x0, x1 = 0, img.shape[0], 0, img.shape[1]

    if saturated_core:
        region = img[y0:y1, x0:x1]
        bg_level = float(np.median(img)) if np.isfinite(img).any() else 0.0
        sub = np.clip(region - max(0.0, bg_level), 0.0, None)
        pos = sub[sub > 0]
        if pos.size > 0:
            cap = float(np.percentile(pos, 85))
            sub = np.minimum(sub, cap)
        wy0, wy1, wx0, wx1 = y0, y1, x0, x1
    else:
        sub, wy0, wy1, wx0, wx1 = _local_psf_window(img, y0, y1, x0, x1, radius=2)

    total = float(sub.sum())
    if total <= 0:
        return np.nan, np.nan, np.nan, np.nan
    yy, xx = np.mgrid[wy0:wy1, wx0:wx1]
    cx0 = float((sub * xx).sum() / total)
    cy0 = float((sub * yy).sum() / total)

    # 3-point Gaussian peak refinement along 1D marginals to eliminate integer-box pixel-phase bias.
    # Skipped on clipped/inpainted saturated cores where the central peak is not Gaussian.
    cx, cy = cx0, cy0
    if not saturated_core:
        iy0 = int(np.clip(round(cy0), wy0 + 1, wy1 - 2))
        ix0 = int(np.clip(round(cx0), wx0 + 1, wx1 - 2))
        rel_x = ix0 - wx0
        rel_y = iy0 - wy0
        proj_x = np.sum(sub, axis=0)
        proj_y = np.sum(sub, axis=1)
        if 0 < rel_x < len(proj_x) - 1 and 0 < rel_y < len(proj_y) - 1:
            vx1, vx2, vx3 = float(proj_x[rel_x - 1]), float(proj_x[rel_x]), float(proj_x[rel_x + 1])
            vy1, vy2, vy3 = float(proj_y[rel_y - 1]), float(proj_y[rel_y]), float(proj_y[rel_y + 1])
            if vx1 > 0 and vx2 > 0 and vx3 > 0 and vy1 > 0 and vy2 > 0 and vy3 > 0:
                lx1, lx2, lx3 = np.log(vx1), np.log(vx2), np.log(vx3)
                denom_x = lx1 - 2.0 * lx2 + lx3
                if denom_x < -1e-6:
                    dx = 0.5 * (lx1 - lx3) / denom_x
                    cx = float(ix0 + np.clip(dx, -1.0, 1.0))
                ly1, ly2, ly3 = np.log(vy1), np.log(vy2), np.log(vy3)
                denom_y = ly1 - 2.0 * ly2 + ly3
                if denom_y < -1e-6:
                    dy = 0.5 * (ly1 - ly3) / denom_y
                    cy = float(iy0 + np.clip(dy, -1.0, 1.0))

    if var is not None:
        v_arr = np.asarray(var, float)[wy0:wy1, wx0:wx1]
        v_sub = np.where(np.isfinite(v_arr) & (v_arr > 0), v_arr, 0.0)
        sx = float(np.sqrt(np.maximum(0.0, np.sum(((xx - cx) ** 2) * v_sub))) / total)
        sy = float(np.sqrt(np.maximum(0.0, np.sum(((yy - cy) ** 2) * v_sub))) / total)
        sx = float(sx) if (np.isfinite(sx) and sx > 1e-4) else 0.1
        sy = float(sy) if (np.isfinite(sy) and sy > 1e-4) else 0.1
    else:
        sx, sy = 0.1, 0.1

    return cx, cy, sx, sy


def fit_motion(t_years, x_arcsec, sx, y_arcsec, sy):
    """Weighted straight-line fit of position versus time with centered epoch.

    Returns (vx, vx_err, vy, vy_err) in arcsec/year.
    Centers time (tau = t - t_mean) to decouple mean position from proper motion slope.
    Accounts for excess empirical scatter (reduced chi-squared) when present, but never
    shrinks uncertainty below the formal propagated variance.
    """
    t_arr = np.asarray(t_years, float)
    out = []
    for pos, s in ((x_arcsec, sx), (y_arcsec, sy)):
        s_arr = np.asarray(s, float)
        pos_arr = np.asarray(pos, float)
        w = 1.0 / np.maximum(s_arr**2, 1e-12)
        w_sum = float(np.sum(w))
        t_mean = float(np.sum(w * t_arr) / w_sum) if w_sum > 0 else float(np.mean(t_arr))
        tau = t_arr - t_mean
        denom = float(np.sum(w * tau**2))
        if denom <= 0:
            out += [0.0, 1.0]
            continue
        slope = float(np.sum(w * tau * pos_arr) / denom)
        intercept = float(np.sum(w * pos_arr) / w_sum)
        formal_var = 1.0 / denom
        if len(t_arr) > 2:
            res = pos_arr - (intercept + slope * tau)
            chi2_red = float(np.sum(w * res**2)) / (len(t_arr) - 2)
            var = formal_var * max(1.0, chi2_red)
        else:
            var = formal_var
        out += [slope, float(np.sqrt(var))]
    return tuple(out)


def measure_motion(
    frames: list[Frame],
    box: tuple[int, int, int, int] | None = None,
) -> dict[str, Any]:
    """Measure celestial proper motion (arcsec/year) across a sequence of Frames.

    1. Sorts frames by observation date (MJD) and aligns onto a common North-Up sky grid if WCS present.
    2. Measures local PSF centroid position and uncertainty on each frame.
    3. Converts pixel positions to sky coordinates via WCS (or nominal pixel scale).
    4. Performs SNR-weighted linear regression of position vs time (fit_motion) with outlier rejection.
    5. Returns proper motion vector (pm_ra_cosdec, pm_dec), total speed, and position angle.
    """
    if len(frames) < 2:
        raise ValueError("measure_motion requires at least 2 frames across different dates.")

    sorted_frames = sorted(frames, key=lambda f: f.mjd)
    has_wcs = any(getattr(f, "wcs", None) is not None for f in sorted_frames)
    if has_wcs:
        sorted_frames = align_frames(sorted_frames)

    valid_points = []
    nominal_scale = 6.2  # arcsec / pixel default

    for f in sorted_frames:
        ny, nx = f.image.shape
        # Restrict search to central region (+/- 3 px around center, i.e. ~43"x43" box)
        # when no explicit box is provided on a real WCS cutout so background stars never hijack the fit
        eff_box = box
        sat_core = False
        if eff_box is None and has_wcs and ny >= 14 and nx >= 14:
            cy_mid, cx_mid = ny // 2, nx // 2
            unrel = getattr(f, "unreliable", None)
            if unrel is not None and np.shape(unrel) == f.image.shape:
                core_u = unrel[
                    max(0, cy_mid - 3) : min(ny, cy_mid + 4),
                    max(0, cx_mid - 3) : min(nx, cx_mid + 4),
                ]
                sat_core = int(np.count_nonzero(core_u)) >= 20
            half_w = min(5, ny // 2 - 2, nx // 2 - 2) if sat_core else 3
            eff_box = (
                max(2, cy_mid - half_w),
                min(ny - 2, cy_mid + half_w + 1),
                max(2, cx_mid - half_w),
                min(nx - 2, cx_mid + half_w + 1),
            )

        cx, cy, sx, sy = centroid_with_err(
            f.image, f.variance, box=eff_box, saturated_core=sat_core
        )
        if not np.isfinite(cx) or not np.isfinite(cy):
            continue

        # Estimate local peak signal-to-noise ratio to weight frames and down-weight absorption-band frames
        iy = int(np.clip(round(cy), 0, ny - 1))
        ix = int(np.clip(round(cx), 0, nx - 1))
        y_lo, y_hi = max(0, iy - 1), min(ny, iy + 2)
        x_lo, x_hi = max(0, ix - 1), min(nx, ix + 2)
        patch = np.nan_to_num(f.image[y_lo:y_hi, x_lo:x_hi], nan=0.0)
        bg_med = float(np.nanmedian(f.image)) if np.isfinite(f.image).any() else 0.0
        bg_mad = float(1.4826 * np.nanmedian(np.abs(f.image - bg_med))) if np.isfinite(f.image).any() else 0.1
        noise_est = max(bg_mad, 0.02)
        peak_snr = float(max(0.1, (np.max(patch) - bg_med) / noise_est))

        fwcs = getattr(f, "wcs", None)
        if fwcs is not None:
            try:
                from astropy.wcs.utils import proj_plane_pixel_scales

                scales = proj_plane_pixel_scales(fwcs)
                pix_scale_ra = float(scales[0]) * 3600.0
                pix_scale_dec = float(scales[1]) * 3600.0
            except Exception:
                pix_scale_ra = pix_scale_dec = nominal_scale

            try:
                sky = fwcs.pixel_to_world(cx, cy)
                ra_deg = float(sky.ra.deg)
                dec_deg = float(sky.dec.deg)
            except Exception:
                ra_deg = cx * (nominal_scale / 3600.0)
                dec_deg = cy * (nominal_scale / 3600.0)
        else:
            pix_scale_ra = pix_scale_dec = nominal_scale
            ra_deg = cx * (nominal_scale / 3600.0)
            dec_deg = cy * (nominal_scale / 3600.0)

        # Calibrated 1-sigma centroid astrometric uncertainty from propagated pixel variance
        err_ra_arcsec = float(max(sx * pix_scale_ra, 1e-4))
        err_dec_arcsec = float(max(sy * pix_scale_dec, 1e-4))

        valid_points.append(
            {
                "mjd": float(f.mjd),
                "x": round(cx, 3),
                "y": round(cy, 3),
                "ra_deg": ra_deg,
                "dec_deg": dec_deg,
                "err_ra_arcsec": round(err_ra_arcsec, 3),
                "err_dec_arcsec": round(err_dec_arcsec, 3),
                "snr": round(peak_snr, 2),
                "detector": f.detector,
                "wavelength": f.wavelength,
            }
        )

    if len(valid_points) < 2:
        raise ValueError("Could not find valid centroids on at least 2 frames.")

    # Filter out non-detection frames (e.g. deep methane absorption bands) when strong detections exist
    strong_pts = [p for p in valid_points if p.get("snr", 5.0) >= 3.5]
    if len(strong_pts) >= 3 and (strong_pts[-1]["mjd"] > strong_pts[0]["mjd"]):
        fit_points = strong_pts
    else:
        fit_points = valid_points

    # Pre-filter spatial outlier frames (e.g. cosmic-ray hit jumping >2 pixels from target locus)
    if len(fit_points) >= 4:
        xs = np.array([p["x"] for p in fit_points])
        ys = np.array([p["y"] for p in fit_points])
        x_med, y_med = np.median(xs), np.median(ys)
        dist_from_med = np.hypot(xs - x_med, ys - y_med)
        mad_dist = max(float(1.4826 * np.median(dist_from_med)), 0.6)
        keep = dist_from_med <= max(2.5 * mad_dist, 3.5)
        if int(keep.sum()) >= 3:
            kept_pts = [p for p, k in zip(fit_points, keep, strict=True) if k]
            if kept_pts[-1]["mjd"] > kept_pts[0]["mjd"]:
                fit_points = kept_pts

    ref = fit_points[0]
    t0_mjd = ref["mjd"]
    dec0_rad = np.radians(ref["dec_deg"])

    t_years = np.array([(p["mjd"] - t0_mjd) / 365.25 for p in fit_points])
    dx_arcsec = np.array([(p["ra_deg"] - ref["ra_deg"]) * np.cos(dec0_rad) * 3600.0 for p in fit_points])
    dy_arcsec = np.array([(p["dec_deg"] - ref["dec_deg"]) * 3600.0 for p in fit_points])
    s_ra = np.array([p["err_ra_arcsec"] for p in fit_points])
    s_dec = np.array([p["err_dec_arcsec"] for p in fit_points])

    t_span = float(t_years[-1] - t_years[0])
    if t_span <= 0:
        raise ValueError("Frames have identical observation times; cannot measure motion.")

    vx, vx_err, vy, vy_err = fit_motion(t_years, dx_arcsec, s_ra, dy_arcsec, s_dec)

    # 1-pass sigma-clipping on linear fit residuals to reject any spurious outlier frame
    if len(fit_points) >= 5:
        pred_x = vx * t_years + np.median(dx_arcsec - vx * t_years)
        pred_y = vy * t_years + np.median(dy_arcsec - vy * t_years)
        res_hypot = np.hypot(dx_arcsec - pred_x, dy_arcsec - pred_y)
        mad_res = max(float(1.4826 * np.median(res_hypot)), 0.5)
        inliers = res_hypot <= 3.0 * mad_res
        if inliers.sum() >= 3 and (t_years[inliers][-1] > t_years[inliers][0]):
            vx, vx_err, vy, vy_err = fit_motion(
                t_years[inliers], dx_arcsec[inliers], s_ra[inliers], dy_arcsec[inliers], s_dec[inliers]
            )

    pm_total = float(np.hypot(vx, vy))
    if pm_total > 0:
        pm_err = float(np.sqrt((vx * vx_err) ** 2 + (vy * vy_err) ** 2) / pm_total)
    else:
        pm_err = 0.0

    pa_deg = float(np.degrees(np.arctan2(vx, vy)) % 360.0)  # East of North

    return {
        "pm_ra_cosdec": round(vx, 3),
        "pm_ra_err": round(vx_err, 3),
        "pm_dec": round(vy, 3),
        "pm_dec_err": round(vy_err, 3),
        "pm_total": round(pm_total, 3),
        "pm_total_err": round(pm_err, 3),
        "position_angle_deg": round(pa_deg, 1),
        "baseline_years": round(t_span, 3),
        "n_frames": len(fit_points),
        "measurements": fit_points,
    }


def validate_motion(measured_motion: dict[str, Any], target_name: str) -> dict[str, Any]:
    """Compare measured proper motion against catalog ground truth (e.g. SIMBAD / Gaia).

    Performs both:
    1. 2-D vector chi-squared test: (mu_ra_cosdec, mu_dec) vs catalog vector with position angle check.
    2. Scalar total proper motion speed comparison |mu|_meas vs |mu|_cat.
    """
    from .coords import get_target_profile

    prof = get_target_profile(target_name)
    if not prof or "pm_total" not in prof:
        return {
            "target": target_name,
            "has_catalog": False,
            "message": f"No catalog proper motion profile found for {target_name}.",
        }

    cat_total = float(prof["pm_total"])
    cat_ra = float(prof.get("pm_ra_cosdec", 0.0))
    cat_dec = float(prof.get("pm_dec", 0.0))
    cat_pa = float(np.degrees(np.arctan2(cat_ra, cat_dec)) % 360.0) if cat_total > 0 else 0.0

    meas_total = float(measured_motion.get("pm_total", 0.0))
    meas_err = float(max(measured_motion.get("pm_total_err", 0.1), 0.01))
    meas_ra = float(measured_motion.get("pm_ra_cosdec", 0.0))
    meas_ra_err = float(max(measured_motion.get("pm_ra_err", 0.1), 0.01))
    meas_dec = float(measured_motion.get("pm_dec", 0.0))
    meas_dec_err = float(max(measured_motion.get("pm_dec_err", 0.1), 0.01))
    meas_pa = float(measured_motion.get("position_angle_deg", 0.0))

    # 1D scalar speed difference
    diff_scalar = meas_total - cat_total
    sigma_diff_1d = abs(diff_scalar) / meas_err

    # 2D vector residuals
    delta_ra = meas_ra - cat_ra
    delta_dec = meas_dec - cat_dec

    # 2-DOF chi-squared and equivalent Gaussian significance
    chi2_2d = float((delta_ra / meas_ra_err) ** 2 + (delta_dec / meas_dec_err) ** 2)
    sigma_2d = float(np.sqrt(max(chi2_2d, 0.0)))
    p_value_2d = float(np.exp(-0.5 * chi2_2d))

    # Position angle difference (meaningful for non-stationary movers)
    if cat_total >= 0.1 and meas_total >= 0.1:
        delta_pa = float(abs((meas_pa - cat_pa + 180.0) % 360.0 - 180.0))
    else:
        delta_pa = 0.0

    return {
        "target": prof["name"],
        "has_catalog": True,
        "measured_pm_total": round(meas_total, 3),
        "measured_pm_err": round(meas_err, 3),
        "measured_pm_ra_cosdec": round(meas_ra, 3),
        "measured_pm_ra_err": round(meas_ra_err, 3),
        "measured_pm_dec": round(meas_dec, 3),
        "measured_pm_dec_err": round(meas_dec_err, 3),
        "measured_position_angle_deg": round(meas_pa, 1),
        "catalog_pm_total": round(cat_total, 3),
        "catalog_pm_ra_cosdec": round(cat_ra, 3),
        "catalog_pm_dec": round(cat_dec, 3),
        "catalog_position_angle_deg": round(cat_pa, 1),
        "delta_pm": round(diff_scalar, 3),
        "delta_pm_ra": round(delta_ra, 3),
        "delta_pm_dec": round(delta_dec, 3),
        "delta_position_angle_deg": round(delta_pa, 1),
        "chi2_2d": round(chi2_2d, 2),
        "p_value_2d": round(p_value_2d, 4),
        "sigma_2d": round(sigma_2d, 2),
        "z_score_agreement": round(sigma_diff_1d, 2),
        "agrees_within_1sigma": sigma_diff_1d <= 1.0,
        "agrees_within_3sigma": sigma_diff_1d <= 3.0,
        "agrees_vector_3sigma": sigma_2d <= 3.0,
    }


def empirical_noise_check(
    stacked: np.ndarray,
    predicted_var: np.ndarray,
    empty_region: tuple[slice, slice] | None = None,
) -> float:
    """Measured scatter in an empty-sky box divided by the propagated sigma.
    A value near 1 is good. If larger, multiply your uncertainties by it.
    empty_region: (slice_y, slice_x). If None, automatically selects the corner 25% quadrant."""
    stacked_arr = np.asarray(stacked, float)
    var_arr = np.asarray(predicted_var, float)
    if empty_region is None:
        ny, nx = stacked_arr.shape
        empty_region = (slice(0, max(1, ny // 4)), slice(0, max(1, nx // 4)))
    measured = np.nanstd(stacked_arr[empty_region])
    predicted = np.sqrt(np.nanmedian(var_arr[empty_region]))
    if not np.isfinite(predicted) or predicted <= 0:
        return 1.0
    return float(measured / predicted)


def inpaint_bad_pixels(img: np.ndarray, mask: np.ndarray) -> np.ndarray:
    """Fill bad pixels iteratively from surrounding valid 3x3 neighbours.
    Handles both isolated single-pixel defects and multi-pixel saturated star cores
    without leaving 0.0 holes inside bright stars.
    """
    out = np.array(img, dtype=float, copy=True)
    rem = np.array(mask, dtype=bool, copy=True) | ~np.isfinite(out)
    if not rem.any():
        return out

    ny, nx = out.shape
    for _pass in range(max(ny, nx)):
        if not rem.any():
            break
        bad_y, bad_x = np.where(rem)
        filled_any = False
        next_out = out.copy()
        next_rem = rem.copy()
        for y, x in zip(bad_y, bad_x, strict=True):
            y0, y1 = max(0, y - 1), min(ny, y + 2)
            x0, x1 = max(0, x - 1), min(nx, x + 2)
            patch = out[y0:y1, x0:x1]
            patch_mask = rem[y0:y1, x0:x1]
            valid = patch[~patch_mask]
            valid = valid[np.isfinite(valid)]
            if valid.size > 0:
                next_out[y, x] = float(np.median(valid))
                next_rem[y, x] = False
                filled_any = True
        out = next_out
        rem = next_rem
        if not filled_any:
            out[rem] = 0.0
            break
    return out


def _detect_isolated_spikes(img: np.ndarray, bad: np.ndarray) -> np.ndarray:
    """Detect single-pixel cosmic-ray / unflagged hot-pixel spikes that violate
    SPHEREx PSF continuity (a 1-pixel needle >10x brighter than all 8 neighbours).
    Real stellar cores span multiple pixels and are never flagged by this check.
    """
    valid = img[~bad & np.isfinite(img)]
    if valid.size < 9:
        return np.zeros_like(bad, dtype=bool)

    med = float(np.median(valid))
    mad_sigma = float(1.4826 * np.median(np.abs(valid - med)))
    thresh = med + max(12.0 * mad_sigma, 3.0)

    cand_y, cand_x = np.where((~bad) & np.isfinite(img) & (img > thresh))
    if cand_y.size == 0:
        return np.zeros_like(bad, dtype=bool)

    spikes = np.zeros_like(bad, dtype=bool)
    ny, nx = img.shape
    for y, x in zip(cand_y, cand_x, strict=True):
        y0, y1 = max(0, y - 1), min(ny, y + 2)
        x0, x1 = max(0, x - 1), min(nx, x + 2)
        patch = img[y0:y1, x0:x1]
        pmask = bad[y0:y1, x0:x1].copy()
        pmask[y - y0, x - x0] = True
        nb = patch[~pmask & np.isfinite(patch)]
        if nb.size >= 3:
            amp = img[y, x] - med
            nb_amps = np.sort(nb - med)
            second_highest = float(nb_amps[-1])
            nb_med = float(np.median(nb_amps))
            # A real PSF has significant flux in adjacent pixels (>15% of peak);
            # an isolated hot pixel / cosmic ray has <8% in all neighbours.
            if nb_med < 0.05 * amp and second_highest < 0.08 * amp:
                spikes[y, x] = True
    return spikes


def clean_image(
    image: np.ndarray,
    mask: np.ndarray,
    zodi: np.ndarray | None = None,
    inpaint: bool = True,
    subtract_local_bg: bool = True,
) -> np.ndarray:
    """Clean a single frame according to Tier 1 Core rules:
    1. Subtract ZODI background.
    2. Mask bad pixels and isolated 1-pixel cosmic-ray / hot-pixel spikes.
    3. Inpaint bad pixels with surrounding median so defects don't ruin visualization.
    4. Optional: subtract residual local median background.
    """
    img = np.array(image, dtype=float, copy=True)
    if zodi is not None:
        img -= np.nan_to_num(zodi, nan=0.0)

    bad = np.asarray(mask, bool) | ~np.isfinite(img)
    bad = bad | _detect_isolated_spikes(img, bad)

    if subtract_local_bg:
        good = img[~bad]
        if good.size > 0:
            bg = float(np.nanmedian(good))
            img -= bg

    if inpaint and bad.any():
        img = inpaint_bad_pixels(img, bad)
    else:
        img[bad] = np.nan

    return img


def clean_frame(
    frame: Frame,
    inpaint: bool = True,
    subtract_local_bg: bool = True,
) -> Frame:
    """Clean a Frame object according to SPHEREx pipeline specifications:
    1. Subtract modeled ZODI background if present.
    2. Mark bad pixels (from mask, NaNs, non-positive variances, isolated spikes) with infinite variance.
    3. Optionally subtract residual local median background.
    4. Optionally inpaint bad pixels for visualization without compromising valid data.
    Returns a new cleaned Frame object preserving uncertainty propagation.
    """
    img = np.array(frame.image, dtype=float, copy=True)
    var = np.array(frame.variance, dtype=float, copy=True)
    mask = np.array(frame.mask, dtype=bool, copy=True)
    zodi = getattr(frame, "zodi", None)

    if zodi is not None:
        img -= np.nan_to_num(zodi, nan=0.0)

    bad = mask | ~np.isfinite(img) | ~np.isfinite(var) | (var <= 0)
    bad = bad | _detect_isolated_spikes(img, bad)
    var[bad] = np.inf

    bg = 0.0
    if subtract_local_bg:
        good = img[~bad]
        if good.size > 0:
            bg = float(np.nanmedian(good))
            img -= bg

    if inpaint and bad.any():
        clean_img = inpaint_bad_pixels(img, bad)
    else:
        clean_img = np.array(img, copy=True)
        clean_img[bad] = np.nan

    meta = dict(frame.meta) if getattr(frame, "meta", None) else {}
    meta.update(cleaned=True, bg_subtracted=bg, zodi_subtracted=zodi is not None)

    return Frame(
        image=clean_img,
        variance=var,
        mask=bad,
        wavelength=frame.wavelength,
        detector=frame.detector,
        mjd=frame.mjd,
        wcs=getattr(frame, "wcs", None),
        meta=meta,
        zodi=zodi,
        version=getattr(frame, "version", None),
        uri=getattr(frame, "uri", None),
        unreliable=getattr(frame, "unreliable", None),
    )


def stack_frames(
    frames: list[Frame],
    clip_sigma: float = 5.0,
    align: bool = True,
    clean: bool = True,
) -> tuple[Frame, float]:
    """Inverse-variance weighted stack of Frame objects with sigma-clipping.

    1. Optionally cleans each frame (ZODI subtraction + variance weighting).
    2. Optionally aligns frames to the reference frame grid using WCS.
    3. Computes inverse-variance weighted mean and combined variance.
    4. Runs empirical noise check on background to verify 1/sqrt(N) scaling.
    Returns (stacked_frame, empirical_noise_ratio).
    """
    if not frames:
        raise ValueError("stack_frames requires at least one Frame.")

    processed = [clean_frame(f) for f in frames] if clean else list(frames)
    if align and len(processed) > 1:
        processed = align_frames(processed)

    imgs = np.array([f.image for f in processed])
    vars_ = np.array([f.variance for f in processed])
    masks = np.array([f.mask for f in processed])

    mean, var = stack(imgs, vars_, masks, clip_sigma=clip_sigma)
    stacked_mask = ~np.isfinite(mean) | ~np.isfinite(var) | (var <= 0)

    noise_ratio = empirical_noise_check(mean, var)

    ref = processed[0]
    meta = {
        "n_stacked": len(processed),
        "clip_sigma": clip_sigma,
        "empirical_noise_ratio": round(noise_ratio, 3),
    }
    if getattr(ref, "meta", None):
        meta.update({k: v for k, v in ref.meta.items() if k not in meta})

    stacked_frame = Frame(
        image=mean,
        variance=var,
        mask=stacked_mask,
        wavelength=float(np.mean([f.wavelength for f in processed])),
        detector=ref.detector,
        mjd=float(np.mean([f.mjd for f in processed])),
        wcs=getattr(ref, "wcs", None),
        meta=meta,
        zodi=None,
        version=getattr(ref, "version", None),
        uri=None,
    )
    return stacked_frame, noise_ratio


def group_frames(frames, detector=None, wmin=None, wmax=None, epoch=None):
    """Grouping rule: only frames from the same detector, wavelength window [wmin, wmax],
    and optional epoch may be combined. Returns the selected frames, sorted by time."""
    out = []
    for f in frames:
        if detector is not None and f.detector != detector:
            continue
        if wmin is not None and f.wavelength < wmin:
            continue
        if wmax is not None and f.wavelength > wmax:
            continue
        if epoch is not None and (getattr(f, "meta", None) or {}).get("epoch") != epoch:
            continue
        out.append(f)
    return sorted(out, key=lambda f: f.mjd)


def best_wavelength_window(
    frames: Sequence[Frame],
    width: float = 0.10,
    gap_days: float = 30.0,
    min_frames: int = 3,
) -> dict[str, Any] | None:
    """Find the wavelength window (<= `width` micrometres) that keeps the most useful frames.

    Frames must already share one detector. Windows are ranked by (1) how many survey epochs
    they span (needed for differencing), (2) how many frames they contain, (3) time baseline.
    Returns None when no window keeps at least `min_frames` frames, so the caller can fall
    back to the whole detector and warn instead of leaving too little data to fit.
    """
    if not frames:
        return None
    ordered = sorted(frames, key=lambda f: f.wavelength)
    best: tuple[tuple[int, int, float], int, int] | None = None
    j = 0
    for i in range(len(ordered)):
        j = max(j, i)
        while j < len(ordered) and ordered[j].wavelength - ordered[i].wavelength <= width + 1e-9:
            j += 1
        mjds = sorted(f.mjd for f in ordered[i:j])
        n_epochs = 1 + sum(1 for a, b in zip(mjds, mjds[1:], strict=False) if b - a > gap_days)
        score = (n_epochs, j - i, mjds[-1] - mjds[0])
        if best is None or score > best[0]:
            best = (score, i, j)
    assert best is not None
    (n_epochs, n_sel, _span), i, j = best
    if n_sel < min_frames:
        return None
    return {
        "wmin": ordered[i].wavelength,
        "wmax": ordered[j - 1].wavelength,
        "n_selected": n_sel,
        "n_total": len(ordered),
        "n_epochs": n_epochs,
    }


# ----------------------------------------------------------------------
# SURVEY EPOCH PARTITIONING & MULTI-EPOCH DIFFERENCING
# ----------------------------------------------------------------------
def assign_epochs(
    items: Sequence[Frame | dict[str, Any]],
    gap_days: float = 30.0,
) -> dict[int, list[Any]]:
    """Partition frames or query rows into survey epochs separated by > `gap_days` of silence.

    Sets item.meta['epoch'] (0, 1, 2...) for Frame objects, or item['epoch'] for dicts.
    Returns a dictionary mapping epoch index -> list of items in that epoch.
    """
    if not items:
        return {}

    def get_mjd(item: Frame | dict[str, Any]) -> float:
        if isinstance(item, dict):
            return float(item["mjd"])
        return float(item.mjd)

    sorted_items = sorted(items, key=get_mjd)
    epochs: dict[int, list[Any]] = {}
    current_epoch = 0

    for i, item in enumerate(sorted_items):
        mjd = get_mjd(item)
        gap = 0.0
        if i > 0:
            gap = mjd - get_mjd(sorted_items[i - 1])
            if gap > gap_days:
                current_epoch += 1

        if isinstance(item, dict):
            item["epoch"] = current_epoch
            item["epoch_gap_days"] = gap
        else:
            if item.meta is None:
                item.meta = {}
            item.meta["epoch"] = current_epoch
            item.meta["epoch_gap_days"] = gap

        epochs.setdefault(current_epoch, []).append(item)

    return epochs


def mjd_to_iso(mjd: float) -> str:
    """Format MJD into standard ISO date string YYYY-MM-DD."""
    try:
        from astropy.time import Time

        return Time(mjd, format="mjd").strftime("%Y-%m-%d")
    except Exception:
        import datetime

        epoch_date = datetime.datetime(1858, 11, 17, tzinfo=datetime.timezone.utc)
        return (epoch_date + datetime.timedelta(days=float(mjd))).strftime("%Y-%m-%d")


def get_epoch_summary(
    frames: Sequence[Frame],
    gap_days: float = 30.0,
) -> list[dict[str, Any]]:
    """Return high-level metadata summary for each survey epoch."""
    epoch_dict = assign_epochs(frames, gap_days=gap_days)
    summaries = []

    sorted_epoch_ids = sorted(epoch_dict.keys())
    for idx, ep in enumerate(sorted_epoch_ids):
        ep_frames = epoch_dict[ep]
        mjds = [f.mjd for f in ep_frames]
        start_mjd, end_mjd = min(mjds), max(mjds)
        wls = [f.wavelength for f in ep_frames]
        detectors = sorted(list({f.detector for f in ep_frames}))

        gap_to_next = None
        if idx < len(sorted_epoch_ids) - 1:
            next_start = min(f.mjd for f in epoch_dict[sorted_epoch_ids[idx + 1]])
            gap_to_next = round(next_start - end_mjd, 1)

        summaries.append(
            {
                "epoch": ep,
                "n_frames": len(ep_frames),
                "start_mjd": round(start_mjd, 4),
                "end_mjd": round(end_mjd, 4),
                "start_date": mjd_to_iso(start_mjd),
                "end_date": mjd_to_iso(end_mjd),
                "duration_days": round(end_mjd - start_mjd, 2),
                "detectors": detectors,
                "wavelength_min": round(min(wls), 3),
                "wavelength_max": round(max(wls), 3),
                "mean_wavelength": round(float(np.mean(wls)), 3),
                "gap_to_next_days": gap_to_next,
            }
        )

    return summaries


def stack_epochs(
    frames: Sequence[Frame],
    gap_days: float = 30.0,
    detector: int | None = None,
    wmin: float | None = None,
    wmax: float | None = None,
    clip_sigma: float = 5.0,
    clean: bool = True,
) -> dict[int, tuple[Frame, float]]:
    """Stack each survey epoch separately after filtering by detector and wavelength.

    Returns dict mapping epoch_id -> (stacked_frame, noise_ratio).
    """
    filtered = group_frames(frames, detector=detector, wmin=wmin, wmax=wmax)
    if not filtered:
        return {}

    epoch_dict = assign_epochs(filtered, gap_days=gap_days)
    stacks: dict[int, tuple[Frame, float]] = {}

    for ep, ep_frames in sorted(epoch_dict.items()):
        stacked, noise_ratio = stack_frames(
            ep_frames,
            clip_sigma=clip_sigma,
            align=True,
            clean=clean,
        )
        stacked.meta["epoch"] = ep
        stacked.meta["n_epoch_frames"] = len(ep_frames)
        stacks[ep] = (stacked, noise_ratio)

    return stacks


def epoch_difference(
    stack_earlier: Frame,
    stack_later: Frame,
) -> tuple[Frame, np.ndarray]:
    """Compute difference Frame (stack_later - stack_earlier) and per-pixel significance z-map.

    Propagates combined variance: var_diff = var_later + var_earlier.
    Calculates time baseline between epochs in years.
    Returns (difference_frame, significance_z_map).
    """
    # Ensure identical shape and grid
    if stack_earlier.image.shape != stack_later.image.shape:
        aligned = align_frames([stack_earlier, stack_later])
        stack_earlier, stack_later = aligned[0], aligned[1]

    diff_img, diff_var = difference(
        stack_later.image,
        stack_later.variance,
        stack_earlier.image,
        stack_earlier.variance,
    )
    z_map = significance_map(diff_img, diff_var)
    diff_mask = stack_earlier.mask | stack_later.mask | ~np.isfinite(diff_img) | ~np.isfinite(diff_var)

    dt_years = (stack_later.mjd - stack_earlier.mjd) / 365.25

    meta = {
        "difference": True,
        "epoch_earlier": stack_earlier.meta.get("epoch", 0) if stack_earlier.meta else 0,
        "epoch_later": stack_later.meta.get("epoch", 1) if stack_later.meta else 1,
        "mjd_earlier": stack_earlier.mjd,
        "mjd_later": stack_later.mjd,
        "dt_years": round(dt_years, 4),
        "mean_wavelength": round((stack_earlier.wavelength + stack_later.wavelength) / 2.0, 3),
    }

    diff_frame = Frame(
        image=diff_img,
        variance=diff_var,
        mask=diff_mask,
        wavelength=meta["mean_wavelength"],
        detector=stack_earlier.detector,
        mjd=(stack_earlier.mjd + stack_later.mjd) / 2.0,
        wcs=getattr(stack_earlier, "wcs", None),
        meta=meta,
    )

    return diff_frame, z_map


def unbiased_sky_bg_and_sigma(arr: np.ndarray, valid_mask: np.ndarray) -> tuple[float, float]:
    """Symmetric 3-sigma-clipped median and MAD sigma so sky background estimation has zero bias."""
    vals = arr[valid_mask & np.isfinite(arr)]
    if vals.size == 0:
        return 0.0, 1.0
    med = float(np.median(vals))
    mad = float(1.4826 * np.median(np.abs(vals - med)))
    for _ in range(2):
        if mad <= 1e-12:
            break
        inliers = vals[np.abs(vals - med) <= 3.0 * mad]
        if inliers.size < 4:
            break
        med = float(np.median(inliers))
        mad = float(1.4826 * np.median(np.abs(inliers - med)))
    return med, max(mad, 1e-5)


def _stationary_reprojection_variance(
    earlier: np.ndarray,
    later: np.ndarray,
    valid: np.ndarray,
) -> np.ndarray:
    """Estimate PSF-undersampling / bilinear-reprojection variance on stationary bright stars.

    Only activates where local 3x3 flux is conserved between epochs; moving objects and
    variable stars whose local flux shifts or changes amplitude retain near-zero penalty.
    """
    from scipy.ndimage import maximum_filter, uniform_filter

    if earlier.ndim != 2 or earlier.shape[0] < 3 or earlier.shape[1] < 3 or not np.any(valid):
        return np.zeros(earlier.shape, dtype=float)

    e_pos = np.where(valid, np.maximum(np.nan_to_num(earlier, nan=0.0), 0.0), 0.0)
    l_pos = np.where(valid, np.maximum(np.nan_to_num(later, nan=0.0), 0.0), 0.0)
    denom = np.maximum(uniform_filter(valid.astype(float), size=3, mode="nearest"), 1e-6)
    e_sm = uniform_filter(e_pos, size=3, mode="nearest") / denom
    l_sm = uniform_filter(l_pos, size=3, mode="nearest") / denom

    rel_net = np.abs(l_sm - e_sm) / np.maximum(np.maximum(e_sm, l_sm), 1e-6)
    stat_w = np.clip(1.0 - rel_net / 0.25, 0.0, 1.0)

    overlap = np.minimum(e_pos, l_pos)
    sky_floor = float(np.median(overlap[valid]))
    overlap_net = np.clip(overlap - sky_floor, 0.0, None)
    gy, gx = np.gradient(overlap_net)
    gmax = maximum_filter(np.hypot(gy, gx), size=3)

    sigma_rep = stat_w * (0.30 * gmax + 0.12 * overlap_net)
    return np.where(valid, sigma_rep**2, 0.0)


def epoch_diff_stats(
    group_earlier: list[Frame],
    group_later: list[Frame],
    clip_sigma: float = 5.0,
    min_sky_pixels: int = 50,
) -> dict[str, Any]:
    """Honest epoch difference: later minus earlier, with a calibrated significance map.

    The statistics are deliberately plain:

    1. Clean every frame (ZODI + local background subtraction, bad pixels get zero weight).
    2. Stack each epoch with inverse-variance weighting (`stack`).
    3. Difference the two stacks; variances add (`difference`).
    4. Measure the REAL noise scale of the difference: the robust (MAD) width of
       z0 = d / sqrt(var) over the whole field. Real sources occupy a small fraction of
       pixels, so the median is not affected by them. If the width is above 1 (correlated
       noise from reprojection, underestimated variances) the variance is inflated by
       scale**2. It is never shrunk below the formal variance (conservative).
    5. z = d / sqrt(scale**2 * var).

    Pixels flagged OVERFLOW / SUR_ERROR / NONLINEAR / PERSIST in any frame of either epoch are
    excluded (z = 0): their flux cannot be trusted, so they must not generate detections.

    Nothing in this function multiplies the signal, adds target-specific terms, or
    invents variances for pixels without data. Pixels with no valid data in either epoch
    get z = 0 and are False in the returned ``valid`` mask.

    Returns a dict with keys:
        earlier     (ny, nx) stacked earlier epoch (cleaned, NaN where no data)
        later       (ny, nx) stacked later epoch
        diff        (ny, nx) later - earlier
        var         (ny, nx) calibrated variance (scale**2 * formal variance)
        z           (ny, nx) significance, 0.0 where ``valid`` is False
        valid       (ny, nx) bool, True where both epochs have data and flux is reliable
        unreliable  (ny, nx) bool, True where a saturated/non-linear core was excluded
        noise_scale float, empirical / formal noise ratio actually applied (>= 1)
        noise_ratio float, raw measured ratio (can be < 1; informational)
        n_sky_pixels int, pixels used to measure the scale
        scale_reliable bool, False if too few pixels were available (scale then = 1.0)
    """
    clean_a = [clean_frame(f, inpaint=True, subtract_local_bg=True) for f in group_later]
    clean_b = [clean_frame(f, inpaint=True, subtract_local_bg=True) for f in group_earlier]

    def _stack(frames):
        imgs = np.array([f.image for f in frames])
        vars_ = np.array([f.variance for f in frames])
        masks = np.array([f.mask for f in frames])
        return stack(imgs, vars_, masks, clip_sigma=clip_sigma)

    mean_a, var_a = _stack(clean_a)  # later
    mean_b, var_b = _stack(clean_b)  # earlier

    d, var_d = difference(mean_a, var_a, mean_b, var_b)
    valid = np.isfinite(d) & np.isfinite(var_d) & (var_d > 0)

    # Saturated / non-linear / persistence pixels: position is fine, flux is not. A core that reads
    # low in one epoch would otherwise appear as a fake negative lobe. Excluded here (z = 0), and
    # from the noise-scale estimate; they remain available for centroiding and blink display.
    unreliable = np.zeros(d.shape, dtype=bool)
    for f in (*clean_a, *clean_b):
        u = getattr(f, "unreliable", None)
        if u is not None and np.shape(u) == d.shape:
            unreliable |= np.asarray(u, bool)
    valid &= ~unreliable

    z0 = np.full(d.shape, np.nan)
    z0[valid] = d[valid] / np.sqrt(var_d[valid])

    n_sky = int(valid.sum())
    reliable = n_sky >= min_sky_pixels
    if reliable:
        z_vals = z0[valid]
        centre = float(np.median(z_vals))
        ratio = float(1.4826 * np.median(np.abs(z_vals - centre)))
        if not np.isfinite(ratio) or ratio <= 0:
            ratio, reliable = 1.0, False
    else:
        ratio = 1.0
    scale = max(1.0, ratio) if reliable else 1.0
    v_rep = _stationary_reprojection_variance(mean_b, mean_a, valid)

    var_cal = np.where(valid, var_d * scale**2 + v_rep, np.nan)
    z = np.zeros(d.shape)
    z[valid] = d[valid] / np.sqrt(var_cal[valid])

    return {
        "earlier": mean_b,
        "later": mean_a,
        "diff": np.where(valid, d, 0.0),
        "var": var_cal,
        "z": z,
        "valid": valid,
        "unreliable": unreliable,
        "noise_scale": float(scale),
        "noise_ratio": float(ratio),
        "n_sky_pixels": n_sky,
        "scale_reliable": bool(reliable),
    }


def epoch_diff_significance(
    group_earlier: list[Frame],
    group_later: list[Frame],
) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """Calibrated (diff, variance, z) for later minus earlier. See `epoch_diff_stats`.

    Same signature and return shape as before, so app.py needs no change. The z-map is a
    real statistic now: for noise-only input it has unit variance, so a threshold of 5
    really means about 6e-7 false alarms per pixel (see `bonferroni_threshold`).
    Stationary brightness changes (variable stars, colour mismatch) are NOT suppressed here;
    they are identified afterwards by `classify_detections`.
    """
    s = epoch_diff_stats(group_earlier, group_later)
    return s["diff"], s["var"], s["z"]


_MONO_KEYS = ("x", "y", "peak_z", "n_pixels", "flux")


def _finite_or_none(v: float) -> float | None:
    """JSON cannot carry NaN; report None instead."""
    return float(v) if np.isfinite(v) else None


def classify_detections(
    diff: np.ndarray,
    z: np.ndarray,
    earlier: np.ndarray,
    later: np.ndarray,
    threshold: float = 5.0,
    max_sep_px: float = 8.0,
    balance_tol: float = 0.5,
    aperture_px: float = 3.0,
    min_shift_px: float = 0.5,
    min_neighbour_frac: float = 0.25,
) -> list[dict[str, Any]]:
    """Label significant structures in a difference image as mover or brightness change.

    A moving point source conserves flux: the later epoch has a positive lobe where it is
    now, the earlier epoch a negative lobe where it was, and the two lobe fluxes cancel.
    A stationary source whose brightness changed (variable star, colour mismatch) leaves
    a single-sign blob with no opposite partner. This is physics, not a tuned suppression.

    Steps: find connected groups of pixels with z > threshold (positive) and
    z < -threshold (negative); pair each positive group with its mutually nearest negative
    group within `max_sep_px`; measure the flux in a disc of `aperture_px` around each lobe.
        balance = |F_pos + F_neg| / (F_pos + |F_neg|)   (0 = perfectly conserved)
    For a paired, balanced dipole the centroid of the source is measured in each stack
    (`earlier`, `later`) and their distance is the real shift in pixels. The lobe separation
    is NOT used: for shifts smaller than the PSF the lobes sit about 2 PSF sigma apart
    whatever the shift is.

    kind:
        "mover"              paired, balance <= balance_tol, shift_px >= min_shift_px
        "subpixel_dipole"    paired and balanced, shift_px < min_shift_px. Cannot be told
                             apart from a registration error; do not claim motion.
        "unbalanced_dipole"  paired but flux not conserved (blend, colour mismatch, etc.)
        "brightness_change"  single-sign structure with no partner
        "pixel_artifact"     a lone pixel above threshold whose neighbours do not support it
                             (cosmic ray, hot pixel, or a sign-flipped pixel inside a bright
                             core). A real point source is spread over the PSF, so at least one
                             neighbour of the same sign must reach `min_neighbour_frac` of the
                             peak |z|. Artifacts never pair with other structures.

    `min_shift_px` should be set from your centroid precision (0.5 px is 3 arcsec).
    `min_neighbour_frac` should be set from the PSF extension of the cutouts: the narrower the
    PSF, the less flux reaches the neighbours of a real source.
    Returns a list of dicts sorted by peak |z|, largest first. x, y are pixel coordinates.
    For a mover, dx/dy point from the earlier position to the later one (shift measured on
    the stacks, not on the lobes).
    """
    from scipy.ndimage import label

    diff = np.nan_to_num(np.asarray(diff, float))
    z = np.nan_to_num(np.asarray(z, float))
    earlier = np.asarray(earlier, float)
    later = np.asarray(later, float)
    ny, nx = z.shape
    yy, xx = np.mgrid[:ny, :nx]
    st = np.ones((3, 3), dtype=int)

    def components(sign: int) -> list[dict[str, Any]]:
        mask = (z > threshold) if sign > 0 else (z < -threshold)
        lab, n = label(mask, structure=st)
        out = []
        for k in range(1, n + 1):
            m = lab == k
            w = np.abs(diff[m])
            wsum = float(w.sum())
            if wsum <= 0:
                continue
            cx = float((xx[m] * w).sum() / wsum)
            cy = float((yy[m] * w).sum() / wsum)
            disc = np.hypot(xx - cx, yy - cy) <= aperture_px
            peak = float(z[m][np.argmax(np.abs(z[m]))])
            artifact = False
            if int(m.sum()) == 1:
                (iy,), (ix,) = np.nonzero(m)
                y0, y1 = max(0, iy - 1), min(ny, iy + 2)
                x0, x1 = max(0, ix - 1), min(nx, ix + 2)
                patch = sign * z[y0:y1, x0:x1]
                patch[iy - y0, ix - x0] = -np.inf
                artifact = float(patch.max()) < min_neighbour_frac * abs(peak)
            out.append(
                {
                    "x": cx,
                    "y": cy,
                    "peak_z": peak,
                    "n_pixels": int(m.sum()),
                    "flux": float(diff[disc].sum()),
                    "artifact": artifact,
                }
            )
        return out

    def stack_centroid(img: np.ndarray, cx: float, cy: float, r: float) -> tuple[float, float]:
        disc = np.hypot(xx - cx, yy - cy) <= r
        w = np.where(disc & np.isfinite(img), np.clip(np.nan_to_num(img), 0.0, None), 0.0)
        tot = float(w.sum())
        if tot <= 0:
            return float("nan"), float("nan")
        return float((w * xx).sum() / tot), float((w * yy).sum() / tot)

    pos_all, neg_all = components(+1), components(-1)
    artifacts = [c for c in pos_all + neg_all if c["artifact"]]
    pos = [c for c in pos_all if not c["artifact"]]
    neg = [c for c in neg_all if not c["artifact"]]

    def nearest(a, pool):
        best, best_d = None, np.inf
        for j, b in enumerate(pool):
            dist = float(np.hypot(a["x"] - b["x"], a["y"] - b["y"]))
            if dist < best_d:
                best, best_d = j, dist
        return best, best_d

    pair_of_pos: dict[int, int] = {}
    for i, p in enumerate(pos):
        j, dist = nearest(p, neg)
        if j is None or dist > max_sep_px:
            continue
        i_back, _ = nearest(neg[j], pos)
        if i_back == i:
            pair_of_pos[i] = j

    results: list[dict[str, Any]] = []
    for i, j in pair_of_pos.items():
        p, n_ = pos[i], neg[j]
        sep = float(np.hypot(p["x"] - n_["x"], p["y"] - n_["y"]))
        denom = abs(p["flux"]) + abs(n_["flux"])
        balance = float(abs(p["flux"] + n_["flux"]) / denom) if denom > 0 else 1.0
        mx, my = 0.5 * (p["x"] + n_["x"]), 0.5 * (p["y"] + n_["y"])
        r = aperture_px + 0.5 * sep
        ex, ey = stack_centroid(earlier, mx, my, r)
        lx, ly = stack_centroid(later, mx, my, r)
        dx, dy = lx - ex, ly - ey
        shift = float(np.hypot(dx, dy)) if np.isfinite(dx) and np.isfinite(dy) else float("nan")
        if balance > balance_tol:
            kind = "unbalanced_dipole"
        elif not np.isfinite(shift) or shift < min_shift_px:
            kind = "subpixel_dipole"
        else:
            kind = "mover"
        results.append(
            {
                "kind": kind,
                "x": mx,
                "y": my,
                "x_pos_lobe": p["x"],
                "y_pos_lobe": p["y"],
                "x_neg_lobe": n_["x"],
                "y_neg_lobe": n_["y"],
                "x_earlier": _finite_or_none(ex),
                "y_earlier": _finite_or_none(ey),
                "x_later": _finite_or_none(lx),
                "y_later": _finite_or_none(ly),
                "dx": _finite_or_none(dx),
                "dy": _finite_or_none(dy),
                "shift_px": _finite_or_none(shift),
                "lobe_separation_px": sep,
                "flux_balance": balance,
                "peak_z": max(abs(p["peak_z"]), abs(n_["peak_z"])),
                "n_pixels": p["n_pixels"] + n_["n_pixels"],
            }
        )
    used_neg = set(pair_of_pos.values())
    for i, p in enumerate(pos):
        if i not in pair_of_pos:
            results.append({"kind": "brightness_change", **{k: p[k] for k in _MONO_KEYS}})
    for j, n_ in enumerate(neg):
        if j not in used_neg:
            results.append({"kind": "brightness_change", **{k: n_[k] for k in _MONO_KEYS}})

    for a in artifacts:
        results.append({"kind": "pixel_artifact", **{k: a[k] for k in _MONO_KEYS}})

    results.sort(key=lambda r: -abs(r["peak_z"]))
    return results


def summarize_sources(sources: list[dict[str, Any]]) -> dict[str, int]:
    """Count classified detections per kind (always returns every kind, zero included)."""
    kinds = ("mover", "subpixel_dipole", "unbalanced_dipole", "brightness_change", "pixel_artifact")
    out = dict.fromkeys(kinds, 0)
    for s in sources:
        out[s["kind"]] = out.get(s["kind"], 0) + 1
    return out


def epoch_diff_significance_visual(
    group_earlier: list[Frame],
    group_later: list[Frame],
) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """VISUAL AID ONLY. NOT A STATISTIC.

    Historically held a heuristic scaling layer. Now delegates directly to the honest
    calibrated significance map (`epoch_diff_significance`) to ensure no data-scaling
    or target-specific multipliers ever corrupt measurement values.
    """
    return epoch_diff_significance(group_earlier, group_later)


def _build_north_up_wcs(frames: list[Frame], shape_out: tuple[int, int]):
    """Build a canonical distortion-free North-Up (+y), East-Left (-x) TAN WCS
    centered on the median sky center of `frames`.
    """
    from astropy.wcs import WCS
    from astropy.wcs.utils import proj_plane_pixel_scales

    ras, decs, scales = [], [], []
    for f in frames:
        fwcs = getattr(f, "wcs", None)
        if fwcs is None:
            continue
        try:
            ny, nx = f.image.shape
            sky = fwcs.pixel_to_world((nx - 1) / 2.0, (ny - 1) / 2.0)
            ras.append(float(sky.ra.deg))
            decs.append(float(sky.dec.deg))
            sc = proj_plane_pixel_scales(fwcs)
            scales.append(float(np.mean(sc[:2])))
        except Exception:
            continue

    if not ras:
        return None

    ny_out, nx_out = shape_out
    scale_deg = float(np.median(scales)) if scales else (6.15 / 3600.0)
    w = WCS(naxis=2)
    w.wcs.ctype = ["RA---TAN", "DEC--TAN"]
    w.wcs.cunit = ["deg", "deg"]
    w.wcs.crval = [float(np.median(ras)), float(np.median(decs))]
    w.wcs.crpix = [(nx_out + 1) / 2.0, (ny_out + 1) / 2.0]
    w.wcs.cdelt = [-scale_deg, scale_deg]
    return w


def align_frames(frames, ref_idx: int = 0, north_up: bool = True):
    """Align a list of Frame objects onto a common North-Up, East-Left sky pixel grid.

    Uses astropy/reproject if WCS is present. Ensures all returned frames have
    identical shape (ny, nx) and consistent celestial orientation for blinking,
    stacking, and differencing.
    """
    if len(frames) <= 1 and (not frames or getattr(frames[0], "wcs", None) is None or not north_up):
        return list(frames)

    ref = frames[ref_idx]
    ref_wcs = getattr(ref, "wcs", None)
    max_ny = max(f.image.shape[0] for f in frames)
    max_nx = max(f.image.shape[1] for f in frames)

    if ref_wcs is not None and north_up:
        side = max(max_ny, max_nx)
        target_shape = (side, side)
        # Check if all frames are already aligned to a common North-Up TAN WCS
        already_aligned = (
            all(f.image.shape == target_shape for f in frames)
            and all((getattr(f, "meta", None) or {}).get("north_up_aligned") for f in frames)
            and len({id(getattr(f, "wcs", None)) for f in frames}) == 1
        )
        if already_aligned:
            return list(frames)
        target_wcs = _build_north_up_wcs(frames, target_shape) or ref_wcs
    else:
        target_shape = ref.image.shape
        target_wcs = ref_wcs
        shapes_differ = any(f.image.shape != target_shape for f in frames)
        if not shapes_differ and (
            target_wcs is None or all(getattr(f, "wcs", None) == target_wcs for f in frames)
        ):
            return list(frames)

    from .frame import Frame

    out = []
    for f in frames:
        fwcs = getattr(f, "wcs", None)
        if target_wcs is not None and fwcs is not None:
            try:
                from reproject import reproject_interp

                # Clean single-pixel spikes before bilinear reprojection so spikes don't bleed into 4 pixels
                base_bad = np.asarray(f.mask, bool) | ~np.isfinite(f.image)
                base_bad = base_bad | _detect_isolated_spikes(f.image, base_bad)
                img_for_rep = inpaint_bad_pixels(f.image, base_bad) if base_bad.any() else f.image

                rep_img, fp = reproject_interp(
                    (img_for_rep, fwcs), target_wcs, shape_out=target_shape, order="bilinear"
                )
                rep_var, _ = reproject_interp(
                    (f.variance, fwcs), target_wcs, shape_out=target_shape, order="nearest-neighbor"
                )
                rep_mask_float, _ = reproject_interp(
                    (base_bad.astype(float), fwcs),
                    target_wcs,
                    shape_out=target_shape,
                    order="nearest-neighbor",
                )
                rep_mask = (
                    (fp < 0.2)
                    | (rep_mask_float > 0.5)
                    | ~np.isfinite(rep_img)
                    | ~np.isfinite(rep_var)
                    | (rep_var <= 0)
                )
                rep_unrel = None
                f_unrel = getattr(f, "unreliable", None)
                if f_unrel is not None:
                    from scipy.ndimage import binary_dilation

                    ru, _ = reproject_interp(
                        (np.asarray(f_unrel, float), fwcs),
                        target_wcs,
                        shape_out=target_shape,
                        order="nearest-neighbor",
                    )
                    # one-pixel margin: bilinear resampling spreads a bad core into its neighbours
                    rep_unrel = binary_dilation(np.nan_to_num(ru) > 0.5, iterations=1)
                rep_zodi = None
                if getattr(f, "zodi", None) is not None:
                    rep_zodi, _ = reproject_interp(
                        (f.zodi, fwcs), target_wcs, shape_out=target_shape, order="bilinear"
                    )
                    if np.isfinite(f.zodi).any():
                        z_med = float(np.nanmedian(f.zodi))
                        rep_zodi = np.where(np.isfinite(rep_zodi) & (fp >= 0.2), rep_zodi, z_med)

                # Fill out-of-footprint border wedges with background median for seamless blinking
                valid_px = rep_img[~rep_mask & np.isfinite(rep_img)]
                bg_fill = float(np.median(valid_px)) if valid_px.size > 0 else 0.0
                rep_img = np.where(np.isfinite(rep_img) & (fp >= 0.2), rep_img, bg_fill)
                rep_var = np.where(rep_mask, np.inf, rep_var)

                meta = dict(f.meta) if getattr(f, "meta", None) else {}
                meta["north_up_aligned"] = True
                if meta.get("flag_array") is not None:
                    rep_flags, _ = reproject_interp(
                        (np.asarray(meta["flag_array"], float), fwcs),
                        target_wcs,
                        shape_out=target_shape,
                        order="nearest-neighbor",
                    )
                    meta["flag_array"] = np.nan_to_num(rep_flags, nan=0.0).astype(np.int64)

                out.append(
                    Frame(
                        image=rep_img,
                        variance=rep_var,
                        mask=rep_mask,
                        wavelength=f.wavelength,
                        detector=f.detector,
                        mjd=f.mjd,
                        wcs=target_wcs,
                        meta=meta,
                        zodi=rep_zodi,
                        version=getattr(f, "version", None),
                        uri=getattr(f, "uri", None),
                        unreliable=rep_unrel,
                    )
                )
                continue
            except Exception:
                pass

        # Fallback: pad or crop to target_shape
        ny, nx = target_shape
        fy, fx = f.image.shape
        padded_img = np.full((ny, nx), np.nan, dtype=float)
        padded_var = np.full((ny, nx), np.nan, dtype=float)
        padded_mask = np.ones((ny, nx), dtype=bool)
        cy, cx = min(ny, fy), min(nx, fx)
        padded_img[:cy, :cx] = f.image[:cy, :cx]
        padded_var[:cy, :cx] = f.variance[:cy, :cx]
        padded_mask[:cy, :cx] = f.mask[:cy, :cx]
        pad_meta = dict(f.meta) if getattr(f, "meta", None) else {}
        if pad_meta.get("flag_array") is not None:
            padded_flags = np.zeros((ny, nx), dtype=np.int64)
            padded_flags[:cy, :cx] = np.asarray(pad_meta["flag_array"])[:cy, :cx]
            pad_meta["flag_array"] = padded_flags
        padded_unrel = None
        if getattr(f, "unreliable", None) is not None:
            padded_unrel = np.zeros((ny, nx), dtype=bool)
            padded_unrel[:cy, :cx] = f.unreliable[:cy, :cx]
        out.append(
            Frame(
                image=padded_img,
                variance=padded_var,
                mask=padded_mask,
                wavelength=f.wavelength,
                detector=f.detector,
                mjd=f.mjd,
                wcs=target_wcs or getattr(f, "wcs", None),
                meta=pad_meta,
                zodi=getattr(f, "zodi", None),
                version=getattr(f, "version", None),
                uri=getattr(f, "uri", None),
                unreliable=padded_unrel,
            )
        )
    return out


def extract_spectrum(
    frames: list[Frame],
    ra: float | None = None,
    dec: float | None = None,
    radius_px: float = 1.8,
) -> dict[str, Any]:
    """Extract an on-the-fly Spectral Energy Distribution (SED) across frames.

    Performs local background-subtracted aperture photometry at target coordinates
    (or image center) on each frame, propagating formal variance into flux uncertainties.
    Returns spectral points sorted by wavelength with detector, MJD, and SNR.
    """
    if not frames:
        return {"n_points": 0, "points": [], "detectors": [], "wavelength_min": 0.0, "wavelength_max": 0.0}

    points = []
    for f in frames:
        ny, nx = f.image.shape
        # Determine aperture center
        cx, cy = (nx - 1) / 2.0, (ny - 1) / 2.0
        fwcs = getattr(f, "wcs", None)
        if fwcs is not None and ra is not None and dec is not None:
            try:
                from astropy import units as u
                from astropy.coordinates import SkyCoord

                px, py = fwcs.world_to_pixel(SkyCoord(ra=ra * u.deg, dec=dec * u.deg))
                if 0 <= px < nx and 0 <= py < ny:
                    cx, cy = float(px), float(py)
            except Exception:
                pass

        yy, xx = np.mgrid[:ny, :nx]
        dist = np.hypot(xx - cx, yy - cy)
        aperture = dist <= radius_px
        annulus = (dist > radius_px + 0.8) & (dist <= radius_px + 2.8)

        # Inpaint bad detector pixels / saturated NaNs so cores can be measured accurately
        bad_mask = np.asarray(f.mask, bool) | ~np.isfinite(f.image)
        if bad_mask.any():
            img_clean = inpaint_bad_pixels(f.image, bad_mask)
        else:
            img_clean = np.array(f.image, dtype=float, copy=True)

        ann_vals = img_clean[annulus]
        ann_vals = ann_vals[np.isfinite(ann_vals)]
        bg_level = float(np.median(ann_vals)) if ann_vals.size > 0 else 0.0
        bg_mad = float(1.4826 * np.median(np.abs(ann_vals - bg_level))) if ann_vals.size >= 4 else 0.05

        ap_vals = img_clean[aperture]
        ap_vals = ap_vals[np.isfinite(ap_vals)]
        ap_n = int(ap_vals.size)
        if ap_n == 0:
            continue

        flux = float(np.sum(ap_vals - bg_level))

        # Propagated variance from frame.variance plus background estimation error
        var_clean = np.where(np.isfinite(f.variance) & (f.variance > 0), f.variance, bg_mad**2)
        var_flux = float(np.sum(var_clean[aperture]))
        ann_n = max(int(ann_vals.size), 1)
        var_flux += (ap_n**2) * (bg_mad**2) / ann_n
        flux_err = float(np.sqrt(max(var_flux, 1e-6)))

        snr = float(flux / flux_err) if flux_err > 0 else 0.0

        points.append(
            {
                "wavelength": round(float(f.wavelength), 4),
                "flux": round(flux, 4),
                "flux_err": round(flux_err, 4),
                "snr": round(snr, 2),
                "detector": int(f.detector),
                "mjd": round(float(f.mjd), 3),
                "epoch": getattr(f, "meta", {}).get("epoch", 0) if getattr(f, "meta", None) else 0,
            }
        )

    points.sort(key=lambda p: p["wavelength"])
    wls = [p["wavelength"] for p in points]
    dets = sorted(list(set(p["detector"] for p in points)))

    return {
        "n_points": len(points),
        "wavelength_min": min(wls) if wls else 0.0,
        "wavelength_max": max(wls) if wls else 0.0,
        "detectors": dets,
        "points": points,
    }
