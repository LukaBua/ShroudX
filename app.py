# AI-assisted: implemented with Codex and AI pair-programming assistant.
"""Core ShroudX API. Run with ``python -m uvicorn app:app``.

All blocking work runs in FastAPI's worker threads (synchronous endpoints).
Per-selection in-flight locks coalesce duplicate requests while allowing cached
fields and health checks to proceed concurrently without waiting on slow IRSA
queries. Routes that call IRSA enforce a bounded concurrency limit and a
per-request timeout.
"""

from __future__ import annotations

import copy
import hashlib
import io
import json
import logging
import math
import os
import re
import threading
import tempfile
import time
from collections import OrderedDict
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, TypeVar
from urllib.parse import urlencode

import numpy as np
from astropy.time import Time
from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import FileResponse, JSONResponse, Response
from PIL import Image, ImageDraw
from starlette.exceptions import HTTPException
from starlette.staticfiles import StaticFiles

import coords
import data
import gaia
import processing
import motion
from frame import Frame
from scripts.validate_motion import load_benchmarks, evaluate_criterion, investigate_systematics

ROOT = Path(__file__).resolve().parent
LOG = logging.getLogger(__name__)
T = TypeVar("T")

GIF_MAX_BYTES = 10_000_000
DEFAULT_REQUEST_TIMEOUT_SEC: float = 30.0
DEFAULT_IRSA_CONCURRENCY: int = 2
DEFAULT_IRSA_QUEUE_TIMEOUT_SEC: float = 2.0


