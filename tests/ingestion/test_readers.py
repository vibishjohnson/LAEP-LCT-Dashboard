"""Streaming readers, format sniffing and archive safety."""

import os
import struct
import zipfile
import zlib
from dataclasses import replace

import pytest

from data_manager.ingestion import readers
from data_manager.ingestion.archive_safety import UnsafeArchive, inspect_archive
from data_manager.ingestion.config import Settings

from .conftest import write_ods, write_xlsx

S = Settings()


def rows_of(path, fmt, sheet=None, settings=S):
    return list(readers.iter_rows(path, fmt, sheet, settings))


# ------------------------------------------------------------------ format detection
def test_sniff_by_content_not_extension(tmp_path, veh0142_ods, veh0142_xlsx):
    assert readers.sniff_format(veh0142_ods, "veh0142 (11).ods") == "ods"
    assert readers.sniff_format(veh0142_xlsx, "veh0142.xlsx") == "xlsx"
    csvp = tmp_path / "a.csv"
    csvp.write_text("A,B\n1,2\n", encoding="utf-8")
    assert readers.sniff_format(str(csvp), "a.csv") == "csv"
    with pytest.raises(readers.UnsupportedFormat, match="does not match"):
        readers.sniff_format(veh0142_ods, "veh0142.csv")           # renamed spreadsheet
    with pytest.raises(readers.UnsupportedFormat, match="does not match"):
        readers.sniff_format(veh0142_xlsx, "veh0142.ods")


