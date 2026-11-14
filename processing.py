"""SPHEREx frame processing: cleaning, grouping, epoch splitting, alignment, stacking, and PNG."""

from __future__ import annotations

import io
from collections.abc import Iterable, Mapping, Sequence
from typing import Any, NamedTuple

import numpy as np
from astropy.time import Time
from astropy.wcs import WCS
from PIL import Image
from reproject import reproject_interp

from frame import Frame

PIXEL_SCALE_ARCSEC: float = 6.15
DEFAULT_MAX_WIDTH_UM: float = 0.10
DEFAULT_EPOCH_GAP_DAYS: float = 30.0

DETECTOR_RANGES_UM: dict[int, tuple[float, float]] = {
    1: (0.75, 1.09),
    2: (1.10, 1.62),
    3: (1.63, 2.41),
    4: (2.42, 3.82),
    5: (3.83, 4.41),
    6: (4.42, 5.00),
}


class GroupResult(tuple):
    """Result of ``group_frames`` behaving as ``(frames, warnings)`` with metadata attributes."""

    frames: tuple[Frame, ...]
    warnings: list[str]
    detector: int
    wavelength_window: tuple[float, float]

    def __new__(
        cls,
        frames: Sequence[Frame],
        warnings: Sequence[str],
        *,
        detector: int,
        wavelength_window: tuple[float, float],
    ) -> GroupResult:
        frames_tuple = tuple(frames)
        warnings_list = list(warnings)
        obj = super().__new__(cls, (frames_tuple, warnings_list))
        obj.frames = frames_tuple
        obj.warnings = warnings_list
        obj.detector = detector
        obj.wavelength_window = (float(wavelength_window[0]), float(wavelength_window[1]))
        return obj


class EpochSummary(dict):
    """Summary dictionary for one observation epoch, matching ``docs/api_contract.md``."""

    def __init__(
        self,
        *,
        epoch: int,
        n_frames: int,
        mjd_start: float,
        mjd_end: float,
        date_start: str,
        date_end: str,
        frames: Sequence[Frame],
        frame_indices: Sequence[int],
    ) -> None:
        super().__init__(
            epoch=int(epoch),
            n_frames=int(n_frames),
            mjd_start=float(mjd_start),
            mjd_end=float(mjd_end),
            date_start=str(date_start),
            date_end=str(date_end),
        )
        self._frames = tuple(frames)
        self._frame_indices = tuple(int(i) for i in frame_indices)

    @property
    def epoch(self) -> int:
        return int(self["epoch"])

    @property
    def n_frames(self) -> int:
        return int(self["n_frames"])

    @property
    def mjd_start(self) -> float:
        return float(self["mjd_start"])

    @property
    def mjd_end(self) -> float:
        return float(self["mjd_end"])

    @property
    def date_start(self) -> str:
        return str(self["date_start"])

    @property
    def date_end(self) -> str:
        return str(self["date_end"])

    @property
    def frames(self) -> tuple[Frame, ...]:
        return self._frames

    @property
    def frame_indices(self) -> tuple[int, ...]:
        return self._frame_indices


class EpochList(list):
    """List of ``EpochSummary`` objects with per-frame epoch assignments."""

    def __init__(
        self,
        summaries: Sequence[EpochSummary],
        *,
        epoch_indices: Sequence[int],
        frames_by_epoch: Sequence[tuple[Frame, ...]],
    ) -> None:
        super().__init__(summaries)
        self.epoch_indices = [int(idx) for idx in epoch_indices]
        self.frames_by_epoch = [tuple(grp) for grp in frames_by_epoch]


class StackResult(NamedTuple):
    """Result of ``stack_frames`` behaving as ``(image, variance, n_frames)``."""

    image: np.ndarray
    variance: np.ndarray
    n_frames: np.ndarray

    @property
    def count(self) -> np.ndarray:
        """Alias for ``n_frames``."""
        return self.n_frames

    @property
    def sigma(self) -> np.ndarray:
        """Return pixel uncertainty ``sqrt(variance)``, with NaN where variance <= 0."""
        valid = np.isfinite(self.variance) & (self.variance > 0)
        result = np.full(self.variance.shape, np.nan, dtype=np.float64)
        np.sqrt(self.variance, out=result, where=valid)
        return result


