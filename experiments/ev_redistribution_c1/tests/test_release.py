"""Release tool tests. Every destructive operation runs against disposable environments under the system temp dir.

The real repository is only read: the live-root tests assert refusal before any write, and the last test compares
production hashes taken at the start of this module with hashes at the end.
"""
import hashlib
import json
import os
import shutil
import stat
import subprocess
import sys
import tempfile
import time
import uuid
from pathlib import Path

import pandas as pd
import pytest

EXP = Path(__file__).resolve().parents[1]
RELEASE_DIR = EXP / 'release'
sys.path.insert(0, str(RELEASE_DIR))
import c1_release as rel  # noqa: E402
import disposable_env as denv  # noqa: E402
import release_config as cfg  # noqa: E402

REPO = cfg.REPO
TOOL = RELEASE_DIR / 'c1_release.py'
BASE = Path(tempfile.gettempdir())            # short base: keeps planned paths under the Windows 260-character limit
INTEG = cfg.INTEGRATION_REL
TARGETS = cfg.TARGET_NAMES
REPO_STATE = REPO.joinpath(*cfg.STATE_REL.split('/'))
PRODUCTION_FILES = [REPO / 'dashboard.html', *sorted(REPO.joinpath(*INTEG.split('/')).glob('*')),
                    *sorted((EXP / 'shadow_integration').glob('*')), *sorted((EXP / 'method_a_archive').glob('*')),
                    *sorted((EXP / 'outputs').glob('*')), REPO / 'lookups' / 'LSOA to DNO.csv',
                    REPO / 'project' / 'output_processed' / 'ev_quarterly_lsoa.csv', REPO / 'project' / 'promotion_history.jsonl']
PRODUCTION_FILES = [p for p in PRODUCTION_FILES if p.exists()]     # promotion_history.jsonl is local admin state (untracked)
digest = lambda p: hashlib.sha256(Path(p).read_bytes()).hexdigest()
_PRODUCTION_BEFORE = {}
_STATE_BEFORE = {}
RELEASE_ID = 'c1-20261014-ukpn-ev'                 # the production release (post-release state)


def state_snapshot():
    """Hashes of the live release-state files (release manifest, backups, audit). Tests must never change them."""
    if not REPO_STATE.exists():
        return {}
    return {Path(os.path.relpath(Path(d) / f, REPO_STATE)).as_posix(): digest(Path(d) / f)
            for d, _, fs in os.walk(REPO_STATE) for f in fs}


def _rmtree(path):
    def onerror(func, p, _):
        os.chmod(p, stat.S_IWRITE)
        func(p)
    shutil.rmtree(path, onerror=onerror)


@pytest.fixture(scope='module', autouse=True)
def production_guard():
    _PRODUCTION_BEFORE.update({str(p): digest(p) for p in PRODUCTION_FILES})
    _STATE_BEFORE.update(state_snapshot())
    yield


@pytest.fixture(scope='session')
def template():
    dest = BASE / f'c1rT-{uuid.uuid4().hex[:6]}'
    denv.build(dest)
    yield dest
    targets = {f'{INTEG}/{n}': n for n in TARGETS}
    for relpath, h in {r: denv.rel.sha256_file(dest.joinpath(*r.split('/'))) for r in denv.env_files()}.items():
        expected = cfg.METHOD_A_TARGET_SHA256[targets[relpath]] if relpath in targets else digest(REPO.joinpath(*relpath.split('/')))
        assert h == expected, f'template copy drifted: {relpath}'   # targets: pre-release Method A baseline
    _rmtree(dest)


@pytest.fixture
def env(template):
    dest = BASE / f'c1r-{uuid.uuid4().hex[:6]}'
    denv.build(dest, template)
    yield dest
    _rmtree(dest)


# ---------------------------------------------------------------- helpers
def tree(root):
    out = {}
    for d, _, fs in os.walk(root):
        for f in fs:
            p = Path(d) / f
            if f == 'release.lock':          # byte-range locked while held; it never carries data
                out[Path(os.path.relpath(p, root)).as_posix()] = 'lockfile'
                continue
            out[Path(os.path.relpath(p, root)).as_posix()] = rel.sha256_file(p)
    return out


def changed(a, b):
    return sorted(k for k in set(a) | set(b) if a.get(k) != b.get(k))


def target_hashes(root):
    return {n: rel.sha256_file(Path(root).joinpath(*INTEG.split('/'), n)) for n in TARGETS}


def tpath(root, name):
    return Path(root).joinpath(*INTEG.split('/'), name)


def rewrite(path, data):
    """Replace a file's bytes via a new inode (never writes through a hard link into the template)."""
    path = Path(path)
    tmp = path.with_name(path.name + '.rw')
    tmp.write_bytes(data)
    os.replace(tmp, path)


def promote_ok(root, rid):
    pf = rel.preflight(root)
    assert pf['ok'], [c for c in pf['checks'] if not c['pass']]
    return rel.promote(root, rid, rel.confirm_token('PROMOTE', rid, pf['digest']))


def rollback_ok(root, rid):
    m = rel.load_manifest(root, rid)
    return rel.rollback(root, rid, rel.confirm_token('ROLLBACK', rid, m['manifest_sha256']))


def recover_ok(root, rid):
    return rel.recover(root, rid, rel.confirm_token('RECOVER', rid))


def run_cli(args, fault=None, marker=None):
    e = {**os.environ, 'PYTHONDONTWRITEBYTECODE': '1'}
    e.pop('C1_RELEASE_FAULT', None)
    if fault:
        e['C1_RELEASE_FAULT'] = fault
    if marker:
        e['C1_RELEASE_FAULT_MARKER'] = str(marker)
    return subprocess.run([sys.executable, str(TOOL), *args], capture_output=True, text=True, env=e, timeout=900)


