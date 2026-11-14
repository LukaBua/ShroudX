# AI-assisted: written with AI pair-programming assistant.
"""Tests for gaia.py, GET /api/catalog, and scripts/fetch_gaia.py using a mocked TAP service."""

from __future__ import annotations

import json
import math
import threading
from pathlib import Path
from typing import Any

import numpy as np
import pytest
from astropy.table import Table
from fastapi.testclient import TestClient

import app as backend
import coords
import data
import gaia
from tests.test_app import frames  # noqa: F401 -- synthetic FITS fixture for image projection
from scripts import fetch_gaia


class FakeTAPJob:
    """Mock astroquery TAP job returning a pre-built Astropy Table."""

    def __init__(self, table: Table):
        self._table = table

    def get_results(self) -> Table:
        return self._table


class FakeGaiaTAP:
    """Mock astroquery.gaia.Gaia TAP client recording launched ADQL queries."""

    def __init__(self, rows: list[dict[str, Any]] | None = None, *, error: Exception | None = None):
        self.rows = list(rows or [])
        self.error = error
        self.queries: list[str] = []

    def launch_job(self, query: str) -> FakeTAPJob:
        self.queries.append(query)
        if self.error is not None:
            raise self.error
        tbl = gaia._rows_to_table(self.rows, max_rows=None)
        return FakeTAPJob(tbl)


def test_propagate_to_epoch_matches_hand_calculation() -> None:
    """propagate_to_epoch matches an exact hand calculation and flags missing proper motions."""
    # Source 1: ra=120.0 deg, dec=60.0 deg (cos(60 deg) = 0.5), ref_epoch=2016.0 -> epoch=2026.0 (dt=10 yr)
    # pmra = -1800.0 mas/yr -> d_ra = (-1800 * 10) / (3_600_000 * 0.5) = -0.01 deg -> ra = 119.99 deg
    # pmdec = +3600.0 mas/yr -> d_dec = (+3600 * 10) / 3_600_000 = +0.01 deg -> dec = 60.01 deg
    # Source 2: missing pmra/pmdec -> keeps (ra, dec) = (120.05, 60.02) and sets pm_missing=True
    raw_rows = [
        {
            "source_id": 1001,
            "ra": 120.0,
            "dec": 60.0,
            "pmra": -1800.0,
            "pmdec": 3600.0,
            "parallax": 25.4,
            "phot_g_mean_mag": 12.3,
            "ref_epoch": 2016.0,
        },
        {
            "source_id": 1002,
            "ra": 120.05,
            "dec": 60.02,
            "pmra": None,
            "pmdec": None,
            "parallax": None,
            "phot_g_mean_mag": 17.8,
            "ref_epoch": 2016.0,
        },
    ]
    table = gaia._rows_to_table(raw_rows)
    propagated = gaia.propagate_to_epoch(table, 2026.0)

    assert len(propagated) == 2
    assert "pm_missing" in propagated.colnames

    row0 = propagated[0]
    assert int(row0["source_id"]) == 1001
    assert bool(row0["pm_missing"]) is False
    expected_ra = 120.0 + (-1800.0 * 10.0) / (3_600_000.0 * math.cos(math.radians(60.0)))
    expected_dec = 60.0 + (3600.0 * 10.0) / 3_600_000.0
    assert float(row0["ra"]) == pytest.approx(expected_ra, abs=1e-10)
    assert float(row0["dec"]) == pytest.approx(expected_dec, abs=1e-10)

    row1 = propagated[1]
    assert int(row1["source_id"]) == 1002
    assert bool(row1["pm_missing"]) is True
    assert float(row1["ra"]) == pytest.approx(120.05, abs=1e-10)
    assert float(row1["dec"]) == pytest.approx(60.02, abs=1e-10)


