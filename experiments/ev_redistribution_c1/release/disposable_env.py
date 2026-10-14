"""Build a disposable environment for release dry runs. Read-only on the repository.

The environment has the repository layout for exactly the files the release tool and the dashboard harness need:
dashboard.html, the comparison CSVs, the whole integration/ folder (Method A targets, forecasts, HP files, metadata),
the C1 shadow files, the C1 source outputs, the Method A archive and the LSOA lookup.

Usage:
  python experiments/ev_redistribution_c1/release/disposable_env.py <absolute dest outside the repository> [template]

With a template (an earlier disposable environment), large files that the release tool never writes are hard-linked
from the template instead of copied. Hard links are only ever made between disposable copies, never into the repository.
"""
import os
import shutil
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import release_config as cfg  # noqa: E402
import c1_release as rel  # noqa: E402

LINK_THRESHOLD = 1_000_000


def env_files():
    files = list(cfg.DISPOSABLE_ENV_FILES)
    for tree in cfg.DISPOSABLE_ENV_TREES:
        top = cfg.REPO.joinpath(*tree.split('/'))
        for d, _, fs in os.walk(top):
            for f in fs:
                files.append(Path(os.path.relpath(Path(d) / f, cfg.REPO)).as_posix())
    return sorted(set(files))


def build(dest, template=None):
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
        if template is not None and relpath not in targets and src.stat().st_size >= LINK_THRESHOLD:
            os.link(template.joinpath(*relpath.split('/')), out)
        else:
            shutil.copyfile(src, out)
        copied[relpath] = rel.sha256_file(out)
    for relpath, h in copied.items():                          # the copy must be exact
        if rel.sha256_file(cfg.REPO.joinpath(*relpath.split('/'))) != h:
            raise RuntimeError(f'copy mismatch for {relpath}')
    return dest, copied


if __name__ == '__main__':
    d, files = build(sys.argv[1], sys.argv[2] if len(sys.argv) > 2 else None)
    print(f'disposable environment {d}: {len(files)} files')
