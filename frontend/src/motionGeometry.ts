import type { Grid, Motion, Observation } from './api';

export type Point = { x: number; y: number; mjd: number };

/** Fit in native pixel coordinates. Screen y is flipped only when drawing. */
export function motionGeometry(motion: Motion, shape: number[], grid?: Grid) {
  const [ny, nx] = shape;
  const points = motion.measurements.filter(p => [p.x, p.y, p.mjd].every(Number.isFinite) && p.x >= 0 && p.x < nx && p.y >= 0 && p.y < ny).slice().sort((a, b) => a.mjd - b.mjd);
  if (!points.length) return null;
  const first = points[0], last = points[points.length - 1];
  const meanTime = points.reduce((sum, p) => sum + p.mjd, 0) / points.length;
  const meanX = points.reduce((sum, p) => sum + p.x, 0) / points.length;
  const meanY = points.reduce((sum, p) => sum + p.y, 0) / points.length;
  const denominator = points.reduce((sum, p) => sum + (p.mjd - meanTime) ** 2, 0);
  let vx = denominator > 0 ? points.reduce((sum, p) => sum + (p.mjd - meanTime) * (p.x - meanX), 0) / denominator : 0;
  let vy = denominator > 0 ? points.reduce((sum, p) => sum + (p.mjd - meanTime) * (p.y - meanY), 0) / denominator : 0;
  const celestial = Boolean(grid?.has_wcs && grid.north_up);
  if (celestial && grid && grid.width_arcsec > 0 && grid.height_arcsec > 0) {
    // Proper motion in east/north; north-up WCS has east toward negative x.
    vx = -motion.pm_ra_cosdec / (grid.width_arcsec / nx) / 365.25;
    vy = motion.pm_dec / (grid.height_arcsec / ny) / 365.25;
  }
  const at = (mjd: number): Point => ({ mjd, x: meanX + vx * (mjd - meanTime), y: meanY + vy * (mjd - meanTime) });
  const start = at(first.mjd), end = at(last.mjd);
  const spanDays = last.mjd - first.mjd;
  const displacementPx = Math.hypot(end.x - start.x, end.y - start.y);
  return { points, first, last, start, end, vx, vy, at, spanDays, displacementPx, celestial, hasDirection: spanDays > 0 && displacementPx > 1e-6 };
}

export function activeMeasurement(points: Point[], frame?: Observation) {
  if (!frame || !points.length) return undefined;
  const nearest = points.reduce((best, p) => Math.abs(p.mjd - frame.mjd) < Math.abs(best.mjd - frame.mjd) ? p : best);
  // Metadata retains six decimals of MJD. Never reuse a nearby frame's detection.
  return Math.abs(nearest.mjd - frame.mjd) <= 0.00001 ? nearest : undefined;
}
