from __future__ import annotations

import math
import re
import warnings
from dataclasses import dataclass, field
from typing import Optional

from astropy import units as u
from astropy.coordinates import Angle, SkyCoord
from astropy.coordinates.name_resolve import NameResolveError

DEFAULT_SURVEY_EPOCH: float = 2026.0
HIGH_PM_THRESHOLD_MAS_YR: float = 1000.0  # 1 arcsec / yr = 1000 mas / yr

# Heuristic mapping of target astrophysical classes to SPHEREx detectors (1-6).
# Used strictly as an initial UI suggestion that the user can override.
TARGET_PROFILES: dict[str, int] = {
    "cold brown dwarf": 5,     # 3.83 - 4.41 um (CH4 / CO window, peak flux for Y/late-T)
    "m dwarf": 2,              # 1.10 - 1.62 um (near-IR J/H stellar continuum peak)
    "t tauri star": 3,         # 1.63 - 2.41 um (K-band inner disk excess & veil)
    "agb / mira variable": 4,  # 2.42 - 3.82 um (molecular bands & warm dust)
    "solar system body": 1,    # 0.75 - 1.09 um (reflected solar continuum)
}


@dataclass(frozen=True)
class OfflineEntry:
    """Catalog parameters for offline target fallback.

    Attributes:
        ra: ICRS Right Ascension in decimal degrees at ref_epoch.
        dec: ICRS Declination in decimal degrees at ref_epoch.
        pmra: Proper motion in RA (mu_alpha * cos(dec)) in mas/yr.
        pmdec: Proper motion in Dec (mu_delta) in mas/yr.
        ref_epoch: Reference epoch as a decimal Julian year (e.g., 2000.0).
        target_class: Optional heuristic key in TARGET_PROFILES.
        citation: ADS bibcode citation for position and proper motion.
    """

    ra: Optional[float]
    dec: Optional[float]
    pmra: Optional[float]
    pmdec: Optional[float]
    ref_epoch: Optional[float]
    target_class: Optional[str] = None
    citation: Optional[str] = None


# Literature values populated from Demo targets data .xlsx (Gaia EDR3, Kirkpatrick+2021, van Leeuwen 2007).
OFFLINE_TARGETS: dict[str, OfflineEntry] = {
    "WISE 0855-0714": OfflineEntry(
        ra=133.818908,
        dec=-7.247048,
        pmra=-8123.700,
        pmdec=673.200,
        ref_epoch=2000.0,
        target_class="cold brown dwarf",
        citation="2021ApJS..253....7K",
    ),
    "Barnard's Star": OfflineEntry(
        ra=269.452077,
        dec=4.693365,
        pmra=-801.551,
        pmdec=10362.394,
        ref_epoch=2000.0,
        target_class="m dwarf",
        citation="2020yCat.1350....0G",
    ),
    "Epsilon Indi B": OfflineEntry(
        ra=331.044147,
        dec=-56.782844,
        pmra=3981.977,
        pmdec=-2466.832,
        ref_epoch=2000.0,
        target_class="cold brown dwarf",
        citation="2020yCat.1350....0G",
    ),
    "RW Aurigae": OfflineEntry(
        ra=76.956526,
        dec=30.401438,
        pmra=7.230,
        pmdec=-20.330,
        ref_epoch=2000.0,
        target_class="t tauri star",
        citation="2007A&A...474..653V",
    ),
    "R Draconis": OfflineEntry(
        ra=248.167611,
        dec=66.754979,
        pmra=-20.643,
        pmdec=6.149,
        ref_epoch=2000.0,
        target_class="agb / mira variable",
        citation="2020yCat.1350....0G",
    ),
}

# Normalization lookup mapping lowercase stripped aliases to canonical OFFLINE_TARGETS keys.
_OFFLINE_ALIASES: dict[str, str] = {
    "wise 0855-0714": "WISE 0855-0714",
    "wise j085510.83-071442.5": "WISE 0855-0714",
    "wise 0855": "WISE 0855-0714",
    "barnard's star": "Barnard's Star",
    "barnards star": "Barnard's Star",
    "barnard star": "Barnard's Star",
    "epsilon indi b": "Epsilon Indi B",
    "eps ind b": "Epsilon Indi B",
    "rw aurigae": "RW Aurigae",
    "rw aur": "RW Aurigae",
    "r draconis": "R Draconis",
    "r dra": "R Draconis",
}


