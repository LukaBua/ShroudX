# AI-assisted: written with AI pair-programming assistant.
"""Measure stationary-star brightness flicker across SPHEREx wavelength and time differences.

Pre-registered Pass / Fail and Decision Criteria (fixed before running on data):
1. Minimum sample gate:
   - At least ``min_stars = 3`` stars from ``data/flicker_stars.csv`` must each have
     at least ``min_frames_per_star = 5`` usable (unmasked, non-unreliable, finite
     positive-flux) frames after cleaning and aperture quality filtering.
   - If fewer than 3 stars have 5 or more usable frames, this module aborts with a
     ``ValueError`` and writes NO files to ``results/``.
2. Pairwise excess fractional scatter definition:
   - For each star $s$ and detector $d$ with median aperture flux $F_{\\mathrm{med}, s, d}$,
     every pair of usable exposures $(i, j)$ ($i < j$) on the same star and detector has
     fractional flux difference
     $\\delta_{ij} = (F_i - F_j) / F_{\\mathrm{med}, s, d}$ and propagated fractional
     measurement variance
     $\\sigma_{\\mathrm{phot}, ij}^2 = (\\sigma_{F, i}^2 + \\sigma_{F, j}^2) /
     F_{\\mathrm{med}, s, d}^2$.
   - For a window width $w$, across all same-star, same-detector pairs with
     $|\\lambda_i - \\lambda_j| < w$ (or $|t_i - t_j| < w$ for the time control),
     the observed RMS scatter is $S_{\\mathrm{obs}}(w) = \\sqrt{\\langle \\delta_{ij}^2 \\rangle}$,
     the expected photometric noise is
     $S_{\\mathrm{phot}}(w) = \\sqrt{\\langle \\sigma_{\\mathrm{phot}, ij}^2 \\rangle}$,
     and the quadrature-subtracted excess scatter is
     $S_{\\mathrm{excess}}(w) = \\sqrt{\\max(0, S_{\\mathrm{obs}}^2(w) - S_{\\mathrm{phot}}^2(w))}$.
3. Window-width recommendation criterion:
   - Given threshold ``threshold = 0.05`` (5% excess fractional flux scatter), the largest
     tested window width $w \\in [0.02, 0.40]\\,\\mu\\mathrm{m}$ for which all non-empty bins
     $w' \\le w$ satisfy $S_{\\mathrm{excess}}(w') \\le \\text{threshold}$ is reported as
     ``max_window_below_threshold_um``.
   - A single recommended ``max_width`` value is supported only if (a) at least one narrow
     bin ($w \\le 0.10\\,\\mu\\mathrm{m}$) contains frame pairs, (b) $S_{\\mathrm{excess}}(w)$
     stays at or below ``threshold`` at the narrowest populated bin, and (c) per-detector
     excess scatter at the recommended width does not exceed ``2 * threshold``. Otherwise,
     the report states plainly that the data do not support a single value.
"""

from __future__ import annotations

import argparse
import csv
import json
import math
import re
import shlex
import sys
from collections.abc import Callable, Sequence
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np

_REPO_ROOT = Path(__file__).resolve().parent.parent
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

import data  # noqa: E402
import processing  # noqa: E402
from frame import Frame  # noqa: E402

DEFAULT_APERTURE_RADIUS_PX: float = 2.0
DEFAULT_THRESHOLD: float = 0.05
DEFAULT_FOV_ARCSEC: float = 120.0
DEFAULT_MIN_STARS: int = 3
DEFAULT_MIN_FRAMES_PER_STAR: int = 5
DEFAULT_MAX_CUTOUTS_PER_STAR: int = 30

DEFAULT_WINDOW_WIDTHS_UM: tuple[float, ...] = tuple(
    round(0.02 * i, 2) for i in range(1, 21)
)
DEFAULT_TIME_WINDOWS_DAYS: tuple[float, ...] = (
    0.5,
    1.0,
    2.0,
    5.0,
    10.0,
    20.0,
    30.0,
    60.0,
    120.0,
    180.0,
    240.0,
    365.0,
)


@dataclass(frozen=True)
class FlickerStar:
    """Target star entry from ``data/flicker_stars.csv``."""

    name: str
    ra: float
    dec: float
    source: str = "gaia_dr3"


@dataclass(frozen=True)
class FramePhotometry:
    """Aperture photometry measurement for one star on one cleaned SPHEREx frame."""

    detector: int
    wavelength_um: float
    mjd: float
    flux: float
    flux_sigma: float
    x_px: float
    y_px: float
    n_aperture_pixels: int
    bg_per_pixel: float
    version: int | None = None
    uri: str | None = None


@dataclass
class DetectorPhotometrySeries:
    """Normalised photometry time/wavelength series for one star on one detector."""

    star_name: str
    detector: int
    n_total: int
    n_usable: int
    n_skipped: int
    median_flux: float
    wavelengths_um: list[float] = field(default_factory=list)
    mjds: list[float] = field(default_factory=list)
    fluxes: list[float] = field(default_factory=list)
    flux_sigmas: list[float] = field(default_factory=list)
    normalised_fluxes: list[float] = field(default_factory=list)
    normalised_sigmas: list[float] = field(default_factory=list)
    versions: list[int | None] = field(default_factory=list)


@dataclass
class StarPhotometrySummary:
    """Aggregated photometry and skipped-frame counts across all detectors for one star."""

    star: FlickerStar
    n_total_frames: int
    n_usable_frames: int
    n_skipped_frames: int
    detectors: dict[int, DetectorPhotometrySeries] = field(default_factory=dict)


@dataclass(frozen=True)
class WindowScatterPoint:
    """Measured, noise, and quadrature-subtracted excess scatter for one window width."""

    window: float
    n_pairs: int
    observed_scatter: float
    photometric_noise: float
    excess_scatter: float
    excess_scatter_sigma: float
    single_frame_excess_scatter: float


@dataclass(frozen=True)
class FlickerRecommendation:
    """Window-width recommendation and justification for ``processing.group_frames``."""

    threshold: float
    max_window_below_threshold_um: float | None
    recommended_max_width_um: float | None
    single_value_supported: bool
    justification: str


