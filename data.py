"""SPHEREx IRSA cutout query, download, disk caching, and Frame construction."""

from __future__ import annotations

import argparse
import csv
import gzip
import io
import json
import os
import re
import shutil
import sys
import tempfile
import time
from collections.abc import Callable, Sequence
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from urllib.request import Request, urlopen

import numpy as np
from astropy.coordinates import SkyCoord
from astropy.io import fits
from astropy.nddata import Cutout2D
from astropy.time import Time
from astropy.wcs import WCS

from flags import DETECTOR_DEFECTS, UNRELIABLE_CORE, flags_to_mask
from frame import Frame

IRSA_BASE_URL: str = "https://irsa.ipac.caltech.edu"
IRSA_TAP_URL: str = f"{IRSA_BASE_URL}/TAP"
PIXEL_SCALE_ARCSEC: float = 6.15
MAX_DOWNLOAD_WORKERS: int = 5
SIZE_BUCKETS_ARCSEC: tuple[int, ...] = (120, 240, 480, 600)
VALID_MODES: frozenset[str] = frozenset({"cache", "live", "auto", "synthetic"})
KEPT_HDU_NAMES: tuple[str, ...] = ("PRIMARY", "IMAGE", "FLAGS", "VARIANCE", "ZODI", "WCS-WAVE")
REQUIRED_HDU_NAMES: tuple[str, ...] = ("IMAGE", "FLAGS", "VARIANCE", "ZODI", "WCS-WAVE")
INDEX_COLUMNS: tuple[str, ...] = ("file", "mjd", "bandpass", "uri")
FITS_SUFFIXES: tuple[str, ...] = (".fits.gz", ".fits")
DEFAULT_CACHE_EXTENSION: str = ".fits.gz"
# Written next to index.csv when some cutouts failed to download, so a later request knows the
# cached sequence is incomplete instead of silently serving it as if it were whole.
INCOMPLETE_MARKER: str = "incomplete.json"
# SPHEREx covers 0.75-5.0 um; anything outside this is a unit or lookup error, not a wavelength.
PLAUSIBLE_WAVELENGTH_UM: tuple[float, float] = (0.70, 5.10)

DETECTOR_RANGES_UM: dict[int, tuple[float, float]] = {
    1: (0.75, 1.09),
    2: (1.10, 1.62),
    3: (1.63, 2.41),
    4: (2.42, 3.82),
    5: (3.83, 4.41),
    6: (4.42, 5.00),
}

_VERSION_REGEX = re.compile(r"(?:l2b[-_])?v(\d+)", re.IGNORECASE)
_DETECTOR_REGEX = re.compile(
    r"(?:SPHEREx[-_]?)?[Dd](\d)\b|\bdet(?:ector)?[-_]?(\d)\b", re.IGNORECASE
)


class DataError(RuntimeError):
    """Base error for SPHEREx data retrieval and caching."""

    code: str = "data_error"


class NoCoverageError(DataError):
    """Raised when IRSA has no SPHEREx images covering the requested position."""

    code: str = "no_coverage"


NoCoverage = NoCoverageError


class NotCachedError(DataError):
    """Raised in cache mode when the requested position is not present in the disk cache."""

    code: str = "not_cached"


class ArchiveUnreachableError(DataError):
    """Raised when IRSA TAP or cutout download endpoints cannot be reached."""

    code: str = "archive_unreachable"


@dataclass(frozen=True)
class ImageRecord:
    """One SPHEREx plane/artifact record returned by the IRSA TAP query."""

    uri: str
    mjd: float
    bandpass: str

    def __iter__(self):
        """Allow tuple-unpacking as ``uri, mjd, bandpass``."""
        yield self.uri
        yield self.mjd
        yield self.bandpass

    def __getitem__(self, idx: int | str) -> Any:
        if isinstance(idx, int):
            return (self.uri, self.mjd, self.bandpass)[idx]
        if idx in ("uri", "mjd", "bandpass"):
            return getattr(self, idx)
        if idx == "time_bounds_lower":
            return self.mjd
        if idx in ("energy_bandpassname", "band"):
            return self.bandpass
        raise KeyError(idx)


def get_data_mode(mode: str | None = None) -> str:
    """Resolve and validate the data mode from argument or ``SPHX_MODE`` env var.

    Args:
        mode: Optional explicit mode override (``cache``, ``live``, ``auto``, ``synthetic``).

    Returns:
        Normalized lowercase mode string.

    Raises:
        ValueError: If the mode is not one of ``cache``, ``live``, ``auto``, ``synthetic``.
    """
    raw = mode if mode is not None else os.environ.get("SPHX_MODE", "auto")
    resolved = str(raw).strip().lower()
    if resolved not in VALID_MODES:
        raise ValueError(
            f"Invalid SPHX_MODE {raw!r}; must be one of {sorted(VALID_MODES)}"
        )
    return resolved


def select_size_bucket(fov_arcsec: float) -> int:
    """Return the smallest cache size bucket (120, 240, 480, 600 arcsec) >= ``fov_arcsec``.

    Args:
        fov_arcsec: Requested field of view in arcseconds (must be in (0, 600]).

    Returns:
        Bucket size in arcseconds.

    Raises:
        ValueError: If ``fov_arcsec`` is non-positive, non-finite, or exceeds 600 arcsec.
    """
    if not np.isfinite(fov_arcsec) or fov_arcsec <= 0:
        raise ValueError(f"fov_arcsec must be positive and finite, got {fov_arcsec}")
    if fov_arcsec > SIZE_BUCKETS_ARCSEC[-1] + 1e-6:
        raise ValueError(
            f"fov_arcsec ({fov_arcsec}) exceeds maximum cutout bucket "
            f"({SIZE_BUCKETS_ARCSEC[-1]} arcsec)"
        )
    for bucket in SIZE_BUCKETS_ARCSEC:
        if fov_arcsec <= bucket + 1e-6:
            return bucket
    return SIZE_BUCKETS_ARCSEC[-1]


def _normalize_coords(ra: float, dec: float) -> tuple[float, float]:
    """Validate and normalize (ra, dec) in decimal degrees."""
    ra_f = float(ra)
    dec_f = float(dec)
    if not (np.isfinite(ra_f) and np.isfinite(dec_f)):
        raise ValueError(f"ra and dec must be finite numbers, got ra={ra}, dec={dec}")
    if not (-90.0 <= dec_f <= 90.0):
        raise ValueError(f"dec must be between -90 and 90 degrees, got {dec_f}")
    return ra_f % 360.0, dec_f


def cache_key_for(ra: float, dec: float, bucket_arcsec: int) -> str:
    """Build deterministic directory key from rounded RA, Dec, and cutout bucket."""
    ra_norm, dec_norm = _normalize_coords(ra, dec)
    ra_round = round(ra_norm, 4)
    if ra_round >= 360.0:
        ra_round = 0.0
    dec_round = round(dec_norm, 4)
    if dec_round == 0.0:
        dec_round = 0.0
    return f"ra{ra_round:08.4f}_dec{dec_round:+08.4f}_s{int(bucket_arcsec)}"


def build_cutout_url(
    uri: str,
    ra: float,
    dec: float,
    size_arcsec: float,
    *,
    base_url: str = IRSA_BASE_URL,
) -> str:
    """Construct the IRSA cutout service URL for a given artifact URI and sky box.

    The URL follows IRSA's SPHEREx cutouts specification:
    ``https://irsa.ipac.caltech.edu/<uri>?center=<ra>,<dec>d&size=<size_deg>``
    """
    ra_norm, dec_norm = _normalize_coords(ra, dec)
    if not np.isfinite(size_arcsec) or size_arcsec <= 0:
        raise ValueError(f"size_arcsec must be positive, got {size_arcsec}")
    size_deg = float(size_arcsec) / 3600.0
    clean_uri = str(uri).lstrip("/")
    if clean_uri.startswith("http://") or clean_uri.startswith("https://"):
        prefix = clean_uri
    else:
        prefix = f"{base_url.rstrip('/')}/{clean_uri}"
    sep = "&" if "?" in prefix else "?"
    return f"{prefix}{sep}center={ra_norm:.6f},{dec_norm:.6f}d&size={size_deg:.6f}"


def parse_version(uri_or_filename: str | None, header: fits.Header | None = None) -> int | None:
    """Parse integer processing pipeline version (e.g. 20 from ``l2b-v20``) from URI or header."""
    candidates: list[str] = []
    if uri_or_filename:
        candidates.append(str(uri_or_filename))
    if header is not None:
        for key in ("VERSION", "PROCVER", "PIPEVER", "L2VER", "FILENAME", "URI"):
            if key in header and header[key] is not None:
                val = header[key]
                if isinstance(val, (int, np.integer)) and not isinstance(val, bool):
                    return int(val)
                candidates.append(str(val))
    for text in candidates:
        match = _VERSION_REGEX.search(text)
        if match:
            return int(match.group(1))
    return None


