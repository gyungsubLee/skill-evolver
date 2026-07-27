# Skill Evolver Feasibility Spike Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Prove on the current macOS Codex CLI and Desktop installations that a trusted plugin `Stop` Hook can capture bounded turn metadata, that the Hook and an explicitly invoked skill can use the same fixed private data root, and that the captured transcript prefix exposes a reliable turn/provenance structure.

**Architecture:** Build a temporary, self-contained Python probe packaged as a local Codex plugin. A fresh challenge proves the installed `$skill-evolver` can read and write the same private root as the Hook under default `workspace-write`; then two bounded Stop observations per surface are converted into sanitized structural fixtures and a deterministic PASS/FAIL report. The probe never creates the production SQLite queue, reviews transcript meaning, calls a model, modifies a target skill, or remains enabled after the spike.

**Tech Stack:** macOS, Codex CLI and Desktop, `/usr/bin/python3` 3.9+, Python standard library (`argparse`, `dataclasses`, `json`, `os`, `pathlib`, `stat`, `tempfile`, `unittest`), Codex plugin manifest and command Hook.

## Global Constraints

- Source specification: `skill-evolver/docs/superpowers/specs/2026-07-26-skill-evolver-design.md`.
- Supported surfaces are macOS Codex CLI and Desktop only.
- Runtime interpreter is exactly `/usr/bin/python3`; minimum version is 3.9.
- Runtime code uses the Python standard library only.
- `evolver.py` is one self-contained entrypoint because `python -I` excludes its sibling directory from `sys.path`.
- The Hook registers only `Stop`; it does not register `SubagentStop` or a `Stop` matcher.
- A Hook invocation writes no stdout, performs no network access, and exits `0` even when capture fails.
- Hook input is capped at 64 KiB; transcript contents are never copied into the repository.
- Each surface must produce two independent post-boundary Stop observations with the same structural layout before the gate can pass.
- Shared-root preflight must run through the installed `$skill-evolver` under default `workspace-write`; requesting elevated filesystem access makes that surface fail.
- The private probe data root is `/Users/igyeongseob/.codex/skill-evolver-feasibility`, mode `0700`; its files are mode `0600`.
- Initialization, arming, promotion, and scrub control commands run from a user-controlled terminal or with permission limited to that exact probe root. Only the in-task `$skill-evolver probe preflight` must run without escalation.
- Committed fixtures contain field names, types, counts, JSON-pointer paths, and booleans only. They contain no session ID, turn ID, transcript text, absolute transcript path, prompt, tool output, token, or credential.
- The workspace root currently has no commits and contains unrelated nested `n8n/` and `neo4j/` repositories. Every commit must stage exact Skill Evolver paths; never run `git add .`.
- If either surface cannot map the captured `turn_id` to a bounded transcript span with provenance, or the Hook and skill cannot share the fixed data root, stop. Amend the design to a session-level queue before writing the Read-only MVP plan.

## Scope Boundary and Follow-on Plans

This plan implements only the specification's Feasibility Spike. It deliberately excludes SQLite, queue retention, candidate creation, model review, prepare/evaluate, apply, versioning, and undo. The complete ordering and gate contracts are defined in `skill-evolver/docs/superpowers/plans/2026-07-26-skill-evolver-implementation-roadmap.md`.

A PASS report authorizes execution of the next already-defined plan:

- `skill-evolver/docs/superpowers/plans/2026-07-26-skill-evolver-read-only-runtime-queue.md`

Read-only work then proceeds through:

- `skill-evolver/docs/superpowers/plans/2026-07-26-skill-evolver-read-only-review-inbox.md`
- `skill-evolver/docs/superpowers/plans/2026-07-26-skill-evolver-read-only-quality-gate.md`

Only after the Read-only quality gate passes may the Evaluate and Apply plans be executed. The thresholds remain: at least 10 reviewed sessions or 30 reviewed turns, evaluation-worth rate at least 50%, target-skill misattribution at most 20%, and zero external-content adoption incidents.

## File Structure

| Path | Responsibility |
| --- | --- |
| `.agents/plugins/marketplace.json` | Local marketplace entry used only to install the probe plugin. |
| `skill-evolver/.gitignore` | Exclude Python caches and local probe artifacts. |
| `skill-evolver/.codex-plugin/plugin.json` | Codex plugin manifest. |
| `skill-evolver/hooks/hooks.json` | Trusted `Stop` command Hook invoking the self-contained probe. |
| `skill-evolver/skills/skill-evolver/SKILL.md` | Explicit-only feasibility commands; no review behavior. |
| `skill-evolver/skills/skill-evolver/references/runtime.json` | Fixed path to the private `installation.json` used by skill-launched commands. |
| `skill-evolver/skills/skill-evolver/scripts/evolver.py` | All probe logic and CLI subcommands. |
| `skill-evolver/skills/skill-evolver/tests/support.py` | Runtime loader, isolated CLI runner, and temporary installation helpers. |
| `skill-evolver/skills/skill-evolver/tests/test_skeleton.py` | Manifest, Hook shape, explicit trigger, isolated runtime tests. |
| `skill-evolver/skills/skill-evolver/tests/test_installation.py` | Private-root creation, permissions, symlink and environment override tests. |
| `skill-evolver/skills/skill-evolver/tests/test_stop_probe.py` | Bounded Hook parsing, silent failure, metadata capture, sanitization tests. |
| `skill-evolver/skills/skill-evolver/tests/test_transcript_probe.py` | Prefix-only JSONL inspection and structural-report tests. |
| `skill-evolver/skills/skill-evolver/tests/test_gate.py` | Cross-surface gate and privacy scrub tests. |
| `skill-evolver/skills/skill-evolver/tests/fixtures/synthetic-transcript.jsonl` | Synthetic transcript used to test prefix boundaries without user data. |
| `skill-evolver/skills/skill-evolver/tests/fixtures/access-cli.structure.json` | Sanitized result of the CLI skill-process read/write preflight. |
| `skill-evolver/skills/skill-evolver/tests/fixtures/access-desktop.structure.json` | Sanitized result of the Desktop skill-process read/write preflight. |
| `skill-evolver/skills/skill-evolver/tests/fixtures/stop-cli.structure.json` | Generated sanitized CLI Stop fixture. |
| `skill-evolver/skills/skill-evolver/tests/fixtures/stop-desktop.structure.json` | Generated sanitized Desktop Stop fixture. |
| `skill-evolver/skills/skill-evolver/tests/fixtures/transcript-cli.structure.json` | Generated sanitized CLI transcript layout. |
| `skill-evolver/skills/skill-evolver/tests/fixtures/transcript-desktop.structure.json` | Generated sanitized Desktop transcript layout. |
| `skill-evolver/docs/feasibility-report.json` | Machine-readable gate inputs, checks, and final decision. |
| `skill-evolver/docs/feasibility-report.md` | Human-readable PASS/FAIL report generated from the JSON report. |
| `skill-evolver/README.md` | Probe purpose, privacy boundary, exact initialization/install/remove/scrub commands, and a pointer to this plan for the interactive capture sequence. |

---

### Task 1: Scaffold the Local Probe Plugin and Isolated Test Harness

**Files:**
- Create: `.agents/plugins/marketplace.json`
- Create: `skill-evolver/.gitignore`
- Create: `skill-evolver/.codex-plugin/plugin.json`
- Create: `skill-evolver/hooks/hooks.json`
- Create: `skill-evolver/skills/skill-evolver/SKILL.md`
- Create: `skill-evolver/skills/skill-evolver/references/runtime.json`
- Create: `skill-evolver/skills/skill-evolver/scripts/evolver.py`
- Create: `skill-evolver/skills/skill-evolver/tests/support.py`
- Create: `skill-evolver/skills/skill-evolver/tests/test_skeleton.py`
- Create: `skill-evolver/README.md`

**Interfaces:**
- Consumes: No prior runtime interfaces.
- Produces: `main(argv: Optional[Sequence[str]] = None) -> int`, `read_bounded_stdin(stream: BinaryIO, limit: int = 65536) -> bytes`, `summarize_hook_shape(payload: object) -> dict[str, object]`, and an isolated command `probe-stop`.

- [ ] **Step 1: Write the failing skeleton tests**

Create `skill-evolver/skills/skill-evolver/tests/support.py`:

```python
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
```

Create `skill-evolver/skills/skill-evolver/tests/test_skeleton.py`:

```python
from __future__ import annotations

import json
import unittest

from support import PLUGIN_ROOT, run_isolated


class SkeletonTests(unittest.TestCase):
    def test_manifest_and_hook_are_discoverable(self) -> None:
        manifest = json.loads(
            (PLUGIN_ROOT / ".codex-plugin" / "plugin.json").read_text(encoding="utf-8")
        )
        hooks = json.loads((PLUGIN_ROOT / "hooks" / "hooks.json").read_text(encoding="utf-8"))

        self.assertEqual(manifest["name"], "skill-evolver")
        self.assertEqual(manifest["skills"], "./skills/")
        self.assertEqual(manifest["hooks"], "./hooks/hooks.json")
        self.assertEqual(set(hooks["hooks"]), {"Stop"})
        group = hooks["hooks"]["Stop"][0]
        self.assertNotIn("matcher", group)
        self.assertEqual(group["hooks"][0]["type"], "command")
        self.assertEqual(group["hooks"][0]["timeout"], 2)
        self.assertNotIn("SubagentStop", hooks["hooks"])

    def test_skill_is_explicit_only(self) -> None:
        skill = (PLUGIN_ROOT / "skills" / "skill-evolver" / "SKILL.md").read_text(
            encoding="utf-8"
        )
        self.assertIn("only when the user explicitly names $skill-evolver", skill)
        self.assertIn("Never invoke after an ordinary task", skill)
        self.assertNotIn("After every task", skill)

    def test_runtime_works_under_isolated_python(self) -> None:
        result = run_isolated("--version")
        self.assertEqual(result.returncode, 0, result.stderr.decode())
        self.assertEqual(result.stdout.decode().strip(), "skill-evolver feasibility 0.0.1")

    def test_probe_stop_is_silent_and_fail_open(self) -> None:
        malformed = run_isolated("probe-stop", "--installation", "/missing/file", stdin=b"{")
        self.assertEqual(malformed.returncode, 0)
        self.assertEqual(malformed.stdout, b"")
        self.assertEqual(malformed.stderr, b"")


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: Run the skeleton tests and verify the failure**

Run:

```bash
/usr/bin/python3 -m unittest discover \
  -s skill-evolver/skills/skill-evolver/tests \
  -p 'test_skeleton.py' -v
```

Expected: FAIL because the manifest, Hook, and `evolver.py` do not exist.

- [ ] **Step 3: Create the marketplace and plugin metadata**

Create `.agents/plugins/marketplace.json`:

```json
{
  "name": "skill-evolver-dev",
  "interface": {
    "displayName": "Skill Evolver Development"
  },
  "plugins": [
    {
      "name": "skill-evolver",
      "source": {
        "source": "local",
        "path": "./skill-evolver"
      },
      "policy": {
        "installation": "AVAILABLE",
        "authentication": "ON_USE"
      },
      "category": "Developer Tools"
    }
  ]
}
```

Create `skill-evolver/.gitignore`:

```gitignore
__pycache__/
*.py[cod]
.DS_Store
.feasibility/
```

Create `skill-evolver/.codex-plugin/plugin.json`:

```json
{
  "name": "skill-evolver",
  "version": "0.0.1",
  "description": "Probe Codex Stop Hook and transcript feasibility before building Skill Evolver.",
  "skills": "./skills/",
  "hooks": "./hooks/hooks.json"
}
```

Create `skill-evolver/hooks/hooks.json`:

```json
{
  "description": "Capture bounded Stop metadata for the Skill Evolver feasibility spike.",
  "hooks": {
    "Stop": [
      {
        "hooks": [
          {
            "type": "command",
            "command": "/usr/bin/python3 -I \"$PLUGIN_ROOT/skills/skill-evolver/scripts/evolver.py\" probe-stop --installation \"/Users/igyeongseob/.codex/skill-evolver-feasibility/installation.json\"",
            "timeout": 2
          }
        ]
      }
    ]
  }
}
```

Create `skill-evolver/skills/skill-evolver/references/runtime.json`:

```json
{
  "schema_version": 1,
  "installation": "/Users/igyeongseob/.codex/skill-evolver-feasibility/installation.json"
}
```

- [ ] **Step 4: Create the explicit-only probe skill**

Create `skill-evolver/skills/skill-evolver/SKILL.md`:

```markdown
---
name: skill-evolver
description: Inspect the Skill Evolver feasibility probe. Use only when the user explicitly names $skill-evolver and asks for probe status, fixture promotion, the feasibility gate, or probe cleanup. Never invoke after an ordinary task.
---

# Skill Evolver Feasibility Probe

This pre-release skill does not review transcripts, create candidates, or modify skills.

Read `references/runtime.json` and pass its absolute `installation` value to the
self-contained `scripts/evolver.py` entrypoint.

Resolve `scripts/evolver.py` relative to this `SKILL.md` and always invoke it as
`/usr/bin/python3 -I` followed by that resolved absolute path. Resolve the plugin
root as this skill directory's second parent. For `probe-gate`, use
`tests/fixtures` under this skill as `--fixture-root` and `docs/feasibility-report.json`
plus `docs/feasibility-report.md` under the plugin root as the two outputs.

Supported user-facing actions:

- `$skill-evolver probe status` → run `probe-status`.
- `$skill-evolver probe list` → run `probe-list`.
- `$skill-evolver probe preflight cli` → run `probe-skill-preflight --surface cli`
  without requesting elevated filesystem access.
- `$skill-evolver probe preflight desktop` → run
  `probe-skill-preflight --surface desktop` without requesting elevated filesystem access.
- `$skill-evolver probe gate` → run `probe-gate` after the six sanitized fixtures exist.
- `$skill-evolver probe cleanup` → show the exact `probe-scrub` command and require the user to run it in a TTY.

Do not open a transcript for status or list. Do not copy raw observations into chat.
Do not claim feasibility passed unless `probe-gate` exits 0 and both report files say `PASS`.
```

- [ ] **Step 5: Implement the initial self-contained CLI**

Create `skill-evolver/skills/skill-evolver/scripts/evolver.py`:

```python
#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import sys
from typing import BinaryIO, Optional, Sequence

VERSION = "skill-evolver feasibility 0.0.1"
MAX_STDIN_BYTES = 65_536
REQUIRED_HOOK_FIELDS = {
    "hook_event_name": str,
    "session_id": str,
    "turn_id": str,
    "cwd": str,
}


def read_bounded_stdin(stream: BinaryIO, limit: int = MAX_STDIN_BYTES) -> bytes:
    raw = stream.read(limit + 1)
    if len(raw) > limit:
        raise ValueError("hook_input_too_large")
    return raw


