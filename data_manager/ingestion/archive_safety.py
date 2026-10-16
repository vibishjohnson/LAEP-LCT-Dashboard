"""
Container checks for zip-based spreadsheets (ODS, XLSX) before any parser touches them.

Rejects: non-zip bytes, encrypted members, absolute or parent-relative member names, too many
members, nested archives, members (or totals) above the configured uncompressed limits and
compression ratios typical of decompression bombs. Declared sizes are enforced while reading:
zipfile stops at the declared size and the CRC check then fails, so a forged header cannot
inflate past the limit.
"""

import zipfile

ODS_MIMETYPE = b"application/vnd.oasis.opendocument.spreadsheet"
NESTED_ARCHIVE_SUFFIXES = (".zip", ".ods", ".xlsx", ".xlsm", ".gz", ".7z", ".rar", ".jar")


class UnsafeArchive(ValueError):
    """The file is not a safe spreadsheet container."""


def _unsafe_name(name):
    n = name.replace("\\", "/")
    return n.startswith("/") or (len(n) > 1 and n[1] == ":") or any(part == ".." for part in n.split("/"))


def inspect_archive(path, settings, kind):
    """Validate the container. kind is 'ods' or 'xlsx'. Returns a summary dict."""
    try:
        zf = zipfile.ZipFile(path)
    except zipfile.BadZipFile as e:
        raise UnsafeArchive(f"not a valid {kind.upper()} file (zip container unreadable: {e})") from e
    with zf:
        infos = zf.infolist()
        if len(infos) > settings.max_archive_members:
            raise UnsafeArchive(f"archive has {len(infos)} members (limit {settings.max_archive_members})")
        total = 0
        for i in infos:
            if i.flag_bits & 0x1:
                raise UnsafeArchive(f"encrypted archive member: {i.filename}")
            if _unsafe_name(i.filename):
                raise UnsafeArchive(f"unsafe member path: {i.filename!r}")
            if i.filename.lower().endswith(NESTED_ARCHIVE_SUFFIXES):
                raise UnsafeArchive(f"nested archive member not allowed: {i.filename}")
            if i.compress_type not in (zipfile.ZIP_STORED, zipfile.ZIP_DEFLATED):
                raise UnsafeArchive(f"unsupported compression method {i.compress_type} for {i.filename}")
            if i.file_size > settings.max_member_uncompressed_bytes:
                raise UnsafeArchive(f"member {i.filename} expands to {i.file_size:,} bytes "
                                    f"(limit {settings.max_member_uncompressed_bytes:,})")
            if i.file_size > 1_000_000 and i.file_size / max(1, i.compress_size) > settings.max_compression_ratio:
                raise UnsafeArchive(f"member {i.filename} compression ratio {i.file_size / max(1, i.compress_size):.0f}:1 "
                                    f"exceeds {settings.max_compression_ratio:.0f}:1 (possible decompression bomb)")
            total += i.file_size
        if total > settings.max_total_uncompressed_bytes:
            raise UnsafeArchive(f"archive expands to {total:,} bytes (limit {settings.max_total_uncompressed_bytes:,})")
        names = {i.filename for i in infos}
        if kind == "ods":
            if "content.xml" not in names or "mimetype" not in names:
                raise UnsafeArchive("not an OpenDocument spreadsheet (content.xml or mimetype missing)")
            if zf.read("mimetype").strip() != ODS_MIMETYPE:
                raise UnsafeArchive("mimetype is not application/vnd.oasis.opendocument.spreadsheet")
            main = zf.getinfo("content.xml")
        elif kind == "xlsx":
            if "[Content_Types].xml" not in names or "xl/workbook.xml" not in names:
                raise UnsafeArchive("not an XLSX workbook ([Content_Types].xml or xl/workbook.xml missing)")
            main = None
        else:
            raise ValueError(kind)
        return {"members": len(infos), "uncompressed_bytes": total,
                "main_member_bytes": main.file_size if main else None}