def _parse_detector(
    hdul: fits.HDUList,
    uri: str | None = None,
    bandpass: str | None = None,
) -> int:
    """Extract SPHEREx detector index (1..6) from FITS headers, bandpass, or URI."""
    headers: list[fits.Header] = []
    if "IMAGE" in hdul:
        headers.append(hdul["IMAGE"].header)
    if "PRIMARY" in hdul:
        headers.append(hdul["PRIMARY"].header)
    elif len(hdul) > 0:
        headers.append(hdul[0].header)

    for hdr in headers:
        for key in ("DETECTOR", "DET_ID", "DETID", "DETNUM", "CAM_ID", "ARRAYID"):
            if key in hdr and hdr[key] is not None:
                raw = hdr[key]
                if isinstance(raw, (int, np.integer)) and not isinstance(raw, bool):
                    det = int(raw)
                    if 1 <= det <= 6:
                        return det
                text = str(raw).strip()
                if text.isdigit() and 1 <= int(text) <= 6:
                    return int(text)
                m = _DETECTOR_REGEX.search(text)
                if m:
                    return int(m.group(1) or m.group(2))

        for key in ("BANDPASS", "FILTER", "INSTRUME", "EXTNAME"):
            if key in hdr and hdr[key] is not None:
                m = _DETECTOR_REGEX.search(str(hdr[key]))
                if m:
                    return int(m.group(1) or m.group(2))

    for source in (bandpass, uri):
        if source:
            m = _DETECTOR_REGEX.search(str(source))
            if m:
                return int(m.group(1) or m.group(2))

    raise ValueError("Could not determine valid SPHEREx detector (1..6) from FITS headers or URI")


def _evaluate_wavelength_um(
    hdul: fits.HDUList,
    celestial_wcs: WCS,
    image_shape: tuple[int, int],
    ra: float,
    dec: float,
) -> float:
    """Evaluate wavelength in micrometres at the target pixel using ``WCS-WAVE`` (key ``'W'``).

    SPHEREx Level-2 ``WCS-WAVE`` extensions store a lookup-table distortion / spectral
    WCS under alternate key ``'W'`` with SIP disabled. When a synthetic test FITS
    provides ``WCS-WAVE`` via standard primary keywords or a 2D wavelength map, that
    representation is also supported cleanly.
    """
    ny, nx = image_shape
    px_center = (nx - 1) / 2.0
    py_center = (ny - 1) / 2.0

    try:
        tx, ty = celestial_wcs.celestial.world_to_pixel_values(float(ra), float(dec))
        tx_f = float(np.asarray(tx).item())
        ty_f = float(np.asarray(ty).item())
        if np.isfinite(tx_f) and np.isfinite(ty_f):
            px = float(np.clip(tx_f, 0.0, max(0.0, float(nx - 1))))
            py = float(np.clip(ty_f, 0.0, max(0.0, float(ny - 1))))
        else:
            px, py = px_center, py_center
    except Exception:
        px, py = px_center, py_center

    wave_hdu = hdul["WCS-WAVE"]
    wave_hdr = wave_hdu.header

    # 1. Try spectral WCS with key="W" (and fallback to default key=" ") on IMAGE or WCS-WAVE.
    # Per IRSA SPHEREx tutorial, key="W" on IMAGE header references the WCS-WAVE BinTable,
    # and SIP must be disabled when evaluating the spectral WCS so IMAGE's celestial SIP
    # coefficients are not applied to the wavelength lookup table coordinates.
    for target_hdu, keys in ((hdul["IMAGE"], ("W",)), (wave_hdu, ("W", " "))):
        hdr = target_hdu.header
        for key in keys:
            if key == "W" and not any(k.endswith("W") for k in hdr.keys()):
                continue
            if key == " " and "CTYPE1" not in hdr and "CRVAL1" not in hdr:
                continue
            try:
                hdr_no_sip = hdr.copy()
                sip_exact = {"A_ORDER", "B_ORDER", "AP_ORDER", "BP_ORDER", "A_DMAX", "B_DMAX"}
                for sip_key in list(hdr_no_sip.keys()):
                    if sip_key in sip_exact or (
                        sip_key.startswith(("A_", "B_", "AP_", "BP_"))
                        and any(ch.isdigit() for ch in sip_key)
                    ):
                        del hdr_no_sip[sip_key]
                with warnings_suppressed():
                    w_wcs = WCS(header=hdr_no_sip, fobj=hdul, key=key, relax=False)
                    w_wcs.sip = None
                vals = w_wcs.pixel_to_world_values(px, py)
                vals_tuple = vals if isinstance(vals, tuple) else (vals,)
                # Check CTYPE/CUNIT/CNAME to pick the wavelength coordinate axis.
                ctypes = [str(w_wcs.wcs.ctype[i]).upper() for i in range(w_wcs.naxis)]
                cunits = [str(w_wcs.wcs.cunit[i]).lower() for i in range(w_wcs.naxis)]
                chosen_val: float | None = None
                chosen_unit: str = ""
                for idx, (ct, cu) in enumerate(zip(ctypes, cunits, strict=False)):
                    if any(tag in ct for tag in ("WAVE", "AWAV", "WL", "LAMBDA")):
                        chosen_val = float(np.asarray(vals_tuple[idx]).item())
                        chosen_unit = cu
                        break
                if chosen_val is None and len(vals_tuple) >= 1:
                    chosen_val = float(np.asarray(vals_tuple[0]).item())
                    chosen_unit = cunits[0] if cunits else ""
                if chosen_val is not None and np.isfinite(chosen_val) and chosen_val > 0:
                    accepted = _accept_wavelength_um(chosen_val, chosen_unit)
                    if accepted is not None:
                        return accepted
            except Exception:
                continue

    # 2. If WCS-WAVE contains a 2D wavelength array or table, evaluate at (py, px).
    if wave_hdu.data is not None:
        arr = np.asarray(wave_hdu.data)
        if arr.ndim == 2 and arr.shape == (ny, nx):
            ix = int(np.clip(round(px), 0, nx - 1))
            iy = int(np.clip(round(py), 0, ny - 1))
            val = float(arr[iy, ix])
            if np.isfinite(val) and val > 0:
                unit = str(wave_hdr.get("BUNIT", wave_hdr.get("CUNIT1W", "um")))
                accepted = _accept_wavelength_um(val, unit)
                if accepted is not None:
                    return accepted
        elif arr.ndim >= 3 and arr.shape[-2:] == (ny, nx):
            ix = int(np.clip(round(px), 0, nx - 1))
            iy = int(np.clip(round(py), 0, ny - 1))
            val = float(arr.reshape(-1, ny, nx)[0, iy, ix])
            if np.isfinite(val) and val > 0:
                unit = str(wave_hdr.get("BUNIT", wave_hdr.get("CUNIT1W", "um")))
                accepted = _accept_wavelength_um(val, unit)
                if accepted is not None:
                    return accepted

    # 3. Scalar header keyword fallback. CRVAL1W is deliberately NOT used here: it is the
    # WCS reference value, not the wavelength at the target pixel, so using it would silently
    # mislabel the frame and corrupt the wavelength-window grouping.
    for hdr in (wave_hdr, hdul["IMAGE"].header):
        for key in ("WAVELEN", "WAVELENG", "LAMBDA", "EFF_WAVE", "CENWAVE"):
            if key in hdr and hdr[key] is not None:
                try:
                    val = float(hdr[key])
                    if np.isfinite(val) and val > 0:
                        unit = str(hdr.get("CUNIT1W", hdr.get("WAVEUNIT", "um")))
                        accepted = _accept_wavelength_um(val, unit)
                        if accepted is not None:
                            return accepted
                except (TypeError, ValueError):
                    continue

    raise ValueError("Could not evaluate finite wavelength from WCS-WAVE extension")


def _convert_wavelength_to_um(value: float, unit_str: str) -> float:
    """Convert a wavelength value to micrometres based on its unit string.

    Raises:
        ValueError: If the unit is not recognised (guessing would mislabel the frame).
    """
    u = unit_str.strip().lower()
    if u in ("um", "micron", "microns", "micrometre", "micrometer", ""):
        return float(value)
    if u == "m":
        return float(value) * 1e6
    if u == "nm":
        return float(value) * 1e-3
    if u in ("angstrom", "angstroms", "a", "aa"):
        return float(value) * 1e-4
    if u == "cm":
        return float(value) * 1e4
    raise ValueError(f"Unrecognised wavelength unit {unit_str!r}")


