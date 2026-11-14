"""Tests for coords.py with all network calls mocked."""

from __future__ import annotations

import math
import pytest
from astropy.coordinates import SkyCoord
from astropy.coordinates.name_resolve import NameResolveError

import coords
from coords import (
    OfflineEntry,
    high_pm_hint,
    parse_position,
    position_at_epoch,
    resolve_name,
)


def test_parse_position_wise_0855_formats_match() -> None:
    """Decimal and both sexagesimal formats yield identical coordinates for WISE 0855."""
    # Exact conversion of 08h55m10.831s, -07d14m42.53s:
    # RA = (8 + 55/60 + 10.831/3600) * 15 = 133.79512916666667 deg
    # Dec = -(7 + 14/60 + 42.53/3600) = -7.2451472222222225 deg
    expected_ra = (8.0 + 55.0 / 60.0 + 10.831 / 3600.0) * 15.0
    expected_dec = -(7.0 + 14.0 / 60.0 + 42.53 / 3600.0)

    ra_dec, dec_dec = parse_position(expected_ra, expected_dec)
    ra_hms, dec_dms = parse_position("08h55m10.831s", "-07d14m42.53s")
    ra_space, dec_space = parse_position("08 55 10.831", "-07 14 42.53")

    assert math.isclose(ra_dec, expected_ra, abs_tol=1e-9)
    assert math.isclose(dec_dec, expected_dec, abs_tol=1e-9)
    assert math.isclose(ra_hms, expected_ra, abs_tol=1e-9)
    assert math.isclose(dec_dms, expected_dec, abs_tol=1e-9)
    assert math.isclose(ra_space, expected_ra, abs_tol=1e-9)
    assert math.isclose(dec_space, expected_dec, abs_tol=1e-9)


@pytest.mark.parametrize(
    "bad_ra, bad_dec",
    [
        (-0.001, 0.0),
        (360.0, 0.0),
        (400.0, 10.0),
        (120.0, -90.001),
        (120.0, 90.001),
        ("24h00m00s", "00d00m00s"),
        ("12h00m00s", "-95d00m00s"),
        ("", "0.0"),
        ("not_a_coord", "10.0"),
    ],
)
def test_parse_position_out_of_range_and_invalid_raises(
    bad_ra: float | str, bad_dec: float | str
) -> None:
    """Out-of-range or malformed RA/Dec inputs raise ValueError."""
    with pytest.raises(ValueError):
        parse_position(bad_ra, bad_dec)


def test_position_at_epoch_matches_hand_computed() -> None:
    """Proper-motion propagation matches hand calculation including cos(dec) factor."""
    # Hand-computed setup:
    # Initial: RA = 120.0 deg, Dec = 60.0 deg (so cos(Dec) = 0.5)
    # Proper motion: pmra = +3600.0 mas/yr (+0.001 deg/yr sky motion),
    #                pmdec = -3600.0 mas/yr (-0.001 deg/yr)
    # Time baseline: ref_epoch = 2016.0, target_epoch = 2026.0 -> dt = 10.0 yr
    # Expected d_dec = -0.001 * 10 = -0.01 deg -> new_dec = 59.99 deg
    # Expected d_ra  = (+0.001 * 10) / cos(60 deg) = +0.01 / 0.5 = +0.02 deg -> new_ra = 120.02 deg
    new_ra, new_dec = position_at_epoch(
        ra=120.0,
        dec=60.0,
        pmra=3600.0,
        pmdec=-3600.0,
        ref_epoch=2016.0,
        target_epoch=2026.0,
    )
    assert math.isclose(new_ra, 120.02, abs_tol=1e-9)
    assert math.isclose(new_dec, 59.99, abs_tol=1e-9)


def test_offline_lookup_works_with_network_disabled(monkeypatch: pytest.MonkeyPatch) -> None:
    """Offline lookup succeeds when network is disabled and propagates high PM."""
    def _fail_network(name: str) -> SkyCoord:
        raise NameResolveError(f"Network disabled for test: {name}")

    monkeypatch.setattr(SkyCoord, "from_name", _fail_network)

    # Inject a test offline table with populated values to test offline resolution & propagation
    # without hardcoding fabricated numbers in the production module.
    mock_table = {
        "WISE 0855-0714": OfflineEntry(
            ra=120.0,
            dec=60.0,
            pmra=3600.0,   # > 1000 mas/yr threshold
            pmdec=-3600.0,
            ref_epoch=2016.0,
            target_class="cold brown dwarf",
        )
    }

    resolved = resolve_name(
        "WISE 0855-0714",
        target_epoch=2026.0,
        offline_table=mock_table,
    )
    assert resolved.source == "offline"
    assert resolved.pm_known is True
    assert math.isclose(resolved.ra, 120.02, abs_tol=1e-9)
    assert math.isclose(resolved.dec, 59.99, abs_tol=1e-9)
    assert any("propagated" in w for w in resolved.warnings)


def test_unknown_name_raises_clear_error(monkeypatch: pytest.MonkeyPatch) -> None:
    """Unknown object name raises a clear ValueError when Sesame fails."""
    def _fail_network(name: str) -> SkyCoord:
        raise NameResolveError(f"Unable to find coordinates for '{name}'")

    monkeypatch.setattr(SkyCoord, "from_name", _fail_network)

    with pytest.raises(ValueError, match="Could not resolve target name 'NonExistentObjectXYZ'"):
        resolve_name("NonExistentObjectXYZ")


def test_high_pm_hint() -> None:
    """high_pm_hint returns a warning for small FOVs and None for large FOVs."""
    assert high_pm_hint(120.0) is not None
    assert high_pm_hint(360.0) is None
    with pytest.raises(ValueError):
        high_pm_hint(-10.0)
