# AI-assisted: implemented with Codex.
"""Per-exposure aperture photometry for a sampled, non-simultaneous SED.

Criteria fixed before evaluation on real targets:
* Use only matched tracking records; never replace missing centroids implicitly.
* Forced photometry at a prediction requires allow_forced=True and is labelled.
* Exclude mask, unreliable pixels, non-finite data, and non-positive variance.
* Reject edge-truncated apertures, usable aperture fraction < 0.8, and annuli
  with fewer than 10 usable pixels after iterative 3-sigma clipping (5 passes).
* Defaults: radius 2.5 pixels, annulus 5..8 pixels, 5x5 subpixel aperture weights.
* Positive flux / propagated sigma >= 5 flags significant aperture signal only;
  it is not an independent target identification. Negative fluxes are retained.
* Known-answer tests: fractional aperture sum and its propagated variance agree
  with explicit calculations; Monte Carlo flux scatter agrees with predicted
  sigma within 25%; masked/unreliable pixels never contribute to flux.

Inputs are cleaned Frames, not necessarily on a common grid: each exposure uses
its own celestial WCS to position the aperture. Different detectors/wavelengths
are measured separately, never coadded. MJy/sr * pixel solid angle * 1e6 gives
Jy. Pixel solid angle is approximated by the celestial projection-plane area
for these small cutouts. Variance = sum(w^2 V) + sum(w)^2 Var(background), with
Gaussian median variance pi/(2N)*max(mean(V_bg), sample variance_bg). These are
conditional statistical errors: positional uncertainty, aperture correction,
absolute calibration, correlated noise and WCS systematics are NOT included.
There is no fitted SED or reduced chi-squared to rescale in this module.

Run ``python spectrum.py --help`` to write a reproducible results/ report.
"""
from __future__ import annotations

import argparse
import math
from dataclasses import asdict, dataclass
from pathlib import Path
import subprocess
import sys
from typing import Any, Mapping, Sequence

import numpy as np
from astropy.stats import sigma_clip
from astropy.wcs.utils import proj_plane_pixel_area

from frame import Frame
from tracking import Prediction, track_frames, write_result


@dataclass(frozen=True)
class PhotometryConfig:
    """Aperture radii in native pixels; no detector-dependent fitting/tuning."""

    radius_px: float = 2.5
    annulus_inner_px: float = 5.0
    annulus_outer_px: float = 8.0
    subpixels: int = 5
    min_aperture_fraction: float = .8
    min_background_pixels: int = 10
    detection_snr: float = 5.0

    def __post_init__(self) -> None:
        if not all(math.isfinite(v) and v > 0 for v in (
            self.radius_px, self.annulus_inner_px, self.annulus_outer_px, self.detection_snr
        )) or not self.radius_px < self.annulus_inner_px < self.annulus_outer_px:
            raise ValueError("Require positive finite radius < inner annulus < outer annulus.")
        if isinstance(self.subpixels, bool) or not isinstance(self.subpixels, int) or not 1 <= self.subpixels <= 25:
            raise ValueError("subpixels must be an integer in 1..25.")
        if (isinstance(self.min_background_pixels, bool)
            or not isinstance(self.min_background_pixels, int) or self.min_background_pixels < 3):
            raise ValueError("Require at least 3 background pixels.")
        if not math.isfinite(self.min_aperture_fraction) or not 0 < self.min_aperture_fraction <= 1:
            raise ValueError("min_aperture_fraction must lie in (0,1].")


def aperture_weights(shape: tuple[int, int], x: float, y: float,
                     radius: float, subpixels: int) -> np.ndarray:
    """Fractional circular pixel coverage, sampled on a uniform subpixel grid."""
    yy, xx = np.indices(shape, dtype=float)
    weights = np.zeros(shape, dtype=float)
    offsets = (np.arange(subpixels) + .5) / subpixels - .5
    for dx in offsets:
        for dy in offsets:
            weights += (xx + dx - x)**2 + (yy + dy - y)**2 <= radius**2
    return weights / subpixels**2


def _position(row: Mapping[str, Any], frame: Frame, index: int,
              allow_forced: bool) -> tuple[Mapping[str, Any] | None, str]:
    """Require explicit frame correspondence and measured/forced semantics."""
    for key, value in (("index", index), ("mjd", frame.mjd), ("detector", frame.detector),
                       ("wavelength_um", frame.wavelength), ("uri", frame.uri),
                       ("version", frame.version)):
        if key not in row or row[key] != value:
            raise ValueError(f"Tracking record {index} has mismatched {key}.")
    if row.get("status") == "detected":
        position = row.get("measured")
        if position is None:
            raise ValueError("Detected tracking row has no measured position.")
        for key in ("sigma_ra_arcsec", "sigma_dec_arcsec"):
            if not math.isfinite(position[key]) or position[key] <= 0:
                raise ValueError("Measured positions require finite positive uncertainties.")
        return position, "measured"
    if allow_forced:
        return row.get("predicted"), "forced"
    return None, "unavailable"


