"""Read-only scan for Method A reversion pathways. Prints JSON; exit code 1 if any BLOCKER is present.

Usage: python experiments/ev_redistribution_c1/protections/reversion_scan.py [--repo <dir>] [--runner <run_integration.py>]

Checks the actual code (parsed with ast, never imported or executed) and data, not documentation:
  R1  external runner publishes ev_actuals_* into the dashboard integration folder
  R3  data_manager/promotion.py replaces ev_actuals_* (EV_CHANGED) or restores whole directories (rmtree)
  R3a promotable staged candidates carrying ev_actuals_* that differ from production
  R2  legacy project/output_processed/ev_actuals_dno.csv that a manual copy would publish
  R3b production backups holding EV actuals (restorable through R3)
  R5  git HEAD holds different EV actuals than production (checkout/restore would revert)
"""
import argparse
import ast
import hashlib
import json
import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
DEFAULT_REPO = HERE.parents[2]
sys.path.insert(0, str(HERE.parent))
from local_paths import runner_file  # noqa: E402
DEFAULT_RUNNER = runner_file('run_integration.py')     # from C1_RUNNER_DIR or integration_runner/local_paths.json
INTEG_REL = 'project/output_processed/integration'
TARGETS = ('ev_actuals_dno.csv', 'ev_actuals_la.csv', 'ev_actuals_laep.csv')


def sha(p):
    return hashlib.sha256(Path(p).read_bytes()).hexdigest()


def _strings(node):
    return [n.value for n in ast.walk(node) if isinstance(n, ast.Constant) and isinstance(n.value, str)]


def _calls(node):
    out = []
    for n in ast.walk(node):
        if isinstance(n, ast.Call):
            f = n.func
            out.append(f.attr if isinstance(f, ast.Attribute) else getattr(f, 'id', ''))
    return out


def _publish_files(tree):
    consts = [n for n in tree.body if isinstance(n, ast.Assign) and any(getattr(t, 'id', '') == 'PUBLISH_FILES' for t in n.targets)]
    return set(_strings(consts[0])) if consts else None


def scan_runner(path):
    """R1: the operational runner may publish to the dashboard only through a guard whose list excludes EV actuals."""
    kind = 'supported-operational'
    if not Path(path).exists():
        return [{'id': 'R1', 'kind': kind, 'severity': 'BLOCKER', 'finding': 'external runner not found; cannot verify it no longer publishes EV actuals', 'evidence': str(path)}]
    tree = ast.parse(Path(path).read_bytes().decode('utf-8'))
    run = next((n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name == 'run'), None)
    if run is None:
        return [{'id': 'R1', 'kind': kind, 'severity': 'BLOCKER', 'finding': 'runner has no run(); publishing path unknown', 'evidence': str(path)}]
    run_src = ast.dump(run)
    if 'copy2' in _calls(run) and 'DASHBOARD_INTEGRATION_PUBLISH' in run_src:
        return [{'id': 'R1', 'kind': kind, 'severity': 'BLOCKER', 'finding': 'external runner copies files straight into the dashboard on QA PASS',
                 'evidence': f'{path}: run() copies {sorted({s for s in _strings(run) if s in TARGETS})} to DASHBOARD_INTEGRATION_PUBLISH'}]
    local = _publish_files(tree)
    if local is not None:                      # Phase 6 proposal shape: PUBLISH_FILES + _publish() in run_integration.py
        published, source = local, str(path)
    else:
        guard = Path(path).with_name('publish_guard.py')
        if 'publish' not in _calls(run) or not guard.exists():
            return [{'id': 'R1', 'kind': kind, 'severity': 'BLOCKER', 'finding': 'runner publishing path not recognised (fail closed)', 'evidence': str(path)}]
        gtree = ast.parse(guard.read_bytes().decode('utf-8'))
        published, source = _publish_files(gtree) or set(), str(guard)
        if not any(s in TARGETS for s in _strings(gtree)):
            return [{'id': 'R1', 'kind': kind, 'severity': 'BLOCKER', 'finding': 'publish guard has no EV-actuals refusal', 'evidence': str(guard)}]
    if any(s in TARGETS or s.startswith('ev_actuals_') for s in published):
        return [{'id': 'R1', 'kind': kind, 'severity': 'BLOCKER', 'finding': 'runner publish list includes ev_actuals_*', 'evidence': f'{source}: {sorted(published)}'}]
    return [{'id': 'R1', 'kind': kind, 'severity': 'OK', 'finding': 'runner publishes only through a guard that excludes ev_actuals_*',
             'evidence': f'{source}: PUBLISH_FILES={sorted(published)}'}]


