"""app.py - FastAPI backend + static page. Owner: backend developer.

Run:   uvicorn app:app --reload
Open:  http://127.0.0.1:8000/?target=WISE0855&mode=blink

Every UI state lives in the URL query string, and the API uses the SAME
parameter names:
    ra, dec     decimal degrees or sexagesimal (or use target=NAME)
    fov         field of view in arcsec (30 to 600)
    mode        blink | stack | diff
    detector    1 to 6
    wmin, wmax  wavelength window in micrometres
    epoch_a, epoch_b   which half of the time range is "earlier" (0) / "later" (1)
"""

from __future__ import annotations

import io
import logging
import warnings
from functools import lru_cache
from pathlib import Path

import matplotlib.colors as mcolors
import numpy as np
from astropy.wcs import FITSFixedWarning
from fastapi import Depends, FastAPI, HTTPException, Query
from fastapi.responses import FileResponse, Response
from fastapi.staticfiles import StaticFiles
from matplotlib import colormaps
from PIL import Image

from . import catalogs
from . import data
from . import flags
from . import sphx_stats as ss
from .coords import PositionError, parse_position, recommend_detector

warnings.filterwarnings("ignore", category=FITSFixedWarning)
logging.getLogger("astropy").setLevel(logging.ERROR)

app = FastAPI(title="SPHEREx sky-motion viewer")
STATIC = Path(__file__).parent.parent / "static"
FRONTEND_DIST = Path(__file__).parent.parent / "frontend" / "dist"
WAVELENGTH_TOLERANCE = 0.1  # micrometres. TODO(statistician): set from the colour experiment
Z_THRESHOLD = 5.0
DISPLAY_SIZE = 512

# Astronomical difference colormap for dark space apps UI:
# Negative significance (disappearance in earlier epoch): vivid ruby red (#ff5252)
# Background noise (-2.5 to +2.5): dark space slate (#0b0f1a)
# Positive significance (appearance in later epoch): vivid emerald green (#38ef7d)
_diff_cdict = {
    "red": [
        (0.0, 1.0, 1.0),
        (0.30, 0.85, 0.85),
        (0.42, 0.12, 0.12),
        (0.50, 0.04, 0.04),
        (0.58, 0.04, 0.04),
        (0.70, 0.15, 0.15),
        (1.0, 0.22, 0.22),
    ],
    "green": [
        (0.0, 0.22, 0.22),
        (0.30, 0.10, 0.10),
        (0.42, 0.06, 0.06),
        (0.50, 0.06, 0.06),
        (0.58, 0.09, 0.09),
        (0.70, 0.65, 0.65),
        (1.0, 0.95, 0.95),
    ],
    "blue": [
        (0.0, 0.35, 0.35),
        (0.30, 0.18, 0.18),
        (0.42, 0.11, 0.11),
        (0.50, 0.10, 0.10),
        (0.58, 0.14, 0.14),
        (0.70, 0.50, 0.50),
        (1.0, 0.70, 0.70),
    ],
}
ASTRO_DIFF_CMAP = mcolors.LinearSegmentedColormap("astro_diff", _diff_cdict)


class Params:
    """Shared, validated URL parameters."""

    def __init__(
        self,
        ra: str | None = Query(None, description="RA, degrees or 08h55m10.8s"),
        dec: str | None = Query(None, description="Dec, degrees or -07d14m42s"),
        target: str | None = Query(None, description="Object name, e.g. WISE0855"),
        fov: float = Query(120.0, ge=30.0, le=600.0, description="Field of view, arcsec"),
        mode: str = Query("blink", pattern="^(blink|stack|diff)$"),
        detector: int | None = Query(None, ge=1, le=6),
        wmin: float | None = Query(None, ge=0.7, le=5.1),
        wmax: float | None = Query(None, ge=0.7, le=5.1),
        epoch_a: int | None = Query(None, ge=0, le=50),
        epoch_b: int | None = Query(None, ge=0, le=50),
    ):
        try:
            self.ra, self.dec = parse_position(ra, dec, target)
        except PositionError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        if wmin is not None and wmax is not None and wmin > wmax:
            raise HTTPException(status_code=400, detail="wmin must be smaller than wmax.")
        self.target = target
        self.fov, self.mode, self.detector = fov, mode, detector
        self.wmin, self.wmax = wmin, wmax
        self.epoch_a, self.epoch_b = epoch_a, epoch_b


