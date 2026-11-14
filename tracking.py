# AI-assisted: implemented with Codex.
"""Conservative per-frame target association using motion.measure_frame_centroid.

Criteria fixed before evaluation on real targets:
* Accept only one qualifying local peak in the prediction's search gate.
* Peaks must exceed 3 local pixel sigmas above the unmasked median background;
  their centroid-aperture SNR must be >= 5. Both thresholds are configurable.
* The converged centroid must remain within the search radius of the prediction.
* Reject ambiguous, off-image, edge-truncated, masked, or low-SNR measurements.
  Rejected measurements have null measured positions; predictions are separate.
* Synthetic known-answer tests require centroid errors < 0.15 pixel for isolated
  high-SNR Gaussian sources, explicit rejection of ambiguous/undetected sources,
  and consistent FITS-to-PNG projection without mutation or frame substitution.

This is positional association, not proof of source identity. Each frame is
independent: no previous centroid is silently reused. Input frames must already
be cleaned/aligned, sorted by MJD, and share one detector/window/version. Pass
one Prediction per frame for an externally determined trajectory; a single
Prediction explicitly requests a constant sky position. No proper motion is
inferred from target names in this module. No fitted chi-squared is produced.

Run ``python tracking.py --help``. The CLI uses existing retrieval/processing
interfaces and writes computed JSON to results/ with command and data versions.
"""
from __future__ import annotations

import argparse
import json
import math
import os
import subprocess
import sys
import tempfile
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Sequence

import numpy as np
from astropy import units as u
from scipy.ndimage import maximum_filter

from frame import Frame
from motion import FrameCentroid, measure_frame_centroid


@dataclass(frozen=True)
class Prediction:
    """Expected ICRS position, distinct from a measured source position."""

    ra: float
    dec: float
    sigma_arcsec: float | None = None

    def __post_init__(self) -> None:
        if not (math.isfinite(self.ra) and 0 <= self.ra < 360
                and math.isfinite(self.dec) and -90 <= self.dec <= 90):
            raise ValueError("Prediction requires finite RA [0,360) and Dec [-90,90].")
        if self.sigma_arcsec is not None and (
            not math.isfinite(self.sigma_arcsec) or self.sigma_arcsec < 0
        ):
            raise ValueError("Prediction uncertainty must be finite and non-negative.")


@dataclass(frozen=True)
class TrackingConfig:
    """Association settings in native pixels; radii are not display smoothing."""

    search_radius_px: float = 4.0
    peak_snr: float = 3.0
    centroid_snr: float = 5.0
    radii: tuple[float, ...] = (4.0, 3.0, 2.5)
    bg_inner_radius: float = 5.0
    bg_outer_radius: float = 8.0
    max_width_um: float = .10

    def __post_init__(self) -> None:
        values = (self.search_radius_px, self.peak_snr, self.centroid_snr,
                  self.bg_inner_radius, self.bg_outer_radius, self.max_width_um, *self.radii)
        if not self.radii or any(not math.isfinite(v) or v <= 0 for v in values):
            raise ValueError("Tracking radii, thresholds and window width must be positive/finite.")
        if self.bg_inner_radius <= max(self.radii) or self.bg_outer_radius <= self.bg_inner_radius:
            raise ValueError("Background annulus must lie outside the centroid apertures.")


def _validate_frames(frames: Sequence[Frame], config: TrackingConfig) -> None:
    if not frames:
        raise ValueError("No SPHEREx frames available for tracking.")
    if len({f.detector for f in frames}) != 1:
        raise ValueError("Tracking requires one detector per sequence.")
    if len({f.version for f in frames}) != 1:
        raise ValueError("Tracking cannot mix processing versions, including unknown versions.")
    if max(f.wavelength for f in frames) - min(f.wavelength for f in frames) > config.max_width_um + 1e-9:
        raise ValueError("Tracking wavelength window exceeds the configured maximum width.")
    if any(b.mjd < a.mjd for a, b in zip(frames, frames[1:])):
        raise ValueError("Tracking frames must be sorted by MJD.")
    for f in frames:
        if f.wcs is None or not f.wcs.has_celestial:
            raise ValueError("Tracking requires a celestial WCS on every frame.")
        if f.zodi is not None:
            raise ValueError("Clean frames before tracking: zodiacal light has not been subtracted.")