def summarize_hook_shape(payload: object) -> dict[str, object]:
    if not isinstance(payload, dict):
        raise ValueError("hook_payload_not_object")
    field_types = {key: type(value).__name__ for key, value in sorted(payload.items())}
    required = {
        key: {
            "present": key in payload,
            "type": field_types.get(key),
            "valid": isinstance(payload.get(key), expected)
            and (not isinstance(payload.get(key), str) or bool(payload[key]))
            and (key != "hook_event_name" or payload.get(key) == "Stop"),
        }
        for key, expected in REQUIRED_HOOK_FIELDS.items()
    }
    transcript = payload.get("transcript_path")
    required["transcript_path"] = {
        "present": "transcript_path" in payload,
        "type": type(transcript).__name__,
        "valid": isinstance(transcript, str) and bool(transcript),
    }
    return {
        "payload_keys": sorted(payload),
        "field_types": field_types,
        "required_fields": required,
    }


def cmd_probe_stop(_args: argparse.Namespace) -> int:
    try:
        raw = read_bounded_stdin(sys.stdin.buffer)
        summarize_hook_shape(json.loads(raw))
    except Exception:
        pass
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="evolver.py")
    parser.add_argument("--version", action="version", version=VERSION)
    subparsers = parser.add_subparsers(dest="command", required=True)
    probe_stop = subparsers.add_parser("probe-stop")
    probe_stop.add_argument("--installation", required=True)
    probe_stop.set_defaults(handler=cmd_probe_stop)
    return parser


def main(argv: Optional[Sequence[str]] = None) -> int:
    args = build_parser().parse_args(argv)
    return int(args.handler(args))


if __name__ == "__main__":
    raise SystemExit(main())
```

- [ ] **Step 6: Create the probe README**

Create `skill-evolver/README.md`:

````markdown
# Skill Evolver Feasibility Probe

This directory contains the pre-MVP probe defined by
`docs/superpowers/specs/2026-07-26-skill-evolver-design.md`.

It answers three questions:

1. Do Codex CLI and Desktop deliver the required `Stop` fields?
2. Can the Hook and an explicitly invoked skill use one fixed private data root?
3. Can the captured transcript prefix map a turn and source provenance without
   reading bytes appended after the Hook?

The probe stores raw metadata only under
`/Users/igyeongseob/.codex/skill-evolver-feasibility` with private permissions.
Only sanitized structural reports are committed. The probe is removed after
the gate report is generated.

## Commands

Initialize the private probe:

```bash
/usr/bin/python3 -I /Users/igyeongseob/Documents/오픈소스/skill-evolver/skills/skill-evolver/scripts/evolver.py probe-init \
  --data-root /Users/igyeongseob/.codex/skill-evolver-feasibility \
  --transcript-root /Users/igyeongseob/.codex/sessions \
  --transcript-root /Users/igyeongseob/.codex/archived_sessions
```

Install from the local marketplace:

```bash
codex plugin marketplace add /Users/igyeongseob/Documents/오픈소스 --json
codex plugin add skill-evolver@skill-evolver-dev --json
```

The exact surface-arm, capture, promotion, and gate commands are recorded in
`docs/superpowers/plans/2026-07-26-skill-evolver-feasibility-spike.md`.

Remove the probe before scrubbing private observations:

```bash
codex plugin remove skill-evolver@skill-evolver-dev --json
codex plugin marketplace remove skill-evolver-dev --json
/usr/bin/python3 -I /Users/igyeongseob/Documents/오픈소스/skill-evolver/skills/skill-evolver/scripts/evolver.py probe-scrub \
  --installation /Users/igyeongseob/.codex/skill-evolver-feasibility/installation.json
```
````

- [ ] **Step 7: Run the skeleton tests**

Run:

```bash
/usr/bin/python3 -m unittest discover \
  -s skill-evolver/skills/skill-evolver/tests \
  -p 'test_skeleton.py' -v
```

Expected: 4 tests PASS.

- [ ] **Step 8: Commit the scaffold with exact paths**

```bash
git add \
  .agents/plugins/marketplace.json \
  skill-evolver/docs/superpowers/specs/2026-07-26-skill-evolver-design.md \
  skill-evolver/docs/superpowers/plans/2026-07-26-skill-evolver-feasibility-spike.md \
  skill-evolver/.gitignore \
  skill-evolver/.codex-plugin/plugin.json \
  skill-evolver/hooks/hooks.json \
  skill-evolver/skills/skill-evolver/SKILL.md \
  skill-evolver/skills/skill-evolver/references/runtime.json \
  skill-evolver/skills/skill-evolver/scripts/evolver.py \
  skill-evolver/skills/skill-evolver/tests/support.py \
  skill-evolver/skills/skill-evolver/tests/test_skeleton.py \
  skill-evolver/README.md
git commit -m "chore: scaffold skill evolver feasibility probe"
```

Expected: the root repository receives its first commit; `n8n/` and `neo4j/` remain unstaged.

---

### Task 2: Create and Validate the Fixed Private Probe Installation

**Files:**
- Modify: `skill-evolver/skills/skill-evolver/scripts/evolver.py`
- Create: `skill-evolver/skills/skill-evolver/tests/test_installation.py`

**Interfaces:**
- Consumes: `main()`, `build_parser()` from Task 1.
- Produces: `Installation`, `atomic_write_json()`, `initialize_probe()`, `load_installation()`, the private root/child/nonce validators, `probe-status`, and `probe-list`.

- [ ] **Step 1: Write the failing installation tests**

Create `skill-evolver/skills/skill-evolver/tests/test_installation.py`:

```python
from __future__ import annotations

import json
import os
import stat
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from support import load_runtime


class InstallationTests(unittest.TestCase):
    def setUp(self) -> None:
        self.runtime = load_runtime()
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name) / "probe"
        self.transcripts = Path(self.temp.name) / "sessions"
        self.transcripts.mkdir(mode=0o700)

    def test_initialize_creates_private_canonical_installation(self) -> None:
        installation_path = self.runtime.initialize_probe(
            self.root, (self.transcripts,), Path("/usr/bin/python3")
        )
        installation = self.runtime.load_installation(installation_path)

        self.assertEqual(installation.data_root, self.root.resolve())
        self.assertEqual(installation.transcript_roots, (self.transcripts.resolve(),))
        self.assertEqual(installation.python, Path("/usr/bin/python3"))
        self.assertEqual(stat.S_IMODE(self.root.stat().st_mode), 0o700)
        self.assertEqual(stat.S_IMODE(installation_path.stat().st_mode), 0o600)
        self.assertEqual(stat.S_IMODE((self.root / "nonce.json").stat().st_mode), 0o600)
        self.assertTrue((self.root / "incoming").is_dir())
        self.assertTrue((self.root / "reports").is_dir())

    def test_load_ignores_environment_overrides(self) -> None:
        installation_path = self.runtime.initialize_probe(
            self.root, (self.transcripts,), Path("/usr/bin/python3")
        )
        with mock.patch.dict(
            os.environ,
            {
                "CODEX_HOME": "/attacker",
                "SKILL_EVOLVER_DATA": "/attacker",
                "PYTHONPATH": "/attacker",
            },
        ):
            installation = self.runtime.load_installation(installation_path)
        self.assertEqual(installation.data_root, self.root.resolve())

    def test_symlink_installation_is_rejected(self) -> None:
        installation_path = self.runtime.initialize_probe(
            self.root, (self.transcripts,), Path("/usr/bin/python3")
        )
        link = Path(self.temp.name) / "installation-link.json"
        link.symlink_to(installation_path)
        with self.assertRaisesRegex(ValueError, "installation_symlink"):
            self.runtime.load_installation(link)

    def test_symlink_data_root_is_rejected_before_initialization(self) -> None:
        target = Path(self.temp.name) / "target"
        target.mkdir(mode=0o700)
        link = Path(self.temp.name) / "probe-link"
        link.symlink_to(target, target_is_directory=True)
        with self.assertRaisesRegex(ValueError, "data_root_symlink"):
            self.runtime.initialize_probe(
                link, (self.transcripts,), Path("/usr/bin/python3")
            )
        self.assertEqual(list(target.iterdir()), [])

    def test_symlinked_private_child_is_rejected_before_initialization(self) -> None:
        for child in ("incoming", "reports"):
            with self.subTest(child=child):
                root = Path(self.temp.name) / f"probe-{child}"
                root.mkdir(mode=0o700)
                target = Path(self.temp.name) / f"target-{child}"
                target.mkdir(mode=0o700)
                (root / child).symlink_to(target, target_is_directory=True)
                with self.assertRaisesRegex(ValueError, "data_child_symlink"):
                    self.runtime.initialize_probe(
                        root, (self.transcripts,), Path("/usr/bin/python3")
                    )
                self.assertEqual(list(target.iterdir()), [])

    def test_world_writable_data_root_is_rejected(self) -> None:
        installation_path = self.runtime.initialize_probe(
            self.root, (self.transcripts,), Path("/usr/bin/python3")
        )
        self.root.chmod(0o777)
        with self.assertRaisesRegex(ValueError, "data_root_permissions"):
            self.runtime.load_installation(installation_path)

    def test_newer_schema_is_rejected(self) -> None:
        installation_path = self.runtime.initialize_probe(
            self.root, (self.transcripts,), Path("/usr/bin/python3")
        )
        payload = json.loads(installation_path.read_text(encoding="utf-8"))
        payload["schema_version"] = 2
        installation_path.write_text(json.dumps(payload), encoding="utf-8")
        installation_path.chmod(0o600)
        with self.assertRaisesRegex(ValueError, "unsupported_installation_schema"):
            self.runtime.load_installation(installation_path)

    def test_tampered_python_path_is_rejected(self) -> None:
        installation_path = self.runtime.initialize_probe(
            self.root, (self.transcripts,), Path("/usr/bin/python3")
        )
        payload = json.loads(installation_path.read_text(encoding="utf-8"))
        payload["python"] = "/tmp/python3"
        installation_path.write_text(json.dumps(payload), encoding="utf-8")
        installation_path.chmod(0o600)
        with self.assertRaisesRegex(ValueError, "unsupported_python"):
            self.runtime.load_installation(installation_path)

    def test_symlinked_nonce_is_rejected_on_load(self) -> None:
        installation_path = self.runtime.initialize_probe(
            self.root, (self.transcripts,), Path("/usr/bin/python3")
        )
        nonce = self.root / "nonce.json"
        nonce.unlink()
        external = Path(self.temp.name) / "external-nonce.json"
        external.write_text('{"nonce":"attacker"}', encoding="utf-8")
        external.chmod(0o600)
        nonce.symlink_to(external)
        with self.assertRaisesRegex(ValueError, "nonce_symlink"):
            self.runtime.load_installation(installation_path)

    def test_private_child_swapped_to_symlink_is_rejected_on_load(self) -> None:
        installation_path = self.runtime.initialize_probe(
            self.root, (self.transcripts,), Path("/usr/bin/python3")
        )
        reports = self.root / "reports"
        reports.rmdir()
        external = Path(self.temp.name) / "external-reports"
        external.mkdir(mode=0o700)
        reports.symlink_to(external, target_is_directory=True)
        with self.assertRaisesRegex(ValueError, "data_child_symlink"):
            self.runtime.load_installation(installation_path)

    def test_tampered_nonce_value_is_rejected(self) -> None:
        installation_path = self.runtime.initialize_probe(
            self.root, (self.transcripts,), Path("/usr/bin/python3")
        )
        nonce = self.root / "nonce.json"
        nonce.write_text('{"schema_version":1,"nonce":""}', encoding="utf-8")
        nonce.chmod(0o600)
        with self.assertRaisesRegex(ValueError, "invalid_nonce"):
            self.runtime.load_installation(installation_path)


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: Run the installation tests and verify the failure**

Run:

```bash
/usr/bin/python3 -m unittest discover \
  -s skill-evolver/skills/skill-evolver/tests \
  -p 'test_installation.py' -v
```

Expected: FAIL because `Installation` and installation helpers are undefined.

- [ ] **Step 3: Add installation types and durable private-file helpers**

Add these imports to `evolver.py`:

```python
import os
import secrets
import stat
import tempfile
from dataclasses import dataclass
from pathlib import Path
```

Replace `from typing import BinaryIO, Optional, Sequence` with:

```python
from typing import BinaryIO, Optional, Sequence, TextIO
```

Add these definitions below the constants:

```python
INSTALLATION_SCHEMA = 1


@dataclass(frozen=True)
class Installation:
    data_root: Path
    transcript_roots: tuple[Path, ...]
    python: Path
    nonce: str


def fsync_directory(path: Path) -> None:
    descriptor = os.open(str(path), os.O_RDONLY)
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def atomic_write_json(path: Path, payload: dict[str, object], mode: int = 0o600) -> None:
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    descriptor, temporary = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    temporary_path = Path(temporary)
    try:
        os.fchmod(descriptor, mode)
        with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
            json.dump(payload, stream, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
            stream.write("\n")
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary_path, path)
        os.chmod(path, mode)
        fsync_directory(path.parent)
    except BaseException:
        temporary_path.unlink(missing_ok=True)
        raise


def validate_private_directory(path: Path) -> Path:
    if path.is_symlink():
        raise ValueError("data_root_symlink")
    resolved = path.resolve(strict=True)
    info = resolved.stat()
    if not stat.S_ISDIR(info.st_mode):
        raise ValueError("data_root_not_directory")
    if info.st_uid != os.getuid():
        raise ValueError("data_root_owner")
    if stat.S_IMODE(info.st_mode) & 0o077:
        raise ValueError("data_root_permissions")
    return resolved


def validate_private_child_directory(path: Path) -> Path:
    if path.is_symlink():
        raise ValueError("data_child_symlink")
    resolved = path.resolve(strict=True)
    info = resolved.stat()
    if (
        not stat.S_ISDIR(info.st_mode)
        or info.st_uid != os.getuid()
        or stat.S_IMODE(info.st_mode) & 0o077
    ):
        raise ValueError("data_child_permissions")
    return resolved


def validate_private_nonce(path: Path) -> Path:
    if path.is_symlink():
        raise ValueError("nonce_symlink")
    resolved = path.resolve(strict=True)
    info = resolved.stat()
    if (
        not stat.S_ISREG(info.st_mode)
        or info.st_uid != os.getuid()
        or stat.S_IMODE(info.st_mode) & 0o077
    ):
        raise ValueError("nonce_permissions")
    return resolved


def initialize_probe(
    data_root: Path,
    transcript_roots: tuple[Path, ...],
    python: Path,
) -> Path:
    if python != Path("/usr/bin/python3") or not python.is_file():
        raise ValueError("unsupported_python")
    requested = data_root.expanduser()
    if requested.is_symlink():
        raise ValueError("data_root_symlink")
    root = requested.parent.resolve(strict=True) / requested.name
    if root.exists():
        root = validate_private_directory(root)
    else:
        root.mkdir(mode=0o700)
        root = validate_private_directory(root)
    for child in ("incoming", "reports"):
        directory = root / child
        if directory.is_symlink():
            raise ValueError("data_child_symlink")
        if not directory.exists():
            directory.mkdir(mode=0o700)
        validate_private_child_directory(directory)
    canonical_transcripts = tuple(item.expanduser().resolve(strict=True) for item in transcript_roots)
    nonce = secrets.token_hex(32)
    installation_path = root / "installation.json"
    atomic_write_json(
        installation_path,
        {
            "schema_version": INSTALLATION_SCHEMA,
            "data_root": str(root),
            "transcript_roots": [str(item) for item in canonical_transcripts],
            "python": "/usr/bin/python3",
        },
    )
    atomic_write_json(root / "nonce.json", {"schema_version": 1, "nonce": nonce})
    return installation_path


def load_installation(path: Path) -> Installation:
    if path.is_symlink():
        raise ValueError("installation_symlink")
    canonical = path.expanduser().resolve(strict=True)
    info = canonical.stat()
    if not stat.S_ISREG(info.st_mode) or info.st_uid != os.getuid():
        raise ValueError("installation_owner_or_type")
    if stat.S_IMODE(info.st_mode) & 0o077:
        raise ValueError("installation_permissions")
    payload = json.loads(canonical.read_text(encoding="utf-8"))
    if payload.get("schema_version") != INSTALLATION_SCHEMA:
        raise ValueError("unsupported_installation_schema")
    root = validate_private_directory(Path(str(payload["data_root"])))
    transcript_roots = tuple(
        Path(str(item)).resolve(strict=True) for item in payload["transcript_roots"]
    )
    if payload.get("python") != "/usr/bin/python3":
        raise ValueError("unsupported_python")
    for child in ("incoming", "reports"):
        validate_private_child_directory(root / child)
    nonce_path = validate_private_nonce(root / "nonce.json")
    nonce_payload = json.loads(nonce_path.read_text(encoding="utf-8"))
    nonce = nonce_payload.get("nonce")
    if (
        nonce_payload.get("schema_version") != 1
        or not isinstance(nonce, str)
        or len(nonce) != 64
        or any(character not in "0123456789abcdef" for character in nonce)
    ):
        raise ValueError("invalid_nonce")
    return Installation(
        data_root=root,
        transcript_roots=transcript_roots,
        python=Path("/usr/bin/python3"),
        nonce=nonce,
    )
```

