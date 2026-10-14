"""Unit tests for the C1 candidate using small synthetic fixtures (no production data)."""
import sys
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

EXP = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(EXP))
import c1_method as m  # noqa: E402
import run_c1  # noqa: E402


def quarter(rows):
    """rows: list of (code, total_bev, total_phev, private_bev, private_phev, company_bev, company_phev)."""
    rows = sorted(rows)
    a = lambda i: np.array([r[i] for r in rows], dtype=np.int64)
    return m.QuarterInput(codes=np.array([r[0] for r in rows]), total_bev=a(1), total_phev=a(2), private_bev=a(3),
                          private_phev=a(4), company_bev=a(5), company_phev=a(6))


def single(p, c):
    """One LSOA with BEV-only private p and company c, plus a recipient so a pool can be distributed."""
    return quarter([('E1', p + c, 0, p, 0, c, 0), ('E2', 10, 0, 10, 0, 0, 0)])


def pooled_total(q, r):
    return (q.total_bev - r['retained_bev']) + (q.total_phev - r['retained_phev'])


@pytest.mark.parametrize('p,c,expected_pool', [(60, 50, 10), (120, 50, 50), (180, 20, 20), (30, 40, 0)])
def test_private_protected_pooling_examples(p, c, expected_pool):
    q = single(p, c)
    r = m.run_quarter(q, 'C1')
    assert pooled_total(q, r)[0] == expected_pool
    assert r['retained_bev'][0] >= p  # private never pooled


def test_fleet_hotspot_retains_100():
    q = single(32, 90985)
    r = m.run_quarter(q, 'C1')
    assert r['retained_bev'][0] + r['retained_phev'][0] == 100
    assert r['retained_private_bev'][0] == 32 and r['retained_company_bev'][0] == 68


def test_zero_private_gives_zero_weight():
    q = quarter([('E1', 50, 0, 0, 0, 50, 0), ('E2', 20, 0, 20, 0, 0, 0), ('E3', 300, 0, 0, 0, 300, 0)])
    r = m.run_quarter(q, 'C1')
    assert r['weight'][0] == 0 and r['received_bev'][0] == 0
    assert r['weight'][1] == 1.0


def test_fuel_specific_retention_and_pooling():
    # P = 30 BEV + 10 PHEV; C = 60 BEV + 20 PHEV; allowance 60 split 45/15 by non-private fuel mix
    q = quarter([('E1', 90, 30, 30, 10, 60, 20), ('E2', 10, 10, 10, 10, 0, 0)])
    r = m.run_quarter(q, 'C1')
    assert (r['retained_bev'][0], r['retained_phev'][0]) == (75, 25)
    assert (q.total_bev - r['retained_bev'])[0] == 15 and (q.total_phev - r['retained_phev'])[0] == 5
    assert r['post_bev'].sum() == q.total_bev.sum() and r['post_phev'].sum() == q.total_phev.sum()


def test_split_by_fuel_tie_goes_to_bev():
    kb, kp = m.split_by_fuel(np.array([1]), np.array([1]), np.array([1]))
    assert (kb[0], kp[0]) == (1, 0)


def test_largest_remainder_tie_breaks_by_ascending_code():
    out = m.largest_remainder(np.array([0.5, 0.5, 0.5]), 2, np.array(['E3', 'E1', 'E2']))
    assert out.tolist() == [0, 1, 1]
    assert m.largest_remainder(np.array([1.6, 0.4]), 2, np.array(['A', 'B'])).tolist() == [2, 0]


def test_unclassified_is_never_treated_as_private():
    # total 150, private 40, company suppressed (0) -> unclassified 110; allowance 60 retained, 50 pooled
    q = quarter([('E1', 150, 0, 40, 0, 0, 0), ('E2', 10, 0, 10, 0, 0, 0)])
    r = m.run_quarter(q, 'C1')
    assert r['unclassified_bev'][0] == 110
    assert r['retained_bev'][0] == 100 and r['retained_private_bev'][0] == 40
    assert r['retained_company_bev'][0] == 0 and r['retained_unclassified_bev'][0] == 60
    assert pooled_total(q, r)[0] == 50