def _sky_measurement(frame: Frame, c: FrameCentroid) -> dict[str, Any]:
    """Propagate marginal pixel errors via a numerical local celestial Jacobian."""
    wcs = frame.wcs.celestial
    sky = wcs.pixel_to_world(c.x, c.y)
    step = .001
    columns = []
    for dx, dy in ((step, 0), (0, step)):
        other = wcs.pixel_to_world(c.x + dx, c.y + dy)
        offsets = sky.spherical_offsets_to(other)
        columns.append([float(v.to_value(u.arcsec)) / step for v in offsets])
    jacobian = np.asarray(columns).T
    covariance = jacobian @ np.diag([c.sigma_x**2, c.sigma_y**2]) @ jacobian.T
    height = frame.image.shape[0]
    return dict(x=float(c.x) + .5, y=height - .5 - float(c.y),
                fits_x=float(c.x), fits_y=float(c.y), sigma_x=float(c.sigma_x),
                sigma_y=float(c.sigma_y), ra=float(sky.icrs.ra.deg), dec=float(sky.icrs.dec.deg),
                sigma_ra_arcsec=float(np.sqrt(covariance[0, 0])),
                sigma_dec_arcsec=float(np.sqrt(covariance[1, 1])),
                covariance_arcsec2=covariance.tolist(), snr=float(c.snr))


def _candidates(frame: Frame, x: float, y: float, config: TrackingConfig) -> list[tuple[int, int]]:
    """Find qualifying local maxima only inside the prediction's bounded gate."""
    valid = (~frame.mask & np.isfinite(frame.image) & np.isfinite(frame.variance)
             & (frame.variance > 0))
    if not np.any(valid):
        return []
    background = float(np.median(frame.image[valid]))
    values = np.where(valid, frame.image, -np.inf)
    yy, xx = np.indices(frame.image.shape)
    peaks = (valid & (np.hypot(xx - x, yy - y) <= config.search_radius_px)
             & (values == maximum_filter(values, size=3, mode="constant", cval=-np.inf))
             & ((values - background) >= config.peak_snr * np.sqrt(np.maximum(frame.variance, 0))))
    return [(int(px), int(py)) for py, px in np.argwhere(peaks)]


def _track_frame(frame: Frame, prediction: Prediction, config: TrackingConfig,
                 index: int) -> dict[str, Any]:
    x, y = frame.wcs.celestial.world_to_pixel_values(prediction.ra, prediction.dec)
    x, y = float(x), float(y)
    height, width = frame.image.shape
    projected = dict(ra=prediction.ra, dec=prediction.dec, sigma_arcsec=prediction.sigma_arcsec,
                     x=x + .5 if math.isfinite(x) else None,
                     y=height - .5 - y if math.isfinite(y) else None)
    result = dict(index=index, mjd=float(frame.mjd), wavelength_um=float(frame.wavelength),
                  detector=int(frame.detector), version=frame.version, uri=frame.uri,
                  image_shape=[height, width], predicted=projected,
                  status="rejected", reason="off_frame", measured=None, warnings=[])
    if not (math.isfinite(x) and math.isfinite(y) and 0 <= x < width and 0 <= y < height):
        return result
    candidates = []
    for px, py in _candidates(frame, x, y, config):
        centroid = measure_frame_centroid(
            frame, px, py, radii=config.radii, snr_threshold=config.centroid_snr,
            bg_inner_radius=config.bg_inner_radius, bg_outer_radius=config.bg_outer_radius)
        if centroid is not None and math.hypot(centroid.x - x, centroid.y - y) <= config.search_radius_px:
            candidates.append(centroid)
    if not candidates:
        result["reason"] = "no_qualified_source"
        return result
    if len(candidates) > 1:
        result["reason"] = "ambiguous"
        result["warnings"].append("Multiple qualifying peaks: no unique target association.")
        return result
    centroid = candidates[0]
    if min(centroid.x, centroid.y, width - 1 - centroid.x,
           height - 1 - centroid.y) < config.bg_outer_radius:
        result["reason"] = "edge_truncated"
        return result
    measurement = _sky_measurement(frame, centroid)
    numeric = [measurement[k] for k in ("x", "y", "sigma_x", "sigma_y", "ra", "dec",
                                       "sigma_ra_arcsec", "sigma_dec_arcsec", "snr")]
    if not all(math.isfinite(v) for v in numeric):
        result["reason"] = "invalid_uncertainty"
        return result
    if centroid.used_unreliable_in_centroid:
        result["warnings"].append("Unreliable/saturated pixels contributed to this centroid.")
    result.update(status="detected", reason=None, measured=measurement)
    return result