def _accept_wavelength_um(value: float, unit_str: str) -> float | None:
    """Return the wavelength in um, or None if the unit is unknown or the value implausible."""
    try:
        um = _convert_wavelength_to_um(value, unit_str)
    except ValueError:
        return None
    lo, hi = PLAUSIBLE_WAVELENGTH_UM
    if np.isfinite(um) and lo <= um <= hi:
        return um
    return None


class warnings_suppressed:
    """Context manager suppressing noisy astropy FITS/WCS warnings during header parsing."""

    def __enter__(self) -> None:
        import warnings

        self._ctx = warnings.catch_warnings()
        self._ctx.__enter__()
        warnings.simplefilter("ignore")

    def __exit__(self, exc_type: Any, exc_val: Any, exc_tb: Any) -> None:
        self._ctx.__exit__(exc_type, exc_val, exc_tb)


def query_images(
    ra: float,
    dec: float,
    *,
    tap_query: Callable[..., Any] | None = None,
) -> list[ImageRecord]:
    """Query IRSA TAP for SPHEREx Level-2 artifacts covering ``(ra, dec)``.

    Executes an ADQL join of ``spherex.artifact`` and ``spherex.plane`` on ``planeid``
    filtered by ``CONTAINS(POINT('ICRS', ra, dec), p.poly)``, ordered by
    ``time_bounds_lower`` ascending.

    Args:
        ra: Right Ascension in decimal degrees (ICRS).
        dec: Declination in decimal degrees (ICRS).
        tap_query: Optional callable replacement for ``Irsa.query_tap`` (used in tests).

    Returns:
        List of ``ImageRecord(uri, mjd, bandpass)`` objects sorted by MJD.

    Raises:
        NoCoverageError: When the archive returns zero matching rows.
        ArchiveUnreachableError: When the IRSA TAP query fails due to network/service error.
    """
    ra_norm, dec_norm = _normalize_coords(ra, dec)
    adql = (
        "SELECT a.uri AS uri, p.time_bounds_lower AS mjd, p.energy_bandpassname AS bandpass "
        "FROM spherex.artifact a "
        "JOIN spherex.plane p ON a.planeid = p.planeid "
        f"WHERE 1 = CONTAINS(POINT('ICRS', {ra_norm:.8f}, {dec_norm:.8f}), p.poly) "
        "ORDER BY p.time_bounds_lower ASC"
    )

    try:
        if tap_query is not None:
            result = tap_query(adql)
        else:
            try:
                from astroquery.ipac.irsa import Irsa

                result = Irsa.query_tap(adql)
            except ImportError:
                import pyvo

                service = pyvo.dal.TAPService(IRSA_TAP_URL)
                result = service.search(adql)
        table = result.to_table() if hasattr(result, "to_table") else result
    except NoCoverageError:
        raise
    except Exception as exc:
        raise ArchiveUnreachableError(
            f"IRSA archive could not be reached while querying "
            f"({ra_norm:.4f}, {dec_norm:.4f}): {exc}"
        ) from exc

    if table is None or len(table) == 0:
        raise NoCoverageError("no SPHEREx coverage at this position")

    records: list[ImageRecord] = []
    colnames = set(getattr(table, "colnames", ()))
    for row in table:
        if colnames:
            uri_val = row["uri"] if "uri" in colnames else row["a.uri"]
            mjd_val = (
                row["mjd"]
                if "mjd" in colnames
                else row["time_bounds_lower"]
            )
            if "bandpass" in colnames:
                band_val = row["bandpass"]
            elif "energy_bandpassname" in colnames:
                band_val = row["energy_bandpassname"]
            else:
                band_val = row["band"]
        elif isinstance(row, dict):
            uri_val = row.get("uri") or row.get("a.uri")
            mjd_val = row.get("mjd") if "mjd" in row else row.get("time_bounds_lower")
            band_val = (
                row.get("bandpass")
                or row.get("energy_bandpassname")
                or row.get("band")
                or ""
            )
        else:
            uri_val, mjd_val, band_val = row[0], row[1], row[2]

        if isinstance(uri_val, bytes):
            uri_val = uri_val.decode("utf-8")
        if isinstance(band_val, bytes):
            band_val = band_val.decode("utf-8")

        mjd_f = float(mjd_val)
        if not np.isfinite(mjd_f):
            continue
        records.append(
            ImageRecord(
                uri=str(uri_val).strip(),
                mjd=mjd_f,
                bandpass=str(band_val).strip(),
            )
        )

    if not records:
        raise NoCoverageError("no SPHEREx coverage at this position")

    records.sort(key=lambda r: r.mjd)
    return records


def _filter_kept_hdus(hdul: fits.HDUList) -> fits.HDUList:
    """Create a new HDUList keeping only PRIMARY, IMAGE, FLAGS, VARIANCE, ZODI, WCS-WAVE."""
    missing = [name for name in REQUIRED_HDU_NAMES if name not in hdul]
    if missing:
        raise ValueError(f"Cutout FITS is missing required HDUs: {missing}")

    kept: list[fits.hdu.base.ExtensionHDU | fits.PrimaryHDU] = []
    if "PRIMARY" in hdul:
        pri = hdul["PRIMARY"]
        kept.append(fits.PrimaryHDU(data=pri.data, header=pri.header.copy()))
    else:
        kept.append(fits.PrimaryHDU())

    for name in REQUIRED_HDU_NAMES:
        hdu = hdul[name]
        hdr = hdu.header.copy()
        hdr["EXTNAME"] = name
        if isinstance(hdu, fits.BinTableHDU):
            kept.append(fits.BinTableHDU(data=hdu.data, header=hdr, name=name))
        elif isinstance(hdu, fits.TableHDU):
            kept.append(fits.TableHDU(data=hdu.data, header=hdr, name=name))
        else:
            kept.append(
                fits.ImageHDU(
                    data=np.array(hdu.data, copy=True) if hdu.data is not None else None,
                    header=hdr,
                    name=name,
                )
            )

    return fits.HDUList(kept)


def _default_fetch_bytes(url: str, timeout: float = 30.0) -> bytes:
    """Download raw bytes from ``url`` via urllib."""
    req = Request(url, headers={"User-Agent": "ShroudX-SPHEREx-Viewer/0.1"})
    with urlopen(req, timeout=timeout) as resp:  # noqa: S310
        return resp.read()


def download_cutout(
    url: str,
    dest: str | Path,
    retries: int = 4,
    *,
    backoff_base: float = 0.5,
    timeout: float = 30.0,
    fetcher: Callable[[str], bytes | fits.HDUList] | None = None,
    sleep_fn: Callable[[float], None] = time.sleep,
    metadata: dict[str, Any] | None = None,
) -> bool:
    """Download a SPHEREx cutout FITS file with retries, stripping PSF and writing atomically.

    Keeps only ``PRIMARY``, ``IMAGE``, ``FLAGS``, ``VARIANCE``, ``ZODI``, and ``WCS-WAVE``
    (dropping ``PSF``), writes to a temporary file in the destination directory, and
    atomically renames it to ``dest``.

    Args:
        url: Cutout service URL to fetch.
        dest: Target file path on disk.
        retries: Maximum number of attempts (must be >= 1; default 4).
        backoff_base: Initial exponential backoff delay in seconds between retries.
        timeout: Per-request network timeout in seconds.
        fetcher: Optional custom callable returning raw FITS bytes or an ``HDUList``.
        sleep_fn: Sleep callable used between retry attempts (injectable for fast tests).
        metadata: Optional metadata values (``mjd``, ``bandpass``, ``uri``) stamped into
            the cached PRIMARY header so ``index.csv`` can be rebuilt if damaged.

    Returns:
        ``True`` if the cutout was downloaded and written, ``False`` after all retries fail.
    """
    if retries < 1:
        raise ValueError(f"retries must be >= 1, got {retries}")

    dest_path = Path(dest)
    dest_path.parent.mkdir(parents=True, exist_ok=True)

    # Resume support: if dest_path (or an uncompressed/compressed sibling) already exists on
    # disk for the same URI (when using the live network fetcher), reuse the valid cached file
    # instead of re-downloading.
    if fetcher is None:
        existing_path = _resolve_cached_fits_path(dest_path.parent, dest_path.name)
        if existing_path is not None and existing_path.stat().st_size > 0:
            try:
                with warnings_suppressed(), fits.open(
                    existing_path, memmap=False
                ) as existing_hdul:
                    if all(name in existing_hdul for name in REQUIRED_HDU_NAMES):
                        expected_uri = str(metadata.get("uri", "")) if metadata else ""
                        cached_uri = str(existing_hdul["PRIMARY"].header.get("SPHXURI", ""))
                        if not expected_uri or cached_uri == expected_uri:
                            if existing_path != dest_path and dest_path.name.endswith(".fits.gz"):
                                compress_fits_file(
                                    existing_path,
                                    dest_path,
                                    remove_original=True,
                                    strip_psf=False,
                                )
                            return True
            except Exception:
                pass

    for attempt in range(retries):
        tmp_path: Path | None = None
        try:
            if fetcher is not None:
                payload = fetcher(url)
            else:
                payload = _default_fetch_bytes(url, timeout=timeout)

            with warnings_suppressed():
                if isinstance(payload, fits.HDUList):
                    filtered = _filter_kept_hdus(payload)
                else:
                    with fits.open(io.BytesIO(bytes(payload)), memmap=False) as hdul:
                        filtered = _filter_kept_hdus(hdul)

                if metadata:
                    pri_hdr = filtered["PRIMARY"].header
                    if "mjd" in metadata and metadata["mjd"] is not None:
                        pri_hdr["MJD-OBS"] = float(metadata["mjd"])
                    if "bandpass" in metadata and metadata["bandpass"]:
                        pri_hdr["BANDPASS"] = str(metadata["bandpass"])
                    if "uri" in metadata and metadata["uri"]:
                        pri_hdr["SPHXURI"] = str(metadata["uri"])

                is_gz = dest_path.name.endswith(".gz")
                tmp_suffix = ".tmp.fits.gz" if is_gz else ".tmp.fits"
                stem = _fits_stem(dest_path.name)
                fd, tmp_name = tempfile.mkstemp(
                    prefix=f".{stem}_",
                    suffix=tmp_suffix,
                    dir=str(dest_path.parent),
                )
                os.close(fd)
                tmp_path = Path(tmp_name)
                filtered.writeto(tmp_path, overwrite=True, output_verify="silentfix")
            os.replace(tmp_path, dest_path)
            return True
        except Exception:
            if tmp_path is not None and tmp_path.exists():
                try:
                    tmp_path.unlink()
                except OSError:
                    pass
            if attempt + 1 < retries:
                delay = backoff_base * (2**attempt)
                if delay > 0:
                    sleep_fn(delay)
    return False