# ----------------------------------------------------------------------
# helpers & caching
# ----------------------------------------------------------------------
def _group_cache_key(p: Params) -> tuple:
    norm_target = (p.target or "").strip().lower() or None
    return (
        round(p.ra, 5),
        round(p.dec, 5),
        round(float(p.fov), 1),
        p.detector,
        norm_target,
        p.wmin,
        p.wmax,
        data.mode(),
    )


@lru_cache(maxsize=32)
def _cached_selected_frames(
    ra5: float,
    dec5: float,
    fov: float,
    detector: int | None,
    target: str | None,
    wmin: float | None,
    wmax: float | None,
    data_mode: str,
) -> tuple[tuple, int, str]:
    try:
        frames = data.get_frames(ra5, dec5, fov)
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    if not frames:
        raise HTTPException(status_code=404, detail="No frames found for this position.")

    if detector is not None:
        det = detector
        det_reason = f"Manual selection: Detector {det}"
    else:
        det, det_reason = recommend_detector(frames, target, ra=ra5, dec=dec5)

    # Colour-flicker rule: with no explicit window, keep only frames within WAVELENGTH_TOLERANCE of
    # each other. Fall back to the whole detector (the UI then warns) if that leaves too few frames.
    # To force the full detector, pass wmin/wmax explicitly (e.g. wmin=0.7&wmax=5.1).
    if wmin is None and wmax is None:
        det_frames = [f for f in frames if f.detector == det]
        win = ss.best_wavelength_window(det_frames, WAVELENGTH_TOLERANCE)
        if win is not None and win["n_selected"] < win["n_total"]:
            wmin, wmax = win["wmin"] - 1e-6, win["wmax"] + 1e-6
            det_reason += (
                f" Wavelength window auto-selected: {win['wmin']:.3f} to {win['wmax']:.3f} um "
                f"({win['n_selected']} of {win['n_total']} frames, {win['n_epochs']} epoch(s))."
            )

    group = ss.group_frames(frames, det, wmin, wmax)
    if not group:
        raise HTTPException(status_code=404, detail="No frames match this detector and wavelength window.")

    aligned = ss.align_frames(group)
    return tuple(aligned), det, det_reason


def selected_frames(p: Params) -> tuple[list, int, str]:
    aligned, det, det_reason = _cached_selected_frames(*_group_cache_key(p))
    return list(aligned), det, det_reason


@lru_cache(maxsize=32)
def _cached_cleaned_frames(
    ra5: float,
    dec5: float,
    fov: float,
    detector: int | None,
    target: str | None,
    wmin: float | None,
    wmax: float | None,
    data_mode: str,
) -> tuple:
    aligned, _, _ = _cached_selected_frames(ra5, dec5, fov, detector, target, wmin, wmax, data_mode)
    return tuple(ss.clean_frame(f, inpaint=True, subtract_local_bg=True) for f in aligned)


def split_epochs(group):
    """Split frames into SPHEREx survey epochs (6-month passes).

    Frames are grouped by observing passes separated by >30 days.
    If only one pass is present, splits the frames in half.
    """
    if len(group) <= 1:
        return [group, group]

    epoch_dict = ss.assign_epochs(group, gap_days=30.0)
    if len(epoch_dict) > 1:
        return [epoch_dict[ep] for ep in sorted(epoch_dict.keys())]

    mid = len(group) // 2
    return [group[:mid] or group, group[mid:] or group]