def test_crossmatch_all_classification_cases() -> None:
    """crossmatch classifies known_gaia_mover, gaia_source_inconsistent_pm, and no_gaia_counterpart."""
    # Gaia source 2001: high-PM mover moving from (269.452077, 4.693365) at 2016.0 to 2026.0
    # Gaia source 2002: nearly stationary background star at (133.7600, -7.2400)
    # Gaia source 2003: faint star lacking proper motion at (133.7800, -7.2500)
    gaia_rows = [
        {
            "source_id": 2001,
            "ra": 269.452077,
            "dec": 4.693365,
            "pmra": -801.551,
            "pmdec": 10362.394,
            "parallax": 546.98,
            "phot_g_mean_mag": 8.2,
            "ref_epoch": 2016.0,
        },
        {
            "source_id": 2002,
            "ra": 133.7600,
            "dec": -7.2400,
            "pmra": -2.0,
            "pmdec": 1.5,
            "parallax": 0.8,
            "phot_g_mean_mag": 15.1,
            "ref_epoch": 2016.0,
        },
        {
            "source_id": 2003,
            "ra": 133.7800,
            "dec": -7.2500,
            "pmra": None,
            "pmdec": None,
            "parallax": None,
            "phot_g_mean_mag": 19.4,
            "ref_epoch": 2016.0,
        },
    ]
    gaia_table = gaia._rows_to_table(gaia_rows)

    # Expected 2026.0 position of source 2001
    ra_2026, dec_2026 = coords.position_at_epoch(
        269.452077, 4.693365, -801.551, 10362.394, 2016.0, 2026.0
    )

    candidates = [
        # 1. Known Gaia mover (within 1 arcsec and within 3 sigma in PM)
        {
            "id": "cand_mover",
            "ra": ra_2026 + (0.4 / 3600.0),
            "dec": dec_2026,
            "pmra": -790.0,
            "pmdec": 10350.0,
            "pmra_sigma": 25.0,
            "pmdec_sigma": 25.0,
            "epoch": 2026.0,
        },
        # 2. Positional match with stationary Gaia star 2002, but candidate claims huge PM (8000 mas/yr)
        {
            "id": "cand_inconsistent",
            "ra": 133.7600,
            "dec": -7.2400,
            "pm_arcsec_per_yr": 8.0,
            "pm_sigma": 0.3,
            "epoch": 2026.0,
        },
        # 3. Positional match with Gaia star 2003 that has pm_missing=True
        {
            "id": "cand_missing_gaia_pm",
            "ra": 133.7800,
            "dec": -7.2500,
            "pm_arcsec_per_yr": 4.5,
            "pm_sigma": 0.4,
            "epoch": 2026.0,
        },
        # 4. Infrared-only mover (e.g. WISE 0855) with no Gaia counterpart within 5 arcsec
        {
            "id": "cand_wise0855",
            "ra": 133.7500,
            "dec": -7.2600,
            "pm_arcsec_per_yr": 8.15,
            "pm_sigma": 0.2,
            "epoch": 2026.0,
        },
    ]

    matches = gaia.crossmatch(candidates, gaia_table, radius_arcsec=5.0, epoch=2026.0)
    assert len(matches) == 4

    # Case 1: known_gaia_mover
    assert matches[0].classification == "known_gaia_mover"
    assert matches[0]["classification"] == "known_gaia_mover"
    assert matches[0].source_id == 2001
    assert matches[0].separation_arcsec is not None
    assert 0.35 <= matches[0].separation_arcsec <= 0.45
    assert matches[0].pm_diff_sigma is not None and matches[0].pm_diff_sigma < 3.0

    # Case 2: gaia_source_inconsistent_pm (PM differs by > 25 sigma)
    assert matches[1].classification == "gaia_source_inconsistent_pm"
    assert matches[1].source_id == 2002
    assert matches[1].separation_arcsec is not None and matches[1].separation_arcsec < 0.2
    assert matches[1].pm_diff_sigma is not None and matches[1].pm_diff_sigma > 3.0

    # Case 3: gaia_source_inconsistent_pm when Gaia has no PM measurement
    assert matches[2].classification == "gaia_source_inconsistent_pm"
    assert matches[2].source_id == 2003
    assert matches[2].pm_diff_sigma is None

    # Case 4: no_gaia_counterpart
    assert matches[3].classification == "no_gaia_counterpart"
    assert matches[3].source_id is None
    assert matches[3].separation_arcsec is None


