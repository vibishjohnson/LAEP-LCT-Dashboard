"""Admin UI/API status for quarantined candidates (app_data_manager.py), exercised in an isolated child process.

The child process imports the real Flask app with a temporary SQLite database, a stub nowcast engine and a no-op
registry initialiser, then points PROJECT_ROOT at a disposable sandbox before calling the routes with the Flask
test client. The real database, registry, staged runs and outputs are hashed before and after.
"""
import hashlib
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

EXP = Path(__file__).resolve().parents[1]
REPO = EXP.parents[1]
sys.path.insert(0, str(REPO))
from tests import promotion_fixtures as fx  # noqa: E402

RUN = 'smmt_nowcast_20261001_121056'
REAL_FILES = [REPO / 'project' / 'data_manager' / 'database.db', REPO / 'project' / 'dft_staging' / 'candidate_registry.json',
              REPO / 'project' / 'promotion_history.jsonl', REPO / 'project' / 'output_processed' / 'integration' / 'monthly_forecast_dno.csv',
              *(REPO / 'project' / 'output_processed' / 'integration' / f'ev_actuals_{g}.csv' for g in ('dno', 'la', 'laep'))]

CHILD = r'''
import json, os, sys
REPO, SANDBOX, TMPDB = sys.argv[1], sys.argv[2], sys.argv[3]
sys.path.insert(0, REPO)
import data_manager.db as dbm
dbm.DB_PATH = TMPDB
dbm.Database.__init__.__defaults__ = (TMPDB,)
import data_manager.candidate_registry as cr
cr.initialize_smmt_candidate = lambda: True
import data_manager.nowcast as nc
class StubNowcast:
    def __init__(self, *a, **k):
        self.weights = {}
    def get_qa_report(self):
        return ""
nc.NowcastEngine = StubNowcast
import app_data_manager as app
app.PROJECT_ROOT = SANDBOX
app.candidate_registry = cr.CandidateRegistry(SANDBOX)      # the sandbox's own registry (see run_app)
client = app.app.test_client()
out = {
    "candidates": client.get("/candidates").get_data(as_text=True),
    "review": client.get("/promotion-review/%s" % sys.argv[4]).get_data(as_text=True),
    "index": client.get("/").get_data(as_text=True),
    "promote": client.post("/api/promote", json={"run_id": sys.argv[4]}).get_json(),
}
print("JSON:" + json.dumps(out))
'''


def run_app(tmp_path, quarantined):
    sandbox = tmp_path / 'sandbox'
    fx.make_project(str(sandbox))
    fx.make_candidate(str(sandbox), RUN, forecast=fx.forecast_frame(smmt=True), quarantined=quarantined)
    (sandbox / 'project' / 'dft_staging' / 'production_candidate_smmt').mkdir()
    # the legacy admin run, registered in the sandbox (a clean checkout has no runtime registry of its own)
    (sandbox / 'project' / 'dft_staging' / 'candidate_registry.json').write_text(json.dumps(
        {RUN: {'type': 'nowcast', 'directory': 'project/dft_staging/production_candidate_smmt', 'created_at': '2026-10-01T12:10:56',
               'metadata': {'type': 'nowcast'}}}), encoding='utf-8')
    before = fx.tree(str(sandbox))
    script = tmp_path / 'child.py'
    script.write_text(CHILD, encoding='utf-8')
    env = {**os.environ, 'PYTHONDONTWRITEBYTECODE': '1'}
    r = subprocess.run([sys.executable, str(script), str(REPO), str(sandbox), str(tmp_path / 'db.sqlite'), RUN],
                       cwd=REPO, capture_output=True, text=True, env=env, timeout=600)
    line = next((l for l in r.stdout.splitlines() if l.startswith('JSON:')), None)
    assert line, r.stderr[-3000:]
    return json.loads(line[5:]), sandbox, before


@pytest.fixture
def real_state():
    # local runtime files (database, registry, history) may be absent in a clean checkout; absence must persist too
    state = lambda: {str(p): hashlib.sha256(p.read_bytes()).hexdigest() if p.exists() else None for p in REAL_FILES}
    snap = state()
    yield
    assert state() == snap


def test_quarantined_candidate_shows_ineligible_and_cannot_be_promoted(tmp_path, real_state):
    out, sandbox, before = run_app(tmp_path, quarantined=True)
    assert 'INELIGIBLE — QUARANTINED' in out['candidates'] and 'READY FOR PROMOTION' not in out['candidates']
    assert 'READY FOR PROMOTION' not in out['index']      # home lists SMMT nowcast candidates only, never legacy runs
    assert 'quarantined' in out['review'] and 'Candidate directory not found' not in out['review']
    assert out['promote']['success'] is False and 'quarantined' in out['promote']['message']
    assert fx.tree(str(sandbox)) == before                                 # API refusal wrote nothing


def test_eligible_candidate_keeps_ready_status(tmp_path, real_state):
    out, sandbox, before = run_app(tmp_path, quarantined=False)
    assert 'READY FOR PROMOTION' in out['candidates'] and 'INELIGIBLE' not in out['candidates']
    assert 'READY FOR PROMOTION' not in out['index']      # the home page offers no legacy admin promotion


def test_app_source_derives_status_from_eligibility():
    src = (REPO / 'app_data_manager.py').read_text(encoding='utf-8')
    assert '"status": "READY FOR PROMOTION"' not in src
    assert src.count('candidate_status(PROJECT_ROOT') == 2          # /candidates and /promotion-review
    assert 'can_promote = os.path.exists(candidate_dir)' not in src
