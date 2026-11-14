import type { Meta } from './api';

export function OrientationRail({ meta }: { meta: Meta }) {
  const verified = Boolean(meta.sky_grid?.has_wcs && meta.sky_grid.north_up);
  return <div className="orientation-rail" aria-label="Image orientation outside the observation"><svg viewBox="0 0 74 54" role="img" aria-label={verified ? 'North is up and east is left' : 'Image axes only; celestial orientation is unverified'}><path d="M39 43 V12 M15 33 H59 M35 17 L39 12 L43 17 M20 29 L15 33 L20 37" fill="none" stroke="currentColor" strokeWidth="1.4" /><text x="39" y="9" textAnchor="middle">{verified ? 'N' : 'UP'}</text><text x="9" y="36" textAnchor="middle">{verified ? 'E' : 'L'}</text></svg><div><strong>{verified ? 'North up · east left' : 'Image orientation'}</strong><span>{verified ? 'Aligned celestial grid' : meta.mock ? 'Illustrative axes · simulated image' : 'Celestial orientation not verified'}</span></div><span className="instrument-note">Instruments stay outside the field</span></div>;
}

export function ScaleRail({ meta }: { meta: Meta }) {
  const width = meta.sky_grid?.width_arcsec || meta.fov;
  const length = width <= 60 ? 10 : width <= 180 ? 30 : 60;
  const bar = Math.min(450, length / width * 512);
  return <div className="scale-rail" aria-label="Angular scale outside the observation"><svg viewBox="0 0 512 34" role="img" aria-label={`${length} arcseconds${meta.mock ? ', nominal simulated scale' : ''}`}><path d={`M10 8 V19 M10 14 H${10 + bar} M${10 + bar} 8 V19`} fill="none" stroke="currentColor" strokeWidth="1.3" /><text x={10 + bar / 2} y="30" textAnchor="middle">{length} arcsec{meta.mock ? ' · nominal' : ''}</text><text x="502" y="18" textAnchor="end">Field width {width.toFixed(1)}″</text></svg></div>;
}
