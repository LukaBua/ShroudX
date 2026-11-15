# AI-assisted: verifies real Gaia DR3 catalog loading without fabricated placeholders.
"""Tests that React/compat /api/catalog serves real cached Gaia DR3 sources."""

from __future__ import annotations

from pathlib import Path

import numpy as np
import pytest
from astropy.io import fits
from astropy.wcs import WCS
from fastapi.testclient import TestClient

import data
from frontend_compat import catalogs, create_practice_app

REPO_ROOT = Path(__file__).resolve().parent.parent


def _fake_wcs_frames(ra: float, dec: float, mjd: float = 61000.0):
    ny, nx = 24, 24
    wcs = WCS(naxis=2)
    wcs.wcs.ctype = ["RA---TAN", "DEC--TAN"]
    wcs.wcs.crval = [ra, dec]
    wcs.wcs.crpix = [12.5, 12.5]
    wcs.wcs.cdelt = [-6.15 / 3600.0, 6.15 / 3600.0]
    wcs.wcs.cunit = ["deg", "deg"]
    wcs.wcs.radesys = "ICRS"
    img = np.ones((ny, nx), dtype=np.float32) * 10.0
    hdu = fits.HDUList(
        [
            fits.PrimaryHDU(),
            fits.ImageHDU(img, header=wcs.to_header(), name="IMAGE"),
            fits.ImageHDU(np.zeros((ny, nx), dtype=np.uint32), name="FLAGS"),
            fits.ImageHDU(np.ones((ny, nx), dtype=np.float32), name="VARIANCE"),
            fits.ImageHDU(np.zeros((ny, nx), dtype=np.float32), name="ZODI"),
            fits.BinTableHDU.from_columns(
                [fits.Column(name="WAVELENGTH", format="D", array=[1.4])],
                name="WCS-WAVE",
            ),
        ]
    )
    frame = data.frame_from_hdul(hdu, ra, dec, mjd, uri="test.fits", bandpass="SPHEREx-D2")
    frame2 = data.frame_from_hdul(hdu, ra, dec, mjd + 180.0, uri="test2.fits", bandpass="SPHEREx-D2")
    return (frame, frame2)


def test_barnard_catalog_uses_real_cached_gaia_sources_no_placeholders(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        data,
        "get_frames",
        lambda ra, dec, fov, **kw: _fake_wcs_frames(ra, dec),
    )
    with TestClient(create_practice_app(REPO_ROOT / "cache", "cache")) as client:
        resp = client.get("/api/catalog", params={"target": "Barnard", "fov": 120})
        assert resp.status_code == 200, resp.text
        payload = resp.json()
        ids = {s["id"] for s in payload["sources"]}

        # Previously hardcoded fabricated placeholders must never appear.
        assert "Gaia DR3 4472832130942576000" not in ids
        assert "Gaia DR3 4472832130942574976" not in ids

        primary = [s for s in payload["sources"] if s["is_primary"]]
        assert len(primary) == 1
        barn = primary[0]
        assert barn["id"] == "Gaia DR3 4472832130942575872"
        # Real Gaia DR3 phot_g_mean_mag is ~8.19, not 9.51 visual magnitude.
        assert barn["mag"] == pytest.approx(8.194, abs=0.01)
        assert barn["pm_total"] == pytest.approx(10393.3, abs=2.0)
        assert "~1.8 pc" in barn["type"]
        assert len(payload["sources"]) > 3
        assert all(s["position_kind"] != "illustrative" for s in payload["sources"])
        assert all(s["type"] != "Gaia DR3 field star" for s in payload["sources"])
        by_id = {s["id"]: s for s in payload["sources"]}
        assert by_id["Gaia DR3 4472835983521061248"]["type"] == "Distant low-PM background star"


