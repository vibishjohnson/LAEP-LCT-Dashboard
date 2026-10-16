"""
C1 -> C1 quarterly release tool: promotion, rollback, recovery and fault injection in disposable environments.

Every environment is a copy of the live state (current C1 files + the sealed October release record) outside the
repository. The approved option B package is read from the C1 workspace and copied before any tampering. A module
guard proves the real production files, release state and candidate package are unchanged.
"""

import hashlib
import json
import os
import shutil
import stat
import subprocess
import sys
import tempfile
import uuid
from pathlib import Path

import pandas as pd
import pytest

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO))
from data_manager.c1_refresh import config as C, release_readiness as rr  # noqa: E402
import c1_quarterly_release as cq  # noqa: E402
import c1_release as rel  # noqa: E402
import release_config as cfg  # noqa: E402

CID = 'C1C-2026Q2-ce41d178-20261015T130154'
WS = C.Workspace()
# The approved package: the local candidate workspace if present, else the committed archive copy (clean checkout).
PKG = Path(WS.path('release_packages', f'{CID}-B'))
if not (PKG / 'package_manifest.json').exists():
    PKG = REPO / 'experiments' / 'ev_redistribution_c1' / 'quarterly_archive' / CID / 'package'
INTEG = REPO / cfg.INTEGRATION_REL
STATE = REPO / cfg.STATE_REL
BASE = Path(C.short_tmp())
sha = lambda p: hashlib.sha256(Path(p).read_bytes()).hexdigest()

pytestmark = pytest.mark.skipif(not (PKG / 'package_manifest.json').exists(), reason='approved option B package not prepared on this machine')


def tree_hashes(top):
    return {str(p.relative_to(top)): sha(p) for p in Path(top).rglob('*') if p.is_file()}


@pytest.fixture(scope='module', autouse=True)
def production_untouched():
    watch = [*(INTEG / n for n in cq.ALL_TARGETS), INTEG / 'monthly_forecast_dno.csv', REPO / 'dashboard.html']
    before = {str(p): sha(p) for p in watch}
    state_before, pkg_before = tree_hashes(STATE / 'releases'), tree_hashes(PKG)
    yield
    assert {str(p): sha(p) for p in watch} == before
    assert tree_hashes(STATE / 'releases') == state_before and tree_hashes(PKG) == pkg_before
    assert not (STATE / 'pins').exists()


@pytest.fixture(scope='module')
def template():
    t = BASE / f'c1qT-{uuid.uuid4().hex[:6]}'
    rr.build_chain_env(t)
    yield t
    rr.remove_env(t)


@pytest.fixture
def env(template):
    e = BASE / f'c1qE-{uuid.uuid4().hex[:6]}'
    rr.build_chain_env(e, template)
    yield e
    rr.remove_env(e)


@pytest.fixture(scope='module')
def pm():
    return cq.load_package(PKG)


@pytest.fixture
def auth_dir():
    d = BASE / f'c1qA-{uuid.uuid4().hex[:6]}'
    d.mkdir()
    yield d
    rr.remove_env(d)


def targets(e):
    return {n: sha(Path(e) / cfg.INTEGRATION_REL / n) for n in cq.ALL_TARGETS}


def auth(auth_dir, pm, rid, **kw):
    return rr.write_authorisation(str(auth_dir / f'auth-{uuid.uuid4().hex[:6]}.json'), pm, rid, **kw)


def promote(e, pkg, a, rid):
    pf = cq.preflight(e, str(pkg), a, rid)
    assert pf['ok'], [c for c in pf['checks'] if not c['pass']]
    return cq.promote(e, str(pkg), a, rid, rel.confirm_token('PROMOTE', rid, pf['digest']))


def failed(pf):
    return {c['check'] for c in pf['checks'] if not c['pass']}


def copy_pkg(tmp):
    d = Path(tmp) / 'pkg'
    shutil.copytree(PKG, d)
    for p in d.rglob('*'):
        if p.is_file():
            os.chmod(p, stat.S_IREAD | stat.S_IWRITE)
    return d