- [ ] **Step 4: Add initialization, status, and list commands**

Add these handlers to `evolver.py`:

```python
def write_json_stdout(payload: dict[str, object], stream: TextIO = sys.stdout) -> None:
    json.dump(payload, stream, ensure_ascii=False, sort_keys=True, indent=2)
    stream.write("\n")


def cmd_probe_init(args: argparse.Namespace) -> int:
    installation = initialize_probe(
        Path(args.data_root),
        tuple(Path(item) for item in args.transcript_root),
        Path("/usr/bin/python3"),
    )
    write_json_stdout({"status": "initialized", "installation": str(installation)})
    return 0


def observation_paths(installation: Installation) -> list[Path]:
    return sorted((installation.data_root / "incoming").glob("*.json"))


def cmd_probe_status(args: argparse.Namespace) -> int:
    installation = load_installation(Path(args.installation))
    observations = observation_paths(installation)
    write_json_stdout(
        {
            "status": "ready",
            "shared_nonce_present": bool(installation.nonce),
            "observation_count": len(observations),
            "latest_observation": observations[-1].name if observations else None,
        }
    )
    return 0


def cmd_probe_list(args: argparse.Namespace) -> int:
    installation = load_installation(Path(args.installation))
    write_json_stdout(
        {
            "observations": [
                {"name": path.name, "size": path.stat().st_size}
                for path in observation_paths(installation)
            ]
        }
    )
    return 0
```

Add these parsers inside `build_parser()` before its `return`:

```python
    probe_init = subparsers.add_parser("probe-init")
    probe_init.add_argument("--data-root", required=True)
    probe_init.add_argument("--transcript-root", action="append", required=True)
    probe_init.set_defaults(handler=cmd_probe_init)

    probe_status = subparsers.add_parser("probe-status")
    probe_status.add_argument("--installation", required=True)
    probe_status.set_defaults(handler=cmd_probe_status)

    probe_list = subparsers.add_parser("probe-list")
    probe_list.add_argument("--installation", required=True)
    probe_list.set_defaults(handler=cmd_probe_list)
```

- [ ] **Step 5: Run the installation and regression tests**

Run:

```bash
/usr/bin/python3 -m unittest discover \
  -s skill-evolver/skills/skill-evolver/tests \
  -p 'test_*.py' -v
```

Expected: all Task 1 and Task 2 tests PASS.

- [ ] **Step 6: Initialize the real private probe directory**

First confirm this is a fresh spike:

```bash
test ! -e /Users/igyeongseob/.codex/skill-evolver-feasibility
```

Expected: exit code `0`. If it exits `1`, stop and ask the user whether to archive or delete the exact prior probe root after its uninstall and TTY scrub; do not overwrite its nonce or observations.

Run:

```bash
/usr/bin/python3 -I \
  skill-evolver/skills/skill-evolver/scripts/evolver.py \
  probe-init \
  --data-root /Users/igyeongseob/.codex/skill-evolver-feasibility \
  --transcript-root /Users/igyeongseob/.codex/sessions \
  --transcript-root /Users/igyeongseob/.codex/archived_sessions
```

Expected JSON:

```json
{
  "installation": "/Users/igyeongseob/.codex/skill-evolver-feasibility/installation.json",
  "status": "initialized"
}
```

Run:

```bash
/usr/bin/python3 -I \
  skill-evolver/skills/skill-evolver/scripts/evolver.py \
  probe-status \
  --installation /Users/igyeongseob/.codex/skill-evolver-feasibility/installation.json
```

Expected: `"status": "ready"`, `"shared_nonce_present": true`, and `"observation_count": 0`.

- [ ] **Step 7: Commit the private-root implementation**

```bash
git add \
  skill-evolver/skills/skill-evolver/scripts/evolver.py \
  skill-evolver/skills/skill-evolver/tests/test_installation.py
git commit -m "feat: add private feasibility installation"
```

Expected: commit succeeds without staging the private data root.

---

### Task 3: Capture and Sanitize Real CLI and Desktop Stop Payloads

**Files:**
- Modify: `skill-evolver/skills/skill-evolver/scripts/evolver.py`
- Create: `skill-evolver/skills/skill-evolver/tests/test_stop_probe.py`
- Create: `skill-evolver/skills/skill-evolver/tests/fixtures/access-cli.structure.json`
- Create: `skill-evolver/skills/skill-evolver/tests/fixtures/access-desktop.structure.json`
- Create: `skill-evolver/skills/skill-evolver/tests/fixtures/stop-cli.structure.json`
- Create: `skill-evolver/skills/skill-evolver/tests/fixtures/stop-desktop.structure.json`

**Interfaces:**
- Consumes: `Installation`, `load_installation()`, `atomic_write_json()`, and `summarize_hook_shape()`.
- Produces: `StopEnvelope`, `is_within()`, `stat_transcript()`, `parse_stop_envelope()`, `capture_stop()`, `arm_skill_preflight()`, `run_skill_preflight()`, `promote_skill_preflight()`, `mark_surface_boundary()`, `surface_observations()`, `promote_surface_stop()`, and their `probe-*` commands.

- [ ] **Step 1: Write the failing Stop capture tests**

Create `skill-evolver/skills/skill-evolver/tests/test_stop_probe.py`:

```python
from __future__ import annotations

import argparse
import json
import os
import stat
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from support import load_runtime, run_isolated


class StopProbeTests(unittest.TestCase):
    def setUp(self) -> None:
        self.runtime = load_runtime()
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.base = Path(self.temp.name)
        self.sessions = self.base / "sessions"
        self.sessions.mkdir(mode=0o700)
        self.transcript = self.sessions / "session.jsonl"
        self.transcript.write_text(
            '{"type":"message","turn_id":"turn-1","content":"transcript-body-secret"}\n',
            encoding="utf-8",
        )
        self.installation_path = self.runtime.initialize_probe(
            self.base / "probe", (self.sessions,), Path("/usr/bin/python3")
        )
        self.installation = self.runtime.load_installation(self.installation_path)
        self.payload = {
            "hook_event_name": "Stop",
            "session_id": "session-secret",
            "turn_id": "turn-secret",
            "transcript_path": str(self.transcript),
            "cwd": str(self.base),
            "model": "probe-model",
        }

    def test_capture_records_metadata_not_transcript_body(self) -> None:
        with mock.patch("socket.socket", side_effect=AssertionError("network forbidden")):
            observation = self.runtime.capture_stop(
                self.installation, json.dumps(self.payload).encode()
            )

        stored = json.loads(observation.read_text(encoding="utf-8"))
        self.assertEqual(stored["event"]["session_id"], "session-secret")
        self.assertEqual(stored["event"]["turn_id"], "turn-secret")
        self.assertEqual(stored["transcript_stat"]["size"], self.transcript.stat().st_size)
        self.assertNotIn("transcript-body-secret", observation.read_text(encoding="utf-8"))
        self.assertEqual(stat.S_IMODE(observation.stat().st_mode), 0o600)

    def test_invalid_event_is_persisted_as_bounded_failure(self) -> None:
        self.payload["hook_event_name"] = "SubagentStop"
        observation = self.runtime.capture_stop(
            self.installation, json.dumps(self.payload).encode()
        )
        stored = json.loads(observation.read_text(encoding="utf-8"))
        self.assertEqual(stored["capture_error_code"], "not_stop_event")
        self.assertNotIn("event", stored)
        self.assertFalse(stored["shape"]["required_fields"]["hook_event_name"]["valid"])

    def test_missing_turn_and_null_transcript_are_persisted_as_invalid_shape(self) -> None:
        self.payload.pop("turn_id")
        self.payload["transcript_path"] = None
        observation = self.runtime.capture_stop(
            self.installation, json.dumps(self.payload).encode()
        )
        stored = json.loads(observation.read_text(encoding="utf-8"))
        required = stored["shape"]["required_fields"]
        self.assertFalse(required["turn_id"]["valid"])
        self.assertFalse(required["transcript_path"]["valid"])
        self.assertEqual(stored["capture_error_code"], "invalid_turn_id")

    def test_wrong_required_field_type_is_persisted_as_invalid_shape(self) -> None:
        self.payload["session_id"] = 7
        observation = self.runtime.capture_stop(
            self.installation, json.dumps(self.payload).encode()
        )
        stored = json.loads(observation.read_text(encoding="utf-8"))
        field = stored["shape"]["required_fields"]["session_id"]
        self.assertEqual(field["type"], "int")
        self.assertFalse(field["valid"])
        self.assertEqual(stored["capture_error_code"], "invalid_session_id")

    def test_transcript_outside_fixed_roots_is_recorded_as_failure(self) -> None:
        outside = self.base / "outside.jsonl"
        outside.write_text("{}\n", encoding="utf-8")
        self.payload["transcript_path"] = str(outside)
        observation = self.runtime.capture_stop(
            self.installation, json.dumps(self.payload).encode()
        )
        stored = json.loads(observation.read_text(encoding="utf-8"))
        self.assertEqual(
            stored["capture_error_code"],
            "transcript_outside_roots",
        )
        self.assertNotIn(str(outside), observation.read_text(encoding="utf-8"))

    def test_hook_command_is_silent_for_valid_and_oversized_input(self) -> None:
        valid = run_isolated(
            "probe-stop",
            "--installation",
            str(self.installation_path),
            stdin=json.dumps(self.payload).encode(),
        )
        oversized = run_isolated(
            "probe-stop",
            "--installation",
            str(self.installation_path),
            stdin=b"x" * 65_537,
        )
        for result in (valid, oversized):
            self.assertEqual(result.returncode, 0)
            self.assertEqual(result.stdout, b"")
            self.assertEqual(result.stderr, b"")

    def test_promoted_fixture_contains_structure_only(self) -> None:
        self.runtime.mark_surface_boundary(self.installation, "cli")
        self.runtime.capture_stop(self.installation, json.dumps(self.payload).encode())
        second = {**self.payload, "turn_id": "turn-secret-2"}
        self.runtime.capture_stop(self.installation, json.dumps(second).encode())
        output = self.base / "stop.structure.json"
        report = self.runtime.promote_surface_stop(self.installation, "cli", output)
        serialized = output.read_text(encoding="utf-8")

        self.assertEqual(report["surface"], "cli")
        self.assertEqual(report["observation_count"], 2)
        self.assertTrue(report["capture_supported"])
        self.assertTrue(report["payload_shapes_stable"])
        self.assertTrue(report["distinct_turns"])
        self.assertTrue(report["required_fields"]["turn_id"]["valid"])
        self.assertNotIn("session-secret", serialized)
        self.assertNotIn("turn-secret", serialized)
        self.assertNotIn(str(self.base), serialized)
        self.assertNotIn("probe-model", serialized)

    def test_duplicate_turn_delivery_is_not_independent(self) -> None:
        self.runtime.mark_surface_boundary(self.installation, "cli")
        self.runtime.capture_stop(self.installation, json.dumps(self.payload).encode())
        self.runtime.capture_stop(self.installation, json.dumps(self.payload).encode())
        report = self.runtime.promote_surface_stop(
            self.installation,
            "cli",
            self.base / "stop.structure.json",
        )
        self.assertFalse(report["distinct_turns"])
        self.assertFalse(report["capture_supported"])

    def test_surface_promotion_writes_failure_for_wrong_observation_count(self) -> None:
        self.runtime.mark_surface_boundary(self.installation, "cli")
        self.runtime.capture_stop(self.installation, json.dumps(self.payload).encode())
        output = self.base / "stop.structure.json"
        report = self.runtime.promote_surface_stop(
            self.installation,
            "cli",
            output,
        )
        self.assertFalse(report["capture_supported"])
        self.assertEqual(report["capture_error_codes"], ["surface_observation_count"])
        self.assertTrue(output.is_file())

    def test_promote_stop_command_returns_two_for_failure_fixture(self) -> None:
        self.runtime.mark_surface_boundary(self.installation, "cli")
        args = argparse.Namespace(
            installation=str(self.installation_path),
            surface="cli",
            output=str(self.base / "stop.structure.json"),
        )
        with mock.patch.object(self.runtime, "write_json_stdout"):
            self.assertEqual(self.runtime.cmd_probe_promote_stop(args), 2)

    def test_skill_preflight_requires_current_challenge_and_round_trips_nonce(self) -> None:
        self.runtime.arm_skill_preflight(self.installation, "cli")
        result = self.runtime.run_skill_preflight(self.installation, "cli")
        report = self.runtime.promote_skill_preflight(
            self.installation,
            "cli",
            self.base / "access.structure.json",
        )
        self.assertEqual(result, {"surface": "cli", "read": True, "write": True})
        self.assertEqual(report["schema_version"], 1)
        self.assertEqual(
            {key: report[key] for key in ("surface", "read", "write")},
            result,
        )

    def test_rearming_invalidates_the_previous_skill_response(self) -> None:
        self.runtime.arm_skill_preflight(self.installation, "cli")
        self.runtime.run_skill_preflight(self.installation, "cli")
        self.runtime.arm_skill_preflight(self.installation, "cli")
        report = self.runtime.promote_skill_preflight(
            self.installation,
            "cli",
            self.base / "access.structure.json",
        )
        self.assertEqual(
            report,
            {
                "schema_version": 1,
                "surface": "cli",
                "read": False,
                "write": False,
            },
        )


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: Run the Stop tests and verify the failure**

Run:

```bash
/usr/bin/python3 -m unittest discover \
  -s skill-evolver/skills/skill-evolver/tests \
  -p 'test_stop_probe.py' -v
