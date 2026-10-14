"""C1 EV actuals release tool: preflight, promote, rollback, recover, status, verify-c1.

Every operation takes an explicit --root (a directory laid out like the repository). The tool never defaults to the
repository. Against the real repository root, promote/rollback/recover are refused while
release_config.LIVE_RELEASE_ENABLED is False; preflight, status and verify-c1 are read-only and allowed anywhere.

Writes are limited to:
  * the three allowlisted targets  <root>/project/output_processed/integration/ev_actuals_{dno,la,laep}.csv
  * staged replacement files next to them  .c1promote-<release>-<name>.tmp / .c1rollback-<release>-<name>.tmp
  * the release state directory    <root>/experiments/ev_redistribution_c1/release/state/

The three-file release is NOT atomic as a set. Each file is replaced atomically (os.replace from a staged file in the
same directory), and a durable release manifest records progress. Any failure triggers an automatic restore of the
originals. A hard interruption leaves the manifest in an incomplete state that blocks further promotions until
`recover` deterministically restores the original (Method A) files from the verified backup.

CLI (from the repository root):
  python experiments/ev_redistribution_c1/release/c1_release.py preflight --root <dir>
  python experiments/ev_redistribution_c1/release/c1_release.py promote   --root <dir> --release-id c1-YYYYMMDD-name --confirm "PROMOTE <id> <digest16>"
  python experiments/ev_redistribution_c1/release/c1_release.py rollback  --root <dir> --release-id <id> --confirm "ROLLBACK <id> <manifest16>"
  python experiments/ev_redistribution_c1/release/c1_release.py recover   --root <dir> --release-id <id> --confirm "RECOVER <id>"
  python experiments/ev_redistribution_c1/release/c1_release.py status    --root <dir>
  python experiments/ev_redistribution_c1/release/c1_release.py verify-c1 --root <dir>
"""
import argparse
import getpass
import hashlib
import io
import json
import os
import re
import socket
import stat
import subprocess
import sys
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import release_config as cfg  # noqa: E402
import integration_writer as iw  # noqa: E402  (pure functions and constants only)

RELEASE_ID_RE = re.compile(r'^c1-\d{8}-[a-z0-9][a-z0-9-]{0,39}$')
STAGED_RE = re.compile(r'^\.c1(promote|rollback)-(c1-\d{8}-[a-z0-9][a-z0-9-]{0,39})-(ev_actuals_(?:dno|la|laep)\.csv)\.tmp$')
INCOMPLETE = {'PROMOTING', 'ROLLING_BACK', 'RECOVERY_REQUIRED'}
TERMINAL = {'COMPLETE', 'FAILED_NOT_APPLIED', 'FAILED_ROLLED_BACK', 'ROLLED_BACK', 'RECOVERED_TO_ORIGINAL'}
TOOL_FILES = [Path(__file__).resolve(), Path(cfg.__file__).resolve(), Path(iw.__file__).resolve()]


# ------------------------------------------------------------------ errors
class ReleaseError(Exception):
    code = 'RELEASE_ERROR'
    exit_code = 1

    def __init__(self, message, detail=None):
        super().__init__(message)
        self.detail = detail


class UnsafePath(ReleaseError):
    code, exit_code = 'UNSAFE_PATH', 4


class LiveReleaseBlocked(ReleaseError):
    code, exit_code = 'LIVE_RELEASE_BLOCKED', 4


class PreflightFailed(ReleaseError):
    code, exit_code = 'PREFLIGHT_FAILED', 2


class ConfirmationMismatch(ReleaseError):
    code, exit_code = 'CONFIRMATION_MISMATCH', 4


class LockBusy(ReleaseError):
    code, exit_code = 'LOCK_BUSY', 3


class IncompleteReleaseExists(ReleaseError):
    code, exit_code = 'INCOMPLETE_RELEASE_EXISTS', 5


class BackupVerificationFailed(ReleaseError):
    code = 'BACKUP_VERIFICATION_FAILED'


class TargetChanged(ReleaseError):
    code = 'TARGET_CHANGED_SINCE_PREFLIGHT'


class PostValidationFailed(ReleaseError):
    code = 'POST_VALIDATION_FAILED'


class ManifestInvalid(ReleaseError):
    code, exit_code = 'MANIFEST_INVALID', 4


class RollbackRefused(ReleaseError):
    code, exit_code = 'ROLLBACK_REFUSED', 4


class RecoveryRequired(ReleaseError):
    code, exit_code = 'RECOVERY_REQUIRED', 5


class InjectedFault(ReleaseError):
    code = 'INJECTED_FAULT'


# ------------------------------------------------------------------ small helpers
def sha256_bytes(data):
    return hashlib.sha256(data).hexdigest()


def sha256_file(path):
    h = hashlib.sha256()
    with open(path, 'rb') as f:
        for block in iter(lambda: f.read(1 << 20), b''):
            h.update(block)
    return h.hexdigest()


def now_utc():
    return datetime.now(timezone.utc).isoformat(timespec='microseconds')


def _norm(p):
    return os.path.normcase(os.path.normpath(str(p)))


def _same(a, b):
    return _norm(a) == _norm(b)


def _within(path, root):
    p, r = _norm(path), _norm(root)
    return p == r or p.startswith(r.rstrip(os.sep) + os.sep)


def _is_reparse(p):
    """True for symlinks and Windows junctions / other reparse points."""
    try:
        st = os.lstat(p)
    except FileNotFoundError:
        return False
    if stat.S_ISLNK(st.st_mode):
        return True
    return bool(getattr(st, 'st_file_attributes', 0) & getattr(stat, 'FILE_ATTRIBUTE_REPARSE_POINT', 0x400))


def seal(obj):
    body = {k: v for k, v in obj.items() if k != 'manifest_sha256'}
    return sha256_bytes(json.dumps(body, sort_keys=True, separators=(',', ':'), default=str).encode('utf-8'))


# ------------------------------------------------------------------ approved re-pins (append-only, hash-chained)
PIN_LOG_REL = f'{cfg.STATE_REL}/pins/pin_log.json'
REPINNABLE = (f'{cfg.INTEGRATION_REL}/metadata.json',)


class PinLogInvalid(ReleaseError):
    code, exit_code = 'PIN_LOG_INVALID', 4


def seal_entry(entry):
    body = {k: v for k, v in entry.items() if k != 'entry_sha256'}
    return sha256_bytes(json.dumps(body, sort_keys=True, separators=(',', ':'), default=str).encode('utf-8'))


def read_pin_log(root):
    """Verified pin-log entries (empty when no re-pin was ever approved). Any break in the chain raises."""
    p = Path(root).joinpath(*PIN_LOG_REL.split('/'))
    if not p.exists():
        return []
    try:
        entries = json.loads(p.read_text(encoding='utf-8'))
    except ValueError as e:
        raise PinLogInvalid(f'pin log is not valid JSON: {e}')
    if not isinstance(entries, list):
        raise PinLogInvalid('pin log must be a list')
    prev = ''
    for i, e in enumerate(entries):
        if not isinstance(e, dict) or e.get('seq') != i or e.get('prev_entry_sha256') != prev or e.get('entry_sha256') != seal_entry(e):
            raise PinLogInvalid(f'pin log entry {i} failed its integrity check')
        if e.get('path') not in REPINNABLE or not re.fullmatch(r'[0-9a-f]{64}', str(e.get('new_sha256'))):
            raise PinLogInvalid(f'pin log entry {i} re-pins a path that may not be re-pinned')
        prev = e['entry_sha256']
    return entries


