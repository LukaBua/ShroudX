# Core Build Prompt Pack: SPHEREx Sky-Motion Viewer

NASA Space Apps 2026, challenge "Planet X and SPHEREx". Core scope only.

## How to use this pack

1. Start a fresh AI coding session. Paste Prompt 0 first, then run Prompts 1 to 8 in order, one at a time. Run the tests and commit after every prompt. Do not start the next prompt until the previous module's tests pass. Frequent commits also give you a timestamped record that the work was built during the event.
2. Build from scratch. Do not paste any source file from the earlier project into the AI. This pack contains only documented facts about the data format and lessons about pitfalls, not code.
3. Read the Judging and Awards Guide (published November 13) for the rules on AI-assisted development and on disclosing pre-event preparation, and follow them. I have not seen the 2026 text, so confirm this yourself.
4. Verify data facts against IRSA's SPHEREx tutorials (spherex-intro and spherex-cutouts) and the SPHEREx Explanatory Supplement. If the AI contradicts the documentation, the documentation wins. In particular, take the FLAGS bit definitions from the MP\_\* keywords in a real FLAGS header, not from memory.
5. Replace anything in \[square brackets\] before sending.

## What counts as core

In scope: coordinate and name input, IRSA ingestion with caching, flag decoding and masking, zodiacal-light and background subtraction, grouping by detector and wavelength window, epoch splitting, alignment to a common sky grid, inverse-variance stacking, a FastAPI backend, and a single-file blink viewer with shareable URLs.

Out of scope until the core works end to end on real data: difference maps, proper-motion fitting, spectra, catalog overlays, pixel inspector, validation scripts, a blind mover search, and the colour-flicker experiment.

Suggested target: a working core on real WISE 0855 data within the first 12 to 14 hours, leaving the rest of the weekend for the science features and the submission.

## Prompt 0: master context (paste first, and again at the start of any new session)

```text
You are my pair-programming partner for a 48-hour hackathon: NASA Space Apps 2026, challenge "Planet X and SPHEREx". We are building a public web tool where a user enters sky coordinates (RA/Dec) or an object name and sees a time series of SPHEREx images of that patch, as a blink ("like a little video") and as stacked images, so moving objects (brown dwarfs, nearby stars, asteroids, comets) are easy to spot.

Stack: Python 3.10+, numpy, scipy, astropy, reproject, astroquery/pyvo, FastAPI, uvicorn, pillow, pytest. Frontend: one static HTML/JS file, no build step. Flat repo layout: frame.py, coords.py, flags.py, data.py, processing.py, app.py, static/index.html, scripts/, tests/.

Working rules:
1. One module per request. Write the code, then pytest tests, then tell me how to run them. Do not touch other modules unless I ask.
2. Small, typed, documented functions. No dead code, no placeholder TODOs.
3. Never fabricate data, catalog entries, identifiers or benchmark numbers. If you do not know a fact about the SPHEREx file format, say so and ask me; I will check the IRSA documentation.
4. Never silently substitute synthetic data for real data. A synthetic mode may exist only when explicitly selected, and every API response from it must say "synthetic": true.
5. Do not claim performance numbers (speedups, latency) that you have not measured.
6. Fail loudly with clear user-facing messages, for example "no SPHEREx coverage at this position".

Facts about the data (from IRSA documentation). Treat these as requirements:
- SPHEREx Level-2 images are 2040x2040 pixel FITS files of hundreds of MB. Never download full images; request small cutouts through IRSA's cutout service.
- Each cutout has HDUs: PRIMARY, IMAGE, FLAGS, VARIANCE, ZODI, PSF, WCS-WAVE. Always read them by name, never by index. Drop PSF when caching (it is large).
- IMAGE and ZODI are in MJy/sr; VARIANCE is in (MJy/sr)^2; sigma = sqrt(VARIANCE). IMAGE is NOT zodiacal-light subtracted: subtract ZODI.
- FLAGS is a bitmask with 16 defined flags. Do not mask on flags != 0: the SOURCE bit (21) is set on many good pixels. Mask only genuine detector defects.
- Wavelength varies per pixel (Linear Variable Filters). It comes from the WCS-WAVE extension (spectral WCS, key "W", SIP off), evaluated at the target pixel. Each exposure sees the sky at a different wavelength, so a fixed star changes brightness between exposures without moving. Therefore frames may only be blinked or stacked together within the same detector (1 to 6) AND a narrow wavelength window (default 0.10 micrometres, configurable).
- Detector wavelength ranges (micrometres): 1: 0.75-1.09, 2: 1.10-1.62, 3: 1.63-2.41, 4: 2.42-3.82, 5: 3.83-4.41, 6: 4.42-5.00.
- Pixel scale is about 6.15 arcsec per pixel. Revisits of one patch cluster into "epochs" about six months apart: split epochs where consecutive observation times differ by more than 30 days (configurable).
- Frames come from different processing versions (for example l2b-v20). Record the version of every frame and never mix them silently.
- IRSA behaviour: TAP queries can take 4 to 17 seconds; parallel cutout downloads fail above roughly 5 workers; downloads drop occasionally, so retry with back-off and cache to disk.

Known pitfalls to design around:
- High proper-motion targets move: a catalogued position can be arcminutes away from where the object is during SPHEREx observations (WISE 0855 has moved about 2 arcminutes since its catalogued position). Name resolution must handle this explicitly.
- Every user-facing claim must be honest: show data version, wavelength window, frame count and warnings.

Acknowledge these rules in three lines, then wait for my first module request.
```

