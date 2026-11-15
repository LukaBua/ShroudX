"""data.py - getting frames. Owner: backend developer.

Three modes, chosen with the environment variable SPHX_MODE:

    auto              ./cache if it has this position, otherwise download from IRSA;
                      failures are reported, never replaced by mock frames
    mock              fake frames with a moving star, no network
    cache  (default)  only read frames already saved in ./cache (offline demo)
    live              read ./cache if it has this position, otherwise download
                      from IRSA and save to ./cache

Real data flow (live mode):
    query_images()      one TAP query: every SPHEREx image covering the position
    download_cutout()   small cutout per image, saved to cache/<key>/NNNN.fits
    frame_from_hdul()   read IMAGE, VARIANCE, FLAGS, ZODI, WCS-WAVE BY NAME -> Frame
Recipe follows the IRSA tutorial "Download a collection of SPHEREx Spectral
Image cutouts" (caltech-ipac.github.io/irsa-tutorials/spherex-cutouts).
"""

from __future__ import annotations

import concurrent.futures
import csv
import hashlib
import json
import logging
import os
import re
import tempfile
import threading
import time
import warnings
from functools import lru_cache, partial
from pathlib import Path

from astropy.io import fits
from astropy.wcs import FITSFixedWarning

warnings.filterwarnings("ignore", category=FITSFixedWarning)
logging.getLogger("astropy").setLevel(logging.ERROR)

import numpy as np

from .flags import flags_to_mask
from .frame import Frame

CACHE_DIR = Path(__file__).parent / "cache"
CACHE_INDEX_LOCK = threading.RLock()
PIXEL_SCALE = 6.2  # arcsec per pixel, approximate for SPHEREx
CUTOUT_BUCKETS = (120, 240, 480, 600)  # arcsec; we download the smallest bucket >= fov
LAYERS = ("IMAGE", "FLAGS", "VARIANCE", "ZODI", "WCS-WAVE")  # PSF is dropped: it is huge

TAP_QUERY = """
SELECT
    a.uri AS uri,
    p.time_bounds_lower AS mjd,
    p.energy_bandpassname AS bandpass
FROM spherex.artifact a
JOIN spherex.plane p ON a.planeid = p.planeid
WHERE 1 = CONTAINS(POINT('ICRS', {ra}, {dec}), p.poly)
ORDER BY p.time_bounds_lower
"""

# Documented file name:  level2_<ObsID>D<detector>_spx_l2b-v<version>-<date>.fits
FILENAME_RE = re.compile(
    r"level2_(?P<week>\d{4}W\d{2})_(?P<passlabel>[0-9A-Za-z]+)_(?P<field>\d+)_(?P<exp>\d+)"
    r"D(?P<det>\d)_spx_l2b(?:_retry)?-v(?P<ver>\d+)"
)


def mode() -> str:
    return os.environ.get("SPHX_MODE", "cache").lower()


def cutout_bucket(fov_arcsec: float) -> int:
    """Smallest standard cutout size (arcsec) that contains the requested field."""
    for b in CUTOUT_BUCKETS:
        if fov_arcsec <= b:
            return b
    return CUTOUT_BUCKETS[-1]


def cache_key(ra: float, dec: float, fov: float) -> str:
    raw = f"{ra:.5f}|{dec:.5f}|{fov:.1f}"
    return hashlib.sha1(raw.encode()).hexdigest()[:12]


