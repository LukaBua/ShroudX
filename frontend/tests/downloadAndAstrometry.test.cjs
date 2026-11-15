const { test } = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const Module = require('node:module');
const ts = require('typescript');

function loadTs(relPath) {
  const file = path.resolve(__dirname, relPath);
  const compiled = ts.transpileModule(fs.readFileSync(file, 'utf8'), {
    compilerOptions: { module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2022 },
  }).outputText;
  const loaded = new Module(file, module);
  loaded._compile(compiled, file);
  return loaded.exports;
}

const { buildExportCaption } = loadTs('../src/downloadImage.ts');
const { classifyAstrometry } = loadTs('../src/astrometryStatus.ts');

test('export captions include mode, star identity, coordinates, and difference color legend', () => {
  const diff = buildExportCaption({
    mode: 'diff',
    targetName: 'WISE 0855-0714',
    ra: 133.7599,
    dec: -7.2422,
    detector: 5,
    fovArcsec: 120,
    epochALabel: 'Pass 0 (13 Aug 2025)',
    epochBLabel: 'Pass 2 (19 Aug 2026)',
    pmTotal: 8.15,
    pmTotalErr: 0.42,
    positionAngleDeg: 274.5,
  });
  assert.match(diff.modeBadge, /DIFFERENCE MODE/);
  assert.equal(diff.targetTitle, 'WISE 0855-0714');
  assert.match(diff.coordsLine, /133\.7599.*-7\.2422/);
  assert.match(diff.detailsLine, /Pass 0.*Pass 2.*Detector 5.*FOV 120/);
  assert.match(diff.legendText, /Green \(\+\).*positive movement/i);
  assert.match(diff.legendText, /Red \(−\).*negative movement/i);
  assert.match(diff.motionLine, /8\.15 ± 0\.42/);
  assert.equal(diff.filename, 'shroudx-wise-0855-0714-diff.png');

  const blink = buildExportCaption({
    mode: 'blink',
    targetName: "Barnard's Star",
    ra: 269.4485,
    dec: 4.7394,
    detector: 2,
    fovArcsec: 120,
    wavelengthUm: 1.425,
    frameIndex: 2,
    frameCount: 12,
    frameDate: '12 Mar 2026',
    synthetic: true,
  });
  assert.match(blink.modeBadge, /BLINK MODE/);
  assert.equal(blink.targetTitle, "Barnard's Star");
  assert.match(blink.detailsLine, /Frame 3\/12 · 12 Mar 2026 · 1\.425 µm · Detector 2/);
  assert.equal(blink.syntheticTag, 'SYNTHETIC / ILLUSTRATIVE');
  assert.equal(blink.filename, 'shroudx-barnard-s-star-blink-f3.png');
});

test('astrometry classification rejects huge-error stationary fits and vector mismatches', () => {
  const base = {
    baseline_years: 1.0,
    n_frames: 9,
    measurements: [],
  };

  // R Draconis: stationary star with saturated core (5.34 ± 5.29 "/yr vs 0.015 "/yr)
  const rDrac = classifyAstrometry(
    {
      ...base,
      pm_total: 5.341,
      pm_total_err: 5.285,
      pm_ra_cosdec: -4.589,
      pm_dec: 2.732,
      position_angle_deg: 300.8,
      validation: { has_catalog: true, catalog_pm_total: 0.015, agrees_within_3sigma: true, agrees_vector_3sigma: true },
    },
    false,
  );
  assert.notEqual(rDrac.tone, 'consistent');
  assert.match(rDrac.badge, /INCONCLUSIVE/);

  // RW Aurigae: stationary star measuring 1.04 ± 0.37 "/yr (43x too fast, huge relative err)
  const rwAur = classifyAstrometry(
    {
      ...base,
      pm_total: 1.04,
      pm_total_err: 0.371,
      pm_ra_cosdec: 1.039,
      pm_dec: 0.046,
      position_angle_deg: 87.5,
      validation: { has_catalog: true, catalog_pm_total: 0.024, agrees_within_3sigma: true, agrees_vector_3sigma: true },
    },
    false,
  );
  assert.notEqual(rwAur.tone, 'consistent');
  assert.match(rwAur.badge, /INCONCLUSIVE/);

  // Epsilon Indi B (without parallax): scalar speed passes 1D 3σ, but 2D vector PA is >3σ off
  const epsIndiCompat = classifyAstrometry(
    {
      ...base,
      pm_total: 4.222,
      pm_total_err: 0.171,
      pm_ra_cosdec: 3.165,
      pm_dec: -2.794,
      position_angle_deg: 131.4,
      validation: { has_catalog: true, catalog_pm_total: 4.705, agrees_within_3sigma: true, agrees_vector_3sigma: false },
    },
    false,
  );
  assert.equal(epsIndiCompat.tone, 'disagree');
  assert.match(epsIndiCompat.badge, /DIRECTION DIFFERS/);

  // Genuine precision vector agreement (e.g. Epsilon Indi B with parallax)
  const epsIndiGood = classifyAstrometry(
    {
      ...base,
      pm_total: 4.608,
      pm_total_err: 0.154,
      pm_ra_cosdec: 3.868,
      pm_dec: -2.501,
      position_angle_deg: 122.9,
      validation: { has_catalog: true, catalog_pm_total: 4.684, agrees_within_3sigma: true, agrees_vector_3sigma: true },
    },
    false,
  );
  assert.equal(epsIndiGood.tone, 'consistent');
});