def download_cutouts_parallel(
    items: Sequence[tuple[str, Path, ImageRecord]],
    *,
    max_workers: int = MAX_DOWNLOAD_WORKERS,
    retries: int = 4,
    backoff_base: float = 0.5,
    fetcher: Callable[[str], bytes | fits.HDUList] | None = None,
    sleep_fn: Callable[[float], None] = time.sleep,
    progress_callback: Callable[[int, int, str, bool], None] | None = None,
) -> tuple[list[tuple[Path, ImageRecord]], list[str]]:
    """Download cutouts in parallel with at most 5 workers and report progress.

    Individual cutout failures are recorded in the returned failure list and reported
    to ``progress_callback`` without aborting the remaining downloads.

    Args:
        items: Sequence of ``(url, dest_path, ImageRecord)`` tuples to download.
        max_workers: Worker cap (clamped to ``1..5`` per IRSA concurrency limit).
        retries: Retry attempts per cutout.
        backoff_base: Backoff base delay in seconds.
        fetcher: Optional custom downloader callable for testing.
        sleep_fn: Optional sleep function for retry backoff.
        progress_callback: Optional ``callback(completed, total, uri, ok)`` invoked as
            each cutout finishes.

    Returns:
        ``(succeeded, failed_uris)`` where ``succeeded`` is ordered matching ``items``.
    """
    if max_workers < 1:
        raise ValueError(f"max_workers must be >= 1, got {max_workers}")
    workers = min(int(max_workers), MAX_DOWNLOAD_WORKERS)
    total = len(items)
    if total == 0:
        return [], []

    outcomes: dict[int, bool] = {}
    completed_count = 0

    with ThreadPoolExecutor(max_workers=workers) as pool:
        future_to_idx = {
            pool.submit(
                download_cutout,
                url,
                dest_path,
                retries=retries,
                backoff_base=backoff_base,
                fetcher=fetcher,
                sleep_fn=sleep_fn,
                metadata={
                    "mjd": record.mjd,
                    "bandpass": record.bandpass,
                    "uri": record.uri,
                },
            ): idx
            for idx, (url, dest_path, record) in enumerate(items)
        }
        for future in as_completed(future_to_idx):
            idx = future_to_idx[future]
            _, _, record = items[idx]
            try:
                ok = bool(future.result())
            except Exception:
                ok = False
            outcomes[idx] = ok
            completed_count += 1
            if progress_callback is not None:
                progress_callback(completed_count, total, record.uri, ok)

    succeeded: list[tuple[Path, ImageRecord]] = []
    failed_uris: list[str] = []
    for idx, (_, dest_path, record) in enumerate(items):
        if outcomes.get(idx, False):
            succeeded.append((dest_path, record))
        else:
            failed_uris.append(record.uri)

    return succeeded, failed_uris


def _write_index_csv(cache_dir: Path, rows: Sequence[tuple[str, float, str, str]]) -> None:
    """Atomically write ``index.csv`` with columns ``(file, mjd, bandpass, uri)``."""
    cache_dir.mkdir(parents=True, exist_ok=True)
    index_path = cache_dir / "index.csv"
    fd, tmp_name = tempfile.mkstemp(prefix=".index_", suffix=".csv", dir=str(cache_dir))
    os.close(fd)
    tmp_path = Path(tmp_name)
    try:
        with tmp_path.open("w", newline="", encoding="utf-8") as fh:
            writer = csv.writer(fh)
            writer.writerow(INDEX_COLUMNS)
            for fname, mjd, bandpass, uri in rows:
                writer.writerow([fname, f"{float(mjd):.8f}", bandpass, uri])
        os.replace(tmp_path, index_path)
    finally:
        if tmp_path.exists():
            try:
                tmp_path.unlink()
            except OSError:
                pass


def _extract_header_mjd(hdul: fits.HDUList) -> float:
    """Extract observation MJD from PRIMARY or IMAGE headers when rebuilding index.csv."""
    headers = []
    if "IMAGE" in hdul:
        headers.append(hdul["IMAGE"].header)
    if "PRIMARY" in hdul:
        headers.append(hdul["PRIMARY"].header)
    elif len(hdul) > 0:
        headers.append(hdul[0].header)

    for hdr in headers:
        for key in ("MJD-OBS", "MJD_OBS", "MJD", "OBSMJD", "TIME_LO"):
            if key in hdr and hdr[key] is not None:
                try:
                    val = float(hdr[key])
                    if np.isfinite(val):
                        return val
                except (TypeError, ValueError):
                    continue
        if "DATE-OBS" in hdr and hdr["DATE-OBS"]:
            try:
                return float(Time(str(hdr["DATE-OBS"])).mjd)
            except Exception:
                continue
    raise ValueError("Could not determine finite MJD from FITS header")


def _extract_header_bandpass_and_uri(hdul: fits.HDUList, fallback_name: str) -> tuple[str, str]:
    """Extract bandpass and URI from FITS headers when rebuilding index.csv."""
    headers = []
    if "PRIMARY" in hdul:
        headers.append(hdul["PRIMARY"].header)
    if "IMAGE" in hdul:
        headers.append(hdul["IMAGE"].header)

    bandpass = ""
    uri = ""
    for hdr in headers:
        if not bandpass:
            for key in ("BANDPASS", "FILTER", "DETECTOR"):
                if key in hdr and hdr[key] is not None:
                    val = hdr[key]
                    if key == "DETECTOR" and str(val).isdigit():
                        bandpass = f"SPHEREx-D{int(val)}"
                    else:
                        bandpass = str(val).strip()
                    break
        if not uri:
            for key in ("SPHXURI", "URI", "FILENAME", "ORIGFILE"):
                if key in hdr and hdr[key] is not None:
                    uri = str(hdr[key]).strip()
                    break

    if not bandpass:
        try:
            det = _parse_detector(hdul, uri=uri or fallback_name)
            bandpass = f"SPHEREx-D{det}"
        except ValueError:
            bandpass = "UNKNOWN"
    if not uri:
        uri = fallback_name
    return bandpass, uri


def _fits_stem(filename: str) -> str:
    """Strip ``.fits.gz`` or ``.fits`` from ``filename``."""
    for suffix in FITS_SUFFIXES:
        if filename.endswith(suffix):
            return filename[: -len(suffix)]
    return Path(filename).stem


def _is_cached_fits_file(path: Path) -> bool:
    """Return True if ``path`` is a non-temporary cached ``.fits`` or ``.fits.gz`` file."""
    name = path.name
    if not path.is_file() or name.startswith("."):
        return False
    if name.endswith((".tmp.fits", ".tmp.fits.gz")):
        return False
    return name.endswith(FITS_SUFFIXES)