class Background:
    """A CLI run in the background with output going to files (pipes can fill and deadlock a waiting parent)."""

    def __init__(self, args, out_dir, fault=None, marker=None):
        e = {**os.environ, 'PYTHONDONTWRITEBYTECODE': '1'}
        e.pop('C1_RELEASE_FAULT', None)
        if fault:
            e['C1_RELEASE_FAULT'] = fault
        if marker:
            e['C1_RELEASE_FAULT_MARKER'] = str(marker)
        tag = uuid.uuid4().hex[:6]
        self.out, self.err = Path(out_dir) / f'out_{tag}.json', Path(out_dir) / f'err_{tag}.txt'
        with open(self.out, 'wb') as o, open(self.err, 'wb') as er:
            self.proc = subprocess.Popen([sys.executable, str(TOOL), *args], stdout=o, stderr=er, env=e)

    def wait(self, timeout=900):
        try:
            return self.proc.wait(timeout=timeout)
        except subprocess.TimeoutExpired:
            self.proc.kill()
            self.proc.wait()
            raise

    def result(self):
        return json.loads(self.out.read_text(encoding='utf-8'))


def wait_for(path, timeout=120):
    end = time.time() + timeout
    while time.time() < end:
        if Path(path).exists():
            return True
        time.sleep(0.1)
    return False


def token_for(root, rid):
    return rel.confirm_token('PROMOTE', rid, rel.preflight(root)['digest'])


def lock_holder(root, marker, crash=False, hold=30):
    code = (f"import sys,time,os; sys.path.insert(0, r'{RELEASE_DIR}'); import c1_release as r; "
            f"root,_ = r.canonical_root(r'{root}'); l = r.ReleaseLock(root, 'test-holder', 'c1-20261012-holder'); l.__enter__(); "
            f"open(r'{marker}', 'w').write('held'); " + ("os._exit(9)" if crash else f"time.sleep({hold}); l.__exit__(None, None, None)"))
    return subprocess.Popen([sys.executable, '-c', code], env={**os.environ, 'PYTHONDONTWRITEBYTECODE': '1'})


def assert_method_a(root):
    assert target_hashes(root) == cfg.METHOD_A_TARGET_SHA256


def assert_c1(root):
    assert target_hashes(root) == cfg.SHADOW_SHA256


ORIG = None


# ---------------------------------------------------------------- live repository is refused (and untouched)
@pytest.mark.parametrize('op', ['promote', 'rollback', 'recover'])
def test_live_repository_refused_before_any_write(op):
    rid = 'c1-20261012-live'
    tokens = {'promote': 'PROMOTE c1-20261012-live 0000000000000000', 'rollback': 'ROLLBACK c1-20261012-live x', 'recover': 'RECOVER c1-20261012-live'}
    with pytest.raises(rel.LiveReleaseBlocked):
        getattr(rel, op)(str(REPO), rid, tokens[op])
    assert not cfg.LIVE_RELEASE_ENABLED and state_snapshot() == _STATE_BEFORE


def test_live_repository_cli_refused_and_status_is_read_only():
    r = run_cli(['promote', '--root', str(REPO), '--release-id', 'c1-20261012-live', '--confirm', 'PROMOTE c1-20261012-live x'])
    assert r.returncode == rel.LiveReleaseBlocked.exit_code and 'LIVE_RELEASE_BLOCKED' in r.stdout
    st = rel.verify_c1(str(REPO))                       # the Method A reversion detector, run read-only on production
    assert st['ok'] and st['targets']['overall'] == 'C1', st['problems']          # production is the released C1
    assert st['targets']['signals']['ev_actuals_dno.csv']['first_period'] == '2025-03'
    assert state_snapshot() == _STATE_BEFORE


def test_live_repository_preflight_passes_read_only():
    before = {str(p): digest(p) for p in PRODUCTION_FILES}
    pf = rel.preflight(str(REPO), require='C1')        # post-release: live targets must be the released C1 bytes
    assert pf['live'] and pf['ok'], [c for c in pf['checks'] if not c['pass']]
    assert pf['target_state']['overall'] == 'C1'
    key_files = {'dashboard.html', f'{INTEG}/monthly_forecast_dno.csv', f'{INTEG}/monthly_forecast_la.csv', f'{INTEG}/metadata.json',
                 f'{INTEG}/hp_actuals_dno.csv', f'{INTEG}/hp_monthly_forecast_la.csv', 'lookups/LSOA to DNO.csv', 'ev/df_VEH0135.csv'}
    assert key_files <= set(pf['protected'])                       # count varies with untracked local state
    assert not set(pf['protected']) & {f'{INTEG}/{n}' for n in TARGETS}
    assert before == {str(p): digest(p) for p in PRODUCTION_FILES} and state_snapshot() == _STATE_BEFORE
    assert not rel.preflight(str(REPO))['ok']                # a second Method A -> C1 promotion is refused


@pytest.mark.parametrize('bad', [lambda: REPO / 'experiments', lambda: REPO / 'project' / 'output_processed', lambda: REPO.parent,
                                 lambda: Path('relative/root')])
def test_roots_inside_containing_or_relative_refused(bad):
    with pytest.raises(rel.UnsafePath):
        rel.preflight(str(bad()))
    with pytest.raises(rel.UnsafePath):
        rel.promote(str(bad()), 'c1-20261012-x', 'PROMOTE c1-20261012-x 0')


# ---------------------------------------------------------------- write allowlist
@pytest.mark.parametrize('relpath', [
    f'{INTEG}/monthly_forecast_dno.csv', f'{INTEG}/monthly_forecast_la.csv', f'{INTEG}/monthly_forecast_laep.csv',
    f'{INTEG}/metadata.json', f'{INTEG}/hp_actuals_dno.csv', f'{INTEG}/hp_monthly_forecast_la.csv', f'{INTEG}/geography_lookup.csv',
    'dashboard.html', 'project/output_processed/dashboard_comparison_la.csv', 'project/output_processed/ev_actuals_dno.csv',
    f'{INTEG}/../integration/metadata.json', f'{INTEG}_evil/ev_actuals_dno.csv', f'{INTEG}/sub/ev_actuals_dno.csv',
    f'{INTEG}/.c1promote-../../x-ev_actuals_dno.csv.tmp', f'{INTEG}/.c1promote-c1-20261012-x-metadata.json.tmp',
    f'{cfg.ARCHIVE_REL}/manifest.json', f'{cfg.SHADOW_REL}/ev_actuals_dno.csv', 'lookups/LSOA to DNO.csv', cfg.STATE_REL])
