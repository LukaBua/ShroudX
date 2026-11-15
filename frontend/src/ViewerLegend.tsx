// AI-assisted: implemented with Codex and AI pair-programming assistant.

import { useState } from 'react';
import type { ExportMode } from './downloadImage';

interface ViewerLegendProps {
  mode: ExportMode;
  showVector: boolean;
  showDetections: boolean;
  showCatalog: boolean;
  showMask: boolean;
  showCompass: boolean;
}

export default function ViewerLegend({
  mode,
  showVector,
  showDetections,
  showCatalog,
  showMask,
  showCompass,
}: ViewerLegendProps) {
  const [expanded, setExpanded] = useState(true);

  return (
    <section className="viewer-legend" aria-label="Image and overlay legend">
      <div className="legend-header">
        <div className="legend-title-group">
          <span className="legend-eyebrow">LEGEND · WHAT IS WHAT</span>
          <span className="legend-mode-pill" data-mode={mode}>
            {mode === 'diff' ? 'Difference Method' : mode === 'stack' ? 'Weighted Stack' : 'Blink Sequence'}
          </span>
        </div>
        <button
          type="button"
          className="legend-toggle"
          onClick={() => setExpanded((v) => !v)}
          aria-expanded={expanded}
        >
          {expanded ? 'Hide legend' : 'Show legend'}
        </button>
      </div>

      {expanded && (
        <div className="legend-body">
          {mode === 'diff' ? (
            <div className="legend-diff-banner" role="note" aria-label="Difference colormap key">
              <div className="legend-diff-scale">
                <span className="legend-scale-neg">− Earlier Epoch A (Departure / Dimmer)</span>
                <span className="legend-scale-bar" aria-hidden="true" />
                <span className="legend-scale-pos">+ Later Epoch B (Arrival / Brighter)</span>
              </div>
              <div className="legend-items">
                <div className="legend-item">
                  <span className="swatch swatch-diff-pos" aria-hidden="true" />
                  <span>
                    <strong>Green area (+):</strong> Positive movement area — brighter in later Epoch B (where a moving star arrived)
                  </span>
                </div>
                <div className="legend-item">
                  <span className="swatch swatch-diff-neg" aria-hidden="true" />
                  <span>
                    <strong>Red area (−):</strong> Negative movement area — brighter in earlier Epoch A (where a moving star departed)
                  </span>
                </div>
                <div className="legend-item">
                  <span className="swatch swatch-diff-zero" aria-hidden="true" />
                  <span>
                    <strong>Dark background (~0):</strong> Stationary stars &amp; constant sky cancel out
                  </span>
                </div>
              </div>
              <p className="legend-tip">
                A moving star appears as a <strong>Red → Green dipole pair</strong> pointing along its direction of motion.
              </p>
            </div>
          ) : (
            <div className="legend-mode-summary">
              {mode === 'blink' ? (
                <div className="legend-item">
                  <span className="swatch swatch-blink" aria-hidden="true" />
                  <span>
                    <strong>Blink grayscale:</strong> Linear/asinh infrared intensity locked to a shared stretch across frames so real motion stands out without brightness flicker.
                  </span>
                </div>
              ) : (
                <div className="legend-item">
                  <span className="swatch swatch-stack" aria-hidden="true" />
                  <span>
                    <strong>Stack grayscale:</strong> Inverse-variance weighted co-add of frames in the selected band (suppresses random background noise by ~√N).
                  </span>
                </div>
              )}
            </div>
          )}

          <div className="legend-grid">
            <div className={`legend-item ${showVector ? '' : 'is-muted'}`}>
              <span className="swatch swatch-vector" aria-hidden="true" />
              <span>
                <strong>Green arrow:</strong> Fitted proper motion vector
              </span>
            </div>
            <div className={`legend-item ${showVector ? '' : 'is-muted'}`}>
              <span className="swatch swatch-track" aria-hidden="true" />
              <span>
                <strong>Blue dashed track:</strong> Measured per-frame centroids
              </span>
            </div>
            <div className={`legend-item ${showVector ? '' : 'is-muted'}`}>
              <span className="swatch swatch-reticle" aria-hidden="true" />
              <span>
                <strong>Gold reticle:</strong> Active frame centroid position
              </span>
            </div>
            <div className={`legend-item ${showCatalog ? '' : 'is-muted'}`}>
              <span className="swatch swatch-gaia" aria-hidden="true" />
              <span>
                <strong>Blue dashed ring:</strong> Gaia DR3 reference position
              </span>
            </div>
            <div className={`legend-item ${showCatalog ? '' : 'is-muted'}`}>
              <span className="swatch swatch-wise" aria-hidden="true" />
              <span>
                <strong>Gold dashed ring:</strong> AllWISE reference position
              </span>
            </div>
            {(mode === 'diff' || showDetections) && (
              <div className={`legend-item ${showDetections ? '' : 'is-muted'}`}>
                <span className="swatch swatch-lobe" aria-hidden="true" />
                <span>
                  <strong>Dashed green / red rings:</strong> Significant residual lobes
                </span>
              </div>
            )}
            <div className={`legend-item ${showMask ? '' : 'is-muted'}`}>
              <span className="swatch swatch-mask" aria-hidden="true" />
              <span>
                <strong>Crimson pixel tint:</strong> Bad / saturated masked pixels
              </span>
            </div>
            <div className={`legend-item ${showCompass ? '' : 'is-muted'}`}>
              <span className="swatch swatch-compass" aria-hidden="true" />
              <span>
                <strong>N ↑ / E ← rails:</strong> Astronomical orientation &amp; angular scale
              </span>
            </div>
          </div>
        </div>
      )}
    </section>
  );
}