## Prompt 1: scaffold and frame.py

```text
Create the repo scaffold and frame.py.

Scaffold: requirements.txt (minimum versions), pyproject.toml with pytest config (pythonpath ".") and ruff, .gitignore (.venv, __pycache__, .pytest_cache, cache/ downloads, frontend build output), tests/__init__.py, and empty scripts/ and static/ folders.

frame.py: a dataclass Frame with fields: image (ny, nx float array, MJy/sr, zodi NOT subtracted), variance, mask (bool, True = bad pixel), wavelength (micrometres at the target), detector (int 1 to 6), mjd (float), wcs (astropy WCS or None), zodi (optional array), version (int or None), uri (str or None), unreliable (optional bool array: saturated or non-linear pixels that stay usable for display and centroiding but not for flux), meta (dict or None).

Add: validate() raising ValueError on shape mismatch, detector outside 1 to 6, or non-finite wavelength or mjd; a property sigma = sqrt(variance) with non-positive variance returned as NaN; and a helper copy_with(**changes) returning a new Frame.

Tests: a valid frame passes; each validation failure raises.
```

## Prompt 2: coords.py

```text
Write coords.py: turn user input into a sky position.

1. parse_position(ra, dec) accepting decimal degrees (133.795, -7.245) and sexagesimal in both styles ("08h55m10.831s" with "-07d14m42.53s", and "08 55 10.831" with "-07 14 42.53"). Treat a bare sexagesimal RA as hours. Enforce 0 <= RA < 360 and -90 <= Dec <= 90 with clear ValueError messages. Return decimal degrees (ICRS).
2. resolve_name(name): resolve via astropy SkyCoord.from_name (SIMBAD/CDS), with a small offline table so the demo works without internet for: WISE 0855-0714, Barnard's Star, Epsilon Indi B, RW Aurigae, R Draconis. For the offline table, define the fields (ra, dec, pmra, pmdec, reference epoch) but leave the numeric values as None; I will fill them in from the literature. Never invent coordinates or proper motions.
3. Proper-motion awareness: position_at_epoch(ra, dec, pmra, pmdec, ref_epoch, target_epoch) applying linear motion (pmra means mu_alpha*cos(dec)). resolve_name returns a ResolvedTarget (ra, dec, source, pm_known, warnings). When the target has known proper motion above 1 arcsec per year, return the position propagated to the SPHEREx survey epoch (default 2026.0, configurable) and add a warning saying so. For raw coordinates typed by the user, expose high_pm_hint(fov_arcsec) so the UI can recommend a larger field of view for fast movers.
4. Target profiles: a small dict mapping target class to a suggested detector (for example cold brown dwarf: 5, M dwarf: 2, T Tauri star: 3), used only as a default the user can override. Document that this is a heuristic.
5. No network calls at import time. Network failures fall back to the offline table or raise a clear error.

Tests (pytest, network mocked): decimal and both sexagesimal formats give the same position for the challenge's WISE 0855 coordinates (RA 08h55m10.831s, Dec -07d14m42.53s); out-of-range inputs raise; proper-motion propagation matches a hand-computed value; offline lookup works with the network disabled; an unknown name raises a clear error.
```

## Prompt 3: flags.py

Before sending, open one real cutout, print the FLAGS header's MP\_\* keywords, and paste the bit table into the prompt. Do not let the AI write the bit meanings from memory.

```text
Write flags.py: decode the SPHEREx FLAGS bitmask.

Do NOT guess bit meanings. Use exactly this table, taken from the FLAGS extension header (MP_* keywords) and Table 8 of the SPHEREx Explanatory Supplement:

[PASTE THE BIT TABLE HERE]

Requirements:
1. FLAG_BITS: dict of name to bit number, from my table.
2. Groups, with a comment giving the rationale for each: DETECTOR_DEFECTS (permanent hardware problems, safe to mask), UNRELIABLE_CORE (saturated, non-linear or persistent pixels whose position is fine but flux is unreliable), TRANSIENTS, INFORMATION_ONLY (never mask; includes SOURCE and FULLSAMPLE).
3. flags_to_mask(flags, names=DETECTOR_DEFECTS) returning a bool array; decode_flags(value) returning the flag names set for one pixel; summarize(flags) returning counts per flag.
4. Provide no function that masks all non-zero flags.

Tests: single-bit and multi-bit values; a SOURCE-only pixel is not masked; a pixel with the hot-pixel flag is masked; uint16, int32 and float inputs; shape preserved.
```

