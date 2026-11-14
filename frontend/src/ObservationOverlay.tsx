import { useId } from 'react';
import type { Catalog, Detections, Grid, Meta, Motion, Observation } from './api';
import { activeMeasurement, motionGeometry } from './motionGeometry';

export type OverlayOptions = { show_compass: boolean; show_vector: boolean; show_detections: boolean; show_catalog: boolean };
export default function ObservationOverlay({ meta, grid, motion, detections, catalog, options, frame, mode }: { meta: Meta; grid?: Grid; motion: Motion | null; detections: Detections | null; catalog: Catalog | null; options: OverlayOptions; frame?: Observation; mode: string }) {
  const uid = useId().replaceAll(':', '');
  const [ny, nx] = meta.frame_shape;
  const px = (x: number) => (x + .5) / nx * 512;
  const py = (y: number) => (ny - .5 - y) / ny * 512;
  const valid = (p: { x: number; y: number }) => Number.isFinite(p.x) && Number.isFinite(p.y) && p.x >= 0 && p.x < nx && p.y >= 0 && p.y < ny;
  const geometry = motion ? motionGeometry(motion, meta.frame_shape, grid) : null;
  const points = geometry?.points || [];
  const active = mode === 'blink' ? activeMeasurement(points, frame) : undefined;
  const trackExtent = points.length ? Math.max(
    (Math.max(...points.map(p => p.x)) - Math.min(...points.map(p => p.x))) / nx,
    (Math.max(...points.map(p => p.y)) - Math.min(...points.map(p => p.y))) / ny,
  ) * 512 : 0;
  return <svg className="observation-overlay" viewBox="0 0 512 512" aria-label="True-scale motion, catalogue, and detection overlays" role="img">
    <defs><marker id={`${uid}-motion`} viewBox="0 0 10 10" refX="8" refY="5" markerWidth="6" markerHeight="6" orient="auto"><path d="M0 1 L9 5 L0 9" fill="none" stroke="#a8cb80" strokeWidth="1.5" /></marker><marker id={`${uid}-reference`} viewBox="0 0 10 10" refX="8" refY="5" markerWidth="5" markerHeight="5" orient="auto"><path d="M0 1 L9 5 L0 9" fill="none" stroke="#75c9f4" strokeWidth="1.5" /></marker></defs>
    {options.show_vector && geometry && <g className="motion-overlay"><title>True-scale measured positions. Enlarged trajectory and direction are shown below the image.</title>
      {mode === 'blink' && trackExtent >= 48 && <><polyline className="image-centroid-track track-halo" points={points.map(p => `${px(p.x)},${py(p.y)}`).join(' ')} fill="none" stroke="#060a10" strokeWidth="3" /><polyline className="image-centroid-track" points={points.map(p => `${px(p.x)},${py(p.y)}`).join(' ')} fill="none" stroke="#75c9f4" strokeWidth="1.3" strokeDasharray="3 4" /></>}
      {geometry.hasDirection && <><line className="image-fit-halo" x1={px(geometry.start.x)} y1={py(geometry.start.y)} x2={px(geometry.end.x)} y2={py(geometry.end.y)} stroke="#080b10" strokeWidth="4.5" /><line className="image-fit-vector" x1={px(geometry.start.x)} y1={py(geometry.start.y)} x2={px(geometry.end.x)} y2={py(geometry.end.y)} stroke="#b9d59a" strokeWidth="2" markerEnd={geometry.displacementPx * 512 / Math.max(nx, ny) >= 18 ? `url(#${uid}-motion)` : undefined} /></>}
      {active && mode === 'blink' && <g className="active-centroid-reticle" transform={`translate(${px(active.x)},${py(active.y)})`} data-native-x={active.x} data-native-y={active.y}><circle r="12" fill="none" stroke="#080b10" strokeWidth="4" /><circle r="12" fill="none" stroke="#eee1a7" strokeWidth="1.5" strokeDasharray="5 3" /><path d="M-19 0 H-14 M14 0 H19 M0 -19 V-14 M0 14 V19" stroke="#eee1a7" strokeWidth="1.5" /></g>}
    </g>}
    {options.show_detections && mode === 'diff' && detections && <g className="detections-overlay">{detections.lobes.filter(valid).map((p, i) => <g key={i}><circle cx={px(p.x)} cy={py(p.y)} r={Math.max(14, Math.min(28, p.radius_px * 512 / nx))} fill="none" stroke={p.polarity === 'positive' ? '#72d5a1' : '#ef8c92'} strokeWidth="1.7" strokeDasharray="5 3" /><title>{p.polarity} residual · peak {p.peak_z?.toFixed(2)} · {p.n_pixels} pixels</title></g>)}<text x="495" y="455" textAnchor="end">|z| &gt; {detections.threshold.toFixed(1)} · {detections.count} pixels</text></g>}
    {options.show_catalog && catalog?.has_sky_projection && <g className="catalog-overlay">{catalog.sources.filter(s => s.position_kind !== 'illustrative' && s.x != null && s.y != null && valid({ x: s.x, y: s.y })).map((s, i) => <g key={`${s.id}-${i}`} transform={`translate(${px(s.x!)},${py(s.y!)})`}><circle r={s.is_primary ? 14 : 9} fill="none" stroke={s.catalog.includes('WISE') ? '#eab276' : '#75c9f4'} strokeDasharray="3 3" /><text x="17" y="3" fill={s.catalog.includes('WISE') ? '#eab276' : '#75c9f4'}>{s.catalog} · {s.mag.toFixed(2)}m</text><title>{s.id} · reference position, not a confirmed source association</title></g>)}</g>}
  </svg>;
}
