# ShroudX — SPHEREx Sky-Motion Viewer

A public web tool built for the **NASA Space Apps 2026** challenge *"Planet X and SPHEREx"*. ShroudX turns a target name or sky position (RA/Dec) into an aligned, wavelength-matched SPHEREx time-series **blink sequence**, **inverse-variance weighted stack**, **signed difference image**, **proper-motion & parallax fit**, and **Gaia DR3 reference overlay**, making moving sources (cold brown dwarfs, high-proper-motion stars, asteroids, comets) measurable across multi-month revisits.

## What it does

- **Coordinate and name resolution (`coords.py`)**: Parses decimal degrees and sexagesimal coordinates (`08h55m10.831s` / `-07d14m42.53s` or space-separated `08 55 10.831` / `-07 14 42.53`, treating bare sexagesimal RA as hours). Resolves object names via SIMBAD/CDS (`SkyCoord.from_name`) or a literature-backed offline benchmark table (`WISE 0855-0714`, `Barnard's Star`, `Epsilon Indi B`, `RW Aurigae`, `R Draconis`), propagating high proper motion (`> 1 arcsec/yr`) to the 2026.0 SPHEREx survey epoch.
- **IRSA cutout ingestion and caching (`data.py`)**: Queries IRSA TAP (`spherex.artifact` joined with `spherex.plane`), fetches small FITS cutouts with exponential-backoff retries (capped at 5 parallel workers), drops the large `PSF` extension and supports gzip compression when caching under `cache/<key>/`, recovers corrupted `index.csv` files from FITS headers, and serves smaller FOV requests by cropping larger cached buckets (`120`, `240`, `480`, `600` arcsec) via `Cutout2D`.
- **SPHEREx flag decoding (`flags.py`)**: Decodes the 32-bit `FLAGS` bitmask against `MP_*` header keywords. Masks genuine `DETECTOR_DEFECTS` (`SUR_ERROR`, `PHANTOM`, `REFERENCE`, `NONFUNC`, `DICHROIC`, `MISSING_DATA`, `HOT`, `COLD`), tracks `UNRELIABLE_CORE` (`OVERFLOW`, `PHANMISS`, `NONLINEAR`, `PERSIST`), and preserves `SOURCE` (bit 21) and `FULLSAMPLE` (bit 12) pixels.
- **Wavelength-aware cleaning, alignment, stacking, and differencing (`processing.py`)**: Evaluates per-pixel wavelength at the target pixel from `WCS-WAVE` (spectral WCS key `'W'`, SIP disabled), subtracts `ZODI` pixel-by-pixel and a 3-sigma-clipped median sky background, selects frames within one detector (`1`–`6`) and a narrow wavelength window (default `<= 0.10` um), splits epochs at `> 30` day gaps, reprojects frames onto a North-up East-left ICRS TAN grid (`~6.15` arcsec/px), builds an inverse-variance weighted MAD-clipped stack, computes epoch-pair significance difference maps (`difference_images`, `render_diff_png`), and renders PNGs with a single shared `asinh`/`linear` stretch.
- **Proper-motion and parallax fitting (`motion.py`)**: Performs inverse-variance weighted centroiding per frame and epoch, computes barycentric parallax factors (`compute_parallax_factors`), fits linear proper motion (and parallax when `>= 3` epochs) via generalized least squares with `sqrt(reduced_chi2)` uncertainty inflation when `reduced_chi2 > 1`, and rejects saturated/low-SNR epochs without inventing measurements.
- **Gaia DR3 catalog cross-match (`gaia.py`)**: Queries `gaiadr3.gaia_source` through astroquery's Gaia TAP service, caches disk responses by rounded position and radius, propagates positions from epoch `2016.0` to the SPHEREx observation epoch (`propagate_to_epoch`), flags missing proper motions (`pm_missing`), and classifies cross-matches (`known_gaia_mover`, `gaia_source_inconsistent_pm`, `no_gaia_counterpart`).
- **FastAPI backend and single-file frontend (`app.py`, `static/index.html`)**: Serves a zero-build static UI and query-string-driven API endpoints (`/health`, `/api/resolve`, `/api/meta`, `/api/image.png`, `/api/blink.gif`, `/api/catalog`, `/api/motion`), featuring Blink/Stack/Difference mode switching, an on-demand Gaia DR3 circle overlay, interactive motion vector/error-circle inspection, client-side brightness/contrast display controls, animated GIF export, and structured JSON errors without ever silently falling back to synthetic data.

