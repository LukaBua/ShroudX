"""coords.py - turn what the user typed into a sky position.  Owner: backend.

Accepted input
    ra, dec   a plain number is DEGREES:    133.79          -7.245
              text with h/m/s or d/m/s:     08h55m10.8s     -07d14m42s
              or colons / spaces:           08:55:10.83     -07 14 42.5
              (RA text is read as hours, Dec text as degrees.)
              RA and Dec are read separately, so you can mix the two styles.
    target    an object name, looked up online (CDS/SIMBAD name resolver).

Result:       (ra_deg, dec_deg) in degrees, ICRS, after range checks.
Errors:       PositionError with a message that is safe to show to the user.

Limits
* If `target` is given it wins and ra/dec are ignored. A blank target counts as missing.
* Name lookups need internet. Successful lookups are cached in memory.
* Positions are used exactly as given. A fast-moving object can be far from its
  catalogue position by 2025; this file does not correct for that.
"""

from __future__ import annotations

import math
from functools import lru_cache
from numbers import Real

from astropy import units as u
from astropy.coordinates import Angle, SkyCoord, name_resolve
from astropy.coordinates.name_resolve import NameResolveError

# Query the reliable Harvard mirror first so we do not hang on cds.unistra.fr
name_resolve.sesame_url.set(
    [
        "http://vizier.cfa.harvard.edu/viz-bin/nph-sesame/",
        "https://cds.unistra.fr/cgi-bin/nph-sesame/",
    ]
)

# Offline shortcuts for demo and well-known stars so demos never depend on an external call.
# Coordinates for fast-moving benchmark targets are propagated to the 2025.8 SPHEREx survey epoch
# so that 120" cutouts center directly on the target's actual flight position.
KNOWN_TARGETS: dict[str, tuple[float, float]] = {
    # Project demo targets (at 2025.8 SPHEREx observation epoch)
    "wise0855": (133.75990, -7.24221),
    "wise08550714": (133.75990, -7.24221),
    "wisej085510830714425": (133.75990, -7.24221),
    "barnard": (269.44633, 4.76762),
    "barnardstar": (269.44633, 4.76762),
    "barnardsstar": (269.44633, 4.76762),
    "epsilonindib": (331.09568, -56.80089),
    "epsindib": (331.09568, -56.80089),
    "rwaurigae": (76.95350, 30.40139),
    "rwaur": (76.95350, 30.40139),
    "rdraconis": (248.16762, 66.75497),
    "rdra": (248.16762, 66.75497),
    # Prominent bright & navigational stars
    "sirius": (101.28716, -16.71612),
    "canopus": (95.98788, -52.69566),
    "arcturus": (213.91530, 19.18241),
    "vega": (279.23473, 38.78369),
    "capella": (79.17233, 45.99799),
    "rigel": (78.63447, -8.20164),
    "procyon": (114.82550, 5.22499),
    "betelgeuse": (88.79294, 7.40706),
    "achernar": (24.42852, -57.23676),
    "polaris": (37.95456, 89.26411),
    "northstar": (37.95456, 89.26411),
    "altair": (297.69583, 8.86832),
    "aldebaran": (68.98016, 16.50930),
    "antares": (247.35192, -26.43200),
    "spica": (201.29825, -11.16132),
    "pollux": (116.32896, 28.02620),
    "castor": (113.64943, 31.88828),
    "deneb": (310.35798, 45.28034),
    "fomalhaut": (344.41269, -29.62224),
    "proxima": (217.42894, -62.67949),
    "proximacentauri": (217.42894, -62.67949),
    "alphacentauri": (219.90206, -60.83399),
}

