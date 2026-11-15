import { preloadImages } from './blinkImages';

import { useEffect, useRef, useState, type FormEvent } from 'react';

import { ArrowUpRight, Search, Play, Pause, SkipBack, SkipForward, Maximize2, Minimize2, Download, Film, Link2, SlidersHorizontal, Crosshair, LoaderCircle, AlertCircle, X, Info, ChevronDown, RotateCcw } from 'lucide-react';

import { dateFromMJD, getJSON, DETECTORS, type Meta, type Motion, type Spectrum, type Pixel, type Catalog, type Detections } from './api';

import useRemote from './useRemote';

import ObservationOverlay from './ObservationOverlay';

import MotionTrackPanel from './MotionTrackPanel';

import { OrientationRail, ScaleRail } from './ViewerInstruments';

import SpectrumPlot from './SpectrumPlot';

import AstrometryPanel from './AstrometryPanel';

import EpochTimeline from './EpochTimeline';

import CatalogPanel from './CatalogPanel';

import CriteriaDialog from './CriteriaDialog';

import ViewerLegend from './ViewerLegend';

import { triggerAnnotatedPngDownload, type ExportMode } from './downloadImage';



type Settings = { target: string; ra: string; dec: string; fov: string; detector: string; wmin: string; wmax: string; mode: string; epoch_a: string; epoch_b: string; clean: boolean; smooth: boolean; show_mask: boolean; speed: string; show_compass: boolean; show_vector: boolean; show_detections: boolean; show_catalog: boolean };

const defaults: Settings = { target: 'WISE0855', ra: '', dec: '', fov: '120', detector: '', wmin: '', wmax: '', mode: 'blink', epoch_a: '', epoch_b: '', clean: true, smooth: true, show_mask: false, speed: '400', show_compass: true, show_vector: true, show_detections: true, show_catalog: true };

const presets = [['WISE0855', 'WISE 0855'], ['Barnard', 'Barnard’s Star'], ['EpsilonIndiB', 'Epsilon Indi B'], ['RWAurigae', 'RW Aurigae'], ['RDraconis', 'R Draconis']];

const presetDisplayNames: Record<string, string> = {
  WISE0855: 'WISE 0855-0714',
  Barnard: "Barnard's Star",
  EpsilonIndiB: 'Epsilon Indi B',
  RWAurigae: 'RW Aurigae',
  RDraconis: 'R Draconis',
};

function readSettings(): Settings {

  const q = new URLSearchParams(location.search), settings = { ...defaults };

  for (const key of Object.keys(defaults) as (keyof Settings)[]) {

    if (!q.has(key)) continue;

    if (typeof defaults[key] === 'boolean') Object.assign(settings, { [key]: q.get(key) === 'true' });

    else Object.assign(settings, { [key]: q.get(key)! });

  }

  if ((q.has('ra') || q.has('dec')) && !q.has('target')) settings.target = '';

  if (q.has('fov') && settings.fov.trim() !== '') {

    const n = Number(settings.fov);

    if (Number.isFinite(n)) settings.fov = String(Math.min(480, Math.max(30, n)));

  }

  if (!['blink', 'stack', 'diff'].includes(settings.mode)) settings.mode = 'blink';

  return settings;

}

function parameters(s: Settings) {

  const q = new URLSearchParams();

  Object.entries(s).forEach(([key, value]) => {

    if (value !== '' && !(s.target && ['ra', 'dec'].includes(key))) q.set(key, String(value));

  });

  return q;

}



