"""Tests for Barnard's Star / 240" defaults and expanded Gaia TAP timeouts."""

import inspect
from pathlib import Path
from types import SimpleNamespace

import pytest
from astropy.table import Table
from fastapi.testclient import TestClient

import app as backend
import frontend_compat.app as compat_app
import frontend_compat.coords as compat_coords
import gaia


def _sample_gaia_table() -> Table:
    return Table({
        "source_id": [4472832130942575872],
        "ra": [269.44633],
        "dec": [4.76762],
        "pmra": [-801.55],
        "pmdec": [10362.39],
        "parallax": [546.97],
        "phot_g_mean_mag": [8.2],
        "ref_epoch": [2016.0],
    })


def test_default_gaia_timeout_and_env_override(monkeypatch):
    assert gaia.DEFAULT_TIMEOUT_SEC == 90.0
    monkeypatch.delenv("SPHX_GAIA_TIMEOUT", raising=False)
    assert gaia.default_timeout_sec() == 90.0
    assert gaia.default_timeout_sec(min_timeout=30.0) == 90.0
    assert gaia.default_timeout_sec(min_timeout=120.0) == 120.0

    monkeypatch.setenv("SPHX_GAIA_TIMEOUT", "150")
    assert gaia.default_timeout_sec() == 150.0
    assert gaia.default_timeout_sec(min_timeout=120.0) == 150.0

    monkeypatch.setenv("SPHX_GAIA_TIMEOUT", "invalid")
    assert gaia.default_timeout_sec() == 90.0


def test_query_gaia_falls_back_to_async_on_sync_timeout(tmp_path):
    calls = []

    class DualTimeoutTAP:
        def launch_job(self, adql):
            calls.append("sync")
            exc = RuntimeError("HTTP Error 504: Gateway Time-out")
            exc.response = SimpleNamespace(status_code=504)
            raise exc

        def launch_job_async(self, adql):
            calls.append("async")
            return SimpleNamespace(get_results=_sample_gaia_table)

    rows = gaia.query_gaia(
        269.44633,
        4.76762,
        240.0,
        tap_client=DualTimeoutTAP(),
        cache_root=tmp_path,
        mode="live",
        timeout=10.0,
    )
    assert calls == ["sync", "async"]
    assert len(rows) == 1
    assert rows[0]["source_id"] == 4472832130942575872


def test_query_gaia_does_not_retry_async_on_non_timeout_error(tmp_path):
    calls = []

    class DualErrorTAP:
        def launch_job(self, adql):
            calls.append("sync")
            raise ValueError("Malformed ADQL query")

        def launch_job_async(self, adql):
            calls.append("async")
            return SimpleNamespace(get_results=_sample_gaia_table)

    with pytest.raises(Exception):
        gaia.query_gaia(
            269.44633,
            4.76762,
            240.0,
            tap_client=DualErrorTAP(),
            cache_root=tmp_path,
            mode="live",
            timeout=10.0,
        )
    assert calls == ["sync"]


def test_app_catalog_endpoint_uses_expanded_gaia_timeout(tmp_path, monkeypatch):
    seen = {}

    def fake_build_catalog(ra, dec, radius, **kwargs):
        seen["ra"] = ra
        seen["dec"] = dec
        seen["radius"] = radius
        seen["timeout"] = kwargs["timeout"]
        return {
            "sources": [],
            "warnings": list(kwargs.get("base_warnings") or ()),
            "cache_hit": False,
            "query_radius_arcsec": radius,
        }

    monkeypatch.setenv("SPHX_REQUEST_TIMEOUT", "30.0")
    monkeypatch.delenv("SPHX_GAIA_TIMEOUT", raising=False)
    monkeypatch.setattr(gaia, "build_catalog", fake_build_catalog)

    application = backend.create_app(mode="synthetic", cache_root=tmp_path)
    with TestClient(application) as client:
        resp = client.get("/api/catalog", params={"ra": "269.44633", "dec": "4.76762", "fov": "480"})
        assert resp.status_code == 200
        assert seen["timeout"] == 90.0

        monkeypatch.setenv("SPHX_GAIA_TIMEOUT", "135")
        resp2 = client.get("/api/catalog", params={"ra": "269.44633", "dec": "4.76762", "fov": "480"})
        assert resp2.status_code == 200
        assert seen["timeout"] == 135.0


def test_compat_params_and_legacy_ui_defaults():
    params_sig = inspect.signature(compat_app.Params.__init__)
    fov_param = params_sig.parameters["fov"].default
    assert fov_param.default == 240.0
    assert compat_coords.KNOWN_TARGETS["barnard"] == (269.44633, 4.76762)
    assert compat_coords.KNOWN_TARGETS["barnardstar"] == (269.44633, 4.76762)

    legacy_html = Path(__file__).resolve().parents[1].joinpath("static", "index.html").read_text(encoding="utf-8")
    assert 'value="Barnard\'s Star"' in legacy_html
    assert 'value="WISE 0855-0714"' not in legacy_html
    assert '<output id="fovValue">240</output>' in legacy_html
    assert "['fov',30,480,240]" in legacy_html
