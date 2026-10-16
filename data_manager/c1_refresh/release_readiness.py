"""
Release readiness for an approved C1 candidate: sealed release package, disposable dry run, dashboard acceptance.

Nothing here can touch production. The dry run builds a disposable environment outside the repository from the
current live files plus the sealed October release record, and runs the C1 -> C1 release tool against it with a
DRY_RUN authorisation (which the tool refuses on the live repository). Production publication needs a separate
PRODUCTION authorisation, LIVE_RELEASE_ENABLED and an explicit confirmation; this module has no code path for it.
"""

import json
import os
import shutil
import subprocess
import tempfile
import time
import uuid

import pandas as pd

from . import config as C
from . import sources as S, store

import c1_quarterly_release as cq      # noqa: E402 (release directory is on sys.path via config)
import c1_release as rel               # noqa: E402
import disposable_env as denv          # noqa: E402
import release_config as rcfg          # noqa: E402

HARNESS = os.path.join(C.C1_DIR, "dashboard_regression", "dashboard_harness.js")
MODES = ("ev", "ev_local", "nonev", "path")
NETWORK_AREAS = ["UKPN Total", "EPN", "LPN", "SPN"]
LA_CODES = {"Peterborough": "E06000031", "Westminster": "E09000033"}
LAEPS = ("Peterborough", "Norwich", "Westminster", "Outer East", "West London")
SUBTYPES = ("Total", "BEV", "PHEV")
OS, OC, OQ = "Estimated EV stock", "Estimated cumulative net stock additions", "Estimated quarterly net stock change"
CHARTS = ("stockChart", "evCumulativeChart", "monthlyChart")
FORECASTS = ("DFES", "UKPN — Holistic Transition before tRESP", "UKPN — Holistic Transition Basic before tRESP")


def package_dir(ws, cid):
    return ws.path("release_packages", f"{cid}-B")


def ensure_package(ws, cid):
    d = package_dir(ws, cid)
    if not os.path.exists(os.path.join(d, "package_manifest.json")):
        os.makedirs(os.path.dirname(d), exist_ok=True)
        cq.prepare_package(ws.path("candidates", cid), d)
    return cq.load_package(d)


def build_chain_env(dest, template=None):
    """Disposable copy of the live state: current production files plus the sealed October release record."""
    env, _ = denv.build(dest, template, baseline="live")
    for sub in ("releases", "backups"):
        src = os.path.join(rcfg.REPO, *rcfg.STATE_REL.split("/"), sub, C.LIVE_RELEASE_ID)
        dst = os.path.join(str(env), *rcfg.STATE_REL.split("/"), sub, C.LIVE_RELEASE_ID)
        os.makedirs(dst, exist_ok=True)
        for n in os.listdir(src):
            shutil.copyfile(os.path.join(src, n), os.path.join(dst, n))
    pins = os.path.join(rcfg.REPO, *rcfg.STATE_REL.split("/"), "pins")
    if os.path.isdir(pins):
        shutil.copytree(pins, os.path.join(str(env), *rcfg.STATE_REL.split("/"), "pins"))
    return env


def remove_env(path):
    shutil.rmtree(path, onerror=lambda f, p, _: (os.chmod(p, 0o666), f(p)))


def write_authorisation(path, pm, release_id, kind="DRY_RUN", approver="dry-run (no production authority)",
                        reference="DRY-RUN", **overrides):
    a = {"schema": cq.AUTH_SCHEMA, "kind": kind, "release_id": release_id, "package_id": pm["package_id"],
         "package_seal_sha256": pm["seal_sha256"], "candidate_id": pm["candidate_id"], "source_set_id": pm["source_set_id"],
         "option": "B", "restatement_report_sha256": pm["restatement_report_sha256"], "decision_record_sha256": pm["decision_record_sha256"],
         "previous_release_id": pm["previous_release_id"], "release_approver": approver, "approval_reference": reference,
         "approved_at": S.now(), "acknowledged_revised_history": True, "organisational_approvals": [],
         "outstanding_governance": ["named independent approvers", "release ticket", "separate production release authorisation"]}
    a.update(overrides)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(a, f, indent=2)
    return path