@dataclass(frozen=True)
class ResolvedTarget:
    """Result of resolving a target name to sky coordinates.

    Attributes:
        ra: ICRS Right Ascension in decimal degrees (propagated if high PM is known).
        dec: ICRS Declination in decimal degrees (propagated if high PM is known).
        source: Provenance string (e.g., 'offline', 'sesame').
        pm_known: Whether proper motion parameters were available for this target.
        warnings: User-facing warning messages (e.g., epoch propagation notices).
    """

    ra: float
    dec: float
    source: str
    pm_known: bool
    warnings: list[str] = field(default_factory=list)


_NUMERIC_RE = re.compile(r"^[+-]?(\d+(\.\d*)?|\.\d+)([eE][+-]?\d+)?$")


def _is_plain_number(val: str) -> bool:
    """Return True if the string represents a plain decimal/scientific number."""
    return bool(_NUMERIC_RE.match(val.strip()))


def parse_position(ra: float | int | str, dec: float | int | str) -> tuple[float, float]:
    """Parse RA and Dec into decimal degrees (ICRS).

    Accepts decimal degrees (float, int, or numeric string) or sexagesimal strings
    in either unit format ('08h55m10.831s', '-07d14m42.53s') or space-separated
    format ('08 55 10.831', '-07 14 42.53'). Bare sexagesimal RA strings are
    interpreted as hours.

    Args:
        ra: Right Ascension in decimal degrees or sexagesimal hours/degrees.
        dec: Declination in decimal degrees or sexagesimal degrees.

    Returns:
        Tuple of (ra_deg, dec_deg) in decimal degrees.

    Raises:
        ValueError: If inputs cannot be parsed or lie outside 0 <= RA < 360
            or -90 <= Dec <= 90.
    """
    ra_deg = _parse_ra(ra)
    dec_deg = _parse_dec(dec)
    return ra_deg, dec_deg


def _parse_ra(ra: float | int | str) -> float:
    if isinstance(ra, (int, float)) and not isinstance(ra, bool):
        ra_deg = float(ra)
    elif isinstance(ra, str):
        s = ra.strip()
        if not s:
            raise ValueError("RA input cannot be empty.")
        if _is_plain_number(s):
            ra_deg = float(s)
        else:
            try:
                with warnings.catch_warnings(record=True) as caught:
                    warnings.simplefilter("always")
                    if any(marker in s.lower() for marker in ("d", "deg", "°")):
                        angle = Angle(s)
                    else:
                        angle = Angle(s, unit=u.hourangle)
                    if caught:
                        raise ValueError(str(caught[0].message))
                ra_deg = float(angle.to(u.deg).value)
            except Exception as exc:
                raise ValueError(
                    f"Could not parse RA '{ra}' as decimal degrees or sexagesimal hours (0 <= RA < 24h)."
                ) from exc
    else:
        raise ValueError(f"Unsupported type for RA: {type(ra).__name__}")

    if not math.isfinite(ra_deg) or not (0.0 <= ra_deg < 360.0):
        raise ValueError(f"RA must satisfy 0 <= RA < 360 degrees (got {ra_deg}).")
    return ra_deg


def _parse_dec(dec: float | int | str) -> float:
    if isinstance(dec, (int, float)) and not isinstance(dec, bool):
        dec_deg = float(dec)
    elif isinstance(dec, str):
        s = dec.strip()
        if not s:
            raise ValueError("Dec input cannot be empty.")
        if _is_plain_number(s):
            dec_deg = float(s)
        else:
            try:
                with warnings.catch_warnings(record=True) as caught:
                    warnings.simplefilter("always")
                    angle = Angle(s, unit=u.deg)
                    if caught:
                        raise ValueError(str(caught[0].message))
                dec_deg = float(angle.to(u.deg).value)
            except Exception as exc:
                raise ValueError(
                    f"Could not parse Dec '{dec}' as decimal or sexagesimal degrees (-90 <= Dec <= 90)."
                ) from exc
    else:
        raise ValueError(f"Unsupported type for Dec: {type(dec).__name__}")

    if not math.isfinite(dec_deg) or not (-90.0 <= dec_deg <= 90.0):
        raise ValueError(f"Dec must satisfy -90 <= Dec <= 90 degrees (got {dec_deg}).")
    return dec_deg