def reseal(d, mutate):
    pmj = json.loads((d / 'package_manifest.json').read_text())
    mutate(d, pmj)
    pmj['files_sha256'] = {f: sha(d / f) for f in pmj['files_sha256']}
    pmj['seal_sha256'] = cq.candidate_seal({k: v for k, v in pmj.items() if k != 'seal_sha256'})
    (d / 'package_manifest.json').write_text(json.dumps(pmj, indent=2, sort_keys=True))


# ------------------------------------------------------------------ the approved path
def test_promote_rollback_exact_and_chain(env, pm, auth_dir):
    oct_hashes = targets(env)
    assert cq.status(env)['current_release']['release_id'] == C.LIVE_RELEASE_ID
    rid = 'c1-20261015-q2-test'
    m = promote(env, PKG, auth(auth_dir, pm, rid), rid)
    assert m['status'] == 'COMPLETE' and m['option'] == 'B' and m['periods'][0] == '2025-03' and len(m['periods']) == 6
    new = targets(env)
    assert all(new[n] == pm['expected_target_sha256'][n] for n in cfg.TARGET_NAMES)
    assert json.loads((env / cfg.INTEGRATION_REL / 'metadata.json').read_text())['latest_actual_period'] == '2026-06'
    rows = {n: len(pd.read_csv(env / cfg.INTEGRATION_REL / n)) for n in cfg.TARGET_NAMES}
    assert rows == {'ev_actuals_dno.csv': 36, 'ev_actuals_la.csv': 1452, 'ev_actuals_laep.csv': 168}
    dno = pd.read_csv(env / cfg.INTEGRATION_REL / 'ev_actuals_dno.csv')
    j = dno[dno.period == '2026-06']
    assert {'UKPN': int(j.install_count.sum()), **{d: int(j[j.DNO == d].install_count.sum()) for d in ('EPN', 'LPN', 'SPN')},
            **{f: int(j[j.EV_Type == f].install_count.sum()) for f in ('BEV', 'PHEV')}} == \
        {'UKPN': 953624, 'EPN': 466621, 'LPN': 199846, 'SPN': 287157, 'BEV': 629236, 'PHEV': 324388}
    assert int(dno[dno.period == '2026-03'].install_count.sum()) == 898042               # restated March 2026
    assert m['restatement']['values_revised'] > 0 and m['restatement']['report_sha256'] == pm['restatement_report_sha256']
    vc = cq.verify_current(env)
    assert vc['ok'] and vc['current_release']['release_id'] == rid
    # evidence kept in the release record
    rdir = env / cfg.STATE_REL / 'releases' / rid
    assert sha(rdir / 'package' / 'package_manifest.json') == sha(PKG / 'package_manifest.json')
    assert (rdir / 'release_authorisation.json').exists() and json.loads((env / cfg.STATE_REL / 'backups' / rid / 'backup_manifest.json').read_text())
    # the pre-commit guard now accepts exactly the new bytes
    sys.path.insert(0, str(REPO / 'experiments' / 'ev_redistribution_c1' / 'protections'))
    import pre_commit_c1_guard as guard
    allowed, latest = guard.allowed_hashes(env)
    assert latest['release_id'] == rid and allowed == {n: pm['expected_target_sha256'][n] for n in cfg.TARGET_NAMES}
    # the October tool can no longer act on top of the new release
    assert not rel.preflight(env, require='C1')['ok']
    with pytest.raises(rel.ReleaseError):
        o = rel.load_manifest(env, C.LIVE_RELEASE_ID)
        rel.rollback(env, C.LIVE_RELEASE_ID, rel.confirm_token('ROLLBACK', C.LIVE_RELEASE_ID, o['manifest_sha256']))
    assert targets(env) == new
    # rollback restores October exactly
    mm = cq.load_release(env, rid)
    rb = cq.rollback(env, rid, rel.confirm_token('ROLLBACK', rid, mm['manifest_sha256']))
    assert rb['status'] == 'ROLLED_BACK' and targets(env) == oct_hashes and rb['rollback']['restored_release'] == C.LIVE_RELEASE_ID
    assert cq.verify_current(env)['current_release']['release_id'] == C.LIVE_RELEASE_ID
    assert [e['reason'][:8] for e in rel.read_pin_log(env)] == ['release ', 'rollback']
    allowed, latest = guard.allowed_hashes(env)
    assert allowed == {n: oct_hashes[n] for n in cfg.TARGET_NAMES}
    # the same approved package can be promoted again under a new release id
    m2 = promote(env, PKG, auth(auth_dir, pm, 'c1-20261015-q2-again'), 'c1-20261015-q2-again')
    assert m2['status'] == 'COMPLETE'