def _iter_cached_fits_files(cache_dir: Path) -> list[Path]:
    """List cached FITS files in ``cache_dir``, preferring ``.fits.gz`` over ``.fits``."""
    if not cache_dir.is_dir():
        return []
    by_stem: dict[str, Path] = {}
    for p in cache_dir.iterdir():
        if not _is_cached_fits_file(p):
            continue
        stem = _fits_stem(p.name)
        existing = by_stem.get(stem)
        if existing is None or (
            p.name.endswith(".fits.gz") and not existing.name.endswith(".fits.gz")
        ):
            by_stem[stem] = p
    return sorted(by_stem.values(), key=lambda p: (_fits_stem(p.name), p.name))


def _resolve_cached_fits_path(cache_dir: Path, fname: str) -> Path | None:
    """Resolve ``fname`` in ``cache_dir``, falling back between ``.fits`` and ``.fits.gz``."""
    exact = cache_dir / fname
    if exact.is_file():
        return exact
    if fname.endswith(".fits"):
        gz = cache_dir / f"{fname}.gz"
        if gz.is_file():
            return gz
    elif fname.endswith(".fits.gz"):
        raw = cache_dir / fname[:-3]
        if raw.is_file():
            return raw
    return None


def _is_gzip_file(path: Path) -> bool:
    """Return True if ``path`` starts with the gzip magic bytes ``\\x1f\\x8b``."""
    with path.open("rb") as fh:
        return fh.read(2) == b"\x1f\x8b"


def compress_fits_file(
    src: str | Path,
    dest: str | Path | None = None,
    *,
    remove_original: bool = True,
    strip_psf: bool = True,
    compresslevel: int = 9,
) -> Path:
    """Atomically gzip-compress a FITS cutout to ``.fits.gz``.

    When ``strip_psf`` is True and the cutout contains a ``PSF`` extension alongside
    all required SPHEREx extensions, the ``PSF`` layer is dropped while writing the
    compressed file. Otherwise the FITS stream is compressed with ``gzip``.
    """
    src_path = Path(src)
    if not src_path.is_file():
        raise FileNotFoundError(f"FITS file not found: {src_path}")

    if dest is not None:
        dest_path = Path(dest)
    elif src_path.name.endswith(".fits.gz"):
        dest_path = src_path
    else:
        dest_path = src_path.with_name(f"{src_path.name}.gz")

    dest_path.parent.mkdir(parents=True, exist_ok=True)
    already_gz = _is_gzip_file(src_path)

    if already_gz and not strip_psf and src_path.resolve() == dest_path.resolve():
        return dest_path

    def _make_tmp() -> Path:
        stem = _fits_stem(dest_path.name)
        tmp_suffix = ".tmp.fits.gz" if dest_path.name.endswith(".gz") else ".tmp.fits"
        fd, tmp_name = tempfile.mkstemp(
            prefix=f".{stem}_",
            suffix=tmp_suffix,
            dir=str(dest_path.parent),
        )
        os.close(fd)
        return Path(tmp_name)

    tmp_path: Path | None = None
    try:
        wrote_stripped = False
        if strip_psf:
            with warnings_suppressed(), fits.open(src_path, memmap=False) as hdul:
                if "PSF" in hdul and all(name in hdul for name in REQUIRED_HDU_NAMES):
                    filtered = _filter_kept_hdus(hdul)
                    tmp_path = _make_tmp()
                    filtered.writeto(tmp_path, overwrite=True, output_verify="silentfix")
                    wrote_stripped = True

        if not wrote_stripped:
            if already_gz and src_path.resolve() == dest_path.resolve():
                return dest_path
            tmp_path = _make_tmp()
            if already_gz:
                with src_path.open("rb") as src_fh, tmp_path.open("wb") as dst_fh:
                    shutil.copyfileobj(src_fh, dst_fh)
            else:
                with (
                    src_path.open("rb") as src_fh,
                    gzip.GzipFile(tmp_path, mode="wb", compresslevel=compresslevel) as gz_fh,
                ):
                    shutil.copyfileobj(src_fh, gz_fh)

        assert tmp_path is not None
        os.replace(tmp_path, dest_path)
        tmp_path = None
    finally:
        if tmp_path is not None and tmp_path.exists():
            try:
                tmp_path.unlink()
            except OSError:
                pass

    if remove_original and src_path.resolve() != dest_path.resolve() and src_path.exists():
        src_path.unlink()

    return dest_path


def compress_cache(
    cache_root: str | Path = "cache",
    *,
    remove_original: bool = True,
    strip_psf: bool = True,
    max_workers: int = 4,
    compresslevel: int = 9,
) -> dict[str, Any]:
    """Compress all cached ``*.fits`` cutouts under ``cache_root`` into ``*.fits.gz``.

    Updates each folder's ``index.csv`` via ``read_or_rebuild_index`` and clears
    the in-process memory cache. Returns a summary dict with file counts and sizes.
    """
    croot = Path(cache_root)
    if not croot.is_dir():
        raise FileNotFoundError(f"Cache directory not found: {croot}")

    dir_set: set[Path] = set()
    for p in croot.rglob("*"):
        if _is_cached_fits_file(p):
            dir_set.add(p.parent)
    candidate_dirs = sorted(dir_set)

    uncompressed_files: list[Path] = []
    already_compressed = 0
    bytes_before = 0
    for cdir in candidate_dirs:
        for p in sorted(cdir.iterdir()):
            if not _is_cached_fits_file(p):
                continue
            bytes_before += p.stat().st_size
            if p.name.endswith(".fits"):
                uncompressed_files.append(p)
            elif p.name.endswith(".fits.gz"):
                already_compressed += 1

    files_compressed = 0
    if uncompressed_files:
        workers = max(1, int(max_workers))
        with ThreadPoolExecutor(max_workers=workers) as pool:
            futures = [
                pool.submit(
                    compress_fits_file,
                    fpath,
                    None,
                    remove_original=remove_original,
                    strip_psf=strip_psf,
                    compresslevel=compresslevel,
                )
                for fpath in uncompressed_files
            ]
            for fut in as_completed(futures):
                fut.result()
                files_compressed += 1

    for cdir in candidate_dirs:
        read_or_rebuild_index(cdir)

    bytes_after = 0
    for cdir in candidate_dirs:
        for p in cdir.iterdir():
            if not _is_cached_fits_file(p):
                continue
            if not remove_original and p.name.endswith(".fits"):
                if p.with_name(f"{p.name}.gz").is_file():
                    continue
            bytes_after += p.stat().st_size

    clear_memory_cache()
    mb_before = bytes_before / 1_000_000.0
    mb_after = bytes_after / 1_000_000.0
    saved_mb = (bytes_before - bytes_after) / 1_000_000.0
    ratio = (bytes_before / bytes_after) if bytes_after > 0 else 1.0
    return {
        "files_compressed": files_compressed,
        "files_already_compressed": already_compressed,
        "directories_updated": len(candidate_dirs),
        "bytes_before": int(bytes_before),
        "bytes_after": int(bytes_after),
        "mb_before": round(mb_before, 4),
        "mb_after": round(mb_after, 4),
        "saved_mb": round(saved_mb, 4),
        "compression_ratio": round(ratio, 3),
    }


def rebuild_cache_index(cache_dir: str | Path) -> list[tuple[str, float, str, str]]:
    """Rebuild ``index.csv`` in ``cache_dir`` by scanning cached FITS headers.

    Args:
        cache_dir: Directory containing cached ``NNNN.fits`` or ``NNNN.fits.gz`` files.

    Returns:
        List of ``(file, mjd, bandpass, uri)`` tuples sorted by MJD and written to ``index.csv``.
    """
    cdir = Path(cache_dir)
    if not cdir.is_dir():
        return []

    fits_files = _iter_cached_fits_files(cdir)
    entries: list[tuple[str, float, str, str]] = []
    for fpath in fits_files:
        try:
            with warnings_suppressed(), fits.open(fpath, memmap=False) as hdul:
                mjd = _extract_header_mjd(hdul)
                bandpass, uri = _extract_header_bandpass_and_uri(hdul, fpath.name)
            entries.append((fpath.name, mjd, bandpass, uri))
        except Exception:
            continue

    entries.sort(key=lambda item: (item[1], item[0]))
    if entries:
        _write_index_csv(cdir, entries)
    return entries


