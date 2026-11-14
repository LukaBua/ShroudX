# API contract

Version 1.2. Owner: Luka. Consumers: Shayan (frontend and `app.py`), everyone writing tests.

This file is the single source of truth for what the backend returns and what the frontend expects. Change it only through Luka, who announces every change to the whole team. Example values below are illustrative, not real data.

## General rules

1. All endpoints are served from the same origin as the page. No CORS is configured.
2. Every parameter is a query-string parameter, so any view can be reproduced from a URL.
3. JSON responses are UTF-8. Image responses are PNG.
4. Angles of position are decimal degrees (ICRS) in responses. Sizes of fields are arcseconds. Wavelengths are micrometres. Times are Modified Julian Date (MJD) plus an ISO date string (UTC, `YYYY-MM-DD`).
5. The server never returns synthetic data unless it was started with `SPHX_MODE=synthetic`. In that case every JSON response has `"synthetic": true` and the page must show a prominent SYNTHETIC DATA label.
6. Every response that carries scientific content includes a `warnings` list of plain-language strings. The page shows them all.

## Shared query parameters

These apply to `/api/resolve` (`target`, `ra` and `dec` only), `/api/meta` and `/api/image.png`.

| Parameter | Type | Default | Rules |
| --- | --- | --- | --- |
| `target` | string | none | Object name, for example `WISE 0855-0714`. Use either `target` or both `ra` and `dec`, never both. |
| `ra` | string or number | none | Decimal degrees (`133.795`) or sexagesimal (`08h55m10.831s` or `08 55 10.831`). A bare sexagesimal value is hours. URL-encode spaces. |
| `dec` | string or number | none | Decimal degrees or sexagesimal (`-07d14m42.53s` or `-07 14 42.53`). |
| `fov` | number | 120 | Field of view in arcseconds, 30 to 600. For targets known to be fast movers the server may apply a larger default; the applied value is returned as `fov_arcsec`. |
| `detector` | integer | auto | 1 to 6. If omitted, the server picks the detector with the most usable frames. |
| `wmin`, `wmax` | number | auto | Wavelength window in micrometres. Give both or neither. If omitted, the server picks the narrowest window of at most 0.10 micrometres that contains the most epochs and then the most frames. |
| `mode` | string | `blink` | `blink`, `stack` or `diff`. Only affects `/api/image.png`. |

Only `/api/image.png` also takes `index` (integer, 0-based, position in the `frames` list returned by `/api/meta` for the same parameters). It is required when `mode=blink` and ignored when `mode=stack` or `mode=diff`.

With `mode=diff`, `/api/image.png` also takes `epoch_a` and `epoch_b` (integers matching `epochs[].epoch` in `/api/meta`; defaults: the first and the last epoch). The image is the signed difference, later minus earlier, with a symmetric stretch around zero. Both epochs must use the same detector and wavelength window; if their mean wavelengths differ by more than half of `max_width` (0.05 micrometres with the default 0.10), `/api/meta` adds a warning when the request is for `mode=diff`.

Difference mode is implemented. Supply the same optional `epoch_a` and `epoch_b` to `/api/meta` so its warnings describe the selected comparison. Reversed epoch selections are normalized chronologically: the sign always means later minus earlier. Unknown, negative, non-integer, repeated, or equal epoch identifiers return 400 `bad_input`. Fewer than two available epochs or no overlapping usable pixels return 422 `insufficient_data`. Epoch parameters are ignored outside Difference mode.

The difference PNG uses the exact aligned grid and dimensions of the blink/stack products. `X-Diff-Limit` always reports the positive symmetric display limit in sigma units (the default renderer percentile of absolute finite significance values, with a positive fallback for an all-zero result). The blink/stack `stretch` field remains unchanged. Scientific differencing and rendering are delegated to `processing.difference_images` and `processing.render_diff_png`.

UI-only parameters, kept in the page URL but never sent to the API: `speed` (milliseconds per frame, 50 to 2000) and `frame` (current frame index).

## Error format

Every error uses the same JSON body.

```json
{
  "error": {
    "code": "no_coverage",
    "message": "No SPHEREx coverage at this position.",
    "detail": {}
  }
}
```

| Status | `code` | When |
| --- | --- | --- |
| 400 | `bad_input` | Missing or malformed parameters, values out of range, both `target` and `ra`/`dec` given, only one of `wmin`/`wmax`. |
| 404 | `no_coverage` | The archive returned no images for this position. |
| 404 | `not_cached` | The server runs in cache mode and this position is not cached. |
| 404 | `unknown_target` | The name could not be resolved and is not in the offline table. |
| 422 | `insufficient_data` | The archive has coverage, but there are too few epochs or frames for the requested computation (for example `/api/motion` with fewer than 2 epochs). |
| 502 | `archive_unreachable` | The archive (IRSA) could not be reached in live or auto mode. The server does not fall back to synthetic data. |
| 500 | `internal_error` | Anything unexpected. The `message` is safe to show. |

