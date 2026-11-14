# AI-assisted: written with AI pair-programming assistant.
"""Gaia DR3 live TAP query, disk caching, epoch propagation, and cross-matching.

Pass / Fail Criteria (fixed before running):
1. Live query & caching: ``query_gaia(ra, dec, radius_arcsec)`` queries
   ``gaiadr3.gaia_source`` for ``source_id, ra, dec, pmra, pmdec, parallax,
   phot_g_mean_mag, ref_epoch``, caches results to disk keyed by rounded position
   and radius, enforces a request timeout, raises ``GaiaUnreachableError`` (502)
   when the TAP service cannot be reached, and never fabricates rows (an empty
   result is valid).
2. Proper-motion propagation: ``propagate_to_epoch(table, epoch)`` moves each
   source from its ``ref_epoch`` (2016.0) to ``epoch`` using ``pmra`` (which
   includes ``cos(dec)``) and ``pmdec``, matching an analytic hand calculation
   to better than 1e-7 deg. Sources lacking finite ``pmra`` or ``pmdec`` retain
   their catalog position and have ``pm_missing == True``.
3. Cross-matching: ``crossmatch(candidates, gaia, radius_arcsec)`` propagates
   Gaia positions to the candidate observation epoch, finds counterparts within
   ``radius_arcsec``, reports ``separation_arcsec``, and classifies each
   candidate as ``known_gaia_mover`` (proper motion consistent within 3 sigma),
   ``gaia_source_inconsistent_pm``, or ``no_gaia_counterpart``.
4. Offline readiness: ``scripts/fetch_gaia.py`` caches Gaia DR3 tables for all
   five demo targets in ``coords.OFFLINE_TARGETS`` and writes reproducible
   measurements to ``results/gaia_catalog.json`` and ``results/gaia_catalog.md``.
"""

from __future__ import annotations

import json
import math
import os
import re
import tempfile
import threading
from collections.abc import Iterator, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, TypeVar

import numpy as np
from astropy import units as u
from astropy.coordinates import SkyCoord
from astropy.table import Table
from astropy.time import Time

import coords
import data

_REPO_ROOT = Path(__file__).resolve().parent

GAIA_TABLE_NAME: str = "gaiadr3.gaia_source"
GAIA_REF_EPOCH: float = 2016.0
GAIA_COLUMNS: tuple[str, ...] = (
    "source_id",
    "ra",
    "dec",
    "pmra",
    "pmdec",
    "parallax",
    "phot_g_mean_mag",
    "ref_epoch",
)
PROPAGATED_COLUMNS: tuple[str, ...] = (*GAIA_COLUMNS, "pm_missing")
MAX_CATALOG_SOURCES: int = 500
DEFAULT_TIMEOUT_SEC: float = 15.0
DEFAULT_PM_REL_TOL: float = 0.10
MIN_PM_SIGMA_MAS_YR: float = 5.0

# Optional module-level override for astroquery.gaia.Gaia (imported lazily when None
# so importing gaia.py never makes network connections at import time).
Gaia: Any = None

T = TypeVar("T")
_CACHE_FILE_RE = re.compile(
    r"^gaia_ra(?P<ra>\d{3}\.\d{4})_dec(?P<dec>[+-]\d{2}\.\d{4})_r(?P<rad>\d{4}\.\d)\.json$"
)


class GaiaError(data.DataError):
    """Base error for Gaia DR3 catalog queries and caching."""

    code: str = "gaia_error"


class GaiaUnreachableError(data.ArchiveUnreachableError):
    """Raised when the ESA Gaia DR3 TAP service cannot be reached or times out."""

    code: str = "archive_unreachable"


class GaiaNotCachedError(data.NotCachedError):
    """Raised in cache mode when the requested Gaia cone is not in the local cache."""

    code: str = "not_cached"


@dataclass(frozen=True)
class CrossmatchResult:
    """Result of cross-matching a candidate source against Gaia DR3 at the target epoch.

    Supports both attribute access (``result.classification``) and mapping access
    (``result["classification"]``).
    """

    classification: str
    separation_arcsec: float | None
    source_id: int | None
    gaia_ra: float | None
    gaia_dec: float | None
    gaia_pmra: float | None
    gaia_pmdec: float | None
    pm_diff_sigma: float | None
    candidate: dict[str, Any]

    def to_dict(self) -> dict[str, Any]:
        """Convert this cross-match result into a JSON-serializable dictionary."""
        return {
            "classification": self.classification,
            "separation_arcsec": self.separation_arcsec,
            "source_id": self.source_id,
            "gaia_ra": self.gaia_ra,
            "gaia_dec": self.gaia_dec,
            "gaia_pmra": self.gaia_pmra,
            "gaia_pmdec": self.gaia_pmdec,
            "pm_diff_sigma": self.pm_diff_sigma,
            "candidate": dict(self.candidate),
        }

    def __getitem__(self, key: str) -> Any:
        return self.to_dict()[key]

    def get(self, key: str, default: Any = None) -> Any:
        return self.to_dict().get(key, default)

    def keys(self) -> Iterator[str]:
        return iter(self.to_dict().keys())

    def __contains__(self, key: object) -> bool:
        return key in self.to_dict()


def _get_gaia_client(tap_client: Any = None) -> Any:
    """Return explicit ``tap_client``, module ``Gaia``, or lazy ``astroquery.gaia.Gaia``."""
    if tap_client is not None:
        return tap_client
    if Gaia is not None:
        return Gaia
    try:
        from astroquery.gaia import Gaia as _AstroqueryGaia
    except Exception as exc:
        raise GaiaUnreachableError(f"Gaia DR3 TAP client could not be loaded: {exc}") from exc
    return _AstroqueryGaia


