# AI-assisted: written with AI pair-programming assistant.
"""Blind multi-epoch search for moving point sources in SPHEREx cutout fields.

Pre-registered Detection, Classification, and False-Match Criteria:
1. Per-epoch source detection:
   - Each observation epoch is coadded with ``processing.stack_frames`` and converted
     to a noise-normalised significance map $S = \\text{image} / \\sigma_{\\text{prop}}$.
   - Local maxima with $S \\ge \\text{threshold}$ (default $5.0\\,\\sigma$) and minimum
     Euclidean separation $\\ge 2.0$ pixels are extracted.
   - Any peak whose position or centroid lies within ``edge_margin_px = 3.0`` pixels of
     the cutout border, or whose circular aperture ($r = 2.0$ px) touches a masked or
     unreliable pixel, is excluded and recorded with its explicit exclusion reason
     (``"near_edge"``, ``"masked_pixel"``, or ``"unreliable_pixel"``).
2. Cross-epoch matching and classification:
   - Given epoch baseline $\\Delta t = |t_B - t_A| / 365.25\\,\\text{yr}$, the search radius
     is $R = \\text{max\\_pm} \\times \\Delta t$ (default $\\text{max\\_pm} = 15.0''/\\text{yr}$).
   - Epoch A and Epoch B detections within $R$ are paired one-to-one in order of
     increasing angular separation $\\theta_{AB}$ so stationary stars ($\\theta \\approx 0$)
     pair with themselves before wider offsets are considered.
   - A matched pair with motion significance $\\theta / \\sigma_\\theta < 3.0$ is classified
     as ``"stationary"``; a matched pair with $\\theta / \\sigma_\\theta \\ge 3.0$ and
     $\\theta \\le R$ is classified as ``"mover_candidate"``. Sources without a partner
     within $R$ are classified as ``"unmatched"``.
3. Chance-coincidence (false-mover) estimation:
   - Epoch B source coordinates are shifted by ``n_random_trials = 100`` random toroidal
     offsets of magnitude strictly larger than $R$ across the usable field area, repeating
     the cross-match and classification each time to estimate ``expected_false_movers``.
4. Scientific honesty disclaimer:
   - Every output (CSV, JSON, and returned data structures) states explicitly that
     reported entries are astrometric mover candidates, not confirmed detections.
"""

from __future__ import annotations

import csv
import json
import math
from collections.abc import Callable, Sequence
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

import numpy as np
from scipy.ndimage import maximum_filter

import data
import processing
from frame import Frame

DAYS_PER_YEAR: float = 365.25
DEFAULT_FOV_ARCSEC: float = 480.0
DEFAULT_SNR_THRESHOLD: float = 5.0
DEFAULT_MAX_PM_ARCSEC_YR: float = 15.0
DEFAULT_MIN_SEPARATION_PX: float = 2.0
DEFAULT_EDGE_MARGIN_PX: float = 3.0
DEFAULT_APERTURE_RADIUS_PX: float = 2.0
DEFAULT_STATIONARY_SIGMA: float = 3.0
DEFAULT_RANDOM_TRIALS: int = 100
CANDIDATE_DISCLAIMER: str = (
    "CANDIDATES ONLY: Reported moving sources are astrometric candidates, not confirmed "
    "detections. Visual inspection and multi-epoch confirmation are required."
)


@dataclass(frozen=True)
class DetectedSource:
    """Single point source detected in an epoch stack."""

    epoch: int
    source_index: int
    x_peak: int
    y_peak: int
    x: float
    y: float
    sigma_x: float
    sigma_y: float
    ra: float
    dec: float
    sigma_ra_arcsec: float
    sigma_dec_arcsec: float
    flux: float
    flux_sigma: float
    snr: float
    peak_snr: float


@dataclass(frozen=True)
class ExcludedSource:
    """Local maximum excluded during per-epoch quality filtering, with reason."""

    epoch: int
    x_peak: int
    y_peak: int
    peak_snr: float
    reason: str
    detail: str


@dataclass(frozen=True)
class MoverCandidate:
    """Cross-matched or single-epoch source entry from the blind mover search."""

    id: str
    rank: int | None
    classification: str
    ra_epoch_a: float | None
    dec_epoch_a: float | None
    ra_epoch_b: float | None
    dec_epoch_b: float | None
    offset_arcsec: float | None
    offset_sigma_arcsec: float | None
    pm_arcsec_per_yr: float | None
    pm_sigma_arcsec_per_yr: float | None
    pm_ra_arcsec_per_yr: float | None
    pm_ra_sigma_arcsec_per_yr: float | None
    pm_dec_arcsec_per_yr: float | None
    pm_dec_sigma_arcsec_per_yr: float | None
    pa_deg: float | None
    pa_sigma_deg: float | None
    snr_epoch_a: float | None
    snr_epoch_b: float | None
    motion_significance: float | None
    quality_flags: tuple[str, ...]
    disclaimer: str = CANDIDATE_DISCLAIMER


