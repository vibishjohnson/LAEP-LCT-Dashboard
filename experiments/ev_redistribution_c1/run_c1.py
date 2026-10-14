"""Run the Method C1 candidate for all five quarters, QA it, and write outputs to ./outputs only.

Usage (from the repository root):
    python experiments/ev_redistribution_c1/run_c1.py

Reads production inputs read-only. Never writes outside experiments/ev_redistribution_c1/outputs/.
"""
import datetime as dt
import hashlib
import json
import subprocess
import sys
from pathlib import Path

import numpy as np
import pandas as pd

EXP_DIR = Path(__file__).resolve().parent
REPO = EXP_DIR.parents[1]
OUTPUT_DIR = EXP_DIR / 'outputs'
sys.path.insert(0, str(EXP_DIR))
import c1_method as m  # noqa: E402

QUARTERS = {'2025 Q1': '2025-03', '2025 Q2': '2025-06', '2025 Q3': '2025-09', '2025 Q4': '2025-12', '2026 Q1': '2026-03'}
FUEL_MAP = {'Battery electric': 'BEV', 'Plug-in hybrid electric (petrol)': 'PHEV', 'Plug-in hybrid electric (diesel)': 'PHEV'}
INPUTS = {
    'veh0135': REPO / 'ev' / 'df_VEH0135.csv',
    'lsoa_dno': REPO / 'lookups' / 'LSOA to DNO.csv',
    'geography': REPO / 'project' / 'output_processed' / 'integration' / 'geography_lookup.csv',
    'la_laep': REPO / 'lookups' / 'LA-LAEP lookup.csv',
    'production_lsoa': REPO / 'project' / 'output_processed' / 'ev_quarterly_lsoa.csv',
}
EXPECTED_DNO_LSOAS = {'EPN': 4993, 'LPN': 2931, 'SPN': 3099}


# ---------------------------------------------------------------- write guard
def guarded_path(name):
    """Resolve an output file name and refuse anything outside OUTPUT_DIR."""
    p = (OUTPUT_DIR / name).resolve()
    if OUTPUT_DIR.resolve() not in p.parents:
        raise PermissionError(f'Refusing to write outside candidate output directory: {p}')
    return p


def write_csv(df, name):
    p = guarded_path(name)
    p.parent.mkdir(parents=True, exist_ok=True)
    df.to_csv(p, index=False)
    return p


def write_text(text, name):
    p = guarded_path(name)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(text, encoding='utf-8')
    return p


# ---------------------------------------------------------------- inputs
def sha256(path):
    h = hashlib.sha256()
    with open(path, 'rb') as f:
        for chunk in iter(lambda: f.read(1 << 20), b''):
            h.update(chunk)
    return h.hexdigest()


def clean_numeric(s):
    s = s.astype(str).str.replace(',', '', regex=False).str.strip().replace({'[x]': np.nan, '[c]': np.nan})
    return pd.to_numeric(s, errors='coerce').fillna(0).astype(np.int64)


def validate_lsoa_lookup(df, key='LSOA21CD', value='DNO'):
    """Fail on duplicate or missing keys/values in an LSOA lookup."""
    if df[key].isna().any():
        raise ValueError('lookup has missing LSOA codes')
    dup = df[key].duplicated()
    if dup.any():
        raise ValueError(f'lookup has duplicate LSOA codes: {df.loc[dup, key].head().tolist()}')
    if df[value].isna().any():
        raise ValueError(f'lookup has missing {value} values')
    return df


def load_geography():
    raw = pd.read_csv(INPUTS['lsoa_dno'], encoding='utf-8-sig')
    canon = raw.dropna(subset=['LSOA21CD'])[['LSOA21CD', 'Majority Licence area', 'LAD22CD', 'LAD22NM']].rename(columns={'Majority Licence area': 'DNO'})
    canon = canon[canon.DNO.isin(['EPN', 'LPN', 'SPN'])].copy()
    validate_lsoa_lookup(canon)
    geo = pd.read_csv(INPUTS['geography'])
    flags = geo.set_index('LSOA21CD')
    canon = canon.merge(geo[['LSOA21CD', 'LAEP', 'geography_flag']], on='LSOA21CD', how='left')
    return canon.sort_values('LSOA21CD').reset_index(drop=True), flags


def load_veh0135():
    cols = ['LSOA21CD', 'Fuel', 'Keepership'] + list(QUARTERS)
    d = pd.read_csv(INPUTS['veh0135'], low_memory=False, usecols=cols)
    d['EV_Type'] = d.Fuel.map(FUEL_MAP)
    d = d[d.EV_Type.notna() & d.Keepership.isin(['Total', 'Private', 'Company'])].copy()
    d['LSOA21CD'] = d.LSOA21CD.astype(str).str.strip()
    for q in QUARTERS:
        d[q] = clean_numeric(d[q])
    return d