# ------------------------------------------------------------------ refusals with zero writes
def _refused_no_writes(e, pkg, a, rid, expect):
    before = targets(e)
    pf = cq.preflight(e, str(pkg), a, rid)
    assert not pf['ok'] and expect & failed(pf), failed(pf)
    with pytest.raises(rel.PreflightFailed):
        cq.promote(e, str(pkg), a, rid, rel.confirm_token('PROMOTE', rid, pf['digest']))
    assert targets(e) == before
    assert not (Path(e) / cfg.STATE_REL / 'releases' / rid).exists() and not (Path(e) / cfg.STATE_REL / 'backups' / rid).exists()


def test_wrong_starting_release_method_a(pm, auth_dir):
    e = BASE / f'c1qM-{uuid.uuid4().hex[:6]}'
    try:
        rr.build_chain_env(e)
        sys.path.insert(0, str(REPO / 'experiments' / 'ev_redistribution_c1' / 'release'))
        import disposable_env as denv
        for n in cfg.TARGET_NAMES:
            (e / cfg.INTEGRATION_REL / n).write_bytes(denv.method_a_target_bytes(n))
        _refused_no_writes(e, PKG, auth(auth_dir, pm, 'c1-20261015-wrong'), 'c1-20261015-wrong', {'release_chain'})
    finally:
        rr.remove_env(e)


def test_stale_start_and_unchanged_targets(env, pm, auth_dir):
    promote(env, PKG, auth(auth_dir, pm, 'c1-20261015-first'), 'c1-20261015-first')
    _refused_no_writes(env, PKG, auth(auth_dir, pm, 'c1-20261015-second'), 'c1-20261015-second', {'starting_state', 'targets_change'})


@pytest.mark.parametrize('field,value', [('option', 'A'), ('candidate_id', 'C1C-OTHER'), ('source_set_id', 'SS-OTHER'),
                                         ('restatement_report_sha256', '0' * 64), ('acknowledged_revised_history', False),
                                         ('release_approver', ''), ('release_id', 'c1-20261015-different'), ('kind', 'LOCAL_BUTTON')])
def test_forged_or_mismatched_authorisation(env, pm, auth_dir, field, value):
    a = auth(auth_dir, pm, value) if field == 'release_id' else auth(auth_dir, pm, 'c1-20261015-auth', **{field: value})
    _refused_no_writes(env, PKG, a, 'c1-20261015-auth', {'authorisation'})


