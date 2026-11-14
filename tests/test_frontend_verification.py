"""Known-content fixtures test preservation checks independently of the checkout."""
import hashlib
import json

from scripts.verify_frontend_copy import verify


def test_baseline_detects_changes_missing_files_and_extra_files(tmp_path):
    frontend = tmp_path / 'frontend'
    frontend.mkdir()
    protected = frontend / 'styles.css'
    protected.write_bytes(b'original font')
    approved = frontend / 'blink.ts'
    approved.write_bytes(b'approved implementation')
    baseline = tmp_path / 'baseline.json'
    baseline.write_text(json.dumps(dict(files={'styles.css': hashlib.sha256(protected.read_bytes()).hexdigest()},
                                       approved_changes=['blink.ts'])))
    assert verify(frontend, baseline)['status'] == 'PASS'
    approved.write_bytes(b'updated blink')
    assert verify(frontend, baseline)['status'] == 'PASS'
    protected.write_bytes(b'changed font')
    assert verify(frontend, baseline)['unexpected_differences'] == ['styles.css']
    protected.write_bytes(b'original font')
    approved.unlink()
    assert verify(frontend, baseline)['missing_files'] == ['blink.ts']
    approved.write_bytes(b'approved implementation')
    (frontend / 'extra.txt').write_bytes(b'unexpected')
    assert verify(frontend, baseline)['added_files'] == ['extra.txt']