def effective_pins(root):
    """Pinned hashes after applying every approved re-pin in order (each must continue from the previous pin)."""
    pins = dict(cfg.PINNED_SHA256)
    for e in read_pin_log(root):
        if pins.get(e['path']) != e['old_sha256']:
            raise PinLogInvalid(f'pin log entry {e["seq"]} does not continue from the current pin for {e["path"]}')
        pins[e['path']] = e['new_sha256']
    return pins


def pin_log_sha256(root):
    p = Path(root).joinpath(*PIN_LOG_REL.split('/'))
    return sha256_file(p) if p.exists() else ''


def check_release_id(release_id):
    if not isinstance(release_id, str) or not RELEASE_ID_RE.match(release_id):
        raise ReleaseError(f'invalid release id {release_id!r}; expected c1-YYYYMMDD-<lowercase-name>')


# ------------------------------------------------------------------ roots and the write allowlist
def canonical_root(root):
    """Return (canonical root, is_live). Disposable roots must be outside the repository and must not contain it."""
    p = Path(root)
    if not p.is_absolute():
        raise UnsafePath(f'root must be an absolute path: {root}')
    if not p.is_dir():
        raise UnsafePath(f'root is not an existing directory: {root}')
    real = Path(os.path.realpath(p))
    repo = Path(os.path.realpath(cfg.REPO))
    if _same(real, repo):
        return real, True
    if _within(real, repo):
        raise UnsafePath(f'disposable roots must be outside the repository: {real}')
    if _within(repo, real):
        raise UnsafePath(f'root contains the repository: {real}')
    return real, False


def safe_path(root, rel):
    """Resolve rel under the canonical root; refuse absolute/traversal/ADS names and any reparse point below the root."""
    relp = Path(rel)
    # anchor covers drives ('C:'), roots ('/abs' is not "absolute" on Windows but joinpath would escape the root) and UNC
    if relp.is_absolute() or relp.anchor or '..' in relp.parts or any(':' in part for part in relp.parts):
        raise UnsafePath(f'refusing unsafe relative path: {rel}')
    cur = Path(root)
    for part in relp.parts:
        cur = cur / part
        if _is_reparse(cur):
            raise UnsafePath(f'symlink/junction refused: {cur}')
    expected = Path(root).joinpath(*relp.parts)
    if not _same(os.path.realpath(expected), expected):
        raise UnsafePath(f'path resolves elsewhere: {expected} -> {os.path.realpath(expected)}')
    return expected


def target_path(root, name):
    if name not in cfg.TARGET_NAMES:
        raise UnsafePath(f'not an allowlisted target: {name}')
    return safe_path(root, f'{cfg.INTEGRATION_REL}/{name}')


def staged_path(root, kind, release_id, name):
    if kind not in ('promote', 'rollback') or name not in cfg.TARGET_NAMES:
        raise UnsafePath('bad staged file request')
    check_release_id(release_id)
    return safe_path(root, f'{cfg.INTEGRATION_REL}/.c1{kind}-{release_id}-{name}.tmp')


def state_path(root, *parts):
    return safe_path(root, '/'.join((cfg.STATE_REL,) + tuple(parts)))


def assert_writable(root, path):
    """The single gate every write/remove passes through."""
    path = Path(path)
    integration = safe_path(root, cfg.INTEGRATION_REL)
    state = safe_path(root, cfg.STATE_REL)
    if _same(path.parent, integration) and (path.name in cfg.TARGET_NAMES or STAGED_RE.match(path.name)):
        return safe_path(root, f'{cfg.INTEGRATION_REL}/{path.name}')
    if _within(path, state) and not _same(path, state):
        return safe_path(root, Path(os.path.relpath(path, root)).as_posix())
    raise UnsafePath(f'write outside the release allowlist refused: {path}')


def _write_new(root, path, data):
    """Create a new file; never overwrite."""
    path = assert_writable(root, path)
    with open(path, 'xb') as f:
        f.write(data)
        f.flush()
        os.fsync(f.fileno())
    return path


def _write_replace(root, path, data):
    """Atomically replace a state file (temp sibling + os.replace)."""
    path = assert_writable(root, path)
    tmp = assert_writable(root, path.with_name(f'{path.name}.{uuid.uuid4().hex[:8]}.part'))
    with open(tmp, 'xb') as f:
        f.write(data)
        f.flush()
        os.fsync(f.fileno())
    _replace(tmp, path)
    return path


def _replace(src, dst, attempts=12, delay=0.05):
    """os.replace with a short retry: on Windows a scanner/indexer briefly holding dst makes the rename fail with
    'Access is denied'. A persistent failure (e.g. a read-only target) is re-raised after the retries."""
    for i in range(attempts):
        try:
            os.replace(src, dst)
            return
        except PermissionError:
            if i == attempts - 1:
                raise
            time.sleep(delay * (i + 1))


def _remove(root, path):
    path = assert_writable(root, path)
    if path.name in cfg.TARGET_NAMES and _same(path.parent, safe_path(root, cfg.INTEGRATION_REL)):
        raise UnsafePath('targets are never deleted')
    if path.exists():
        os.remove(path)


def _mkdir_checked(root, rel):
    p = safe_path(root, rel)
    try:
        os.mkdir(p)
    except FileExistsError:                 # another process may create it concurrently (before the lock is taken)
        pass
    p = safe_path(root, rel)                # re-check: must be a real directory, not a junction/symlink
    if not p.is_dir():
        raise UnsafePath(f'state path is not a directory: {p}')
    return p


def _makedirs(root, rel_parts):
    """Create state directories one component at a time (each checked)."""
    base = cfg.STATE_REL.split('/')
    for i in range(1, len(base) + 1):
        _mkdir_checked(root, '/'.join(base[:i]))
    cur_rel = cfg.STATE_REL
    for part in rel_parts:
        cur_rel = f'{cur_rel}/{part}'
        _mkdir_checked(root, cur_rel)
    return safe_path(root, cur_rel)


# ------------------------------------------------------------------ fault injection (disposable roots only)
def _fault(live, stage, actions=None):
    if live:
        return
    spec = os.environ.get('C1_RELEASE_FAULT', '')
    for item in filter(None, spec.split(',')):
        st, _, mode = item.partition(':')
        if st != stage:
            continue
        if mode == 'raise':
            raise InjectedFault(f'injected failure at {stage}')
        if mode == 'exit':
            os._exit(70)                      # hard interruption: no cleanup, no finally blocks
        if mode.startswith('sleep='):
            marker = os.environ.get('C1_RELEASE_FAULT_MARKER')
            if marker:
                Path(marker).write_text(stage)
            time.sleep(float(mode.split('=', 1)[1]))
        elif actions and mode in actions:
            actions[mode]()


# ------------------------------------------------------------------ cross-process lock
if os.name == 'nt':
    import msvcrt

    def _try_lock(fd):
        os.lseek(fd, 0, os.SEEK_SET)
        msvcrt.locking(fd, msvcrt.LK_NBLCK, 1)

    def _unlock(fd):
        os.lseek(fd, 0, os.SEEK_SET)
        msvcrt.locking(fd, msvcrt.LK_UNLCK, 1)