## Prompt 4: data.py

```text
Write data.py: fetch SPHEREx cutouts from IRSA, cache them, and return Frame objects.

Service facts (verify against IRSA's SPHEREx cutouts tutorial and tell me if any detail looks wrong): IRSA TAP, tables spherex.artifact and spherex.plane joined on planeid; select the artifact uri, the plane's time_bounds_lower (MJD) and energy_bandpassname; filter with CONTAINS(POINT('ICRS', ra, dec), plane poly). The cutout URL has the form https://irsa.ipac.caltech.edu/<uri>?center=<ra>,<dec>d&size=<size_deg>

Requirements:
1. query_images(ra, dec) using astroquery.ipac.irsa Irsa.query_tap (or pyvo), returning rows of (uri, mjd, bandpass). Raise a clear NoCoverage error when nothing is returned.
2. download_cutout(url, dest, retries=4) with back-off between retries. Keep only PRIMARY, IMAGE, FLAGS, VARIANCE, ZODI and WCS-WAVE. Write to a temporary file, then rename (atomic). Return a success bool.
3. Download in parallel with at most 5 workers and report progress. Individual failures are reported but not fatal as long as some frames succeed.
4. Cache as cache/<key>/NNNN.fits plus index.csv (file, mjd, bandpass, uri). The key comes from the rounded RA, Dec and the cutout size bucket (120, 240, 480, 600 arcsec). If the index is missing or damaged, rebuild it from the FITS headers. Serve a smaller field of view by cropping a cached larger cutout with astropy Cutout2D.
5. frame_from_hdul(hdul, ra, dec, mjd, uri): read layers by name; mask = defect flags | non-finite pixels | non-positive variance; unreliable = the UNRELIABLE_CORE flags; detector from the header; wavelength from the WCS-WAVE spectral WCS evaluated at the target pixel; processing version parsed from the filename; return a validated Frame.
6. Modes via the SPHX_MODE environment variable: cache (offline; clear error if not cached), live (query IRSA), auto (cache first, then IRSA), synthetic (explicit only: generates frames with a moving source and wavelength-dependent brightness, with meta["synthetic"] = True on every Frame). In auto mode, if IRSA is unreachable, raise a clear error. NEVER fall back to synthetic automatically.
7. get_frames(ra, dec, fov) returning a tuple of Frames sorted by MJD, with in-process caching.
8. scripts/fetch_cutouts.py: a CLI that fetches and caches a target by name or coordinates, so I can pre-download demo fields.

Tests, with no network, using small FITS files built inside the tests: reading by name, flag masking, damaged-index recovery, cropping geometry, mode selection, no silent synthetic fallback, and retry logic with a mocked failing downloader.
```

## Prompt 5: processing.py

```text
Write processing.py: turn raw Frames into comparable, display-ready images.

1. clean_frame(frame): subtract ZODI pixel by pixel when present; then subtract a 3-sigma-clipped median of the unmasked pixels as a local background; inpaint masked pixels with a local median for display only (keep the mask); return a new Frame and never mutate the input.
2. group_frames(frames, detector=None, wmin=None, wmax=None, max_width=0.10): choose the detector (default: the one with the most frames). When no window is given, choose the narrowest window of width <= max_width micrometres that contains the most distinct epochs and then the most frames. Return the chosen frames plus warnings (fewer than 3 frames, window forced wider than max_width, mixed processing versions).
3. split_epochs(frames, gap_days=30.0): assign an epoch index from MJD gaps; return per-epoch frame counts, start and end MJD, and date strings.
4. align_frames(frames): reproject all frames onto one common celestial grid centred on the target, north up and east left (astropy TAN WCS, about 6.15 arcsec per pixel, reproject_interp). Reproject the variance too and document the approximation used. Raise if a frame has no WCS.
5. stack_frames(frames): inverse-variance weighted coadd with MAD sigma clipping across frames per pixel; return the image, the variance, and the number of frames contributing per pixel.
6. render_png(image, stretch): percentile stretch with optional asinh, returning PNG bytes. Compute one stretch for a whole sequence and reuse it for every frame, so blinking is not disturbed by changing contrast. Expose the stretch values.

Tests with synthetic frames: zodi removal; background level near zero; grouping picks the intended window; epoch splitting at the 30-day gap; stack noise falling roughly as 1/sqrt(N) on simulated Gaussian noise; alignment preserves a source position under a rotated WCS; the PNG stretch is identical across a sequence.
```