# ----------------------------------------------------------------------
# MOCK DATA: a star that moves, with colour-dependent brightness
# ----------------------------------------------------------------------
def make_mock_frames(ra: float, dec: float, fov: float, n_frames: int = 16) -> list[Frame]:
    """Fake frames that reproduce the central SPHEREx problem:
    * a fixed bright star whose brightness depends on wavelength (flicker),
    * a faint mover drifting across the field,
    * a few bad pixels.
    """
    rng = np.random.default_rng(int(cache_key(ra, dec, fov), 16) % (2**32))
    n = int(np.clip(round(fov / PIXEL_SCALE), 24, 96))
    yy, xx = np.mgrid[:n, :n]
    frames: list[Frame] = []
    for i in range(n_frames):
        wavelength = 2.0 + 0.2 * rng.random()  # 2.0 to 2.2 micrometres
        mjd = 61000.0 + 15.0 * i  # one frame every 15 days
        sigma = 2.0
        # fixed star: brightness changes with colour
        colour = 1.0 + 3.0 * (wavelength - 2.1)
        fixed = 80 * colour * np.exp(-((xx - 0.3 * n) ** 2 + (yy - 0.35 * n) ** 2) / (2 * 1.4**2))
        # mover: drifts about 0.4 pixel per frame
        mx = 0.2 * n + 0.4 * i
        my = 0.7 * n - 0.15 * i
        mover = 40 * np.exp(-((xx - mx) ** 2 + (yy - my) ** 2) / (2 * 1.4**2))
        img = fixed + mover + rng.normal(0, sigma, (n, n))
        var = np.full((n, n), sigma**2)
        mask = np.zeros((n, n), bool)
        bad = rng.integers(0, n, size=(3, 2))
        for by, bx in bad:
            mask[by, bx] = True
            img[by, bx] = np.nan
        frames.append(Frame(img, var, mask, float(wavelength), 3, mjd))
    return frames


# ----------------------------------------------------------------------
# REAL DATA
# ----------------------------------------------------------------------
def query_images(ra: float, dec: float) -> list[dict]:
    """Query IRSA SIA2 for every SPHEREx spectral image whose footprint covers
    (ra, dec). Returns rows with uri, mjd (observation time), bandpass."""
    import data as root_data

    t0 = time.time()
    records = root_data.query_sia_images(ra, dec)
    print(f"SIA query finished in {time.time() - t0:.1f} s, {len(records)} images found.")
    return [{"uri": str(r.uri), "mjd": float(r.mjd), "bandpass": str(r.bandpass)} for r in records]


def parse_rows(rows: list[dict]) -> list[dict]:
    """Add detector, week, pass label, field, exposure and processing version
    by reading the file name. Rows whose name does not match keep None."""
    out = []
    for r in rows:
        m = FILENAME_RE.search(r["uri"])
        d = dict(r)
        if m:
            d.update(
                detector=int(m["det"]),
                week=m["week"],
                passlabel=m["passlabel"],
                field=m["field"],
                exp=m["exp"],
                version=int(m["ver"]),
            )
        else:
            d.update(detector=None, week=None, passlabel=None, field=None, exp=None, version=None)
        out.append(d)
    return out


def cutout_url(uri: str, ra: float, dec: float, size_deg: float) -> str:
    return f"https://irsa.ipac.caltech.edu/{uri}?center={ra},{dec}d&size={size_deg}"


def download_cutout(url: str, dest: Path, retries: int = 4) -> bool:
    """Download one cutout and save it WITHOUT the PSF extension.
    Retries with back-off because IRSA drops requests now and then.
    Returns True on success."""
    import http.client
    import urllib.error

    from astropy.utils.data import conf

    conf.remote_timeout = 120
    last = None
    tmp = dest.with_suffix(".tmp")
    for attempt in range(retries):
        try:
            with fits.open(url, cache=False, memmap=False) as hdul:
                keep = [fits.PrimaryHDU(header=hdul[0].header)]
                keep += [hdul[name].copy() for name in LAYERS]
                tmp.parent.mkdir(parents=True, exist_ok=True)
                fits.HDUList(keep).writeto(tmp, overwrite=True)
            tmp.replace(dest)
            return True
        except (
            TimeoutError,
            urllib.error.HTTPError,
            urllib.error.URLError,
            http.client.IncompleteRead,
            http.client.RemoteDisconnected,
            OSError,
        ) as exc:
            last = exc
            if tmp.exists():
                try:
                    tmp.unlink()
                except OSError:
                    pass
            time.sleep(3 * (attempt + 1))
    print(f"  giving up on {url[-80:]}  ({last})")
    return False


