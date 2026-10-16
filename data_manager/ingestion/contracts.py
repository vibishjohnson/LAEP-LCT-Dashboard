"""
DfT dataset contracts, identification, validation and profiling.

The contracts describe the real published tables (checked against the DfT releases for 2026 Q1
and 2026 Q2), not generic columns:

  VEH0135  df_VEH0135.csv   LSOA21CD, LSOA21NM, Fuel, Keepership, <quarters newest first>
  VEH0142  veh0142.ods      sheet VEH0142, 4 title rows, then Units, BodyType, Fuel, Keepership,
                            ONS Sort, ONS Code, ONS Geography, <quarters>; Units = Number
  VEH0105  veh0105.ods      sheet VEH0105, same layout; Units = Thousands
  VEH0125  df_VEH0125.csv   LSOA21CD, LSOA21NM, BodyType, Keepership, LicenceStatus, <quarters>

A single streaming pass validates every row, builds the UKPN-scoped profile used for the
comparison with production, writes the canonical table (header + data rows only, numbers
without display formatting, suppression markers kept) and keeps a short preview.
"""

import csv
import hashlib
import os
import re
from dataclasses import dataclass, field

from . import readers

QUARTER_RE = re.compile(r"^(\d{4}) Q([1-4])$")
MARKERS = {"[c]", "[x]", "[z]", "[low]", "[u]", "[w]"}
UKPN_DNOS = ("EPN", "LPN", "SPN")
C1_FUELS = {"Battery electric": "BEV", "Plug-in hybrid electric (petrol)": "PHEV", "Plug-in hybrid electric (diesel)": "PHEV"}
C1_KEEPERSHIP = ("Total", "Private", "Company")
HEADER_SEARCH_ROWS = 40


@dataclass(frozen=True)
class Contract:
    dataset: str
    title: str
    role: str
    production_file: str              # file name in ev/, the current approved source
    id_columns: tuple                 # exact leading header cells
    key_columns: tuple                # unique row key
    geo_column: str
    geo_pattern: str                  # valid geography codes
    geo_placeholders: tuple = ()      # accepted non-geographic codes
    units: str = None                 # required value of the Units column
    sheet_names: tuple = ()           # expected worksheet names (ODS/XLSX)
    required_values: dict = field(default_factory=dict)   # column -> values that must appear
    integer_values: bool = True
    min_rows: int = 1000
    min_quarters: int = 8
    title_keywords: tuple = ()


