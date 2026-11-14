const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const Module = require('node:module');
const ts = require('typescript');

const file = path.resolve(__dirname, '../src/motionGeometry.ts');
const compiled = ts.transpileModule(fs.readFileSync(file, 'utf8'), { compilerOptions: { module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2022 } }).outputText;
const loaded = new Module(file, module);
loaded._compile(compiled, file);
const { motionGeometry, activeMeasurement } = loaded.exports;

test('fitted east/north velocity uses celestial handedness, not noisy endpoints', () => {
  const motion = { pm_ra_cosdec: 10, pm_dec: 5, measurements: [{ x: 50, y: 50, mjd: 61000 }, { x: 51, y: 49, mjd: 61365.25 }] };
  const fit = motionGeometry(motion, [100, 100], { has_wcs: true, north_up: true, width_arcsec: 100, height_arcsec: 100 });
  assert.ok(Math.abs((fit.end.x - fit.start.x) + 10) < 1e-8);
  assert.ok(Math.abs((fit.end.y - fit.start.y) - 5) < 1e-8);
  assert.equal(fit.celestial, true);
});

test('closely spaced exposures highlight their own measurement, not the first one', () => {
  const points = [{ x: 1, y: 2, mjd: 61000 }, { x: 4, y: 5, mjd: 61000.001 }];
  assert.equal(activeMeasurement(points, { mjd: 61000.001 }), points[1]);
  assert.equal(activeMeasurement(points, { mjd: 61000.0010004 }), points[1]);
  assert.equal(activeMeasurement(points, { mjd: 61000.002 }), undefined);
});

test('unoriented mock images fit their measured pixel trajectory rather than a sky angle', () => {
  const measurements = [{ x: 2, y: 8, mjd: 61000 }, { x: 4, y: 7, mjd: 61010 }, { x: 6, y: 6, mjd: 61020 }];
  const fit = motionGeometry({ measurements, pm_ra_cosdec: -999, pm_dec: 999 }, [20, 20]);
  assert.equal(fit.celestial, false);
  assert.ok(Math.abs(fit.vx - .2) < 1e-10);
  assert.ok(Math.abs(fit.vy + .1) < 1e-10);
});

test('stationary and identical-time sequences do not invent an arrow direction', () => {
  const stationary = motionGeometry({ measurements: [{ x: 5, y: 5, mjd: 61000 }, { x: 5, y: 5, mjd: 61001 }] }, [10, 10]);
  assert.equal(stationary.hasDirection, false);
  const sameTime = motionGeometry({ measurements: [{ x: 4, y: 5, mjd: 61000 }, { x: 6, y: 5, mjd: 61000 }] }, [10, 10]);
  assert.equal(sameTime.hasDirection, false);
  assert.ok(Number.isFinite(sameTime.start.x));
});