def _measure(frame: Frame, position: Mapping[str, Any], kind: str,
             config: PhotometryConfig) -> dict[str, Any]:
    """Measure flux without filling, rescaling or correcting missing pixels."""
    ra, dec = float(position["ra"]), float(position["dec"])
    if not (math.isfinite(ra) and 0 <= ra < 360 and math.isfinite(dec) and -90 <= dec <= 90):
        raise ValueError("Photometry position must contain valid finite ICRS coordinates.")
    x, y = frame.wcs.celestial.world_to_pixel_values(ra, dec)
    x, y = float(x), float(y)
    ny, nx = frame.image.shape
    result = dict(status="rejected", reason="off_frame", position_kind=kind,
                  position=dict(position), flux_jy=None, flux_sigma_jy=None, snr=None,
                  significant_aperture_signal=False, warnings=[])
    if not (math.isfinite(x) and math.isfinite(y) and 0 <= x < nx and 0 <= y < ny):
        return result
    if min(x + .5, y + .5, nx - .5 - x, ny - .5 - y) < config.radius_px:
        result["reason"] = "edge_truncated"
        return result
    usable = (~frame.mask & np.isfinite(frame.image) & np.isfinite(frame.variance)
              & (frame.variance > 0))
    if frame.unreliable is not None:
        usable &= ~frame.unreliable
    weights = aperture_weights(frame.image.shape, x, y, config.radius_px, config.subpixels)
    area = float(np.sum(weights))
    kept = np.where(usable, weights, 0.)
    valid_area = float(np.sum(kept))
    fraction = valid_area / area
    result["aperture_fraction"] = fraction
    if fraction < config.min_aperture_fraction or valid_area <= 0:
        result["reason"] = "insufficient_aperture"
        return result
    yy, xx = np.indices(frame.image.shape)
    distance = np.hypot(xx - x, yy - y)
    # WCS round-trips can put an integer-centred aperture ~1e-11 pixel off-centre.
    # Stabilize inclusion of annulus boundary pixels at numerical precision.
    ring = (usable & (distance >= config.annulus_inner_px - 1e-8)
            & (distance <= config.annulus_outer_px + 1e-8))
    pixels = frame.image[ring]
    variances = frame.variance[ring]
    if pixels.size:
        clipped = sigma_clip(pixels, sigma=3., maxiters=5)
        selected = ~np.ma.getmaskarray(clipped)
        pixels, variances = pixels[selected], variances[selected]
    if len(pixels) < config.min_background_pixels:
        result["reason"] = "insufficient_background"
        return result
    bg = float(np.median(pixels))
    bg_variance = math.pi / (2 * len(pixels)) * max(float(np.mean(variances)),
                                                  float(np.var(pixels, ddof=1)))
    # Explicit indexing prevents masked NaNs/infinities from leaking through 0 * NaN.
    use = kept > 0
    net = float(np.sum(kept[use] * (frame.image[use] - bg)))
    var = float(np.sum(kept[use]**2 * frame.variance[use])) + valid_area**2 * bg_variance
    solid_angle = float(proj_plane_pixel_area(frame.wcs.celestial)) * (math.pi / 180)**2
    if not math.isfinite(solid_angle) or solid_angle <= 0 or not math.isfinite(var) or var <= 0:
        result["reason"] = "invalid_uncertainty"
        return result
    factor = solid_angle * 1e6
    flux, sigma = net * factor, math.sqrt(var) * factor
    if not math.isfinite(flux) or not math.isfinite(sigma) or sigma <= 0:
        result["reason"] = "invalid_uncertainty"
        return result
    snr = flux / sigma
    result.update(status="forced" if kind == "forced" else "measured" if snr >= config.detection_snr else "low_snr",
                  reason=None, flux_jy=flux, flux_sigma_jy=sigma, snr=snr,
                  significant_aperture_signal=snr >= config.detection_snr,
                  aperture_area_pixels=area, usable_area_pixels=valid_area,
                  background_mjy_sr=bg, background_sigma_mjy_sr=math.sqrt(bg_variance),
                  n_background_pixels=len(pixels), pixel_solid_angle_sr=solid_angle)
    if fraction < 1:
        result["warnings"].append("Partial aperture: flux has not been corrected for excluded pixels.")
    if min(x + .5, y + .5, nx - .5 - x, ny - .5 - y) < config.annulus_outer_px:
        result["warnings"].append("Background annulus is truncated at the image edge.")
    if kind == "forced":
        result["warnings"].append("Forced photometry at a prediction; this does not establish a target detection.")
    return result


