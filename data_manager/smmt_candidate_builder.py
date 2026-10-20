"""
SMMT provisional EV nowcast candidates (C1-anchored). Builds isolated, sealed candidates; never publishes.

The nowcast is a separate provisional estimate, not an observation:
  * Baseline: the published C1 EV actuals at the latest actual period (integration/metadata.json
    `latest_actual_period`). The C1 LSOA stock is used and must aggregate exactly to the live
    ev_actuals_{dno,la,laep}.csv, so the nowcast starts from the plotted actual and never double counts it.
  * Additions: SMMT monthly UK new registrations (current entries in the data-manager database) for the
    contiguous months after the baseline, treated as net stock additions. Cars + vans per fuel.
  * Fixed licence-area shares (approved method): EPN, LPN and SPN shares of total UK stock, separately for BEV and
    PHEV, at the baseline quarter: C1 licence-area stock / UK stock in that quarter's VEH0135 (all UK rows, including
    Scotland, Northern Ireland and "Miscellaneous", because SMMT registrations are UK-wide). The shares are held
    fixed until a newer C1 quarter becomes the baseline. Fuel definition as C1: BEV = Battery electric;
    PHEV = Plug-in hybrid electric (petrol|diesel).
  * Each month's UK registrations are split over EPN, LPN, SPN and the rest of the UK by the largest remainder
    method, then added cumulatively to each licence area's baseline stock.
  * Within a licence area: C1 LSOA stock shares per fuel at the baseline; monthly additions rounded with the
    largest remainder method (ties broken by LSOA21CD), so every month, fuel and licence area is conserved exactly.
  * DNO / LA / LAEP: aggregated from the LSOA results with C1's own LSOA attributes.

Outputs go to project/dft_staging/smmt_candidates/<candidate_id>/ only. A candidate is written to a temporary
folder and moved into place when complete; a failed run leaves a FAILED manifest and no data.
"""

import hashlib
import json
import os
import shutil
import sqlite3
import uuid
from datetime import datetime, timezone
from typing import Callable, Dict, List, Optional, Tuple

import numpy as np
import pandas as pd

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

METHOD_ID = "SMMT_NOWCAST_C1_FIXED_DNO_SHARE_V1"
DNOS = ("EPN", "LPN", "SPN")
FORECAST_SOURCE = "SMMT_NOWCAST"
FORECAST_SCENARIO = "SMMT_PROVISIONAL"
INTEGRATION_REL = "project/output_processed/integration"
C1_LSOA_REL = "experiments/ev_redistribution_c1/outputs/c1_lsoa_quarterly.csv"
VEH0135_REL = "ev/df_VEH0135.csv"
RELEASES_REL = "experiments/ev_redistribution_c1/release/state/releases"
QUARTERLY_ARCHIVE_REL = "experiments/ev_redistribution_c1/quarterly_archive"
CANDIDATES_REL = "project/dft_staging/smmt_candidates"
LIVE_WATCH = ("ev_actuals_dno.csv", "ev_actuals_la.csv", "ev_actuals_laep.csv", "metadata.json",
              "monthly_forecast_dno.csv", "monthly_forecast_la.csv", "monthly_forecast_laep.csv")
FUEL_MAP = {"Battery electric": "BEV", "Plug-in hybrid electric (petrol)": "PHEV",
            "Plug-in hybrid electric (diesel)": "PHEV"}
GEOS = {"dno": ["DNO"], "la": ["LAD22CD", "LAD22NM"], "laep": ["LAEP"]}
FORECAST_COLUMNS = ["value", "time_grain", "tech_type", "EV_Type", "forecast_source", "forecast_scenario",
                    "dfes_pathway", "building_block_id", "metric", "observation_source", "period", "period_end",
                    "is_anchor", "is_interpolated", "source_year", "anchor_date"]
DATA_FILES = ["nowcast_lsoa.csv", "smmt_nowcast_dno.csv", "smmt_nowcast_la.csv", "smmt_nowcast_laep.csv",
              "ukpn_monthly_summary.csv", "dno_monthly_summary.csv", "qa_checks.csv"]
PUBLISHED_FILES = ("smmt_nowcast_dno.csv", "smmt_nowcast_la.csv", "smmt_nowcast_laep.csv")
PUBLICATION_SCHEMA = "lct-smmt-nowcast-publication/1"
PREVIEW_FILES = ["preview/monthly_forecast_dno.csv", "preview/monthly_forecast_la.csv",
                 "preview/monthly_forecast_laep.csv"]


class CandidateError(Exception):
    """A check failed; the candidate is recorded as FAILED with this message."""


def _sha_file(path: str) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def _sha_json(obj) -> str:
    return hashlib.sha256(json.dumps(obj, sort_keys=True, separators=(",", ":"), default=str).encode()).hexdigest()


def _now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def month_end(period: str) -> str:
    return (pd.Period(period, freq="M").end_time.normalize()).strftime("%Y-%m-%d")