def wavelength_from_hdul(hdul, ra: float, dec: float) -> float:
    """Wavelength (micrometres) at the target position, from the spectral WCS
    (key W, built from the WCS-WAVE table). Recipe from the IRSA tutorial."""
    from astropy import units as u
    from astropy.coordinates import SkyCoord
    from astropy.wcs import WCS

    header = hdul["IMAGE"].header
    x, y = WCS(header).world_to_pixel(SkyCoord(ra=ra * u.deg, dec=dec * u.deg))
    spectral = WCS(header, fobj=hdul, key="W")
    spectral.sip = None  # SIP distortion must be off for the spectral WCS
    wavelength, _bandwidth = spectral.pixel_to_world(x, y)
    return float(wavelength.to(u.micrometer).value)


def frame_from_hdul(hdul, ra: float, dec: float, mjd: float, uri: str | None = None) -> Frame:
    """Build a Frame from an opened cutout. Layers are read BY NAME.
    Nothing is subtracted here: the image is returned as delivered (MJy/sr)
    and ZODI is stored next to it. Cleaning belongs to the stats module."""
    from astropy.wcs import WCS

    header = hdul["IMAGE"].header
    image = np.array(hdul["IMAGE"].data, dtype=float)
    variance = np.array(hdul["VARIANCE"].data, dtype=float)
    zodi = np.array(hdul["ZODI"].data, dtype=float)
    flags = np.array(hdul["FLAGS"].data)

    from .flags import DETECTOR_DEFECTS, UNRELIABLE_CORE

    # Mask only dead/hot/missing detector pixels so bright star cores (OVERFLOW, SUR_ERROR,
    # NONLINEAR) and moving targets (TRANSIENT, OUTLIER) are never erased by inpainting.
    mask = (
        flags_to_mask(flags, names=DETECTOR_DEFECTS)
        | ~np.isfinite(image)
        | ~np.isfinite(variance)
        | (variance <= 0)
    )

    unreliable = flags_to_mask(flags, names=UNRELIABLE_CORE)

    detector = header.get("DETECTOR")
    version = None
    if uri:
        m = FILENAME_RE.search(uri)
        if m:
            version = int(m["ver"])
            detector = detector or int(m["det"])

    return Frame(
        image=image,
        variance=variance,
        mask=mask,
        wavelength=wavelength_from_hdul(hdul, ra, dec),
        detector=int(detector) if detector is not None else -1,
        mjd=float(mjd),
        wcs=WCS(header),
        meta={
            "obsid": header.get("OBSID"),
            "bunit": header.get("BUNIT"),
            # Raw Table 8 bitmask, kept so the pixel inspector reports real flags.
            "flag_array": np.asarray(flags).astype(np.int64),
        },
        zodi=zodi,
        version=version,
        uri=uri,
        unreliable=unreliable,
    )


def _cache_dir(ra: float, dec: float, size: float) -> Path:
    """Resolve imported ShroudX cutouts without changing their files or WCS."""
    key = cache_key(ra, dec, size)
    aliases_path = CACHE_DIR / "imported_aliases.json"
    if aliases_path.is_file():
        aliases = json.loads(aliases_path.read_text(encoding="utf-8"))
        folder = aliases.get(key)
        if folder is not None:
            if not isinstance(folder, str) or Path(folder).name != folder or folder in (".", ".."):
                raise ValueError("Invalid imported cache folder alias")
            return CACHE_DIR / folder
    return CACHE_DIR / key


def _download_worker(
    i_row: tuple[int, dict], folder: Path, ra: float, dec: float, size_deg: float
) -> tuple[int, dict, Path | None]:

    i, r = i_row
    dest = folder / f"{i:04d}.fits"
    if dest.exists():  # already downloaded — verify OBSID matches requested URI
        try:
            with fits.open(dest) as hdul:
                obsid = str(hdul["IMAGE"].header.get("OBSID", ""))
            if not obsid or obsid in r.get("uri", ""):
                return i, r, dest
        except Exception:
            pass
    good = download_cutout(cutout_url(r["uri"], ra, dec, size_deg), dest)
    if good:
        return i, r, dest
    return i, r, None