def stack_group(group, cleaned: list | tuple | None = None):
    if cleaned is None:
        cleaned = [ss.clean_frame(f, inpaint=True, subtract_local_bg=True) for f in group]
    imgs = np.array([f.image for f in cleaned])
    vars_ = np.array([f.variance for f in cleaned])
    masks = np.array([f.mask for f in cleaned])
    mean, var = ss.stack(imgs, vars_, masks)
    nan_mask = ~np.isfinite(mean)
    if nan_mask.any():
        mean = ss.inpaint_bad_pixels(np.nan_to_num(mean, nan=0.0), nan_mask)
        var_med = float(np.nanmedian(var)) if np.isfinite(var).any() else 1.0
        var = np.where(np.isfinite(var) & (var > 0), var, var_med)
    return mean, var


@lru_cache(maxsize=32)
def _cached_stack(
    ra5: float,
    dec5: float,
    fov: float,
    detector: int | None,
    target: str | None,
    wmin: float | None,
    wmax: float | None,
    data_mode: str,
) -> tuple[np.ndarray, np.ndarray]:
    aligned, _, _ = _cached_selected_frames(ra5, dec5, fov, detector, target, wmin, wmax, data_mode)
    cleaned = _cached_cleaned_frames(ra5, dec5, fov, detector, target, wmin, wmax, data_mode)
    return stack_group(list(aligned), cleaned=cleaned)


@lru_cache(maxsize=64)
def _cached_epoch_diff(
    ra5: float,
    dec5: float,
    fov: float,
    detector: int | None,
    target: str | None,
    wmin: float | None,
    wmax: float | None,
    ep_a: int,
    ep_b: int,
    data_mode: str,
) -> dict:
    aligned, _, _ = _cached_selected_frames(ra5, dec5, fov, detector, target, wmin, wmax, data_mode)
    epochs = split_epochs(list(aligned))
    if ep_a >= len(epochs) or ep_b >= len(epochs):
        raise HTTPException(
            status_code=400,
            detail=f"Requested epochs {ep_a},{ep_b} but only {len(epochs)} epoch(s) are available.",
        )
    return ss.epoch_diff_stats(epochs[ep_a], epochs[ep_b])


@lru_cache(maxsize=32)
def _cached_motion_fit(
    ra5: float,
    dec5: float,
    fov: float,
    detector: int | None,
    target: str | None,
    wmin: float | None,
    wmax: float | None,
    data_mode: str,
) -> dict:
    cleaned = _cached_cleaned_frames(ra5, dec5, fov, detector, target, wmin, wmax, data_mode)
    if len(cleaned) < 2:
        raise HTTPException(status_code=400, detail="Need at least 2 frames to measure motion.")
    try:
        return ss.measure_motion(list(cleaned))
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"Motion fit failed: {exc}") from exc


@lru_cache(maxsize=32)
def _cached_spectrum(
    ra5: float,
    dec5: float,
    fov: float,
    detector: int | None,
    target: str | None,
    wmin: float | None,
    wmax: float | None,
    data_mode: str,
) -> dict:
    try:
        frames = (data.all_band_frames(ra5,dec5,fov,detector=detector) if hasattr(data,'all_band_frames')
                  else data.get_frames(ra5,dec5,fov))
    except Exception as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    if not frames:
        raise HTTPException(status_code=404, detail="No frames found for this position.")

    filtered = ss.group_frames(frames, detector=detector, wmin=wmin, wmax=wmax)
    if not filtered:
        filtered = list(frames)

    return ss.extract_spectrum(filtered, ra=ra5, dec=dec5)


def clear_caches() -> None:
    """Clear all in-memory frame, alignment, stack, difference, and motion caches."""
    data.get_frames.cache_clear()
    _cached_selected_frames.cache_clear()
    _cached_cleaned_frames.cache_clear()
    _cached_stack.cache_clear()
    _cached_epoch_diff.cache_clear()
    _cached_motion_fit.cache_clear()
    _cached_spectrum.cache_clear()