def track_frames(frames: Sequence[Frame], predictions: Prediction | Sequence[Prediction],
                 config: TrackingConfig | None = None) -> dict[str, Any]:
    """Measure each frame independently, preserving indices and rejected entries.

    PNG positions use edge coordinates (pixel centres at half-integers, y down).
    Uncertainties exclude WCS calibration errors and reprojection correlations.
    Predictions are never returned as measured detections or used as a fallback.
    """
    config = config or TrackingConfig()
    _validate_frames(frames, config)
    positions = [predictions] * len(frames) if isinstance(predictions, Prediction) else list(predictions)
    if len(positions) != len(frames) or not all(isinstance(p, Prediction) for p in positions):
        raise ValueError("Provide one Prediction or exactly one Prediction per frame.")
    synthetic = [(f.meta or {}).get("synthetic", False)
                 or (f.meta or {}).get("data_source") == "synthetic"
                 or (f.uri or "").startswith("synthetic://") for f in frames]
    if any(synthetic) and not all(synthetic):
        raise ValueError("Tracking cannot mix synthetic and real observations.")
    entries = [_track_frame(f, p, config, i) for i, (f, p) in enumerate(zip(frames, positions))]
    warnings = ["Positional association is not proof of source identity.",
                "Centroid errors exclude WCS calibration systematics and reprojection correlations."]
    if isinstance(predictions, Prediction):
        warnings.append("Constant-position prediction: moving sources outside the search gate are rejected.")
    if frames[0].version is None:
        warnings.append("Processing version is unknown.")
    if all(synthetic):
        warnings.append("SYNTHETIC DATA: these tracks do not measure a real astronomical target.")
    return dict(synthetic=bool(all(synthetic)), config=asdict(config), frames=entries,
                n_frames=len(entries), n_detected=sum(r["status"] == "detected" for r in entries),
                warnings=warnings)


def write_result(result: dict[str, Any], destination: Path, command: str) -> None:
    """Write a computed report atomically, preserving parameters and provenance."""
    output = {**result, "command": command,
              "data_versions": sorted({str(f["version"]) for f in result["frames"]})}
    destination.parent.mkdir(parents=True, exist_ok=True)
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", suffix=".tmp",
                                         dir=destination.parent, delete=False) as handle:
            temporary = Path(handle.name)
            json.dump(output, handle, indent=2, allow_nan=False)
        os.replace(temporary, destination)
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)


def main(argv: Sequence[str] | None = None) -> int:
    """Rerunnable measurement command; no science numbers are supplied by hand."""
    import coords
    import data
    import processing

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--target")
    parser.add_argument("--ra")
    parser.add_argument("--dec")
    parser.add_argument("--fov", type=float, default=120)
    parser.add_argument("--detector", type=int, choices=range(1, 7))
    parser.add_argument("--wmin", type=float)
    parser.add_argument("--wmax", type=float)
    parser.add_argument("--search-radius", type=float, default=4)
    parser.add_argument("--mode", choices=sorted(data.VALID_MODES), default="cache")
    parser.add_argument("--cache-dir", type=Path, default=Path("cache"))
    parser.add_argument("--output", type=Path, default=Path("results/tracking.json"))
    args = parser.parse_args(argv)
    try:
        if not 30 <= args.fov <= 600:
            raise ValueError("Field of view must be between 30 and 600 arcseconds.")
        if args.target and args.ra is None and args.dec is None:
            resolved = coords.resolve_name(args.target)
            ra, dec = resolved.ra, resolved.dec
            extra_warnings = list(resolved.warnings)
        elif not args.target and args.ra is not None and args.dec is not None:
            ra, dec = coords.parse_position(args.ra, args.dec)
            extra_warnings = []
        else:
            raise ValueError("Use a target name or both RA/Dec, not both.")
        raw = data.get_frames(ra, dec, args.fov, mode=args.mode, cache_root=args.cache_dir)
        group = processing.group_frames(raw, detector=args.detector, wmin=args.wmin, wmax=args.wmax)
        aligned = processing.align_frames(tuple(processing.clean_frame(f) for f in group.frames),
                                          target_ra=ra, target_dec=dec, fov_arcsec=args.fov)
        result = track_frames(aligned, Prediction(ra, dec),
                              TrackingConfig(search_radius_px=args.search_radius))
        if result["synthetic"] and args.mode != "synthetic":
            raise RuntimeError("Synthetic frames were returned outside explicit synthetic mode.")
        result["warnings"] += extra_warnings + list(group.warnings)
        result["selection"] = dict(ra=ra, dec=dec, fov_arcsec=args.fov,
                                   detector=group.detector, window_um=group.wavelength_window,
                                   mode=args.mode, cache_dir=str(args.cache_dir.resolve()))
        command = subprocess.list2cmdline([sys.executable, str(Path(__file__).resolve()),
                                           *(list(argv) if argv is not None else sys.argv[1:])])
        write_result(result, args.output, command)
        print(f"{result['n_detected']}/{result['n_frames']} frames associated; report: {args.output}")
        return 0
    except (ValueError, RuntimeError, OSError) as exc:
        print(f"Tracking failed: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