def test_query_gaia_cache_hit_avoids_second_query_and_empty_is_valid(tmp_path: Path) -> None:
    """query_gaia caches results to disk, avoids a second TAP query on cache hit, and never invents rows."""
    rows = [
        {
            "source_id": 3002,
            "ra": 133.761,
            "dec": -7.241,
            "pmra": -12.4,
            "pmdec": 4.2,
            "parallax": 2.1,
            "phot_g_mean_mag": 16.4,
            "ref_epoch": 2016.0,
        },
        {
            "source_id": 3001,
            "ra": 133.760,
            "dec": -7.242,
            "pmra": 5.1,
            "pmdec": -3.8,
            "parallax": 3.5,
            "phot_g_mean_mag": 13.2,
            "ref_epoch": 2016.0,
        },
    ]
    fake_tap = FakeGaiaTAP(rows)

    # First query hits TAP and writes cache file
    first = gaia.query_gaia(
        133.76,
        -7.24,
        120.0,
        cache_root=tmp_path,
        mode="auto",
        tap_client=fake_tap,
    )
    assert len(fake_tap.queries) == 1
    assert "gaiadr3.gaia_source" in fake_tap.queries[0]
    for col in gaia.GAIA_COLUMNS:
        assert col in fake_tap.queries[0]
    # Sorted brightest first (source 3001 has G=13.2, source 3002 has G=16.4)
    assert [int(r["source_id"]) for r in first] == [3001, 3002]
    assert first.meta["data_source"] == "archive"

    # Second query at the same position & radius hits disk cache without calling TAP
    failing_tap = FakeGaiaTAP(error=RuntimeError("TAP should not be called on cache hit"))
    second = gaia.query_gaia(
        133.76,
        -7.24,
        120.0,
        cache_root=tmp_path,
        mode="auto",
        tap_client=failing_tap,
    )
    assert len(failing_tap.queries) == 0
    assert [int(r["source_id"]) for r in second] == [3001, 3002]
    assert second.meta["data_source"] == "cache"

    # Smaller radius (60 arcsec) at the same center also hits the covering 120" cache
    smaller = gaia.query_gaia(
        133.76,
        -7.24,
        60.0,
        cache_root=tmp_path,
        mode="cache",
        tap_client=failing_tap,
    )
    assert len(smaller) == 2
    assert smaller.meta["data_source"] == "cache"

    # Empty result from TAP is valid, cached, and never fabricates rows
    empty_tap = FakeGaiaTAP([])
    empty_tbl = gaia.query_gaia(
        10.0,
        20.0,
        60.0,
        cache_root=tmp_path,
        mode="auto",
        tap_client=empty_tap,
    )
    assert len(empty_tbl) == 0
    assert list(empty_tbl.colnames) == list(gaia.GAIA_COLUMNS)
    assert len(empty_tap.queries) == 1

    # Subsequent query for the empty field hits cache without re-querying TAP
    empty_cached = gaia.query_gaia(
        10.0,
        20.0,
        60.0,
        cache_root=tmp_path,
        mode="auto",
        tap_client=failing_tap,
    )
    assert len(empty_cached) == 0
    assert len(failing_tap.queries) == 0


def test_query_gaia_unreachable_and_timeout_and_cache_miss(tmp_path: Path) -> None:
    """query_gaia raises GaiaUnreachableError on TAP failure/timeout and GaiaNotCachedError in cache mode."""
    with pytest.raises(gaia.GaiaNotCachedError):
        gaia.query_gaia(45.0, -15.0, 60.0, cache_root=tmp_path, mode="cache")

    down_tap = FakeGaiaTAP(error=ConnectionError("ESA Gaia TAP connection reset"))
    with pytest.raises(gaia.GaiaUnreachableError, match="Gaia DR3 TAP service is unreachable"):
        gaia.query_gaia(
            45.0,
            -15.0,
            60.0,
            cache_root=tmp_path,
            mode="live",
            tap_client=down_tap,
        )

    release = threading.Event()

    class HangingTAP:
        def launch_job(self, query: str) -> Any:
            release.wait(timeout=5.0)
            raise AssertionError("Should have timed out")

    try:
        with pytest.raises(gaia.GaiaUnreachableError, match="timed out"):
            gaia.query_gaia(
                45.0,
                -15.0,
                60.0,
                cache_root=tmp_path,
                mode="live",
                timeout=0.05,
                tap_client=HangingTAP(),
            )
    finally:
        release.set()


