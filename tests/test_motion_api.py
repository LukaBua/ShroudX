# AI-assisted: implemented with Codex.
"""HTTP adaptation tests with a mocked motion module and synthetic FITS frames."""
from __future__ import annotations

import copy
import json
from types import SimpleNamespace

import numpy as np
import pytest
from fastapi.testclient import TestClient

import app as backend
import coords
import data
from tests.test_app import frames  # noqa: F401 -- pytest fixture

POSITION = {"ra": "133.76", "dec": "-7.24"}


@pytest.fixture
def motion_api(monkeypatch, tmp_path, frames):
    calls = []
    fit_overrides = {}
    benchmark_file = tmp_path / "benchmarks.csv"
    # Test fixtures only, not literature entries for production.
    benchmark_file.write_text(
        "name,ra,dec,pmra,pmdec,parallax,citation\n"
        "Test mover,133.76,-7.24,4000,0,100,synthetic-test-fixture\n"
        "Test stationary,120,0,0,0,,synthetic-test-fixture\n", encoding="utf-8")
    monkeypatch.setattr(data, "get_frames", lambda *a, **k: copy.deepcopy(frames))

    def measure(groups, ra, dec):
        calls.append((groups, ra, dec))
        positions = []
        for i, group in enumerate(groups):
            x, y = 9.5 - i, 9.5 + i
            r, d = group[0].wcs.celestial.pixel_to_world_values(x, y)
            positions.append(SimpleNamespace(mjd=float(np.mean([f.mjd for f in group])),
                ra=float(r), dec=float(d), sigma_ra_arcsec=.4, sigma_dec_arcsec=.6,
                n_frames_used=len(group), n_frames_rejected=0))
        result = dict(epochs=tuple(positions), pm_total_arcsec_yr=4.,
            sigma_pm_total_arcsec_yr=.2, pm_ra_arcsec_yr=4., pm_dec_arcsec_yr=0.,
            pa_deg=90., sigma_pa_deg=3., parallax_arcsec=None, sigma_parallax_arcsec=None,
            reduced_chi2=float("nan"), uncertainties_inflated=False, n_epochs=len(positions),
            motion_significance=20., parallax_fixed=False,
            warnings=["Two epochs: parallax and proper motion are degenerate."])
        result.update(fit_overrides)
        return SimpleNamespace(**result)

    monkeypatch.setattr(backend.motion, "measure_and_fit_epochs", measure)
    monkeypatch.setattr(coords, "resolve_name", lambda name:
        coords.ResolvedTarget(133.76, -7.24, "offline", False, []))
    application = backend.create_app(mode="auto", cache_root=tmp_path / "cache",
        results_root=tmp_path / "results", benchmarks_path=benchmark_file)
    with TestClient(application, raise_server_exceptions=False) as client:
        yield client, calls, fit_overrides, tmp_path, application


def test_motion_mapping_overlay_and_reproducible_artifact(motion_api):
    client, calls, _, root, _ = motion_api
    meta = client.get("/api/meta", params=POSITION).json()
    response = client.get("/api/motion", params=POSITION)
    assert response.status_code == 200, response.text
    body = response.json()
    assert [ep["epoch"] for ep in body["epochs"]] == [0, 1]
    assert [ep["n_frames"] for ep in body["epochs"]] == [2, 2]
    assert body["epochs"][0]["sigma_arcsec"] == .6
    assert body["epochs"][0]["x"] == pytest.approx(10, abs=1e-7)
    assert body["epochs"][0]["y"] == pytest.approx(10, abs=1e-7)
    assert body["epochs"][1]["x"] < body["epochs"][0]["x"]
    assert body["epochs"][1]["y"] < body["epochs"][0]["y"]
    assert body["epochs"][0]["radius_px"] == pytest.approx(.6 / 6.15)
    assert body["image_shape"] == meta["image_shape"]
    assert body["fit"]["pm_arcsec_per_yr"] == 4
    assert body["fit"]["pm_sigma"] == .2
    assert body["fit"]["parallax_arcsec"] is None
    assert body["fit"]["parallax_sigma"] is None
    assert body["fit"]["chi2_reduced"] is None
    assert body["benchmark"]["verdict"] is None
    assert any("degenerate" in w for w in body["warnings"])
    assert all(f.zodi is None for group in calls[0][0] for f in group)
    assert calls[0][1:] == (133.76, -7.24)
    files = list((root / "results").glob("motion-*.json"))
    assert len(files) == 1
    assert json.loads(files[0].read_text()) == body
    assert "/api/motion?" in body["provenance"]["command"]
    assert all(f["version"] == 20 for f in body["provenance"]["frames"])


@pytest.mark.parametrize("sigma,pm,verdict", [(.2, 4, "AGREE"),
    (2, 4, "UNINFORMATIVE"), (.2, 6, "DISAGREE")])