CONTRACTS = {
    "VEH0135": Contract(
        dataset="VEH0135",
        title="Licensed plug-in and low-emission vehicles by fuel, keepership and LSOA",
        role="Direct input to Method C1 (BEV/PHEV by keepership per LSOA, quarters 2025 Q1-2026 Q1 in the "
             "current release) and to scripts/01_ev_processing.py (Total keepership).",
        production_file="df_VEH0135.csv",
        id_columns=("LSOA21CD", "LSOA21NM", "Fuel", "Keepership"),
        key_columns=("LSOA21CD", "Fuel", "Keepership"),
        geo_column="LSOA21CD",
        geo_pattern=r"^(E01|W01|S01)\d{6}$|^N21\d{6}$|^95[A-Z]{2}\d{2}[A-Z0-9]\d$",
        geo_placeholders=("Miscellaneous",),
        required_values={"Fuel": tuple(C1_FUELS), "Keepership": C1_KEEPERSHIP},
        min_rows=200_000,
    ),
    "VEH0142": Contract(
        dataset="VEH0142",
        title="Licensed plug-in vehicles by body type, fuel, keepership and local authority",
        role="Input to scripts/01_ev_processing.py (LAD body-type proportions for lsoa_ev_bodytype.csv, used by the "
             "SMMT nowcast workflow) and diagnostics. Not read by Method C1.",
        production_file="VEH0142.csv",
        id_columns=("Units", "BodyType", "Fuel", "Keepership", "ONS Sort", "ONS Code", "ONS Geography"),
        key_columns=("BodyType", "Fuel", "Keepership", "ONS Sort"),
        geo_column="ONS Code",
        geo_pattern=r"^[EWSNK]\d{8}$",
        geo_placeholders=("[z]",),
        units="Number",
        sheet_names=("VEH0142",),
        required_values={"Fuel": ("Battery electric", "Plug-in hybrid electric (petrol)", "Total"),
                         "BodyType": ("Cars", "Light goods vehicles", "Total"), "Keepership": C1_KEEPERSHIP},
        min_rows=20_000,
        title_keywords=("VEH0142", "plug-in"),
    ),
    "VEH0105": Contract(
        dataset="VEH0105",
        title="Licensed vehicles by body type, fuel, keepership and local authority (thousands)",
        role="No production consumer found. Listed by the legacy data manager (dft_update.py) and described in "
             "METHODOLOGY.md for a total-vehicle van share that the current scripts do not implement.",
        production_file="VEH0105.csv",
        id_columns=("Units", "BodyType", "Fuel", "Keepership", "ONS Sort", "ONS Code", "ONS Geography"),
        key_columns=("BodyType", "Fuel", "Keepership", "ONS Sort"),
        geo_column="ONS Code",
        geo_pattern=r"^[EWSNK]\d{8}$",
        geo_placeholders=("[z]",),
        units="Thousands",
        sheet_names=("VEH0105",),
        required_values={"Fuel": ("Petrol", "Diesel", "Total"), "BodyType": ("Cars", "Light goods vehicles", "Total"),
                         "Keepership": C1_KEEPERSHIP},
        integer_values=False,
        min_rows=30_000,
        title_keywords=("VEH0105",),
    ),
    "VEH0125": Contract(
        dataset="VEH0125",
        title="Vehicles by body type, keepership, licence status and LSOA",
        role="No production consumer found. Listed by the legacy data manager; accepted for completeness.",
        production_file="df_VEH0125.csv",
        id_columns=("LSOA21CD", "LSOA21NM", "BodyType", "Keepership", "LicenceStatus"),
        key_columns=("LSOA21CD", "BodyType", "Keepership", "LicenceStatus"),
        geo_column="LSOA21CD",
        geo_pattern=r"^(E01|W01|S01)\d{6}$|^N21\d{6}$|^95[A-Z]{2}\d{2}[A-Z0-9]\d$",
        geo_placeholders=("Miscellaneous",),
        required_values={"BodyType": ("Cars",), "LicenceStatus": ("Licensed",)},
        min_rows=500_000,
    ),
}


def quarter_end(label):
    m = QUARTER_RE.match(label)
    y, q = int(m.group(1)), int(m.group(2))
    return f"{y}-{('03-31', '06-30', '09-30', '12-31')[q - 1]}"


def quarter_index(label):
    m = QUARTER_RE.match(label)
    return int(m.group(1)) * 4 + int(m.group(2)) - 1


# ------------------------------------------------------------------ UKPN scope lookup
class UkpnScope:
    """LSOA21CD -> DNO and the set of local authorities with any UKPN LSOA (lookups/LSOA to DNO.csv)."""

    def __init__(self, lookup_path):
        self.lsoa_dno, self.lad_dnos = {}, {}
        self.source = lookup_path
        if not lookup_path or not os.path.exists(lookup_path):
            return
        with open(lookup_path, newline="", encoding="utf-8-sig") as f:
            for row in csv.DictReader(f):
                dno = (row.get("Majority Licence area") or "").strip()
                code = (row.get("LSOA21CD") or "").strip()
                if code and dno in UKPN_DNOS:
                    self.lsoa_dno[code] = dno
                    lad = (row.get("LAD22CD") or "").strip()
                    if lad:
                        self.lad_dnos.setdefault(lad, set()).add(dno)

    @property
    def available(self):
        return bool(self.lsoa_dno)


# ------------------------------------------------------------------ header detection
def find_header(rows_head, contract):
    """Index of the header row within the first rows, or None."""
    want = list(contract.id_columns)
    for i, r in enumerate(rows_head[:HEADER_SEARCH_ROWS]):
        cells = [c.strip() for c in r]
        if cells[:len(want)] == want and any(QUARTER_RE.match(c) for c in cells[len(want):]):
            return i
    return None


