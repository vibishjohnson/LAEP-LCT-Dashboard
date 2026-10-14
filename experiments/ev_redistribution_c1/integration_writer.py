"""C1 integration writer - builds dashboard EV actuals files from the validated C1 LSOA output. SHADOW ONLY.

Usage (from the repository root):
    python experiments/ev_redistribution_c1/integration_writer.py

Writes:
    experiments/ev_redistribution_c1/shadow_integration/{ev_actuals_dno,ev_actuals_la,ev_actuals_laep}.csv
    experiments/ev_redistribution_c1/shadow_integration/manifest.json, metadata_patch_proposal.json
    experiments/ev_redistribution_c1/method_a_archive/  (byte copy of the Method A DNO history + manifest)

Never writes to project/output_processed/, integration/, ev/, lookups/, data_manager/ or any other repository path.
Outputs are deterministic: re-running produces byte-identical files.
"""
import hashlib
import json
import sys
from pathlib import Path

import numpy as np
import pandas as pd

EXP_DIR = Path(__file__).resolve().parent
REPO = EXP_DIR.parents[1]
SHADOW_DIR = EXP_DIR / 'shadow_integration'
ARCHIVE_DIR = EXP_DIR / 'method_a_archive'

C1_LSOA = EXP_DIR / 'outputs' / 'c1_lsoa_quarterly.csv'
C1_METADATA = EXP_DIR / 'outputs' / 'run_metadata.json'
LSOA_DNO = REPO / 'lookups' / 'LSOA to DNO.csv'
GEOGRAPHY = REPO / 'project' / 'output_processed' / 'integration' / 'geography_lookup.csv'
PROD_INTEGRATION = REPO / 'project' / 'output_processed' / 'integration'
PROD_METHOD_A_DNO = PROD_INTEGRATION / 'ev_actuals_dno.csv'
PROD_METHOD_A_LSOA = REPO / 'project' / 'output_processed' / 'ev_quarterly_lsoa.csv'

METHODOLOGY_ID = 'C1_DYNAMIC_PRIVATE_EV'
LPN_MIN_SHARE = 0.20
EXPECTED_PERIODS = ['2025-03', '2025-06', '2025-09', '2025-12', '2026-03']
EXPECTED_LSOAS = 11023
EXPECTED_DNO_LSOAS = {'EPN': 4993, 'LPN': 2931, 'SPN': 3099}
FUELS = ('BEV', 'PHEV')

# Production integration schemas (verified against the existing files by the tests).
DNO_COLUMNS = ['period', 'tech_type', 'DNO', 'EV_Type', 'install_count', 'total_kw']
LA_COLUMNS = ['period', 'LAD22CD', 'LAD22NM', 'EV_Type', 'value', 'period_end', 'time_grain', 'tech_type', 'observation_source',
              'forecast_source', 'forecast_scenario', 'dfes_pathway', 'building_block_id', 'metric', 'is_interpolated',
              'is_anchor', 'source_year', 'anchor_date']
LAEP_COLUMNS = ['period', 'LAEP'] + LA_COLUMNS[3:]
OBSERVATION_CONSTANTS = {'time_grain': 'quarter', 'tech_type': 'EV', 'observation_source': 'ACTUAL', 'forecast_source': np.nan,
                         'forecast_scenario': np.nan, 'dfes_pathway': np.nan, 'building_block_id': np.nan, 'metric': 'stock',
                         'is_interpolated': False, 'is_anchor': False, 'source_year': np.nan, 'anchor_date': np.nan}
FORBIDDEN_ROOTS = [REPO / 'project', REPO / 'ev', REPO / 'lookups', REPO / 'data_manager', REPO / 'scripts', REPO / 'templates', REPO / 'dfes']


# ------------------------------------------------------------------ write guard
def _is_within(path, root):
    try:
        path.relative_to(root)
        return True
    except ValueError:
        return False


ALLOWED_REPO_ROOTS = (SHADOW_DIR, ARCHIVE_DIR)


