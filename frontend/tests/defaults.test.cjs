const { test } = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');

test('explorer defaults to Barnard star, 240 arcsec FOV, and extended Vite proxy timeout', () => {
  const explorerPath = path.resolve(__dirname, '../src/Explorer.tsx');
  const explorer = fs.readFileSync(explorerPath, 'utf8');
  assert.match(explorer, /target:\s*'Barnard'/);
  assert.match(explorer, /fov:\s*'240'/);
  assert.match(explorer, /const presets = \[\['Barnard',\s*'Barnard’s Star'\],\s*\['WISE0855'/);
  assert.match(explorer, /change\('target',\s*'Barnard'\)/);

  const vitePath = path.resolve(__dirname, '../vite.config.ts');
  const viteConfig = fs.readFileSync(vitePath, 'utf8');
  assert.match(viteConfig, /timeout:\s*120000/);
  assert.match(viteConfig, /proxyTimeout:\s*120000/);
});

test('frontend omits original dashboard link and archive disclaimer note', () => {
  const appPath = path.resolve(__dirname, '../src/App.tsx');
  const appSource = fs.readFileSync(appPath, 'utf8');
  assert.doesNotMatch(appSource, /Open original dashboard/i);
  assert.doesNotMatch(appSource, /href=["']\/legacy["']/i);

  const explorerPath = path.resolve(__dirname, '../src/Explorer.tsx');
  const explorer = fs.readFileSync(explorerPath, 'utf8');
  assert.doesNotMatch(explorer, /Dates and available wavelengths depend on the archive/i);
  assert.doesNotMatch(explorer, /Mock observations are explicitly/i);
});
