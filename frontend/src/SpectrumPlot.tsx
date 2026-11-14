import { useState } from 'react';
import { dateFromMJD, DETECTORS, type Spectrum, type SpectrumPoint } from './api';

export default function SpectrumPlot({ data, wavelength }: { data: Spectrum; wavelength?: number }) {
  const [hovered, setHovered] = useState<SpectrumPoint | null>(null);
  const points = data.points.filter(p => [p.flux, p.flux_err, p.wavelength].every(Number.isFinite)).sort((a, b) => a.wavelength - b.wavelength);
  if (!points.length) return <p className="insight-note">No usable spectral points for this selection.</p>;
  const minX = Math.min(...points.map(p => p.wavelength)), maxX = Math.max(...points.map(p => p.wavelength));
  const minY = Math.min(0, ...points.map(p => p.flux - p.flux_err)), maxY = Math.max(.1, ...points.map(p => p.flux + p.flux_err));
  const padX = Math.max((maxX - minX) * .05, .005), padY = (maxY - minY) * .1;
  const x = (v: number) => 65 + (v - minX + padX) / (maxX - minX + 2 * padX) * 490;
  const y = (v: number) => 175 - (v - minY + padY) / (maxY - minY + 2 * padY) * 140;
  const color = (det: number) => DETECTORS.find(d => d.id === det)?.color || '#d6b58d';
  const snr = (p: SpectrumPoint) => p.snr ?? (p.flux_err > 0 ? p.flux / p.flux_err : 0);
  const cursorVisible = wavelength !== undefined && wavelength >= minX - padX && wavelength <= maxX + padX;
  return <section className="spectrum-card" aria-label="Spectral energy distribution">
    <div className="science-heading"><h4>On-the-fly spectrum <span>SED</span></h4><span>{points.length} samples</span></div>
    <p className="insight-note spectrum-summary">{minX.toFixed(3)}–{maxX.toFixed(3)} µm · peak SNR {Math.max(...points.map(snr)).toFixed(1)} · background-subtracted aperture sums</p>
    <div className="spectrum-chart"><svg className="spectrum-plot" viewBox="0 0 600 225" role="img" aria-label={`${points.length} spectral samples with uncertainty bars, detector colours, and current-frame wavelength`}>
      {[0, .5, 1].map(t => { const v = minY + (maxY - minY) * t; return <g key={t}><line x1="65" x2="555" y1={y(v)} y2={y(v)} stroke="#ffffff13" /><text x="57" y={y(v) + 3} textAnchor="end">{v.toPrecision(3)}</text></g>; })}
      <line x1="65" x2="555" y1={y(0)} y2={y(0)} stroke="#ffffff28" strokeDasharray="3 4" />
      <path d={points.map((p, i) => `${i ? 'L' : 'M'}${x(p.wavelength)},${y(p.flux)}`).join(' ')} fill="none" stroke="#a4b8d450" strokeWidth="1.2" strokeDasharray="3 3" />
      {cursorVisible && <g className="spectrum-cursor"><line x1={x(wavelength!)} x2={x(wavelength!)} y1="24" y2="180" stroke="#eee1a7" strokeDasharray="3 3" /><text x={Math.min(520, Math.max(110, x(wavelength!)))} y="18" textAnchor="middle">Frame λ {wavelength!.toFixed(3)} µm</text></g>}
      {points.map((p, i) => <g key={i} className="spectral-point"><line x1={x(p.wavelength)} x2={x(p.wavelength)} y1={y(p.flux - p.flux_err)} y2={y(p.flux + p.flux_err)} stroke={color(p.detector)} opacity=".7" />{[-1, 1].map(sign => <line key={sign} x1={x(p.wavelength) - 3} x2={x(p.wavelength) + 3} y1={y(p.flux + sign * p.flux_err)} y2={y(p.flux + sign * p.flux_err)} stroke={color(p.detector)} />)}<circle cx={x(p.wavelength)} cy={y(p.flux)} r={hovered === p ? 5 : 3.5} fill={color(p.detector)} stroke="#0b1017" /><circle className="spectral-hit" cx={x(p.wavelength)} cy={y(p.flux)} r="9" fill="transparent" tabIndex={0} aria-label={`${p.wavelength} micrometres, flux ${p.flux}, detector ${p.detector}`} onMouseEnter={() => setHovered(p)} onMouseLeave={() => setHovered(null)} onFocus={() => setHovered(p)} onBlur={() => setHovered(null)} onClick={() => setHovered(p)}><title>{p.wavelength} µm · {p.flux} ± {p.flux_err} · SNR {snr(p).toFixed(1)}</title></circle></g>)}
      <line x1="65" x2="555" y1="180" y2="180" stroke="#526070" />
      {[0, 1, 2, 3].map(i => { const v = minX + (maxX - minX) * i / 3; return <text key={i} x={x(v)} y="197" textAnchor="middle">{v.toFixed(3)}</text>; })}
      <text x="310" y="218" textAnchor="middle">Wavelength λ (µm)</text><text transform="translate(14 110) rotate(-90)" textAnchor="middle">Aperture sum (native units)</text>
    </svg></div>
    <div className="spectrum-tooltip" role="status">{hovered ? <><b>λ {hovered.wavelength.toFixed(3)} µm</b><span>{hovered.flux.toFixed(3)} ± {hovered.flux_err.toFixed(3)}</span><span style={{ color: color(hovered.detector) }}>Detector {hovered.detector}</span><span>SNR {snr(hovered).toFixed(1)}</span>{hovered.mjd !== undefined && <span>MJD {hovered.mjd.toFixed(2)} · {dateFromMJD(hovered.mjd)}</span>}</> : <span>Hover, focus, or tap a sample for brightness, uncertainty, detector, SNR, and date.</span>}</div>
    <div className="spectrum-legend">{DETECTORS.filter(d => points.some(p => p.detector === d.id)).map(d => <span key={d.id} style={{ color: d.color }}><i style={{ background: d.color }} /> D{d.id} · {d.range} µm</span>)}</div>
    <p className="insight-note">Samples may come from different dates. Variability and masked pixels affect interpretation; available observations do not necessarily cover all 102 channels. The connecting line is a visual guide.</p>
  </section>;
}
