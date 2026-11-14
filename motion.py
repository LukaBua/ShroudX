"""Astrometric centroiding, epoch combination, and proper motion / parallax fitting.

Pre-committed Validation Criteria (enforced by scripts/validate_motion.py):
- Stationary benchmark (literature pm == 0):
    * AGREE if motion_significance = |pm| / sigma_pm < 3.0.
    * DISAGREE if motion_significance >= 3.0.
- Moving targets (literature pm > 0):
    * UNINFORMATIVE if sigma_pm >= 0.3 * pm_lit (not a pass).
    * AGREE if sigma_pm < 0.3 * pm_lit AND
      sqrt((pm_ra - pm_ra_lit)^2 + (pm_dec - pm_dec_lit)^2) < 3.0 * sigma_pm.
    * DISAGREE otherwise.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Sequence

import numpy as np
from astropy.coordinates import SkyCoord, get_body_barycentric
from astropy.time import Time
import astropy.units as u
from astropy.wcs import WCS

from frame import Frame

DAYS_PER_YEAR: float = 365.25
ARCSEC_PER_RAD: float = 180.0 / np.pi * 3600.0


@dataclass(frozen=True)
class FrameCentroid:
    """Single-frame centroid and flux measurement."""

    x: float
    y: float
    sigma_x: float
    sigma_y: float
    flux: float
    sigma_flux: float
    snr: float
    mjd: float
    n_centroid_pixels: int
    n_flux_pixels: int
    used_unreliable_in_centroid: bool


@dataclass(frozen=True)
class EpochPosition:
    """Inverse-variance combined astrometric position for one observation epoch."""

    mjd: float
    ra: float
    dec: float
    sigma_ra_arcsec: float
    sigma_dec_arcsec: float
    x: float
    y: float
    sigma_x: float
    sigma_y: float
    n_frames_used: int
    n_frames_rejected: int


@dataclass(frozen=True)
class MotionFitResult:
    """Result of weighted least-squares proper motion and parallax fit."""

    t0_mjd: float
    ra0_deg: float
    dec0_deg: float
    sigma_ra0_arcsec: float
    sigma_dec0_arcsec: float
    pm_ra_arcsec_yr: float
    sigma_pm_ra_arcsec_yr: float
    pm_dec_arcsec_yr: float
    sigma_pm_dec_arcsec_yr: float
    pm_total_arcsec_yr: float
    sigma_pm_total_arcsec_yr: float
    pa_deg: float
    sigma_pa_deg: float
    parallax_arcsec: float | None
    sigma_parallax_arcsec: float | None
    parallax_fitted: bool
    parallax_fixed: bool
    chi2: float
    dof: int
    reduced_chi2: float
    uncertainties_inflated: bool
    n_epochs: int
    n_frames_used: int
    n_frames_rejected: int
    motion_significance: float
    covariance: np.ndarray
    parameter_names: tuple[str, ...]
    epochs: tuple[EpochPosition, ...]
    version: int | None
    warnings: list[str] = field(default_factory=list)


def measure_frame_centroid(
    frame: Frame,
    expected_x: float,
    expected_y: float,
    radii: Sequence[float] = (4.0, 3.0, 2.5),
    bg_inner_radius: float = 5.0,
    bg_outer_radius: float = 8.0,
    snr_threshold: float = 5.0,
) -> FrameCentroid | None:
    """Compute iterative shrinking-window flux-weighted centroid on a cleaned frame.

    Pixels flagged in `frame.mask` are excluded from all calculations.
    Pixels flagged in `frame.unreliable` are INCLUDED in the centroid position
    calculation (per Frame contract) and EXCLUDED from the photometric flux
    and SNR calculation.

    Analytical centroid variance from first-order error propagation of
    x_c = sum(x_i * w_i) / sum(w_i), where w_i = I_i - B:
        Var(x_c) = sum_{i in A} ((x_i - x_c)^2 * Var(w_i)) / (sum_{i in A} w_i)^2

    Args:
        frame: Cleaned and aligned SPHEREx Frame.
        expected_x: Initial guess x pixel coordinate (0-indexed).
        expected_y: Initial guess y pixel coordinate (0-indexed).
        radii: Sequence of shrinking aperture radii in pixels.
        bg_inner_radius: Inner radius of background annulus in pixels.
        bg_outer_radius: Outer radius of background annulus in pixels.
        snr_threshold: Minimum centroid-aperture SNR required to accept frame.

    Returns:
        FrameCentroid if valid and SNR >= snr_threshold, else None.
    """
    ny, nx = frame.image.shape
    yy, xx = np.indices((ny, nx), dtype=np.float64)

    valid_base = (
        (~frame.mask)
        & np.isfinite(frame.image)
        & np.isfinite(frame.variance)
        & (frame.variance > 0.0)
    )
    if frame.unreliable is not None:
        valid_phot = valid_base & (~frame.unreliable)
    else:
        valid_phot = valid_base

    cx = float(expected_x)
    cy = float(expected_y)

    # Estimate local background inside annulus around initial expected position
    dist_init = np.hypot(xx - cx, yy - cy)
    annulus = (
        valid_phot
        & (dist_init >= bg_inner_radius)
        & (dist_init <= bg_outer_radius)
    )
    if np.count_nonzero(annulus) >= 5:
        bg_pixels = frame.image[annulus]
        bg_level = float(np.median(bg_pixels))
        # Variance of the median of N pixels ~ (pi / 2) * mean(var) / N
        n_bg = int(np.count_nonzero(annulus))
        bg_var = float((np.pi / 2.0) * np.mean(frame.variance[annulus]) / n_bg)
    else:
        bg_level = 0.0
        bg_var = 0.0

    net_image = frame.image.astype(np.float64) - bg_level
    pixel_var = frame.variance.astype(np.float64) + bg_var

    ap_centroid = np.zeros_like(valid_base)
    for r in radii:
        dist = np.hypot(xx - cx, yy - cy)
        ap_centroid = valid_base & (dist <= r)
        if np.count_nonzero(ap_centroid) < 3:
            return None
        w = net_image[ap_centroid]
        w_sum = float(np.sum(w))
        if w_sum <= 0.0:
            return None
        cx = float(np.sum(xx[ap_centroid] * w) / w_sum)
        cy = float(np.sum(yy[ap_centroid] * w) / w_sum)
        if not (0.0 <= cx < nx and 0.0 <= cy < ny):
            return None

    # Final aperture around converged centroid
    final_r = float(radii[-1])
    dist_final = np.hypot(xx - cx, yy - cy)
    ap_centroid = valid_base & (dist_final <= final_r)
    n_cent_pix = int(np.count_nonzero(ap_centroid))
    if n_cent_pix < 3:
        return None

    w_cent = net_image[ap_centroid]
    v_cent = pixel_var[ap_centroid]
    w_sum = float(np.sum(w_cent))
    if w_sum <= 0.0:
        return None

    cx = float(np.sum(xx[ap_centroid] * w_cent) / w_sum)
    cy = float(np.sum(yy[ap_centroid] * w_cent) / w_sum)

    cent_noise = float(np.sqrt(np.sum(v_cent)))
    if cent_noise <= 0.0:
        return None
    snr = w_sum / cent_noise
    if snr < snr_threshold:
        return None

    dx = xx[ap_centroid] - cx
    dy = yy[ap_centroid] - cy
    var_x = float(np.sum((dx ** 2) * v_cent) / (w_sum ** 2))
    var_y = float(np.sum((dy ** 2) * v_cent) / (w_sum ** 2))
    if var_x <= 0.0 or var_y <= 0.0 or not (np.isfinite(var_x) and np.isfinite(var_y)):
        return None

    sigma_x = float(np.sqrt(var_x))
    sigma_y = float(np.sqrt(var_y))

    # Photometric flux strictly excludes unreliable pixels
    ap_flux = valid_phot & (dist_final <= final_r)
    n_flux_pix = int(np.count_nonzero(ap_flux))
    if n_flux_pix > 0:
        flux = float(np.sum(net_image[ap_flux]))
        sigma_flux = float(np.sqrt(np.sum(pixel_var[ap_flux])))
    else:
        flux = float("nan")
        sigma_flux = float("nan")

    used_unrel = bool(
        frame.unreliable is not None and np.any(frame.unreliable[ap_centroid])
    )

    return FrameCentroid(
        x=cx,
        y=cy,
        sigma_x=sigma_x,
        sigma_y=sigma_y,
        flux=flux,
        sigma_flux=sigma_flux,
        snr=snr,
        mjd=float(frame.mjd),
        n_centroid_pixels=n_cent_pix,
        n_flux_pixels=n_flux_pix,
        used_unreliable_in_centroid=used_unrel,
    )


def _pixel_to_sky_with_errors(
    wcs: WCS,
    x: float,
    y: float,
    sigma_x: float,
    sigma_y: float,
) -> tuple[float, float, float, float]:
    """Convert pixel coordinates and uncertainties to (ra, dec, sigma_ra*, sigma_dec) via local Jacobian."""
    ra0, dec0 = wcs.pixel_to_world_values(x, y)
    ra0 = float(ra0)
    dec0 = float(dec0)
    cos_dec = float(np.cos(np.deg2rad(dec0)))

    eps = 1e-3
    ra_dx, dec_dx = wcs.pixel_to_world_values(x + eps, y)
    ra_dy, dec_dy = wcs.pixel_to_world_values(x, y + eps)

    # Handle RA wrap-around near 0/360 deg
    dra_dx_deg = ((float(ra_dx) - ra0 + 180.0) % 360.0 - 180.0) / eps
    dra_dy_deg = ((float(ra_dy) - ra0 + 180.0) % 360.0 - 180.0) / eps
    ddec_dx_deg = (float(dec_dx) - dec0) / eps
    ddec_dy_deg = (float(dec_dy) - dec0) / eps

    # Convert derivatives to arcsec/pixel on tangent plane (RA includes cos(dec))
    dra_star_dx = dra_dx_deg * cos_dec * 3600.0
    dra_star_dy = dra_dy_deg * cos_dec * 3600.0
    ddec_dx = ddec_dx_deg * 3600.0
    ddec_dy = ddec_dy_deg * 3600.0

    var_ra_arcsec = (dra_star_dx * sigma_x) ** 2 + (dra_star_dy * sigma_y) ** 2
    var_dec_arcsec = (ddec_dx * sigma_x) ** 2 + (ddec_dy * sigma_y) ** 2

    return ra0, dec0, float(np.sqrt(var_ra_arcsec)), float(np.sqrt(var_dec_arcsec))


def combine_epoch_centroids(
    frames: Sequence[Frame],
    expected_ra: float,
    expected_dec: float,
    snr_threshold: float = 5.0,
    radii: Sequence[float] = (4.0, 3.0, 2.5),
    bg_inner_radius: float = 5.0,
    bg_outer_radius: float = 8.0,
) -> tuple[EpochPosition | None, list[FrameCentroid], list[str]]:
    """Measure centroids in all frames of an epoch and combine with inverse-variance weights.

    Args:
        frames: Aligned frames belonging to a single observation epoch.
        expected_ra: Expected target Right Ascension in decimal degrees.
        expected_dec: Expected target Declination in decimal degrees.
        snr_threshold: Minimum SNR threshold per frame.
        radii: Shrinking centroid aperture radii in pixels.
        bg_inner_radius: Inner radius of background annulus in pixels.
        bg_outer_radius: Outer radius of background annulus in pixels.

    Returns:
        Tuple of (EpochPosition or None if all frames rejected, list of FrameCentroids, warnings).
    """
    if not frames:
        raise ValueError("Cannot combine an empty sequence of frames.")

    warnings: list[str] = []
    versions = {f.version for f in frames if f.version is not None}
    if len(versions) > 1:
        raise ValueError(f"Mixed processing versions within epoch: {sorted(versions)}")

    ref_wcs = frames[0].wcs
    if ref_wcs is None:
        raise ValueError("Aligned frames must have a valid WCS to compute sky positions.")

    centroids: list[FrameCentroid] = []
    n_rejected = 0

    for frame in frames:
        wcs = frame.wcs if frame.wcs is not None else ref_wcs
        exp_x, exp_y = wcs.world_to_pixel_values(expected_ra, expected_dec)
        c = measure_frame_centroid(
            frame,
            expected_x=float(exp_x),
            expected_y=float(exp_y),
            radii=radii,
            bg_inner_radius=bg_inner_radius,
            bg_outer_radius=bg_outer_radius,
            snr_threshold=snr_threshold,
        )
        if c is None:
            n_rejected += 1
        else:
            centroids.append(c)

    if not centroids:
        warnings.append(
            f"All {len(frames)} frames in epoch rejected (SNR < {snr_threshold})."
        )
        return None, [], warnings

    wx = np.array([1.0 / (c.sigma_x ** 2) for c in centroids], dtype=np.float64)
    wy = np.array([1.0 / (c.sigma_y ** 2) for c in centroids], dtype=np.float64)
    xs = np.array([c.x for c in centroids], dtype=np.float64)
    ys = np.array([c.y for c in centroids], dtype=np.float64)
    mjds = np.array([c.mjd for c in centroids], dtype=np.float64)

    mean_x = float(np.sum(wx * xs) / np.sum(wx))
    mean_y = float(np.sum(wy * ys) / np.sum(wy))
    sigma_x = float(np.sqrt(1.0 / np.sum(wx)))
    sigma_y = float(np.sqrt(1.0 / np.sum(wy)))
    mean_mjd = float(np.mean(mjds))

    ra, dec, sigma_ra, sigma_dec = _pixel_to_sky_with_errors(
        ref_wcs, mean_x, mean_y, sigma_x, sigma_y
    )

    if any(c.used_unreliable_in_centroid for c in centroids):
        warnings.append(
            "One or more frames in epoch included unreliable/saturated pixels in centroiding "
            "(excluded from flux)."
        )

    epoch_pos = EpochPosition(
        mjd=mean_mjd,
        ra=ra,
        dec=dec,
        sigma_ra_arcsec=sigma_ra,
        sigma_dec_arcsec=sigma_dec,
        x=mean_x,
        y=mean_y,
        sigma_x=sigma_x,
        sigma_y=sigma_y,
        n_frames_used=len(centroids),
        n_frames_rejected=n_rejected,
    )
    return epoch_pos, centroids, warnings


def compute_parallax_factors(
    ra_deg: float, dec_deg: float, mjds: Sequence[float] | np.ndarray
) -> tuple[np.ndarray, np.ndarray]:
    """Compute barycentric parallax factors (P_alpha, P_delta) in AU.

    Given Earth's ICRS barycentric vector B = (X, Y, Z) in AU:
        P_alpha = X * sin(alpha) - Y * cos(alpha)
        P_delta = X * cos(alpha) * sin(delta) + Y * sin(alpha) * sin(delta) - Z * cos(delta)
    Such that the apparent tangent-plane shift is:
        Delta(alpha) * cos(delta) = parallax * P_alpha
        Delta(delta)              = parallax * P_delta

    Args:
        ra_deg: Reference Right Ascension in decimal degrees.
        dec_deg: Reference Declination in decimal degrees.
        mjds: Array or sequence of Modified Julian Dates.

    Returns:
        Tuple of 1D numpy arrays (p_alpha, p_delta) in AU.
    """
    times = Time(np.asarray(mjds, dtype=np.float64), format="mjd", scale="utc")
    bary = get_body_barycentric("earth", times)
    x_au = bary.x.to_value(u.au)
    y_au = bary.y.to_value(u.au)
    z_au = bary.z.to_value(u.au)

    ra_rad = np.deg2rad(ra_deg)
    dec_rad = np.deg2rad(dec_deg)

    sin_a = np.sin(ra_rad)
    cos_a = np.cos(ra_rad)
    sin_d = np.sin(dec_rad)
    cos_d = np.cos(dec_rad)

    p_alpha = x_au * sin_a - y_au * cos_a
    p_delta = x_au * cos_a * sin_d + y_au * sin_a * sin_d - z_au * cos_d
    return np.asarray(p_alpha, dtype=np.float64), np.asarray(p_delta, dtype=np.float64)


def fit_astrometric_motion(
    epochs: Sequence[EpochPosition],
    fixed_parallax_arcsec: float | None = None,
    version: int | None = None,
    extra_warnings: Sequence[str] = (),
) -> MotionFitResult:
    """Fit proper motion and parallax to multi-epoch sky positions via weighted least squares.

    Model:
        Delta_alpha*(t) = Delta_alpha*_0 + pm_ra * (t - t0) + parallax * P_alpha(t)
        Delta_delta(t)  = Delta_delta_0  + pm_dec * (t - t0) + parallax * P_delta(t)

    If len(epochs) < 3, parallax and proper motion are degenerate: fits 4 parameters
    (Delta_alpha*_0, Delta_delta_0, pm_ra, pm_dec), optionally subtracting a fixed
    literature parallax first, and records an explicit warning.

    Args:
        epochs: Sequence of at least 2 EpochPosition objects.
        fixed_parallax_arcsec: Optional literature parallax in arcsec. If provided
            (or when len(epochs) < 3), parallax is held fixed to this value.
        version: Processing version recorded in the result.
        extra_warnings: Additional warnings propagated from epoch centroiding.

    Returns:
        MotionFitResult containing fitted parameters, uncertainties, chi-squared,
        covariance matrix, and warnings.
    """
    if len(epochs) < 2:
        raise ValueError(
            f"At least 2 epochs are required to fit proper motion, got {len(epochs)}."
        )

    warnings: list[str] = list(extra_warnings)
    n_epochs = len(epochs)

    mjds = np.array([ep.mjd for ep in epochs], dtype=np.float64)
    t0_mjd = float(np.mean(mjds))
    dt_yr = (mjds - t0_mjd) / DAYS_PER_YEAR

    if np.ptp(mjds) < 1.0:
        warnings.append(
            f"Total time baseline is very short ({np.ptp(mjds):.2f} days); "
            "proper motion uncertainties may be large."
        )

    # Tangent-plane offsets relative to first epoch coordinates
    ref_ra = float(epochs[0].ra)
    ref_dec = float(epochs[0].dec)
    ref_coord = SkyCoord(ra=ref_ra * u.deg, dec=ref_dec * u.deg, frame="icrs")
    epoch_coords = SkyCoord(
        ra=np.array([ep.ra for ep in epochs], dtype=np.float64) * u.deg,
        dec=np.array([ep.dec for ep in epochs], dtype=np.float64) * u.deg,
        frame="icrs",
    )
    dra_arcsec, ddec_arcsec = ref_coord.spherical_offsets_to(epoch_coords)
    y_ra = dra_arcsec.to_value(u.arcsec)
    y_dec = ddec_arcsec.to_value(u.arcsec)

    sig_ra = np.array([ep.sigma_ra_arcsec for ep in epochs], dtype=np.float64)
    sig_dec = np.array([ep.sigma_dec_arcsec for ep in epochs], dtype=np.float64)

    p_alpha, p_delta = compute_parallax_factors(ref_ra, ref_dec, mjds)

    fit_parallax = (n_epochs >= 3) and (fixed_parallax_arcsec is None)
    parallax_fixed = False

    if n_epochs < 3:
        if fixed_parallax_arcsec is not None:
            parallax_fixed = True
            warnings.append(
                f"Fewer than 3 epochs ({n_epochs}): parallax and proper motion are degenerate. "
                f"Fit proper motion only with parallax fixed to {fixed_parallax_arcsec:.4f} arcsec."
            )
            y_ra = y_ra - fixed_parallax_arcsec * p_alpha
            y_dec = y_dec - fixed_parallax_arcsec * p_delta
        else:
            warnings.append(
                f"Fewer than 3 epochs ({n_epochs}): parallax and proper motion are degenerate. "
                "Fit proper motion only (parallax not fitted)."
            )
    elif fixed_parallax_arcsec is not None:
        parallax_fixed = True
        warnings.append(
            f"Parallax fixed to literature value ({fixed_parallax_arcsec:.4f} arcsec)."
        )
        y_ra = y_ra - fixed_parallax_arcsec * p_alpha
        y_dec = y_dec - fixed_parallax_arcsec * p_delta

    # Assemble observation vector y and design matrix X (2 * n_epochs rows)
    y_vec = np.concatenate([y_ra, y_dec])
    sig_vec = np.concatenate([sig_ra, sig_dec])
    weights = 1.0 / (sig_vec ** 2)

    zeros = np.zeros(n_epochs, dtype=np.float64)
    ones = np.ones(n_epochs, dtype=np.float64)

    if fit_parallax:
        param_names = ("dra0", "ddec0", "pm_ra", "pm_dec", "parallax")
        design_ra = np.column_stack([ones, zeros, dt_yr, zeros, p_alpha])
        design_dec = np.column_stack([zeros, ones, zeros, dt_yr, p_delta])
    else:
        param_names = ("dra0", "ddec0", "pm_ra", "pm_dec")
        design_ra = np.column_stack([ones, zeros, dt_yr, zeros])
        design_dec = np.column_stack([zeros, ones, zeros, dt_yr])

    design = np.vstack([design_ra, design_dec])
    n_params = design.shape[1]

    # Weighted least squares: (X^T W X) beta = X^T W y
    xw = design * weights[:, None]
    fisher = design.T @ xw
    rhs = design.T @ (weights * y_vec)

    try:
        cov = np.linalg.inv(fisher)
        beta = cov @ rhs
    except np.linalg.LinAlgError as exc:
        raise ValueError(
            "Singular normal matrix in astrometric fit; epochs may be degenerate in time."
        ) from exc

    residuals = y_vec - (design @ beta)
    chi2 = float(np.sum(weights * (residuals ** 2)))
    dof = int(2 * n_epochs - n_params)
    reduced_chi2 = chi2 / dof if dof > 0 else float("nan")

    uncertainties_inflated = False
    if dof > 0 and reduced_chi2 > 1.0:
        cov = cov * reduced_chi2
        uncertainties_inflated = True
        warnings.append(
            f"Reduced chi-squared is {reduced_chi2:.2f} (> 1): parameter uncertainties "
            f"inflated by sqrt(reduced_chi2) = {np.sqrt(reduced_chi2):.2f}."
        )

    dra0_arcsec = float(beta[0])
    ddec0_arcsec = float(beta[1])
    pm_ra = float(beta[2])
    pm_dec = float(beta[3])

    sigma_ra0 = float(np.sqrt(cov[0, 0]))
    sigma_dec0 = float(np.sqrt(cov[1, 1]))
    sigma_pm_ra = float(np.sqrt(cov[2, 2]))
    sigma_pm_dec = float(np.sqrt(cov[3, 3]))

    if fit_parallax:
        parallax_val: float | None = float(beta[4])
        sigma_parallax: float | None = float(np.sqrt(cov[4, 4]))
    elif parallax_fixed:
        parallax_val = float(fixed_parallax_arcsec)  # type: ignore[arg-type]
        sigma_parallax = 0.0
    else:
        parallax_val = None
        sigma_parallax = None

    # Convert tangent offset at t0 back to ICRS RA0, Dec0
    coord_t0 = ref_coord.spherical_offsets_by(
        dra0_arcsec * u.arcsec, ddec0_arcsec * u.arcsec
    )
    ra0_deg = float(coord_t0.ra.deg)
    dec0_deg = float(coord_t0.dec.deg)

    # Total proper motion and position angle error propagation using 2x2 pm covariance
    pm_cov = cov[2:4, 2:4]
    pm_total = float(np.hypot(pm_ra, pm_dec))
    if pm_total > 0.0:
        grad_pm = np.array([pm_ra / pm_total, pm_dec / pm_total], dtype=np.float64)
        var_pm_total = float(grad_pm @ pm_cov @ grad_pm)
        sigma_pm_total = float(np.sqrt(max(var_pm_total, 0.0)))

        pa_rad = float(np.arctan2(pm_ra, pm_dec)) % (2.0 * np.pi)
        pa_deg = float(np.rad2deg(pa_rad))
        grad_pa_deg = np.rad2deg(
            np.array([pm_dec / (pm_total ** 2), -pm_ra / (pm_total ** 2)], dtype=np.float64)
        )
        var_pa = float(grad_pa_deg @ pm_cov @ grad_pa_deg)
        sigma_pa_deg = float(np.sqrt(max(var_pa, 0.0)))
    else:
        sigma_pm_total = float(np.hypot(sigma_pm_ra, sigma_pm_dec))
        pa_deg = 0.0
        sigma_pa_deg = 180.0

    motion_significance = (
        pm_total / sigma_pm_total if sigma_pm_total > 0.0 else 0.0
    )

    n_frames_used = sum(ep.n_frames_used for ep in epochs)
    n_frames_rejected = sum(ep.n_frames_rejected for ep in epochs)

    return MotionFitResult(
        t0_mjd=t0_mjd,
        ra0_deg=ra0_deg,
        dec0_deg=dec0_deg,
        sigma_ra0_arcsec=sigma_ra0,
        sigma_dec0_arcsec=sigma_dec0,
        pm_ra_arcsec_yr=pm_ra,
        sigma_pm_ra_arcsec_yr=sigma_pm_ra,
        pm_dec_arcsec_yr=pm_dec,
        sigma_pm_dec_arcsec_yr=sigma_pm_dec,
        pm_total_arcsec_yr=pm_total,
        sigma_pm_total_arcsec_yr=sigma_pm_total,
        pa_deg=pa_deg,
        sigma_pa_deg=sigma_pa_deg,
        parallax_arcsec=parallax_val,
        sigma_parallax_arcsec=sigma_parallax,
        parallax_fitted=fit_parallax,
        parallax_fixed=parallax_fixed,
        chi2=chi2,
        dof=dof,
        reduced_chi2=reduced_chi2,
        uncertainties_inflated=uncertainties_inflated,
        n_epochs=n_epochs,
        n_frames_used=n_frames_used,
        n_frames_rejected=n_frames_rejected,
        motion_significance=motion_significance,
        covariance=cov,
        parameter_names=param_names,
        epochs=tuple(epochs),
        version=version,
        warnings=warnings,
    )


def measure_and_fit_epochs(
    epoch_frame_groups: Sequence[Sequence[Frame]],
    target_ra: float,
    target_dec: float,
    snr_threshold: float = 5.0,
    fixed_parallax_arcsec: float | None = None,
    radii: Sequence[float] = (4.0, 3.0, 2.5),
    bg_inner_radius: float = 5.0,
    bg_outer_radius: float = 8.0,
) -> MotionFitResult:
    """Measure per-epoch positions from grouped, aligned frames and fit motion.

    Args:
        epoch_frame_groups: Sequence of epochs, each containing cleaned, aligned Frame objects.
        target_ra: Expected target RA in decimal degrees.
        target_dec: Expected target Dec in decimal degrees.
        snr_threshold: Minimum frame SNR threshold.
        fixed_parallax_arcsec: Optional fixed parallax in arcseconds.
        radii: Shrinking centroid aperture radii in pixels.
        bg_inner_radius: Inner radius of background annulus in pixels.
        bg_outer_radius: Outer radius of background annulus in pixels.

    Returns:
        MotionFitResult for the target.
    """
    all_versions = {
        f.version
        for group in epoch_frame_groups
        for f in group
        if f.version is not None
    }
    if len(all_versions) > 1:
        raise ValueError(
            f"Cannot mix different processing versions across epochs: {sorted(all_versions)}"
        )
    version = next(iter(all_versions)) if len(all_versions) == 1 else None

    epochs: list[EpochPosition] = []
    warnings: list[str] = []
    total_rejected = 0

    for idx, frames in enumerate(epoch_frame_groups):
        ep_pos, _, ep_warns = combine_epoch_centroids(
            frames,
            expected_ra=target_ra,
            expected_dec=target_dec,
            snr_threshold=snr_threshold,
            radii=radii,
            bg_inner_radius=bg_inner_radius,
            bg_outer_radius=bg_outer_radius,
        )
        for w in ep_warns:
            warnings.append(f"Epoch {idx + 1}: {w}")
        if ep_pos is not None:
            epochs.append(ep_pos)
            total_rejected += ep_pos.n_frames_rejected
        else:
            total_rejected += len(frames)

    if total_rejected > 0:
        warnings.append(
            f"Rejected {total_rejected} frame(s) below SNR threshold ({snr_threshold})."
        )

    return fit_astrometric_motion(
        epochs=epochs,
        fixed_parallax_arcsec=fixed_parallax_arcsec,
        version=version,
        extra_warnings=warnings,
    )