---

## Quick start (PowerShell)

### 1. Install dependencies

Requires Python 3.10+.

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
```

For running the tests and linting, install the development requirements instead
(this adds `pytest`, `ruff`, and `httpx`):

```powershell
.\.venv\Scripts\python.exe -m pip install -r requirements-dev.txt
```

### 2. Choose `SPHX_MODE`

Set `SPHX_MODE` before starting the server (`SPHX_CACHE_DIR` optionally overrides the default `cache/` directory):

| `SPHX_MODE` | Behaviour |
| --- | --- |
| `auto` *(default)* | Serves from the local disk cache (`cache/`) if a matching or larger bucket exists; otherwise queries IRSA TAP and caches downloaded cutouts. If an earlier download was partial (some cutouts failed), it retries the missing cutouts; if IRSA is unreachable it serves the partial cache with an incomplete-sequence warning. Raises `archive_unreachable` (`502`) if IRSA cannot be reached and nothing is cached. Never falls back to synthetic data. |
| `cache` | Offline mode using only existing cached cutouts and cached Gaia tables. Returns `404` (`not_cached`) when a position is missing from disk. |
| `live` | Queries IRSA TAP / Gaia TAP directly (bypassing in-process caches while updating disk caches). |
| `synthetic` | Explicit simulated-data mode generating a synthetic moving source and wavelength-dependent star. Every JSON response includes `"synthetic": true`, PNG responses include `X-Synthetic: true`, and the UI displays a `SYNTHETIC DATA` banner. Gaia catalog rows are never synthesized. |

### 3. Pre-fetch demo targets (`scripts/fetch_cutouts.py` & `scripts/fetch_gaia.py`)

Pre-download and cache SPHEREx cutouts from IRSA at 480 arcsec (from which smaller FOVs like 120 or 300 arcsec can be cropped locally):

```powershell
# Pre-fetch by target name (uses epoch-propagated coordinates for high-PM offline targets)
.\.venv\Scripts\python.exe scripts/fetch_cutouts.py --target "WISE 0855-0714" --fov 480
.\.venv\Scripts\python.exe scripts/fetch_cutouts.py --target "Barnard's Star" --fov 480
.\.venv\Scripts\python.exe scripts/fetch_cutouts.py --target "Epsilon Indi B" --fov 480

# Pre-fetch by coordinates (note '=' when passing a negative --dec value).
# 133.76 / -7.24 is WISE 0855's 2026 position; its catalog name encodes an older epoch.
.\.venv\Scripts\python.exe scripts/fetch_cutouts.py --ra 133.76 --dec=-7.24 --fov 480

