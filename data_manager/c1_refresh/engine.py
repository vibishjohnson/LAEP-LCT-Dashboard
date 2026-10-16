"""
C1 candidate engine: run the approved method on a source set, QA it, build the integration outputs,
analyse historical restatements and write a sealed candidate package.

Approved code is imported, not copied: c1_method (the calculation), run_c1 (quarter input, C1 run,
canonical LSOA rows, accounting / geography / aggregation checks) and integration_writer (geography,
C1 LSOA validation, DNO/LA/LAEP frames, CSV bytes). The only C1 logic written here is the VEH0135
loader, which repeats run_c1.load_veh0135 line for line with the path and quarter list as parameters;
reproducing the live release byte for byte (reproduce_baseline) proves it is unchanged in effect.
"""

import io
import json
import os
import stat
import time

import numpy as np
import pandas as pd

from . import config as C
from . import sources as S

import c1_method as m                 # noqa: E402  (approved method, unmodified)
import run_c1 as rc                   # noqa: E402
import integration_writer as iw       # noqa: E402

PROVENANCE_COLS = ("source_veh0135_sha256", "source_lsoa_dno_sha256", "code_git_head", "run_utc")


# ------------------------------------------------------------------ method run
def load_veh0135(path, quarters):
    """run_c1.load_veh0135 with the path and quarters as parameters (same operations, same order)."""
    cols = ["LSOA21CD", "Fuel", "Keepership"] + list(quarters)
    d = pd.read_csv(path, low_memory=False, usecols=cols)
    d["EV_Type"] = d.Fuel.map(rc.FUEL_MAP)
    d = d[d.EV_Type.notna() & d.Keepership.isin(["Total", "Private", "Company"])].copy()
    d["LSOA21CD"] = d.LSOA21CD.astype(str).str.strip()
    for q in quarters:
        d[q] = rc.clean_numeric(d[q])
    return d


def run_method(source_path, quarters, provenance):
    canon, flags = rc.load_geography()
    d = load_veh0135(source_path, quarters)
    out, national = {}, {}
    for q in quarters:
        per = C.quarter_period(q)
        qi = rc.quarter_input(d, q)
        ukpn, nat = rc.run_method(qi, canon, "C1")
        out[per] = rc.canonical_long(ukpn, canon, per, m.METHODOLOGY_ID, {**provenance, "lpn_min_share": m.LPN_FLOOR["C1"]})
        national[per] = (qi, nat)
    del d
    return canon, flags, out, national


def provenance_for(set_manifest):
    return {"source_veh0135_sha256": set_manifest["source"]["sha256"][:16],
            "source_lsoa_dno_sha256": set_manifest["lookups"]["lsoa_dno"]["sha256"][:16],
            "code_git_head": (S.git_head() or "unknown")[:7],
            "run_utc": S.now()}


def lsoa_frame(out):
    return pd.concat([out[p] for p in sorted(out)], ignore_index=True)


def integration_frames(c1_lsoa, periods):
    canon = iw.load_geography()
    lsoa = iw.validate_c1_lsoa(c1_lsoa, canon, periods)
    return iw.build_integration_frames(lsoa, canon), canon