class StretchParams(dict):
    """Shared display stretch parameters matching ``docs/api_contract.md``."""

    def __init__(self, *, mode: str, vmin: float, vmax: float) -> None:
        super().__init__(mode=str(mode), vmin=float(vmin), vmax=float(vmax))

    @property
    def mode(self) -> str:
        return str(self["mode"])

    @property
    def vmin(self) -> float:
        return float(self["vmin"])

    @property
    def vmax(self) -> float:
        return float(self["vmax"])


def _sigma_clipped_median(
    values: np.ndarray,
    *,
    sigma_clip: float,
    max_iters: int,
) -> float:
    """Compute iterative sigma-clipped median of a 1D array of finite values."""
    if values.size == 0:
        return 0.0
    clipped = values
    med = float(np.median(clipped))
    for _ in range(max_iters):
        if clipped.size < 3:
            break
        med = float(np.median(clipped))
        mad = float(np.median(np.abs(clipped - med)))
        sigma_est = 1.4826 * mad if mad > 0 else float(np.std(clipped))
        if sigma_est <= 0 or not np.isfinite(sigma_est):
            break
        keep = np.abs(clipped - med) <= sigma_clip * sigma_est
        if not np.any(keep) or int(np.count_nonzero(keep)) == clipped.size:
            break
        clipped = clipped[keep]
        med = float(np.median(clipped))
    return med