# Pre-fetch Gaia DR3 reference catalogs for the demo fields into cache/gaia/
.\.venv\Scripts\python.exe scripts/fetch_gaia.py
```

`fetch_cutouts.py` CLI options: `--target`, `--ra`, `--dec`, `--fov` (default `480.0`), `--cache-dir` (default `cache`), `--workers` (`1`–`5`, default `5`), `--retries` (default `4`), `--mode` (`live`, `auto`, `cache`, default `live`).

### 4. Start the server (one-command local run)

Run locally in `SPHX_MODE=auto` (serves the bundled demo fields from `cache/` immediately and queries IRSA on cache misses) with a single command:

```bash
SPHX_MODE=auto python -m uvicorn app:app --host 127.0.0.1 --port "${PORT:-8000}"
```

Or run the production container image locally with a single command (Docker or Podman):

```bash
docker build -t shroudx . && docker run --rm -p 8000:8000 -e PORT=8000 -e SPHX_MODE=auto shroudx
```

PowerShell equivalent:

```powershell
# Default SPHX_MODE=auto (or set $env:SPHX_MODE = "synthetic" for explicit simulated data)
$env:SPHX_MODE = "auto"; .\.venv\Scripts\python.exe -m uvicorn app:app --host 127.0.0.1 --port 8000
```

Open <http://127.0.0.1:8000/?target=WISE+0855-0714> or <http://127.0.0.1:8000/?ra=133.76&dec=-7.24>. Keep the UI **Data connection** selector on **Project API** to run the Python pipeline; **Synthetic interface preview** runs browser-only fixture graphics without hitting the backend.

### 5. Bundled demo cache (`scripts/bundle_cache.py`)

To build and verify the compact compressed bundled cache (`<= 50.0 MB`) of the five demo fields and write `results/bundled_cache.json` and `results/bundled_cache.md`:

```bash
python scripts/bundle_cache.py
```

Measured output from [`results/bundled_cache.md`](results/bundled_cache.md):

- **Command**: `python scripts/bundle_cache.py`
- **Data versions**: `l2b-v27`
- **Verdict**: **PASS** (limit <= 50.0 MB)
- **Total bundled cache size**: **18.10 MB** (17.26 MiB, 18,103,093 bytes across 227 FITS cutouts; per-cutout size 77.9 +/- 3.2 kB), plus **379.63 kB** of cached Gaia DR3 tables in `cache/gaia/`

| Target | Cache directory | Bundled cutouts | Default det | Default window (um) | Window frames (epochs) | Version | Size (MB) |
| --- | --- | ---: | ---: | --- | ---: | --- | ---: |
| WISE 0855-0714 | `ra133.7598_dec-07.2422_s480` | 43 | 5 | 4.145-4.225 | 11 (4) | l2b-v27 | 3.35 |
| Barnard's Star | `ra269.4463_dec+04.7682_s480` | 47 | 2 | 1.461-1.554 | 12 (3) | l2b-v27 | 3.69 |
| Epsilon Indi B | `ra331.0966_dec-56.8007_s480` | 47 | 5 | 3.983-4.070 | 12 (6) | l2b-v27 | 3.68 |
| RW Aurigae | `ra076.9565_dec+30.4014_s480` | 45 | 3 | 1.639-1.736 | 9 (3) | l2b-v27 | 3.48 |
| R Draconis | `ra248.1676_dec+66.7550_s480` | 45 | 4 | 3.115-3.192 | 8 (6) | l2b-v27 | 3.52 |

### 6. Hosted deployment & smoke testing (`scripts/smoke_test.py`)

The `Dockerfile` uses `python:3.12-slim`, runs as non-root `appuser` (UID `10001`), sets `SPHX_MODE=auto`, bundles `/app/cache` (`18.10 MB` cutouts + `379.63 kB` Gaia DR3 cache), enforces per-request timeouts (`SPHX_REQUEST_TIMEOUT=30.0`) and concurrency limits (`SPHX_IRSA_CONCURRENCY=2`) on routes calling IRSA, and binds `uvicorn` to `$PORT`. No secrets or credentials are included in the image or repository.

- **Fly.io**:
  ```bash
  fly launch --no-deploy --dockerfile Dockerfile
  fly deploy --ha=false
  ```
- **Render**:
  Create a new **Web Service** from the repository with **Runtime: Docker**, set health check path to `/health`, and leave `SPHX_MODE=auto` (Render injects `$PORT` automatically).
- **Hugging Face Spaces**:
  Create a **Docker Space** (exposed port `7860`), set `PORT=7860` and `SPHX_MODE=auto` in the Space configuration, and push `Dockerfile`, `.dockerignore`, source files, and the bundled `cache/`.

Verify the local app or deployed URL with the smoke-test script (checks `/health`, `/api/meta`, `/api/image.png`, and `/api/motion`, writing latency measurements and results to `results/smoke_test.json`):

```bash
python scripts/smoke_test.py
python scripts/smoke_test.py --url https://<DEPLOYED_HOST_URL>
```

Measured local output from [`results/smoke_test.json`](results/smoke_test.json) (`command: "python scripts/smoke_test.py"`, `target: "WISE 0855-0714"`, `data_mode: "auto"`, `data_versions: ["l2b-v27"]`, `repeats: 3`):

| Route | Status | Mean +/- Sigma Latency (ms) | Verdict |
| --- | ---: | ---: | --- |
| `/health` | `200` | `26.3 +/- 20.63` | **PASS** |
| `/api/meta` | `200` | `494.33 +/- 839.58` | **PASS** |
| `/api/image.png` | `200` | `11.79 +/- 8.55` | **PASS** |
| `/api/motion` | `200` | `17.03 +/- 8.89` | **PASS** |

### 7. Run tests and checks

```powershell
.\.venv\Scripts\python.exe -m pytest -q
.\.venv\Scripts\python.exe -m ruff check --no-cache .
node --test tests/test_viewer.cjs
```

---

## Science validation & experiment results

All tables and numbers below were generated directly by scripts under `scripts/` and `experiments/` with no hand-edited values.

### Proper-motion and parallax validation (`scripts/validate_motion.py`)

Command: `python scripts/validate_motion.py` (`2026-11-14T18:45:30Z`, data version `v27`, outputs in [`results/motion_validation.csv`](results/motion_validation.csv) and [`results/motion_validation.md`](results/motion_validation.md)). `*` marks uncertainties inflated by `sqrt(reduced_chi2)`.

Pre-committed criteria:
- **Stationary** (`pm_lit < 0.05 "/yr`): `AGREE` if `|pm| / sigma < 3`, else `DISAGREE`.
- **Moving** (`pm_lit >= 0.05 "/yr`): `UNINFORMATIVE` if `sigma >= 0.3 * pm_lit` (not a pass); `AGREE` if `|pm_vec - pm_lit_vec| < 3 * sigma` and `sigma < 0.3 * pm_lit`; `DISAGREE` otherwise.

