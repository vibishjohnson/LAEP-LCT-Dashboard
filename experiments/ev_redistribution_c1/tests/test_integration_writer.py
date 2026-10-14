"""Tests for the C1 integration writer. Production files are only read; all writes go to pytest temp dirs."""
import hashlib
import json
import os
import re
import sys
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

EXP = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(EXP))
import integration_writer as w  # noqa: E402

REPO = w.REPO
PROD = w.PROD_INTEGRATION
PROTECTED = [REPO / 'dashboard.html', PROD / 'ev_actuals_dno.csv', PROD / 'ev_actuals_la.csv', PROD / 'ev_actuals_laep.csv',
             PROD / 'metadata.json', PROD / 'monthly_forecast_dno.csv', PROD / 'monthly_forecast_la.csv', PROD / 'monthly_forecast_laep.csv',
             PROD / 'geography_lookup.csv', REPO / 'project/output_processed/ev_quarterly_lsoa.csv', REPO / 'ev/df_VEH0135.csv',
             REPO / 'lookups/LSOA to DNO.csv', EXP / 'outputs/c1_lsoa_quarterly.csv', EXP / 'outputs/run_metadata.json']
digest = lambda p: hashlib.sha256(Path(p).read_bytes()).hexdigest()


@pytest.fixture(scope='module')
def written(tmp_path_factory):
    before = {p: digest(p) for p in PROTECTED}
    shadow, archive = tmp_path_factory.mktemp('shadow'), tmp_path_factory.mktemp('archive')
    dno, la, laep, manifest, arch = w.write_all(shadow, archive)
    after = {p: digest(p) for p in PROTECTED}
    files = {n: pd.read_csv(shadow / n) for n in ('ev_actuals_dno.csv', 'ev_actuals_la.csv', 'ev_actuals_laep.csv')}
    return dict(shadow=shadow, archive=archive, manifest=manifest, archive_manifest=arch, files=files, before=before, after=after,
                canon=w.load_geography(), c1=pd.read_csv(w.C1_LSOA))


# ---------------------------------------------------------------- safety
def test_no_protected_file_changed(written):
    assert written['before'] == written['after']


@pytest.mark.parametrize('target', [
    'project/output_processed/integration', 'project/output_processed', 'project', 'ev', 'data_manager', 'lookups', 'scripts', '.',
    'experiments', 'experiments/ev_redistribution_c1', 'experiments/ev_redistribution_c1/outputs', 'experiments/ev_redistribution_c1/tests'])
def test_write_guard_rejects_repository_locations(target):
    with pytest.raises(PermissionError):
        w.check_output_root((REPO / target).resolve())


def test_write_guard_allows_only_shadow_and_archive():
    assert w.check_output_root(w.SHADOW_DIR) == w.SHADOW_DIR.resolve()
    assert w.check_output_root(w.ARCHIVE_DIR) == w.ARCHIVE_DIR.resolve()


def test_write_guard_rejects_relative_root():
    with pytest.raises(PermissionError):
        w.check_output_root(Path('experiments/ev_redistribution_c1/shadow_integration'))


@pytest.mark.parametrize('name', ['../ev_actuals_dno.csv', '../../../../project/output_processed/integration/ev_actuals_dno.csv',
                                  'sub/../../x.csv', str((REPO / 'project/output_processed/integration/ev_actuals_dno.csv').resolve())])
def test_write_guard_rejects_traversal_and_absolute_names(tmp_path, name):
    with pytest.raises(PermissionError):
        w.guarded_file(tmp_path, name)


def test_write_guard_rejects_symlinked_root(tmp_path):
    real = tmp_path / 'pretend_production'; real.mkdir()
    link = tmp_path / 'shadow_link'
    try:
        os.symlink(real, link, target_is_directory=True)
    except (OSError, NotImplementedError):
        pytest.skip('symlinks not permitted on this system')
    with pytest.raises(PermissionError):
        w.check_output_root(link)