def recommend_epoch_pair(epoch_summary: list[dict]) -> list[int]:
    """Select the optimal (earlier, later) epoch pair maximizing time baseline
    while minimizing wavelength mismatch between epochs and preferring multi-frame passes.
    """
    if len(epoch_summary) < 2:
        return [0, 1]

    best_pair = [0, len(epoch_summary) - 1]
    best_score = -1e9
    for i in range(len(epoch_summary)):
        for j in range(i + 1, len(epoch_summary)):
            ep_a, ep_b = epoch_summary[i], epoch_summary[j]
            dt_days = float(ep_b["start_mjd"] - ep_a["start_mjd"])
            dwl = abs(float(ep_b["mean_wavelength"] - ep_a["mean_wavelength"]))
            min_n = min(int(ep_a.get("n_frames", 1)), int(ep_b.get("n_frames", 1)))
            # Reward long baseline (>100 days) and multi-frame depth, penalize wavelength mismatch
            depth_bonus = 90.0 if min_n >= 2 else -120.0
            score = dt_days - 350.0 * dwl + depth_bonus
            if score > best_score:
                best_score = score
                best_pair = [int(ep_a["epoch"]), int(ep_b["epoch"])]
    return best_pair


def resolve_epoch_pair(p: Params, group: list, epochs: list) -> tuple[int, int]:
    """Resolve (epoch_a, epoch_b), defaulting to the recommended epoch pair when not explicitly set."""
    if p.epoch_a is not None and p.epoch_b is not None:
        return p.epoch_a, p.epoch_b
    rec = recommend_epoch_pair(ss.get_epoch_summary(group))
    ep_a = p.epoch_a if p.epoch_a is not None else rec[0]
    ep_b = p.epoch_b if p.epoch_b is not None else (rec[1] if len(rec) > 1 else min(1, len(epochs) - 1))
    return min(ep_a, max(0, len(epochs) - 1)), min(ep_b, max(0, len(epochs) - 1))


def to_png(
    arr: np.ndarray,
    lo: float | None = None,
    hi: float | None = None,
    cmap: str | mcolors.Colormap = "gray",
    smooth: bool = True,
) -> bytes:
    arr = np.asarray(arr, float)
    valid = arr[np.isfinite(arr)]
    if valid.size == 0:
        valid = np.zeros(1)

    if lo is None or hi is None or not np.isfinite(lo) or not np.isfinite(hi) or lo >= hi:
        med = float(np.median(valid))
        mad_sigma = float(1.4826 * np.median(np.abs(valid - med)))
        sigma = max(mad_sigma, float(np.std(valid)) * 0.5, 1e-4)

        # Anchor high-point to the central target region so a bright edge star
        # does not wash out a faint central brown dwarf (like WISE 0855)
        ny, nx = arr.shape
        center_box = arr[
            ny // 4 : max(ny // 4 + 1, 3 * ny // 4),
            nx // 4 : max(nx // 4 + 1, 3 * nx // 4),
        ]
        c_valid = center_box[np.isfinite(center_box)]
        c_peak = (
            float(np.percentile(c_valid, 99.5)) if c_valid.size > 0 else float(np.percentile(valid, 99.5))
        )

        # Suppress background sky noise below +0.6*sigma for a crisp dark-sky background
        lo = med + 0.6 * sigma
        hi = max(c_peak, med + 4.5 * sigma)

    arr_clean = np.nan_to_num(arr, nan=lo)
    spread = max(hi - lo, 1e-12)
    norm = np.clip((arr_clean - lo) / spread, 0.0, 1.0)
    if cmap == "gray":
        norm = np.power(norm, 0.65)

    if isinstance(cmap, mcolors.Colormap):
        cm = cmap
    elif cmap in colormaps:
        cm = colormaps[cmap]
    else:
        cm = colormaps["gray"]

    if smooth and (norm.shape[0] < DISPLAY_SIZE or norm.shape[1] < DISPLAY_SIZE):
        import scipy.ndimage

        zoom_y = DISPLAY_SIZE / norm.shape[0]
        zoom_x = DISPLAY_SIZE / norm.shape[1]
        # Ringing-free Gaussian-regularized bilinear interpolation for natural PSF contours
        norm_smooth = scipy.ndimage.gaussian_filter(norm, sigma=0.35, mode="nearest")
        norm_zoom = scipy.ndimage.zoom(norm_smooth, (zoom_y, zoom_x), order=1)
        norm_zoom = np.clip(norm_zoom, 0.0, 1.0)
        rgba = (cm(norm_zoom) * 255).astype(np.uint8)
        im = Image.fromarray(rgba).convert("RGB")
    else:
        rgba = (cm(norm) * 255).astype(np.uint8)
        im = Image.fromarray(rgba).convert("RGB").resize((DISPLAY_SIZE, DISPLAY_SIZE), Image.NEAREST)

    im = im.transpose(Image.FLIP_TOP_BOTTOM)  # astronomical convention: bottom = south
    buf = io.BytesIO()
    im.save(buf, format="PNG")
    return buf.getvalue()


