"""Forecast refresh through the guarded runner, then metadata re-pin for the C1 release preflight.

Everything runs in disposable environments (system temp dir). The real metadata.json is never modified.
"""
import json
import os
import shutil
import stat
import sys
import tempfile
import uuid
from pathlib import Path

import pandas as pd
import pytest

EXP = Path(__file__).resolve().parents[1]
REPO = EXP.parents[1]
sys.path.insert(0, str(EXP / 'release'))
sys.path.insert(0, str(REPO / 'integration_runner'))
import c1_release as rel  # noqa: E402
import disposable_env as denv  # noqa: E402
import release_config as cfg  # noqa: E402
import repin_metadata as rp  # noqa: E402
from dashboard_integration import publish_guard as guard  # noqa: E402

sys.path.insert(0, str(EXP))
from local_paths import runner_dir  # noqa: E402
RUNNER_OUTPUTS = (runner_dir() or Path('__runner_not_configured__')) / 'outputs'
BASE = Path(tempfile.gettempdir())
META = f'{cfg.INTEGRATION_REL}/metadata.json'


def _rmtree(p):
    shutil.rmtree(p, onerror=lambda f, path, _: (os.chmod(path, stat.S_IWRITE), f(path)))


@pytest.fixture(scope='module')
def template():
    d = BASE / f'c1rMT-{uuid.uuid4().hex[:6]}'
    denv.build(d)
    yield d
    _rmtree(d)


@pytest.fixture
def env(template):
    d = BASE / f'c1rM-{uuid.uuid4().hex[:6]}'
    denv.build(d, template)
    yield d
    _rmtree(d)


@pytest.fixture
def refresh_outputs(tmp_path):
    """What a legitimate runner refresh produces today (the runner's own validated outputs)."""
    if not RUNNER_OUTPUTS.exists():
        pytest.skip('runner outputs not present')
    out = tmp_path / 'runner_outputs'
    out.mkdir()
    for name in guard.PUBLISH_FILES:
        shutil.copyfile(RUNNER_OUTPUTS / name, out / name)
    return out


def integ(env):
    return Path(env).joinpath(*cfg.INTEGRATION_REL.split('/'))


def targets(env):
    return {n: rel.sha256_file(integ(env) / n) for n in cfg.TARGET_NAMES}


def pin_log(env):
    return Path(env).joinpath(*rel.PIN_LOG_REL.split('/'))


def failing(pf):
    return {c['check']: c['detail'] for c in pf['checks'] if not c['pass']}


def refresh(env, outputs, tmp_path):
    return guard.publish(outputs, integ(env), tmp_path / 'publish_backups')


def test_refresh_then_approved_repin_then_release_works(env, refresh_outputs, tmp_path):
    pf0 = rel.preflight(env)
    assert pf0['ok']
    ev_before = targets(env)
    forecasts_before = {n: rel.sha256_file(integ(env) / n) for n in ('monthly_forecast_dno.csv', 'monthly_forecast_la.csv', 'monthly_forecast_laep.csv')}
    old_meta = json.loads((integ(env) / 'metadata.json').read_text())

    report = refresh(env, refresh_outputs, tmp_path)                     # 1-2: legitimate forecast refresh
    assert report['ev_actuals_unchanged'] and targets(env) == ev_before  # 3: no EV actuals change
    for n, h in forecasts_before.items():                                 # 4-5: forecasts valid, restored forecast intact
        assert rel.sha256_file(integ(env) / n) == h
        assert guard.validate_forecast(integ(env) / n, n) == []
    new_meta = json.loads((integ(env) / 'metadata.json').read_text())     # 6: how metadata changed
    assert {k: v for k, v in old_meta.items() if k != 'ev_vintage'} == new_meta and 'ev_vintage' not in new_meta

    pf1 = rel.preflight(env)                                              # 7: preflight fails only on the metadata pin
    assert failing(pf1) == {'pinned_files': f"changed or missing: ['{META}']"}

    plan = rp.plan(env)                                                   # 8: validated, approval-gated re-pin
    assert plan['valid'] and plan['needed'] and plan['problems'] == []
    assert plan['checks']['latest_actual_period'] == {'metadata': '2026-03', 'published_ev_actuals': '2026-03', 'c1_window_end': '2026-03'}
    with pytest.raises(rel.ConfirmationMismatch):
        rp.repin(env, 'REPIN yes')
    assert not pin_log(env).exists()
    entry = rp.repin(env, plan['approval_token'])
    assert entry['old_sha256'] == cfg.PINNED_SHA256[META] and entry['new_sha256'] == rel.sha256_file(integ(env) / 'metadata.json')
    assert entry['metadata_snapshot'] == new_meta and entry['entry_sha256'] == rel.seal_entry(entry)

    pf2 = rel.preflight(env)
    assert pf2['ok'] and pf2['digest'] != pf0['digest']
    with pytest.raises(rp.RepinRefused, match='already matches'):
        rp.repin(env, plan['approval_token'])
    m = rel.promote(env, 'c1-20261013-afterrepin', rel.confirm_token('PROMOTE', 'c1-20261013-afterrepin', pf2['digest']))
    assert m['status'] == 'COMPLETE' and rel.verify_c1(env)['ok']


