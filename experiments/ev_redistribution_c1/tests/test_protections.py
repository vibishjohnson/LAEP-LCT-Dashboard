"""Implemented protections against Method A reversion, tested on disposable copies only.

* data_manager/promotion.py: the ORIGINAL code (revision 71610eb, before the safeguards commit) is shown to revert C1 and delete unrelated files
  (pathway evidence); the PROTECTED working-tree code keeps C1 EV actuals, keeps DFES/NESO forecasts, still promotes
  SMMT nowcast forecast rows, refuses quarantined and EV-actuals-carrying candidates, and rolls back file by file
  from verified backups only.
* read-only reversion scan: live repository has no unresolved supported pathway; original code is still detected.
* pre-commit guard: staged EV actuals must match a sealed C1 release manifest.
* integration writer: strict no-write on re-runs; the Method A archive is write-once and frozen.
Production and external files are only read; all execution happens in pytest temporary directories.
(The external runner's guarded publishing is tested in test_runner_guard.py.)
"""
import hashlib
import importlib.util
import io
import json
import os
import shutil
import subprocess
import sys
import time
from pathlib import Path

import pandas as pd
import pytest

EXP = Path(__file__).resolve().parents[1]
REPO = EXP.parents[1]
sys.path.insert(0, str(EXP))
sys.path.insert(0, str(EXP / 'protections'))
sys.path.insert(0, str(EXP / 'release'))
import integration_writer as iw  # noqa: E402
import c1_release as rel  # noqa: E402
import release_config as cfg  # noqa: E402
import reversion_scan as rs  # noqa: E402

INTEG = REPO / 'project' / 'output_processed' / 'integration'
SHADOW = EXP / 'shadow_integration'
STAGED_RUN = REPO / 'project' / 'dft_staging' / 'run_smmt_nowcast_20261001_121056'
REAL_BACKUPS = REPO / 'project' / 'production_backups'


def require_local_admin_state():
    """These fixtures come from this machine's untracked admin state (quarantined staged run, historic backups)."""
    if not (STAGED_RUN / 'QUARANTINED.json').exists() or not REAL_BACKUPS.is_dir():
        pytest.skip('local admin state (project/dft_staging staged run, project/production_backups) not present; '
                    'equivalent synthetic coverage is in tests/test_phase5_promotion.py')
GUARD = EXP / 'protections' / 'pre_commit_c1_guard.py'
TARGETS = cfg.TARGET_NAMES
RUN_ID = 'smmt_nowcast_20261001_121056'
ORIGINAL_REV = '71610eb'      # last revision with the original, unprotected promotion.py
sha = lambda b: hashlib.sha256(b).hexdigest()


def method_a_bytes(name):
    """Original pre-release Method A bytes (git history / release backup, verified against the pinned hash).
    The live integration files are C1 since release c1-20261014-ukpn-ev."""
    import disposable_env
    return disposable_env.method_a_target_bytes(name)
fsha = lambda p: sha(Path(p).read_bytes())


def load_module(path, name):
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def git_show(path):
    return subprocess.run(['git', 'show', f'{ORIGINAL_REV}:{path}'], cwd=REPO, capture_output=True, check=True).stdout


def head_lines(path, n):
    with open(path, 'rb') as f:
        return b''.join(f.readline() for _ in range(n))


# ---------------------------------------------------------------- disposable project for promotion.py
def make_project(root):
    """Small project laid out like production, with the C1 EV actuals already published (post-release state)."""
    out = root / 'project' / 'output_processed'
    integ = out / 'integration'
    integ.mkdir(parents=True)
    for n in TARGETS:
        shutil.copyfile(SHADOW / n, integ / n)
    for n in ('monthly_forecast_dno.csv', 'metadata.json', 'geography_lookup.csv', 'hp_actuals_dno.csv', 'hp_actuals_la.csv',
              'hp_actuals_laep.csv', 'hp_metadata.json'):
        shutil.copyfile(INTEG / n, integ / n)
    for n in ('monthly_forecast_la.csv', 'monthly_forecast_laep.csv', 'hp_monthly_forecast_dno.csv', 'hp_monthly_forecast_la.csv',
              'hp_monthly_forecast_laep.csv'):
        (integ / n).write_bytes(head_lines(INTEG / n, 3000))
    for n in ('dashboard_comparison_dno_monthly.csv', 'dashboard_comparison_la.csv', 'dashboard_comparison_laep.csv',
              'dashboard_comparison_lsoa.csv'):
        (out / n).write_bytes(head_lines(REPO / 'project' / 'output_processed' / n, 500))
    (out / 'unrelated_report.csv').write_bytes(b'a,b\r\n1,2\r\n')
    return out


