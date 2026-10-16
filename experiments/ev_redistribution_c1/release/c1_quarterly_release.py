"""C1 -> C1 quarterly release: extends the C1 release tool (c1_release.py) to publish a sealed refresh candidate.

Reuses c1_release unchanged for every safety primitive: absolute/canonical roots, the symlink/junction-refusing path
resolver, release ids, the OS release lock (shared with c1_release, so the two tools never run concurrently), atomic
os.replace with retry, sealed manifests, the append-only metadata re-pin log, fault injection and the live switch
(release_config.LIVE_RELEASE_ENABLED). Expected values are never configured by hand: they are derived from a sealed
release package (built from a sealed candidate and its recorded decision), the validated geography and the quarter
list, and every one is re-validated against the files.

The release chain lives in state/releases/ next to the October Method-A-to-C1 release:
  * the October manifest (c1_release format) is read with c1_release.load_manifest and is never modified;
  * quarterly manifests (schema c1-quarterly-release/1) use the same target layout (targets.<name>.old_sha256 /
    new_sha256), so the pre-commit guard follows the latest release automatically.
The current release is the COMPLETE release whose published hashes equal the live files.

Writes (disposable roots, or the live root only with LIVE_RELEASE_ENABLED and a PRODUCTION authorisation):
  * the three EV actuals targets and integration/metadata.json (latest_actual_period only)
  * staged files  integration/.c1q<promote|rollback>-<release>-<name>.tmp
  * the release state directory (manifest, package copy, backup, protected snapshot, re-pin log)

CLI (from the repository root):
  python experiments/ev_redistribution_c1/release/c1_quarterly_release.py status         --root <dir>
  python experiments/ev_redistribution_c1/release/c1_quarterly_release.py verify-current --root <dir>
  python experiments/ev_redistribution_c1/release/c1_quarterly_release.py prepare-package --candidate-dir <dir> --out <dir>
  python experiments/ev_redistribution_c1/release/c1_quarterly_release.py preflight --root <dir> --package <dir> --authorisation <json> --release-id <id>
  python experiments/ev_redistribution_c1/release/c1_quarterly_release.py promote   --root <dir> --package <dir> --authorisation <json> --release-id <id> --confirm "PROMOTE <id> <digest16>"
  python experiments/ev_redistribution_c1/release/c1_quarterly_release.py rollback  --root <dir> --release-id <id> --confirm "ROLLBACK <id> <manifest16>"
  python experiments/ev_redistribution_c1/release/c1_quarterly_release.py recover   --root <dir> --release-id <id> --confirm "RECOVER <id>"
"""
import argparse
import getpass
import hashlib
import io
import json
import os
import re
import shutil
import socket
import sys
from pathlib import Path

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import release_config as cfg  # noqa: E402
import c1_release as rel  # noqa: E402
import integration_writer as iw  # noqa: E402

SCHEMA = 'c1-quarterly-release/1'
PACKAGE_SCHEMA = 'c1-release-package/1'
AUTH_SCHEMA = 'c1-release-authorisation/1'
FIRST_PERIOD = '2025-03'                      # permanent: C1 history starts in March 2025 and only grows
METADATA = 'metadata.json'
ALL_TARGETS = tuple(cfg.TARGET_NAMES) + (METADATA,)
QSTAGED_RE = re.compile(r'^\.c1q(promote|rollback)-(c1-\d{8}-[a-z0-9][a-z0-9-]{0,39})-(ev_actuals_(?:dno|la|laep)\.csv|metadata\.json)\.tmp$')
PACKAGE_FILES = ('payload/ev_actuals_dno.csv', 'payload/ev_actuals_la.csv', 'payload/ev_actuals_laep.csv', 'candidate_manifest.json',
                 'source_set_manifest.json', 'decision_record.json', 'reports/restatement_geography.csv', 'reports/summary.json',
                 'reports/qa_checks.csv')
OPTIONS = {'B': 'RESTATED'}                   # the only option this phase can package; A needs its own decision and package
TOOL_FILES = [Path(__file__).resolve(), *rel.TOOL_FILES]


class QuarterlyError(rel.ReleaseError):
    code = 'QUARTERLY_RELEASE_ERROR'


class PackageInvalid(rel.ReleaseError):
    code, exit_code = 'PACKAGE_INVALID', 4


class AuthorisationInvalid(rel.ReleaseError):
    code, exit_code = 'AUTHORISATION_INVALID', 4


# ------------------------------------------------------------------ hashing helpers shared with the candidate packages
def _json_seal(obj, exclude):
    body = {k: v for k, v in obj.items() if k != exclude}
    return hashlib.sha256(json.dumps(body, sort_keys=True, default=str).encode('utf-8')).hexdigest()


def candidate_seal(man):
    """Seal algorithm of data_manager.c1_refresh (sources.seal): sha256 of the sorted JSON without seal_sha256."""
    return _json_seal(man, 'seal_sha256')


def decision_record_hash(rec):
    """data_manager.c1_refresh.store record hash: seal of the record without record_sha256."""
    return _json_seal({k: v for k, v in rec.items() if k != 'record_sha256'}, 'seal_sha256')


def next_period(p):
    y, m = int(p[:4]), int(p[5:7])
    m += 3
    return f'{y + (m > 12)}-{(m - 1) % 12 + 1:02d}'


def expanding_ok(periods):
    return bool(periods) and periods[0] == FIRST_PERIOD and all(b == next_period(a) for a, b in zip(periods, periods[1:]))


# ------------------------------------------------------------------ write allowlist (c1_release primitives + metadata.json)
def q_target_path(root, name):
    if name not in ALL_TARGETS:
        raise rel.UnsafePath(f'not an allowlisted quarterly target: {name}')
    return rel.safe_path(root, f'{cfg.INTEGRATION_REL}/{name}')


def q_staged_path(root, kind, release_id, name):
    rel.check_release_id(release_id)
    if kind not in ('promote', 'rollback') or name not in ALL_TARGETS:
        raise rel.UnsafePath('bad staged file request')
    return rel.safe_path(root, f'{cfg.INTEGRATION_REL}/.c1q{kind}-{release_id}-{name}.tmp')


def q_writable(root, path):
    path = Path(path)
    integration = rel.safe_path(root, cfg.INTEGRATION_REL)
    if rel._same(path.parent, integration) and (path.name in ALL_TARGETS or QSTAGED_RE.match(path.name)):
        return rel.safe_path(root, f'{cfg.INTEGRATION_REL}/{path.name}')
    return rel.assert_writable(root, path)          # the state directory, or refusal


def q_write_new(root, path, data):
    path = q_writable(root, path)
    with open(path, 'xb') as f:
        f.write(data)
        f.flush()
        os.fsync(f.fileno())
    return path


def q_remove(root, path):
    path = q_writable(root, path)
    if path.name in ALL_TARGETS and rel._same(path.parent, rel.safe_path(root, cfg.INTEGRATION_REL)):
        raise rel.UnsafePath('targets are never deleted')
    if path.exists():
        os.remove(path)


def q_clean_staged(root, release_id):
    for p in rel.safe_path(root, cfg.INTEGRATION_REL).iterdir():
        m = QSTAGED_RE.match(p.name)
        if m and m.group(2) == release_id:
            q_remove(root, p)