def fetch_real_frames(
    ra: float, dec: float, fov: float, workers: int = 5, limit: int | None = None
) -> list[Frame]:
    """Query IRSA, download a cutout per image into the cache, return Frames.
    `limit` keeps only the first N images (use it while testing).
    Failed downloads are skipped and counted, never silently ignored."""
    size = cutout_bucket(fov)
    folder = _cache_dir(ra, dec, size)
    rows = parse_rows(query_images(ra, dec))
    if limit and len(rows) > limit:
        # Sample evenly across the full mission time baseline (e.g. multi-epoch passes)
        indices = np.linspace(0, len(rows) - 1, limit, dtype=int)
        rows = [rows[i] for i in indices]

    size_deg = size / 3600.0
    t0 = time.time()
    done = 0
    ok_rows = []
    worker = partial(_download_worker, folder=folder, ra=ra, dec=dec, size_deg=size_deg)

    try:
        with concurrent.futures.ThreadPoolExecutor(max_workers=workers) as ex:
            for _i, r, dest in ex.map(worker, list(enumerate(rows))):
                done += 1
                if dest is not None:
                    ok_rows.append({**r, "file": dest.name})
                if done % 10 == 0 or done == len(rows):
                    print(f"  cutouts: {done}/{len(rows)}  ({time.time() - t0:.0f} s)")
    finally:
        if ok_rows:
            _write_index(folder, ok_rows)

    print(f"Downloaded {len(ok_rows)} of {len(rows)} cutouts ({len(rows) - len(ok_rows)} failed).")
    if not ok_rows and not (folder / "index.csv").exists():
        return []
    return load_cached_frames(ra, dec, fov)


def _write_index(folder: Path, rows: list[dict]) -> None:
    """Merge metadata and replace the index only after a complete, flushed write."""
    folder.mkdir(parents=True, exist_ok=True)
    index_file = folder / "index.csv"
    with CACHE_INDEX_LOCK:
        existing = {row["file"]: row for row in _read_cache_index(folder)}
        existing.update({row["file"]: row for row in rows})
        sorted_rows = sorted(existing.values(), key=lambda row: float(row["mjd"]))
        temp_path = None
        try:
            with tempfile.NamedTemporaryFile(
                mode="w", newline="", dir=folder, prefix="index-", suffix=".tmp", delete=False
            ) as f:
                temp_path = Path(f.name)
                writer = csv.DictWriter(
                    f, fieldnames=["file", "mjd", "bandpass", "uri"], extrasaction="ignore"
                )
                writer.writeheader()
                writer.writerows(sorted_rows)
                f.flush()
                os.fsync(f.fileno())
            temp_path.replace(index_file)
        finally:
            if temp_path is not None and temp_path.exists():
                temp_path.unlink()


def _read_cache_index(folder: Path) -> list[dict]:
    try:
        with open(folder / "index.csv", newline="") as f:
            reader = csv.DictReader(f)
            if not {"file", "mjd", "bandpass", "uri"}.issubset(reader.fieldnames or []):
                return []
            rows = list(reader)
        if any(
            not row.get("file") or not row.get("uri") or not np.isfinite(float(row["mjd"])) for row in rows
        ):
            return []
        return rows
    except (OSError, csv.Error, ValueError, TypeError, KeyError):
        return []