else:
    import fcntl

    def _try_lock(fd):
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)

    def _unlock(fd):
        fcntl.flock(fd, fcntl.LOCK_UN)


class ReleaseLock:
    """OS byte-range lock (msvcrt on Windows, flock elsewhere) on state/release.lock.

    The OS drops the lock when the holding process exits or is killed, so a lock left behind by a dead process is
    detected by actually acquiring it, never by its age. Holder details are kept in state/release.lock.json for
    diagnostics; an entry with released=false found on acquisition means the previous operation was interrupted.
    """

    def __init__(self, root, operation, release_id):
        self.root, self.operation, self.release_id = root, operation, release_id
        self.fd = None
        self.previous_unreleased = None

    def __enter__(self):
        _makedirs(self.root, [])
        lock_file = assert_writable(self.root, state_path(self.root, 'release.lock'))
        info_file = state_path(self.root, 'release.lock.json')
        fd = os.open(lock_file, os.O_RDWR | os.O_CREAT, 0o644)
        try:
            _try_lock(fd)
        except OSError:
            os.close(fd)
            holder = _read_json(info_file)
            raise LockBusy(f'another release operation holds the lock: {holder}', holder)
        self.fd = fd
        try:
            prev = _read_json(info_file)
            if prev and not prev.get('released', True):
                self.previous_unreleased = prev
            self.info = {'pid': os.getpid(), 'host': socket.gethostname(), 'user': getpass.getuser(), 'operation': self.operation,
                         'release_id': self.release_id, 'acquired_at': now_utc(), 'token': uuid.uuid4().hex, 'released': False}
            _write_replace(self.root, info_file, json.dumps(self.info, indent=2).encode('utf-8'))
        except BaseException:
            _unlock(fd)
            os.close(fd)
            raise
        return self

    def __exit__(self, *exc):
        try:
            self.info.update(released=True, released_at=now_utc())
            _write_replace(self.root, state_path(self.root, 'release.lock.json'), json.dumps(self.info, indent=2).encode('utf-8'))
        finally:
            try:
                _unlock(self.fd)
            finally:
                os.close(self.fd)
        return False


def lock_state(root):
    """Read-only probe: is the lock currently held by a live process?"""
    lock_file = state_path(root, 'release.lock')
    info = _read_json(state_path(root, 'release.lock.json'))
    if not lock_file.exists():
        return {'held': False, 'info': info}
    fd = os.open(lock_file, os.O_RDWR)
    try:
        try:
            _try_lock(fd)
        except OSError:
            return {'held': True, 'info': info}
        _unlock(fd)
        return {'held': False, 'info': info, 'previous_holder_unreleased': bool(info and not info.get('released', True))}
    finally:
        os.close(fd)


def _read_json(path):
    try:
        return json.loads(Path(path).read_text(encoding='utf-8'))
    except FileNotFoundError:
        return None
    except (ValueError, OSError):
        return {'unreadable': str(path)}


# ------------------------------------------------------------------ manifests
def manifest_path(root, release_id):
    return state_path(root, 'releases', release_id, 'release_manifest.json')


def save_manifest(root, manifest):
    manifest['updated_at'] = now_utc()
    manifest['manifest_sha256'] = seal(manifest)
    _write_replace(root, manifest_path(root, manifest['release_id']), (json.dumps(manifest, indent=2, sort_keys=True) + '\n').encode('utf-8'))


def load_manifest(root, release_id):
    check_release_id(release_id)
    p = manifest_path(root, release_id)
    if not p.exists():
        raise ManifestInvalid(f'no release manifest for {release_id}')
    try:
        m = json.loads(p.read_text(encoding='utf-8'))
    except ValueError as e:
        raise ManifestInvalid(f'release manifest is not valid JSON: {e}')
    if m.get('manifest_sha256') != seal(m):
        raise ManifestInvalid('release manifest integrity check failed (content does not match manifest_sha256)')
    required = {'release_id', 'status', 'targets', 'methodology_id', 'lpn_min_share'}
    if not required <= set(m) or m['release_id'] != release_id:
        raise ManifestInvalid('release manifest is missing fields or names another release')
    if set(m['targets']) != set(cfg.TARGET_NAMES):
        raise ManifestInvalid('release manifest targets are not exactly the allowlist')
    for name, t in m['targets'].items():
        if t.get('new_sha256') != cfg.SHADOW_SHA256[name] or not re.fullmatch(r'[0-9a-f]{64}', str(t.get('old_sha256'))):
            raise ManifestInvalid(f'release manifest hashes invalid for {name}')
    return m


def list_releases(root):
    d = state_path(root, 'releases')
    out = []
    if not d.exists():
        return out
    for sub in sorted(d.iterdir()):
        if not sub.is_dir() or _is_reparse(sub):
            out.append({'release_id': sub.name, 'status': 'INVALID_ENTRY'})
            continue
        try:
            m = load_manifest(root, sub.name)
            out.append({'release_id': sub.name, 'status': m['status'], 'started_at': m.get('started_at'),
                        'manifest_sha256': m['manifest_sha256']})
        except ReleaseError as e:
            status = 'NOT_STARTED' if not (sub / 'release_manifest.json').exists() else 'MANIFEST_INVALID'
            out.append({'release_id': sub.name, 'status': status, 'error': str(e)})
    return out


def verify_backup(root, release_id, expected_old):
    bdir = state_path(root, 'backups', release_id)
    mfile = bdir / 'backup_manifest.json'
    if not mfile.exists():
        raise BackupVerificationFailed(f'backup manifest missing for {release_id}')
    bm = json.loads(mfile.read_text(encoding='utf-8'))
    if bm.get('manifest_sha256') != seal(bm) or bm.get('release_id') != release_id:
        raise BackupVerificationFailed('backup manifest integrity check failed')
    data = {}
    for name in cfg.TARGET_NAMES:
        entry = bm['files'].get(name, {})
        p = safe_path(root, f'{cfg.STATE_REL}/backups/{release_id}/{name}')
        b = p.read_bytes()
        if sha256_bytes(b) != entry.get('sha256') or entry.get('sha256') != expected_old[name]:
            raise BackupVerificationFailed(f'backup bytes for {name} do not match the recorded original hash')
        data[name] = b
    return bm, data


# ------------------------------------------------------------------ content validation
_REBUILD_CACHE = {}


def load_canon(root):
    raw = pd.read_csv(safe_path(root, 'lookups/LSOA to DNO.csv'), encoding='utf-8-sig').dropna(subset=['LSOA21CD'])
    canon = raw[raw['Majority Licence area'].isin(['EPN', 'LPN', 'SPN'])][['LSOA21CD', 'Majority Licence area', 'LAD22CD', 'LAD22NM']]
    canon = canon.rename(columns={'Majority Licence area': 'DNO'})
    laep = pd.read_csv(safe_path(root, f'{cfg.INTEGRATION_REL}/geography_lookup.csv'))[['LSOA21CD', 'LAEP']]
    if canon.LSOA21CD.duplicated().any() or laep.LSOA21CD.duplicated().any():
        raise ValueError('duplicate LSOAs in geography lookups')
    canon = canon.merge(laep, on='LSOA21CD', how='left')
    if len(canon) != iw.EXPECTED_LSOAS or canon.DNO.value_counts().to_dict() != iw.EXPECTED_DNO_LSOAS:
        raise ValueError('canonical geography does not have the expected 11,023 UKPN LSOAs')
    return canon.set_index('LSOA21CD')