def group_label(group) -> str:
    dets = sorted({f.detector for f in group})
    w = [f.wavelength for f in group]
    return (
        f"detector {', '.join(map(str, dets))}, {min(w):.2f} to {max(w):.2f} micrometres, {len(group)} frames"
    )


# ----------------------------------------------------------------------
# API
# ----------------------------------------------------------------------
def sky_grid_info(group) -> dict:
    """Describe the rendered grid for correctly scaled frontend overlays."""
    f = group[0]
    ny, nx = f.image.shape
    width = nx * data.PIXEL_SCALE
    height = ny * data.PIXEL_SCALE
    if f.wcs is not None:
        from astropy.wcs.utils import proj_plane_pixel_scales

        scales = np.asarray(proj_plane_pixel_scales(f.wcs.celestial), dtype=float) * 3600.0
        width, height = nx * abs(float(scales[0])), ny * abs(float(scales[1]))
    return {
        "has_wcs": f.wcs is not None,
        "north_up": bool((f.meta or {}).get("north_up_aligned", False)),
        "width_arcsec": width,
        "height_arcsec": height,
    }


@app.get("/api/meta")
def meta(p: Params = Depends()):
    group, det, det_reason = selected_frames(p)
    w = [f.wavelength for f in group]
    spread = max(w) - min(w) if w else 0.0
    warnings = []
    if spread > WAVELENGTH_TOLERANCE:
        warnings.append(
            f"Wavelength spread {spread:.2f} um exceeds tolerance {WAVELENGTH_TOLERANCE} um: "
            "stars may flicker and difference images may show false residuals."
        )

    epochs = split_epochs(group)
    epoch_map = {}
    for ep_idx, ep_frames in enumerate(epochs):
        for f in ep_frames:
            epoch_map[id(f)] = ep_idx

    mjds = [f.mjd for f in group]
    span_days = max(mjds) - min(mjds) if mjds else 0.0

    epoch_summary = ss.get_epoch_summary(group)
    rec_epochs = recommend_epoch_pair(epoch_summary)

    return {
        "ra": p.ra,
        "dec": p.dec,
        "fov": p.fov,
        "mode": p.mode,
        "detector": det,
        "detector_info": det_reason,
        # Mock frames are available only through explicitly selected test mode.
        "mock": all(f.wcs is None and f.uri is None for f in group),
        "label": group_label(group),
        "n_frames": len(group),
        "n_epochs": len(epochs),
        "span_days": round(span_days, 1),
        "epochs": epoch_summary,
        "recommended_epochs": rec_epochs,
        "frame_shape": list(group[0].image.shape) if group else [20, 20],
        "sky_grid": sky_grid_info(group),
        "frames": [
            {
                "index": i,
                "wavelength": round(f.wavelength, 3),
                # Preserve enough timing precision to match the exact motion sample.
                "mjd": round(f.mjd, 6),
                "detector": f.detector,
                "epoch": epoch_map.get(id(f), 0),
            }
            for i, f in enumerate(group)
        ],
        "warnings": warnings,
    }