def check_output_root(root):
    """Validate an output directory against an explicit allowlist.

    Inside the repository only shadow_integration/ and method_a_archive/ (in this experiment) are allowed; anything outside
    the repository (e.g. a test temp dir) is allowed. Symlinked roots or parents are refused so a shadow path cannot
    redirect into production. There is no fallback location: an invalid root raises.
    """
    root = Path(root)
    if not root.is_absolute():
        raise PermissionError(f'output root must be absolute: {root}')
    for part in [root, *root.parents]:
        if part.exists() and part.is_symlink():
            raise PermissionError(f'symlinked output path refused: {part}')
    real = root.resolve()
    if _is_within(real, REPO.resolve()):
        for bad in FORBIDDEN_ROOTS:
            if _is_within(real, bad.resolve()):
                raise PermissionError(f'refusing forbidden location: {real}')
        if not any(_is_within(real, allowed.resolve()) for allowed in ALLOWED_REPO_ROOTS):
            raise PermissionError(f'refusing repository location outside the shadow/archive allowlist: {real}')
    return real


def guarded_file(root, name):
    """Resolve a file name inside a validated root; refuse traversal or absolute names."""
    real_root = check_output_root(root)
    if Path(name).is_absolute() or '..' in Path(name).parts:
        raise PermissionError(f'refusing path traversal / absolute file name: {name}')
    target = (real_root / name).resolve()
    if not _is_within(target, real_root):
        raise PermissionError(f'refusing write outside output root: {target}')
    return target


def _store(p, data, write_once=False):
    """Strict no-write when the content is unchanged (the file is not opened for writing, mtime is kept).
    write_once files (the Method A archive) may be created but never replaced with different bytes."""
    if p.exists():
        if p.read_bytes() == data:
            return False
        if write_once:
            raise PermissionError(f'write-once file would change; refusing to overwrite: {p}')
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_bytes(data)
    return True


def write_csv(df, root, name, write_once=False):
    p = guarded_file(root, name)
    data = df.to_csv(index=False, lineterminator='\r\n').encode('utf-8')   # production files use CRLF, no BOM
    _store(p, data, write_once)
    return p, hashlib.sha256(data).hexdigest()


def write_json(obj, root, name, write_once=False):
    p = guarded_file(root, name)
    data = (json.dumps(obj, indent=2, sort_keys=True) + '\n').encode('utf-8')
    _store(p, data, write_once)
    return p


def copy_bytes(src, root, name, write_once=False):
    p = guarded_file(root, name)
    data = Path(src).read_bytes()
    _store(p, data, write_once)
    return p, hashlib.sha256(data).hexdigest()