def test_write_gate_refuses_everything_outside_the_allowlist(env, relpath):
    with pytest.raises(rel.UnsafePath):
        rel.assert_writable(env, Path(env) / relpath)


def test_write_gate_allows_only_targets_staged_files_and_state(env):
    for n in TARGETS:
        assert rel.assert_writable(env, tpath(env, n)) == tpath(env, n)
        rel.assert_writable(env, tpath(env, f'.c1promote-c1-20261012-x-{n}.tmp'))
    rel.assert_writable(env, Path(env) / cfg.STATE_REL / 'releases' / 'c1-20261012-x' / 'release_manifest.json')
    with pytest.raises(rel.UnsafePath):
        rel._remove(env, tpath(env, 'ev_actuals_dno.csv'))       # targets are never deleted
    with pytest.raises(rel.UnsafePath):
        rel.target_path(env, 'monthly_forecast_dno.csv')
    with pytest.raises(rel.UnsafePath):
        rel.assert_writable(env, Path(str(BASE)) / 'elsewhere' / 'ev_actuals_dno.csv')


@pytest.mark.parametrize('relpath', ['../x', '/abs', 'C:/abs', 'a/../../b', 'x.csv:stream', '..'])
def test_safe_path_refuses_traversal_absolute_and_streams(env, relpath):
    with pytest.raises(rel.UnsafePath):
        rel.safe_path(env, relpath)


@pytest.mark.parametrize('rid', ['../x', 'C1-20261012-x', 'c1-2026-x', 'c1-20261012-', 'c1-20261012-' + 'a' * 41, 'c1-20261012-a/b', ''])
def test_release_id_validation(env, rid):
    with pytest.raises(rel.ReleaseError):
        rel.promote(env, rid, 'x')
    assert not (Path(env) / cfg.STATE_REL).exists()


def _junction(link, target):
    if os.name != 'nt':
        os.symlink(target, link, target_is_directory=True)
        return
    r = subprocess.run(['cmd', '/c', 'mklink', '/J', str(link), str(target)], capture_output=True, text=True)
    if r.returncode != 0:
        pytest.skip(f'junction creation failed: {r.stdout} {r.stderr}')


def test_junction_in_target_tree_refused_with_zero_writes(env):
    integ = Path(env).joinpath(*INTEG.split('/'))
    elsewhere = Path(env) / 'elsewhere_integration'
    os.replace(integ, elsewhere)
    _junction(integ, elsewhere)
    before = tree(elsewhere)
    pf = rel.preflight(env)
    assert not pf['ok'] and not next(c for c in pf['checks'] if c['check'] == 'paths_safe')['pass']
    with pytest.raises(rel.PreflightFailed):
        rel.promote(env, 'c1-20261012-junction', 'PROMOTE c1-20261012-junction 0')
    assert tree(elsewhere) == before and not (Path(env) / cfg.STATE_REL).exists()
    os.rmdir(integ)                                         # removes the junction only


def test_junction_state_directory_refused(env, tmp_path):
    state = Path(env).joinpath(*cfg.STATE_REL.split('/'))
    state.parent.mkdir(parents=True, exist_ok=True)
    outside = tmp_path / 'redirected_state'
    outside.mkdir()
    _junction(state, outside)
    pf = rel.preflight(env)                                  # refused already at preflight
    assert not pf['ok'] and 'junction' in next(c for c in pf['checks'] if c['check'] == 'no_incomplete_release')['detail']
    with pytest.raises(rel.PreflightFailed):
        rel.promote(env, 'c1-20261012-statejunction', rel.confirm_token('PROMOTE', 'c1-20261012-statejunction', pf['digest']))
    root, _ = rel.canonical_root(env)
    with pytest.raises(rel.UnsafePath):                     # and the lock/state writer refuses it independently
        with rel.ReleaseLock(root, 'test', 'c1-20261012-statejunction'):
            pass
    assert list(outside.iterdir()) == [] and target_hashes(env) == cfg.METHOD_A_TARGET_SHA256
    os.rmdir(state)


def test_symlinked_target_file_refused(env, tmp_path):
    t = tpath(env, 'ev_actuals_la.csv')
    real = tmp_path / 'real_la.csv'
    real.write_bytes(t.read_bytes())
    os.remove(t)
    try:
        os.symlink(real, t)
    except (OSError, NotImplementedError):
        pytest.skip('LIMITATION: this Windows account cannot create file symlinks (SeCreateSymbolicLinkPrivilege)')
    assert not rel.preflight(env)['ok']
    with pytest.raises(rel.UnsafePath):
        rel.target_path(env, 'ev_actuals_la.csv')


def test_long_root_fails_closed_on_windows(template):
    if os.name != 'nt':
        pytest.skip('MAX_PATH limit applies to Windows only')
    dest = BASE / ('c1r-long-' + 'x' * 100)
    denv.build(dest, template)
    try:
        pf = rel.preflight(str(dest))
        assert not next(c for c in pf['checks'] if c['check'] == 'path_lengths')['pass'] and not pf['ok']
        with pytest.raises(rel.PreflightFailed):
            rel.promote(str(dest), 'c1-20261012-long', 'PROMOTE c1-20261012-long 0')
        assert not (dest / cfg.STATE_REL).exists()
    finally:
        _rmtree(dest)


# ---------------------------------------------------------------- preflight
def test_preflight_passes_and_is_read_only(env):
    before = tree(env)
    pf = rel.preflight(env)
    assert pf['ok'], [c for c in pf['checks'] if not c['pass']]
    assert pf['target_state']['overall'] == 'METHOD_A' and len(pf['checks']) == 14
    assert rel.preflight(env)['digest'] == pf['digest']    # deterministic
    assert tree(env) == before and not (Path(env) / cfg.STATE_REL).exists()


def _extra_field(data):
    lines = data.split(b'\r\n')
    lines[3] += b',extra'
    return b'\r\n'.join(lines)