def test_tampered_payload_missing_file_and_forged_packages(env, pm, auth_dir, tmp_path):
    rid = 'c1-20261015-tamper'
    d = copy_pkg(tmp_path / 'a')
    p = d / 'payload' / 'ev_actuals_dno.csv'
    rows = p.read_bytes().split(b'\r\n')
    rows[-2] = rows[-2].replace(b',EV,', b',EV,1', 1)                # one published value changed
    p.write_bytes(b'\r\n'.join(rows))
    assert sha(p) != pm['expected_target_sha256']['ev_actuals_dno.csv']
    _refused_no_writes(env, d, auth(auth_dir, pm, rid), rid, {'package'})                       # wrong input hash
    d = copy_pkg(tmp_path / 'b')
    (d / 'reports' / 'summary.json').unlink()
    _refused_no_writes(env, d, auth(auth_dir, pm, rid), rid, {'package'})                       # missing candidate file
    d = copy_pkg(tmp_path / 'c')                                                              # modified report, package re-sealed
    reseal(d, lambda dd, pmj: (dd / 'reports' / 'restatement_geography.csv').write_bytes(
        (dd / 'reports' / 'restatement_geography.csv').read_bytes().replace(b'898042', b'898043')))
    _refused_no_writes(env, d, auth(auth_dir, pm, rid), rid, {'package'})
    d = copy_pkg(tmp_path / 'd')                                                              # option B approval removed
    def drop_approval(dd, pmj):
        rec = json.loads((dd / 'decision_record.json').read_text())
        rec['restatement_approval'] = None
        rec['record_sha256'] = cq.decision_record_hash(rec)
        (dd / 'decision_record.json').write_text(json.dumps(rec))
        pmj['decision_record_sha256'] = rec['record_sha256']
    reseal(d, drop_approval)
    _refused_no_writes(env, d, auth(auth_dir, pm, rid), rid, {'package'})
    d = copy_pkg(tmp_path / 'e')                                                              # decision for another candidate
    def other_candidate(dd, pmj):
        rec = json.loads((dd / 'decision_record.json').read_text())
        rec['candidate_id'] = 'C1C-2026Q2-OTHER'
        rec['record_sha256'] = cq.decision_record_hash(rec)
        (dd / 'decision_record.json').write_text(json.dumps(rec))
        pmj['decision_record_sha256'] = rec['record_sha256']
    reseal(d, other_candidate)
    _refused_no_writes(env, d, auth(auth_dir, pm, rid), rid, {'package'})
    for key, val in (('methodology_id', 'C1_DYNAMIC_PRIVATE_EV_V2'), ('lpn_min_share', 0.25), ('source_set_id', 'SS-VEH0135-2026Q2-other')):
        d = copy_pkg(tmp_path / f'f{key}')
        reseal(d, lambda dd, pmj, k=key, v=val: pmj.__setitem__(k, v))
        _refused_no_writes(env, d, auth(auth_dir, pm, rid), rid, {'package'})


def test_changed_forecast_or_metadata_blocks_release(env, pm, auth_dir):
    f = env / cfg.INTEGRATION_REL / 'monthly_forecast_dno.csv'
    f.write_bytes(f.read_bytes() + b'\r\n')
    _refused_no_writes(env, PKG, auth(auth_dir, pm, 'c1-20261015-fc'), 'c1-20261015-fc', {'pinned_files'})
    e2 = BASE / f'c1qX-{uuid.uuid4().hex[:6]}'
    try:
        rr.build_chain_env(e2)
        mp = e2 / cfg.INTEGRATION_REL / 'metadata.json'
        mp.write_bytes(mp.read_bytes().replace(b'"qa_pass": true', b'"qa_pass": false'))
        _refused_no_writes(e2, PKG, auth(auth_dir, pm, 'c1-20261015-md'), 'c1-20261015-md', {'pinned_files'})
    finally:
        rr.remove_env(e2)


