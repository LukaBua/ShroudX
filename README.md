# ShroudX

SPHEREx time-series blink and stack viewer. The core FastAPI backend serves the
single-file viewer and the endpoints in `docs/api_contract.md`.

## Run locally (PowerShell)

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
$env:SPHX_MODE = "synthetic"
.\.venv\Scripts\python.exe -m uvicorn app:app --host 127.0.0.1 --port 8000
```

Open <http://127.0.0.1:8000/?ra=133.76&dec=-7.24>. Leave the viewer's connection
set to **Project API** to exercise the Python pipeline. Synthetic mode uses
simulated observations and labels them explicitly. Coordinates avoid needing
online name resolution. The separate **Synthetic interface preview** selection
exercises only the browser, not the Python backend.

Set `SPHX_MODE` before starting the server:

- `auto` (default): read the cache, otherwise query IRSA; archive failures are errors.
- `cache`: use existing cached cutouts only; missing fields return `not_cached`.
- `live`: use the data layer's live retrieval path.
- `synthetic`: explicitly generate simulated frames; never used as a fallback.

The cache defaults to this project's `cache/` directory. Override it with
`SPHX_CACHE_DIR`. No practice-project files or cache are used. Real data must be
downloaded separately with `scripts/fetch_cutouts.py` or through the API.

## Core endpoints

- `/health`: data mode and measured disk-cache size.
- `/api/resolve?ra=133.76&dec=-7.24`: coordinate/name resolution and warnings.
- `/api/meta?ra=133.76&dec=-7.24`: sequence metadata, epochs, provenance and stretch.
- `/api/image.png?ra=133.76&dec=-7.24&mode=blink&index=0`: a cleaned, aligned frame.
- `/api/image.png?ra=133.76&dec=-7.24&mode=stack`: the weighted stack.

Use `target` or both `ra` and `dec`. Optional selection parameters are `fov`
(30–600 arcseconds), `detector` (1–6), and paired `wmin`/`wmax` (micrometres).
Every frame and stack in a selection shares one display stretch. Blocking work
runs in worker threads. The application serializes pipeline builds and caches
rendered results for five minutes, up to eight selections and 64 MiB of PNGs.
The data layer maintains its own frame cache. Restart to clear both caches.

Difference imaging, motion fitting, Gaia overlays and GIF export are Phase 2
features and are not implemented by this core API. `mode=diff` returns a clear
400 response. The offline name table still needs verified catalogue values;
online name resolution does not automatically supply proper motion. Reprojection
uncertainties are approximate, and stacking may suppress moving sources.

## Verification

```powershell
.\.venv\Scripts\python.exe -m pytest -q
node --test tests/test_viewer.cjs
```

API tests use generated FITS cutouts and explicit synthetic data without network
calls. They do not establish scientific accuracy on real SPHEREx observations.
The viewer, API, and their new tests were AI-assisted with Codex; existing modules
carry their own attribution where provided.