def quarter_label(period: str) -> str:
    p = pd.Period(period, freq="M")
    if p.month % 3:
        raise CandidateError(f"latest actual period {period} is not a quarter end")
    return f"{p.year} Q{p.quarter}"


def largest_remainder(weights: np.ndarray, total: int) -> np.ndarray:
    """Integers proportional to `weights` summing exactly to `total`; ties go to the earlier position."""
    if total == 0 or weights.sum() <= 0:
        return np.zeros(len(weights), dtype=np.int64)
    raw = weights / weights.sum() * total
    base = np.floor(raw).astype(np.int64)
    rest = int(total - base.sum())
    order = np.argsort(-(raw - base), kind="stable")
    base[order[:rest]] += 1
    return base


class SMmtCandidateBuilder:
    """Build, list, verify and reject SMMT nowcast candidates."""

    # SMMT four-category mapping (cars and vans are combined per fuel for the geographic allocation)
    SMMT_CATEGORY_MAP = {
        "BEV Cars": ("BEV", "Cars"),
        "BEV Vans": ("BEV", "Vans"),
        "PHEV Cars": ("PHEV", "Cars"),
        "PHEV Vans": ("PHEV", "Vans"),
    }
    DB_COLUMNS = {"BEV Cars": "bev_cars", "BEV Vans": "bev_vans", "PHEV Cars": "phev_cars", "PHEV Vans": "phev_vans"}

    def __init__(self, project_root: str = PROJECT_ROOT, db_path: Optional[str] = None):
        self.root = project_root
        if db_path is None:
            from data_manager.db import DB_PATH
            db_path = DB_PATH
        self.db_path = db_path
        self.candidates_dir = os.path.join(project_root, *CANDIDATES_REL.split("/"))

    # ------------------------------------------------------------------ inputs
    def _p(self, rel: str) -> str:
        return os.path.join(self.root, *rel.split("/"))

    def smmt_entries(self) -> List[Dict]:
        """Current SMMT entries (one per month), read-only."""
        if not os.path.exists(self.db_path):
            return []
        con = sqlite3.connect(f"file:{self.db_path}?mode=ro", uri=True)
        con.row_factory = sqlite3.Row
        try:
            rows = con.execute("SELECT id, year, month, bev_cars, bev_vans, phev_cars, phev_vans, entered_at, notes "
                               "FROM smmt_entries WHERE is_current = 1 ORDER BY year, month").fetchall()
        finally:
            con.close()
        return [dict(r) for r in rows]

    def release_sources(self, baseline: str) -> Dict:
        """C1 LSOA output and VEH0135 behind the live C1 actuals at `baseline`.

        The October release used experiments/.../outputs/c1_lsoa_quarterly.csv and ev/df_VEH0135.csv. A later C1-to-C1
        quarterly release keeps both in its sealed archive (quarterly_archive/<candidate_id>/candidate and /source), so
        the COMPLETE release ending at the baseline is used. Either way the LSOA output must reproduce the live actuals
        exactly (checked in calculate), so a wrong source fails closed.
        """
        default = {"c1_lsoa_path": C1_LSOA_REL, "veh0135_path": VEH0135_REL, "release": "October C1 release outputs"}
        if baseline in set(pd.read_csv(self._p(C1_LSOA_REL), usecols=["period"])["period"]):
            return default
        rdir = self._p(RELEASES_REL)
        found = []
        for rid in sorted(os.listdir(rdir)) if os.path.isdir(rdir) else []:
            p = os.path.join(rdir, rid, "release_manifest.json")
            if not os.path.exists(p):
                continue
            m = json.load(open(p, encoding="utf-8"))
            if m.get("kind") == "C1_TO_C1" and m.get("status") == "COMPLETE" and (m.get("periods") or [None])[-1] == baseline:
                found.append(m)
        for m in sorted(found, key=lambda m: m.get("started_at", ""), reverse=True):
            arch = f"{QUARTERLY_ARCHIVE_REL}/{m['candidate_id']}"
            lsoa = f"{arch}/candidate/" + ("c1_lsoa_latest_vintage.csv" if m.get("option") == "B" else "c1_lsoa_append_only.csv")
            src_dir = self._p(f"{arch}/source")
            veh = [f"{arch}/source/{n}" for n in sorted(os.listdir(src_dir)) if n.endswith(".csv")
                   and _sha_file(os.path.join(src_dir, n)) == m.get("source_sha256")] if os.path.isdir(src_dir) else []
            if os.path.exists(self._p(lsoa)) and veh:
                return {"c1_lsoa_path": lsoa, "veh0135_path": veh[0], "release": m["release_id"]}
        raise CandidateError(f"No C1 LSOA output found for the live C1 actuals at {baseline} (no October output and no "
                             f"COMPLETE quarterly release archive for that period)")

    def inputs(self) -> Dict:
        """Everything a candidate depends on, with hashes. Its fingerprint detects stale candidates."""
        integ = self._p(INTEGRATION_REL)
        meta = json.load(open(os.path.join(integ, "metadata.json"), encoding="utf-8"))
        live = {n: _sha_file(os.path.join(integ, n)) for n in LIVE_WATCH if os.path.exists(os.path.join(integ, n))}
        entries = self.smmt_entries()
        try:
            srcs = self.release_sources(meta.get("latest_actual_period"))
        except CandidateError:
            srcs = {"c1_lsoa_path": C1_LSOA_REL, "veh0135_path": VEH0135_REL, "release": "unresolved"}
        inp = {
            "baseline_period": meta.get("latest_actual_period"),
            "live_integration_sha256": live,
            "c1_release": srcs["release"],
            "c1_lsoa_path": srcs["c1_lsoa_path"],
            "veh0135_path": srcs["veh0135_path"],
            "c1_lsoa_sha256": _sha_file(self._p(srcs["c1_lsoa_path"])),
            "veh0135_sha256": _sha_file(self._p(srcs["veh0135_path"])),
            "smmt_entries": [{k: e[k] for k in ("id", "year", "month", "bev_cars", "bev_vans", "phev_cars", "phev_vans")}
                             for e in entries],
        }
        # content hashes only (the path labels are provenance; the hashes already identify the files)
        inp["fingerprint"] = _sha_json({k: v for k, v in inp.items() if k not in ("c1_release", "c1_lsoa_path", "veh0135_path")})
        inp["smmt_notes"] = {f"{e['year']}-{e['month']:02d}": (e.get("notes") or "") for e in entries}
        return inp

    def nowcast_months(self, baseline: str, entries: List[Dict]) -> Tuple[List[str], Dict]:
        """Contiguous SMMT months after the baseline; reports months ignored and why."""
        have = {f"{e['year']}-{e['month']:02d}": e for e in entries}
        months, cur = [], pd.Period(baseline, freq="M") + 1
        while str(cur) in have:
            months.append(str(cur))
            cur += 1
        after = sorted(m for m in have if m > baseline)
        info = {
            "ignored_at_or_before_baseline": sorted(m for m in have if m <= baseline),
            "ignored_after_gap": [m for m in after if m not in months],
            "first_missing_month": str(cur),
        }
        return months, info

    def _baseline(self, baseline: str, lsoa_rel: str = C1_LSOA_REL) -> pd.DataFrame:
        c1 = pd.read_csv(self._p(lsoa_rel), usecols=["period", "LSOA21CD", "DNO", "LAD22CD", "LAD22NM", "LAEP",
                                                       "EV_Type", "final_estimated_stock"],
                         dtype={"LAD22CD": str, "LSOA21CD": str})
        b = c1[c1["period"] == baseline].copy()
        if b.empty:
            raise CandidateError(f"C1 LSOA output has no rows for the baseline period {baseline}")
        if b.duplicated(["LSOA21CD", "EV_Type"]).any():
            raise CandidateError("C1 baseline has duplicate LSOA21CD/EV_Type rows")
        return b.rename(columns={"final_estimated_stock": "baseline_stock"}).reset_index(drop=True)

    def _check_baseline_matches_live(self, b: pd.DataFrame, baseline: str) -> Dict:
        integ = self._p(INTEGRATION_REL)
        out = {}
        for key, cols, fname, col in (("dno", ["DNO"], "ev_actuals_dno.csv", "install_count"),
                                      ("la", ["LAD22CD"], "ev_actuals_la.csv", "value"),
                                      ("laep", ["LAEP"], "ev_actuals_laep.csv", "value")):
            live = pd.read_csv(os.path.join(integ, fname), dtype={"LAD22CD": str})
            live = live[live["period"] == baseline].groupby(cols + ["EV_Type"])[col].sum()
            agg = b.groupby(cols + ["EV_Type"])["baseline_stock"].sum()
            joined = pd.concat([live.rename("live"), agg.rename("c1")], axis=1)
            diff = int((joined["live"].fillna(-1) != joined["c1"].fillna(-1)).sum())
            out[key] = {"keys": int(len(joined)), "mismatched": diff}
            if diff:
                raise CandidateError(f"C1 baseline does not reproduce the live {fname} at {baseline} ({diff} keys differ)")
        return out

    def _uk_stock(self, baseline: str, veh_rel: str = VEH0135_REL) -> Dict[str, int]:
        q = quarter_label(baseline)
        hdr = pd.read_csv(self._p(veh_rel), nrows=0).columns
        if q not in hdr:
            raise CandidateError(f"VEH0135 has no {q} column for the baseline period {baseline}")
        s = pd.read_csv(self._p(veh_rel), usecols=["Fuel", "Keepership", q], dtype=str)
        s = s[s["Keepership"].str.strip().str.lower() == "total"]
        s["EV_Type"] = s["Fuel"].map(FUEL_MAP)
        s = s[s["EV_Type"].notna()]
        v = pd.to_numeric(s[q].str.replace(",", "", regex=False), errors="coerce").fillna(0)
        return {k: int(x) for k, x in v.groupby(s["EV_Type"]).sum().items()}

    # ------------------------------------------------------------------ calculation
    def calculate(self, inp: Dict, progress: Callable[[str], None] = lambda s: None) -> Dict:
        baseline = inp["baseline_period"]
        if not baseline:
            raise CandidateError("integration/metadata.json has no latest_actual_period")
        progress("Checking SMMT months")
        months, month_info = self.nowcast_months(baseline, inp["smmt_entries"])
        if not months:
            raise CandidateError(f"No SMMT entry for {month_info['first_missing_month']}, the first month after the "
                                 f"latest C1 actual ({baseline}); nothing to nowcast")
        entries = {f"{e['year']}-{e['month']:02d}": e for e in inp["smmt_entries"]}
        bad = [m for m in months for c in self.DB_COLUMNS.values() if not isinstance(entries[m][c], int) or entries[m][c] < 0]
        if bad:
            raise CandidateError(f"SMMT entries must be non-negative integers: {sorted(set(bad))}")

        progress("Loading C1 baseline")
        srcs = self.release_sources(baseline)
        b = self._baseline(baseline, srcs["c1_lsoa_path"])
        live_check = self._check_baseline_matches_live(b, baseline)
        ukpn = {f: int(x) for f, x in b.groupby("EV_Type")["baseline_stock"].sum().items()}
        progress("Calculating fixed licence-area shares of UK stock")
        uk = self._uk_stock(baseline, srcs["veh0135_path"])
        dno_stock = {f: {d: int(x) for d, x in b[b["EV_Type"] == f].groupby("DNO")["baseline_stock"].sum().items()}
                     for f in ("BEV", "PHEV")}
        dno_share, share = {}, {}
        for f in ("BEV", "PHEV"):
            if sorted(dno_stock[f]) != list(DNOS) or not uk.get(f) or not 0 < ukpn.get(f, 0) < uk[f]:
                raise CandidateError(f"licence-area shares for {f} are undefined (DNO stock {dno_stock[f]}, UK {uk.get(f)})")
            dno_share[f] = {d: dno_stock[f][d] / uk[f] for d in DNOS}     # fixed until a newer C1 quarter is the baseline
            share[f] = ukpn[f] / uk[f]

        progress("Allocating monthly registrations to licence areas and LSOAs")
        smmt = {m: {"BEV": entries[m]["bev_cars"] + entries[m]["bev_vans"],
                    "PHEV": entries[m]["phev_cars"] + entries[m]["phev_vans"]} for m in months}
        lsoa_frames, monthly = [], []
        for f in ("BEV", "PHEV"):
            # each month's UK registrations: largest remainder over EPN, LPN, SPN and the rest of the UK
            bucket_w = np.array([dno_share[f][d] for d in DNOS] + [1 - share[f]])
            per_dno = {d: [] for d in DNOS}
            for m in months:
                split = largest_remainder(bucket_w, smmt[m][f])
                for i, d in enumerate(DNOS):
                    per_dno[d].append(int(split[i]))
            for d in DNOS:
                bf = b[(b["EV_Type"] == f) & (b["DNO"] == d)].sort_values("LSOA21CD").reset_index(drop=True)
                w = bf["baseline_stock"].to_numpy(dtype=float)
                cum_lsoa, cum_d = np.zeros(len(bf), dtype=np.int64), 0
                for m, add in zip(months, per_dno[d]):
                    alloc = largest_remainder(w, add)         # within the licence area by C1 LSOA stock
                    if alloc.sum() != add or (alloc < 0).any():
                        raise CandidateError(f"allocation did not conserve {d} {f} {m}")
                    cum_lsoa = cum_lsoa + alloc
                    cum_d += add
                    fr = bf[["LSOA21CD", "DNO", "LAD22CD", "LAD22NM", "LAEP", "EV_Type", "baseline_stock"]].copy()
                    fr["period"] = m
                    fr["cumulative_addition"] = cum_lsoa
                    fr["nowcast_stock"] = fr["baseline_stock"] + cum_lsoa
                    lsoa_frames.append(fr)
                    monthly.append({"period": m, "EV_Type": f, "DNO": d, "smmt_uk_registrations": smmt[m][f],
                                    "share_of_uk_stock": dno_share[f][d], "additions": add, "cumulative_additions": cum_d,
                                    "baseline_stock": dno_stock[f][d], "nowcast_stock": dno_stock[f][d] + cum_d})
        dno_monthly = pd.DataFrame(monthly).sort_values(["period", "EV_Type", "DNO"]).reset_index(drop=True)
        monthly = []
        for (m, f), g in dno_monthly.groupby(["period", "EV_Type"]):
            monthly.append({"period": m, "EV_Type": f, "smmt_uk_registrations": int(g["smmt_uk_registrations"].iloc[0]),
                            "ukpn_share": share[f], "ukpn_additions": int(g["additions"].sum()),
                            "ukpn_cumulative_additions": int(g["cumulative_additions"].sum()),
                            "ukpn_baseline_stock": ukpn[f], "ukpn_nowcast_stock": ukpn[f] + int(g["cumulative_additions"].sum())})
        lsoa = pd.concat(lsoa_frames, ignore_index=True)
        lsoa = lsoa[["period", "LSOA21CD", "DNO", "LAD22CD", "LAD22NM", "LAEP", "EV_Type", "baseline_stock",
                     "cumulative_addition", "nowcast_stock"]].sort_values(["period", "EV_Type", "LSOA21CD"]).reset_index(drop=True)

        progress("Aggregating to DNO, LA and LAEP")
        geo = {}
        for name, cols in GEOS.items():
            g = lsoa.dropna(subset=[cols[0]]).groupby(cols + ["EV_Type", "period"], as_index=False)["nowcast_stock"].sum()
            g = g.rename(columns={"nowcast_stock": "value"})
            g["time_grain"], g["tech_type"] = "month", "EV"
            g["forecast_source"], g["forecast_scenario"] = FORECAST_SOURCE, FORECAST_SCENARIO
            g["dfes_pathway"], g["building_block_id"], g["metric"] = "", "", "stock"
            g["observation_source"] = "SMMT"
            g["period_end"] = g["period"].map(month_end)
            g["is_anchor"], g["is_interpolated"], g["source_year"] = False, False, ""
            g["anchor_date"] = month_end(baseline)
            geo[name] = g[cols + FORECAST_COLUMNS].sort_values(["period", "EV_Type"] + cols).reset_index(drop=True)

        summary = pd.DataFrame(monthly).sort_values(["period", "EV_Type"]).reset_index(drop=True)
        return {"baseline": baseline, "months": months, "month_info": month_info, "share": share, "uk_stock": uk,
                "ukpn_baseline": ukpn, "live_check": live_check, "smmt": smmt, "lsoa": lsoa, "geo": geo,
                "summary": summary, "lsoa_baseline": b, "dno_share": dno_share, "dno_monthly": dno_monthly,
                "sources": srcs}

    def qa(self, r: Dict, live_before: Dict) -> pd.DataFrame:
        lsoa, geo, s = r["lsoa"], r["geo"], r["summary"]
        checks = []

        def add(cid, ok, detail):
            checks.append({"check": cid, "passed": bool(ok), "detail": detail})

        add("baseline_reproduces_live_actuals", True, f"C1 LSOA stock at {r['baseline']} equals live DNO/LA/LAEP actuals "
            + json.dumps(r["live_check"]))
        add("months_contiguous_from_baseline", r["months"][0] == str(pd.Period(r["baseline"], freq="M") + 1),
            f"{r['months'][0]}..{r['months'][-1]}; first month without SMMT data: {r['month_info']['first_missing_month']}")
        horizon_ok = all(p <= r["months"][-1] for p in lsoa["period"].unique())
        add("stops_at_first_missing_smmt_month", horizon_ok,
            f"no estimate after {r['months'][-1]}; months after a gap (reported, not used): "
            f"{r['month_info']['ignored_after_gap'] or 'none'}")
        add("unique_lsoa_keys", not lsoa.duplicated(["period", "LSOA21CD", "EV_Type"]).any(), f"{len(lsoa):,} LSOA rows")
        add("lsoa_coverage_equals_c1_baseline", lsoa.groupby("period")["LSOA21CD"].nunique().eq(
            r["lsoa_baseline"]["LSOA21CD"].nunique()).all(), f"{r['lsoa_baseline']['LSOA21CD'].nunique():,} LSOAs every month")
        add("non_negative_additions", (lsoa["cumulative_addition"] >= 0).all(), "")
        mono = lsoa.sort_values("period").groupby(["LSOA21CD", "EV_Type"])["cumulative_addition"].apply(
            lambda x: x.is_monotonic_increasing).all()
        add("stock_non_decreasing_per_lsoa", mono, "")
        cons = lsoa.groupby(["period", "EV_Type"])["cumulative_addition"].sum()
        exp = s.set_index(["period", "EV_Type"])["ukpn_cumulative_additions"]
        add("lsoa_additions_conserve_ukpn_total", (cons == exp).all(), "per month and fuel")
        dm = r["dno_monthly"]
        by_dno = lsoa.groupby(["period", "EV_Type", "DNO"])["cumulative_addition"].sum()
        add("lsoa_additions_conserve_each_licence_area",
            (by_dno == dm.set_index(["period", "EV_Type", "DNO"])["cumulative_additions"]).all(),
            "per month, fuel and licence area")
        split_ok = all(int(g["additions"].sum()) <= int(g["smmt_uk_registrations"].iloc[0]) and
                       abs(float(g["additions"].sum()) - g["smmt_uk_registrations"].iloc[0] * g["share_of_uk_stock"].sum()) <= 1
                       for _, g in dm.groupby(["period", "EV_Type"]))
        add("licence_area_split_of_uk_registrations", split_ok,
            "EPN+LPN+SPN additions = UK registrations x fixed shares, within largest-remainder rounding (<=1 vehicle)")
        add("shares_fixed_at_baseline", dm.groupby(["EV_Type", "DNO"])["share_of_uk_stock"].nunique().eq(1).all(),
            json.dumps({f: {d: round(v, 6) for d, v in s.items()} for f, s in r["dno_share"].items()}))
        for name, cols in GEOS.items():
            tot = geo[name].groupby(["period", "EV_Type"])["value"].sum()
            mapped = lsoa[lsoa[cols[0]].notna()]
            want = mapped.groupby(["period", "EV_Type"])["nowcast_stock"].sum()
            unassigned = lsoa[lsoa[cols[0]].isna()]["LSOA21CD"].nunique()
            full = s.set_index(["period", "EV_Type"])["ukpn_nowcast_stock"]
            ok = (tot == want).all() and (unassigned or (tot == full).all())
            add(f"{name}_reconciles_to_ukpn", ok,
                f"{name.upper()} totals equal UKPN nowcast stock" if not unassigned else
                f"{name.upper()} totals equal the {mapped['LSOA21CD'].nunique():,} LSOAs with a {cols[0]}; "
                f"{unassigned:,} LSOAs have none and are excluded exactly as in the live actuals")
            add(f"{name}_unique_keys", not geo[name].duplicated(cols[:1] + ["EV_Type", "period"]).any(), "")
        add("bev_phev_separate", set(s["EV_Type"]) == {"BEV", "PHEV"}, "BEV and PHEV allocated independently")
        add("share_in_range", all(0 < v < 1 for v in r["share"].values()),
            json.dumps({k: round(v, 6) for k, v in r["share"].items()}))
        add("no_actual_rows_in_candidate", all((g["forecast_source"] == FORECAST_SOURCE).all() for g in geo.values()),
            "candidate rows are SMMT_NOWCAST only; C1 actuals are not copied or modified")
        integ = self._p(INTEGRATION_REL)
        live_after = {n: _sha_file(os.path.join(integ, n)) for n in live_before}
        add("live_integration_untouched", live_after == live_before, "hashes of live actuals, forecasts and metadata")
        return pd.DataFrame(checks)

    # ------------------------------------------------------------------ candidates
    def generate(self, progress: Callable[[str], None] = lambda s: None, job_id: Optional[str] = None) -> Dict:
        """Create a candidate. Returns its manifest (status VALIDATED or FAILED)."""
        os.makedirs(self.candidates_dir, exist_ok=True)
        progress("Reading inputs")
        inp = self.inputs()
        created = _now()
        last = None
        tmp = os.path.join(self.candidates_dir, f".tmp-{uuid.uuid4().hex[:8]}")
        try:
            r = self.calculate(inp, progress)
            last = r["months"][-1]
            cid = f"SMMT-{r['baseline']}-to-{last}-{inp['fingerprint'][:8]}-{created.replace(':', '').replace('-', '')}"
            progress("Running QA")
            qa = self.qa(r, inp["live_integration_sha256"])
            os.makedirs(os.path.join(tmp, "preview"))
            progress("Writing candidate files")
            r["lsoa"].to_csv(os.path.join(tmp, "nowcast_lsoa.csv"), index=False, lineterminator="\n")
            for name, g in r["geo"].items():
                g.to_csv(os.path.join(tmp, f"smmt_nowcast_{name}.csv"), index=False, lineterminator="\n")
                live = pd.read_csv(self._p(f"{INTEGRATION_REL}/monthly_forecast_{name}.csv"), dtype=str, keep_default_na=False)
                live = live[live["forecast_source"] != FORECAST_SOURCE]       # a preview never carries older SMMT rows
                prev = pd.concat([live, g.astype(str)[live.columns]], ignore_index=True)
                prev.to_csv(os.path.join(tmp, "preview", f"monthly_forecast_{name}.csv"), index=False, lineterminator="\n")
            r["summary"].to_csv(os.path.join(tmp, "ukpn_monthly_summary.csv"), index=False, lineterminator="\n")
            r["dno_monthly"].to_csv(os.path.join(tmp, "dno_monthly_summary.csv"), index=False, lineterminator="\n")
            qa.to_csv(os.path.join(tmp, "qa_checks.csv"), index=False, lineterminator="\n")
            status = "VALIDATED" if qa["passed"].all() else "FAILED"
            manifest = self._manifest(inp, r, qa, created, job_id, status, cid, tmp)
            json.dump(manifest, open(os.path.join(tmp, "manifest.json"), "w", encoding="utf-8"), indent=1)
            final = os.path.join(self.candidates_dir, cid)
            os.replace(tmp, final)
            progress("Done")
            return manifest
        except Exception as e:  # noqa: BLE001 - every failure is recorded, nothing is published
            shutil.rmtree(tmp, ignore_errors=True)
            cid = f"SMMT-FAILED-{inp['fingerprint'][:8]}-{created.replace(':', '').replace('-', '')}"
            manifest = {"candidate_id": cid, "method_id": METHOD_ID, "status": "FAILED", "created_utc": created,
                        "job_id": job_id, "inputs": inp,
                        "errors": [str(e) if isinstance(e, CandidateError) else f"{type(e).__name__}: {e}"],
                        "published": False}
            os.makedirs(os.path.join(self.candidates_dir, cid))
            json.dump(manifest, open(os.path.join(self.candidates_dir, cid, "manifest.json"), "w", encoding="utf-8"), indent=1)
            progress("Failed")
            return manifest

    def _manifest(self, inp, r, qa, created, job_id, status, cid, folder) -> Dict:
        files = {rel: _sha_file(os.path.join(folder, *rel.split("/"))) for rel in DATA_FILES + PREVIEW_FILES}
        s = r["summary"]
        last = r["months"][-1]
        by_month = {m: {f: int(s[(s.period == m) & (s.EV_Type == f)]["ukpn_nowcast_stock"].iloc[0]) for f in ("BEV", "PHEV")}
                    for m in r["months"]}
        dno = r["geo"]["dno"]
        latest_dno = {d: {f: int(v) for f, v in g.set_index("EV_Type")["value"].items()}
                      for d, g in dno[dno.period == last].groupby("DNO")}
        warnings = []
        if r["month_info"]["ignored_after_gap"]:
            warnings.append(f"SMMT months after a gap were not used: {r['month_info']['ignored_after_gap']}")
        man = {
            "candidate_id": cid, "method_id": METHOD_ID, "status": status, "created_utc": created, "job_id": job_id,
            "label": "SMMT provisional EV nowcast (not an observation)",
            "baseline_period": r["baseline"], "baseline_source": "C1 EV actuals (live release)",
            "nowcast_months": r["months"], "latest_nowcast_month": last, "month_info": r["month_info"],
            "ukpn_share": r["share"], "dno_share_of_uk_stock": r["dno_share"],
            "c1_release_sources": r["sources"],
            "smmt_entry_notes": {m: inp["smmt_notes"].get(m, "") for m in r["months"]},
            "uk_stock_at_baseline": r["uk_stock"], "ukpn_baseline_stock": r["ukpn_baseline"],
            "smmt_uk_registrations": r["smmt"], "ukpn_stock_by_month": by_month, "dno_stock_latest_month": latest_dno,
            "coverage": {"lsoa": int(r["lsoa_baseline"]["LSOA21CD"].nunique()), "dno": int(dno["DNO"].nunique()),
                         "la": int(r["geo"]["la"]["LAD22CD"].nunique()), "laep": int(r["geo"]["laep"]["LAEP"].nunique())},
            "qa_passed": int(qa["passed"].sum()), "qa_total": int(len(qa)),
            "errors": [f"{c}: {d}" for c, d in qa.loc[~qa["passed"], ["check", "detail"]].itertuples(index=False)],
            "warnings": warnings, "inputs": inp, "files_sha256": files,
            "data_sha256": _sha_json({k: v for k, v in files.items() if k in DATA_FILES}),
            "published": False,
            "publication": "Not published. The dashboard forecast files and dashboard.html are pinned by the C1 release "
                           "protections; publishing requires a separately approved, governed change.",
        }
        man["diff_vs_previous"] = self._diff_vs_previous(man)
        man["seal_sha256"] = _sha_json({k: v for k, v in man.items() if k != "seal_sha256"})
        return man

    def _diff_vs_previous(self, man: Dict) -> Optional[Dict]:
        prev = [m for m in self.list_candidates() if m.get("status") == "VALIDATED" and m["candidate_id"] != man["candidate_id"]]
        if not prev:
            return None
        p = prev[0]
        months = sorted(set(p.get("ukpn_stock_by_month", {})) | set(man["ukpn_stock_by_month"]))
        rows = []
        for m in months:
            a, b = p.get("ukpn_stock_by_month", {}).get(m), man["ukpn_stock_by_month"].get(m)
            for f in ("BEV", "PHEV"):
                rows.append({"period": m, "EV_Type": f, "previous": a and a[f], "this": b and b[f],
                             "change": (b[f] - a[f]) if a and b else None})
        key = lambda e: f"{e['year']}-{e['month']:02d}"
        old = {key(e): e for e in p["inputs"]["smmt_entries"]}
        new = {key(e): e for e in man["inputs"]["smmt_entries"]}
        changed_entries = sorted(m for m in set(old) | set(new) if old.get(m) != new.get(m))
        return {"previous_candidate_id": p["candidate_id"], "ukpn_stock": rows,
                "smmt_months_changed_or_added": changed_entries,
                "baseline_changed": p.get("baseline_period") != man["baseline_period"],
                "same_data": p.get("data_sha256") == man["data_sha256"]}

    def publication_manifest(self, cid: str) -> Dict:
        """Manifest for the published SMMT files (smmt_nowcast_{dno,la,laep}.csv). The candidate preview serves it, and
        data_manager/smmt_publish.py writes it, so preview and publication share one contract."""
        man = self.load(cid)
        folder = self.folder(cid)
        return {
            "schema": PUBLICATION_SCHEMA, "candidate_id": cid, "candidate_seal_sha256": man["seal_sha256"],
            "method_id": man["method_id"], "label": "SMMT provisional EV nowcast (not an observation)",
            "baseline_period": man["baseline_period"], "nowcast_months": man["nowcast_months"],
            "latest_nowcast_month": man["latest_nowcast_month"],
            "dno_share_of_uk_stock": man.get("dno_share_of_uk_stock"), "uk_stock_at_baseline": man.get("uk_stock_at_baseline"),
            "c1_release_sources": man.get("c1_release_sources"), "input_fingerprint": man["inputs"]["fingerprint"],
            "files": {n: _sha_file(os.path.join(folder, n)) for n in PUBLISHED_FILES},
        }

    def list_candidates(self) -> List[Dict]:
        if not os.path.isdir(self.candidates_dir):
            return []
        out = []
        for d in os.listdir(self.candidates_dir):
            p = os.path.join(self.candidates_dir, d, "manifest.json")
            if d.startswith("SMMT-") and os.path.exists(p):
                out.append(json.load(open(p, encoding="utf-8")))
        return sorted(out, key=lambda m: m.get("created_utc", ""), reverse=True)

    def folder(self, cid: str) -> str:
        if not cid.startswith("SMMT-") or os.sep in cid or "/" in cid or ".." in cid:
            raise CandidateError("invalid candidate id")
        return os.path.join(self.candidates_dir, cid)

    def load(self, cid: str) -> Dict:
        return json.load(open(os.path.join(self.folder(cid), "manifest.json"), encoding="utf-8"))

    def verify(self, cid: str) -> Tuple[bool, str]:
        man = self.load(cid)
        if man.get("status") == "FAILED" and "seal_sha256" not in man:
            return False, "failed candidate (no data)"
        if _sha_json({k: v for k, v in man.items() if k != "seal_sha256"}) != man["seal_sha256"]:
            return False, "manifest seal does not match"
        bad = [rel for rel, h in man["files_sha256"].items()
               if not os.path.exists(os.path.join(self.folder(cid), *rel.split("/"))) or
               _sha_file(os.path.join(self.folder(cid), *rel.split("/"))) != h]
        return (not bad), (f"modified or missing: {bad}" if bad else f"seal and {len(man['files_sha256'])} files verified")

    def status(self, cid: str) -> Dict:
        """Lifecycle state: VALIDATED / STALE / REJECTED / FAILED / TAMPERED. Never PUBLISHED in this build."""
        man = self.load(cid)
        rej = os.path.join(self.folder(cid), "rejection.json")
        if man.get("status") == "FAILED":
            return {"state": "FAILED", "detail": "; ".join(man.get("errors", [])), "published": False}
        ok, detail = self.verify(cid)
        if not ok:
            return {"state": "TAMPERED", "detail": detail, "published": False}
        if os.path.exists(rej):
            r = json.load(open(rej, encoding="utf-8"))
            return {"state": "REJECTED", "detail": f"{r['reason']} ({r['rejected_utc']})", "published": False}
        if man.get("method_id") != METHOD_ID:
            return {"state": "STALE", "detail": f"generated with superseded method {man.get('method_id')}; generate a new "
                                                f"candidate ({METHOD_ID})", "published": False}
        cur = self.inputs()["fingerprint"]
        if cur != man["inputs"]["fingerprint"]:
            return {"state": "STALE", "detail": "inputs changed since generation (SMMT entries, C1 baseline, VEH0135 "
                                                "or live integration files); generate a new candidate", "published": False}
        return {"state": "VALIDATED", "detail": detail, "published": False}

    def reject(self, cid: str, reason: str) -> Dict:
        if not reason or not reason.strip():
            raise CandidateError("a rejection reason is required")
        if self.load(cid).get("status") == "FAILED":
            raise CandidateError("failed candidates are already unusable")
        rec = {"candidate_id": cid, "reason": reason.strip(), "rejected_utc": _now()}
        p = os.path.join(self.folder(cid), "rejection.json")
        if os.path.exists(p):
            raise CandidateError("candidate already rejected")
        json.dump(rec, open(p, "w", encoding="utf-8"), indent=1)
        return rec
