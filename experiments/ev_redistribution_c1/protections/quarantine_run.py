"""Reversible quarantine of a staged admin candidate run. Never modifies or deletes existing files.

Quarantine adds two files to project/dft_staging/run_<run_id>/:
  qa_report.md      ASCII report containing "FAIL". The admin CandidateValidator already refuses any candidate whose
                    qa_report.md contains FAIL (existing mechanism, honoured by the original and the protected code).
  QUARANTINED.json  audit record: reason, original status/metadata, registry entry, sha256 of every original file.
                    The protected promotion.py refuses runs with this marker explicitly.

Release (undo) removes exactly those two files after verifying the original files are unchanged.

Usage:
  python experiments/ev_redistribution_c1/protections/quarantine_run.py quarantine <run_id> --reason "..." [--root <repo>]
  python experiments/ev_redistribution_c1/protections/quarantine_run.py release <run_id> [--root <repo>]
  python experiments/ev_redistribution_c1/protections/quarantine_run.py verify <run_id> [--root <repo>]
"""
import argparse
import getpass
import hashlib
import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

DEFAULT_ROOT = Path(__file__).resolve().parents[3]
MARKER, QA = 'QUARANTINED.json', 'qa_report.md'


def sha(p):
    h = hashlib.sha256()
    with open(p, 'rb') as f:
        for block in iter(lambda: f.read(1 << 20), b''):
            h.update(block)
    return h.hexdigest()


def run_dir(root, run_id):
    if not run_id or any(c in run_id for c in '/\\:') or '..' in run_id:
        raise ValueError(f'invalid run id {run_id!r}')
    d = Path(root) / 'project' / 'dft_staging' / f'run_{run_id}'
    if not d.is_dir() or d.is_symlink():
        raise FileNotFoundError(f'run directory not found: {d}')
    return d


def inventory(d):
    out = {}
    for base, _, files in os.walk(d):
        for f in files:
            rel = Path(os.path.relpath(Path(base) / f, d)).as_posix()
            if rel not in (MARKER, QA):
                out[rel] = sha(Path(base) / f)
    return dict(sorted(out.items()))


def quarantine(root, run_id, reason):
    d = run_dir(root, run_id)
    for name in (MARKER, QA):
        if (d / name).exists():
            raise FileExistsError(f'{d / name} already exists; refusing to overwrite')
    meta = json.loads((d / 'metadata.json').read_text(encoding='utf-8')) if (d / 'metadata.json').exists() else None
    registry_file = Path(root) / 'project' / 'dft_staging' / 'candidate_registry.json'
    registry = json.loads(registry_file.read_text(encoding='utf-8')).get(run_id) if registry_file.exists() else None
    record = {
        'run_id': run_id, 'path': str(d), 'quarantined_at': datetime.now(timezone.utc).isoformat(timespec='seconds'),
        'quarantined_by': getpass.getuser(), 'reason': reason,
        'original_status': (meta or {}).get('status'), 'original_qa_status': (meta or {}).get('qa_status'),
        'original_metadata': meta, 'registry_entry': registry,
        'files_sha256': inventory(d),
        'mechanism': 'qa_report.md containing FAIL (existing CandidateValidator QA gate) + this marker (explicit check in protected promotion.py)',
        'reverse_with': f'python experiments/ev_redistribution_c1/protections/quarantine_run.py release {run_id}',
    }
    qa_text = (f'# QA REPORT - QUARANTINED\r\n\r\nQA STATUS: FAIL\r\n\r\nRun {run_id} is quarantined and must not be promoted.\r\n'
               f'Reason: {reason}\r\n\r\nAudit record: {MARKER} (sha256 of every original file).\r\n'
               f'Reverse only with an approved change: quarantine_run.py release {run_id}\r\n')
    (d / MARKER).write_text(json.dumps(record, indent=2, sort_keys=True), encoding='ascii', errors='strict')
    with open(d / QA, 'x', encoding='ascii', newline='') as f:
        f.write(qa_text)
    return record


def verify(root, run_id):
    d = run_dir(root, run_id)
    record = json.loads((d / MARKER).read_text(encoding='ascii'))
    now = inventory(d)
    changed = sorted(k for k in set(now) | set(record['files_sha256']) if now.get(k) != record['files_sha256'].get(k))
    qa_fail = 'FAIL' in (d / QA).read_text(encoding='ascii').upper()
    return {'quarantined': True, 'qa_gate_fail': qa_fail, 'original_files_unchanged': not changed, 'changed': changed,
            'files': len(record['files_sha256'])}


def release(root, run_id):
    d = run_dir(root, run_id)
    v = verify(root, run_id)
    if not v['original_files_unchanged']:
        raise RuntimeError(f'original files changed since quarantine: {v["changed"]}; refusing to release')
    os.remove(d / QA)
    os.remove(d / MARKER)
    return v


if __name__ == '__main__':
    ap = argparse.ArgumentParser()
    ap.add_argument('op', choices=['quarantine', 'release', 'verify'])
    ap.add_argument('run_id')
    ap.add_argument('--reason', default='')
    ap.add_argument('--root', default=str(DEFAULT_ROOT))
    a = ap.parse_args()
    if a.op == 'quarantine':
        if not a.reason:
            sys.exit('a --reason is required')
        r = quarantine(a.root, a.run_id, a.reason)
        print(json.dumps({k: r[k] for k in ('run_id', 'path', 'original_status', 'original_qa_status', 'quarantined_at')}, indent=2),
              f'\nfiles recorded: {len(r["files_sha256"])}')
    elif a.op == 'verify':
        print(json.dumps(verify(a.root, a.run_id), indent=2))
    else:
        print(json.dumps(release(a.root, a.run_id), indent=2))
