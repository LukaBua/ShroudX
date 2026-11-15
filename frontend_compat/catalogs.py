# AI-assisted: updated with real Gaia DR3 catalog integration.
"""catalogs.py - Real Gaia DR3 & AllWISE catalog references for SPHEREx cutouts.

Replaces hardcoded placeholder catalog entries with real ESA Gaia DR3 astrometry
loaded from local ``cache/gaia/`` files (or queried live via ``gaia.build_catalog``
when running in ``auto``/``live`` mode) and propagated from J2016.0 to the mean
observation epoch of the selected SPHEREx frames.
"""

from __future__ import annotations

import math
from pathlib import Path
from typing import Any

import coords as root_coords
import gaia
from .coords import _norm, get_target_profile

_MAX_RETURNED_SOURCES = 200
_MIN_RELIABLE_PARALLAX_MAS = 1.0
_NEARBY_PARALLAX_MAS = 2.0
_HIGH_PM_MAS_YR = 50.0
_MODERATE_PM_MAS_YR = 15.0
_COMPANION_MAX_SEP_ARCSEC = 30.0
_COMPANION_MIN_PRIMARY_PM_MAS_YR = 15.0


def _angular_sep_arcsec(ra1: float, dec1: float, ra2: float, dec2: float) -> float:
    """Great-circle angular separation in arcseconds."""
    d_ra = math.radians(ra2 - ra1)
    d_dec = math.radians(dec2 - dec1)
    a = (
        math.sin(d_dec / 2.0) ** 2
        + math.cos(math.radians(dec1)) * math.cos(math.radians(dec2)) * math.sin(d_ra / 2.0) ** 2
    )
    return math.degrees(2.0 * math.asin(math.sqrt(min(1.0, a)))) * 3600.0


def _is_finite_number(value: Any) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(float(value))


def _format_parallax_distance(parallax_mas: float | None) -> str | None:
    """Return an honest parallax distance label only when ``parallax >= 1.0`` mas."""
    if not _is_finite_number(parallax_mas) or float(parallax_mas) < _MIN_RELIABLE_PARALLAX_MAS:
        return None
    dist_pc = 1000.0 / float(parallax_mas)
    if dist_pc < 10.0:
        return f"~{dist_pc:.1f} pc"
    return f"~{round(dist_pc)} pc"


def _is_comoving_companion(row: dict[str, Any], primary_row: dict[str, Any] | None) -> bool:
    """Return True when ``row`` closely matches the primary star's distance and PM vector."""
    if primary_row is None or int(row["source_id"]) == int(primary_row["source_id"]):
        return False
    pri_pmra, pri_pmdec, pri_plx = primary_row.get("pmra"), primary_row.get("pmdec"), primary_row.get("parallax")
    pmra, pmdec, plx = row.get("pmra"), row.get("pmdec"), row.get("parallax")
    if not all(_is_finite_number(v) for v in (pri_pmra, pri_pmdec, pri_plx, pmra, pmdec, plx)):
        return False
    pri_plx_f, plx_f = float(pri_plx), float(plx)
    if pri_plx_f <= _MIN_RELIABLE_PARALLAX_MAS or plx_f <= _MIN_RELIABLE_PARALLAX_MAS:
        return False
    pri_pm = math.hypot(float(pri_pmra), float(pri_pmdec))
    if pri_pm < _COMPANION_MIN_PRIMARY_PM_MAS_YR:
        return False
    if _angular_sep_arcsec(primary_row["ra"], primary_row["dec"], row["ra"], row["dec"]) > _COMPANION_MAX_SEP_ARCSEC:
        return False
    d_pm = math.hypot(float(pmra) - float(pri_pmra), float(pmdec) - float(pri_pmdec))
    if d_pm > max(5.0, 0.25 * pri_pm):
        return False
    return abs(plx_f - pri_plx_f) / max(plx_f, pri_plx_f) <= 0.30