# ------------------------------------------------------------------ QA (numerical evidence for every check)
def qa_checks(canon, flags, out, national, frames, iw_canon, periods):
    keyed = {("C1", p): out[p] for p in out}
    nat_keyed = {("C1", p): national[p] for p in national}
    acc = rc.accounting_checks(keyed, nat_keyed)
    acc["detail"] = ""
    geo = rc.geography_checks(keyed, canon, flags)
    agg = rc.aggregate(keyed)
    aggc = rc.aggregation_checks(agg, canon)
    rows = [acc.assign(group="accounting")[["group", "period", "check", "passed", "detail"]],
            geo.assign(group="geography", period=""), aggc.assign(group="aggregation", period="")]
    extra = []
    for per in periods:
        qi, nat = national[per]
        df = nat["national"]
        geo_mask = df.LSOA21CD != m.MISC_CODE
        priv = (df.private_bev + df.private_phev).where(geo_mask, 0).astype(float)
        expected_w = priv / priv.sum()
        dev = float(np.abs(df.recipient_weight.values - expected_w.values).max())
        extra.append(("method", per, "recipient weight = same-quarter private stock share (Miscellaneous 0)", dev < 1e-12, f"max |diff| {dev:.2e}"))
        extra.append(("method", per, "Miscellaneous rows present and treated as non-recipient", bool((df[~geo_mask].recipient_weight == 0).all()),
                      f"{int((~geo_mask).sum())} Miscellaneous code(s), raw {int((df[~geo_mask].raw_bev + df[~geo_mask].raw_phev).sum()):,}"))
        again = m.run_quarter(qi, "C1")
        same = all(np.array_equal(again[k], nat_val) for k, nat_val in (("received_bev", df.received_bev.values), ("received_phev", df.received_phev.values)))
        extra.append(("method", per, "largest-remainder rounding deterministic (re-run identical)", same,
                      f"pool BEV {nat['pool_bev']:,} PHEV {nat['pool_phev']:,}; received sums {int(df.received_bev.sum()):,}/{int(df.received_phev.sum()):,}"))
        share = nat["lpn_post"] / nat["ukpn_post"]
        extra.append(("method", per, "LPN floor 0.20 applied by formula (status reported, not assumed)", nat["lpn_floor"] == 0.20,
                      f"LPN post share {share:.4%}; uplift {nat['uplift']:,} ({'ACTIVE' if nat['uplift'] else 'inactive'})"))
    dno, la, laep = frames
    exp = C.expected_rows(len(periods))
    for name, f in zip(C.INTEGRATION_NAMES, frames):
        extra.append(("integration", "", f"{name}: {exp[name]} rows", len(f) == exp[name], f"{len(f)} rows"))
        extra.append(("integration", "", f"{name}: periods {periods[0]}..{periods[-1]} only, no duplicates",
                      sorted(f.period.unique()) == periods and not f.duplicated([c for c in f.columns if c in ("period", "DNO", "LAD22CD", "LAEP", "EV_Type")]).any(),
                      f"{sorted(f.period.unique())}"))
    extra.append(("integration", "", "121 LAs and 14 LAEPs", la.LAD22CD.nunique() == 121 and laep.LAEP.nunique() == 14,
                  f"{la.LAD22CD.nunique()} LAs, {laep.LAEP.nunique()} LAEPs"))
    lsoa_tot = lsoa_frame(out).groupby(["period", "EV_Type"]).final_estimated_stock.sum()
    dno_tot = dno.groupby(["period", "EV_Type"]).install_count.sum()
    la_tot = la.groupby(["period", "EV_Type"]).value.sum()
    extra.append(("integration", "", "DNO = LA = sum of LSOAs (every period, both fuels)", bool((lsoa_tot == dno_tot).all() and (lsoa_tot == la_tot).all()),
                  f"max diff {int(max((lsoa_tot - dno_tot).abs().max(), (lsoa_tot - la_tot).abs().max()))}"))
    lad_laep = iw_canon.dropna(subset=["LAEP"]).drop_duplicates("LAD22CD").set_index("LAD22CD").LAEP
    via = la.assign(LAEP=la.LAD22CD.map(lad_laep)).dropna(subset=["LAEP"]).groupby(["period", "LAEP", "EV_Type"]).value.sum()
    lp = laep.set_index(["period", "LAEP", "EV_Type"]).value
    extra.append(("integration", "", "each LAEP = sum of its mapped LAs", bool((via.reindex(lp.index) == lp).all()), f"{len(lp)} LAEP rows compared"))
    ids = set(lsoa_frame(out).methodology_id)
    extra.append(("integration", "", "only C1 observations (no Method A rows), first period 2025-03", ids == {m.METHODOLOGY_ID} and periods[0] == "2025-03",
                  f"methodology ids {sorted(ids)}"))
    rows.append(pd.DataFrame(extra, columns=["group", "period", "check", "passed", "detail"]))
    qa = pd.concat(rows, ignore_index=True)
    qa["passed"] = qa.passed.astype(bool)
    return qa, agg


# ------------------------------------------------------------------ published history and restatements
def published():
    """The live published C1 state (read-only): LSOA output of the release and the three integration files."""
    lsoa = pd.read_csv(os.path.join(C.PROJECT_ROOT, C.PUBLISHED_C1_LSOA_REL))
    files = {n: open(os.path.join(C.PROJECT_ROOT, C.LIVE_INTEGRATION_REL, n), "rb").read() for n in C.INTEGRATION_NAMES}
    frames = {n: pd.read_csv(io.BytesIO(b)) for n, b in files.items()}
    return lsoa, files, frames


