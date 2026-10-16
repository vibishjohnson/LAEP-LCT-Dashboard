"""End-to-end ingestion over real HTTP: streaming upload, background worker, review, staging, history."""

import hashlib
import json
import os
import shutil
import socket
import stat
import threading
import time
from dataclasses import replace

import pytest

from data_manager.ingestion import contracts
from data_manager.ingestion.jobs import JobRunner, QueueFull
from data_manager.ingestion.registry import Registry

from .conftest import EV, dft_workbook, make_live, read_csv_rows, write_ods

PROTECTED = [os.path.join(EV, f) for f in sorted(os.listdir(EV))] + [
    os.path.join(os.path.dirname(EV), "project", "output_processed", "integration", n)
    for n in ("ev_actuals_dno.csv", "ev_actuals_la.csv", "ev_actuals_laep.csv")]


def sha(p):
    h = hashlib.sha256()
    with open(p, "rb") as f:
        for c in iter(lambda: f.read(1 << 20), b""):
            h.update(c)
    return h.hexdigest()


@pytest.fixture(scope="module", autouse=True)
def production_untouched():
    before = {p: (sha(p), os.path.getmtime(p)) for p in PROTECTED if os.path.isfile(p)}
    yield
    after = {p: (sha(p), os.path.getmtime(p)) for p in PROTECTED if os.path.isfile(p)}
    assert after == before, "production inputs or EV actuals changed during ingestion tests"


def next_quarter_workbook(table, csv_path, factor=1.02):
    """The production table with a new latest quarter column (DfT layout: newest first)."""
    wb = dft_workbook(table, csv_path)
    rows = wb[table]
    hdr_i = 4
    hdr = rows[hdr_i]
    latest = hdr[7]
    y, q = int(latest[:4]), int(latest[-1])
    new = f"{y + (q == 4)} Q{q % 4 + 1}"
    rows[hdr_i] = hdr[:7] + [new] + hdr[7:]
    for i in range(hdr_i + 1, len(rows)):
        r = rows[i]
        v = r[7].replace(",", "")
        nv = str(round(float(v) * factor, 3 if "." in v else None)) if v.replace(".", "", 1).isdigit() else r[7]
        rows[i] = r[:7] + [nv] + r[7:]
    return wb, new


@pytest.fixture(scope="module")
def q2_ods(tmp_path_factory):
    d = tmp_path_factory.mktemp("q2")
    wb, new = next_quarter_workbook("VEH0142", os.path.join(EV, "VEH0142.csv"))
    assert new == "2026 Q2"
    return write_ods(str(d / "veh0142 (q2).ods"), wb, numeric_from=7)


@pytest.fixture
def csv142(tmp_path):
    p = tmp_path / "VEH0142.csv"
    shutil.copyfile(os.path.join(EV, "VEH0142.csv"), p)
    return str(p)


def test_small_csv_upload_validates_and_stages_immutably(live, csv142):
    st, d = live.upload(csv142, "veh0142 export.csv")
    assert st == 202 and d["sha256"] == sha(csv142)
    job = live.wait(d["job_id"])
    assert job["state"] == "READY", job
    res = job["result"]
    assert res["dataset"] == "VEH0142" and res["format"] == "csv" and res["profile"]["latest_quarter"] == "2026 Q1"
    assert res["profile"]["rows"] == 25996 and res["status"] == "VALID"
    assert res["preview"]["header"][:7] == list(contracts.CONTRACTS["VEH0142"].id_columns) and len(res["preview"]["rows"]) == 20
    s, v = live.post(f"/api/ingest/jobs/{d['job_id']}/stage", {"dataset": "VEH0142", "period": "2026 Q1"})
    assert s == 200 and v["ok"], v
    ver = v["version"]
    assert ver["label"] == "VEH0142-2026Q1-v1" and ver["status"] == "STAGED" and ver["sha256"] == sha(csv142)
    reg = live.state.registry
    vdir = reg.path(ver["path"])
    assert sha(os.path.join(vdir, "original.csv")) == sha(csv142)                     # original bytes preserved
    m = json.load(open(os.path.join(vdir, "manifest.json"), encoding="utf-8"))
    assert m["files"]["original.csv"]["sha256"] == sha(csv142) and "not approved" in m["status"]
    assert all(not os.stat(os.path.join(vdir, n)).st_mode & stat.S_IWRITE for n in os.listdir(vdir))   # read-only
    assert reg.verify_version(ver["id"])
    assert not os.path.exists(reg.upload_path(d["job_id"]))                           # incoming cleaned up
    # duplicate of a stored version is refused at upload time
    st2, d2 = live.upload(csv142, "copy.csv")
    assert st2 == 409 and "VEH0142-2026Q1-v1" in d2["error"]