```

Expected: FAIL because Stop envelope and capture functions are undefined.

- [ ] **Step 3: Add bounded Stop parsing and transcript stat capture**

Add `time` to the imports in `evolver.py`, then add:

```python
import time


@dataclass(frozen=True)
class StopEnvelope:
    session_id: str
    turn_id: str
    transcript_path: Path
    cwd: Path
    shape: dict[str, object]


def is_within(path: Path, roots: tuple[Path, ...]) -> bool:
    candidate = str(path)
    return any(
        os.path.commonpath((candidate, str(root))) == str(root)
        for root in roots
    )


def bounded_text(payload: dict[str, object], key: str, maximum: int) -> str:
    value = payload.get(key)
    if not isinstance(value, str) or not value or len(value.encode("utf-8")) > maximum:
        raise ValueError(f"invalid_{key}")
    return value


def parse_stop_envelope(raw: bytes, installation: Installation) -> StopEnvelope:
    if len(raw) > MAX_STDIN_BYTES:
        raise ValueError("hook_input_too_large")
    payload = json.loads(raw)
    shape = summarize_hook_shape(payload)
    if payload.get("hook_event_name") != "Stop":
        raise ValueError("not_stop_event")
    session_id = bounded_text(payload, "session_id", 512)
    turn_id = bounded_text(payload, "turn_id", 512)
    cwd = Path(bounded_text(payload, "cwd", 4_096)).expanduser().resolve(strict=True)
    transcript_value = bounded_text(payload, "transcript_path", 4_096)
    transcript_path = Path(transcript_value).expanduser()
    if transcript_path.is_symlink():
        raise ValueError("transcript_symlink")
    transcript_path = transcript_path.resolve(strict=True)
    if not is_within(transcript_path, installation.transcript_roots):
        raise ValueError("transcript_outside_roots")
    return StopEnvelope(session_id, turn_id, transcript_path, cwd, shape)


def stat_transcript(path: Path) -> dict[str, object]:
    flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0)
    descriptor = os.open(str(path), flags)
    try:
        info = os.fstat(descriptor)
    finally:
        os.close(descriptor)
    if not stat.S_ISREG(info.st_mode):
        raise ValueError("transcript_not_regular")
    if info.st_uid != os.getuid():
        raise ValueError("transcript_owner")
    return {
        "size": info.st_size,
        "mtime_ns": info.st_mtime_ns,
        "device": info.st_dev,
        "inode": info.st_ino,
        "regular": True,
        "owned_by_current_user": True,
    }


CAPTURE_ERROR_CODES = {
    "hook_input_too_large",
    "invalid_cwd",
    "invalid_session_id",
    "invalid_transcript_path",
    "invalid_turn_id",
    "not_stop_event",
    "transcript_not_regular",
    "transcript_outside_roots",
    "transcript_owner",
    "transcript_symlink",
}


def safe_capture_error_code(error: BaseException) -> str:
    code = str(error)
    return code if isinstance(error, ValueError) and code in CAPTURE_ERROR_CODES else "transcript_unavailable"


def capture_stop(installation: Installation, raw: bytes) -> Path:
    if len(raw) > MAX_STDIN_BYTES:
        raise ValueError("hook_input_too_large")
    payload = json.loads(raw)
    shape = summarize_hook_shape(payload)
    observation = {
        "schema_version": 1,
        "received_at_ns": time.time_ns(),
        "installation_nonce": installation.nonce,
        "shape": shape,
    }
    try:
        envelope = parse_stop_envelope(raw, installation)
        transcript_info = stat_transcript(envelope.transcript_path)
    except (KeyError, OSError, ValueError) as error:
        observation["capture_error_code"] = safe_capture_error_code(error)
    else:
        observation["event"] = {
            "hook_event_name": "Stop",
            "session_id": envelope.session_id,
            "turn_id": envelope.turn_id,
            "transcript_path": str(envelope.transcript_path),
            "cwd": str(envelope.cwd),
        }
        observation["transcript_stat"] = transcript_info
    destination = installation.data_root / "incoming" / (
        f"{observation['received_at_ns']}-{os.getpid()}-{secrets.token_hex(4)}.json"
    )
    atomic_write_json(destination, observation)
    return destination
```

- [ ] **Step 4: Replace the silent Hook handler**

Replace `cmd_probe_stop()` with:

```python
def cmd_probe_stop(args: argparse.Namespace) -> int:
    try:
        installation = load_installation(Path(args.installation))
        raw = read_bounded_stdin(sys.stdin.buffer)
        capture_stop(installation, raw)
    except Exception:
        pass
    return 0
```

- [ ] **Step 5: Add the actual skill-process read/write preflight**

```python
def validate_surface(surface: str) -> str:
    if surface not in {"cli", "desktop"}:
        raise ValueError("invalid_surface")
    return surface


def arm_skill_preflight(
    installation: Installation,
    surface: str,
) -> dict[str, object]:
    surface = validate_surface(surface)
    reports = installation.data_root / "reports"
    challenge_path = reports / f"{surface}-skill-challenge.json"
    response_path = reports / f"{surface}-skill-response.json"
    response_path.unlink(missing_ok=True)
    atomic_write_json(
        challenge_path,
        {
            "schema_version": 1,
            "surface": surface,
            "installation_nonce": installation.nonce,
            "challenge": secrets.token_hex(32),
        },
    )
    fsync_directory(reports)
    return {"surface": surface, "armed": True}


def run_skill_preflight(
    installation: Installation,
    surface: str,
) -> dict[str, object]:
    surface = validate_surface(surface)
    reports = installation.data_root / "reports"
    challenge = json.loads(
        (reports / f"{surface}-skill-challenge.json").read_text(encoding="utf-8")
    )
    if (
        challenge.get("surface") != surface
        or challenge.get("installation_nonce") != installation.nonce
        or not isinstance(challenge.get("challenge"), str)
    ):
        raise ValueError("invalid_skill_challenge")
    response_path = reports / f"{surface}-skill-response.json"
    atomic_write_json(
        response_path,
        {
            "schema_version": 1,
            "surface": surface,
            "installation_nonce": installation.nonce,
            "challenge": challenge["challenge"],
        },
    )
    response = json.loads(response_path.read_text(encoding="utf-8"))
    if response != {
        "schema_version": 1,
        "surface": surface,
        "installation_nonce": installation.nonce,
        "challenge": challenge["challenge"],
    }:
        raise ValueError("skill_preflight_round_trip")
    return {"surface": surface, "read": True, "write": True}
```

- [ ] **Step 6: Add sanitized preflight promotion and handlers**

Add:

```python
def promote_skill_preflight(
    installation: Installation,
    surface: str,
    output: Path,
) -> dict[str, object]:
    surface = validate_surface(surface)
    reports = installation.data_root / "reports"
    passed = False
    try:
        challenge = json.loads(
            (reports / f"{surface}-skill-challenge.json").read_text(encoding="utf-8")
        )
        response = json.loads(
            (reports / f"{surface}-skill-response.json").read_text(encoding="utf-8")
        )
        passed = (
            challenge.get("surface") == surface
            and response.get("surface") == surface
            and challenge.get("installation_nonce") == installation.nonce
            and response.get("installation_nonce") == installation.nonce
            and response.get("challenge") == challenge.get("challenge")
        )
    except (KeyError, OSError, json.JSONDecodeError):
        passed = False
    report = {
        "schema_version": 1,
        "surface": surface,
        "read": passed,
        "write": passed,
    }
    atomic_write_json(output.resolve(), report)
    return report


def cmd_probe_arm_skill_preflight(args: argparse.Namespace) -> int:
    installation = load_installation(Path(args.installation))
    write_json_stdout(arm_skill_preflight(installation, args.surface))
    return 0


def cmd_probe_skill_preflight(args: argparse.Namespace) -> int:
    installation = load_installation(Path(args.installation))
    write_json_stdout(run_skill_preflight(installation, args.surface))
    return 0


def cmd_probe_promote_access(args: argparse.Namespace) -> int:
    installation = load_installation(Path(args.installation))
    report = promote_skill_preflight(installation, args.surface, Path(args.output))
    write_json_stdout(report)
    return 0 if report["read"] and report["write"] else 2
```

- [ ] **Step 7: Add exact two-observation surface boundaries**

```python
def mark_surface_boundary(
    installation: Installation,
    surface: str,
) -> dict[str, object]:
    surface = validate_surface(surface)
    observations = observation_paths(installation)
    boundary = observations[-1].name if observations else None
    atomic_write_json(
        installation.data_root / "reports" / f"{surface}-boundary.json",
        {"schema_version": 1, "surface": surface, "after": boundary},
    )
    return {"surface": surface, "marked": True}


def surface_observations(
    installation: Installation,
    surface: str,
) -> list[Path]:
    surface = validate_surface(surface)
    observations = observation_paths(installation)
    marker = json.loads(
        (
            installation.data_root / "reports" / f"{surface}-boundary.json"
        ).read_text(encoding="utf-8")
    )
    if marker.get("surface") != surface:
        raise ValueError("surface_boundary_mismatch")
    after = marker.get("after")
    if after is None:
        selected = observations
    else:
        names = [path.name for path in observations]
        if after not in names:
            raise ValueError("surface_boundary_missing")
        selected = observations[names.index(after) + 1 :]
    if len(selected) != 2:
        raise ValueError("surface_observation_count")
    return selected
```

- [ ] **Step 8: Add Stop aggregation and a sanitized failure fixture**

Add:

```python
def aggregate_required_fields(
    observations: list[dict[str, object]],
) -> dict[str, object]:
    names = ("hook_event_name", "session_id", "turn_id", "cwd", "transcript_path")
    result: dict[str, object] = {}
    for name in names:
        fields = [item["shape"]["required_fields"][name] for item in observations]
        types = {field.get("type") for field in fields}
        result[name] = {
            "present": all(field.get("present") is True for field in fields),
            "type": next(iter(types)) if len(types) == 1 else "mixed",
            "valid": all(field.get("valid") is True for field in fields),
        }
    return result


def failed_stop_promotion(
    surface: str,
    output: Path,
    code: str,
) -> dict[str, object]:
    report = {
        "schema_version": 1,
        "surface": surface,
        "observation_count": 0,
        "capture_supported": False,
        "distinct_turns": False,
        "hook_event_name": "Stop",
        "payload_shapes_stable": False,
        "payload_keys": [],
        "field_types": {},
        "required_fields": {
            name: {"present": False, "type": None, "valid": False}
            for name in (
                "hook_event_name",
                "session_id",
                "turn_id",
                "cwd",
                "transcript_path",
            )
        },
        "capture_error_codes": [code],
        "transcript_stat": {
            "present": False,
            "regular": False,
            "owned_by_current_user": False,
            "size_positive": False,
            "has_mtime_ns": False,
            "has_device": False,
            "has_inode": False,
        },
        "shared_nonce_match": False,
    }
    atomic_write_json(output.resolve(), report)
    return report
```

- [ ] **Step 9: Add successful Stop promotion**

Add:

```python
def promote_surface_stop(
    installation: Installation,
    surface: str,
    output: Path,
) -> dict[str, object]:
    surface = validate_surface(surface)
    try:
        paths = surface_observations(installation, surface)
        observations = [
            json.loads(path.read_text(encoding="utf-8"))
            for path in paths
        ]
        if not all(isinstance(item, dict) for item in observations):
            raise ValueError("invalid_stop_observation")
    except (KeyError, OSError, TypeError, ValueError, json.JSONDecodeError) as error:
        allowed = {
            "invalid_stop_observation",
            "surface_boundary_mismatch",
            "surface_boundary_missing",
            "surface_observation_count",
        }
        code = str(error)
        return failed_stop_promotion(
            surface,
            output,
            code if isinstance(error, ValueError) and code in allowed else "surface_observation_unavailable",
        )
    nonce_matches = all(
        item.get("installation_nonce") == installation.nonce
        for item in observations
    )
    shapes = [item["shape"] for item in observations]
    transcript_infos = [
        item.get("transcript_stat")
        for item in observations
    ]
    transcript_present = all(isinstance(item, dict) for item in transcript_infos)
    turn_identities = {
        (
            item["event"]["session_id"],
            item["event"]["turn_id"],
        )
        for item in observations
        if isinstance(item.get("event"), dict)
    }
    required_fields = aggregate_required_fields(observations)
    capture_error_codes = sorted(
        {
            str(item["capture_error_code"])
            for item in observations
            if "capture_error_code" in item
        }
    )
    payload_shapes_stable = shapes[0] == shapes[1]
    distinct_turns = len(turn_identities) == 2
    capture_supported = (
        nonce_matches
        and payload_shapes_stable
        and distinct_turns
        and not capture_error_codes
        and all(field["valid"] is True for field in required_fields.values())
        and transcript_present
        and all(
            item["regular"] is True
            and item["owned_by_current_user"] is True
            and item["size"] > 0
            and isinstance(item["mtime_ns"], int)
            and isinstance(item["device"], int)
            and isinstance(item["inode"], int)
            for item in transcript_infos
        )
    )
    report = {
        "schema_version": 1,
        "surface": surface,
        "observation_count": len(observations),
        "capture_supported": capture_supported,
        "distinct_turns": distinct_turns,
        "hook_event_name": "Stop",
        "payload_shapes_stable": payload_shapes_stable,
        "payload_keys": sorted(
            set(shapes[0]["payload_keys"]) | set(shapes[1]["payload_keys"])
        ),
        "field_types": (
            shapes[0]["field_types"]
            if shapes[0]["field_types"] == shapes[1]["field_types"]
            else {}
        ),
        "required_fields": required_fields,
        "capture_error_codes": capture_error_codes,
        "transcript_stat": {
            "present": transcript_present,
            "regular": transcript_present
            and all(item["regular"] is True for item in transcript_infos),
            "owned_by_current_user": transcript_present
            and all(item["owned_by_current_user"] is True for item in transcript_infos),
            "size_positive": transcript_present
            and all(item["size"] > 0 for item in transcript_infos),
            "has_mtime_ns": transcript_present
            and all(isinstance(item["mtime_ns"], int) for item in transcript_infos),
            "has_device": transcript_present
            and all(isinstance(item["device"], int) for item in transcript_infos),
            "has_inode": transcript_present
            and all(isinstance(item["inode"], int) for item in transcript_infos),
        },
        "shared_nonce_match": nonce_matches,
    }
    atomic_write_json(
        installation.data_root / "reports" / f"{surface}-observation.json",
        {
            "schema_version": 1,
            "surface": surface,
            "observations": [path.name for path in paths],
        },
    )
    atomic_write_json(output.resolve(), report)
    return report
