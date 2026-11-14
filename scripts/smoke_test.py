# AI-assisted: written with AI pair-programming assistant.
"""Smoke-test script checking /health, /api/meta, /api/image.png, and /api/motion on a URL.

Pass / Fail Criteria (fixed before running):
1. GET /health: HTTP 200, JSON body with ``status == "ok"``,
   ``data_mode in {"auto", "cache", "live", "synthetic"}``, and ``cache_size_mb``
   either null or a finite number in ``(0, 50.0]`` MB.
2. GET /api/meta: HTTP 200 for the target query, JSON body with ``n_frames >= 3``,
   ``len(epochs) >= 2``, ``1 <= detector <= 6``, ``wavelength_window_um`` width
   ``<= 0.10`` um, recorded integer ``version`` on frames, and ``synthetic == False``
   whenever ``data_mode != "synthetic"``.
3. GET /api/image.png: HTTP 200 for both ``mode=blink&index=0`` and ``mode=stack``,
   ``Content-Type`` starting with ``image/png``, decodable PNG dimensions matching
   ``image_shape`` from ``/api/meta``, and header ``X-Synthetic == "false"`` when
   ``data_mode != "synthetic"``.
4. GET /api/motion: HTTP 200 for the target query, JSON body containing ``epochs``
   (list with ``len >= 2`` and finite ``sigma_arcsec > 0``), ``fit`` (finite
   ``pm_arcsec_per_yr`` and ``pm_sigma > 0``), ``benchmark``, and ``warnings``.
   If ``/api/motion`` returns 404 or any non-200 status, it is reported as ``FAIL``
   (never hidden).
5. Overall verdict is ``PASS`` only if all checked endpoints pass; otherwise ``FAIL``.
"""

from __future__ import annotations

import argparse
import io
import json
import sys
import time
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen

import numpy as np
from PIL import Image

MAX_CACHE_SIZE_MB: float = 50.0


@dataclass(frozen=True)
class HTTPResponseData:
    """Normalized HTTP response returned by the smoke-test transport."""

    status_code: int
    headers: Mapping[str, str]
    body: bytes

    def json(self) -> Any:
        return json.loads(self.body.decode("utf-8"))


RequesterFn = Callable[[str, float], HTTPResponseData]


def default_requester(url: str, timeout: float = 20.0) -> HTTPResponseData:
    """Perform an HTTP GET request using ``urllib.request``."""
    req = Request(url, headers={"User-Agent": "ShroudX-SmokeTest/0.1"})
    try:
        with urlopen(req, timeout=timeout) as resp:  # noqa: S310
            headers = {k.lower(): v for k, v in resp.headers.items()}
            return HTTPResponseData(
                status_code=int(resp.status),
                headers=headers,
                body=resp.read(),
            )
    except HTTPError as exc:
        headers = {k.lower(): v for k, v in (exc.headers.items() if exc.headers else [])}
        body = exc.read() if hasattr(exc, "read") else b""
        return HTTPResponseData(status_code=int(exc.code), headers=headers, body=body)
    except URLError as exc:
        raise RuntimeError(f"Network error contacting {url}: {exc}") from exc


def _measure_request(
    url: str,
    *,
    timeout: float,
    repeats: int,
    requester: RequesterFn,
) -> tuple[HTTPResponseData | None, float, float, str | None]:
    """Run ``repeats`` requests to ``url`` and return last response + latency (mean, sigma ms)."""
    samples_ms: list[float] = []
    last_resp: HTTPResponseData | None = None
    last_err: str | None = None

    for _ in range(max(1, repeats)):
        t0 = time.perf_counter()
        try:
            last_resp = requester(url, timeout)
            elapsed_ms = (time.perf_counter() - t0) * 1000.0
            samples_ms.append(elapsed_ms)
        except Exception as exc:
            elapsed_ms = (time.perf_counter() - t0) * 1000.0
            samples_ms.append(elapsed_ms)
            last_err = str(exc)

    mean_ms = float(np.mean(samples_ms)) if samples_ms else 0.0
    sigma_ms = float(np.std(samples_ms, ddof=1)) if len(samples_ms) > 1 else 0.0
    return last_resp, round(mean_ms, 2), round(sigma_ms, 2), last_err