def recover_cache_index(folder: Path) -> list[dict]:
    """Recover a missing/empty index from local FITS metadata, without network access.

    The original archive URL cannot be inferred reliably. Use the actual local
    file URI for recovered rows rather than inventing archive provenance.
    """
    from astropy.time import Time

    with CACHE_INDEX_LOCK:
        rows = _read_cache_index(folder)
        if rows and any((folder / row["file"]).is_file() for row in rows):
            return rows
        recovered = []
        for path in sorted([*folder.glob("*.fits"), *folder.glob("*.fits.gz")]):
            try:
                with fits.open(path, memmap=False) as hdul:
                    for name in LAYERS:
                        hdul[name]  # Require every layer used by frame_from_hdul.
                    header = hdul["IMAGE"].header
                    mjd = header.get("MJD-OBS", header.get("MJD-BEG"))
                    if mjd is None:
                        mjd = Time(header["DATE-OBS"], format="isot", scale="utc").mjd
                    mjd = float(mjd)
                    detector = int(header["DETECTOR"])
                    if not np.isfinite(mjd) or not 1 <= detector <= 6:
                        raise ValueError("Invalid observation time or detector")
                recovered.append(
                    {
                        "file": path.name,
                        "mjd": mjd,
                        "bandpass": f"SPHEREx-D{detector}",
                        "uri": path.resolve().as_uri(),
                    }
                )
            except (OSError, ValueError, TypeError, KeyError, IndexError) as exc:
                logging.getLogger(__name__).warning("Cannot recover cached cutout %s: %s", path, exc)
        if recovered:
            _write_index(folder, recovered)
            logging.getLogger(__name__).warning(
                "Recovered %s cache entries from FITS headers in %s", len(recovered), folder
            )
        return recovered


def find_cached_bucket(ra: float, dec: float, fov: float) -> int | None:
    """Find the best available cached bucket size for (ra, dec), preferring
    exact match, then smallest enclosing larger bucket, then any cached bucket."""
    req_b = cutout_bucket(fov)

    def available(bucket):
        folder = _cache_dir(ra, dec, bucket)
        return ((folder / "index.csv").exists()
                or next(folder.glob("*.fits"), None) is not None
                or next(folder.glob("*.fits.gz"), None) is not None)

    if available(req_b):
        return req_b
    for b in CUTOUT_BUCKETS:
        if b > req_b and available(b):
            return b
    for b in reversed(CUTOUT_BUCKETS):
        if available(b):
            return b
    return None