def make_candidate(root, forecast_dno=None, with_ev=True, run_id=RUN_ID):
    require_local_admin_state()
    """Replica of the staged SMMT run (without its quarantine files): optional Method A-era ev_actuals and a forecast."""
    run = root / 'project' / 'dft_staging' / f'run_{run_id}'
    ci = run / 'outputs' / 'integration'
    ci.mkdir(parents=True)
    (run / 'outputs' / 'lsoa_ev_bodytype.csv').write_bytes(b'period,LSOA21CD,ev_count\r\n')
    shutil.copyfile(STAGED_RUN / 'metadata.json', run / 'metadata.json')
    if with_ev:
        for n in TARGETS:
            shutil.copyfile(STAGED_RUN / 'outputs' / 'integration' / n, ci / n)
    if forecast_dno is None:
        shutil.copyfile(STAGED_RUN / 'outputs' / 'integration' / 'monthly_forecast_dno.csv', ci / 'monthly_forecast_dno.csv')
    else:
        (ci / 'monthly_forecast_dno.csv').write_bytes(forecast_dno)
    return run


def clean_nowcast_forecast(prod_path):
    require_local_admin_state()
    """Production forecast + the staged SMMT_NOWCAST rows (deduplicated): what a correct nowcast candidate looks like."""
    prod = pd.read_csv(prod_path, low_memory=False, float_precision='round_trip')
    staged = pd.read_csv(STAGED_RUN / 'outputs' / 'integration' / 'monthly_forecast_dno.csv', low_memory=False, float_precision='round_trip')
    smmt = staged[staged.forecast_source == 'SMMT_NOWCAST'][prod.columns].drop_duplicates([c for c in prod.columns if c != 'value'])
    return pd.concat([prod, smmt], ignore_index=True).to_csv(index=False, lineterminator='\r\n').encode('utf-8')


def tree(root):
    return {Path(os.path.relpath(Path(d) / f, root)).as_posix(): fsha(Path(d) / f) for d, _, fs in os.walk(root) for f in fs}


@pytest.fixture
def modules(tmp_path):
    (tmp_path / 'orig').mkdir()
    (tmp_path / 'protected').mkdir()
    (tmp_path / 'orig' / 'promotion.py').write_bytes(git_show('data_manager/promotion.py'))
    shutil.copyfile(REPO / 'data_manager' / 'promotion.py', tmp_path / 'protected' / 'promotion.py')
    return (load_module(tmp_path / 'orig' / 'promotion.py', f'promo_orig_{tmp_path.name}'),
            load_module(tmp_path / 'protected' / 'promotion.py', f'promo_prot_{tmp_path.name}'))


def promote_nowcast(protected, root):
    out = make_project(root)
    make_candidate(root, forecast_dno=clean_nowcast_forecast(out / 'integration' / 'monthly_forecast_dno.csv'), with_ev=False)
    ok, msg = protected.AtomicPromotion(str(root)).promote(RUN_ID)
    assert ok, msg
    return out, msg.split('Backup: ')[1]


# ---------------------------------------------------------------- evidence: the original code is a reversion pathway
def test_pre_safeguard_revision_is_the_original_unprotected_code():
    src = git_show('data_manager/promotion.py').decode()
    assert '"integration/ev_actuals_dno.csv",' in src and 'shutil.rmtree(prod_output)' in src


def test_original_promotion_reverts_c1_and_replaces_forecast(tmp_path, modules):
    orig, _ = modules
    out = make_project(tmp_path / 'p')
    make_candidate(tmp_path / 'p')
    ok, msg = orig.AtomicPromotion(str(tmp_path / 'p')).promote(RUN_ID)
    assert ok, msg
    for n in TARGETS:
        assert fsha(out / 'integration' / n) == fsha(STAGED_RUN / 'outputs' / 'integration' / n) != cfg.SHADOW_SHA256[n]