export default function Explorer({ expanded, onExpand }: { expanded: boolean; onExpand: () => void }) {

  const [form, setForm] = useState<Settings>(readSettings);

  const [inputMode, setInputMode] = useState(() => readSettings().target ? 'name' : 'coordinates');

  const [meta, setMeta] = useState<Meta | null>(null);

  const [applied, setApplied] = useState<Settings | null>(null);

  const [images, setImages] = useState<string[]>([]);

  const [index, setIndex] = useState(0), [playing, setPlaying] = useState(false);

  const [busy, setBusy] = useState(false), [progress, setProgress] = useState('');

  const [error, setError] = useState(''), [notice, setNotice] = useState('');

  const [advanced, setAdvanced] = useState(false), [tab, setTab] = useState('overview');

  const [criteriaOpen, setCriteriaOpen] = useState(false);

  const [pixel, setPixel] = useState<Pixel | null>(null), [pixelBusy, setPixelBusy] = useState(false);

  const [pngBusy, setPngBusy] = useState(false), [gifBusy, setGifBusy] = useState(false);

  const request = useRef<AbortController | null>(null), pixelRequest = useRef<AbortController | null>(null);

  const seq = useRef(0);

  const ownedImages = useRef<string[]>([]);

  const viewport = useRef<HTMLDivElement>(null);

  const hoverTimer = useRef<ReturnType<typeof setTimeout> | null>(null);

  const lastPixel = useRef('');

  const resourceParams = applied ? parameters(applied) : new URLSearchParams();

  ['show_compass', 'show_vector', 'show_detections', 'show_catalog', 'clean', 'smooth', 'show_mask', 'speed'].forEach(k => resourceParams.delete(k));

  const resourceQuery = meta && applied ? resourceParams.toString() : null;

  const motionResource = useRemote<Motion>(resourceQuery ? '/api/motion?' + resourceQuery : null);

  const spectrumResource = useRemote<Spectrum>(resourceQuery && tab === 'spectrum' ? '/api/spectrum?' + resourceQuery : null);

  const catalogResource = useRemote<Catalog>(resourceQuery ? '/api/catalog?' + resourceQuery : null);

  const detectionResource = useRemote<Detections>(resourceQuery && applied?.mode === 'diff' ? '/api/detections?' + resourceQuery : null);

  const motion = motionResource.data, spectrum = spectrumResource.data;

  const activeResource = tab === 'motion' ? motionResource : tab === 'spectrum' ? spectrumResource : tab === 'catalog' ? catalogResource : null;

  const aladinUrl = meta ? `https://aladin.cds.unistra.fr/AladinLite/?target=${encodeURIComponent(`${meta.ra} ${meta.dec}`)}&fov=${meta.fov / 3600}&survey=P%2FALLWISE%2Fcolor` : null;





  async function load(settings: Settings) {

    request.current?.abort(); pixelRequest.current?.abort(); if (hoverTimer.current) clearTimeout(hoverTimer.current);

    const controller = new AbortController(); request.current = controller;

    const run = ++seq.current;

    ownedImages.current.forEach(URL.revokeObjectURL); ownedImages.current = [];

    setBusy(true); setError(''); setPlaying(false); setImages([]); setMeta(null); setIndex(0); setPixel(null);

    setPixelBusy(false); lastPixel.current = ''; setTab('overview');

    setProgress('Finding observations and preparing the sky grid…');

    try {

      const q = parameters(settings);

      const result = await getJSON<Meta>('/api/meta?' + q, controller.signal);

      if (!result.n_frames) throw new Error('No observations are available for this selection. Try another target or filter.');

      let chosen = { ...settings };

      if (settings.mode === 'diff') {

        chosen = { ...chosen, epoch_a: chosen.epoch_a || String(result.recommended_epochs?.[0] ?? 0), epoch_b: chosen.epoch_b || String(result.recommended_epochs?.[1] ?? 1) };

      }

      const imageParams = parameters(chosen);

      const count = chosen.mode === 'blink' ? result.n_frames : 1;

      const urls = Array.from({ length: count }, (_, i) => '/api/image.png?' + imageParams + '&index=' + i);

      setProgress(`Preparing images · 0 / ${count}`);

      const loaded = await preloadImages(urls, controller.signal, completed => {

        if (seq.current === run) setProgress(`Preparing images · ${completed} / ${count}`);

      });

      if (controller.signal.aborted || seq.current !== run) { loaded.forEach(URL.revokeObjectURL); return; }

      ownedImages.current = loaded;

      setMeta(result); setApplied(chosen); setForm(chosen); setImages(loaded);

      history.replaceState(null, '', '?' + imageParams + '#explorer');

    } catch (err) {

      if ((err as Error).name !== 'AbortError' && seq.current === run) { controller.abort(); setError((err as Error).message); }

    } finally { if (seq.current === run) setBusy(false); }

  }

  useEffect(() => {

    const q = new URLSearchParams(location.search);

    if (q.has('target') || q.has('ra')) void load(readSettings());

    return () => { ownedImages.current.forEach(URL.revokeObjectURL); request.current?.abort(); pixelRequest.current?.abort(); if (hoverTimer.current) clearTimeout(hoverTimer.current); };

  }, []);

  useEffect(() => {

    if (!playing || applied?.mode !== 'blink' || images.length < 2) return;

    const timer = setInterval(() => setIndex(i => (i + 1) % images.length), Math.max(50, Number(form.speed) || 400));

    return () => clearInterval(timer);

  }, [playing, images.length, form.speed, applied?.mode]);

  useEffect(() => {

    const pause = () => { if (document.hidden) setPlaying(false); };

    document.addEventListener('visibilitychange', pause);

    return () => document.removeEventListener('visibilitychange', pause);

  }, []);

  useEffect(() => { pixelRequest.current?.abort(); if (hoverTimer.current) clearTimeout(hoverTimer.current); lastPixel.current = ''; setPixel(null); setPixelBusy(false); }, [index]);

  useEffect(() => {

    if (!expanded) return;

    const escape = (e: KeyboardEvent) => { if (e.key === 'Escape' && !document.querySelector('dialog[open]')) onExpand(); };

    addEventListener('keydown', escape); return () => removeEventListener('keydown', escape);

  }, [expanded, onExpand]);

  useEffect(() => { if (!notice) return; const id = setTimeout(() => setNotice(''), 4000); return () => clearTimeout(id); }, [notice]);



  function submit(e: FormEvent) {
    e.preventDefault();
    const targetChanged = !applied || form.target !== applied.target || form.ra !== applied.ra || form.dec !== applied.dec;
    const next = targetChanged ? { ...form, epoch_a: '', epoch_b: '' } : form;
    if (targetChanged) setForm(next);
    void load(next);
  }

  function change(key: keyof Settings, value: string | boolean) { setForm(s => ({ ...s, [key]: value })); }

  function updateDisplay(key: 'clean' | 'smooth' | 'show_mask', checked: boolean) {

    const next = { ...form, [key]: checked };

    setForm(next);

    if (meta) void load(next);

  }

  function toggleOverlay(key: 'show_compass' | 'show_vector' | 'show_detections' | 'show_catalog', checked: boolean) {

    setForm(s => ({ ...s, [key]: checked }));

    if (applied) { const next = { ...applied, [key]: checked, speed: form.speed }; setApplied(next); history.replaceState(null, '', '?' + parameters(next) + '#explorer'); }

  }

  function selectEpoch(epoch: number) {

    if (!meta) return;

    const frameIndex = meta.frames.findIndex(f => f.epoch === epoch);

    if (applied?.mode === 'blink') { setPlaying(false); setIndex(Math.max(0, frameIndex)); }

    else void load({ ...form, mode: 'blink' }).then(() => { setIndex(Math.max(0, frameIndex)); });

  }

  function mode(value: string) { const next = { ...form, mode: value }; setForm(next); if (meta) void load(next); }

  function selectPreset(value: string) { const next = { ...form, target: value, ra: '', dec: '', detector: '', wmin: '', wmax: '', epoch_a: '', epoch_b: '' }; setInputMode('name'); setForm(next); void load(next); }

  function step(delta: number) { setPlaying(false); setIndex(i => (i + delta + images.length) % images.length); setPixel(null); }

  function cancel() { ++seq.current; request.current?.abort(); setBusy(false); setProgress(''); setNotice('Request cancelled. You can choose another field.'); }

  async function share() {

    if (!applied) return;

    const url = `${location.origin}/?${parameters({ ...applied, speed: form.speed })}#explorer`;

    history.replaceState(null, '', url);

    try { await navigator.clipboard.writeText(url); setNotice('Explorer link copied.'); }

    catch { setNotice('Your view is saved in the address bar. Copy the URL to share it.'); }

  }

  function formatEpochLabel(epochId: string | number | undefined): string | null {
    if (epochId === undefined || epochId === '') return null;
    const ep = viewerEpochs.find(e => String(e.epoch) === String(epochId));
    return ep ? `Pass ${ep.epoch} (${dateFromMJD(ep.start_mjd)})` : `Pass ${epochId}`;
  }

  async function downloadPng() {
    if (!meta || !applied || !images[index] || pngBusy) return;
    setPngBusy(true);
    try {
      const wls = meta.frames.map(f => f.wavelength).filter(Number.isFinite);
      const wavelengthRangeUm: [number, number] | null = wls.length ? [Math.min(...wls), Math.max(...wls)] : null;
      const starName = (applied.target && presetDisplayNames[applied.target]) || applied.target || meta.label;
      const recA = applied.epoch_a || meta.recommended_epochs?.[0];
      const recB = applied.epoch_b || meta.recommended_epochs?.[meta.recommended_epochs.length - 1];
      await triggerAnnotatedPngDownload(images[index], {
        mode: (applied.mode || 'blink') as ExportMode,
        targetName: starName,
        ra: meta.ra,
        dec: meta.dec,
        detector: applied.detector || meta.detector,
        fovArcsec: meta.fov,
        wavelengthUm: currentFrame?.wavelength ?? null,
        wavelengthRangeUm,
        frameIndex: index,
        frameCount: meta.n_frames,
        frameDate: currentFrame ? dateFromMJD(currentFrame.mjd) : null,
        epochALabel: formatEpochLabel(recA),
        epochBLabel: formatEpochLabel(recB),
        pmTotal: motion?.pm_total ?? null,
        pmTotalErr: motion?.pm_total_err ?? null,
        positionAngleDeg: motion?.position_angle_deg ?? null,
        diffPalette: 'color',
        synthetic: meta.mock,
        smooth: applied.smooth,
        northUp: meta.sky_grid?.north_up ?? true,
      });
      setNotice('Downloaded annotated PNG image.');
    } catch (err) {
      setNotice((err as Error).message || 'Could not export annotated PNG.');
    } finally {
      setPngBusy(false);
    }
  }

  async function downloadGif() {
    if (!meta || !applied || gifBusy) return;
    setGifBusy(true);
    try {
      const q = new URLSearchParams();
      if (applied.target) q.set('target', presetDisplayNames[applied.target] || applied.target);
      else if (applied.ra && applied.dec) { q.set('ra', applied.ra); q.set('dec', applied.dec); }
      else { q.set('ra', String(meta.ra)); q.set('dec', String(meta.dec)); }
      if (applied.fov) q.set('fov', applied.fov);
      if (applied.detector) q.set('detector', applied.detector);
      if (applied.wmin && applied.wmax) { q.set('wmin', applied.wmin); q.set('wmax', applied.wmax); }
      q.set('speed', form.speed || '400');
      const r = await fetch('/api/blink.gif?' + q.toString());
      if (!r.ok) {
        const body = await r.json().catch(() => ({}));
        throw new Error(body?.error?.message || body?.detail || `GIF export failed (${r.status}).`);
      }
      const ctype = r.headers.get('content-type') || '';
      if (!ctype.includes('image/gif')) throw new Error('GIF endpoint did not return an image/gif.');
      const blob = await r.blob();
      const url = URL.createObjectURL(blob);
      const slug = (applied.target || `${meta.ra.toFixed(2)}_${meta.dec.toFixed(2)}`).replace(/[^a-zA-Z0-9._-]+/g, '-').toLowerCase();
      const a = document.createElement('a');
      a.href = url;
      a.download = `shroudx-${slug}-blink.gif`;
      document.body.appendChild(a);
      a.click();
      a.remove();
      setTimeout(() => URL.revokeObjectURL(url), 2000);
      setNotice('Downloaded annotated Blink GIF.');
    } catch (err) {
      setNotice((err as Error).message || 'Could not download Blink GIF.');
    } finally {
      setGifBusy(false);
    }
  }

  function inspect(e: React.MouseEvent<HTMLDivElement>) {

    if (hoverTimer.current) clearTimeout(hoverTimer.current);

    void inspectAt(e.clientX, e.clientY, e.currentTarget.getBoundingClientRect(), true);

  }

  function hoverInspect(e: React.MouseEvent<HTMLDivElement>) {

    if (playing || !meta || applied?.mode !== 'blink') return;

    if (hoverTimer.current) clearTimeout(hoverTimer.current);

    const clientX = e.clientX, clientY = e.clientY, rect = e.currentTarget.getBoundingClientRect();

    hoverTimer.current = setTimeout(() => { void inspectAt(clientX, clientY, rect, false); }, 100);

  }

  async function inspectAt(clientX: number, clientY: number, rect: DOMRect, force: boolean) {

    if (!meta || !applied || applied.mode !== 'blink' || busy) return;

    const [ny, nx] = meta.frame_shape;

    const x = Math.max(0, Math.min(nx - 1, Math.floor((clientX - rect.left) / rect.width * nx)));

    const y = Math.max(0, Math.min(ny - 1, Math.floor((1 - (clientY - rect.top) / rect.height) * ny)));

    const key = `${index}:${x}:${y}`;

    if (!force && lastPixel.current === key) return;

    lastPixel.current = key;

    pixelRequest.current?.abort(); const controller = new AbortController(); pixelRequest.current = controller;

    setPixelBusy(true); const q = parameters(applied); q.set('x', String(x)); q.set('y', String(y)); q.set('index', String(index));

    try { const p = await getJSON<Pixel>('/api/pixel?' + q, controller.signal); if (!controller.signal.aborted) setPixel(p); }

    catch (err) { if ((err as Error).name !== 'AbortError') setNotice((err as Error).message); }

    finally { if (!controller.signal.aborted) setPixelBusy(false); }

  }

  function showTab(value: string) { setTab(value); }

  const currentFrame = meta?.frames[index];

  const viewerEpochs = meta ? [...new Set(meta.frames.map(f => f.epoch))].sort((a, b) => a - b).map(epoch => {

    const frames = meta.frames.filter(f => f.epoch === epoch);

    return { epoch, start_mjd: Math.min(...frames.map(f => f.mjd)), n_frames: frames.length };

  }) : [];

  const isBlink = applied?.mode === 'blink';

  return <div className={'workspace' + (expanded ? ' expanded' : '')}>

    <div className="workspace-bar"><span><span className="status-dot" /> SPHEREx EXPLORER <span className="workspace-version mono">/ 01</span></span><div><button className="criteria-trigger small-button" onClick={() => setCriteriaOpen(true)}>Project criteria</button><span className="source-badge">{meta ? meta.mock ? 'SIMULATED DATA' : 'ARCHIVE OBSERVATIONS' : 'READY TO EXPLORE'}</span><button className="icon-button" title={expanded ? 'Exit focused view (Esc)' : 'Expand workspace'} aria-label={expanded ? 'Exit focused view' : 'Expand workspace'} onClick={onExpand}>{expanded ? <Minimize2 size={16} /> : <Maximize2 size={16} />}</button></div></div>

    <div className="workspace-body">

      <aside className="search-panel"><div className="panel-heading"><Crosshair size={17} /><h3>Find your patch of sky</h3></div><p className="panel-intro">Start with a familiar name.<br />Or follow your own coordinates.</p>

        <form onSubmit={submit}>

          <div className="segmented input-tabs"><button type="button" className={inputMode === 'name' ? 'active' : ''} onClick={() => { setInputMode('name'); change('target', 'WISE0855'); }}>Object name</button><button type="button" className={inputMode === 'coordinates' ? 'active' : ''} onClick={() => { setInputMode('coordinates'); change('target', ''); }}>Coordinates</button></div>

          {inputMode === 'name' ? <label className="field-label">TARGET NAME<div className="search-field"><Search size={16} /><input aria-label="Target name" value={form.target} onChange={e => change('target', e.target.value)} placeholder="e.g. Barnard’s Star" required /></div></label> : <div className="coordinate-fields"><label className="field-label">RIGHT ASCENSION<input aria-label="Right ascension" value={form.ra} onChange={e => change('ra', e.target.value)} placeholder="133.75990" required /></label><label className="field-label">DECLINATION<input aria-label="Declination" value={form.dec} onChange={e => change('dec', e.target.value)} placeholder="−7.24221" required /></label><p className="field-help">Decimal degrees or sexagesimal coordinates.</p></div>}

          <label className="field-label fov-label">FIELD OF VIEW <input className="fov-number" aria-label="Exact field of view in arcseconds" type="number" min="30" max="480" value={form.fov} onChange={e => change('fov', e.target.value)} /><input type="range" aria-label="Field of view" min="30" max="480" step="10" value={form.fov} onChange={e => change('fov', e.target.value)} /><span className="range-labels"><span>30″ · close</span><span>480″ · wide</span></span></label>

          <button type="button" className="advanced-toggle" aria-expanded={advanced} onClick={() => setAdvanced(!advanced)}><SlidersHorizontal size={14} /> Observation settings <ChevronDown size={14} className={advanced ? 'rotated' : ''} /></button>

          {advanced && <div className="advanced-fields"><label className="field-label">DETECTOR<select value={form.detector} onChange={e => change('detector', e.target.value)}><option value="">Automatic (coverage first)</option>{DETECTORS.map(d => <option value={d.id} key={d.id}>D{d.id} · {d.range} µm</option>)}</select></label><div className="field-row"><label className="field-label">MIN λ (µm)<input type="number" step="0.01" min="0.7" max="5.1" value={form.wmin} onChange={e => change('wmin', e.target.value)} placeholder="Any" /></label><label className="field-label">MAX λ (µm)<input type="number" step="0.01" min="0.7" max="5.1" value={form.wmax} onChange={e => change('wmax', e.target.value)} placeholder="Any" /></label></div><p className="field-help">Narrow, matching wavelengths make comparisons more meaningful.</p><label className="check-label"><input type="checkbox" checked={form.clean} onChange={e => updateDisplay('clean', e.target.checked)} /> Clean individual blink frames</label><label className="check-label"><input type="checkbox" checked={form.smooth} onChange={e => updateDisplay('smooth', e.target.checked)} /> Smooth display (visual only)</label><label className="check-label"><input type="checkbox" checked={form.show_mask} onChange={e => updateDisplay('show_mask', e.target.checked)} /> Show pixel mask in blink</label></div>}

          <button type="submit" className="button button-light explore-submit" disabled={busy}>{busy ? <><LoaderCircle className="spin" size={16} /> Preparing field</> : <>Open observations <ArrowUpRight size={17} /></>}</button>

        </form>

        <div className="preset-section"><p className="eyebrow">A FEW PLACES TO BEGIN</p><div className="presets">{presets.map(([value, label]) => <button disabled={busy} className={form.target === value ? 'selected' : ''} onClick={() => selectPreset(value)} key={value}>{label}<ArrowUpRight size={12} /></button>)}</div></div>

        <p className="panel-bottom-note"><Info size={14} /> Dates and available wavelengths depend on the archive. Mock observations are explicitly labelled.</p>

      </aside>

      <div className="observation-panel">

        <div className="observation-toolbar"><div className="segmented mode-tabs" aria-label="Observation mode">{[['blink', 'Blink'], ['stack', 'Stack'], ['diff', 'Difference']].map(([value, label]) => <button disabled={busy} type="button" key={value} onClick={() => mode(value)} className={form.mode === value ? 'active' : ''}>{label}</button>)}</div><div className="toolbar-actions">{aladinUrl && <a className="aladin-link" href={aladinUrl} target="_blank" rel="noreferrer" title="Open the current field in CDS Aladin Lite">CDS Aladin <ArrowUpRight size={13} /></a>}<button type="button" className="icon-button" disabled={!meta || busy} onClick={share} title="Copy view link" aria-label="Copy view link"><Link2 size={16} /></button><button type="button" className="small-button export-button" disabled={!images[index] || busy || pngBusy} onClick={downloadPng} title="Download PNG with mode, star name, and legend inside the image" aria-label="Download annotated PNG"><Download size={14} /><span>{pngBusy ? 'Saving…' : 'Photo PNG'}</span></button><button type="button" className="small-button export-button" disabled={!meta || busy || gifBusy || meta.n_frames < 2} onClick={downloadGif} title="Download animated Blink GIF with mode and star caption" aria-label="Download Blink GIF"><Film size={14} /><span>{gifBusy ? 'Building…' : 'Blink GIF'}</span></button></div></div>

        {form.mode === 'diff' && <div className="epoch-select"><span>Compare periods</span><label>A<select aria-label="Earlier epoch" value={form.epoch_a} onChange={e => change('epoch_a', e.target.value)}><option value="">Recommended</option>{viewerEpochs.map(ep => <option key={ep.epoch} value={ep.epoch}>{ep.epoch} · {dateFromMJD(ep.start_mjd)} · {ep.n_frames}f</option>)}</select></label><span>→</span><label>B<select aria-label="Later epoch" value={form.epoch_b} onChange={e => change('epoch_b', e.target.value)}><option value="">Recommended</option>{viewerEpochs.map(ep => <option key={ep.epoch} value={ep.epoch}>{ep.epoch} · {dateFromMJD(ep.start_mjd)} · {ep.n_frames}f</option>)}</select></label><button disabled={busy} onClick={() => load(form)} className="small-button">Apply</button></div>}

        <div className="viewport-shell">{meta && form.show_compass && <OrientationRail meta={meta} />}<div className={'sky-viewport' + (meta && isBlink ? ' inspectable' : '')} ref={viewport} onClick={inspect} onMouseMove={hoverInspect} onMouseLeave={() => { if (hoverTimer.current) clearTimeout(hoverTimer.current); }}>

          {images[index] && <img className="observation-image" src={images[index]} alt={`${applied?.target || 'Selected sky region'} — ${applied?.mode} ${isBlink ? `observation ${index + 1}` : 'image'}`} style={{ imageRendering: applied?.smooth ? 'auto' : 'pixelated' }} onError={() => setError('The image could not be displayed. Reload the observations.')} />}

          {meta && applied && <ObservationOverlay meta={meta} grid={meta.sky_grid} motion={motion} detections={detectionResource.data} catalog={catalogResource.data} options={form} frame={currentFrame} mode={applied.mode} />}

          {!meta && !busy && !error && <div className="viewer-empty"><div className="empty-orbits"><Crosshair size={30} strokeWidth={.8} /></div><p className="eyebrow">YOUR NEXT DISCOVERY STARTS HERE</p><h3>A small window.<br /><em>A vast universe.</em></h3><p>Choose a target to load observations.</p></div>}

          {busy && <div className="viewer-message" role="status"><LoaderCircle size={30} className="spin" strokeWidth={1} /><h3>Bringing the sky into focus.</h3><p>{progress}</p><button className="small-button" onClick={cancel}>Cancel request <X size={12} /></button></div>}

          {error && <div className="viewer-message error-message" role="alert"><AlertCircle size={26} /><h3>We couldn’t open this field.</h3><p>{error}</p><button className="small-button" onClick={() => load(form)}><RotateCcw size={13} /> Try again</button></div>}

          {meta && <><div className="viewport-label mono">{meta.mock ? 'SIMULATION' : 'SPHEREx / INFRARED'}<br /><span>{(applied?.target && presetDisplayNames[applied.target]) || applied?.target || `${meta.ra.toFixed(4)}°, ${meta.dec.toFixed(4)}°`}</span></div><div className="viewport-cross cross-tl" /><div className="viewport-cross cross-tr" /><div className="viewport-cross cross-bl" /><div className="viewport-cross cross-br" /><span className="viewport-bottom mono">{isBlink && currentFrame ? `${dateFromMJD(currentFrame.mjd)} · MJD ${currentFrame.mjd.toFixed(2)} · Epoch ${currentFrame.epoch}` : applied?.mode === 'stack' ? 'COMBINED OBSERVATIONS' : `PERIOD ${applied?.epoch_b} − PERIOD ${applied?.epoch_a}`}</span>{meta.mock && <span className="mock-note">Synthetic sources · not an image of the named target</span>}</>}

        </div>{meta && form.show_compass && <ScaleRail meta={meta} />}</div>

        <div className="playback"><div className="playback-buttons"><button className="icon-button" disabled={!isBlink || images.length < 2} onClick={() => step(-1)} aria-label="Previous frame"><SkipBack size={16} /></button><button className="play-button" disabled={!isBlink || images.length < 2} onClick={() => setPlaying(!playing)} aria-label={playing ? 'Pause animation' : 'Play animation'}>{playing ? <Pause size={17} /> : <Play size={17} fill="currentColor" />}</button><button className="icon-button" disabled={!isBlink || images.length < 2} onClick={() => step(1)} aria-label="Next frame"><SkipForward size={16} /></button></div><div className="frame-slider"><input aria-label="Observation frame" type="range" min="0" max={Math.max(1, images.length - 1)} value={index} disabled={!isBlink || images.length < 2} onChange={e => { setPlaying(false); setIndex(Number(e.target.value)); setPixel(null); }} /><span className="mono">{meta ? isBlink ? `FRAME ${String(index + 1).padStart(2, '0')} / ${images.length}` : applied?.mode.toUpperCase() : 'NO FIELD SELECTED'}</span></div><label className="speed-label">ms/frame<input aria-label="Playback speed in milliseconds" type="number" min="50" max="2000" step="50" value={form.speed} onChange={e => { change('speed', e.target.value); if (applied) history.replaceState(null, '', '?' + parameters({ ...applied, speed: e.target.value }) + '#explorer'); }} /></label></div>

        <div className="overlay-controls" aria-label="Viewer overlay options">{([['show_vector', 'Motion vector & track'], ['show_compass', 'Compass & scale'], ['show_detections', 'Detection lobes'], ['show_catalog', 'Gaia / WISE']] as const).map(([key, label]) => <label className="check-label" key={key}><input type="checkbox" checked={form[key]} onChange={e => toggleOverlay(key, e.target.checked)} />{label}</label>)}</div>

        <ViewerLegend
          mode={(applied?.mode || form.mode || 'blink') as ExportMode}
          showVector={form.show_vector}
          showDetections={form.show_detections}
          showCatalog={form.show_catalog}
          showMask={form.show_mask}
          showCompass={form.show_compass}
        />

        {meta && motion && form.show_vector && <MotionTrackPanel motion={motion} meta={meta} frame={currentFrame} mode={applied?.mode || 'blink'} />}

        {meta && <div className="pixel-hint">{isBlink ? playing ? 'Pause to hover or click and inspect a pixel.' : 'Hover or click to inspect native pixel values, uncertainty, sky coordinates, and flags.' : 'Pixel inspection is available in Blink mode; the displayed image is a derived product.'}</div>}

        {meta && <EpochTimeline meta={meta} currentEpoch={isBlink ? currentFrame?.epoch : undefined} onSelect={selectEpoch} />}

        {meta && <div className="data-label"><span className="status-dot" /><span>{meta.label}</span>{isBlink && currentFrame && <span className="mono">{currentFrame.wavelength.toFixed(3)} µm</span>}</div>}

        {!!meta?.warnings?.length && <div className="data-warning"><AlertCircle size={15} /><p>{meta.warnings.join(' ')}</p></div>}

        {(pixel || pixelBusy) && <div className="pixel-readout" role="status"><Crosshair size={15} />{pixelBusy ? <span>Reading pixel…</span> : pixel && <><span>Raw pixel [{pixel.x}, {pixel.y}] · {pixel.flux?.toFixed(3) ?? '—'} ± {pixel.sigma?.toFixed(3) ?? '—'} MJy/sr<br /><small>{pixel.has_sky_coordinates !== false ? `RA ${pixel.ra_deg.toFixed(5)}° · Dec ${pixel.dec_deg.toFixed(5)}°` : 'No celestial coordinates for synthetic pixels'} · D{pixel.detector} · λ {pixel.wavelength?.toFixed(3)} µm<br />Variance {pixel.variance?.toFixed(3) ?? '—'} · ZODI {pixel.zodi?.toFixed(3) ?? 'unavailable'} · {pixel.is_masked ? 'Excluded by mask' : 'Not masked'}</small><small>Flags: {pixel.quality.active_flags.join(', ') || 'None recorded'} · bitmask {pixel.quality.flag_value ?? '—'}</small></span><span className={'pixel-quality ' + pixel.quality.category}>{pixel.quality.category.replaceAll('_', ' ')}</span></>}</div>}

        <div className="insights"><div className="insight-tabs" role="tablist" aria-label="Observation information">{[['overview', 'Overview'], ['motion', 'Astrometry'], ['spectrum', 'Spectrum'], ['catalog', 'Catalogue'], ['guide', 'How to read']].map(([value, label]) => <button role="tab" aria-selected={tab === value} key={value} disabled={busy || (!meta && !['overview', 'guide'].includes(value))} onClick={() => showTab(value)} className={tab === value ? 'active' : ''}>{label}</button>)}</div><div className="insight-content" role="tabpanel">

          {tab === 'guide' ? <div className="guide-content"><p><b>Blink</b> alternates individual observations. Look for changes relative to the background.</p><p><b>Stack</b> combines the full selected set to reduce noise. Moving sources may blur.</p><p><b>Difference</b> compares two periods. Green and red represent opposite signs of change—not confirmed movers. Detection circles summarize pixels above the backend’s fixed threshold.</p><p><b>Motion overlays</b> show true-scale measured positions and fitted motion on the image. The separate position plot enlarges small tracks with equal axis scaling; its direction dial has a fixed-length arrow. Compass and scale sit outside the observation.</p><p><b>Catalogue markers</b> are stored reference positions, not time-propagated or confirmed identifications. They are not drawn on synthetic sources.</p><p>Wavelength differences can cause flicker. Smoothing changes appearance, not measured resolution. Values in the pixel inspector refer to individual native frames.</p></div> : tab === 'overview' ? meta ? <>

            <div className="field-metrics"><div><span className="mono">OBSERVATIONS</span><strong>{meta.n_frames}</strong></div><div><span className="mono">TIME SPAN</span><strong>{meta.span_days}<small> days</small></strong></div><div><span className="mono">DETECTOR</span><strong>0{meta.detector}</strong></div><div><span className="mono">FIELD WIDTH</span><strong>{meta.fov}<small>″</small></strong></div></div><p className="insight-note">{meta.detector_info}<br />Centre RA {meta.ra.toFixed(5)}° / Dec {meta.dec.toFixed(5)}° · {meta.frame_shape[1]} × {meta.frame_shape[0]} native pixels</p>

            {motionResource.loading ? <p className="insight-loading"><LoaderCircle size={15} className="spin" /> Measuring motion…</p> : motionResource.error ? <ResourceError message={motionResource.error} retry={motionResource.retry} /> : motion && <AstrometryPanel motion={motion} mock={meta.mock} />}

            {spectrumResource.loading ? <p className="insight-loading"><LoaderCircle size={15} className="spin" /> Extracting spectral samples…</p> : spectrumResource.error ? <ResourceError message={spectrumResource.error} retry={spectrumResource.retry} /> : spectrum && <SpectrumPlot data={spectrum} wavelength={isBlink ? currentFrame?.wavelength : undefined} />}

            {form.show_catalog && (catalogResource.loading ? <p className="insight-loading">Loading catalogue references…</p> : catalogResource.error ? <ResourceError message={catalogResource.error} retry={catalogResource.retry} /> : catalogResource.data && <CatalogPanel data={catalogResource.data} />)}

          </> : <p className="insight-note">Observation dates, detector selection, astrometry, spectra, and reference sources will appear after you open a field.</p> : activeResource?.loading ? <p className="insight-loading"><LoaderCircle size={15} className="spin" /> Loading {tab}…</p> : activeResource?.error ? <ResourceError message={activeResource.error} retry={activeResource.retry} /> : tab === 'motion' && motion ? <AstrometryPanel motion={motion} mock={Boolean(meta?.mock)} /> : tab === 'spectrum' && spectrum ? <SpectrumPlot data={spectrum} wavelength={isBlink ? currentFrame?.wavelength : undefined} /> : tab === 'catalog' && catalogResource.data ? <CatalogPanel data={catalogResource.data} /> : null}

          {applied?.mode === 'diff' && <section className="detection-summary"><div className="science-heading"><h4>Significant residuals</h4><span>Backend threshold</span></div>{detectionResource.loading ? <p className="insight-loading">Finding residual lobes…</p> : detectionResource.error ? <ResourceError message={detectionResource.error} retry={detectionResource.retry} /> : detectionResource.data && <><p>{detectionResource.data.count} significant pixels · |z| &gt; {detectionResource.data.threshold.toFixed(1)} · Epoch {detectionResource.data.epoch_b} − Epoch {detectionResource.data.epoch_a}</p><div className="detection-legend">{detectionResource.data.lobes.map((lobe, i) => <span key={i} className={lobe.polarity}>{lobe.polarity === 'positive' ? '+' : '−'} {lobe.polarity} lobe · peak {lobe.peak_z?.toFixed(2)} · {lobe.n_pixels} pixels</span>)}</div><p className="insight-note">These are polarity summaries of thresholded residuals, not separate confirmed objects or a calibrated discovery probability.</p></>}</section>}



        </div></div>

      </div>

    </div>

    <CriteriaDialog open={criteriaOpen} onClose={() => setCriteriaOpen(false)} onTarget={selectPreset} />

    {notice && <div className="toast" role="status">{notice}<button className="icon-button" onClick={() => setNotice('')} aria-label="Dismiss notification"><X size={14} /></button></div>}

  </div>;

}



function ResourceError({ message, retry }: { message: string; retry: () => void }) {

  return <div className="resource-error" role="status"><AlertCircle size={14} /><span>{message}</span><button className="small-button" onClick={retry}>Retry</button></div>;

}