def _mutations():
    def append(rel_):
        return lambda e: rewrite(Path(e) / rel_, (Path(e) / rel_).read_bytes() + b'\r\n')
    return {
        'invalid shadow CSV': (lambda e: rewrite(Path(e) / cfg.SHADOW_REL / 'ev_actuals_la.csv',
                                                 _extra_field((Path(e) / cfg.SHADOW_REL / 'ev_actuals_la.csv').read_bytes())),
                               ['shadow_hashes', 'rebuild_from_c1_source', 'shadow_structure']),
        'changed C1 source': (lambda e: rewrite(Path(e) / cfg.C1_OUTPUTS_REL / 'c1_lsoa_quarterly.csv',
                                                (Path(e) / cfg.C1_OUTPUTS_REL / 'c1_lsoa_quarterly.csv').read_bytes().replace(b',0.2', b',0.25', 1)),
                              ['pinned_files', 'shadow_manifest', 'rebuild_from_c1_source']),
        'missing source file': (lambda e: os.remove(Path(e) / cfg.C1_OUTPUTS_REL / 'run_metadata.json'), ['pinned_files', 'c1_metadata']),
        'target not Method A': (append(f'{INTEG}/ev_actuals_dno.csv'), ['targets_state']),
        'target missing': (lambda e: os.remove(tpath(e, 'ev_actuals_laep.csv')), ['paths_safe', 'targets_state']),
        'dashboard changed': (append('dashboard.html'), ['pinned_files']),
        'forecast changed': (append(f'{INTEG}/monthly_forecast_dno.csv'), ['pinned_files']),
        'metadata latest changed': (lambda e: rewrite(Path(e) / INTEG / 'metadata.json', (Path(e) / INTEG / 'metadata.json').read_bytes().replace(b'"2026-03"', b'"2026-06"')),
                                    ['pinned_files', 'metadata_latest_actual']),
        'archive changed': (append(f'{cfg.ARCHIVE_REL}/method_a_dno_history_before_2025-03.csv'), ['pinned_files']),
        'lookup changed': (append('lookups/LSOA to DNO.csv'), ['pinned_files', 'shadow_manifest']),
        'shadow manifest changed': (lambda e: rewrite(Path(e) / cfg.SHADOW_REL / 'manifest.json', (Path(e) / cfg.SHADOW_REL / 'manifest.json').read_bytes().replace(b'0.2,', b'0.25,', 1)),
                                    ['pinned_files', 'shadow_manifest']),
        'staged leftover': (lambda e: tpath(e, '.c1promote-c1-20261012-old-ev_actuals_dno.csv.tmp').write_bytes(b'x'), ['no_staged_leftovers']),
    }


@pytest.mark.parametrize('case', list(_mutations()))
def test_failed_preflight_rejects_promotion_with_zero_writes(env, case):
    mutate, expect_failed = _mutations()[case]
    mutate(env)
    before = tree(env)
    pf = rel.preflight(env)
    failed = {c['check'] for c in pf['checks'] if not c['pass']}
    assert not pf['ok'] and set(expect_failed) <= failed, failed
    with pytest.raises(rel.PreflightFailed):
        rel.promote(env, 'c1-20261012-badpf', rel.confirm_token('PROMOTE', 'c1-20261012-badpf', pf['digest']))
    r = run_cli(['promote', '--root', str(env), '--release-id', 'c1-20261012-badpf2', '--confirm', 'x'])
    assert r.returncode == rel.PreflightFailed.exit_code
    assert tree(env) == before and not (Path(env) / cfg.STATE_REL).exists()


@pytest.mark.parametrize('confirm', ['', 'PROMOTE', 'PROMOTE c1-20261012-ok', 'PROMOTE c1-20261012-ok 0000000000000000',
                                     'promote c1-20261012-ok {d}', 'PROMOTE c1-20261012-other {d}'])
def test_promotion_requires_exact_confirmation(env, confirm):
    before = tree(env)
    d = rel.preflight(env)['digest'][:16]
    with pytest.raises(rel.ConfirmationMismatch):
        rel.promote(env, 'c1-20261012-ok', confirm.format(d=d))
    assert tree(env) == before


def test_running_the_script_without_an_operation_does_nothing(env):
    before = tree(env)
    for args in ([], ['--root', str(env)], ['promote', '--root', str(env)]):
        assert run_cli(args).returncode != 0
    assert tree(env) == before


# ---------------------------------------------------------------- successful promotion and rollback
def test_promotion_replaces_exactly_three_files_with_c1_bytes(env):
    before = tree(env)
    m = promote_ok(env, 'c1-20261012-first')
    after = tree(env)
    diff = changed(before, after)
    assert [d for d in diff if not d.startswith(cfg.STATE_REL + '/')] == [f'{INTEG}/{n}' for n in sorted(TARGETS)]
    assert_c1(env)
    for n in TARGETS:
        assert tpath(env, n).read_bytes() == (Path(env) / cfg.SHADOW_REL / n).read_bytes()
    for n in TARGETS:                                        # five C1 quarters, no Method A history in the active files
        f = pd.read_csv(tpath(env, n))
        assert sorted(f.period.unique()) == cfg.EXPECTED_PERIODS and (f.period >= '2025-03').all()
    for relpath in [k for k in before if k.startswith(cfg.ARCHIVE_REL)]:
        assert after[relpath] == before[relpath]             # the legacy archive is untouched
    assert m['status'] == 'COMPLETE' and m['promotion_qa']['pass'] and m['promotion_qa']['protected_changed'] == []
    assert rel.verify_c1(env)['ok']