def identify(head_by_sheet, filename=""):
    """Score every contract against the first rows of every sheet. Returns candidates, best first."""
    out = []
    for sheet, head in head_by_sheet.items():
        title_text = " ".join(" ".join(r) for r in head[:8]).lower()
        for c in CONTRACTS.values():
            idx = find_header(head, c)
            if idx is None:
                continue
            score, reasons = 50, [f"header row {idx + 1} matches the {c.dataset} columns"]
            if c.sheet_names and sheet in c.sheet_names:
                score += 20
                reasons.append(f"worksheet named {sheet}")
            if c.title_keywords and any(k.lower() in title_text for k in c.title_keywords):
                score += 15
                reasons.append("title rows name the table")
            if c.dataset.lower() in filename.lower():
                score += 5
                reasons.append("file name mentions the table (hint only)")
            out.append({"dataset": c.dataset, "sheet": sheet, "header_row": idx, "score": score, "reasons": reasons})
    out.sort(key=lambda d: -d["score"])
    # Ambiguity: the ODS tables VEH0142 and VEH0105 share a header; the Units / sheet / title decide.
    return out


# ------------------------------------------------------------------ validation + profile (one pass)
class Check:
    def __init__(self):
        self.items = []

    def add(self, name, ok, detail, blocking=True):
        self.items.append({"check": name, "status": "PASS" if ok else ("FAIL" if blocking else "WARN"),
                           "blocking": blocking, "detail": detail})
        return ok

    @property
    def errors(self):
        return [i for i in self.items if i["status"] == "FAIL"]

    @property
    def warnings(self):
        return [i for i in self.items if i["status"] == "WARN"]


def _num(v):
    """Parse a numeric cell; returns (value or None, is_marker)."""
    s = v.strip()
    if not s:
        return None, False
    if s in MARKERS:
        return None, True
    if s.isdigit():
        return int(s), False
    try:
        return float(s.replace(",", "")), False
    except ValueError:
        return None, None


