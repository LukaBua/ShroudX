# Original dashboard → React feature mapping

Compared against `static/index.html`. The galaxy, page layout, team section,
focused workspace, PNG download, and responsive/reduced-motion behavior remain.

| Original element / function | React location |
| --- | --- |
| Target chips and name / RA / Dec fields | `Explorer.tsx`: five presets and name/coordinate tabs |
| Exact FOV input | Slider plus numeric arcsecond input |
| Six-array selector with wavelength ranges | Observation settings; shared `DETECTORS` in `api.ts` |
| Detector recommendation and wavelength limits | Field overview, detector selector, min/max wavelength |
| Epoch A / B dropdowns | Difference toolbar, with dates and frame counts |
| Play, pause, prev, next, 50–2000 ms speed | Playback toolbar and frame scrubber |
| Frame index, epoch, date/MJD, wavelength | Viewer label, playback counter, and data label |
| Clean, smooth, bad-pixel display | Observation settings |
| Compass rose and dynamic scale bar | `ObservationOverlay.tsx`, compass/scale toggle |
| Target reticle and measured motion vector | True-scale source locator and fitted motion; readable direction dial outside the image |
| Catalogue comparison vector | Reference motion/angle reported separately so competing arrows do not obscure the source |
| Per-frame centroid and epoch-average track | Auto-scaled, equal-axis position plot with every measured centroid, fitted trajectory, first/last markers, and precisely matched active frame |
| Positive/negative detection rings | Difference overlay and residual summary with threshold/counts |
| Gaia / AllWISE catalogue markers | WCS-projected reference overlay, independent toggle |
| Catalogue reference details | `CatalogPanel.tsx`: identifiers, coordinates, magnitudes, motion, object type |
| Pixel hover/click inspector | Debounced requests, quality category badge, active flags/bitmask, coordinates, brightness, sigma, variance, ZODI, detector, wavelength, mask state |
| Epoch timeline, gaps, wavelength bounds, frame counts | `EpochTimeline.tsx`; clickable groups reflect the viewer's actual frame assignments |
| Motion speed/error, position angle, reference, residual, baseline, agreement badge | `AstrometryPanel.tsx`, also adds RA/Dec motion components |
| SED line, samples, uncertainties, ticks, axis labels | `SpectrumPlot.tsx` |
| Detector colours and wavelength legend | Spectrum sample colours and six-array range legend |
| Spectral point tooltip: brightness/error, SNR, detector, MJD/date | Pointer/focus/touch sample inspection |
| Spectral wavelength cursor synchronized to blinking | Current-frame wavelength line and label |
| Spectrum coverage and peak SNR summary | Spectrum heading and summary |
| CDS Aladin shortcut | Toolbar link using API-resolved RA/Dec and FOV |
| Criteria modal and benchmark Load buttons | `CriteriaDialog.tsx`, native accessible dialog with Escape/backdrop close |
| Eight considerations | Criteria dialog, with descriptions matching the implemented routes |
| Shareable query parameters | All original parameters plus display/overlay/speed state |
| Original dashboard access | `/legacy` |

## Interpretation and compatibility

- Scientific arrays and calculations remain in the existing Python pipeline.
  The API adds rendered-grid geometry and WCS-projected catalogue pixel positions
  so the frontend does not guess marker positions from blank name-input fields.
- Catalogue data are static benchmark references. No new live Gaia/SIMBAD/WISE
  query, source identification, or per-date catalogue propagation is claimed.
- Synthetic-reference fallback entries are identified as placeholders. They are
  listed but not drawn as actual detections. Mock compass/scale are illustrative.
- Significant pixels and positive/negative summaries are not confirmed movers;
  the backend still uses a fixed threshold. The UI displays that threshold.
- The criteria dialog preserves historical figures as reported benchmark results.
  It does not copy the old dashboard's unverified “100% compliant” certification.
- Failure of an optional science endpoint does not remove the images or controls;
  each resource has an error message and retry action.
- Compass and angular scale are in dedicated rails above/below the observation,
  never superimposed on the star field. Small tracks are enlarged in a separate
  explicitly labelled plot. The image itself keeps true-scale positions and
  omits clustered sample markers. Fitted sky motion replaces the noisy endpoint
  vector; mock/unoriented images use a pixel-coordinate fit instead.
- Frame times retain six decimal places of MJD for reliable sample association.
  A missing centroid leaves no active marker rather than reusing a nearby frame.
- The original timeline could disagree with the split-half comparison fallback.
  React derives displayed epoch groups from the actual per-frame assignments.
