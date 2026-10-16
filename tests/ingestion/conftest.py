"""
Fixtures for the ingestion tests. Everything destructive happens in pytest tmp directories.

Production files (ev/, lookups/) are only ever read. Real DfT downloads, when present on this
machine, are used read-only; tests that need them are skipped elsewhere.
"""

import csv
import http.client
import json
import os
import shutil
import sys
import threading
import time
import zipfile
from dataclasses import replace
from xml.sax.saxutils import escape

import pytest

REPO = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, REPO)

from data_manager.ingestion.config import Settings  # noqa: E402

EV = os.path.join(REPO, "ev")
LOOKUP = os.path.join(REPO, "lookups", "LSOA to DNO.csv")
# Real DfT publications for the read-only tests: LCT_DFT_SAMPLES_DIR, else ~/Downloads (tests skip when a file is absent).
DOWNLOADS = os.environ.get("LCT_DFT_SAMPLES_DIR") or os.path.join(os.path.expanduser("~"), "Downloads")
REAL = {
    "veh0142_2026q2_ods": os.path.join(DOWNLOADS, "veh0142 (11).ods"),
    "veh0142_2026q1_ods": os.path.join(DOWNLOADS, "veh0142 (10).ods"),
    "veh0105_2026q2_ods": os.path.join(DOWNLOADS, "veh0105 (4).ods"),
    "veh0105_2026q1_ods": os.path.join(DOWNLOADS, "veh0105 (3).ods"),
    "veh0135_2026q2_csv": os.path.join(DOWNLOADS, "df_VEH0135 (7).csv"),
    "veh0135_2026q1_dl_csv": os.path.join(DOWNLOADS, "df_VEH0135 (6).csv"),
    "veh0125_2026q2_csv": os.path.join(DOWNLOADS, "df_VEH0125 (5).csv"),
    "veh0142_xlsx": os.environ.get("LCT_DFT_SAMPLE_XLSX") or os.path.join(os.path.expanduser("~"), "Desktop", "veh0142 work", "veh0142 (3).xlsx"),
}

TITLE_0142 = ["Licensed plug-in vehicles (PiVs) at the end of the quarter by body type, fuel type, keepership (private and company) "
              "and upper and lower tier local authority: United Kingdom",
              "Table VEH0142",
              "This worksheet contains 1 table. Some cells refer to notes which can be found on the notes worksheet.",
              "Some shorthand is used in this table, [z] = not applicable."]
TITLE_0105 = ["Licensed vehicles at the end of the quarter by body type, fuel type, keepership (private and company) and upper and "
              "lower tier local authority: United Kingdom", "Table VEH0105",
              "This worksheet contains one table. Some cells refer to notes which can be found on the notes worksheet.",
              "Some shorthand is used in this table [x] = not available, [z] = not applicable."]
COVER = [["Vehicle licensing statistics"], ["This spreadsheet contains data tables published by the Department for Transport."]]


def real(name):
    p = REAL[name]
    if not os.path.exists(p):
        pytest.skip(f"real DfT file not present on this machine: {p}")
    return p


def read_csv_rows(path, limit=None):
    with open(path, newline="", encoding="utf-8-sig") as f:
        rows = []
        for i, r in enumerate(csv.reader(f)):
            if limit is not None and i >= limit:
                break
            rows.append(r)
        return rows


# ------------------------------------------------------------------ spreadsheet writers (LibreOffice-style XML)
def _ods_cell(v):
    if v is None or v == "":
        return "<table:table-cell/>"
    if isinstance(v, (int, float)) and not isinstance(v, bool):
        disp = f"{v:,}" if isinstance(v, int) else f"{v:,.2f}"
        return (f'<table:table-cell office:value-type="float" office:value="{v!r}" calcext:value-type="float">'
                f"<text:p>{disp}</text:p></table:table-cell>")
    s = escape(str(v))
    lead = len(s) - len(s.lstrip(" "))
    if lead:      # LibreOffice writes leading spaces as <text:s/>
        s = f'<text:s text:c="{lead}"/>' + s.lstrip(" ") if lead > 1 else "<text:s/>" + s[1:]
    return f'<table:table-cell office:value-type="string" calcext:value-type="string"><text:p>{s}</text:p></table:table-cell>'


def _numeric(v):
    """CSV text -> spreadsheet value as DfT stores it (numbers numeric, markers text)."""
    if not isinstance(v, str):
        return v
    t = v.replace(",", "")
    if t.isdigit():
        return int(t)
    try:
        return float(t) if any(ch.isdigit() for ch in t) and t.replace(".", "", 1).isdigit() else v
    except ValueError:
        return v