def read_or_rebuild_index(cache_dir: str | Path) -> list[tuple[str, float, str, str]]:
    """Read ``index.csv`` from ``cache_dir``, rebuilding from FITS headers if missing or damaged."""
    cdir = Path(cache_dir)
    if not cdir.is_dir():
        return []

    disk_files = _iter_cached_fits_files(cdir)
    if not disk_files:
        return []
    disk_names = {p.name for p in disk_files}

    index_path = cdir / "index.csv"
    if not index_path.is_file():
        return rebuild_cache_index(cdir)

    try:
        rows: list[tuple[str, float, str, str]] = []
        mapped = False
        with index_path.open("r", newline="", encoding="utf-8") as fh:
            reader = csv.DictReader(fh)
            if reader.fieldnames is None or tuple(reader.fieldnames) != INDEX_COLUMNS:
                return rebuild_cache_index(cdir)
            for row in reader:
                fname = (row.get("file") or "").strip()
                mjd_str = (row.get("mjd") or "").strip()
                bandpass = (row.get("bandpass") or "").strip()
                uri = (row.get("uri") or "").strip()
                if not fname or not mjd_str or not uri:
                    return rebuild_cache_index(cdir)
                resolved = _resolve_cached_fits_path(cdir, fname)
                if resolved is None or resolved.name not in disk_names:
                    return rebuild_cache_index(cdir)
                if resolved.name != fname:
                    mapped = True
                mjd = float(mjd_str)
                if not np.isfinite(mjd):
                    return rebuild_cache_index(cdir)
                rows.append((resolved.name, mjd, bandpass, uri))
        if len(rows) != len(disk_files) or {r[0] for r in rows} != disk_names:
            return rebuild_cache_index(cdir)
        if mapped:
            _write_index_csv(cdir, rows)
        return rows
    except Exception:
        return rebuild_cache_index(cdir)


def _crop_frame_to_fov(
    frame: Frame,
    ra: float,
    dec: float,
    fov_arcsec: float,
) -> Frame:
    """Crop a larger ``Frame`` around ``(ra, dec)`` to ``fov_arcsec`` using ``Cutout2D``."""
    if frame.wcs is None:
        return frame

    cel_wcs = frame.wcs.celestial
    ny, nx = frame.image.shape

    # Determine pixel scale from WCS (or fallback to PIXEL_SCALE_ARCSEC)
    try:
        scales_deg = cel_wcs.proj_plane_pixel_scales()
        scale_arcsec = float(np.mean([s.to_value("arcsec") for s in scales_deg]))
        if not np.isfinite(scale_arcsec) or scale_arcsec <= 0:
            scale_arcsec = PIXEL_SCALE_ARCSEC
    except Exception:
        scale_arcsec = PIXEL_SCALE_ARCSEC

    target_npix = max(1, int(np.round(float(fov_arcsec) / scale_arcsec)))
    if target_npix >= ny and target_npix >= nx:
        return frame

    position = SkyCoord(ra=float(ra), dec=float(dec), unit="deg", frame="icrs")
    cutout_shape = (min(ny, target_npix), min(nx, target_npix))

    img_cut = Cutout2D(
        frame.image,
        position=position,
        size=cutout_shape,
        wcs=cel_wcs,
        mode="trim",
    )
    if img_cut.data.size == 0:
        return frame

    yslice, xslice = img_cut.slices_original
    var_cut = np.array(frame.variance[yslice, xslice], copy=True)
    mask_cut = np.array(frame.mask[yslice, xslice], dtype=bool, copy=True)
    zodi_cut = (
        np.array(frame.zodi[yslice, xslice], copy=True)
        if frame.zodi is not None
        else None
    )
    unrel_cut = (
        np.array(frame.unreliable[yslice, xslice], dtype=bool, copy=True)
        if frame.unreliable is not None
        else None
    )
    meta = dict(frame.meta) if frame.meta is not None else {}
    meta["cropped_from_shape"] = (ny, nx)
    meta["fov_arcsec"] = float(fov_arcsec)

    return frame.copy_with(
        image=np.array(img_cut.data, copy=True),
        variance=var_cut,
        mask=mask_cut,
        wcs=img_cut.wcs.deepcopy(),
        zodi=zodi_cut,
        unreliable=unrel_cut,
        meta=meta,
    )


def frame_from_hdul(
    hdul: fits.HDUList,
    ra: float,
    dec: float,
    mjd: float,
    uri: str | None = None,
    *,
    bandpass: str | None = None,
) -> Frame:
    """Construct a validated ``Frame`` from an open SPHEREx cutout ``HDUList``.

    Always accesses FITS extensions by name (never by index):
    - ``IMAGE``: surface brightness in MJy/sr (zodiacal light not subtracted).
    - ``VARIANCE``: pixel variance in (MJy/sr)^2.
    - ``FLAGS``: 32-bit flag bitmask; ``mask = DETECTOR_DEFECTS | non-finite | variance <= 0``;
      ``unreliable = UNRELIABLE_CORE``.
    - ``ZODI``: zodiacal light model in MJy/sr.
    - ``WCS-WAVE``: spectral WCS (key ``'W'``) evaluated at the target pixel ``(ra, dec)``.

    Args:
        hdul: Open ``astropy.io.fits.HDUList`` containing the SPHEREx cutout layers.
        ra: Target Right Ascension in decimal degrees (ICRS).
        dec: Target Declination in decimal degrees (ICRS).
        mjd: Observation Modified Julian Date.
        uri: Source URI or filename used to record provenance and parse processing version.
        bandpass: Optional bandpass string from the TAP query.

    Returns:
        Validated ``Frame`` object.
    """
    missing = [name for name in REQUIRED_HDU_NAMES if name not in hdul]
    if missing:
        raise ValueError(f"HDUList is missing required named extensions: {missing}")

    img_hdu = hdul["IMAGE"]
    var_hdu = hdul["VARIANCE"]
    flg_hdu = hdul["FLAGS"]
    zodi_hdu = hdul["ZODI"]

    image = np.array(img_hdu.data, dtype=np.float64, copy=True)
    variance = np.array(var_hdu.data, dtype=np.float64, copy=True)
    flags_arr = np.asarray(flg_hdu.data)
    zodi = np.array(zodi_hdu.data, dtype=np.float64, copy=True)

    defect_mask = flags_to_mask(flags_arr, DETECTOR_DEFECTS)
    nonfinite_mask = (~np.isfinite(image)) | (~np.isfinite(variance)) | (~np.isfinite(zodi))
    nonpos_var_mask = ~(variance > 0)
    mask = np.asarray(defect_mask | nonfinite_mask | nonpos_var_mask, dtype=bool)

    unreliable = np.asarray(flags_to_mask(flags_arr, UNRELIABLE_CORE), dtype=bool)

    with warnings_suppressed():
        cel_wcs = WCS(img_hdu.header).celestial

    detector = _parse_detector(hdul, uri=uri, bandpass=bandpass)
    wavelength = _evaluate_wavelength_um(hdul, cel_wcs, image.shape, ra, dec)

    pri_hdr = hdul["PRIMARY"].header if "PRIMARY" in hdul else img_hdu.header
    version = parse_version(uri, header=img_hdu.header) or parse_version(uri, header=pri_hdr)

    meta: dict[str, Any] = {
        "ra": float(ra),
        "dec": float(dec),
    }
    if bandpass:
        meta["bandpass"] = str(bandpass)
    for key in ("DATE-OBS", "EXPTIME", "FILTER", "OBJECT"):
        if key in img_hdu.header:
            meta[key] = img_hdu.header[key]

    return Frame(
        image=image,
        variance=variance,
        mask=mask,
        wavelength=float(wavelength),
        detector=int(detector),
        mjd=float(mjd),
        wcs=cel_wcs,
        zodi=zodi,
        version=version,
        uri=str(uri) if uri is not None else None,
        unreliable=unreliable,
        meta=meta,
    )


def _find_cached_bucket(
    cache_root: Path,
    ra: float,
    dec: float,
    requested_fov_arcsec: float,
) -> tuple[Path, int, list[tuple[str, float, str, str]]] | None:
    """Locate an existing cache folder with size bucket >= ``requested_fov_arcsec``."""
    min_bucket = select_size_bucket(requested_fov_arcsec)
    for bucket in SIZE_BUCKETS_ARCSEC:
        if bucket < min_bucket:
            continue
        key = cache_key_for(ra, dec, bucket)
        candidate_dir = cache_root / key
        rows = read_or_rebuild_index(candidate_dir)
        if rows:
            return candidate_dir, bucket, rows
    return None


def _load_frames_from_cache_dir(
    cache_dir: Path,
    rows: Sequence[tuple[str, float, str, str]],
    ra: float,
    dec: float,
    fov_arcsec: float,
    bucket_arcsec: int,
) -> tuple[Frame, ...]:
    """Load, optionally crop, and MJD-sort ``Frame`` objects from a cache directory."""
    loaded: list[Frame] = []
    need_crop = float(fov_arcsec) < float(bucket_arcsec) - 1e-6
    for fname, mjd, bandpass, uri in rows:
        fpath = _resolve_cached_fits_path(cache_dir, fname)
        if fpath is None:
            continue
        with warnings_suppressed(), fits.open(fpath, memmap=False) as hdul:
            frame = frame_from_hdul(
                hdul,
                ra=ra,
                dec=dec,
                mjd=mjd,
                uri=uri,
                bandpass=bandpass,
            )
        if frame.meta is None:
            frame.meta = {}
        frame.meta["data_source"] = "cache"
        frame.meta["cache_bucket_arcsec"] = bucket_arcsec
        frame.meta["synthetic"] = False
        if need_crop:
            frame = _crop_frame_to_fov(frame, ra=ra, dec=dec, fov_arcsec=fov_arcsec)
        loaded.append(frame)

    loaded.sort(key=lambda f: f.mjd)
    return tuple(loaded)