def validate_and_profile(rows, contract, scope, canonical_path=None, preview_rows=20, max_rows=5_000_000, progress=None):
    """Consume the row iterator once. Returns (checks, profile, preview, canonical_sha256)."""
    ck = Check()
    head = []
    for r in rows:
        head.append(r)
        if len(head) >= HEADER_SEARCH_ROWS or find_header(head, contract) is not None:
            break
    hidx = find_header(head, contract)
    if not ck.add("header_found", hidx is not None,
                  f"header row {hidx + 1}" if hidx is not None else
                  f"no row in the first {HEADER_SEARCH_ROWS} matches {list(contract.id_columns)} + quarter columns"):
        return ck, {}, {}, None
    header = [c.strip() for c in head[hidx]]
    while header and header[-1] == "":
        header.pop()
    title_rows = [" ".join(c for c in r if c) for r in head[:hidx] if any(r)]
    width = len(header)
    nid = len(contract.id_columns)
    qcols = header[nid:]
    bad_q = [q for q in qcols if not QUARTER_RE.match(q)]
    ck.add("quarter_columns_valid", not bad_q, f"{len(qcols)} quarter columns" if not bad_q else f"unrecognised columns {bad_q[:5]}")
    good_q = [q for q in qcols if QUARTER_RE.match(q)]
    idxs = [quarter_index(q) for q in good_q]
    contiguous = bool(idxs) and all(a - b == 1 for a, b in zip(idxs, idxs[1:]))
    ck.add("quarter_sequence", contiguous and len(good_q) >= contract.min_quarters,
           f"{good_q[0]} back to {good_q[-1]}, newest first, no gaps" if contiguous and good_q else
           f"quarters must be contiguous, newest first, at least {contract.min_quarters}: {good_q[:6]}...")
    if not good_q:
        return ck, {}, {}, None
    latest = good_q[0]
    col = {h: i for i, h in enumerate(header)}
    key_idx = [col[k] for k in contract.key_columns]
    geo_i = col[contract.geo_column]
    geo_re = re.compile(contract.geo_pattern)
    q_pos = [col[q] for q in good_q]
    latest_pos = q_pos[0]
    prev_pos = q_pos[1] if len(q_pos) > 1 else None
    is_lsoa = contract.geo_column == "LSOA21CD"

    seen_keys, dup_keys, dup_examples = set(), 0, []
    rows_n, blank_rows, wide_rows, bad_geo, placeholders = 0, 0, 0, 0, 0
    bad_geo_examples, bad_num_examples, unit_bad = [], [], 0
    bad_num = neg_num = nonint = markers = 0
    latest_numeric = prev_numeric = 0
    categories = {k: {} for k in ("Units", "BodyType", "Fuel", "Keepership", "LicenceStatus") if k in col}
    ukpn_rows = nonukpn_rows = 0
    # profile accumulators
    sums = {}           # (scope, series) -> [per quarter]
    ukpn_lsoas_latest = set()
    preview = []
    out_f = writer = None
    if canonical_path:
        out_f = open(canonical_path, "w", newline="", encoding="utf-8")
        writer = csv.writer(out_f, lineterminator="\n")
        writer.writerow(header)

    def acc(scope_key, series, values):
        lst = sums.get((scope_key, series))
        if lst is None:
            lst = sums[(scope_key, series)] = [0.0] * len(good_q)
        for j, v in enumerate(values):
            if v is not None:
                lst[j] += v

    try:
        for r in rows:
            if not r or not any(c.strip() for c in r):
                blank_rows += 1
                continue
            rows_n += 1
            if rows_n > max_rows:
                ck.add("row_limit", False, f"more than {max_rows:,} data rows")
                break
            if len(r) > width:
                if any(c.strip() for c in r[width:]):
                    wide_rows += 1
                r = r[:width]
            elif len(r) < width:
                r = r + [""] * (width - len(r))
            # keys
            k = hash(tuple(r[i] for i in key_idx))
            if k in seen_keys:
                dup_keys += 1
                if len(dup_examples) < 5:
                    dup_examples.append([r[i] for i in key_idx])
            else:
                seen_keys.add(k)
            # categories
            for cname, counter in categories.items():
                v = r[col[cname]]
                counter[v] = counter.get(v, 0) + 1
            if "Units" in col and contract.units and r[col["Units"]] != contract.units:
                unit_bad += 1
            # geography (kept exactly as published; only validated)
            g = r[geo_i]
            if g in contract.geo_placeholders:
                placeholders += 1
            elif not geo_re.match(g):
                bad_geo += 1
                if len(bad_geo_examples) < 5:
                    bad_geo_examples.append(g)
            # numbers
            vals = []
            for j, p in enumerate(q_pos):
                v, mk = _num(r[p])
                if mk is None:
                    bad_num += 1
                    if len(bad_num_examples) < 5:
                        bad_num_examples.append(f"{good_q[j]}={r[p]!r}")
                elif mk:
                    markers += 1
                elif v is not None:
                    if v < 0:
                        neg_num += 1
                    if contract.integer_values and isinstance(v, float) and not v.is_integer():
                        nonint += 1
                vals.append(v)
            if vals[0] is not None:
                latest_numeric += 1
            if prev_pos is not None and vals[1] is not None:
                prev_numeric += 1
            # UKPN scope + profile series
            if is_lsoa:
                dno = scope.lsoa_dno.get(g)
            else:
                dnos = scope.lad_dnos.get(g)
                dno = "UKPN_LA" if dnos else None
            if dno:
                ukpn_rows += 1
            else:
                nonukpn_rows += 1
            _profile_row(contract, r, col, vals, g, dno, acc, ukpn_lsoas_latest)
            if writer:
                writer.writerow(r)
            if len(preview) < preview_rows:
                preview.append(r)
            if progress and rows_n % 5000 == 0:
                progress(rows_n)
    finally:
        if out_f:
            out_f.close()

    ck.add("minimum_rows", rows_n >= contract.min_rows, f"{rows_n:,} data rows (minimum {contract.min_rows:,})")
    ck.add("unique_keys", dup_keys == 0, f"{len(contract.key_columns)}-part key {list(contract.key_columns)} unique" if not dup_keys
           else f"{dup_keys} duplicate keys, e.g. {dup_examples}")
    if contract.units:
        ck.add("units", unit_bad == 0, f"Units = {contract.units} on every row" if not unit_bad else
               f"{unit_bad} rows with Units other than {contract.units} (VEH0142 is Number, VEH0105 is Thousands)")
    ck.add("row_width", wide_rows == 0, "no values beyond the header" if not wide_rows else f"{wide_rows} rows have values beyond the last header column")
    ck.add("geography_codes", bad_geo == 0,
           f"all {contract.geo_column} values valid ({placeholders} accepted placeholder rows {list(contract.geo_placeholders)})" if not bad_geo
           else f"{bad_geo} invalid {contract.geo_column} values, e.g. {bad_geo_examples}")
    ck.add("numeric_cells", bad_num == 0 and neg_num == 0,
           f"all quarter cells numeric or DfT markers ({markers:,} suppressed/marker cells)" if not (bad_num or neg_num)
           else f"{bad_num} non-numeric and {neg_num} negative quarter cells, e.g. {bad_num_examples}")
    if contract.integer_values:
        ck.add("integer_counts", nonint == 0, "all counts are whole numbers" if not nonint else f"{nonint} non-integer counts")
    for cname, wanted in contract.required_values.items():
        have = categories.get(cname, {})
        missing = [w for w in wanted if w not in have]
        ck.add(f"required_{cname.lower()}_values", not missing, f"{cname} includes {list(wanted)}" if not missing else f"{cname} missing {missing}")
    complete = latest_numeric > 0 and (prev_pos is None or latest_numeric >= 0.5 * prev_numeric)
    ck.add("latest_quarter_populated", complete,
           f"{latest}: {latest_numeric:,} numeric cells (previous quarter {prev_numeric:,})")
    if scope.available:
        ck.add("ukpn_scope_present", ukpn_rows > 0, f"{ukpn_rows:,} rows in UKPN scope (EPN/LPN/SPN), {nonukpn_rows:,} outside")
    if is_lsoa and scope.available:
        n_ukpn = len(scope.lsoa_dno)
        cov = len(ukpn_lsoas_latest)
        ck.add("ukpn_lsoa_coverage", cov >= 0.9 * n_ukpn,
               f"{cov:,} of {n_ukpn:,} UKPN LSOAs have plug-in stock rows in {latest}", blocking=False)

    profile = {
        "dataset": contract.dataset, "header_row": hidx + 1, "title_rows": title_rows[:6], "columns": width,
        "id_columns": list(contract.id_columns), "quarters": good_q, "latest_quarter": latest,
        "period_end": quarter_end(latest), "rows": rows_n, "blank_rows_skipped": blank_rows,
        "marker_cells": markers, "placeholder_geo_rows": placeholders,
        "categories": {k: dict(sorted(v.items(), key=lambda kv: -kv[1])[:40]) for k, v in categories.items()},
        "scope": {"field": f"{contract.geo_column} via {os.path.basename(scope.source or '')} "
                           f"({'LSOA21CD -> Majority Licence area' if is_lsoa else 'LAD22CD of UKPN LSOAs'})",
                  "total_rows": rows_n, "ukpn_rows": ukpn_rows, "non_ukpn_rows": nonukpn_rows,
                  "ukpn_lsoas_with_stock_latest": len(ukpn_lsoas_latest) if is_lsoa else None},
        "series": {f"{s}|{name}": dict(zip(good_q, [round(x, 6) for x in vals])) for (s, name), vals in sums.items()},
    }
    canonical_sha = _file_sha256(canonical_path) if canonical_path and not ck.errors else None
    return ck, profile, {"header": header, "rows": preview}, canonical_sha


