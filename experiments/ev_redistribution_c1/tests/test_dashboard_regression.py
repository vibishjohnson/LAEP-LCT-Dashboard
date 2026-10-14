"""Regression tests: the working-tree dashboard.html run against production vs the shadow C1 integration files.

The page script runs headless in Node (dashboard_regression/dashboard_harness.js); the harness only reads files.
Expected values are derived here independently from the shadow CSVs and the production DFES forecast rows.
"""
import hashlib
import json
import shutil
import subprocess
from pathlib import Path

import pandas as pd
import pytest

EXP = Path(__file__).resolve().parents[1]
REPO = EXP.parents[1]
SHADOW = EXP / 'shadow_integration'
HARNESS = EXP / 'dashboard_regression' / 'dashboard_harness.js'
PROD = REPO / 'project/output_processed/integration'
EV_FILES = ('ev_actuals_dno.csv', 'ev_actuals_la.csv', 'ev_actuals_laep.csv')
PERIODS = ['2025-03', '2025-06', '2025-09', '2025-12', '2026-03']
DATES = [p + '-01' for p in PERIODS]
OS, OC, OQ = 'Estimated EV stock', 'Estimated cumulative net stock additions', 'Estimated quarterly net stock change'
CHARTS = ('stockChart', 'evCumulativeChart', 'monthlyChart')
FORECASTS = ('DFES', 'UKPN — Holistic Transition before tRESP', 'UKPN — Holistic Transition Basic before tRESP')
LA_CODES = {'Peterborough': 'E06000031', 'Westminster': 'E09000033'}
NETWORK_AREAS = ['UKPN Total', 'EPN', 'LPN', 'SPN']
LOCAL = [f'LA|{n}' for n in LA_CODES] + [f'LAEP|{n} LAEP' for n in ('Peterborough', 'Norwich', 'Westminster', 'Outer East', 'West London')]
SUBTYPES = ('Total', 'BEV', 'PHEV')
digest = lambda p: hashlib.sha256(Path(p).read_bytes()).hexdigest()

pytestmark = pytest.mark.skipif(shutil.which('node') is None, reason='node not installed')


def run_harness(mode, redirect=None, tmp=None):
    args = ['node', '--no-opt', '--no-sparkplug', str(HARNESS), mode]
    if redirect:
        rf = Path(tmp) / f'redirect_{mode}.json'
        rf.write_text(json.dumps({k: str(v) for k, v in redirect.items()}), encoding='utf-8')
        args.append(str(rf))
    out = subprocess.run(args, cwd=REPO, capture_output=True, timeout=600)
    data = json.loads(out.stdout.decode('utf-8'))
    assert 'fatal' not in data, data.get('fatal')
    return data


def by_label(data):
    return {r['label']: r for r in data['results']}


def traces(result, chart):
    return {t['name']: t for t in result['plots'][chart]['traces']}


@pytest.fixture(scope='module')
def runs(tmp_path_factory):
    tmp = tmp_path_factory.mktemp('harness')
    protected = [REPO / 'dashboard.html', *(PROD / f for f in EV_FILES), PROD / 'metadata.json', PROD / 'monthly_forecast_dno.csv',
                 PROD / 'monthly_forecast_la.csv', PROD / 'monthly_forecast_laep.csv', *(SHADOW / f for f in EV_FILES)]
    before = {p: digest(p) for p in protected}
    redirect = {f: SHADOW / f for f in EV_FILES}
    out = {}
    for mode in ('ev', 'ev_local', 'nonev', 'path'):
        out[('prod', mode)] = run_harness(mode)
        out[('c1', mode)] = run_harness(mode, redirect, tmp)
    out['unchanged'] = before == {p: digest(p) for p in protected}
    out['tmp'] = tmp
    return out