def quarter_input(d, q):
    def pivot(keeper):
        x = d[(d.Keepership == keeper) & (d[q] > 0)].pivot_table(index='LSOA21CD', columns='EV_Type', values=q, aggfunc='sum', fill_value=0)
        for f in ('BEV', 'PHEV'):
            if f not in x:
                x[f] = 0
        return x
    tot = pivot('Total')
    tot = tot[(tot.BEV + tot.PHEV) > 0].sort_index()
    pri = pivot('Private').reindex(tot.index).fillna(0)
    com = pivot('Company').reindex(tot.index).fillna(0)
    i64 = lambda s: s.values.astype(np.int64)
    return m.QuarterInput(codes=tot.index.values.astype(str), total_bev=i64(tot.BEV), total_phev=i64(tot.PHEV),
                          private_bev=i64(pri.BEV), private_phev=i64(pri.PHEV), company_bev=i64(com.BEV), company_phev=i64(com.PHEV))


# ---------------------------------------------------------------- pipeline
def run_method(qi, canon, method):
    r = m.run_quarter(qi, method)
    df = pd.DataFrame({'LSOA21CD': qi.codes, 'raw_bev': qi.total_bev, 'raw_phev': qi.total_phev,
                       **{k: v for k, v in r.items() if isinstance(v, np.ndarray) and k != 'weight'}, 'recipient_weight': r['weight']})
    ukpn = df.merge(canon[['LSOA21CD', 'DNO']], on='LSOA21CD', how='inner')
    is_lpn = (ukpn.DNO == 'LPN').values
    add_b, add_p, uplift = m.lpn_uplift(ukpn.post_bev.values, ukpn.post_phev.values, is_lpn, ukpn.LSOA21CD.values, m.LPN_FLOOR[method])
    ukpn['uplift_bev'], ukpn['uplift_phev'] = add_b, add_p
    post_ukpn = int((ukpn.post_bev + ukpn.post_phev).sum())
    post_lpn = int((ukpn.post_bev + ukpn.post_phev)[is_lpn].sum())
    nat = dict(pool_bev=r['pool_bev'], pool_phev=r['pool_phev'], uplift=uplift, national=df, lpn_floor=m.LPN_FLOOR[method],
               ukpn_post=post_ukpn, lpn_post=post_lpn)
    return ukpn, nat


def canonical_long(ukpn, canon, period, method_id, provenance):
    """Expand to all canonical UKPN LSOAs (zero-stock LSOAs kept as explicit zeros), one row per fuel."""
    full = canon.merge(ukpn.drop(columns=['DNO']), on='LSOA21CD', how='left')
    num = [c for c in ukpn.columns if c not in ('LSOA21CD', 'DNO')]
    full[num] = full[num].fillna(0)
    rows = []
    for fuel, sfx in (('BEV', 'bev'), ('PHEV', 'phev')):
        x = pd.DataFrame({
            'methodology_id': method_id, 'period': period, 'LSOA21CD': full.LSOA21CD, 'DNO': full.DNO, 'LAD22CD': full.LAD22CD,
            'LAD22NM': full.LAD22NM, 'LAEP': full.LAEP, 'EV_Type': fuel,
            'raw_total': full[f'raw_{sfx}'], 'raw_private': full[f'private_{sfx}'], 'raw_company': full[f'company_{sfx}'], 'raw_unclassified': full[f'unclassified_{sfx}'],
            'retained_private': full[f'retained_private_{sfx}'], 'retained_company_diag': full[f'retained_company_{sfx}'],
            'retained_unclassified_diag': full[f'retained_unclassified_{sfx}'], 'retained_total': full[f'retained_{sfx}'],
            'pooled_total': full[f'raw_{sfx}'] - full[f'retained_{sfx}'], 'redistribution_received': full[f'received_{sfx}'],
            'post_redistribution': full[f'post_{sfx}'], 'lpn_uplift': full[f'uplift_{sfx}'],
            'final_estimated_stock': full[f'post_{sfx}'] + full[f'uplift_{sfx}'], 'recipient_weight': full.recipient_weight})
        rows.append(x)
    out = pd.concat(rows, ignore_index=True)
    int_cols = [c for c in out.columns if c.startswith(('raw_', 'retained_', 'pooled_', 'redistribution_', 'post_', 'lpn_', 'final_'))]
    out[int_cols] = out[int_cols].astype(np.int64)
    out['estimate_type'] = 'MODELLED_NOT_OBSERVED_LOCATION'          # see README.md / run_metadata.json
    out['unclassified_treatment'] = 'NONPRIVATE_JOINT_BLOCK'          # company + unclassified share the allowance; never private
    for k, v in provenance.items():
        out[k] = v
    return out


