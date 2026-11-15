// AI-assisted: implemented with Codex and AI pair-programming assistant.

import type { Motion } from './api';

export interface AstrometryClassification {
  tone: 'consistent' | 'disagree' | 'reference-only';
  badge: string;
  note: string;
}

const STATIONARY_PM_LIMIT_ARCSEC_YR = 0.05;
const MAX_STATIONARY_ERR_ARCSEC_YR = 0.25;
const MAX_RELATIVE_ERR_FRACTION = 0.3;

export function classifyAstrometry(motion: Motion, mock: boolean): AstrometryClassification {
  if (mock) {
    return {
      tone: 'reference-only',
      badge: 'SIMULATION / REFERENCE ONLY',
      note: 'Synthetic sequence — illustrative motion only, not an archive measurement.',
    };
  }

  const val = motion.validation;
  if (!val?.has_catalog) {
    return {
      tone: 'reference-only',
      badge: 'MEASURED / NO REFERENCE',
      note: 'No Gaia/literature reference proper motion available for vector comparison.',
    };
  }

  const pmTotal = motion.pm_total;
  const pmErr = motion.pm_total_err;
  const catTotal = val.catalog_pm_total ?? 0;

  if (val.status === 'UNINFORMATIVE') {
    return {
      tone: 'reference-only',
      badge: 'INCONCLUSIVE · LOW PRECISION',
      note: 'Measurement uncertainty is too large relative to reference motion to confirm agreement.',
    };
  }
  if (val.status === 'AGREE') {
    return {
      tone: 'consistent',
      badge: 'WITHIN 3σ OF REFERENCE',
      note: 'Measured proper motion speed and sky direction agree with reference within 3σ.',
    };
  }
  if (val.status === 'DISAGREE') {
    return {
      tone: 'disagree',
      badge: 'OUTSIDE VECTOR 3σ',
      note: 'Measured proper motion vector differs >3σ from the reference catalogue.',
    };
  }

  if (catTotal < STATIONARY_PM_LIMIT_ARCSEC_YR) {
    if (!Number.isFinite(pmErr) || pmErr <= 0 || pmErr >= MAX_STATIONARY_ERR_ARCSEC_YR) {
      return {
        tone: 'reference-only',
        badge: 'INCONCLUSIVE · HIGH UNCERTAINTY',
        note: 'Uncertainty (≥ 0.25 ″/yr) is too large to validate a nearly stationary star (often caused by saturated-core masking or blending).',
      };
    }
    const zZero = pmTotal / pmErr;
    if (zZero >= 3.0 || (pmTotal >= 0.15 && val.agrees_vector_3sigma === false)) {
      return {
        tone: 'disagree',
        badge: 'OUTSIDE 3σ OF REFERENCE',
        note: 'Measured centroid shift exceeds the stationary reference beyond uncertainty (check saturated core or blending).',
      };
    }
    return {
      tone: 'consistent',
      badge: 'WITHIN 3σ OF REFERENCE',
      note: 'Measured drift is consistent with a stationary reference within 3σ.',
    };
  }

  if (!Number.isFinite(pmErr) || pmErr <= 0 || pmErr >= MAX_RELATIVE_ERR_FRACTION * catTotal) {
    return {
      tone: 'reference-only',
      badge: 'INCONCLUSIVE · LOW PRECISION',
      note: 'Uncertainty exceeds 30% of reference speed; this fit is uninformative rather than confirmation.',
    };
  }

  const vecOk =
    val.agrees_vector_3sigma !== undefined
      ? val.agrees_vector_3sigma
      : typeof val.sigma_2d === 'number'
        ? val.sigma_2d <= 3.0
        : Boolean(val.agrees_within_3sigma);

  if (vecOk) {
    return {
      tone: 'consistent',
      badge: val.agrees_vector_3sigma !== undefined ? 'WITHIN VECTOR 3σ' : 'WITHIN 3σ OF REFERENCE',
      note: 'Measured proper motion speed and sky direction agree with reference within 3σ.',
    };
  }

  if (val.agrees_within_3sigma) {
    return {
      tone: 'disagree',
      badge: 'DIRECTION DIFFERS (>3σ VECTOR)',
      note: 'Scalar speed is within 3σ, but 2D vector / position angle differs >3σ from reference (e.g., unmodeled annual parallax).',
    };
  }

  return {
    tone: 'disagree',
    badge: 'OUTSIDE VECTOR 3σ',
    note: 'Measured proper motion vector differs >3σ from the reference catalogue.',
  };
}