def test_ods_versions_supersede_and_restatements_need_a_reason(live, veh0142_ods, q2_ods, tmp_path):
    s1, a = live.upload(veh0142_ods)
    j1 = live.wait(a["job_id"])
    assert j1["state"] == "READY" and j1["result"]["sheet"] == "VEH0142"
    assert live.post(f"/api/ingest/jobs/{a['job_id']}/stage", {"dataset": "VEH0142", "period": "2026 Q1"})[0] == 200
    s2, b = live.upload(q2_ods)
    j2 = live.wait(b["job_id"])
    assert j2["result"]["profile"]["latest_quarter"] == "2026 Q2"
    # confirmation must match what was identified
    assert live.post(f"/api/ingest/jobs/{b['job_id']}/stage", {"dataset": "VEH0105", "period": "2026 Q2"})[0] == 409
    st, v2 = live.post(f"/api/ingest/jobs/{b['job_id']}/stage", {"dataset": "VEH0142", "period": "2026 Q2"})
    assert st == 200
    reg = live.state.registry
    v1 = reg.versions("VEH0142")[-1]
    assert v2["version"]["supersedes_version_id"] == v1["id"]
    assert reg.version(v1["id"])["status"] == "STAGED"                                  # never overwritten
    # an older quarter cannot be staged after a newer one
    older = tmp_path / "older.ods"
    wb = dft_workbook("VEH0142", os.path.join(EV, "VEH0142.csv"))
    wb["VEH0142"][5][7] = str(int(wb["VEH0142"][5][7].replace(",", "")) + 1)          # different bytes, same 2026 Q1
    write_ods(str(older), wb, numeric_from=7)
    s3, c = live.upload(str(older))
    live.wait(c["job_id"])
    st3, r3 = live.post(f"/api/ingest/jobs/{c['job_id']}/stage", {"dataset": "VEH0142", "period": "2026 Q1"})
    assert st3 == 409 and "older reporting period" in r3["error"]
    # a revision of the newest quarter needs an explicit restatement reason
    wb2, _ = next_quarter_workbook("VEH0142", os.path.join(EV, "VEH0142.csv"), factor=1.03)
    rev = tmp_path / "rev.ods"
    write_ods(str(rev), wb2, numeric_from=7)
    s4, e = live.upload(str(rev))
    live.wait(e["job_id"])
    st4, r4 = live.post(f"/api/ingest/jobs/{e['job_id']}/stage", {"dataset": "VEH0142", "period": "2026 Q2"})
    assert st4 == 409 and "conflicting reporting period" in r4["error"]
    st5, r5 = live.post(f"/api/ingest/jobs/{e['job_id']}/stage",
                        {"dataset": "VEH0142", "period": "2026 Q2", "restatement_reason": "DfT revised 2026 Q2 on publication day"})
    assert st5 == 200 and r5["version"]["restates_version_id"] == v2["version"]["id"]
    assert r5["version"]["label"] == "VEH0142-2026Q2-v2"
    labels = [v["label"] for v in reg.versions("VEH0142")]
    assert labels == ["VEH0142-2026Q2-v2", "VEH0142-2026Q2-v1", "VEH0142-2026Q1-v1"]
    assert all(reg.verify_version(v["id"]) for v in reg.versions("VEH0142"))
    # history pages
    s, html = live.request("GET", "/dft-sources")
    assert s == 200 and "VEH0142-2026Q2-v2" in html and "DfT revised 2026 Q2" in html
    s, html = live.request("GET", f"/dft-sources/{r5['version']['id']}")
    assert s == 200 and "Stored files match the manifest" in html


