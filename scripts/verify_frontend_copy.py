"""Verify preserved frontend files against the saved original import hashes.

PASS requires the exact expected file inventory and matching hashes outside the
three explicitly approved blink implementation/test files. Missing files, added
files or any unrelated edits fail. No removed practice-project folder is needed.
The baseline is tests/frontend_baseline.json, recovered from the earlier report.
"""
from __future__ import annotations

import hashlib
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def inventory(folder: Path) -> dict[str, str]:
    """Return content hashes for all authored frontend files."""
    result = {}
    for path in folder.rglob('*'):
        relative = path.relative_to(folder)
        if {'node_modules', 'dist', '.git'}.intersection(relative.parts):
            continue
        if path.is_file() and not path.name.endswith('.tsbuildinfo'):
            result[relative.as_posix()] = hashlib.sha256(path.read_bytes()).hexdigest()
    return result


def verify(folder: Path, baseline: Path) -> dict:
    """Check the saved import baseline without accepting missing or extra files."""
    specification = json.loads(baseline.read_text(encoding='utf-8'))
    source = specification['files']
    approved = set(specification['approved_changes'])
    if not source:
        raise ValueError('Frontend baseline must not be empty')
    target = inventory(folder)
    expected = source.keys() | approved
    missing = sorted(expected - target.keys())
    added = sorted(target.keys() - expected)
    differences = sorted(name for name in source.keys() | target.keys() if source.get(name) != target.get(name))
    unexpected = sorted((set(differences) - approved) | set(missing) | set(added))
    return dict(status='PASS' if not unexpected else 'FAIL', exact_copy=not differences,
                unexpected_differences=unexpected, missing_files=missing, added_files=added,
                source_files=len(source), target_files=len(target), differences=differences,
                baseline=str(baseline), files=source)


def main() -> int:
    report = verify(ROOT / 'frontend', ROOT / 'tests/frontend_baseline.json')
    report.update(command=f'"{sys.executable}" scripts/verify_frontend_copy.py',
                  data_versions=[], note='Frontend preservation check; no astronomical measurements.')
    output = ROOT / 'results' / 'frontend-copy-verification.json'
    output.parent.mkdir(exist_ok=True)
    output.write_text(json.dumps(report, indent=2), encoding='utf-8')
    print(json.dumps({key: value for key, value in report.items() if key not in ('files', 'command', 'note', 'data_versions')}))
    return 0 if report['status'] == 'PASS' else 1


if __name__ == '__main__':
    raise SystemExit(main())
