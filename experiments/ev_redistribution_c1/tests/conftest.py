"""Session-wide no-write guard for validated artefacts and production files.

Records sha256 (byte-content integrity) and st_mtime_ns (strict no-write: a rewrite with identical bytes still
changes the modification time) for every guarded file at the start of the test session, and fails the session if
any differs at the end. Tests must only write to temporary directories.
"""
import hashlib
import os
import sys
from pathlib import Path

EXP = Path(__file__).resolve().parents[1]
REPO = EXP.parents[1]
INTEG = REPO / 'project' / 'output_processed' / 'integration'
sys.path.insert(0, str(EXP))
from local_paths import runner_dir  # noqa: E402
RUNNER = runner_dir() or Path('__runner_not_configured__')   # operational runner (guarded only if present)


def guarded_files():
    files = [EXP / 'c1_method.py', EXP / 'run_c1.py', REPO / 'dashboard.html', REPO / 'lookups' / 'LSOA to DNO.csv',
             REPO / 'project' / 'output_processed' / 'ev_quarterly_lsoa.csv', REPO / 'data_manager' / 'promotion.py']
    for d in (EXP / 'outputs', EXP / 'method_a_archive', EXP / 'shadow_integration', INTEG,
              REPO / 'integration_runner' / 'dashboard_integration', RUNNER):
        if d.exists():
            files += sorted(p for p in d.iterdir() if p.is_file())
    staged = REPO / 'project' / 'dft_staging' / 'run_smmt_nowcast_20261001_121056'
    files += [p for p in (staged / 'QUARANTINED.json', staged / 'qa_report.md', staged / 'metadata.json') if p.exists()]
    return files


def snapshot():
    out = {}
    for p in guarded_files():
        st = os.stat(p)
        out[str(p)] = (hashlib.sha256(p.read_bytes()).hexdigest(), st.st_mtime_ns, st.st_size)
    return out


_BEFORE = {}


def pytest_sessionstart(session):
    _BEFORE.update(snapshot())


def pytest_sessionfinish(session, exitstatus):
    after = snapshot()
    content = [p for p in set(_BEFORE) | set(after) if (_BEFORE.get(p) or (None,))[0] != (after.get(p) or (None,))[0]]
    rewritten = [p for p in set(_BEFORE) & set(after) if _BEFORE[p][0] == after[p][0] and _BEFORE[p][1] != after[p][1]]
    if content or rewritten:
        tr = session.config.pluginmanager.get_plugin('terminalreporter')
        msg = (f'NO-WRITE GUARD FAILED: content changed {sorted(content)}; '
               f'rewritten with identical bytes (mtime changed) {sorted(rewritten)}')
        if tr:
            tr.write_line(msg, red=True)
        session.exitstatus = 1