# ------------------------------------------------------------------ release chain
def load_release(root, release_id):
    """Any release in the chain: quarterly manifests are validated here, the October release by c1_release."""
    rel.check_release_id(release_id)
    p = rel.manifest_path(root, release_id)
    if not p.exists():
        raise rel.ManifestInvalid(f'no release manifest for {release_id}')
    try:
        raw = json.loads(p.read_text(encoding='utf-8'))
    except ValueError as e:
        raise rel.ManifestInvalid(f'release manifest is not valid JSON: {e}')
    if raw.get('schema') != SCHEMA:
        m = rel.load_manifest(root, release_id)
        m.setdefault('periods', list(cfg.EXPECTED_PERIODS))
        m['_kind'] = 'METHOD_A_TO_C1'
        return m
    if raw.get('manifest_sha256') != rel.seal(raw):
        raise rel.ManifestInvalid('quarterly release manifest integrity check failed')
    need = {'release_id', 'status', 'targets', 'metadata_target', 'methodology_id', 'lpn_min_share', 'periods', 'previous_release_id',
            'previous_manifest_sha256', 'candidate_id', 'package'}
    if not need <= set(raw) or raw['release_id'] != release_id or set(raw['targets']) != set(cfg.TARGET_NAMES):
        raise rel.ManifestInvalid('quarterly release manifest is missing fields, names another release or has other targets')
    for name, t in list(raw['targets'].items()) + [(METADATA, raw['metadata_target'])]:
        if not all(re.fullmatch(r'[0-9a-f]{64}', str(t.get(k))) for k in ('old_sha256', 'new_sha256')):
            raise rel.ManifestInvalid(f'quarterly release manifest hashes invalid for {name}')
    raw['_kind'] = 'C1_TO_C1'
    return raw


def release_chain(root):
    d = rel.state_path(root, 'releases')
    out = []
    if not d.exists():
        return out
    for sub in sorted(d.iterdir()):
        if not sub.is_dir() or rel._is_reparse(sub):
            out.append({'release_id': sub.name, 'status': 'INVALID_ENTRY'})
            continue
        try:
            m = load_release(root, sub.name)
            out.append({'release_id': sub.name, 'status': m['status'], 'kind': m['_kind'], 'started_at': m.get('started_at'),
                        'completed_at': m.get('completed_at'), 'manifest_sha256': m['manifest_sha256'], 'periods': m.get('periods'),
                        'previous_release_id': m.get('previous_release_id')})
        except rel.ReleaseError as e:
            st = 'NOT_STARTED' if not (sub / 'release_manifest.json').exists() else 'MANIFEST_INVALID'
            out.append({'release_id': sub.name, 'status': st, 'error': str(e)})
    return out


def live_hashes(root):
    return {n: (rel.sha256_file(q_target_path(root, n)) if q_target_path(root, n).is_file() else None) for n in ALL_TARGETS}


def current_release(root, hashes=None):
    """The single COMPLETE release whose published EV actuals equal the live files, or an error."""
    hashes = hashes or live_hashes(root)
    chain = release_chain(root)
    bad = [r for r in chain if r['status'] in rel.INCOMPLETE or r['status'] in ('MANIFEST_INVALID', 'INVALID_ENTRY')]
    if bad:
        raise QuarterlyError(f'release chain has incomplete or invalid entries: {bad}')
    matches = []
    for r in chain:
        if r['status'] != 'COMPLETE':
            continue
        m = load_release(root, r['release_id'])
        if all(m['targets'][n]['new_sha256'] == hashes[n] for n in cfg.TARGET_NAMES):
            matches.append(m)
    if len(matches) != 1:
        raise QuarterlyError(f'live EV actuals match {len(matches)} completed releases (expected exactly one); '
                             f'live {[h[:12] if h else None for h in hashes.values()]}')
    return matches[0]


# ------------------------------------------------------------------ release packages (built outside the root)
def _sha(path):
    return rel.sha256_file(path)


def prepare_package(candidate_dir, out_dir):
    """Build a sealed release package for option B from a sealed candidate and its latest recorded decision."""
    cdir, out = Path(candidate_dir), Path(out_dir)
    man = json.loads((cdir / 'manifest.json').read_text(encoding='utf-8'))
    if man.get('seal_sha256') != candidate_seal(man):
        raise PackageInvalid('candidate manifest seal is invalid')
    for relp, h in man['files_sha256'].items():
        if _sha(cdir.joinpath(*relp.split('/'))) != h:
            raise PackageInvalid(f'candidate file changed: {relp}')
    if not man['qa']['all_passed']:
        raise PackageInvalid('candidate QA did not pass')
    cid = man['candidate_id']
    ws_cand = cdir.parent
    dec_path = ws_cand / f'{cid}.decisions.jsonl'
    state = json.loads((ws_cand / f'{cid}.state.json').read_text(encoding='utf-8'))
    decisions = [json.loads(l) for l in dec_path.read_text(encoding='utf-8').splitlines() if l.strip()] if dec_path.exists() else []
    if not decisions or state['state'] != 'APPROVED_FOR_FUTURE_RELEASE':
        raise PackageInvalid('the candidate has no recorded approval for a future release')
    prev = None
    for d in decisions:
        if d['previous_record_sha256'] != prev or d['record_sha256'] != decision_record_hash(d):
            raise PackageInvalid('candidate decision log failed its hash-chain check')
        prev = d['record_sha256']
    dec = decisions[-1]
    if dec['option'] not in OPTIONS:
        raise PackageInvalid(f"the recorded decision is option {dec['option']} ({dec['option_name']}); this tool packages only "
                             'option B, which needs its own recorded decision')
    report = man['files_sha256']['reports/restatement_geography.csv']
    ra = dec.get('restatement_approval') or {}
    if not (ra.get('approved') and (ra.get('reason') or '').strip() and ra.get('report_sha256') == report and dec['restatement_report_sha256'] == report):
        raise PackageInvalid('option B decision lacks an explicit restatement approval bound to this restatement report')
    if dec['candidate_id'] != cid or dec['source_set_id'] != man['source_set_id'] or dec['candidate_seal_sha256'] != man['seal_sha256']:
        raise PackageInvalid('decision record names another candidate, source set or candidate version')
    expected = man['publication_options']['B_RESTATED']['expected_production_sha256']
    if dec['expected_production_sha256'] != expected:
        raise PackageInvalid('decision record expected hashes differ from the candidate')
    ws_root = ws_cand.parent
    ss_path = ws_root / 'source_sets' / man['source_set_id'] / 'manifest.json'
    ss = json.loads(ss_path.read_text(encoding='utf-8'))
    if ss.get('seal_sha256') != candidate_seal(ss) or ss['seal_sha256'] != man['source_set_seal'] or ss['source']['sha256'] != man['input_sha256']['VEH0135']:
        raise PackageInvalid('source set manifest does not match the candidate')
    summary = json.loads((cdir / 'reports' / 'summary.json').read_text(encoding='utf-8'))
    pkg_id = f'{cid}-B'
    if out.exists() and any(out.iterdir()):
        raise PackageInvalid(f'package directory already exists and is not empty: {out}')
    (out / 'payload').mkdir(parents=True, exist_ok=True)
    (out / 'reports').mkdir(exist_ok=True)
    copies = {f'payload/{n}': cdir / 'integration' / 'restated' / n for n in cfg.TARGET_NAMES}
    copies.update({'candidate_manifest.json': cdir / 'manifest.json', 'source_set_manifest.json': ss_path,
                   'reports/restatement_geography.csv': cdir / 'reports' / 'restatement_geography.csv',
                   'reports/summary.json': cdir / 'reports' / 'summary.json', 'reports/qa_checks.csv': cdir / 'reports' / 'qa_checks.csv'})
    for dst, src in copies.items():
        shutil.copyfile(src, out.joinpath(*dst.split('/')))
    (out / 'decision_record.json').write_text(json.dumps(dec, indent=2, sort_keys=True), encoding='utf-8')
    files = {f: _sha(out.joinpath(*f.split('/'))) for f in PACKAGE_FILES}
    if any(files[f'payload/{n}'] != expected[n] for n in cfg.TARGET_NAMES):
        raise PackageInvalid('payload copy does not match the candidate expected hashes')
    pm = {'schema': PACKAGE_SCHEMA, 'package_id': pkg_id, 'created_at': rel.now_utc(), 'option': 'B', 'option_name': OPTIONS['B'],
          'candidate_id': cid, 'candidate_seal_sha256': man['seal_sha256'], 'source_set_id': man['source_set_id'],
          'source_set_seal_sha256': ss['seal_sha256'], 'source_sha256': ss['source']['sha256'], 'source_bytes': ss['source']['size_bytes'],
          'methodology_id': man['methodology']['methodology_id'], 'lpn_min_share': man['methodology']['lpn_min_share'],
          'periods': man['periods'], 'new_period': man['new_period'],
          'previous_release_id': man['live_release']['release_id'], 'previous_manifest_sha256': man['live_release']['manifest_sha256'],
          'previous_target_sha256': man['live_release']['target_sha256'], 'expected_target_sha256': expected,
          'expected_totals': summary['latest_vintage_series'], 'restatement_report_sha256': report,
          'decision_record_sha256': dec['record_sha256'], 'decided_by': dec['decided_by'], 'decided_at': dec['decided_at'],
          'files_sha256': files, 'status': 'PREPARED (not authorised for production)'}
    pm['seal_sha256'] = candidate_seal(pm)
    (out / 'package_manifest.json').write_text(json.dumps(pm, indent=2, sort_keys=True), encoding='utf-8')
    for p in out.rglob('*'):
        if p.is_file():
            os.chmod(p, 0o444)
    return pm