def source_revisions(old_path, new_path, quarters):
    """Row-level comparison of the C1-relevant source rows of two VEH0135 vintages (raw text and parsed values)."""
    cols = ["LSOA21CD", "Fuel", "Keepership"] + list(quarters)
    keep = lambda d: d[d.Fuel.isin(list(rc.FUEL_MAP)) & d.Keepership.isin(["Total", "Private", "Company"])]
    a = keep(pd.read_csv(old_path, usecols=cols, dtype=str, keep_default_na=False))
    b = keep(pd.read_csv(new_path, usecols=cols, dtype=str, keep_default_na=False))
    a.LSOA21CD, b.LSOA21CD = a.LSOA21CD.str.strip(), b.LSOA21CD.str.strip()
    j = a.merge(b, on=["LSOA21CD", "Fuel", "Keepership"], how="outer", suffixes=("_old", "_new"), indicator=True)
    rows = []
    for q in quarters:
        o, n = j[f"{q}_old"].fillna(""), j[f"{q}_new"].fillna("")
        both = j._merge == "both"
        changed = both & (o != n)
        sup_change = changed & ((o == "[c]") | (n == "[c]"))
        vo, vn = rc.clean_numeric(o), rc.clean_numeric(n)
        for fuel in ("BEV", "PHEV"):
            for keeper in ("Total", "Private", "Company"):
                sel = j.Fuel.map(rc.FUEL_MAP).eq(fuel) & j.Keepership.eq(keeper)
                rows.append(dict(quarter=q, period=C.quarter_period(q), fuel=fuel, keepership=keeper,
                                 national_old=int(vo[sel].sum()), national_new=int(vn[sel].sum()),
                                 rows_changed=int((changed & sel).sum()), rows_changed_suppression=int((sup_change & sel).sum()),
                                 rows_only_old=int(((j._merge == "left_only") & sel).sum()), rows_only_new=int(((j._merge == "right_only") & sel).sum())))
    df = pd.DataFrame(rows)
    df["diff"] = df.national_new - df.national_old
    df["pct"] = (100 * df["diff"] / df.national_old.replace(0, np.nan)).round(4)
    codes_old, codes_new = set(a.LSOA21CD), set(b.LSOA21CD)
    geo = {"codes_old": len(codes_old), "codes_new": len(codes_new), "only_old": sorted(codes_old - codes_new)[:200],
           "only_new": sorted(codes_new - codes_old)[:200], "n_only_old": len(codes_old - codes_new), "n_only_new": len(codes_new - codes_old),
           "schema_old_cols": list(pd.read_csv(old_path, nrows=0).columns[:4]), "schema_new_cols": list(pd.read_csv(new_path, nrows=0).columns[:4])}
    return df, geo


def geography_report(canon, new_path, old_path, latest_q):
    """Canonical UKPN LSOAs with/without source rows in each vintage; the canonical set is never modified."""
    def present(path, q):
        d = pd.read_csv(path, usecols=["LSOA21CD", "Fuel", "Keepership", q], dtype=str, keep_default_na=False)
        d = d[d.Fuel.isin(list(rc.FUEL_MAP)) & (d.Keepership == "Total")]
        return set(d.LSOA21CD.str.strip())
    can = set(canon.LSOA21CD)
    new_codes, old_codes = present(new_path, latest_q), present(old_path, "2026 Q1")
    return {"canonical_ukpn_lsoas": len(can), "canonical_with_rows_new_source": len(can & new_codes),
            "canonical_without_rows_new_source": len(can - new_codes), "canonical_with_rows_old_source": len(can & old_codes),
            "canonical_newly_present": sorted(can & (new_codes - old_codes)), "canonical_no_longer_present": sorted(can & (old_codes - new_codes)),
            "note": "Canonical membership comes from lookups/LSOA to DNO.csv and is never changed to match a source; "
                    "canonical LSOAs without source rows are kept as explicit zeros (approved rule)."}


