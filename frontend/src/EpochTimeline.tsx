import { Fragment } from 'react';
import { dateFromMJD, type Meta } from './api';

export default function EpochTimeline({ meta, currentEpoch, onSelect }: { meta: Meta; currentEpoch?: number; onSelect: (epoch: number) => void }) {
  // Use the viewer's actual frame assignments, including its split-half fallback.
  const ids = [...new Set(meta.frames.map(f => f.epoch))].sort((a, b) => a - b);
  const groups = ids.map(id => { const frames = meta.frames.filter(f => f.epoch === id); return { id, count: frames.length, start: Math.min(...frames.map(f => f.mjd)), end: Math.max(...frames.map(f => f.mjd)), lo: Math.min(...frames.map(f => f.wavelength)), hi: Math.max(...frames.map(f => f.wavelength)) }; });
  return <section className="epoch-timeline" aria-label="Observation epoch timeline"><div className="science-heading"><h4>Observation timeline</h4><span>{groups.length} groups · {(meta.span_days / 365.25).toFixed(2)} yr baseline</span></div><div className="epoch-pills">{groups.map((group, i) => <Fragment key={group.id}><button className={'epoch-pill' + (currentEpoch === group.id ? ' current' : '')} onClick={() => onSelect(group.id)} aria-label={`Show epoch ${group.id}`}><b>Epoch {group.id} <span>{group.count} frames</span></b><span>{dateFromMJD(group.start)}{group.end !== group.start ? ` – ${dateFromMJD(group.end)}` : ''}</span><small>λ {group.lo.toFixed(3)}–{group.hi.toFixed(3)} µm</small></button>{i < groups.length - 1 && <span className="epoch-gap">{Math.max(0, groups[i + 1].start - group.end).toFixed(0)}d →</span>}</Fragment>)}</div><p className="insight-note">Groups follow the backend’s time-gap rule; a single observing pass can be split into two comparison halves. Click a group to inspect its first frame.</p></section>;
}