def _classify_gaia_source(
    row: dict[str, Any],
    *,
    is_primary: bool,
    profile: dict[str, Any] | None,
    primary_row: dict[str, Any] | None,
) -> str:
    """Classify a Gaia DR3 source purely from cached astrometry (parallax and PM)."""
    plx = row.get("parallax")
    dist_note = _format_parallax_distance(plx)
    if is_primary and profile is not None:
        base = f"{profile['name']} · {profile['type']}"
        return f"{base} · {dist_note}" if dist_note else base
    if _is_comoving_companion(row, primary_row):
        return f"Co-moving companion candidate ({dist_note})" if dist_note else "Co-moving companion candidate"

    pm_ra = row.get("pmra")
    pm_dec = row.get("pmdec")
    pm_missing = bool(row.get("pm_missing", pm_ra is None or pm_dec is None))
    if pm_missing or not (_is_finite_number(pm_ra) and _is_finite_number(pm_dec)):
        if dist_note:
            return f"Field source · {dist_note} (PM missing; J2016.0 pos)"
        return "Field source (PM missing; J2016.0 pos)"

    pm_total = math.hypot(float(pm_ra), float(pm_dec))
    nearby_plx = _is_finite_number(plx) and float(plx) >= _NEARBY_PARALLAX_MAS
    if pm_total >= _HIGH_PM_MAS_YR:
        return f"High-PM foreground star ({dist_note})" if dist_note else "High-PM field star"
    if pm_total >= _MODERATE_PM_MAS_YR:
        if nearby_plx and dist_note:
            return f"Nearby foreground star ({dist_note})"
        if dist_note:
            return f"Moderate-PM field star ({dist_note})"
        return "Moderate-PM background star"
    if nearby_plx and dist_note:
        return f"Nearby low-PM star ({dist_note})"
    if dist_note:
        return f"Low-PM field star ({dist_note})"
    return "Distant low-PM background star"


def _wise0855_reference(
    ra: float,
    dec: float,
    radius_arcsec: float,
    epoch: float,
) -> dict[str, Any] | None:
    """Return the real Kirkpatrick et al. (2021) AllWISE entry for WISE 0855-0714 if in field."""
    entry = root_coords.OFFLINE_TARGETS.get("WISE 0855-0714")
    if entry is None or entry.ra is None or entry.dec is None or entry.pmra is None or entry.pmdec is None or entry.ref_epoch is None:
        return None
    ra_ep, dec_ep = root_coords.position_at_epoch(
        ra=entry.ra,
        dec=entry.dec,
        pmra=entry.pmra,
        pmdec=entry.pmdec,
        ref_epoch=entry.ref_epoch,
        target_epoch=epoch,
    )
    sep = _angular_sep_arcsec(ra, dec, ra_ep, dec_ep)
    if sep > radius_arcsec:
        return None
    pm_total = math.hypot(entry.pmra, entry.pmdec)
    return {
        "id": "WISE J085510.83-071442.5",
        "catalog": "AllWISE",
        "ra_deg": round(float(ra_ep), 7),
        "dec_deg": round(float(dec_ep), 7),
        "mag": 13.97,
        "band": "W2 (4.6 µm)",
        "pm_ra": round(float(entry.pmra), 2),
        "pm_dec": round(float(entry.pmdec), 2),
        "pm_total": round(float(pm_total), 1),
        "parallax_mas": None,
        "pm_missing": False,
        "type": "Y-dwarf (Kirkpatrick+ 2021 / AllWISE; optical dropout)",
        "is_primary": True,
        "sep_arcsec": round(float(sep), 2),
    }


def _resolve_gaia_cache_root(cache_root: str | Path | None) -> Path | None:
    if cache_root is None:
        return None
    candidate = Path(cache_root)
    gaia_sub = candidate if candidate.name == "gaia" else candidate / "gaia"
    if gaia_sub.is_dir() and any(gaia_sub.glob("gaia_ra*_dec*_r*.json")):
        return candidate
    return None


def _primary_target_id(
    rows: list[dict[str, Any]],
    profile: dict[str, Any] | None,
    epoch: float,
) -> int | None:
    """Identify the single real Gaia DR3 source corresponding to the benchmark target."""
    if profile is None:
        return None
    name = str(profile.get("name") or "")
    entry = root_coords.lookup_offline_target(name)
    if entry is None or entry.ra is None or entry.dec is None:
        return None
    if entry.pmra is not None and entry.pmdec is not None and entry.ref_epoch is not None:
        ref_ra, ref_dec = root_coords.position_at_epoch(
            ra=entry.ra,
            dec=entry.dec,
            pmra=entry.pmra,
            pmdec=entry.pmdec,
            ref_epoch=entry.ref_epoch,
            target_epoch=epoch,
        )
    else:
        ref_ra, ref_dec = float(entry.ra), float(entry.dec)

    prof_pm_mas = float(profile.get("pm_total") or 0.0) * 1000.0
    candidates: list[tuple[float, float, int]] = []
    for row in rows:
        sep = _angular_sep_arcsec(ref_ra, ref_dec, row["ra"], row["dec"])
        if sep > 3.5:
            continue
        pm_ra, pm_dec = row.get("pmra"), row.get("pmdec")
        if prof_pm_mas >= 100.0:
            if pm_ra is None or pm_dec is None or math.hypot(pm_ra, pm_dec) < 0.5 * prof_pm_mas:
                continue
        gmag = row.get("g_mag")
        candidates.append((float("inf") if gmag is None else float(gmag), sep, int(row["source_id"])))
    if not candidates:
        return None
    candidates.sort()
    return candidates[0][2]