@app.get("/api/image.png")
def image(
    p: Params = Depends(),
    index: int = Query(0, ge=0),
    clean: bool = Query(True, description="Subtract ZODI and inpaint bad pixels"),
    smooth: bool = Query(True, description="Smooth sub-pixel interpolation"),
    show_mask: bool = Query(False, description="Overlay bad-pixel mask in red"),
):
    key = _group_cache_key(p)
    group, _, _ = selected_frames(p)

    if p.mode == "blink":
        if index >= len(group):
            raise HTTPException(status_code=404, detail="Frame index out of range.")
        f = group[index]
        if show_mask:
            mask_rgb = np.zeros((*f.image.shape, 3), dtype=np.uint8)
            mask_rgb[~f.mask] = [200, 200, 200]
            mask_rgb[f.mask] = [255, 60, 60]
            im = Image.fromarray(mask_rgb).resize((DISPLAY_SIZE, DISPLAY_SIZE), Image.NEAREST)
            im = im.transpose(Image.FLIP_TOP_BOTTOM)
            buf = io.BytesIO()
            im.save(buf, format="PNG")
            return Response(buf.getvalue(), media_type="image/png")

        if clean:
            cleaned = _cached_cleaned_frames(*key)
            img = cleaned[index].image
        else:
            img = f.image
        # Use the legacy ShroudX transfer curve and one stretch for the sequence.
        import processing
        sequence = cleaned if clean else group
        arrays = [np.where(frame.mask, np.nan, frame.image) for frame in sequence]
        stretch = processing.compute_stretch(arrays)
        png = processing.render_png(arrays[index], stretch)
        im = Image.open(io.BytesIO(png))
        im = im.resize((DISPLAY_SIZE, DISPLAY_SIZE), Image.Resampling.BILINEAR if smooth else Image.Resampling.NEAREST)
        output = io.BytesIO()
        im.save(output, format="PNG")
        return Response(output.getvalue(), media_type="image/png")

    epochs = split_epochs(group)
    if p.mode == "stack":
        mean, _ = _cached_stack(*key)
        return Response(to_png(mean, cmap="gray", smooth=smooth), media_type="image/png")

    # diff mode: later minus earlier, shown as a z-score map
    ep_a, ep_b = resolve_epoch_pair(p, group, epochs)
    z = _cached_epoch_diff(*key[:-1], ep_a, ep_b, key[-1])["z"]
    return Response(to_png(z, lo=-8, hi=8, cmap=ASTRO_DIFF_CMAP, smooth=smooth), media_type="image/png")