def test_motion_csv_comparison_uses_existing_criteria(motion_api, sigma, pm, verdict):
    client, _, overrides, _, _ = motion_api
    overrides.update(sigma_pm_total_arcsec_yr=sigma, pm_ra_arcsec_yr=pm,
                     pm_total_arcsec_yr=pm, motion_significance=pm / sigma)
    response = client.get("/api/motion", params={"target": "Test mover"})
    assert response.status_code == 200, response.text
    benchmark = response.json()["benchmark"]
    assert benchmark["verdict"] == verdict
    assert benchmark["literature_pm_arcsec_per_yr"] == 4
    assert benchmark["literature_parallax_arcsec"] == .1
    assert benchmark["citation"] == "synthetic-test-fixture"
    if verdict == "DISAGREE":
        assert any("SYSTEMATIC" in w for w in response.json()["warnings"])


def test_motion_scaled_errors_are_forwarded(motion_api):
    client, _, overrides, _, _ = motion_api
    overrides.update(reduced_chi2=4, uncertainties_inflated=True,
                     sigma_pm_total_arcsec_yr=.4)
    body = client.get("/api/motion", params=POSITION).json()
    assert body["fit"]["uncertainty_scaled"] is True
    assert body["fit"]["pm_sigma"] == .4  # Not scaled twice by the API.
    assert any("inflated" in w for w in body["warnings"])


def test_motion_errors_and_optional_view_params(motion_api, monkeypatch, frames):
    client, calls, _, _, application = motion_api
    assert client.get("/api/motion").status_code == 400
    assert client.get("/api/motion", params={**POSITION, "mode": "diff", "epoch_a": "ignored"}).status_code == 200
    assert len(calls) == 1
    application.state.products.entries.clear()
    monkeypatch.setattr(data, "get_frames", lambda *a, **k: frames[:2])
    assert client.get("/api/motion", params=POSITION).status_code == 422
    application.state.products.entries.clear()
    def no_coverage(*a, **k):
        raise data.NoCoverageError("No SPHEREx coverage at this position.")
    monkeypatch.setattr(data, "get_frames", no_coverage)
    response = client.get("/api/motion", params=POSITION)
    assert response.status_code == 404
    assert response.json()["error"]["code"] == "no_coverage"


def test_motion_rejected_frames_return_422(motion_api, monkeypatch):
    client, _, _, root, _ = motion_api
    def rejected(*a, **k):
        raise ValueError("At least 2 epochs are required to fit proper motion, got 1.")
    monkeypatch.setattr(backend.motion, "measure_and_fit_epochs", rejected)
    response = client.get("/api/motion", params=POSITION)
    assert response.status_code == 422
    assert response.json()["error"]["code"] == "insufficient_data"
    assert not (root / "results").exists()


def test_motion_synthetic_label_and_no_literature_verdict(motion_api, tmp_path):
    _, _, _, root, _ = motion_api
    application = backend.create_app(mode="synthetic", cache_root=tmp_path,
        results_root=tmp_path / "synthetic-results", benchmarks_path=root / "benchmarks.csv")
    with TestClient(application) as client:
        response = client.get("/api/motion", params={"target": "Test mover"})
        assert response.status_code == 200
        assert response.json()["synthetic"] is True
        assert response.json()["benchmark"]["verdict"] is None


def test_motion_preserves_ids_when_an_epoch_is_rejected(motion_api, monkeypatch, frames):
    client, _, overrides, _, _ = motion_api
    three_epochs = frames + tuple(f.copy_with(mjd=f.mjd + 360) for f in frames[:2])
    monkeypatch.setattr(data, "get_frames", lambda *a, **k: three_epochs)
    overrides.update(n_epochs=2, epochs=tuple(SimpleNamespace(
        mjd=mjd, ra=133.76, dec=-7.24, sigma_ra_arcsec=.4, sigma_dec_arcsec=.6,
        n_frames_used=2) for mjd in [60901., 61261.]))
    response = client.get("/api/motion", params=POSITION)
    assert response.status_code == 200, response.text
    assert [ep["epoch"] for ep in response.json()["epochs"]] == [0, 2]


def test_motion_real_synthetic_pipeline(tmp_path):
    data.clear_memory_cache()
    with TestClient(backend.create_app(mode="synthetic", cache_root=tmp_path / "cache",
                                      results_root=tmp_path / "results")) as client:
        response = client.get("/api/motion", params=POSITION)
        assert response.status_code == 200, response.text
        body = response.json()
        assert body["synthetic"] is True
        assert body["fit"]["n_epochs"] == 2
        assert body["fit"]["pm_sigma"] > 0
        assert body["fit"]["chi2_reduced"] is None
        assert len(list((tmp_path / "results").glob("*.json"))) == 1
    data.clear_memory_cache()
