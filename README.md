# ShroudX — SPHEREx Sky-Motion Viewer

A public web tool built for the **NASA Space Apps 2026** challenge *"Planet X and SPHEREx"*. ShroudX turns a target name or sky position (RA/Dec) into an aligned, wavelength-matched SPHEREx time-series **blink sequence** and **inverse-variance weighted stack**, making moving sources (cold brown dwarfs, high-proper-motion stars, asteroids, comets) visible across multi-month revisits.

## What it does

- **Coordinate and name resolution (`coords.py`)**: Parses decimal degrees and sexagesimal coordinates (`08h55m10.831s` / `-07d14m42.53s` or space-separated `08 55 10.831` / `-07 14 42.53`, treating bare sexagesimal RA as hours). Resolves object names via SIMBAD/CDS (`SkyCoord.from_name`) or a literature-backed offline benchmark table (`WISE 0855-0714`, `Barnard's Star`, `Epsilon Indi B`, `RW Aurigae`, `R Draconis`), propagating high proper motion (`> 1 arcsec/yr`) to the 2026.0 SPHEREx survey epoch.
- **IRSA cutout ingestion and caching (`data.py`)**: Queries IRSA TAP (`spherex.artifact` joined with `spherex.plane`), fetches small FITS cutouts with exponential-backoff retries (capped at 5 parallel workers), drops the large `PSF` extension when caching to disk under `cache/<key>/`, recovers corrupted `index.csv` files from FITS headers, and serves smaller FOV requests by cropping larger cached buckets (`120`, `240`, `480`, `600` arcsec) via `Cutout2D`.
- **SPHEREx flag decoding (`flags.py`)**: Decodes the 32-bit `FLAGS` bitmask against `MP_*` header keywords. Masks genuine `DETECTOR_DEFECTS` (`SUR_ERROR`, `PHANTOM`, `REFERENCE`, `NONFUNC`, `DICHROIC`, `MISSING_DATA`, `HOT`, `COLD`), tracks `UNRELIABLE_CORE` (`OVERFLOW`, `PHANMISS`, `NONLINEAR`, `PERSIST`), and preserves `SOURCE` (bit 21) and `FULLSAMPLE` (bit 12) pixels.
- **Wavelength-aware cleaning, alignment, and stacking (`processing.py`)**: Evaluates per-pixel wavelength at the target pixel from `WCS-WAVE` (spectral WCS key `'W'`, SIP disabled), subtracts `ZODI` pixel-by-pixel and a 3-sigma-clipped median sky background, selects frames within one detector (`1`–`6`) and a narrow wavelength window (default `<= 0.10` um) to prevent chromatic star flicker, splits epochs at `> 30` day gaps, reprojects frames onto a North-up East-left ICRS TAN grid (`~6.15` arcsec/px), builds an inverse-variance weighted MAD-clipped stack, and renders PNGs with a single shared `asinh`/`linear` stretch across the entire blink sequence and stack.
- **FastAPI backend and single-file frontend (`app.py`, `static/index.html`)**: Serves a zero-build static UI and query-string-driven API endpoints (`/health`, `/api/resolve`, `/api/meta`, `/api/image.png`), returning structured JSON errors (`no_coverage`, `not_cached`, `unknown_target`, `insufficient_data`, `archive_unreachable`, `bad_input`) without ever silently falling back to synthetic data.

---

## Quick start (PowerShell)

### 1. Install dependencies

Requires Python 3.10+.

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
```

For linting during development, also install `ruff` (or `pip install -e .[dev]`):

```powershell
.\.venv\Scripts\python.exe -m pip install ruff
```

### 2. Choose `SPHX_MODE`

Set `SPHX_MODE` before starting the server (`SPHX_CACHE_DIR` optionally overrides the default `cache/` directory):

| `SPHX_MODE` | Behaviour |
| --- | --- |
| `auto` *(default)* | Serves from the local disk cache (`cache/`) if a matching or larger bucket exists; otherwise queries IRSA TAP and caches downloaded cutouts. Raises `archive_unreachable` (`502`) if IRSA cannot be reached. Never falls back to synthetic data. |
| `cache` | Offline mode using only existing cached cutouts. Returns `404` (`not_cached`) when a position is missing from disk. |
| `live` | Queries IRSA TAP and downloads cutouts directly (bypassing existing cache hits, while updating the disk cache). |
| `synthetic` | Explicit simulated-data mode generating a synthetic moving source and wavelength-dependent star. Every JSON response includes `"synthetic": true`, PNG responses include `X-Synthetic: true`, and the UI displays a `SYNTHETIC DATA` banner. |

### 3. Pre-fetch demo targets (`scripts/fetch_cutouts.py`)

Pre-download and cache SPHEREx cutouts from IRSA at 480 arcsec (from which smaller FOVs like 120 or 300 arcsec can be cropped locally):

```powershell
# Pre-fetch by target name (uses epoch-propagated coordinates for high-PM offline targets)
.\.venv\Scripts\python.exe scripts/fetch_cutouts.py --target "WISE 0855-0714" --fov 480
.\.venv\Scripts\python.exe scripts/fetch_cutouts.py --target "Barnard's Star" --fov 480
.\.venv\Scripts\python.exe scripts/fetch_cutouts.py --target "Epsilon Indi B" --fov 480

