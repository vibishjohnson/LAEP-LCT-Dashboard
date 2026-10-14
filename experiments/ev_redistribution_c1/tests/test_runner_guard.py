"""Guarded integration runner: publication behaviour on disposable copies (no real publication).

Uses the version-controlled copy in integration_runner/dashboard_integration/. The publish folder starts as a
post-C1-release dashboard integration folder (C1 EV actuals already published).
"""
import hashlib
import importlib
import json
import os
import shutil
import sys
from pathlib import Path

import pandas as pd
import pytest

EXP = Path(__file__).resolve().parents[1]
REPO = EXP.parents[1]
PKG = REPO / 'integration_runner' / 'dashboard_integration'
sys.path.insert(0, str(EXP))
from local_paths import runner_dir  # noqa: E402
EXTERNAL = runner_dir() or Path('__runner_not_configured__')
INTEG = REPO / 'project' / 'output_processed' / 'integration'
SHADOW = EXP / 'shadow_integration'
EV = ('ev_actuals_dno.csv', 'ev_actuals_la.csv', 'ev_actuals_laep.csv')
FORECASTS = ('monthly_forecast_dno.csv', 'monthly_forecast_la.csv', 'monthly_forecast_laep.csv')
sha = lambda p: hashlib.sha256(Path(p).read_bytes()).hexdigest()


@pytest.fixture(scope='module')
def pkg(tmp_path_factory):
    root = tmp_path_factory.mktemp('runner')
    shutil.copytree(PKG, root / 'dashboard_integration', ignore=shutil.ignore_patterns('__pycache__'))
    sys.path.insert(0, str(root))
    for m in [m for m in sys.modules if m.split('.')[0] == 'dashboard_integration']:
        del sys.modules[m]
    guard = importlib.import_module('dashboard_integration.publish_guard')
    runner = importlib.import_module('dashboard_integration.run_integration')     # imports cleanly with the guard
    yield guard, runner, root
    sys.path.remove(str(root))
    for m in [m for m in sys.modules if m.split('.')[0] == 'dashboard_integration']:
        del sys.modules[m]


@pytest.fixture(scope='module')
def source_outputs(tmp_path_factory):
    """Runner outputs as produced today: DFES/NESO forecasts (identical to the restored files), geography, metadata."""
    d = tmp_path_factory.mktemp('src')
    for n in FORECASTS:
        shutil.copyfile(INTEG / n, d / n)
    shutil.copyfile(INTEG / 'geography_lookup.csv', d / 'geography_lookup.csv')
    meta = json.loads((INTEG / 'metadata.json').read_text())
    meta.pop('ev_vintage', None)
    (d / 'metadata.json').write_text(json.dumps(meta, indent=2))
    (d / 'run_report.md').write_text('# report\n')
    for n in EV:                                              # Method A actuals the runner still builds for QA
        shutil.copyfile(INTEG / n, d / n)
    return d


@pytest.fixture
def dirs(tmp_path, source_outputs):
    out, pub = tmp_path / 'outputs', tmp_path / 'publish'
    shutil.copytree(source_outputs, out)
    pub.mkdir()
    for n in FORECASTS + ('geography_lookup.csv', 'metadata.json'):
        shutil.copyfile(INTEG / n, pub / n)
    for n in EV:                                              # post-release: C1 is live
        shutil.copyfile(SHADOW / n, pub / n)
    return out, pub, tmp_path / 'backups'


def tree(d):
    return {p.name: sha(p) for p in Path(d).iterdir() if p.is_file()} if Path(d).exists() else {}


def rewrite_csv(path, fn):
    df = pd.read_csv(path, low_memory=False)
    fn(df).to_csv(path, index=False)