@pytest.fixture(scope='module')
def expected():
    dno = pd.read_csv(SHADOW / 'ev_actuals_dno.csv')
    la = pd.read_csv(SHADOW / 'ev_actuals_la.csv')
    laep = pd.read_csv(SHADOW / 'ev_actuals_laep.csv')
    fc = pd.read_csv(PROD / 'monthly_forecast_dno.csv')
    dfes = fc[(fc.forecast_source == 'DFES') & (fc.period == '2025-03')]
    exp = {}
    for sub in SUBTYPES:
        fuels = ['BEV', 'PHEV'] if sub == 'Total' else [sub]
        for area in NETWORK_AREAS:
            d = dno[dno.EV_Type.isin(fuels)]; f = dfes[dfes.EV_Type.isin(fuels)]
            if area != 'UKPN Total':
                d, f = d[d.DNO == area], f[f.DNO == area]
            s = d.groupby('period').install_count.sum()
            exp[f'Network|{area}|{sub}'] = dict(actual=[int(s[p]) for p in PERIODS], opening=float(f.value.sum()))
        for name, code in LA_CODES.items():
            s = la[(la.LAD22CD == code) & la.EV_Type.isin(fuels)].groupby('period').value.sum()
            exp[f'LA|{name}|{sub}'] = dict(actual=[int(s[p]) for p in PERIODS], opening=float(s['2025-03']))
        for name in ('Peterborough', 'Norwich', 'Westminster', 'Outer East', 'West London'):
            s = laep[(laep.LAEP == f'{name} LAEP') & laep.EV_Type.isin(fuels)].groupby('period').value.sum()
            exp[f'LAEP|{name} LAEP|{sub}'] = dict(actual=[int(s[p]) for p in PERIODS], opening=float(s['2025-03']))
    return exp


C1_LABELS = [f'Network|{a}|{s}' for a in NETWORK_AREAS for s in SUBTYPES] + [f'{g}|{s}' for g in LOCAL for s in SUBTYPES]


def c1_result(runs, label):
    return {**by_label(runs[('c1', 'ev')]), **by_label(runs[('c1', 'ev_local')])}[label]


def prod_result(runs, label):
    return {**by_label(runs[('prod', 'ev')]), **by_label(runs[('prod', 'ev_local')])}[label]


# ---------------------------------------------------------------- run hygiene
def test_harness_read_only_and_startup_clean(runs):
    assert runs['unchanged']
    for key, data in runs.items():
        if isinstance(key, tuple):
            assert data['startup'] == {'error': '', 'errorShown': False}, key
            assert not any(r['kpiError'] for r in data['results']), key


def test_c1_run_reads_shadow_files_and_production_run_does_not(runs):
    for mode in ('ev_local', 'nonev'):          # LA/LAEP files are fetched lazily on first LA/LAEP selection
        c1 = {Path(f['url']).name: f['target'] for f in runs[('c1', mode)]['fetched']}
        prod = {Path(f['url']).name: f['target'] for f in runs[('prod', mode)]['fetched']}
        for f in EV_FILES:
            assert 'shadow_integration' in c1[f] and 'shadow_integration' not in prod[f]
        assert set(c1) == set(prod)


def test_all_selections_present(runs):
    labels = {**by_label(runs[('c1', 'ev')]), **by_label(runs[('c1', 'ev_local')])}
    assert set(C1_LABELS) <= set(labels)
    for label in C1_LABELS:
        r = labels[label]
        assert r['state']['tech'] == 'EV Vehicles' and r['state']['ev'] == label.rsplit('|', 1)[1]
        assert r['cumulativeVisible'] != 'none'


# ---------------------------------------------------------------- C1 series values
@pytest.mark.parametrize('label', C1_LABELS)
def test_three_ev_charts_match_shadow_c1(runs, expected, label):
    r, e = c1_result(runs, label), expected[label]
    stock, cum, qtr = (traces(r, c)[n] for c, n in zip(CHARTS, (OS, OC, OQ)))
    displayed = [e['opening']] + e['actual'][1:]       # Network opens on DFES, LA/LAEP on C1 March 2025
    assert stock['x'] == DATES and cum['x'] == DATES and qtr['x'] == DATES[1:]
    assert stock['y'] == pytest.approx(displayed, abs=1e-6)
    assert cum['y'][0] == 0 and cum['y'] == pytest.approx([v - displayed[0] for v in displayed], abs=1e-6)
    assert qtr['y'] == pytest.approx([b - a for a, b in zip(displayed, displayed[1:])], abs=1e-6)
    assert sum(qtr['y']) == pytest.approx(cum['y'][-1], abs=1e-6)


@pytest.mark.parametrize('label', [l for l in C1_LABELS if not l.startswith('Network')])
def test_local_opening_is_c1_march_2025(runs, expected, label):
    assert traces(c1_result(runs, label), 'stockChart')[OS]['y'][0] == expected[label]['actual'][0]


