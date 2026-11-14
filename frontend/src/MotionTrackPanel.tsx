import { useId } from 'react';
import type { Meta, Motion, Observation } from './api';
import { activeMeasurement, motionGeometry } from './motionGeometry';

export default function MotionTrackPanel({ motion, meta, frame, mode }: { motion: Motion; meta: Meta; frame?: Observation; mode: string }) {
  const uid = useId().replaceAll(':', '');
  const geometry = motionGeometry(motion, meta.frame_shape, meta.sky_grid);
  if (!geometry) return <p className="insight-note">No in-field centroid measurements are available to draw a track.</p>;
  const { points, start, end, hasDirection, spanDays, displacementPx } = geometry;
  const active = mode === 'blink' ? activeMeasurement(points, frame) : undefined;
  const values = [...points, start, end];
  const minX = Math.min(...values.map(p => p.x)), maxX = Math.max(...values.map(p => p.x));
  const minY = Math.min(...values.map(p => p.y)), maxY = Math.max(...values.map(p => p.y));
  const span = Math.max(maxX - minX, maxY - minY, .01) * 1.45;
  const centerX = (minX + maxX) / 2, centerY = (minY + maxY) / 2;
  const x = (value: number) => 140 + (value - centerX) / span * 204;
  const y = (value: number) => 140 - (value - centerY) / span * 204;
  const magnification = Math.max(...meta.frame_shape) / span;
  const directionLength = Math.hypot(end.x - start.x, end.y - start.y);
  const dx = hasDirection ? (end.x - start.x) / directionLength * 33 : 0;
  const dy = hasDirection ? -(end.y - start.y) / directionLength * 33 : 0;
  const uncertain = motion.pm_total <= 1.5 * motion.pm_total_err;
  const reference = motion.validation;
  return <section className="motion-track-panel" aria-label="Enlarged measured trajectory"><div className="science-heading"><h4>Motion, with room to see it</h4><span className="track-zoom-label">{magnification >= 1 ? `${magnification.toFixed(1)}× enlarged position plot` : `${(1 / magnification).toFixed(1)}× wider position plot`}</span></div>
    <div className="motion-track-layout"><div className="trajectory-chart"><svg viewBox="0 0 280 280" className="trajectory-svg" role="img" aria-label="Auto-scaled centroid positions and fitted trajectory in native pixels"><defs><marker id={`${uid}-track-arrow`} viewBox="0 0 10 10" refX="8" refY="5" markerWidth="6" markerHeight="6" orient="auto"><path d="M1 1 L9 5 L1 9" fill="none" stroke="#b9d59a" strokeWidth="1.8" /></marker></defs>
      {[0, 1, 2, 3, 4].map(i => { const pos = 38 + i * 51; return <g key={i}><line x1="38" x2="242" y1={pos} y2={pos} stroke="#ffffff0e" /><line y1="38" y2="242" x1={pos} x2={pos} stroke="#ffffff0e" /></g>; })}
      <polyline className="trajectory-measurements" points={points.map(p => `${x(p.x)},${y(p.y)}`).join(' ')} fill="none" stroke="#7ebbe2" strokeOpacity=".6" strokeWidth="1.4" strokeDasharray="3 4" />
      {points.map((p, i) => <circle key={i} className="trajectory-point" cx={x(p.x)} cy={y(p.y)} r="3.5" fill="#80b7d9" stroke="#0b1119"><title>Frame measurement · MJD {p.mjd.toFixed(6)} · x {p.x.toFixed(3)}, y {p.y.toFixed(3)} px</title></circle>)}
      {hasDirection && <line className="trajectory-fit" x1={x(start.x)} y1={y(start.y)} x2={x(end.x)} y2={y(end.y)} stroke="#b9d59a" strokeWidth="2.2" markerEnd={`url(#${uid}-track-arrow)`} />}
      <rect x={x(points[0].x) - 5} y={y(points[0].y) - 5} width="10" height="10" fill="none" stroke="#eeeae2" strokeWidth="1.5" />
      <circle cx={x(points[points.length - 1].x)} cy={y(points[points.length - 1].y)} r="6" fill="none" stroke="#d6b58d" strokeWidth="1.8" />
      {active && <g className="trajectory-current"><circle cx={x(active.x)} cy={y(active.y)} r="10" fill="#eee1a715" stroke="#eee1a7" strokeWidth="1.5" /><circle cx={x(active.x)} cy={y(active.y)} r="3" fill="#eee1a7" /></g>}
      <text x="140" y="18" textAnchor="middle">{geometry.celestial ? 'NORTH ↑ · EAST ←' : 'IMAGE UP ↑ · IMAGE LEFT ←'}</text>
      <text x="38" y="258">{(centerX - span / 2).toFixed(span < .1 ? 3 : 2)}</text><text x="242" y="258" textAnchor="end">{(centerX + span / 2).toFixed(span < .1 ? 3 : 2)}</text><text x="140" y="275" textAnchor="middle">Native image x (pixels)</text><text transform="translate(13 140) rotate(-90)" textAnchor="middle">Native image y (pixels)</text>
      <text x="32" y="43" textAnchor="end">{(centerY + span / 2).toFixed(span < 1 ? 3 : 1)}</text><text x="32" y="243" textAnchor="end">{(centerY - span / 2).toFixed(span < 1 ? 3 : 1)}</text>
    </svg></div><div className="motion-direction-details"><div className="direction-summary"><svg className="direction-dial" viewBox="0 0 100 100" role="img" aria-label={hasDirection ? 'Fitted direction indicator; arrow length is illustrative' : 'No resolved fitted direction'}><circle cx="50" cy="50" r="43" fill="#ffffff03" stroke="#ffffff17" /><path d="M50 10 V20 M50 80 V90 M10 50 H20 M80 50 H90" stroke="#ffffff35" />{hasDirection ? <><line x1={50 - dx * .5} y1={50 - dy * .5} x2={50 + dx} y2={50 + dy} stroke="#b9d59a" strokeWidth="3" /><path d={`M${50 + dx - dx * .22 + dy * .17} ${50 + dy - dy * .22 - dx * .17} L${50 + dx} ${50 + dy} L${50 + dx - dx * .22 - dy * .17} ${50 + dy - dy * .22 + dx * .17}`} fill="none" stroke="#b9d59a" strokeWidth="3" /></> : <circle cx="50" cy="50" r="4" fill="#9aa6b3" />}</svg><div><span className="eyebrow">FITTED DIRECTION</span><strong>{hasDirection ? geometry.celestial ? `${motion.position_angle_deg.toFixed(1)}°` : 'Image-space fit' : 'Not resolved'}</strong><p>{geometry.celestial ? 'Position angle east of north' : 'No verified celestial orientation'}</p><small>Direction indicator only · fixed arrow length</small></div></div>
      <div className="track-stats"><div><span>Fitted displacement</span><strong>{displacementPx.toFixed(3)} <small>native pixels</small></strong></div><div><span>Measured time span</span><strong>{spanDays < 30 ? spanDays.toFixed(2) : spanDays.toFixed(0)} <small>days</small></strong></div><div><span>Reported speed</span><strong>{motion.pm_total.toFixed(2)} ± {motion.pm_total_err.toFixed(2)} <small>″/yr</small></strong></div></div>
      <div className="track-legend"><span><i className="legend-measured" /> Measurements</span><span><i className="legend-fit" /> Fitted motion</span><span>□ First</span><span className="legend-last">○ Last</span><span className="legend-current">◎ Current frame</span></div>
      <p className="insight-note">{mode === 'blink' && !active ? 'No matching centroid was returned for this frame. ' : ''}{uncertain ? 'The reported motion is small relative to its uncertainty; position scatter is not proof of movement. ' : ''}The image retains true-scale positions. This separate plot is enlarged equally on both axes; it does not magnify the star’s actual movement.</p>
      {reference?.has_catalog && <p className="insight-note">Catalogue reference: {reference.catalog_pm_total?.toFixed(2)} ″/yr{reference.catalog_position_angle_deg !== undefined ? ` · PA ${reference.catalog_position_angle_deg.toFixed(1)}°` : ''}. {meta.mock ? 'Reference values do not describe the simulated source.' : 'No catalogue arrow is superimposed on the source.'}</p>}
    </div></div>
  </section>;
}