def rebuild_matches_shadow(root, shadow_bytes):
    """Re-derive the three files from the validated C1 LSOA source and compare bytes with the shadow files."""
    src = safe_path(root, f'{cfg.C1_OUTPUTS_REL}/c1_lsoa_quarterly.csv')
    key = (sha256_file(src), sha256_file(safe_path(root, 'lookups/LSOA to DNO.csv')),
           sha256_file(safe_path(root, f'{cfg.INTEGRATION_REL}/geography_lookup.csv')),
           tuple(sha256_bytes(shadow_bytes[n]) for n in cfg.TARGET_NAMES))
    if key not in _REBUILD_CACHE:
        canon = load_canon(root)
        c1 = pd.read_csv(src)
        lsoa = iw.validate_c1_lsoa(c1, canon, cfg.EXPECTED_PERIODS)
        if int(c1.lpn_uplift.sum()) != 0:
            raise ValueError('LPN floor uplift is non-zero in the C1 source (expected inactive)')
        frames = iw.build_integration_frames(lsoa, canon)
        result = {n: iw.to_csv_bytes(f) == shadow_bytes[n] for n, f in zip(cfg.TARGET_NAMES, frames)}
        _REBUILD_CACHE[key] = result
    return _REBUILD_CACHE[key]


def validate_actuals(files, geography):
    """Structural checks on a set of EV actuals files (name -> bytes). Returns a list of failures (empty = valid)."""
    fails = []
    frames = {}
    for name in cfg.TARGET_NAMES:
        if name not in files:
            fails.append(f'{name}: missing')
            continue
        try:
            frames[name] = pd.read_csv(io.BytesIO(files[name]))
        except Exception as e:  # noqa: BLE001
            fails.append(f'{name}: unreadable CSV ({e})')
    if fails:
        return fails
    cols = {'ev_actuals_dno.csv': iw.DNO_COLUMNS, 'ev_actuals_la.csv': iw.LA_COLUMNS, 'ev_actuals_laep.csv': iw.LAEP_COLUMNS}
    keys = {'ev_actuals_dno.csv': ['period', 'DNO', 'EV_Type'], 'ev_actuals_la.csv': ['period', 'LAD22CD', 'EV_Type'],
            'ev_actuals_laep.csv': ['period', 'LAEP', 'EV_Type']}
    for name, f in frames.items():
        if list(f.columns) != cols[name]:
            fails.append(f'{name}: columns differ from the production schema')
            continue
        if len(f) != cfg.EXPECTED_ROWS[name]:
            fails.append(f'{name}: {len(f)} rows, expected {cfg.EXPECTED_ROWS[name]}')
        if sorted(f.period.astype(str).unique()) != cfg.EXPECTED_PERIODS:
            fails.append(f'{name}: periods {sorted(f.period.astype(str).unique())[:3]}... not the five C1 quarters')
        if (f.period.astype(str) < cfg.EXPECTED_PERIODS[0]).any():
            fails.append(f'{name}: contains pre-2025-03 (Method A history) rows')
        if f.duplicated(keys[name]).any() or f[keys[name]].isna().any().any():
            fails.append(f'{name}: duplicate or null keys')
        if set(f.EV_Type) != {'BEV', 'PHEV'}:
            fails.append(f'{name}: EV_Type values {sorted(set(f.EV_Type))}')
        v = f['install_count' if 'install_count' in f else 'value']
        if not pd.api.types.is_integer_dtype(v) or (v < 0).any():
            fails.append(f'{name}: stock values must be non-negative integers')
    if fails:
        return fails
    dno, la, laep = (frames[n] for n in cfg.TARGET_NAMES)
    for per, exp in cfg.EXPECTED_TOTALS.items():
        d = dno[dno.period == per]
        got = {'UKPN': int(d.install_count.sum()), **{k: int(d[d.DNO == k].install_count.sum()) for k in ('EPN', 'LPN', 'SPN')},
               **{k: int(d[d.EV_Type == k].install_count.sum()) for k in ('BEV', 'PHEV')}}
        if got != exp:
            fails.append(f'{per}: DNO totals {got} != expected C1 totals {exp}')
    by = lambda f, v: f.groupby(['period', 'EV_Type'])[v].sum()
    if not by(dno, 'install_count').equals(by(la, 'value')):
        fails.append('LA totals do not reconcile with DNO totals for every period/fuel')
    if la.LAD22CD.nunique() != cfg.EXPECTED_LAS or laep.LAEP.nunique() != cfg.EXPECTED_LAEPS:
        fails.append(f'coverage: {la.LAD22CD.nunique()} LAs / {laep.LAEP.nunique()} LAEPs')
    lad_laep = geography.dropna(subset=['LAEP']).drop_duplicates('LAD22CD').set_index('LAD22CD').LAEP
    via = la[la.LAD22CD.isin(lad_laep.index)].assign(LAEP=lambda x: x.LAD22CD.map(lad_laep)).groupby(['period', 'LAEP', 'EV_Type']).value.sum()
    if not via.sort_index().equals(laep.set_index(['period', 'LAEP', 'EV_Type']).value.sort_index()):
        fails.append('LAEP values are not the sum of their mapped LAs')
    return fails


def classify_targets(hashes, files=None):
    """C1 / METHOD_A (pinned) / METHOD_A_VARIANT / UNKNOWN per file, plus an overall state."""
    per = {}
    for name, h in hashes.items():
        if h is None:
            per[name] = 'MISSING'
        elif h == cfg.SHADOW_SHA256[name]:
            per[name] = 'C1'
        elif h == cfg.METHOD_A_TARGET_SHA256[name]:
            per[name] = 'METHOD_A'
        elif h[:16] in cfg.KNOWN_METHOD_A_VARIANTS:
            per[name] = 'METHOD_A_VARIANT'
        else:
            per[name] = 'UNKNOWN'
    states = set(per.values())
    overall = states.pop() if len(states) == 1 else 'MIXED'
    signals = {}
    for name, data in (files or {}).items():
        try:
            f = pd.read_csv(io.BytesIO(data))
            v = 'install_count' if 'install_count' in f else 'value'
            signals[name] = {'first_period': str(f.period.min()), 'last_period': str(f.period.max()), 'n_periods': int(f.period.nunique()),
                             'latest_total': int(f[f.period == f.period.max()][v].sum())}
        except Exception as e:  # noqa: BLE001
            signals[name] = {'error': str(e)}
    return {'per_file': per, 'overall': overall, 'signals': signals}


def protected_snapshot(root):
    """sha256 of every file under the protected trees, excluding targets, staged files and the release state dir."""
    integration = safe_path(root, cfg.INTEGRATION_REL)
    state = Path(root).joinpath(*cfg.STATE_REL.split('/'))
    snap = {}
    for rel in cfg.PROTECTED_TREES:
        top = Path(root).joinpath(*rel.split('/'))
        if not top.exists():
            continue
        paths = [top] if top.is_file() else (Path(d) / f for d, _, fs in os.walk(top) for f in fs)
        for p in paths:
            if _within(p, state):
                continue
            if _same(p.parent, integration) and (p.name in cfg.TARGET_NAMES or STAGED_RE.match(p.name)):
                continue
            snap[Path(os.path.relpath(p, root)).as_posix()] = sha256_file(p)
    return dict(sorted(snap.items()))