def write_ods(path, sheets, numeric_from=None, trailing=True, content_override=None, mimetype=None, extra=None):
    """sheets: {name: rows}. Cells from column `numeric_from` on (data rows only) are written as numbers."""
    parts = ['<?xml version="1.0" encoding="UTF-8"?>\n<office:document-content '
             'xmlns:office="urn:oasis:names:tc:opendocument:xmlns:office:1.0" '
             'xmlns:table="urn:oasis:names:tc:opendocument:xmlns:table:1.0" '
             'xmlns:text="urn:oasis:names:tc:opendocument:xmlns:text:1.0" '
             'xmlns:calcext="urn:org:documentfoundation:names:experimental:calc:xmlns:calcext:1.0" office:version="1.3">'
             "<office:body><office:spreadsheet>"]
    for name, rows in sheets.items():
        parts.append(f'<table:table table:name="{escape(name)}" table:style-name="ta1">'
                     f'<table:table-column table:number-columns-repeated="80"/>')
        for r in rows:
            cells = []
            for j, v in enumerate(r):
                if numeric_from is not None and j >= numeric_from and isinstance(v, str):
                    v = _numeric(v)
                cells.append(_ods_cell(v))
            if trailing:
                cells.append('<table:table-cell table:number-columns-repeated="16300"/>')
            parts.append('<table:table-row table:style-name="ro1">' + "".join(cells) + "</table:table-row>")
        if trailing:
            parts.append('<table:table-row table:number-rows-repeated="1048000"><table:table-cell table:number-columns-repeated="16384"/></table:table-row>')
        parts.append("</table:table>")
    parts.append("</office:spreadsheet></office:body></office:document-content>")
    content = content_override if content_override is not None else "".join(parts).encode("utf-8")
    with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr(zipfile.ZipInfo("mimetype"), mimetype or "application/vnd.oasis.opendocument.spreadsheet")
        z.writestr("META-INF/manifest.xml", '<?xml version="1.0"?><manifest:manifest xmlns:manifest="urn:oasis:names:tc:opendocument:xmlns:manifest:1.0"/>')
        z.writestr("content.xml", content)
        for k, v in (extra or {}).items():
            z.writestr(k, v)
    return path


def write_xlsx(path, sheets, numeric_from=None):
    """Minimal valid XLSX with inline strings (what openpyxl and Excel read)."""
    def col(i):
        s = ""
        i += 1
        while i:
            i, r = divmod(i - 1, 26)
            s = chr(65 + r) + s
        return s
    with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as z:
        names = list(sheets)
        z.writestr("[Content_Types].xml", '<?xml version="1.0" encoding="UTF-8"?><Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">'
                   '<Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/><Default Extension="xml" ContentType="application/xml"/>'
                   '<Override PartName="/xl/workbook.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet.main+xml"/>'
                   + "".join(f'<Override PartName="/xl/worksheets/sheet{i + 1}.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.worksheet+xml"/>' for i in range(len(names)))
                   + "</Types>")
        z.writestr("_rels/.rels", '<?xml version="1.0" encoding="UTF-8"?><Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
                   '<Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" Target="xl/workbook.xml"/></Relationships>')
        z.writestr("xl/workbook.xml", '<?xml version="1.0" encoding="UTF-8"?><workbook xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main" '
                   'xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships"><sheets>'
                   + "".join(f'<sheet name="{escape(n)}" sheetId="{i + 1}" r:id="rId{i + 1}"/>' for i, n in enumerate(names)) + "</sheets></workbook>")
        z.writestr("xl/_rels/workbook.xml.rels", '<?xml version="1.0" encoding="UTF-8"?><Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
                   + "".join(f'<Relationship Id="rId{i + 1}" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/worksheet" Target="worksheets/sheet{i + 1}.xml"/>' for i in range(len(names)))
                   + '</Relationships>')
        for i, (n, rows) in enumerate(sheets.items()):
            out = ['<?xml version="1.0" encoding="UTF-8"?><worksheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main"><sheetData>']
            for ri, r in enumerate(rows, 1):
                cells = []
                for ci, v in enumerate(r):
                    ref = f"{col(ci)}{ri}"
                    if numeric_from is not None and ci >= numeric_from and isinstance(v, str):
                        v = _numeric(v)
                    if v is None or v == "":
                        continue
                    if isinstance(v, (int, float)):
                        cells.append(f'<c r="{ref}"><v>{v!r}</v></c>')
                    else:
                        cells.append(f'<c r="{ref}" t="inlineStr"><is><t xml:space="preserve">{escape(str(v))}</t></is></c>')
                out.append(f'<row r="{ri}">' + "".join(cells) + "</row>")
            out.append("</sheetData></worksheet>")
            z.writestr(f"xl/worksheets/sheet{i + 1}.xml", "".join(out))
    return path


def dft_workbook(table, csv_path):
    """{sheet: rows} reproducing the DfT ODS layout from the production CSV export of the same table."""
    rows = read_csv_rows(csv_path)
    titles = TITLE_0142 if table == "VEH0142" else TITLE_0105
    return {"Cover_sheet": COVER, "Contents": [["Worksheet", "Description"], [table, titles[0]]],
            "Notes": [["Note number", "Note text"], ["Note 1", "Figures for the latest quarter are provisional."]],
            table: [[t] for t in titles] + rows}


