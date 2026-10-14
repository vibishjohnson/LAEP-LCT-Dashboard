"""Approved re-pin of integration/metadata.json for the C1 release preflight (never modifies metadata.json itself).

A legitimate forecast refresh through the guarded integration runner rewrites metadata.json, which changes its
pinned hash and makes the release preflight fail closed. This tool validates the new metadata and the surrounding
state, and only with an exact operator approval token appends a sealed, hash-chained entry to
experiments/ev_redistribution_c1/release/state/pins/pin_log.json. c1_release.preflight then uses the re-pinned hash.

It aborts (writes nothing) when:
  * the metadata has unknown or missing keys, wrong types, or unexpected values: latest_actual_period must equal
    the latest period of the published EV actuals and the C1 reporting window; forecast_start/end must equal the
    published DNO forecast range; horizons and anchor dates must be the known values; qa_pass and
    ht_outputs_untouched must be true;
  * any monthly forecast fails the guarded runner's contract (coverage, series, duplicate keys, no actuals);
  * any other pinned file, shadow file or C1 source differs, or any preflight check other than the metadata pin fails;
  * the pin log is broken, the root is the live repository while the live-release switch is off, or the approval
    token is not exactly "REPIN <path> <old16> <new16>".

Usage:
  python experiments/ev_redistribution_c1/release/repin_metadata.py --root <dir>                 (plan only)
  python experiments/ev_redistribution_c1/release/repin_metadata.py --root <dir> --approve "REPIN ..."
"""
import argparse
import getpass
import json
import socket
import sys
from datetime import datetime
from pathlib import Path

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent))
import release_config as cfg  # noqa: E402
import c1_release as rel  # noqa: E402

sys.path.insert(0, str(cfg.REPO / 'integration_runner'))
from dashboard_integration.publish_guard import validate_forecast  # noqa: E402

PATH = f'{cfg.INTEGRATION_REL}/metadata.json'
REQUIRED = {'generated_at': str, 'latest_actual_period': str, 'forecast_start_period': str, 'forecast_end_period': str,
            'neso_horizon_years': str, 'dfes_horizon_years': str, 'neso_anchor_dates': str,
            'ht_outputs_untouched': bool, 'qa_pass': bool}
OPTIONAL = {'ev_vintage'}
EXPECTED_VALUES = {'neso_horizon_years': '2024-2050', 'dfes_horizon_years': '2024-2050',
                   'neso_anchor_dates': '2025-03-31 to 2051-03-31', 'ht_outputs_untouched': True, 'qa_pass': True}


class RepinRefused(rel.ReleaseError):
    code, exit_code = 'REPIN_REFUSED', 2