def _run_with_timeout(fn: Callable[[], T], timeout: float) -> T:
    """Execute ``fn`` with a wall-clock timeout, raising ``GaiaUnreachableError`` if exceeded."""
    if not math.isfinite(timeout) or timeout <= 0:
        raise ValueError(f"timeout must be a positive finite number, got {timeout}")
    outcome: dict[str, Any] = {}
    done = threading.Event()

    def _worker() -> None:
        try:
            outcome["result"] = fn()
        except BaseException as exc:
            outcome["error"] = exc
        finally:
            done.set()

    thread = threading.Thread(target=_worker, daemon=True)
    thread.start()
    if not done.wait(timeout=timeout):
        raise GaiaUnreachableError(
            f"Gaia DR3 TAP service timed out after {timeout:.1f}s. "
            "Please retry or use a cached field."
        )
    if "error" in outcome:
        err = outcome["error"]
        if isinstance(err, GaiaError):
            raise err
        raise GaiaUnreachableError(f"Gaia DR3 TAP service is unreachable: {err}") from err
    return outcome["result"]


def gaia_cache_key(ra: float, dec: float, radius_arcsec: float) -> str:
    """Build a deterministic disk-cache key from rounded ICRS coordinates and radius."""
    ra_deg, dec_deg = coords.parse_position(ra, dec)
    if not math.isfinite(radius_arcsec) or radius_arcsec <= 0:
        raise ValueError(f"radius_arcsec must be positive and finite, got {radius_arcsec}")
    ra_norm = round(float(ra_deg), 4) % 360.0
    dec_norm = round(float(dec_deg), 4)
    rad_norm = round(float(radius_arcsec), 1)
    return f"gaia_ra{ra_norm:08.4f}_dec{dec_norm:+08.4f}_r{rad_norm:06.1f}"


def _gaia_cache_dir(cache_root: str | Path | None = None) -> Path:
    """Resolve the ``<cache_root>/gaia`` directory."""
    base = Path(cache_root or os.environ.get("SPHX_CACHE_DIR", str(_REPO_ROOT / "cache"))).resolve()
    return base if base.name == "gaia" else base / "gaia"


def _clean_float(val: Any) -> float:
    """Convert a scalar or masked value to float, mapping None/masked/non-finite to NaN."""
    if val is None or val is np.ma.masked:
        return math.nan
    if hasattr(val, "mask") and bool(np.all(val.mask)):
        return math.nan
    try:
        out = float(val)
    except (TypeError, ValueError):
        return math.nan
    return out if math.isfinite(out) else math.nan


def _clean_int(val: Any) -> int:
    """Convert a Gaia ``source_id`` value to a Python ``int``."""
    if val is None or val is np.ma.masked:
        raise ValueError("Gaia row is missing required integer source_id")
    if hasattr(val, "item"):
        val = val.item()
    return int(val)


def _empty_gaia_table(*, include_pm_missing: bool = False) -> Table:
    """Create an empty ``astropy.table.Table`` with the canonical Gaia DR3 schema."""
    names = list(PROPAGATED_COLUMNS if include_pm_missing else GAIA_COLUMNS)
    dtypes = ["i8", "f8", "f8", "f8", "f8", "f8", "f8", "f8"]
    if include_pm_missing:
        dtypes.append("bool")
    return Table(names=names, dtype=dtypes)


def _rows_to_table(
    rows: Sequence[Mapping[str, Any]],
    *,
    include_pm_missing: bool = False,
    max_rows: int | None = MAX_CATALOG_SOURCES,
) -> Table:
    """Normalize a sequence of row mappings into a sorted ``astropy.table.Table``."""
    if not rows:
        return _empty_gaia_table(include_pm_missing=include_pm_missing)

    normalized: list[dict[str, Any]] = []
    for row in rows:
        sid = _clean_int(row["source_id"])
        ra_val = _clean_float(row["ra"])
        dec_val = _clean_float(row["dec"])
        if not (math.isfinite(ra_val) and math.isfinite(dec_val)):
            continue
        pmra_val = _clean_float(row["pmra"]) if "pmra" in row else math.nan
        pmdec_val = _clean_float(row["pmdec"]) if "pmdec" in row else math.nan
        plx_val = _clean_float(row["parallax"]) if "parallax" in row else math.nan
        gmag_val = _clean_float(row["phot_g_mean_mag"]) if "phot_g_mean_mag" in row else (
            _clean_float(row["g_mag"]) if "g_mag" in row else math.nan
        )
        ref_ep = _clean_float(row["ref_epoch"]) if "ref_epoch" in row else GAIA_REF_EPOCH
        if not math.isfinite(ref_ep):
            ref_ep = GAIA_REF_EPOCH

        item: dict[str, Any] = {
            "source_id": sid,
            "ra": ra_val % 360.0,
            "dec": dec_val,
            "pmra": pmra_val,
            "pmdec": pmdec_val,
            "parallax": plx_val,
            "phot_g_mean_mag": gmag_val,
            "ref_epoch": ref_ep,
        }
        if include_pm_missing:
            pm_miss = row.get("pm_missing")
            if pm_miss is None:
                pm_miss = not (math.isfinite(pmra_val) and math.isfinite(pmdec_val))
            item["pm_missing"] = bool(pm_miss)
        normalized.append(item)

    if not normalized:
        return _empty_gaia_table(include_pm_missing=include_pm_missing)

    # Sort brightest first (smallest phot_g_mean_mag first, NaNs last), tie-break by source_id
    normalized.sort(
        key=lambda r: (
            0 if math.isfinite(r["phot_g_mean_mag"]) else 1,
            r["phot_g_mean_mag"] if math.isfinite(r["phot_g_mean_mag"]) else 0.0,
            r["source_id"],
        )
    )
    if max_rows is not None and max_rows > 0:
        normalized = normalized[:max_rows]

    cols = PROPAGATED_COLUMNS if include_pm_missing else GAIA_COLUMNS
    dtypes = ["i8", "f8", "f8", "f8", "f8", "f8", "f8", "f8"] + (
        ["bool"] if include_pm_missing else []
    )
    data_cols = [[r[c] for r in normalized] for c in cols]
    return Table(data=data_cols, names=cols, dtype=dtypes)