def test_sniff_rejects_binary_legacy_and_foreign_zip(tmp_path):
    xls = tmp_path / "old.xls"
    xls.write_bytes(b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1" + b"\0" * 600)
    with pytest.raises(readers.UnsupportedFormat, match="legacy binary Excel"):
        readers.sniff_format(str(xls), "old.xls")
    binf = tmp_path / "x.csv"
    binf.write_bytes(b"A,B\n\x00\x01\x02")
    with pytest.raises(readers.UnsupportedFormat, match="binary"):
        readers.sniff_format(str(binf), "x.csv")
    z = tmp_path / "x.ods"
    with zipfile.ZipFile(z, "w") as zf:
        zf.writestr("readme.txt", "hello")
    with pytest.raises(readers.UnsupportedFormat, match="neither"):
        readers.sniff_format(str(z), "x.ods")


# ------------------------------------------------------------------ ODS streaming
def test_ods_values_spaces_repeats_and_leading_zeros(tmp_path):
    p = write_ods(str(tmp_path / "t.ods"), {
        "Other": [["ignore me"]],
        "Data": [["Title row"], [], ["Code", "Name", "2026 Q2"],
                 ["E06000047", " County Durham", 8235], ["00123", "  two spaces", 0.42599999999999999], ["[z]", "Unknown", "[c]"]]})
    rows = rows_of(p, "ods", "Data")
    assert rows[0] == ["Title row"]
    assert rows[1] == []                                  # blank row kept as []
    assert rows[3] == ["E06000047", " County Durham", "8235"]          # stored value, not "8,235"
    assert rows[4] == ["00123", "  two spaces", "0.426"]                # leading zeros and spaces preserved
    assert rows[5] == ["[z]", "Unknown", "[c]"]
    assert len(rows) == 7                                 # 1,048,000 trailing blank rows collapse to one []


def test_ods_columns_repeated_and_covered_cells(tmp_path):
    content = ('<?xml version="1.0" encoding="UTF-8"?><office:document-content xmlns:office="urn:oasis:names:tc:opendocument:xmlns:office:1.0" '
               'xmlns:table="urn:oasis:names:tc:opendocument:xmlns:table:1.0" xmlns:text="urn:oasis:names:tc:opendocument:xmlns:text:1.0">'
               '<office:body><office:spreadsheet><table:table table:name="S">'
               '<table:table-row><table:table-cell office:value-type="float" office:value="5" table:number-columns-repeated="3"><text:p>5</text:p></table:table-cell>'
               '<table:table-cell table:number-columns-repeated="2"/><table:covered-table-cell office:value-type="string"><text:p>x<text:span>y</text:span></text:p></table:covered-table-cell>'
               '<table:table-cell table:number-columns-repeated="16000"/></table:table-row>'
               '<table:table-row table:number-rows-repeated="3"><table:table-cell office:value-type="string"><text:p>r</text:p></table:table-cell></table:table-row>'
               '</table:table></office:spreadsheet></office:body></office:document-content>')
    p = write_ods(str(tmp_path / "r.ods"), {}, content_override=content.encode())
    rows = rows_of(p, "ods", "S")
    assert rows[0] == ["5", "5", "5", "", "", "xy"]
    assert rows[1:] == [["r"], ["r"], ["r"]]


def test_ods_missing_sheet_and_sheet_listing(tmp_path, veh0142_ods):
    assert readers.sheet_names(veh0142_ods, "ods", S) == ["Cover_sheet", "Contents", "Notes", "VEH0142"]
    with pytest.raises(readers.ParseError, match="not found"):
        rows_of(veh0142_ods, "ods", "VEH9999")


def test_ods_malformed_xml(tmp_path):
    p = write_ods(str(tmp_path / "bad.ods"), {}, content_override=b'<?xml version="1.0"?><office:document-content xmlns:office="x"><unclosed>')
    with pytest.raises(readers.ParseError, match="malformed"):
        rows_of(p, "ods", "S")


@pytest.mark.parametrize("payload", [
    b'<?xml version="1.0"?><!DOCTYPE lol [<!ENTITY a "aaaaaaaaaa"><!ENTITY b "&a;&a;&a;&a;&a;&a;&a;&a;">]><r>&b;</r>',
    b'<?xml version="1.0"?><!DOCTYPE r SYSTEM "http://example.invalid/evil.dtd"><r/>',
])
def test_ods_refuses_dtd_and_entities(tmp_path, payload):
    p = write_ods(str(tmp_path / "ent.ods"), {}, content_override=payload)
    with pytest.raises(UnsafeArchive, match="DTD"):
        rows_of(p, "ods", "S")


def test_ods_wrong_mimetype_rejected(tmp_path):
    p = write_ods(str(tmp_path / "m.ods"), {"S": [["a"]]}, mimetype="application/zip")
    with pytest.raises(UnsafeArchive, match="mimetype"):
        inspect_archive(p, S, "ods")


def test_ods_row_limits(tmp_path):
    content = ('<?xml version="1.0"?><office:document-content xmlns:office="urn:oasis:names:tc:opendocument:xmlns:office:1.0" '
               'xmlns:table="urn:oasis:names:tc:opendocument:xmlns:table:1.0" xmlns:text="urn:oasis:names:tc:opendocument:xmlns:text:1.0">'
               '<office:body><office:spreadsheet><table:table table:name="S">'
               '<table:table-row table:number-rows-repeated="900000000"><table:table-cell office:value-type="string"><text:p>v</text:p></table:table-cell></table:table-row>'
               '</table:table></office:spreadsheet></office:body></office:document-content>')
    p = write_ods(str(tmp_path / "rep.ods"), {}, content_override=content.encode())
    with pytest.raises(readers.ParseError, match="repeated"):
        rows_of(p, "ods", "S")
    wide = content.replace('table:number-rows-repeated="900000000"', "").replace(
        '<table:table-cell office:value-type="string">', '<table:table-cell office:value-type="string" table:number-columns-repeated="99999">')
    p2 = write_ods(str(tmp_path / "wide.ods"), {}, content_override=wide.encode())
    with pytest.raises(readers.ParseError, match="columns"):
        rows_of(p2, "ods", "S")


# ------------------------------------------------------------------ archive safety
def _zip_with(path, members):
    with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr(zipfile.ZipInfo("mimetype"), "application/vnd.oasis.opendocument.spreadsheet")
        for name, data in members.items():
            z.writestr(name, data)
    return path


def test_decompression_bomb_ratio_rejected(tmp_path):
    p = _zip_with(str(tmp_path / "bomb.ods"), {"content.xml": b"<a>" + b" " * (60 * 2**20) + b"</a>"})
    with pytest.raises(UnsafeArchive, match="compression ratio"):
        inspect_archive(p, S, "ods")
    with pytest.raises(UnsafeArchive):
        rows_of(p, "ods", "S")


def test_declared_size_limit_and_total(tmp_path):
    s = replace(S, max_member_uncompressed_bytes=1_000_000, max_total_uncompressed_bytes=1_500_000, max_compression_ratio=10_000)
    p = _zip_with(str(tmp_path / "big.ods"), {"content.xml": os.urandom(1_200_000)})
    with pytest.raises(UnsafeArchive, match="expands to"):
        inspect_archive(p, s, "ods")
    p2 = _zip_with(str(tmp_path / "tot.ods"), {"content.xml": os.urandom(900_000), "styles.xml": os.urandom(900_000)})
    with pytest.raises(UnsafeArchive, match="expands to"):
        inspect_archive(p2, s, "ods")


def test_forged_size_header_cannot_inflate(tmp_path):
    """A member whose header under-declares its size is cut at the declared size and fails the CRC check."""
    p = str(tmp_path / "forged.ods")
    real = b'<?xml version="1.0"?><office:document-content xmlns:office="urn:oasis:names:tc:opendocument:xmlns:office:1.0">' + b"<x/>" * 50000 + b"</office:document-content>"
    _zip_with(p, {"content.xml": real})
    data = bytearray(open(p, "rb").read())
    # patch the uncompressed size in the local header and the central directory to 1,000 bytes
    for sig, off in ((b"PK\x03\x04", 22), (b"PK\x01\x02", 24)):
        pos = -1
        while True:
            pos = data.find(sig, pos + 1)
            if pos < 0:
                break
            name_len = struct.unpack("<H", data[pos + (26 if sig == b"PK\x03\x04" else 28): pos + (28 if sig == b"PK\x03\x04" else 30)])[0]
            name_at = pos + (30 if sig == b"PK\x03\x04" else 46)
            if bytes(data[name_at:name_at + name_len]) == b"content.xml":
                data[pos + off: pos + off + 4] = struct.pack("<I", 1000)
    open(p, "wb").write(bytes(data))
    with pytest.raises((UnsafeArchive, readers.ParseError)):
        rows_of(p, "ods", "S")


@pytest.mark.parametrize("name", ["../evil.xml", "/abs.xml", "C:/win.xml", "sub/../../x.xml"])
def test_path_traversal_member_names_rejected(tmp_path, name):
    p = _zip_with(str(tmp_path / "trav.ods"), {"content.xml": b"<a/>", name: b"x"})
    with pytest.raises(UnsafeArchive, match="unsafe member path"):
        inspect_archive(p, S, "ods")


def test_nested_archive_and_member_count_rejected(tmp_path):
    p = _zip_with(str(tmp_path / "nest.ods"), {"content.xml": b"<a/>", "inner.zip": b"PK\x03\x04"})
    with pytest.raises(UnsafeArchive, match="nested archive"):
        inspect_archive(p, S, "ods")
    p2 = _zip_with(str(tmp_path / "many.ods"), {"content.xml": b"<a/>", **{f"f{i}.xml": b"x" for i in range(250)}})
    with pytest.raises(UnsafeArchive, match="members"):
        inspect_archive(p2, S, "ods")


def test_encrypted_member_rejected(tmp_path):
    p = _zip_with(str(tmp_path / "enc.ods"), {"content.xml": b"<a/>"})
    data = bytearray(open(p, "rb").read())
    for sig, off in ((b"PK\x03\x04", 6), (b"PK\x01\x02", 8)):
        pos = data.find(sig)
        while pos >= 0:
            data[pos + off] |= 0x1
            pos = data.find(sig, pos + 1)
    open(p, "wb").write(bytes(data))
    with pytest.raises(UnsafeArchive, match="encrypted"):
        inspect_archive(p, S, "ods")


def test_truncated_zip_rejected(tmp_path, veh0142_ods):
    p = tmp_path / "trunc.ods"
    p.write_bytes(open(veh0142_ods, "rb").read()[: 200_000])
    with pytest.raises((UnsafeArchive, readers.UnsupportedFormat)):
        readers.sniff_format(str(p), "trunc.ods")
        inspect_archive(str(p), S, "ods")


# ------------------------------------------------------------------ XLSX and CSV
def test_xlsx_rows_and_missing_sheet(tmp_path):
    p = write_xlsx(str(tmp_path / "t.xlsx"), {"Cover": [["c"]], "Data": [["Title"], ["Code", "2026 Q1"], ["01234", 1234], ["E06000001", 0.5]]})
    assert readers.sheet_names(p, "xlsx", S) == ["Cover", "Data"]
    assert rows_of(p, "xlsx", "Data") == [["Title"], ["Code", "2026 Q1"], ["01234", "1234"], ["E06000001", "0.5"]]
    with pytest.raises(readers.ParseError, match="not found"):
        rows_of(p, "xlsx", "Nope")


def test_xlsx_bomb_rejected_before_openpyxl(tmp_path):
    p = str(tmp_path / "bomb.xlsx")
    with zipfile.ZipFile(p, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr("[Content_Types].xml", "<x/>")
        z.writestr("xl/workbook.xml", "<x/>")
        z.writestr("xl/worksheets/sheet1.xml", b"<a>" + b"0" * (80 * 2**20) + b"</a>")
    with pytest.raises(UnsafeArchive, match="compression ratio"):
        readers.sheet_names(p, "xlsx", S)


def test_csv_encodings_and_quoted_values(tmp_path):
    p = tmp_path / "bom.csv"
    p.write_bytes("\ufeffLSOA21CD,Name,2026 Q1\r\nE01000001,\"City, of London\",\"8,235\"\r\n".encode("utf-8"))
    assert rows_of(str(p), "csv") == [["LSOA21CD", "Name", "2026 Q1"], ["E01000001", "City, of London", "8,235"]]
    q = tmp_path / "ansi.csv"
    q.write_bytes("Code,Name\r\nW01000001,Ynys M\xf4n\r\n".encode("cp1252"))
    assert rows_of(str(q), "csv")[1] == ["W01000001", "Ynys Môn"]


def test_csv_progress_reports_bytes(tmp_path):
    p = tmp_path / "p.csv"
    p.write_text("A,B\n" + "1,2\n" * 300000, encoding="utf-8")
    seen = []
    for _ in readers.iter_rows(str(p), "csv", None, S, progress=seen.append):
        pass
    assert seen and seen[-1] == pytest.approx(1.0) and all(0 <= x <= 1 for x in seen)