def _generate_synthetic_frames(
    ra: float,
    dec: float,
    fov_arcsec: float,
    *,
    detector: int = 5,
) -> tuple[Frame, ...]:
    """Generate explicit synthetic SPHEREx frames with a moving source and chromatic star.

    Used ONLY when ``SPHX_MODE=synthetic`` is explicitly set. Sets
    ``meta["synthetic"] = True`` and ``meta["data_source"] = "synthetic"`` on every ``Frame``.
    """
    ra_norm, dec_norm = _normalize_coords(ra, dec)
    npix = max(9, int(np.round(float(fov_arcsec) / PIXEL_SCALE_ARCSEC)))
    scale_deg = PIXEL_SCALE_ARCSEC / 3600.0

    wcs = WCS(naxis=2)
    wcs.wcs.ctype = ["RA---TAN", "DEC--TAN"]
    wcs.wcs.crval = [ra_norm, dec_norm]
    wcs.wcs.crpix = [(npix + 1) / 2.0, (npix + 1) / 2.0]
    wcs.wcs.cdelt = [-scale_deg, scale_deg]
    wcs.wcs.cunit = ["deg", "deg"]
    wcs.wcs.radesys = "ICRS"

    w_lo, w_hi = DETECTOR_RANGES_UM.get(detector, (3.83, 4.41))
    w_mid = 0.5 * (w_lo + w_hi)

    # Two epochs separated by ~180 days, plus one out-of-window wavelength frame
    schedule = [
        (60900.1, w_mid - 0.02, 0),
        (60901.4, w_mid + 0.01, 0),
        (61082.2, w_mid - 0.01, 1),
        (61083.6, w_mid + 0.02, 1),
        (61084.5, min(w_hi, w_mid + 0.22), 1),
    ]

    yy, xx = np.mgrid[0:npix, 0:npix]
    cx = (npix - 1) / 2.0
    cy = (npix - 1) / 2.0
    sigma_psf = 1.1

    frames: list[Frame] = []
    for idx, (mjd, wl, epoch_idx) in enumerate(schedule):
        rng = np.random.default_rng(1000 + idx)
        # Moving source shifts by ~2.5 pixels between epoch 0 and epoch 1
        dx = -1.25 + 2.5 * epoch_idx + 0.05 * (idx % 2)
        dy = -0.6 + 1.2 * epoch_idx
        mover_flux = 35.0 * (1.0 + 0.15 * ((wl - w_mid) / 0.1))
        mover = mover_flux * np.exp(
            -0.5 * (((xx - (cx + dx)) ** 2 + (yy - (cy + dy)) ** 2) / (sigma_psf**2))
        )

        # Stationary star with strong wavelength-dependent brightness
        star_x = max(2.0, cx - 4.0)
        star_y = min(float(npix - 3), cy + 3.0)
        star_flux = 50.0 * ((wl / w_mid) ** (-2.5))
        star = star_flux * np.exp(
            -0.5 * (((xx - star_x) ** 2 + (yy - star_y) ** 2) / (sigma_psf**2))
        )

        zodi = np.full((npix, npix), 12.0 + 0.5 * (wl - w_lo), dtype=np.float64)
        variance = np.full((npix, npix), 0.25, dtype=np.float64)
        noise = rng.normal(0.0, 0.5, size=(npix, npix))
        image = zodi + mover + star + noise

        mask = np.zeros((npix, npix), dtype=bool)
        unreliable = np.zeros((npix, npix), dtype=bool)

        frames.append(
            Frame(
                image=image,
                variance=variance,
                mask=mask,
                wavelength=float(wl),
                detector=detector,
                mjd=float(mjd),
                wcs=wcs.deepcopy(),
                zodi=zodi,
                version=20,
                uri=f"synthetic://spherex/l2b-v20/synth_{idx:04d}.fits",
                unreliable=unreliable,
                meta={
                    "synthetic": True,
                    "data_source": "synthetic",
                    "ra": ra_norm,
                    "dec": dec_norm,
                    "fov_arcsec": float(fov_arcsec),
                },
            )
        )

    frames.sort(key=lambda f: f.mjd)
    return tuple(frames)


# In-process LRU cache keyed by (mode, cache_root_str, rounded_ra, rounded_dec, rounded_fov).
# Not thread-safe on its own: app.py serializes pipeline builds with a lock.
_MEMORY_CACHE: dict[tuple[str, str, float, float, float], tuple[Frame, ...]] = {}
_MEMORY_CACHE_MAX: int = 32


def clear_memory_cache() -> None:
    """Clear the in-process ``get_frames`` frame cache."""
    _MEMORY_CACHE.clear()


def _remember(key: tuple[str, str, float, float, float], frames: Sequence[Frame]) -> None:
    """Store a defensive copy of ``frames`` as the most recently used cache entry."""
    _MEMORY_CACHE.pop(key, None)
    while len(_MEMORY_CACHE) >= _MEMORY_CACHE_MAX:
        _MEMORY_CACHE.pop(next(iter(_MEMORY_CACHE)))
    _MEMORY_CACHE[key] = _clone_frames(frames)


def _write_incomplete_marker(cache_dir: Path, failed: int, total: int) -> None:
    """Record (or clear) the fact that some cutouts in ``cache_dir`` failed to download."""
    marker = cache_dir / INCOMPLETE_MARKER
    if failed <= 0:
        marker.unlink(missing_ok=True)
        return
    fd, tmp_name = tempfile.mkstemp(prefix=".incomplete_", suffix=".tmp", dir=str(cache_dir))
    os.close(fd)
    tmp_path = Path(tmp_name)
    try:
        tmp_path.write_text(json.dumps({"failed": int(failed), "total": int(total)}), "utf-8")
        os.replace(tmp_path, marker)
    finally:
        tmp_path.unlink(missing_ok=True)


def _read_incomplete_marker(cache_dir: Path) -> int:
    """Return how many cutouts the marker says are missing (0 when there is no marker)."""
    marker = cache_dir / INCOMPLETE_MARKER
    if not marker.is_file():
        return 0
    try:
        return max(1, int(json.loads(marker.read_text("utf-8"))["failed"]))
    except Exception:
        return 1  # An unreadable marker still means "do not trust this cache as complete".


def _tag_failed_downloads(frames: Sequence[Frame], failed: int) -> None:
    """Record the number of missing cutouts on each frame so the API can warn about it."""
    if failed <= 0:
        return
    for f in frames:
        if f.meta is None:
            f.meta = {}
        f.meta["failed_downloads"] = int(failed)


def _clone_frames(
    frames: Sequence[Frame],
    *,
    data_source_override: str | None = None,
) -> tuple[Frame, ...]:
    """Return defensive copies of cached ``Frame`` objects so callers cannot mutate cache state."""
    cloned: list[Frame] = []
    for f in frames:
        meta = dict(f.meta) if f.meta is not None else {}
        if data_source_override is not None:
            meta["data_source"] = data_source_override
        cloned.append(
            f.copy_with(
                image=np.array(f.image, copy=True),
                variance=np.array(f.variance, copy=True),
                mask=np.array(f.mask, dtype=bool, copy=True),
                wcs=f.wcs.deepcopy() if f.wcs is not None else None,
                zodi=np.array(f.zodi, copy=True) if f.zodi is not None else None,
                unreliable=(
                    np.array(f.unreliable, dtype=bool, copy=True)
                    if f.unreliable is not None
                    else None
                ),
                meta=meta,
            )
        )
    return tuple(cloned)