@app.get("/api/detections")
def detections(p: Params = Depends()):
    """Pixel coordinates and dipole lobes of significant pixels in the difference image."""
    key = _group_cache_key(p)
    group, _, _ = selected_frames(p)
    epochs = split_epochs(group)
    ep_a, ep_b = resolve_epoch_pair(p, group, epochs)
    st = _cached_epoch_diff(*key[:-1], ep_a, ep_b, key[-1])
    z = st["z"]
    hits = ss.detect(z, threshold=Z_THRESHOLD)
    sources = ss.classify_detections(st["diff"], z, st["earlier"], st["later"], threshold=Z_THRESHOLD)
    ys, xs = np.where(hits)

    # Lobes drawn by the UI come ONLY from classified movers (a positive "arrival" lobe and a
    # negative "departure" lobe). A variable star or an artifact is never drawn as a dipole.
    lobes = []
    for src in sources:
        if src["kind"] != "mover":
            continue
        lobes.append(
            {
                "polarity": "negative",
                "x": round(src["x_neg_lobe"], 3),
                "y": round(src["y_neg_lobe"], 3),
                "radius_px": 1.0,
                "peak_z": round(src["peak_z"], 2),
                "n_pixels": src["n_pixels"],
            }
        )
        lobes.append(
            {
                "polarity": "positive",
                "x": round(src["x_pos_lobe"], 3),
                "y": round(src["y_pos_lobe"], 3),
                "radius_px": 1.0,
                "peak_z": round(src["peak_z"], 2),
                "n_pixels": src["n_pixels"],
            }
        )

    return {
        "threshold": Z_THRESHOLD,
        "epoch_a": ep_a,
        "epoch_b": ep_b,
        "count": int(hits.sum()),
        "noise_scale": round(st["noise_scale"], 3),  # empirical / formal noise ratio applied to z
        "summary": ss.summarize_sources(sources),
        "masked_core_pixels": int(st["unreliable"].sum()),
        "sources": sources,
        "lobes": lobes,
        "pixels": [{"x": int(x), "y": int(y), "z": float(z[y, x])} for y, x in zip(ys, xs, strict=True)][
            :500
        ],
    }


@app.get("/api/motion")
def motion(p: Params = Depends()):
    """Measure proper motion across aligned frames and compare to literature catalog."""
    key = _group_cache_key(p)
    group, det, det_reason = selected_frames(p)
    if len(group) < 2:
        raise HTTPException(status_code=400, detail="Need at least 2 frames to measure motion.")

    res = _cached_motion_fit(*key)
    val = ss.validate_motion(res, p.target) if p.target else {}
    epochs = ss.get_epoch_summary(group)

    return {
        "target": p.target,
        "detector": det,
        "detector_info": det_reason,
        "n_frames": res["n_frames"],
        "baseline_years": res["baseline_years"],
        "pm_total": res["pm_total"],
        "pm_total_err": res["pm_total_err"],
        "pm_ra_cosdec": res["pm_ra_cosdec"],
        "pm_dec": res["pm_dec"],
        "position_angle_deg": res["position_angle_deg"],
        "measurements": res["measurements"],
        "validation": val,
        "epochs": epochs,
        "frame_shape": list(group[0].image.shape),
    }


@app.get("/api/spectrum")
def spectrum(p: Params = Depends()):
    """Extract on-the-fly Spectral Energy Distribution (SED) across SPHEREx channels."""
    key = _group_cache_key(p)
    return _cached_spectrum(*key)


@app.get("/api/catalog")
def catalog_overlay(p: Params = Depends()):
    """Cross-match and return Gaia DR3 and AllWISE catalog sources in the field."""
    references = catalogs.get_catalog_sources(p.ra, p.dec, p.fov, p.target)
    group, _, _ = selected_frames(p)
    fwcs = group[0].wcs
    sources = []
    for reference in references:
        # Catalogue references are not detections and are not epoch-propagated here.
        source = {**reference, "x": None, "y": None, "position_kind": "reference"}
        if reference["catalog"] == "Cross-match":
            source["position_kind"] = "illustrative"
        elif fwcs is not None:
            from astropy import units as u
            from astropy.coordinates import SkyCoord

            x, y = fwcs.world_to_pixel(SkyCoord(reference["ra_deg"] * u.deg, reference["dec_deg"] * u.deg))
            if np.isfinite(x) and np.isfinite(y):
                source.update(x=float(x), y=float(y))
        sources.append(source)
    return {
        "target": p.target,
        "ra": p.ra,
        "dec": p.dec,
        "fov": p.fov,
        "count": len(sources),
        "sources": sources,
        "has_sky_projection": fwcs is not None,
        "provenance": (
            "Stored benchmark catalogue references; no live catalogue search or date"
            " propagation is performed."
        ),
    }