def test_series_validation_rejects_gaps_geography_duplicates(pm):
    canon = rel.load_canon(REPO)
    geo = pd.read_csv(INTEG / 'geography_lookup.csv')
    files = {n: (PKG / 'payload' / n).read_bytes() for n in cfg.TARGET_NAMES}
    assert cq.validate_series(files, canon, geo, pm['periods'], pm['expected_totals']) == []
    gap = {n: pd.read_csv(PKG / 'payload' / n).query("period != '2025-09'").to_csv(index=False, lineterminator='\r\n').encode() for n in cfg.TARGET_NAMES}
    assert any('expanding' in f or 'rows' in f for f in cq.validate_series(gap, canon, geo, [p for p in pm['periods'] if p != '2025-09']))
    la = pd.read_csv(PKG / 'payload' / 'ev_actuals_la.csv')
    wrong = dict(files, **{'ev_actuals_la.csv': la.replace({'LAD22CD': {'E09000033': 'E09000099'}}).to_csv(index=False).encode()})
    assert any('geography' in f for f in cq.validate_series(wrong, canon, geo, pm['periods']))
    dup = dict(files, **{'ev_actuals_dno.csv': pd.concat([pd.read_csv(PKG / 'payload' / 'ev_actuals_dno.csv')] * 1).pipe(
        lambda d: pd.concat([d, d.head(1)])).to_csv(index=False).encode()})
    assert any('duplicate' in f or 'rows' in f for f in cq.validate_series(dup, canon, geo, pm['periods']))
    assert not cq.expanding_ok(['2025-06', '2025-09']) and not cq.expanding_ok(['2025-03', '2025-09'])
    assert cq.next_period('2026-12') == '2027-03'


# ------------------------------------------------------------------ metadata.json write scope
def test_metadata_scope_only_latest_actual_period(env, pm, auth_dir):
    mp = env / cfg.INTEGRATION_REL / 'metadata.json'
    before_bytes = mp.read_bytes()
    rid = 'c1-20261015-meta'
    m = promote(env, PKG, auth(auth_dir, pm, rid), rid)
    after_bytes = mp.read_bytes()
    a, b = json.loads(before_bytes), json.loads(after_bytes)
    assert a['latest_actual_period'] == '2026-03' and b['latest_actual_period'] == '2026-06'
    assert {k: v for k, v in a.items() if k != 'latest_actual_period'} == {k: v for k, v in b.items() if k != 'latest_actual_period'}
    assert after_bytes == before_bytes.replace(b'"latest_actual_period": "2026-03"', b'"latest_actual_period": "2026-06"')
    assert m['metadata_target']['old_sha256'] == sha256_b(before_bytes) and m['metadata_target']['new_sha256'] == sha256_b(after_bytes)
    assert (env / cfg.STATE_REL / 'backups' / rid / 'metadata.json').read_bytes() == before_bytes          # in the backup
    bm = json.loads((env / cfg.STATE_REL / 'backups' / rid / 'backup_manifest.json').read_text())
    assert set(bm['files']) == set(cq.ALL_TARGETS)
    cq.rollback(env, rid, rel.confirm_token('ROLLBACK', rid, cq.load_release(env, rid)['manifest_sha256']))
    assert mp.read_bytes() == before_bytes                                                            # and in the rollback


def sha256_b(b):
    return hashlib.sha256(b).hexdigest()


def test_metadata_edit_between_preflight_and_promotion_is_refused(env, pm, auth_dir):
    rid = 'c1-20261015-race'
    a = auth(auth_dir, pm, rid)
    pf = cq.preflight(env, str(PKG), a, rid)
    mp = env / cfg.INTEGRATION_REL / 'metadata.json'
    mp.write_bytes(mp.read_bytes().replace(b'"qa_pass": true', b'"qa_pass": false'))                  # concurrent edit
    before = targets(env)
    with pytest.raises(rel.PreflightFailed):
        cq.promote(env, str(PKG), a, rid, rel.confirm_token('PROMOTE', rid, pf['digest']))
    assert targets(env) == before and not (env / cfg.STATE_REL / 'releases' / rid).exists()


def test_metadata_edit_during_promotion_restores_previous_release(env, pm, auth_dir, monkeypatch):
    rid = 'c1-20261015-race2'
    a = auth(auth_dir, pm, rid)
    pf = cq.preflight(env, str(PKG), a, rid)
    ev_before = {n: h for n, h in targets(env).items() if n != 'metadata.json'}
    monkeypatch.setenv('C1_RELEASE_FAULT', 'q_after_stage:mutate_metadata')
    with pytest.raises(rel.TargetChanged):
        cq.promote(env, str(PKG), a, rid, rel.confirm_token('PROMOTE', rid, pf['digest']))
    m = cq.load_release(env, rid)
    assert {n: h for n, h in targets(env).items() if n != 'metadata.json'} == ev_before          # EV files restored
    assert m['status'] in ('FAILED_ROLLED_BACK', 'RECOVERY_REQUIRED') and m['metadata_target']['state'] == 'pending'
    assert b'"qa_pass": false' in (env / cfg.INTEGRATION_REL / 'metadata.json').read_bytes()       # the external edit is not overwritten