def _table_to_serializable_rows(table: Table | Sequence[Mapping[str, Any]]) -> list[dict[str, Any]]:
    """Convert an ``astropy.table.Table`` or row sequence into JSON-ready dictionaries."""
    if isinstance(table, Table):
        colnames = set(table.colnames)
        out: list[dict[str, Any]] = []
        for row in table:
            pmra_v = _clean_float(row["pmra"]) if "pmra" in colnames else math.nan
            pmdec_v = _clean_float(row["pmdec"]) if "pmdec" in colnames else math.nan
            plx_v = _clean_float(row["parallax"]) if "parallax" in colnames else math.nan
            gmag_v = (
                _clean_float(row["phot_g_mean_mag"])
                if "phot_g_mean_mag" in colnames
                else math.nan
            )
            ref_v = _clean_float(row["ref_epoch"]) if "ref_epoch" in colnames else GAIA_REF_EPOCH
            entry: dict[str, Any] = {
                "source_id": _clean_int(row["source_id"]),
                "ra": float(row["ra"]),
                "dec": float(row["dec"]),
                "pmra": None if math.isnan(pmra_v) else pmra_v,
                "pmdec": None if math.isnan(pmdec_v) else pmdec_v,
                "parallax": None if math.isnan(plx_v) else plx_v,
                "phot_g_mean_mag": None if math.isnan(gmag_v) else gmag_v,
                "ref_epoch": GAIA_REF_EPOCH if math.isnan(ref_v) else ref_v,
            }
            if "pm_missing" in colnames:
                entry["pm_missing"] = bool(row["pm_missing"])
            out.append(entry)
        return out

    return _table_to_serializable_rows(
        _rows_to_table(table, include_pm_missing=False, max_rows=None)
    )


def _write_cache_file(
    cache_path: Path,
    *,
    ra: float,
    dec: float,
    radius_arcsec: float,
    table: Table,
) -> None:
    """Atomically write a Gaia query result to disk as JSON."""
    cache_path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "source": "gaia_dr3",
        "table": GAIA_TABLE_NAME,
        "ra_deg": round(float(ra), 6),
        "dec_deg": round(float(dec), 6),
        "radius_arcsec": round(float(radius_arcsec), 3),
        "n_rows": int(len(table)),
        "rows": _table_to_serializable_rows(table),
    }
    fd, tmp_name = tempfile.mkstemp(
        prefix=".gaia_",
        suffix=".tmp",
        dir=str(cache_path.parent),
    )
    tmp_path = Path(tmp_name)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            json.dump(payload, fh, indent=2)
            fh.write("\n")
        os.replace(tmp_path, cache_path)
    finally:
        if tmp_path.exists():
            tmp_path.unlink(missing_ok=True)


def _angular_separation_arcsec(
    ra1_deg: float,
    dec1_deg: float,
    ra2_deg: float | np.ndarray,
    dec2_deg: float | np.ndarray,
) -> float | np.ndarray:
    """Compute exact spherical angular separation in arcseconds using the haversine formula."""
    ra1 = np.deg2rad(ra1_deg)
    dec1 = np.deg2rad(dec1_deg)
    ra2 = np.deg2rad(ra2_deg)
    dec2 = np.deg2rad(dec2_deg)
    dra = ra2 - ra1
    ddec = dec2 - dec1
    a = np.sin(ddec / 2.0) ** 2 + np.cos(dec1) * np.cos(dec2) * np.sin(dra / 2.0) ** 2
    a = np.clip(a, 0.0, 1.0)
    angle_rad = 2.0 * np.arcsin(np.sqrt(a))
    arcsec = np.rad2deg(angle_rad) * 3600.0
    if np.ndim(arcsec) == 0:
        return float(arcsec)
    return arcsec


