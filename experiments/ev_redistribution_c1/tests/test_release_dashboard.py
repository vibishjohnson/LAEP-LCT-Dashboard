"""Dashboard regression after a simulated promotion (and after rollback) in a disposable environment.

The harness runs the environment's copy of dashboard.html with the environment as its working directory, so every
fetch (EV actuals, forecasts, HP, comparison files) is served from the promoted disposable files, not from the repository.
Expected results: identical to the Phase 4 shadow-redirect run (independently validated by test_dashboard_regression.py),
with independent spot checks here; after rollback, identical to the production run.
"""
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

EXP = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(EXP / 'release'))
import c1_release as rel  # noqa: E402
import disposable_env as denv  # noqa: E402
import release_config as cfg  # noqa: E402

REPO = cfg.REPO
HARNESS = EXP / 'dashboard_regression' / 'dashboard_harness.js'
SHADOW = EXP / 'shadow_integration'
MODES = ('ev', 'ev_local', 'nonev', 'path')
FIELDS = ('plots', 'kpis', 'titles', 'caveats', 'cumulativeVisible', 'state', 'handlerErrors')
OS_, OC, OQ = 'Estimated EV stock', 'Estimated cumulative net stock additions', 'Estimated quarterly net stock change'
FORECASTS = ('DFES', 'UKPN — Holistic Transition before tRESP', 'UKPN — Holistic Transition Basic before tRESP')
DATES = [p + '-01' for p in cfg.EXPECTED_PERIODS]

pytestmark = pytest.mark.skipif(shutil.which('node') is None, reason='node not installed')


def harness(mode, cwd, redirect=None, tmp=None):
    args = ['node', '--no-opt', '--no-sparkplug', str(HARNESS), mode]
    if redirect:
        rf = Path(tmp) / f'redirect_{mode}.json'
        rf.write_text(json.dumps({k: str(v) for k, v in redirect.items()}), encoding='utf-8')
        args.append(str(rf))
    out = subprocess.run(args, cwd=cwd, capture_output=True, timeout=600)
    data = json.loads(out.stdout.decode('utf-8'))
    assert 'fatal' not in data, data.get('fatal')
    return data


def labels(data):
    return {r['label']: r for r in data['results']}


def tr(result, chart):
    return {t['name']: t for t in result['plots'][chart]['traces']}


def kpi(result, name):
    card = next(k for k in result['kpis'] if f'|{name}|' in k)
    _, _, value, unit, _ = card.split('|')
    return int(value.replace(',', '')), unit


@pytest.fixture(scope='module')
def runs(tmp_path_factory):
    tmp = tmp_path_factory.mktemp('dash')
    base = Path(tempfile.gettempdir())
    template, env = base / f'c1rDT-{uuid.uuid4().hex[:6]}', base / f'c1rD-{uuid.uuid4().hex[:6]}'
    denv.build(template)
    denv.build(env, template)
    try:
        out = {'env': env}
        out.update({('prod', m): harness(m, REPO) for m in MODES})
        out.update({('shadow', m): harness(m, REPO, {n: SHADOW / n for n in cfg.TARGET_NAMES}, tmp) for m in MODES})
        pf = rel.preflight(env)
        out['promotion'] = rel.promote(env, 'c1-20261012-dash', rel.confirm_token('PROMOTE', 'c1-20261012-dash', pf['digest']))
        out['promoted_targets'] = {n: rel.sha256_file(env / cfg.INTEGRATION_REL / n) for n in cfg.TARGET_NAMES}
        out.update({('promoted', m): harness(m, env) for m in MODES})
        m = rel.load_manifest(env, 'c1-20261012-dash')
        out['rollback'] = rel.rollback(env, 'c1-20261012-dash', rel.confirm_token('ROLLBACK', 'c1-20261012-dash', m['manifest_sha256']))
        out['rolled_back_targets'] = {n: rel.sha256_file(env / cfg.INTEGRATION_REL / n) for n in cfg.TARGET_NAMES}
        out.update({('rolledback', m_): harness(m_, env) for m_ in MODES})
        yield out
    finally:
        for d in (env, template):
            shutil.rmtree(d, onerror=lambda f, p, _: (os.chmod(p, stat.S_IWRITE), f(p)))


def test_promotion_and_rollback_in_environment(runs):
    assert runs['promotion']['status'] == 'COMPLETE' and runs['promoted_targets'] == cfg.SHADOW_SHA256
    assert runs['rollback']['status'] == 'ROLLED_BACK' and runs['rolled_back_targets'] == cfg.METHOD_A_TARGET_SHA256