# ------------------------------------------------------------------ dashboard acceptance
def harness(mode, cwd):
    out = subprocess.run(["node", "--no-opt", "--no-sparkplug", HARNESS, mode], cwd=cwd, capture_output=True, timeout=900)
    data = json.loads(out.stdout.decode("utf-8"))
    if "fatal" in data:
        raise RuntimeError(f"harness fatal: {data['fatal']}")
    return data


def _by_label(data):
    return {r["label"]: r for r in data["results"]}


def _traces(r, chart):
    return {t["name"]: t for t in r["plots"][chart]["traces"]}


def _kpi(r, name):
    card = next(k for k in r["kpis"] if f"|{name}|" in k)
    _, _, value, unit, _ = card.split("|")
    return int(value.replace(",", "")), unit


def _forecast_extends(new, old, last_period):
    """The page draws forecasts over the actuals window: the promoted trace must be the live trace exactly, plus the
    new quarter's months, ending at the new latest actual. Absent in both is fine."""
    if new is None or old is None:
        return new is None and old is None
    nx, ox = new.get("x") or [], old.get("x") or []
    return (nx[:len(ox)] == ox and (new.get("y") or [])[:len(ox)] == (old.get("y") or [])
            and len(nx) > len(ox) and nx[-1] == last_period + "-01"
            and {k: v for k, v in new.items() if k not in ("x", "y")} == {k: v for k, v in old.items() if k not in ("x", "y")})


def _caveats_norm(caveats, last_period):
    return [c.replace(f"({last_period})", "(<latest actual>)") for c in caveats]


