from __future__ import annotations

import importlib.util
import json
import subprocess
import sys
from pathlib import Path

TEST_ROOT = Path(__file__).resolve().parent
SKILL_ROOT = TEST_ROOT.parent
PLUGIN_ROOT = SKILL_ROOT.parents[1]
WORKSPACE_ROOT = PLUGIN_ROOT.parent
SCRIPT = SKILL_ROOT / "scripts" / "evolver.py"
PROBE_SCRIPT = TEST_ROOT / "feasibility_probe.py"


def _load(path: Path, name: str):
    spec = importlib.util.spec_from_file_location(name, path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"cannot load {path}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def load_runtime():
    return _load(SCRIPT, "skill_evolver_runtime")


def load_probe_runtime():
    return _load(PROBE_SCRIPT, "skill_evolver_feasibility_probe")


def _run(
    path: Path,
    *args: str,
    stdin: bytes = b"",
) -> subprocess.CompletedProcess[bytes]:
    return subprocess.run(
        ["/usr/bin/python3", "-I", str(path), *args],
        input=stdin,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=False,
    )


def run_isolated(*args: str, stdin: bytes = b"") -> subprocess.CompletedProcess[bytes]:
    return _run(SCRIPT, *args, stdin=stdin)


def run_probe_isolated(
    *args: str,
    stdin: bytes = b"",
) -> subprocess.CompletedProcess[bytes]:
    return _run(PROBE_SCRIPT, *args, stdin=stdin)


def read_json(path: Path) -> dict[str, object]:
    return json.loads(path.read_text(encoding="utf-8"))
