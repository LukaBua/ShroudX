from __future__ import annotations

import math
import pytest
from astropy.coordinates import SkyCoord
from astropy.coordinates.name_resolve import NameResolveError

from coords import (
    OFFLINE_TARGETS,
    high_pm_hint,
    parse_position,
    position_at_epoch,
    resolve_name,
)


def test_parse_position_wise_0855_formats_match() -> None:
    """Decimal and both sexagesimal formats yield identical coordinates for WISE 0855."""
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
    """Offline lookup succeeds for all 5 demo targets when network is disabled."""
    def _fail_network(name: str) -> SkyCoord:
        raise NameResolveError(f"Network disabled for test: {name}")

    monkeypatch.setattr(SkyCoord, "from_name", _fail_network)

    # High-PM target: WISE 0855-0714 propagated from 2000.0 to 2026.0
    wise = resolve_name("WISE 0855-0714", target_epoch=2026.0)
    assert wise.source == "offline"
    assert wise.pm_known is True
    assert math.isclose(wise.ra, 133.759765, abs_tol=1e-5)
    assert math.isclose(wise.dec, -7.242186, abs_tol=1e-5)
    assert any("propagated" in w for w in wise.warnings)

    # Low-PM target: RW Aurigae stays at J2000 reference position
    rw_aur = resolve_name("RW Aurigae", target_epoch=2026.0)
    assert rw_aur.source == "offline"
    assert rw_aur.pm_known is True
    assert math.isclose(rw_aur.ra, 76.956526, abs_tol=1e-6)
    assert math.isclose(rw_aur.dec, 30.401438, abs_tol=1e-6)
    assert len(rw_aur.warnings) == 0

    # Ensure all 5 canonical targets are populated and resolve cleanly offline
    for target_name in OFFLINE_TARGETS:
        res = resolve_name(target_name, target_epoch=2026.0)
        assert res.source == "offline"
        assert res.pm_known is True


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