```

- [ ] **Step 10: Add the Stop boundary and promotion command handlers**

Add:

```python
def cmd_probe_mark_surface(args: argparse.Namespace) -> int:
    installation = load_installation(Path(args.installation))
    write_json_stdout(mark_surface_boundary(installation, args.surface))
    return 0


def cmd_probe_promote_stop(args: argparse.Namespace) -> int:
    installation = load_installation(Path(args.installation))
    report = promote_surface_stop(installation, args.surface, Path(args.output))
    write_json_stdout(report)
    return 0 if report["capture_supported"] else 2
```

- [ ] **Step 11: Register the preflight, boundary, and promotion commands**

```python
    arm_preflight = subparsers.add_parser("probe-arm-skill-preflight")
    arm_preflight.add_argument("--installation", required=True)
    arm_preflight.add_argument("--surface", choices=("cli", "desktop"), required=True)
    arm_preflight.set_defaults(handler=cmd_probe_arm_skill_preflight)

    skill_preflight = subparsers.add_parser("probe-skill-preflight")
    skill_preflight.add_argument("--installation", required=True)
    skill_preflight.add_argument("--surface", choices=("cli", "desktop"), required=True)
    skill_preflight.set_defaults(handler=cmd_probe_skill_preflight)

    promote_access = subparsers.add_parser("probe-promote-access")
    promote_access.add_argument("--installation", required=True)
    promote_access.add_argument("--surface", choices=("cli", "desktop"), required=True)
    promote_access.add_argument("--output", required=True)
    promote_access.set_defaults(handler=cmd_probe_promote_access)

    mark_surface = subparsers.add_parser("probe-mark-surface")
    mark_surface.add_argument("--installation", required=True)
    mark_surface.add_argument("--surface", choices=("cli", "desktop"), required=True)
    mark_surface.set_defaults(handler=cmd_probe_mark_surface)

    promote_stop = subparsers.add_parser("probe-promote-stop")
    promote_stop.add_argument("--installation", required=True)
    promote_stop.add_argument("--surface", choices=("cli", "desktop"), required=True)
    promote_stop.add_argument("--output", required=True)
    promote_stop.set_defaults(handler=cmd_probe_promote_stop)
```

- [ ] **Step 12: Run the Stop probe tests**

Run:

```bash
/usr/bin/python3 -m unittest discover \
  -s skill-evolver/skills/skill-evolver/tests \
  -p 'test_*.py' -v
```

Expected: all tests PASS, including silent success for valid and oversized Hook input.

- [ ] **Step 13: Add and install the local development marketplace**

Run:

```bash
codex plugin marketplace add /Users/igyeongseob/Documents/오픈소스 --json
codex plugin add skill-evolver@skill-evolver-dev --json
```

Expected: both commands return successful JSON and `codex plugin list` shows `skill-evolver@skill-evolver-dev` installed.

- [ ] **Step 14: Run the CLI skill-process read/write preflight**

Arm a fresh private challenge:

```bash
/usr/bin/python3 -I \
  skill-evolver/skills/skill-evolver/scripts/evolver.py \
  probe-arm-skill-preflight \
  --installation /Users/igyeongseob/.codex/skill-evolver-feasibility/installation.json \
  --surface cli
```

Start a new Codex CLI task in `/Users/igyeongseob/Documents/오픈소스` with the default `workspace-write` sandbox. Run `/hooks`, verify that the source is `skill-evolver`, inspect the exact command from `hooks/hooks.json`, and trust that command.

Send this exact prompt:

```text
Use $skill-evolver to run the CLI probe preflight. Do not request or use elevated filesystem permission. Return only the preflight command's JSON.
```

After that turn stops, promote the challenge result:

```bash
/usr/bin/python3 -I \
  skill-evolver/skills/skill-evolver/scripts/evolver.py \
  probe-promote-access \
  --installation /Users/igyeongseob/.codex/skill-evolver-feasibility/installation.json \
  --surface cli \
  --output /Users/igyeongseob/Documents/오픈소스/skill-evolver/skills/skill-evolver/tests/fixtures/access-cli.structure.json
```

Expected on success: exit code `0` with `{"surface":"cli","read":true,"write":true}`. Expected when the default sandbox cannot share the root: exit code `2` with both booleans false. Preserve either sanitized result; do not approve escalation to force a pass.

- [ ] **Step 15: Capture two independent CLI turns after an exact boundary**

Mark the boundary after the preflight task has stopped:

```bash
/usr/bin/python3 -I \
  skill-evolver/skills/skill-evolver/scripts/evolver.py \
  probe-mark-surface \
  --installation /Users/igyeongseob/.codex/skill-evolver-feasibility/installation.json \
  --surface cli
```

In one new CLI task rooted at `/Users/igyeongseob/Documents/오픈소스`, send the first prompt and wait for the complete response:

```text
Use the shell to run pwd once, then reply with only probe-cli-one-complete.
```

Then send the second prompt in the same task and wait for the complete response:

```text
Use the shell to run pwd once, then reply with only probe-cli-two-complete.
```

Promote the exact two observations after the marker:

```bash
/usr/bin/python3 -I \
  skill-evolver/skills/skill-evolver/scripts/evolver.py \
  probe-promote-stop \
  --installation /Users/igyeongseob/.codex/skill-evolver-feasibility/installation.json \
  --surface cli \
  --output /Users/igyeongseob/Documents/오픈소스/skill-evolver/skills/skill-evolver/tests/fixtures/stop-cli.structure.json
```

Expected on a supported capture: exit code `0` with `"surface": "cli"`, `"observation_count": 2`, `"capture_supported": true`, `"distinct_turns": true`, and `"payload_shapes_stable": true`. On a real failure, exit code `2` still writes a sanitized fixture. If its `capture_error_codes` is exactly `["surface_observation_count"]` and unrelated task activity is plausible, close those tasks, create a new CLI marker, and repeat only the two exact prompts once; preserve a repeated failure.

- [ ] **Step 16: Run the Desktop preflight and capture two independent Desktop turns**

Arm a fresh Desktop challenge:

```bash
/usr/bin/python3 -I \
  skill-evolver/skills/skill-evolver/scripts/evolver.py \
  probe-arm-skill-preflight \
  --installation /Users/igyeongseob/.codex/skill-evolver-feasibility/installation.json \
  --surface desktop
```

Open a new Codex Desktop task rooted at `/Users/igyeongseob/Documents/오픈소스` with the default `workspace-write` sandbox. Review and trust the same plugin Hook when prompted. Send:

```text
Use $skill-evolver to run the Desktop probe preflight. Do not request or use elevated filesystem permission. Return only the preflight command's JSON.
```

After that turn stops, run:

```bash
/usr/bin/python3 -I \
  skill-evolver/skills/skill-evolver/scripts/evolver.py \
  probe-promote-access \
  --installation /Users/igyeongseob/.codex/skill-evolver-feasibility/installation.json \
  --surface desktop \
  --output /Users/igyeongseob/Documents/오픈소스/skill-evolver/skills/skill-evolver/tests/fixtures/access-desktop.structure.json
```

Preserve either the exit-`0` PASS fixture or exit-`2` FAIL fixture. Then mark the Desktop boundary:

```bash
/usr/bin/python3 -I \
  skill-evolver/skills/skill-evolver/scripts/evolver.py \
  probe-mark-surface \
  --installation /Users/igyeongseob/.codex/skill-evolver-feasibility/installation.json \
  --surface desktop
```

In one new Desktop task, send and await these two prompts sequentially:

```text
Use the shell to run pwd once, then reply with only probe-desktop-one-complete.
```

```text
Use the shell to run pwd once, then reply with only probe-desktop-two-complete.
```

Promote the exact two observations:

```bash
/usr/bin/python3 -I \
  skill-evolver/skills/skill-evolver/scripts/evolver.py \
  probe-promote-stop \
  --installation /Users/igyeongseob/.codex/skill-evolver-feasibility/installation.json \
  --surface desktop \
  --output /Users/igyeongseob/Documents/오픈소스/skill-evolver/skills/skill-evolver/tests/fixtures/stop-desktop.structure.json
```

Expected on a supported capture: exit code `0` with `"surface": "desktop"`, `"observation_count": 2`, `"capture_supported": true`, `"distinct_turns": true`, and `"payload_shapes_stable": true`. On a real failure, exit code `2` still writes the sanitized fixture. Preserve it for the hard gate rather than changing the probe to accept invalid fields.

- [ ] **Step 17: Check that committed fixtures contain no raw values**

Run:

```bash
rg -n \
  '/Users/|session-secret|turn-secret|probe-(cli|desktop)-(one|two)-complete|Authorization|Bearer' \
  skill-evolver/skills/skill-evolver/tests/fixtures/access-*.structure.json \
  skill-evolver/skills/skill-evolver/tests/fixtures/stop-*.structure.json
```

Expected: no matches and exit code `1`.

- [ ] **Step 18: Commit the Stop probe and sanitized fixtures**

```bash
git add \
  skill-evolver/skills/skill-evolver/scripts/evolver.py \
  skill-evolver/skills/skill-evolver/tests/test_stop_probe.py \
  skill-evolver/skills/skill-evolver/tests/fixtures/access-cli.structure.json \
  skill-evolver/skills/skill-evolver/tests/fixtures/access-desktop.structure.json \
  skill-evolver/skills/skill-evolver/tests/fixtures/stop-cli.structure.json \
  skill-evolver/skills/skill-evolver/tests/fixtures/stop-desktop.structure.json
git commit -m "test: verify codex stop hook surfaces"
```

Expected: only source, tests, and sanitized structural fixtures are committed.

---

### Task 4: Prove Prefix-Bounded Turn and Provenance Discovery

**Files:**
- Modify: `skill-evolver/skills/skill-evolver/scripts/evolver.py`
- Create: `skill-evolver/skills/skill-evolver/tests/test_transcript_probe.py`
- Create: `skill-evolver/skills/skill-evolver/tests/fixtures/synthetic-transcript.jsonl`
- Create: `skill-evolver/skills/skill-evolver/tests/fixtures/transcript-cli.structure.json`
- Create: `skill-evolver/skills/skill-evolver/tests/fixtures/transcript-desktop.structure.json`

**Interfaces:**
- Consumes: each private `surface-observation.json` mapping containing exactly two raw Stop observation names from Task 3.
- Produces: `walk_scalars()`, `read_exact_prefix()`, `inspect_transcript_structure()`, `load_surface_observations()`, `promote_transcript_structure()`, and `probe-promote-transcript`.

- [ ] **Step 1: Create a synthetic transcript with a known append boundary**

Create `skill-evolver/skills/skill-evolver/tests/fixtures/synthetic-transcript.jsonl`:

```jsonl
{"record_type":"message","turn_id":"turn-before","role":"user","content":"before"}
{"record_type":"message","turn_id":"turn-target","role":"user","content":"target prompt"}
{"record_type":"tool_result","turn_id":"turn-target","role":"tool","content":"tool result"}
{"record_type":"message","turn_id":"turn-target","role":"assistant","content":"target answer"}
{"record_type":"message","turn_id":"turn-after","role":"user","content":"appended later"}
```

- [ ] **Step 2: Write the failing transcript probe tests**

Create `skill-evolver/skills/skill-evolver/tests/test_transcript_probe.py`:

```python
from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from support import TEST_ROOT, load_runtime


class TranscriptProbeTests(unittest.TestCase):
    def setUp(self) -> None:
        self.runtime = load_runtime()
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        source = TEST_ROOT / "fixtures" / "synthetic-transcript.jsonl"
        lines = source.read_bytes().splitlines(keepends=True)
        self.transcript = self.root / "session.jsonl"
        self.transcript.write_bytes(b"".join(lines[:4]))
        captured = self.transcript.stat()
        self.observation = {
            "schema_version": 1,
            "event": {
                "turn_id": "turn-target",
                "transcript_path": str(self.transcript),
            },
            "transcript_stat": {
                "size": captured.st_size,
                "mtime_ns": captured.st_mtime_ns,
                "device": captured.st_dev,
                "inode": captured.st_ino,
            },
        }
        with self.transcript.open("ab") as stream:
            stream.write(lines[4])

    def test_inspection_reads_only_captured_prefix(self) -> None:
        report = self.runtime.inspect_transcript_structure(self.observation, "cli")

        self.assertTrue(report["supported"])
        self.assertTrue(report["suffix_ignored"])
        self.assertFalse(report["read_past_boundary"])
        self.assertEqual(report["turn_occurrence_count"], 3)
        self.assertEqual(report["turn_record_span"], [1, 3])
        self.assertTrue(report["turn_record_span_contiguous"])
        self.assertEqual(report["provenance_values"], ["assistant", "tool", "user"])
        serialized = json.dumps(report)
        self.assertNotIn("turn-target", serialized)
        self.assertNotIn("target prompt", serialized)
        self.assertNotIn(str(self.root), serialized)

    def test_inode_change_fails_closed(self) -> None:
        replacement = self.root / "replacement.jsonl"
        replacement.write_text("{}\n", encoding="utf-8")
        replacement.replace(self.transcript)
        with self.assertRaisesRegex(ValueError, "transcript_changed"):
            self.runtime.inspect_transcript_structure(self.observation, "cli")

    def test_partial_captured_record_fails_closed(self) -> None:
        self.observation["transcript_stat"]["size"] -= 1
        with self.assertRaisesRegex(ValueError, "captured_prefix_partial_record"):
            self.runtime.inspect_transcript_structure(self.observation, "cli")

    def test_oversized_captured_prefix_fails_before_read(self) -> None:
        self.observation["transcript_stat"]["size"] = 2_097_153
        with self.assertRaisesRegex(ValueError, "oversized_transcript"):
            self.runtime.inspect_transcript_structure(self.observation, "cli")

    def test_unsupported_format_produces_sanitized_failure_report(self) -> None:
        broken = self.root / "broken.jsonl"
        broken.write_bytes(b"not-json\n")
        info = broken.stat()
        observation = {
            "schema_version": 1,
            "event": {"turn_id": "turn-target", "transcript_path": str(broken)},
            "transcript_stat": {
                "size": info.st_size,
                "mtime_ns": info.st_mtime_ns,
                "device": info.st_dev,
                "inode": info.st_ino,
            },
        }
        report = self.runtime.safe_inspect_transcript_structure(observation, "desktop")
        self.assertEqual(
            report,
            {
                "schema_version": 1,
                "surface": "desktop",
                "supported": False,
                "error_code": "unsupported_jsonl",
            },
        )

    def test_promotion_requires_two_observations_with_stable_layout(self) -> None:
        sessions = self.root / "sessions"
        sessions.mkdir(mode=0o700)
        installation_path = self.runtime.initialize_probe(
            self.root / "probe", (sessions,), Path("/usr/bin/python3")
        )
        installation = self.runtime.load_installation(installation_path)
        copied_transcript = sessions / "session.jsonl"
        copied_transcript.write_bytes(self.transcript.read_bytes())
        copied = copied_transcript.stat()
        observation = {
            **self.observation,
            "installation_nonce": installation.nonce,
            "event": {
                **self.observation["event"],
                "transcript_path": str(copied_transcript),
            },
            "transcript_stat": {
                "size": copied.st_size,
                "mtime_ns": copied.st_mtime_ns,
                "device": copied.st_dev,
                "inode": copied.st_ino,
            },
        }
        for name in ("one.json", "two.json"):
            self.runtime.atomic_write_json(
                installation.data_root / "incoming" / name,
                observation,
            )
        self.runtime.atomic_write_json(
            installation.data_root / "reports" / "cli-observation.json",
            {
                "schema_version": 1,
                "surface": "cli",
                "observations": ["one.json", "two.json"],
            },
        )
        report = self.runtime.promote_transcript_structure(
            installation,
            "cli",
            self.root / "transcript.structure.json",
        )
        self.assertTrue(report["supported"])
        self.assertEqual(report["observation_count"], 2)
        self.assertTrue(report["layouts_stable"])


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 3: Run the transcript tests and verify the failure**