def run_all():
    canon, flags = load_geography()
    d = load_veh0135()
    prov = {'source_veh0135_sha256': sha256(INPUTS['veh0135'])[:16], 'source_lsoa_dno_sha256': sha256(INPUTS['lsoa_dno'])[:16],
            'code_git_head': git_head(), 'run_utc': dt.datetime.now(dt.timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ')}
    out, national = {}, {}
    for q, per in QUARTERS.items():
        qi = quarter_input(d, q)
        for method, mid in (('C1', m.METHODOLOGY_ID), ('A', m.CONTROL_METHODOLOGY_ID)):
            ukpn, nat = run_method(qi, canon, method)
            out[(method, per)] = canonical_long(ukpn, canon, per, mid, {**prov, 'lpn_min_share': m.LPN_FLOOR[method]})
            national[(method, per)] = (qi, nat)
    return canon, flags, out, national, prov


def git_head():
    try:
        return subprocess.run(['git', 'rev-parse', '--short', 'HEAD'], cwd=REPO, capture_output=True, text=True).stdout.strip()
    except OSError:
        return 'unknown'


# ---------------------------------------------------------------- QA
def accounting_checks(out, national):
    checks = []
    for (method, per), (qi, nat) in national.items():
        df = nat['national']
        misc = df.LSOA21CD == m.MISC_CODE
        tb, tp = int(qi.total_bev.sum()), int(qi.total_phev.sum())
        c = {
            'raw = retained + pooled (per LSOA, per fuel)': bool(((df.retained_bev + (df.raw_bev - df.retained_bev)) == df.raw_bev).all() and (df.retained_bev <= df.raw_bev).all() and (df.retained_phev <= df.raw_phev).all()),
            'national BEV conserved (post-redistribution)': int(df.post_bev.sum()) == tb,
            'national PHEV conserved (post-redistribution)': int(df.post_phev.sum()) == tp,
            'received = distributable pool (BEV, PHEV)': int(df.received_bev.sum()) == nat['pool_bev'] and int(df.received_phev.sum()) == nat['pool_phev'],
            'Miscellaneous receives nothing': int(df[misc].received_bev.sum() + df[misc].received_phev.sum()) == 0,
            'Miscellaneous retained + pooled = Miscellaneous raw': int((df[misc].retained_bev + df[misc].retained_phev).sum()) + int(((df[misc].raw_bev - df[misc].retained_bev) + (df[misc].raw_phev - df[misc].retained_phev)).sum()) == int((df[misc].raw_bev + df[misc].raw_phev).sum()),
            'distributed geographic stock + Miscellaneous retained = national raw': int(df[~misc].post_bev.sum() + df[~misc].post_phev.sum()) + int(df[misc].retained_bev.sum() + df[misc].retained_phev.sum()) == tb + tp,
            'keeper components sum to raw (private+company+unclassified)': bool(((df.private_bev + df.company_bev + df.unclassified_bev) == df.raw_bev).all() and ((df.private_phev + df.company_phev + df.unclassified_phev) == df.raw_phev).all()),
            'retained diagnostics sum to retained': bool(((df.retained_private_bev + df.retained_company_bev + df.retained_unclassified_bev) == df.retained_bev).all() and ((df.retained_private_phev + df.retained_company_phev + df.retained_unclassified_phev) == df.retained_phev).all()),
            'diagnostic components within bounds': bool((df.retained_company_bev <= df.company_bev).all() and (df.retained_unclassified_bev <= df.unclassified_bev).all() and (df.retained_company_phev <= df.company_phev).all() and (df.retained_unclassified_phev <= df.unclassified_phev).all() and (df.retained_unclassified_bev >= 0).all() and (df.retained_unclassified_phev >= 0).all()),
            'no negative values (national)': bool((df.select_dtypes('number') >= 0).all().all()),
        }
        if method == 'C1':
            geo = ~misc
            c['private BEV/PHEV retained in source LSOA (C1)'] = bool((df[geo].retained_bev >= df[geo].private_bev).all() and (df[geo].retained_phev >= df[geo].private_phev).all())
        u = out[(method, per)]
        c['final = post + uplift (every row)'] = bool((u.final_estimated_stock == u.post_redistribution + u.lpn_uplift).all())
        c['uplift only in LPN'] = int(u[u.DNO != 'LPN'].lpn_uplift.sum()) == 0
        c['total uplift = formula uplift'] = int(u.lpn_uplift.sum()) == nat['uplift']
        f = nat['lpn_floor']
        expected_uplift = max(0, int(round((f * nat['ukpn_post'] - nat['lpn_post']) / (1 - f))))
        c[f'uplift = max(0, round(({f:.2f}*UKPN_post - LPN_post)/{1 - f:.2f})) recomputed independently'] = nat['uplift'] == expected_uplift
        lpn_final = int(u[u.DNO == 'LPN'].final_estimated_stock.sum())
        c['LPN never reduced (final LPN >= post LPN)'] = lpn_final >= nat['lpn_post']
        c[f'LPN final share >= {f:.0%} floor (within 1 vehicle) or unchanged'] = (lpn_final + 1) / int(u.final_estimated_stock.sum()) >= f or nat['uplift'] == 0
        c['final counts non-negative integers'] = bool((u.final_estimated_stock >= 0).all() and u.final_estimated_stock.dtype.kind == 'i')
        dno = u.groupby('DNO').final_estimated_stock.sum()
        c['EPN+LPN+SPN = UKPN'] = int(dno.sum()) == int(u.final_estimated_stock.sum())
        c['UKPN final = UKPN post + uplift'] = int(u.final_estimated_stock.sum()) == int(u.post_redistribution.sum()) + nat['uplift']
        for name, ok in c.items():
            checks.append(dict(method=method, period=per, check=name, passed=bool(ok)))
    return pd.DataFrame(checks)


def geography_checks(out, canon, flags):
    checks = []
    ht_only = set(flags.index[flags.geography_flag == 'HT_ONLY_BOUNDARY'])
    counts = canon.DNO.value_counts().to_dict()
    checks.append(('canonical LSOA count = 11,023', len(canon) == 11023, len(canon)))
    checks.append(('DNO LSOA counts EPN 4,993 / LPN 2,931 / SPN 3,099', counts == EXPECTED_DNO_LSOAS, counts))
    checks.append(('canonical LAEP/flag join complete', bool(canon.geography_flag.notna().all()), int(canon.geography_flag.isna().sum())))
    checks.append(('no HT_ONLY_BOUNDARY LSOAs in canonical set', not canon.LSOA21CD.isin(ht_only).any(), int(canon.LSOA21CD.isin(ht_only).sum())))
    checks.append(('each LSOA maps to one LAD', canon.groupby('LSOA21CD').LAD22CD.nunique().max() == 1, ''))
    for (method, per), u in out.items():
        key_dup = int(u.duplicated(['period', 'LSOA21CD', 'EV_Type']).sum())
        checks.append((f'{method} {per}: every canonical LSOA present for BEV and PHEV', len(u) == 2 * len(canon) and set(u.LSOA21CD) == set(canon.LSOA21CD), len(u)))
        checks.append((f'{method} {per}: no duplicate LSOA/quarter/fuel keys', key_dup == 0, key_dup))
        checks.append((f'{method} {per}: no non-canonical LSOAs', set(u.LSOA21CD) <= set(canon.LSOA21CD), ''))
    return pd.DataFrame(checks, columns=['check', 'passed', 'detail'])


def aggregate(out):
    rows = []
    for (method, per), u in out.items():
        t = u.copy()
        tot = t.groupby(['LSOA21CD', 'DNO', 'LAD22CD', 'LAD22NM', 'LAEP'], dropna=False)[['raw_total', 'post_redistribution', 'lpn_uplift', 'final_estimated_stock']].sum().reset_index()
        tot['EV_Type'] = 'Total'
        both = pd.concat([t, tot], ignore_index=True)
        for level, keys in (('UKPN', []), ('DNO', ['DNO']), ('LA', ['LAD22CD', 'LAD22NM']), ('LAEP', ['LAEP'])):
            src = both if level != 'LAEP' else both[both.LAEP.notna()]
            g = src.groupby(keys + ['EV_Type'])[['raw_total', 'post_redistribution', 'lpn_uplift', 'final_estimated_stock']].sum().reset_index()
            if level == 'UKPN':
                g.insert(0, 'geography', 'UKPN')
            elif level == 'LA':
                g = g.rename(columns={'LAD22CD': 'geography', 'LAD22NM': 'geography_name'})
            else:
                g = g.rename(columns={keys[0]: 'geography'})
            g['level'], g['methodology'], g['period'] = level, method, per
            rows.append(g)
    return pd.concat(rows, ignore_index=True)


def aggregation_checks(agg, canon):
    checks = []
    for (method, per), g in agg.groupby(['methodology', 'period']):
        uk = g[g.level == 'UKPN'].set_index('EV_Type').final_estimated_stock
        for lvl in ('DNO', 'LA'):
            s = g[g.level == lvl].groupby('EV_Type').final_estimated_stock.sum()
            checks.append((f'{method} {per}: {lvl} totals sum to UKPN (BEV, PHEV, Total)', bool((s.reindex(uk.index) == uk).all()), ''))
        checks.append((f'{method} {per}: 121 LAs', g[(g.level == 'LA') & (g.EV_Type == 'Total')].geography.nunique() == 121, g[(g.level == 'LA')].geography.nunique()))
        checks.append((f'{method} {per}: 14 LAEPs', g[(g.level == 'LAEP') & (g.EV_Type == 'Total')].geography.nunique() == 14, ''))
        for lvl in ('UKPN', 'DNO', 'LA', 'LAEP'):
            p = g[g.level == lvl].pivot_table(index='geography', columns='EV_Type', values='final_estimated_stock', aggfunc='sum')
            checks.append((f'{method} {per}: {lvl} BEV + PHEV = Total', bool((p.BEV + p.PHEV == p.Total).all()), ''))
        la = g[(g.level == 'LA') & (g.EV_Type == 'Total')].set_index('geography').final_estimated_stock
        la_laep = canon.dropna(subset=['LAEP']).drop_duplicates('LAD22CD').set_index('LAD22CD').LAEP
        via_la = la.reindex(la_laep.index).groupby(la_laep).sum()
        laep = g[(g.level == 'LAEP') & (g.EV_Type == 'Total')].set_index('geography').final_estimated_stock
        checks.append((f'{method} {per}: each LAEP = sum of its mapped LAs', bool((via_la.reindex(laep.index) == laep).all()), ''))
    lad_laep = canon.dropna(subset=['LAEP']).groupby('LAD22CD').LAEP.nunique()
    checks.append(('no LA mapped to more than one LAEP', int((lad_laep > 1).sum()) == 0, int((lad_laep > 1).sum())))
    return pd.DataFrame(checks, columns=['check', 'passed', 'detail'])


def production_reproduction(out, national):
    """Method A reference vs the existing production LSOA file (read-only)."""
    prod = pd.read_csv(INPUTS['production_lsoa'])
    rows = []
    for per in QUARTERS.values():
        a = out[('A', per)]
        mine = a[a.final_estimated_stock > 0].set_index(['LSOA21CD', 'EV_Type']).final_estimated_stock
        p = prod[prod.period == per].groupby(['LSOA21CD', 'EV_Type']).install_count.sum()
        p = p[p > 0]
        j = pd.concat([mine.rename('candidate'), p.rename('production')], axis=1).fillna(-1)
        lpn = a[a.DNO == 'LPN']
        rows.append(dict(period=per, lsoa_fuel_keys=len(j), mismatches=int((j['candidate'] != j['production']).sum()),
                         ukpn_post=int(a.post_redistribution.sum()), lpn_post=int(lpn.post_redistribution.sum()),
                         uplift=int(a.lpn_uplift.sum()), final=int(a.final_estimated_stock.sum()),
                         bev=int(a[a.EV_Type == 'BEV'].final_estimated_stock.sum()), phev=int(a[a.EV_Type == 'PHEV'].final_estimated_stock.sum()),
                         **{d: int(a[a.DNO == d].final_estimated_stock.sum()) for d in ('EPN', 'LPN', 'SPN')}))
    return pd.DataFrame(rows)


def volatility(out):
    rows = []
    pers = list(QUARTERS.values())
    for method in ('A', 'C1'):
        for i in range(1, len(pers)):
            a, b = out[(method, pers[i - 1])], out[(method, pers[i])]
            for sub in ('BEV', 'PHEV', 'Total'):
                def series(u, col):
                    x = u if sub == 'Total' else u[u.EV_Type == sub]
                    return x.groupby('LSOA21CD')[col].sum()
                fa, fb = series(a, 'final_estimated_stock'), series(b, 'final_estimated_stock')
                d = fb - fa
                ra, rb = a.groupby('LSOA21CD').raw_total.sum(), b.groupby('LSOA21CD').raw_total.sum()
                cross = ((ra <= 100) & (rb > 100)) | ((ra > 100) & (rb <= 100))
                draw = series(b, 'raw_total') - series(a, 'raw_total')
                rows.append(dict(methodology=method, transition=f'{pers[i - 1]}->{pers[i]}', subtype=sub, net_change=int(d.sum()),
                                 positive_sum=int(d[d > 0].sum()), negative_sum=int(d[d < 0].sum()), n_negative=int((d < 0).sum()),
                                 n_decline_gt10=int((d < -10).sum()), n_decline_gt25=int((d < -25).sum()), n_decline_gt50=int((d < -50).sum()), n_decline_gt100=int((d < -100).sum()),
                                 n_threshold_crossers=int(cross.sum()), threshold_crossing_loss=int(d[cross & (d < 0)].sum()),
                                 model_declines_raw_rose=int(d[(d < 0) & (draw > 0)].sum()), n_model_declines_raw_rose=int(((d < 0) & (draw > 0)).sum())))
    return pd.DataFrame(rows)


def weight_and_pool_movement(national):
    pers = list(QUARTERS.values())
    rows = []
    for i in range(1, len(pers)):
        qa, na = national[('C1', pers[i - 1])]
        qb, nb = national[('C1', pers[i])]
        wa = pd.Series(na['national'].recipient_weight.values, index=qa.codes)
        wb = pd.Series(nb['national'].recipient_weight.values, index=qb.codes)
        idx = wa.index.union(wb.index)
        wa, wb = wa.reindex(idx).fillna(0), wb.reindex(idx).fillna(0)
        pa, pb = na['pool_bev'] + na['pool_phev'], nb['pool_bev'] + nb['pool_phev']
        w_eff = pb * (wb - wa)
        rows.append(dict(transition=f'{pers[i - 1]}->{pers[i]}', national_pool_prev=pa, national_pool=pb, pool_growth=pb - pa,
                         abs_weight_effect_all_lsoas=round(float(w_eff.abs().sum())), n_lsoas_weight_effect_gt5=int((w_eff.abs() > 5).sum())))
    return pd.DataFrame(rows)


def national_accounting(national):
    rows = []
    for (method, per), (qi, nat) in national.items():
        df = nat['national']
        misc = df.LSOA21CD == m.MISC_CODE
        geo = ~misc
        rows.append(dict(methodology=method, period=per, national_raw_bev=int(qi.total_bev.sum()), national_raw_phev=int(qi.total_phev.sum()),
                         private=int((df.private_bev + df.private_phev)[geo].sum()), company=int((df.company_bev + df.company_phev)[geo].sum()),
                         unclassified=int((df.unclassified_bev + df.unclassified_phev)[geo].sum()),
                         misc_raw=int((df.raw_bev + df.raw_phev)[misc].sum()), misc_retained_nongeographic=int((df.retained_bev + df.retained_phev)[misc].sum()),
                         pool_bev=nat['pool_bev'], pool_phev=nat['pool_phev'], pool_from_misc=int(((df.raw_bev - df.retained_bev) + (df.raw_phev - df.retained_phev))[misc].sum()),
                         pooled_company_diag=int(((df.company_bev - df.retained_company_bev) + (df.company_phev - df.retained_company_phev))[geo].sum()),
                         pooled_unclassified_diag=int(((df.unclassified_bev - df.retained_unclassified_bev) + (df.unclassified_phev - df.retained_unclassified_phev))[geo].sum()),
                         pooled_private=int(((df.private_bev - df.retained_private_bev) + (df.private_phev - df.retained_private_phev))[geo].sum()),
                         recipients_weight_gt0=int((df.recipient_weight > 0).sum()),
                         zero_private_geographic_lsoas=int(((df.private_bev + df.private_phev) == 0)[geo].sum()) if method == 'C1' else None,
                         zero_private_with_unclassified=int((((df.private_bev + df.private_phev) == 0) & ((df.unclassified_bev + df.unclassified_phev) > 0))[geo].sum()) if method == 'C1' else None,
                         lpn_floor_share=nat['lpn_floor'], ukpn_post=nat['ukpn_post'], lpn_post=nat['lpn_post'],
                         lpn_post_share_pct=round(100 * nat['lpn_post'] / nat['ukpn_post'], 4), lpn_uplift=nat['uplift'],
                         ukpn_final=nat['ukpn_post'] + nat['uplift'], lpn_final=nat['lpn_post'] + nat['uplift']))
    return pd.DataFrame(rows)


def examples(agg, out):
    want = {'LA': ['Peterborough', 'Westminster', 'City of London'],
            'LAEP': ['Peterborough LAEP', 'Westminster LAEP', 'City of London LAEP', 'Outer East LAEP', 'Norwich LAEP', 'West London LAEP', 'Norfolk - Suffolk LAEP']}
    t = agg[agg.EV_Type == 'Total']
    rows = []
    for lvl, names in want.items():
        for n in names:
            col = 'geography_name' if lvl == 'LA' else 'geography'
            for per in QUARTERS.values():
                a = int(t[(t.level == lvl) & (t[col] == n) & (t.methodology == 'A') & (t.period == per)].final_estimated_stock.sum())
                c = int(t[(t.level == lvl) & (t[col] == n) & (t.methodology == 'C1') & (t.period == per)].final_estimated_stock.sum())
                rows.append(dict(level=lvl, geography=n, period=per, A=a, C1=c, C1_minus_A=c - a, pct=round(100 * (c - a) / a, 2) if a else None))
    hs = []
    for code in ('E01015686', 'E01035716', 'E01034451'):
        for per in QUARTERS.values():
            for method in ('A', 'C1'):
                u = out[(method, per)]
                x = u[u.LSOA21CD == code].sum(numeric_only=True)
                hs.append(dict(LSOA21CD=code, period=per, methodology=method, raw_private=int(x.raw_private), raw_company=int(x.raw_company),
                               raw_unclassified=int(x.raw_unclassified), retained=int(x.retained_total), received=int(x.redistribution_received),
                               post=int(x.post_redistribution), uplift=int(x.lpn_uplift), final=int(x.final_estimated_stock)))
    return pd.DataFrame(rows), pd.DataFrame(hs)


def main():
    canon, flags, out, national, prov = run_all()
    acc = accounting_checks(out, national)
    geo = geography_checks(out, canon, flags)
    agg = aggregate(out)
    aggc = aggregation_checks(agg, canon)
    prod = production_reproduction(out, national)
    vol = volatility(out)
    wmov = weight_and_pool_movement(national)
    nacc = national_accounting(national)
    ex, hs = examples(agg, out)
    c1_lsoa = pd.concat([out[('C1', p)] for p in QUARTERS.values()], ignore_index=True)
    write_csv(c1_lsoa, 'c1_lsoa_quarterly.csv')
    write_csv(agg[agg.methodology == 'C1'].assign(methodology_id=m.METHODOLOGY_ID), 'c1_geography_aggregates.csv')
    cmp_ = agg.pivot_table(index=['level', 'geography', 'EV_Type', 'period'], columns='methodology', values='final_estimated_stock', aggfunc='sum').reset_index()
    cmp_['C1_minus_A'] = cmp_.C1 - cmp_.A
    write_csv(cmp_, 'comparison_A_vs_C1.csv')
    write_csv(nacc, 'national_accounting.csv')
    write_csv(vol, 'volatility_A_vs_C1.csv')
    write_csv(wmov, 'c1_weight_and_pool_movement.csv')
    write_csv(ex, 'high_impact_geographies.csv')
    write_csv(hs, 'hotspot_lsoas.csv')
    write_csv(prod, 'method_A_production_reproduction.csv')
    ctrl = []
    for _, r in prod.iterrows():
        ctrl.append((f'Method A reference {r.period}: LSOA/fuel values identical to production file', r.mismatches == 0, f'{r.mismatches} of {r.lsoa_fuel_keys}'))
    a26 = prod.set_index('period').loc['2026-03']
    for name, got, exp in (('A Mar-26 UKPN post', a26.ukpn_post, 882247), ('A Mar-26 LPN post', a26.lpn_post, 200524),
                           ('A Mar-26 uplift', a26.uplift, 26717), ('A Mar-26 final UKPN', a26.final, 908964),
                           ('A Mar-26 BEV', a26.bev, 598878), ('A Mar-26 PHEV', a26.phev, 310086),
                           ('A Mar-26 EPN', a26.EPN, 419870), ('A Mar-26 LPN', a26.LPN, 227241), ('A Mar-26 SPN', a26.SPN, 261853)):
        ctrl.append((f'{name} = {exp:,}', int(got) == exp, int(got)))
    tc = agg[(agg.EV_Type == 'Total') & (agg.methodology == 'C1')]
    val = lambda lvl, g, per, col='final_estimated_stock': int(tc[(tc.level == lvl) & (tc.geography == g) & (tc.period == per)][col].sum())
    # 20% LPN floor controls (redistribution unchanged from the prior C1 experiment; only the LPN floor differs)
    for name, got, exp in (('C1 Mar-26 UKPN post-redistribution', val('UKPN', 'UKPN', '2026-03', 'post_redistribution'), 896098),
                           ('C1 Mar-26 LPN post-redistribution', val('DNO', 'LPN', '2026-03', 'post_redistribution'), 191386),
                           ('C1 Mar-26 LPN uplift (20% floor)', val('UKPN', 'UKPN', '2026-03', 'lpn_uplift'), 0),
                           ('C1 Mar-26 final UKPN', val('UKPN', 'UKPN', '2026-03'), 896098),
                           ('C1 Mar-25 UKPN post-redistribution', val('UKPN', 'UKPN', '2025-03', 'post_redistribution'), 719684),
                           ('C1 Mar-26 Peterborough LAEP (no LPN LSOAs)', val('LAEP', 'Peterborough LAEP', '2026-03'), 7936),
                           ('C1 Mar-26 Norwich LAEP (no LPN LSOAs)', val('LAEP', 'Norwich LAEP', '2026-03'), 2889)):
        ctrl.append((f'{name} = {exp:,} (control, not forced)', got == exp, got))
    for per in QUARTERS.values():
        ctrl.append((f'C1 {per}: final UKPN = post-redistribution + uplift', val('UKPN', 'UKPN', per) == val('UKPN', 'UKPN', per, 'post_redistribution') + val('UKPN', 'UKPN', per, 'lpn_uplift'), ''))
    ctrl = pd.DataFrame(ctrl, columns=['check', 'passed', 'detail'])
    allchecks = pd.concat([acc.assign(group='accounting'), geo.assign(group='geography'), aggc.assign(group='aggregation'),
                           ctrl.assign(group='controls')], ignore_index=True)
    write_csv(allchecks, 'qa_checks.csv')
    meta = dict(methodology_id=m.METHODOLOGY_ID, status='EXPERIMENTAL CANDIDATE - not production, not used by dashboard', provenance=prov,
                lpn_minimum_share={'C1': m.C1_LPN_MIN_SHARE, 'A_reference': m.PRODUCTION_LPN_TARGET_SHARE},
                codes={'MODELLED_NOT_OBSERVED_LOCATION': 'keeper-address registrations after modelled redistribution; not observed vehicle locations',
                       'NONPRIVATE_JOINT_BLOCK': 'company and unclassified (suppressed-keeper) stock share the retention allowance proportionally and are pooled together; never treated as private'},
                inputs={k: str(v.relative_to(REPO)) for k, v in INPUTS.items()}, checks_passed=int(allchecks.passed.sum()), checks_total=len(allchecks))
    write_text(json.dumps(meta, indent=2, default=str), 'run_metadata.json')
    write_text(report(meta, allchecks, prod, nacc, agg, vol, wmov, ex, hs), 'qa_report.md')
    print(f"checks passed {meta['checks_passed']}/{meta['checks_total']}")
    for _, r in prod.iterrows():
        print('A', r.period, 'LSOA/fuel mismatches vs production', r.mismatches, '| post', r.ukpn_post, 'LPN post', r.lpn_post, 'uplift', r.uplift, 'final', r.final)
    uk = agg[(agg.level == 'UKPN') & (agg.EV_Type == 'Total') & (agg.methodology == 'C1')].set_index('period')
    for per, r in uk.iterrows():
        print('C1', per, 'post', int(r.post_redistribution), 'uplift', int(r.lpn_uplift), 'final', int(r.final_estimated_stock))
    failed = allchecks[~allchecks.passed]
    if len(failed):
        print('FAILED CHECKS:\n', failed.to_string())
    return 0 if failed.empty else 1


def table(df, index=True):
    """Plain-text table in a fenced block (avoids an optional tabulate dependency)."""
    return '```\n' + df.to_string(index=index) + '\n```'


def report(meta, checks, prod, nacc, agg, vol, wmov, ex, hs):
    t = agg[agg.EV_Type == 'Total']
    net = t[t.level.isin(['UKPN', 'DNO'])].pivot_table(index=['period', 'geography'], columns='methodology', values=['post_redistribution', 'lpn_uplift', 'final_estimated_stock'], aggfunc='sum')
    laep = t[t.level == 'LAEP'].pivot_table(index='geography', columns=['period', 'methodology'], values='final_estimated_stock', aggfunc='sum')
    lines = ['# Method C1 candidate - QA report', '', f"Status: {meta['status']}", f"Methodology: {meta['methodology_id']}", f"Provenance: {json.dumps(meta['provenance'])}", '',
             'Outputs are modelled estimates of keeper-address registrations after redistribution. They are not observed vehicle locations.', '',
             f"## QA checks: {meta['checks_passed']} / {meta['checks_total']} passed", '', table(checks.groupby('group').passed.agg(['sum', 'count'])), '',
             '## Method A reference vs production', '', table(prod, index=False), '', '## National accounting', '', table(nacc, index=False), '',
             '## UKPN / DNO (Total EV)', '', table(net), '', '## LAEP final estimated stock (Total EV)', '', table(laep), '',
             '## High-impact geographies', '', table(ex, index=False), '', '## Hotspot LSOAs', '', table(hs, index=False), '',
             '## Volatility (Total EV)', '', table(vol[vol.subtype == 'Total'], index=False), '', '## C1 weight and pool movement', '', table(wmov, index=False), '']
    return '\n'.join(lines)


if __name__ == '__main__':
    sys.exit(main())