`detail` is an object that may carry extra fields (for example the parameter name that failed). The page shows `message` and may ignore `detail`.

## GET /health

Returns the server state. No parameters.

```json
{
  "status": "ok",
  "data_mode": "auto",
  "cache_size_mb": 38.4
}
```

`data_mode` is one of `cache`, `live`, `auto`, `synthetic`. `cache_size_mb` is `null` if not measured.

## GET /api/resolve

Turns a name or coordinates into a position and says what the server knows about it.

Parameters: `target`, or `ra` and `dec`.

```json
{
  "ra": 133.7599,
  "dec": -7.24221,
  "source": "offline_table",
  "pm_known": true,
  "warnings": [
    "Position propagated from the catalogue epoch to 2026.0 using the stored proper motion."
  ],
  "suggested_fov_arcsec": 240,
  "suggested_detector": 5
}
```

| Field | Type | Meaning |
| --- | --- | --- |
| `ra`, `dec` | number | Position in decimal degrees (ICRS), after any proper-motion propagation. |
| `source` | string | `simbad`, `offline_table` or `coordinates` (the user typed raw coordinates). |
| `pm_known` | boolean | Whether the server knew a proper motion for this object. |
| `warnings` | string list | Plain-language notes, including proper-motion and fast-mover hints. |
| `suggested_fov_arcsec` | number or null | A larger field of view the page may offer for fast movers. |
| `suggested_detector` | integer or null | A default detector suggestion; a heuristic the user can override. |

## GET /api/meta

Runs the whole pipeline up to grouping and epoch splitting and describes the result. The page calls this first, then fetches one image per frame.

```json
{
  "synthetic": false,
  "data_source": "cache",
  "target": {
    "name": "WISE 0855-0714",
    "ra": 133.7599,
    "dec": -7.24221,
    "source": "offline_table",
    "pm_known": true
  },
  "fov_arcsec": 240,
  "pixel_scale_arcsec": 6.15,
  "image_shape": [39, 39],
  "detector": 5,
  "wavelength_window_um": [4.10, 4.20],
  "detector_ranges_um": {
    "1": [0.75, 1.09],
    "2": [1.10, 1.62],
    "3": [1.63, 2.41],
    "4": [2.42, 3.82],
    "5": [3.83, 4.41],
    "6": [4.42, 5.00]
  },
  "n_frames": 3,
  "frames": [
    { "index": 0, "mjd": 60900.2, "date": "2025-08-13", "wavelength_um": 4.12, "epoch": 0, "version": 20 },
    { "index": 1, "mjd": 60902.7, "date": "2025-08-15", "wavelength_um": 4.15, "epoch": 0, "version": 20 },
    { "index": 2, "mjd": 61085.3, "date": "2026-02-14", "wavelength_um": 4.11, "epoch": 1, "version": 20 }
  ],
  "epochs": [
    { "epoch": 0, "n_frames": 2, "mjd_start": 60900.2, "mjd_end": 60902.7, "date_start": "2025-08-13", "date_end": "2025-08-15" },
    { "epoch": 1, "n_frames": 1, "mjd_start": 61085.3, "mjd_end": 61085.3, "date_start": "2026-02-14", "date_end": "2026-02-14" }
  ],
  "stretch": { "mode": "asinh", "vmin": -0.8, "vmax": 12.5 },
  "warnings": [
    "Fewer than 3 frames in the chosen window."
  ]
}
```

| Field | Type | Meaning |
| --- | --- | --- |
| `synthetic` | boolean | True only in synthetic mode. |
| `data_source` | string | `archive` (fetched just now), `cache` (served from disk) or `synthetic`. |
| `target` | object | The resolved position, as in `/api/resolve`. `name` is null for raw coordinates. |
| `fov_arcsec` | number | The field of view actually used. |
| `pixel_scale_arcsec` | number | Pixel size of the returned images, about 6.15. |
| `image_shape` | integer pair | `[height, width]` in pixels of every PNG returned for these parameters. |
| `detector` | integer | The detector actually used. |
| `wavelength_window_um` | number pair | The window actually used: `[low, high]`. |
| `detector_ranges_um` | object | Wavelength range of each detector, for the page's detector selector. |
| `n_frames` | integer | Length of `frames`. |
| `frames` | list | One entry per frame in blink order (oldest first). `index` is what `/api/image.png` takes. `version` is the processing version, or null. |
| `epochs` | list | Observation passes, split where consecutive frames are more than 30 days apart. |
| `stretch` | object | The one display stretch shared by every frame in `mode=blink` and by the `mode=stack` image. The page may show it but must not change it. It does not apply to `mode=diff`, which has its own symmetric stretch (see `/api/image.png`). |
| `warnings` | string list | Plain-language notes: too few frames, window wider than 0.10 micrometres, mixed processing versions, proper-motion hints, and so on. |