def scan_promotion(path):
    tree = ast.parse(Path(path).read_bytes().decode('utf-8'))
    out = []
    ev_changed = []
    for cls in (n for n in tree.body if isinstance(n, ast.ClassDef) and n.name == 'AtomicPromotion'):
        for n in cls.body:
            if isinstance(n, ast.Assign) and any(getattr(t, 'id', '') == 'EV_CHANGED' for t in n.targets):
                ev_changed = _strings(n)
    hits = [s for s in ev_changed if s.startswith('integration/ev_actuals_')]
    out.append({'id': 'R3', 'kind': 'supported-operational', 'severity': 'BLOCKER' if hits else 'OK',
                'finding': 'promotion.py EV_CHANGED replaces ev_actuals_*' if hits else 'promotion.py EV_CHANGED excludes ev_actuals_*',
                'evidence': f'{path}: EV_CHANGED ev_actuals entries {hits}'})
    promote_fns = [n for n in ast.walk(tree) if isinstance(n, ast.FunctionDef) and n.name in ('promote', '_promote_locked')]
    swap_calls = sorted({c for fn in promote_fns for c in _calls(fn) if c in ('rename', 'copytree')})
    out.append({'id': 'R3', 'kind': 'supported-operational', 'severity': 'WARN' if swap_calls else 'OK',
                'finding': 'promotion swaps or copies the whole output_processed directory' if swap_calls
                else 'promotion replaces managed files individually (no directory swap)',
                'evidence': f'{path}: promote calls {swap_calls}'})
    restore = next((n for n in ast.walk(tree) if isinstance(n, ast.FunctionDef) and n.name == 'restore_backup'), None)
    whole = restore is not None and 'rmtree' in _calls(restore)
    out.append({'id': 'R3', 'kind': 'supported-operational', 'severity': 'BLOCKER' if whole else 'OK',
                'finding': 'restore_backup deletes and recopies the whole output_processed tree' if whole else 'restore_backup is file-level',
                'evidence': f'{path}: restore_backup calls {sorted(set(_calls(restore))) if restore else None}'})
    return out