def _file_sha256(path):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def _profile_row(contract, r, col, vals, g, dno, acc, ukpn_lsoas_latest):
    """Comparison series: the C1-relevant totals for VEH0135, headline totals for the others."""
    d = contract.dataset
    if d == "VEH0135":
        fuel = C1_FUELS.get(r[col["Fuel"]])
        keep = r[col["Keepership"]]
        if fuel and keep in C1_KEEPERSHIP:
            acc("ALL", f"{fuel}_{keep}", vals)
            if dno:
                acc(dno, f"{fuel}_{keep}", vals)
                if keep == "Total" and vals[0]:
                    ukpn_lsoas_latest.add(g)
            if g == "Miscellaneous":
                acc("MISCELLANEOUS", f"{fuel}_{keep}", vals)
    elif d in ("VEH0142", "VEH0105"):
        body, fuel, keep = r[col["BodyType"]], r[col["Fuel"]], r[col["Keepership"]]
        if keep == "Total" and body in ("Total", "Cars", "Light goods vehicles"):
            if g in ("K02000001", "K03000001"):
                acc({"K02000001": "UK", "K03000001": "GB"}[g], f"{body}|{fuel}", vals)
            elif dno:
                acc("UKPN_LAs", f"{body}|{fuel}", vals)
    elif d == "VEH0125":
        if r[col["LicenceStatus"]] == "Licensed" and r[col["Keepership"]] == "Total":
            acc("ALL", r[col["BodyType"]], vals)
            if dno:
                acc(dno, r[col["BodyType"]], vals)