@pytest.mark.parametrize('label', [l for l in C1_LABELS if l.startswith('Network')])
def test_network_opening_is_dfes_and_unchanged_from_production(runs, expected, label):
    y = traces(c1_result(runs, label), 'stockChart')[OS]['y'][0]
    assert y == pytest.approx(expected[label]['opening']) == traces(prod_result(runs, label), 'stockChart')[OS]['y'][0]


def test_negative_local_change_remains_visible(runs, expected):
    r = c1_result(runs, 'LA|Westminster|BEV')
    q, c = traces(r, 'monthlyChart')[OQ]['y'], traces(r, 'evCumulativeChart')[OC]['y']
    a = expected['LA|Westminster|BEV']['actual']
    assert q[0] == a[1] - a[0] < 0 and min(c) < 0
    assert r['plots']['monthlyChart']['traces'][0]['y'] == q


def test_no_pre_2025_points_in_any_ev_chart(runs):
    for label in C1_LABELS:
        r = c1_result(runs, label)
        for chart in CHARTS:
            for t in r['plots'][chart]['traces']:
                if t['name'] in (OS, OC, OQ):
                    assert min(t['x']) >= '2025-03-01' and t['x'] == sorted(t['x'])


# ---------------------------------------------------------------- invariance vs production
@pytest.mark.parametrize('label', C1_LABELS)
def test_forecasts_titles_caveats_axes_unchanged(runs, label):
    c, p = c1_result(runs, label), prod_result(runs, label)
    assert c['titles'] == p['titles'] and c['caveats'] == p['caveats']
    for chart in CHARTS:
        ct, pt = traces(c, chart), traces(p, chart)
        assert list(ct) == list(pt)                             # legend entries and order
        for name in FORECASTS:
            assert ct[name] == pt[name], (chart, name)
        assert c['plots'][chart]['layout'] == p['plots'][chart]['layout']


def test_bev_plus_phev_equals_total_in_every_chart(runs):
    groups = {l.rsplit('|', 1)[0] for l in C1_LABELS}
    for g in groups:
        tot, bev, phev = (c1_result(runs, f'{g}|{s}') for s in SUBTYPES)
        for chart in CHARTS:
            for name in (OS, OC, OQ, *FORECASTS):
                if name not in traces(tot, chart):
                    continue
                t, b, p = (traces(r, chart)[name]['y'] for r in (tot, bev, phev))
                assert t == pytest.approx([x + y for x, y in zip(b, p)], abs=1e-6), (g, chart, name)


def test_dno_charts_sum_to_ukpn(runs):
    for s in SUBTYPES:
        for chart, name in zip(CHARTS, (OS, OC, OQ)):
            u, *parts = (traces(c1_result(runs, f'Network|{a}|{s}'), chart)[name]['y'] for a in NETWORK_AREAS)
            assert u == pytest.approx([sum(v) for v in zip(*parts)], abs=1e-6), (s, chart)


def test_switching_path_matches_direct_selection(runs):
    direct = {'Network|UKPN Total': 'Network|UKPN Total', 'Network|UKPN Total#2': 'Network|UKPN Total', 'LA|Peterborough': 'LA|Peterborough',
              'LAEP|Outer East LAEP': 'LAEP|Outer East LAEP', 'Network|EPN': 'Network|EPN', 'LAEP|Norwich LAEP': 'LAEP|Norwich LAEP'}
    for label, r in by_label(runs[('c1', 'path')]).items():
        _, sub, lvl, area = label.split('|')
        ref = c1_result(runs, f'{direct[f"{lvl}|{area}"]}|{sub}')
        assert r['plots'] == ref['plots'] and r['kpis'] == ref['kpis'] and r['titles'] == ref['titles'], label


# ---------------------------------------------------------------- KPIs
def kpi(r, name):
    card = next(k for k in r['kpis'] if f'|{name}|' in k)
    _, _, value, unit, _ = card.split('|')
    return int(value.replace(',', '')), unit


@pytest.mark.parametrize('label', C1_LABELS)
def test_kpis_earliest_and_latest_actual(runs, expected, label):
    r, e = c1_result(runs, label), expected[label]
    assert kpi(r, 'Earliest Observation') == (e['actual'][0], 'vehicles (2025-03)')
    assert kpi(r, 'Latest Actual Stock') == (e['actual'][-1], 'vehicles (2026-03)')
    p = prod_result(runs, label)
    assert [k for k in r['kpis'] if 'at latest actual' in k] == [k for k in p['kpis'] if 'at latest actual' in k]