def position_at_epoch(
    ra: float,
    dec: float,
    pmra: float,
    pmdec: float,
    ref_epoch: float,
    target_epoch: float = DEFAULT_SURVEY_EPOCH,
) -> tuple[float, float]:
    """Propagate ICRS coordinates linearly from ref_epoch to target_epoch.

    Uses the standard small-angle linear approximation where pmra represents
    mu_alpha * cos(dec) in milliarcseconds per year (mas/yr) and pmdec represents
    mu_delta in mas/yr.

    Args:
        ra: Right Ascension at ref_epoch in decimal degrees [0, 360).
        dec: Declination at ref_epoch in decimal degrees [-90, 90].
        pmra: Proper motion in RA (mu_alpha * cos(dec)) in mas/yr.
        pmdec: Proper motion in Dec in mas/yr.
        ref_epoch: Reference epoch in decimal Julian years (e.g., 2000.0).
        target_epoch: Target epoch in decimal Julian years (default 2026.0).

    Returns:
        Tuple of (new_ra_deg, new_dec_deg) in decimal degrees.

    Raises:
        ValueError: If initial coordinates are invalid, at the exact celestial pole
            with non-zero RA proper motion, or if propagation crosses the pole.
    """
    ra_deg, dec_deg = parse_position(ra, dec)
    dt = float(target_epoch) - float(ref_epoch)
    if dt == 0.0:
        return ra_deg, dec_deg

    # Convert mas/yr to deg/yr: 1 deg = 3,600,000 mas
    d_dec_deg = (float(pmdec) / 3_600_000.0) * dt
    new_dec = dec_deg + d_dec_deg
    if not (-90.0 <= new_dec <= 90.0):
        raise ValueError(
            f"Proper motion propagation moved Declination out of bounds: {new_dec:.6f} deg."
        )

    cos_dec = math.cos(math.radians(dec_deg))
    if abs(cos_dec) < 1e-12:
        if pmra != 0.0:
            raise ValueError("Cannot apply linear RA proper motion at the celestial pole.")
        d_ra_deg = 0.0
    else:
        d_ra_deg = (float(pmra) / 3_600_000.0) * dt / cos_dec

    new_ra = (ra_deg + d_ra_deg) % 360.0
    return new_ra, new_dec


def _resolve_from_offline_entry(
    canonical_name: str,
    entry: OfflineEntry,
    target_epoch: float,
) -> ResolvedTarget:
    """Build a ResolvedTarget from an OfflineEntry, propagating high proper motion."""
    if entry.ra is None or entry.dec is None:
        raise ValueError(
            f"Offline target '{canonical_name}' has unpopulated coordinates (None). "
            "Please populate OFFLINE_TARGETS with literature values."
        )

    ra = float(entry.ra)
    dec = float(entry.dec)
    warnings_list: list[str] = []
    pm_known = (
        entry.pmra is not None
        and entry.pmdec is not None
        and entry.ref_epoch is not None
    )

    if pm_known:
        assert entry.pmra is not None
        assert entry.pmdec is not None
        assert entry.ref_epoch is not None
        total_pm_mas_yr = math.hypot(entry.pmra, entry.pmdec)
        if total_pm_mas_yr > HIGH_PM_THRESHOLD_MAS_YR:
            ra, dec = position_at_epoch(
                ra=ra,
                dec=dec,
                pmra=entry.pmra,
                pmdec=entry.pmdec,
                ref_epoch=entry.ref_epoch,
                target_epoch=target_epoch,
            )
            total_pm_arcsec = total_pm_mas_yr / 1000.0
            cite_note = f" (source: {entry.citation})" if entry.citation else ""
            warnings_list.append(
                f"High proper-motion target ({total_pm_arcsec:.2f} arcsec/yr): "
                f"position propagated from epoch {entry.ref_epoch:.1f} to SPHEREx "
                f"epoch {target_epoch:.1f}{cite_note}."
            )
    else:
        warnings_list.append(
            f"Proper motion parameters are not set for '{canonical_name}'; "
            "using catalog reference coordinates without epoch propagation."
        )

    return ResolvedTarget(
        ra=ra,
        dec=dec,
        source="offline",
        pm_known=pm_known,
        warnings=warnings_list,
    )