def plan(root):
    root, live = rel.canonical_root(root)
    pins = rel.effective_pins(root)                                   # raises if the pin log is broken
    meta_path = rel.safe_path(root, PATH)
    old, new = pins[PATH], rel.sha256_file(meta_path)
    problems, checks = [], {}

    try:
        meta = json.loads(meta_path.read_text(encoding='utf-8'))
    except ValueError as e:
        raise RepinRefused(f'metadata.json is not valid JSON: {e}')
    if not isinstance(meta, dict):
        raise RepinRefused('metadata.json must be a JSON object')
    unknown = sorted(set(meta) - set(REQUIRED) - OPTIONAL)
    missing = sorted(set(REQUIRED) - set(meta))
    if unknown or missing:
        problems.append(f'unexpected keys {unknown} / missing keys {missing}')
    for k, t in REQUIRED.items():
        if k in meta and not isinstance(meta[k], t):
            problems.append(f'{k} has type {type(meta[k]).__name__}, expected {t.__name__}')
    for k, v in EXPECTED_VALUES.items():
        if meta.get(k) != v:
            problems.append(f'{k}={meta.get(k)!r}, expected {v!r}')
    try:
        datetime.fromisoformat(str(meta.get('generated_at')))
    except ValueError:
        problems.append('generated_at is not an ISO timestamp')
    if meta.get('ev_vintage') not in (None,) and not (isinstance(meta.get('ev_vintage'), str) and len(meta['ev_vintage']) == 10):
        problems.append(f'ev_vintage={meta.get("ev_vintage")!r} is not null or YYYY-MM-DD')

    actual_latest = str(pd.read_csv(rel.safe_path(root, f'{cfg.INTEGRATION_REL}/ev_actuals_dno.csv'), usecols=['period']).period.max())
    checks['latest_actual_period'] = {'metadata': meta.get('latest_actual_period'), 'published_ev_actuals': actual_latest,
                                      'c1_window_end': cfg.EXPECTED_PERIODS[-1]}
    if not (meta.get('latest_actual_period') == actual_latest == cfg.EXPECTED_PERIODS[-1]):
        problems.append(f'latest_actual_period {meta.get("latest_actual_period")} does not match the published EV actuals '
                        f'({actual_latest}) and the C1 window ({cfg.EXPECTED_PERIODS[-1]})')

    forecasts = {}
    for name in ('monthly_forecast_dno.csv', 'monthly_forecast_la.csv', 'monthly_forecast_laep.csv'):
        found = validate_forecast(rel.safe_path(root, f'{cfg.INTEGRATION_REL}/{name}'), name)
        forecasts[name] = found or 'valid'
        problems += found
    checks['forecasts'] = forecasts
    dno = pd.read_csv(rel.safe_path(root, f'{cfg.INTEGRATION_REL}/monthly_forecast_dno.csv'), usecols=['period'])
    if (meta.get('forecast_start_period'), meta.get('forecast_end_period')) != (str(dno.period.min()), str(dno.period.max())):
        problems.append(f'forecast range in metadata {meta.get("forecast_start_period")}..{meta.get("forecast_end_period")} '
                        f'differs from the published forecast {dno.period.min()}..{dno.period.max()}')

    others = {p: h for p, h in pins.items() if p != PATH}
    changed = [p for p, h in others.items() if not rel.safe_path(root, p).is_file() or rel.sha256_file(rel.safe_path(root, p)) != h]
    checks['other_pinned_files_unchanged'] = not changed
    if changed:
        problems.append(f'other pinned files changed: {changed}')
    pf = rel.preflight(root)
    state = (pf.get('target_state') or {}).get('overall')
    if state == 'C1':                                                # re-pin after the release: targets are C1
        pf = rel.preflight(root, require='C1')
    failing = {c['check']: c['detail'] for c in pf['checks'] if not c['pass']}
    unexpected = {k: v for k, v in failing.items() if k != 'pinned_files'}
    if unexpected:
        problems.append(f'other preflight checks fail: {unexpected}')
    if 'pinned_files' in failing and failing['pinned_files'] != f"changed or missing: ['{PATH}']" and old != new:
        problems.append(f'pinned-file failure is not limited to metadata.json: {failing["pinned_files"]}')
    checks['preflight_failures'] = failing

    return {'root': str(root), 'live': live, 'path': PATH, 'old_sha256': old, 'new_sha256': new,
            'needed': old != new, 'valid': not problems, 'problems': problems, 'checks': checks,
            'metadata': meta, 'approval_token': f'REPIN {PATH} {old[:16]} {new[:16]}'}


def repin(root, approve):
    p = plan(root)
    if p['live'] and not cfg.LIVE_RELEASE_ENABLED:
        raise rel.LiveReleaseBlocked('re-pinning the live repository is part of an approved release (LIVE_RELEASE_ENABLED=False)')
    if not p['needed']:
        raise RepinRefused('metadata.json already matches its pin; nothing to re-pin')
    if not p['valid']:
        raise RepinRefused(f'metadata change is not a validated legitimate refresh: {p["problems"]}')
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
                 'approval_token': approve, 'checks': p2['checks'], 'metadata_snapshot': p2['metadata'],
                 'prev_entry_sha256': entries[-1]['entry_sha256'] if entries else ''}
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
            print(json.dumps({k: v for k, v in r.items() if k != 'metadata'}, indent=2, default=str))
            sys.exit(0 if r['valid'] else 2)
        e = repin(a.root, a.approve)
        print(json.dumps({k: e[k] for k in ('seq', 'path', 'old_sha256', 'new_sha256', 'approved_by', 'approved_at', 'entry_sha256')}, indent=2))
    except rel.ReleaseError as err:
        print(json.dumps({'error': err.code, 'message': str(err)}, indent=2))
        sys.exit(err.exit_code)