def test_backup_and_manifest_are_complete(env):
    m = promote_ok(env, 'c1-20261012-manifest')
    bdir = Path(env) / cfg.STATE_REL / 'backups' / 'c1-20261012-manifest'
    bm = json.loads((bdir / 'backup_manifest.json').read_text(encoding='utf-8'))
    assert bm['manifest_sha256'] == rel.seal(bm) and bm['release_id'] == 'c1-20261012-manifest' and bm['created_at']
    for n in TARGETS:
        assert (bdir / n).read_bytes() == denv.method_a_target_bytes(n)             # original Method A bytes
        assert bm['files'][n]['sha256'] == cfg.METHOD_A_TARGET_SHA256[n]
        assert Path(bm['files'][n]['original_path']) == tpath(env, n).resolve() and Path(bm['files'][n]['backup_path']) == (bdir / n).resolve()
    assert bm['shadow_sha256'] == cfg.SHADOW_SHA256 and bm['sources_sha256']
    loaded = rel.load_manifest(env, 'c1-20261012-manifest')
    assert loaded['manifest_sha256'] == rel.seal(loaded)
    assert loaded['methodology_id'] == 'C1_DYNAMIC_PRIVATE_EV' and loaded['lpn_min_share'] == 0.2
    assert loaded['expected_periods'] == cfg.EXPECTED_PERIODS
    assert loaded['coverage'] == {'licence_areas': ['EPN', 'LPN', 'SPN'], 'local_authorities': 121, 'laeps': 14, 'lsoas': 11023,
                                  'fuels': ['BEV', 'PHEV'], 'rows': cfg.EXPECTED_ROWS}
    assert {n: t['old_sha256'] for n, t in loaded['targets'].items()} == cfg.METHOD_A_TARGET_SHA256
    assert {n: t['new_sha256'] for n, t in loaded['targets'].items()} == cfg.SHADOW_SHA256 == loaded['new_production_sha256']
    assert all(Path(t['path']) == tpath(env, n).resolve() for n, t in loaded['targets'].items())
    assert loaded['backup']['dir'] and all(c['pass'] for c in loaded['preflight']['checks'])
    prov = loaded['provenance']
    assert prov['repository_head'] and prov['operator'] and prov['host'] and set(prov['tool_sha256']) == {'c1_release.py', 'release_config.py', 'integration_writer.py'}
    assert loaded['started_at'] and loaded['completed_at'] and loaded['rollback'] is None
    assert 'password' not in json.dumps(loaded).lower() and 'token' not in loaded
    snap = Path(env) / cfg.STATE_REL / 'releases' / 'c1-20261012-manifest' / 'protected_snapshot.json'
    assert rel.sha256_file(snap) == loaded['protected_snapshot_sha256']


def test_rollback_restores_original_bytes_and_nothing_else(env):
    pf0 = rel.preflight(env)
    before = tree(env)
    promote_ok(env, 'c1-20261012-rb')
    m = rollback_ok(env, 'c1-20261012-rb')
    assert m['status'] == 'ROLLED_BACK' and m['rollback']['restored'] == list(TARGETS)
    assert_method_a(env)
    after = tree(env)
    assert [d for d in changed(before, after) if not d.startswith(cfg.STATE_REL + '/')] == []
    pf1 = rel.preflight(env)
    assert pf1['ok'] and pf1['digest'] == pf0['digest']
    with pytest.raises(rel.RollbackRefused):                 # a second rollback of the same release is refused
        rollback_ok(env, 'c1-20261012-rb')


def test_repeated_promotion_and_rollback_are_deterministic(env):
    digests, manifests = set(), []
    for i in range(3):
        digests.add(rel.preflight(env)['digest'])
        promote_ok(env, f'c1-20261012-cycle{i}')
        assert_c1(env)
        assert rel.verify_c1(env)['ok']
        rollback_ok(env, f'c1-20261012-cycle{i}')
        assert_method_a(env)
        manifests.append(rel.load_manifest(env, f'c1-20261012-cycle{i}'))
    assert len(digests) == 1
    assert all(m['status'] == 'ROLLED_BACK' and m['new_production_sha256'] == cfg.SHADOW_SHA256 for m in manifests)


def test_promotion_is_refused_when_already_c1(env):
    promote_ok(env, 'c1-20261012-once')
    before = tree(env)
    pf = rel.preflight(env)
    assert not pf['ok'] and pf['target_state']['overall'] == 'C1'
    with pytest.raises(rel.PreflightFailed):
        rel.promote(env, 'c1-20261012-twice', rel.confirm_token('PROMOTE', 'c1-20261012-twice', pf['digest']))
    assert tree(env) == before


def test_release_ids_and_backups_are_never_reused(env):
    promote_ok(env, 'c1-20261012-reuse')
    rollback_ok(env, 'c1-20261012-reuse')
    before = tree(env)
    with pytest.raises(rel.ReleaseError, match='never reused'):
        promote_ok(env, 'c1-20261012-reuse')
    assert tree(env) == before
    with pytest.raises(FileExistsError):
        rel._write_new(env, Path(env) / cfg.STATE_REL / 'backups' / 'c1-20261012-reuse' / 'ev_actuals_dno.csv', b'x')


# ---------------------------------------------------------------- rollback refusals and manifest integrity
def _reseal(root, rid, mutate):
    p = rel.manifest_path(root, rid)
    m = json.loads(p.read_text(encoding='utf-8'))
    mutate(m)
    m['manifest_sha256'] = rel.seal(m)
    rewrite(p, json.dumps(m).encode('utf-8'))


@pytest.mark.parametrize('case', ['tampered', 'not_json', 'wrong_new_hash', 'extra_target', 'unknown_release'])
def test_invalid_manifests_refused(env, case):
    promote_ok(env, 'c1-20261012-man')
    p = rel.manifest_path(env, 'c1-20261012-man')
    if case == 'tampered':
        m = json.loads(p.read_text(encoding='utf-8'))
        m['status'] = 'COMPLETE'
        m['targets']['ev_actuals_dno.csv']['old_sha256'] = cfg.SHADOW_SHA256['ev_actuals_dno.csv']
        rewrite(p, json.dumps(m).encode('utf-8'))
    elif case == 'not_json':
        rewrite(p, b'{not json')
    elif case == 'wrong_new_hash':
        _reseal(env, 'c1-20261012-man', lambda m: m['targets']['ev_actuals_la.csv'].update(new_sha256='0' * 64))
    elif case == 'extra_target':
        _reseal(env, 'c1-20261012-man', lambda m: m['targets'].update({'metadata.json': m['targets']['ev_actuals_dno.csv']}))
    before = tree(env)
    rid = 'c1-20261012-nothere' if case == 'unknown_release' else 'c1-20261012-man'
    with pytest.raises(rel.ManifestInvalid):
        rel.rollback(env, rid, 'ROLLBACK x')
    with pytest.raises((rel.ManifestInvalid, rel.ReleaseError)):
        recover_ok(env, rid)
    assert [d for d in changed(before, tree(env)) if not d.endswith('release.lock.json')] == []
    assert_c1(env)