def load_package(pkg_dir):
    """Re-verify a package completely (seal, files, embedded candidate seal, decision record, bindings)."""
    d = Path(pkg_dir)
    pm = json.loads((d / 'package_manifest.json').read_text(encoding='utf-8'))
    if pm.get('schema') != PACKAGE_SCHEMA or pm.get('seal_sha256') != candidate_seal(pm):
        raise PackageInvalid('package manifest seal is invalid')
    if set(pm['files_sha256']) != set(PACKAGE_FILES):
        raise PackageInvalid('package file list is not the expected set')
    for f, h in pm['files_sha256'].items():
        p = d.joinpath(*f.split('/'))
        if not p.is_file() or _sha(p) != h:
            raise PackageInvalid(f'package file missing or changed: {f}')
    man = json.loads((d / 'candidate_manifest.json').read_text(encoding='utf-8'))
    ss = json.loads((d / 'source_set_manifest.json').read_text(encoding='utf-8'))
    dec = json.loads((d / 'decision_record.json').read_text(encoding='utf-8'))
    problems = []
    if man.get('seal_sha256') != candidate_seal(man) or man['seal_sha256'] != pm['candidate_seal_sha256'] or man['candidate_id'] != pm['candidate_id']:
        problems.append('embedded candidate manifest does not match')
    if ss.get('seal_sha256') != candidate_seal(ss) or ss['seal_sha256'] != pm['source_set_seal_sha256'] or ss['source_set_id'] != pm['source_set_id']:
        problems.append('embedded source set does not match')
    if ss['source']['sha256'] != man['input_sha256']['VEH0135'] or ss['source']['sha256'] != pm['source_sha256']:
        problems.append('source hash chain broken (source set / candidate / package)')
    for n in cfg.TARGET_NAMES:
        if pm['files_sha256'][f'payload/{n}'] != man['publication_options']['B_RESTATED']['expected_production_sha256'][n] or \
                pm['expected_target_sha256'][n] != pm['files_sha256'][f'payload/{n}']:
            problems.append(f'payload {n} is not the candidate option B output')
    if pm['files_sha256']['reports/restatement_geography.csv'] != man['files_sha256']['reports/restatement_geography.csv'] or \
            pm['restatement_report_sha256'] != man['files_sha256']['reports/restatement_geography.csv']:
        problems.append('restatement report is not the candidate report')
    ra = dec.get('restatement_approval') or {}
    if (dec.get('record_sha256') != decision_record_hash(dec) or dec['record_sha256'] != pm['decision_record_sha256'] or dec['option'] != 'B'
            or dec['candidate_id'] != pm['candidate_id'] or dec['source_set_id'] != pm['source_set_id']
            or dec['candidate_seal_sha256'] != pm['candidate_seal_sha256'] or not ra.get('approved')
            or ra.get('report_sha256') != pm['restatement_report_sha256'] or not (ra.get('reason') or '').strip()):
        problems.append('decision record is not a valid option B restatement approval for this candidate')
    if man['methodology']['methodology_id'] != pm['methodology_id'] or man['methodology']['lpn_min_share'] != pm['lpn_min_share']:
        problems.append('methodology in package and candidate differ')
    if problems:
        raise PackageInvalid('; '.join(problems))
    pm['_dir'] = str(d)
    return pm


def load_authorisation(path):
    a = json.loads(Path(path).read_text(encoding='utf-8'))
    a['_sha256'] = _sha(path)
    return a


def check_authorisation(a, pm, release_id, live):
    """Binding of the release authorisation to this exact release, package, candidate, source set and report."""
    need = {'schema': AUTH_SCHEMA, 'release_id': release_id, 'package_id': pm['package_id'], 'package_seal_sha256': pm['seal_sha256'],
            'candidate_id': pm['candidate_id'], 'source_set_id': pm['source_set_id'], 'option': 'B',
            'restatement_report_sha256': pm['restatement_report_sha256'], 'decision_record_sha256': pm['decision_record_sha256'],
            'previous_release_id': pm['previous_release_id']}
    bad = [k for k, v in need.items() if a.get(k) != v]
    if not (a.get('release_approver') or '').strip() or not (a.get('approval_reference') or '').strip() or not a.get('approved_at'):
        bad.append('release_approver/approval_reference/approved_at')
    if a.get('acknowledged_revised_history') is not True:
        bad.append('acknowledged_revised_history')
    kind = a.get('kind')
    if live and kind != 'PRODUCTION':
        bad.append(f'kind {kind!r} cannot authorise a live release')
    if not live and kind not in ('DRY_RUN', 'PRODUCTION'):
        bad.append(f'kind {kind!r}')
    if live and a.get('outstanding_governance'):
        bad.append(f"outstanding governance items: {a['outstanding_governance']}")
    if kind == 'PRODUCTION' and not (a.get('release_operator') or '').strip():
        bad.append('release_operator (a PRODUCTION authorisation names the person running the release)')
    if kind == 'PRODUCTION' and a.get('target_allowlist') != [f'{cfg.INTEGRATION_REL}/{n}' for n in ALL_TARGETS]:
        bad.append('target_allowlist must list exactly the three EV actuals files and metadata.json')
    if bad:
        raise AuthorisationInvalid(f'release authorisation does not authorise this release: {bad}')
    return True


# ------------------------------------------------------------------ content validation (structure derived, not hardcoded)
def expected_rows(canon, n_periods):
    las, laeps = canon.LAD22CD.nunique(), canon.LAEP.dropna().nunique()
    return {'ev_actuals_dno.csv': 3 * 2 * n_periods, 'ev_actuals_la.csv': las * 2 * n_periods, 'ev_actuals_laep.csv': laeps * 2 * n_periods}


