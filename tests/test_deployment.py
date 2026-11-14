# AI-assisted: written with AI pair-programming assistant.
"""Tests for hosted deployment, IRSA timeouts/concurrency, cache bundling, and smoke tests."""

from __future__ import annotations

import copy
import json
import threading
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import numpy as np
import pytest
from fastapi.testclient import TestClient

import app as backend
import coords
import data
from scripts import bundle_cache, smoke_test
from tests.test_data import _make_cutout_hdul

ROOT = Path(__file__).resolve().parent.parent


def _write_synthetic_cutout(
    path: Path,
    *,
    ra: float,
    dec: float,
    detector: int,
    mjd: float,
    w0_um: float,
    uri: str,
) -> None:
    """Write a synthetic FITS cutout with known target position, detector, wavelength, and MJD."""
    yy, xx = np.mgrid[:20, :20]
    image = 12.0 + 35.0 * np.exp(-((xx - 10.0) ** 2 + (yy - 10.0) ** 2) / 3.0)
    with _make_cutout_hdul(
        ny=20,
        nx=20,
        ra=ra,
        dec=dec,
        detector=detector,
        mjd=mjd,
        w0_um=w0_um,
        image=image,
        include_psf=False,
        uri=uri,
    ) as hdul:
        with data.warnings_suppressed():
            hdul.writeto(path, overwrite=True, output_verify="silentfix")


def test_dockerfile_and_dockerignore_requirements() -> None:
    """Dockerfile uses python slim, non-root user, SPHX_MODE=auto, uvicorn on $PORT."""
    dockerfile = (ROOT / "Dockerfile").read_text(encoding="utf-8")
    dockerignore = (ROOT / ".dockerignore").read_text(encoding="utf-8")

    assert "FROM python:" in dockerfile and "-slim" in dockerfile
    assert "SPHX_MODE=auto" in dockerfile
    assert "useradd" in dockerfile and "USER appuser" in dockerfile
    assert "uvicorn app:app" in dockerfile and "PORT" in dockerfile
    for module_file in (
        "frame.py",
        "coords.py",
        "flags.py",
        "data.py",
        "gaia.py",
        "motion.py",
        "processing.py",
        "app.py",
    ):
        assert module_file in dockerfile, f"Dockerfile missing backend module {module_file}"
    for forbidden in ("API_KEY", "SECRET", "PASSWORD", "TOKEN"):
        assert forbidden not in dockerfile.upper()

    for ignored in (".git", ".venv", "__pycache__", ".pytest_cache", ".env"):
        assert ignored in dockerignore