def scan_data(repo, promotion_protected=False):
    repo = Path(repo)
    out = []
    prod = {n: sha(repo / INTEG_REL / n) for n in TARGETS if (repo / INTEG_REL / n).exists()}
    staging = repo / 'project' / 'dft_staging'
    promotable, quarantined = [], []
    for run in sorted(staging.glob('run_*')) if staging.exists() else []:
        rid = run.name[4:]
        files = [n for n in TARGETS if (run / 'outputs' / 'integration' / n).exists()
                 and sha(run / 'outputs' / 'integration' / n) != prod.get(n)]
        if not files:
            continue
        if (run / 'QUARANTINED.json').exists():
            quarantined.append(run.name)
            continue
        eligible = (run / 'outputs' / 'lsoa_ev_bodytype.csv').exists() and not any(t in rid.upper() for t in ('TEST', 'SYNTHETIC'))
        if eligible:
            promotable.append(f'{run.name}: {files}')
    if promotable:
        sev = 'WARN' if promotion_protected else 'BLOCKER'
        finding = ('staged candidates carry different ev_actuals_*; protected promotion.py rejects them' if promotion_protected
                   else 'promotable staged candidates carry different ev_actuals_* (promotion.py would publish them)')
    else:
        sev, finding = 'OK', 'no promotable staged candidate carries different ev_actuals_*'
    out.append({'id': 'R3a', 'kind': 'supported-operational', 'severity': sev, 'finding': finding,
                'evidence': {'promotable': promotable, 'quarantined': quarantined}})
    legacy = repo / 'project' / 'output_processed' / 'ev_actuals_dno.csv'
    if legacy.exists():
        same = sha(legacy) == prod.get('ev_actuals_dno.csv')
        out.append({'id': 'R2', 'kind': 'manual-admin', 'severity': 'INFO',
                    'finding': ('legacy output_processed/ev_actuals_dno.csv ' + ('equals' if same else 'differs from') +
                                ' production; no code copies it into integration/, but a manual copy would revert C1'),
                    'evidence': str(legacy)})
    backups = sorted({p.parents[2].name for p in (repo / 'project' / 'production_backups').glob('*/output_processed/integration/ev_actuals_dno.csv')})
    out.append({'id': 'R3b', 'kind': 'supported-operational' if not promotion_protected else 'manual-admin',
                'severity': ('INFO' if promotion_protected else 'WARN') if backups else 'OK',
                'finding': f'{len(backups)} production backups hold EV actuals' + (
                    '; protected rollback never restores EV actuals and refuses historic backups (manual copying remains possible)'
                    if promotion_protected else ' (restorable via unprotected promotion.py)'),
                'evidence': backups[:3] + (['...'] if len(backups) > 3 else [])})
    try:
        head = {}
        for n in TARGETS:
            blob = subprocess.run(['git', 'show', f'HEAD:{INTEG_REL}/{n}'], cwd=repo, capture_output=True, check=True).stdout
            head[n] = blob.split(b'oid sha256:')[1][:64].decode() if b'oid sha256:' in blob else hashlib.sha256(blob).hexdigest()
        differs = [n for n in TARGETS if head.get(n) != prod.get(n)]
        out.append({'id': 'R5', 'kind': 'manual-admin', 'severity': 'WARN' if differs else 'INFO',
                    'finding': 'git HEAD differs from production for ' + str(differs) if differs else 'git HEAD equals production EV actuals (commit C1 after release)',
                    'evidence': {n: (head.get(n, '')[:12], (prod.get(n) or '')[:12]) for n in TARGETS}})
    except (subprocess.CalledProcessError, FileNotFoundError, IndexError):
        out.append({'id': 'R5', 'severity': 'INFO', 'finding': 'not a git checkout; skipped', 'evidence': None})
    return out


def scan(repo=DEFAULT_REPO, runner=DEFAULT_RUNNER):
    repo = Path(repo)
    promotion = scan_promotion(repo / 'data_manager' / 'promotion.py')
    protected = all(f['severity'] == 'OK' for f in promotion)
    findings = scan_runner(runner) + promotion + scan_data(repo, protected)
    copy_manifest = repo / 'integration_runner' / 'SOURCE_MANIFEST.json'
    if copy_manifest.exists() and Path(runner).exists():
        manifest = json.loads(copy_manifest.read_text(encoding='utf-8'))
        current = manifest.get('current_sha256_eol_normalised', manifest['current_sha256'])
        norm = (lambda p: hashlib.sha256(Path(p).read_bytes().replace(b'\r\n', b'\n')).hexdigest()) \
            if 'current_sha256_eol_normalised' in manifest else sha
        drift = sorted(n for n, h in current.items() if not (Path(runner).parent / n).exists() or norm(Path(runner).parent / n) != h)
        findings.append({'id': 'R1v', 'kind': 'supported-operational', 'severity': 'WARN' if drift else 'OK',
                         'finding': 'operational runner differs from the version-controlled copy' if drift
                         else 'operational runner matches the version-controlled copy', 'evidence': drift})
    return {'blockers': sum(f['severity'] == 'BLOCKER' for f in findings), 'findings': findings}


if __name__ == '__main__':
    ap = argparse.ArgumentParser()
    ap.add_argument('--repo', default=str(DEFAULT_REPO))
    ap.add_argument('--runner', default=str(DEFAULT_RUNNER))
    a = ap.parse_args()
    r = scan(a.repo, a.runner)
    print(json.dumps(r, indent=2))
    sys.exit(1 if r['blockers'] else 0)