def test_unclassified_shares_allowance_with_company_proportionally():
    # private 20, company 60, unclassified 40 -> allowance 80 of 100 non-private retained; pool 20
    q = quarter([('E1', 120, 0, 20, 0, 60, 0), ('E2', 10, 0, 10, 0, 0, 0)])
    r = m.run_quarter(q, 'C1')
    assert pooled_total(q, r)[0] == 20
    assert r['retained_company_bev'][0] + r['retained_unclassified_bev'][0] == 80
    assert r['retained_company_bev'][0] == 48 and r['retained_unclassified_bev'][0] == 32


def test_reported_components_are_capped_at_total():
    pb, pp, cb, cp, ub, up = m.classify_keepers(np.array([10]), np.array([0]), np.array([15]), np.array([0]), np.array([5]), np.array([0]))
    assert (pb[0], cb[0], ub[0]) == (10, 0, 0)


def test_miscellaneous_keeps_100_and_never_receives():
    q = quarter([(m.MISC_CODE, 500, 0, 300, 0, 200, 0), ('E1', 40, 0, 40, 0, 0, 0), ('E2', 20, 0, 20, 0, 0, 0)])
    r = m.run_quarter(q, 'C1')
    misc = q.codes == m.MISC_CODE
    assert r['retained_bev'][misc][0] == 100            # production treatment, not private protection
    assert r['received_bev'][misc][0] == 0 and r['weight'][misc][0] == 0
    assert r['post_bev'][~misc].sum() + r['retained_bev'][misc][0] == q.total_bev.sum()


def test_recipients_include_lsoas_over_100():
    q = quarter([('E1', 150, 0, 150, 0, 0, 0), ('E2', 300, 0, 0, 0, 300, 0)])
    r = m.run_quarter(q, 'C1')
    assert r['received_bev'][0] == 200                   # E2 pools 200 company vehicles, E1 is the only private-weighted recipient


def test_national_conservation_random_fixture():
    rng = np.random.default_rng(7)
    rows = []
    for i in range(300):
        tb, tp = int(rng.integers(0, 400)), int(rng.integers(0, 200))
        pb, pp = int(rng.integers(0, tb + 1)), int(rng.integers(0, tp + 1))
        cb, cp = int(rng.integers(0, tb - pb + 1)), int(rng.integers(0, tp - pp + 1))
        rows.append((f'E{i:05d}', tb, tp, pb, pp, cb, cp))
    rows.append((m.MISC_CODE, 5000, 3000, 2000, 1000, 2000, 1500))
    q = quarter([r for r in rows if r[1] + r[2] > 0])
    for method in ('A', 'C1'):
        r = m.run_quarter(q, method)
        assert r['post_bev'].sum() == q.total_bev.sum() and r['post_phev'].sum() == q.total_phev.sum()
        assert r['received_bev'].sum() == r['pool_bev'] and r['received_phev'].sum() == r['pool_phev']
        assert (r['post_bev'] >= 0).all() and (r['post_phev'] >= 0).all()
        comp = r['retained_private_bev'] + r['retained_company_bev'] + r['retained_unclassified_bev']
        assert (comp == r['retained_bev']).all()
        if method == 'C1':
            geo = q.codes != m.MISC_CODE
            assert (r['post_bev'][geo] >= r['private_bev'][geo]).all() and (r['post_phev'][geo] >= r['private_phev'][geo]).all()


def test_method_a_reference_threshold():
    q = quarter([('E1', 150, 50, 150, 50, 0, 0), ('E2', 50, 0, 50, 0, 0, 0), ('E3', 100, 0, 0, 0, 100, 0)])
    r = m.run_quarter(q, 'A')
    assert r['retained_bev'][0] + r['retained_phev'][0] == 100   # A ignores keeper type
    assert r['received_bev'][2] == 0                               # exactly 100 is not a recipient
    assert r['received_bev'][1] == 75 and r['received_phev'][1] == 25


def test_lpn_floor_configuration():
    assert m.C1_LPN_MIN_SHARE == 0.20 and m.LPN_FLOOR['C1'] == 0.20
    assert m.LPN_FLOOR['A'] == 0.25                                    # Method A reference keeps production's 25%