@dataclass
class MoverSearchResult:
    """Full output of a blind moving-object search on one sky field."""

    disclaimer: str
    ra_center: float
    dec_center: float
    fov_arcsec: float
    pixel_scale_arcsec: float
    detector: int
    wavelength_window_um: tuple[float, float]
    epoch_a: int
    epoch_b: int
    mjd_epoch_a: float
    mjd_epoch_b: float
    baseline_years: float
    search_radius_arcsec: float
    threshold_sigma: float
    max_pm_arcsec_per_yr: float
    n_sources_epoch_a: int
    n_sources_epoch_b: int
    n_excluded_epoch_a: int
    n_excluded_epoch_b: int
    n_stationary: int
    n_mover_candidates: int
    n_unmatched: int
    expected_false_movers: float
    expected_false_movers_sigma: float
    analytic_false_movers: float
    n_random_trials: int
    data_versions: list[str]
    warnings: list[str]
    candidates: list[MoverCandidate] = field(default_factory=list)
    all_matches: list[MoverCandidate] = field(default_factory=list)
    sources_epoch_a: list[DetectedSource] = field(default_factory=list)
    sources_epoch_b: list[DetectedSource] = field(default_factory=list)
    excluded_sources: list[ExcludedSource] = field(default_factory=list)


def _pixel_scale_from_wcs(frame: Frame, fallback: float = processing.PIXEL_SCALE_ARCSEC) -> float:
    """Extract pixel scale in arcsec/pixel from frame WCS, or return fallback."""
    if frame.wcs is None:
        return fallback
    try:
        scales_deg = frame.wcs.celestial.proj_plane_pixel_scales()
        vals = [float(getattr(s, "value", s)) for s in scales_deg]
        mean_deg = float(np.mean(vals[:2]))
        if np.isfinite(mean_deg) and mean_deg > 0:
            return mean_deg * 3600.0
    except Exception:
        pass
    return fallback