def test_original_rollback_deletes_files_created_after_backup(tmp_path, modules):
    orig, _ = modules
    out = make_project(tmp_path / 'p')
    ok, backup_id = orig.ProductionBackup(str(tmp_path / 'p')).create_backup('manual', 'x', 'y')
    (out / 'created_after_backup.csv').write_bytes(b'new\r\n')
    ok, msg = orig.ProductionBackup(str(tmp_path / 'p')).restore_backup(backup_id)
    assert ok and not (out / 'created_after_backup.csv').exists()


# ---------------------------------------------------------------- protected promotion
def test_quarantined_candidate_is_refused(tmp_path, modules):
    _, prot = modules
    out = make_project(tmp_path / 'p')
    run = make_candidate(tmp_path / 'p', forecast_dno=(out / 'integration' / 'monthly_forecast_dno.csv').read_bytes(), with_ev=False)
    shutil.copyfile(STAGED_RUN / 'QUARANTINED.json', run / 'QUARANTINED.json')
    before = tree(tmp_path / 'p')
    ok, msg = prot.AtomicPromotion(str(tmp_path / 'p')).promote(RUN_ID)
    assert not ok and 'quarantined' in msg and tree(tmp_path / 'p') == before


def test_real_staged_run_is_quarantined_and_refused_read_only():
    require_local_admin_state()
    v = load_module(REPO / 'data_manager' / 'promotion.py', 'promo_real_ro').CandidateValidator(str(REPO))
    ok, errors = v.can_promote(RUN_ID)
    assert not ok and 'quarantined' in errors[0]
    q = json.loads((STAGED_RUN / 'QUARANTINED.json').read_text(encoding='ascii'))
    assert q['original_status'] == 'READY_FOR_PROMOTION' and len(q['files_sha256']) == 48
    for rel_path, h in q['files_sha256'].items():
        assert fsha(STAGED_RUN / rel_path) == h                       # original contents preserved
    assert 'FAIL' in (STAGED_RUN / 'qa_report.md').read_text(encoding='ascii')
    for other in ('nowcast_20260930_164329', 'nowcast_20260930_164350'):
        assert v.can_promote(other) == (True, [])                     # legitimate runs remain eligible


def test_candidate_with_unauthorised_ev_actuals_is_refused(tmp_path, modules):
    _, prot = modules
    out = make_project(tmp_path / 'p')
    make_candidate(tmp_path / 'p', forecast_dno=(out / 'integration' / 'monthly_forecast_dno.csv').read_bytes(), with_ev=True)
    before = tree(tmp_path / 'p')
    ok, msg = prot.AtomicPromotion(str(tmp_path / 'p')).promote(RUN_ID)
    assert not ok and 'EV actuals are published only by the C1 release tool' in msg
    assert tree(tmp_path / 'p') == before


def test_stale_forecast_is_refused_before_the_swap(tmp_path, modules):
    _, prot = modules
    out = make_project(tmp_path / 'p')
    make_candidate(tmp_path / 'p', with_ev=False)                    # staged forecast: DFES/NESO rows missing
    before = tree(out)
    ok, msg = prot.AtomicPromotion(str(tmp_path / 'p')).promote(RUN_ID)
    assert not ok and 'DFES/NESO forecast rows differ' in msg and 'Production was not changed' in msg
    assert tree(out) == before


def test_legitimate_nowcast_promotion_still_works(tmp_path, modules):
    _, prot = modules
    root = tmp_path / 'p'
    out = make_project(root)
    before = tree(out)
    prod_fc = out / 'integration' / 'monthly_forecast_dno.csv'
    make_candidate(root, forecast_dno=clean_nowcast_forecast(prod_fc), with_ev=False)
    ok, msg = prot.AtomicPromotion(str(root)).promote(RUN_ID)
    assert ok, msg
    after = tree(out)
    changed = {k for k in after if after[k] != before.get(k)}
    assert 'integration/monthly_forecast_dno.csv' in changed and changed <= {'integration/monthly_forecast_dno.csv', 'integration/metadata.json'}
    for n in TARGETS:
        assert fsha(out / 'integration' / n) == cfg.SHADOW_SHA256[n]
    fc = pd.read_csv(prod_fc, low_memory=False)
    base = pd.read_csv(INTEG / 'monthly_forecast_dno.csv', low_memory=False)
    smmt = fc[fc.forecast_source == 'SMMT_NOWCAST']
    assert len(smmt) == 30 and sorted(smmt.period.unique()) == ['2026-04', '2026-05', '2026-06', '2026-07', '2026-08']
    cols = list(base.columns)
    assert fc[fc.forecast_source != 'SMMT_NOWCAST'].sort_values(cols).reset_index(drop=True).equals(base.sort_values(cols).reset_index(drop=True))
    for n in ('hp_actuals_dno.csv', 'hp_monthly_forecast_la.csv', 'geography_lookup.csv'):
        assert after[f'integration/{n}'] == before[f'integration/{n}']
    assert after['unrelated_report.csv'] == before['unrelated_report.csv']