def sha256_file(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


# ------------------------------------------------------------------ inputs
def load_geography():
    """Authoritative mapping: canonical LSOA->DNO/LAD from lookups/LSOA to DNO.csv, LAEP from integration geography_lookup."""
    raw = pd.read_csv(LSOA_DNO, encoding='utf-8-sig').dropna(subset=['LSOA21CD'])
    canon = raw[raw['Majority Licence area'].isin(['EPN', 'LPN', 'SPN'])][['LSOA21CD', 'Majority Licence area', 'LAD22CD', 'LAD22NM']]
    canon = canon.rename(columns={'Majority Licence area': 'DNO'})
    if canon.LSOA21CD.duplicated().any():
        raise ValueError('duplicate LSOA codes in LSOA to DNO lookup')
    laep = pd.read_csv(GEOGRAPHY)[['LSOA21CD', 'LAEP']]
    if laep.LSOA21CD.duplicated().any():
        raise ValueError('duplicate LSOA codes in geography lookup')
    canon = canon.merge(laep, on='LSOA21CD', how='left')
    if len(canon) != EXPECTED_LSOAS or canon.DNO.value_counts().to_dict() != EXPECTED_DNO_LSOAS:
        raise ValueError(f'canonical geography mismatch: {len(canon)} LSOAs, {canon.DNO.value_counts().to_dict()}')
    if canon[['LAD22CD', 'LAD22NM']].isna().any().any():
        raise ValueError('canonical LSOAs missing LAD mapping')
    return canon.set_index('LSOA21CD')


def validate_c1_lsoa(df, canon, expected_periods=EXPECTED_PERIODS):
    """Reject malformed or non-validated C1 input. Returns the minimal frame used for aggregation."""
    required = ['methodology_id', 'period', 'LSOA21CD', 'EV_Type', 'final_estimated_stock', 'post_redistribution', 'lpn_uplift', 'lpn_min_share']
    missing = [c for c in required if c not in df.columns]
    if missing:
        raise ValueError(f'C1 input missing columns: {missing}')
    if df[required].isna().any().any():
        raise ValueError('C1 input has nulls in required columns')
    if set(df.methodology_id) != {METHODOLOGY_ID}:
        raise ValueError(f'unexpected methodology_id: {sorted(set(df.methodology_id))}')
    if not np.allclose(df.lpn_min_share.astype(float), LPN_MIN_SHARE):
        raise ValueError('C1 input is not the 20% LPN-floor version')
    if sorted(df.period.unique()) != list(expected_periods):
        raise ValueError(f'unexpected periods: {sorted(df.period.unique())}')
    if set(df.EV_Type) != set(FUELS):
        raise ValueError(f'unexpected EV_Type values: {sorted(set(df.EV_Type))}')
    if df.duplicated(['period', 'LSOA21CD', 'EV_Type']).any():
        raise ValueError('duplicate period/LSOA/EV_Type keys in C1 input')
    stock = df.final_estimated_stock
    if not pd.api.types.is_integer_dtype(stock) or (stock < 0).any():
        raise ValueError('final_estimated_stock must be non-negative integers')
    if not (stock == df.post_redistribution + df.lpn_uplift).all():
        raise ValueError('final_estimated_stock != post_redistribution + lpn_uplift')
    for per, g in df.groupby('period'):
        if set(g.LSOA21CD) != set(canon.index) or len(g) != 2 * len(canon):
            raise ValueError(f'{per}: C1 input does not cover exactly the {len(canon)} canonical LSOAs for both fuels')
    return df[['period', 'LSOA21CD', 'EV_Type', 'final_estimated_stock']].rename(columns={'final_estimated_stock': 'stock'})


# ------------------------------------------------------------------ aggregation (method-agnostic)
def period_end(period):
    return (pd.Period(period, freq='M').end_time.normalize()).strftime('%Y-%m-%d')


def build_integration_frames(lsoa, canon):
    """lsoa: columns period, LSOA21CD, EV_Type, stock (integers). Returns (dno, la, laep) in production schemas."""
    x = lsoa.join(canon, on='LSOA21CD', how='inner')
    if len(x) != len(lsoa):
        raise ValueError('LSOA rows without canonical geography')
    dno = x.groupby(['period', 'DNO', 'EV_Type'], as_index=False).stock.sum().rename(columns={'stock': 'install_count'})
    dno['tech_type'] = 'EV'
    dno['total_kw'] = 0.0
    dno = dno[DNO_COLUMNS].sort_values(['period', 'DNO', 'EV_Type'], kind='stable').reset_index(drop=True)

    def observation_frame(keys, columns):
        g = x.dropna(subset=[keys[0]]).groupby(['period'] + keys + ['EV_Type'], as_index=False).stock.sum().rename(columns={'stock': 'value'})
        g['period_end'] = g.period.map(period_end)
        for k, v in OBSERVATION_CONSTANTS.items():
            g[k] = v
        return g[columns].sort_values(['period', keys[0], 'EV_Type'], kind='stable').reset_index(drop=True)

    la = observation_frame(['LAD22CD', 'LAD22NM'], LA_COLUMNS)
    laep = observation_frame(['LAEP'], LAEP_COLUMNS)
    for f in (dno, la, laep):
        f['install_count' if 'install_count' in f else 'value'] = f['install_count' if 'install_count' in f else 'value'].astype(np.int64)
    return dno, la, laep


def to_csv_bytes(df):
    return df.to_csv(index=False, lineterminator='\r\n').encode('utf-8')


# ------------------------------------------------------------------ main writer
def write_all(shadow_root=SHADOW_DIR, archive_root=ARCHIVE_DIR):
    canon = load_geography()
    c1 = pd.read_csv(C1_LSOA)
    lsoa = validate_c1_lsoa(c1, canon)
    dno, la, laep = build_integration_frames(lsoa, canon)
    if (sorted(dno.period.unique()) != EXPECTED_PERIODS or la.LAD22CD.nunique() != 121 or laep.LAEP.nunique() != 14):
        raise ValueError('shadow output failed coverage checks')

    outputs = {}
    for name, frame in (('ev_actuals_dno.csv', dno), ('ev_actuals_la.csv', la), ('ev_actuals_laep.csv', laep)):
        _, digest = write_csv(frame, shadow_root, name)
        outputs[name] = {'sha256': digest, 'rows': len(frame), 'periods': sorted(frame.period.unique())}

    meta = json.loads(C1_METADATA.read_text(encoding='utf-8'))
    inputs = {str(p.relative_to(REPO)).replace('\\', '/'): sha256_file(p) for p in (C1_LSOA, C1_METADATA, LSOA_DNO, GEOGRAPHY)}
    manifest = {
        'methodology_id': METHODOLOGY_ID, 'lpn_min_share': LPN_MIN_SHARE, 'status': 'SHADOW - not production',
        'periods': EXPECTED_PERIODS, 'candidate_provenance': meta.get('provenance', {}), 'candidate_checks': [meta.get('checks_passed'), meta.get('checks_total')],
        'inputs_sha256': inputs, 'outputs': outputs,
        'intended_production_targets': {k: f'project/output_processed/integration/{k}' for k in outputs},
    }
    write_json(manifest, shadow_root, 'manifest.json')
    write_json({'proposed_changes_for_integration_metadata_json': {
        'latest_actual_period': max(EXPECTED_PERIODS), 'ev_actuals_first_period': min(EXPECTED_PERIODS),
        'ev_actuals_methodology_id': METHODOLOGY_ID, 'ev_actuals_lpn_min_share': LPN_MIN_SHARE,
        'ev_actuals_method_a_archive': 'experiments/ev_redistribution_c1/method_a_archive/manifest.json'},
        'note': 'Proposal only. Apply at an approved promotion; the live metadata.json is not modified by this writer.'},
        shadow_root, 'metadata_patch_proposal.json')

    archive = write_method_a_archive(archive_root)
    return dno, la, laep, manifest, archive


def write_method_a_archive(archive_root=ARCHIVE_DIR):
    """Byte copy of the Method A DNO history (2011-12 onwards) plus a labelled pre-2025 history and a manifest.

    The archive is write-once: re-running with identical inputs writes nothing; different bytes are refused.
    Once production holds C1 (no pre-2025 history), the archive can no longer be (re)built from production.
    An existing archive is frozen: it is verified against its manifest and never rebuilt.
    """
    existing = guarded_file(archive_root, 'manifest.json')
    if existing.exists():
        manifest = json.loads(existing.read_text(encoding='utf-8'))
        if sha256_file(guarded_file(archive_root, 'ev_actuals_dno_method_a_original.csv')) != manifest['copy_sha256']:
            raise ValueError('Method A archive does not match its manifest')
        return manifest
    src = pd.read_csv(PROD_METHOD_A_DNO)
    if not (src.period < min(EXPECTED_PERIODS)).any():
        raise ValueError('production ev_actuals_dno.csv no longer contains Method A history; the archive cannot be rebuilt from it')
    _, digest = copy_bytes(PROD_METHOD_A_DNO, archive_root, 'ev_actuals_dno_method_a_original.csv', write_once=True)
    hist = src[src.period < min(EXPECTED_PERIODS)].copy()
    hist.insert(0, 'methodology_id', 'A_PRODUCTION_LEGACY')
    hist['estimate_type'] = 'LEGACY_METHOD_A_NOT_COMPARABLE_WITH_C1'
    write_csv(hist, archive_root, 'method_a_dno_history_before_2025-03.csv', write_once=True)
    manifest = {
        'methodology_id': 'A_PRODUCTION_LEGACY',
        'source_file': 'project/output_processed/integration/ev_actuals_dno.csv',
        'source_sha256': sha256_file(PROD_METHOD_A_DNO), 'copy_sha256': digest,
        'source_rows': len(src), 'source_periods': [src.period.min(), src.period.max(), int(src.period.nunique())],
        'history_before_c1_rows': len(hist), 'history_before_c1_periods': [hist.period.min(), hist.period.max(), int(hist.period.nunique())],
        'method_a_lsoa_record': {'file': 'project/output_processed/ev_quarterly_lsoa.csv', 'sha256': sha256_file(PROD_METHOD_A_LSOA),
                                 'note': 'not copied (large); production file left untouched'},
        'note': 'Method A legacy history. Do not concatenate with Method C1 actuals into one stock series.',
    }
    write_json(manifest, archive_root, 'manifest.json', write_once=True)
    return manifest


if __name__ == '__main__':
    dno, la, laep, manifest, archive = write_all()
    print('shadow outputs:', {k: v['rows'] for k, v in manifest['outputs'].items()})
    for per, g in dno.groupby('period'):
        print(per, 'UKPN', int(g.install_count.sum()), {d: int(g[g.DNO == d].install_count.sum()) for d in ('EPN', 'LPN', 'SPN')})
    print('Method A archive:', archive['source_rows'], 'rows; history before C1:', archive['history_before_c1_rows'])
    sys.exit(0)