def get_catalog_sources(
    ra: float,
    dec: float,
    fov_arcsec: float,
    target_name: str | None = None,
    *,
    epoch: float = root_coords.DEFAULT_SURVEY_EPOCH,
    cache_root: str | Path | None = None,
    mode: str = "cache",
    timeout: float = gaia.DEFAULT_TIMEOUT_SEC,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """Return real Gaia DR3 (plus AllWISE for WISE 0855) sources propagated to ``epoch``.

    Never fabricates sources in synthetic/mock mode. Raises ``gaia.GaiaError`` subclasses
    (``GaiaNotCachedError`` / ``GaiaUnreachableError``) when real Gaia data cannot be loaded.
    """
    norm_mode = (mode or "cache").strip().lower()
    if norm_mode in ("mock", "synthetic"):
        return [], {
            "epoch": round(float(epoch), 4),
            "source": "synthetic",
            "truncated": False,
            "warnings": ["Synthetic / simulation mode — Gaia DR3 / AllWISE rows are never fabricated."],
        }

    gaia_mode = "live" if norm_mode == "live" else ("auto" if norm_mode == "auto" else "cache")
    radius_arcsec = (float(fov_arcsec) / 2.0) * math.sqrt(2.0)
    resolved_cache = _resolve_gaia_cache_root(cache_root)
    catalog = gaia.build_catalog(
        ra,
        dec,
        radius_arcsec,
        epoch=float(epoch),
        cache_root=resolved_cache,
        mode=gaia_mode,
        timeout=timeout,
        max_rows=_MAX_RETURNED_SOURCES,
    )

    profile = get_target_profile(target_name, ra, dec)
    is_wise_field = profile is not None and _norm(profile.get("name", "")).startswith("wise0855")
    gaia_rows = catalog.get("sources", [])
    primary_gaia_id = None if is_wise_field else _primary_target_id(gaia_rows, profile, float(epoch))
    primary_row = (
        next((row for row in gaia_rows if int(row["source_id"]) == primary_gaia_id), None)
        if primary_gaia_id is not None
        else None
    )

    sources: list[dict[str, Any]] = []
    has_allwise = False
    if is_wise_field or _angular_sep_arcsec(ra, dec, 133.7599, -7.24221) <= radius_arcsec:
        wise_ref = _wise0855_reference(ra, dec, radius_arcsec, float(epoch))
        if wise_ref is not None:
            sources.append(wise_ref)
            has_allwise = True

    for row in gaia_rows:
        pm_ra = row.get("pmra")
        pm_dec = row.get("pmdec")
        pm_missing = bool(row.get("pm_missing", pm_ra is None or pm_dec is None))
        has_pm = _is_finite_number(pm_ra) and _is_finite_number(pm_dec)
        pm_total = (
            round(float(math.hypot(float(pm_ra), float(pm_dec))), 1)
            if has_pm
            else None
        )
        sep = _angular_sep_arcsec(ra, dec, row["ra"], row["dec"])
        is_primary = primary_gaia_id is not None and int(row["source_id"]) == primary_gaia_id
        desc = _classify_gaia_source(
            row,
            is_primary=is_primary,
            profile=profile,
            primary_row=primary_row,
        )

        sources.append(
            {
                "id": f"Gaia DR3 {row['source_id']}",
                "catalog": "Gaia DR3",
                "ra_deg": row["ra"],
                "dec_deg": row["dec"],
                "mag": row.get("g_mag"),
                "band": "G",
                "pm_ra": round(float(pm_ra), 2) if _is_finite_number(pm_ra) else None,
                "pm_dec": round(float(pm_dec), 2) if _is_finite_number(pm_dec) else None,
                "pm_total": pm_total,
                "parallax_mas": row.get("parallax"),
                "pm_missing": pm_missing,
                "type": desc,
                "is_primary": is_primary,
                "sep_arcsec": round(float(sep), 2),
            }
        )

    sources.sort(
        key=lambda s: (
            0 if s["is_primary"] else 1,
            float("inf") if s["mag"] is None else float(s["mag"]),
            float(s["sep_arcsec"]),
        )
    )
    meta = {
        "epoch": catalog.get("epoch", round(float(epoch), 4)),
        "source": "gaia_dr3+allwise" if has_allwise else "gaia_dr3",
        "has_allwise": has_allwise,
        "warnings": list(catalog.get("warnings", [])),
    }
    return sources, meta