def test_rollback_refuses_externally_changed_target(env):
    promote_ok(env, 'c1-20261012-ext')
    rewrite(tpath(env, 'ev_actuals_laep.csv'), b'someone else wrote this\r\n')
    before = tree(env)
    with pytest.raises(rel.RollbackRefused):
        rollback_ok(env, 'c1-20261012-ext')
    assert [d for d in changed(before, tree(env)) if not d.endswith('release.lock.json')] == []


def test_rollback_refuses_tampered_backup(env):
    promote_ok(env, 'c1-20261012-tb')
    b = Path(env) / cfg.STATE_REL / 'backups' / 'c1-20261012-tb' / 'ev_actuals_la.csv'
    rewrite(b, b'X' + b.read_bytes()[1:])
    before = tree(env)
    with pytest.raises(rel.BackupVerificationFailed):
        rollback_ok(env, 'c1-20261012-tb')
    assert [d for d in changed(before, tree(env)) if not d.endswith('release.lock.json')] == []
    assert_c1(env)


def test_rollback_refuses_when_a_later_release_completed(env):
    promote_ok(env, 'c1-20261012-early')
    src = Path(env) / cfg.STATE_REL / 'releases'
    shutil.copytree(src / 'c1-20261012-early', src / 'c1-20261012-later')
    _reseal(env, 'c1-20261012-later', lambda m: m.update(release_id='c1-20261012-later', started_at='9999-01-01T00:00:00+00:00'))
    with pytest.raises(rel.RollbackRefused, match='ambiguous'):
        rollback_ok(env, 'c1-20261012-early')
    assert_c1(env)


# ---------------------------------------------------------------- injected failures (in-process)
FAULTS = {
    'after_preflight:mutate_target': ('FAILED_NOT_APPLIED', [], rel.BackupVerificationFailed),
    'after_backup_copy:corrupt_backup': ('FAILED_NOT_APPLIED', [], rel.BackupVerificationFailed),
    'after_stage_promote:raise': ('FAILED_NOT_APPLIED', [], rel.InjectedFault),
    'after_replace_1:raise': ('FAILED_ROLLED_BACK', ['ev_actuals_dno.csv'], rel.InjectedFault),
    'after_replace_2:raise': ('FAILED_ROLLED_BACK', ['ev_actuals_dno.csv', 'ev_actuals_la.csv'], rel.InjectedFault),
    'before_replace_3:raise': ('FAILED_ROLLED_BACK', ['ev_actuals_dno.csv', 'ev_actuals_la.csv'], rel.InjectedFault),
    'after_replace_3:raise': ('FAILED_ROLLED_BACK', list(TARGETS), rel.InjectedFault),
    'after_publish:corrupt_published': ('FAILED_ROLLED_BACK', list(TARGETS), rel.PostValidationFailed),
    'after_publish:touch_protected': ('FAILED_ROLLED_BACK', list(TARGETS), rel.PostValidationFailed),
}


@pytest.mark.parametrize('fault', list(FAULTS))
def test_injected_failure_restores_originals(env, fault, monkeypatch):
    status, restored, exc = FAULTS[fault]
    rid = 'c1-20261012-fault'
    token = token_for(env, rid)
    monkeypatch.setenv('C1_RELEASE_FAULT', fault)
    with pytest.raises(exc):
        rel.promote(env, rid, token)
    monkeypatch.delenv('C1_RELEASE_FAULT')
    m = rel.load_manifest(env, rid)
    assert m['status'] == status and m['auto_restore']['restored'] == restored and m['failure']['type'] == exc.__name__
    leftovers = [p.name for p in tpath(env, 'x').parent.iterdir() if p.name.startswith('.c1')]
    assert leftovers == []
    hashes = target_hashes(env)
    assert not any(hashes[n] == cfg.SHADOW_SHA256[n] for n in TARGETS)          # no C1 bytes left anywhere
    if fault == 'after_preflight:mutate_target':
        assert m['auto_restore']['external_unknown'] == {'ev_actuals_dno.csv': hashes['ev_actuals_dno.csv']}
        assert not rel.preflight(env)['ok']                                     # the external change keeps it fail-closed
        return
    assert_method_a(env)
    if fault == 'after_publish:touch_protected':
        assert m['promotion_qa'] is None and not rel.preflight(env)['ok']      # protected change detected; still blocked
        return
    promote_ok(env, 'c1-20261012-retry')                                        # recoverable: a fresh release succeeds
    assert_c1(env)


@pytest.mark.parametrize('position', [0, 1])
def test_read_only_destination(env, position):
    t = tpath(env, TARGETS[position])
    os.chmod(t, stat.S_IREAD)
    try:
        with pytest.raises(PermissionError):
            promote_ok(env, 'c1-20261012-ro')
        m = rel.load_manifest(env, 'c1-20261012-ro')
        assert m['status'] == ('FAILED_NOT_APPLIED' if position == 0 else 'FAILED_ROLLED_BACK')
        assert m['auto_restore']['restored'] == list(TARGETS[:position])
        assert_method_a(env)
    finally:
        os.chmod(t, stat.S_IWRITE | stat.S_IREAD)