def test_api_catalog_endpoint_contract_and_error_modes(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, frames
) -> None:
    """GET /api/catalog returns contract-compliant JSON, caps at 500 brightest, and handles 502/404."""
    rows = [
        {
            "source_id": 9001,
            "ra": 133.7600,
            "dec": -7.2400,
            "pmra": 120.0,
            "pmdec": -360.0,
            "parallax": 4.8,
            "phot_g_mean_mag": 14.2,
            "ref_epoch": 2016.0,
        },
        {
            "source_id": 9002,
            "ra": 133.7580,
            "dec": -7.2430,
            "pmra": None,
            "pmdec": None,
            "parallax": None,
            "phot_g_mean_mag": 18.9,
            "ref_epoch": 2016.0,
        },
    ]
    fake_tap = FakeGaiaTAP(rows)
    monkeypatch.setattr(gaia, "Gaia", fake_tap)
    def image_frames(ra, dec, *args, **kwargs):
        projected = []
        for original in frames:
            wcs = original.wcs.deepcopy()
            wcs.wcs.crval = [ra, dec]
            projected.append(original.copy_with(wcs=wcs))
        return tuple(projected)
    monkeypatch.setattr(data, "get_frames", image_frames)

    app_auto = backend.create_app(mode="auto", cache_root=tmp_path)
    with TestClient(app_auto, raise_server_exceptions=False) as client:
        resp = client.get("/api/catalog", params={"ra": "133.76", "dec": "-7.24", "fov": "120"})
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert body["source"] == "gaia_dr3"
        expected_epoch = gaia.Time(np.mean([f.mjd for f in frames]), format="mjd").jyear
        assert body["epoch"] == pytest.approx(expected_epoch, abs=1e-4)
        assert body["n_sources"] == 2
        assert len(body["sources"]) == 2
        assert body["sources"][0]["source_id"] == 9001
        assert body["sources"][0]["g_mag"] == pytest.approx(14.2)
        assert body["sources"][0]["pm_missing"] is False
        assert body["sources"][1]["source_id"] == 9002
        assert body["sources"][1]["pmra"] is None
        assert body["sources"][1]["pm_missing"] is True
        assert any("lack proper-motion" in w for w in body["warnings"])
        assert len(fake_tap.queries) == 1

    # In cache mode: cached position succeeds (200), uncached position returns 404 not_cached
    app_cache = backend.create_app(mode="cache", cache_root=tmp_path)
    with TestClient(app_cache, raise_server_exceptions=False) as client:
        hit_resp = client.get(
            "/api/catalog", params={"ra": "133.76", "dec": "-7.24", "fov": "120"}
        )
        assert hit_resp.status_code == 200
        assert hit_resp.json()["n_sources"] == 2

        miss_resp = client.get(
            "/api/catalog", params={"ra": "10.0", "dec": "10.0", "fov": "120"}
        )
        assert miss_resp.status_code == 404
        assert miss_resp.json()["error"]["code"] == "not_cached"

    # When Gaia TAP is unreachable on an uncached field: returns 502 archive_unreachable with no fallback
    monkeypatch.setattr(gaia, "Gaia", FakeGaiaTAP(error=OSError("Gaia TAP down")))
    with TestClient(app_auto, raise_server_exceptions=False) as client:
        err_resp = client.get(
            "/api/catalog", params={"ra": "25.0", "dec": "-10.0", "fov": "120"}
        )
        assert err_resp.status_code == 502
        err_body = err_resp.json()
        assert err_body["error"]["code"] == "archive_unreachable"
        assert "Gaia" in err_body["error"]["message"]


def test_fetch_gaia_cli_caches_demo_targets_and_writes_results(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """scripts/fetch_gaia.py caches demo targets and writes results/gaia_catalog.{json,md}."""
    test_targets = {
        "Barnard's Star": coords.OFFLINE_TARGETS["Barnard's Star"],
        "WISE 0855-0714": coords.OFFLINE_TARGETS["WISE 0855-0714"],
    }
    monkeypatch.setattr(coords, "OFFLINE_TARGETS", test_targets)

    # Mock TAP returning Barnard's Star at its 2016.0 position when queried near RA=269.45
    class DemoAwareTAP:
        def launch_job(self, query: str) -> FakeTAPJob:
            if "269.4" in query:
                b_ra, b_dec = coords.position_at_epoch(
                    269.452077, 4.693365, -801.551, 10362.394, 2000.0, 2016.0
                )
                return FakeTAPJob(
                    gaia._rows_to_table(
                        [
                            {
                                "source_id": 4472832130942575872,
                                "ra": b_ra,
                                "dec": b_dec,
                                "pmra": -801.551,
                                "pmdec": 10362.394,
                                "parallax": 546.9759,
                                "phot_g_mean_mag": 8.19,
                                "ref_epoch": 2016.0,
                            }
                        ]
                    )
                )
            # WISE 0855 field has background stars only (WISE 0855 itself is invisible in G band)
            return FakeTAPJob(
                gaia._rows_to_table(
                    [
                        {
                            "source_id": 5751234567890,
                            "ra": 133.7700,
                            "dec": -7.2300,
                            "pmra": -3.1,
                            "pmdec": 1.2,
                            "parallax": 1.1,
                            "phot_g_mean_mag": 15.6,
                            "ref_epoch": 2016.0,
                        }
                    ]
                )
            )

    monkeypatch.setattr(gaia, "Gaia", DemoAwareTAP())
    cache_dir = tmp_path / "cache"
    results_dir = tmp_path / "results"

    rc = fetch_gaia.main(
        [
            "--cache-dir",
            str(cache_dir),
            "--results-dir",
            str(results_dir),
            "--mode",
            "live",
        ]
    )
    assert rc == 0
    report = json.loads((results_dir / "gaia_catalog.json").read_text(encoding="utf-8"))
    assert report["verdict"] == "PASS"
    assert len(report["fields"]) == 2
    by_name = {f["target"]: f for f in report["fields"]}
    assert by_name["Barnard's Star"]["target_crossmatch"]["classification"] == "known_gaia_mover"
    assert by_name["WISE 0855-0714"]["target_crossmatch"]["classification"] == "no_gaia_counterpart"