def _load_from_cache(
    cache_dir: Path,
    ra: float,
    dec: float,
    radius_arcsec: float,
    *,
    max_rows: int | None = MAX_CATALOG_SOURCES,
    exact_only: bool = False,
    filter_radius_arcsec: float | None = None,
) -> Table | None:
    """Load a cached Gaia table by exact key or from a covering cached cone."""
    key = gaia_cache_key(ra, dec, radius_arcsec)
    exact_path = cache_dir / f"{key}.json"
    if exact_path.is_file():
        try:
            payload = json.loads(exact_path.read_text(encoding="utf-8"))
            tbl = _rows_to_table(payload.get("rows", []), max_rows=max_rows)
            tbl.meta["data_source"] = "cache"
            tbl.meta["cache_key"] = key
            total_rows = int(payload.get("n_rows", len(tbl)))
            tbl.meta["truncated"] = max_rows is not None and total_rows > max_rows
            return tbl
        except (OSError, ValueError, KeyError, json.JSONDecodeError):
            pass

    if exact_only or not cache_dir.is_dir():
        return None

    # Check if a larger cached cone completely covers the requested (ra, dec, radius_arcsec)
    candidates: list[tuple[float, Path, dict[str, Any]]] = []
    for path in sorted(cache_dir.glob("gaia_ra*_dec*_r*.json")):
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
            c_ra = float(payload["ra_deg"])
            c_dec = float(payload["dec_deg"])
            c_rad = float(payload["radius_arcsec"])
        except (OSError, ValueError, KeyError, json.JSONDecodeError):
            continue
        center_sep = float(_angular_separation_arcsec(ra, dec, c_ra, c_dec))
        if center_sep + float(radius_arcsec) <= c_rad + 1.0:
            candidates.append((c_rad, path, payload))

    if not candidates:
        return None

    candidates.sort(key=lambda item: item[0])
    _, chosen_path, chosen_payload = candidates[0]
    raw_rows = chosen_payload.get("rows", [])
    row_filter_rad = (
        float(radius_arcsec) if filter_radius_arcsec is None else float(filter_radius_arcsec)
    )
    filtered_rows = [
        r
        for r in raw_rows
        if float(_angular_separation_arcsec(ra, dec, float(r["ra"]), float(r["dec"])))
        <= row_filter_rad + 1e-6
    ]
    tbl = _rows_to_table(filtered_rows, max_rows=max_rows)
    tbl.meta["data_source"] = "cache"
    tbl.meta["cache_key"] = chosen_path.stem
    tbl.meta["truncated"] = max_rows is not None and len(filtered_rows) > max_rows
    return tbl


def build_gaia_adql(
    ra: float,
    dec: float,
    radius_arcsec: float,
    *,
    max_rows: int = MAX_CATALOG_SOURCES,
) -> str:
    """Build the ADQL cone-search query for ``gaiadr3.gaia_source``."""
    ra_deg, dec_deg = coords.parse_position(ra, dec)
    if not math.isfinite(radius_arcsec) or radius_arcsec <= 0:
        raise ValueError(f"radius_arcsec must be positive and finite, got {radius_arcsec}")
    radius_deg = float(radius_arcsec) / 3600.0
    cols_sql = ", ".join(GAIA_COLUMNS)
    # Request max_rows + 1 so we can detect and warn when the field exceeds max_rows
    top_n = max(1, int(max_rows)) + 1
    return (
        f"SELECT TOP {top_n} {cols_sql} "
        f"FROM {GAIA_TABLE_NAME} "
        f"WHERE 1 = CONTAINS("
        f"POINT('ICRS', ra, dec), "
        f"CIRCLE('ICRS', {ra_deg:.8f}, {dec_deg:.8f}, {radius_deg:.8f})"
        f") "
        f"ORDER BY phot_g_mean_mag ASC"
    )


def query_gaia(
    ra: float,
    dec: float,
    radius_arcsec: float,
    *,
    cache_root: str | Path | None = None,
    mode: str | None = None,
    timeout: float = DEFAULT_TIMEOUT_SEC,
    max_rows: int = MAX_CATALOG_SOURCES,
    tap_client: Any = None,
) -> Table:
    """Query ``gaiadr3.gaia_source`` via astroquery's Gaia TAP interface with disk caching.

    Args:
        ra: Right Ascension in decimal degrees (ICRS).
        dec: Declination in decimal degrees (ICRS).
        radius_arcsec: Cone search radius in arcseconds (> 0).
        cache_root: Optional root cache directory (defaults to ``SPHX_CACHE_DIR`` or ``cache/``).
        mode: Optional data mode override (``auto``, ``cache``, ``live``, ``synthetic``).
        timeout: Maximum wall-clock time in seconds for a live TAP query.
        max_rows: Maximum number of sources returned (brightest first, default 500).
        tap_client: Optional TAP client override (defaults to ``astroquery.gaia.Gaia``).

    Returns:
        An ``astropy.table.Table`` with columns ``source_id, ra, dec, pmra, pmdec,
        parallax, phot_g_mean_mag, ref_epoch``.

    Raises:
        ValueError: If coordinates, radius, or timeout are invalid.
        GaiaNotCachedError: In ``cache`` mode when the position is not cached on disk.
        GaiaUnreachableError: When the Gaia TAP service fails or times out.
    """
    ra_deg, dec_deg = coords.parse_position(ra, dec)
    if not math.isfinite(radius_arcsec) or radius_arcsec <= 0:
        raise ValueError(f"radius_arcsec must be positive and finite, got {radius_arcsec}")
    if not math.isfinite(timeout) or timeout <= 0:
        raise ValueError(f"timeout must be positive and finite, got {timeout}")

    data_mode = data.get_data_mode(mode)
    cdir = _gaia_cache_dir(cache_root)
    key = gaia_cache_key(ra_deg, dec_deg, radius_arcsec)
    cache_path = cdir / f"{key}.json"

    if data_mode in ("cache", "auto"):
        cached = _load_from_cache(
            cdir,
            ra_deg,
            dec_deg,
            radius_arcsec,
            max_rows=max_rows,
        )
        if cached is not None:
            return cached
        if data_mode == "cache":
            raise GaiaNotCachedError(
                f"Gaia DR3 catalog for RA={ra_deg:.4f}, Dec={dec_deg:+.4f}, "
                f'radius={radius_arcsec:.1f}" is not cached locally.'
            )

    adql = build_gaia_adql(ra_deg, dec_deg, radius_arcsec, max_rows=max_rows)

    def _execute_tap() -> Any:
        client = _get_gaia_client(tap_client)
        if hasattr(client, "launch_job"):
            job = client.launch_job(adql)
        elif hasattr(client, "launch_job_async"):
            job = client.launch_job_async(adql)
        else:
            raise GaiaUnreachableError("Configured Gaia TAP client has no launch_job method.")
        if hasattr(job, "get_results"):
            return job.get_results()
        return job

    raw_result = _run_with_timeout(_execute_tap, timeout=timeout)

    if isinstance(raw_result, Table):
        missing_cols = [c for c in GAIA_COLUMNS if c not in raw_result.colnames]
        if missing_cols:
            raise GaiaUnreachableError(
                f"Gaia TAP response is missing required columns: {missing_cols}"
            )
        raw_rows: list[Mapping[str, Any]] = [
            {col: row[col] for col in GAIA_COLUMNS} for row in raw_result
        ]
    elif isinstance(raw_result, Sequence):
        raw_rows = list(raw_result)
    else:
        raise GaiaUnreachableError(
            f"Unexpected Gaia TAP result type: {type(raw_result).__name__}"
        )

    full_table = _rows_to_table(raw_rows, max_rows=None)
    if data_mode != "synthetic":
        _write_cache_file(
            cache_path,
            ra=ra_deg,
            dec=dec_deg,
            radius_arcsec=radius_arcsec,
            table=full_table,
        )

    truncated = len(full_table) > max_rows
    result_table = full_table[:max_rows].copy() if truncated else full_table
    result_table.meta["data_source"] = "archive"
    result_table.meta["cache_key"] = key
    result_table.meta["truncated"] = truncated
    return result_table