def git_provenance():
    try:
        head = subprocess.run(['git', 'rev-parse', 'HEAD'], cwd=cfg.REPO, capture_output=True, text=True, timeout=30).stdout.strip()
    except Exception:  # noqa: BLE001
        head = None
    return {'repository_head': head or None, 'tool_sha256': {p.name: sha256_file(p) for p in TOOL_FILES}}


# ------------------------------------------------------------------ preflight (read-only)
def preflight(root, require='METHOD_A'):
    root, live = canonical_root(root)
    checks, facts = [], {}

    def check(name, fn):
        try:
            ok, detail = fn()
        except Exception as e:  # noqa: BLE001 - every failure fails closed
            ok, detail = False, f'{type(e).__name__}: {e}'
        checks.append({'check': name, 'pass': bool(ok), 'detail': detail})
        return ok

    def paths():
        for n in cfg.TARGET_NAMES:
            p = target_path(root, n)
            if not p.is_file():
                return False, f'target missing or not a regular file: {p}'
        for rel in (cfg.SHADOW_REL, cfg.C1_OUTPUTS_REL, cfg.ARCHIVE_REL, 'lookups'):
            safe_path(root, rel)
        return True, 'targets present; no symlink/junction below the root'

    def targets():
        hashes = {n: sha256_file(target_path(root, n)) for n in cfg.TARGET_NAMES}
        facts['target_sha256'] = hashes
        facts['target_state'] = classify_targets(hashes)
        state = facts['target_state']['overall']
        return state == require, f'targets are {state} (required {require})'

    def shadow():
        data = {n: safe_path(root, f'{cfg.SHADOW_REL}/{n}').read_bytes() for n in cfg.TARGET_NAMES}
        facts['shadow_bytes'] = data
        bad = [n for n in cfg.TARGET_NAMES if sha256_bytes(data[n]) != cfg.SHADOW_SHA256[n]]
        return not bad, f'shadow hash mismatch: {bad}' if bad else 'shadow files match pinned C1 hashes'

    def pinned():
        pins = effective_pins(root)
        facts['pins'] = pins
        bad = [rel for rel, h in pins.items() if not safe_path(root, rel).is_file() or sha256_file(safe_path(root, rel)) != h]
        repinned = len(read_pin_log(root))
        return not bad, f'changed or missing: {bad}' if bad else f'{len(pins)} pinned files match ({repinned} approved re-pins)'

    def shadow_manifest():
        m = json.loads(safe_path(root, f'{cfg.SHADOW_REL}/manifest.json').read_text(encoding='utf-8'))
        problems = []
        if m.get('methodology_id') != cfg.METHODOLOGY_ID or m.get('lpn_min_share') != cfg.LPN_MIN_SHARE or m.get('periods') != cfg.EXPECTED_PERIODS:
            problems.append('methodology / floor / periods')
        for n in cfg.TARGET_NAMES:
            if m['outputs'][n]['sha256'] != cfg.SHADOW_SHA256[n] or m['outputs'][n]['rows'] != cfg.EXPECTED_ROWS[n]:
                problems.append(f'output entry {n}')
        for rel, h in m['inputs_sha256'].items():
            if sha256_file(safe_path(root, rel)) != h:
                problems.append(f'input {rel}')
        return not problems, problems or 'provenance manifest consistent with the files'

    def c1_meta():
        meta = json.loads(safe_path(root, f'{cfg.C1_OUTPUTS_REL}/run_metadata.json').read_text(encoding='utf-8'))
        ok = meta.get('methodology_id') == cfg.METHODOLOGY_ID and meta.get('lpn_minimum_share', {}).get('C1') == cfg.LPN_MIN_SHARE
        return ok, f"methodology {meta.get('methodology_id')}, C1 LPN floor {meta.get('lpn_minimum_share', {}).get('C1')}"

    def rebuild():
        res = rebuild_matches_shadow(root, facts['shadow_bytes'])
        return all(res.values()), f'rebuilt from C1 LSOA source, byte-equal: {res}; LPN uplift 0'

    def structure():
        geo = pd.read_csv(safe_path(root, f'{cfg.INTEGRATION_REL}/geography_lookup.csv'))
        fails = validate_actuals(facts['shadow_bytes'], geo)
        return not fails, fails or 'rows 30/1210/140, five quarters, keys, BEV/PHEV, DNO=LA, LAEP=sum(LA), totals'

    def metadata():
        meta = json.loads(safe_path(root, f'{cfg.INTEGRATION_REL}/metadata.json').read_text(encoding='utf-8'))
        return meta.get('latest_actual_period') == cfg.EXPECTED_PERIODS[-1], f"latest_actual_period={meta.get('latest_actual_period')}"

    def archive():
        h = pd.read_csv(safe_path(root, f'{cfg.ARCHIVE_REL}/method_a_dno_history_before_2025-03.csv'))
        ok = len(h) == 318 and (h.period < cfg.EXPECTED_PERIODS[0]).all() and set(h.methodology_id) == {'A_PRODUCTION_LEGACY'}
        return ok, f'{len(h)} legacy rows {h.period.min()}..{h.period.max()}'

    def releases():
        rel = list_releases(root) if Path(root).joinpath(*cfg.STATE_REL.split('/')).exists() else []
        facts['releases'] = rel
        bad = [r for r in rel if r['status'] in INCOMPLETE or r['status'] in ('MANIFEST_INVALID', 'INVALID_ENTRY')]
        return not bad, f'incomplete or invalid releases: {bad}' if bad else f'{len(rel)} earlier releases, none incomplete'

    def leftovers():
        integ = safe_path(root, cfg.INTEGRATION_REL)
        left = [p.name for p in integ.iterdir() if STAGED_RE.match(p.name) or p.name.startswith('.c1')]
        return not left, f'staged leftovers: {left}' if left else 'no staged files'

    def path_lengths():
        longest_id = 'c1-00000000-' + 'x' * 40
        planned = [str(manifest_path(root, longest_id)) + '.00000000.part',
                   str(state_path(root, 'backups', longest_id, 'backup_manifest.json')),
                   str(staged_path(root, 'rollback', longest_id, 'ev_actuals_laep.csv')),
                   str(state_path(root, 'release.lock.json')) + '.00000000.part']
        n = max(len(p) for p in planned)
        return (n < 250 or os.name != 'nt'), f'longest planned path {n} characters (Windows limit 260 without long-path support)'

    def protected():
        facts['protected'] = protected_snapshot(root)
        return True, f"{len(facts['protected'])} protected files hashed"

    for name, fn in [('paths_safe', paths), ('targets_state', targets), ('shadow_hashes', shadow), ('pinned_files', pinned),
                     ('shadow_manifest', shadow_manifest), ('c1_metadata', c1_meta), ('rebuild_from_c1_source', rebuild),
                     ('shadow_structure', structure), ('metadata_latest_actual', metadata), ('method_a_archive', archive),
                     ('no_incomplete_release', releases), ('no_staged_leftovers', leftovers), ('path_lengths', path_lengths),
                     ('protected_snapshot', protected)]:
        if name in ('rebuild_from_c1_source', 'shadow_structure') and 'shadow_bytes' not in facts:
            checks.append({'check': name, 'pass': False, 'detail': 'skipped: shadow files unreadable'})
            continue
        check(name, fn)

    ok = all(c['pass'] for c in checks)
    digest_body = {'checks': [(c['check'], c['pass']) for c in checks], 'targets': facts.get('target_sha256'),
                   'protected': facts.get('protected'), 'shadow': cfg.SHADOW_SHA256, 'require': require,
                   'pins': facts.get('pins'), 'pin_log': pin_log_sha256(root)}
    digest = sha256_bytes(json.dumps(digest_body, sort_keys=True).encode('utf-8'))
    return {'ok': ok, 'root': str(root), 'live': live, 'require': require, 'checks': checks, 'digest': digest,
            'target_sha256': facts.get('target_sha256'), 'target_state': facts.get('target_state'),
            'protected': facts.get('protected', {}), 'releases': facts.get('releases', [])}