def detect_epoch_sources(
    epoch_frames: Sequence[Frame],
    epoch_index: int = 0,
    *,
    threshold: float = DEFAULT_SNR_THRESHOLD,
    min_separation_px: float = DEFAULT_MIN_SEPARATION_PX,
    edge_margin_px: float = DEFAULT_EDGE_MARGIN_PX,
    aperture_radius_px: float = DEFAULT_APERTURE_RADIUS_PX,
    annulus_inner_px: float = 4.0,
    annulus_outer_px: float = 7.0,
) -> tuple[ processing.StackResult, np.ndarray, list[DetectedSource], list[ExcludedSource]]:
    """Stack one epoch and detect, measure, and filter point sources on the SNR map.

    Args:
        epoch_frames: Aligned ``Frame`` sequence belonging to one observation epoch.
        epoch_index: Integer index of the epoch.
        threshold: Minimum peak significance in units of propagated sigma (default 5.0).
        min_separation_px: Minimum pixel distance between distinct local maxima (default 2.0).
        edge_margin_px: Minimum distance in pixels from cutout edges (default 3.0).
        aperture_radius_px: Circular aperture radius for centroid and flux (default 2.0).
        annulus_inner_px: Inner radius of background annulus in pixels.
        annulus_outer_px: Outer radius of background annulus in pixels.

    Returns:
        Tuple ``(stack, snr_map, detected_sources, excluded_sources)``.
    """
    if not epoch_frames:
        raise ValueError("epoch_frames must not be empty")
    if not np.isfinite(threshold) or threshold <= 0:
        raise ValueError(f"threshold must be positive, got {threshold}")
    if not np.isfinite(min_separation_px) or min_separation_px < 1.0:
        raise ValueError(f"min_separation_px must be >= 1.0, got {min_separation_px}")
    if not np.isfinite(edge_margin_px) or edge_margin_px < 0:
        raise ValueError(f"edge_margin_px must be >= 0, got {edge_margin_px}")

    stack = processing.stack_frames(epoch_frames)
    sigma = stack.sigma
    ny, nx = stack.image.shape

    valid_snr = np.isfinite(stack.image) & np.isfinite(sigma) & (sigma > 0)
    snr_map = np.full((ny, nx), np.nan, dtype=np.float64)
    np.divide(stack.image, sigma, out=snr_map, where=valid_snr)

    # Composite defect masks across the epoch frames
    any_frame_masked = np.any(
        np.stack([np.asarray(f.mask, dtype=bool) for f in epoch_frames], axis=0),
        axis=0,
    )
    stack_masked = (stack.n_frames <= 0) | (~valid_snr) | any_frame_masked

    unrel_arrays = [
        np.asarray(f.unreliable, dtype=bool)
        for f in epoch_frames
        if f.unreliable is not None
    ]
    any_unreliable = (
        np.any(np.stack(unrel_arrays, axis=0), axis=0)
        if unrel_arrays
        else np.zeros((ny, nx), dtype=bool)
    )

    # Find 8-connected local maxima above threshold, then enforce min_separation_px greedily
    work_snr = np.where(np.isfinite(snr_map), snr_map, -np.inf)
    local_max = (work_snr >= threshold) & (work_snr == maximum_filter(work_snr, size=3, mode="constant", cval=-np.inf))
    cand_coords = np.argwhere(local_max)
    if cand_coords.size == 0:
        return stack, snr_map, [], []

    cand_snr = np.array([work_snr[int(y), int(x)] for y, x in cand_coords], dtype=np.float64)
    order = np.argsort(-cand_snr)

    kept_peaks: list[tuple[int, int, float]] = []
    for idx in order:
        yp, xp = int(cand_coords[idx, 0]), int(cand_coords[idx, 1])
        psnr = float(cand_snr[idx])
        if any(math.hypot(xp - kx, yp - ky) < min_separation_px - 1e-9 for ky, kx, _ in kept_peaks):
            continue
        kept_peaks.append((yp, xp, psnr))

    yy, xx = np.mgrid[0:ny, 0:nx]
    wcs = epoch_frames[0].wcs.celestial if epoch_frames[0].wcs is not None else None
    pix_scale = _pixel_scale_from_wcs(epoch_frames[0])

    detected: list[DetectedSource] = []
    excluded: list[ExcludedSource] = []

    for yp, xp, psnr in kept_peaks:
        # 1. Edge check on peak coordinate
        if (
            xp < edge_margin_px - 1e-9
            or xp > (nx - 1) - edge_margin_px + 1e-9
            or yp < edge_margin_px - 1e-9
            or yp > (ny - 1) - edge_margin_px + 1e-9
        ):
            excluded.append(
                ExcludedSource(
                    epoch=epoch_index,
                    x_peak=xp,
                    y_peak=yp,
                    peak_snr=psnr,
                    reason="near_edge",
                    detail=f"Peak ({xp}, {yp}) is within {edge_margin_px:.1f} px of cutout edge ({nx}x{ny}).",
                )
            )
            continue

        dist_peak = np.hypot(xx - float(xp), yy - float(yp))
        ap_mask = dist_peak <= aperture_radius_px

        # 2. Masked pixel check
        if bool(np.any(stack_masked[ap_mask])):
            excluded.append(
                ExcludedSource(
                    epoch=epoch_index,
                    x_peak=xp,
                    y_peak=yp,
                    peak_snr=psnr,
                    reason="masked_pixel",
                    detail=f"Aperture around ({xp}, {yp}) touches a masked detector pixel.",
                )
            )
            continue

        # 3. Unreliable pixel check
        if bool(np.any(any_unreliable[ap_mask])):
            excluded.append(
                ExcludedSource(
                    epoch=epoch_index,
                    x_peak=xp,
                    y_peak=yp,
                    peak_snr=psnr,
                    reason="unreliable_pixel",
                    detail=f"Aperture around ({xp}, {yp}) touches an unreliable/saturated pixel.",
                )
            )
            continue

        # 4. Local background annulus and flux-weighted centroid
        ann_mask = (
            (dist_peak >= annulus_inner_px)
            & (dist_peak <= annulus_outer_px)
            & (~stack_masked)
            & (~any_unreliable)
        )
        bg = float(np.median(stack.image[ann_mask])) if np.any(ann_mask) else 0.0

        ap_img = stack.image[ap_mask] - bg
        ap_var = stack.variance[ap_mask]
        ap_x = xx[ap_mask].astype(np.float64)
        ap_y = yy[ap_mask].astype(np.float64)

        pos_w = np.maximum(0.0, ap_img)
        w_sum = float(np.sum(pos_w))
        if w_sum <= 0 or not np.isfinite(w_sum):
            excluded.append(
                ExcludedSource(
                    epoch=epoch_index,
                    x_peak=xp,
                    y_peak=yp,
                    peak_snr=psnr,
                    reason="nonpositive_flux",
                    detail=f"Aperture around ({xp}, {yp}) has non-positive background-subtracted flux.",
                )
            )
            continue

        xc = float(np.sum(ap_x * pos_w) / w_sum)
        yc = float(np.sum(ap_y * pos_w) / w_sum)

        if (
            xc < edge_margin_px - 1e-9
            or xc > (nx - 1) - edge_margin_px + 1e-9
            or yc < edge_margin_px - 1e-9
            or yc > (ny - 1) - edge_margin_px + 1e-9
        ):
            excluded.append(
                ExcludedSource(
                    epoch=epoch_index,
                    x_peak=xp,
                    y_peak=yp,
                    peak_snr=psnr,
                    reason="near_edge",
                    detail=f"Centroid ({xc:.2f}, {yc:.2f}) is within {edge_margin_px:.1f} px of cutout edge.",
                )
            )
            continue

        var_xc = float(np.sum(((ap_x - xc) ** 2) * ap_var) / (w_sum**2))
        var_yc = float(np.sum(((ap_y - yc) ** 2) * ap_var) / (w_sum**2))
        sigma_x = float(math.sqrt(max(var_xc, 1e-6)))
        sigma_y = float(math.sqrt(max(var_yc, 1e-6)))

        flux = float(np.sum(ap_img))
        flux_sigma = float(math.sqrt(max(float(np.sum(ap_var)), 1e-12)))
        ap_snr = float(flux / flux_sigma) if flux > 0 else psnr
        snr_out = float(max(ap_snr, psnr))

        if wcs is not None:
            ra_val, dec_val = wcs.pixel_to_world_values(xc, yc)
            ra_deg = float(np.asarray(ra_val).item()) % 360.0
            dec_deg = float(np.asarray(dec_val).item())
        else:
            ra_deg = float(xc * pix_scale / 3600.0)
            dec_deg = float(yc * pix_scale / 3600.0)

        detected.append(
            DetectedSource(
                epoch=epoch_index,
                source_index=len(detected),
                x_peak=xp,
                y_peak=yp,
                x=xc,
                y=yc,
                sigma_x=sigma_x,
                sigma_y=sigma_y,
                ra=ra_deg,
                dec=dec_deg,
                sigma_ra_arcsec=float(sigma_x * pix_scale),
                sigma_dec_arcsec=float(sigma_y * pix_scale),
                flux=flux,
                flux_sigma=flux_sigma,
                snr=snr_out,
                peak_snr=psnr,
            )
        )

    return stack, snr_map, detected, excluded