def test_irsa_request_timeout_returns_502_with_clear_message(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """An IRSA query exceeding request_timeout fails with 502 archive_unreachable."""
    release = threading.Event()

    def hanging_get_frames(*args, **kwargs):
        release.wait(timeout=5.0)
        raise AssertionError("Should have timed out before returning")

    monkeypatch.setattr(data, "get_frames", hanging_get_frames)
    application = backend.create_app(
        mode="auto",
        cache_root=tmp_path,
        request_timeout=0.08,
        irsa_concurrency=2,
    )
    try:
        with TestClient(application, raise_server_exceptions=False) as client:
            resp = client.get("/api/meta", params={"ra": "133.76", "dec": "-7.24"})
            assert resp.status_code == 502
            body = resp.json()
            assert body["error"]["code"] == "archive_unreachable"
            assert "IRSA" in body["error"]["message"]
            assert "timed out" in body["error"]["message"]
            assert body.get("synthetic") is not True
    finally:
        release.set()


def test_irsa_concurrency_limit_does_not_block_cached_fields_or_health(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """When IRSA concurrency slots are full, cached fields and /health still respond immediately."""
    # Populate a cached cutout bucket for (133.76, -7.24)
    bucket = 120
    key = data.cache_key_for(133.76, -7.24, bucket)
    cdir = tmp_path / key
    cdir.mkdir(parents=True)
    rows = []
    for idx, mjd in enumerate([60900.1, 60901.1, 61080.1, 61081.1], start=1):
        fname = f"{idx:04d}.fits"
        uri = f"l2b-v27/cached_{idx:04d}.fits"
        _write_synthetic_cutout(
            cdir / fname,
            ra=133.76,
            dec=-7.24,
            detector=5,
            mjd=mjd,
            w0_um=4.12 + 0.01 * idx,
            uri=uri,
        )
        rows.append((fname, mjd, "SPHEREx-D5", uri))
    data._write_index_csv(cdir, rows)

    real_get_frames = data.get_frames
    entered = threading.Event()
    release = threading.Event()

    def selective_get_frames(ra, dec, fov, **kwargs):
        # Cached field at (133.76, -7.24) loads normally from disk
        if abs(float(ra) - 133.76) < 0.01 and abs(float(dec) - (-7.24)) < 0.01:
            return real_get_frames(ra, dec, fov, **kwargs)
        # Uncached field blocks simulating a slow IRSA query, then returns frames at (ra, dec)
        entered.set()
        release.wait(timeout=5.0)
        yy, xx = np.mgrid[:20, :20]
        image = 12.0 + 35.0 * np.exp(-((xx - 10.0) ** 2 + (yy - 10.0) ** 2) / 3.0)
        out = []
        for idx, mjd in enumerate([60900.1, 60901.1, 61080.1, 61081.1], start=1):
            with _make_cutout_hdul(
                ny=20,
                nx=20,
                ra=float(ra),
                dec=float(dec),
                detector=5,
                mjd=mjd,
                w0_um=4.12 + 0.01 * idx,
                image=image,
                include_psf=False,
                uri=f"l2b-v27/live_{idx:04d}.fits",
            ) as hdul:
                f = data.frame_from_hdul(
                    hdul, float(ra), float(dec), mjd, uri=f"l2b-v27/live_{idx:04d}.fits"
                )
            f.meta.update(data_source="archive", synthetic=False)
            out.append(f)
        return tuple(out)

    monkeypatch.setattr(data, "get_frames", selective_get_frames)
    application = backend.create_app(
        mode="auto",
        cache_root=tmp_path,
        request_timeout=5.0,
        irsa_concurrency=1,
        irsa_queue_timeout=0.05,
    )

    with TestClient(application, raise_server_exceptions=False) as client:
        with ThreadPoolExecutor(max_workers=2) as pool:
            slow_future = pool.submit(
                client.get, "/api/meta", params={"ra": "10.0", "dec": "10.0"}
            )
            try:
                assert entered.wait(timeout=2.0)

                # 1. Second uncached request hits the concurrency limit (irsa_concurrency=1)
                busy_resp = client.get("/api/meta", params={"ra": "20.0", "dec": "20.0"})
                assert busy_resp.status_code == 502
                assert busy_resp.json()["error"]["code"] == "archive_unreachable"
                assert "concurrency limit" in busy_resp.json()["error"]["message"]

                # 2. Cached field is NOT blocked by the slow IRSA request!
                cached_meta = client.get("/api/meta", params={"ra": "133.76", "dec": "-7.24"})
                assert cached_meta.status_code == 200
                assert cached_meta.json()["data_source"] == "cache"

                cached_img = client.get(
                    "/api/image.png",
                    params={"ra": "133.76", "dec": "-7.24", "mode": "blink", "index": "0"},
                )
                assert cached_img.status_code == 200
                assert cached_img.headers["x-data-source"] == "cache"

                # 3. /health responds with data_mode="auto" and positive cache_size_mb
                health_resp = client.get("/health")
                assert health_resp.status_code == 200
                assert health_resp.json()["data_mode"] == "auto"
                assert health_resp.json()["cache_size_mb"] > 0
            finally:
                release.set()

            assert slow_future.result(timeout=5.0).status_code == 200


def test_bundle_demo_cache_with_synthetic_cutouts(tmp_path: Path) -> None:
    """bundle_demo_cache selects valid multi-epoch windows and enforces the size budget."""
    src_dir = tmp_path / "raw_cache"
    out_dir = tmp_path / "bundled_cache"
    res_dir = tmp_path / "results"

    test_targets = {
        "Test Mover": coords.OfflineEntry(
            ra=133.76,
            dec=-7.24,
            pmra=0.0,
            pmdec=0.0,
            ref_epoch=2026.0,
            target_class="cold brown dwarf",
            citation="test_citation",
        )
    }

    key = data.cache_key_for(133.76, -7.24, 480)
    cdir = src_dir / key
    cdir.mkdir(parents=True)

    rows = []
    idx = 0
    for det, w_base in ((2, 1.35), (5, 4.15)):
        for mjd_offset in (0.0, 1.0, 180.0, 181.0):
            idx += 1
            fname = f"{idx:04d}.fits"
            mjd = 60900.0 + mjd_offset
            uri = f"l2b-v27/synth_{idx:04d}.fits"
            _write_synthetic_cutout(
                cdir / fname,
                ra=133.76,
                dec=-7.24,
                detector=det,
                mjd=mjd,
                w0_um=w_base + 0.005 * (idx % 3),
                uri=uri,
            )
            rows.append((fname, mjd, f"SPHEREx-D{det}", uri))
    data._write_index_csv(cdir, rows)

    report = bundle_cache.bundle_demo_cache(
        src_dir,
        out_dir,
        targets=test_targets,
        max_size_mb=50.0,
    )
    assert report["verdict"] == "PASS"
    assert report["data_versions"] == ["l2b-v27"]
    assert 0 < report["total_size_mb"] <= 50.0
    assert report["fields"][0]["default_window_frames"] == 4
    assert report["fields"][0]["default_window_epochs"] == 2

    # Failing size limit must report FAIL
    fail_report = bundle_cache.bundle_demo_cache(
        src_dir,
        tmp_path / "bundled_fail",
        targets=test_targets,
        max_size_mb=0.0001,
    )
    assert fail_report["verdict"] == "FAIL"
    assert any("exceeds target" in f for f in fail_report["failures"])

    # Verify CLI writes results/bundled_cache.json and bundled_cache.md
    monkeypatch_targets = coords.OFFLINE_TARGETS
    orig_targets = dict(monkeypatch_targets)
    try:
        monkeypatch_targets.clear()
        monkeypatch_targets.update(test_targets)
        rc = bundle_cache.main(
            [
                "--source-dir",
                str(src_dir),
                "--output-dir",
                str(tmp_path / "cli_bundle_check"),
                "--results-dir",
                str(res_dir),
            ]
        )
        assert rc == 0
        written = json.loads((res_dir / "bundled_cache.json").read_text(encoding="utf-8"))
        assert written["verdict"] == "PASS"
        assert written["total_size_mb"] <= 50.0
        assert "python scripts/bundle_cache.py" in written["command"]
    finally:
        monkeypatch_targets.clear()
        monkeypatch_targets.update(orig_targets)


def test_smoke_test_script_passes_and_reports_missing_motion(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """run_smoke_test verifies /health, /api/meta, /api/image.png, and /api/motion."""
    yy, xx = np.mgrid[:20, :20]
    synth_frames = []
    for i, mjd in enumerate([60900.5, 60901.5, 61080.5, 61081.5]):
        image = 10 + 30 * np.exp(-((xx - 10) ** 2 + (yy - 10) ** 2) / 3)
        with _make_cutout_hdul(mjd=mjd, image=image, w0_um=4.12 + i * 0.01) as hdul:
            frame = data.frame_from_hdul(hdul, 133.76, -7.24, mjd, uri="l2b-v27/test.fits")
        frame.meta.update(data_source="cache", synthetic=False)
        synth_frames.append(frame)

    monkeypatch.setattr(data, "get_frames", lambda *a, **k: copy.deepcopy(tuple(synth_frames)))
    (tmp_path / "dummy.fits").write_bytes(b"0" * 1024)
    application = backend.create_app(mode="auto", cache_root=tmp_path)
    # Explicitly simulate an older deployment without motion. The production
    # application now provides this route; the test installs its own below.
    application.router.routes = [route for route in application.router.routes
                                 if getattr(route, "path", None) != "/api/motion"]

    with TestClient(application, raise_server_exceptions=False) as client:
        def client_requester(url: str, timeout: float) -> smoke_test.HTTPResponseData:
            path_and_query = url.split("http://testserver", 1)[-1]
            resp = client.get(path_and_query)
            return smoke_test.HTTPResponseData(
                status_code=resp.status_code,
                headers={k.lower(): v for k, v in resp.headers.items()},
                body=resp.content,
            )

        # 1. Before /api/motion exists: /health, /api/meta, /api/image.png PASS; /api/motion FAILs
        report_pre_motion = smoke_test.run_smoke_test(
            "http://testserver",
            target="WISE 0855-0714",
            repeats=2,
            requester=client_requester,
        )
        assert report_pre_motion["checks"]["/health"]["verdict"] == "PASS"
        assert report_pre_motion["checks"]["/api/meta"]["verdict"] == "PASS"
        assert report_pre_motion["checks"]["/api/image.png"]["verdict"] == "PASS"
        assert report_pre_motion["checks"]["/api/motion"]["verdict"] == "FAIL"
        assert report_pre_motion["verdict"] == "FAIL"

        # 2. Register a contract-compliant /api/motion route and verify full PASS
        @application.get("/api/motion")
        def mock_motion():
            return {
                "epochs": [
                    {
                        "epoch": 0,
                        "mjd": 60901.0,
                        "ra": 133.7599,
                        "dec": -7.2422,
                        "sigma_arcsec": 0.45,
                        "n_frames": 2,
                    },
                    {
                        "epoch": 1,
                        "mjd": 61081.0,
                        "ra": 133.7588,
                        "dec": -7.2421,
                        "sigma_arcsec": 0.42,
                        "n_frames": 2,
                    },
                ],
                "fit": {
                    "pm_arcsec_per_yr": 8.12,
                    "pm_sigma": 0.35,
                    "position_angle_deg": 274.7,
                    "pa_sigma": 2.5,
                    "parallax_arcsec": None,
                    "parallax_sigma": None,
                    "chi2_reduced": 0.92,
                    "n_epochs": 2,
                    "significance": 23.2,
                    "uncertainty_scaled": False,
                },
                "benchmark": {
                    "literature_pm_arcsec_per_yr": 8.15,
                    "literature_parallax_arcsec": 0.439,
                    "verdict": "AGREE",
                },
                "warnings": [],
            }

        report_full = smoke_test.run_smoke_test(
            "http://testserver",
            target="WISE 0855-0714",
            repeats=2,
            requester=client_requester,
        )
        assert report_full["verdict"] == "PASS"
        assert report_full["data_versions"] == ["l2b-v27"]
        for endpoint in ("/health", "/api/meta", "/api/image.png", "/api/motion"):
            assert report_full["checks"][endpoint]["verdict"] == "PASS"
            assert report_full["checks"][endpoint]["latency_ms_mean"] >= 0.0
            assert report_full["checks"][endpoint]["latency_ms_sigma"] >= 0.0
