"""Prototype git pre-commit guard for the dashboard EV actuals (NOT installed).

Refuses a commit when:
  * any integration/ev_actuals_*.csv is staged and its content is not exactly the bytes recorded by the latest
    sealed C1 release manifest (C1 bytes after a COMPLETE release; the original bytes after ROLLED_BACK or
    RECOVERED_TO_ORIGINAL). The release manifest carries the methodology id, LPN floor, reporting periods and
    source/shadow provenance hashes, which are checked here too;
  * an ev_actuals file is deleted;
  * files under project/production_backups/ or project/dft_staging/ are staged (bulk Method A copies), unless
    C1_ALLOW_BULK_STAGING=1.

Staged CSVs are Git LFS pointers ("oid sha256:<hex>"); the oid is the sha256 of the real content, so no smudge is
needed. Non-pointer blobs are hashed directly.

Install (separately approved phase): .git/hooks/pre-commit containing
    python experiments/ev_redistribution_c1/protections/pre_commit_c1_guard.py
"""
import hashlib
import json
import os
import re
import subprocess
import sys
from pathlib import Path

TARGETS = {f'project/output_processed/integration/ev_actuals_{g}.csv': f'ev_actuals_{g}.csv' for g in ('dno', 'la', 'laep')}
STATE_REL = 'experiments/ev_redistribution_c1/release/state'
BULK_PREFIXES = ('project/production_backups/', 'project/dft_staging/')
POINTER_RE = re.compile(rb'^version https://git-lfs\.github\.com/spec/v1\noid sha256:([0-9a-f]{64})\nsize \d+\n?$')


def seal(obj):
    body = {k: v for k, v in obj.items() if k != 'manifest_sha256'}
    return hashlib.sha256(json.dumps(body, sort_keys=True, separators=(',', ':'), default=str).encode('utf-8')).hexdigest()


def git(top, *args):
    return subprocess.run(['git', *args], cwd=top, capture_output=True, check=True).stdout


def staged_entries(top):
    raw = git(top, 'diff', '--cached', '--name-status', '-z', '--no-renames').split(b'\0')
    items = [x.decode('utf-8') for x in raw if x]
    return list(zip(items[0::2], items[1::2]))


def staged_sha256(top, path):
    blob = git(top, 'show', f':{path}')
    m = POINTER_RE.match(blob.replace(b'\r\n', b'\n'))
    return m.group(1).decode() if m else hashlib.sha256(blob).hexdigest()


def allowed_hashes(top):
    """Bytes allowed for each target according to the most recent sealed release manifest."""
    releases = Path(top, *STATE_REL.split('/'), 'releases')
    manifests = []
    for p in sorted(releases.glob('*/release_manifest.json')) if releases.exists() else []:
        try:
            m = json.loads(p.read_text(encoding='utf-8'))
        except ValueError:
            continue
        if m.get('manifest_sha256') != seal(m):
            continue
        if m.get('methodology_id') != 'C1_DYNAMIC_PRIVATE_EV' or m.get('lpn_min_share') != 0.2:
            continue
        periods = m.get('expected_periods') or []
        if not periods or periods[0] != '2025-03' or not m.get('sources_sha256') or not m.get('shadow_sha256'):
            continue
        if m.get('status') in ('COMPLETE', 'ROLLED_BACK', 'RECOVERED_TO_ORIGINAL'):
            manifests.append(m)
    if not manifests:
        return None, None
    latest = max(manifests, key=lambda m: m.get('completed_at') or m.get('updated_at') or '')
    key = 'new_sha256' if latest['status'] == 'COMPLETE' else 'old_sha256'
    return {name: t[key] for name, t in latest['targets'].items()}, latest


def check(top):
    problems = []
    entries = staged_entries(top)
    allowed, latest = None, None
    for status, path in entries:
        if path in TARGETS:
            if status.startswith('D'):
                problems.append(f'{path}: deleting dashboard EV actuals is not allowed')
                continue
            if allowed is None:
                allowed, latest = allowed_hashes(top)
            if allowed is None:
                problems.append(f'{path}: no sealed C1 release manifest authorises a change to this file')
                continue
            got = staged_sha256(top, path)
            if got != allowed.get(TARGETS[path]):
                problems.append(f'{path}: staged sha256 {got[:16]} is not the {latest["status"]} bytes of release '
                                f'{latest["release_id"]} ({allowed.get(TARGETS[path], "")[:16]})')
        elif path.startswith(BULK_PREFIXES) and os.environ.get('C1_ALLOW_BULK_STAGING') != '1':
            problems.append(f'{path}: staging production backups / DfT staging copies is blocked')
    return problems


def main():
    top = git(os.getcwd(), 'rev-parse', '--show-toplevel').decode().strip()
    problems = check(top)
    for p in problems:
        print('C1 guard:', p, file=sys.stderr)
    return 1 if problems else 0


if __name__ == '__main__':
    sys.exit(main())