`frames[].epoch` matches `epochs[].epoch`.

## GET /api/image.png

Returns a PNG for one frame or for the stack.

- `mode=blink` with `index=N`: the cleaned, aligned frame N.
- `mode=stack`: the inverse-variance weighted stack of all frames in the window.
- `mode=diff` with optional `epoch_a` and `epoch_b`: the signed significance difference of two epoch stacks (implemented, Phase 2, Prompt 16).

Rules:

1. The image has the size given by `image_shape` in `/api/meta` for the same parameters.
2. North is up and east is left.
3. Every `blink` and `stack` image for one parameter set uses the `stretch` from `/api/meta`, so blinking is not disturbed by changing contrast. `mode=diff` images use a symmetric stretch around zero with one fixed limit for the whole result; that limit (in units of sigma) is returned in the response header `X-Diff-Limit`.
4. Errors use the JSON error format above, not an image.

## Phase 2 endpoints

GIF export is implemented. The remaining endpoints are reserved drafts whose exact shapes are defined in Prompts 11 and 13; their examples are agreed starting points for building against mock data.

### GET /api/blink.gif

Implemented: same selection parameters as `/api/meta`, plus integer `speed` (milliseconds per frame, 50 to 2000, default 500). `mode` is validated as for `/api/meta` but export always includes the entire blink sequence, including when the viewer is in stack mode. `index` and UI-only parameters are ignored.

Returns `image/gif` as the attachment `shroudx-blink.gif`, looping forever with one frame per blink observation in the same order. Images come directly from the PNG products and share their authoritative stretch. A caption strip outside the image shows frame ordinal, UTC date, wavelength in micrometres, and a SYNTHETIC DATA label in synthetic mode. Narrow images are centred on a wider caption canvas. GIF palette quantization and any image downscaling are display operations only. Client brightness and contrast filters are never applied to the export.

GIF delays have 10 ms resolution; speeds are rounded to the nearest 10 ms (ties upward). The encoded file is strictly below 10,000,000 bytes; the image area is progressively downscaled if necessary. `X-Gif-Size-Bytes` always reports the exact response-body size, whether downscaled or not. `X-Synthetic` and `X-Data-Source` report provenance as for PNGs. `Content-Disposition` requests a download.

Errors use the standard JSON error body: 400 for invalid parameters/speed, 404 for no coverage or missing cache, 502 for an unreachable archive, and 422 if no usable frames exist or even a minimum-size export cannot fit all frames under the size limit. No frames are silently dropped.

### GET /api/catalog

Same parameters as `/api/meta`. Returns Gaia DR3 sources in the field, moved to the SPHEREx observation epoch with their proper motions. Never fabricated: if Gaia cannot be reached the server returns 502 with no fallback data, and in cache mode it serves only cached Gaia results (otherwise `not_cached`).

```json
{
  "source": "gaia_dr3",
  "epoch": 2026.0,
  "n_sources": 2,
  "sources": [
    { "source_id": 1234567890123456789, "ra": 133.7601, "dec": -7.2420, "g_mag": 15.2, "pmra": 12.1, "pmdec": -3.4, "parallax": 4.8, "pm_missing": false },
    { "source_id": 1234567890123456790, "ra": 133.7580, "dec": -7.2431, "g_mag": 18.9, "pmra": null, "pmdec": null, "parallax": null, "pm_missing": true }
  ],
  "warnings": []
}
```

Values above are illustrative, not real data. At most 500 sources are returned, brightest first. Proper motions are in milliarcseconds per year, as Gaia publishes them.

### GET /api/motion

Implemented. Same selection parameters as `/api/meta`; `mode` is validated but does not change the fit, and `epoch_a`/`epoch_b` are ignored. The fit uses all accepted epochs in the same cleaned, aligned detector/wavelength selection as the images, through `motion.measure_and_fit_epochs`. The API does not supply a fixed literature parallax or tune the fit to benchmark values. Returns:

```json
{
  "epochs": [
    { "epoch": 0, "mjd": 60901.4, "ra": 133.7599, "dec": -7.24221, "sigma_arcsec": 0.9, "n_frames": 2 }
  ],
  "fit": {
    "pm_arcsec_per_yr": 8.1,
    "pm_sigma": 1.2,
    "position_angle_deg": 245.0,
    "pa_sigma": 9.0,
    "parallax_arcsec": null,
    "parallax_sigma": null,
    "chi2_reduced": 1.1,
    "n_epochs": 2,
    "significance": 6.7,
    "uncertainty_scaled": false
  },
  "benchmark": {
    "literature_pm_arcsec_per_yr": null,
    "literature_parallax_arcsec": null,
    "verdict": null
  },
  "warnings": []
}
```