def propagate_to_epoch(
    table: Table | Sequence[Mapping[str, Any]],
    epoch: float,
) -> Table:
    """Propagate Gaia DR3 sources from ``ref_epoch`` (2016.0) to ``epoch``.

    Uses linear proper-motion propagation where ``pmra`` is ``mu_alpha * cos(dec)``
    in mas/yr and ``pmdec`` is ``mu_delta`` in mas/yr. Sources lacking a finite
    ``pmra`` or ``pmdec`` retain their catalog position and are flagged with
    ``pm_missing = True``.

    Args:
        table: Input Gaia ``Table`` or sequence of row dictionaries.
        epoch: Target observation epoch in decimal Julian years (e.g. ``2026.0``).

    Returns:
        A new ``astropy.table.Table`` with updated ``ra``, ``dec`` and boolean
        column ``pm_missing``.
    """
    if not math.isfinite(epoch):
        raise ValueError(f"epoch must be a finite Julian year, got {epoch}")
    target_epoch = float(epoch)

    base_table = (
        table
        if isinstance(table, Table)
        else _rows_to_table(table, include_pm_missing=False, max_rows=None)
    )
    if len(base_table) == 0:
        empty = _empty_gaia_table(include_pm_missing=True)
        empty.meta = dict(getattr(base_table, "meta", {}))
        empty.meta["epoch"] = target_epoch
        return empty

    out_rows: list[dict[str, Any]] = []
    colnames = set(base_table.colnames)
    for row in base_table:
        sid = _clean_int(row["source_id"])
        ra_0 = float(row["ra"])
        dec_0 = float(row["dec"])
        pmra = _clean_float(row["pmra"]) if "pmra" in colnames else math.nan
        pmdec = _clean_float(row["pmdec"]) if "pmdec" in colnames else math.nan
        parallax = _clean_float(row["parallax"]) if "parallax" in colnames else math.nan
        gmag = (
            _clean_float(row["phot_g_mean_mag"])
            if "phot_g_mean_mag" in colnames
            else math.nan
        )
        ref_ep = _clean_float(row["ref_epoch"]) if "ref_epoch" in colnames else GAIA_REF_EPOCH
        if not math.isfinite(ref_ep):
            ref_ep = GAIA_REF_EPOCH

        if math.isfinite(pmra) and math.isfinite(pmdec):
            new_ra, new_dec = coords.position_at_epoch(
                ra_0,
                dec_0,
                pmra,
                pmdec,
                ref_ep,
                target_epoch=target_epoch,
            )
            pm_missing = False
        else:
            new_ra, new_dec = ra_0 % 360.0, dec_0
            pm_missing = True

        out_rows.append(
            {
                "source_id": sid,
                "ra": new_ra,
                "dec": new_dec,
                "pmra": pmra,
                "pmdec": pmdec,
                "parallax": parallax,
                "phot_g_mean_mag": gmag,
                "ref_epoch": ref_ep,
                "pm_missing": pm_missing,
            }
        )

    cols = PROPAGATED_COLUMNS
    dtypes = ["i8", "f8", "f8", "f8", "f8", "f8", "f8", "f8", "bool"]
    data_cols = [[r[c] for r in out_rows] for c in cols]
    propagated = Table(data=data_cols, names=cols, dtype=dtypes)
    propagated.meta = dict(getattr(base_table, "meta", {}))
    propagated.meta["epoch"] = target_epoch
    return propagated


def _candidate_to_dict(candidate: Any) -> dict[str, Any]:
    """Normalize a candidate object, Table Row, or Mapping to a dictionary."""
    if isinstance(candidate, Mapping):
        return dict(candidate)
    if hasattr(candidate, "colnames"):
        return {col: candidate[col] for col in candidate.colnames}
    if hasattr(candidate, "__dataclass_fields__"):
        return {k: getattr(candidate, k) for k in candidate.__dataclass_fields__}
    if hasattr(candidate, "__dict__"):
        return dict(vars(candidate))
    raise TypeError(f"Unsupported candidate type: {type(candidate).__name__}")