# ------------------------------------------------------------------ comparison with production
def compare_profiles(candidate, production):
    """Basic numerical comparison: new quarter, restatement of the production latest quarter, rows, categories."""
    if not production:
        return {"available": False}
    cq, pq = candidate["latest_quarter"], production["latest_quarter"]
    out = {"available": True, "production_latest": pq, "candidate_latest": cq,
           "quarter_relation": ("NEWER" if quarter_index(cq) > quarter_index(pq) else
                                "SAME" if cq == pq else "OLDER"),
           "rows": {"production": production["rows"], "candidate": candidate["rows"],
                    "change_pct": _pct(candidate["rows"], production["rows"])},
           "series": [], "category_changes": {}}
    for key in sorted(set(candidate["series"]) | set(production["series"])):
        c, p = candidate["series"].get(key, {}), production["series"].get(key, {})
        row = {"series": key, "production_at_production_latest": p.get(pq), "candidate_at_production_latest": c.get(pq),
               "candidate_latest": c.get(cq)}
        row["restatement_pct"] = _pct(row["candidate_at_production_latest"], row["production_at_production_latest"])
        row["growth_pct"] = _pct(row["candidate_latest"], row["candidate_at_production_latest"]) if cq != pq else None
        out["series"].append(row)
    for cname in set(candidate.get("categories", {})) | set(production.get("categories", {})):
        a, b = candidate["categories"].get(cname, {}), production["categories"].get(cname, {})
        changed = {v: {"production": b.get(v, 0), "candidate": a.get(v, 0)} for v in set(a) | set(b)
                   if b.get(v, 0) and abs(a.get(v, 0) - b.get(v, 0)) > 0.02 * b.get(v, 0) or (v in a) != (v in b)}
        if changed:
            out["category_changes"][cname] = changed
    return out


def _pct(a, b):
    if a is None or b in (None, 0):
        return None
    return round(100.0 * (a - b) / b, 3)


def comparison_warnings(cmp):
    """Non-blocking review flags derived from the comparison."""
    flags = []
    if not cmp.get("available"):
        return flags
    if cmp["quarter_relation"] == "OLDER":
        flags.append(f"candidate latest quarter {cmp['candidate_latest']} is older than production {cmp['production_latest']}")
    if cmp["rows"]["change_pct"] is not None and abs(cmp["rows"]["change_pct"]) > 5:
        flags.append(f"row count changed {cmp['rows']['change_pct']:+.1f}% versus production")
    for s in cmp["series"]:
        if s["restatement_pct"] is not None and abs(s["restatement_pct"]) > 1:
            flags.append(f"{s['series']}: {cmp['production_latest']} restated {s['restatement_pct']:+.2f}%")
    for cname, ch in cmp["category_changes"].items():
        flags.append(f"{cname} categories changed: " + ", ".join(
            f"{v} {d['production']:,}->{d['candidate']:,}" for v, d in list(ch.items())[:4]))
    return flags