def test_wise0855_and_other_targets_use_real_gaia_and_never_fabricate_in_synthetic(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        data,
        "get_frames",
        lambda ra, dec, fov, **kw: _fake_wcs_frames(ra, dec),
    )
    with TestClient(create_practice_app(REPO_ROOT / "cache", "cache")) as client:
        wise = client.get("/api/catalog", params={"target": "WISE0855", "fov": 120}).json()
        assert wise["sources"][0]["id"] == "WISE J085510.83-071442.5"
        assert wise["sources"][0]["is_primary"] is True
        assert all(
            s["id"] not in {"Gaia DR3 5683416480749419136", "Gaia DR3 5683416480749420032"}
            for s in wise["sources"]
        )
        assert len(wise["sources"]) > 2

        eps = client.get("/api/catalog", params={"target": "EpsilonIndiB", "fov": 120}).json()
        eps_by_id = {s["id"]: s for s in eps["sources"]}
        assert [s["id"] for s in eps["sources"] if s["is_primary"]] == ["Gaia DR3 6412596012146801152"]
        assert "~3.7 pc" in eps_by_id["Gaia DR3 6412596012146801152"]["type"]
        assert eps_by_id["Gaia DR3 6412595909067585024"]["type"] == "Nearby foreground star (~440 pc)"

        rwaur = client.get("/api/catalog", params={"target": "RWAurigae", "fov": 120}).json()
        rwaur_by_id = {s["id"]: s for s in rwaur["sources"]}
        assert [s["id"] for s in rwaur["sources"] if s["is_primary"]] == ["Gaia DR3 156430822114424576"]
        assert "~156 pc" in rwaur_by_id["Gaia DR3 156430822114424576"]["type"]
        assert rwaur_by_id["Gaia DR3 156430817820015232"]["type"] == "Co-moving companion candidate (~183 pc)"

        rdra = client.get("/api/catalog", params={"target": "RDraconis", "fov": 120}).json()
        rdra_by_id = {s["id"]: s for s in rdra["sources"]}
        assert [s["id"] for s in rdra["sources"] if s["is_primary"]] == ["Gaia DR3 1648328016354496384"]
        assert "~799 pc" in rdra_by_id["Gaia DR3 1648328016354496384"]["type"]
        assert "PM missing" in rdra_by_id["Gaia DR3 1648328016354500352"]["type"]

    with TestClient(create_practice_app(REPO_ROOT / "cache", "synthetic")) as synth_client:
        synth = synth_client.get("/api/catalog", params={"target": "Barnard", "fov": 120}).json()
        assert synth["sources"] == []
        assert synth["count"] == 0


def test_offline_gaia_classification_edge_cases() -> None:
    assert catalogs._format_parallax_distance(None) is None
    assert catalogs._format_parallax_distance(-0.3) is None
    assert catalogs._format_parallax_distance(0.99) is None
    assert catalogs._format_parallax_distance(float("nan")) is None
    assert catalogs._format_parallax_distance(546.9759) == "~1.8 pc"
    assert catalogs._format_parallax_distance(5.356) == "~187 pc"

    base_kw = {"is_primary": False, "profile": None, "primary_row": None}
    assert (
        catalogs._classify_gaia_source(
            {"source_id": 1, "ra": 0.0, "dec": 0.0, "pmra": 190.0, "pmdec": 0.0, "parallax": 5.356},
            **base_kw,
        )
        == "High-PM foreground star (~187 pc)"
    )
    assert (
        catalogs._classify_gaia_source(
            {"source_id": 2, "ra": 0.0, "dec": 0.0, "pmra": 55.0, "pmdec": 0.0, "parallax": -0.2},
            **base_kw,
        )
        == "High-PM field star"
    )
    assert (
        catalogs._classify_gaia_source(
            {"source_id": 3, "ra": 0.0, "dec": 0.0, "pmra": 2.0, "pmdec": 3.0, "parallax": -0.5},
            **base_kw,
        )
        == "Distant low-PM background star"
    )
    assert (
        catalogs._classify_gaia_source(
            {"source_id": 4, "ra": 0.0, "dec": 0.0, "pmra": float("nan"), "pmdec": 1.0, "parallax": None},
            **base_kw,
        )
        == "Field source (PM missing; J2016.0 pos)"
    )