def render_blink_gif(product: Products, speed: int) -> bytes:
    """Caption the cached PNGs without recomputing their authoritative stretch.

    GIF stores delays in 10 ms units; round requested delays to the nearest
    10 ms. Ordinal captions keep identical observations as separate GIF frames.
    Reduce the image area until the encoded file is strictly below 10 MB.
    """
    images = []
    for png in product.blink:
        with Image.open(io.BytesIO(png)) as image:
            images.append(image.convert("L"))
    if not images:
        raise APIError(422, "insufficient_data", "No frames available for GIF export.")
    width, height = images[0].size
    palette = [channel for value in range(256) for channel in (value, value, value)]
    while True:
        animation = []
        for i, (image, frame) in enumerate(zip(images, product.metadata["frames"], strict=True)):
            canvas = Image.new("L", (max(360, width), height + 30), 0)
            canvas.paste(
                image.resize((width, height), Image.Resampling.NEAREST),
                ((canvas.width - width) // 2, 0),
            )
            draw = ImageDraw.Draw(canvas)
            draw.text(
                (4, height + 2),
                f"{i + 1}/{len(images)} | {frame['date']} | {frame['wavelength_um']:.4f} um",
                fill=255,
            )
            label = "SYNTHETIC DATA | " if product.metadata["synthetic"] else ""
            draw.text((4, height + 16), label + "ShroudX / SPHEREx", fill=255)
            indexed = canvas.convert("P")
            indexed.putpalette(palette)
            animation.append(indexed)
        output = io.BytesIO()
        animation[0].save(
            output,
            format="GIF",
            save_all=True,
            append_images=animation[1:],
            loop=0,
            duration=10 * ((speed + 5) // 10),
            disposal=2,
            optimize=False,
        )
        result = output.getvalue()
        if len(result) < GIF_MAX_BYTES:
            return result
        if width == 1 and height == 1:
            raise APIError(
                422,
                "insufficient_data",
                "Too many frames to export a GIF below 10 MB; narrow the wavelength window.",
            )
        width, height = max(1, int(width * 0.7)), max(1, int(height * 0.7))


class APIError(Exception):
    """An expected failure with a safe, contract-shaped public response."""

    def __init__(self, status: int, code: str, message: str, **detail: Any):
        super().__init__(message)
        self.status, self.code, self.message, self.detail = status, code, message, detail


def bad_input(message: str, parameter: str) -> APIError:
    return APIError(400, "bad_input", message, parameter=parameter)


@dataclass(frozen=True)
class Position:
    target: str | None
    ra: str | None
    dec: str | None


@dataclass(frozen=True)
class Selection:
    position: Position
    fov: float | None
    detector: int | None
    wmin: float | None
    wmax: float | None


@dataclass
class Products:
    metadata: dict[str, Any]
    blink: tuple[bytes, ...]
    stack: bytes
    aligned: tuple[Frame, ...] = ()


@dataclass
class _InFlightBuild:
    """Tracks an in-progress product build for a single Selection key."""

    event: threading.Event = field(default_factory=threading.Event)
    result: Products | None = None
    error: BaseException | None = None


class ProductCache:
    """Bounded, expiring LRU with per-key in-flight coalescing."""

    def __init__(self, max_entries: int = 8, ttl: float = 300, max_bytes: int = 64 * 1024**2):
        self.max_entries, self.ttl, self.max_bytes = max_entries, ttl, max_bytes
        self.entries: OrderedDict[Any, tuple[float, Products, int]] = OrderedDict()
        self.inflight: dict[Any, _InFlightBuild] = {}
        self.lock = threading.RLock()

    def get(
        self,
        key: Any,
        build: Callable[[], Products],
        *,
        wait_timeout: float = DEFAULT_REQUEST_TIMEOUT_SEC,
    ) -> tuple[Products, bool]:
        with self.lock:
            now = time.monotonic()
            for old_key, (deadline, _, _) in list(self.entries.items()):
                if deadline <= now:
                    del self.entries[old_key]
            if key in self.entries:
                self.entries.move_to_end(key)
                return self.entries[key][1], True
            inflight = self.inflight.get(key)
            if inflight is None:
                inflight = _InFlightBuild()
                self.inflight[key] = inflight
                is_leader = True
            else:
                is_leader = False

        if not is_leader:
            if not inflight.event.wait(timeout=wait_timeout):
                raise data.ArchiveUnreachableError(
                    f"IRSA archive request timed out after {wait_timeout:.1f}s. "
                    "Please retry or choose a cached demo target."
                )
            if inflight.error is not None:
                raise inflight.error
            assert inflight.result is not None
            return inflight.result, True

        try:
            result = build()
        except BaseException as exc:
            with self.lock:
                inflight.error = exc
                self.inflight.pop(key, None)
                inflight.event.set()
            raise

        with self.lock:
            size = sum(map(len, result.blink)) + len(result.stack)
            size += sum(a.nbytes for f in result.aligned
                        for a in (f.image, f.variance, f.mask, f.unreliable)
                        if a is not None)
            if size <= self.max_bytes:
                self.entries[key] = (time.monotonic() + self.ttl, result, size)
                while (
                    len(self.entries) > self.max_entries
                    or sum(entry[2] for entry in self.entries.values()) > self.max_bytes
                ):
                    self.entries.popitem(last=False)
            inflight.result = result
            self.inflight.pop(key, None)
            inflight.event.set()
        return result, False


def _run_with_timeout(fn: Callable[[], T], timeout: float) -> T:
    """Execute ``fn`` with a wall-clock timeout, raising ``ArchiveUnreachableError`` if exceeded."""
    outcome: dict[str, Any] = {}
    done = threading.Event()

    def _target() -> None:
        try:
            outcome["result"] = fn()
        except BaseException as exc:
            outcome["error"] = exc
        finally:
            done.set()

    worker = threading.Thread(target=_target, daemon=True)
    worker.start()
    if not done.wait(timeout=timeout):
        raise data.ArchiveUnreachableError(
            f"IRSA archive request timed out after {timeout:.1f}s. "
            "Please retry or choose a cached demo target."
        )
    if "error" in outcome:
        raise outcome["error"]
    return outcome["result"]


def _will_query_irsa(
    data_mode: str,
    cache_root: Path,
    ra: float,
    dec: float,
    fov: float,
) -> bool:
    """Return True if fetching ``(ra, dec, fov)`` will contact the live IRSA archive."""
    if data_mode == "live":
        return True
    if data_mode != "auto":
        return False
    try:
        return data._find_cached_bucket(cache_root, ra, dec, fov) is None
    except Exception:
        return True


def query_value(request: Request, key: str) -> str | None:
    values = request.query_params.getlist(key)
    if len(values) > 1:
        raise bad_input(f"Provide {key} only once.", key)
    if not values:
        return None
    value = values[0].strip()
    if not value:
        raise bad_input(f"{key} must not be empty.", key)
    return value


def parse_position(request: Request) -> Position:
    target, ra, dec = (query_value(request, name) for name in ("target", "ra", "dec"))
    if target is not None:
        if ra is not None or dec is not None:
            raise bad_input("Use a target name or both RA and Dec, not both.", "target")
    elif ra is None or dec is None:
        raise bad_input("Provide a target name or both RA and Dec.", "target")
    return Position(target, ra, dec)


def number(request: Request, key: str) -> float | None:
    value = query_value(request, key)
    if value is None:
        return None
    try:
        result = float(value)
    except ValueError as exc:
        raise bad_input(f"{key} must be a finite number.", key) from exc
    if not math.isfinite(result):
        raise bad_input(f"{key} must be a finite number.", key)
    return result


def integer(request: Request, key: str) -> int | None:
    value = query_value(request, key)
    if value is None:
        return None
    if not re.fullmatch(r"[+-]?\d+", value):
        raise bad_input(f"{key} must be an integer.", key)
    return int(value)


def parse_selection(request: Request) -> tuple[Selection, str]:
    position = parse_position(request)
    fov, wmin, wmax = (number(request, key) for key in ("fov", "wmin", "wmax"))
    if fov is not None and not 30 <= fov <= 600:
        raise bad_input("Field of view must be between 30 and 600 arcseconds.", "fov")
    detector = integer(request, "detector")
    if detector is not None and not 1 <= detector <= 6:
        raise bad_input("Detector must be an integer from 1 to 6.", "detector")
    if (wmin is None) != (wmax is None):
        raise bad_input("Provide both wavelength limits or neither.", "wmin")
    if wmin is not None and (wmin <= 0 or wmax <= 0 or wmin > wmax):
        raise bad_input("Wavelength limits must be positive and minimum <= maximum.", "wmin")
    mode = query_value(request, "mode") or "blink"
    if mode not in ("blink", "stack", "diff"):
        raise bad_input("Mode must be blink, stack or diff.", "mode")
    return Selection(position, fov, detector, wmin, wmax), mode


def resolve(position: Position) -> dict[str, Any]:
    """Adapt the coordinate module without inventing missing catalogue values."""
    if position.target is None:
        try:
            ra, dec = coords.parse_position(position.ra, position.dec)
        except (ValueError, TypeError) as exc:
            raise bad_input(str(exc), "ra/dec") from exc
        return dict(
            ra=ra,
            dec=dec,
            source="coordinates",
            pm_known=False,
            warnings=[],
            suggested_fov_arcsec=None,
            suggested_detector=None,
        )
    try:
        result = coords.resolve_name(position.target)
    except ValueError as exc:
        raise APIError(404, "unknown_target", str(exc)) from exc
    source = {"offline": "offline_table", "sesame": "simbad"}.get(result.source, result.source)
    if source not in ("offline_table", "simbad"):
        raise RuntimeError("Unknown coordinate provenance")
    profile = coords.lookup_offline_target(position.target)
    suggestion = coords.TARGET_PROFILES.get(profile.target_class) if profile else None
    fast = bool(
        profile
        and profile.pmra is not None
        and profile.pmdec is not None
        and math.hypot(profile.pmra, profile.pmdec) > coords.HIGH_PM_THRESHOLD_MAS_YR
    )
    return dict(
        ra=result.ra,
        dec=result.dec,
        source=source,
        pm_known=result.pm_known,
        warnings=list(result.warnings),
        suggested_fov_arcsec=300 if fast else None,
        suggested_detector=suggestion,
    )


def build_products(
    selection: Selection,
    data_mode: str,
    cache_root: Path,
    request_timeout: float = DEFAULT_REQUEST_TIMEOUT_SEC,
    irsa_semaphore: threading.BoundedSemaphore | None = None,
    irsa_queue_timeout: float = DEFAULT_IRSA_QUEUE_TIMEOUT_SEC,
) -> Products:
    resolved = resolve(selection.position)
    fov = selection.fov if selection.fov is not None else (resolved["suggested_fov_arcsec"] or 120)
    needs_irsa = _will_query_irsa(data_mode, cache_root, resolved["ra"], resolved["dec"], fov)

    def _fetch() -> tuple[Any, ...]:
        return data.get_frames(
            resolved["ra"],
            resolved["dec"],
            fov,
            mode=data_mode,
            cache_root=cache_root,
        )

    if needs_irsa and irsa_semaphore is not None:
        acquired = irsa_semaphore.acquire(timeout=irsa_queue_timeout)
        if not acquired:
            raise data.ArchiveUnreachableError(
                "IRSA archive concurrency limit reached while other archive queries are running. "
                "Please retry shortly or select a cached demo target."
            )
        try:
            frames = _run_with_timeout(_fetch, timeout=request_timeout)
        finally:
            irsa_semaphore.release()
    else:
        frames = _run_with_timeout(_fetch, timeout=request_timeout)

    if not frames:
        raise APIError(404, "no_coverage", "No SPHEREx coverage at this position.")
    is_synthetic = data_mode == "synthetic"
    for frame in frames:
        provenance = frame.meta or {}
        flagged = (
            provenance.get("synthetic", False)
            or provenance.get("data_source") == "synthetic"
            or (frame.uri or "").startswith("synthetic://")
        )
        if flagged and not is_synthetic:
            raise RuntimeError("Synthetic frames returned outside synthetic mode")
    grouped = processing.group_frames(
        frames,
        detector=selection.detector,
        wmin=selection.wmin,
        wmax=selection.wmax,
    )
    if not grouped.frames:
        raise APIError(
            422,
            "insufficient_data",
            "No usable frames in this detector/wavelength window.",
        )
    aligned = processing.align_frames(
        tuple(processing.clean_frame(f) for f in grouped.frames),
        target_ra=resolved["ra"],
        target_dec=resolved["dec"],
        fov_arcsec=fov,
    )
    if not any(
        np.any(~f.mask & np.isfinite(f.image) & np.isfinite(f.variance) & (f.variance > 0))
        for f in aligned
    ):
        raise APIError(422, "insufficient_data", "No usable pixels remain in this field.")
    epochs = processing.split_epochs(aligned)
    stretch = processing.compute_stretch(aligned)
    stack = processing.stack_frames(aligned)
    warnings = list(resolved["warnings"]) + list(grouped.warnings)
    failed = max(int((f.meta or {}).get("failed_downloads", 0)) for f in frames)
    if failed:
        warnings.append(f"{failed} cutout downloads failed; this sequence may be incomplete.")
    if is_synthetic:
        warnings.append("SYNTHETIC DATA: simulated observations, not real SPHEREx measurements.")
    if len(epochs) < 2:
        warnings.append("Only one observation epoch is available; motion is not established.")
    if resolved["suggested_fov_arcsec"] and fov < resolved["suggested_fov_arcsec"]:
        warnings.append(
            "This field may be too small for a fast-moving target; consider 300 arcseconds."
        )
    warnings.extend(
        [
            (
                "Reprojected pixel uncertainties are approximate and "
                "neighbouring pixels are correlated."
            ),
            (
                "Sigma-clipped stacking across epochs can suppress moving objects; "
                "inspect the blink sequence."
            ),
        ]
    )
    sources = {(f.meta or {}).get("data_source") for f in frames}
    source = "synthetic" if is_synthetic else "archive" if "archive" in sources else "cache"
    metadata = {
        "synthetic": is_synthetic,
        "data_source": source,
        "target": {
            "name": selection.position.target,
            **{k: resolved[k] for k in ("ra", "dec", "source", "pm_known")},
        },
        "fov_arcsec": fov,
        "pixel_scale_arcsec": processing.PIXEL_SCALE_ARCSEC,
        "image_shape": list(aligned[0].image.shape),
        "detector": grouped.detector,
        "wavelength_window_um": list(grouped.wavelength_window),
        "detector_ranges_um": processing.DETECTOR_RANGES_UM,
        "n_frames": len(aligned),
        "frames": [
            dict(
                index=i,
                mjd=float(f.mjd),
                date=str(Time(f.mjd, format="mjd", scale="utc").iso[:10]),
                wavelength_um=float(f.wavelength),
                epoch=epochs.epoch_indices[i],
                version=int(f.version) if f.version is not None else None,
            )
            for i, f in enumerate(aligned)
        ],
        "epochs": [dict(ep) for ep in epochs],
        "stretch": dict(stretch),
        "warnings": list(dict.fromkeys(warnings)),
    }
    return Products(
        metadata,
        tuple(processing.render_png(f, stretch) for f in aligned),
        processing.render_png(stack.image, stretch),
        tuple(aligned),
    )


def difference_request(request: Request) -> tuple[int | None, int | None]:
    """Validate explicit epoch syntax before any archive work."""
    pair = integer(request, "epoch_a"), integer(request, "epoch_b")
    for key, value in zip(("epoch_a", "epoch_b"), pair):
        if value is not None and value < 0:
            raise bad_input("Epoch identifiers must be non-negative integers.", key)
    return pair


def difference_product(product: Products, pair: tuple[int | None, int | None]):
    """Validate against the sequence and delegate scientific work to processing."""
    epochs = product.metadata["epochs"]
    if len(epochs) < 2:
        raise APIError(422, "insufficient_data", "At least two epochs are required for difference imaging.")
    a = epochs[0]["epoch"] if pair[0] is None else pair[0]
    b = epochs[-1]["epoch"] if pair[1] is None else pair[1]
    valid = {ep["epoch"] for ep in epochs}
    if a not in valid or b not in valid or a == b:
        raise bad_input("Choose two distinct epoch identifiers from /api/meta.", "epoch_a/epoch_b")
    return processing.difference_images(product.aligned, epoch_a=a, epoch_b=b)


def difference_limit(diff: np.ndarray) -> float:
    """Match the renderer's default symmetric percentile and supply it explicitly."""
    values = np.abs(diff[np.isfinite(diff)])
    if not values.size:
        raise APIError(422, "insufficient_data", "The selected epochs have no overlapping usable pixels.")
    limit = float(np.percentile(values, processing.DEFAULT_DIFF_PERCENTILE))
    return limit if limit > 0 else float(np.max(values)) or 1.0


def motion_result(product: Products, selection: Selection, benchmarks_path: Path) -> dict[str, Any]:
    """Adapt motion results; verdict criteria remain in validate_motion.py.

    No catalogue positions are inferred from proximity. Match names/known aliases
    only, and read all literature values from the cited benchmark CSV. Epoch
    error circles use max(sigma_RA*, sigma_Dec), not a 2-D confidence contour.
    """
    groups = processing.split_epochs(product.aligned)
    if len(groups) < 2:
        raise APIError(422, "insufficient_data", "At least two epochs are required to measure motion.")
    target = product.metadata["target"]
    try:
        fit = motion.measure_and_fit_epochs(groups.frames_by_epoch, target["ra"], target["dec"])
    except ValueError as exc:
        raise APIError(422, "insufficient_data", str(exc)) from exc
    warnings = list(product.metadata["warnings"]) + list(fit.warnings)
    if fit.reduced_chi2 > 1 and not fit.uncertainties_inflated:
        raise RuntimeError("Motion module returned unscaled errors for reduced chi-squared > 1")
    if fit.uncertainties_inflated:
        warnings.append("Fit uncertainties were inflated because reduced chi-squared exceeds 1.")
    if not math.isfinite(fit.reduced_chi2):
        warnings.append("Reduced chi-squared is undefined: no residual degrees of freedom.")
    benchmark = dict(literature_pm_arcsec_per_yr=None, literature_parallax_arcsec=None,
                     verdict=None, citation=None)
    def canonical(name: str) -> str:
        return coords._OFFLINE_ALIASES.get(name.strip().lower(), name.strip()).casefold()
    match = None
    if selection.position.target:
        if benchmarks_path.exists():
            match = next((row for row in load_benchmarks(benchmarks_path)
                          if canonical(row.name) == canonical(selection.position.target)), None)
        else:
            warnings.append("Benchmark CSV unavailable; no literature comparison was performed.")
    if match is not None:
        if not match.citation.strip():
            raise RuntimeError("Benchmark literature values require a citation")
        benchmark.update(literature_pm_arcsec_per_yr=match.pm_total_arcsec_yr,
                         literature_parallax_arcsec=match.parallax_arcsec, citation=match.citation)
        if not product.metadata["synthetic"]:
            verdict, _ = evaluate_criterion(match, fit)
            benchmark["verdict"] = verdict
            if verdict == "DISAGREE":
                warnings.append(investigate_systematics(match, fit, product.aligned, verdict))
                warnings.append("Catalogue disagreement is unresolved; check WCS, chromatic centroids, "
                                "binary photocentre, parallax and saturated/masked pixels. No tuning was applied.")
        else:
            warnings.append("Synthetic motion is not compared with literature; benchmark verdict is unset.")
    epochs = []
    height, width = product.metadata["image_shape"]
    for ep in fit.epochs:
        matches = [g for g in groups if g["mjd_start"] - 1e-8 <= ep.mjd <= g["mjd_end"] + 1e-8]
        if len(matches) != 1:
            raise RuntimeError("Motion centroid cannot be associated with an observation epoch")
        x, y = product.aligned[0].wcs.celestial.world_to_pixel_values(ep.ra, ep.dec)
        sigma = max(float(ep.sigma_ra_arcsec), float(ep.sigma_dec_arcsec))
        epochs.append(dict(epoch=matches[0]["epoch"], mjd=float(ep.mjd), ra=float(ep.ra),
                           dec=float(ep.dec), sigma_arcsec=sigma, n_frames=int(ep.n_frames_used),
                           x=float(x) + .5, y=height - .5 - float(y),
                           radius_px=sigma / product.metadata["pixel_scale_arcsec"]))
    if len(epochs) < 2:
        raise APIError(422, "insufficient_data", "Too few accepted epoch centroids to measure motion.")
    return dict(epochs=epochs, fit=dict(
        pm_arcsec_per_yr=float(fit.pm_total_arcsec_yr), pm_sigma=float(fit.sigma_pm_total_arcsec_yr),
        position_angle_deg=float(fit.pa_deg), pa_sigma=float(fit.sigma_pa_deg),
        parallax_arcsec=fit.parallax_arcsec, parallax_sigma=fit.sigma_parallax_arcsec,
        chi2_reduced=float(fit.reduced_chi2) if math.isfinite(fit.reduced_chi2) else None,
        n_epochs=int(fit.n_epochs), significance=float(fit.motion_significance),
        uncertainty_scaled=bool(fit.uncertainties_inflated)), benchmark=benchmark,
        warnings=list(dict.fromkeys(warnings)), synthetic=product.metadata["synthetic"],
        image_shape=[height, width])


def save_motion_result(body: dict[str, Any], product: Products, selection: Selection,
                       mode: str, results_root: Path, benchmarks_path: Path) -> None:
    """Atomically save computed results and a rerunnable HTTP command with provenance."""
    query = {"target": selection.position.target} if selection.position.target else {
        "ra": selection.position.ra, "dec": selection.position.dec}
    query.update(fov=product.metadata["fov_arcsec"], detector=product.metadata["detector"],
                 wmin=product.metadata["wavelength_window_um"][0],
                 wmax=product.metadata["wavelength_window_um"][1])
    route = "/api/motion?" + urlencode(query)
    provenance = dict(data_mode=mode, request=route,
                      frames=[dict(uri=f.uri, mjd=f.mjd, wavelength_um=f.wavelength, version=f.version)
                              for f in product.aligned],
                      benchmarks_sha256=hashlib.sha256(benchmarks_path.read_bytes()).hexdigest()
                      if benchmarks_path.exists() else None)
    key = hashlib.sha256(json.dumps(provenance, sort_keys=True).encode()).hexdigest()[:20]
    filename = f"motion-{key}.json"
    provenance["command"] = f'curl.exe --fail "http://127.0.0.1:8000{route}" --output "results/{filename}"'
    provenance["instructions"] = "Run the server in the recorded data mode with the same data/cache and code."
    body["provenance"] = provenance
    results_root.mkdir(parents=True, exist_ok=True)
    path = None
    try:
        with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", dir=results_root,
                                         suffix=".tmp", delete=False) as handle:
            path = Path(handle.name)
            json.dump(body, handle, allow_nan=False, indent=2)
        os.replace(path, results_root / filename)
    finally:
        if path is not None:
            path.unlink(missing_ok=True)


def create_app(
    *,
    mode: str | None = None,
    cache_root: str | Path | None = None,
    request_timeout: float | None = None,
    irsa_concurrency: int | None = None,
    irsa_queue_timeout: float | None = None,
    results_root: str | Path | None = None,
    benchmarks_path: str | Path | None = None,
) -> FastAPI:
    """Create an isolated application, freezing data mode and concurrency policy at startup."""
    application = FastAPI(title="ShroudX", docs_url=None, redoc_url=None, openapi_url=None)
    data_mode = data.get_data_mode(mode)
    root = Path(cache_root or os.environ.get("SPHX_CACHE_DIR", str(ROOT / "cache"))).resolve()
    timeout_sec = float(
        request_timeout
        if request_timeout is not None
        else os.environ.get("SPHX_REQUEST_TIMEOUT", str(DEFAULT_REQUEST_TIMEOUT_SEC))
    )
    max_irsa = int(
        irsa_concurrency
        if irsa_concurrency is not None
        else os.environ.get("SPHX_IRSA_CONCURRENCY", str(DEFAULT_IRSA_CONCURRENCY))
    )
    queue_timeout_sec = float(
        irsa_queue_timeout
        if irsa_queue_timeout is not None
        else os.environ.get(
            "SPHX_IRSA_QUEUE_TIMEOUT",
            str(min(timeout_sec, DEFAULT_IRSA_QUEUE_TIMEOUT_SEC)),
        )
    )
    if timeout_sec <= 0:
        raise ValueError(f"request_timeout must be positive, got {timeout_sec}")
    if max_irsa < 1:
        raise ValueError(f"irsa_concurrency must be >= 1, got {max_irsa}")

    cache = ProductCache()
    irsa_semaphore = threading.BoundedSemaphore(max_irsa)
    application.state.products = cache
    application.state.data_mode = data_mode
    application.state.cache_root = root
    motion_results = Path(results_root) if results_root is not None else ROOT / "results"
    benchmark_file = Path(benchmarks_path) if benchmarks_path is not None else ROOT / "data" / "benchmarks.csv"
    application.state.request_timeout = timeout_sec
    application.state.irsa_concurrency = max_irsa
    application.state.irsa_semaphore = irsa_semaphore

    def json_response(body: dict, status: int = 200) -> JSONResponse:
        if data_mode == "synthetic":
            body = {**body, "synthetic": True}
        return JSONResponse(body, status_code=status, headers={"Cache-Control": "no-store"})

    def error_response(error: APIError) -> JSONResponse:
        return json_response(
            {"error": {"code": error.code, "message": error.message, "detail": error.detail}},
            error.status,
        )

    @application.exception_handler(APIError)
    async def api_error_handler(request: Request, exc: APIError):
        return error_response(exc)

    @application.exception_handler(data.DataError)
    async def data_error_handler(request: Request, exc: data.DataError):
        mapping = {"no_coverage": 404, "not_cached": 404, "archive_unreachable": 502}
        status = mapping.get(exc.code, 500)
        if status == 500:
            return error_response(
                APIError(500, "internal_error", "Unable to process the observations.")
            )
        msg = str(exc).strip()
        if exc.code == "archive_unreachable" and "IRSA" not in msg and "Gaia" not in msg:
            msg = (
                f"IRSA archive is unreachable: {msg}"
                if msg
                else (
                    "IRSA archive is currently unreachable. "
                    "Please retry or select a cached demo target."
                )
            )
        return error_response(APIError(status, exc.code, msg))

    @application.exception_handler(RequestValidationError)
    async def validation_error_handler(request: Request, exc: RequestValidationError):
        return error_response(bad_input("Malformed request parameters.", "query"))

    @application.exception_handler(HTTPException)
    async def http_error_handler(request: Request, exc: HTTPException):
        return error_response(APIError(exc.status_code, "bad_input", str(exc.detail)))

    @application.exception_handler(Exception)
    async def unexpected_error_handler(request: Request, exc: Exception):
        LOG.error("Unhandled ShroudX request error", exc_info=exc)
        return error_response(
            APIError(500, "internal_error", "Unable to process the observations.")
        )

    def products(selection: Selection) -> tuple[Products, bool]:
        # The app has a fixed mode/cache root. Selection includes every scientific
        # input; view/index do not alter the sequence or its shared display stretch.
        return cache.get(
            selection,
            lambda: build_products(
                selection,
                data_mode,
                root,
                timeout_sec,
                irsa_semaphore,
                queue_timeout_sec,
            ),
            wait_timeout=timeout_sec,
        )

    @application.get("/health")
    def health():
        try:
            size = sum(p.stat().st_size for p in root.rglob("*") if p.is_file()) / 1_000_000
        except OSError:
            size = None
        return json_response(dict(status="ok", data_mode=data_mode, cache_size_mb=size))

    @application.get("/api/resolve")
    def resolve_endpoint(request: Request):
        result = resolve(parse_position(request))
        if data_mode == "synthetic":
            result["warnings"].append("Server is in SYNTHETIC DATA mode; images are simulated.")
        return json_response(result)

    @application.get("/api/meta")
    def metadata_endpoint(request: Request):
        selection, view = parse_selection(request)
        pair = difference_request(request) if view == "diff" else None
        product, hit = products(selection)
        body = copy.deepcopy(product.metadata)
        if view == "diff":
            diff = difference_product(product, pair)
            difference_limit(diff.diff)
            body["warnings"] = list(dict.fromkeys(body["warnings"] + list(diff.warnings)))
        if hit and body["data_source"] == "archive":
            body["data_source"] = "cache"
        return json_response(body)

    @application.get("/api/motion")
    def motion_endpoint(request: Request):
        selection, _ = parse_selection(request)
        product, _ = products(selection)
        body = motion_result(product, selection, benchmark_file)
        save_motion_result(body, product, selection, data_mode, motion_results, benchmark_file)
        return json_response(body)

    @application.get("/api/image.png")
    def image_endpoint(request: Request):
        selection, view = parse_selection(request)
        pair = difference_request(request) if view == "diff" else None
        index = integer(request, "index") if view == "blink" else None
        if view == "blink" and (index is None or index < 0):
            raise bad_input("Blink mode requires a non-negative frame index.", "index")
        product, hit = products(selection)
        if view == "blink" and index >= len(product.blink):
            raise bad_input("Frame index is outside the selected sequence.", "index")
        content = product.blink[index] if view == "blink" else product.stack
        extra_headers = {}
        if view == "diff":
            diff = difference_product(product, pair)
            limit = difference_limit(diff.diff)
            content = processing.render_diff_png(diff.diff, limit=limit)
            with Image.open(io.BytesIO(content)) as image:
                if list(reversed(image.size)) != product.metadata["image_shape"]:
                    raise RuntimeError("Difference renderer returned an unexpected image size")
            extra_headers["X-Diff-Limit"] = str(limit)
        source = product.metadata["data_source"]
        if hit and source == "archive":
            source = "cache"
        return Response(
            content,
            media_type="image/png",
            headers={
                "Cache-Control": "no-store",
                "X-Data-Source": source,
                "X-Synthetic": str(data_mode == "synthetic").lower(),
                **extra_headers,
            },
        )

    @application.get("/api/blink.gif")
    def blink_gif_endpoint(request: Request):
        selection, _ = parse_selection(request)
        speed = integer(request, "speed")
        speed = 500 if speed is None else speed
        if not 50 <= speed <= 2000:
            raise bad_input("GIF speed must be between 50 and 2000 milliseconds.", "speed")
        product, hit = products(selection)
        content = render_blink_gif(product, speed)
        source = product.metadata["data_source"]
        if hit and source == "archive":
            source = "cache"
        return Response(
            content,
            media_type="image/gif",
            headers={
                "Cache-Control": "no-store",
                "X-Gif-Size-Bytes": str(len(content)),
                "X-Data-Source": source,
                "X-Synthetic": str(data_mode == "synthetic").lower(),
                "Content-Disposition": 'attachment; filename="shroudx-blink.gif"',
            },
        )

    @application.get("/api/catalog")
    def catalog_endpoint(request: Request):
        selection, _ = parse_selection(request)
        resolved = resolve(selection.position)
        fov = (
            selection.fov
            if selection.fov is not None
            else (resolved["suggested_fov_arcsec"] or 120)
        )
        body = gaia.build_catalog(
            resolved["ra"],
            resolved["dec"],
            fov / 2.0,
            epoch=coords.DEFAULT_SURVEY_EPOCH,
            cache_root=root,
            mode=data_mode,
            timeout=timeout_sec,
            base_warnings=resolved["warnings"],
        )
        return json_response(body)

    @application.get("/", include_in_schema=False)
    def index():
        return FileResponse(ROOT / "static" / "index.html", media_type="text/html")

    application.mount("/static", StaticFiles(directory=ROOT / "static"), name="static")
    return application


app = create_app()