def _extract_candidate_epoch(cand: Mapping[str, Any], default_epoch: float) -> float:
    """Determine the observation epoch (Julian year) for a candidate."""
    for key in ("epoch", "epoch_yr", "obs_epoch"):
        val = cand.get(key)
        if val is not None:
            fval = _clean_float(val)
            # Distinguish a Julian year (e.g. 2026.0) from an integer epoch index (0, 1, 2)
            if math.isfinite(fval) and fval >= 1900.0:
                return fval
    mjd = cand.get("mjd")
    if mjd is not None:
        fmjd = _clean_float(mjd)
        if math.isfinite(fmjd) and fmjd > 0:
            return float(Time(fmjd, format="mjd", scale="utc").jyear)
    return default_epoch


def _compare_proper_motion_sigma(
    cand: Mapping[str, Any],
    gaia_pmra: float,
    gaia_pmdec: float,
) -> float | None:
    """Compute the proper-motion discrepancy in units of sigma between ``cand`` and Gaia.

    Returns ``None`` if either the Gaia source or the candidate lacks a proper-motion
    measurement.
    """
    if not (math.isfinite(gaia_pmra) and math.isfinite(gaia_pmdec)):
        return None

    # Case 1: Candidate provides component proper motions (pmra, pmdec) in mas/yr
    c_pmra = _clean_float(cand.get("pmra", cand.get("pmra_mas_yr")))
    c_pmdec = _clean_float(cand.get("pmdec", cand.get("pmdec_mas_yr")))
    if not (math.isfinite(c_pmra) and math.isfinite(c_pmdec)):
        # Also check arcsec/yr component keys
        c_pmra_as = _clean_float(cand.get("pm_ra_arcsec_yr", cand.get("pmra_arcsec_yr")))
        c_pmdec_as = _clean_float(cand.get("pm_dec_arcsec_yr", cand.get("pmdec_arcsec_yr")))
        if math.isfinite(c_pmra_as) and math.isfinite(c_pmdec_as):
            c_pmra = c_pmra_as * 1000.0
            c_pmdec = c_pmdec_as * 1000.0

    if math.isfinite(c_pmra) and math.isfinite(c_pmdec):
        d_pmra = c_pmra - gaia_pmra
        d_pmdec = c_pmdec - gaia_pmdec
        sig_ra = _clean_float(
            cand.get("pmra_sigma", cand.get("pmra_err", cand.get("pmra_error")))
        )
        sig_dec = _clean_float(
            cand.get("pmdec_sigma", cand.get("pmdec_err", cand.get("pmdec_error")))
        )
        if math.isfinite(sig_ra) and math.isfinite(sig_dec) and sig_ra > 0 and sig_dec > 0:
            return float(math.hypot(d_pmra / sig_ra, d_pmdec / sig_dec))

        sig_tot = _clean_float(
            cand.get("pm_sigma_mas_yr", cand.get("pm_sigma", cand.get("pm_err")))
        )
        if not math.isfinite(sig_tot) or sig_tot <= 0:
            gaia_mag = math.hypot(gaia_pmra, gaia_pmdec)
            sig_tot = max(MIN_PM_SIGMA_MAS_YR, DEFAULT_PM_REL_TOL * gaia_mag)
        return float(math.hypot(d_pmra, d_pmdec) / sig_tot)

    # Case 2: Candidate provides total proper motion in arcsec/yr or mas/yr
    c_pm_as = _clean_float(cand.get("pm_arcsec_per_yr", cand.get("pm_arcsec_yr")))
    if math.isfinite(c_pm_as):
        c_pm_mas = c_pm_as * 1000.0
        sig_as = _clean_float(cand.get("pm_sigma", cand.get("pm_sigma_arcsec_yr")))
        sig_mas = (
            sig_as * 1000.0
            if (math.isfinite(sig_as) and sig_as > 0)
            else max(MIN_PM_SIGMA_MAS_YR, DEFAULT_PM_REL_TOL * math.hypot(gaia_pmra, gaia_pmdec))
        )
    else:
        c_pm_mas = _clean_float(cand.get("pm_mas_per_yr", cand.get("pm")))
        if not math.isfinite(c_pm_mas):
            return None
        sig_mas = _clean_float(cand.get("pm_sigma", cand.get("pm_err")))
        if not math.isfinite(sig_mas) or sig_mas <= 0:
            sig_mas = max(
                MIN_PM_SIGMA_MAS_YR,
                DEFAULT_PM_REL_TOL * math.hypot(gaia_pmra, gaia_pmdec),
            )

    pa_deg = _clean_float(cand.get("position_angle_deg", cand.get("pa_deg")))
    if math.isfinite(pa_deg):
        pa_rad = math.radians(pa_deg)
        c_pmra_vec = c_pm_mas * math.sin(pa_rad)
        c_pmdec_vec = c_pm_mas * math.cos(pa_rad)
        return float(math.hypot(c_pmra_vec - gaia_pmra, c_pmdec_vec - gaia_pmdec) / sig_mas)

    gaia_pm_mas = math.hypot(gaia_pmra, gaia_pmdec)
    return float(abs(c_pm_mas - gaia_pm_mas) / sig_mas)