def confirm_token(op, release_id, value=''):
    return f'{op} {release_id} {value[:16]}'.strip()


# ------------------------------------------------------------------ promotion
def _stage(root, live, kind, release_id, payload):
    staged = {}
    for name, data in payload.items():
        p = staged_path(root, kind, release_id, name)
        _write_new(root, p, data)
        if sha256_file(p) != sha256_bytes(data):
            raise ReleaseError(f'staged file verification failed for {name}')
        staged[name] = p
    _fault(live, f'after_stage_{kind}')
    return staged


def _clean_staged(root, release_id):
    integ = safe_path(root, cfg.INTEGRATION_REL)
    for p in integ.iterdir():
        m = STAGED_RE.match(p.name)
        if m and m.group(2) == release_id:
            _remove(root, p)


def _restore_originals(root, live, manifest, backup_data, kind):
    """Put the original bytes back for every target this release replaced (or that holds the C1 bytes).

    Deterministic rule per target:
      hash == original                         -> nothing to do
      hash == C1, or the manifest says the tool replaced/restored it -> restore the original bytes from the verified backup
      otherwise (never touched by this release, unknown bytes)       -> external change: left untouched and reported
    """
    current = {n: sha256_file(target_path(root, n)) for n in cfg.TARGET_NAMES}
    restore, external = [], {}
    for n in cfg.TARGET_NAMES:
        t = manifest['targets'][n]
        if current[n] == t['old_sha256']:
            continue
        if current[n] == t['new_sha256'] or t['state'] in ('replaced', 'restored'):
            restore.append(n)
        else:
            external[n] = current[n]
    if restore and backup_data is None:
        raise RecoveryRequired(f'{restore} must be restored but no verified backup is available')
    _clean_staged(root, manifest['release_id'])
    staged = _stage(root, live, 'rollback', manifest['release_id'], {n: backup_data[n] for n in restore}) if restore else {}
    for i, name in enumerate(restore, 1):
        _replace(staged[name], target_path(root, name))
        manifest['targets'][name]['state'] = 'restored'
        save_manifest(root, manifest)
        _fault(live, f'{kind}_after_restore_{i}')
    final = {n: sha256_file(target_path(root, n)) for n in cfg.TARGET_NAMES}
    bad = [n for n in restore if final[n] != manifest['targets'][n]['old_sha256']]
    if bad:
        raise RecoveryRequired(f'restore verification failed for {bad}')
    return {'restored': restore, 'external_unknown': external, 'final_sha256': final}


def promote(root, release_id, confirm):
    check_release_id(release_id)
    root, live = canonical_root(root)
    if live and not cfg.LIVE_RELEASE_ENABLED:
        raise LiveReleaseBlocked('promotion against the live repository is disabled (release_config.LIVE_RELEASE_ENABLED=False)')
    pf = preflight(root)                                        # lock-free and read-only: a failure writes nothing
    if not pf['ok']:
        raise PreflightFailed('preflight failed; nothing was written', [c for c in pf['checks'] if not c['pass']])
    if confirm != confirm_token('PROMOTE', release_id, pf['digest']):
        raise ConfirmationMismatch(f'confirmation must be exactly: PROMOTE {release_id} <first 16 characters of the preflight digest>; '
                                   'run preflight and copy its digest (a changed state changes the digest)')
    if state_path(root, 'releases', release_id).exists() or state_path(root, 'backups', release_id).exists():
        raise ReleaseError(f'release id {release_id} has been used before; release ids are never reused')

    with ReleaseLock(root, 'promote', release_id) as lock:
        pf2 = preflight(root)
        if not pf2['ok'] or pf2['digest'] != pf['digest']:
            raise PreflightFailed('state changed between confirmation and lock; nothing was written',
                                  [c for c in pf2['checks'] if not c['pass']])
        _makedirs(root, ['releases'])
        rdir = state_path(root, 'releases', release_id)
        try:
            os.mkdir(rdir)                                      # exclusive: a release id is used exactly once
        except FileExistsError:
            raise ReleaseError(f'release id {release_id} has been used before; release ids are never reused')
        old = pf2['target_sha256']
        manifest = {
            'release_id': release_id, 'operation': 'promote', 'status': 'PROMOTING', 'started_at': now_utc(),
            'methodology_id': cfg.METHODOLOGY_ID, 'lpn_min_share': cfg.LPN_MIN_SHARE, 'expected_periods': cfg.EXPECTED_PERIODS,
            'coverage': {'licence_areas': ['EPN', 'LPN', 'SPN'], 'local_authorities': cfg.EXPECTED_LAS, 'laeps': cfg.EXPECTED_LAEPS,
                         'lsoas': iw.EXPECTED_LSOAS, 'fuels': ['BEV', 'PHEV'], 'rows': cfg.EXPECTED_ROWS},
            'targets': {n: {'path': str(target_path(root, n)), 'old_sha256': old[n], 'new_sha256': cfg.SHADOW_SHA256[n], 'state': 'pending'}
                        for n in cfg.TARGET_NAMES},
            'sources_sha256': {rel: h for rel, h in cfg.PINNED_SHA256.items() if rel.startswith(cfg.C1_OUTPUTS_REL) or rel.startswith(cfg.SHADOW_REL)},
            'shadow_sha256': cfg.SHADOW_SHA256,
            'preflight': {'digest': pf2['digest'], 'checks': pf2['checks'], 'protected_files': len(pf2['protected'])},
            'provenance': {**git_provenance(), 'operator': getpass.getuser(), 'host': socket.gethostname(), 'pid': os.getpid(),
                           'root': str(root), 'live': live},
            'previous_lock_unreleased': lock.previous_unreleased, 'phases': [{'phase': 'started', 'at': now_utc()}],
            'backup': None, 'promotion_qa': None, 'rollback': None,
        }
        _write_new(root, rdir / 'protected_snapshot.json', json.dumps(pf2['protected'], indent=0, sort_keys=True).encode('utf-8'))
        manifest['protected_snapshot_sha256'] = sha256_file(rdir / 'protected_snapshot.json')
        save_manifest(root, manifest)

        def mutate_target():
            p = target_path(root, cfg.TARGET_NAMES[0])
            p.write_bytes(p.read_bytes() + b'\r\n')

        def corrupt_backup():
            p = state_path(root, 'backups', release_id, cfg.TARGET_NAMES[0])
            p.write_bytes(b'X' + p.read_bytes()[1:])

        def corrupt_published():
            p = target_path(root, cfg.TARGET_NAMES[1])
            p.write_bytes(p.read_bytes()[:-3])

        def touch_protected():
            p = safe_path(root, f'{cfg.INTEGRATION_REL}/monthly_forecast_dno.csv')
            p.write_bytes(p.read_bytes() + b'\r\n')

        backup_data = None
        try:
            _fault(live, 'after_preflight', {'mutate_target': mutate_target})
            backup_data, bm = _make_backup(root, live, release_id, old, manifest, corrupt_backup)
            manifest['backup'] = bm
            manifest['phases'].append({'phase': 'backup_verified', 'at': now_utc()})
            save_manifest(root, manifest)
            payload = {n: safe_path(root, f'{cfg.SHADOW_REL}/{n}').read_bytes() for n in cfg.TARGET_NAMES}
            if any(sha256_bytes(payload[n]) != cfg.SHADOW_SHA256[n] for n in cfg.TARGET_NAMES):
                raise PreflightFailed('shadow files changed after preflight')
            staged = _stage(root, live, 'promote', release_id, payload)
            manifest['phases'].append({'phase': 'staged', 'at': now_utc()})
            save_manifest(root, manifest)
            for i, name in enumerate(cfg.TARGET_NAMES, 1):
                if sha256_file(target_path(root, name)) != old[name]:
                    raise TargetChanged(f'{name} changed since preflight; replacement stopped before touching it')
                _fault(live, f'before_replace_{i}')
                _replace(staged[name], target_path(root, name))
                manifest['targets'][name]['state'] = 'replaced'
                manifest['phases'].append({'phase': f'replaced {name}', 'at': now_utc()})
                save_manifest(root, manifest)
                _fault(live, f'after_replace_{i}')
            _fault(live, 'after_publish', {'corrupt_published': corrupt_published, 'touch_protected': touch_protected})
            manifest['promotion_qa'] = post_promotion_validation(root, pf2['protected'])
            manifest['status'] = 'COMPLETE'
            manifest['completed_at'] = now_utc()
            manifest['new_production_sha256'] = {n: sha256_file(target_path(root, n)) for n in cfg.TARGET_NAMES}
            manifest['phases'].append({'phase': 'complete', 'at': now_utc()})
            save_manifest(root, manifest)
            return manifest
        except BaseException as exc:
            _auto_restore(root, live, manifest, backup_data, exc)
            raise