def test_forecast_guard_rejects_duplicated_nowcast_rows(tmp_path, modules):
    _, prot = modules
    out = make_project(tmp_path / 'p')
    good = pd.read_csv(io.BytesIO(clean_nowcast_forecast(out / 'integration' / 'monthly_forecast_dno.csv')), low_memory=False)
    p = tmp_path / 'dup.csv'
    pd.concat([good, good[good.forecast_source == 'SMMT_NOWCAST']]).to_csv(p, index=False)
    ok, msg = prot.forecast_rows_preserved(str(out / 'integration' / 'monthly_forecast_dno.csv'), str(p))
    assert not ok and 'duplicate SMMT_NOWCAST' in msg


def test_forecast_guard_tolerance_is_round_off_only(tmp_path, modules):
    _, prot = modules
    prod = INTEG / 'monthly_forecast_dno.csv'
    d = pd.read_csv(prod, low_memory=False)                  # default fast parser: last-digit round-off on ~15% of values
    cases = {'roundtrip': d, 'dfes_plus_one': d.copy(), 'neso_row_missing': d.drop(d.index[d.forecast_source == 'NESO'][:1]),
             'dfes_row_duplicated': pd.concat([d, d[d.forecast_source == 'DFES'].head(1)])}
    cases['dfes_plus_one'].loc[d.index[d.forecast_source == 'DFES'][10], 'value'] += 1
    results = {}
    for name, frame in cases.items():
        p = tmp_path / f'{name}.csv'
        frame.to_csv(p, index=False)
        results[name] = prot.forecast_rows_preserved(str(prod), str(p))
    assert results['roundtrip'][0]
    assert not results['dfes_plus_one'][0] and 'values differ' in results['dfes_plus_one'][1]
    assert not results['neso_row_missing'][0] and 'rows differ' in results['neso_row_missing'][1]
    assert not results['dfes_row_duplicated'][0]


def test_c1_check_still_fires_if_ev_actuals_slip_past_the_validator(tmp_path, modules, monkeypatch):
    _, prot = modules
    out = make_project(tmp_path / 'p')
    make_candidate(tmp_path / 'p', forecast_dno=(out / 'integration' / 'monthly_forecast_dno.csv').read_bytes(), with_ev=True)
    monkeypatch.setattr(prot.CandidateValidator, 'can_promote', lambda self, run_id: (True, []))
    monkeypatch.setattr(prot.AtomicPromotion, 'EV_CHANGED', prot.AtomicPromotion.EV_CHANGED + list(prot.C1_PROTECTED_FILES))
    before = tree(out)
    ok, msg = prot.AtomicPromotion(str(tmp_path / 'p')).promote(RUN_ID)
    assert not ok and ('unmanaged files in promotion plan' in msg or 'C1 EV actuals' in msg) and tree(out) == before


# ---------------------------------------------------------------- protected rollback and backups
def test_rollback_restores_only_managed_files_and_never_c1(tmp_path, modules):
    _, prot = modules
    root = tmp_path / 'p'
    out = make_project(root)
    original_fc = (out / 'integration' / 'monthly_forecast_dno.csv').read_bytes()
    out, backup_id = promote_nowcast(prot, tmp_path / 'q')
    out_q = out
    (out_q / 'created_after_backup.csv').write_bytes(b'new\r\n')
    (out_q / 'integration' / 'geography_lookup.csv').write_bytes(b'changed later by another workflow\r\n')
    for n in TARGETS:                                     # a later C1 release after the backup
        (out_q / 'integration' / n).write_bytes((SHADOW / n).read_bytes())
    c1_now = {n: fsha(out_q / 'integration' / n) for n in TARGETS}
    ok, msg = prot.ManualRollback(str(tmp_path / 'q')).rollback(backup_id)
    assert ok, msg
    assert (out_q / 'integration' / 'monthly_forecast_dno.csv').read_bytes() == original_fc
    assert (out_q / 'created_after_backup.csv').exists()
    assert (out_q / 'integration' / 'geography_lookup.csv').read_bytes().startswith(b'changed later')
    assert {n: fsha(out_q / 'integration' / n) for n in TARGETS} == c1_now
    assert not list((out_q / 'integration').glob('*.restore_tmp'))
    manifest = json.loads((tmp_path / 'q' / 'project' / 'production_backups' / backup_id / 'manifest.json').read_text())
    assert manifest['format_version'] == 2 and set(manifest['managed_files_sha256']) <= set(prot.PROMOTION_MANAGED_FILES)
    assert not set(manifest['managed_files_sha256']) & set(prot.C1_PROTECTED_FILES)