def _sky_offset_arcsec(
    ra_a: float,
    dec_a: float,
    ra_b: float,
    dec_b: float,
) -> tuple[float, float, float]:
    """Compute tangent-plane offsets ``(dra_east_arcsec, ddec_north_arcsec, total_arcsec)`` from A to B."""
    mean_dec_rad = math.radians(0.5 * (dec_a + dec_b))
    dra_deg = ((ra_b - ra_a + 180.0) % 360.0) - 180.0
    dra_arcsec = dra_deg * math.cos(mean_dec_rad) * 3600.0
    ddec_arcsec = (dec_b - dec_a) * 3600.0
    total_arcsec = math.hypot(dra_arcsec, ddec_arcsec)
    return dra_arcsec, ddec_arcsec, total_arcsec


def crossmatch_epoch_sources(
    sources_a: Sequence[DetectedSource],
    sources_b: Sequence[DetectedSource],
    *,
    baseline_years: float,
    search_radius_arcsec: float,
    stationary_sigma: float = DEFAULT_STATIONARY_SIGMA,
) -> list[MoverCandidate]:
    """Cross-match Epoch A and Epoch B detections within ``search_radius_arcsec`` and classify.

    Matches pairs one-to-one in order of ascending separation so stationary stars
    ($\\theta \\approx 0$) pair with their own counterparts first.
    """
    if baseline_years <= 0 or not np.isfinite(baseline_years):
        raise ValueError(f"baseline_years must be positive, got {baseline_years}")
    if search_radius_arcsec < 0 or not np.isfinite(search_radius_arcsec):
        raise ValueError(f"search_radius_arcsec must be non-negative, got {search_radius_arcsec}")

    candidate_pairs: list[tuple[float, int, int, float, float]] = []
    for i, sa in enumerate(sources_a):
        for j, sb in enumerate(sources_b):
            dra, ddec, sep = _sky_offset_arcsec(sa.ra, sa.dec, sb.ra, sb.dec)
            if sep <= search_radius_arcsec + 1e-9:
                candidate_pairs.append((sep, i, j, dra, ddec))

    candidate_pairs.sort(key=lambda item: item[0])

    matched_a: set[int] = set()
    matched_b: set[int] = set()
    mover_entries: list[MoverCandidate] = []
    stationary_entries: list[MoverCandidate] = []
    unmatched_entries: list[MoverCandidate] = []

    for sep, i, j, dra, ddec in candidate_pairs:
        if i in matched_a or j in matched_b:
            continue
        matched_a.add(i)
        matched_b.add(j)
        sa = sources_a[i]
        sb = sources_b[j]

        sig_dra = math.hypot(sa.sigma_ra_arcsec, sb.sigma_ra_arcsec)
        sig_ddec = math.hypot(sa.sigma_dec_arcsec, sb.sigma_dec_arcsec)
        if sep > 1e-9:
            sig_sep = math.sqrt((dra * sig_dra) ** 2 + (ddec * sig_ddec) ** 2) / sep
            pa_deg = math.degrees(math.atan2(dra, ddec)) % 360.0
            pa_sigma_deg = math.degrees(
                math.sqrt((ddec * sig_dra) ** 2 + (dra * sig_ddec) ** 2) / (sep**2)
            )
        else:
            sig_sep = math.hypot(sig_dra, sig_ddec) / math.sqrt(2.0)
            pa_deg = 0.0
            pa_sigma_deg = 180.0

        sig_sep = max(sig_sep, 1e-6)
        significance = sep / sig_sep

        pm_ra = dra / baseline_years
        pm_ra_sig = sig_dra / baseline_years
        pm_dec = ddec / baseline_years
        pm_dec_sig = sig_ddec / baseline_years
        pm_tot = sep / baseline_years
        pm_tot_sig = sig_sep / baseline_years

        if significance < stationary_sigma:
            classification = "stationary"
            qflags = ("candidate_not_detection", "stationary_3sigma")
        else:
            classification = "mover_candidate"
            qflags = ("candidate_not_detection", "significant_offset")

        entry = MoverCandidate(
            id="",
            rank=None,
            classification=classification,
            ra_epoch_a=sa.ra,
            dec_epoch_a=sa.dec,
            ra_epoch_b=sb.ra,
            dec_epoch_b=sb.dec,
            offset_arcsec=sep,
            offset_sigma_arcsec=sig_sep,
            pm_arcsec_per_yr=pm_tot,
            pm_sigma_arcsec_per_yr=pm_tot_sig,
            pm_ra_arcsec_per_yr=pm_ra,
            pm_ra_sigma_arcsec_per_yr=pm_ra_sig,
            pm_dec_arcsec_per_yr=pm_dec,
            pm_dec_sigma_arcsec_per_yr=pm_dec_sig,
            pa_deg=pa_deg,
            pa_sigma_deg=pa_sigma_deg,
            snr_epoch_a=sa.snr,
            snr_epoch_b=sb.snr,
            motion_significance=significance,
            quality_flags=qflags,
        )
        if classification == "mover_candidate":
            mover_entries.append(entry)
        else:
            stationary_entries.append(entry)

    for i, sa in enumerate(sources_a):
        if i not in matched_a:
            unmatched_entries.append(
                MoverCandidate(
                    id="",
                    rank=None,
                    classification="unmatched",
                    ra_epoch_a=sa.ra,
                    dec_epoch_a=sa.dec,
                    ra_epoch_b=None,
                    dec_epoch_b=None,
                    offset_arcsec=None,
                    offset_sigma_arcsec=None,
                    pm_arcsec_per_yr=None,
                    pm_sigma_arcsec_per_yr=None,
                    pm_ra_arcsec_per_yr=None,
                    pm_ra_sigma_arcsec_per_yr=None,
                    pm_dec_arcsec_per_yr=None,
                    pm_dec_sigma_arcsec_per_yr=None,
                    pa_deg=None,
                    pa_sigma_deg=None,
                    snr_epoch_a=sa.snr,
                    snr_epoch_b=None,
                    motion_significance=None,
                    quality_flags=(
                        "candidate_not_detection",
                        "unmatched_epoch_a_only",
                        "possible_transient_artefact_or_left_field",
                    ),
                )
            )

    for j, sb in enumerate(sources_b):
        if j not in matched_b:
            unmatched_entries.append(
                MoverCandidate(
                    id="",
                    rank=None,
                    classification="unmatched",
                    ra_epoch_a=None,
                    dec_epoch_a=None,
                    ra_epoch_b=sb.ra,
                    dec_epoch_b=sb.dec,
                    offset_arcsec=None,
                    offset_sigma_arcsec=None,
                    pm_arcsec_per_yr=None,
                    pm_sigma_arcsec_per_yr=None,
                    pm_ra_arcsec_per_yr=None,
                    pm_ra_sigma_arcsec_per_yr=None,
                    pm_dec_arcsec_per_yr=None,
                    pm_dec_sigma_arcsec_per_yr=None,
                    pa_deg=None,
                    pa_sigma_deg=None,
                    snr_epoch_a=None,
                    snr_epoch_b=sb.snr,
                    motion_significance=None,
                    quality_flags=(
                        "candidate_not_detection",
                        "unmatched_epoch_b_only",
                        "possible_transient_artefact_or_entered_field",
                    ),
                )
            )

    mover_entries.sort(
        key=lambda c: (
            -(c.motion_significance or 0.0),
            -math.hypot(c.snr_epoch_a or 0.0, c.snr_epoch_b or 0.0),
        )
    )
    stationary_entries.sort(key=lambda c: -(c.snr_epoch_a or 0.0))

    ranked_all: list[MoverCandidate] = []
    for r_idx, mc in enumerate(mover_entries, start=1):
        ranked_all.append(
            MoverCandidate(
                **{
                    **asdict(mc),
                    "id": f"mover_{r_idx:03d}",
                    "rank": r_idx,
                }
            )
        )
    for s_idx, sc in enumerate(stationary_entries, start=1):
        ranked_all.append(
            MoverCandidate(
                **{
                    **asdict(sc),
                    "id": f"stat_{s_idx:03d}",
                    "rank": None,
                }
            )
        )
    for u_idx, uc in enumerate(unmatched_entries, start=1):
        ranked_all.append(
            MoverCandidate(
                **{
                    **asdict(uc),
                    "id": f"unmatched_{u_idx:03d}",
                    "rank": None,
                }
            )
        )

    return ranked_all


