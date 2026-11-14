# AI-assisted: implemented with Codex.
"""Tracking/SED API integration with known-answer synthetic frame fixtures."""
from __future__ import annotations

import copy
import json

import pytest
from fastapi.testclient import TestClient

import app as backend
import data
import tracking
from tests.test_tracking import frame

POSITION = {"ra": "359.999", "dec": "20", "fov": 240}


@pytest.fixture
def measurements(monkeypatch, tmp_path):
    raw = tuple(sorted([
        frame(mjd=60000.).copy_with(wavelength=4.10),
        frame(mjd=60001.).copy_with(wavelength=4.12),
        frame(mjd=60180.).copy_with(wavelength=4.11),
        frame(mjd=60000.5).copy_with(detector=2, wavelength=1.25),
    ], key=lambda f: f.mjd))
    # Fabricated test exposures only; not added to the real data/cache.
    raw = tuple(f.copy_with(uri=f"fixture://measurement/{i}",
                            meta={"data_source": "cache", "synthetic": False})
                for i, f in enumerate(raw))
    calls = []

    def retrieve(*args, **kwargs):
        calls.append((args, kwargs))
        return copy.deepcopy(raw)

    monkeypatch.setattr(data, "get_frames", retrieve)
    app = backend.create_app(mode="cache", cache_root=tmp_path / "cache",
                             results_root=tmp_path / "results")
    with TestClient(app, raise_server_exceptions=False) as client:
        yield client, raw, calls, tmp_path


def test_tracking_matches_blink_and_saves_reproducible_result(measurements):
    client, _, calls, root = measurements
    metadata = client.get("/api/meta", params=POSITION).json()
    response = client.get("/api/tracking", params=POSITION)
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["synthetic"] is False
    assert body["data_source"] == "cache"
    assert body["n_frames"] == metadata["n_frames"] == 3
    assert body["n_detected"] == 3
    assert body["image_shape"] == metadata["image_shape"]
    for row, meta in zip(body["frames"], metadata["frames"]):
        assert all(row[key] == meta[key] for key in ("index", "mjd", "epoch", "uri", "version"))
        assert row["measured"]["x"] == pytest.approx(19.5, abs=.15)
        assert row["measured"]["sigma_ra_arcsec"] > 0
        assert row["predicted"] is not row["measured"]
    assert len(calls) == 1
    artifacts = list((root / "results").glob("tracking-*.json"))
    assert len(artifacts) == 1
    assert json.loads(artifacts[0].read_text()) == body
    assert body["data_versions"] == ["27"]
    rerun = client.get(body["provenance"]["request"])
    assert rerun.status_code == 200
    assert rerun.json()["frames"] == body["frames"]


def test_spectrum_all_bands_and_explicit_filters(measurements):
    client, _, _, root = measurements
    response = client.get("/api/spectrum", params=POSITION)
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["n_frames"] == 4
    assert body["n_points"] == 4
    assert {p["detector"] for p in body["frames"]} == {2, 5}
    assert body["flux_unit"] == "Jy"
    assert body["allow_forced"] is False
    assert all(p["position_kind"] == "measured" and p["flux_sigma_jy"] > 0
               for p in body["frames"])
    assert any("non-simultaneous" in w for w in body["warnings"])
    artifacts = list((root / "results").glob("spectrum-*.json"))
    assert json.loads(artifacts[0].read_text()) == body
    response = client.get("/api/spectrum", params={**POSITION, "detector": 2})
    assert response.status_code == 200
    assert response.json()["n_frames"] == 1
    response = client.get("/api/spectrum", params={**POSITION, "wmin": 4.115, "wmax": 4.13})
    assert response.json()["n_frames"] == 1
    assert response.json()["frames"][0]["wavelength_um"] == 4.12


def test_forced_photometry_is_only_used_explicitly(measurements, monkeypatch):
    client, _, _, _ = measurements
    original = tracking.track_frames

    def rejected(*args, **kwargs):
        body = original(*args, **kwargs)
        for row in body["frames"]:
            row.update(status="rejected", reason="ambiguous", measured=None)
        body["n_detected"] = 0
        return body

    monkeypatch.setattr(tracking, "track_frames", rejected)
    body = client.get("/api/tracking", params=POSITION).json()
    assert body["n_detected"] == 0
    assert all(r["measured"] is None for r in body["frames"])
    body = client.get("/api/spectrum", params=POSITION).json()
    assert body["n_points"] == 0
    assert all(r["reason"] == "no_position" for r in body["frames"])
    body = client.get("/api/spectrum", params={**POSITION, "allow_forced": "true"}).json()
    assert body["n_points"] == 4
    assert all(r["status"] == "forced" for r in body["frames"])
    assert all(r["tracking_reason"] == "ambiguous" for r in body["frames"])


@pytest.mark.parametrize("route", ["/api/tracking", "/api/spectrum"])
def test_invalid_parameters_before_archive_and_errors(measurements, monkeypatch, route):
    client, _, calls, _ = measurements
    for change in ({"search_radius": "nan"}, {"search_radius": 0}, {"search_radius": 11},
                   {"detector": 7}, {"wmin": 4.1}):
        assert client.get(route, params={**POSITION, **change}).status_code == 400
    assert not calls
    def unavailable(*a, **k):
        raise data.ArchiveUnreachableError("IRSA unreachable.")
    monkeypatch.setattr(data, "get_frames", unavailable)
    response = client.get(route, params=POSITION)
    assert response.status_code == 502
    assert response.json()["error"]["code"] == "archive_unreachable"


def test_empty_spectrum_and_invalid_forced_option(measurements):
    client, _, calls, _ = measurements
    assert client.get("/api/spectrum", params={**POSITION, "allow_forced": "yes"}).status_code == 400
    assert not calls
    assert client.get("/api/spectrum", params={**POSITION, "detector": 1}).status_code == 422


def test_synthetic_pipeline_and_error_labels(tmp_path):
    data.clear_memory_cache()
    app = backend.create_app(mode="synthetic", cache_root=tmp_path / "cache",
                             results_root=tmp_path / "results")
    with TestClient(app) as client:
        for route in ("/api/tracking", "/api/spectrum"):
            response = client.get(route, params={**POSITION, "mode": "diff"})
            assert response.status_code == 200, response.text
            assert response.json()["synthetic"] is True
            assert any("SYNTHETIC" in w for w in response.json()["warnings"])
            assert client.get(route).json()["synthetic"] is True
    data.clear_memory_cache()