def _historic_backup(root):
    require_local_admin_state()
    """Copy of a real historic backup manifest (no format version, no hashes) with a small output tree."""
    real = sorted((REPO / 'project' / 'production_backups').iterdir())[-1]
    b = root / 'project' / 'production_backups' / real.name
    (b / 'output_processed' / 'integration').mkdir(parents=True)
    shutil.copyfile(real / 'manifest.json', b / 'manifest.json')
    shutil.copyfile(INTEG / 'monthly_forecast_dno.csv', b / 'output_processed' / 'integration' / 'monthly_forecast_dno.csv')
    for n in TARGETS:
        (b / 'output_processed' / 'integration' / n).write_bytes(method_a_bytes(n))    # Method A EV actuals
    return real.name


def test_historic_backups_are_refused_with_explanation(tmp_path, modules):
    _, prot = modules
    root = tmp_path / 'p'
    out = make_project(root)
    backup_id = _historic_backup(root)
    before = tree(root)
    ok, msg = prot.ManualRollback(str(root)).rollback(backup_id)
    assert not ok and 'historic format' in msg and 'refused' in msg
    assert tree(root) == before                              # no restore, no pre-restore backup, no deletions
    listed = {b['backup_id']: b['rollback_supported'] for b in prot.ManualRollback(str(root)).list_available_backups()}
    assert listed[backup_id] is False


def test_all_real_historic_backups_would_be_refused_read_only():
    require_local_admin_state()
    prot = load_module(REPO / 'data_manager' / 'promotion.py', 'promo_real_backups')
    pb = prot.ProductionBackup.__new__(prot.ProductionBackup)          # avoid __init__ makedirs
    pb.project_root, pb.backup_root = str(REPO), str(REPO / 'project' / 'production_backups')
    ids = sorted(p.name for p in Path(pb.backup_root).iterdir() if p.is_dir())
    assert len(ids) == 16
    for backup_id in ids:
        ok, msg, _ = pb.verify_backup(backup_id)
        assert not ok and 'historic format' in msg


@pytest.mark.parametrize('damage', ['corrupt', 'missing_file', 'foreign_file', 'no_manifest', 'traversal_id'])
def test_damaged_backups_fail_closed(tmp_path, modules, damage):
    _, prot = modules
    out, backup_id = promote_nowcast(prot, tmp_path / 'q')
    bdir = tmp_path / 'q' / 'project' / 'production_backups' / backup_id
    if damage == 'corrupt':
        p = bdir / 'output_processed' / 'integration' / 'monthly_forecast_dno.csv'
        p.write_bytes(p.read_bytes() + b'x')
    elif damage == 'missing_file':
        os.remove(bdir / 'output_processed' / 'integration' / 'metadata.json')
    elif damage == 'foreign_file':
        m = json.loads((bdir / 'manifest.json').read_text())
        m['managed_files_sha256']['integration/ev_actuals_dno.csv'] = '0' * 64
        (bdir / 'manifest.json').write_text(json.dumps(m))
    elif damage == 'no_manifest':
        os.remove(bdir / 'manifest.json')
    else:
        backup_id = f'..\\{backup_id}'
    before = tree(tmp_path / 'q')
    ok, msg = prot.ManualRollback(str(tmp_path / 'q')).rollback(backup_id)
    assert not ok and msg.startswith('Rollback refused')
    assert tree(tmp_path / 'q') == before