| Target | Status | PM Lit ("/yr) | PM Meas +/- Sigma ("/yr) | Diff (sigma) | Plx Meas +/- Sigma (") | chi2_red | Epochs (Frames) | Window (um) |
| :--- | :--- | ---: | ---: | ---: | ---: | ---: | ---: | :--- |
| Barnard's Star | **UNINFORMATIVE** | 10.3933 | 5.5559 +/- 21.1471* | 0.70 | 5.2561 +/- 8.0676 | 117425.86 | 4 (6/7) | D1:0.745-0.843 |
| WISE J085510.83-071442.5 | **UNINFORMATIVE** | 8.1515 | 10.8940 +/- 11.2509 | 1.55 | -4.6645 +/- 3.7537 | 0.11 | 3 (3/7) | D5:4.145-4.225 |
| eps Ind B | **AGREE** | 4.6842 | 4.6075 +/- 0.1540* | 0.76 | 0.5293 +/- 0.1015 | 51.28 | 6 (12/12) | D5:3.983-4.070 |
| RW Aurigae | **DISAGREE** | 0.0216 | 0.5271 +/- 0.1041* | 5.06 | 0.4342 +/- 0.0426 | 149.79 | 3 (9/9) | D3:1.639-1.736 |
| R Draconis | **UNINFORMATIVE** | 0.0215 | N/A | N/A | N/A | N/A | 0 (0/8) | D4:3.115-3.192 |

### Gaia DR3 catalog cache and cross-match (`scripts/fetch_gaia.py`)

Command: `python scripts/fetch_gaia.py` (catalog `Gaia DR3 (gaiadr3.gaia_source)`, propagated epoch `2026.0`, verdict **PASS**, total size `379.63 kB`, outputs in [`results/gaia_catalog.json`](results/gaia_catalog.json) and [`results/gaia_catalog.md`](results/gaia_catalog.md)):

| Target | Radius (") | Sources (<=500) | Missing PM | Brightest G (mag) | Median G +/- sigma | Target Cross-Match (<=5") | Sep (") |
| --- | ---: | ---: | ---: | ---: | --- | --- | ---: |
| WISE 0855-0714 | 240 | 129 | 18 | 12.25 | 19.10 +/- 1.88 | `no_gaia_counterpart` | — |
| Barnard's Star | 240 | 500 | 5 | 8.19 | 18.38 +/- 1.45 | `known_gaia_mover` | 0.00 |
| Epsilon Indi B | 240 | 114 | 14 | 12.95 | 19.33 +/- 1.84 | `known_gaia_mover` | 0.02 |
| RW Aurigae | 240 | 466 | 51 | 11.88 | 19.08 +/- 1.72 | `known_gaia_mover` | 0.69 |
| R Draconis | 240 | 90 | 19 | 6.37 | 19.21 +/- 2.39 | `known_gaia_mover` | 0.56 |

### Stationary-star chromatic flicker experiment (`experiments/flicker.py`)

Command: `python experiments/flicker.py --mode cache` (data version `l2b-v27`, `2.0 px` circular aperture with `3.5`–`6.0 px` sky annulus, outputs in [`results/flicker.json`](results/flicker.json), [`results/flicker_flux_vs_wavelength.png`](results/flicker_flux_vs_wavelength.png), and [`results/flicker_scatter_wavelength_vs_time.png`](results/flicker_scatter_wavelength_vs_time.png)).

- **Sample**: `5/5` qualifying isolated stationary Gaia DR3 field stars (`pm = 2.43`–`9.17 mas/yr`, `G = 12.25`–`13.93 mag`, nearest Gaia neighbor `>= 24.7"`), `204` usable / `22` skipped frames across all 6 detectors.
- **Wavelength-window excess scatter**: `8.47% +/- 1.84%` at `w = 0.02 um` (`176` pairs), `7.85% +/- 1.50%` at `0.04 um` (`340` pairs), `10.00% +/- 1.44%` at `0.10 um` (`595` pairs), and `10.29% +/- 1.36%` at `0.40 um` (`612` pairs).
- **Matched-wavelength time control** (`|d_lambda| <= 0.03 um`): `1.05%` excess scatter at `<= 0.5 d` (`5` pairs), `1.93%` at `<= 10 d` (`18` pairs), `3.49%` at `<= 30 d` (`28` pairs), `4.72%` at `<= 120 d` (`55` pairs), rising to `6.67%`–`7.43%` at `180`–`365 d`.
- **Outcome**: `max_window_below_threshold_um = null` (`single_value_supported = false`). Even at the narrowest populated wavelength bin (`0.02 um`), multi-epoch unweighted `2.0 px` aperture photometry exhibits `8.47% +/- 1.84%` excess scatter (`> 5%`), so the experiment states plainly that a single `max_width` cutoff is not supported by photometry alone without narrower filtering, PSF/pixel-phase correction, or per-source SED modelling.

---

## API reference

All endpoints are same-origin (`docs/api_contract.md` v1.2). Query parameters make every view shareable by URL.

| Method & Path | Query Parameters | Response | Notes |
| --- | --- | --- | --- |
| `GET /` | — | `text/html` | Serves `static/index.html`. |
| `GET /health` | — | JSON | Returns `status`, frozen `data_mode`, and `cache_size_mb`. Adds `"synthetic": true` in synthetic mode. |
| `GET /api/resolve` | `target` **or** (`ra` and `dec`) | JSON | Returns ICRS `ra`, `dec`, `source` (`offline_table`, `simbad`, `coordinates`), `pm_known`, `warnings`, `suggested_fov_arcsec`, `suggested_detector`. |
| `GET /api/meta` | `target` or (`ra`, `dec`), optional `fov` (`30`–`600`), `detector` (`1`–`6`), paired `wmin`/`wmax` (um), `mode` (`blink`/`stack`/`diff`), `epoch_a`/`epoch_b` | JSON | Runs `get_frames`, `group_frames`, `clean_frame`, `align_frames`, `split_epochs`, `compute_stretch`, `stack_frames`. Includes wavelength-difference caution when `mode=diff`. |
| `GET /api/image.png` | Same as `/api/meta`, plus `index` (required when `mode=blink`) and optional `epoch_a`/`epoch_b` (`mode=diff`) | `image/png` | North-up, East-left PNG rendered with shared `stretch` (blink/stack) or symmetric signed significance stretch (`mode=diff`, header `X-Diff-Limit`). Headers: `X-Data-Source`, `X-Synthetic`, `Cache-Control: no-store`. |
| `GET /api/blink.gif` | Same as `/api/meta`, plus optional `speed` (`50`–`2000` ms, default `500`, rounded to 10 ms) | `image/gif` | Animated GIF download (`shroudx-blink.gif`, `< 10` MB) with shared `stretch` and caption strip. Headers: `Content-Disposition`, `X-Gif-Size-Bytes`, `X-Data-Source`, `X-Synthetic`, `Cache-Control: no-store`. |
| `GET /api/catalog` | Same as `/api/meta` | JSON | Gaia DR3 sources within the image footprint propagated to the sequence mean observation epoch (max `500`, brightest first), with projected PNG `(x, y)`, `source_id_text`, `g_mag`, `pmra`, `pmdec`, `parallax`, `pm_missing`. Never synthesized. |
| `GET /api/motion` | Same as `/api/meta` | JSON | Per-epoch measured centroids and error radii (`epochs[]`), GLS proper-motion & parallax `fit`, literature `benchmark` comparison & verdict when listed in `data/benchmarks.csv`, and atomic provenance saved to `results/motion-<hash>.json`. |

### Error codes

Errors return JSON shaped as `{"error": {"code": "...", "message": "...", "detail": {...}}}`:

| HTTP Status | `code` | Meaning |
| --- | --- | --- |
| `400` | `bad_input` | Invalid, missing, or conflicting parameters (for example malformed/equal/unknown `epoch_a`/`epoch_b` in `mode=diff` or unpaired `wmin`/`wmax`). |
| `404` | `no_coverage` | IRSA TAP returned no SPHEREx images covering the position. |
| `404` | `not_cached` | `SPHX_MODE=cache` is active and the position (or Gaia catalog) is not in the local cache. |
| `404` | `unknown_target` | Target name could not be resolved via SIMBAD/CDS or the offline table. |
| `422` | `insufficient_data` | Coverage exists, but too few epochs, frames, or overlapping unmasked pixels remain for the requested product (`mode=diff`, `/api/motion`, or `< 10` MB GIF export). |
| `502` | `archive_unreachable` | IRSA TAP, cutout downloads, or ESA Gaia TAP could not be reached in `auto` or `live` mode. |
| `500` | `internal_error` | Unexpected processing failure (safe message; internal details are logged only). |

---

## Honest limitations

- **Blind mover detection omitted**: `GET /api/movers` (automated blind candidate search) was scoped as a stretch goal and is not implemented; users discover movers visually via blink/difference imaging and fit them via `/api/motion`.
- **Bright/saturated and faint targets**: Very bright stars saturate SPHEREx detectors (`R Draconis` yields `0/8` usable epochs due to `OVERFLOW`/`SUR_ERROR` core masking; `Barnard's Star` exhibits extreme `reduced_chi2 = 117425.86`, inflating its PM uncertainty to `UNINFORMATIVE`). Faint cold brown dwarfs can fall below SNR thresholds in individual exposures (`WISE J0855` loses epoch 4 completely to `SNR < 5.0` rejections, yielding `UNINFORMATIVE`), and unresolved/variable sources such as `RW Aurigae` (`DISAGREE`, `5.06 sigma`) suffer centroid residuals. No fit parameters are tuned to force benchmark agreement.
- **Chromatic & pixel-phase scatter in aperture photometry**: In `experiments/flicker.py`, unweighted `2.0 px` aperture photometry on `6.15"/px` undersampled SPHEREx cutouts shows `8.47% +/- 1.84%` excess scatter even at `w = 0.02 um` across multi-month baselines (whereas same-epoch matched-wavelength stability within `0.5` day is `1.05%`), leaving `recommended_max_width_um = null`.
- **Cutout size limit**: Cutouts are capped at `600` arcsec (`SIZE_BUCKETS_ARCSEC = (120, 240, 480, 600)`), and API `fov` accepts `30`–`600` arcsec.
- **Proper-motion propagation scope**: Offline SPHEREx epoch propagation to `2026.0` applies to `coords.OFFLINE_TARGETS`, while `/api/catalog` propagates Gaia DR3 sources (those with valid PMs) to the exact mean sequence epoch; arbitrary online SIMBAD targets without Gaia cross-match do not auto-propagate.
- **Reprojection variance & stacking trade-offs**: `align_frames` reprojects `variance` bilinearly via `reproject_interp` (approximating correlated pixel noise), and `stack_frames` suppresses objects moving quickly across pixels via per-pixel sigma clipping.
- **Test coverage scope**: Automated `pytest`, `ruff`, and Node tests verify synthetic frames and mocked TAP responses; live IRSA/Gaia connectivity is tested when reachable (`scripts/smoke_test.py`, `scripts/fetch_gaia.py`).

---

## Built during the hackathon

Developed during the NASA Space Apps 2026 hackathon:

- `frame.py`: Shared `Frame` dataclass, shape/dtype/bounds validation, `sigma` property, and `copy_with` helper.
- `coords.py`: Decimal/sexagesimal coordinate parser, SIMBAD/CDS resolver, literature-cited offline target table (`OFFLINE_TARGETS`), linear proper-motion epoch propagation, target profile hints, and small-FOV warnings.
- `flags.py`: SPHEREx 32-bit `FLAGS` decoder grounded in `MP_*` FITS header keywords, flag classifications (`DETECTOR_DEFECTS`, `UNRELIABLE_CORE`, `TRANSIENTS`, `INFORMATION_ONLY`, `IMAGE_ARTIFACTS`), and `verify_against_header`.
- `data.py` & `scripts/fetch_cutouts.py`: IRSA TAP querying (`spherex.artifact` / `spherex.plane`), retry/backoff parallel cutout downloader (`<= 5` workers, `PSF` stripping, gzip cache support, atomic writes), bucketed disk cache with index recovery and `Cutout2D` cropping, `WCS-WAVE` evaluation, `SPHX_MODE` handling, and CLI pre-fetcher.
- `processing.py`: `ZODI` and sigma-clipped sky background subtraction, masked-pixel display inpainting, detector/wavelength grouping, 30-day epoch splitting, North-up East-left TAN WCS alignment, inverse-variance MAD-clipped coadding, epoch difference imaging (`difference_images`, `render_diff_png`), and shared-stretch PNG rendering.
- `motion.py` & `scripts/validate_motion.py`: Inverse-variance centroiding, barycentric parallax factors, generalized least-squares proper-motion and parallax fitting with `sqrt(chi2_red)` inflation, graceful saturated/rejected epoch handling, and benchmark validation runner.
- `gaia.py` & `scripts/fetch_gaia.py`: Gaia DR3 TAP client with disk caching and timeouts, `2016.0 -> target epoch` PM propagation, PM-aware cross-matching, and offline field cache pre-fetcher.
- `experiments/flicker.py`: Stationary-star aperture photometry and paired wavelength- vs time-window excess scatter analysis with covering-field cache support and publication plots.
- `scripts/bundle_cache.py` & `scripts/smoke_test.py`: Compressed `<= 50 MB` demo cache bundler and local/remote endpoint smoke test (`GET /health`, `/api/meta`, `/api/image.png`, `/api/motion`).
- `app.py` & `static/index.html`: Thread-safe FastAPI backend with bounded expiring `ProductCache`, request timeouts, IRSA concurrency limiting, Difference mode, animated `/api/blink.gif` export, Gaia DR3 overlay (`/api/catalog`), astrometric fitting (`/api/motion`), and dependency-free interactive viewer.
- `docs/`, `data/`, & `results/`: API contract (`docs/api_contract.md`), source-of-truth notes (`docs/source_of_truth.md`), literature CSVs (`data/benchmarks.csv`, `data/flicker_stars.csv`), and script-generated validation/experiment outputs.
- `tests/`: Automated Python and JavaScript test suites (`test_app.py`, `test_catalog_api.py`, `test_coords.py`, `test_data.py`, `test_deployment.py`, `test_flags.py`, `test_flicker.py`, `test_frame.py`, `test_gaia.py`, `test_integration.py`, `test_motion.py`, `test_motion_api.py`, `test_processing.py`, `test_viewer.cjs`).

### Data credits & AI assistance disclosure

- **Data sources**: Based on SPHEREx Level-2 spectral image cutouts retrieved via the **NASA/IPAC Infrared Science Archive (IRSA)** (`spherex.artifact`, `spherex.plane`), target resolution via **CDS SIMBAD**, and reference astrometry from the **European Space Agency (ESA) mission Gaia DR3** (`gaiadr3.gaia_source`).
- **AI assistance**: AI coding assistants were used as pair-programming tools during the hackathon across modules, tests, and documentation (files carrying `# AI-assisted:` headers note AI pair-programming assistance). All IRSA SPHEREx data-format rules, flag bit tables, Gaia DR3 schema checks, and literature target parameters were verified against project specifications and cited sources.