def test_no_fallback_when_shadow_unavailable(tmp_path):
    blocker = tmp_path / 'not_a_dir'; blocker.write_text('x')
    before = digest(PROD / 'ev_actuals_dno.csv')
    with pytest.raises(OSError):
        w.write_csv(pd.DataFrame({'a': [1]}), blocker, 'ev_actuals_dno.csv')
    assert digest(PROD / 'ev_actuals_dno.csv') == before


# ---------------------------------------------------------------- input validation
def tiny():
    canon = pd.DataFrame({'LSOA21CD': ['E1', 'E2'], 'DNO': ['EPN', 'LPN'], 'LAD22CD': ['L1', 'L2'], 'LAD22NM': ['A', 'B'], 'LAEP': [None, 'X']}).set_index('LSOA21CD')
    rows = [dict(methodology_id=w.METHODOLOGY_ID, period=p, LSOA21CD=c, EV_Type=f, final_estimated_stock=5, post_redistribution=5, lpn_uplift=0, lpn_min_share=0.2)
            for p in w.EXPECTED_PERIODS for c in ('E1', 'E2') for f in w.FUELS]
    return pd.DataFrame(rows), canon


def test_tiny_valid_input_accepted():
    df, canon = tiny()
    assert len(w.validate_c1_lsoa(df, canon)) == 20


@pytest.mark.parametrize('mutate', [
    lambda d: d.drop(columns=['final_estimated_stock']),
    lambda d: d.assign(methodology_id='A_PRODUCTION_REFERENCE'),
    lambda d: d.assign(lpn_min_share=0.25),
    lambda d: d[d.period != '2026-03'],
    lambda d: pd.concat([d, d.iloc[:1]]),
    lambda d: d.assign(final_estimated_stock=-1, post_redistribution=-1),
    lambda d: d.assign(final_estimated_stock=5.5, post_redistribution=5.5),
    lambda d: d.assign(final_estimated_stock=6),
    lambda d: d[d.LSOA21CD != 'E2'],
    lambda d: d.assign(EV_Type='Total'),
    lambda d: d.assign(lpn_uplift=np.nan),
])
def test_malformed_input_rejected(mutate):
    df, canon = tiny()
    with pytest.raises(ValueError):
        w.validate_c1_lsoa(mutate(df.copy()).reset_index(drop=True), canon)


# ---------------------------------------------------------------- format proof
def test_writer_reproduces_production_files_byte_for_byte_from_method_a():
    canon = w.load_geography()
    a = pd.read_csv(w.PROD_METHOD_A_LSOA).rename(columns={'install_count': 'stock'})[['period', 'LSOA21CD', 'EV_Type', 'stock']]
    dno, la, laep = w.build_integration_frames(a, canon)
    assert w.to_csv_bytes(dno) == (PROD / 'ev_actuals_dno.csv').read_bytes()
    five = lambda f: f[f.period.isin(w.EXPECTED_PERIODS)].reset_index(drop=True)
    assert w.to_csv_bytes(five(la)) == (PROD / 'ev_actuals_la.csv').read_bytes()
    assert w.to_csv_bytes(five(laep)) == (PROD / 'ev_actuals_laep.csv').read_bytes()


# ---------------------------------------------------------------- output content
@pytest.mark.parametrize('name', ['ev_actuals_dno.csv', 'ev_actuals_la.csv', 'ev_actuals_laep.csv'])
def test_schema_matches_production(written, name):
    new, prod = written['files'][name], pd.read_csv(PROD / name)
    assert list(new.columns) == list(prod.columns)
    assert (new.dtypes.astype(str) == prod.dtypes.astype(str)).all()
    raw = (written['shadow'] / name).read_bytes()
    assert b'\r\n' in raw and not raw.startswith(b'\xef\xbb\xbf')


@pytest.mark.parametrize('name', ['ev_actuals_la.csv', 'ev_actuals_laep.csv'])
def test_observation_constants_match_production(written, name):
    new, prod = written['files'][name], pd.read_csv(PROD / name)
    for col in w.OBSERVATION_CONSTANTS:
        assert new[col].astype(str).unique().tolist() == prod[col].astype(str).unique().tolist(), col