## Prompt 6: app.py

```text
Write app.py (FastAPI) exposing the core as an API. All parameters are query strings so every view is shareable by URL: target, ra, dec, fov (30 to 600 arcsec, default 120, larger default for known fast movers), detector (1 to 6), wmin, wmax, mode (blink or stack).

Endpoints:
- GET /api/resolve: name or coordinates in; ra, dec and warnings out (including proper-motion notes).
- GET /api/meta: runs get_frames, group_frames and split_epochs; returns the resolved position, fov, detector, wavelength window, a per-frame list (index, mjd, date, wavelength, epoch, version), the epoch summary, warnings, data_source (archive, cache or synthetic), n_frames, and the detector wavelength ranges.
- GET /api/image.png?index=N: PNG of cleaned, aligned frame N (blink), or of the stack (mode=stack), using the shared stretch.
- GET /health.

Requirements: cache heavy work in-process, keyed by the full parameter set; blocking work must not stall the event loop; clear error bodies with status codes (400 bad input, 404 no coverage, 502 IRSA unreachable); no endpoint returns synthetic data unless SPHX_MODE=synthetic, and then every response says so; serve static/index.html at / and static assets; CORS closed by default.

Tests with FastAPI TestClient and synthetic FITS fixtures: every endpoint, every error code, the shared-stretch property, and parameter validation.
```

## Prompt 7: frontend (static/index.html)

```text
Write static/index.html: a single file, vanilla JS, no build step, responsive, readable on a projector.

1. Input panel: target name OR RA/Dec (decimal or sexagesimal); field-of-view slider (30 to 600 arcsec); detector selector (auto, then 1 to 6, each with its wavelength range); optional wavelength window; Blink/Stack toggle.
2. Blink viewer: preload all frame PNGs with a progress indicator; play and pause; step back and forward; speed slider (50 to 2000 ms per frame); scrubber. For each frame show date, MJD, epoch, wavelength and processing version.
3. Epoch timeline showing the observation passes and frame counts.
4. Overlays: compass (north up, east left), a scale bar in arcseconds computed from the field of view, and a crosshair at the field centre. Instruments must not cover the image.
5. An always-visible status banner: data source (archive, cache or synthetic), number of frames, detector and wavelength window, and every API warning in plain language. If the data is synthetic, show a prominent "SYNTHETIC DATA" label.
6. Shareable URL: all parameters synced to the query string and restored on load.
7. States: loading with progress; empty ("no SPHEREx coverage here"); error with a retry button. Keyboard-accessible controls, labels and sufficient contrast.
8. A line of honest guidance: a visible change between frames is a candidate, not proof of motion, and frames in one sequence share a wavelength window to avoid colour flicker.

No network dependencies other than API calls to the same origin.
```

## Prompt 8: integration, README and self-review

```text
Integration pass.
(a) Write tests/test_integration.py running the full path on synthetic FITS fixtures: coordinates, get_frames, group, epochs, align, stack, then PNG through the API.
(b) Write README.md: what it does; quick start (install, SPHX_MODE options, how to pre-fetch demo targets with scripts/fetch_cutouts.py); an API table; honest limitations (cutout size, no proper-motion fitting yet); and a section "Built during the hackathon" listing what was developed in the event. Keep it factual, with no unmeasured claims.
(c) Run ruff and pytest and list any failures.
(d) Review every module against the working rules: silent synthetic fallback, masking on flags != 0, index-based HDU access, fabricated data. Report findings before fixing anything.
```

## Checks you must do yourself between prompts

1. After Prompt 3, confirm the bit table against a real FLAGS header.
2. After Prompt 4, on the venue network early, run scripts/fetch\_cutouts.py for each demo target and for two fields you have not used before. Downloads are slow and IRSA is the single biggest schedule risk, so start them first.
3. After Prompt 5, check one real sequence by eye: the wavelength window is narrow, background is flat, and a known mover such as WISE 0855 visibly shifts between epochs.
4. After Prompt 7, open the page on a laptop and a phone and test a shared URL.
5. Enter the challenge's published WISE 0855 coordinates directly. If the field looks empty, the proper-motion handling is not working.

## What to build next, once the core runs on real data

1. A hosted public deployment, so judges can open a URL instead of installing anything.
2. The colour-flicker experiment: a stationary star's flux against wavelength, before and after windowing. It justifies the whole design and makes a strong demo visual.
3. Multi-epoch difference maps and a proper-motion and parallax fit validated on the benchmark targets, with honest uncertainties.
4. A live Gaia query for the catalog overlay, with no hand-typed entries.
5. A blind mover search on an arbitrary field.