@app.get("/api/pixel")
def pixel_inspect(
    p: Params = Depends(),
    x: int = Query(..., ge=0, description="Pixel X coordinate"),
    y: int = Query(..., ge=0, description="Pixel Y coordinate"),
    index: int = Query(0, ge=0, description="Frame index"),
):
    """Interactive pixel quality inspector returning flux, uncertainty, sky coords, and Table 8 flags."""
    group, det, det_reason = selected_frames(p)
    if index >= len(group):
        raise HTTPException(status_code=404, detail="Frame index out of range.")
    f = group[index]
    ny, nx = f.image.shape
    if x >= nx or y >= ny:
        raise HTTPException(status_code=400, detail=f"Pixel ({x}, {y}) out of bounds ({nx}x{ny}).")

    val = float(f.image[y, x]) if np.isfinite(f.image[y, x]) else None
    var = float(f.variance[y, x]) if (np.isfinite(f.variance[y, x]) and f.variance[y, x] > 0) else None
    sig = float(np.sqrt(var)) if var is not None else None
    zodi_layer = getattr(f, "zodi", None)
    zodi_val = float(f.zodi[y, x]) if (zodi_layer is not None and np.isfinite(f.zodi[y, x])) else None
    is_masked = bool(f.mask[y, x])

    ra_deg, dec_deg = p.ra, p.dec
    fwcs = getattr(f, "wcs", None)
    if fwcs is not None:
        try:
            sky = fwcs.pixel_to_world(x, y)
            ra_deg, dec_deg = float(sky.ra.deg), float(sky.dec.deg)
        except Exception:
            pass

    flag_int = 0
    flag_arr = (getattr(f, "meta", None) or {}).get("flag_array")
    if flag_arr is not None and np.shape(flag_arr) == f.image.shape:
        flag_int = int(flag_arr[y, x])
    elif is_masked:
        # Synthetic frames carry no FLAGS layer: report masked pixels as dead, everything else as clean.
        flag_int = 1 << flags.FLAG_BITS["NONFUNC"]

    quality = flags.explain_pixel(flag_int)

    return {
        "x": x,
        "y": y,
        "ra_deg": round(ra_deg, 6),
        "dec_deg": round(dec_deg, 6),
        "has_sky_coordinates": fwcs is not None,
        "flux": round(val, 4) if val is not None else None,
        "variance": round(var, 4) if var is not None else None,
        "sigma": round(sig, 4) if sig is not None else None,
        "zodi": round(zodi_val, 4) if zodi_val is not None else None,
        "is_masked": is_masked,
        "detector": f.detector,
        "wavelength": round(f.wavelength, 4),
        "quality": quality,
    }


app.mount("/static", StaticFiles(directory=STATIC), name="static")
# check_dir=False: React build may be created after server starts (otherwise /assets 404s until restart)
app.mount("/assets", StaticFiles(directory=FRONTEND_DIST / "assets", check_dir=False), name="frontend-assets")


@app.get("/")
def index():
    if (FRONTEND_DIST / "index.html").is_file():
        return FileResponse(FRONTEND_DIST / "index.html")
    return FileResponse(STATIC / "index.html")


@app.get("/legacy", include_in_schema=False)
def legacy_index():
    """Keep the original scientific dashboard available during the React migration."""
    return FileResponse(STATIC / "index.html")


if __name__ == "__main__":
    import socket

    import uvicorn

    try:
        sock = socket.socket(socket.AF_INET6, socket.SOCK_STREAM)
        sock.setsockopt(socket.IPPROTO_IPV6, socket.IPV6_V6ONLY, 0)
        sock.bind(("::", 8000))
        sock.listen(128)
        cfg = uvicorn.Config(app="app:app", log_level="info")
        srv = uvicorn.Server(cfg)
        srv.run(sockets=[sock])
    except Exception:
        uvicorn.run("app:app", host="0.0.0.0", port=8000, reload=True)