@pytest.mark.parametrize('mode', MODES)
def test_dashboard_reads_only_the_disposable_environment(runs, mode):
    for kind in ('promoted', 'rolledback'):
        data = runs[(kind, mode)]
        assert data['startup'] == {'error': '', 'errorShown': False}
        assert all(not f['target'].startswith('..') and not os.path.isabs(f['target']) for f in data['fetched'])
        assert not any(r['handlerErrors'] or r['kpiError'] for r in data['results'])


@pytest.mark.parametrize('mode', MODES)
def test_promoted_dashboard_equals_validated_shadow_run(runs, mode):
    p, s = labels(runs[('promoted', mode)]), labels(runs[('shadow', mode)])
    assert list(p) == list(s) and p
    for label in p:
        for field in FIELDS:
            assert p[label][field] == s[label][field], (label, field)


@pytest.mark.parametrize('mode', MODES)
def test_rolled_back_dashboard_equals_production(runs, mode):
    r, p = labels(runs[('rolledback', mode)]), labels(runs[('prod', mode)])
    assert list(r) == list(p)
    for label in r:
        for field in FIELDS:
            assert r[label][field] == p[label][field], (label, field)


def test_non_ev_views_unchanged_after_promotion(runs):
    p, prod = labels(runs[('promoted', 'nonev')]), labels(runs[('prod', 'nonev')])
    assert len(p) == 24
    assert all(p[l][f] == prod[l][f] for l in p for f in FIELDS)


def test_promoted_ev_series_independent_checks(runs):
    env = runs['env']
    shadow = {n: pd.read_csv(SHADOW / n) for n in cfg.TARGET_NAMES}
    ev = {**labels(runs[('promoted', 'ev')]), **labels(runs[('promoted', 'ev_local')])}
    prod = {**labels(runs[('prod', 'ev')]), **labels(runs[('prod', 'ev_local')])}
    assert len(ev) == 12 + 21
    for label, r in ev.items():
        level, area, sub = label.split('|')
        stock, cum, qtr = tr(r, 'stockChart')[OS_], tr(r, 'evCumulativeChart')[OC], tr(r, 'monthlyChart')[OQ]
        assert stock['x'] == DATES and cum['x'] == DATES and qtr['x'] == DATES[1:] and cum['y'][0] == 0
        assert kpi(r, 'Earliest Observation')[1] == 'vehicles (2025-03)' and kpi(r, 'Latest Actual Stock')[1] == 'vehicles (2026-03)'
        fuels = ['BEV', 'PHEV'] if sub == 'Total' else [sub]
        if level == 'Network':
            assert stock['y'][0] == tr(prod[label], 'stockChart')[OS_]['y'][0]             # DFES opening, unchanged
            d = shadow['ev_actuals_dno.csv']
            d = d[d.EV_Type.isin(fuels) & ((d.DNO == area) if area != 'UKPN Total' else True)]
            series = d.groupby('period').install_count.sum()
        elif level == 'LA':
            d = shadow['ev_actuals_la.csv']
            series = d[(d.LAD22NM == area) & d.EV_Type.isin(fuels)].groupby('period').value.sum()
        else:
            d = shadow['ev_actuals_laep.csv']
            series = d[(d.LAEP == area) & d.EV_Type.isin(fuels)].groupby('period').value.sum()
        expected = [int(series[p]) for p in cfg.EXPECTED_PERIODS]
        if level != 'Network':
            assert stock['y'][0] == expected[0]                                              # C1 March 2025 opening
        assert stock['y'][1:] == expected[1:]
        assert kpi(r, 'Earliest Observation')[0] == expected[0] and kpi(r, 'Latest Actual Stock')[0] == expected[-1]
        for chart in ('stockChart', 'evCumulativeChart', 'monthlyChart'):
            for name in FORECASTS:
                assert tr(r, chart)[name] == tr(prod[label], chart)[name], (label, chart, name)
    assert kpi(ev['Network|UKPN Total|Total'], 'Latest Actual Stock')[0] == 896098
    assert kpi(prod['Network|UKPN Total|Total'], 'Earliest Observation')[1] == 'vehicles (2011-12)'
    assert min(tr(ev['LA|Westminster|BEV'], 'monthlyChart')[OQ]['y']) < 0                  # negative local change visible