@dataclass
class FlickerExperimentResult:
    """Complete result bundle written to ``results/flicker.json``."""

    command: str
    data_versions: list[str]
    aperture_settings: dict[str, float]
    criteria: dict[str, Any]
    frame_counts: dict[str, int]
    stars: list[StarPhotometrySummary]
    wavelength_window_analysis: list[WindowScatterPoint]
    time_window_analysis: list[WindowScatterPoint]
    same_wavelength_time_analysis: list[WindowScatterPoint]
    recommendation: FlickerRecommendation
    plot_paths: list[str]


def load_flicker_stars(
    csv_path: str | Path = _REPO_ROOT / "data" / "flicker_stars.csv",
) -> list[FlickerStar]:
    """Load stationary star targets from ``data/flicker_stars.csv``.

    Args:
        csv_path: Path to CSV file with columns ``name,ra,dec,source``.

    Returns:
        List of validated ``FlickerStar`` objects.
    """
    path = Path(csv_path)
    if not path.is_file():
        raise FileNotFoundError(f"Flicker star catalog not found: {path}")

    stars: list[FlickerStar] = []
    with path.open("r", newline="", encoding="utf-8") as fh:
        reader = csv.DictReader(fh)
        required = {"name", "ra", "dec", "source"}
        if reader.fieldnames is None or not required.issubset(set(reader.fieldnames)):
            raise ValueError(
                f"CSV {path} must contain columns {sorted(required)}, got {reader.fieldnames}"
            )
        for row in reader:
            name = (row.get("name") or "").strip()
            if not name or name.startswith("#"):
                continue
            ra = float((row.get("ra") or "").strip())
            dec = float((row.get("dec") or "").strip())
            source = (row.get("source") or "unknown").strip()
            if not (np.isfinite(ra) and np.isfinite(dec)):
                raise ValueError(f"Non-finite coordinates for star {name!r}: ra={ra}, dec={dec}")
            stars.append(FlickerStar(name=name, ra=ra, dec=dec, source=source))

    if not stars:
        raise ValueError(f"No valid star rows found in {path}")
    return stars


def _resolve_annulus_radii(
    aperture_radius: float,
    annulus_inner: float | None,
    annulus_outer: float | None,
) -> tuple[float, float]:
    """Return validated ``(r_in, r_out)`` background annulus radii in pixels."""
    if not np.isfinite(aperture_radius) or aperture_radius <= 0:
        raise ValueError(f"aperture_radius must be positive, got {aperture_radius}")
    r_in = float(annulus_inner) if annulus_inner is not None else max(aperture_radius + 1.5, 3.5)
    r_out = float(annulus_outer) if annulus_outer is not None else max(r_in + 2.5, 6.0)
    if not (np.isfinite(r_in) and np.isfinite(r_out)) or r_in < aperture_radius or r_out <= r_in:
        raise ValueError(
            f"Require aperture_radius <= annulus_inner < annulus_outer, got "
            f"({aperture_radius}, {r_in}, {r_out})"
        )
    return r_in, r_out


def measure_star_frame_photometry(
    frame: Frame,
    ra: float,
    dec: float,
    *,
    aperture_radius: float = DEFAULT_APERTURE_RADIUS_PX,
    annulus_inner: float | None = None,
    annulus_outer: float | None = None,
) -> tuple[FramePhotometry | None, str]:
    """Perform circular aperture photometry at ``(ra, dec)`` using ``frame.wcs``.

    Skips the frame if:
    - ``frame.wcs`` is None or ``(ra, dec)`` projects outside the frame bounds.
    - The circular aperture extends outside the image or touches any ``frame.mask``
      or ``frame.unreliable`` pixel, or any pixel with non-finite image/variance or
      non-positive variance.
    - The background-subtracted aperture flux is non-positive or non-finite.

    Args:
        frame: Cleaned ``Frame`` instance.
        ra: Star Right Ascension in decimal degrees (ICRS).
        dec: Star Declination in decimal degrees (ICRS).
        aperture_radius: Circular aperture radius in pixels (default 2.0).
        annulus_inner: Inner background annulus radius in pixels.
        annulus_outer: Outer background annulus radius in pixels.

    Returns:
        Tuple ``(photometry_or_none, status_reason)`` where ``status_reason`` is
        ``"ok"``, ``"outside_fov"``, ``"masked_or_unreliable"``, or ``"nonpositive_flux"``.
    """
    r_in, r_out = _resolve_annulus_radii(aperture_radius, annulus_inner, annulus_outer)
    if frame.wcs is None:
        return None, "outside_fov"

    ny, nx = frame.image.shape
    try:
        x_val, y_val = frame.wcs.celestial.world_to_pixel_values(float(ra), float(dec))
        x_px = float(np.asarray(x_val).item())
        y_px = float(np.asarray(y_val).item())
    except Exception:
        return None, "outside_fov"

    if not (np.isfinite(x_px) and np.isfinite(y_px)):
        return None, "outside_fov"
    if x_px < -0.5 or x_px > (nx - 0.5) or y_px < -0.5 or y_px > (ny - 0.5):
        return None, "outside_fov"
    if (
        x_px - aperture_radius < -0.5
        or x_px + aperture_radius > (nx - 0.5)
        or y_px - aperture_radius < -0.5
        or y_px + aperture_radius > (ny - 0.5)
    ):
        return None, "masked_or_unreliable"

    yy, xx = np.mgrid[0:ny, 0:nx]
    dist = np.hypot(xx - x_px, yy - y_px)
    ap_mask = dist <= aperture_radius
    n_ap = int(np.count_nonzero(ap_mask))
    if n_ap == 0:
        return None, "masked_or_unreliable"

    img = np.asarray(frame.image, dtype=np.float64)
    var = np.asarray(frame.variance, dtype=np.float64)
    bad = (
        np.asarray(frame.mask, dtype=bool)
        | (~np.isfinite(img))
        | (~np.isfinite(var))
        | (var <= 0)
    )
    if frame.unreliable is not None:
        bad = bad | np.asarray(frame.unreliable, dtype=bool)

    if bool(np.any(bad[ap_mask])):
        return None, "masked_or_unreliable"

    ann_mask = (dist >= r_in) & (dist <= r_out) & (~bad)
    bg_per_pixel = float(np.median(img[ann_mask])) if np.any(ann_mask) else 0.0

    flux = float(np.sum(img[ap_mask] - bg_per_pixel))
    flux_var = float(np.sum(var[ap_mask]))
    if not np.isfinite(flux) or flux <= 0.0 or not np.isfinite(flux_var) or flux_var <= 0.0:
        return None, "nonpositive_flux"

    flux_sigma = float(math.sqrt(flux_var))
    return (
        FramePhotometry(
            detector=int(frame.detector),
            wavelength_um=float(frame.wavelength),
            mjd=float(frame.mjd),
            flux=flux,
            flux_sigma=flux_sigma,
            x_px=x_px,
            y_px=y_px,
            n_aperture_pixels=n_ap,
            bg_per_pixel=bg_per_pixel,
            version=frame.version,
            uri=frame.uri,
        ),
        "ok",
    )