def test_metadata_unexpected_starting_period_fails_closed(env, pm, auth_dir):
    mp = env / cfg.INTEGRATION_REL / 'metadata.json'
    mp.write_bytes(mp.read_bytes().replace(b'"latest_actual_period": "2026-03"', b'"latest_actual_period": "2025-12"'))
    _refused_no_writes(env, PKG, auth(auth_dir, pm, 'c1-20261015-mstart'), 'c1-20261015-mstart', {'metadata_latest_actual', 'pinned_files'})


# ------------------------------------------------------------------ failures during promotion
@pytest.mark.parametrize('fault,status', [('q_after_replace_2:raise', 'FAILED_ROLLED_BACK'), ('q_after_stage:raise', 'FAILED_NOT_APPLIED'),
                                          ('q_after_publish:corrupt_published', 'FAILED_ROLLED_BACK'),
                                          ('q_after_backup_copy:corrupt_backup', 'FAILED_NOT_APPLIED'),
                                          ('q_after_replace_4:raise', 'FAILED_ROLLED_BACK')])
def test_failures_auto_restore_previous_release(env, pm, auth_dir, monkeypatch, fault, status):
    before = targets(env)
    rid = 'c1-20261015-fault'
    a = auth(auth_dir, pm, rid)
    pf = cq.preflight(env, str(PKG), a, rid)
    monkeypatch.setenv('C1_RELEASE_FAULT', fault)
    with pytest.raises(rel.ReleaseError):
        cq.promote(env, str(PKG), a, rid, rel.confirm_token('PROMOTE', rid, pf['digest']))
    m = cq.load_release(env, rid)
    assert m['status'] == status and targets(env) == before
    assert cq.verify_current(env)['current_release']['release_id'] == C.LIVE_RELEASE_ID
    assert not [p for p in (env / cfg.INTEGRATION_REL).iterdir() if p.name.startswith('.c1')]
    assert rel.effective_pins(env)[f'{cfg.INTEGRATION_REL}/metadata.json'] == before['metadata.json']


def test_post_promotion_change_to_protected_file_is_detected(env, pm, auth_dir, monkeypatch):
    rid = 'c1-20261015-prot'
    a = auth(auth_dir, pm, rid)
    pf = cq.preflight(env, str(PKG), a, rid)
    before = targets(env)
    monkeypatch.setenv('C1_RELEASE_FAULT', 'q_after_publish:touch_protected')
    with pytest.raises(rel.PostValidationFailed):
        cq.promote(env, str(PKG), a, rid, rel.confirm_token('PROMOTE', rid, pf['digest']))
    assert targets(env) == before and cq.load_release(env, rid)['status'] == 'FAILED_ROLLED_BACK'


def test_missing_permission_on_target(env, pm, auth_dir):
    rid = 'c1-20261015-perm'
    a = auth(auth_dir, pm, rid)
    pf = cq.preflight(env, str(PKG), a, rid)
    t = env / cfg.INTEGRATION_REL / 'ev_actuals_la.csv'
    before = targets(env)
    os.chmod(t, stat.S_IREAD)
    try:
        with pytest.raises((PermissionError, rel.ReleaseError)):
            cq.promote(env, str(PKG), a, rid, rel.confirm_token('PROMOTE', rid, pf['digest']))
    finally:
        os.chmod(t, stat.S_IREAD | stat.S_IWRITE)
    assert targets(env) == before and cq.load_release(env, rid)['status'] in ('FAILED_ROLLED_BACK', 'FAILED_NOT_APPLIED')