`verdict` is `AGREE`, `UNINFORMATIVE` or `DISAGREE` when the target is in `data/benchmarks.csv`, otherwise null. Errors: 404 `no_coverage` for no coverage, 422 `insufficient_data` for too few epochs or frames.

Details of the implemented response:

- `epochs[].epoch` retains the original ID from `/api/meta` even if other epochs are rejected. `n_frames` counts accepted centroid measurements. `sigma_arcsec` is the larger of the RA*cos(Dec) and Dec marginal uncertainties. It is a conservative display radius, not a calibrated 2-D confidence region. Fit uncertainties and `uncertainty_scaled` are taken from `motion.py`; uncertainties are not scaled a second time by the API.
- `epochs[]` additionally includes `x`, `y`, and `radius_px` for the overlay, projected through the aligned celestial WCS. Coordinates use PNG edges as the origin: `(0,0)` is the upper left, pixel centres are at half-integers, and `y` includes the FITS-to-PNG vertical flip. `radius_px = sigma_arcsec / pixel_scale_arcsec`. `image_shape` gives `[height, width]` for the SVG viewBox. The arrow connects earliest to latest accepted measured positions; it is not a fitted trajectory.
- `chi2_reduced` is null when residual degrees of freedom are zero, with a warning. Unfitted parallax and its uncertainty are null. Errors/warnings returned by the motion module are shown without inventing missing measurements. A significance below 3 means motion is not established.
- Benchmark names are matched by name or the coordinate module's known aliases, not by sky proximity. Literature values and `benchmark.citation` come only from `data/benchmarks.csv`, with mas converted to arcsec by the existing validation CSV loader. Verdicts use `scripts/validate_motion.py`'s predefined criteria: below 0.05 arcsec/yr literature motion, compare measured significance to 3; otherwise UNINFORMATIVE when sigma >= 0.3 times literature motion, AGREE when the vector residual is below 3 sigma, and DISAGREE otherwise. Disagreement includes the existing systematics diagnostics and an unresolved-systematics warning. Synthetic results have no benchmark verdict.
- `synthetic` explicitly identifies simulated data. Base selection and motion warnings are combined. The Motion panel is optional: failure leaves blink/stack images available.
- Every successful measurement is atomically written to `results/motion-<hash>.json`. `provenance` contains a rerunnable HTTP command, the data mode, selected frame URIs/MJDs/wavelengths/versions, and the benchmark CSV SHA-256. Reproduction requires the same code and data/cache. The command assumes a local server on port 8000 in the recorded mode. Computed results are never hand-edited.

Additional errors: 400 for malformed selection parameters, 404 for missing cache or unknown target, 502 for archive failure. Motion-module rejection (too few accepted centroids, singular fit, mixed versions) returns 422 with its explanatory message. Unexpected failures return a safe 500. Missing benchmark CSV disables comparison with a warning; malformed or uncited benchmark records fail loudly.

### GET /api/movers

Same parameters as `/api/meta`, plus `threshold` (3 to 10, sigma, default 5) and `max_pm` (arcseconds per year, default 15). Returns the ranked candidates, the false-match estimate, per-epoch source counts and warnings. The exact candidate fields are defined in Prompt 13.

## Building against mock data

Until the real endpoints exist, the frontend can serve static JSON files shaped exactly like the examples above plus a handful of PNGs of the size given by `image_shape`. Swap in the real endpoints by changing the base path only. If the frontend needs a field this contract does not have, ask Luka to add it here first.

## Change process

1. Anyone who needs a change asks Luka, with the reason.
2. Luka edits this file and announces the change to everyone at once.
3. Owners update their code and tests the same day.
4. Never change a field's name, type or meaning without updating this file.

## Changelog

- Implemented `/api/motion`, projected centroid overlays, optional panel, benchmark verdict adaptation and reproducible JSON artifacts; documented null chi-squared and marginal error-circle semantics.
- Added implemented Difference mode, epoch validation and error semantics, chronological sign convention, wavelength warnings and `X-Diff-Limit` behavior.
- 1.2: Implemented `/api/blink.gif`, with exact size and provenance headers, captioned frames, integer speed validation and GIF timing resolution. Documented downscaling and display-only brightness/contrast controls.
- 1.1: `/api/resolve` takes only `target`, `ra`, `dec`. Added the 422 `insufficient_data` error. Stated that `stretch` in `/api/meta` applies to blink and stack only, and added the `X-Diff-Limit` header for diff images. The diff wavelength warning threshold is now half of `max_width`.
- 1.0: first version.