def validate_series(files, canon, geography, periods, expected_totals=None):
    """Structural and accounting checks for a C1 series of any length (periods from March 2025, no gaps)."""
    fails = []
    if not expanding_ok(periods):
        fails.append(f'periods {periods} are not the expanding March-2025 series without gaps')
    frames = {}
    for n in cfg.TARGET_NAMES:
        try:
            frames[n] = pd.read_csv(io.BytesIO(files[n]))
        except Exception as e:  # noqa: BLE001
            fails.append(f'{n}: unreadable ({e})')
    if fails:
        return fails
    rows = expected_rows(canon, len(periods))
    cols = {'ev_actuals_dno.csv': iw.DNO_COLUMNS, 'ev_actuals_la.csv': iw.LA_COLUMNS, 'ev_actuals_laep.csv': iw.LAEP_COLUMNS}
    keys = {'ev_actuals_dno.csv': ['period', 'DNO', 'EV_Type'], 'ev_actuals_la.csv': ['period', 'LAD22CD', 'EV_Type'],
            'ev_actuals_laep.csv': ['period', 'LAEP', 'EV_Type']}
    for n, f in frames.items():
        if list(f.columns) != cols[n]:
            fails.append(f'{n}: columns differ from the production schema')
            continue
        if len(f) != rows[n]:
            fails.append(f'{n}: {len(f)} rows, expected {rows[n]} ({len(periods)} quarters)')
        if sorted(f.period.astype(str).unique()) != list(periods):
            fails.append(f'{n}: periods {sorted(f.period.astype(str).unique())} != {periods}')
        if f.duplicated(keys[n]).any() or f[keys[n]].isna().any().any():
            fails.append(f'{n}: duplicate or null keys')
        if set(f.EV_Type) != {'BEV', 'PHEV'}:
            fails.append(f'{n}: EV_Type {sorted(set(f.EV_Type))}')
        v = f['install_count' if 'install_count' in f else 'value']
        if not pd.api.types.is_integer_dtype(v) or (v < 0).any():
            fails.append(f'{n}: stock must be non-negative integers')
    if fails:
        return fails
    dno, la, laep = (frames[n] for n in cfg.TARGET_NAMES)
    if set(dno.DNO) != {'EPN', 'LPN', 'SPN'} or set(la.LAD22CD) != set(canon.LAD22CD) or set(laep.LAEP) != set(canon.LAEP.dropna()):
        fails.append('geography coverage differs from the canonical UKPN geography')
    by = lambda f, v: f.groupby(['period', 'EV_Type'])[v].sum()
    if not by(dno, 'install_count').equals(by(la, 'value')):
        fails.append('LA totals do not reconcile with DNO totals')
    lad_laep = geography.dropna(subset=['LAEP']).drop_duplicates('LAD22CD').set_index('LAD22CD').LAEP
    via = la[la.LAD22CD.isin(lad_laep.index)].assign(LAEP=lambda x: x.LAD22CD.map(lad_laep)).groupby(['period', 'LAEP', 'EV_Type']).value.sum()
    if not via.sort_index().equals(laep.set_index(['period', 'LAEP', 'EV_Type']).value.sort_index()):
        fails.append('LAEP values are not the sum of their mapped LAs')
    for per, exp in (expected_totals or {}).items():
        d = dno[dno.period == per]
        got = {'UKPN': int(d.install_count.sum()), **{k: int(d[d.DNO == k].install_count.sum()) for k in ('EPN', 'LPN', 'SPN')},
               **{k: int(d[d.EV_Type == k].install_count.sum()) for k in ('BEV', 'PHEV')}}
        if got != {k: int(v) for k, v in exp.items()}:
            fails.append(f'{per}: totals {got} != candidate {exp}')
    return fails


def restatement_check(report_bytes, live_files, payload, hist_periods, new_period):
    """Every revised historical value: published == live file, recalculated == payload, diff consistent; new quarter excluded."""
    r = pd.read_csv(io.BytesIO(report_bytes))
    fails = []
    if sorted(r.period.unique()) != sorted(hist_periods) or new_period in set(r.period):
        fails.append(f'report periods {sorted(r.period.unique())} != historical {hist_periods}')
    spec = {'DNO': ('ev_actuals_dno.csv', 'DNO', 'install_count'), 'LA': ('ev_actuals_la.csv', 'LAD22CD', 'value'),
            'LAEP': ('ev_actuals_laep.csv', 'LAEP', 'value')}
    compared = revised = 0
    for level, (n, key, val) in spec.items():
        live = pd.read_csv(io.BytesIO(live_files[n])).set_index(['period', key, 'EV_Type'])[val]
        new = pd.read_csv(io.BytesIO(payload[n]))
        new = new[new.period.isin(hist_periods)].set_index(['period', key, 'EV_Type'])[val]
        rr = r[(r.level == level) & r.EV_Type.isin(['BEV', 'PHEV'])].set_index(['period', 'geography', 'EV_Type'])
        rr.index.names = ['period', key, 'EV_Type']
        if len(rr) != len(live) or set(rr.index) != set(live.index) or set(rr.index) != set(new.index):
            fails.append(f'{level}: report rows do not cover exactly the published keys')
            continue
        pub_ok = (rr.published == live.reindex(rr.index)).all()
        rec_ok = (rr.recalculated == new.reindex(rr.index)).all()
        diff_ok = ((rr.recalculated - rr.published) == rr['diff']).all()
        if not (pub_ok and rec_ok and diff_ok):
            fails.append(f'{level}: published {pub_ok}, recalculated {rec_ok}, diff {diff_ok}')
        compared += len(rr)
        revised += int((rr['diff'] != 0).sum())
    uk = r[(r.level == 'UKPN') & r.EV_Type.isin(['BEV', 'PHEV'])].set_index(['period', 'EV_Type'])
    dno = r[(r.level == 'DNO') & r.EV_Type.isin(['BEV', 'PHEV'])].groupby(['period', 'EV_Type'])[['published', 'recalculated']].sum()
    if not (uk[['published', 'recalculated']].sort_index() == dno.sort_index()).all().all():
        fails.append('UKPN rows are not the sum of the DNO rows')
    return fails, {'values_compared': compared, 'values_revised': revised}


# ------------------------------------------------------------------ metadata (latest_actual_period only)
def metadata_update(current_bytes, old_period, new_period):
    needle = f'"latest_actual_period": "{old_period}"'.encode()
    if current_bytes.count(needle) != 1:
        raise QuarterlyError(f'metadata.json latest_actual_period is not exactly {old_period!r}')
    new = current_bytes.replace(needle, f'"latest_actual_period": "{new_period}"'.encode())
    a, b = json.loads(current_bytes), json.loads(new)
    if {k: v for k, v in a.items() if k != 'latest_actual_period'} != {k: v for k, v in b.items() if k != 'latest_actual_period'}:
        raise QuarterlyError('metadata update would change more than latest_actual_period')
    return new


def _append_pin(root, old, new, release_id, reason):
    """Record the approved metadata.json hash change in the c1_release re-pin log (append-only, hash-chained)."""
    entries = rel.read_pin_log(root)
    pins = rel.effective_pins(root)
    path = f'{cfg.INTEGRATION_REL}/metadata.json'
    if pins[path] == new:
        return None
    if pins[path] != old:
        raise QuarterlyError(f'metadata pin is {pins[path][:12]}, expected {old[:12]} before re-pinning')
    entry = {'seq': len(entries), 'path': path, 'old_sha256': old, 'new_sha256': new, 'approved_by': getpass.getuser(),
             'host': socket.gethostname(), 'approved_at': rel.now_utc(), 'reason': reason, 'release_id': release_id,
             'prev_entry_sha256': entries[-1]['entry_sha256'] if entries else ''}
    entry['entry_sha256'] = rel.seal_entry(entry)
    rel._makedirs(root, ['pins'])
    rel._write_replace(root, rel.state_path(root, 'pins', 'pin_log.json'), json.dumps(entries + [entry], indent=2).encode('utf-8'))
    rel.effective_pins(root)                    # re-verify the chain
    return entry