# ---------------------------------------------------------------- read-only reversion scan
def test_live_scan_has_no_unresolved_supported_pathway():
    before = {p: fsha(p) for p in (REPO / 'data_manager' / 'promotion.py', *(INTEG / n for n in TARGETS))}
    r = rs.scan()
    assert r['blockers'] == 0, r
    supported = [f for f in r['findings'] if f.get('kind') == 'supported-operational']
    assert {f['id'] for f in supported} >= {'R1', 'R3', 'R3a', 'R1v'} and all(f['severity'] == 'OK' for f in supported)
    manual = {f['id'] for f in r['findings'] if f.get('kind') == 'manual-admin'}
    assert manual == {'R2', 'R3b', 'R5'}                         # documented, not claimed impossible
    assert before == {p: fsha(p) for p in before}


def test_scan_still_detects_the_original_code(tmp_path):
    repo = tmp_path / 'repo'
    (repo / 'data_manager').mkdir(parents=True)
    (repo / 'data_manager' / 'promotion.py').write_bytes(git_show('data_manager/promotion.py'))
    make_project(repo)
    make_candidate(repo)
    runner = tmp_path / 'run_integration.py'
    shutil.copyfile(REPO / 'integration_runner' / 'original' / 'run_integration.py', runner)
    blockers = sorted(f['id'] for f in rs.scan(repo, runner)['findings'] if f['severity'] == 'BLOCKER')
    assert blockers == ['R1', 'R3', 'R3', 'R3a']


def test_scan_fails_closed_on_unrecognised_runner(tmp_path):
    repo = tmp_path / 'repo'
    (repo / 'data_manager').mkdir(parents=True)
    shutil.copyfile(REPO / 'data_manager' / 'promotion.py', repo / 'data_manager' / 'promotion.py')
    make_project(repo)
    runner = tmp_path / 'run_integration.py'
    runner.write_text('def run():\n    publish_somewhere_else()\n')
    assert [f['id'] for f in rs.scan(repo, runner)['findings'] if f['severity'] == 'BLOCKER'] == ['R1']


# ---------------------------------------------------------------- pre-commit guard (temporary git repository)
def _git(repo, *args, check=True):
    return subprocess.run(['git', *args], cwd=repo, capture_output=True, text=True, check=check)


@pytest.fixture
def repo(tmp_path):
    r = tmp_path / 'repo'
    r.mkdir()
    _git(r, 'init', '-q')
    for k, v in (('user.name', 't'), ('user.email', 't@example.invalid'), ('core.autocrlf', 'false'), ('commit.gpgsign', 'false')):
        _git(r, 'config', k, v)
    d = r / 'project' / 'output_processed' / 'integration'
    d.mkdir(parents=True)
    for n in TARGETS:
        (d / n).write_bytes(method_a_bytes(n))                # Method A baseline (pre-release production)
    _git(r, 'add', '-A')
    _git(r, 'commit', '-q', '-m', 'baseline', '--no-verify')
    return r


def guard(repo, env=None):
    e = dict(os.environ)
    e.pop('C1_ALLOW_BULK_STAGING', None)
    e.update(env or {})
    e['PYTHONDONTWRITEBYTECODE'] = '1'
    return subprocess.run([sys.executable, str(GUARD)], cwd=repo, capture_output=True, text=True, env=e)


def write_manifest(repo, rid, status, completed_at='2026-10-20T00:00:00+00:00', tamper=False):
    m = {'release_id': rid, 'status': status, 'methodology_id': 'C1_DYNAMIC_PRIVATE_EV', 'lpn_min_share': 0.2,
         'expected_periods': cfg.EXPECTED_PERIODS, 'sources_sha256': {'x': 'y'}, 'shadow_sha256': cfg.SHADOW_SHA256,
         'completed_at': completed_at, 'targets': {n: {'old_sha256': cfg.METHOD_A_TARGET_SHA256[n], 'new_sha256': cfg.SHADOW_SHA256[n]}
                                                    for n in TARGETS}}
    m['manifest_sha256'] = rel.seal(m)
    if tamper:
        m['targets']['ev_actuals_dno.csv']['new_sha256'] = '0' * 64
    p = repo / cfg.STATE_REL / 'releases' / rid / 'release_manifest.json'
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(m))


def stage_c1(repo):
    for n in TARGETS:
        shutil.copyfile(SHADOW / n, repo / cfg.INTEGRATION_REL / n)
    _git(repo, 'add', '--', *[f'{cfg.INTEGRATION_REL}/{n}' for n in TARGETS])