def get_frames(
    ra: float,
    dec: float,
    fov: float = 120.0,
    *,
    mode: str | None = None,
    cache_root: str | Path = "cache",
    max_workers: int = MAX_DOWNLOAD_WORKERS,
    retries: int = 4,
    backoff_base: float = 0.5,
    tap_query: Callable[..., Any] | None = None,
    fetcher: Callable[[str], bytes | fits.HDUList] | None = None,
    sleep_fn: Callable[[float], None] = time.sleep,
    progress_callback: Callable[[int, int, str, bool], None] | None = None,
    use_memory_cache: bool = True,
) -> tuple[Frame, ...]:
    """Return a tuple of SPHEREx ``Frame`` objects covering ``(ra, dec)`` sorted by MJD.

    Respects ``SPHX_MODE`` (or the explicit ``mode`` argument):
    - ``cache``: serve from ``cache/<key>/`` only; raise ``NotCachedError`` if not cached.
    - ``live``: query IRSA TAP, download cutouts in parallel (<= 5 workers), cache to disk,
      and return ``Frame`` objects.
    - ``auto``: serve from disk cache if a matching or larger cutout bucket is cached;
      otherwise query IRSA and cache. If IRSA is unreachable, raise ``ArchiveUnreachableError``.
      Never falls back to synthetic data.
    - ``synthetic``: explicit synthetic mode only; every returned ``Frame`` has
      ``frame.meta["synthetic"] == True`` and ``frame.meta["data_source"] == "synthetic"``.

    Args:
        ra: Target Right Ascension in decimal degrees (ICRS).
        dec: Target Declination in decimal degrees (ICRS).
        fov: Requested field of view in arcseconds (up to 600 arcsec).
        mode: Optional mode override (defaults to ``os.environ.get("SPHX_MODE", "auto")``).
        cache_root: Root directory for cached cutouts (default ``"cache"``).
        max_workers: Maximum parallel download workers (capped at 5).
        retries: Per-cutout download retry count.
        backoff_base: Initial retry backoff in seconds.
        tap_query: Optional injectable TAP query callable for tests.
        fetcher: Optional injectable cutout fetcher callable for tests.
        sleep_fn: Optional sleep callable for retry backoff.
        progress_callback: Optional progress reporter ``(completed, total, uri, ok)``.
        use_memory_cache: Whether to use in-process caching across repeated calls.

    Returns:
        Tuple of validated ``Frame`` objects sorted by ascending ``mjd``.
    """
    resolved_mode = get_data_mode(mode)
    ra_norm, dec_norm = _normalize_coords(ra, dec)
    bucket_arcsec = select_size_bucket(fov)
    croot = Path(cache_root)

    mem_key = (
        resolved_mode,
        str(croot.resolve()),
        round(ra_norm, 4),
        round(dec_norm, 4),
        round(float(fov), 2),
    )
    # ``live`` means "ask the archive", so it never short-circuits on the in-process cache.
    if use_memory_cache and resolved_mode != "live" and mem_key in _MEMORY_CACHE:
        cached_source = "synthetic" if resolved_mode == "synthetic" else "cache"
        _MEMORY_CACHE[mem_key] = _MEMORY_CACHE.pop(mem_key)  # mark most recently used
        return _clone_frames(_MEMORY_CACHE[mem_key], data_source_override=cached_source)

    if resolved_mode == "synthetic":
        synth_frames = _generate_synthetic_frames(ra_norm, dec_norm, fov)
        if use_memory_cache:
            _remember(mem_key, synth_frames)
        return synth_frames

    disk_frames: tuple[Frame, ...] = ()
    disk_missing = 0
    if resolved_mode in ("cache", "auto"):
        hit = _find_cached_bucket(croot, ra_norm, dec_norm, fov)
        if hit is not None:
            cache_dir, found_bucket, rows = hit
            disk_missing = _read_incomplete_marker(cache_dir)
            disk_frames = _load_frames_from_cache_dir(
                cache_dir,
                rows,
                ra=ra_norm,
                dec=dec_norm,
                fov_arcsec=fov,
                bucket_arcsec=found_bucket,
            )
            # An incomplete cache is only trusted as-is in offline ``cache`` mode; ``auto``
            # mode retries the missing cutouts below.
            if disk_frames and not (disk_missing and resolved_mode == "auto"):
                _tag_failed_downloads(disk_frames, disk_missing)
                if use_memory_cache:
                    _remember(mem_key, disk_frames)
                return disk_frames
        if resolved_mode == "cache":
            raise NotCachedError(
                f"Position (RA={ra_norm:.4f}, Dec={dec_norm:.4f}, FOV={fov:.0f}\") "
                "is not in the local cutout cache (SPHX_MODE=cache)."
            )

    # live mode, or auto mode on cache miss / incomplete cache: query IRSA TAP and download.
    try:
        frames = _fetch_live_frames(
            ra_norm,
            dec_norm,
            fov,
            bucket_arcsec,
            croot,
            max_workers=max_workers,
            retries=retries,
            backoff_base=backoff_base,
            tap_query=tap_query,
            fetcher=fetcher,
            sleep_fn=sleep_fn,
            progress_callback=progress_callback,
        )
    except DataError:
        if disk_frames and disk_missing:
            # The retry could not reach IRSA: serve what is on disk, flagged as incomplete,
            # and do not memoise it so the next request tries again.
            _tag_failed_downloads(disk_frames, disk_missing)
            return disk_frames
        raise

    if use_memory_cache:
        _remember(mem_key, frames)
    return frames


def _fetch_live_frames(
    ra_norm: float,
    dec_norm: float,
    fov: float,
    bucket_arcsec: int,
    croot: Path,
    *,
    max_workers: int,
    retries: int,
    backoff_base: float,
    tap_query: Callable[..., Any] | None,
    fetcher: Callable[[str], bytes | fits.HDUList] | None,
    sleep_fn: Callable[[float], None],
    progress_callback: Callable[[int, int, str, bool], None] | None,
) -> tuple[Frame, ...]:
    """Query IRSA, download missing cutouts into the disk cache, and load them as Frames."""
    records = query_images(ra_norm, dec_norm, tap_query=tap_query)
    key = cache_key_for(ra_norm, dec_norm, bucket_arcsec)
    target_cache_dir = croot / key
    target_cache_dir.mkdir(parents=True, exist_ok=True)

    download_jobs: list[tuple[str, Path, ImageRecord]] = []
    for idx, rec in enumerate(records, start=1):
        fname = f"{idx:04d}{DEFAULT_CACHE_EXTENSION}"
        dest_path = target_cache_dir / fname
        url = build_cutout_url(rec.uri, ra_norm, dec_norm, bucket_arcsec)
        download_jobs.append((url, dest_path, rec))

    succeeded, failed_uris = download_cutouts_parallel(
        download_jobs,
        max_workers=max_workers,
        retries=retries,
        backoff_base=backoff_base,
        fetcher=fetcher,
        sleep_fn=sleep_fn,
        progress_callback=progress_callback,
    )

    if not succeeded:
        raise ArchiveUnreachableError(
            f"All {len(download_jobs)} SPHEREx cutout downloads from IRSA failed "
            f"for position (RA={ra_norm:.4f}, Dec={dec_norm:.4f})."
        )

    index_rows = [
        (dest_path.name, rec.mjd, rec.bandpass, rec.uri)
        for dest_path, rec in succeeded
    ]
    index_rows.sort(key=lambda item: (item[1], item[0]))
    _write_index_csv(target_cache_dir, index_rows)
    _write_incomplete_marker(target_cache_dir, len(failed_uris), len(download_jobs))

    frames = _load_frames_from_cache_dir(
        target_cache_dir,
        index_rows,
        ra=ra_norm,
        dec=dec_norm,
        fov_arcsec=fov,
        bucket_arcsec=bucket_arcsec,
    )
    if not frames:
        raise ArchiveUnreachableError(
            "Downloaded SPHEREx cutouts could not be parsed into valid Frames."
        )

    for f in frames:
        if f.meta is None:
            f.meta = {}
        f.meta["data_source"] = "archive"
        f.meta["synthetic"] = False
    _tag_failed_downloads(frames, len(failed_uris))
    return frames


def _build_cli_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Compress cached SPHEREx FITS cutouts to .fits.gz in-place."
    )
    parser.add_argument(
        "--cache-dir",
        type=Path,
        default=Path("cache"),
        help="Root cache directory containing SPHEREx cutout folders (default: cache).",
    )
    parser.add_argument(
        "--workers",
        type=int,
        default=4,
        help="Number of parallel compression workers (default: 4).",
    )
    parser.add_argument(
        "--keep-original",
        action="store_true",
        help="Keep uncompressed .fits files after creating .fits.gz copies.",
    )
    parser.add_argument(
        "--no-strip-psf",
        action="store_true",
        help="Do not strip the PSF extension while compressing .fits files.",
    )
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    """CLI entry point to compress an existing SPHEREx cutout cache to ``.fits.gz``."""
    parser = _build_cli_parser()
    args = parser.parse_args(argv)
    try:
        summary = compress_cache(
            args.cache_dir,
            remove_original=not args.keep_original,
            strip_psf=not args.no_strip_psf,
            max_workers=args.workers,
        )
    except FileNotFoundError as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 1

    print(
        f"Compressed {summary['files_compressed']} FITS file(s) across "
        f"{summary['directories_updated']} folder(s) "
        f"({summary['files_already_compressed']} already compressed): "
        f"{summary['mb_before']:.2f} MB -> {summary['mb_after']:.2f} MB "
        f"(saved {summary['saved_mb']:.2f} MB, ratio {summary['compression_ratio']:.2f}x)."
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