def dashboard_acceptance(env_root, files, periods, live_runs=None):
    """Run the real page against the promoted environment; expected values derived independently from the files."""
    live_last = json.load(open(os.path.join(rcfg.REPO, rcfg.INTEGRATION_REL, "metadata.json"), encoding="utf-8"))["latest_actual_period"]
    runs = {m: harness(m, env_root) for m in MODES}
    live_runs = live_runs or {m: harness(m, rcfg.REPO) for m in MODES}
    dno, la, laep = (pd.read_csv(os.path.join(env_root, rcfg.INTEGRATION_REL, n)) for n in rcfg.TARGET_NAMES)
    fc = pd.read_csv(os.path.join(env_root, rcfg.INTEGRATION_REL, "monthly_forecast_dno.csv"))
    dfes = fc[(fc.forecast_source == "DFES") & (fc.period == "2025-03")]
    dates = [p + "-01" for p in periods]
    exp = {}
    for sub in SUBTYPES:
        fuels = ["BEV", "PHEV"] if sub == "Total" else [sub]
        for area in NETWORK_AREAS:
            d, f = dno[dno.EV_Type.isin(fuels)], dfes[dfes.EV_Type.isin(fuels)]
            if area != "UKPN Total":
                d, f = d[d.DNO == area], f[f.DNO == area]
            s = d.groupby("period").install_count.sum()
            exp[f"Network|{area}|{sub}"] = dict(actual=[int(s[p]) for p in periods], opening=float(f.value.sum()))
        for name, code in LA_CODES.items():
            s = la[(la.LAD22CD == code) & la.EV_Type.isin(fuels)].groupby("period").value.sum()
            exp[f"LA|{name}|{sub}"] = dict(actual=[int(s[p]) for p in periods], opening=float(s["2025-03"]))
        for name in LAEPS:
            s = laep[(laep.LAEP == f"{name} LAEP") & laep.EV_Type.isin(fuels)].groupby("period").value.sum()
            exp[f"LAEP|{name} LAEP|{sub}"] = dict(actual=[int(s[p]) for p in periods], opening=float(s["2025-03"]))
    got = {**_by_label(runs["ev"]), **_by_label(runs["ev_local"])}
    live = {**_by_label(live_runs["ev"]), **_by_label(live_runs["ev_local"])}
    checks = []

    def add(name, ok, detail=""):
        checks.append({"check": name, "pass": bool(ok), "detail": detail})

    for m, data in runs.items():
        add(f"{m}: startup clean, no KPI or handler errors", data["startup"] == {"error": "", "errorShown": False}
            and not any(r["kpiError"] or r["handlerErrors"] for r in data["results"]), f"{len(data['results'])} views")
    bad_series, bad_kpi, bad_fc = [], [], []
    for label, e in exp.items():
        r = got[label]
        stock, cum, qtr = (_traces(r, c)[n] for c, n in zip(CHARTS, (OS, OC, OQ)))
        shown = ([e["opening"]] if label.startswith("Network") else [e["actual"][0]]) + e["actual"][1:]
        ok = (stock["x"] == dates and qtr["x"] == dates[1:] and all(abs(a - b) < 1e-6 for a, b in zip(stock["y"], shown))
              and cum["y"][0] == 0 and all(abs(a - (b - shown[0])) < 1e-6 for a, b in zip(cum["y"], shown))
              and all(abs(a - (y - x)) < 1e-6 for a, x, y in zip(qtr["y"], shown, shown[1:])))
        if not ok:
            bad_series.append(label)
        if _kpi(r, "Earliest Observation") != (e["actual"][0], f"vehicles ({periods[0]})") or \
                _kpi(r, "Latest Actual Stock") != (e["actual"][-1], f"vehicles ({periods[-1]})"):
            bad_kpi.append((label, _kpi(r, "Earliest Observation"), _kpi(r, "Latest Actual Stock")))
        lv = live[label]
        for chart in CHARTS:
            ct, lt = _traces(r, chart), _traces(lv, chart)
            if not all(_forecast_extends(ct.get(n), lt.get(n), periods[-1]) for n in FORECASTS) or \
                    r["plots"][chart]["layout"].get("title") != lv["plots"][chart]["layout"].get("title"):
                bad_fc.append((label, chart))
        if r["titles"] != lv["titles"] or _caveats_norm(r["caveats"], periods[-1]) != _caveats_norm(lv["caveats"], live_last):
            bad_fc.append((label, "titles/caveats"))
    add(f"{len(exp)} EV views: stock, quarterly change and cumulative net additions equal the promoted files for {len(periods)} quarters",
        not bad_series, bad_series[:5])
    add("Earliest Observation = 2025-03 and Latest Actual Stock = last quarter for every EV view", not bad_kpi, bad_kpi[:3])
    add("DFES/NESO forecast traces = live traces exactly, extended over the new quarter from the unchanged forecast files; "
        "titles identical; caveats differ only in the latest actual date", not bad_fc, bad_fc[:5])
    netw = exp["Network|UKPN Total|Total"]
    add("Network opens on the unchanged DFES March 2025 value; LA/LAEP open on C1 March 2025",
        abs(_traces(got["Network|UKPN Total|Total"], "stockChart")[OS]["y"][0] - netw["opening"]) < 1e-6
        and _traces(got["LA|Westminster|Total"], "stockChart")[OS]["y"][0] == exp["LA|Westminster|Total"]["actual"][0],
        f"Network opening {netw['opening']:.1f}")
    nonev, live_nonev = _by_label(runs["nonev"]), _by_label(live_runs["nonev"])
    diff = [l for l in nonev if nonev[l]["plots"] != live_nonev.get(l, {}).get("plots") or nonev[l]["kpis"] != live_nonev[l]["kpis"]]
    add(f"{len(nonev)} Heat Pump / Solar / EV Charger views identical to the live dashboard", not diff and len(nonev) == len(live_nonev), diff[:5])
    path_got, path_live = _by_label(runs["path"]), _by_label(live_runs["path"])
    add("pathway/scenario views: forecast traces = live, extended over the new quarter",
        set(path_got) == set(path_live) and all(_forecast_extends(_traces(path_got[l], c).get(n), _traces(path_live[l], c).get(n), periods[-1])
                                                for l in path_got for c in CHARTS if c in path_got[l]["plots"] for n in FORECASTS),
        f"{len(path_got)} views")
    kp = got["Network|UKPN Total|Total"]
    return {"pass": all(c["pass"] for c in checks), "checks": checks,
            "network_ukpn_kpis": {"earliest": _kpi(kp, "Earliest Observation"), "latest": _kpi(kp, "Latest Actual Stock")},
            "views": {m: len(d["results"]) for m, d in runs.items()}}