def test_failed_auto_restore_requires_and_supports_recovery(env, monkeypatch):
    token = token_for(env, 'c1-20261012-double')
    monkeypatch.setenv('C1_RELEASE_FAULT', 'after_replace_2:raise,auto_after_restore_1:raise')
    with pytest.raises(rel.InjectedFault):
        rel.promote(env, 'c1-20261012-double', token)
    monkeypatch.delenv('C1_RELEASE_FAULT')
    m = rel.load_manifest(env, 'c1-20261012-double')
    assert m['status'] == 'RECOVERY_REQUIRED'
    assert rel.classify_targets(target_hashes(env))['overall'] == 'MIXED'
    pf = rel.preflight(env)
    assert not pf['ok'] and not next(c for c in pf['checks'] if c['check'] == 'no_incomplete_release')['pass']
    with pytest.raises(rel.PreflightFailed):
        rel.promote(env, 'c1-20261012-blocked', rel.confirm_token('PROMOTE', 'c1-20261012-blocked', pf['digest']))
    with pytest.raises(rel.RollbackRefused):
        rollback_ok(env, 'c1-20261012-double')
    r = recover_ok(env, 'c1-20261012-double')
    assert r['status'] == 'RECOVERED_TO_ORIGINAL' and r['recovery']['from_status'] == 'RECOVERY_REQUIRED'
    assert_method_a(env)
    assert rel.preflight(env)['ok']


# ---------------------------------------------------------------- hard interruptions (real process exit)
@pytest.mark.parametrize('stage', ['after_backup_copy', 'after_stage_promote', 'after_replace_1', 'after_replace_2',
                                   'before_replace_3', 'after_replace_3', 'after_publish'])
def test_interrupted_promotion_blocks_and_recovers(env, stage):
    rid = 'c1-20261012-kill'
    r = run_cli(['promote', '--root', str(env), '--release-id', rid, '--confirm', token_for(env, rid)], fault=f'{stage}:exit')
    assert r.returncode == 70, r.stdout + r.stderr
    m = rel.load_manifest(env, rid)
    assert m['status'] == 'PROMOTING'
    lock = rel.lock_state(env)
    assert lock['held'] is False and lock['previous_holder_unreleased'] is True       # the OS released the dead process's lock
    st = rel.status(env)
    assert any(x['release_id'] == rid and x['status'] == 'PROMOTING' for x in st['releases'])
    pf = rel.preflight(env)
    assert not pf['ok']
    with pytest.raises(rel.PreflightFailed):
        rel.promote(env, 'c1-20261012-next', rel.confirm_token('PROMOTE', 'c1-20261012-next', pf['digest']))
    out = recover_ok(env, rid)
    assert out['status'] == 'RECOVERED_TO_ORIGINAL' and out['recovery']['previous_lock_unreleased']
    assert_method_a(env)
    assert [p.name for p in tpath(env, 'x').parent.iterdir() if p.name.startswith('.c1')] == []
    assert rel.preflight(env)['ok']
    promote_ok(env, 'c1-20261012-after')
    assert_c1(env)


def test_interrupted_rollback_and_interrupted_recovery(env):
    rid = 'c1-20261012-irb'
    promote_ok(env, rid)
    m = rel.load_manifest(env, rid)
    r = run_cli(['rollback', '--root', str(env), '--release-id', rid, '--confirm', rel.confirm_token('ROLLBACK', rid, m['manifest_sha256'])],
                fault='rollback_after_restore_1:exit')
    assert r.returncode == 70
    assert rel.load_manifest(env, rid)['status'] == 'ROLLING_BACK'
    assert rel.classify_targets(target_hashes(env))['overall'] == 'MIXED'
    with pytest.raises(rel.RollbackRefused):
        rollback_ok(env, rid)
    assert not rel.preflight(env)['ok']
    r = run_cli(['recover', '--root', str(env), '--release-id', rid, '--confirm', f'RECOVER {rid}'], fault='recover_after_restore_1:exit')
    assert r.returncode == 70                                       # recovery itself interrupted
    assert rel.load_manifest(env, rid)['status'] == 'ROLLING_BACK'
    out = recover_ok(env, rid)                                      # recovery is repeatable
    assert out['status'] == 'RECOVERED_TO_ORIGINAL'
    assert_method_a(env)
    assert rel.preflight(env)['ok']


# ---------------------------------------------------------------- locking and concurrency
def test_lock_is_exclusive_within_and_across_processes(env, tmp_path):
    root, _ = rel.canonical_root(env)
    with rel.ReleaseLock(root, 'a', 'c1-20261012-a'):
        with pytest.raises(rel.LockBusy):
            with rel.ReleaseLock(root, 'b', 'c1-20261012-b'):
                pass
        assert rel.lock_state(root)['held'] is True
    marker = tmp_path / 'held'
    p = lock_holder(env, marker, hold=30)
    try:
        assert wait_for(marker)
        before = tree(env)
        with pytest.raises(rel.LockBusy) as e:
            promote_ok(env, 'c1-20261012-blocked')
        assert e.value.detail['operation'] == 'test-holder'
        assert [d for d in changed(before, tree(env)) if not d.startswith(cfg.STATE_REL)] == []
        assert_method_a(env)
    finally:
        p.kill()
        p.wait()


def test_lock_age_alone_never_frees_a_live_lock(env, tmp_path):
    marker = tmp_path / 'held'
    p = lock_holder(env, marker, hold=30)
    try:
        assert wait_for(marker)
        info = Path(env) / cfg.STATE_REL / 'release.lock.json'
        d = json.loads(info.read_text(encoding='utf-8'))
        d['acquired_at'] = '2000-01-01T00:00:00+00:00'                     # looks ancient
        rewrite(info, json.dumps(d).encode('utf-8'))
        with pytest.raises(rel.LockBusy):
            promote_ok(env, 'c1-20261012-old')
    finally:
        p.kill()
        p.wait()


def test_stale_lock_from_dead_process_is_recovered_safely(env, tmp_path):
    marker = tmp_path / 'held'
    p = lock_holder(env, marker, crash=True)
    assert p.wait(timeout=120) == 9 and marker.exists()
    st = rel.lock_state(env)
    assert st['held'] is False and st['previous_holder_unreleased'] is True and st['info']['pid'] == p.pid
    m = promote_ok(env, 'c1-20261012-stale')
    assert m['status'] == 'COMPLETE' and m['previous_lock_unreleased']['pid'] == p.pid