Run:

```bash
/usr/bin/python3 -m unittest discover \
  -s skill-evolver/skills/skill-evolver/tests \
  -p 'test_transcript_probe.py' -v
```

Expected: FAIL because the transcript inspection functions are undefined.

- [ ] **Step 4: Add JSON-pointer walking and exact prefix reads**

Replace `from typing import BinaryIO, Optional, Sequence, TextIO` with:

```python
from typing import BinaryIO, Iterator, Optional, Sequence, TextIO
```

Then add:

```python
PROVENANCE_KEYS = {"role", "source_kind"}
PROVENANCE_VALUES = {
    "assistant",
    "external_content",
    "tool",
    "tool_output",
    "user",
    "user_direct",
}
MAX_TRANSCRIPT_PROBE_BYTES = 2_097_152


def pointer_escape(value: str) -> str:
    return value.replace("~", "~0").replace("/", "~1")


def walk_scalars(value: object, pointer: str = "") -> Iterator[tuple[str, object]]:
    if isinstance(value, dict):
        for key in sorted(value):
            child = f"{pointer}/{pointer_escape(str(key))}"
            yield from walk_scalars(value[key], child)
    elif isinstance(value, list):
        for index, item in enumerate(value):
            yield from walk_scalars(item, f"{pointer}/{index}")
    else:
        yield pointer or "/", value


def read_exact_prefix(descriptor: int, size: int) -> bytes:
    if size < 0:
        raise ValueError("negative_captured_size")
    if size > MAX_TRANSCRIPT_PROBE_BYTES:
        raise ValueError("oversized_transcript")
    chunks: list[bytes] = []
    remaining = size
    while remaining:
        chunk = os.read(descriptor, min(65_536, remaining))
        if not chunk:
            raise ValueError("transcript_changed")
        chunks.append(chunk)
        remaining -= len(chunk)
    return b"".join(chunks)
```

- [ ] **Step 5: Add bounded transcript record loading**

Add:

```python
def load_captured_records(
    observation: dict[str, object],
) -> tuple[list[object], int, int]:
    event = observation["event"]
    captured = observation["transcript_stat"]
    captured_size = int(captured["size"])
    if captured_size > MAX_TRANSCRIPT_PROBE_BYTES:
        raise ValueError("oversized_transcript")
    transcript_path = Path(str(event["transcript_path"]))
    if transcript_path.is_symlink():
        raise ValueError("transcript_changed")
    flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0)
    descriptor = os.open(str(transcript_path), flags)
    try:
        current = os.fstat(descriptor)
        if (
            current.st_dev != captured["device"]
            or current.st_ino != captured["inode"]
            or current.st_size < captured_size
        ):
            raise ValueError("transcript_changed")
        prefix = read_exact_prefix(descriptor, captured_size)
    finally:
        os.close(descriptor)
    if prefix and not prefix.endswith(b"\n"):
        raise ValueError("captured_prefix_partial_record")

    records: list[object] = []
    try:
        for raw_line in prefix.splitlines():
            if raw_line:
                records.append(json.loads(raw_line))
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise ValueError("unsupported_jsonl") from error
    return records, captured_size, current.st_size
```

- [ ] **Step 6: Add deterministic turn and provenance discovery**

Add:

```python
def discover_turn_structure(
    records: list[object],
    turn_id: str,
) -> dict[str, object]:
    turn_matches: list[tuple[int, str]] = []
    provenance: list[tuple[int, str, str]] = []
    for index, record in enumerate(records):
        for pointer, scalar in walk_scalars(record):
            leaf = pointer.rsplit("/", 1)[-1]
            if scalar == turn_id:
                turn_matches.append((index, pointer))
            if leaf in PROVENANCE_KEYS and scalar in PROVENANCE_VALUES:
                provenance.append((index, pointer, str(scalar)))
    if not turn_matches:
        raise ValueError("turn_id_not_found")

    turn_indices = sorted({index for index, _pointer in turn_matches})
    start, end = turn_indices[0], turn_indices[-1]
    contiguous = turn_indices == list(range(start, end + 1))
    relevant_provenance = [
        (index, pointer, value)
        for index, pointer, value in provenance
        if start <= index <= end
    ]
    provenance_values = sorted({value for _index, _pointer, value in relevant_provenance})
    if not {"user", "assistant"}.issubset(provenance_values):
        raise ValueError("provenance_not_found")

    return {
        "turn_occurrence_count": len(turn_matches),
        "turn_record_span": [start, end],
        "turn_record_span_contiguous": contiguous,
        "turn_id_pointer_paths": sorted({pointer for _index, pointer in turn_matches}),
        "provenance_pointer_paths": sorted(
            {pointer for _index, pointer, _value in relevant_provenance}
        ),
        "provenance_values": provenance_values,
    }
```

- [ ] **Step 7: Compose the sanitized inspection and fail-closed wrapper**

Add:

```python
def inspect_transcript_structure(
    observation: dict[str, object],
    surface: str,
) -> dict[str, object]:
    records, captured_size, current_size = load_captured_records(observation)
    turn = discover_turn_structure(
        records,
        str(observation["event"]["turn_id"]),
    )
    return {
        "schema_version": 1,
        "surface": surface,
        "supported": True,
        "format": "jsonl",
        "record_count": len(records),
        "captured_size": captured_size,
        "current_size": current_size,
        "suffix_ignored": current_size > captured_size,
        "read_past_boundary": False,
        **turn,
    }


def safe_inspect_transcript_structure(
    observation: dict[str, object],
    surface: str,
) -> dict[str, object]:
    if "event" not in observation or "transcript_stat" not in observation:
        code = observation.get("capture_error_code")
        return {
            "schema_version": 1,
            "surface": surface,
            "supported": False,
            "error_code": (
                str(code)
                if isinstance(code, str) and code in CAPTURE_ERROR_CODES
                else "capture_invalid"
            ),
        }
    try:
        return inspect_transcript_structure(observation, surface)
    except (KeyError, OSError, TypeError, ValueError) as error:
        allowed = {
            "captured_prefix_partial_record",
            "oversized_transcript",
            "provenance_not_found",
            "transcript_changed",
            "turn_id_not_found",
            "unsupported_jsonl",
        }
        code = str(error)
        return {
            "schema_version": 1,
            "surface": surface,
            "supported": False,
            "error_code": code if isinstance(error, ValueError) and code in allowed else "transcript_unavailable",
        }
```

- [ ] **Step 8: Add surface mapping lookup and the sanitized failure result**

Add:

```python
def load_surface_observations(
    installation: Installation,
    surface: str,
) -> list[dict[str, object]]:
    surface = validate_surface(surface)
    mapping_path = installation.data_root / "reports" / f"{surface}-observation.json"
    mapping = json.loads(mapping_path.read_text(encoding="utf-8"))
    filenames = mapping.get("observations")
    if (
        mapping.get("surface") != surface
        or not isinstance(filenames, list)
        or len(filenames) != 2
        or len(set(filenames)) != 2
    ):
        raise ValueError("invalid_surface_mapping")
    observations: list[dict[str, object]] = []
    for value in filenames:
        filename = str(value)
        if Path(filename).name != filename:
            raise ValueError("invalid_observation_name")
        observation_path = installation.data_root / "incoming" / filename
        observations.append(json.loads(observation_path.read_text(encoding="utf-8")))
    return observations


def failed_transcript_promotion(
    surface: str,
    code: str,
) -> dict[str, object]:
    return {
        "schema_version": 1,
        "surface": surface,
        "observation_count": 0,
        "supported": False,
        "layouts_stable": False,
        "error_codes": [code],
    }
```

- [ ] **Step 9: Add transcript fixture promotion**

Add:

```python
def promote_transcript_structure(
    installation: Installation,
    surface: str,
    output: Path,
) -> dict[str, object]:
    surface = validate_surface(surface)
    try:
        observations = load_surface_observations(installation, surface)
    except (KeyError, OSError, TypeError, ValueError, json.JSONDecodeError):
        report = failed_transcript_promotion(surface, "surface_observation_unavailable")
        atomic_write_json(output.resolve(), report)
        return report
    if not all(
        observation.get("installation_nonce") == installation.nonce
        for observation in observations
    ):
        report = failed_transcript_promotion(surface, "shared_nonce_mismatch")
        atomic_write_json(output.resolve(), report)
        return report

    individual = [
        safe_inspect_transcript_structure(observation, surface)
        for observation in observations
    ]
    all_supported = all(item.get("supported") is True for item in individual)
    turn_layouts = [
        tuple(item.get("turn_id_pointer_paths", []))
        for item in individual
    ]
    provenance_layouts = [
        tuple(item.get("provenance_pointer_paths", []))
        for item in individual
    ]
    layouts_stable = (
        all_supported
        and turn_layouts[0] == turn_layouts[1]
        and provenance_layouts[0] == provenance_layouts[1]
    )
    spans_contiguous = all(
        item.get("turn_record_span_contiguous") is True
        for item in individual
    )
    supported = all_supported and layouts_stable and spans_contiguous
    provenance_values = (
        sorted(
            set(individual[0].get("provenance_values", []))
            & set(individual[1].get("provenance_values", []))
        )
        if all_supported
        else []
    )
    error_codes = sorted(
        {
            str(item["error_code"])
            for item in individual
            if "error_code" in item
        }
    )
    if all_supported and not layouts_stable:
        error_codes.append("layout_unstable")
    if all_supported and not spans_contiguous:
        error_codes.append("turn_span_not_contiguous")
    report = {
        "schema_version": 1,
        "surface": surface,
        "observation_count": len(individual),
        "supported": supported,
        "layouts_stable": layouts_stable,
        "format": "jsonl" if all_supported else None,
        "read_past_boundary": any(
            item.get("read_past_boundary") is not False
            for item in individual
        ),
        "suffix_ignored": any(
            item.get("suffix_ignored") is True
            for item in individual
        ),
        "turn_occurrence_counts": [
            item.get("turn_occurrence_count", 0)
            for item in individual
        ],
        "turn_record_spans_contiguous": spans_contiguous,
        "turn_id_pointer_paths": list(turn_layouts[0]) if layouts_stable else [],
        "provenance_pointer_paths": (
            list(provenance_layouts[0]) if layouts_stable else []
        ),
        "provenance_values": provenance_values,
        "error_codes": error_codes,
    }
    atomic_write_json(output.resolve(), report)
    return report
```

- [ ] **Step 10: Add the transcript promotion command and parser**

Add:

```python
def cmd_probe_promote_transcript(args: argparse.Namespace) -> int:
    installation = load_installation(Path(args.installation))
    report = promote_transcript_structure(
        installation,
        args.surface,
        Path(args.output),
    )
    write_json_stdout(report)
    return 0 if report["supported"] else 2
```

Add this parser inside `build_parser()`:

```python
    promote_transcript = subparsers.add_parser("probe-promote-transcript")
    promote_transcript.add_argument("--installation", required=True)
    promote_transcript.add_argument("--surface", choices=("cli", "desktop"), required=True)
    promote_transcript.add_argument("--output", required=True)
    promote_transcript.set_defaults(handler=cmd_probe_promote_transcript)
```

- [ ] **Step 11: Run all deterministic tests**

Run:

```bash
/usr/bin/python3 -m unittest discover \
  -s skill-evolver/skills/skill-evolver/tests \
  -p 'test_*.py' -v
```

Expected: all tests PASS.

- [ ] **Step 12: Generate the sanitized CLI transcript layout**

Run:

```bash
/usr/bin/python3 -I \
  skill-evolver/skills/skill-evolver/scripts/evolver.py \
  probe-promote-transcript \
  --installation /Users/igyeongseob/.codex/skill-evolver-feasibility/installation.json \
  --surface cli \
  --output /Users/igyeongseob/Documents/오픈소스/skill-evolver/skills/skill-evolver/tests/fixtures/transcript-cli.structure.json
```

Expected on a supported layout: exit code `0`, `"observation_count": 2`, `"supported": true`, `"layouts_stable": true`, `"read_past_boundary": false`, at least one `turn_id_pointer_paths` entry, and provenance containing the exact values `"user"` and `"assistant"`.

- [ ] **Step 13: Generate the sanitized Desktop transcript layout**

Run:

```bash
/usr/bin/python3 -I \
  skill-evolver/skills/skill-evolver/scripts/evolver.py \
  probe-promote-transcript \
  --installation /Users/igyeongseob/.codex/skill-evolver-feasibility/installation.json \
  --surface desktop \
  --output /Users/igyeongseob/Documents/오픈소스/skill-evolver/skills/skill-evolver/tests/fixtures/transcript-desktop.structure.json
```

Expected on a supported layout: exit code `0`, `"observation_count": 2`, `"supported": true`, `"layouts_stable": true`, `"read_past_boundary": false`, at least one `turn_id_pointer_paths` entry, and provenance containing the exact values `"user"` and `"assistant"`.

If either command exits `2`, keep the sanitized failure fixture, do not alter the parser to guess, and proceed only to Task 5's FAIL-report and cleanup path.

- [ ] **Step 14: Scan all committed structural fixtures for raw content**

Run:

```bash
rg -n \
  '/Users/|session-secret|turn-secret|target prompt|tool result|probe-(cli|desktop)-(one|two)-complete|Authorization|Bearer|PRIVATE KEY' \
  skill-evolver/skills/skill-evolver/tests/fixtures/*.structure.json
```

Expected: no matches and exit code `1`.

- [ ] **Step 15: Commit the transcript probe and structural fixtures**

```bash
git add \
  skill-evolver/skills/skill-evolver/scripts/evolver.py \
  skill-evolver/skills/skill-evolver/tests/test_transcript_probe.py \
  skill-evolver/skills/skill-evolver/tests/fixtures/synthetic-transcript.jsonl \
  skill-evolver/skills/skill-evolver/tests/fixtures/transcript-cli.structure.json \
  skill-evolver/skills/skill-evolver/tests/fixtures/transcript-desktop.structure.json
git commit -m "test: prove transcript turn boundary feasibility"
```

Expected: the commit contains no raw Codex transcript.

---

### Task 5: Generate the Hard-Gate Report and Remove the Probe