def _inpaint_bad_pixels(
    image: np.ndarray,
    bad: np.ndarray,
    *,
    inpaint_size: int,
    fallback: float,
) -> np.ndarray:
    """Fill bad pixels using the local median of valid neighbors (for display only)."""
    out = image.copy()
    if not np.any(bad):
        return out
    valid = ~bad
    if not np.any(valid):
        out[bad] = fallback
        return out

    ny, nx = image.shape
    base_radius = max(1, inpaint_size // 2)
    max_radius = max(ny, nx)
    for y, x in np.argwhere(bad):
        radius = base_radius
        filled = False
        while radius <= max_radius:
            y0 = max(0, int(y) - radius)
            y1 = min(ny, int(y) + radius + 1)
            x0 = max(0, int(x) - radius)
            x1 = min(nx, int(x) + radius + 1)
            patch_valid = valid[y0:y1, x0:x1]
            if np.any(patch_valid):
                out[y, x] = float(np.median(image[y0:y1, x0:x1][patch_valid]))
                filled = True
                break
            radius *= 2
        if not filled:
            out[y, x] = fallback
    return out


def clean_frame(
    frame: Frame,
    *,
    sigma_clip: float = 3.0,
    max_iters: int = 5,
    inpaint_size: int = 5,
) -> Frame:
    """Subtract zodiacal light and background, and inpaint masked pixels for display.

    Steps:
    1. Copy arrays so the input ``Frame`` is never mutated.
    2. Subtract ``frame.zodi`` pixel by pixel when present.
    3. Estimate local background via iterative 3-sigma-clipped median of unmasked
       finite pixels and subtract it.
    4. Inpaint masked or non-finite pixels with a local median for display only,
       preserving ``frame.mask`` unchanged.

    Args:
        frame: Input ``Frame`` object.
        sigma_clip: Clipping threshold in sigma for background estimation.
        max_iters: Maximum sigma-clipping iterations.
        inpaint_size: Initial square neighborhood width for display inpainting.

    Returns:
        A new validated ``Frame`` with ``zodi=None`` and original ``mask`` preserved.
    """
    if sigma_clip <= 0 or not np.isfinite(sigma_clip):
        raise ValueError(f"sigma_clip must be positive, got {sigma_clip}")
    if max_iters < 1:
        raise ValueError(f"max_iters must be >= 1, got {max_iters}")
    if inpaint_size < 3:
        raise ValueError(f"inpaint_size must be >= 3, got {inpaint_size}")

    img = np.array(frame.image, dtype=np.float64, copy=True)
    variance = np.array(frame.variance, copy=True)
    mask = np.array(frame.mask, dtype=bool, copy=True)
    unreliable = (
        np.array(frame.unreliable, dtype=bool, copy=True)
        if frame.unreliable is not None
        else None
    )

    if frame.zodi is not None:
        img = img - np.asarray(frame.zodi, dtype=np.float64)

    valid = (~mask) & np.isfinite(img)
    bg = _sigma_clipped_median(img[valid], sigma_clip=sigma_clip, max_iters=max_iters)
    img = np.where(np.isfinite(img), img - bg, np.nan)

    bad = mask | ~np.isfinite(img)
    inpainted = _inpaint_bad_pixels(img, bad, inpaint_size=inpaint_size, fallback=0.0)

    meta = dict(frame.meta) if frame.meta is not None else {}
    meta["zodi_subtracted"] = frame.zodi is not None
    meta["background_level"] = bg

    return frame.copy_with(
        image=inpainted,
        variance=variance,
        mask=mask,
        zodi=None,
        unreliable=unreliable,
        meta=meta,
    )


def _is_usable_frame(frame: Frame) -> bool:
    """Return True if a frame contains at least one unmasked finite pixel."""
    return bool(np.any((~frame.mask) & np.isfinite(frame.image)))


def _count_epochs(sorted_frames: Sequence[Frame], gap_days: float) -> int:
    """Count distinct observation epochs in MJD-sorted frames."""
    if not sorted_frames:
        return 0
    epochs = 1
    prev_mjd = sorted_frames[0].mjd
    for frame in sorted_frames[1:]:
        if frame.mjd - prev_mjd > gap_days:
            epochs += 1
        prev_mjd = frame.mjd
    return epochs


def _pick_best_window(
    det_frames: Sequence[Frame],
    *,
    max_width: float,
    gap_days: float,
) -> tuple[tuple[Frame, ...], float, float]:
    """Choose the narrowest wavelength window maximizing distinct epochs then frame count."""
    wavelengths = sorted({float(f.wavelength) for f in det_frames})
    total_epochs = _count_epochs(det_frames, gap_days)
    tol = 1e-9

    def evaluate_pairs(
        *, enforce_max_width: bool, prioritize_narrow: bool = False
    ) -> tuple[tuple[int, float | int, float | int, float], tuple[Frame, ...], float, float] | None:
        best_score: tuple[int, float | int, float | int, float] | None = None
        best_result: tuple[tuple[Frame, ...], float, float] | None = None
        for i, w_lo in enumerate(wavelengths):
            for w_hi in wavelengths[i:]:
                width = w_hi - w_lo
                if enforce_max_width and width > max_width + tol:
                    break
                subset = tuple(
                    f for f in det_frames if (w_lo - 1e-12) <= f.wavelength <= (w_hi + 1e-12)
                )
                n_epochs = _count_epochs(subset, gap_days)
                n_frames = len(subset)
                rounded_width = round(width, 9)
                if prioritize_narrow:
                    primary = n_epochs if total_epochs >= 2 else int(n_frames >= 2)
                    score: tuple[int, float | int, float | int, float] = (
                        primary,
                        -rounded_width,
                        n_frames,
                        -w_lo,
                    )
                else:
                    score = (n_epochs, n_frames, -rounded_width, -w_lo)
                if best_score is None or score > best_score:
                    best_score = score
                    best_result = (subset, w_lo, w_hi)
        if best_score is None or best_result is None:
            return None
        return best_score, best_result[0], best_result[1], best_result[2]

    narrow = evaluate_pairs(enforce_max_width=True)
    if narrow is None:
        return (), 0.0, 0.0

    narrow_score, narrow_frames, w_lo, w_hi = narrow
    need_widen = (total_epochs >= 2 and narrow_score[0] < 2) or (
        total_epochs == 1 and len(det_frames) >= 2 and narrow_score[1] < 2
    )
    if need_widen:
        wide = evaluate_pairs(enforce_max_width=False, prioritize_narrow=True)
        if wide is not None:
            _, wide_frames, wide_lo, wide_hi = wide
            return wide_frames, wide_lo, wide_hi

    return narrow_frames, w_lo, w_hi


def group_frames(
    frames: Sequence[Frame],
    detector: int | None = None,
    wmin: float | None = None,
    wmax: float | None = None,
    max_width: float = DEFAULT_MAX_WIDTH_UM,
    gap_days: float = DEFAULT_EPOCH_GAP_DAYS,
) -> GroupResult:
    """Select compatible frames from one detector and wavelength window.

    Args:
        frames: Candidate ``Frame`` sequence.
        detector: Target detector (1 to 6). If None, picks the detector with the
            most usable frames (tie-breaking by distinct epochs, total frames,
            then lowest detector number).
        wmin: Optional lower wavelength bound in micrometres.
        wmax: Optional upper wavelength bound in micrometres.
        max_width: Maximum automatic wavelength window width in micrometres.
        gap_days: Epoch separation threshold in days used to maximize epochs.

    Returns:
        ``GroupResult`` containing ``(chosen_frames, warnings)``, plus
        ``.detector`` and ``.wavelength_window`` attributes.
    """
    if not frames:
        raise ValueError("frames must not be empty")
    if (wmin is None) != (wmax is None):
        raise ValueError("wmin and wmax must both be provided or both be None")
    if wmin is not None and wmax is not None:
        wmin = float(wmin)
        wmax = float(wmax)
        if not (np.isfinite(wmin) and np.isfinite(wmax)):
            raise ValueError("wmin and wmax must be finite numbers")
        if wmin > wmax:
            raise ValueError(f"wmin ({wmin}) must be <= wmax ({wmax})")
    if not np.isfinite(max_width) or max_width <= 0:
        raise ValueError(f"max_width must be positive, got {max_width}")
    if not np.isfinite(gap_days) or gap_days <= 0:
        raise ValueError(f"gap_days must be positive, got {gap_days}")

    if detector is not None:
        if (
            isinstance(detector, bool)
            or not isinstance(detector, (int, np.integer))
            or not (1 <= detector <= 6)
        ):
            raise ValueError(f"detector must be an integer between 1 and 6, got {detector}")
        chosen_det = int(detector)
    else:
        best_det_score: tuple[int, int, int, int] | None = None
        chosen_det = 1
        for det in sorted({int(f.detector) for f in frames}):
            det_all = sorted((f for f in frames if f.detector == det), key=lambda f: f.mjd)
            det_usable = [f for f in det_all if _is_usable_frame(f)]
            score = (
                len(det_usable),
                _count_epochs(det_usable, gap_days),
                len(det_all),
                -det,
            )
            if best_det_score is None or score > best_det_score:
                best_det_score = score
                chosen_det = det

    det_all = sorted((f for f in frames if f.detector == chosen_det), key=lambda f: f.mjd)
    det_usable = [f for f in det_all if _is_usable_frame(f)]
    pool = det_usable if det_usable else det_all

    if not pool:
        default_w = DETECTOR_RANGES_UM[chosen_det][0]
        win = (wmin, wmax) if (wmin is not None and wmax is not None) else (default_w, default_w)
        warnings = ["Fewer than 3 frames in the chosen window."]
        if win[1] - win[0] > max_width + 1e-9:
            warnings.append(
                f"Wavelength window width ({win[1] - win[0]:.3f} um) is wider than "
                f"max_width ({max_width:.2f} um); colour flicker may occur."
            )
        return GroupResult((), warnings, detector=chosen_det, wavelength_window=win)

    if wmin is not None and wmax is not None:
        chosen = tuple(f for f in pool if (wmin - 1e-12) <= f.wavelength <= (wmax + 1e-12))
        w_lo, w_hi = wmin, wmax
    else:
        chosen, w_lo, w_hi = _pick_best_window(pool, max_width=max_width, gap_days=gap_days)

    warnings: list[str] = []
    if len(chosen) < 3:
        warnings.append("Fewer than 3 frames in the chosen window.")

    width = w_hi - w_lo
    if width > max_width + 1e-9:
        warnings.append(
            f"Wavelength window width ({width:.3f} um) is wider than "
            f"max_width ({max_width:.2f} um); colour flicker may occur."
        )

    versions = {f.version for f in chosen}
    if len(versions) > 1:
        sorted_versions = sorted(versions, key=lambda v: (-1 if v is None else v))
        warnings.append(f"Mixed processing versions in chosen frames: {sorted_versions}.")

    return GroupResult(chosen, warnings, detector=chosen_det, wavelength_window=(w_lo, w_hi))


def _mjd_to_iso_date(mjd: float) -> str:
    """Convert Modified Julian Date to UTC ISO date string ``YYYY-MM-DD``."""
    return str(Time(mjd, format="mjd", scale="utc").utc.iso[:10])


def split_epochs(
    frames: Sequence[Frame],
    gap_days: float = DEFAULT_EPOCH_GAP_DAYS,
) -> EpochList:
    """Group frames into observation epochs separated by MJD gaps exceeding ``gap_days``.

    Args:
        frames: Sequence of ``Frame`` objects.
        gap_days: Minimum gap in days between consecutive observations that starts
            a new epoch.

    Returns:
        ``EpochList`` containing ``EpochSummary`` dictionaries sorted chronologically,
        with ``.epoch_indices`` and ``.frames_by_epoch`` attributes.
    """
    if not np.isfinite(gap_days) or gap_days <= 0:
        raise ValueError(f"gap_days must be positive, got {gap_days}")
    if not frames:
        return EpochList([], epoch_indices=[], frames_by_epoch=[])

    ordered = sorted(frames, key=lambda f: f.mjd)
    groups: list[list[Frame]] = [[ordered[0]]]
    group_indices: list[list[int]] = [[0]]
    epoch_indices = [0]

    for idx, frame in enumerate(ordered[1:], start=1):
        if frame.mjd - ordered[idx - 1].mjd > gap_days:
            groups.append([])
            group_indices.append([])
        ep = len(groups) - 1
        groups[ep].append(frame)
        group_indices[ep].append(idx)
        epoch_indices.append(ep)

    summaries: list[EpochSummary] = []
    frames_by_epoch: list[tuple[Frame, ...]] = []
    for ep_idx, (ep_frames, ep_pos) in enumerate(zip(groups, group_indices, strict=True)):
        mjd_start = float(ep_frames[0].mjd)
        mjd_end = float(ep_frames[-1].mjd)
        summaries.append(
            EpochSummary(
                epoch=ep_idx,
                n_frames=len(ep_frames),
                mjd_start=mjd_start,
                mjd_end=mjd_end,
                date_start=_mjd_to_iso_date(mjd_start),
                date_end=_mjd_to_iso_date(mjd_end),
                frames=ep_frames,
                frame_indices=ep_pos,
            )
        )
        frames_by_epoch.append(tuple(ep_frames))

    return EpochList(summaries, epoch_indices=epoch_indices, frames_by_epoch=frames_by_epoch)


def _mean_sky_center(frames: Sequence[Frame]) -> tuple[float, float]:
    """Compute spherical mean sky center (RA, Dec in deg) from frame WCS centers."""
    vecs = []
    for frame in frames:
        assert frame.wcs is not None
        ny, nx = frame.image.shape
        ra_deg, dec_deg = frame.wcs.celestial.pixel_to_world_values((nx - 1) / 2.0, (ny - 1) / 2.0)
        ra_rad = np.deg2rad(float(ra_deg))
        dec_rad = np.deg2rad(float(dec_deg))
        vecs.append(
            (
                np.cos(dec_rad) * np.cos(ra_rad),
                np.cos(dec_rad) * np.sin(ra_rad),
                np.sin(dec_rad),
            )
        )
    mean_vec = np.mean(vecs, axis=0)
    norm = float(np.linalg.norm(mean_vec))
    if norm == 0.0:
        ra0, dec0 = frames[0].wcs.celestial.pixel_to_world_values(  # type: ignore[union-attr]
            (frames[0].image.shape[1] - 1) / 2.0,
            (frames[0].image.shape[0] - 1) / 2.0,
        )
        return float(ra0) % 360.0, float(dec0)
    x, y, z = mean_vec / norm
    ra_out = float(np.rad2deg(np.arctan2(y, x))) % 360.0
    dec_out = float(np.rad2deg(np.arctan2(z, np.hypot(x, y))))
    return ra_out, dec_out


def _build_north_up_tan_wcs(
    ra_deg: float,
    dec_deg: float,
    shape_out: tuple[int, int],
    pixel_scale_arcsec: float,
) -> WCS:
    """Build a North-up, East-left ICRS TAN WCS centered on (ra_deg, dec_deg)."""
    ny_out, nx_out = shape_out
    scale_deg = pixel_scale_arcsec / 3600.0
    wcs = WCS(naxis=2)
    wcs.wcs.ctype = ["RA---TAN", "DEC--TAN"]
    wcs.wcs.crval = [float(ra_deg), float(dec_deg)]
    wcs.wcs.crpix = [(nx_out + 1) / 2.0, (ny_out + 1) / 2.0]
    wcs.wcs.cdelt = [-scale_deg, scale_deg]
    wcs.wcs.cunit = ["deg", "deg"]
    wcs.wcs.radesys = "ICRS"
    return wcs


def _meta_sky_center(frame: Frame) -> tuple[float, float] | None:
    """Return finite (ra, dec) from frame.meta if present and numeric, else None."""
    if frame.meta is None or "ra" not in frame.meta or "dec" not in frame.meta:
        return None
    try:
        ra = float(frame.meta["ra"])
        dec = float(frame.meta["dec"])
    except (TypeError, ValueError):
        return None
    if not (np.isfinite(ra) and np.isfinite(dec)):
        return None
    return ra, dec


def align_frames(
    frames: Sequence[Frame],
    target_ra: float | None = None,
    target_dec: float | None = None,
    fov_arcsec: float | None = None,
    pixel_scale_arcsec: float = PIXEL_SCALE_ARCSEC,
) -> tuple[Frame, ...]:
    """Reproject frames onto a common North-up, East-left celestial TAN grid.

    Variance propagation note:
        ``variance`` is reprojected with ``reproject_interp(..., order='bilinear')``
        directly (sum w_i * V_i) rather than weighting by squared interpolation
        weights (sum w_i^2 * V_i). For a half-pixel shift, this overestimates
        per-pixel sigma by up to 2x (variance by up to 4x) and introduces positive
        covariance between adjacent pixels, while avoiding underestimation of
        integrated noise in multi-pixel apertures when covariance is neglected.

    Args:
        frames: Non-empty sequence of ``Frame`` objects with valid WCS.
        target_ra: Optional target RA in decimal degrees (ICRS).
        target_dec: Optional target Dec in decimal degrees (ICRS).
        fov_arcsec: Optional square field of view in arcseconds; if None, uses
            the maximum input frame dimensions.
        pixel_scale_arcsec: Output pixel scale in arcseconds per pixel.

    Returns:
        Tuple of reprojected ``Frame`` objects sharing the common TAN WCS.
    """
    if not frames:
        raise ValueError("frames must not be empty")
    if (target_ra is None) != (target_dec is None):
        raise ValueError("target_ra and target_dec must both be provided or both be None")
    if not np.isfinite(pixel_scale_arcsec) or pixel_scale_arcsec <= 0:
        raise ValueError(f"pixel_scale_arcsec must be positive, got {pixel_scale_arcsec}")
    if fov_arcsec is not None and (not np.isfinite(fov_arcsec) or fov_arcsec <= 0):
        raise ValueError(f"fov_arcsec must be positive, got {fov_arcsec}")

    for idx, frame in enumerate(frames):
        if frame.wcs is None:
            raise ValueError(f"Frame at index {idx} has no WCS; cannot align")

    if target_ra is not None and target_dec is not None:
        ra0 = float(target_ra)
        dec0 = float(target_dec)
    elif (meta_center := _meta_sky_center(frames[0])) is not None:
        ra0, dec0 = meta_center
    else:
        ra0, dec0 = _mean_sky_center(frames)

    if fov_arcsec is not None:
        n_pix = max(1, int(np.round(fov_arcsec / pixel_scale_arcsec)))
        shape_out = (n_pix, n_pix)
    else:
        ny_out = max(int(f.image.shape[0]) for f in frames)
        nx_out = max(int(f.image.shape[1]) for f in frames)
        shape_out = (ny_out, nx_out)

    target_wcs = _build_north_up_tan_wcs(ra0, dec0, shape_out, pixel_scale_arcsec)

    aligned: list[Frame] = []
    for frame in frames:
        in_wcs = frame.wcs.celestial  # type: ignore[union-attr]
        img_out, footprint = reproject_interp(
            (np.asarray(frame.image, dtype=np.float64), in_wcs),
            target_wcs,
            shape_out=shape_out,
            order="bilinear",
            return_footprint=True,
        )
        var_out = reproject_interp(
            (np.asarray(frame.variance, dtype=np.float64), in_wcs),
            target_wcs,
            shape_out=shape_out,
            order="bilinear",
            return_footprint=False,
        )
        mask_interp = reproject_interp(
            (frame.mask.astype(np.float64), in_wcs),
            target_wcs,
            shape_out=shape_out,
            order="bilinear",
            return_footprint=False,
        )
        mask_out = (
            (footprint <= 0)
            | (~np.isfinite(img_out))
            | (~np.isfinite(var_out))
            | (~np.isfinite(mask_interp))
            | (mask_interp > 1e-6)
        )

        unreliable_out: np.ndarray | None = None
        if frame.unreliable is not None:
            unrel_interp = reproject_interp(
                (frame.unreliable.astype(np.float64), in_wcs),
                target_wcs,
                shape_out=shape_out,
                order="bilinear",
                return_footprint=False,
            )
            unreliable_out = np.isfinite(unrel_interp) & (unrel_interp > 1e-6)

        zodi_out: np.ndarray | None = None
        if frame.zodi is not None:
            zodi_out = reproject_interp(
                (np.asarray(frame.zodi, dtype=np.float64), in_wcs),
                target_wcs,
                shape_out=shape_out,
                order="bilinear",
                return_footprint=False,
            )

        meta = dict(frame.meta) if frame.meta is not None else None
        aligned.append(
            frame.copy_with(
                image=img_out,
                variance=var_out,
                mask=mask_out,
                wcs=target_wcs.deepcopy(),
                zodi=zodi_out,
                unreliable=unreliable_out,
                meta=meta,
            )
        )

    return tuple(aligned)


def stack_frames(
    frames: Sequence[Frame],
    sigma_clip: float = 3.0,
    max_iters: int = 3,
) -> StackResult:
    """Compute an inverse-variance weighted coadd with per-pixel MAD sigma clipping.

    Note:
        Per-pixel MAD clipping across all frames rejects sources present at a
        pixel in only a minority of exposures. Because a fast-moving object
        occupies different pixels in different epochs, stacking all frames
        together can suppress or erase the mover; use per-epoch stacks, blink,
        or difference imaging to inspect moving targets.

    Args:
        frames: Non-empty sequence of aligned ``Frame`` objects sharing shape.
        sigma_clip: Rejection threshold in units of MAD-derived / propagated sigma.
        max_iters: Maximum iterative clipping passes.

    Returns:
        ``StackResult`` ``(image, variance, n_frames)`` where ``image`` and
        ``variance`` are NaN at pixels with zero contributing frames.
    """
    if not frames:
        raise ValueError("frames must not be empty")
    if sigma_clip <= 0 or not np.isfinite(sigma_clip):
        raise ValueError(f"sigma_clip must be positive, got {sigma_clip}")
    if max_iters < 1:
        raise ValueError(f"max_iters must be >= 1, got {max_iters}")

    shape = frames[0].image.shape
    for idx, frame in enumerate(frames[1:], start=1):
        if frame.image.shape != shape:
            raise ValueError(
                f"Frame {idx} shape {frame.image.shape} does not match frame 0 shape {shape}"
            )

    images = np.stack([np.asarray(f.image, dtype=np.float64) for f in frames], axis=0)
    variances = np.stack([np.asarray(f.variance, dtype=np.float64) for f in frames], axis=0)
    masks = np.stack([np.asarray(f.mask, dtype=bool) for f in frames], axis=0)

    valid = (~masks) & np.isfinite(images) & np.isfinite(variances) & (variances > 0)
    safe_sigma = np.where(valid, np.sqrt(np.where(valid, variances, 1.0)), 0.0)

    for _ in range(max_iters):
        eligible = np.count_nonzero(valid, axis=0) >= 3
        if not np.any(eligible):
            break

        sub_valid = valid[:, eligible]
        sub_imgs = np.where(sub_valid, images[:, eligible], np.nan)
        med = np.nanmedian(sub_imgs, axis=0)
        mad = np.nanmedian(np.abs(sub_imgs - med[None, :]), axis=0)
        mad_sigma = 1.4826 * mad

        sub_sigma = safe_sigma[:, eligible]
        scale = np.maximum(mad_sigma[None, :], sub_sigma)
        scale_valid = np.where(scale > 0, scale, np.inf)

        kept = sub_valid & (np.abs(sub_imgs - med[None, :]) <= sigma_clip * scale_valid)
        # Ensure at least one valid sample remains where data existed before this pass.
        empty_after = ~np.any(kept, axis=0)
        if np.any(empty_after):
            kept[:, empty_after] = sub_valid[:, empty_after]

        if np.array_equal(kept, sub_valid):
            break
        valid[:, eligible] = kept

    n_contrib = np.count_nonzero(valid, axis=0).astype(np.int32)
    weights = np.where(valid, 1.0 / np.where(valid, variances, 1.0), 0.0)
    weight_sum = np.sum(weights, axis=0)
    has_data = (n_contrib > 0) & (weight_sum > 0)

    ny, nx = shape
    stack_img = np.full((ny, nx), np.nan, dtype=np.float64)
    stack_var = np.full((ny, nx), np.nan, dtype=np.float64)
    weighted_sum = np.sum(weights * np.where(valid, images, 0.0), axis=0)
    stack_img[has_data] = weighted_sum[has_data] / weight_sum[has_data]
    stack_var[has_data] = 1.0 / weight_sum[has_data]

    return StackResult(stack_img, stack_var, n_contrib)


def compute_stretch(
    frames_or_images: Frame | np.ndarray | Iterable[Frame | np.ndarray],
    *,
    mode: str = "asinh",
    pmin: float = 1.0,
    pmax: float = 99.5,
) -> StretchParams:
    """Compute one shared percentile stretch across a frame or image sequence.

    Args:
        frames_or_images: A ``Frame``, a 2D numpy array, or an iterable of them.
        mode: Stretch transfer curve, either ``"asinh"`` or ``"linear"``.
        pmin: Lower percentile in ``[0, 100)``.
        pmax: Upper percentile in ``(pmin, 100]``.

    Returns:
        ``StretchParams`` dict with keys ``"mode"``, ``"vmin"``, and ``"vmax"``.
    """
    if mode not in ("asinh", "linear"):
        raise ValueError(f"stretch mode must be 'asinh' or 'linear', got {mode!r}")
    if not (0.0 <= pmin < pmax <= 100.0):
        raise ValueError(f"Require 0 <= pmin < pmax <= 100, got pmin={pmin}, pmax={pmax}")

    items = (
        [frames_or_images]
        if isinstance(frames_or_images, (Frame, np.ndarray))
        else list(frames_or_images)
    )

    chunks: list[np.ndarray] = []
    for item in items:
        if isinstance(item, Frame):
            arr = np.asarray(item.image, dtype=np.float64)
            unmasked = arr[(~item.mask) & np.isfinite(arr)]
            vals = unmasked if unmasked.size > 0 else arr[np.isfinite(arr)]
        else:
            arr = np.asarray(item, dtype=np.float64)
            vals = arr[np.isfinite(arr)]
        if vals.size > 0:
            chunks.append(vals)

    if not chunks:
        return StretchParams(mode=mode, vmin=0.0, vmax=1.0)

    all_vals = np.concatenate(chunks)
    vmin, vmax = np.percentile(all_vals, [pmin, pmax])
    return StretchParams(mode=mode, vmin=float(vmin), vmax=float(vmax))


def render_png(
    image: np.ndarray | Frame,
    stretch: StretchParams | Mapping[str, Any] | tuple[float, float] | None = None,
    *,
    mode: str = "asinh",
    pmin: float = 1.0,
    pmax: float = 99.5,
    asinh_a: float = 0.1,
) -> bytes:
    """Render a 2D image or ``Frame`` to North-up, East-left grayscale PNG bytes.

    Args:
        image: 2D array or ``Frame`` to render.
        stretch: Precomputed stretch mapping (``{"vmin": ..., "vmax": ..., "mode": ...}``)
            or ``(vmin, vmax)`` tuple. When None, computed from ``image`` directly.
        mode: Default transfer function (``"asinh"`` or ``"linear"``) if not set in ``stretch``.
        pmin: Lower percentile used when ``stretch`` is None.
        pmax: Upper percentile used when ``stretch`` is None.
        asinh_a: Softening scale parameter for ``asinh`` stretch (must be > 0).

    Returns:
        PNG-encoded image bytes.
    """
    arr = np.asarray(image.image if isinstance(image, Frame) else image, dtype=np.float64)
    if arr.ndim != 2:
        raise ValueError(f"image must be 2D, got ndim={arr.ndim}")
    if asinh_a <= 0 or not np.isfinite(asinh_a):
        raise ValueError(f"asinh_a must be positive, got {asinh_a}")

    if stretch is None:
        resolved = compute_stretch(image, mode=mode, pmin=pmin, pmax=pmax)
        stretch_mode = resolved.mode
        vmin = resolved.vmin
        vmax = resolved.vmax
    elif isinstance(stretch, Mapping):
        vmin = float(stretch["vmin"])
        vmax = float(stretch["vmax"])
        stretch_mode = str(stretch.get("mode", mode))
    elif isinstance(stretch, (tuple, list)) and len(stretch) == 2:
        vmin = float(stretch[0])
        vmax = float(stretch[1])
        stretch_mode = mode
    else:
        raise TypeError("stretch must be a StretchParams/mapping, (vmin, vmax) tuple, or None")

    if stretch_mode not in ("asinh", "linear"):
        raise ValueError(f"stretch mode must be 'asinh' or 'linear', got {stretch_mode!r}")

    finite = np.isfinite(arr)
    if not np.isfinite(vmin) or not np.isfinite(vmax) or vmax <= vmin:
        norm = np.zeros_like(arr, dtype=np.float64)
    else:
        clipped = np.clip(np.where(finite, arr, vmin), vmin, vmax)
        norm = (clipped - vmin) / (vmax - vmin)
        if stretch_mode == "asinh":
            norm = np.arcsinh(norm / asinh_a) / np.arcsinh(1.0 / asinh_a)

    norm = np.where(finite, norm, 0.0)
    u8 = np.clip(np.round(norm * 255.0), 0, 255).astype(np.uint8)
    # Flip vertically so FITS row y=ny-1 (North) is at the top of the PNG image.
    u8 = np.flipud(u8)

    buf = io.BytesIO()
    Image.fromarray(u8, mode="L").save(buf, format="PNG")
    return buf.getvalue()