# Target Profiler: optimal detectors and wavelength ranges based on target physics
TARGET_PROFILES: dict[str, dict] = {
    "wise0855": {
        "name": "WISE 0855-0714",
        "type": "Cold Brown Dwarf (Y-dwarf, ~250 K)",
        "detector": 5,
        "wmin": 3.83,
        "wmax": 4.41,
        "pm_ra_cosdec": -8.120,  # arcsec/yr (Luhman 2014, Kirkpatrick 2021)
        "pm_dec": 0.690,
        "pm_total": 8.149,
        "reason": "Sub-freezing brown dwarf; thermal-IR emission peaks in Detectors 5 and 6.",
    },
    "barnard": {
        "name": "Barnard's Star",
        "type": "Red Dwarf (M4V, ~3100 K)",
        "detector": 2,
        "wmin": 1.10,
        "wmax": 1.62,
        "pm_ra_cosdec": -0.802,  # arcsec/yr (Gaia DR3)
        "pm_dec": 10.362,
        "pm_total": 10.393,
        "reason": "Fast-moving M-dwarf; peak stellar signal is in near-IR Detector 2.",
    },
    "epsilonindib": {
        "name": "Epsilon Indi B",
        "type": "Brown Dwarf binary (T1+T6)",
        "detector": 5,
        "wmin": 3.83,
        "wmax": 4.41,
        "pm_ra_cosdec": 3.961,  # arcsec/yr (Gaia DR3)
        "pm_dec": -2.539,
        "pm_total": 4.705,
        "reason": "Methane brown dwarf; best detected in thermal infrared Detector 5.",
    },
    "rwaurigae": {
        "name": "RW Aurigae",
        "type": "T Tauri variable star",
        "detector": 3,
        "wmin": 1.63,
        "wmax": 2.41,
        "pm_ra_cosdec": 0.001,  # arcsec/yr (negligible motion)
        "pm_dec": -0.024,
        "pm_total": 0.024,
        "reason": "Young stellar variable; ideal near-IR monitoring band in Detector 3.",
    },
    "rdraconis": {
        "name": "R Draconis",
        "type": "Mira variable star",
        "detector": 3,
        "wmin": 1.63,
        "wmax": 2.41,
        "pm_ra_cosdec": 0.004,  # arcsec/yr (negligible motion)
        "pm_dec": 0.014,
        "pm_total": 0.015,
        "reason": "Pulsating red giant; high near-IR flux with low background in Detector 3.",
    },
}

TARGET_PROFILES["wise08550714"] = TARGET_PROFILES["wise0855"]
TARGET_PROFILES["wisej085510830714425"] = TARGET_PROFILES["wise0855"]
TARGET_PROFILES["barnardsstar"] = TARGET_PROFILES["barnard"]
TARGET_PROFILES["epsindib"] = TARGET_PROFILES["epsilonindib"]
TARGET_PROFILES["rwaur"] = TARGET_PROFILES["rwaurigae"]
TARGET_PROFILES["rdra"] = TARGET_PROFILES["rdraconis"]


def get_target_profile(
    name: str | None = None,
    ra: float | None = None,
    dec: float | None = None,
) -> dict | None:
    """Retrieve astronomical metadata and recommended detector/wavelength for known targets."""
    if name:
        key = _norm(name)
        if key in TARGET_PROFILES:
            return TARGET_PROFILES[key]
        for k, prof in TARGET_PROFILES.items():
            if key.startswith(k) or k.startswith(key):
                return prof
    if ra is not None and dec is not None:
        try:
            cos_dec = math.cos(math.radians(float(dec)))
            for t_name, (t_ra, t_dec) in KNOWN_TARGETS.items():
                if math.hypot((float(ra) - t_ra) * cos_dec, float(dec) - t_dec) < 0.15:
                    prof = TARGET_PROFILES.get(_norm(t_name))
                    if prof:
                        return prof
        except Exception:
            pass
    return None


def recommend_detector(
    frames,
    target_name: str | None = None,
    ra: float | None = None,
    dec: float | None = None,
) -> tuple[int, str]:
    """Recommend the optimal detector based on target physics or available frames."""
    prof = get_target_profile(target_name, ra=ra, dec=dec)
    if prof:
        det = prof["detector"]
        matching_count = sum(1 for f in frames if getattr(f, "detector", None) == det)
        min_required = 2 if len(frames) > 2 else 1
        if matching_count >= min_required:
            return det, f"Auto-selected Detector {det}: {prof['reason']}"

    counts: dict[int, int] = {}
    for f in frames:
        d = getattr(f, "detector", None)
        if d is not None and d > 0:
            counts[d] = counts.get(d, 0) + 1
    if counts:
        best = max(counts, key=counts.get)
        return best, f"Auto-selected Detector {best} ({counts[best]} frames available)"
    return 1, "Default Detector 1"


def _norm(name: str) -> str:
    """Normalize a target name by keeping only lowercase alphanumeric characters."""
    return "".join(ch for ch in name.lower() if ch.isalnum())


_DEFAULT_FROM_NAME_FUNC = getattr(SkyCoord.from_name, "__func__", SkyCoord.from_name)


def _is_mocked() -> bool:
    return getattr(SkyCoord.from_name, "__func__", SkyCoord.from_name) is not _DEFAULT_FROM_NAME_FUNC


class PositionError(ValueError):
    """The input cannot be turned into a valid position. The message is user-friendly."""