def restatement(pub_lsoa, pub_frames, new_lsoa, new_frames, periods_hist, national_new):
    """Published values vs latest-vintage recalculation for every historical quarter, at every level."""
    key = ["period", "LSOA21CD", "EV_Type"]
    a = pub_lsoa[pub_lsoa.period.isin(periods_hist)][key + ["DNO", "LAD22NM", "LAEP", "raw_total", "final_estimated_stock"]]
    b = new_lsoa[new_lsoa.period.isin(periods_hist)][key + ["raw_total", "final_estimated_stock"]]
    j = a.merge(b, on=key, how="outer", suffixes=("_published", "_recalculated"), indicator=True)
    lsoa = j.rename(columns={"final_estimated_stock_published": "published", "final_estimated_stock_recalculated": "recalculated"})
    lsoa["diff"] = lsoa.recalculated - lsoa.published
    lsoa["raw_diff"] = lsoa.raw_total_recalculated - lsoa.raw_total_published
    lsoa["pct"] = (100 * lsoa["diff"] / lsoa.published.replace(0, np.nan)).round(3)
    geo_rows = []
    names = {"ev_actuals_dno.csv": (["DNO"], "install_count"), "ev_actuals_la.csv": (["LAD22CD", "LAD22NM"], "value"),
             "ev_actuals_laep.csv": (["LAEP"], "value")}
    for n, (keys, val) in names.items():
        p = pub_frames[n][pub_frames[n].period.isin(periods_hist)]
        r = new_frames[n][new_frames[n].period.isin(periods_hist)]
        x = p.merge(r, on=["period"] + keys + ["EV_Type"], how="outer", suffixes=("_published", "_recalculated"))
        level = {"ev_actuals_dno.csv": "DNO", "ev_actuals_la.csv": "LA", "ev_actuals_laep.csv": "LAEP"}[n]
        for (per, *k), g in x.groupby(["period"] + keys):
            for fuel in ("BEV", "PHEV"):
                gf = g[g.EV_Type == fuel]
                pv, rv = int(gf[f"{val}_published"].sum()), int(gf[f"{val}_recalculated"].sum())
                geo_rows.append(dict(level=level, geography=k[0], geography_name=k[1] if len(k) > 1 else k[0], period=per, EV_Type=fuel,
                                     published=pv, recalculated=rv))
    geo = pd.DataFrame(geo_rows)
    tot = geo.groupby(["level", "geography", "geography_name", "period"], as_index=False)[["published", "recalculated"]].sum().assign(EV_Type="Total")
    geo = pd.concat([geo, tot], ignore_index=True)
    uk = geo[geo.level == "DNO"].groupby(["period", "EV_Type"], as_index=False)[["published", "recalculated"]].sum()
    uk["level"], uk["geography"], uk["geography_name"] = "UKPN", "UKPN", "UKPN"
    geo = pd.concat([uk, geo], ignore_index=True)
    geo["diff"] = geo.recalculated - geo.published
    geo["pct"] = (100 * geo["diff"] / geo.published.replace(0, np.nan)).round(4)
    return lsoa, geo


def national_pools(national_by_vintage):
    rows = []
    for vintage, national in national_by_vintage.items():
        for per, (qi, nat) in national.items():
            rows.append(dict(vintage=vintage, period=per, national_bev=int(qi.total_bev.sum()), national_phev=int(qi.total_phev.sum()),
                             pool_bev=nat["pool_bev"], pool_phev=nat["pool_phev"], ukpn_post=nat["ukpn_post"], lpn_post=nat["lpn_post"],
                             lpn_post_share_pct=round(100 * nat["lpn_post"] / nat["ukpn_post"], 4), lpn_uplift=nat["uplift"],
                             lpn_floor_active=nat["uplift"] > 0))
    return pd.DataFrame(rows)