def test_valid_outputs_publish_forecasts_and_never_ev_actuals(pkg, dirs):
    guard, _, _ = pkg
    out, pub, backups = dirs
    before = tree(pub)
    report = guard.publish(out, pub, backups)
    after = tree(pub)
    assert sorted(report['published']) == sorted(guard.PUBLISH_FILES) and report['ev_actuals_unchanged']
    for n in EV:
        assert after[n] == before[n] == sha(SHADOW / n)                    # C1 untouched; Method A in outputs ignored
    for n in guard.PUBLISH_FILES:
        assert after[n] == sha(out / n)
    assert not [p for p in pub.iterdir() if p.name.endswith('.publish_tmp')]
    b = Path(report['backup_dir'])
    manifest = json.loads((b / 'manifest.json').read_text())
    assert {n: sha(b / n) for n in manifest['files_sha256']} == manifest['files_sha256']
    assert manifest['files_sha256']['monthly_forecast_dno.csv'] == before['monthly_forecast_dno.csv']


def test_restored_forecast_republished_byte_identical(pkg, dirs):
    guard, _, _ = pkg
    out, pub, backups = dirs
    guard.publish(out, pub, backups)
    for n in FORECASTS:
        assert sha(pub / n) == sha(INTEG / n)


def _drop_month(df):
    m = (df.DNO == 'EPN') & (df.forecast_source == 'NESO') & (df.period == '2030-06')
    return df[~m]


CASES = {
    'duplicate keys': ('monthly_forecast_dno.csv', lambda df: pd.concat([df, df.head(3)]), 'duplicate keys'),
    'missing NESO HTB series': ('monthly_forecast_dno.csv', lambda df: df[df.forecast_scenario != 'HolisticTransitionBasic'], 'missing series NESO/HolisticTransitionBasic'),
    'missing month': ('monthly_forecast_dno.csv', _drop_month, 'incomplete coverage'),
    'actuals inside forecast': ('monthly_forecast_dno.csv', lambda df: df.assign(observation_source=['ACTUAL'] + ['DFES'] * (len(df) - 1)), 'not DFES/NESO'),
    'negative value': ('monthly_forecast_laep.csv', lambda df: df.assign(value=[-1.0] + list(df.value[1:])), 'negative'),
    'LA disappears': ('monthly_forecast_la.csv', lambda df: df[df.LAD22CD != df.LAD22CD.iloc[0]], 'geographies would disappear'),
    'range shrinks': ('monthly_forecast_laep.csv', lambda df: df[df.period <= '2050-03'], 'period range shrinks'),
    'missing column': ('monthly_forecast_dno.csv', lambda df: df.drop(columns=['forecast_scenario']), 'missing columns'),
}


@pytest.mark.parametrize('case', list(CASES))
def test_invalid_forecasts_refused_with_nothing_written(pkg, dirs, case):
    guard, _, _ = pkg
    out, pub, backups = dirs
    name, fn, expect = CASES[case]
    rewrite_csv(out / name, fn)
    before = tree(pub)
    with pytest.raises(guard.PublishRefused, match=expect):
        guard.publish(out, pub, backups)
    assert tree(pub) == before and not backups.exists()


def test_missing_output_refused(pkg, dirs):
    guard, _, _ = pkg
    out, pub, backups = dirs
    os.remove(out / 'monthly_forecast_la.csv')
    before = tree(pub)
    with pytest.raises(guard.PublishRefused, match='missing output monthly_forecast_la.csv'):
        guard.publish(out, pub, backups)
    assert tree(pub) == before


def test_geography_change_needs_deliberate_override(pkg, dirs, monkeypatch):
    guard, _, _ = pkg
    out, pub, backups = dirs
    (out / 'geography_lookup.csv').write_bytes((out / 'geography_lookup.csv').read_bytes() + b'X,,,,,,,,,\n')
    with pytest.raises(guard.PublishRefused, match='geography_lookup.csv would change'):
        guard.publish(out, pub, backups)
    monkeypatch.setenv('C1_ALLOW_GEOGRAPHY_CHANGE', '1')
    assert guard.publish(out, pub, backups)['ev_actuals_unchanged']