# Pre-fetch by coordinates (note '=' when passing a negative --dec value)
.\.venv\Scripts\python.exe scripts/fetch_cutouts.py --ra 08h55m10.831s --dec=-07d14m42.53s --fov 480
```

CLI options: `--target`, `--ra`, `--dec`, `--fov` (default `480.0`), `--cache-dir` (default `cache`), `--workers` (`1`–`5`, default `5`), `--retries` (default `4`), `--mode` (`live`, `auto`, `cache`, default `live`).

### 4. Start the server

```powershell
# Example: explicit synthetic mode for offline UI exploration
$env:SPHX_MODE = "synthetic"
.\.venv\Scripts\python.exe -m uvicorn app:app --host 127.0.0.1 --port 8000
```

Open <http://127.0.0.1:8000/?ra=133.76&dec=-7.24> (or omit `SPHX_MODE` / set `$env:SPHX_MODE = "cache"` once cutouts are cached). Keep the UI **Data connection** selector on **Project API** to run the Python pipeline; **Synthetic interface preview** runs browser-only fixture graphics without hitting the backend.

### 5. Run tests and checks

```powershell
.\.venv\Scripts\python.exe -m pytest -q
.\.venv\Scripts\python.exe -m ruff check .
node --test tests/test_viewer.cjs
```

---

## API reference

All endpoints are same-origin (`docs/api_contract.md` v1.1). Query parameters make every view shareable by URL.

| Method & Path | Query Parameters | Response | Notes |
| --- | --- | --- | --- |
| `GET /` | — | `text/html` | Serves `static/index.html`. |
| `GET /health` | — | JSON | Returns `status`, frozen `data_mode`, and `cache_size_mb`. Adds `"synthetic": true` in synthetic mode. |
| `GET /api/resolve` | `target` **or** (`ra` and `dec`) | JSON | Returns ICRS `ra`, `dec`, `source` (`offline_table`, `simbad`, `coordinates`), `pm_known`, `warnings`, `suggested_fov_arcsec`, `suggested_detector`. |
| `GET /api/meta` | `target` or (`ra`, `dec`), optional `fov` (`30`–`600`), `detector` (`1`–`6`), paired `wmin`/`wmax` (um), `mode` (`blink`/`stack`) | JSON | Runs `get_frames`, `group_frames`, `clean_frame`, `align_frames`, `split_epochs`, `compute_stretch`, `stack_frames`. Returns `frames`, `epochs`, `stretch`, `wavelength_window_um`, `detector_ranges_um`, `data_source`, `synthetic`, and `warnings`. |
| `GET /api/image.png` | Same as `/api/meta`, plus `index` (required when `mode=blink`) | `image/png` | North-up, East-left PNG rendered with the shared `stretch` from `/api/meta`. Headers: `X-Data-Source`, `X-Synthetic`, `Cache-Control: no-store`. |

### Error codes

Errors return JSON shaped as `{"error": {"code": "...", "message": "...", "detail": {...}}}`:

| HTTP Status | `code` | Meaning |
| --- | --- | --- |
| `400` | `bad_input` | Invalid, missing, or conflicting parameters (including unsupported `mode=diff`). |
| `404` | `no_coverage` | IRSA TAP returned no SPHEREx images covering the position. |
| `404` | `not_cached` | `SPHX_MODE=cache` is active and the position is not in the local cache. |
| `404` | `unknown_target` | Target name could not be resolved via SIMBAD/CDS or the offline table. |
| `422` | `insufficient_data` | Coverage exists, but no usable frames or unmasked pixels remain for the selected detector/wavelength window. |
| `502` | `archive_unreachable` | IRSA TAP or cutout download endpoints could not be reached in `auto` or `live` mode. |
| `500` | `internal_error` | Unexpected processing failure (safe message; internal details are logged only). |

---

## Honest limitations

- **Cutout size limit**: Cutouts are capped at `600` arcsec (`SIZE_BUCKETS_ARCSEC = (120, 240, 480, 600)`), and API `fov` accepts `30`–`600` arcsec.
- **No proper-motion or parallax fitting yet**: `motion.py` and `/api/motion` are not implemented in this core release; the viewer enables visual blinking and epoch-grouped inspection rather than quantitative astrometric fitting.
- **Core features not yet implemented**: Difference imaging (`mode=diff` returns `400 bad_input`), animated GIF export (`/api/blink.gif`), Gaia DR3 catalog overlays (`/api/catalog`), and blind mover detection (`/api/movers`) are deferred Phase 2 features.
- **Proper-motion propagation scope**: Offline epoch propagation to `2026.0` applies only to the five targets in `coords.OFFLINE_TARGETS` (`data/benchmarks.csv` sources). Arbitrary names resolved via online SIMBAD/CDS (`SkyCoord.from_name`) return catalog coordinates without proper-motion propagation, so fast movers queried online may require a wider FOV.
- **Reprojection variance approximation**: `align_frames` reprojects `variance` bilinearly via `reproject_interp`, which approximates pixel noise and introduces correlation between adjacent pixels.
- **Stacking suppresses fast movers**: `stack_frames` performs per-pixel MAD sigma-clipping across all frames; objects that move across pixels between epochs can be rejected or dimmed in the full stack, so moving targets should be inspected via blink playback or per-epoch views.
- **Test coverage scope**: Automated `pytest`, `ruff`, and Node tests verify synthetic FITS cutouts, mocked TAP/network responses, and explicit synthetic mode; they do not establish scientific accuracy on live IRSA SPHEREx Level-2 products without running against real archive data.

---

## Built during the hackathon

Developed during the NASA Space Apps 2026 hackathon (core pipeline and viewer):

- `frame.py`: Shared `Frame` dataclass, shape/dtype/bounds validation, `sigma` property, and `copy_with` helper.
- `coords.py`: Decimal and sexagesimal coordinate parser, SIMBAD/CDS resolver, literature-cited offline target table (`OFFLINE_TARGETS`), linear proper-motion epoch propagation, target profile detector hints, and small-FOV warnings.
- `flags.py`: SPHEREx 32-bit `FLAGS` decoder grounded in `MP_*` FITS header keywords, flag group classifications (`DETECTOR_DEFECTS`, `UNRELIABLE_CORE`, `TRANSIENTS`, `INFORMATION_ONLY`, `IMAGE_ARTIFACTS`), and `verify_against_header`.
- `data.py` & `scripts/fetch_cutouts.py`: IRSA TAP querying (`spherex.artifact` / `spherex.plane`), retry/backoff parallel cutout downloader (`<= 5` workers, `PSF` stripping, atomic writes), bucketed disk cache with corrupted-index rebuild and `Cutout2D` cropping, named-HDU `Frame` construction with `WCS-WAVE` evaluation, `SPHX_MODE` handling, and CLI pre-fetcher.
- `processing.py`: `ZODI` and sigma-clipped sky background subtraction, masked-pixel display inpainting, detector and narrow wavelength-window grouping, 30-day epoch splitting, North-up East-left TAN WCS alignment via `reproject_interp`, inverse-variance MAD-clipped coadding, and shared-stretch PNG rendering.
- `app.py` & `static/index.html`: Thread-safe FastAPI backend with bounded expiring `ProductCache`, structured contract error handling, and a dependency-free single-file blink/stack HTML/JS viewer with epoch timeline and URL state synchronization.
- `docs/` & `data/`: API contract (`docs/api_contract.md`), source-of-truth notes (`docs/source_of_truth.md`), and literature-cited CSV tables (`data/benchmarks.csv`, `data/flicker_stars.csv`).
- `tests/`: Unit and end-to-end integration tests across all backend modules (`test_frame.py`, `test_coords.py`, `test_flags.py`, `test_data.py`, `test_processing.py`, `test_app.py`, `test_integration.py`) plus frontend VM harness tests (`test_viewer.cjs`).

### AI assistance disclosure

AI coding assistants were used as pair-programming tools during the hackathon across modules, tests, and documentation (files carrying `# AI-assisted:` headers note the assistant used). All IRSA SPHEREx data-format rules, flag bit tables, and literature target parameters were checked against project specifications and cited sources.