def _make_backup(root, live, release_id, old, manifest, corrupt_backup):
    _makedirs(root, ['backups'])
    bdir = state_path(root, 'backups', release_id)
    os.mkdir(bdir)                                              # FileExistsError: never overwrite an earlier backup
    files = {}
    for name in cfg.TARGET_NAMES:
        data = target_path(root, name).read_bytes()
        if sha256_bytes(data) != old[name]:
            raise BackupVerificationFailed(f'{name} changed since preflight (backup refused)')
        _write_new(root, bdir / name, data)
        files[name] = {'original_path': str(target_path(root, name)), 'backup_path': str(bdir / name),
                       'sha256': old[name], 'bytes': len(data)}
    _fault(live, 'after_backup_copy', {'corrupt_backup': corrupt_backup})
    bm = {'release_id': release_id, 'created_at': now_utc(), 'kind': 'pre-promotion backup of the three EV actuals targets',
          'files': files, 'shadow_sha256': cfg.SHADOW_SHA256,
          'sources_sha256': manifest['sources_sha256'], 'provenance': manifest['provenance']}
    bm['manifest_sha256'] = seal(bm)
    _write_new(root, bdir / 'backup_manifest.json', (json.dumps(bm, indent=2, sort_keys=True) + '\n').encode('utf-8'))
    _, data = verify_backup(root, release_id, old)              # re-read and verify every byte
    return data, {'dir': str(bdir), 'manifest_sha256': sha256_file(bdir / 'backup_manifest.json'),
                  'files': {n: {'path': f['backup_path'], 'sha256': f['sha256']} for n, f in files.items()}}


def _auto_restore(root, live, manifest, backup_data, exc):
    manifest['failure'] = {'type': type(exc).__name__, 'code': getattr(exc, 'code', None), 'message': str(exc), 'at': now_utc()}
    try:
        result = _restore_originals(root, live, manifest, backup_data, 'auto')
        # External changes to targets this release never touched are reported, not overwritten; preflight then fails
        # closed on the next attempt because the targets are no longer the pinned originals.
        manifest['status'] = 'FAILED_ROLLED_BACK' if result['restored'] else 'FAILED_NOT_APPLIED'
        manifest['auto_restore'] = result
    except BaseException as e2:  # noqa: BLE001
        manifest['status'] = 'RECOVERY_REQUIRED'
        manifest['auto_restore'] = {'error': f'{type(e2).__name__}: {e2}'}
    manifest['phases'].append({'phase': manifest['status'], 'at': now_utc()})
    try:
        save_manifest(root, manifest)
    except BaseException:  # noqa: BLE001 - the manifest on disk still shows PROMOTING, which also requires recovery
        pass


def post_promotion_validation(root, protected_before):
    files = {n: target_path(root, n).read_bytes() for n in cfg.TARGET_NAMES}
    qa = {'published_sha256': {n: sha256_bytes(b) for n, b in files.items()}}
    qa['hashes_match_shadow'] = all(qa['published_sha256'][n] == cfg.SHADOW_SHA256[n] for n in cfg.TARGET_NAMES)
    geo = pd.read_csv(safe_path(root, f'{cfg.INTEGRATION_REL}/geography_lookup.csv'))
    qa['structure_failures'] = validate_actuals(files, geo)
    qa['target_state'] = classify_targets(qa['published_sha256'])['overall']
    after = protected_snapshot(root)
    qa['protected_changed'] = sorted(k for k in set(protected_before) | set(after) if protected_before.get(k) != after.get(k))
    qa['staged_leftovers'] = [p.name for p in safe_path(root, cfg.INTEGRATION_REL).iterdir() if p.name.startswith('.c1')]
    qa['pass'] = (qa['hashes_match_shadow'] and not qa['structure_failures'] and qa['target_state'] == 'C1'
                  and not qa['protected_changed'] and not qa['staged_leftovers'])
    if not qa['pass']:
        raise PostValidationFailed(f'post-promotion validation failed: {qa}', qa)
    return qa