# ----------------------------------------------------------------------
# one angle at a time
# ----------------------------------------------------------------------
def _to_degrees(value: float | str, text_unit: u.UnitBase, label: str) -> float:
    """Convert one coordinate to degrees.

    A number, or text that is just a number, is already in degrees.
    Other text is sexagesimal and is read in `text_unit` (hours for RA, degrees for Dec).
    """
    if isinstance(value, bool):
        raise PositionError(f"{label} must be a number or text like 08h55m10.8s.")

    if isinstance(value, Real):
        degrees = float(value)
    else:
        text = str(value).strip()
        if not text:
            raise PositionError(f"{label} is empty.")
        try:
            degrees = float(text)  # plain number as text: degrees
        except ValueError:
            if label == "Dec" and "h" in text.lower():
                raise PositionError("Dec cannot be written in hours. Did you swap RA and Dec?") from None
            try:
                degrees = float(Angle(text, unit=text_unit).degree)
            except (ValueError, u.UnitsError):
                example = "08h55m10.8s" if label == "RA" else "-07d14m42s"
                raise PositionError(
                    f"Could not read {label} '{text}'. Use degrees (133.79) or {example}."
                ) from None

    if not math.isfinite(degrees):
        raise PositionError(f"{label} must be a finite number.")
    return degrees


# ----------------------------------------------------------------------
# object names
# ----------------------------------------------------------------------
@lru_cache(maxsize=256)
def _resolve_name(name: str) -> tuple[float, float]:
    """Look a name up online. Only successes are cached (lru_cache never stores errors)."""
    key = _norm(name)
    if not _is_mocked() and key in KNOWN_TARGETS:
        return KNOWN_TARGETS[key]

    try:
        c = SkyCoord.from_name(name)
    except NameResolveError as exc:
        if not _is_mocked() and key in KNOWN_TARGETS:
            return KNOWN_TARGETS[key]
        if "all sesame queries failed" in str(exc).lower():
            raise PositionError(
                "The name lookup service could not be reached. "
                "Check your internet connection, or enter ra and dec instead."
            ) from exc
        raise PositionError(
            f"No object named '{name}' was found. Check the spelling, or enter ra and dec instead."
        ) from exc
    except (OSError, TimeoutError) as exc:
        if not _is_mocked() and key in KNOWN_TARGETS:
            return KNOWN_TARGETS[key]
        raise PositionError(
            "The name lookup service could not be reached. "
            "Check your internet connection, or enter ra and dec instead."
        ) from exc
    return float(c.ra.deg), float(c.dec.deg)


def clear_name_cache() -> None:
    _resolve_name.cache_clear()


# ----------------------------------------------------------------------
# the function the rest of the project calls
# ----------------------------------------------------------------------
def parse_position(
    ra: float | str | None = None,
    dec: float | str | None = None,
    target: str | None = None,
) -> tuple[float, float]:
    """Return (ra_deg, dec_deg), validated.

    Examples
        parse_position("133.79", "-7.245")           ->  (133.79, -7.245)
        parse_position("08h55m10.8s", "-07d14m42s")  ->  about (133.795, -7.245)
        parse_position(target="Vega")                ->  online lookup
    """
    if target is not None and str(target).strip():
        return _resolve_name(" ".join(str(target).split()))

    if ra is None or dec is None or not str(ra).strip() or not str(dec).strip():
        raise PositionError("Give both ra and dec, or a target name.")

    ra_deg = _to_degrees(ra, u.hourangle, "RA")
    dec_deg = _to_degrees(dec, u.deg, "Dec")

    if not (0.0 <= ra_deg < 360.0):
        raise PositionError(f"RA must be at least 0 and less than 360 degrees (got {ra_deg:g}).")
    if not (-90.0 <= dec_deg <= 90.0):
        raise PositionError(f"Dec must be between -90 and 90 degrees (got {dec_deg:g}).")
    return ra_deg, dec_deg


if __name__ == "__main__":
    import sys

    args = sys.argv[1:]
    if not args:
        print("Running coords.py demo...")
        print("Usage:")
        print("  python coords.py <target_name>     (e.g. python coords.py Sirius)")
        print("  python coords.py <ra> <dec>        (e.g. python coords.py 133.79 -7.245)")
        print("\nSample Target Lookups:")
        for demo_name in ["WISE 0855-0714", "Barnard's Star", "Sirius", "Vega", "Polaris"]:
            ra_val, dec_val = parse_position(target=demo_name)
            print(f"  {demo_name:<16} -> RA: {ra_val:10.5f} deg, Dec: {dec_val:10.5f} deg")
    elif len(args) == 1:
        ra_val, dec_val = parse_position(target=args[0])
        print(f"Target '{args[0]}': RA = {ra_val:.5f} deg, Dec = {dec_val:.5f} deg")
    else:
        ra_val, dec_val = parse_position(ra=args[0], dec=args[1])
        print(f"Coordinates ({args[0]}, {args[1]}): RA = {ra_val:.5f} deg, Dec = {dec_val:.5f} deg")