def test_exactly_five_periods_and_no_method_a_history(written):
    for name, f in written['files'].items():
        assert sorted(f.period.unique()) == w.EXPECTED_PERIODS, name
        assert (f.period >= '2025-03').all(), name
        assert all(re.fullmatch(r'\d{4}-\d{2}', p) for p in f.period), name
    for name in ('ev_actuals_la.csv', 'ev_actuals_laep.csv'):
        f = written['files'][name]
        assert (f.period_end == f.period.map(w.period_end)).all()
        assert set(f.period_end) == {'2025-03-31', '2025-06-30', '2025-09-30', '2025-12-31', '2026-03-31'}


def test_source_has_11023_canonical_lsoas_each_period(written):
    c1 = written['c1']
    for per, g in c1.groupby('period'):
        assert g.LSOA21CD.nunique() == 11023
        assert g.drop_duplicates('LSOA21CD').DNO.value_counts().to_dict() == w.EXPECTED_DNO_LSOAS


def test_keys_unique_ids_not_null_counts_integer(written):
    keys = {'ev_actuals_dno.csv': ['period', 'DNO', 'EV_Type'], 'ev_actuals_la.csv': ['period', 'LAD22CD', 'EV_Type'], 'ev_actuals_laep.csv': ['period', 'LAEP', 'EV_Type']}
    rows = {'ev_actuals_dno.csv': 30, 'ev_actuals_la.csv': 1210, 'ev_actuals_laep.csv': 140}
    for name, k in keys.items():
        f = written['files'][name]
        assert len(f) == rows[name] and not f.duplicated(k).any() and not f[k].isna().any().any()
        assert set(f.EV_Type) == {'BEV', 'PHEV'}
        v = f['install_count' if 'install_count' in f else 'value']
        assert pd.api.types.is_integer_dtype(v) and (v >= 0).all()
    assert written['files']['ev_actuals_la.csv'].LAD22CD.nunique() == 121
    assert written['files']['ev_actuals_laep.csv'].LAEP.nunique() == 14


def test_every_fuel_reconciles_to_c1_lsoa_source(written):
    c1 = written['c1'].groupby(['period', 'EV_Type']).final_estimated_stock.sum()
    dno = written['files']['ev_actuals_dno.csv'].groupby(['period', 'EV_Type']).install_count.sum()
    la = written['files']['ev_actuals_la.csv'].groupby(['period', 'EV_Type']).value.sum()
    assert (dno == c1).all() and (la == c1).all()
    by_dno_src = written['c1'].groupby(['period', 'DNO', 'EV_Type']).final_estimated_stock.sum()
    assert (written['files']['ev_actuals_dno.csv'].set_index(['period', 'DNO', 'EV_Type']).install_count == by_dno_src).all()


def test_laep_equals_sum_of_mapped_las_and_partial_coverage(written):
    canon = written['canon']
    la = written['files']['ev_actuals_la.csv']
    lad_laep = canon.dropna(subset=['LAEP']).drop_duplicates('LAD22CD').set_index('LAD22CD').LAEP
    assert canon.dropna(subset=['LAEP']).groupby('LAD22CD').LAEP.nunique().max() == 1
    via = la[la.LAD22CD.isin(lad_laep.index)].assign(LAEP=lambda d: d.LAD22CD.map(lad_laep)).groupby(['period', 'LAEP', 'EV_Type']).value.sum()
    laep = written['files']['ev_actuals_laep.csv'].set_index(['period', 'LAEP', 'EV_Type']).value
    assert (via.reindex(laep.index) == laep).all()
    total_laep = laep.groupby(level='period').sum(); total_ukpn = la.groupby('period').value.sum()
    assert (total_laep < total_ukpn).all()          # LAEP coverage is partial by design