# ------------------------------------------------------------------ rollback and recovery
def rollback(root, release_id, confirm):
    check_release_id(release_id)
    root, live = canonical_root(root)
    if live and not cfg.LIVE_RELEASE_ENABLED:
        raise LiveReleaseBlocked('rollback against the live repository is disabled (release_config.LIVE_RELEASE_ENABLED=False)')
    m = load_manifest(root, release_id)
    if confirm != confirm_token('ROLLBACK', release_id, m['manifest_sha256']):
        raise ConfirmationMismatch(f'confirmation must be exactly: ROLLBACK {release_id} <first 16 of manifest_sha256>')
    with ReleaseLock(root, 'rollback', release_id):
        m = load_manifest(root, release_id)
        if m['status'] != 'COMPLETE':
            raise RollbackRefused(f'release {release_id} is {m["status"]}; only a COMPLETE release can be rolled back '
                                  '(use recover for incomplete releases)')
        others = [r for r in list_releases(root) if r['release_id'] != release_id]
        if any(r['status'] in INCOMPLETE or r['status'] in ('MANIFEST_INVALID', 'INVALID_ENTRY') for r in others):
            raise RollbackRefused(f'other releases are incomplete or invalid: {others}')
        later = [r for r in others if r['status'] == 'COMPLETE' and (r.get('started_at') or '') > m['started_at']]
        if later:
            raise RollbackRefused(f'ambiguous: later completed releases exist {later}; roll back the newest first')
        old = {n: m['targets'][n]['old_sha256'] for n in cfg.TARGET_NAMES}
        _, backup_data = verify_backup(root, release_id, old)
        before = protected_snapshot(root)
        current = {n: sha256_file(target_path(root, n)) for n in cfg.TARGET_NAMES}
        conflicts = {n: h for n, h in current.items() if h not in (old[n], m['targets'][n]['new_sha256'])}
        if conflicts:
            raise RollbackRefused(f'targets changed since the release (neither C1 nor original): {conflicts}')
        m['status'] = 'ROLLING_BACK'
        m['phases'].append({'phase': 'rollback_started', 'at': now_utc()})
        save_manifest(root, m)
        result = _restore_originals(root, live, m, backup_data, 'rollback')
        after = protected_snapshot(root)
        changed = sorted(k for k in set(before) | set(after) if before.get(k) != after.get(k))
        if changed:
            m['status'] = 'RECOVERY_REQUIRED'
            save_manifest(root, m)
            raise RecoveryRequired(f'protected files changed during rollback: {changed}')
        m['status'] = 'ROLLED_BACK'
        m['rollback'] = {**result, 'at': now_utc(), 'operator': getpass.getuser(), 'protected_unchanged': True}
        m['phases'].append({'phase': 'rolled_back', 'at': now_utc()})
        save_manifest(root, m)
        return m


def recover(root, release_id, confirm):
    """Deterministic recovery of an interrupted/failed operation: restore the original files from the verified backup."""
    check_release_id(release_id)
    root, live = canonical_root(root)
    if live and not cfg.LIVE_RELEASE_ENABLED:
        raise LiveReleaseBlocked('recovery against the live repository is disabled (release_config.LIVE_RELEASE_ENABLED=False)')
    if confirm != confirm_token('RECOVER', release_id):
        raise ConfirmationMismatch(f'confirmation must be exactly: RECOVER {release_id}')
    with ReleaseLock(root, 'recover', release_id) as lock:
        m = load_manifest(root, release_id)
        if m['status'] not in INCOMPLETE:
            raise ReleaseError(f'release {release_id} is {m["status"]}; nothing to recover')
        others = [r for r in list_releases(root) if r['release_id'] != release_id and r['status'] in INCOMPLETE]
        if others:
            raise ReleaseError(f'ambiguous: other incomplete releases exist {others}')
        old = {n: m['targets'][n]['old_sha256'] for n in cfg.TARGET_NAMES}
        try:
            _, backup_data = verify_backup(root, release_id, old)
        except (BackupVerificationFailed, FileNotFoundError, OSError, ValueError):
            backup_data = None                                  # acceptable only if every target is still original
        prior = m['status']
        m['phases'].append({'phase': f'recovery_started_from_{prior}', 'at': now_utc()})
        try:
            result = _restore_originals(root, live, m, backup_data, 'recover')
            if result['external_unknown']:
                raise RecoveryRequired(f'targets changed outside the tool: {result["external_unknown"]}')
        except RecoveryRequired as e:
            m['status'] = 'RECOVERY_REQUIRED'
            m['recovery'] = {'error': str(e), 'at': now_utc(), 'manual_action': 'compare targets with the backup directory; do not promote'}
            save_manifest(root, m)
            raise
        m['status'] = 'RECOVERED_TO_ORIGINAL'
        m['recovery'] = {**result, 'from_status': prior, 'at': now_utc(), 'operator': getpass.getuser(),
                         'previous_lock_unreleased': lock.previous_unreleased}
        m['phases'].append({'phase': 'recovered_to_original', 'at': now_utc()})
        save_manifest(root, m)
        return m


# ------------------------------------------------------------------ read-only status / Method A reversion detector
def status(root):
    root, live = canonical_root(root)
    hashes, files = {}, {}
    for n in cfg.TARGET_NAMES:
        p = target_path(root, n)
        files[n] = p.read_bytes() if p.is_file() else None
        hashes[n] = sha256_bytes(files[n]) if files[n] is not None else None
    state_dir = Path(root).joinpath(*cfg.STATE_REL.split('/'))
    return {'root': str(root), 'live': live, 'targets': classify_targets(hashes, {k: v for k, v in files.items() if v is not None}),
            'target_sha256': hashes, 'releases': list_releases(root) if state_dir.exists() else [],
            'lock': lock_state(root) if state_dir.exists() else {'held': False, 'info': None}}


def verify_c1(root):
    """Read-only post-release monitor: passes only when all three targets are the validated C1 bytes and
    the content is the five-quarter C1 series (detects Method A reversion and mixed histories)."""
    st = status(root)
    problems = []
    if st['targets']['overall'] != 'C1':
        problems.append(f"targets are {st['targets']['per_file']}")
    for name, sig in st['targets']['signals'].items():
        if sig.get('first_period') != cfg.EXPECTED_PERIODS[0] or sig.get('n_periods') != len(cfg.EXPECTED_PERIODS):
            problems.append(f'{name}: periods {sig.get("first_period")}..{sig.get("last_period")} ({sig.get("n_periods")}) - Method A history or mixed series')
    dno = st['targets']['signals'].get('ev_actuals_dno.csv', {})
    if dno.get('latest_total') == cfg.METHOD_A_MARCH_2026_UKPN:
        problems.append('DNO March 2026 total equals the Method A value 908,964')
    return {'ok': not problems, 'problems': problems, **st}


# ------------------------------------------------------------------ CLI
def _print(obj):
    slim = {k: v for k, v in obj.items() if k not in ('protected',)} if isinstance(obj, dict) else obj
    print(json.dumps(slim, indent=2, default=str))


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('operation', choices=['preflight', 'promote', 'rollback', 'recover', 'status', 'verify-c1'])
    ap.add_argument('--root', required=True, help='absolute path of the environment root (no default)')
    ap.add_argument('--release-id')
    ap.add_argument('--confirm', default='')
    a = ap.parse_args(argv)
    try:
        if a.operation == 'preflight':
            r = preflight(a.root)
            r['confirm_format'] = 'PROMOTE <release-id> ' + r['digest'][:16]
            _print(r)
            return 0 if r['ok'] else PreflightFailed.exit_code
        if a.operation == 'status':
            _print(status(a.root))
            return 0
        if a.operation == 'verify-c1':
            r = verify_c1(a.root)
            _print(r)
            return 0 if r['ok'] else 6
        fn = {'promote': promote, 'rollback': rollback, 'recover': recover}[a.operation]
        _print(fn(a.root, a.release_id, a.confirm))
        return 0
    except ReleaseError as e:
        _print({'error': e.code, 'message': str(e), 'detail': e.detail})
        return e.exit_code
    except Exception as e:  # noqa: BLE001 - unexpected errors still produce a machine-readable result
        _print({'error': 'UNEXPECTED_ERROR', 'message': f'{type(e).__name__}: {e}', 'detail': None})
        return 1


if __name__ == '__main__':
    sys.exit(main())