def test_published_nowcast_rows_are_not_dropped_silently(pkg, dirs, monkeypatch):
    guard, _, _ = pkg
    out, pub, backups = dirs
    df = pd.read_csv(pub / 'monthly_forecast_dno.csv', low_memory=False)
    smmt = df[(df.forecast_source == 'DFES') & (df.period == '2026-04')].assign(forecast_source='SMMT_NOWCAST', forecast_scenario='Nowcast',
                                                                              observation_source='SMMT_NOWCAST')
    pd.concat([df, smmt]).to_csv(pub / 'monthly_forecast_dno.csv', index=False)
    before = tree(pub)
    with pytest.raises(guard.PublishRefused, match='drop SMMT_NOWCAST'):
        guard.publish(out, pub, backups)
    assert tree(pub) == before
    monkeypatch.setenv('C1_ALLOW_DROP_NOWCAST', '1')
    guard.publish(out, pub, backups)


def test_metadata_must_match_published_actuals(pkg, dirs):
    guard, _, _ = pkg
    out, pub, backups = dirs
    meta = json.loads((out / 'metadata.json').read_text())
    meta['latest_actual_period'] = '2026-06'
    (out / 'metadata.json').write_text(json.dumps(meta))
    with pytest.raises(guard.PublishRefused, match='latest_actual_period'):
        guard.publish(out, pub, backups)


def test_ev_actuals_can_never_be_added_to_the_publish_list(pkg, dirs, monkeypatch):
    guard, _, _ = pkg
    out, pub, backups = dirs
    monkeypatch.setattr(guard, 'PUBLISH_FILES', guard.PUBLISH_FILES + ('ev_actuals_dno.csv',))
    before = tree(pub)
    with pytest.raises(guard.PublishRefused, match='EV actuals'):
        guard.publish(out, pub, backups)
    assert tree(pub) == before


def test_runner_has_a_single_guarded_publish_path(pkg):
    _, runner, root = pkg
    src = (root / 'dashboard_integration' / 'run_integration.py').read_text(encoding='utf-8')
    body = src[src.index('def run() -> dict:'):]
    assert body.count('DASHBOARD_INTEGRATION_PUBLISH') == 1 and 'publish(OUTPUT_DIR, DASHBOARD_INTEGRATION_PUBLISH)' in body
    assert 'copy2' not in body and runner.publish.__module__.endswith('publish_guard')
    original = (REPO / 'integration_runner' / 'original' / 'run_integration.py').read_text(encoding='utf-8').splitlines()
    patched = src.splitlines()
    removed = [l for l in original if l not in patched]
    assert all('ev_actuals' in l or 'DASHBOARD_INTEGRATION_PUBLISH' in l or l.strip().startswith(('"', 'for name', ']:', 'shutil.copy2', 'print("QA passed'))
               for l in removed), removed


def test_operational_runner_matches_version_controlled_copy():
    if not EXTERNAL.exists():
        pytest.skip('external runner not present')
    manifest = json.loads((REPO / 'integration_runner' / 'SOURCE_MANIFEST.json').read_text())
    norm = lambda p: hashlib.sha256(Path(p).read_bytes().replace(b'\r\n', b'\n')).hexdigest()
    sys.path.insert(0, str(REPO / 'integration_runner'))
    import provenance
    repo_expected = provenance.expected_repository_hashes(manifest)       # operational, except the sanitised path constants
    for name, h in manifest['current_sha256_eol_normalised'].items():
        assert norm(PKG / name) == repo_expected[name], f'repo copy drifted: {name}'
        assert norm(EXTERNAL / name) == h, f'operational runner differs from the version-controlled copy: {name}'
    assert provenance.problems(EXTERNAL, manifest) == []
    for name, h in manifest['current_sha256'].items():
        assert sha(EXTERNAL / name) == h, f'operational runner bytes changed: {name}'
    assert norm(REPO / 'integration_runner' / 'original' / 'run_integration.py') == manifest['original_sha256_eol_normalised']['run_integration.py']
