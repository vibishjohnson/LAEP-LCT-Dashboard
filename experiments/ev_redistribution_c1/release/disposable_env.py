"""Build a disposable environment for release dry runs. Read-only on the repository.

The environment has the repository layout for exactly the files the release tool and the dashboard harness need:
dashboard.html, the comparison CSVs, the whole integration/ folder (Method A targets, forecasts, HP files, metadata),
the C1 shadow files, the C1 source outputs, the Method A archive and the LSOA lookup.

Usage:
  python experiments/ev_redistribution_c1/release/disposable_env.py <absolute dest outside the repository> [template]

With a template (an earlier disposable environment), large files that the release tool never writes are hard-linked
from the template instead of copied. Hard links are only ever made between disposable copies, never into the repository.
"""
import hashlib
import os
import shutil
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import release_config as cfg  # noqa: E402
import c1_release as rel  # noqa: E402

LINK_THRESHOLD = 1_000_000
# Last commit whose three integration targets hold the original Method A bytes (before release c1-20261014-ukpn-ev).
METHOD_A_GIT_REV = 'bfde3aa0494a84d201d585664735b346aa8abfb6'
RELEASE_BACKUP_ID = 'c1-20261014-ukpn-ev'
_METHOD_A_CACHE = {}


def method_a_target_bytes(name):
    """Original (pre-release) Method A bytes of an integration target, from immutable sources only.

    Tries git history at METHOD_A_GIT_REV (LFS object from the local store), then the verified release backup.
    Every source is checked against the pinned Method A hash; anything else raises.
    """
    if name not in cfg.TARGET_NAMES:
        raise ValueError(f'not a release target: {name}')
    if name in _METHOD_A_CACHE:
        return _METHOD_A_CACHE[name]
    expected = cfg.METHOD_A_TARGET_SHA256[name]
    candidates = []
    try:
        pointer = subprocess.run(['git', 'show', f'{METHOD_A_GIT_REV}:{cfg.INTEGRATION_REL}/{name}'], cwd=cfg.REPO,
                                 capture_output=True, check=True, timeout=120).stdout
        candidates.append(subprocess.run(['git', 'lfs', 'smudge'], cwd=cfg.REPO, input=pointer, capture_output=True,
                                         check=True, timeout=300).stdout)
    except (OSError, subprocess.SubprocessError):
        pass
    backup = cfg.REPO.joinpath(*cfg.STATE_REL.split('/'), 'backups', RELEASE_BACKUP_ID, name)
    if backup.is_file():
        candidates.append(backup.read_bytes())
    for data in candidates:
        if hashlib.sha256(data).hexdigest() == expected:
            _METHOD_A_CACHE[name] = data
            return data
    raise RuntimeError(f'no verified Method A source for {name} (git {METHOD_A_GIT_REV[:7]} or release backup)')


def env_files():
    files = list(cfg.DISPOSABLE_ENV_FILES)
    for tree in cfg.DISPOSABLE_ENV_TREES:
        top = cfg.REPO.joinpath(*tree.split('/'))
        for d, _, fs in os.walk(top):
            for f in fs:
                files.append(Path(os.path.relpath(Path(d) / f, cfg.REPO)).as_posix())
    return sorted(set(files))


def build(dest, template=None, baseline='method_a'):
    """baseline='method_a': the three targets hold the original pre-release Method A bytes (migration and dry-run
    tests). baseline='live': the targets are copied from the current production files (post-release state)."""
    if baseline not in ('method_a', 'live'):
        raise ValueError(f'unknown baseline {baseline!r}')
    dest = Path(dest)
    if not dest.is_absolute():
        raise rel.UnsafePath('destination must be absolute')
    real = Path(os.path.realpath(dest))
    repo = Path(os.path.realpath(cfg.REPO))
    if rel._within(real, repo) or rel._within(repo, real):
        raise rel.UnsafePath(f'disposable environments must be outside the repository: {real}')
    if dest.exists() and any(dest.iterdir()):
        raise rel.UnsafePath(f'destination is not empty: {dest}')
    dest.mkdir(parents=True, exist_ok=True)
    template = Path(template) if template else None
    targets = {f'{cfg.INTEGRATION_REL}/{n}' for n in cfg.TARGET_NAMES}
    copied = {}
    for relpath in env_files():
        src = cfg.REPO.joinpath(*relpath.split('/'))
        out = dest.joinpath(*relpath.split('/'))
        out.parent.mkdir(parents=True, exist_ok=True)
        if relpath in targets and baseline == 'method_a':
            out.write_bytes(method_a_target_bytes(relpath.rsplit('/', 1)[1]))
        elif template is not None and relpath not in targets and src.stat().st_size >= LINK_THRESHOLD:
            os.link(template.joinpath(*relpath.split('/')), out)
        else:
            shutil.copyfile(src, out)
        copied[relpath] = rel.sha256_file(out)
    for relpath, h in copied.items():                          # the copy must be exact
        if relpath in targets and baseline == 'method_a':
            ok = h == cfg.METHOD_A_TARGET_SHA256[relpath.rsplit('/', 1)[1]]
        else:
            ok = rel.sha256_file(cfg.REPO.joinpath(*relpath.split('/'))) == h
        if not ok:
            raise RuntimeError(f'copy mismatch for {relpath}')
    return dest, copied


if __name__ == '__main__':
    d, files = build(sys.argv[1], sys.argv[2] if len(sys.argv) > 2 else None)
    print(f'disposable environment {d}: {len(files)} files')
