"""The real Data Manager app with the ingestion blueprint, and static guarantees about what ingestion can touch."""

import ast
import json
import os
import subprocess
import sys

import pytest

from data_manager.ingestion import registry as registry_mod
from data_manager.ingestion.config import Settings
from data_manager.ingestion.registry import Registry

from .conftest import REPO

PKG = os.path.join(REPO, "data_manager", "ingestion")

SMOKE = r"""
import json, os, sys
sys.path.insert(0, os.getcwd())
import app_data_manager as m
c = m.app.test_client()
out = {}
for path in ("/", "/health", "/dft-update", "/dft-ingest", "/dft-sources", "/api/ingest/jobs", "/candidates"):
    r = c.get(path)
    out[path] = [r.status_code, r.get_data(as_text=True)]
print(json.dumps({k: [v[0], ("DFT INGEST" in v[1]), ("OPEN DFT INGESTION" in v[1]), ("simulateNormalization" in v[1])] for k, v in out.items()}))
"""


def test_full_app_serves_ingestion_pages_with_disposable_registry(tmp_path):
    env = {**os.environ, "LCT_SOURCE_REGISTRY": str(tmp_path / "reg"), "OPENBLAS_NUM_THREADS": "1", "PYTHONDONTWRITEBYTECODE": "1"}
    p = subprocess.run([sys.executable, "-c", SMOKE], cwd=REPO, env=env, capture_output=True, text=True, timeout=300)
    assert p.returncode == 0, p.stderr[-2000:]
    res = json.loads(p.stdout.strip().splitlines()[-1])
    for path, (code, *_rest) in res.items():
        assert code == 200, (path, code)
    assert res["/dft-ingest"][1] and res["/dft-sources"][1]             # navigation link present
    assert res["/dft-update"][2] and not res["/dft-update"][3]          # simulated upload removed, link to the real workflow
    assert os.path.exists(tmp_path / "reg" / "registry.db")            # the disposable registry was used
    assert not os.path.exists(os.path.join(REPO, "source_registry"))   # the real one was not created by tests


def test_default_registry_lives_outside_release_protected_trees():
    root = os.path.relpath(Settings().registry_root, REPO).replace("\\", "/")
    sys.path.insert(0, os.path.join(REPO, "experiments", "ev_redistribution_c1", "release"))
    import release_config as cfg
    assert not any(root == t or root.startswith(t + "/") for t in cfg.PROTECTED_TREES), root
    assert root == "source_registry"


def _string_constants(path):
    """String literals that are code (docstrings excluded: they describe what the module does not touch)."""
    tree = ast.parse(open(path, encoding="utf-8").read())
    docs = {id(n.body[0].value) for n in ast.walk(tree)
            if isinstance(n, (ast.Module, ast.FunctionDef, ast.ClassDef)) and n.body and isinstance(n.body[0], ast.Expr)
            and isinstance(n.body[0].value, ast.Constant)}
    return [n.value for n in ast.walk(tree) if isinstance(n, ast.Constant) and isinstance(n.value, str) and id(n) not in docs]


def test_ingestion_code_never_references_production_outputs_or_promotion():
    forbidden = ("ev_actuals", "output_processed", "integration/", "promotion", "LIVE_RELEASE_ENABLED", "dashboard_data", "c1_release")
    for name in os.listdir(PKG):
        if not name.endswith(".py"):
            continue
        src = open(os.path.join(PKG, name), encoding="utf-8").read()
        consts = " ".join(_string_constants(os.path.join(PKG, name)))
        for f in forbidden:
            assert f not in consts, (name, f)
        tree = ast.parse(src)
        imported = {a.name for n in ast.walk(tree) if isinstance(n, ast.Import) for a in n.names} | \
                   {n.module or "" for n in ast.walk(tree) if isinstance(n, ast.ImportFrom)}
        assert not any("promotion" in i or "c1_release" in i or "integration_writer" in i for i in imported), (name, imported)


def test_writes_are_confined_to_the_registry(tmp_path):
    reg = Registry(Settings(registry_root=str(tmp_path / "r")))
    reg.ensure()
    for bad in (("..", "x"), ("..", "..", "ev", "df_VEH0135.csv"), (os.path.join(REPO, "ev"),)):
        with pytest.raises(ValueError, match="escapes"):
            reg.path(*bad)
    assert open(tmp_path / "r" / ".gitignore").read().strip().endswith("*")


def test_production_ev_dir_is_only_read_by_the_worker():
    """worker.production_profile opens ev/ files for reading only and caches inside the registry."""
    src = open(os.path.join(PKG, "worker.py"), encoding="utf-8").read()
    tree = ast.parse(src)
    fn = next(n for n in ast.walk(tree) if isinstance(n, ast.FunctionDef) and n.name == "production_profile")
    opens = [n for n in ast.walk(fn) if isinstance(n, ast.Call) and getattr(n.func, "id", "") == "open"]
    modes = [ast.literal_eval(c.args[1]) if len(c.args) > 1 else "r" for c in opens]
    writes = [c for c, m in zip(opens, modes) if "w" in m]
    assert all("cache" in ast.unparse(c.args[0]) for c in writes)