# ------------------------------------------------------------------ preflight (read-only)
def preflight(root, package_dir, authorisation_path, release_id):
    root, live = rel.canonical_root(root)
    checks, facts = [], {}

    def check(name, fn):
        try:
            ok, detail = fn()
        except Exception as e:  # noqa: BLE001 - fail closed
            ok, detail = False, f'{type(e).__name__}: {e}'
        checks.append({'check': name, 'pass': bool(ok), 'detail': detail})
        return ok

    def ids():
        rel.check_release_id(release_id)
        used = rel.state_path(root, 'releases', release_id).exists() or rel.state_path(root, 'backups', release_id).exists()
        return not used, 'release id unused' if not used else f'release id {release_id} has been used before'

    def paths():
        for n in ALL_TARGETS:
            if not q_target_path(root, n).is_file():
                return False, f'{n} missing'
        rel.safe_path(root, 'lookups')
        return True, 'targets and metadata present; no symlink/junction below the root'

    def chain():
        facts['hashes'] = live_hashes(root)
        cur = current_release(root, facts['hashes'])
        facts['current'] = cur
        return True, f"current release {cur['release_id']} ({cur['_kind']}, {len(cur['periods'])} quarters, {cur['periods'][-1]})"

    def package():
        facts['pm'] = load_package(package_dir)
        return True, f"package {facts['pm']['package_id']} verified (seal, {len(facts['pm']['files_sha256'])} files, candidate, source set, decision)"

    def starting_state():
        pm, cur = facts['pm'], facts['current']
        ok = (pm['previous_release_id'] == cur['release_id'] and pm['previous_manifest_sha256'] == cur['manifest_sha256']
              and all(pm['previous_target_sha256'][n] == facts['hashes'][n] for n in cfg.TARGET_NAMES))
        return ok, (f"package continues from {cur['release_id']}" if ok else
                    f"package expects {pm['previous_release_id']} ({pm['previous_manifest_sha256'][:12]}), live is {cur['release_id']} ({cur['manifest_sha256'][:12]})")

    def authorisation():
        a = load_authorisation(authorisation_path)
        facts['auth'] = a
        check_authorisation(a, facts['pm'], release_id, live)
        return True, f"{a['kind']} authorisation by {a['release_approver']} ({a['approval_reference']}), sha {a['_sha256'][:12]}"

    def live_switch():
        if live and not cfg.LIVE_RELEASE_ENABLED:
            return False, 'LIVE_RELEASE_ENABLED is False: the live repository cannot be released'
        return True, 'disposable root' if not live else 'live release enabled'

    def methodology():
        pm, cur = facts['pm'], facts['current']
        ok = pm['methodology_id'] == cur['methodology_id'] == cfg.METHODOLOGY_ID and pm['lpn_min_share'] == cur['lpn_min_share'] == cfg.LPN_MIN_SHARE
        return ok, f"{pm['methodology_id']} / LPN floor {pm['lpn_min_share']} (current {cur['methodology_id']} / {cur['lpn_min_share']})"

    def window():
        pm, cur = facts['pm'], facts['current']
        ok = pm['periods'] == list(cur['periods']) + [next_period(cur['periods'][-1])] and expanding_ok(pm['periods']) and pm['new_period'] == pm['periods'][-1]
        return ok, f"{cur['periods'][0]}..{cur['periods'][-1]} ({len(cur['periods'])}) -> {pm['periods'][0]}..{pm['periods'][-1]} ({len(pm['periods'])})"

    def structure():
        pm = facts['pm']
        facts['payload'] = {n: Path(pm['_dir'], 'payload', n).read_bytes() for n in cfg.TARGET_NAMES}
        canon = rel.load_canon(root)
        geo = pd.read_csv(rel.safe_path(root, f'{cfg.INTEGRATION_REL}/geography_lookup.csv'))
        facts['canon'], facts['geo'] = canon, geo
        fails = validate_series(facts['payload'], canon, geo, pm['periods'], pm['expected_totals'])
        rows = expected_rows(canon, len(pm['periods']))
        return not fails, fails or f'rows {rows}, keys, coverage, DNO=LA, LAEP=sum(LA), totals = candidate for {len(pm["periods"])} quarters'

    def current_structure():
        files = {n: q_target_path(root, n).read_bytes() for n in cfg.TARGET_NAMES}
        facts['live_files'] = files
        fails = validate_series(files, facts['canon'], facts['geo'], facts['current']['periods'])
        return not fails, fails or 'live series valid for the current release'

    def restatement():
        pm = facts['pm']
        rb = Path(pm['_dir'], 'reports', 'restatement_geography.csv').read_bytes()
        fails, stats = restatement_check(rb, facts['live_files'], facts['payload'], facts['current']['periods'], pm['new_period'])
        facts['restatement'] = stats
        return not fails, fails or f"{stats['values_revised']} of {stats['values_compared']} published values revised, all as reported"

    def metadata():
        cur = facts['current']
        data = q_target_path(root, METADATA).read_bytes()
        new = metadata_update(data, cur['periods'][-1], facts['pm']['new_period'])
        facts['metadata'] = {'old': rel.sha256_bytes(data), 'new': rel.sha256_bytes(new), 'new_bytes': new}
        return True, f"latest_actual_period {cur['periods'][-1]} -> {facts['pm']['new_period']} (only field changed)"

    def pinned():
        pins = rel.effective_pins(root)
        facts['pins'] = pins
        bad = [r for r, h in pins.items() if not rel.safe_path(root, r).is_file() or rel.sha256_file(rel.safe_path(root, r)) != h]
        return not bad, f'changed or missing pinned files: {bad}' if bad else f'{len(pins)} pinned files match ({len(rel.read_pin_log(root))} approved re-pins)'

    def leftovers():
        left = [p.name for p in rel.safe_path(root, cfg.INTEGRATION_REL).iterdir() if p.name.startswith('.c1')]
        return not left, f'staged leftovers: {left}' if left else 'no staged files'

    def changes():
        pm = facts['pm']
        same = [n for n in cfg.TARGET_NAMES if pm['expected_target_sha256'][n] == facts['hashes'][n]]
        return not same, f'targets already hold the new bytes: {same}' if same else 'all three targets change'

    def protected():
        facts['protected'] = rel.protected_snapshot(root)
        return True, f"{len(facts['protected'])} protected files hashed"

    steps = [('release_id', ids), ('paths_safe', paths), ('live_switch', live_switch), ('release_chain', chain), ('package', package),
             ('starting_state', starting_state), ('authorisation', authorisation), ('methodology', methodology), ('expanding_window', window),
             ('candidate_structure', structure), ('current_structure', current_structure), ('restatement_accounting', restatement),
             ('metadata_latest_actual', metadata), ('pinned_files', pinned), ('targets_change', changes), ('no_staged_leftovers', leftovers),
             ('protected_snapshot', protected)]
    deps = {'starting_state': ('current', 'pm'), 'authorisation': ('pm',), 'methodology': ('current', 'pm'), 'expanding_window': ('current', 'pm'),
            'candidate_structure': ('pm',), 'current_structure': ('canon', 'current'), 'restatement_accounting': ('payload', 'live_files', 'pm'),
            'metadata_latest_actual': ('current', 'pm'), 'targets_change': ('pm', 'hashes')}
    for name, fn in steps:
        missing = [d for d in deps.get(name, ()) if d not in facts]
        if missing:
            checks.append({'check': name, 'pass': False, 'detail': f'skipped: depends on failed {missing}'})
            continue
        check(name, fn)
    ok = all(c['pass'] for c in checks)
    body = {'checks': [(c['check'], c['pass']) for c in checks], 'hashes': facts.get('hashes'), 'protected': facts.get('protected'),
            'package_seal': (facts.get('pm') or {}).get('seal_sha256'), 'auth': (facts.get('auth') or {}).get('_sha256'),
            'release_id': release_id, 'pins': facts.get('pins'), 'pin_log': rel.pin_log_sha256(root),
            'metadata_new': (facts.get('metadata') or {}).get('new')}
    digest = rel.sha256_bytes(json.dumps(body, sort_keys=True).encode('utf-8'))
    return {'ok': ok, 'root': str(root), 'live': live, 'release_id': release_id, 'checks': checks, 'digest': digest, '_facts': facts}


def _public(pf):
    return {k: v for k, v in pf.items() if k != '_facts'}


# ------------------------------------------------------------------ backup / restore (four files)
def _backup(root, live, release_id, old, manifest):
    rel._makedirs(root, ['backups'])
    bdir = rel.state_path(root, 'backups', release_id)
    os.mkdir(bdir)
    files = {}
    for n in ALL_TARGETS:
        data = q_target_path(root, n).read_bytes()
        if rel.sha256_bytes(data) != old[n]:
            raise rel.BackupVerificationFailed(f'{n} changed since preflight (backup refused)')
        rel._write_new(root, bdir / n, data)
        files[n] = {'sha256': old[n], 'bytes': len(data)}
    rel._fault(live, 'q_after_backup_copy', {'corrupt_backup': lambda: (bdir / cfg.TARGET_NAMES[0]).write_bytes(b'X' + (bdir / cfg.TARGET_NAMES[0]).read_bytes()[1:])})
    bm = {'release_id': release_id, 'created_at': rel.now_utc(), 'kind': 'pre-release backup of the three EV actuals targets and metadata.json',
          'previous_release_id': manifest['previous_release_id'], 'files': files}
    bm['manifest_sha256'] = rel.seal(bm)
    rel._write_new(root, bdir / 'backup_manifest.json', (json.dumps(bm, indent=2, sort_keys=True) + '\n').encode('utf-8'))
    return verify_backup(root, release_id, old)