def resolve_name(
    name: str,
    target_epoch: float = DEFAULT_SURVEY_EPOCH,
    offline_table: Optional[dict[str, OfflineEntry]] = None,
) -> ResolvedTarget:
    """Resolve an astronomical object name to ICRS coordinates.

    Checks the offline lookup table first if the target is a known benchmark
    with populated coordinates and proper motion, and otherwise queries Sesame
    (SIMBAD/CDS) via SkyCoord.from_name.

    Args:
        name: Object identifier (e.g., 'WISE 0855-0714', "Barnard's Star").
        target_epoch: Target epoch in decimal Julian years for high-PM propagation.
        offline_table: Optional override dict for OFFLINE_TARGETS (useful in tests).

    Returns:
        ResolvedTarget containing coordinates, provenance, PM status, and warnings.

    Raises:
        ValueError: If name is empty, cannot be resolved online or offline, or
            resolves to an unpopulated offline entry when network fails.
    """
    cleaned = name.strip()
    if not cleaned:
        raise ValueError("Target name cannot be empty.")

    table = offline_table if offline_table is not None else OFFLINE_TARGETS
    canonical_key = _OFFLINE_ALIASES.get(cleaned.lower())
    if canonical_key is None and cleaned in table:
        canonical_key = cleaned

    offline_entry = table.get(canonical_key) if canonical_key else None
    if offline_entry is not None and offline_entry.ra is not None and offline_entry.dec is not None:
        return _resolve_from_offline_entry(canonical_key or cleaned, offline_entry, target_epoch)

    try:
        coord = SkyCoord.from_name(cleaned)
        ra_deg = float(coord.icrs.ra.deg)
        dec_deg = float(coord.icrs.dec.deg)
        warnings_list = [
            "Resolved via online Sesame lookup (coordinates only; proper motion not "
            "automatically propagated). Verify field of view for fast-moving objects."
        ]
        return ResolvedTarget(
            ra=ra_deg,
            dec=dec_deg,
            source="sesame",
            pm_known=False,
            warnings=warnings_list,
        )
    except (NameResolveError, Exception) as net_exc:
        if offline_entry is not None:
            return _resolve_from_offline_entry(canonical_key or cleaned, offline_entry, target_epoch)
        raise ValueError(
            f"Could not resolve target name '{cleaned}' via SIMBAD/CDS or offline table."
        ) from net_exc


def high_pm_hint(fov_arcsec: float, min_recommended_fov_arcsec: float = 300.0) -> Optional[str]:
    """Return a user-facing hint if the cutout field of view may clip fast movers.

    High proper-motion targets (like WISE 0855 or Barnard's Star) can shift by
    several arcminutes between catalog reference epochs (e.g. J2000) and the
    2026 SPHEREx survey epoch.

    Args:
        fov_arcsec: Currently requested cutout width/height in arcseconds.
        min_recommended_fov_arcsec: Threshold below which a warning hint is returned.

    Returns:
        Warning string if fov_arcsec < min_recommended_fov_arcsec, else None.

    Raises:
        ValueError: If fov_arcsec <= 0.
    """
    if fov_arcsec <= 0:
        raise ValueError(f"Field of view must be positive (got {fov_arcsec} arcsec).")
    if fov_arcsec < min_recommended_fov_arcsec:
        return (
            f"Requested FOV ({fov_arcsec:.0f} arcsec) is relatively small for raw catalog "
            f"coordinates. Fast-moving targets (>1 arcsec/yr) can move several arcminutes "
            f"since J2000; consider increasing FOV to >= {min_recommended_fov_arcsec:.0f} arcsec "
            "or resolving by name to apply proper-motion propagation."
        )
    return None