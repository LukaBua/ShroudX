# AI-assisted: implemented with Codex.
"""Gaia overlay API tests using a mocked Gaia module and generated FITS frames."""
from __future__ import annotations

import copy

import numpy as np
import pytest
from astropy.time import Time
from fastapi.testclient import TestClient

import app as backend
import gaia
from tests.test_app import api, frames  # noqa: F401 -- pytest fixtures

POSITION = {"ra": "133.76", "dec": "-7.24"}


@pytest.fixture
def catalog_mock(monkeypatch):
    calls = []
    # These identifiers and rows are synthetic test fixtures only.
    body = dict(source="gaia_dr3", epoch=2026., n_sources=2, warnings=["Missing PM in one row."],
        sources=[dict(source_id=1234567890123456789, ra=133.76, dec=-7.24,
                      g_mag=12., pmra=10., pmdec=-3., parallax=2., pm_missing=False),
                 dict(source_id=987654321098765432, ra=133.762, dec=-7.238,
                      g_mag=None, pmra=None, pmdec=None, parallax=None, pm_missing=True)])

    def build(ra, dec, radius, **kwargs):
        calls.append((ra, dec, radius, kwargs))
        result = copy.deepcopy(body)
        result["epoch"] = kwargs["epoch"]
        result["warnings"] += kwargs["base_warnings"]
        return result

    monkeypatch.setattr(gaia, "build_catalog", build)
    return calls, body


def test_catalog_projects_to_png_grid_with_lossless_id(api, catalog_mock):
    client, frame_calls, _ = api
    calls, raw = catalog_mock
    metadata = client.get("/api/meta", params=POSITION).json()
    response = client.get("/api/catalog", params={**POSITION, "mode": "diff", "epoch_a": "ignored"})
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["image_shape"] == metadata["image_shape"]
    assert body["n_sources"] == 2
    assert body["sources"][0]["source_id_text"] == "1234567890123456789"
    assert body["sources"][0]["source_id"] == 1234567890123456789
    assert body["sources"][0]["x"] == pytest.approx(10., abs=1e-7)
    assert body["sources"][0]["y"] == pytest.approx(10., abs=1e-7)
    assert body["sources"][1]["x"] < 10
    assert body["sources"][1]["y"] < 10
    assert body["sources"][1]["pmra"] is None
    assert "Missing PM in one row." in body["warnings"]
    assert "source_id_text" not in raw["sources"][0]  # Do not mutate Gaia's object.
    ra, dec, radius, options = calls[0]
    assert (ra, dec) == (133.76, -7.24)
    assert radius == pytest.approx(np.hypot(20, 20) * 6.15 / 2)
    assert options["epoch"] == pytest.approx(Time(np.mean([f["mjd"] for f in metadata["frames"]]),
                                                 format="mjd").jyear)
    assert options["mode"] == "auto"
    assert options["timeout"] > 0
    assert len(frame_calls) == 1


def test_catalog_empty_result_is_valid(api, catalog_mock):
    client, _, _ = api
    _, body = catalog_mock
    body.update(n_sources=0, sources=[], warnings=[])
    response = client.get("/api/catalog", params=POSITION)
    assert response.status_code == 200
    assert response.json()["sources"] == []
    assert response.json()["n_sources"] == 0


@pytest.mark.parametrize("exception,status,code", [
    (gaia.GaiaUnreachableError("Gaia is unreachable."), 502, "archive_unreachable"),
    (gaia.GaiaNotCachedError("Gaia is not cached."), 404, "not_cached"),
])
def test_catalog_failure_does_not_break_blink(api, monkeypatch, exception, status, code):
    client, _, _ = api
    def fail(*a, **k):
        raise exception
    monkeypatch.setattr(gaia, "build_catalog", fail)
    response = client.get("/api/catalog", params=POSITION)
    assert response.status_code == status
    assert response.json()["error"]["code"] == code
    assert "Gaia" in response.json()["error"]["message"]
    assert "IRSA" not in response.json()["error"]["message"]
    assert "sources" not in response.json()
    assert client.get("/api/meta", params=POSITION).status_code == 200
    assert client.get("/api/image.png", params={**POSITION, "index": 0}).status_code == 200


@pytest.mark.parametrize("mode", ["cache", "synthetic"])
def test_catalog_passes_mode_and_root(api, catalog_mock, tmp_path, mode):
    calls, _ = catalog_mock
    application = backend.create_app(mode=mode, cache_root=tmp_path)
    with TestClient(application) as client:
        response = client.get("/api/catalog", params=POSITION)
        assert response.status_code == 200
        assert calls[0][3]["mode"] == mode
        assert calls[0][3]["cache_root"] == tmp_path.resolve()
        if mode == "synthetic":
            assert response.json()["synthetic"] is True


def test_catalog_rejects_input_before_gaia_query(api, catalog_mock):
    client, _, _ = api
    calls, _ = catalog_mock
    assert client.get("/api/catalog", params={**POSITION, "fov": 900}).status_code == 400
    assert not calls