def run_smoke_test(
    base_url: str,
    *,
    target: str = "WISE 0855-0714",
    timeout: float = 20.0,
    repeats: int = 3,
    requester: RequesterFn = default_requester,
) -> dict[str, Any]:
    """Run smoke checks on ``/health``, ``/api/meta``, ``/api/image.png``, and ``/api/motion``."""
    clean_base = base_url.rstrip("/")
    checks: dict[str, dict[str, Any]] = {}
    data_versions: set[str] = set()
    data_mode_seen: str | None = None
    expected_shape: tuple[int, int] | None = None

    # 1. /health
    health_url = f"{clean_base}/health"
    resp, mean_ms, sigma_ms, err = _measure_request(
        health_url, timeout=timeout, repeats=repeats, requester=requester
    )
    health_reasons: list[str] = []
    if err is not None or resp is None:
        health_reasons.append(err or "No response")
    elif resp.status_code != 200:
        health_reasons.append(f"Expected HTTP 200, got {resp.status_code}")
    else:
        try:
            payload = resp.json()
            if payload.get("status") != "ok":
                health_reasons.append(f"status={payload.get('status')!r} != 'ok'")
            data_mode_seen = payload.get("data_mode")
            if data_mode_seen not in {"auto", "cache", "live", "synthetic"}:
                health_reasons.append(f"Invalid data_mode={data_mode_seen!r}")
            cache_mb = payload.get("cache_size_mb")
            if cache_mb is not None:
                if not isinstance(cache_mb, (int, float)) or not np.isfinite(cache_mb):
                    health_reasons.append(f"Non-finite cache_size_mb={cache_mb!r}")
                elif float(cache_mb) > MAX_CACHE_SIZE_MB:
                    health_reasons.append(
                        f"cache_size_mb={float(cache_mb):.2f} exceeds {MAX_CACHE_SIZE_MB:.1f} MB"
                    )
        except Exception as exc:
            health_reasons.append(f"Invalid JSON on /health: {exc}")

    checks["/health"] = {
        "url": health_url,
        "status_code": resp.status_code if resp is not None else None,
        "verdict": "PASS" if not health_reasons else "FAIL",
        "latency_ms_mean": mean_ms,
        "latency_ms_sigma": sigma_ms,
        "reasons": health_reasons,
    }

    # 2. /api/meta
    meta_query = urlencode({"target": target})
    meta_url = f"{clean_base}/api/meta?{meta_query}"
    resp, mean_ms, sigma_ms, err = _measure_request(
        meta_url, timeout=timeout, repeats=repeats, requester=requester
    )
    meta_reasons: list[str] = []
    if err is not None or resp is None:
        meta_reasons.append(err or "No response")
    elif resp.status_code != 200:
        meta_reasons.append(f"Expected HTTP 200, got {resp.status_code}")
    else:
        try:
            meta = resp.json()
            if data_mode_seen != "synthetic" and meta.get("synthetic") is True:
                meta_reasons.append("Unexpected synthetic=True outside synthetic mode")
            n_frames = int(meta.get("n_frames", 0))
            frames = meta.get("frames", [])
            epochs = meta.get("epochs", [])
            if n_frames < 3 or len(frames) != n_frames:
                meta_reasons.append(f"Invalid frame count n_frames={n_frames}, len={len(frames)}")
            if len(epochs) < 2:
                meta_reasons.append(f"Expected >= 2 epochs, got {len(epochs)}")
            det = meta.get("detector")
            if not isinstance(det, int) or not (1 <= det <= 6):
                meta_reasons.append(f"Invalid detector={det!r}")
            w_win = meta.get("wavelength_window_um", [0.0, 1.0])
            if (
                not isinstance(w_win, list)
                or len(w_win) != 2
                or (float(w_win[1]) - float(w_win[0])) > 0.10 + 1e-6
            ):
                meta_reasons.append(f"Invalid wavelength_window_um={w_win!r}")
            shape = meta.get("image_shape")
            if isinstance(shape, list) and len(shape) == 2:
                expected_shape = (int(shape[0]), int(shape[1]))
            else:
                meta_reasons.append(f"Invalid image_shape={shape!r}")
            for f in frames:
                ver = f.get("version")
                if ver is not None:
                    data_versions.add(f"l2b-v{int(ver)}")
                else:
                    meta_reasons.append("Frame missing processing version")
        except Exception as exc:
            meta_reasons.append(f"Invalid JSON on /api/meta: {exc}")

    checks["/api/meta"] = {
        "url": meta_url,
        "status_code": resp.status_code if resp is not None else None,
        "verdict": "PASS" if not meta_reasons else "FAIL",
        "latency_ms_mean": mean_ms,
        "latency_ms_sigma": sigma_ms,
        "reasons": meta_reasons,
    }

    # 3. /api/image.png (blink index 0 and stack)
    img_reasons: list[str] = []
    img_latencies_mean: list[float] = []
    img_latencies_sigma: list[float] = []
    last_img_status: int | None = None
    for params_dict in (
        {"target": target, "mode": "blink", "index": "0"},
        {"target": target, "mode": "stack"},
    ):
        img_url = f"{clean_base}/api/image.png?{urlencode(params_dict)}"
        resp, mean_ms, sigma_ms, err = _measure_request(
            img_url, timeout=timeout, repeats=repeats, requester=requester
        )
        img_latencies_mean.append(mean_ms)
        img_latencies_sigma.append(sigma_ms)
        if err is not None or resp is None:
            img_reasons.append(f"{params_dict['mode']}: {err or 'No response'}")
            continue
        last_img_status = resp.status_code
        if resp.status_code != 200:
            img_reasons.append(f"{params_dict['mode']}: Expected HTTP 200, got {resp.status_code}")
            continue
        ctype = resp.headers.get("content-type", "")
        if not ctype.startswith("image/png"):
            img_reasons.append(f"{params_dict['mode']}: Expected image/png, got {ctype!r}")
            continue
        if data_mode_seen != "synthetic" and resp.headers.get("x-synthetic") == "true":
            img_reasons.append(f"{params_dict['mode']}: Unexpected X-Synthetic=true header")
        try:
            with Image.open(io.BytesIO(resp.body)) as im:
                width, height = im.size
                if expected_shape is not None and (height, width) != expected_shape:
                    img_reasons.append(
                        f"{params_dict['mode']}: PNG shape {(height, width)} != {expected_shape}"
                    )
        except Exception as exc:
            img_reasons.append(f"{params_dict['mode']}: Invalid PNG bytes ({exc})")

    blink_q = urlencode({"target": target, "mode": "blink", "index": "0"})
    checks["/api/image.png"] = {
        "url": f"{clean_base}/api/image.png?{blink_q}",
        "status_code": last_img_status,
        "verdict": "PASS" if not img_reasons else "FAIL",
        "latency_ms_mean": round(float(np.mean(img_latencies_mean)), 2),
        "latency_ms_sigma": round(float(np.mean(img_latencies_sigma)), 2),
        "reasons": img_reasons,
    }

    # 4. /api/motion
    motion_url = f"{clean_base}/api/motion?{meta_query}"
    resp, mean_ms, sigma_ms, err = _measure_request(
        motion_url, timeout=timeout, repeats=repeats, requester=requester
    )
    motion_reasons: list[str] = []
    if err is not None or resp is None:
        motion_reasons.append(err or "No response")
    elif resp.status_code != 200:
        motion_reasons.append(f"Expected HTTP 200 on /api/motion, got {resp.status_code}")
    else:
        try:
            motion = resp.json()
            epochs_list = motion.get("epochs")
            fit = motion.get("fit")
            benchmark = motion.get("benchmark")
            warnings_list = motion.get("warnings")
            if not isinstance(epochs_list, list) or len(epochs_list) < 2:
                motion_reasons.append("epochs must be a list with at least 2 entries")
            else:
                for ep in epochs_list:
                    sigma_arcsec = ep.get("sigma_arcsec")
                    if not isinstance(sigma_arcsec, (int, float)) or float(sigma_arcsec) <= 0:
                        motion_reasons.append(f"Invalid epoch sigma_arcsec={sigma_arcsec!r}")
            if not isinstance(fit, dict):
                motion_reasons.append("fit must be a JSON object")
            else:
                pm = fit.get("pm_arcsec_per_yr")
                pm_sig = fit.get("pm_sigma")
                if not isinstance(pm, (int, float)) or not np.isfinite(pm):
                    motion_reasons.append(f"Non-finite fit.pm_arcsec_per_yr={pm!r}")
                if not isinstance(pm_sig, (int, float)) or float(pm_sig) <= 0:
                    motion_reasons.append(f"Invalid fit.pm_sigma={pm_sig!r}")
            if not isinstance(benchmark, dict):
                motion_reasons.append("benchmark must be a JSON object")
            if not isinstance(warnings_list, list):
                motion_reasons.append("warnings must be a list")
        except Exception as exc:
            motion_reasons.append(f"Invalid JSON on /api/motion: {exc}")

    checks["/api/motion"] = {
        "url": motion_url,
        "status_code": resp.status_code if resp is not None else None,
        "verdict": "PASS" if not motion_reasons else "FAIL",
        "latency_ms_mean": mean_ms,
        "latency_ms_sigma": sigma_ms,
        "reasons": motion_reasons,
    }

    overall_verdict = (
        "PASS"
        if all(item["verdict"] == "PASS" for item in checks.values())
        else "FAIL"
    )
    return {
        "verdict": overall_verdict,
        "base_url": clean_base,
        "target": target,
        "repeats": repeats,
        "data_mode": data_mode_seen,
        "data_versions": sorted(data_versions),
        "checks": checks,
    }


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Smoke-test a deployed ShroudX instance "
            "(/health, /api/meta, /api/image.png, /api/motion)."
        )
    )
    parser.add_argument(
        "--url",
        type=str,
        default="http://127.0.0.1:8000",
        help="Base URL of the deployed ShroudX service (default: http://127.0.0.1:8000).",
    )
    parser.add_argument(
        "--target",
        type=str,
        default="WISE 0855-0714",
        help="Target name to query during smoke tests (default: 'WISE 0855-0714').",
    )
    parser.add_argument(
        "--timeout",
        type=float,
        default=20.0,
        help="Per-request timeout in seconds (default: 20.0).",
    )
    parser.add_argument(
        "--repeats",
        type=int,
        default=3,
        help="Number of repeated probes per endpoint to estimate latency uncertainty (default: 3).",
    )
    parser.add_argument(
        "--results-dir",
        type=Path,
        default=Path("results"),
        help="Directory where smoke_test.json is written (default: results).",
    )
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    cmd_args = list(argv) if argv is not None else sys.argv[1:]
    command_str = "python scripts/smoke_test.py" + (
        " " + " ".join(cmd_args) if cmd_args else ""
    )

    report = run_smoke_test(
        args.url,
        target=args.target,
        timeout=args.timeout,
        repeats=args.repeats,
    )
    report["command"] = command_str

    results_dir = Path(args.results_dir)
    results_dir.mkdir(parents=True, exist_ok=True)
    out_path = results_dir / "smoke_test.json"
    out_path.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")

    print(
        f"Smoke test against {report['base_url']} "
        f"(target={report['target']!r}): {report['verdict']}"
    )
    for endpoint, info in report["checks"].items():
        status = info["status_code"]
        lat = f"{info['latency_ms_mean']:.1f} +/- {info['latency_ms_sigma']:.1f} ms"
        reason_str = f" ({'; '.join(info['reasons'])})" if info["reasons"] else ""
        print(f"  {endpoint:16s} -> {info['verdict']} [HTTP {status}, {lat}]{reason_str}")

    return 0 if report["verdict"] == "PASS" else 1


if __name__ == "__main__":
    raise SystemExit(main())
