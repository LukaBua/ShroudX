"""Serve the unchanged practice frontend's original API without replacing ShroudX's API.

The original implementation is namespaced beside this adapter. Each application
gets separate module instances and caches. Only legacy display query parameters,
an explicit client header, or the copied page's referrer select this protocol.
Regular API clients retain the current ShroudX contracts. No synthetic fallback
is enabled: synthetic data must be explicitly selected at application startup.
"""
from __future__ import annotations

import importlib
import importlib.util
import hashlib
import json
import re
import sys
import uuid
from contextvars import ContextVar
from functools import wraps
from pathlib import Path
from urllib.parse import parse_qs, urlsplit, urlencode

from astropy.coordinates import SkyCoord
from starlette.responses import JSONResponse

ROUTES = {"/api/meta", "/api/image.png", "/api/motion", "/api/spectrum", "/api/catalog",
          "/api/pixel", "/api/detections"}
DISPLAY_KEYS = {"clean", "smooth", "show_mask", "show_vector", "show_compass"}


def is_practice_request(scope: dict) -> bool:
    """Recognise the original browser protocol without changing its source files."""
    if scope.get("path") not in ROUTES:
        return False
    if scope['path'] in {"/api/pixel", "/api/detections"}:
        return True
    headers = dict(scope.get("headers", ()))
    if headers.get(b"x-shroudx-frontend") == b"spaceapps":
        return True
    query = parse_qs(scope.get("query_string", b"").decode())
    if DISPLAY_KEYS.intersection(query):
        return True
    referer = headers.get(b"referer", b"").decode()
    return bool(DISPLAY_KEYS.intersection(parse_qs(urlsplit(referer).query)))


def create_practice_app(cache_root: Path, mode: str):
    """Load an isolated copy of the original API and point it at current cutouts."""
    package_name = "_shroudx_practice_" + uuid.uuid4().hex
    directory = Path(__file__).parent
    spec = importlib.util.spec_from_file_location(package_name, directory / "__init__.py",
                                                submodule_search_locations=[str(directory)])
    package = importlib.util.module_from_spec(spec)
    sys.modules[package_name] = package
    spec.loader.exec_module(package)
    original = importlib.import_module(package_name + ".app")
    loader = importlib.import_module(package_name + ".data")
    loader.CACHE_DIR = cache_root
    loader.mode = lambda: "mock" if mode == "synthetic" else mode
    import data as current_data
    def all_band_frames(ra,dec,fov,detector=None):
        if detector is not None:
            return current_data.get_detector_frames(ra,dec,fov,detector=detector,mode=mode,cache_root=cache_root)
        return current_data.get_frames(ra,dec,fov,mode=mode,cache_root=cache_root)
    loader.all_band_frames = all_band_frames
    hashed_folder = loader._cache_dir

    def imported_folder(ra: float, dec: float, size: float) -> Path:
        exact = hashed_folder(ra, dec, size)
        if exact.exists():
            return exact
        matches = []
        for path in cache_root.glob(f"ra*_dec*_s{int(size)}"):
            match = re.fullmatch(r"ra([0-9.]+)_dec([+-][0-9.]+)_s[0-9]+", path.name)
            if match:
                centre = SkyCoord(float(match[1]), float(match[2]), unit="deg")
                if centre.separation(SkyCoord(ra, dec, unit="deg")).arcsec < 30:
                    matches.append(path)
        if not matches:
            for path in cache_root.glob(f'detector-*/ra*_dec*_s{int(size)}'):
                match = re.fullmatch(r"ra([0-9.]+)_dec([+-][0-9.]+)_s[0-9]+", path.name)
                if match and SkyCoord(float(match[1]),float(match[2]),unit='deg').separation(SkyCoord(ra,dec,unit='deg')).arcsec < 30:
                    matches.append(path)
        return matches[0] if len(matches) == 1 else exact

    loader._cache_dir = imported_folder
    provenance = ContextVar(package_name + '_inputs', default=None)

    def capture_frames(function, selected=False):
        @wraps(function)
        def wrapped(*args, **kwargs):
            result = function(*args, **kwargs)
            frames = result[0] if selected else result
            record = provenance.get()
            if record is not None:
                for frame in frames:
                    entry = dict(uri=frame.uri, mjd=frame.mjd, detector=frame.detector,
                                 wavelength_um=frame.wavelength, version=frame.version)
                    if entry not in record:
                        record.append(entry)
            return result
        wrapped.cache_clear = getattr(function, "cache_clear", lambda: None)
        return wrapped

    if mode in ("auto", "live"):
        def get_real_frames(ra, dec, fov, detector=None):
            return current_data.get_detector_frames(
                ra, dec, fov, detector=detector, mode=mode, cache_root=cache_root
            )
        get_real_frames.cache_clear = getattr(loader.get_frames, "cache_clear", lambda: None)
        loader.get_frames = get_real_frames

    loader.get_frames = capture_frames(loader.get_frames)
    original._cached_selected_frames = capture_frames(original._cached_selected_frames, selected=True)
    original.app.state.compat_inputs = provenance
    return original.app