def verify_backup(root, release_id, expected_old):
    bdir = rel.state_path(root, 'backups', release_id)
    mfile = bdir / 'backup_manifest.json'
    if not mfile.exists():
        raise rel.BackupVerificationFailed(f'backup manifest missing for {release_id}')
    bm = json.loads(mfile.read_text(encoding='utf-8'))
    if bm.get('manifest_sha256') != rel.seal(bm) or bm.get('release_id') != release_id or set(bm.get('files', {})) != set(ALL_TARGETS):
        raise rel.BackupVerificationFailed('backup manifest integrity check failed')
    data = {}
    for n in ALL_TARGETS:
        b = rel.safe_path(root, f'{cfg.STATE_REL}/backups/{release_id}/{n}').read_bytes()
        if rel.sha256_bytes(b) != bm['files'][n]['sha256'] or bm['files'][n]['sha256'] != expected_old[n]:
            raise rel.BackupVerificationFailed(f'backup bytes for {n} do not match the recorded original hash')
        data[n] = b
    return data


def _old(m):
    return {**{n: m['targets'][n]['old_sha256'] for n in cfg.TARGET_NAMES}, METADATA: m['metadata_target']['old_sha256']}


def _new(m):
    return {**{n: m['targets'][n]['new_sha256'] for n in cfg.TARGET_NAMES}, METADATA: m['metadata_target']['new_sha256']}


def _tstate(m, n):
    return m['metadata_target'] if n == METADATA else m['targets'][n]


def _restore(root, live, m, backup, kind):
    """Deterministic restore of the previous release's bytes for every target this release touched."""
    old, new = _old(m), _new(m)
    cur = {n: rel.sha256_file(q_target_path(root, n)) for n in ALL_TARGETS}
    restore, external = [], {}
    for n in ALL_TARGETS:
        if cur[n] == old[n]:
            continue
        if cur[n] == new[n] or _tstate(m, n)['state'] in ('replaced', 'restored'):
            restore.append(n)
        else:
            external[n] = cur[n]
    if restore and backup is None:
        raise rel.RecoveryRequired(f'{restore} must be restored but no verified backup is available')
    q_clean_staged(root, m['release_id'])
    staged = {}
    for n in restore:
        staged[n] = q_write_new(root, q_staged_path(root, 'rollback', m['release_id'], n), backup[n])
    for i, n in enumerate(restore, 1):
        rel._replace(staged[n], q_target_path(root, n))
        _tstate(m, n)['state'] = 'restored'
        rel.save_manifest(root, m)
        rel._fault(live, f'q_{kind}_after_restore_{i}')
    final = {n: rel.sha256_file(q_target_path(root, n)) for n in ALL_TARGETS}
    bad = [n for n in restore if final[n] != old[n]]
    if bad:
        raise rel.RecoveryRequired(f'restore verification failed for {bad}')
    pin = _append_pin(root, new[METADATA], old[METADATA], m['release_id'], f'{kind}: metadata restored to {m["previous_release_id"]}') \
        if final[METADATA] == old[METADATA] and rel.effective_pins(root)[f'{cfg.INTEGRATION_REL}/metadata.json'] == new[METADATA] else None
    return {'restored': restore, 'external_unknown': external, 'final_sha256': final, 'pin_entry': pin['seq'] if pin else None}


# ------------------------------------------------------------------ promotion
def post_validation(root, m, protected_before, canon, geo):
    files = {n: q_target_path(root, n).read_bytes() for n in cfg.TARGET_NAMES}
    qa = {'published_sha256': {n: rel.sha256_bytes(b) for n, b in files.items()}}
    qa['hashes_match_package'] = all(qa['published_sha256'][n] == m['targets'][n]['new_sha256'] for n in cfg.TARGET_NAMES)
    qa['metadata_matches'] = rel.sha256_file(q_target_path(root, METADATA)) == m['metadata_target']['new_sha256']
    meta = json.loads(q_target_path(root, METADATA).read_text(encoding='utf-8'))
    qa['latest_actual_period'] = meta.get('latest_actual_period')
    qa['structure_failures'] = validate_series(files, canon, geo, m['periods'], m['expected_totals'])
    after = rel.protected_snapshot(root)
    mrel = f'{cfg.INTEGRATION_REL}/{METADATA}'
    qa['protected_changed'] = sorted(k for k in set(protected_before) | set(after) if k != mrel and protected_before.get(k) != after.get(k))
    qa['staged_leftovers'] = [p.name for p in rel.safe_path(root, cfg.INTEGRATION_REL).iterdir() if p.name.startswith('.c1')]
    qa['pass'] = (qa['hashes_match_package'] and qa['metadata_matches'] and qa['latest_actual_period'] == m['periods'][-1]
                  and not qa['structure_failures'] and not qa['protected_changed'] and not qa['staged_leftovers'])
    if not qa['pass']:
        raise rel.PostValidationFailed(f'post-release validation failed: {qa}', qa)
    return qa