# ------------------------------------------------------------------ session fixtures (built once, read-only afterwards)
@pytest.fixture(scope="session")
def fixtures_dir(tmp_path_factory):
    return tmp_path_factory.mktemp("ingest_fixtures")


@pytest.fixture(scope="session")
def veh0142_ods(fixtures_dir):
    return write_ods(str(fixtures_dir / "veh0142.ods"), dft_workbook("VEH0142", os.path.join(EV, "VEH0142.csv")), numeric_from=7)


@pytest.fixture(scope="session")
def veh0105_ods(fixtures_dir):
    return write_ods(str(fixtures_dir / "veh0105.ods"), dft_workbook("VEH0105", os.path.join(EV, "VEH0105.csv")), numeric_from=7)


@pytest.fixture(scope="session")
def veh0142_xlsx(fixtures_dir):
    return write_xlsx(str(fixtures_dir / "veh0142.xlsx"), dft_workbook("VEH0142", os.path.join(EV, "VEH0142.csv")), numeric_from=7)


@pytest.fixture(scope="session")
def veh0135_csv(fixtures_dir):
    """Byte copy of the production VEH0135 (2026 Q1); the source is only read."""
    dst = str(fixtures_dir / "df_VEH0135.csv")
    shutil.copyfile(os.path.join(EV, "df_VEH0135.csv"), dst)
    os.chmod(dst, 0o444)
    return dst


@pytest.fixture(scope="session")
def veh0135_header_rows():
    return read_csv_rows(os.path.join(EV, "df_VEH0135.csv"), limit=4000)


# ------------------------------------------------------------------ settings and live server
@pytest.fixture
def settings(tmp_path):
    return replace(Settings(), registry_root=str(tmp_path / "registry"), production_ev_dir=EV, lsoa_dno_lookup=LOOKUP,
                   compare_with_production=False)


def overrides_of(s):
    base = Settings()
    keys = ("production_ev_dir", "lsoa_dno_lookup", "compare_with_production", "max_upload_bytes", "max_member_uncompressed_bytes",
            "max_total_uncompressed_bytes", "max_compression_ratio", "worker_memory_limit_mb", "worker_timeout_seconds",
            "keep_failed_upload_bytes", "max_queued_jobs", "max_concurrent_uploads", "max_columns")
    return {k: getattr(s, k) for k in keys if getattr(s, k) != getattr(base, k)}


class LiveServer:
    def __init__(self, settings):
        from flask import Flask
        from werkzeug.serving import make_server
        from data_manager.ingestion import web
        self.app = Flask("ingest_test", template_folder=os.path.join(REPO, "templates"))
        self.state = web.init_app(self.app, settings, overrides_of(settings))
        self.server = make_server("127.0.0.1", 0, self.app, threaded=True)
        self.port = self.server.server_port
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()

    def conn(self):
        return http.client.HTTPConnection("127.0.0.1", self.port, timeout=600)

    def request(self, method, path, body=None, headers=None):
        c = self.conn()
        try:
            c.request(method, path, body=body, headers=headers or {})
            r = c.getresponse()
            data = r.read()
            try:
                return r.status, json.loads(data)
            except ValueError:
                return r.status, data.decode("utf-8", "replace")
        finally:
            c.close()

    def upload(self, path, filename=None, chunk=1 << 20):
        """Stream a file from disk as the browser does (PUT, raw body)."""
        from urllib.parse import quote
        size = os.path.getsize(path)
        c = self.conn()
        try:
            c.putrequest("PUT", "/api/ingest/upload?filename=" + quote(filename or os.path.basename(path)))
            c.putheader("Content-Length", str(size))
            c.putheader("Content-Type", "application/octet-stream")
            c.endheaders()
            try:
                with open(path, "rb") as f:
                    for block in iter(lambda: f.read(chunk), b""):
                        c.send(block)
            except (ConnectionAbortedError, ConnectionResetError, BrokenPipeError):
                pass                       # the server answered early (413/429) and closed; read that answer
            r = c.getresponse()
            return r.status, json.loads(r.read())
        finally:
            c.close()

    def post(self, path, body):
        return self.request("POST", path, json.dumps(body), {"Content-Type": "application/json"})

    def wait(self, job_id, timeout=600):
        t0 = time.time()
        while time.time() - t0 < timeout:
            s, d = self.request("GET", f"/api/ingest/jobs/{job_id}")
            if d["job"]["state"] not in ("UPLOADING", "QUEUED", "PARSING", "VALIDATING"):
                return d["job"]
            time.sleep(0.3)
        raise TimeoutError(job_id)

    def close(self):
        self.server.shutdown()
        self.state.runner.wait_idle(120)


@pytest.fixture
def live(settings):
    srv = LiveServer(settings)
    yield srv
    srv.close()


def make_live(settings):
    return LiveServer(settings)