def _interrupted(e, code_tail, fault):
    script = ("import sys; sys.path.insert(0, r'%s'); from data_manager.c1_refresh import config;"
              "import c1_quarterly_release as cq, c1_release as rel;" % REPO) + code_tail
    p = subprocess.run([sys.executable, '-c', script], env={**os.environ, 'C1_RELEASE_FAULT': fault, 'OPENBLAS_NUM_THREADS': '1'},
                       capture_output=True, timeout=600)
    assert p.returncode == 70, p.stderr.decode()[-2000:]


def test_interrupted_promotion_is_recovered(env, pm, auth_dir):
    before = targets(env)
    rid = 'c1-20261015-intr'
    a = auth(auth_dir, pm, rid)
    pf = cq.preflight(env, str(PKG), a, rid)
    _interrupted(env, f"cq.promote(r'{env}', r'{PKG}', r'{a}', '{rid}', '{rel.confirm_token('PROMOTE', rid, pf['digest'])}')", 'q_after_replace_2:exit')
    assert cq.load_release(env, rid)['status'] == 'PROMOTING' and targets(env) != before
    assert 'error' in cq.status(env)['current_release'] and not cq.preflight(env, str(PKG), a, rid + 'x')['ok']
    r = cq.recover(env, rid, rel.confirm_token('RECOVER', rid))
    assert r['status'] == 'RECOVERED_TO_ORIGINAL' and targets(env) == before
    assert cq.verify_current(env)['current_release']['release_id'] == C.LIVE_RELEASE_ID


def test_interrupted_rollback_is_recovered_to_previous_release(env, pm, auth_dir):
    before = targets(env)
    rid = 'c1-20261015-irb'
    promote(env, PKG, auth(auth_dir, pm, rid), rid)
    m = cq.load_release(env, rid)
    _interrupted(env, f"cq.rollback(r'{env}', '{rid}', '{rel.confirm_token('ROLLBACK', rid, m['manifest_sha256'])}')", 'q_rollback_after_restore_2:exit')
    assert cq.load_release(env, rid)['status'] == 'ROLLING_BACK'
    r = cq.recover(env, rid, rel.confirm_token('RECOVER', rid))
    assert r['status'] == 'RECOVERED_TO_ORIGINAL' and targets(env) == before


def test_concurrent_operation_is_refused(env, pm, auth_dir):
    rid = 'c1-20261015-conc'
    a = auth(auth_dir, pm, rid)
    pf = cq.preflight(env, str(PKG), a, rid)
    before = targets(env)
    with rel.ReleaseLock(env, 'promote', 'c1-20261015-other'):
        with pytest.raises(rel.LockBusy):
            cq.promote(env, str(PKG), a, rid, rel.confirm_token('PROMOTE', rid, pf['digest']))
    assert targets(env) == before and not (env / cfg.STATE_REL / 'releases' / rid).exists()


# ------------------------------------------------------------------ rollback refusals
def test_rollback_refusals(env, pm, auth_dir):
    rid = 'c1-20261015-rbr'
    promote(env, PKG, auth(auth_dir, pm, rid), rid)
    m = cq.load_release(env, rid)
    tok = rel.confirm_token('ROLLBACK', rid, m['manifest_sha256'])
    with pytest.raises(rel.RollbackRefused, match='Method A'):                       # rollback to the Method A archive
        cq.rollback(env, C.LIVE_RELEASE_ID, 'x')
    with pytest.raises(rel.ConfirmationMismatch):
        cq.rollback(env, rid, 'ROLLBACK wrong')
    b = env / cfg.STATE_REL / 'backups' / rid / 'ev_actuals_dno.csv'                 # corrupted backup
    good = b.read_bytes()
    os.chmod(b, stat.S_IREAD | stat.S_IWRITE)
    b.write_bytes(b'X' + good[1:])
    with pytest.raises(rel.BackupVerificationFailed):
        cq.rollback(env, rid, tok)
    b.write_bytes(good)
    t = env / cfg.INTEGRATION_REL / 'ev_actuals_laep.csv'                            # target changed after the release
    orig = t.read_bytes()
    t.write_bytes(orig + b'\r\n')
    with pytest.raises(rel.ReleaseError):
        cq.rollback(env, rid, tok)
    assert t.read_bytes() == orig + b'\r\n'                                          # refused: nothing restored over it
    t.write_bytes(orig)
    b.unlink()                                                                       # missing backup
    with pytest.raises((rel.BackupVerificationFailed, FileNotFoundError)):
        cq.rollback(env, rid, tok)
    assert cq.load_release(env, rid)['status'] == 'COMPLETE'


