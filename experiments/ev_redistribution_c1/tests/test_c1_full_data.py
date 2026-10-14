"""Full-data integration tests for the C1 candidate. Reads production inputs read-only; writes nothing."""
import hashlib
import sys
from pathlib import Path

import pandas as pd
import pytest

EXP = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(EXP))
import c1_method as m  # noqa: E402
import run_c1  # noqa: E402

PROTECTED = [run_c1.REPO / p for p in (
    'dashboard.html', 'scripts/05_ev_quarterly_processing.py', 'project/output_processed/ev_quarterly_lsoa.csv',
    'project/output_processed/ev_quarterly_dno.csv', 'project/output_processed/integration/ev_actuals_dno.csv',
    'project/output_processed/integration/ev_actuals_la.csv', 'project/output_processed/integration/ev_actuals_laep.csv',
    'project/output_processed/integration/monthly_forecast_dno.csv', 'ev/df_VEH0135.csv', 'lookups/LSOA to DNO.csv',
    'project/output_processed/integration/geography_lookup.csv')]

pytestmark = pytest.mark.skipif(not all(p.exists() for p in run_c1.INPUTS.values()), reason='production inputs not available')


def digest(p):
    return hashlib.sha256(p.read_bytes()).hexdigest()


@pytest.fixture(scope='module')
def results():
    before = {p: digest(p) for p in PROTECTED}
    canon, flags, out, national, prov = run_c1.run_all()
    agg = run_c1.aggregate(out)
    after = {p: digest(p) for p in PROTECTED}
    return dict(canon=canon, flags=flags, out=out, national=national, agg=agg, before=before, after=after)


def test_production_files_untouched(results):
    assert results['before'] == results['after']


def test_method_a_reference_reproduces_production_every_lsoa(results):
    prod = run_c1.production_reproduction(results['out'], results['national'])
    assert (prod.mismatches == 0).all(), prod.to_string()
    r = prod.set_index('period').loc['2026-03']
    assert (r.ukpn_post, r.lpn_post, r.uplift, r.final) == (882247, 200524, 26717, 908964)
    assert (r.bev, r.phev, r.EPN, r.LPN, r.SPN) == (598878, 310086, 419870, 227241, 261853)


@pytest.mark.parametrize('period,final,post,uplift', [('2025-03', 719684, 719684, 0), ('2026-03', 896098, 896098, 0)])
def test_c1_20pct_floor_controls(results, period, final, post, uplift):
    u = results['out'][('C1', period)]
    assert (int(u.final_estimated_stock.sum()), int(u.post_redistribution.sum()), int(u.lpn_uplift.sum())) == (final, post, uplift)


def test_c1_mar26_lpn_post_control(results):
    u = results['out'][('C1', '2026-03')]
    assert int(u[u.DNO == 'LPN'].post_redistribution.sum()) == 191386


def test_c1_20pct_floor_inactive_every_quarter(results):
    for per in run_c1.QUARTERS.values():
        _, nat = results['national'][('C1', per)]
        assert nat['lpn_floor'] == 0.20
        assert nat['lpn_post'] / nat['ukpn_post'] > 0.20 and nat['uplift'] == 0


def test_c1_laep_controls(results):
    a = results['agg']
    t = a[(a.methodology == 'C1') & (a.period == '2026-03') & (a.level == 'LAEP') & (a.EV_Type == 'Total')].set_index('geography')
    assert (t.loc['Peterborough LAEP'].final_estimated_stock, t.loc['Norwich LAEP'].final_estimated_stock) == (7936, 2889)
    assert (t.final_estimated_stock == t.post_redistribution).all()          # no uplift anywhere at 20%


def test_accounting_identities_all_pass(results):
    c = run_c1.accounting_checks(results['out'], results['national'])
    assert c.passed.all(), c[~c.passed].to_string()


def test_geography_checks_all_pass(results):
    c = run_c1.geography_checks(results['out'], results['canon'], results['flags'])
    assert c.passed.all(), c[~c.passed].to_string()


def test_aggregation_checks_all_pass(results):
    c = run_c1.aggregation_checks(results['agg'], results['canon'])
    assert c.passed.all(), c[~c.passed].to_string()


def test_no_private_vehicle_pooled_in_c1(results):
    for per in run_c1.QUARTERS.values():
        qi, nat = results['national'][('C1', per)]
        df = nat['national']
        geo = df.LSOA21CD != m.MISC_CODE
        assert ((df.private_bev - df.retained_private_bev)[geo] == 0).all()
        assert ((df.private_phev - df.retained_private_phev)[geo] == 0).all()


def test_candidate_output_is_labelled_and_not_monotonic_forced(results):
    out = results['out']
    for per in run_c1.QUARTERS.values():
        assert (out[('C1', per)].methodology_id == m.METHODOLOGY_ID).all()
    vol = run_c1.volatility(out)
    assert vol[(vol.methodology == 'C1') & (vol.subtype == 'Total')].n_negative.sum() > 0   # declines are reported, not clipped