def analytic_false_mover_expectation(
    n_epoch_a: int,
    n_epoch_b: int,
    search_radius_arcsec: float,
    field_area_arcsec2: float,
    min_mover_offset_arcsec: float = 0.0,
) -> float:
    """Return the analytical Poisson expectation for chance mover matches in ``field_area_arcsec2``.

    For $N_A$ sources in Epoch A and density $\\rho_B = N_B / A$ in Epoch B, the expected
    number of Epoch A sources with a chance neighbour in the mover annulus
    $[r_{\\min}, R]$ is $N_A \\, (1 - \\exp(-\\rho_B \\pi (R^2 - r_{\\min}^2)))$.
    """
    if n_epoch_a <= 0 or n_epoch_b <= 0 or search_radius_arcsec <= min_mover_offset_arcsec or field_area_arcsec2 <= 0:
        return 0.0
    annulus_area = math.pi * (search_radius_arcsec**2 - max(0.0, min_mover_offset_arcsec) ** 2)
    density_b = float(n_epoch_b) / float(field_area_arcsec2)
    prob_match = 1.0 - math.exp(-density_b * annulus_area)
    return float(n_epoch_a * prob_match)


def estimate_false_mover_rate(
    sources_a: Sequence[DetectedSource],
    sources_b: Sequence[DetectedSource],
    *,
    baseline_years: float,
    search_radius_arcsec: float,
    field_width_arcsec: float,
    field_height_arcsec: float,
    ra_center: float,
    dec_center: float,
    stationary_sigma: float = DEFAULT_STATIONARY_SIGMA,
    n_trials: int = DEFAULT_RANDOM_TRIALS,
    seed: int = 42,
) -> tuple[float, float, float]:
    """Estimate false mover candidates via ``n_trials`` random shifts larger than ``R``.

    Shifts Epoch B positions toroidally within the usable field box by stratified
    random 2D offsets $(\\Delta x, \\Delta y)$ whose direct and wrapped magnitudes are
    strictly larger than ``search_radius_arcsec``, breaking all true physical pairs while
    preserving the exact source density.

    Returns:
        Tuple ``(mean_false_movers, std_error_false_movers, analytic_expectation)``.
    """
    if n_trials < 1:
        raise ValueError(f"n_trials must be >= 1, got {n_trials}")

    usable_w = max(float(field_width_arcsec), 4.0 * search_radius_arcsec + 10.0)
    usable_h = max(float(field_height_arcsec), 4.0 * search_radius_arcsec + 10.0)
    area_arcsec2 = usable_w * usable_h

    if not sources_a or not sources_b or search_radius_arcsec <= 0:
        return 0.0, 0.0, 0.0

    # Estimate typical 3-sigma stationary radius for analytic comparison
    med_sig_a = float(np.median([ math.hypot(s.sigma_ra_arcsec, s.sigma_dec_arcsec) for s in sources_a ]))
    med_sig_b = float(np.median([ math.hypot(s.sigma_ra_arcsec, s.sigma_dec_arcsec) for s in sources_b ]))
    r_min_3sigma = stationary_sigma * math.hypot(med_sig_a, med_sig_b) / math.sqrt(2.0)
    r_min_eff = min(r_min_3sigma, 0.5 * search_radius_arcsec)
    analytic = analytic_false_mover_expectation(
        len(sources_a),
        len(sources_b),
        search_radius_arcsec,
        area_arcsec2,
        min_mover_offset_arcsec=r_min_eff,
    )

    cos_dec = max(0.05, math.cos(math.radians(dec_center)))
    a_offsets = [
        _sky_offset_arcsec(ra_center, dec_center, s.ra, s.dec)[:2] for s in sources_a
    ]
    b_offsets = [
        _sky_offset_arcsec(ra_center, dec_center, s.ra, s.dec)[:2] for s in sources_b
    ]

    margin = min(search_radius_arcsec * 1.15 + 0.5, 0.4 * min(usable_w, usable_h))
    span_w = max(1.0, usable_w - 2.0 * margin)
    span_h = max(1.0, usable_h - 2.0 * margin)

    rng = np.random.default_rng(seed)
    n_side = max(1, int(math.sqrt(n_trials)))
    counts: list[int] = []

    for t_idx in range(n_trials):
        gx = t_idx % n_side
        gy = (t_idx // n_side) % n_side
        u_x = (gx + float(rng.uniform(0.05, 0.95))) / n_side
        u_y = (gy + float(rng.uniform(0.05, 0.95))) / n_side
        shift_x = margin + u_x * span_w
        shift_y = margin + u_y * span_h

        shifted_b: list[DetectedSource] = []
        for idx_b, sb in enumerate(sources_b):
            bx, by = b_offsets[idx_b]
            nx_arc = ((bx + 0.5 * usable_w + shift_x) % usable_w) - 0.5 * usable_w
            ny_arc = ((by + 0.5 * usable_h + shift_y) % usable_h) - 0.5 * usable_h
            new_ra = (ra_center + (nx_arc / (3600.0 * cos_dec))) % 360.0
            new_dec = max(-90.0, min(90.0, dec_center + ny_arc / 3600.0))
            shifted_b.append(
                DetectedSource(
                    epoch=sb.epoch,
                    source_index=sb.source_index,
                    x_peak=sb.x_peak,
                    y_peak=sb.y_peak,
                    x=sb.x,
                    y=sb.y,
                    sigma_x=sb.sigma_x,
                    sigma_y=sb.sigma_y,
                    ra=new_ra,
                    dec=new_dec,
                    sigma_ra_arcsec=sb.sigma_ra_arcsec,
                    sigma_dec_arcsec=sb.sigma_dec_arcsec,
                    flux=sb.flux,
                    flux_sigma=sb.flux_sigma,
                    snr=sb.snr,
                    peak_snr=sb.peak_snr,
                )
            )

        # Evaluate cross-match using periodic distance on the torus [usable_w x usable_h]
        trial_pairs: list[tuple[float, int, int, float, float]] = []
        for i, (ax, ay) in enumerate(a_offsets):
            for j, sb in enumerate(shifted_b):
                bx_s, by_s = _sky_offset_arcsec(ra_center, dec_center, sb.ra, sb.dec)[:2]
                dx = ((bx_s - ax + 0.5 * usable_w) % usable_w) - 0.5 * usable_w
                dy = ((by_s - ay + 0.5 * usable_h) % usable_h) - 0.5 * usable_h
                sep = math.hypot(dx, dy)
                if sep <= search_radius_arcsec + 1e-9:
                    trial_pairs.append((sep, i, j, dx, dy))

        trial_pairs.sort(key=lambda item: item[0])
        used_a: set[int] = set()
        used_b: set[int] = set()
        false_movers_this_trial = 0
        for sep, i, j, dx, dy in trial_pairs:
            if i in used_a or j in used_b:
                continue
            used_a.add(i)
            used_b.add(j)
            sa = sources_a[i]
            sb = shifted_b[j]
            sig_dx = math.hypot(sa.sigma_ra_arcsec, sb.sigma_ra_arcsec)
            sig_dy = math.hypot(sa.sigma_dec_arcsec, sb.sigma_dec_arcsec)
            sig_sep = (
                math.sqrt((dx * sig_dx) ** 2 + (dy * sig_dy) ** 2) / sep
                if sep > 1e-9
                else math.hypot(sig_dx, sig_dy) / math.sqrt(2.0)
            )
            if sep / max(sig_sep, 1e-6) >= stationary_sigma:
                false_movers_this_trial += 1

        counts.append(false_movers_this_trial)

    arr = np.asarray(counts, dtype=np.float64)
    mean_false = float(np.mean(arr))
    se_false = float(np.std(arr, ddof=1) / math.sqrt(len(arr))) if len(arr) > 1 else 0.0
    return mean_false, se_false, analytic


def search_movers_in_frames(
    frames: Sequence[Frame],
    *,
    ra: float | None = None,
    dec: float | None = None,
    fov_arcsec: float = DEFAULT_FOV_ARCSEC,
    detector: int | None = None,
    wmin: float | None = None,
    wmax: float | None = None,
    max_width: float = processing.DEFAULT_MAX_WIDTH_UM,
    gap_days: float = processing.DEFAULT_EPOCH_GAP_DAYS,
    threshold: float = DEFAULT_SNR_THRESHOLD,
    max_pm: float = DEFAULT_MAX_PM_ARCSEC_YR,
    min_separation_px: float = DEFAULT_MIN_SEPARATION_PX,
    edge_margin_px: float = DEFAULT_EDGE_MARGIN_PX,
    aperture_radius_px: float = DEFAULT_APERTURE_RADIUS_PX,
    stationary_sigma: float = DEFAULT_STATIONARY_SIGMA,
    n_random_trials: int = DEFAULT_RANDOM_TRIALS,
    epoch_a: int | None = None,
    epoch_b: int | None = None,
    clean_and_align: bool = True,
    seed: int = 42,
) -> MoverSearchResult:
    """Run the blind moving-object search on a sequence of ``Frame`` objects."""
    if not frames:
        raise ValueError("frames must not be empty")
    if not np.isfinite(threshold) or threshold <= 0:
        raise ValueError(f"threshold must be positive, got {threshold}")
    if not np.isfinite(max_pm) or max_pm <= 0:
        raise ValueError(f"max_pm must be positive, got {max_pm}")

    grouped = processing.group_frames(
        frames,
        detector=detector,
        wmin=wmin,
        wmax=wmax,
        max_width=max_width,
        gap_days=gap_days,
    )
    if not grouped.frames:
        raise ValueError("No usable frames in the selected detector and wavelength window")

    if clean_and_align:
        cleaned = tuple(processing.clean_frame(f) for f in grouped.frames)
        aligned = processing.align_frames(
            cleaned,
            target_ra=ra,
            target_dec=dec,
            fov_arcsec=fov_arcsec,
        )
    else:
        aligned = tuple(grouped.frames)

    epochs = processing.split_epochs(aligned, gap_days=gap_days)
    if len(epochs) < 2:
        raise ValueError(
            f"At least 2 observation epochs are required for blind mover search, got {len(epochs)}"
        )

    idx_a = 0 if epoch_a is None else int(epoch_a)
    idx_b = (len(epochs) - 1) if epoch_b is None else int(epoch_b)
    if not (0 <= idx_a < len(epochs)) or not (0 <= idx_b < len(epochs)) or idx_a == idx_b:
        raise ValueError(
            f"Invalid epoch pair ({idx_a}, {idx_b}) for {len(epochs)} available epochs"
        )
    if idx_a > idx_b:
        idx_a, idx_b = idx_b, idx_a

    frames_a = epochs.frames_by_epoch[idx_a]
    frames_b = epochs.frames_by_epoch[idx_b]
    mjd_a = float(np.mean([float(f.mjd) for f in frames_a]))
    mjd_b = float(np.mean([float(f.mjd) for f in frames_b]))
    baseline_years = abs(mjd_b - mjd_a) / DAYS_PER_YEAR
    if baseline_years <= 0:
        raise ValueError("Epoch time baseline must be positive")

    search_radius_arcsec = float(max_pm) * baseline_years

    _, _, sources_a, excl_a = detect_epoch_sources(
        frames_a,
        epoch_index=idx_a,
        threshold=threshold,
        min_separation_px=min_separation_px,
        edge_margin_px=edge_margin_px,
        aperture_radius_px=aperture_radius_px,
    )
    _, _, sources_b, excl_b = detect_epoch_sources(
        frames_b,
        epoch_index=idx_b,
        threshold=threshold,
        min_separation_px=min_separation_px,
        edge_margin_px=edge_margin_px,
        aperture_radius_px=aperture_radius_px,
    )

    all_matches = crossmatch_epoch_sources(
        sources_a,
        sources_b,
        baseline_years=baseline_years,
        search_radius_arcsec=search_radius_arcsec,
        stationary_sigma=stationary_sigma,
    )
    mover_candidates = [m for m in all_matches if m.classification == "mover_candidate"]
    n_stationary = sum(1 for m in all_matches if m.classification == "stationary")
    n_unmatched = sum(1 for m in all_matches if m.classification == "unmatched")

    ny, nx = aligned[0].image.shape
    pix_scale = _pixel_scale_from_wcs(aligned[0])
    usable_nx = max(1.0, float(nx) - 2.0 * edge_margin_px)
    usable_ny = max(1.0, float(ny) - 2.0 * edge_margin_px)
    usable_w_arcsec = usable_nx * pix_scale
    usable_h_arcsec = usable_ny * pix_scale

    if ra is not None and dec is not None:
        ra_c, dec_c = float(ra), float(dec)
    elif aligned[0].wcs is not None:
        rv, dv = aligned[0].wcs.celestial.pixel_to_world_values((nx - 1) / 2.0, (ny - 1) / 2.0)
        ra_c = float(np.asarray(rv).item()) % 360.0
        dec_c = float(np.asarray(dv).item())
    else:
        ra_c, dec_c = 0.0, 0.0

    mean_false, se_false, analytic_false = estimate_false_mover_rate(
        sources_a,
        sources_b,
        baseline_years=baseline_years,
        search_radius_arcsec=search_radius_arcsec,
        field_width_arcsec=usable_w_arcsec,
        field_height_arcsec=usable_h_arcsec,
        ra_center=ra_c,
        dec_center=dec_c,
        stationary_sigma=stationary_sigma,
        n_trials=n_random_trials,
        seed=seed,
    )

    versions = sorted(
        {f"l2b-v{int(f.version)}" for f in aligned if f.version is not None}
    )
    warns = [CANDIDATE_DISCLAIMER, *grouped.warnings]

    return MoverSearchResult(
        disclaimer=CANDIDATE_DISCLAIMER,
        ra_center=ra_c,
        dec_center=dec_c,
        fov_arcsec=float(fov_arcsec),
        pixel_scale_arcsec=pix_scale,
        detector=grouped.detector,
        wavelength_window_um=grouped.wavelength_window,
        epoch_a=idx_a,
        epoch_b=idx_b,
        mjd_epoch_a=mjd_a,
        mjd_epoch_b=mjd_b,
        baseline_years=baseline_years,
        search_radius_arcsec=search_radius_arcsec,
        threshold_sigma=float(threshold),
        max_pm_arcsec_per_yr=float(max_pm),
        n_sources_epoch_a=len(sources_a),
        n_sources_epoch_b=len(sources_b),
        n_excluded_epoch_a=len(excl_a),
        n_excluded_epoch_b=len(excl_b),
        n_stationary=n_stationary,
        n_mover_candidates=len(mover_candidates),
        n_unmatched=n_unmatched,
        expected_false_movers=mean_false,
        expected_false_movers_sigma=se_false,
        analytic_false_movers=analytic_false,
        n_random_trials=int(n_random_trials),
        data_versions=versions if versions else ["unknown"],
        warnings=warns,
        candidates=mover_candidates,
        all_matches=all_matches,
        sources_epoch_a=sources_a,
        sources_epoch_b=sources_b,
        excluded_sources=[*excl_a, *excl_b],
    )


def search_movers(
    ra: float,
    dec: float,
    fov: float = DEFAULT_FOV_ARCSEC,
    detector: int | None = None,
    wmin: float | None = None,
    wmax: float | None = None,
    *,
    threshold: float = DEFAULT_SNR_THRESHOLD,
    max_pm: float = DEFAULT_MAX_PM_ARCSEC_YR,
    min_separation_px: float = DEFAULT_MIN_SEPARATION_PX,
    edge_margin_px: float = DEFAULT_EDGE_MARGIN_PX,
    aperture_radius_px: float = DEFAULT_APERTURE_RADIUS_PX,
    stationary_sigma: float = DEFAULT_STATIONARY_SIGMA,
    n_random_trials: int = DEFAULT_RANDOM_TRIALS,
    data_mode: str | None = None,
    cache_root: str | Path = "cache",
    frames: Sequence[Frame] | None = None,
    seed: int = 42,
) -> MoverSearchResult:
    """Retrieve SPHEREx frames for ``(ra, dec, fov)`` and run a blind moving-source search."""
    raw_frames = (
        frames
        if frames is not None
        else data.get_frames(ra, dec, fov=fov, mode=data_mode, cache_root=cache_root)
    )
    return search_movers_in_frames(
        raw_frames,
        ra=ra,
        dec=dec,
        fov_arcsec=fov,
        detector=detector,
        wmin=wmin,
        wmax=wmax,
        threshold=threshold,
        max_pm=max_pm,
        min_separation_px=min_separation_px,
        edge_margin_px=edge_margin_px,
        aperture_radius_px=aperture_radius_px,
        stationary_sigma=stationary_sigma,
        n_random_trials=n_random_trials,
        clean_and_align=True,
        seed=seed,
    )


def write_movers_outputs(
    result: MoverSearchResult,
    csv_path: str | Path,
    json_path: str | Path,
    *,
    include_all_classifications: bool = False,
    command: str | None = None,
) -> tuple[Path, Path]:
    """Write ranked mover search results to CSV and JSON files with candidate disclaimers."""
    cpath = Path(csv_path)
    jpath = Path(json_path)
    cpath.parent.mkdir(parents=True, exist_ok=True)
    jpath.parent.mkdir(parents=True, exist_ok=True)

    rows_to_write = result.all_matches if include_all_classifications else result.candidates
    fieldnames = [
        "id",
        "rank",
        "classification",
        "ra_epoch_a",
        "dec_epoch_a",
        "ra_epoch_b",
        "dec_epoch_b",
        "offset_arcsec",
        "offset_sigma_arcsec",
        "pm_arcsec_per_yr",
        "pm_sigma_arcsec_per_yr",
        "pm_ra_arcsec_per_yr",
        "pm_ra_sigma_arcsec_per_yr",
        "pm_dec_arcsec_per_yr",
        "pm_dec_sigma_arcsec_per_yr",
        "pa_deg",
        "pa_sigma_deg",
        "snr_epoch_a",
        "snr_epoch_b",
        "motion_significance",
        "quality_flags",
        "disclaimer",
    ]

    with cpath.open("w", newline="", encoding="utf-8") as fh:
        fh.write(f"# {CANDIDATE_DISCLAIMER}\n")
        writer = csv.DictWriter(fh, fieldnames=fieldnames)
        writer.writeheader()
        for item in rows_to_write:
            d = asdict(item)
            d["quality_flags"] = ";".join(item.quality_flags)
            writer.writerow(d)

    payload = _clean_json(asdict(result))
    if command is not None:
        payload["command"] = command
    jpath.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    return cpath, jpath


def _clean_json(obj: Any) -> Any:
    """Convert non-finite floats and tuples for strict JSON serialisation."""
    if isinstance(obj, float):
        return obj if math.isfinite(obj) else None
    if isinstance(obj, dict):
        return {str(k): _clean_json(v) for k, v in obj.items()}
    if isinstance(obj, (list, tuple)):
        return [_clean_json(v) for v in obj]
    return obj