def collect_star_photometry(
    star: FlickerStar,
    frames: Sequence[Frame],
    *,
    aperture_radius: float = DEFAULT_APERTURE_RADIUS_PX,
    annulus_inner: float | None = None,
    annulus_outer: float | None = None,
) -> StarPhotometrySummary:
    """Clean frames Containing ``star``, run aperture photometry, and normalise per detector.

    Args:
        star: Target ``FlickerStar``.
        frames: Candidate ``Frame`` sequence for this field.
        aperture_radius: Circular aperture radius in pixels (default 2.0).
        annulus_inner: Optional inner annulus radius in pixels.
        annulus_outer: Optional outer annulus radius in pixels.

    Returns:
        ``StarPhotometrySummary`` with per-detector normalised flux series and
        counts of total, usable, and skipped frames.
    """
    _resolve_annulus_radii(aperture_radius, annulus_inner, annulus_outer)
    by_det_measurements: dict[int, list[FramePhotometry]] = {}
    by_det_counts: dict[int, list[int]] = {}  # [total, usable, skipped]

    n_total = 0
    n_usable = 0
    n_skipped = 0

    for raw_frame in frames:
        det = int(raw_frame.detector)
        cleaned = processing.clean_frame(raw_frame)
        phot, reason = measure_star_frame_photometry(
            cleaned,
            star.ra,
            star.dec,
            aperture_radius=aperture_radius,
            annulus_inner=annulus_inner,
            annulus_outer=annulus_outer,
        )
        if reason == "outside_fov":
            continue

        n_total += 1
        counts = by_det_counts.setdefault(det, [0, 0, 0])
        counts[0] += 1

        if phot is None:
            n_skipped += 1
            counts[2] += 1
            continue

        n_usable += 1
        counts[1] += 1
        by_det_measurements.setdefault(det, []).append(phot)

    det_series: dict[int, DetectorPhotometrySeries] = {}
    for det in sorted(by_det_counts.keys()):
        tot, usable, skipped = by_det_counts[det]
        meas = sorted(
            by_det_measurements.get(det, []),
            key=lambda m: (m.mjd, m.wavelength_um),
        )
        if meas:
            flux_arr = np.array([m.flux for m in meas], dtype=np.float64)
            med_flux = float(np.median(flux_arr))
            norm_flux = [float(m.flux / med_flux) for m in meas]
            norm_sigma = [float(m.flux_sigma / med_flux) for m in meas]
        else:
            med_flux = float("nan")
            norm_flux = []
            norm_sigma = []

        det_series[det] = DetectorPhotometrySeries(
            star_name=star.name,
            detector=det,
            n_total=tot,
            n_usable=usable,
            n_skipped=skipped,
            median_flux=med_flux,
            wavelengths_um=[m.wavelength_um for m in meas],
            mjds=[m.mjd for m in meas],
            fluxes=[m.flux for m in meas],
            flux_sigmas=[m.flux_sigma for m in meas],
            normalised_fluxes=norm_flux,
            normalised_sigmas=norm_sigma,
            versions=[m.version for m in meas],
        )

    return StarPhotometrySummary(
        star=star,
        n_total_frames=n_total,
        n_usable_frames=n_usable,
        n_skipped_frames=n_skipped,
        detectors=det_series,
    )