def test_guard_blocks_ev_actuals_without_release_manifest(repo):
    stage_c1(repo)
    r = guard(repo)
    assert r.returncode == 1 and 'no sealed C1 release manifest' in r.stderr


def test_guard_allows_exact_c1_bytes_of_completed_release(repo):
    write_manifest(repo, 'c1-20261020-live', 'COMPLETE')
    stage_c1(repo)
    assert guard(repo).returncode == 0


def test_guard_blocks_other_bytes_and_tampered_manifest(repo):
    write_manifest(repo, 'c1-20261020-live', 'COMPLETE')
    stage_c1(repo)
    p = repo / cfg.INTEGRATION_REL / 'ev_actuals_la.csv'
    p.write_bytes(p.read_bytes() + b'2026-06,E1,X,BEV,1\r\n')
    _git(repo, 'add', '--', f'{cfg.INTEGRATION_REL}/ev_actuals_la.csv')
    r = guard(repo)
    assert r.returncode == 1 and 'ev_actuals_la.csv' in r.stderr
    write_manifest(repo, 'c1-20261020-live', 'COMPLETE', tamper=True)
    stage_c1(repo)
    r = guard(repo)
    assert r.returncode == 1 and 'no sealed C1 release manifest' in r.stderr


def test_guard_after_rollback_allows_original_and_blocks_c1(repo):
    write_manifest(repo, 'c1-20261020-live', 'ROLLED_BACK')
    stage_c1(repo)
    assert guard(repo).returncode == 1
    for n in TARGETS:
        (repo / cfg.INTEGRATION_REL / n).write_bytes(method_a_bytes(n))
    _git(repo, 'add', '--', *[f'{cfg.INTEGRATION_REL}/{n}' for n in TARGETS])
    assert guard(repo).returncode == 0


def test_guard_reads_lfs_pointer_oid(repo):
    write_manifest(repo, 'c1-20261020-live', 'COMPLETE')
    path = repo / cfg.INTEGRATION_REL / 'ev_actuals_dno.csv'
    data = (SHADOW / 'ev_actuals_dno.csv').read_bytes()
    path.write_bytes(f'version https://git-lfs.github.com/spec/v1\noid sha256:{sha(data)}\nsize {len(data)}\n'.encode())
    _git(repo, 'add', '--', f'{cfg.INTEGRATION_REL}/ev_actuals_dno.csv')
    assert guard(repo).returncode == 0
    path.write_bytes(f'version https://git-lfs.github.com/spec/v1\noid sha256:{"1" * 64}\nsize 5\n'.encode())
    _git(repo, 'add', '--', f'{cfg.INTEGRATION_REL}/ev_actuals_dno.csv')
    assert guard(repo).returncode == 1


def test_guard_with_the_real_sealed_release_accepts_c1_and_rejects_method_a_restoration(repo):
    real = REPO / cfg.STATE_REL / 'releases' / 'c1-20261014-ukpn-ev' / 'release_manifest.json'
    if not real.exists():
        pytest.skip('production release record is local state and not present on this machine')
    dst = repo / cfg.STATE_REL / 'releases' / 'c1-20261014-ukpn-ev' / 'release_manifest.json'
    dst.parent.mkdir(parents=True)
    shutil.copyfile(real, dst)                                  # exact bytes: the seal must still verify
    stage_c1(repo)
    assert guard(repo).returncode == 0                          # the released C1 bytes are accepted
    _git(repo, 'commit', '-q', '-m', 'c1', '--no-verify')
    for n in TARGETS:                                           # attempt to restore the original Method A bytes over C1
        (repo / cfg.INTEGRATION_REL / n).write_bytes(method_a_bytes(n))
    _git(repo, 'add', '--', *[f'{cfg.INTEGRATION_REL}/{n}' for n in TARGETS])
    r = guard(repo)
    assert r.returncode == 1 and 'COMPLETE bytes of release c1-20261014-ukpn-ev' in r.stderr


def test_guard_blocks_deletion_and_bulk_backup_staging(repo):
    write_manifest(repo, 'c1-20261020-live', 'COMPLETE')
    _git(repo, 'rm', '-q', '--cached', '--', f'{cfg.INTEGRATION_REL}/ev_actuals_laep.csv')
    r = guard(repo)
    assert r.returncode == 1 and 'deleting' in r.stderr
    _git(repo, 'reset', '-q')
    b = repo / 'project' / 'production_backups' / 'promotion_x' / 'output_processed' / 'integration'
    b.mkdir(parents=True)
    (b / 'ev_actuals_dno.csv').write_bytes(method_a_bytes('ev_actuals_dno.csv'))
    _git(repo, 'add', '--', 'project/production_backups')
    r = guard(repo)
    assert r.returncode == 1 and 'production backups' in r.stderr
    assert guard(repo, {'C1_ALLOW_BULK_STAGING': '1'}).returncode == 0


