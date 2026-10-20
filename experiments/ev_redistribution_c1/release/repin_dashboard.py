"""Approved re-pin of dashboard.html for the SMMT nowcast loader (never modifies dashboard.html itself).

dashboard.html is pinned by the C1 release protections. The only accepted change is the SMMT nowcast loader
(data_manager/smmt_dashboard_patch.py) applied to the pinned page: c1_release.DASHBOARD_REPIN lists that single
old -> new hash pair and the pin log refuses any other dashboard entry. This tool checks that the working dashboard.html
is exactly that patched page, that nothing else pinned has changed and that the current C1 release verifies, and only
with the exact approval token appends a sealed, hash-chained entry to state/pins/pin_log.json.

It aborts (writes nothing) when the dashboard is not exactly the approved patched page, any other pinned file differs,
the pin log is broken, the root is the live repository while the live-release switch is off, or the token is not
exactly "REPIN dashboard.html <old16> <new16>".

Usage:
  python experiments/ev_redistribution_c1/release/repin_dashboard.py --root <dir>                 (plan only)
  python experiments/ev_redistribution_c1/release/repin_dashboard.py --root <dir> --approve "REPIN ..."
"""
import argparse
import getpass
import json
import socket
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import release_config as cfg  # noqa: E402
import c1_release as rel  # noqa: E402
import c1_quarterly_release as cq  # noqa: E402

PATH = 'dashboard.html'


class RepinRefused(rel.ReleaseError):
    code, exit_code = 'REPIN_REFUSED', 2


def plan(root):
    root, live = rel.canonical_root(root)
    pins = rel.effective_pins(root)                                   # raises if the pin log is broken
    old, new = pins[PATH], rel.sha256_file(rel.safe_path(root, PATH))
    problems, checks = [], {}
    allowed = (old, new) in rel.DASHBOARD_REPIN
    checks['approved_dashboard_change'] = allowed
    if old != new and not allowed:
        problems.append(f'dashboard.html {new[:16]} is not the approved SMMT-loader version of the pinned page {old[:16]}')
    others = {p: h for p, h in pins.items() if p != PATH}
    changed = [p for p, h in others.items() if not rel.safe_path(root, p).is_file() or rel.sha256_file(rel.safe_path(root, p)) != h]
    checks['other_pinned_files_unchanged'] = not changed
    if changed:
        problems.append(f'other pinned files changed: {changed}')
    # the live C1 state must be exactly one completed release (October C1, or a later C1-to-C1 quarterly release)
    vc = cq.verify_current(root)
    checks['current_c1_release'] = {'ok': vc['ok'], 'release_id': (vc.get('current_release') or {}).get('release_id'),
                                    'problems': vc['problems']}
    if not vc['ok']:
        problems.append(f'current C1 release does not verify: {vc["problems"]}')
    return {'root': str(root), 'live': live, 'path': PATH, 'old_sha256': old, 'new_sha256': new, 'needed': old != new,
            'valid': not problems, 'problems': problems, 'checks': checks,
            'approval_token': f'REPIN {PATH} {old[:16]} {new[:16]}'}


def repin(root, approve):
    p = plan(root)
    if p['live'] and not cfg.LIVE_RELEASE_ENABLED:
        raise rel.LiveReleaseBlocked('re-pinning the live repository is part of an approved release (LIVE_RELEASE_ENABLED=False)')
    if not p['needed']:
        raise RepinRefused('dashboard.html already matches its pin; nothing to re-pin')
    if not p['valid']:
        raise RepinRefused(f'dashboard change is not the approved SMMT loader: {p["problems"]}')
    if approve != p['approval_token']:
        raise rel.ConfirmationMismatch(f'approval must be exactly: {p["approval_token"]}')
    root_path = Path(p['root'])
    with rel.ReleaseLock(root_path, 'repin', 'c1-00000000-repin'):
        p2 = plan(root_path)                                         # re-validate under the lock
        if (p2['old_sha256'], p2['new_sha256'], p2['valid']) != (p['old_sha256'], p['new_sha256'], True):
            raise RepinRefused('state changed between approval and lock; nothing was written')
        entries = rel.read_pin_log(root_path)
        entry = {'seq': len(entries), 'path': PATH, 'old_sha256': p['old_sha256'], 'new_sha256': p['new_sha256'],
                 'approved_by': getpass.getuser(), 'host': socket.gethostname(), 'approved_at': rel.now_utc(),
                 'approval_token': approve,
                 'reason': ('SMMT provisional nowcast loader (smmt_dashboard_patch) applied'
                            if p['old_sha256'] == cfg.PINNED_SHA256[PATH] else 'SMMT dashboard change rolled back to the pinned page'),
                 'checks': p2['checks'], 'prev_entry_sha256': entries[-1]['entry_sha256'] if entries else ''}
        entry['entry_sha256'] = rel.seal_entry(entry)
        rel._makedirs(root_path, ['pins'])
        rel._write_replace(root_path, rel.safe_path(root_path, rel.PIN_LOG_REL),
                           (json.dumps(entries + [entry], indent=2, sort_keys=True) + '\n').encode('utf-8'))
        rel.read_pin_log(root_path)                                  # verify what was written
        return entry


if __name__ == '__main__':
    ap = argparse.ArgumentParser()
    ap.add_argument('--root', required=True)
    ap.add_argument('--approve', default='')
    a = ap.parse_args()
    try:
        if not a.approve:
            r = plan(a.root)
            print(json.dumps(r, indent=2, default=str))
            sys.exit(0 if r['valid'] else 2)
        e = repin(a.root, a.approve)
        print(json.dumps({k: e[k] for k in ('seq', 'path', 'old_sha256', 'new_sha256', 'approved_by', 'approved_at', 'entry_sha256')}, indent=2))
    except rel.ReleaseError as err:
        print(json.dumps({'error': err.code, 'message': str(err)}, indent=2))
        sys.exit(err.exit_code)
