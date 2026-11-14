# SPHEREx React frontend

React + TypeScript + Vite. A canvas galaxy introduction, scroll-linked transition,
API-connected observation workspace, and an editable team section. The galaxy is
an illustration, not observational data. No external images are required.

## Run the built app (one terminal)

From the repository root, after the frontend has been built:

```powershell
$env:SPHX_MODE="mock"
.\.venv\Scripts\python.exe -m uvicorn app:app --reload
```

Open http://127.0.0.1:8000. Use `cache` instead of `mock` to use prepared real
cutouts. The original dashboard remains at http://127.0.0.1:8000/legacy.

## Develop with hot reload (two terminals)

Start FastAPI as above. In another terminal:

```powershell
cd frontend
npm install
npm run dev
```

Open http://127.0.0.1:5173. Vite proxies `/api` and `/legacy` to FastAPI on port
8000, so CORS configuration is not needed. Node.js must be installed for npm.

If using the Codex-bundled runtime without npm on PATH, run from the repo root:

```powershell
powershell -ExecutionPolicy Bypass -File .\frontend\run.ps1
```

The helper uses npm, pnpm, or the bundled pnpm runtime if available. Pass `-Build`
to build instead. Restart FastAPI after the first build so it mounts `/assets`.

## Build

```powershell
cd frontend
npm run build
```

This type-checks TypeScript and creates `frontend/dist`. FastAPI serves the build
at `/` when it exists, or falls back to the original dashboard if it does not.
Dependencies and build output are ignored by Git. The pnpm lockfile is committed
for reproducible pnpm installations.

## Customize

- Team profiles: `src/team.ts`. Replace placeholder names, bios, and initials.
- Portraits: place approved photos in `public/team/` and set `photo: '/team/name.jpg'`.
- Appearance and responsive layout: `src/styles.css`.
- Galaxy illustration: `src/Galaxy.tsx` (drawn only on resize).
- API client and response types: `src/api.ts`.
- Observation controls and viewer: `src/Explorer.tsx`.

The explorer supports name/coordinate input, five presets, exact and slider FOV,
detector wavelength ranges, wavelength filters, blink/stack/difference, epoch
selection, 50–2000 ms playback, frame stepping, display settings, debounced hover
and click pixel inspection, PNG download, and shareable URLs. Requests and
preloading are cancellable. Motion, spectrum, and catalogue requests run
independently after image loading, with individual error/retry states. No new
job API is added.

The overview now includes the original dashboard's astrometry/catalogue
comparison, detector-coloured interactive spectrum, wavelength playback cursor,
observation timeline, and reference-source table. Compass/scale, measured motion
and centroid tracks, catalogue references, and difference-detection lobes have
independent toggles persisted in the URL. An Aladin link uses resolved coordinates.

The compass and scale occupy rails outside the image. Motion uses the fitted
velocity, with a separate enlarged position plot and a fixed-length direction
indicator. Small tracks are not artificially expanded over the photograph;
the diagnostics state their enlargement and actual pixel displacement.
The project-criteria dialog includes benchmark target shortcuts and eight project
considerations. See `FEATURE_PARITY.md` for the original-to-React feature mapping.

Direct links containing target or coordinate parameters jump to the workspace.
The focused workspace can be closed with Escape. Reduced-motion preferences are
respected. Google Fonts are optional; system fonts work offline. Team portraits
are visibly marked placeholders until real member details are supplied.

The new UI displays scientific outputs from the existing backend; it does not
independently validate measurements or catalogue matches. Catalogue sources are
stored benchmark references, not a live query. Their markers are projected through
the rendered frame's WCS and are not date-propagated or presented as confirmed
matches. Synthetic images have no celestial WCS, so they show a labelled nominal
compass/scale and no celestial catalogue markers. The criteria dialog identifies
the repository's historical benchmark numbers as reported results, not live
measurements or an independent NASA compliance certification.