**Files:**
- Modify: `skill-evolver/skills/skill-evolver/scripts/evolver.py`
- Modify: `skill-evolver/skills/skill-evolver/SKILL.md`
- Create: `skill-evolver/skills/skill-evolver/tests/test_gate.py`
- Create: `skill-evolver/docs/feasibility-report.json`
- Create: `skill-evolver/docs/feasibility-report.md`

**Interfaces:**
- Consumes: the six sanitized access, Stop, and transcript structural fixtures from Tasks 3 and 4 and the fixed installation from Task 2.
- Produces: surface-bound fixture validators, `structural_differences()`, `evaluate_feasibility_gate()`, `render_gate_markdown()`, `atomic_write_text()`, `write_gate_report()`, `scrub_probe_raw()`, `probe-gate`, and `probe-scrub`.

- [ ] **Step 1: Write the failing gate and cleanup tests**

Create `skill-evolver/skills/skill-evolver/tests/test_gate.py`:

```python
from __future__ import annotations

import argparse
import io
import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from support import load_runtime


def stop_fixture(surface: str, valid: bool = True) -> dict[str, object]:
    required = {
        name: {"present": True, "type": "str", "valid": valid}
        for name in ("hook_event_name", "session_id", "turn_id", "cwd", "transcript_path")
    }
    return {
        "schema_version": 1,
        "surface": surface,
        "observation_count": 2,
        "capture_supported": True,
        "distinct_turns": True,
        "hook_event_name": "Stop",
        "payload_shapes_stable": True,
        "payload_keys": sorted(required),
        "field_types": {name: "str" for name in required},
        "required_fields": required,
        "transcript_stat": {
            "present": True,
            "regular": True,
            "owned_by_current_user": True,
            "size_positive": True,
            "has_mtime_ns": True,
            "has_device": True,
            "has_inode": True,
        },
        "shared_nonce_match": True,
    }


def access_fixture(surface: str, passed: bool = True) -> dict[str, object]:
    return {
        "schema_version": 1,
        "surface": surface,
        "read": passed,
        "write": passed,
    }


def transcript_fixture(surface: str, supported: bool = True) -> dict[str, object]:
    if not supported:
        return {
            "schema_version": 1,
            "surface": surface,
            "observation_count": 2,
            "supported": False,
            "layouts_stable": False,
            "error_codes": ["turn_id_not_found"],
        }
    return {
        "schema_version": 1,
        "surface": surface,
        "observation_count": 2,
        "supported": True,
        "layouts_stable": True,
        "format": "jsonl",
        "suffix_ignored": True,
        "read_past_boundary": False,
        "turn_occurrence_counts": [3, 3],
        "turn_record_spans_contiguous": True,
        "turn_id_pointer_paths": ["/payload/turn_id"],
        "provenance_pointer_paths": ["/payload/role"],
        "provenance_values": ["assistant", "tool", "user"],
    }


class GateTests(unittest.TestCase):
    def setUp(self) -> None:
        self.runtime = load_runtime()
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)

    def test_gate_passes_only_when_both_surfaces_pass_every_check(self) -> None:
        report = self.runtime.evaluate_feasibility_gate(
            stop_fixture("cli"),
            stop_fixture("desktop"),
            transcript_fixture("cli"),
            transcript_fixture("desktop"),
            access_fixture("cli"),
            access_fixture("desktop"),
        )
        self.assertEqual(report["decision"], "PASS")
        self.assertTrue(all(report["checks"].values()))
        self.assertTrue(
            all(not values for values in report["schema_differences"].values())
        )
        markdown = self.runtime.render_gate_markdown(report)
        self.assertIn("CLI and Desktop", markdown)
        self.assertIn("CLI/Desktop schema differences", markdown)

    def test_gate_failure_names_the_failed_surface(self) -> None:
        report = self.runtime.evaluate_feasibility_gate(
            stop_fixture("cli"),
            stop_fixture("desktop"),
            transcript_fixture("cli"),
            transcript_fixture("desktop", supported=False),
            access_fixture("cli"),
            access_fixture("desktop"),
        )
        self.assertEqual(report["decision"], "FAIL")
        self.assertFalse(report["checks"]["desktop_transcript_supported"])
        self.assertEqual(
            report["next_action"],
            "amend_design_for_session_level_queue",
        )

    def test_gate_rejects_duplicated_or_mislabeled_surface_fixture(self) -> None:
        report = self.runtime.evaluate_feasibility_gate(
            stop_fixture("cli"),
            stop_fixture("cli"),
            transcript_fixture("cli"),
            transcript_fixture("cli"),
            access_fixture("cli"),
            access_fixture("cli"),
        )
        self.assertEqual(report["decision"], "FAIL")
        self.assertFalse(report["checks"]["desktop_stop_contract"])
        self.assertFalse(report["checks"]["desktop_transcript_supported"])
        self.assertFalse(report["checks"]["desktop_skill_data_root"])

    def test_gate_rejects_duplicate_turn_delivery(self) -> None:
        duplicate = stop_fixture("cli")
        duplicate["distinct_turns"] = False
        report = self.runtime.evaluate_feasibility_gate(
            duplicate,
            stop_fixture("desktop"),
            transcript_fixture("cli"),
            transcript_fixture("desktop"),
            access_fixture("cli"),
            access_fixture("desktop"),
        )
        self.assertEqual(report["decision"], "FAIL")
        self.assertFalse(report["checks"]["cli_stop_contract"])

    def test_write_gate_report_creates_both_outputs_and_exit_codes(self) -> None:
        fixtures = self.root / "fixtures"
        fixtures.mkdir()
        values = {
            "stop-cli.structure.json": stop_fixture("cli"),
            "stop-desktop.structure.json": stop_fixture("desktop"),
            "transcript-cli.structure.json": transcript_fixture("cli"),
            "transcript-desktop.structure.json": transcript_fixture("desktop"),
            "access-cli.structure.json": access_fixture("cli"),
            "access-desktop.structure.json": access_fixture("desktop"),
        }
        for name, value in values.items():
            self.runtime.atomic_write_json(fixtures / name, value)
        output_json = self.root / "report.json"
        output_markdown = self.root / "report.md"
        args = argparse.Namespace(
            fixture_root=str(fixtures),
            output_json=str(output_json),
            output_markdown=str(output_markdown),
        )
        with mock.patch.object(self.runtime, "write_json_stdout"):
            self.assertEqual(self.runtime.cmd_probe_gate(args), 0)
        self.assertEqual(json.loads(output_json.read_text())["decision"], "PASS")
        self.assertIn("Decision: **PASS**", output_markdown.read_text())

        self.runtime.atomic_write_json(
            fixtures / "access-desktop.structure.json",
            access_fixture("desktop", passed=False),
        )
        with mock.patch.object(self.runtime, "write_json_stdout"):
            self.assertEqual(self.runtime.cmd_probe_gate(args), 2)
        self.assertEqual(json.loads(output_json.read_text())["decision"], "FAIL")

    def test_non_object_gate_fixture_fails_closed(self) -> None:
        fixtures = self.root / "fixtures"
        fixtures.mkdir()
        (fixtures / "stop-cli.structure.json").write_text("[]", encoding="utf-8")
        report = self.runtime.write_gate_report(
            fixtures,
            self.root / "report.json",
            self.root / "report.md",
        )
        self.assertEqual(report["decision"], "FAIL")
        self.assertEqual(report["next_action"], "amend_design_for_session_level_queue")

    def test_malformed_json_gate_fixture_fails_closed(self) -> None:
        fixtures = self.root / "fixtures"
        fixtures.mkdir()
        (fixtures / "stop-cli.structure.json").write_text("{", encoding="utf-8")
        report = self.runtime.write_gate_report(
            fixtures,
            self.root / "report.json",
            self.root / "report.md",
        )
        self.assertEqual(report["decision"], "FAIL")
        self.assertFalse(report["checks"]["gate_inputs_valid"])

    def test_scrub_deletes_only_private_raw_observations(self) -> None:
        sessions = self.root / "sessions"
        sessions.mkdir(mode=0o700)
        installation_path = self.runtime.initialize_probe(
            self.root / "probe", (sessions,), Path("/usr/bin/python3")
        )
        installation = self.runtime.load_installation(installation_path)
        raw = installation.data_root / "incoming" / "raw.json"
        mapping = installation.data_root / "reports" / "cli-observation.json"
        boundary = installation.data_root / "reports" / "cli-boundary.json"
        challenge = installation.data_root / "reports" / "cli-skill-challenge.json"
        response = installation.data_root / "reports" / "cli-skill-response.json"
        committed_report = installation.data_root / "reports" / "gate.json"
        self.runtime.atomic_write_json(raw, {"secret": "raw"})
        self.runtime.atomic_write_json(mapping, {"observation": "raw.json"})
        self.runtime.atomic_write_json(boundary, {"after": None})
        self.runtime.atomic_write_json(challenge, {"challenge": "private"})
        self.runtime.atomic_write_json(response, {"challenge": "private"})
        self.runtime.atomic_write_json(committed_report, {"decision": "PASS"})

        result = self.runtime.scrub_probe_raw(
            installation,
            "DELETE-FEASIBILITY-RAW",
        )

        self.assertEqual(
            result,
            {"observations_deleted": 1, "ephemeral_reports_deleted": 4},
        )
        self.assertFalse(raw.exists())
        self.assertFalse(mapping.exists())
        self.assertFalse(boundary.exists())
        self.assertFalse(challenge.exists())
        self.assertFalse(response.exists())
        self.assertTrue(committed_report.exists())
        self.assertTrue(installation_path.exists())

    def test_scrub_rejects_wrong_confirmation(self) -> None:
        sessions = self.root / "sessions"
        sessions.mkdir(mode=0o700)
        installation_path = self.runtime.initialize_probe(
            self.root / "probe", (sessions,), Path("/usr/bin/python3")
        )
        installation = self.runtime.load_installation(installation_path)
        with self.assertRaisesRegex(ValueError, "confirmation_mismatch"):
            self.runtime.scrub_probe_raw(installation, "DELETE")

    def test_scrub_command_rejects_non_tty_stdin(self) -> None:
        sessions = self.root / "sessions"
        sessions.mkdir(mode=0o700)
        installation_path = self.runtime.initialize_probe(
            self.root / "probe", (sessions,), Path("/usr/bin/python3")
        )
        args = argparse.Namespace(installation=str(installation_path))
        with mock.patch.object(self.runtime.sys, "stdin", io.StringIO()):
            with self.assertRaisesRegex(ValueError, "tty_required"):
                self.runtime.cmd_probe_scrub(args)


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: Run the gate tests and verify the failure**

Run:

```bash
/usr/bin/python3 -m unittest discover \
  -s skill-evolver/skills/skill-evolver/tests \
  -p 'test_gate.py' -v
```

Expected: FAIL because gate and scrub functions are undefined.

- [ ] **Step 3: Implement structural fixture validators**

Add:

```python
def stop_fixture_passes(
    fixture: dict[str, object],
    expected_surface: str,
) -> bool:
    required = fixture.get("required_fields", {})
    transcript_stat = fixture.get("transcript_stat", {})
    return (
        fixture.get("surface") == expected_surface
        and fixture.get("observation_count") == 2
        and fixture.get("capture_supported") is True
        and fixture.get("distinct_turns") is True
        and fixture.get("payload_shapes_stable") is True
        and fixture.get("hook_event_name") == "Stop"
        and fixture.get("shared_nonce_match") is True
        and isinstance(required, dict)
        and set(required)
        == {"hook_event_name", "session_id", "turn_id", "cwd", "transcript_path"}
        and isinstance(transcript_stat, dict)
        and all(
            isinstance(value, dict) and value.get("valid") is True
            for value in required.values()
        )
        and all(
            transcript_stat.get(key) is True
            for key in (
                "present",
                "regular",
                "owned_by_current_user",
                "size_positive",
                "has_mtime_ns",
                "has_device",
                "has_inode",
            )
        )
    )


def transcript_fixture_passes(
    fixture: dict[str, object],
    expected_surface: str,
) -> bool:
    occurrences = fixture.get("turn_occurrence_counts")
    turn_paths = fixture.get("turn_id_pointer_paths")
    provenance_paths = fixture.get("provenance_pointer_paths")
    provenance_values = fixture.get("provenance_values")
    return (
        fixture.get("surface") == expected_surface
        and fixture.get("observation_count") == 2
        and fixture.get("supported") is True
        and fixture.get("layouts_stable") is True
        and fixture.get("format") == "jsonl"
        and fixture.get("read_past_boundary") is False
        and isinstance(occurrences, list)
        and len(occurrences) == 2
        and all(isinstance(value, int) and value > 0 for value in occurrences)
        and fixture.get("turn_record_spans_contiguous") is True
        and isinstance(turn_paths, list)
        and bool(turn_paths)
        and all(isinstance(value, str) for value in turn_paths)
        and isinstance(provenance_paths, list)
        and bool(provenance_paths)
        and all(isinstance(value, str) for value in provenance_paths)
        and isinstance(provenance_values, list)
        and all(isinstance(value, str) for value in provenance_values)
        and {"user", "assistant"}.issubset(set(provenance_values))
    )


def access_fixture_passes(
    fixture: dict[str, object],
    expected_surface: str,
) -> bool:
    return (
        fixture.get("surface") == expected_surface
        and fixture.get("read") is True
        and fixture.get("write") is True
    )
```

- [ ] **Step 4: Add cross-surface structural differences**

Add:

```python
def structural_differences(
    cli_stop: dict[str, object],
    desktop_stop: dict[str, object],
    cli_transcript: dict[str, object],
    desktop_transcript: dict[str, object],
) -> dict[str, object]:
    def only(left: object, right: object) -> list[object]:
        left_values = set(left) if isinstance(left, list) else set()
        right_values = set(right) if isinstance(right, list) else set()
        return sorted(left_values - right_values)

    return {
        "stop_keys_only_cli": only(
            cli_stop.get("payload_keys"),
            desktop_stop.get("payload_keys"),
        ),
        "stop_keys_only_desktop": only(
            desktop_stop.get("payload_keys"),
            cli_stop.get("payload_keys"),
        ),
        "turn_paths_only_cli": only(
            cli_transcript.get("turn_id_pointer_paths"),
            desktop_transcript.get("turn_id_pointer_paths"),
        ),
        "turn_paths_only_desktop": only(
            desktop_transcript.get("turn_id_pointer_paths"),
            cli_transcript.get("turn_id_pointer_paths"),
        ),
        "provenance_paths_only_cli": only(
            cli_transcript.get("provenance_pointer_paths"),
            desktop_transcript.get("provenance_pointer_paths"),
        ),
        "provenance_paths_only_desktop": only(
            desktop_transcript.get("provenance_pointer_paths"),
            cli_transcript.get("provenance_pointer_paths"),
        ),
    }