def crop_frame(f: Frame, ra: float, dec: float, target_fov: float, source_fov: float) -> Frame:
    """Sub-crop a Frame to a smaller target FOV (arcseconds)."""
    if target_fov >= source_fov:
        return f

    fwcs = getattr(f, "wcs", None)
    if fwcs is not None:
        try:
            from astropy import units as u
            from astropy.coordinates import SkyCoord
            from astropy.nddata import Cutout2D

            coord = SkyCoord(ra=ra * u.deg, dec=dec * u.deg)
            size = (target_fov * u.arcsec, target_fov * u.arcsec)

            cut_img = Cutout2D(f.image, coord, size, wcs=fwcs, mode="partial", fill_value=np.nan)
            cut_var = Cutout2D(f.variance, coord, size, wcs=fwcs, mode="partial", fill_value=np.inf)
            cut_mask = Cutout2D(f.mask.astype(float), coord, size, wcs=fwcs, mode="partial", fill_value=1.0)
            cut_zodi = None
            if getattr(f, "zodi", None) is not None:
                cut_zodi = Cutout2D(f.zodi, coord, size, wcs=fwcs, mode="partial", fill_value=np.nan).data

            new_meta = dict(f.meta) if getattr(f, "meta", None) else {}
            if new_meta.get("flag_array") is not None:
                new_meta["flag_array"] = np.asarray(
                    Cutout2D(
                        np.asarray(new_meta["flag_array"]),
                        coord,
                        size,
                        wcs=fwcs,
                        mode="partial",
                        fill_value=0,
                    ).data
                ).astype(np.int64)

            cut_unrel = None
            if getattr(f, "unreliable", None) is not None:
                cut_unrel = (
                    Cutout2D(
                        f.unreliable.astype(float), coord, size, wcs=fwcs, mode="partial", fill_value=0.0
                    ).data
                    > 0.5
                )

            return Frame(
                image=cut_img.data,
                variance=cut_var.data,
                mask=(cut_mask.data > 0.5) | ~np.isfinite(cut_img.data),
                wavelength=f.wavelength,
                detector=f.detector,
                mjd=f.mjd,
                wcs=cut_img.wcs,
                meta=new_meta,
                zodi=cut_zodi,
                version=getattr(f, "version", None),
                uri=getattr(f, "uri", None),
                unreliable=cut_unrel,
            )
        except Exception:
            pass

    # Fallback when no WCS or Cutout2D fails: slice center pixels
    ny, nx = f.image.shape
    crop_n = int(np.clip(round(target_fov / PIXEL_SCALE), 4, min(ny, nx)))
    if crop_n >= min(ny, nx):
        return f
    y0 = max(0, (ny - crop_n) // 2)
    y1 = y0 + crop_n
    x0 = max(0, (nx - crop_n) // 2)
    x1 = x0 + crop_n

    slice_meta = dict(f.meta) if getattr(f, "meta", None) else {}
    if slice_meta.get("flag_array") is not None:
        slice_meta["flag_array"] = np.asarray(slice_meta["flag_array"])[y0:y1, x0:x1]

    return Frame(
        image=f.image[y0:y1, x0:x1],
        variance=f.variance[y0:y1, x0:x1],
        mask=f.mask[y0:y1, x0:x1],
        wavelength=f.wavelength,
        detector=f.detector,
        mjd=f.mjd,
        wcs=None,
        meta=slice_meta,
        zodi=f.zodi[y0:y1, x0:x1] if getattr(f, "zodi", None) is not None else None,
        version=getattr(f, "version", None),
        uri=getattr(f, "uri", None),
        unreliable=f.unreliable[y0:y1, x0:x1] if getattr(f, "unreliable", None) is not None else None,
    )


def load_cached_frames(ra: float, dec: float, fov: float) -> list[Frame]:
    """Read frames saved earlier. Raises FileNotFoundError if this position
    was never downloaded (run scripts/fetch_demo.py first)."""

    bucket = find_cached_bucket(ra, dec, fov)
    if bucket is None:
        raise FileNotFoundError(
            f"No cached frames for RA {ra:.5f}, Dec {dec:.5f}, size {cutout_bucket(fov)} arcsec. "
            "Run: python scripts/fetch_demo.py --ra ... --dec ... first."
        )

    folder = _cache_dir(ra, dec, bucket)
    rows = _read_cache_index(folder)
    if not rows or not any((folder / row["file"]).is_file() for row in rows):
        rows = recover_cache_index(folder)
    if not rows:
        raise FileNotFoundError(
            f"No readable cached cutouts in {folder}. Run scripts/fetch_demo.py for this position."
        )
    frames = []
    for row in rows:
        fits_path = folder / row["file"]
        if not fits_path.exists():
            continue
        with fits.open(fits_path, memmap=False) as hdul:
            frames.append(frame_from_hdul(hdul, ra, dec, float(row["mjd"]), row["uri"]))
    frames.sort(key=lambda fr: fr.mjd)

    if fov < bucket:
        frames = [crop_frame(fr, ra, dec, fov, bucket) for fr in frames]

    return frames


def _irsa_reachable(timeout: float = 3.0) -> bool:
    import socket

    try:
        with socket.create_connection(("irsa.ipac.caltech.edu", 443), timeout=timeout):
            return True
    except OSError:
        return False


@lru_cache(maxsize=32)
def get_frames(ra: float, dec: float, fov: float) -> tuple[Frame, ...]:
    m = mode()
    if m == "mock":
        return tuple(make_mock_frames(ra, dec, fov))
    if m == "cache":
        return tuple(load_cached_frames(ra, dec, fov))
    if m == "live":
        try:
            return tuple(load_cached_frames(ra, dec, fov))
        except FileNotFoundError:
            return tuple(fetch_real_frames(ra, dec, fov))
    if m == "auto":
        try:
            return tuple(load_cached_frames(ra, dec, fov))
        except FileNotFoundError:
            if not _irsa_reachable():
                raise FileNotFoundError(
                    "No cached SPHEREx coverage at this position and IRSA is unavailable. "
                    "No mock data was substituted."
                ) from None
            real = fetch_real_frames(ra, dec, fov, limit=20)
            if not real:
                raise FileNotFoundError("No SPHEREx coverage at this position.")
            return tuple(real)
    raise ValueError(f"Unknown SPHX_MODE: {m}")