def test_lpn_uplift_arithmetic_production_25pct_reference():
    bev = np.array([600, 150, 100]); phev = np.array([100, 50, 0])     # UKPN 1000, LPN (idx 1) 200
    add_b, add_p, x = m.lpn_uplift(bev, phev, np.array([False, True, False]), np.array(['E1', 'E2', 'E3']), 0.25)
    assert x == 67                                                     # round((250-200)/0.75)=round(66.67)
    assert add_b.sum() + add_p.sum() == 67 and add_b[0] == 0 and add_b[2] == 0
    assert add_b[1] == int(67 * 150 / 200)                             # truncation for BEV share


@pytest.mark.parametrize('lpn,expected', [(140, 75), (200, 0), (210, 0), (400, 0)])
def test_c1_20pct_floor(lpn, expected):
    # UKPN post = 1000; uplift = max(0, round((0.20*1000 - LPN)/0.80)); never reduces LPN
    bev = np.array([1000 - lpn, lpn]); phev = np.array([0, 0])
    add_b, add_p, x = m.lpn_uplift(bev, phev, np.array([False, True]), np.array(['E1', 'E2']), m.LPN_FLOOR['C1'])
    assert x == expected and add_b.sum() + add_p.sum() == expected and add_b[0] == 0
    if expected:
        assert (lpn + x) / (1000 + x) == pytest.approx(0.20)


def test_validate_lookup_rejects_duplicates_and_missing():
    ok = pd.DataFrame({'LSOA21CD': ['E1', 'E2'], 'DNO': ['EPN', 'LPN']})
    run_c1.validate_lsoa_lookup(ok)
    with pytest.raises(ValueError):
        run_c1.validate_lsoa_lookup(pd.DataFrame({'LSOA21CD': ['E1', 'E1'], 'DNO': ['EPN', 'LPN']}))
    with pytest.raises(ValueError):
        run_c1.validate_lsoa_lookup(pd.DataFrame({'LSOA21CD': ['E1', None], 'DNO': ['EPN', 'LPN']}))
    with pytest.raises(ValueError):
        run_c1.validate_lsoa_lookup(pd.DataFrame({'LSOA21CD': ['E1', 'E2'], 'DNO': ['EPN', None]}))


def test_zero_stock_canonical_lsoas_are_kept_as_zero_rows():
    canon = pd.DataFrame({'LSOA21CD': ['E1', 'E9'], 'DNO': ['EPN', 'LPN'], 'LAD22CD': ['L1', 'L2'], 'LAD22NM': ['A', 'B'], 'LAEP': [None, 'X'], 'geography_flag': ['x', 'x']})
    q = quarter([('E1', 50, 10, 50, 10, 0, 0)])
    r = m.run_quarter(q, 'C1')
    df = pd.DataFrame({'LSOA21CD': q.codes, 'raw_bev': q.total_bev, 'raw_phev': q.total_phev,
                       **{k: v for k, v in r.items() if isinstance(v, np.ndarray) and k != 'weight'}, 'recipient_weight': r['weight']})
    uk = df.merge(canon[['LSOA21CD', 'DNO']], on='LSOA21CD')
    uk['uplift_bev'] = 0; uk['uplift_phev'] = 0
    out = run_c1.canonical_long(uk, canon, '2026-03', m.METHODOLOGY_ID, {})
    assert len(out) == 4 and set(out.LSOA21CD) == {'E1', 'E9'}
    assert (out[out.LSOA21CD == 'E9'].final_estimated_stock == 0).all()
    assert (out.methodology_id == 'C1_DYNAMIC_PRIVATE_EV').all()


@pytest.mark.parametrize('name', ['../x.csv', '../../../dashboard.html', '../../project/output_processed/ev_quarterly_lsoa.csv', '/tmp/x.csv'])
def test_write_guard_refuses_paths_outside_outputs(name):
    with pytest.raises(PermissionError):
        run_c1.guarded_path(name)


def test_write_guard_allows_outputs_dir():
    assert run_c1.OUTPUT_DIR.resolve() in run_c1.guarded_path('ok.csv').parents
