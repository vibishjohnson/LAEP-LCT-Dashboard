"""
Read-only tests against the real DfT publications downloaded on this machine (skipped elsewhere).

These pin the parser to the actual published structure (sheet names, title rows, header row,
row counts, Units) and measure time and memory. The real files are only read; uploads are
streamed from them into disposable registries.
"""

import csv
import os
import threading
import time
from dataclasses import replace

import pytest

from data_manager.ingestion import contracts, readers
from data_manager.ingestion.config import Settings
from data_manager.ingestion.procmem import self_memory_mb

from .conftest import EV, make_live, real, read_csv_rows

S = Settings()
BENCH = []


@pytest.fixture
def cmp_settings(settings):
    return replace(settings, compare_with_production=True)


class ServerMemory:
    """Samples this process's working set (the Flask server runs in-process) while a block runs."""

    def __enter__(self):
        self.base = self_memory_mb()[0]
        self.peak, self.stop = self.base, False
        self.t = threading.Thread(target=self._run, daemon=True)
        self.t.start()
        return self

    def _run(self):
        while not self.stop:
            self.peak = max(self.peak, self_memory_mb()[0])
            time.sleep(0.02)

    def __exit__(self, *a):
        self.stop = True
        self.t.join()

    @property
    def delta(self):
        return self.peak - self.base


def upload_and_wait(srv, path, name=None):
    with ServerMemory() as m:
        t0 = time.time()
        s, d = srv.upload(path, name)
        t_up = time.time() - t0
    assert s == 202, d
    job = srv.wait(d["job_id"], timeout=1200)
    srv.state.runner.wait_idle(120)              # the dispatcher records the worker's peak after the job finishes
    bench = {"file": os.path.basename(path), "bytes": os.path.getsize(path), "upload_s": round(t_up, 1),
             "server_ws_delta_mb": m.delta, "worker": srv.state.runner.last_run,
             "worker_self": (job.get("result") or {}).get("memory_mb"), "timings": (job.get("result") or {}).get("timings_s")}
    BENCH.append(bench)
    print("\nBENCH", bench)
    return job, bench


def test_real_veh0142_ods_2026q2(cmp_settings):
    p = real("veh0142_2026q2_ods")
    srv = make_live(cmp_settings)
    try:
        job, b = upload_and_wait(srv, p)
    finally:
        srv.close()
    r = job["result"]
    assert job["state"] == "READY", job.get("error")
    assert (r["dataset"], r["format"], r["sheet"]) == ("VEH0142", "ods", "VEH0142")
    assert r["sheets"] == ["Cover_sheet", "Contents", "Notes", "VEH0142"]
    p_ = r["profile"]
    assert p_["latest_quarter"] == "2026 Q2" and p_["header_row"] == 5 and p_["rows"] == 26156 and p_["columns"] == 74
    assert p_["title_rows"][1] == "Table VEH0142" and p_["categories"]["Units"] == {"Number": 26156}
    cmp = r["comparison"]
    assert cmp["quarter_relation"] == "NEWER" and cmp["production_latest"] == "2026 Q1"
    uk_bev = next(s for s in cmp["series"] if s["series"] == "UK|Total|Battery electric")
    assert uk_bev["candidate_latest"] > uk_bev["candidate_at_production_latest"] > 0
    assert b["worker"]["peak_commit_mb"] < 400 and b["server_ws_delta_mb"] < 100


def test_real_veh0105_ods_2026q2_largest_xml(cmp_settings):
    p = real("veh0105_2026q2_ods")
    srv = make_live(cmp_settings)
    try:
        job, b = upload_and_wait(srv, p)
    finally:
        srv.close()
    r = job["result"]
    assert job["state"] == "READY", job.get("error")
    assert (r["dataset"], r["sheet"]) == ("VEH0105", "VEH0105")
    assert r["profile"]["latest_quarter"] == "2026 Q2" and r["profile"]["rows"] == 40759
    assert r["profile"]["categories"]["Units"] == {"Thousands": 40759}
    assert b["worker"]["peak_commit_mb"] < 400


def test_real_ods_2026q1_reproduces_production_csv_exports(tmp_path):
    """The streaming ODS parse of the 2026 Q1 releases equals the production CSV exports of the same releases."""
    for key, table in (("veh0142_2026q1_ods", "VEH0142"), ("veh0105_2026q1_ods", "VEH0105")):
        p = real(key)
        canon = str(tmp_path / f"{table}.csv")
        ck, prof, _, _ = contracts.validate_and_profile(readers.iter_rows(p, "ods", table, S), contracts.CONTRACTS[table],
                                                        contracts.UkpnScope(None), canon)
        assert not ck.errors and prof["latest_quarter"] == "2026 Q1"
        a, b = read_csv_rows(canon), read_csv_rows(os.path.join(EV, f"{table}.csv"))
        assert a[0] == b[0] and len(a) == len(b)
        rounded = 0
        for x, y in zip(a[1:], b[1:]):
            assert x[:7] == y[:7]
            for u, v in zip(x[7:], y[7:]):
                v = v.replace(",", "")
                if u == v or (u not in contracts.MARKERS and float(u) == float(v)):
                    continue          # '0.1' vs '0.10' is formatting only
                # production VEH0105.csv holds the 2-decimal display value of the stored ODS number
                assert table == "VEH0105" and abs(float(u) - float(v)) <= 0.005 + 1e-9, (u, v)
                rounded += 1
        if table == "VEH0142":
            assert rounded == 0
        else:
            assert rounded == 11224