def test_production_network_earliest_observation_is_method_a_history(runs):
    # Documents why the shadow files carry only five quarters: production shows 2011-12 at network level.
    assert kpi(prod_result(runs, 'Network|UKPN Total|Total'), 'Earliest Observation')[1] == 'vehicles (2011-12)'


# ---------------------------------------------------------------- cross-technology isolation
def test_non_ev_views_identical(runs):
    p, c = by_label(runs[('prod', 'nonev')]), by_label(runs[('c1', 'nonev')])
    assert list(p) == list(c) and len(p) == 24
    for label in p:
        for field in ('plots', 'titles', 'caveats', 'kpis', 'cumulativeVisible', 'state'):
            assert p[label][field] == c[label][field], (label, field)


def damaged_redirect(tmp_path, name, damage):
    redirect = {f: SHADOW / f for f in EV_FILES}
    bad = tmp_path / name                          # never created for 'missing_file' -> the fetch returns 404
    if damage == 'missing_field':
        lines = (SHADOW / name).read_bytes().split(b'\r\n')
        lines[3] = lines[3].rsplit(b',', 1)[0]
        bad.write_bytes(b'\r\n'.join(lines))
    redirect[name] = bad
    return redirect


def handler_errors(data):
    return [e for r in data['results'] for e in r['handlerErrors']]


@pytest.mark.parametrize('damage', ['missing_field', 'missing_file'])
def test_damaged_dno_file_stops_whole_dashboard_at_startup(damage, tmp_path):
    """Promotion risk: the DNO EV file loads at startup, so a bad copy takes down HP, Solar and Chargers too."""
    data = run_harness('nonev', damaged_redirect(tmp_path, 'ev_actuals_dno.csv', damage), tmp_path)
    assert data['startup']['errorShown'] and data['results'] == []


@pytest.mark.parametrize('name,level', [('ev_actuals_la.csv', 'LA'), ('ev_actuals_laep.csv', 'LAEP')])
def test_malformed_local_file_blanks_ev_and_heat_pump_views_without_visible_error(runs, name, level, tmp_path):
    """Promotion risk: the LA/LAEP files load lazily inside the level-button handler, which has no catch.
    A parse error is an unhandled rejection: no message is shown, and the HP LA/LAEP load that follows is skipped."""
    redirect = damaged_redirect(tmp_path, name, 'missing_field')
    nonev, ev = run_harness('nonev', redirect, tmp_path), run_harness('ev_local', redirect, tmp_path)
    assert not nonev['startup']['errorShown'] and not ev['startup']['errorShown']
    errors = handler_errors(nonev) + handler_errors(ev)
    assert errors and all(e.startswith(f'level {level}: Row 3: expected') for e in errors)
    clean_nonev, damaged_nonev = by_label(runs[('c1', 'nonev')]), by_label(nonev)
    for label, r in damaged_nonev.items():
        tech, lvl = label.split('|')[:2]
        changed = r['plots'] != clean_nonev[label]['plots']
        assert changed == (tech == 'Heat Pump' and lvl == level), label
        if changed:
            assert all(t['y'] == [] for t in r['plots']['stockChart']['traces'])
    for label, r in by_label(ev).items():
        if label.startswith(level + '|'):
            assert r['plots']['stockChart']['traces'] == [] and '|Latest Actual Stock|—|' in ''.join(r['kpis'])


@pytest.mark.parametrize('name,level', [('ev_actuals_la.csv', 'LA'), ('ev_actuals_laep.csv', 'LAEP')])
def test_missing_local_file_silently_blanks_ev_views_only(runs, name, level, tmp_path):
    redirect = damaged_redirect(tmp_path, name, 'missing_file')
    nonev, ev = run_harness('nonev', redirect, tmp_path), run_harness('ev_local', redirect, tmp_path)
    assert not handler_errors(nonev) and not handler_errors(ev) and not ev['startup']['errorShown']
    clean = by_label(runs[('c1', 'nonev')])
    assert all(r['plots'] == clean[l]['plots'] and r['kpis'] == clean[l]['kpis'] for l, r in by_label(nonev).items())
    for label, r in by_label(ev).items():
        assert (r['plots']['stockChart']['traces'] == []) == label.startswith(level + '|'), label