def compute_window_scatter(
    summaries: Sequence[StarPhotometrySummary],
    window_widths: Sequence[float] = DEFAULT_WINDOW_WIDTHS_UM,
    *,
    coordinate: str = "wavelength",
    max_wavelength_diff_um: float | None = None,
) -> list[WindowScatterPoint]:
    """Compute pairwise fractional flux scatter and quadrature-subtracted excess scatter.

    For each star and detector with >= 2 usable frames, forms all pairs $(i < j)$:
    - $\\delta_{ij} = (F_i - F_j) / F_{\\mathrm{med}}$
    - $\\sigma_{\\mathrm{phot}, ij}^2 = (\\sigma_{F, i}^2 + \\sigma_{F, j}^2) / F_{\\mathrm{med}}^2$

    Args:
        summaries: Sequence of ``StarPhotometrySummary`` objects.
        window_widths: Sequence of window widths $w > 0$ (in micrometres when
            ``coordinate="wavelength"``, or days when ``coordinate="time"``).
        coordinate: Either ``"wavelength"`` or ``"time"``.
        max_wavelength_diff_um: Optional upper bound on $|\\lambda_i - \\lambda_j|$
            applied when ``coordinate="time"`` to isolate pure time variation at
            fixed wavelength.

    Returns:
        List of ``WindowScatterPoint`` objects, one per width in ``window_widths``.
    """
    if coordinate not in ("wavelength", "time"):
        raise ValueError(f"coordinate must be 'wavelength' or 'time', got {coordinate!r}")

    pair_coords: list[float] = []
    pair_dflux_sq: list[float] = []
    pair_noise_sq: list[float] = []

    for summary in summaries:
        for series in summary.detectors.values():
            n = len(series.normalised_fluxes)
            if n < 2 or not np.isfinite(series.median_flux) or series.median_flux <= 0:
                continue
            wl = series.wavelengths_um
            t = series.mjds
            nf = series.normalised_fluxes
            ns = series.normalised_sigmas
            for i in range(n):
                for j in range(i + 1, n):
                    dw = abs(wl[i] - wl[j])
                    if max_wavelength_diff_um is not None and dw > max_wavelength_diff_um + 1e-12:
                        continue
                    dt = abs(t[i] - t[j])
                    coord_diff = dw if coordinate == "wavelength" else dt
                    df = nf[i] - nf[j]
                    n_var = ns[i] ** 2 + ns[j] ** 2
                    pair_coords.append(coord_diff)
                    pair_dflux_sq.append(df * df)
                    pair_noise_sq.append(n_var)

    coords_arr = np.asarray(pair_coords, dtype=np.float64)
    dflux_sq_arr = np.asarray(pair_dflux_sq, dtype=np.float64)
    noise_sq_arr = np.asarray(pair_noise_sq, dtype=np.float64)

    points: list[WindowScatterPoint] = []
    for w in window_widths:
        w_val = float(w)
        if coords_arr.size == 0:
            points.append(
                WindowScatterPoint(
                    window=w_val,
                    n_pairs=0,
                    observed_scatter=float("nan"),
                    photometric_noise=float("nan"),
                    excess_scatter=float("nan"),
                    excess_scatter_sigma=float("nan"),
                    single_frame_excess_scatter=float("nan"),
                )
            )
            continue

        sel = coords_arr < (w_val + 1e-12)
        n_pairs = int(np.count_nonzero(sel))
        if n_pairs == 0:
            points.append(
                WindowScatterPoint(
                    window=w_val,
                    n_pairs=0,
                    observed_scatter=float("nan"),
                    photometric_noise=float("nan"),
                    excess_scatter=float("nan"),
                    excess_scatter_sigma=float("nan"),
                    single_frame_excess_scatter=float("nan"),
                )
            )
            continue

        sub_dflux_sq = dflux_sq_arr[sel]
        sub_noise_sq = noise_sq_arr[sel]
        mean_obs_sq = float(np.mean(sub_dflux_sq))
        mean_noise_sq = float(np.mean(sub_noise_sq))
        obs_scatter = float(math.sqrt(max(0.0, mean_obs_sq)))
        phot_noise = float(math.sqrt(max(0.0, mean_noise_sq)))

        excess_sq = max(0.0, mean_obs_sq - mean_noise_sq)
        excess_scatter = float(math.sqrt(excess_sq))

        # Uncertainty on excess_scatter via propagation of u_k = df_k^2 - noise_k^2
        u_k = sub_dflux_sq - sub_noise_sq
        if n_pairs >= 2:
            se_u = float(np.std(u_k, ddof=1) / math.sqrt(n_pairs))
            noise_floor_se = float(math.sqrt(2.0 / n_pairs) * mean_noise_sq)
            sigma_u = max(se_u, noise_floor_se)
        else:
            sigma_u = float(math.sqrt(2.0) * max(mean_obs_sq, mean_noise_sq))

        if excess_scatter > math.sqrt(max(sigma_u, 0.0) / 2.0) and excess_scatter > 0:
            excess_sigma = float(sigma_u / (2.0 * excess_scatter))
        else:
            excess_sigma = float(math.sqrt(max(0.0, sigma_u)))

        points.append(
            WindowScatterPoint(
                window=w_val,
                n_pairs=n_pairs,
                observed_scatter=obs_scatter,
                photometric_noise=phot_noise,
                excess_scatter=excess_scatter,
                excess_scatter_sigma=excess_sigma,
                single_frame_excess_scatter=float(excess_scatter / math.sqrt(2.0)),
            )
        )

    return points


def evaluate_recommendation(
    summaries: Sequence[StarPhotometrySummary],
    wavelength_curve: Sequence[WindowScatterPoint],
    *,
    threshold: float = DEFAULT_THRESHOLD,
) -> FlickerRecommendation:
    """Determine the largest window width staying below ``threshold`` and assess support.

    Args:
        summaries: Per-star photometry summaries.
        wavelength_curve: Excess scatter vs wavelength window width points.
        threshold: Maximum allowed excess fractional flux scatter (default 0.05 = 5%).

    Returns:
        ``FlickerRecommendation`` with recommended ``max_width`` and plain-language justification.
    """
    if not np.isfinite(threshold) or threshold <= 0:
        raise ValueError(f"threshold must be positive, got {threshold}")

    populated = [pt for pt in wavelength_curve if pt.n_pairs > 0 and np.isfinite(pt.excess_scatter)]
    if not populated:
        return FlickerRecommendation(
            threshold=float(threshold),
            max_window_below_threshold_um=None,
            recommended_max_width_um=None,
            single_value_supported=False,
            justification=(
                "No same-star, same-detector frame pairs were found within the tested "
                "wavelength windows (0.02 to 0.40 um). The data do not support a single "
                "recommended max_width value."
            ),
        )

    # Walk sorted widths to find largest w where all populated w' <= w stay <= threshold
    largest_below: float | None = None
    first_exceeding: WindowScatterPoint | None = None
    for pt in sorted(populated, key=lambda p: p.window):
        if pt.excess_scatter <= threshold:
            largest_below = pt.window
        else:
            first_exceeding = pt
            break

    if largest_below is None:
        narrowest = min(populated, key=lambda p: p.window)
        return FlickerRecommendation(
            threshold=float(threshold),
            max_window_below_threshold_um=None,
            recommended_max_width_um=None,
            single_value_supported=False,
            justification=(
                f"Even at the narrowest populated wavelength window (w = {narrowest.window:.2f}"
                f" um, n_pairs = {narrowest.n_pairs}), the quadrature-subtracted excess scatter "
                f"is {narrowest.excess_scatter * 100:.2f}% +/- "
                f"{narrowest.excess_scatter_sigma * 100:.2f}%, "
                f"which exceeds the {threshold * 100:.1f}% threshold. The data do not support a "
                "single max_width value without narrower wavelength filtering or "
                "per-source SED modelling."
            ),
        )

    # Check consistency across individual detectors at largest_below
    det_scatters: dict[int, float] = {}
    for det in range(1, 7):
        det_only: list[StarPhotometrySummary] = []
        for s in summaries:
            if det in s.detectors:
                det_only.append(
                    StarPhotometrySummary(
                        star=s.star,
                        n_total_frames=s.detectors[det].n_total,
                        n_usable_frames=s.detectors[det].n_usable,
                        n_skipped_frames=s.detectors[det].n_skipped,
                        detectors={det: s.detectors[det]},
                    )
                )
        if det_only:
            pt_det = compute_window_scatter(det_only, [largest_below], coordinate="wavelength")[0]
            if pt_det.n_pairs >= 2 and np.isfinite(pt_det.excess_scatter):
                det_scatters[det] = pt_det.excess_scatter

    inconsistent_dets = [d for d, val in det_scatters.items() if val > 2.0 * threshold]
    narrowest_populated = min(populated, key=lambda p: p.window)
    has_narrow_coverage = narrowest_populated.window <= 0.10
    supported = has_narrow_coverage and (len(inconsistent_dets) == 0)

    pt_at_rec = next(p for p in populated if p.window == largest_below)
    if not supported:
        reason = (
            f"detector(s) {inconsistent_dets} exhibit excess scatter > "
            f"{200 * threshold:.0f}% at w = {largest_below:.2f} um"
            if inconsistent_dets
            else (
                "no frame pairs exist at w <= 0.10 um "
                f"(narrowest populated bin is {narrowest_populated.window:.2f} um)"
            )
        )
        return FlickerRecommendation(
            threshold=float(threshold),
            max_window_below_threshold_um=largest_below,
            recommended_max_width_um=None,
            single_value_supported=False,
            justification=(
                f"Aggregate excess scatter stays below {threshold * 100:.1f}% up to "
                f"w = {largest_below:.2f} um ({pt_at_rec.excess_scatter * 100:.2f}% +/- "
                f"{pt_at_rec.excess_scatter_sigma * 100:.2f}%), but {reason}. "
                "The data do not support a single universal max_width value."
            ),
        )

    exceed_note = (
        f" and rises above {threshold * 100:.1f}% at w = {first_exceeding.window:.2f} um "
        f"({first_exceeding.excess_scatter * 100:.2f}% +/- "
        f"{first_exceeding.excess_scatter_sigma * 100:.2f}%)"
        if first_exceeding is not None
        else " across the full tested range up to 0.40 um"
    )
    return FlickerRecommendation(
        threshold=float(threshold),
        max_window_below_threshold_um=largest_below,
        recommended_max_width_um=largest_below,
        single_value_supported=True,
        justification=(
            f"Excess fractional flux scatter stays below the {threshold * 100:.1f}% threshold "
            f"up to w = {largest_below:.2f} um (excess scatter = "
            f"{pt_at_rec.excess_scatter * 100:.2f}% +/- "
            f"{pt_at_rec.excess_scatter_sigma * 100:.2f}% across {pt_at_rec.n_pairs} pairs)"
            f"{exceed_note}. Recommended processing.group_frames "
            f"max_width = {largest_below:.2f} um."
        ),
    )