@pytest.mark.parametrize('same_id', [False, True, False])
def test_two_simultaneous_promotions_exactly_one_wins(env, tmp_path, same_id):
    ids = ('c1-20261012-racea', 'c1-20261012-racea') if same_id else ('c1-20261012-racea', 'c1-20261012-raceb')
    tokens = {f'{rid}#{i}': token_for(env, rid) for i, rid in enumerate(ids)}
    procs = {k: Background(['promote', '--root', str(env), '--release-id', k.split('#')[0], '--confirm', t], tmp_path) for k, t in tokens.items()}
    codes = {k: p.wait() for k, p in procs.items()}
    outs = {k: p.result() for k, p in procs.items()}
    winners = [k for k, c in codes.items() if c == 0]
    assert len(winners) == 1, (codes, outs)
    loser = next(k for k in codes if k not in winners)
    assert outs[loser]['error'] in ('LOCK_BUSY', 'PREFLIGHT_FAILED', 'RELEASE_ERROR'), outs[loser]
    assert_c1(env)
    rels = {r['release_id']: r['status'] for r in rel.status(env)['releases']}
    assert rels == {winners[0].split('#')[0]: 'COMPLETE'}


def test_promotion_refused_while_rollback_is_active(env, tmp_path):
    rid = 'c1-20261012-active'
    promote_ok(env, rid)
    m = rel.load_manifest(env, rid)
    marker = tmp_path / 'rolling'
    p = Background(['rollback', '--root', str(env), '--release-id', rid, '--confirm', rel.confirm_token('ROLLBACK', rid, m['manifest_sha256'])],
                   tmp_path, fault='rollback_after_restore_1:sleep=20', marker=marker)
    try:
        assert wait_for(marker)
        with pytest.raises((rel.PreflightFailed, rel.LockBusy)):
            rel.promote(env, 'c1-20261012-during', rel.confirm_token('PROMOTE', 'c1-20261012-during', rel.preflight(env)['digest']))
        with pytest.raises(rel.LockBusy):
            recover_ok(env, rid)
        assert rel.lock_state(env)['held'] is True
    finally:
        assert p.wait() == 0, p.err.read_text()
    assert rel.load_manifest(env, rid)['status'] == 'ROLLED_BACK'
    assert_method_a(env)
    assert not (Path(env) / cfg.STATE_REL / 'releases' / 'c1-20261012-during').exists()


# ---------------------------------------------------------------- forecast / non-EV protection and production state
def test_forecasts_and_non_ev_files_untouched_through_promote_and_rollback(env):
    integ = Path(env).joinpath(*INTEG.split('/'))
    watch = {p.name: rel.sha256_file(p) for p in integ.iterdir() if p.name not in TARGETS}
    watch['dashboard.html'] = rel.sha256_file(Path(env) / 'dashboard.html')
    assert {'monthly_forecast_dno.csv', 'monthly_forecast_la.csv', 'monthly_forecast_laep.csv', 'metadata.json',
            'hp_actuals_dno.csv', 'hp_monthly_forecast_la.csv', 'geography_lookup.csv'} <= set(watch)
    promote_ok(env, 'c1-20261012-protect')
    rollback_ok(env, 'c1-20261012-protect')
    now = {p.name: rel.sha256_file(p) for p in integ.iterdir() if p.name not in TARGETS}
    now['dashboard.html'] = rel.sha256_file(Path(env) / 'dashboard.html')
    assert now == watch
    for rel_, h in rel.effective_pins(env).items():      # pins after any approved re-pin (metadata, SMMT dashboard)
        assert rel.sha256_file(Path(env) / rel_) == h


def test_live_production_is_the_sealed_c1_release():
    if not (REPO_STATE / 'releases' / RELEASE_ID / 'release_manifest.json').exists():
        pytest.skip('release state of the production release is not present on this machine')
    m = rel.load_manifest(rel.canonical_root(str(REPO))[0], RELEASE_ID)   # verifies the manifest seal
    assert m['status'] == 'COMPLETE' and m['methodology_id'] == 'C1_DYNAMIC_PRIVATE_EV' and m['lpn_min_share'] == 0.2
    for n in TARGETS:
        assert digest(REPO / INTEG / n) == m['targets'][n]['new_sha256'] == cfg.SHADOW_SHA256[n]
        assert m['targets'][n]['old_sha256'] == cfg.METHOD_A_TARGET_SHA256[n]
    assert not [r for r in rel.list_releases(rel.canonical_root(str(REPO))[0]) if r['status'] in rel.INCOMPLETE]


def test_release_backup_and_git_history_preserve_method_a():
    backup = REPO_STATE / 'backups' / RELEASE_ID
    if not backup.exists():
        pytest.skip('release backup is local state and not present on this machine')
    root = rel.canonical_root(str(REPO))[0]
    _, data = rel.verify_backup(root, RELEASE_ID, cfg.METHOD_A_TARGET_SHA256)
    for n in TARGETS:
        assert data[n] == denv.method_a_target_bytes(n)       # backup bytes == git history bytes == pinned Method A


def test_c1_to_c1_update_is_refused_by_the_current_tool(template, tmp_path):
    """Future quarterly C1 -> C1 updates are not supported by this tool version: the old-state pins are Method A.
    A C1 baseline must fail preflight and the promotion must write nothing (fail closed until re-pinned)."""
    dest = BASE / f'c1rL-{uuid.uuid4().hex[:6]}'
    denv.build(dest, template, baseline='live')
    try:
        assert target_hashes(dest) == cfg.SHADOW_SHA256
        before = tree(dest)
        pf = rel.preflight(dest)
        assert not pf['ok'] and pf['target_state']['overall'] == 'C1'
        assert [c['check'] for c in pf['checks'] if not c['pass']] == ['targets_state']
        with pytest.raises(rel.PreflightFailed):
            rel.promote(dest, 'c1-20261014-c1toc1', rel.confirm_token('PROMOTE', 'c1-20261014-c1toc1', pf['digest']))
        assert tree(dest) == before
    finally:
        _rmtree(dest)


def test_zz_production_untouched_by_this_module():
    assert {str(p): digest(p) for p in PRODUCTION_FILES} == _PRODUCTION_BEFORE
    assert state_snapshot() == _STATE_BEFORE
    assert [p.name for p in REPO.joinpath(*INTEG.split('/')).iterdir() if p.name.startswith('.c1')] == []
