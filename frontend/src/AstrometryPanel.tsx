import type { Motion } from './api';
import { classifyAstrometry } from './astrometryStatus';

export default function AstrometryPanel({ motion, mock }: { motion: Motion; mock: boolean }) {
  const val = motion.validation;
  const hasReference = Boolean(val?.has_catalog);
  const status = classifyAstrometry(motion, mock);
  const residualDetail = !hasReference
    ? 'No catalogue comparison available'
    : val?.sigma_2d !== undefined && val?.catalog_position_angle_deg !== undefined
      ? `2D vector: ${val.sigma_2d.toFixed(2)}σ · Cat PA ${val.catalog_position_angle_deg.toFixed(1)}°`
      : `${val?.z_score_agreement?.toFixed(2)}σ 1D speed residual`;
  return <section className="astrometry-card" aria-label="Astrometry and catalogue comparison"><div className="science-heading"><h4>Proper motion & astrometry</h4><span className={'comparison-badge ' + status.tone}>{status.badge}</span></div>
    <div className="astrometry-metrics"><div><span>MEASURED SPEED</span><strong>{motion.pm_total.toFixed(2)} <small>± {motion.pm_total_err.toFixed(2)} ″/yr</small></strong></div><div><span>POSITION ANGLE</span><strong>{motion.position_angle_deg.toFixed(1)}° <small>east of north</small></strong></div><div><span>RA MOTION · μα COS δ</span><strong>{motion.pm_ra_cosdec.toFixed(3)} <small>″/yr</small></strong></div><div><span>DEC MOTION · μδ</span><strong>{motion.pm_dec.toFixed(3)} <small>″/yr</small></strong></div><div><span>CATALOGUE REFERENCE</span><strong>{hasReference ? val?.catalog_pm_total?.toFixed(2) : 'Unavailable'} <small>{hasReference ? '″/yr' : ''}</small></strong><p>{val?.target || 'No known target selected'}</p></div><div><span>RESIDUAL / AGREEMENT</span><strong>{hasReference && val?.delta_pm !== undefined ? `${val.delta_pm > 0 ? '+' : ''}${val.delta_pm.toFixed(2)}` : '—'} <small>{hasReference ? '″/yr' : ''}</small></strong><p>{residualDetail}</p></div><div><span>OBSERVATION BASELINE</span><strong>{motion.baseline_years.toFixed(2)} <small>years</small></strong><p>{motion.n_frames} measured frames</p></div></div>
    <p className="insight-note">{status.note}</p>
  </section>;
}