# ------------------------------------------------------------------ the dry run
def dry_run(ws, cid, with_dashboard=True, progress=print):
    st = store.state(ws, cid)
    if st["state"] != "APPROVED_FOR_FUTURE_RELEASE":
        raise ValueError(f"candidate is {st['state']}; a dry run needs a recorded option B approval")
    t0 = time.time()
    pm = ensure_package(ws, cid)
    base = C.short_tmp()
    env = os.path.join(base, f"c1q-dry-{uuid.uuid4().hex[:8]}")
    work = os.path.join(base, f"c1q-auth-{uuid.uuid4().hex[:8]}")
    os.makedirs(work)
    rid = f"c1-{time.strftime('%Y%m%d')}-dryrun-{pm['new_period'].replace('-', '')}"
    report = {"candidate_id": cid, "package_id": pm["package_id"], "package_seal": pm["seal_sha256"], "release_id": rid, "at": S.now(),
              "env": env, "steps": []}

    def step(name, ok, detail=None):
        report["steps"].append({"step": name, "pass": bool(ok), "detail": detail})
        progress(f"{'PASS' if ok else 'FAIL'} {name}")
        return ok

    live_before = {n: rel.sha256_file(os.path.join(rcfg.REPO, rcfg.INTEGRATION_REL, n)) for n in cq.ALL_TARGETS}
    try:
        build_chain_env(env)
        auth = write_authorisation(os.path.join(work, "authorisation.json"), pm, rid)
        s0 = cq.status(env)
        step("recognises the October release as current", s0["current_release"].get("release_id") == C.LIVE_RELEASE_ID, s0["current_release"])
        auth_a = write_authorisation(os.path.join(work, "authorisation_A.json"), pm, rid, option="A")
        pfa = cq.preflight(env, package_dir(ws, cid), auth_a, rid)
        step("refuses an option A authorisation for the option B package", not pfa["ok"] and
             any(c["check"] == "authorisation" and not c["pass"] for c in pfa["checks"]))
        pf = cq.preflight(env, package_dir(ws, cid), auth, rid)
        step("preflight passes (package, decision, authorisation, window, structure, restatements, metadata, pins)", pf["ok"],
             [f"{c['check']}: {c['detail']}" for c in pf["checks"]])
        if not pf["ok"]:
            return _save(ws, cid, report, t0)
        t1 = time.time()
        m = cq.promote(env, package_dir(ws, cid), auth, rid, rel.confirm_token("PROMOTE", rid, pf["digest"]))
        report["promote_seconds"] = round(time.time() - t1, 1)
        new = {n: rel.sha256_file(os.path.join(env, rcfg.INTEGRATION_REL, n)) for n in cq.ALL_TARGETS}
        step("promotion COMPLETE with backup of the October files", m["status"] == "COMPLETE" and m["backup"] is not None,
             {"backup": m["backup"], "restatement": m["restatement"]})
        step("published hashes = package option B hashes", all(new[n] == pm["expected_target_sha256"][n] for n in rcfg.TARGET_NAMES),
             {n: new[n][:16] for n in rcfg.TARGET_NAMES})
        step("forecasts and every non-target protected file unchanged", m["promotion_qa"]["protected_changed"] == [],
             f"{len(rel.protected_snapshot(env))} protected files")
        meta = json.load(open(os.path.join(env, rcfg.INTEGRATION_REL, "metadata.json"), encoding="utf-8"))
        step("Latest Actual advanced to the new quarter", meta["latest_actual_period"] == pm["new_period"], meta["latest_actual_period"])
        vc = cq.verify_current(env)
        step("new release is the current release (verify-current)", vc["ok"] and vc["current_release"]["release_id"] == rid, vc["problems"])
        pf_again = cq.preflight(env, package_dir(ws, cid), auth, rid + "x")
        step("refuses a second promotion from the now-stale starting state", not pf_again["ok"],
             [c["check"] for c in pf_again["checks"] if not c["pass"]])
        if with_dashboard:
            t2 = time.time()
            payload = {n: open(os.path.join(env, rcfg.INTEGRATION_REL, n), "rb").read() for n in rcfg.TARGET_NAMES}
            dash = dashboard_acceptance(env, payload, pm["periods"])
            report["dashboard"] = dash
            report["dashboard_seconds"] = round(time.time() - t2, 1)
            step("dashboard acceptance on the promoted six-quarter data", dash["pass"], [c for c in dash["checks"] if not c["pass"]])
        man = cq.load_release(env, rid)
        t3 = time.time()
        rb = cq.rollback(env, rid, rel.confirm_token("ROLLBACK", rid, man["manifest_sha256"]))
        report["rollback_seconds"] = round(time.time() - t3, 1)
        after = {n: rel.sha256_file(os.path.join(env, rcfg.INTEGRATION_REL, n)) for n in cq.ALL_TARGETS}
        step("rollback restores the exact October files and metadata", rb["status"] == "ROLLED_BACK" and after == live_before,
             {n: after[n][:16] for n in cq.ALL_TARGETS})
        vc2 = cq.verify_current(env)
        step("October is current again after rollback", vc2["ok"] and vc2["current_release"]["release_id"] == C.LIVE_RELEASE_ID, vc2["problems"])
        step("the October Method-A-to-C1 release cannot be rolled back by the quarterly tool",
             _refused(lambda: cq.rollback(env, C.LIVE_RELEASE_ID, "x"), rel.RollbackRefused))
        pins = rel.read_pin_log(env)
        step("metadata re-pins recorded and chained (release, rollback)", len(pins) == 2 and rel.effective_pins(env)[f"{rcfg.INTEGRATION_REL}/metadata.json"] == live_before["metadata.json"],
             [(p["seq"], p["reason"]) for p in pins])
    finally:
        live_after = {n: rel.sha256_file(os.path.join(rcfg.REPO, rcfg.INTEGRATION_REL, n)) for n in cq.ALL_TARGETS}
        step("real production files untouched during the dry run", live_after == live_before)
        remove_env(env)
        remove_env(work)
        report["env_removed"] = not os.path.exists(env)
    return _save(ws, cid, report, t0)