def crossmatch(
    candidates: Sequence[Any],
    gaia: Table | Sequence[Mapping[str, Any]],
    radius_arcsec: float,
    *,
    epoch: float | None = None,
    sigma_threshold: float = 3.0,
) -> list[CrossmatchResult]:
    """Cross-match candidate sources against Gaia DR3 at the observation epoch.

    For each candidate:
    - Finds Gaia sources within ``radius_arcsec`` after propagating Gaia from
      ``ref_epoch`` (2016.0) to the target epoch.
    - Classifies as:
      - ``known_gaia_mover``: A Gaia counterpart lies within ``radius_arcsec`` and
        its proper motion agrees with the candidate's measured proper motion within
        ``sigma_threshold`` (default 3 sigma).
      - ``gaia_source_inconsistent_pm``: A Gaia counterpart lies within
        ``radius_arcsec``, but proper motions disagree by more than 3 sigma (or
        proper motion is missing).
      - ``no_gaia_counterpart``: No Gaia DR3 source lies within ``radius_arcsec``.

    Args:
        candidates: Sequence of candidate dicts/objects with ``ra``, ``dec`` and
            optional proper-motion fields.
        gaia: Gaia DR3 ``Table`` (from ``query_gaia`` or ``propagate_to_epoch``)
            or sequence of source dicts.
        radius_arcsec: Maximum match separation in arcseconds (> 0).
        epoch: Optional target epoch (Julian year). Defaults to candidate epoch,
            ``gaia.meta["epoch"]``, or ``2026.0``.
        sigma_threshold: Consistency threshold in sigma units (default 3.0).

    Returns:
        List of ``CrossmatchResult`` instances, one per candidate.
    """
    if not math.isfinite(radius_arcsec) or radius_arcsec <= 0:
        raise ValueError(f"radius_arcsec must be positive and finite, got {radius_arcsec}")

    gaia_table = (
        gaia
        if isinstance(gaia, Table)
        else _rows_to_table(
            gaia,
            include_pm_missing=any("pm_missing" in r for r in gaia),
            max_rows=None,
        )
    )
    already_propagated_epoch = gaia_table.meta.get("epoch")
    default_epoch = (
        float(epoch)
        if epoch is not None
        else (
            float(already_propagated_epoch)
            if already_propagated_epoch is not None
            else coords.DEFAULT_SURVEY_EPOCH
        )
    )

    propagated_cache: dict[float, tuple[Table, SkyCoord | None]] = {}

    def _get_gaia_at_epoch(target_ep: float) -> tuple[Table, SkyCoord | None]:
        ep_key = round(float(target_ep), 6)
        if ep_key not in propagated_cache:
            if (
                "pm_missing" in gaia_table.colnames
                and already_propagated_epoch is not None
                and abs(float(already_propagated_epoch) - ep_key) < 1e-6
            ):
                prop = gaia_table
            else:
                prop = propagate_to_epoch(gaia_table, ep_key)
            sky = (
                SkyCoord(
                    ra=np.asarray(prop["ra"], dtype=float) * u.deg,
                    dec=np.asarray(prop["dec"], dtype=float) * u.deg,
                    frame="icrs",
                )
                if len(prop) > 0
                else None
            )
            propagated_cache[ep_key] = (prop, sky)
        return propagated_cache[ep_key]

    results: list[CrossmatchResult] = []
    for raw_cand in candidates:
        cand = _candidate_to_dict(raw_cand)
        cand_ra, cand_dec = coords.parse_position(cand["ra"], cand["dec"])
        cand_epoch = (
            float(epoch)
            if epoch is not None
            else _extract_candidate_epoch(cand, default_epoch)
        )
        prop_table, gaia_sky = _get_gaia_at_epoch(cand_epoch)

        if gaia_sky is None or len(prop_table) == 0:
            results.append(
                CrossmatchResult(
                    classification="no_gaia_counterpart",
                    separation_arcsec=None,
                    source_id=None,
                    gaia_ra=None,
                    gaia_dec=None,
                    gaia_pmra=None,
                    gaia_pmdec=None,
                    pm_diff_sigma=None,
                    candidate=cand,
                )
            )
            continue

        cand_coord = SkyCoord(ra=cand_ra * u.deg, dec=cand_dec * u.deg, frame="icrs")
        seps_arcsec = np.asarray(cand_coord.separation(gaia_sky).arcsec, dtype=float)
        within_idx = np.flatnonzero(seps_arcsec <= float(radius_arcsec))
        if within_idx.size == 0:
            results.append(
                CrossmatchResult(
                    classification="no_gaia_counterpart",
                    separation_arcsec=None,
                    source_id=None,
                    gaia_ra=None,
                    gaia_dec=None,
                    gaia_pmra=None,
                    gaia_pmdec=None,
                    pm_diff_sigma=None,
                    candidate=cand,
                )
            )
            continue

        # Order matches by separation (closest first)
        ordered_idx = within_idx[np.argsort(seps_arcsec[within_idx])]
        best_idx = int(ordered_idx[0])
        best_row = prop_table[best_idx]
        best_sep = float(seps_arcsec[best_idx])

        g_pmra = _clean_float(best_row["pmra"])
        g_pmdec = _clean_float(best_row["pmdec"])
        pm_diff = _compare_proper_motion_sigma(cand, g_pmra, g_pmdec)

        if pm_diff is not None and pm_diff <= float(sigma_threshold):
            classification = "known_gaia_mover"
        else:
            classification = "gaia_source_inconsistent_pm"

        results.append(
            CrossmatchResult(
                classification=classification,
                separation_arcsec=round(best_sep, 4),
                source_id=_clean_int(best_row["source_id"]),
                gaia_ra=float(best_row["ra"]),
                gaia_dec=float(best_row["dec"]),
                gaia_pmra=None if math.isnan(g_pmra) else g_pmra,
                gaia_pmdec=None if math.isnan(g_pmdec) else g_pmdec,
                pm_diff_sigma=None if pm_diff is None else round(float(pm_diff), 4),
                candidate=cand,
            )
        )

    return results