def extract_spectrum(frames: Sequence[Frame], tracking_rows: Sequence[Mapping[str, Any]],
                     *, allow_forced: bool = False,
                     config: PhotometryConfig | None = None) -> dict[str, Any]:
    """Return one sample per frame, including rejected points and their reasons."""
    config = config or PhotometryConfig()
    if not isinstance(allow_forced, bool):
        raise ValueError("allow_forced must be an explicit boolean.")
    if not frames or len(frames) != len(tracking_rows):
        raise ValueError("Provide non-empty Frames and exactly one matched tracking row per frame.")
    points = []
    synthetic = []
    for i, (frame, row) in enumerate(zip(frames, tracking_rows)):
        if frame.wcs is None or not frame.wcs.has_celestial:
            raise ValueError("SED photometry requires a celestial WCS.")
        if frame.zodi is not None:
            raise ValueError("Clean frames before SED photometry: subtract zodiacal light first.")
        position, kind = _position(row, frame, i, allow_forced)
        measured = (_measure(frame, position, kind, config) if position is not None else
                    dict(status="rejected", reason="no_position", position_kind=kind,
                         position=None, flux_jy=None, flux_sigma_jy=None, snr=None,
                         significant_aperture_signal=False, warnings=[]))
        measured["warnings"] = list(row.get("warnings", [])) + measured["warnings"]
        points.append(dict(index=i, mjd=float(frame.mjd), wavelength_um=float(frame.wavelength),
                           detector=int(frame.detector), version=frame.version, uri=frame.uri,
                           tracking_reason=row.get("reason"), **measured))
        synthetic.append(bool((frame.meta or {}).get("synthetic", False)
                              or (frame.meta or {}).get("data_source") == "synthetic"
                              or (frame.uri or "").startswith("synthetic://")))
    if any(synthetic) and not all(synthetic):
        raise ValueError("SED cannot combine synthetic and real observations.")
    warnings = ["Samples are separate exposures and may be non-simultaneous; variability can mimic spectral structure.",
                "Tracking is positional association, not proof of target identity; aperture signal can include blends.",
                "Flux is an uncorrected aperture sum, not total-source photometry or a fitted SED.",
                "Statistical errors exclude position, calibration, WCS systematics and pixel correlations.",
                "Wavelengths are Frame.wavelength at the requested target pixel, not a re-evaluated moving centroid.",
                "Pixel solid angle uses the small-cutout projection-plane approximation."]
    if len({f.version for f in frames}) > 1:
        warnings.append("Mixed processing versions: samples are kept separate and must be compared cautiously.")
    if any(f.version is None for f in frames):
        warnings.append("Some processing versions are unknown.")
    if all(synthetic):
        warnings.append("SYNTHETIC DATA: these samples are not astronomical observations.")
    return dict(synthetic=all(synthetic), flux_unit="Jy", config=asdict(config),
                allow_forced=allow_forced, frames=points, n_frames=len(points),
                n_points=sum(p["flux_jy"] is not None for p in points), warnings=warnings)


def main(argv: Sequence[str] | None = None) -> int:
    """Measure available exposures, without grouping different wavelengths into an image."""
    import coords
    import data
    import processing

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--target")
    parser.add_argument("--ra")
    parser.add_argument("--dec")
    parser.add_argument("--fov", type=float, default=240)
    parser.add_argument("--detector", type=int, choices=range(1, 7))
    parser.add_argument("--mode", choices=sorted(data.VALID_MODES), default="cache")
    parser.add_argument("--cache-dir", type=Path, default=Path("cache"))
    parser.add_argument("--allow-forced", action="store_true")
    parser.add_argument("--output", type=Path, default=Path("results/spectrum.json"))
    args = parser.parse_args(argv)
    try:
        if not 30 <= args.fov <= 600:
            raise ValueError("FOV must be between 30 and 600 arcseconds.")
        if args.target and args.ra is None and args.dec is None:
            resolved = coords.resolve_name(args.target)
            ra, dec, warnings = resolved.ra, resolved.dec, list(resolved.warnings)
        elif not args.target and args.ra is not None and args.dec is not None:
            ra, dec = coords.parse_position(args.ra, args.dec)
            warnings = []
        else:
            raise ValueError("Use a target name or both RA/Dec, not both.")
        raw = data.get_frames(ra, dec, args.fov, mode=args.mode, cache_root=args.cache_dir)
        cleaned = [processing.clean_frame(f) for f in raw if args.detector is None or f.detector == args.detector]
        rows = []
        for i, frame in enumerate(cleaned):
            row = track_frames([frame], Prediction(ra, dec))["frames"][0]
            row["index"] = i
            rows.append(row)
        result = extract_spectrum(cleaned, rows, allow_forced=args.allow_forced)
        if result["synthetic"] and args.mode != "synthetic":
            raise RuntimeError("Synthetic data returned outside explicit synthetic mode.")
        result["warnings"] += warnings
        result["warnings"].append("CLI tracking uses a constant-position prediction, not an inferred moving trajectory.")
        result["selection"] = dict(ra=ra, dec=dec, fov_arcsec=args.fov, detector=args.detector,
                                   mode=args.mode, cache_dir=str(args.cache_dir.resolve()))
        command = subprocess.list2cmdline([sys.executable, str(Path(__file__).resolve()),
                                           *(list(argv) if argv is not None else sys.argv[1:])])
        write_result(result, args.output, command)
        print(f"{result['n_points']}/{result['n_frames']} aperture samples; report: {args.output}")
        return 0
    except (ValueError, RuntimeError, OSError) as exc:
        print(f"SED photometry failed: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
