"""Correspondence between the version-controlled runner copy and the operational runner.

SOURCE_MANIFEST.json keeps the operational hashes (current_sha256*). Files listed under
repository_copy_sanitisation differ in the repository only by the recorded hunks (machine-specific paths resolved
from local configuration). Everything is compared with CRLF normalised to LF.
"""
import hashlib
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parent
PKG = ROOT / "dashboard_integration"


def _text(p):
    return Path(p).read_bytes().replace(b"\r\n", b"\n").decode("utf-8")


def _sha(s):
    return hashlib.sha256(s.encode("utf-8")).hexdigest()


def manifest():
    return json.loads((ROOT / "SOURCE_MANIFEST.json").read_text(encoding="utf-8"))


def expected_repository_hashes(m=None):
    """Normalised sha256 each repository file must have: the operational hash, unless sanitised."""
    m = m or manifest()
    out = dict(m["current_sha256_eol_normalised"])
    for name, s in m.get("repository_copy_sanitisation", {}).get("files", {}).items():
        assert s["operational_sha256_eol_normalised"] == out[name], name
        out[name] = s["repository_sha256_eol_normalised"]
    return out


def problems(operational_dir=None, m=None):
    """List of differences; empty means the repository copy corresponds to the manifest (and the operational folder)."""
    m = m or manifest()
    found = []
    expected = expected_repository_hashes(m)
    actual = {p.name: _sha(_text(p)) for p in sorted(PKG.glob("*.py"))}
    if actual != expected:
        found.append(("repository copy", sorted(n for n in set(actual) | set(expected) if actual.get(n) != expected.get(n))))
    if operational_dir is None:
        return found
    op = Path(operational_dir)
    for name, digest in m["current_sha256_eol_normalised"].items():
        if not (op / name).exists() or _sha(_text(op / name)) != digest:
            found.append(("operational normalised", name))
    for name, digest in m["current_sha256"].items():
        if not (op / name).exists() or hashlib.sha256((op / name).read_bytes()).hexdigest() != digest:
            found.append(("operational bytes", name))
    for name, s in m.get("repository_copy_sanitisation", {}).get("files", {}).items():
        if not (op / name).exists():
            continue
        a, b = _text(op / name).split("\n"), _text(PKG / name).split("\n")
        rebuilt, pos = [], 1
        for h in s["hunks"]:
            i1, i2 = h["operational_lines"]
            j1, j2 = h["repository_lines"]
            if _sha("\n".join(a[i1 - 1:i2])) != h["operational_text_sha256"]:
                found.append(("sanitisation hunk does not match the operational lines", name, i1))
            rebuilt += a[pos - 1:i1 - 1] + b[j1 - 1:j2]
            pos = i2 + 1
        rebuilt += a[pos - 1:]
        if rebuilt != b:
            found.append(("operational and repository copies differ outside the recorded hunks", name))
    return found