def _refused(fn, exc):
    try:
        fn()
    except exc:
        return True
    except Exception:  # noqa: BLE001
        return False
    return False


def _save(ws, cid, report, t0):
    from data_manager.ingestion.procmem import self_memory_mb
    report["seconds"] = round(time.time() - t0, 1)
    report["memory_mb"] = dict(zip(("working_set", "peak_working_set", "peak_commit"), self_memory_mb()))
    report["pass"] = all(s["pass"] for s in report["steps"])
    d = ws.path("release_dryruns", f"{cid}-B")
    os.makedirs(d, exist_ok=True)
    name = f"dryrun-{time.strftime('%Y%m%dT%H%M%S')}.json"
    for n in (name, "latest.json"):
        with open(os.path.join(d, n), "w", encoding="utf-8") as f:
            json.dump(report, f, indent=2, default=str)
    return report


def latest_dry_run(ws, cid):
    p = ws.path("release_dryruns", f"{cid}-B", "latest.json")
    return json.load(open(p, encoding="utf-8")) if os.path.exists(p) else None


def readiness(ws, cid):
    """Release-readiness view model: what is done and what is still missing before a production release."""
    st = store.state(ws, cid)
    decs = store.decisions(ws, cid)
    pkg, pkg_err = None, None
    try:
        if os.path.exists(os.path.join(package_dir(ws, cid), "package_manifest.json")):
            pkg = cq.load_package(package_dir(ws, cid))
    except Exception as e:  # noqa: BLE001
        pkg_err = str(e)
    dr = latest_dry_run(ws, cid)
    if pkg and dr and dr.get("package_seal") != pkg["seal_sha256"]:
        dr = dict(dr, stale=True)
    stages = [("Candidate validated", st["state"] not in ("CANDIDATE_GENERATED", "REJECTED") and not st.get("qa_failed")),
              ("Restatement decision recorded", bool(decs) and decs[-1]["option"] == "B" and st["state"] == "APPROVED_FOR_FUTURE_RELEASE"),
              ("Release package prepared", pkg is not None),
              ("Dry run validated", bool(dr and dr.get("pass") and not dr.get("stale"))),
              ("Awaiting production approval", False)]
    missing = []
    if not stages[1][1]:
        missing.append("recorded option B decision with restatement approval")
    if not stages[2][1]:
        missing.append("sealed release package" + (f" ({pkg_err})" if pkg_err else ""))
    if not stages[3][1]:
        missing.append("passing dry run for the current package")
    missing += ["release code (ingestion, C1 refresh, quarterly release tool) committed and reviewed",
                "candidate source bytes archived where a clean checkout can retrieve them",
                "named release approver and release ticket (PRODUCTION authorisation)",
                "organisational governance approvals, if required",
                "separate authorisation to set LIVE_RELEASE_ENABLED for the release window"]
    return {"state": st, "decision": decs[-1] if decs else None, "package": pkg, "package_error": pkg_err, "dry_run": dr,
            "stages": stages, "missing": missing, "publication_enabled": False,
            "live_release_enabled": rcfg.LIVE_RELEASE_ENABLED}