def test_real_veh0135_2026q2_csv_against_production(cmp_settings):
    p = real("veh0135_2026q2_csv")
    srv = make_live(cmp_settings)
    try:
        job, b = upload_and_wait(srv, p)
    finally:
        srv.close()
    r = job["result"]
    assert job["state"] == "READY", job.get("error")
    pr = r["profile"]
    assert r["dataset"] == "VEH0135" and pr["latest_quarter"] == "2026 Q2" and pr["rows"] == 286113
    sc = pr["scope"]
    assert sc["total_rows"] == sc["ukpn_rows"] + sc["non_ukpn_rows"] == 286113
    cmp = r["comparison"]
    assert cmp["quarter_relation"] == "NEWER" and cmp["rows"]["production"] == 293825
    assert "Fuel" in cmp["category_changes"] and "Hybrid electric (petrol)" in cmp["category_changes"]["Fuel"]
    ukpn = {s["series"]: s for s in cmp["series"]}
    for dno in ("EPN", "LPN", "SPN"):
        s = ukpn[f"{dno}|BEV_Total"]
        assert s["candidate_latest"] > s["candidate_at_production_latest"] > 0
    assert b["server_ws_delta_mb"] < 100


def test_real_same_quarter_redownload_is_a_conflict(cmp_settings):
    """DfT's own 2026 Q1 file differs from production only by a BOM and one re-encoded Scottish LSOA name."""
    p = real("veh0135_2026q1_dl_csv")
    srv = make_live(cmp_settings)
    try:
        job, _ = upload_and_wait(srv, p)
        r = job["result"]
        assert job["state"] == "READY" and r["comparison"]["quarter_relation"] == "SAME"
        assert all(s["restatement_pct"] in (0.0, None) for s in r["comparison"]["series"])
        s, d = srv.post(f"/api/ingest/jobs/{job['id']}/stage", {"dataset": "VEH0135", "period": "2026 Q1"})
        assert s == 409 and "conflicting reporting period" in d["error"]
    finally:
        srv.close()


def test_real_veh0125_largest_csv_streams(settings):
    p = real("veh0125_2026q2_csv")
    srv = make_live(replace(settings, worker_timeout_seconds=1800))
    try:
        job, b = upload_and_wait(srv, p)
    finally:
        srv.close()
    assert job["state"] == "READY", job.get("error")
    assert job["result"]["dataset"] == "VEH0125" and job["result"]["profile"]["latest_quarter"] == "2026 Q2"
    assert b["server_ws_delta_mb"] < 100                      # 245 MB streamed to disk, not held in memory
    assert b["worker"]["peak_commit_mb"] < 400


def test_real_xlsx_workbook(settings):
    p = real("veh0142_xlsx")
    sheets = readers.sheet_names(p, "xlsx", S)
    heads = {}
    for s in sheets:
        it = readers.iter_rows(p, "xlsx", s, S)
        heads[s] = [r for _, r in zip(range(40), it)]
        it.close()
    best = contracts.identify(heads, os.path.basename(p))[0]
    assert (best["dataset"], best["sheet"], best["header_row"]) == ("VEH0142", "VEH0142", 4)
    ck, prof, prev, _ = contracts.validate_and_profile(readers.iter_rows(p, "xlsx", best["sheet"], S),
                                                       contracts.CONTRACTS["VEH0142"], contracts.UkpnScope(None))
    assert prof["latest_quarter"] == "2025 Q2" and prof["rows"] > 20000
    # This 2025 Q2 workbook repeats 819 'Other vehicles' keys with different values: ambiguous, so it is blocked.
    assert [e["check"] for e in ck.errors] == ["unique_keys"] and "819 duplicate keys" in ck.errors[0]["detail"]

def test_pandas_odf_engine_cannot_open_real_veh0142_under_1_4_gb(tmp_path):
    """Documents the root cause: pandas/odfpy builds a full DOM. Run in a capped child process."""
    import subprocess
    import sys
    from data_manager.ingestion.procmem import MemoryCap
    p = real("veh0142_2026q2_ods")
    cap = MemoryCap(1400)
    proc = subprocess.Popen([sys.executable, "-c", "import sys,pandas as pd; pd.read_excel(sys.argv[1], engine='odf', sheet_name='VEH0142')", p],
                            stdout=subprocess.PIPE, stderr=subprocess.STDOUT, env={**os.environ, "OPENBLAS_NUM_THREADS": "1"})
    cap.assign(proc)
    out, _ = proc.communicate(timeout=900)
    peak = cap.peak_mb()
    cap.close()
    if os.name == "nt":
        assert proc.returncode != 0 and b"MemoryError" in out, out[-500:]
        assert peak >= 1300