def promote(root, package_dir, authorisation_path, release_id, confirm):
    rel.check_release_id(release_id)
    root, live = rel.canonical_root(root)
    if live and not cfg.LIVE_RELEASE_ENABLED:
        raise rel.LiveReleaseBlocked('promotion against the live repository is disabled (release_config.LIVE_RELEASE_ENABLED=False)')
    pf = preflight(root, package_dir, authorisation_path, release_id)
    if not pf['ok']:
        raise rel.PreflightFailed('preflight failed; nothing was written', [c for c in pf['checks'] if not c['pass']])
    if confirm != rel.confirm_token('PROMOTE', release_id, pf['digest']):
        raise rel.ConfirmationMismatch(f'confirmation must be exactly: PROMOTE {release_id} <first 16 characters of the preflight digest>')
    with rel.ReleaseLock(root, 'promote-quarterly', release_id) as lock:
        pf2 = preflight(root, package_dir, authorisation_path, release_id)
        if not pf2['ok'] or pf2['digest'] != pf['digest']:
            raise rel.PreflightFailed('state changed between confirmation and lock; nothing was written', [c for c in pf2['checks'] if not c['pass']])
        f = pf2['_facts']
        pm, cur, auth = f['pm'], f['current'], f['auth']
        rel._makedirs(root, ['releases'])
        rdir = rel.state_path(root, 'releases', release_id)
        os.mkdir(rdir)
        old = dict(f['hashes'])
        protected_before = f['protected']
        pkg_copy = dict(pm['files_sha256'])
        pkg_copy['package_manifest.json'] = rel.sha256_file(Path(pm['_dir'], 'package_manifest.json'))
        m = {'schema': SCHEMA, 'release_id': release_id, 'operation': 'promote', 'status': 'PROMOTING', 'started_at': rel.now_utc(),
             'kind': 'C1_TO_C1', 'methodology_id': pm['methodology_id'], 'lpn_min_share': pm['lpn_min_share'],
             'periods': pm['periods'], 'new_period': pm['new_period'], 'expected_totals': pm['expected_totals'],
             # Fields the pre-commit guard requires of a sealed C1 release (same meaning as in the October manifest).
             'expected_periods': pm['periods'],
             'sources_sha256': {'VEH0135': pm['source_sha256'], 'package_seal': pm['seal_sha256'],
                                'restatement_report': pm['restatement_report_sha256']},
             'shadow_sha256': dict(pm['expected_target_sha256']),
             'previous_release_id': cur['release_id'], 'previous_manifest_sha256': cur['manifest_sha256'],
             'candidate_id': pm['candidate_id'], 'candidate_seal_sha256': pm['candidate_seal_sha256'], 'source_set_id': pm['source_set_id'],
             'source_sha256': pm['source_sha256'], 'option': 'B', 'option_name': OPTIONS['B'],
             'restatement': {'report_sha256': pm['restatement_report_sha256'], **f['restatement'],
                             'decision_record_sha256': pm['decision_record_sha256'], 'decided_by': pm['decided_by'], 'decided_at': pm['decided_at']},
             'authorisation': {k: auth.get(k) for k in ('kind', 'release_approver', 'approval_reference', 'approved_at', 'organisational_approvals',
                                                        'outstanding_governance')} | {'sha256': auth['_sha256']},
             'package': {'package_id': pm['package_id'], 'seal_sha256': pm['seal_sha256'], 'files_sha256': pkg_copy},
             'coverage': {'licence_areas': ['EPN', 'LPN', 'SPN'], 'local_authorities': int(f['canon'].LAD22CD.nunique()),
                          'laeps': int(f['canon'].LAEP.dropna().nunique()), 'lsoas': len(f['canon']), 'rows': expected_rows(f['canon'], len(pm['periods']))},
             'targets': {n: {'path': f'{cfg.INTEGRATION_REL}/{n}', 'old_sha256': old[n], 'new_sha256': pm['expected_target_sha256'][n], 'state': 'pending'}
                         for n in cfg.TARGET_NAMES},
             'metadata_target': {'path': f'{cfg.INTEGRATION_REL}/{METADATA}', 'old_sha256': old[METADATA], 'new_sha256': f['metadata']['new'],
                                 'latest_actual_period': [cur['periods'][-1], pm['new_period']], 'state': 'pending'},
             'preflight': {'digest': pf2['digest'], 'checks': pf2['checks']},
             'protected_snapshot_sha256': rel.sha256_bytes(json.dumps(protected_before, indent=0, sort_keys=True).encode('utf-8')),
             'provenance': {'repository_head': rel.git_provenance()['repository_head'], 'tool_sha256': {p.name: rel.sha256_file(p) for p in TOOL_FILES},
                            'operator': getpass.getuser(), 'live': live},
             'previous_lock_unreleased': lock.previous_unreleased, 'phases': [{'phase': 'started', 'at': rel.now_utc()}],
             'backup': None, 'promotion_qa': None, 'rollback': None}
        rel.save_manifest(root, m)
        backup = None
        try:
            # Evidence copied into the release record: package, authorisation, protected snapshot (verified on copy).
            for sub in (['package'], ['package', 'payload'], ['package', 'reports']):
                rel._makedirs(root, ['releases', release_id] + sub)
            for fn in list(PACKAGE_FILES) + ['package_manifest.json']:
                data = Path(pm['_dir'], *fn.split('/')).read_bytes()
                if rel.sha256_bytes(data) != pkg_copy[fn]:
                    raise PackageInvalid(f'package file {fn} changed after preflight')
                rel._write_new(root, rdir / 'package' / Path(*fn.split('/')), data)
            auth_bytes = Path(authorisation_path).read_bytes()
            if rel.sha256_bytes(auth_bytes) != auth['_sha256']:
                raise AuthorisationInvalid('authorisation changed after preflight')
            rel._write_new(root, rdir / 'release_authorisation.json', auth_bytes)
            rel._write_new(root, rdir / 'protected_snapshot.json', json.dumps(protected_before, indent=0, sort_keys=True).encode('utf-8'))
            rel._fault(live, 'q_after_preflight', {'mutate_target': lambda: q_target_path(root, cfg.TARGET_NAMES[0]).write_bytes(
                q_target_path(root, cfg.TARGET_NAMES[0]).read_bytes() + b'\r\n')})
            backup = _backup(root, live, release_id, old, m)
            m['backup'] = {'dir': f'{cfg.STATE_REL}/backups/{release_id}', 'files': {n: old[n] for n in ALL_TARGETS}}
            m['phases'].append({'phase': 'backup_verified', 'at': rel.now_utc()})
            rel.save_manifest(root, m)
            payload = dict(f['payload'])
            payload[METADATA] = f['metadata']['new_bytes']
            staged = {}
            for n in ALL_TARGETS:
                p = q_write_new(root, q_staged_path(root, 'promote', release_id, n), payload[n])
                if rel.sha256_file(p) != (m['targets'][n]['new_sha256'] if n != METADATA else m['metadata_target']['new_sha256']):
                    raise rel.ReleaseError(f'staged file verification failed for {n}')
                staged[n] = p
            rel._fault(live, 'q_after_stage', {'mutate_metadata': lambda: q_target_path(root, METADATA).write_bytes(
                q_target_path(root, METADATA).read_bytes().replace(b'"qa_pass": true', b'"qa_pass": false'))})
            m['phases'].append({'phase': 'staged', 'at': rel.now_utc()})
            rel.save_manifest(root, m)
            for i, n in enumerate(ALL_TARGETS, 1):
                if rel.sha256_file(q_target_path(root, n)) != old[n]:
                    raise rel.TargetChanged(f'{n} changed since preflight; replacement stopped before touching it')
                rel._fault(live, f'q_before_replace_{i}')
                rel._replace(staged[n], q_target_path(root, n))
                _tstate(m, n)['state'] = 'replaced'
                m['phases'].append({'phase': f'replaced {n}', 'at': rel.now_utc()})
                rel.save_manifest(root, m)
                rel._fault(live, f'q_after_replace_{i}')
            rel._fault(live, 'q_after_publish', {
                'corrupt_published': lambda: q_target_path(root, cfg.TARGET_NAMES[1]).write_bytes(q_target_path(root, cfg.TARGET_NAMES[1]).read_bytes()[:-3]),
                'touch_protected': lambda: rel.safe_path(root, f'{cfg.INTEGRATION_REL}/monthly_forecast_dno.csv').write_bytes(
                    rel.safe_path(root, f'{cfg.INTEGRATION_REL}/monthly_forecast_dno.csv').read_bytes() + b'\r\n')})
            m['promotion_qa'] = post_validation(root, m, protected_before, f['canon'], f['geo'])
            pin = _append_pin(root, old[METADATA], m['metadata_target']['new_sha256'], release_id,
                              f"release {release_id}: latest_actual_period {cur['periods'][-1]} -> {pm['new_period']}")
            m['metadata_pin_entry'] = pin['seq'] if pin else None
            rel._fault(live, 'q_after_pin')
            m['status'] = 'COMPLETE'
            m['completed_at'] = rel.now_utc()
            m['new_production_sha256'] = {n: rel.sha256_file(q_target_path(root, n)) for n in ALL_TARGETS}
            m['phases'].append({'phase': 'complete', 'at': rel.now_utc()})
            rel.save_manifest(root, m)
            return m
        except BaseException as exc:
            _auto_restore(root, live, m, backup, exc)
            raise


def _auto_restore(root, live, m, backup, exc):
    m['failure'] = {'type': type(exc).__name__, 'code': getattr(exc, 'code', None), 'message': str(exc)[:2000], 'at': rel.now_utc()}
    try:
        res = _restore(root, live, m, backup, 'auto')
        m['status'] = 'FAILED_ROLLED_BACK' if res['restored'] else 'FAILED_NOT_APPLIED'
        m['auto_restore'] = res
    except BaseException as e2:  # noqa: BLE001
        m['status'] = 'RECOVERY_REQUIRED'
        m['auto_restore'] = {'error': f'{type(e2).__name__}: {e2}'}
    m['phases'].append({'phase': m['status'], 'at': rel.now_utc()})
    try:
        rel.save_manifest(root, m)
    except BaseException:  # noqa: BLE001
        pass