def test_wrong_identification_can_be_rejected_and_revalidated(live, veh0105_ods):
    s, d = live.upload(veh0105_ods, "VEH0142 (looks like).ods")
    job = live.wait(d["job_id"])
    assert job["result"]["dataset"] == "VEH0105"                                       # content wins over the name
    st, _ = live.post(f"/api/ingest/jobs/{d['job_id']}/revalidate", {"dataset": "VEH0142"})
    assert st == 202
    job = live.wait(d["job_id"])
    assert job["state"] == "FAILED" and job["stage"] == "validate" and "units" in job["error"]
    assert job["result"]["identification"]["method"] == "reviewer override"
    assert live.post(f"/api/ingest/jobs/{d['job_id']}/stage", {"dataset": "VEH0142", "period": "2026 Q1"})[0] == 409
    st, _ = live.post(f"/api/ingest/jobs/{d['job_id']}/revalidate", {"dataset": "VEH0105", "sheet": "VEH0105"})
    job = live.wait(d["job_id"])
    assert job["state"] == "READY" and job["result"]["dataset"] == "VEH0105"
    st, _ = live.post(f"/api/ingest/jobs/{d['job_id']}/reject", {"reason": "not needed"})
    assert st == 200 and live.state.registry.job(d["job_id"])["state"] == "REJECTED"
    assert not os.path.exists(live.state.registry.upload_path(d["job_id"]))


def test_oversized_and_malformed_requests(settings, tmp_path):
    srv = make_live(replace(settings, max_upload_bytes=1024 * 1024))
    try:
        big = tmp_path / "big.csv"
        big.write_bytes(b"A,B\n" + b"1,2\n" * 400_000)
        s, d = srv.post("/api/ingest/precheck", {"filename": "big.csv", "size": os.path.getsize(big)})
        assert s == 413 and "limit is 1 MB" in d["error"]
        c = srv.conn()                                                          # PUT declaring 1.6 MB: refused before reading
        c.putrequest("PUT", "/api/ingest/upload?filename=big.csv")
        c.putheader("Content-Length", str(os.path.getsize(big)))
        c.endheaders()
        r = c.getresponse()
        assert r.status == 413 and "limit" in json.loads(r.read())["error"]
        c.close()
        s, d = srv.request("PUT", "/api/ingest/upload?filename=x.txt", b"abc", {"Content-Length": "3"})
        assert s == 400 and "unsupported file type" in d["error"]
        s, d = srv.request("PUT", "/api/ingest/upload?filename=x.csv", b"", {"Content-Length": "0"})
        assert s == 400
        c = srv.conn()                                                          # chunked body: no Content-Length
        c.putrequest("PUT", "/api/ingest/upload?filename=x.csv")
        c.putheader("Transfer-Encoding", "chunked")
        c.endheaders()
        c.send(b"3\r\nabc\r\n0\r\n\r\n")
        assert c.getresponse().status == 411
        c.close()
        s, d = srv.request("POST", "/api/ingest/jobs/x/reject", "reason=1", {"Content-Type": "application/x-www-form-urlencoded"})
        assert s == 415
        assert srv.state.registry.jobs() == []                                     # nothing was created
        assert os.listdir(srv.state.registry.path("incoming")) == []
    finally:
        srv.close()