# ------------------------------------------------------------------ baseline reproduction
def reproduce_baseline(ws, release_set_id, progress=print):
    """Re-run the approved C1 method on the exact source of the live release and compare with what was published."""
    sm = S.load(ws, release_set_id)
    src = S.verified_source_path(ws, sm)
    t0 = time.time()
    canon, flags, out, national = run_method(src, sm["quarters"], provenance_for(sm))
    progress(f"baseline method run {time.time() - t0:.1f}s")
    mine = lsoa_frame(out)
    pub_lsoa, pub_files, _ = published()
    numeric = [c for c in pub_lsoa.columns if c not in PROVENANCE_COLS]
    a = pub_lsoa.sort_values(["period", "EV_Type", "LSOA21CD"]).reset_index(drop=True)
    b = mine.sort_values(["period", "EV_Type", "LSOA21CD"]).reset_index(drop=True)
    same_cols = list(a.columns) == list(b.columns)
    diffs = {}
    for c in numeric:
        x, y = a[c], b[c]
        neq = ~((x == y) | (x.isna() & y.isna()))
        if c == "recipient_weight":
            neq = (x - y).abs() > 1e-15
        diffs[c] = int(neq.sum())
    prov = {c: {"published": sorted(map(str, a[c].unique()))[:3], "reproduced": sorted(map(str, b[c].unique()))[:3]} for c in PROVENANCE_COLS}
    (dno, la, laep), _ = integration_frames(mine, sm["periods"])
    bytes_equal = {n: iw.to_csv_bytes(f) == pub_files[n] for n, f in zip(C.INTEGRATION_NAMES, (dno, la, laep))}
    totals = {per: {"UKPN": int(dno[dno.period == per].install_count.sum()),
                    **{d: int(dno[(dno.period == per) & (dno.DNO == d)].install_count.sum()) for d in ("EPN", "LPN", "SPN")},
                    "uplift": national[per][1]["uplift"]} for per in sm["periods"]}
    report = {"source_set_id": release_set_id, "source_sha256": sm["source"]["sha256"], "periods": sm["periods"],
              "rows_published": len(a), "rows_reproduced": len(b), "columns_identical": same_cols,
              "numeric_cell_differences": diffs, "numeric_identical": same_cols and len(a) == len(b) and not any(diffs.values()),
              "provenance_fields_differ": prov, "integration_bytes_identical": bytes_equal,
              "integration_sha256": {n: S.sha256_file(os.path.join(C.PROJECT_ROOT, C.LIVE_INTEGRATION_REL, n)) for n in C.INTEGRATION_NAMES},
              "totals": totals, "lpn_floor": 0.20, "method_a_rows": int((mine.methodology_id != m.METHODOLOGY_ID).sum()),
              "seconds": round(time.time() - t0, 1), "at": S.now()}
    report["passed"] = report["numeric_identical"] and all(bytes_equal.values()) and report["method_a_rows"] == 0
    d = ws.path("baseline", release_set_id)
    os.makedirs(d, exist_ok=True)
    with open(os.path.join(d, "reproduction.json"), "w", encoding="utf-8") as f:
        json.dump(report, f, indent=2, default=str)
    return report


# ------------------------------------------------------------------ candidate package
def _write(path, data):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "wb") as f:
        f.write(data)
    return S.sha256_file(path)