```

- [ ] **Step 5: Add the final gate evaluator**

Add:

```python
def evaluate_feasibility_gate(
    cli_stop: dict[str, object],
    desktop_stop: dict[str, object],
    cli_transcript: dict[str, object],
    desktop_transcript: dict[str, object],
    cli_access: dict[str, object],
    desktop_access: dict[str, object],
) -> dict[str, object]:
    checks = {
        "cli_stop_contract": stop_fixture_passes(cli_stop, "cli"),
        "desktop_stop_contract": stop_fixture_passes(desktop_stop, "desktop"),
        "cli_shared_data_root": cli_stop.get("shared_nonce_match") is True,
        "desktop_shared_data_root": desktop_stop.get("shared_nonce_match") is True,
        "cli_skill_data_root": access_fixture_passes(cli_access, "cli"),
        "desktop_skill_data_root": access_fixture_passes(desktop_access, "desktop"),
        "cli_transcript_supported": transcript_fixture_passes(
            cli_transcript,
            "cli",
        ),
        "desktop_transcript_supported": transcript_fixture_passes(
            desktop_transcript,
            "desktop",
        ),
    }
    decision = "PASS" if all(checks.values()) else "FAIL"
    return {
        "schema_version": 1,
        "decision": decision,
        "checks": checks,
        "schema_differences": structural_differences(
            cli_stop,
            desktop_stop,
            cli_transcript,
            desktop_transcript,
        ),
        "surfaces": {
            "cli": {
                "stop_keys": cli_stop.get("payload_keys", []),
                "turn_id_pointer_paths": cli_transcript.get("turn_id_pointer_paths", []),
                "provenance_pointer_paths": cli_transcript.get(
                    "provenance_pointer_paths", []
                ),
            },
            "desktop": {
                "stop_keys": desktop_stop.get("payload_keys", []),
                "turn_id_pointer_paths": desktop_transcript.get(
                    "turn_id_pointer_paths", []
                ),
                "provenance_pointer_paths": desktop_transcript.get(
                    "provenance_pointer_paths", []
                ),
            },
        },
        "next_action": (
            "write_read_only_mvp_plan"
            if decision == "PASS"
            else "amend_design_for_session_level_queue"
        ),
    }
```

- [ ] **Step 6: Implement human-readable report rendering**

Add:

```python
def render_gate_markdown(report: dict[str, object]) -> str:
    lines = [
        "# Skill Evolver Feasibility Report",
        "",
        f"Decision: **{report['decision']}**",
        "",
        "The gate checks Codex CLI and Desktop against the same bounded Stop and transcript contract.",
        "",
        "## Checks",
        "",
    ]
    for name, passed in report["checks"].items():
        lines.append(f"- [{'x' if passed else ' '}] `{name}`")
    lines.extend(["", "## CLI/Desktop schema differences", ""])
    for name, values in report["schema_differences"].items():
        lines.append(
            f"- `{name}`: `{json.dumps(values, ensure_ascii=False, sort_keys=True)}`"
        )
    lines.extend(
        [
            "",
            "## Next action",
            "",
            (
                "Write the Read-only MVP plan using the recorded JSON-pointer paths."
                if report["decision"] == "PASS"
                else "Stop implementation and amend the design to use a session-level queue."
            ),
            "",
        ]
    )
    return "\n".join(lines)
```

- [ ] **Step 7: Implement atomic report writes and the gate command**

Add:

```python
def atomic_write_text(path: Path, value: str, mode: int = 0o644) -> None:
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    descriptor, temporary = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    temporary_path = Path(temporary)
    try:
        os.fchmod(descriptor, mode)
        with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
            stream.write(value)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary_path, path)
        os.chmod(path, mode)
        fsync_directory(path.parent)
    except BaseException:
        temporary_path.unlink(missing_ok=True)
        raise


def write_gate_report(
    fixture_root: Path,
    output_json: Path,
    output_markdown: Path,
) -> dict[str, object]:
    def load(name: str) -> dict[str, object]:
        value = json.loads((fixture_root / name).read_text(encoding="utf-8"))
        if not isinstance(value, dict):
            raise ValueError("gate_fixture_not_object")
        return value

    try:
        report = evaluate_feasibility_gate(
            load("stop-cli.structure.json"),
            load("stop-desktop.structure.json"),
            load("transcript-cli.structure.json"),
            load("transcript-desktop.structure.json"),
            load("access-cli.structure.json"),
            load("access-desktop.structure.json"),
        )
    except (KeyError, OSError, TypeError, ValueError, json.JSONDecodeError):
        report = {
            "schema_version": 1,
            "decision": "FAIL",
            "checks": {"gate_inputs_valid": False},
            "schema_differences": {},
            "surfaces": {},
            "next_action": "amend_design_for_session_level_queue",
        }
    atomic_write_json(output_json.resolve(), report)
    markdown = render_gate_markdown(report)
    atomic_write_text(output_markdown.resolve(), markdown)
    return report


def cmd_probe_gate(args: argparse.Namespace) -> int:
    report = write_gate_report(
        Path(args.fixture_root),
        Path(args.output_json),
        Path(args.output_markdown),
    )
    write_json_stdout(report)
    return 0 if report["decision"] == "PASS" else 2
```

Add this parser inside `build_parser()`:

```python
    probe_gate = subparsers.add_parser("probe-gate")
    probe_gate.add_argument("--fixture-root", required=True)
    probe_gate.add_argument("--output-json", required=True)
    probe_gate.add_argument("--output-markdown", required=True)
    probe_gate.set_defaults(handler=cmd_probe_gate)
```

- [ ] **Step 8: Implement bounded raw-observation cleanup**

Add:

```python
def scrub_probe_raw(
    installation: Installation,
    confirmation: str,
) -> dict[str, int]:
    if confirmation != "DELETE-FEASIBILITY-RAW":
        raise ValueError("confirmation_mismatch")
    observations = observation_paths(installation)
    reports = installation.data_root / "reports"
    ephemeral_reports = sorted(
        {
            path
            for pattern in (
                "*-observation.json",
                "*-boundary.json",
                "*-skill-challenge.json",
                "*-skill-response.json",
            )
            for path in reports.glob(pattern)
        }
    )
    for path in observations + ephemeral_reports:
        if path.is_symlink() or path.parent.is_symlink():
            raise ValueError("scrub_symlink")
    for path in observations:
        path.unlink()
    for path in ephemeral_reports:
        path.unlink()
    fsync_directory(installation.data_root / "incoming")
    fsync_directory(reports)
    return {
        "observations_deleted": len(observations),
        "ephemeral_reports_deleted": len(ephemeral_reports),
    }


def cmd_probe_scrub(args: argparse.Namespace) -> int:
    if not sys.stdin.isatty():
        raise ValueError("tty_required")
    typed = input("Type DELETE-FEASIBILITY-RAW to delete private raw observations: ")
    installation = load_installation(Path(args.installation))
    result = scrub_probe_raw(installation, typed)
    write_json_stdout(result)
    return 0
```

Add this parser inside `build_parser()`:

```python
    probe_scrub = subparsers.add_parser("probe-scrub")
    probe_scrub.add_argument("--installation", required=True)
    probe_scrub.set_defaults(handler=cmd_probe_scrub)
```

- [ ] **Step 9: Run all deterministic tests**

Run:

```bash
/usr/bin/python3 -m unittest discover \
  -s skill-evolver/skills/skill-evolver/tests \
  -p 'test_*.py' -v
```

Expected: all tests PASS.

- [ ] **Step 10: Generate the final gate reports**

Run:

```bash
/usr/bin/python3 -I \
  skill-evolver/skills/skill-evolver/scripts/evolver.py \
  probe-gate \
  --fixture-root /Users/igyeongseob/Documents/오픈소스/skill-evolver/skills/skill-evolver/tests/fixtures \
  --output-json /Users/igyeongseob/Documents/오픈소스/skill-evolver/docs/feasibility-report.json \
  --output-markdown /Users/igyeongseob/Documents/오픈소스/skill-evolver/docs/feasibility-report.md
```

Expected on success: exit code `0`; both reports say `PASS`; `next_action` is `write_read_only_mvp_plan`.

Expected on failure: exit code `2`; both reports say `FAIL`; `next_action` is `amend_design_for_session_level_queue`. Do not write or execute the Read-only MVP plan on this branch.

- [ ] **Step 11: Verify the reports and fixtures contain no private content**

Run:

```bash
rg -n \
  '/Users/|session-secret|turn-secret|target prompt|tool result|probe-(cli|desktop)-(one|two)-complete|Authorization|Bearer|PRIVATE KEY' \
  skill-evolver/docs/feasibility-report.* \
  skill-evolver/skills/skill-evolver/tests/fixtures/*.structure.json
```

Expected: no matches and exit code `1`.

- [ ] **Step 12: Remove the installed probe before deleting raw observations**

Run:

```bash
codex plugin remove skill-evolver@skill-evolver-dev --json
codex plugin marketplace remove skill-evolver-dev --json
```

Expected: both commands return successful JSON. Before starting another task, run `probe-status` with the command below. Then complete one ordinary Codex turn and run the same command again:

```bash
/usr/bin/python3 -I \
  skill-evolver/skills/skill-evolver/scripts/evolver.py \
  probe-status \
  --installation /Users/igyeongseob/.codex/skill-evolver-feasibility/installation.json
```

Expected: the second JSON has exactly the same `observation_count` and `latest_observation` as the first JSON.

- [ ] **Step 13: Inventory and delete private raw observations from a user-controlled TTY**

Before deleting anything, inventory the exact targets:

```bash
/usr/bin/find \
  /Users/igyeongseob/.codex/skill-evolver-feasibility/incoming \
  -type f \( \
    -name '*.json' -o \
    -name '.*.json.*' \
  \) | /usr/bin/wc -l
/usr/bin/find \
  /Users/igyeongseob/.codex/skill-evolver-feasibility/reports \
  -type f \( \
    -name '*-observation.json' -o \
    -name '.*-observation.json.*' -o \
    -name '*-boundary.json' -o \
    -name '.*-boundary.json.*' -o \
    -name '*-skill-challenge.json' -o \
    -name '.*-skill-challenge.json.*' -o \
    -name '*-skill-response.json' -o \
    -name '.*-skill-response.json.*' \
  \) | /usr/bin/wc -l
```

Expected: record the first number as the observation inventory and the second as
the ephemeral-report inventory. Both inventories include interrupted
`atomic_write_json` temporary files. Either number may be `0` after a legitimate
feasibility failure.

Then run in a terminal outside the agent:

```bash
/usr/bin/python3 -I \
  /Users/igyeongseob/Documents/오픈소스/skill-evolver/skills/skill-evolver/scripts/evolver.py \
  probe-scrub \
  --installation /Users/igyeongseob/.codex/skill-evolver-feasibility/installation.json
```

At the prompt, type exactly:

```text
DELETE-FEASIBILITY-RAW
```

Expected: `observations_deleted` exactly equals the recorded observation
inventory, and `ephemeral_reports_deleted` exactly equals the recorded
ephemeral-report inventory. Do not use a fixed minimum: failed Hook delivery or
skill preflight can legitimately leave either inventory empty.

Run:

```bash
/usr/bin/python3 -I \
  skill-evolver/skills/skill-evolver/scripts/evolver.py \
  probe-status \
  --installation /Users/igyeongseob/.codex/skill-evolver-feasibility/installation.json
```

Expected: `"observation_count": 0`.

Run the ephemeral-report inventory command above once more.

Expected: it prints `0`.

- [ ] **Step 14: Update the probe skill with the final gate boundary**

Append this section to `skill-evolver/skills/skill-evolver/SKILL.md`:

```markdown
## Gate boundary

A `PASS` report authorizes writing a separate Read-only MVP implementation plan.
It does not authorize SQLite queue implementation, model review, skill mutation,
evaluation, apply, or undo.

A `FAIL` report requires a design amendment for session-level capture. Do not
guess transcript fields or broaden filesystem access to force a pass.
```

- [ ] **Step 15: Run final verification**

Run:

```bash
/usr/bin/python3 -m unittest discover \
  -s skill-evolver/skills/skill-evolver/tests \
  -p 'test_*.py' -v
/usr/bin/python3 -m json.tool \
  skill-evolver/docs/feasibility-report.json >/dev/null
/usr/bin/python3 -m json.tool \
  .agents/plugins/marketplace.json >/dev/null
/usr/bin/python3 -m json.tool \
  skill-evolver/.codex-plugin/plugin.json >/dev/null
/usr/bin/python3 -m json.tool \
  skill-evolver/hooks/hooks.json >/dev/null
/bin/sh -c '
for fixture in skill-evolver/skills/skill-evolver/tests/fixtures/*.structure.json; do
  /usr/bin/python3 -m json.tool "$fixture" >/dev/null || exit 1
done
'
find skill-evolver/skills/skill-evolver/tests/fixtures \
  -name '*.structure.json' | wc -l
```

Expected: all unit tests PASS, the final command prints `6`, and every JSON validation exits `0`.

- [ ] **Step 16: Commit the gate report and cleanup implementation**

```bash
git add \
  skill-evolver/skills/skill-evolver/scripts/evolver.py \
  skill-evolver/skills/skill-evolver/SKILL.md \
  skill-evolver/skills/skill-evolver/tests/test_gate.py \
  skill-evolver/docs/feasibility-report.json \
  skill-evolver/docs/feasibility-report.md
git commit -m "test: record skill evolver feasibility gate"
```

Expected: the final commit contains the sanitized decision and no raw observations.

## Completion Criteria

### Spike completion — applies to PASS and FAIL

- CLI and Desktop were each attempted with the exact reviewed local Hook and actual `$skill-evolver` process; unsupported behavior was preserved as a sanitized failure fixture rather than guessed around.
- All six surface-bound structural fixture files exist, contain no prompt, response, transcript, token, private path, or raw identifier, and parse as JSON.
- `probe-gate` generated one deterministic JSON report, matching Markdown report, and exit code: `0` for PASS or `2` for FAIL.
- Any installed probe and local marketplace were removed, and a subsequent ordinary turn created no new observation.
- Pre-scrub inventory counts exactly matched the deletion result. Raw observations and private mappings, boundaries, challenges, and responses are absent afterward, even when the original counts were zero.
- The final commit contains only implementation, tests, sanitized fixtures, and reports; it contains no private probe data.

### Additional PASS gate criteria

- Both surfaces produced exactly two real Stop observations with stable payload shapes, distinct `(session_id, turn_id)` pairs, and non-empty `session_id`, `turn_id`, `cwd`, and usable `transcript_path` fields.
- Both surfaces' Hook observations carried the same private installation nonce, and both actual `$skill-evolver` processes read and wrote their fresh challenge under default `workspace-write` without escalation.
- Both transcript reports inspected two Hook-captured byte boundaries, reported `read_past_boundary: false`, and exposed identical per-surface turn-ID and provenance JSON-pointer layouts across the two observations.
- The JSON and Markdown reports list CLI-only and Desktop-only Stop keys, turn paths, and provenance paths.
- `next_action` is `write_read_only_mvp_plan`; only then create the separate Read-only MVP plan.

### FAIL outcome criteria

- At least one named gate check is false, and its sanitized fixture or gate input error records why without exposing private content.
- `next_action` is `amend_design_for_session_level_queue`.
- Do not write or execute the Read-only MVP plan. Amend the design around session-level capture before any queue, review, evaluation, apply, or undo work.