def plot_normalised_flux_vs_wavelength(
    summaries: Sequence[StarPhotometrySummary],
    output_path: Path,
) -> Path:
    """Plot normalized flux vs wavelength in B&W, one panel per star per detector."""
    panels: list[tuple[str, int, DetectorPhotometrySeries]] = []
    for summary in summaries:
        for det, series in sorted(summary.detectors.items()):
            if series.n_usable > 0:
                panels.append((summary.star.name, det, series))

    n_panels = max(1, len(panels))
    ncols = min(3, n_panels)
    nrows = int(math.ceil(n_panels / ncols))

    fig, axes = plt.subplots(
        nrows=nrows,
        ncols=ncols,
        figsize=(4.2 * ncols, 3.2 * nrows),
        squeeze=False,
    )
    fig.patch.set_facecolor("white")

    for idx, (star_name, det, series) in enumerate(panels):
        ax = axes[idx // ncols][idx % ncols]
        ax.set_facecolor("white")
        wl = np.asarray(series.wavelengths_um, dtype=np.float64)
        nf = np.asarray(series.normalised_fluxes, dtype=np.float64)
        ns = np.asarray(series.normalised_sigmas, dtype=np.float64)

        ax.axhline(1.0, color="0.4", linestyle="--", linewidth=0.9)
        ax.errorbar(
            wl,
            nf,
            yerr=ns,
            fmt="o",
            color="black",
            ecolor="0.35",
            markersize=4.0,
            elinewidth=1.0,
            capsize=2.0,
        )
        ax.set_title(f"{star_name} · Det {det} (n={series.n_usable})", fontsize=9, color="black")
        ax.set_xlabel("Wavelength (um)", fontsize=8, color="black")
        ax.set_ylabel("Flux / Median Flux", fontsize=8, color="black")
        ax.tick_params(colors="black", labelsize=8)
        for spine in ax.spines.values():
            spine.set_edgecolor("black")

    for idx in range(len(panels), nrows * ncols):
        axes[idx // ncols][idx % ncols].set_visible(False)

    fig.tight_layout()
    output_path.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(output_path, dpi=150, facecolor="white", edgecolor="white")
    plt.close(fig)
    return output_path


def plot_excess_scatter_wavelength_and_time(
    wavelength_curve: Sequence[WindowScatterPoint],
    time_curve: Sequence[WindowScatterPoint],
    same_wl_time_curve: Sequence[WindowScatterPoint],
    recommendation: FlickerRecommendation,
    output_path: Path,
) -> Path:
    """Plot excess fractional scatter vs wavelength window w and time difference dt in B&W."""
    fig, (ax_wl, ax_t) = plt.subplots(1, 2, figsize=(10.0, 4.2))
    fig.patch.set_facecolor("white")

    # Panel 1: Excess scatter vs wavelength window width w
    wl_pts = [p for p in wavelength_curve if p.n_pairs > 0 and np.isfinite(p.excess_scatter)]
    ax_wl.set_facecolor("white")
    ax_wl.axhline(
        recommendation.threshold,
        color="0.3",
        linestyle="--",
        linewidth=1.0,
        label=f"Threshold ({recommendation.threshold * 100:.1f}%)",
    )
    if wl_pts:
        w_arr = np.array([p.window for p in wl_pts], dtype=np.float64)
        ex_arr = np.array([p.excess_scatter for p in wl_pts], dtype=np.float64)
        sig_arr = np.array([p.excess_scatter_sigma for p in wl_pts], dtype=np.float64)
        obs_arr = np.array([p.observed_scatter for p in wl_pts], dtype=np.float64)

        ax_wl.fill_between(
            w_arr,
            np.maximum(0.0, ex_arr - sig_arr),
            ex_arr + sig_arr,
            color="0.80",
            label="Excess scatter +/- 1 sigma",
        )
        ax_wl.plot(
            w_arr,
            ex_arr,
            "o-",
            color="black",
            linewidth=1.4,
            markersize=4.0,
            label="Excess scatter",
        )
        ax_wl.plot(
            w_arr,
            obs_arr,
            "s:",
            color="0.35",
            linewidth=1.0,
            markersize=3.5,
            label="Total observed scatter",
        )

    if recommendation.max_window_below_threshold_um is not None:
        ax_wl.axvline(
            recommendation.max_window_below_threshold_um,
            color="black",
            linestyle="-.",
            linewidth=1.0,
            label=f"Max w <= thresh ({recommendation.max_window_below_threshold_um:.2f} um)",
        )

    ax_wl.set_title("Excess Fractional Scatter vs Wavelength Window", fontsize=10, color="black")
    ax_wl.set_xlabel("Wavelength window width w (um)", fontsize=9, color="black")
    ax_wl.set_ylabel("Fractional flux scatter", fontsize=9, color="black")
    ax_wl.tick_params(colors="black", labelsize=8)
    ax_wl.legend(frameon=False, fontsize=7.5)
    for spine in ax_wl.spines.values():
        spine.set_edgecolor("black")

    # Panel 2: Excess scatter vs observation-time difference
    t_pts = [p for p in time_curve if p.n_pairs > 0 and np.isfinite(p.excess_scatter)]
    sw_pts = [p for p in same_wl_time_curve if p.n_pairs > 0 and np.isfinite(p.excess_scatter)]
    ax_t.set_facecolor("white")
    ax_t.axhline(
        recommendation.threshold,
        color="0.3",
        linestyle="--",
        linewidth=1.0,
        label=f"Threshold ({recommendation.threshold * 100:.1f}%)",
    )
    if t_pts:
        t_arr = np.array([p.window for p in t_pts], dtype=np.float64)
        tex_arr = np.array([p.excess_scatter for p in t_pts], dtype=np.float64)
        tsig_arr = np.array([p.excess_scatter_sigma for p in t_pts], dtype=np.float64)
        ax_t.fill_between(
            t_arr,
            np.maximum(0.0, tex_arr - tsig_arr),
            tex_arr + tsig_arr,
            color="0.85",
        )
        ax_t.plot(
            t_arr,
            tex_arr,
            "o-",
            color="black",
            linewidth=1.4,
            markersize=4.0,
            label="All wavelengths (|dt| < W_t)",
        )
    if sw_pts:
        st_arr = np.array([p.window for p in sw_pts], dtype=np.float64)
        sex_arr = np.array([p.excess_scatter for p in sw_pts], dtype=np.float64)
        ax_t.plot(
            st_arr,
            sex_arr,
            "^--",
            color="0.35",
            linewidth=1.2,
            markersize=4.0,
            label="Matched wavelength (|d_lambda| <= 0.03 um)",
        )

    ax_t.set_title("Excess Fractional Scatter vs Time Separation", fontsize=10, color="black")
    ax_t.set_xlabel("Observation-time window W_t (days)", fontsize=9, color="black")
    ax_t.set_ylabel("Fractional flux scatter", fontsize=9, color="black")
    ax_t.tick_params(colors="black", labelsize=8)
    ax_t.legend(frameon=False, fontsize=7.5)
    for spine in ax_t.spines.values():
        spine.set_edgecolor("black")

    fig.tight_layout()
    output_path.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(output_path, dpi=150, facecolor="white", edgecolor="white")
    plt.close(fig)
    return output_path


def _make_capped_tap_query(max_cutouts: int) -> Callable[[str], list[dict[str, Any]]]:
    """Build a TAP query callable that caps deep-field NEP results evenly across detectors."""

    def _tap_query(adql: str) -> list[dict[str, Any]]:
        try:
            from astroquery.ipac.irsa import Irsa

            res = Irsa.query_tap(adql)
            table = res.to_table() if hasattr(res, "to_table") else res
        except ImportError:
            import pyvo

            service = pyvo.dal.TAPService(data.IRSA_TAP_URL)
            res = service.search(adql)
            table = res.to_table() if hasattr(res, "to_table") else res

        rows: list[dict[str, Any]] = []
        colnames = set(getattr(table, "colnames", ()))
        for row in table:
            uri_val = row["uri"] if "uri" in colnames else row["a.uri"]
            mjd_val = row["mjd"] if "mjd" in colnames else row["time_bounds_lower"]
            band_val = (
                row["bandpass"]
                if "bandpass" in colnames
                else (
                    row["energy_bandpassname"]
                    if "energy_bandpassname" in colnames
                    else row["band"]
                )
            )
            if isinstance(uri_val, bytes):
                uri_val = uri_val.decode("utf-8")
            if isinstance(band_val, bytes):
                band_val = band_val.decode("utf-8")
            rows.append(
                {
                    "uri": str(uri_val).strip(),
                    "mjd": float(mjd_val),
                    "bandpass": str(band_val).strip(),
                }
            )

        if max_cutouts <= 0 or len(rows) <= max_cutouts:
            return rows

        # Group by bandpass/detector so every detector gets a uniform MJD subsample
        by_band: dict[str, list[dict[str, Any]]] = {}
        for r in rows:
            by_band.setdefault(r["bandpass"], []).append(r)

        n_bands = max(1, len(by_band))
        per_band = max(5, max_cutouts // n_bands)
        thinned: list[dict[str, Any]] = []
        for band_rows in by_band.values():
            band_rows.sort(key=lambda item: item["mjd"])
            if len(band_rows) <= per_band:
                thinned.extend(band_rows)
            else:
                idxs = np.linspace(0, len(band_rows) - 1, per_band, dtype=int)
                thinned.extend(band_rows[int(i)] for i in idxs)

        thinned.sort(key=lambda item: item["mjd"])
        return thinned[:max_cutouts]

    return _tap_query


_BUCKET_DIR_RE = re.compile(
    r"^ra([+-]?\d+(?:\.\d+)?)_dec([+-]?\d+(?:\.\d+)?)_s(\d+)$"
)


def _angular_sep_arcsec(
    ra1_deg: float, dec1_deg: float, ra2_deg: float, dec2_deg: float
) -> float:
    """Great-circle separation in arcseconds between two ICRS positions."""
    ra1, dec1, ra2, dec2 = (math.radians(v) for v in (ra1_deg, dec1_deg, ra2_deg, dec2_deg))
    dra = ra2 - ra1
    ddec = dec2 - dec1
    a = (
        math.sin(ddec / 2.0) ** 2
        + math.cos(dec1) * math.cos(dec2) * math.sin(dra / 2.0) ** 2
    )
    return math.degrees(2.0 * math.asin(min(1.0, math.sqrt(max(0.0, a))))) * 3600.0


def _find_covering_cached_field(
    cache_root: Path,
    ra: float,
    dec: float,
) -> tuple[float, float, float] | None:
    """Return ``(center_ra, center_dec, size_arcsec)`` of a cached cutout covering ``(ra, dec)``."""
    if not cache_root.is_dir():
        return None
    best: tuple[float, float, tuple[float, float, float]] | None = None
    for entry in cache_root.iterdir():
        if not entry.is_dir():
            continue
        match = _BUCKET_DIR_RE.match(entry.name)
        if match is None or not (entry / "index.csv").is_file():
            continue
        c_ra = float(match.group(1))
        c_dec = float(match.group(2))
        size_arcsec = float(match.group(3))
        sep = _angular_sep_arcsec(c_ra, c_dec, ra, dec)
        if sep <= 0.45 * size_arcsec:
            score = (-size_arcsec, sep)
            if best is None or score < (best[0], best[1]):
                best = (score[0], score[1], (c_ra, c_dec, size_arcsec))
    return None if best is None else best[2]


def _recover_partial_cache(
    cache_root: Path,
    ra: float,
    dec: float,
    fov_arcsec: float,
    min_frames: int,
) -> None:
    """If an interrupted run left unindexed .fits files in a cache bucket, build index.csv."""
    if not cache_root.is_dir():
        return
    ra_norm, dec_norm = data._normalize_coords(ra, dec)
    for bucket in data.SIZE_BUCKETS_ARCSEC:
        if bucket + 1e-6 < fov_arcsec:
            continue
        key = data.cache_key_for(ra_norm, dec_norm, bucket)
        cdir = cache_root / key
        if cdir.is_dir() and not (cdir / "index.csv").is_file():
            fits_count = sum(
                1 for p in cdir.glob("*.fits") if not p.name.startswith(".") and p.is_file()
            )
            if fits_count >= min_frames:
                data.rebuild_cache_index(cdir)


def run_flicker_experiment(
    *,
    stars_csv: str | Path = _REPO_ROOT / "data" / "flicker_stars.csv",
    stars: Sequence[FlickerStar] | None = None,
    frame_loader: Callable[[FlickerStar], Sequence[Frame]] | None = None,
    results_dir: str | Path = _REPO_ROOT / "results",
    cache_root: str | Path = _REPO_ROOT / "cache",
    data_mode: str = "cache",
    fov_arcsec: float = DEFAULT_FOV_ARCSEC,
    aperture_radius: float = DEFAULT_APERTURE_RADIUS_PX,
    annulus_inner: float | None = None,
    annulus_outer: float | None = None,
    window_widths_um: Sequence[float] = DEFAULT_WINDOW_WIDTHS_UM,
    time_windows_days: Sequence[float] = DEFAULT_TIME_WINDOWS_DAYS,
    threshold: float = DEFAULT_THRESHOLD,
    min_stars: int = DEFAULT_MIN_STARS,
    min_frames_per_star: int = DEFAULT_MIN_FRAMES_PER_STAR,
    max_cutouts_per_star: int = DEFAULT_MAX_CUTOUTS_PER_STAR,
    command: str | None = None,
) -> FlickerExperimentResult:
    """Execute stationary-star chromatic flicker experiment and write outputs to ``results_dir``.

    Raises:
        ValueError: If fewer than ``min_stars`` (default 3) have ``>= min_frames_per_star``
            (default 5) usable frames; in that case no files are written to ``results_dir``.
    """
    r_in, r_out = _resolve_annulus_radii(aperture_radius, annulus_inner, annulus_outer)
    star_list = list(stars) if stars is not None else load_flicker_stars(stars_csv)
    croot = Path(cache_root)
    tap_fn = _make_capped_tap_query(max_cutouts_per_star) if max_cutouts_per_star > 0 else None

    def _default_loader(star: FlickerStar) -> Sequence[Frame]:
        _recover_partial_cache(croot, star.ra, star.dec, fov_arcsec, min_frames_per_star)
        print(
            f"[flicker] Loading frames for {star.name} "
            f"(RA={star.ra:.4f}, Dec={star.dec:.4f}, mode={data_mode})...",
            flush=True,
        )

        def _on_progress(done: int, total: int, _uri: str, ok: bool) -> None:
            if done == 1 or done % 5 == 0 or done == total:
                status = "ok" if ok else "FAIL"
                print(
                    f"  [{star.name}] cutout {done}/{total} ({status})",
                    flush=True,
                )

        if data_mode in ("cache", "auto"):
            try:
                return data.get_frames(
                    star.ra,
                    star.dec,
                    fov=fov_arcsec,
                    mode="cache",
                    cache_root=croot,
                )
            except data.NotCachedError:
                cover = _find_covering_cached_field(croot, star.ra, star.dec)
                if cover is not None:
                    c_ra, c_dec, c_fov = cover
                    return data.get_frames(
                        c_ra,
                        c_dec,
                        fov=c_fov,
                        mode="cache",
                        cache_root=croot,
                    )
                if data_mode == "cache":
                    print(
                        f"  [{star.name}] skipped: not in local cache or covering field.",
                        flush=True,
                    )
                    return ()

        try:
            return data.get_frames(
                star.ra,
                star.dec,
                fov=fov_arcsec,
                mode=data_mode,
                cache_root=croot,
                tap_query=tap_fn,
                progress_callback=_on_progress,
            )
        except data.DataError as exc:
            print(f"  [{star.name}] skipped due to DataError: {exc}", flush=True)
            return ()

    loader = frame_loader if frame_loader is not None else _default_loader

    summaries: list[StarPhotometrySummary] = []
    versions_seen: set[str] = set()

    for star in star_list:
        raw_frames = loader(star)
        summary = collect_star_photometry(
            star,
            raw_frames,
            aperture_radius=aperture_radius,
            annulus_inner=r_in,
            annulus_outer=r_out,
        )
        summaries.append(summary)
        for det_s in summary.detectors.values():
            for v in det_s.versions:
                if v is not None:
                    versions_seen.add(f"l2b-v{int(v)}")

    qualifying_stars = [s for s in summaries if s.n_usable_frames >= min_frames_per_star]
    if len(qualifying_stars) < min_stars:
        raise ValueError(
            f"Insufficient usable stars for flicker experiment: {len(qualifying_stars)} of "
            f"required {min_stars} stars have >= {min_frames_per_star} usable frames. "
            "No results written."
        )

    wl_curve = compute_window_scatter(summaries, window_widths_um, coordinate="wavelength")
    time_curve = compute_window_scatter(summaries, time_windows_days, coordinate="time")
    same_wl_time_curve = compute_window_scatter(
        summaries,
        time_windows_days,
        coordinate="time",
        max_wavelength_diff_um=0.03,
    )
    recommendation = evaluate_recommendation(summaries, wl_curve, threshold=threshold)

    out_dir = Path(results_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    flux_plot_path = plot_normalised_flux_vs_wavelength(
        summaries, out_dir / "flicker_flux_vs_wavelength.png"
    )
    scatter_plot_path = plot_excess_scatter_wavelength_and_time(
        wl_curve,
        time_curve,
        same_wl_time_curve,
        recommendation,
        out_dir / "flicker_scatter_wavelength_vs_time.png",
    )

    cmd_str = (
        command
        if command is not None
        else "python " + " ".join(shlex.quote(a) for a in sys.argv)
    )
    total_frames = sum(s.n_total_frames for s in summaries)
    usable_frames = sum(s.n_usable_frames for s in summaries)
    skipped_frames = sum(s.n_skipped_frames for s in summaries)

    result = FlickerExperimentResult(
        command=cmd_str,
        data_versions=sorted(versions_seen) if versions_seen else ["unknown"],
        aperture_settings={
            "aperture_radius_px": float(aperture_radius),
            "annulus_inner_radius_px": float(r_in),
            "annulus_outer_radius_px": float(r_out),
            "fov_arcsec": float(fov_arcsec),
        },
        criteria={
            "threshold_fractional_scatter": float(threshold),
            "min_stars": int(min_stars),
            "min_frames_per_star": int(min_frames_per_star),
            "window_widths_um": [float(w) for w in window_widths_um],
            "time_windows_days": [float(t) for t in time_windows_days],
        },
        frame_counts={
            "total": int(total_frames),
            "usable": int(usable_frames),
            "skipped": int(skipped_frames),
            "n_stars_total": len(summaries),
            "n_stars_with_5plus_usable": len(qualifying_stars),
        },
        stars=summaries,
        wavelength_window_analysis=wl_curve,
        time_window_analysis=time_curve,
        same_wavelength_time_analysis=same_wl_time_curve,
        recommendation=recommendation,
        plot_paths=[str(flux_plot_path), str(scatter_plot_path)],
    )

    json_path = out_dir / "flicker.json"
    json_path.write_text(
        json.dumps(_serialize_for_json(asdict(result)), indent=2) + "\n",
        encoding="utf-8",
    )

    print(
        f"Flicker experiment complete: {len(qualifying_stars)}/{len(summaries)} qualifying stars, "
        f"{usable_frames} usable frames ({skipped_frames} skipped)."
    )
    print(
        f"Largest window below {threshold * 100:.1f}% excess scatter: "
        f"{recommendation.max_window_below_threshold_um} um"
    )
    print(
        f"Recommended processing.group_frames max_width: "
        f"{recommendation.recommended_max_width_um} um"
    )
    print(f"Justification: {recommendation.justification}")

    return result


def _serialize_for_json(obj: Any) -> Any:
    """Convert NaN/Inf floats to None recursively so ``results/flicker.json`` is strict JSON."""
    if isinstance(obj, float):
        return obj if math.isfinite(obj) else None
    if isinstance(obj, dict):
        return {str(k): _serialize_for_json(v) for k, v in obj.items()}
    if isinstance(obj, (list, tuple)):
        return [_serialize_for_json(v) for v in obj]
    return obj


def build_parser() -> argparse.ArgumentParser:
    """Build CLI argument parser for ``experiments/flicker.py``."""
    parser = argparse.ArgumentParser(
        description=(
            "Measure stationary-star brightness variation vs wavelength and time difference."
        )
    )
    parser.add_argument(
        "--stars-csv",
        type=Path,
        default=_REPO_ROOT / "data" / "flicker_stars.csv",
        help="Path to stationary stars CSV (default: data/flicker_stars.csv).",
    )
    parser.add_argument(
        "--results-dir",
        type=Path,
        default=_REPO_ROOT / "results",
        help="Directory for flicker.json and flicker_*.png outputs.",
    )
    parser.add_argument(
        "--cache-root",
        type=Path,
        default=_REPO_ROOT / "cache",
        help="Cutout cache root directory.",
    )
    parser.add_argument(
        "--mode",
        type=str,
        default="auto",
        choices=("cache", "live", "auto", "synthetic"),
        help="Data retrieval mode passed to data.get_frames (default: auto).",
    )
    parser.add_argument(
        "--fov",
        type=float,
        default=DEFAULT_FOV_ARCSEC,
        help="Field of view in arcseconds (default: 120).",
    )
    parser.add_argument(
        "--aperture-radius",
        type=float,
        default=DEFAULT_APERTURE_RADIUS_PX,
        help="Circular aperture radius in pixels (default: 2.0).",
    )
    parser.add_argument(
        "--threshold",
        type=float,
        default=DEFAULT_THRESHOLD,
        help="Fractional excess scatter threshold (default: 0.05 for 5 percent).",
    )
    parser.add_argument(
        "--max-cutouts-per-star",
        type=int,
        default=DEFAULT_MAX_CUTOUTS_PER_STAR,
        help=(
            "Maximum cutouts fetched per uncached star in auto/live mode to avoid "
            "downloading thousands of North Ecliptic Pole deep-field frames "
            "(default: 30; 0 for all)."
        ),
    )
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    """CLI entry point for ``experiments/flicker.py``."""
    args = build_parser().parse_args(argv)
    cmd = "python " + " ".join(
        shlex.quote(a) for a in (sys.argv if argv is None else ["experiments/flicker.py", *argv])
    )
    run_flicker_experiment(
        stars_csv=args.stars_csv,
        results_dir=args.results_dir,
        cache_root=args.cache_root,
        data_mode=args.mode,
        fov_arcsec=args.fov,
        aperture_radius=args.aperture_radius,
        threshold=args.threshold,
        max_cutouts_per_star=args.max_cutouts_per_star,
        command=cmd,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
