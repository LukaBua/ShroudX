export type Observation = { index: number; wavelength: number; mjd: number; detector: number; epoch: number };
export type Epoch = { epoch: number; start_mjd: number; end_mjd: number; n_frames: number; mean_wavelength: number; start_date?: string; end_date?: string; wavelength_min?: number; wavelength_max?: number; gap_to_next_days?: number | null };
export type Meta = { ra: number; dec: number; fov: number; mock: boolean; label: string; detector: number; detector_info: string; n_frames: number; n_epochs: number; span_days: number; frames: Observation[]; warnings: string[]; epochs: Epoch[]; recommended_epochs: number[]; frame_shape: number[]; sky_grid?: Grid };
export type Motion = { pm_total: number; pm_total_err: number; pm_ra_cosdec: number; pm_dec: number; position_angle_deg: number; baseline_years: number; n_frames: number; parallax_arcsec?: number | null; parallax_err?: number | null; reduced_chi2?: number | null; n_epochs?: number; measurements: { x: number; y: number; mjd: number; snr?: number }[]; validation?: { has_catalog?: boolean; target?: string; catalog_pm_total?: number; catalog_pm_ra_cosdec?: number; catalog_pm_dec?: number; catalog_position_angle_deg?: number; delta_pm?: number; delta_position_angle_deg?: number; z_score_agreement?: number; sigma_2d?: number; agrees_within_1sigma?: boolean; agrees_within_3sigma?: boolean; agrees_vector_3sigma?: boolean; status?: 'AGREE' | 'DISAGREE' | 'UNINFORMATIVE' } };
export type SpectrumPoint = { wavelength: number; flux: number; flux_err: number; detector: number; snr?: number; mjd?: number; epoch?: number };
export type Spectrum = { points: SpectrumPoint[]; n_points: number; detectors?: number[]; wavelength_min?: number; wavelength_max?: number };
export type Pixel = { x: number; y: number; flux: number | null; sigma: number | null; variance?: number | null; zodi?: number | null; wavelength?: number; detector?: number; is_masked?: boolean; ra_deg: number; dec_deg: number; has_sky_coordinates?: boolean; quality: { category: string; active_flags: string[]; flag_value?: number; is_bad?: boolean } };
export type CatalogSource = { id: string; catalog: string; ra_deg: number; dec_deg: number; mag: number | null; band: string; pm_ra: number | null; pm_dec: number | null; pm_total: number | null; parallax_mas?: number | null; pm_missing?: boolean; type: string; is_primary: boolean; sep_arcsec?: number; x?: number | null; y?: number | null; position_kind?: string };
export type Catalog = { sources: CatalogSource[]; count: number; epoch?: number; provenance?: string; has_sky_projection?: boolean };
export type Detections = { threshold: number; count: number; epoch_a: number; epoch_b: number; lobes: { polarity: string; x: number; y: number; radius_px: number; peak_z?: number; n_pixels?: number }[]; pixels: { x: number; y: number; z: number }[] };
export type Grid = { has_wcs: boolean; north_up: boolean; width_arcsec: number; height_arcsec: number };
export const DETECTORS = [
  { id: 1, range: '0.75–1.09', color: '#75c9f4' }, { id: 2, range: '1.10–1.62', color: '#679cfa' },
  { id: 3, range: '1.63–2.41', color: '#67c5b5' }, { id: 4, range: '2.42–3.82', color: '#eab276' },
  { id: 5, range: '3.83–4.41', color: '#a8cb80' }, { id: 6, range: '4.42–5.00', color: '#e88c9d' },
];
export async function getJSON<T>(path: string, signal?: AbortSignal): Promise<T> {
  let response: Response;
  try { response = await fetch(path, { signal }); }
  catch (error) { if ((error as Error).name === 'AbortError') throw error; throw new Error('Cannot reach the explorer. Start FastAPI on port 8000 and try again.'); }
  if (!response.ok) {
    const body = await response.json().catch(() => ({}));
    throw new Error(typeof body.detail === 'string' ? body.detail : `Request failed (${response.status}). Check your filters and available data.`);
  }
  return response.json();
}
export function dateFromMJD(mjd: number) {
  return new Date((mjd - 40587) * 86400000).toLocaleDateString('en-GB', { day: '2-digit', month: 'short', year: 'numeric', timeZone: 'UTC' });
}