def build_candidate(ws, set_id, release_set_id, progress=print):
    """Generate, QA and seal a candidate. Writes only below <workspace>/candidates/<candidate_id>."""
    sm = S.load(ws, set_id)
    if sm["kind"] != "REGISTERED_VERSION":
        raise ValueError("candidates are generated only from registered (validated) source versions")
    baseline = json.load(open(ws.path("baseline", release_set_id, "reproduction.json"), encoding="utf-8"))
    if not baseline["passed"]:
        raise ValueError("baseline reproduction has not passed; refusing to generate a candidate")
    rm = S.load(ws, release_set_id)
    src_new, src_old = S.verified_source_path(ws, sm), S.verified_source_path(ws, rm)
    live_before = {n: S.sha256_file(os.path.join(C.PROJECT_ROOT, C.LIVE_INTEGRATION_REL, n)) for n in C.INTEGRATION_NAMES + ("metadata.json",)}
    cid = f"C1C-{sm['reporting_quarter'].replace(' ', '')}-{sm['source']['sha256'][:8]}-{S.now().replace(':', '').replace('-', '')[:15]}"
    root = ws.path("candidates", cid)
    tmp = root + ".partial"                         # never visible as a candidate until sealed and renamed
    os.makedirs(tmp)
    timings, t0 = {}, time.time()
    periods, hist_periods = sm["periods"], sm["periods"][:-1]
    new_period = sm["periods"][-1]

    progress("running C1 on the new source (all quarters)")
    canon, flags, out, national = run_method(src_new, sm["quarters"], provenance_for(sm))
    timings["method_new"] = round(time.time() - t0, 1)
    c1 = lsoa_frame(out)
    (dno, la, laep), iw_canon = integration_frames(c1, periods)
    progress("QA")
    qa, agg = qa_checks(canon, flags, out, national, (dno, la, laep), iw_canon, periods)

    progress("restatement analysis")
    t1 = time.time()
    old_national = run_method(src_old, rm["quarters"], provenance_for(rm))[3]     # pools / LPN floor of the published vintage
    pub_lsoa, pub_files, pub_frames = published()
    new_frames = dict(zip(C.INTEGRATION_NAMES, (dno, la, laep)))
    lsoa_rest, geo_rest = restatement(pub_lsoa, pub_frames, c1, new_frames, hist_periods, national)
    src_rev, src_geo = source_revisions(src_old, src_new, rm["quarters"])
    geo_report = geography_report(iw_canon.reset_index(), src_new, src_old, sm["quarters"][-1])
    pools = national_pools({"published (2026 Q1 vintage)": old_national, "latest (" + sm["reporting_quarter"] + " vintage)": national})
    timings["restatement"] = round(time.time() - t1, 1)

    # publication alternatives
    restated_bytes = {n: iw.to_csv_bytes(f) for n, f in new_frames.items()}
    append_bytes, append_checks = {}, []
    for n, f in new_frames.items():
        new_rows = iw.to_csv_bytes(f[f.period == new_period])
        body = new_rows.split(b"\r\n", 1)[1]
        append_bytes[n] = pub_files[n] + body
        parsed = pd.read_csv(io.BytesIO(append_bytes[n]))
        ok_prefix = append_bytes[n].startswith(pub_files[n])
        ok_hist = parsed[parsed.period != new_period].reset_index(drop=True).equals(pub_frames[n].reset_index(drop=True))
        ok_new = restated_bytes[n].endswith(body)
        append_checks.append(("publication", "", f"append-only {n}: published bytes kept exactly as a prefix", ok_prefix, f"{len(pub_files[n]):,} bytes"))
        append_checks.append(("publication", "", f"append-only {n}: historical rows identical to live", ok_hist, f"{len(pub_frames[n])} rows"))
        append_checks.append(("publication", "", f"append-only {n}: {new_period} rows identical to the recalculation", ok_new,
                              f"{len(f[f.period == new_period])} rows"))
        append_checks.append(("publication", "", f"append-only {n}: {C.expected_rows(len(periods))[n]} rows",
                              len(parsed) == C.expected_rows(len(periods))[n], f"{len(parsed)} rows"))
    lsoa_changed = lsoa_rest[(lsoa_rest["diff"] != 0) | (lsoa_rest._merge != "both")]
    differ = any(append_bytes[n] != restated_bytes[n] for n in C.INTEGRATION_NAMES)
    for n in C.INTEGRATION_NAMES:
        a_df, r_df = pd.read_csv(io.BytesIO(append_bytes[n])), pd.read_csv(io.BytesIO(restated_bytes[n]))
        keys = [c for c in a_df.columns if c in ("period", "DNO", "LAD22CD", "LAEP", "EV_Type")]
        val = "install_count" if "install_count" in a_df else "value"
        same_keys = a_df[keys].equals(r_df[keys])
        new_same = a_df[a_df.period == new_period].reset_index(drop=True).equals(r_df[r_df.period == new_period].reset_index(drop=True))
        n_rev = int((a_df[val] != r_df[val]).sum()) if same_keys else -1
        append_checks.append(("publication", "", f"{n}: options share keys and {new_period} rows; differ only in historical values",
                              same_keys and new_same and not (a_df.drop(columns=[val]).compare(r_df.drop(columns=[val])).size if same_keys else 1),
                              f"{n_rev} historical rows revised"))
    append_checks.append(("publication", "", "both options open in March 2025", all(pd.read_csv(io.BytesIO(b)).period.min() == "2025-03" for b in list(append_bytes.values()) + list(restated_bytes.values())), ""))
    live_after = {n: S.sha256_file(os.path.join(C.PROJECT_ROOT, C.LIVE_INTEGRATION_REL, n)) for n in C.INTEGRATION_NAMES + ("metadata.json",)}
    append_checks.append(("safety", "", "live integration files and metadata.json unchanged during generation", live_before == live_after,
                          {k: v[:12] for k, v in live_after.items()}))
    qa = pd.concat([qa, pd.DataFrame(append_checks, columns=qa.columns)], ignore_index=True)
    qa["passed"] = qa.passed.astype(bool)

    # write package
    progress("writing package")
    files = {}

    def put(rel, data):
        files[rel] = _write(os.path.join(tmp, *rel.split("/")), data)

    put("c1_lsoa_latest_vintage.csv", c1.to_csv(index=False).encode("utf-8"))
    # Append-only LSOA history: the published bytes untouched (re-serialising would perturb float digits), new rows appended.
    pub_lsoa_bytes = open(os.path.join(C.PROJECT_ROOT, C.PUBLISHED_C1_LSOA_REL), "rb").read()
    eol = "\r\n" if b"\r\n" in pub_lsoa_bytes[:4096] else "\n"
    new_lsoa = c1[c1.period == new_period]
    if list(new_lsoa.columns) != list(pub_lsoa.columns) or not pub_lsoa_bytes.endswith(eol.encode()):
        raise ValueError("new LSOA rows do not match the published LSOA layout")
    put("c1_lsoa_append_only.csv", pub_lsoa_bytes + new_lsoa.to_csv(index=False, lineterminator=eol).encode("utf-8").split(eol.encode(), 1)[1])
    for n in C.INTEGRATION_NAMES:
        put(f"integration/append_only/{n}", append_bytes[n])
        put(f"integration/restated/{n}", restated_bytes[n])
    put("reports/qa_checks.csv", qa.to_csv(index=False).encode("utf-8"))
    put("reports/restatement_geography.csv", geo_rest.to_csv(index=False).encode("utf-8"))
    put("reports/restatement_lsoa_changes.csv", lsoa_changed.drop(columns=["_merge"]).to_csv(index=False).encode("utf-8"))
    put("reports/source_revisions.csv", src_rev.to_csv(index=False).encode("utf-8"))
    put("reports/national_pools_and_lpn_floor.csv", pools.to_csv(index=False).encode("utf-8"))
    put("reports/geography_aggregates.csv", agg.to_csv(index=False).encode("utf-8"))
    put("reports/source_geography.json", json.dumps({"source_codes": src_geo, "canonical": geo_report}, indent=2, default=str).encode("utf-8"))
    summary = summarise(sm, rm, qa, dno, pools, geo_rest, lsoa_rest, src_rev, append_bytes, restated_bytes, new_period, hist_periods, geo_report, src_geo)
    put("reports/summary.json", json.dumps(summary, indent=2, default=str).encode("utf-8"))
    timings["total"] = round(time.time() - t0, 1)
    from data_manager.ingestion.procmem import self_memory_mb
    mem = dict(zip(("working_set_mb", "peak_working_set_mb", "peak_commit_mb"), self_memory_mb()))
    manifest = {
        "schema": C.CANDIDATE_SCHEMA, "candidate_id": cid, "created_at": S.now(), "source_set_id": set_id,
        "source_set_seal": sm["seal_sha256"], "baseline_source_set_id": release_set_id, "baseline_reproduction_passed": True,
        "methodology": {"methodology_id": m.METHODOLOGY_ID, "lpn_min_share": m.LPN_FLOOR["C1"], "code": S.code_revision()},
        "input_sha256": {"VEH0135": sm["source"]["sha256"], **{k: v["sha256"] for k, v in sm["lookups"].items()},
                         "published_c1_lsoa": S.sha256_file(os.path.join(C.PROJECT_ROOT, C.PUBLISHED_C1_LSOA_REL)),
                         "baseline_VEH0135": rm["source"]["sha256"]},
        "periods": periods, "new_period": new_period, "historical_periods": hist_periods,
        "live_release": sm["live_release"],
        "publication_options": {
            "A_APPEND_ONLY": {"expected_production_sha256": {n: S.sha256_file(os.path.join(tmp, "integration", "append_only", n)) for n in C.INTEGRATION_NAMES},
                              "description": f"five published quarters kept byte-for-byte; {new_period} appended"},
            "B_RESTATED": {"expected_production_sha256": {n: S.sha256_file(os.path.join(tmp, "integration", "restated", n)) for n in C.INTEGRATION_NAMES},
                           "description": "all quarters recalculated from the latest DfT vintage; requires explicit restatement approval",
                           "differs_from_append_only": differ},
            "C_REJECT_OR_DEFER": {"description": "dashboard unchanged"}},
        "qa": {"passed": int(qa.passed.sum()), "total": len(qa), "all_passed": bool(qa.passed.all())},
        "restatements_detected": bool(differ), "timings_s": timings, "resources": mem,
        "files_sha256": files,
        "status_note": "Sealed candidate. Not approved, not published. Decisions are recorded separately in decisions.jsonl.",
    }
    manifest["seal_sha256"] = S.seal(manifest)
    _write(os.path.join(tmp, "manifest.json"), json.dumps(manifest, indent=2, sort_keys=True, default=str).encode("utf-8"))
    for dp, _, fs in os.walk(tmp):
        for f in fs:
            os.chmod(os.path.join(dp, f), stat.S_IREAD | stat.S_IRGRP | stat.S_IROTH)
    os.replace(tmp, root)
    from . import store
    store.init_state(ws, cid, manifest)
    return manifest