class FrontendCompatibility:
    """Dispatch the original frontend to its original scientific API implementation."""

    def __init__(self, app, cache_root: Path, mode: str):
        self.app, self.cache_root, self.mode = app, cache_root, mode
        self.practice = None

    async def core_image_view(self, scope, receive, send):
        """Reuse the HTML viewer's exact selection, alignment and cached PNG bytes."""
        messages = []
        async def capture(message):
            messages.append(message)
        query = parse_qs(scope.get('query_string', b'').decode())
        aliases = {'WISE0855': 'WISE 0855-0714', 'Barnard': "Barnard's Star",
                   'EpsilonIndiB': 'Epsilon Indi B', 'RWAurigae': 'RW Aurigae', 'RDraconis': 'R Draconis'}
        if query.get('target', [''])[0] in aliases:
            query['target'] = [aliases[query['target'][0]]]
        core_scope = {**scope, 'query_string': urlencode(query, doseq=True).encode()}
        await self.app(core_scope, receive, capture)
        start = next(m for m in messages if m['type'] == 'http.response.start')
        if scope['path'] == '/api/meta' and start['status'] == 200:
            body = b''.join(m.get('body', b'') for m in messages if m['type'] == 'http.response.body')
            meta = json.loads(body)
            frames = [{**f, 'wavelength': f['wavelength_um']} for f in meta['frames']]
            epochs = [{**e, 'start_mjd': e['mjd_start'], 'end_mjd': e['mjd_end'],
                       'mean_wavelength': sum(f['wavelength'] for f in frames if f['epoch'] == e['epoch']) / e['n_frames']}
                      for e in meta['epochs']]
            adapted = {**meta, 'ra': meta['target']['ra'], 'dec': meta['target']['dec'],
                       'fov': meta['fov_arcsec'], 'mock': meta['synthetic'], 'frames': frames,
                       'frame_shape': meta['image_shape'], 'epochs': epochs, 'n_epochs': len(epochs),
                       'span_days': frames[-1]['mjd'] - frames[0]['mjd'],
                       'recommended_epochs': [epochs[0]['epoch'], epochs[-1]['epoch']],
                       'label': f"Detector {meta['detector']} - {meta['n_frames']} observations",
                       'detector_info': 'Same wavelength selection and aligned frames as the legacy dashboard.',
                       'sky_grid': {'has_wcs': not meta['synthetic'], 'north_up': True,
                                    'width_arcsec': meta['image_shape'][1] * meta['pixel_scale_arcsec'],
                                    'height_arcsec': meta['image_shape'][0] * meta['pixel_scale_arcsec']}}
            await JSONResponse(adapted)(scope, receive, send)
        elif start['status'] >= 400:
            body = b''.join(m.get('body', b'') for m in messages if m['type'] == 'http.response.body')
            try:
                error = json.loads(body)
                if 'error' in error:
                    error['detail'] = error['error']['message']
                    await JSONResponse(error, status_code=start['status'])(scope, receive, send)
                    return
            except (ValueError, KeyError, TypeError):
                pass
            for message in messages:
                await send(message)
        else:
            for message in messages:
                await send(message)

    async def __call__(self, scope, receive, send):
        if scope['type'] != 'http' or not is_practice_request(scope):
            await self.app(scope, receive, send)
            return
        query = parse_qs(scope.get('query_string', b'').decode())
        view = query.get('mode', ['blink'])[0]
        # Stack always uses the core coadd; Difference remains on the original path.
        default_blink = (view == 'blink'
                         and query.get('clean', ['true'])[0].lower() == 'true'
                         and query.get('show_mask', ['false'])[0].lower() == 'false')
        if scope['path'] in {'/api/meta', '/api/image.png'} and (view == 'stack' or default_blink):
            await self.core_image_view(scope, receive, send)
            return
        if self.practice is None:
            self.practice = create_practice_app(self.cache_root, self.mode)
        messages = []
        inputs = []
        token = self.practice.state.compat_inputs.set(inputs)

        async def capture(message):
            messages.append(message)

        try:
            await self.practice(scope, receive, capture)
        except Exception as exc:
            import urllib.error
            import requests
            import data as current_data
            import gaia as current_gaia

            if isinstance(
                exc,
                (
                    current_data.ArchiveUnreachableError,
                    current_gaia.GaiaUnreachableError,
                    requests.exceptions.RequestException,
                    urllib.error.URLError,
                    TimeoutError,
                ),
            ) or getattr(exc, "code", None) == "archive_unreachable":
                status = 502
            elif isinstance(
                exc,
                (current_data.NoCoverageError, current_data.NotCachedError, FileNotFoundError),
            ) or getattr(exc, "code", None) in ("no_coverage", "not_cached"):
                status = 404
            elif isinstance(getattr(exc, "status_code", None), int):
                status = exc.status_code
            else:
                status = 422
            response = JSONResponse({"detail": str(exc) or "Unable to process the practice view."}, status_code=status)
            messages.clear()
            await response(scope, receive, capture)
        finally:
            self.practice.state.compat_inputs.reset(token)
        start = next(message for message in messages if message['type'] == 'http.response.start')
        headers = dict(start['headers'])
        body = b''.join(message.get('body', b'') for message in messages if message['type'] == 'http.response.body')
        if headers.get(b'content-type', b'').startswith(b'application/json'):
            result = json.loads(body)
            result['synthetic'] = self.mode == 'synthetic'
            result['data_versions'] = sorted({str(frame['version']) for frame in inputs})
            body = json.dumps(result, allow_nan=False).encode()
            headers[b'content-length'] = str(len(body)).encode()
            if start['status'] == 200:
                query = scope.get('query_string', b'').decode()
                path = scope['path'] + ('?' + query if query else '')
                record = dict(result=result, inputs=inputs, data_mode=self.mode,
                              cache_root=str(self.cache_root), request=path,
                              data_versions=sorted({str(frame['version']) for frame in inputs}))
                key = hashlib.sha256(json.dumps(record, sort_keys=True).encode()).hexdigest()[:20]
                record['command'] = (f'curl.exe --fail -H "X-ShroudX-Frontend: spaceapps" '
                                     f'"http://127.0.0.1:8000{path}"')
                results = Path(__file__).parent.parent / 'results'
                results.mkdir(exist_ok=True)
                target = results / f'frontend-compat-{key}.json'
                temporary = target.with_suffix('.' + uuid.uuid4().hex + '.tmp')
                temporary.write_text(json.dumps(record, indent=2), encoding='utf-8')
                temporary.replace(target)
        headers[b'x-shroudx-frontend'] = b'spaceapps'
        headers[b'x-synthetic'] = str(self.mode == 'synthetic').lower().encode()
        await send({**start, 'headers': list(headers.items())})
        await send({'type': 'http.response.body', 'body': body})
