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
import io
import logging
import math
import os
import re
import threading
import time
from collections import OrderedDict
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, TypeVar

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
import processing

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
    if mode not in ("blink", "stack"):
        message = (
            "Difference imaging is not available in the core API yet. Use blink or stack."
            if mode == "diff"
            else "Mode must be blink or stack."
        )
        raise bad_input(message, "mode")
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
    )


def create_app(
    *,
    mode: str | None = None,
    cache_root: str | Path | None = None,
    request_timeout: float | None = None,
    irsa_concurrency: int | None = None,
    irsa_queue_timeout: float | None = None,
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
        if exc.code == "archive_unreachable" and "IRSA" not in msg:
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
        selection, _ = parse_selection(request)
        product, hit = products(selection)
        body = copy.deepcopy(product.metadata)
        if hit and body["data_source"] == "archive":
            body["data_source"] = "cache"
        return json_response(body)

    @application.get("/api/image.png")
    def image_endpoint(request: Request):
        selection, view = parse_selection(request)
        index = integer(request, "index") if view == "blink" else None
        if view == "blink" and (index is None or index < 0):
            raise bad_input("Blink mode requires a non-negative frame index.", "index")
        product, hit = products(selection)
        if view == "blink" and index >= len(product.blink):
            raise bad_input("Frame index is outside the selected sequence.", "index")
        content = product.blink[index] if view == "blink" else product.stack
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

    @application.get("/", include_in_schema=False)
    def index():
        return FileResponse(ROOT / "static" / "index.html", media_type="text/html")

    application.mount("/static", StaticFiles(directory=ROOT / "static"), name="static")
    return application


app = create_app()