def test_symlinked_target_directory_is_refused(env, pm, auth_dir):
    integ = env / cfg.INTEGRATION_REL
    real = integ.with_name('integration_real')
    integ.rename(real)
    made = subprocess.run(['cmd', '/c', 'mklink', '/J', str(integ), str(real)], capture_output=True) if os.name == 'nt' else None
    if os.name != 'nt':
        os.symlink(real, integ)
    if not integ.exists():
        real.rename(integ)
        pytest.skip(f'cannot create a junction here: {made.stderr if made else ""}')
    before = tree_hashes(real)
    pf = cq.preflight(env, str(PKG), auth(auth_dir, pm, 'c1-20261015-link'), 'c1-20261015-link')
    assert not pf['ok'] and 'paths_safe' in failed(pf)
    assert tree_hashes(real) == before
    os.rmdir(integ) if os.name == 'nt' else integ.unlink()
    real.rename(integ)


def test_live_repository_is_blocked(pm, auth_dir):
    rid = 'c1-20261015-live'
    a = auth(auth_dir, pm, rid)
    pf = cq.preflight(str(REPO), str(PKG), a, rid)
    assert not pf['ok'] and {'live_switch', 'authorisation'} <= failed(pf)
    with pytest.raises(rel.LiveReleaseBlocked):
        cq.promote(str(REPO), str(PKG), a, rid, rel.confirm_token('PROMOTE', rid, pf['digest']))
    with pytest.raises(rel.LiveReleaseBlocked):
        cq.rollback(str(REPO), rid, 'x')
    with pytest.raises(rel.LiveReleaseBlocked):
        cq.recover(str(REPO), rid, 'x')
    prod = rr.write_authorisation(str(auth_dir / 'prod.json'), pm, rid, kind='PRODUCTION', approver='Someone', reference='CHG-1')
    pf2 = cq.preflight(str(REPO), str(PKG), prod, rid)
    assert 'authorisation' in failed(pf2) and 'live_switch' in failed(pf2)     # outstanding governance + switch off
    detail = next(c['detail'] for c in pf2['checks'] if c['check'] == 'authorisation')
    assert 'release_operator' in detail and 'target_allowlist' in detail and 'outstanding governance' in detail


def test_production_template_is_incomplete_until_filled(pm, auth_dir):
    tpl = json.loads((REPO / 'experiments' / 'ev_redistribution_c1' / 'release' / 'authorisation_template_C1C-2026Q2.json').read_text())
    for k in ('package_seal_sha256', 'candidate_id', 'source_set_id', 'restatement_report_sha256', 'decision_record_sha256', 'previous_release_id'):
        assert tpl[k] == pm[k if k != 'package_seal_sha256' else 'seal_sha256'], k
    assert tpl['kind'] == 'PRODUCTION' and tpl['option'] == 'B' and tpl['release_approver'] is None and tpl['release_operator'] is None
    assert tpl['approval_reference'] is None and tpl['outstanding_governance']
    p = auth_dir / 'tpl.json'
    p.write_text(json.dumps(tpl))
    with pytest.raises(cq.AuthorisationInvalid):
        cq.check_authorisation(cq.load_authorisation(p), pm, 'c1-20261020-ukpn-ev-2026q2', live=False)
    assert cq.verify_current(str(REPO))['ok'] and cq.status(str(REPO))['current_release']['release_id'] == C.LIVE_RELEASE_ID