# ------------------------------------------------------------------ rollback / recovery
def rollback(root, release_id, confirm):
    """Restore the immediately preceding approved release. Only quarterly (C1 -> C1) releases can be rolled back here;
    the October Method-A-to-C1 release is never rolled back by this tool (that would restore Method A)."""
    rel.check_release_id(release_id)
    root, live = rel.canonical_root(root)
    if live and not cfg.LIVE_RELEASE_ENABLED:
        raise rel.LiveReleaseBlocked('rollback against the live repository is disabled (release_config.LIVE_RELEASE_ENABLED=False)')
    m = load_release(root, release_id)
    if m['_kind'] != 'C1_TO_C1':
        raise rel.RollbackRefused(f'{release_id} is the Method-A-to-C1 release; rolling it back would restore Method A and is not '
                                  'a C1 -> C1 operation (refused)')
    if confirm != rel.confirm_token('ROLLBACK', release_id, m['manifest_sha256']):
        raise rel.ConfirmationMismatch(f'confirmation must be exactly: ROLLBACK {release_id} <first 16 of manifest_sha256>')
    with rel.ReleaseLock(root, 'rollback-quarterly', release_id):
        m = load_release(root, release_id)
        if m['status'] != 'COMPLETE':
            raise rel.RollbackRefused(f'release {release_id} is {m["status"]}; only a COMPLETE release can be rolled back')
        chain = release_chain(root)
        if any(r['status'] in rel.INCOMPLETE or r['status'] in ('MANIFEST_INVALID', 'INVALID_ENTRY') for r in chain):
            raise rel.RollbackRefused(f'release chain has incomplete or invalid entries: {chain}')
        later = [r for r in chain if r.get('previous_release_id') == release_id and r['status'] == 'COMPLETE']
        if later:
            raise rel.RollbackRefused(f'later releases build on {release_id}: {later}; roll back the newest first')
        prev = load_release(root, m['previous_release_id'])
        if prev['manifest_sha256'] != m['previous_manifest_sha256'] or prev['status'] != 'COMPLETE' or \
                any(prev['targets'][n]['new_sha256'] != m['targets'][n]['old_sha256'] for n in cfg.TARGET_NAMES):
            raise rel.RollbackRefused('the recorded previous release no longer matches; rollback target is not a recognised release')
        cur = current_release(root)
        if cur['release_id'] != release_id:
            raise rel.RollbackRefused(f'{release_id} is not the current release (current: {cur["release_id"]})')
        backup = verify_backup(root, release_id, _old(m))
        before = rel.protected_snapshot(root)
        m['status'] = 'ROLLING_BACK'
        m['phases'].append({'phase': 'rollback_started', 'at': rel.now_utc()})
        rel.save_manifest(root, m)
        res = _restore(root, live, m, backup, 'rollback')
        after = rel.protected_snapshot(root)
        mrel = f'{cfg.INTEGRATION_REL}/{METADATA}'
        changed = sorted(k for k in set(before) | set(after) if k != mrel and before.get(k) != after.get(k))
        if changed or res['external_unknown']:
            m['status'] = 'RECOVERY_REQUIRED'
            rel.save_manifest(root, m)
            raise rel.RecoveryRequired(f'protected files changed during rollback: {changed} {res["external_unknown"]}')
        m['status'] = 'ROLLED_BACK'
        m['rollback'] = {**res, 'at': rel.now_utc(), 'operator': getpass.getuser(), 'restored_release': prev['release_id'], 'protected_unchanged': True}
        m['phases'].append({'phase': 'rolled_back', 'at': rel.now_utc()})
        rel.save_manifest(root, m)
        return m


def recover(root, release_id, confirm):
    rel.check_release_id(release_id)
    root, live = rel.canonical_root(root)
    if live and not cfg.LIVE_RELEASE_ENABLED:
        raise rel.LiveReleaseBlocked('recovery against the live repository is disabled (release_config.LIVE_RELEASE_ENABLED=False)')
    if confirm != rel.confirm_token('RECOVER', release_id):
        raise rel.ConfirmationMismatch(f'confirmation must be exactly: RECOVER {release_id}')
    with rel.ReleaseLock(root, 'recover-quarterly', release_id) as lock:
        m = load_release(root, release_id)
        if m['_kind'] != 'C1_TO_C1':
            raise QuarterlyError('use c1_release.py recover for the Method-A-to-C1 release')
        if m['status'] not in rel.INCOMPLETE:
            raise QuarterlyError(f'release {release_id} is {m["status"]}; nothing to recover')
        try:
            backup = verify_backup(root, release_id, _old(m))
        except (rel.BackupVerificationFailed, FileNotFoundError, OSError, ValueError):
            backup = None
        prior = m['status']
        try:
            res = _restore(root, live, m, backup, 'recover')
            if res['external_unknown']:
                raise rel.RecoveryRequired(f'targets changed outside the tool: {res["external_unknown"]}')
        except rel.RecoveryRequired as e:
            m['status'] = 'RECOVERY_REQUIRED'
            m['recovery'] = {'error': str(e), 'at': rel.now_utc(), 'manual_action': 'compare targets with the backup; do not promote'}
            rel.save_manifest(root, m)
            raise
        m['status'] = 'RECOVERED_TO_ORIGINAL'
        m['recovery'] = {**res, 'from_status': prior, 'at': rel.now_utc(), 'previous_lock_unreleased': lock.previous_unreleased}
        m['phases'].append({'phase': 'recovered_to_previous_release', 'at': rel.now_utc()})
        rel.save_manifest(root, m)
        return m


# ------------------------------------------------------------------ read-only status
def status(root):
    root, live = rel.canonical_root(root)
    out = {'root': str(root), 'live': live, 'live_release_enabled': cfg.LIVE_RELEASE_ENABLED, 'hashes': live_hashes(root),
           'chain': release_chain(root)}
    try:
        cur = current_release(root, out['hashes'])
        out['current_release'] = {'release_id': cur['release_id'], 'kind': cur['_kind'], 'periods': cur['periods'],
                                  'manifest_sha256': cur['manifest_sha256']}
    except rel.ReleaseError as e:
        out['current_release'] = {'error': str(e)}
    return out


def verify_current(root):
    """Chain-aware monitor: the live files are exactly one completed C1 release, starting March 2025, and Latest Actual
    in metadata.json equals that release's last quarter."""
    st = status(root)
    problems = []
    cur = st['current_release']
    if 'error' in cur:
        problems.append(cur['error'])
    else:
        if not expanding_ok(cur['periods']):
            problems.append(f'current release periods {cur["periods"]} are not the expanding March-2025 series')
        meta = json.loads(q_target_path(st['root'], METADATA).read_text(encoding='utf-8'))
        if meta.get('latest_actual_period') != cur['periods'][-1]:
            problems.append(f"metadata latest_actual_period {meta.get('latest_actual_period')} != {cur['periods'][-1]}")
        dno = pd.read_csv(q_target_path(st['root'], 'ev_actuals_dno.csv'))
        if sorted(dno.period.unique()) != cur['periods'] or (dno.period < FIRST_PERIOD).any():
            problems.append('DNO file periods do not match the current release (Method A history or mixed series)')
    return {'ok': not problems, 'problems': problems, **st}


# ------------------------------------------------------------------ CLI
def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('operation', choices=['status', 'verify-current', 'prepare-package', 'preflight', 'promote', 'rollback', 'recover'])
    ap.add_argument('--root')
    ap.add_argument('--package')
    ap.add_argument('--authorisation')
    ap.add_argument('--release-id')
    ap.add_argument('--confirm', default='')
    ap.add_argument('--candidate-dir')
    ap.add_argument('--out')
    a = ap.parse_args(argv)
    try:
        if a.operation == 'prepare-package':
            r = prepare_package(a.candidate_dir, a.out)
        elif a.operation == 'status':
            r = status(a.root)
        elif a.operation == 'verify-current':
            r = verify_current(a.root)
            print(json.dumps(r, indent=2, default=str))
            return 0 if r['ok'] else 6
        elif a.operation == 'preflight':
            r = _public(preflight(a.root, a.package, a.authorisation, a.release_id))
            r['confirm_format'] = f'PROMOTE {a.release_id} {r["digest"][:16]}'
            print(json.dumps(r, indent=2, default=str))
            return 0 if r['ok'] else rel.PreflightFailed.exit_code
        elif a.operation == 'promote':
            r = promote(a.root, a.package, a.authorisation, a.release_id, a.confirm)
        elif a.operation == 'rollback':
            r = rollback(a.root, a.release_id, a.confirm)
        else:
            r = recover(a.root, a.release_id, a.confirm)
        print(json.dumps({k: v for k, v in r.items() if k != '_facts'}, indent=2, default=str))
        return 0
    except rel.ReleaseError as e:
        print(json.dumps({'error': e.code, 'message': str(e), 'detail': e.detail}, indent=2, default=str))
        return e.exit_code
    except Exception as e:  # noqa: BLE001
        print(json.dumps({'error': 'UNEXPECTED_ERROR', 'message': f'{type(e).__name__}: {e}'}, indent=2))
        return 1


if __name__ == '__main__':
    sys.exit(main())