def test_interrupted_upload_leaves_nothing_behind(live, csv142):
    size = os.path.getsize(csv142)
    sock = socket.create_connection(("127.0.0.1", live.port))
    sock.sendall(f"PUT /api/ingest/upload?filename=VEH0142.csv HTTP/1.1\r\nHost: x\r\nContent-Length: {size}\r\n"
                 f"Content-Type: application/octet-stream\r\n\r\n".encode())
    with open(csv142, "rb") as f:
        sock.sendall(f.read(size // 3))
    time.sleep(0.5)
    sock.close()                                                              # browser tab closed mid-upload
    reg = live.state.registry
    for _ in range(100):
        jobs = reg.jobs()
        if jobs and jobs[0]["state"] == "FAILED":
            break
        time.sleep(0.1)
    job = reg.jobs()[0]
    assert job["state"] == "FAILED" and job["stage"] == "upload" and "interrupted" in job["error"]
    assert os.listdir(reg.path("incoming")) == []
    # a fresh upload of the same file afterwards works
    s, d = live.upload(csv142)
    assert s == 202 and live.wait(d["job_id"])["state"] == "READY"


def test_concurrent_identical_uploads_accept_exactly_one(live, csv142):
    results = []

    def go():
        results.append(live.upload(csv142, chunk=64 * 1024))
    ts = [threading.Thread(target=go) for _ in range(2)]
    [t.start() for t in ts]
    [t.join() for t in ts]
    codes = sorted(s for s, _ in results)
    assert codes == [202, 409], results
    ok = next(d for s, d in results if s == 202)
    assert live.wait(ok["job_id"])["state"] == "READY"


def test_concurrent_upload_limit(settings, csv142, veh0142_ods):
    srv = make_live(replace(settings, max_concurrent_uploads=1))
    try:
        size = os.path.getsize(csv142)
        sock = socket.create_connection(("127.0.0.1", srv.port))
        sock.sendall(f"PUT /api/ingest/upload?filename=a.csv HTTP/1.1\r\nHost: x\r\nContent-Length: {size}\r\n\r\n".encode())
        sock.sendall(b"Units," * 10)                                              # first upload holds the slot
        time.sleep(0.5)
        s, d = srv.post("/api/ingest/precheck", {"filename": "veh0142.ods", "size": os.path.getsize(veh0142_ods)})
        assert s == 429 and "in progress" in d["error"]                           # the page is told before sending
        s, d = srv.request("PUT", "/api/ingest/upload?filename=b.csv", b"A,B\n1,2\n", {"Content-Length": "8"})
        assert s == 429 and "in progress" in d["error"]                           # and the PUT itself enforces it
        sock.close()
        time.sleep(1.0)
        s, d = srv.upload(veh0142_ods)
        assert s == 202 and srv.wait(d["job_id"])["state"] == "READY"
    finally:
        srv.close()


def test_queue_is_bounded(settings):
    reg = Registry(settings)
    reg.ensure()
    runner = JobRunner(replace(settings, max_queued_jobs=1))                       # dispatcher not started
    a, b = reg.create_job("a.csv", 1), reg.create_job("b.csv", 1)
    runner.submit(a)
    with pytest.raises(QueueFull):
        runner.submit(b)
    assert reg.job(b)["state"] == "FAILED" and "waiting" in reg.job(b)["error"]


def test_resubmission_racing_the_dispatcher_is_not_marked_failed(settings, csv142):
    """Reviewer re-validates the instant a job finishes, before the dispatcher's post-run check."""
    from .conftest import overrides_of
    reg = Registry(settings)
    reg.ensure()
    job = reg.create_job("VEH0142.csv", os.path.getsize(csv142))
    shutil.copyfile(csv142, reg.upload_path(job))
    reg.update_job(job, sha256=sha(csv142), received_bytes=os.path.getsize(csv142), state="QUEUED")
    runner = JobRunner(settings, overrides_of(settings))
    original, calls = runner.registry.job, []

    def job_lookup(job_id):
        calls.append(job_id)
        if len(calls) == 2:                                   # the post-run check: re-queue first, as the UI would
            runner.registry.update_job(job_id, state="QUEUED", stage="queued", dataset_override="VEH0142")
        return original(job_id)
    runner.registry.job = job_lookup
    runner.run_one(job)
    j = original(job)
    assert j["state"] == "QUEUED" and j["error"] is None, j      # left for the next run, not marked FAILED
    assert j["attempts"] == 1


def test_parser_failure_is_recorded_and_recoverable(live, tmp_path):
    good = open(os.path.join(EV, "VEH0142.csv"), encoding="utf-8").read().splitlines()
    rows = "".join(f"<table:table-row><table:table-cell office:value-type=\"string\"><text:p>{l[:30]}</text:p></table:table-cell></table:table-row>" for l in good[:200])
    content = ('<?xml version="1.0"?><office:document-content xmlns:office="urn:oasis:names:tc:opendocument:xmlns:office:1.0" '
               'xmlns:table="urn:oasis:names:tc:opendocument:xmlns:table:1.0" xmlns:text="urn:oasis:names:tc:opendocument:xmlns:text:1.0">'
               '<office:body><office:spreadsheet><table:table table:name="VEH0142">' + rows + "<table:table-row><broken")
    p = write_ods(str(tmp_path / "broken.ods"), {}, content_override=content.encode())
    s, d = live.upload(p)
    job = live.wait(d["job_id"])
    assert job["state"] == "FAILED" and job["result"]["status"] == "ERROR" and job["upload_present"]
    assert live.post(f"/api/ingest/jobs/{d['job_id']}/retry", {})[0] == 202                # retry is possible
    assert live.wait(d["job_id"])["state"] == "FAILED"
    assert live.post(f"/api/ingest/jobs/{d['job_id']}/reject", {"reason": "corrupt download"})[0] == 200
    kept = live.state.registry.path("failed", d["job_id"])
    assert os.path.exists(os.path.join(kept, "original.bin"))                               # kept for diagnosis
    assert json.load(open(os.path.join(kept, "error.json")))["original_kept"] is True


def test_malicious_archive_upload_is_deleted(live, tmp_path):
    import zipfile
    p = str(tmp_path / "bomb.ods")
    with zipfile.ZipFile(p, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr(zipfile.ZipInfo("mimetype"), "application/vnd.oasis.opendocument.spreadsheet")
        z.writestr("content.xml", b"<a>" + b" " * (100 * 2**20) + b"</a>")
    s, d = live.upload(p)
    job = live.wait(d["job_id"])
    assert job["state"] == "FAILED" and job["result"]["status"] == "REJECTED_UNSAFE" and "compression ratio" in job["error"]
    assert not job["upload_present"]
    kept = live.state.registry.path("failed", d["job_id"])
    assert os.listdir(kept) == ["error.json"]                                               # unsafe bytes are not kept


def test_worker_memory_cap_and_timeout_fail_safely(settings, veh0142_ods):
    srv = make_live(replace(settings, worker_memory_limit_mb=30))
    try:
        s, d = srv.upload(veh0142_ods)
        job = srv.wait(d["job_id"])
        assert job["state"] == "FAILED" and ("memory" in job["error"] or "exited" in job["error"]), job["error"]
        assert srv.request("GET", "/dft-ingest")[0] == 200                                   # the app is unaffected
    finally:
        srv.close()
    srv = make_live(replace(settings, worker_timeout_seconds=2))
    try:
        s, d = srv.upload(veh0142_ods)
        job = srv.wait(d["job_id"])
        assert job["state"] == "FAILED" and "time limit" in job["error"]
    finally:
        srv.close()


def test_restart_recovery(settings, csv142):
    reg = Registry(settings)
    reg.ensure()
    a = reg.create_job("a.csv", 10)
    open(reg.upload_path(a, part=True), "wb").write(b"x")
    b = reg.create_job("b.csv", 10)
    shutil.copyfile(csv142, reg.upload_path(b))
    reg.update_job(b, state="PARSING", sha256=sha(csv142), received_bytes=os.path.getsize(csv142))
    srv = make_live(settings)                                                   # a new process starts
    try:
        srv.request("GET", "/dft-ingest")
        ja, jb = reg.job(a), reg.job(b)
        assert ja["state"] == "FAILED" and "upload interrupted" in ja["error"] and not os.path.exists(reg.upload_path(a, part=True))
        assert jb["state"] == "FAILED" and "Retry" in jb["error"]
        assert srv.post(f"/api/ingest/jobs/{b}/retry", {})[0] == 202
        assert srv.wait(b)["state"] == "READY"
    finally:
        srv.close()


def test_pages_render(live):
    for path in ("/dft-ingest", "/dft-sources"):
        s, html = live.request("GET", path)
        assert s == 200 and "LCT Data Manager" in html and "DFT INGEST" in html
    s, html = live.request("GET", "/dft-ingest")
    assert "ev/df_VEH0135.csv" in html and "2026 Q1" in html and 'accept=".csv,.xlsx,.ods"' in html