# ---------------------------------------------------------------- integration writer: strict no-write and frozen archive
def _mtimes(d):
    return {p.name: os.stat(p).st_mtime_ns for p in Path(d).iterdir() if p.is_file()}


def test_writer_rerun_is_strict_no_write(tmp_path):
    s, a = tmp_path / 'shadow', tmp_path / 'archive'
    shutil.copytree(SHADOW, s)
    shutil.copytree(EXP / 'method_a_archive', a)
    before_s, before_a = _mtimes(s), _mtimes(a)
    time.sleep(0.05)
    iw.write_all(s, a)
    assert _mtimes(s) == before_s and _mtimes(a) == before_a
    assert tree(s) == tree(SHADOW) and tree(a) == tree(EXP / 'method_a_archive')


def test_archive_is_write_once(tmp_path):
    a = tmp_path / 'archive'
    a.mkdir()
    (a / 'method_a_dno_history_before_2025-03.csv').write_bytes(b'different\r\n')
    with pytest.raises(PermissionError, match='write-once'):
        iw.write_csv(pd.DataFrame({'x': [1]}), a, 'method_a_dno_history_before_2025-03.csv', write_once=True)
    assert (a / 'method_a_dno_history_before_2025-03.csv').read_bytes() == b'different\r\n'


def test_existing_archive_is_frozen_and_verified(tmp_path, monkeypatch):
    a = tmp_path / 'archive'
    shutil.copytree(EXP / 'method_a_archive', a)
    monkeypatch.setattr(iw, 'PROD_METHOD_A_DNO', SHADOW / 'ev_actuals_dno.csv')     # production now holds C1
    before = _mtimes(a)
    m = iw.write_method_a_archive(a)
    assert m['source_rows'] == 348 and _mtimes(a) == before
    (a / 'ev_actuals_dno_method_a_original.csv').write_bytes(b'tampered')
    with pytest.raises(ValueError, match='does not match'):
        iw.write_method_a_archive(a)


def test_archive_cannot_be_built_from_c1_production(tmp_path, monkeypatch):
    monkeypatch.setattr(iw, 'PROD_METHOD_A_DNO', SHADOW / 'ev_actuals_dno.csv')
    with pytest.raises(ValueError, match='no longer contains Method A history'):
        iw.write_method_a_archive(tmp_path / 'new_archive')
    assert not (tmp_path / 'new_archive').exists()


# ---------------------------------------------------------------- C1 still detectable after a simulated release
def test_c1_detectable_after_release_and_admin_promotion(tmp_path, modules):
    """Release C1 in a disposable environment, then run a legitimate admin nowcast promotion on it: verify-c1 stays OK."""
    _, prot = modules
    sys.path.insert(0, str(EXP / 'release'))
    import disposable_env as denv
    env = Path(os.environ.get('TEMP', tempfile_dir())) / f'c1rA-{os.getpid()}-{time.time_ns() % 100000}'
    denv.build(env)
    try:
        pf = rel.preflight(str(env))
        rel.promote(str(env), 'c1-20261013-admin', rel.confirm_token('PROMOTE', 'c1-20261013-admin', pf['digest']))
        assert rel.verify_c1(str(env))['ok']
        prod_fc = env / 'project' / 'output_processed' / 'integration' / 'monthly_forecast_dno.csv'
        make_candidate(env, forecast_dno=clean_nowcast_forecast(prod_fc), with_ev=False)
        for n in ('hp_actuals_dno.csv',):
            assert (env / 'project' / 'output_processed' / 'integration' / n).exists()
        ok, msg = prot.AtomicPromotion(str(env)).promote(RUN_ID)
        assert ok, msg
        v = rel.verify_c1(str(env))
        assert v['ok'] and v['targets']['overall'] == 'C1'
    finally:
        shutil.rmtree(env, onerror=lambda f, p, _: (os.chmod(p, 0o666), f(p)))


def tempfile_dir():
    import tempfile
    return tempfile.gettempdir()