def summarise(sm, rm, qa, dno, pools, geo_rest, lsoa_rest, src_rev, append_bytes, restated_bytes, new_period, hist_periods, geo_report, src_geo):
    tot = lambda frame, per, dnos=None: int(frame[(frame.period == per) & (frame.DNO.isin(dnos) if dnos else True)].install_count.sum())
    new = {"UKPN": tot(dno, new_period), **{d: tot(dno, new_period, [d]) for d in ("EPN", "LPN", "SPN")},
           **{f: int(dno[(dno.period == new_period) & (dno.EV_Type == f)].install_count.sum()) for f in ("BEV", "PHEV")}}
    series = {per: {"UKPN": tot(dno, per), **{d: tot(dno, per, [d]) for d in ("EPN", "LPN", "SPN")},
                    **{f: int(dno[(dno.period == per) & (dno.EV_Type == f)].install_count.sum()) for f in ("BEV", "PHEV")}} for per in sorted(dno.period.unique())}
    g = geo_rest[geo_rest.EV_Type == "Total"]
    affected = {lvl: int(g[(g.level == lvl) & (g["diff"] != 0)].geography.nunique()) for lvl in ("DNO", "LA", "LAEP")}
    lsoa_affected = int(lsoa_rest[lsoa_rest["diff"] != 0].LSOA21CD.nunique())
    top = lambda lvl, col: g[g.level == lvl].reindex(g[g.level == lvl][col].abs().sort_values(ascending=False).index).head(10)[
        ["geography", "geography_name", "period", "published", "recalculated", "diff", "pct"]].to_dict("records")
    return {"source_set_id": sm["source_set_id"], "reporting_quarter": sm["reporting_quarter"], "new_period": new_period,
            "new_quarter_values": new, "latest_vintage_series": series,
            "lpn_floor": pools.to_dict("records"),
            "restatement_ukpn": geo_rest[geo_rest.level == "UKPN"].to_dict("records"),
            "restatement_dno": geo_rest[(geo_rest.level == "DNO") & (geo_rest.EV_Type == "Total")].to_dict("records"),
            "affected_geographies": {**affected, "LSOA": lsoa_affected},
            "largest_la_abs": top("LA", "diff"), "largest_la_pct": top("LA", "pct"),
            "largest_laep_abs": top("LAEP", "diff"), "largest_laep_pct": top("LAEP", "pct"),
            "largest_lsoa_abs": lsoa_rest.reindex(lsoa_rest["diff"].abs().sort_values(ascending=False).index).head(15)[
                ["period", "LSOA21CD", "EV_Type", "DNO", "LAD22NM", "published", "recalculated", "diff", "raw_diff"]].to_dict("records"),
            "source_revisions_total": src_rev[src_rev.keepership == "Total"].to_dict("records"),
            "source_geography": {k: v for k, v in src_geo.items() if not k.startswith("only_")},
            "canonical_geography": {k: (v if not isinstance(v, list) else {"count": len(v), "examples": v[:10]}) for k, v in geo_report.items()},
            "qa_failed": qa[~qa.passed].to_dict("records"),
            "append_vs_restated_bytes_differ": {n: append_bytes[n] != restated_bytes[n] for n in append_bytes}}


def verify_package(ws, cid):
    root = ws.path("candidates", cid)
    man = json.load(open(os.path.join(root, "manifest.json"), encoding="utf-8"))
    ok = man.get("seal_sha256") == S.seal(man)
    bad = [rel for rel, h in man["files_sha256"].items() if S.sha256_file(os.path.join(root, *rel.split("/"))) != h]
    return ok and not bad, {"seal_valid": ok, "changed_files": bad}
