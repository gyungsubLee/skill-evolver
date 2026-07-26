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


def load_runtime():
    spec = importlib.util.spec_from_file_location("skill_evolver_runtime", SCRIPT)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"cannot load {SCRIPT}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def run_isolated(*args: str, stdin: bytes = b"") -> subprocess.CompletedProcess[bytes]:
    return subprocess.run(
        ["/usr/bin/python3", "-I", str(SCRIPT), *args],
        input=stdin,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=False,
    )


def read_json(path: Path) -> dict[str, object]:
    return json.loads(path.read_text(encoding="utf-8"))