def test_bev_plus_phev_equals_total_everywhere(written):
    c1 = written['c1']
    total = c1.groupby(['period', 'DNO']).final_estimated_stock.sum()
    for name, key in (('ev_actuals_dno.csv', 'DNO'), ('ev_actuals_la.csv', 'LAD22CD'), ('ev_actuals_laep.csv', 'LAEP')):
        f = written['files'][name]; v = 'install_count' if 'install_count' in f else 'value'
        p = f.pivot_table(index=['period', key], columns='EV_Type', values=v, aggfunc='sum')
        assert (p.BEV + p.PHEV == f.groupby(['period', key])[v].sum()).all()
    dno = written['files']['ev_actuals_dno.csv'].groupby(['period', 'DNO']).install_count.sum()
    assert (dno == total).all()


def test_cross_dno_las_counted_once(written):
    canon = written['canon']
    cross = canon.groupby('LAD22CD').DNO.nunique(); cross = cross[cross > 1].index
    assert len(cross) == 11
    la = written['files']['ev_actuals_la.csv'].set_index(['period', 'LAD22CD', 'EV_Type']).value
    src = written['c1'][written['c1'].LAD22CD.isin(cross)].groupby(['period', 'LAD22CD', 'EV_Type']).final_estimated_stock.sum()
    assert (la.reindex(src.index) == src).all()


def test_manifest_provenance_and_hashes(written):
    m = written['manifest']
    assert m['methodology_id'] == 'C1_DYNAMIC_PRIVATE_EV' and m['lpn_min_share'] == 0.2 and m['periods'] == w.EXPECTED_PERIODS
    for rel, h in m['inputs_sha256'].items():
        assert digest(REPO / rel) == h
    for name, info in m['outputs'].items():
        assert digest(written['shadow'] / name) == info['sha256']
    saved = json.loads((written['shadow'] / 'manifest.json').read_text())
    assert saved == json.loads(json.dumps(m))
    assert 'run_utc' not in json.dumps(saved['outputs'])


def test_rerun_is_byte_identical_and_matches_committed_shadow(written, tmp_path):
    w.write_all(tmp_path / 's2', tmp_path / 'a2')
    for name in ('ev_actuals_dno.csv', 'ev_actuals_la.csv', 'ev_actuals_laep.csv', 'manifest.json', 'metadata_patch_proposal.json'):
        assert (tmp_path / 's2' / name).read_bytes() == (written['shadow'] / name).read_bytes(), name
        if (w.SHADOW_DIR / name).exists():
            assert (w.SHADOW_DIR / name).read_bytes() == (written['shadow'] / name).read_bytes(), f'shadow_integration/{name} is stale'
    for name in ('ev_actuals_dno_method_a_original.csv', 'method_a_dno_history_before_2025-03.csv', 'manifest.json'):
        assert (tmp_path / 'a2' / name).read_bytes() == (written['archive'] / name).read_bytes(), name


def test_method_a_archive_preserves_history(written):
    arch = written['archive']
    assert (arch / 'ev_actuals_dno_method_a_original.csv').read_bytes() == (PROD / 'ev_actuals_dno.csv').read_bytes()
    hist = pd.read_csv(arch / 'method_a_dno_history_before_2025-03.csv')
    prod = pd.read_csv(PROD / 'ev_actuals_dno.csv'); prod_hist = prod[prod.period < '2025-03'].reset_index(drop=True)
    assert (hist.methodology_id == 'A_PRODUCTION_LEGACY').all()
    assert hist[prod.columns].equals(prod_hist)
    m = written['archive_manifest']
    assert m['source_sha256'] == digest(PROD / 'ev_actuals_dno.csv') == m['copy_sha256']
    assert (m['source_rows'], m['history_before_c1_rows']) == (348, 318) and m['history_before_c1_periods'][:2] == ['2011-12', '2024-12']


def test_metadata_patch_is_proposal_only_and_consistent(written):
    live = json.loads((PROD / 'metadata.json').read_text())
    patch = json.loads((written['shadow'] / 'metadata_patch_proposal.json').read_text())['proposed_changes_for_integration_metadata_json']
    assert patch['latest_actual_period'] == max(written['files']['ev_actuals_dno.csv'].period) == live['latest_actual_period']
    assert digest(PROD / 'metadata.json') == written['before'][PROD / 'metadata.json']