def _bad_metadata(env, mutate):
    p = integ(env) / 'metadata.json'
    meta = json.loads(p.read_text())
    mutate(meta)
    p.write_text(json.dumps(meta, indent=2))


@pytest.mark.parametrize('case,mutate,expect', [
    ('later latest actual', lambda m: m.update(latest_actual_period='2026-06'), 'latest_actual_period'),
    ('unknown key', lambda m: m.update(c1_override=True), 'unexpected keys'),
    ('qa failed', lambda m: m.update(qa_pass=False), 'qa_pass'),
    ('forecast range wrong', lambda m: m.update(forecast_end_period='2050-03'), 'forecast range'),
    ('missing key', lambda m: m.pop('neso_anchor_dates'), 'missing keys'),
])
def test_unexpected_metadata_changes_abort(env, case, mutate, expect):
    _bad_metadata(env, mutate)
    plan = rp.plan(env)
    assert not plan['valid'] and any(expect in p for p in plan['problems'])
    with pytest.raises(rp.RepinRefused):
        rp.repin(env, plan['approval_token'])
    assert not pin_log(env).exists()


def test_repin_refused_when_a_forecast_is_also_changed(env, refresh_outputs, tmp_path):
    refresh(env, refresh_outputs, tmp_path)
    p = integ(env) / 'monthly_forecast_laep.csv'
    d = pd.read_csv(p, low_memory=False)
    pd.concat([d, d.head(1)]).to_csv(p.with_name('x.csv'), index=False)
    os.replace(p.with_name('x.csv'), p)
    plan = rp.plan(env)
    assert not plan['valid'] and any('duplicate keys' in x for x in plan['problems'])
    assert any('other pinned files changed' in x for x in plan['problems'])
    with pytest.raises(rp.RepinRefused):
        rp.repin(env, plan['approval_token'])


def test_tampered_pin_log_fails_closed(env, refresh_outputs, tmp_path):
    refresh(env, refresh_outputs, tmp_path)
    rp.repin(env, rp.plan(env)['approval_token'])
    assert rel.preflight(env)['ok']
    log = json.loads(pin_log(env).read_text())
    log[0]['new_sha256'] = rel.sha256_file(integ(env) / 'monthly_forecast_dno.csv')       # try to bless another hash
    p = pin_log(env)
    p.with_name('t.json').write_text(json.dumps(log))
    os.replace(p.with_name('t.json'), p)
    pf = rel.preflight(env)
    assert not pf['ok'] and 'PinLogInvalid' in failing(pf)['pinned_files']
    with pytest.raises(rel.PinLogInvalid):
        rp.plan(env)


def test_pin_log_cannot_repin_other_paths(env):
    entry = {'seq': 0, 'path': f'{cfg.INTEGRATION_REL}/monthly_forecast_dno.csv', 'old_sha256': cfg.PINNED_SHA256[f'{cfg.INTEGRATION_REL}/monthly_forecast_dno.csv'],
             'new_sha256': '0' * 64, 'prev_entry_sha256': ''}
    entry['entry_sha256'] = rel.seal_entry(entry)
    rel._makedirs(Path(env), ['pins'])
    pin_log(env).write_text(json.dumps([entry]))
    pf = rel.preflight(env)
    assert not pf['ok'] and 'may not be re-pinned' in failing(pf)['pinned_files']


def test_live_repository_repin_is_refused_and_writes_nothing():
    with pytest.raises(rel.LiveReleaseBlocked):
        rp.repin(str(REPO), 'REPIN anything')
    assert not (REPO / rel.PIN_LOG_REL).exists() and not REPO.joinpath(*cfg.STATE_REL.split('/')).exists()
