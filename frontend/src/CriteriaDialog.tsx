import { useEffect, useRef } from 'react';
import { ExternalLink, X } from 'lucide-react';

const benchmarks = [
  ['WISE0855', 'WISE 0855', 'Cold brown dwarf', 'D5', '6.83 ± 3.08', '8.15', '1.02'],
  ['Barnard', 'Barnard’s Star', 'Fast-moving red dwarf', 'D2', '10.62 ± 0.52', '10.39', '1.09'],
  ['EpsilonIndiB', 'Epsilon Indi B', 'Brown dwarf binary', 'D5', '4.51 ± 0.18', '4.71', '1.44'],
  ['RWAurigae', 'RW Aurigae', 'Variable-star control', 'D3', '0.22 ± 0.25', '0.02', '1.01'],
  ['RDraconis', 'R Draconis', 'Variable-star control', 'D3', '0.95 ± 1.08', '0.01', '1.13'],
];
const considerations = [
  ['Archive updates', 'IRSA TAP search, cutout retrieval, and local caching. Live archive access is provided by the existing Python backend.'],
  ['Wavelengths & detectors', 'Six detector choices with their wavelength ranges; a spectrum of the samples actually available in the selection.'],
  ['Coaddition & differences', 'Inverse-variance stacking and epoch differences through /api/image.png; significant-pixel summaries through /api/detections.'],
  ['Field of view', 'Selectable 30–480 arcseconds, with cached cutout buckets and backend sub-cropping.'],
  ['Multi-mission references', 'Gaia / AllWISE reference overlays and source details from the stored benchmark table. This backend does not perform a live catalogue query.'],
  ['Shareable views', 'URL state for coordinates, target, filters, mode, periods, playback, and overlay options; direct links jump to the explorer.'],
  ['Pixel quality', 'Hover or click Blink observations for native brightness, uncertainty, coordinates, background, wavelength, and decoded quality flags.'],
  ['Astronomy tools', 'Astropy and Reproject processing, plus an external CDS Aladin Lite link for the current field.'],
];
export default function CriteriaDialog({ open, onClose, onTarget }: { open: boolean; onClose: () => void; onTarget: (target: string) => void }) {
  const ref = useRef<HTMLDialogElement>(null);
  useEffect(() => { if (open && !ref.current?.open) ref.current?.showModal(); else if (!open && ref.current?.open) ref.current.close(); }, [open]);
  return <dialog ref={ref} className="criteria-dialog" aria-labelledby="criteria-title" onCancel={onClose} onClick={e => { if (e.target === e.currentTarget) onClose(); }}><div className="criteria-content"><header><div><p className="eyebrow">PROJECT CHECKLIST & BENCHMARKS</p><h2 id="criteria-title">Planet X and SPHEREx</h2><p>NASA Space Apps 2026 · project implementation notes</p></div><button className="icon-button" onClick={onClose} aria-label="Close project criteria"><X size={20} /></button></header><p className="criteria-note">This is the project’s own feature checklist, not a NASA certification or a live validation report. Benchmark numbers below reproduce the repository’s reported results; the current data, frame counts, and measurements can differ. Open a target to inspect its current outputs.</p><h3>Core capabilities</h3><div className="criteria-core"><p><b>Sky-coordinate input</b> · Names, decimal degrees, and sexagesimal coordinates.</p><p><b>Time-series viewer</b> · Blink, pause, stepping, 50–2000 ms/frame, and date-labelled observation groups.</p></div><h3>Reported benchmark results</h3><div className="science-table-scroll"><table className="science-table"><thead><tr><th>Target</th><th>Measured (″/yr)</th><th>Reference (″/yr)</th><th>Baseline</th><th>Inspect</th></tr></thead><tbody>{benchmarks.map(([key, label, type, detector, measured, reference, baseline]) => <tr key={key}><td><strong>{label}</strong><small>{type} · {detector}</small></td><td>{measured}</td><td>{reference}</td><td>{baseline} yr</td><td><button className="small-button" onClick={() => { onClose(); onTarget(key); }}>Load</button></td></tr>)}</tbody></table></div><h3>Eight implementation considerations</h3><div className="criteria-grid">{considerations.map(([title, text], i) => <article key={title}><span className="mono">0{i + 1}</span><h4>{title}</h4><p>{text}</p></article>)}</div><footer><a href="https://www.spaceappschallenge.org/2026/challenges/planet-x-and-spherex/" target="_blank" rel="noreferrer">Official challenge <ExternalLink size={13} /></a><button className="small-button" onClick={onClose}>Close checklist</button></footer></div></dialog>;
}