def _catalog_query_radius(radius_arcsec: float, epoch: float) -> float:
    """Expand ref-epoch Gaia search cone so high-PM stars moving into the FOV are kept."""
    dt = abs(float(epoch) - GAIA_REF_EPOCH)
    if dt <= 0.0:
        return float(radius_arcsec)
    return float(radius_arcsec) + min(150.0, max(15.0, 12.0 * dt))


def build_catalog(
    ra: float,
    dec: float,
    radius_arcsec: float,
    *,
    epoch: float = coords.DEFAULT_SURVEY_EPOCH,
    cache_root: str | Path | None = None,
    mode: str | None = None,
    timeout: float = DEFAULT_TIMEOUT_SEC,
    max_rows: int = MAX_CATALOG_SOURCES,
    base_warnings: Sequence[str] = (),
    tap_client: Any = None,
) -> dict[str, Any]:
    """Query Gaia DR3, propagate to ``epoch``, and format the ``/api/catalog`` response."""
    ra_deg, dec_deg = coords.parse_position(ra, dec)
    if not math.isfinite(radius_arcsec) or radius_arcsec <= 0:
        raise ValueError(f"radius_arcsec must be positive and finite, got {radius_arcsec}")
    if not math.isfinite(timeout) or timeout <= 0:
        raise ValueError(f"timeout must be positive and finite, got {timeout}")

    data_mode = data.get_data_mode(mode)
    warnings_list: list[str] = list(base_warnings)
    query_radius = _catalog_query_radius(radius_arcsec, epoch)
    cdir = _gaia_cache_dir(cache_root)

    table: Table | None = None
    if data_mode in ("cache", "auto"):
        table = _load_from_cache(
            cdir,
            ra_deg,
            dec_deg,
            query_radius,
            max_rows=None,
        )
        if table is None and query_radius > float(radius_arcsec):
            # Preserve pre-cached 240" demo cones when FOV radius is slightly smaller than
            # the cache cone while still keeping all cached sources out to query_radius.
            table = _load_from_cache(
                cdir,
                ra_deg,
                dec_deg,
                radius_arcsec,
                max_rows=None,
                filter_radius_arcsec=query_radius,
            )
        if table is None and data_mode == "cache":
            raise GaiaNotCachedError(
                f"Gaia DR3 catalog for RA={ra_deg:.4f}, Dec={dec_deg:+.4f}, "
                f'radius={radius_arcsec:.1f}" is not cached locally.'
            )

    if table is None:
        fetch_max_rows = (
            max(int(max_rows), 2000) if query_radius > float(radius_arcsec) else int(max_rows)
        )
        table = query_gaia(
            ra_deg,
            dec_deg,
            query_radius,
            cache_root=cache_root,
            mode=data_mode,
            timeout=timeout,
            max_rows=fetch_max_rows,
            tap_client=tap_client,
        )

    pre_truncated = bool(table.meta.get("truncated", False))
    propagated = propagate_to_epoch(table, epoch)
    if len(propagated) > 0:
        seps = np.asarray(
            _angular_separation_arcsec(
                ra_deg,
                dec_deg,
                np.asarray(propagated["ra"], dtype=np.float64),
                np.asarray(propagated["dec"], dtype=np.float64),
            ),
            dtype=np.float64,
        )
        propagated = propagated[seps <= float(radius_arcsec) + 1e-6]

    truncated = pre_truncated or len(propagated) > int(max_rows)
    if len(propagated) > int(max_rows):
        propagated = propagated[: int(max_rows)]

    sources: list[dict[str, Any]] = []
    n_pm_missing = 0
    for row in propagated:
        pmra_v = _clean_float(row["pmra"])
        pmdec_v = _clean_float(row["pmdec"])
        plx_v = _clean_float(row["parallax"])
        gmag_v = _clean_float(row["phot_g_mean_mag"])
        pm_miss = bool(row["pm_missing"])
        if pm_miss:
            n_pm_missing += 1
        sources.append(
            {
                "source_id": _clean_int(row["source_id"]),
                "ra": round(float(row["ra"]), 7),
                "dec": round(float(row["dec"]), 7),
                "g_mag": None if math.isnan(gmag_v) else round(gmag_v, 4),
                "pmra": None if math.isnan(pmra_v) else round(pmra_v, 4),
                "pmdec": None if math.isnan(pmdec_v) else round(pmdec_v, 4),
                "parallax": None if math.isnan(plx_v) else round(plx_v, 4),
                "pm_missing": pm_miss,
            }
        )

    if truncated:
        warnings_list.append(
            f"Field contains more than {max_rows} Gaia DR3 sources; "
            f"showing the {max_rows} brightest."
        )
    if n_pm_missing:
        warnings_list.append(
            f"{n_pm_missing} Gaia DR3 source(s) lack proper-motion measurements "
            "and remain at their 2016.0 catalog positions."
        )
    if data_mode == "synthetic":
        warnings_list.append(
            "Server is in SYNTHETIC DATA mode; Gaia DR3 catalog rows are never fabricated."
        )

    return {
        "source": "gaia_dr3",
        "epoch": round(float(epoch), 4),
        "n_sources": len(sources),
        "sources": sources,
        "warnings": list(dict.fromkeys(warnings_list)),
    }
