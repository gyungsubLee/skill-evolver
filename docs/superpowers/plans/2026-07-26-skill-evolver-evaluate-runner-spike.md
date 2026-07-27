# Skill Evolver Evaluate Runner Spike Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Prove that the pinned Codex CLI can evaluate an explicitly supplied synthetic skill under a fixed JSON, sandbox, network, credential, and external resource-supervisor contract before Skill Evolver enables `evaluate`.

**Architecture:** Extend the self-contained `evolver.py` with a fail-closed runner probe that consumes the Read-only quality PASS report, launches one exact Codex CLI version twice against repository-owned synthetic fixtures, validates structured output and event JSONL, and records a canonical private runner contract. A Python standard-library supervisor enforces wall-clock, descendant-process, resident-memory, and output-disk ceilings outside Codex; any version drift, schema error, tool activity, limit breach, crash, or missing quality gate produces a durable FAIL report and leaves `evaluate` disabled.

**Tech Stack:** macOS, Codex CLI `0.145.0`, model `gpt-5.6-sol`, `/usr/bin/python3` 3.9+, Python standard library (`argparse`, `dataclasses`, `hashlib`, `json`, `os`, `pathlib`, `selectors`, `signal`, `subprocess`, `tempfile`, `time`, `unittest`), Codex `exec --ephemeral --sandbox read-only --output-schema --json --output-last-message`.

## Global Constraints

- Source specification: `skill-evolver/docs/superpowers/specs/2026-07-26-skill-evolver-design.md`.
- This plan starts only after `skill-evolver/docs/release-reports/read-only-quality-gate.json` exists and its decision is `PASS`.
- The Read-only quality gate must report at least 10 reviewed sessions or 30 reviewed turns, evaluation-worth rate at least `0.50`, target-skill misattribution at most `0.20`, and exactly `0` external-content adoption incidents.
- The runner binary selected by this spike is exactly `/opt/homebrew/Caskroom/codex/0.145.0/codex-aarch64-apple-darwin`; its reported version must be exactly `codex-cli 0.145.0` or `codex-cli-exec 0.145.0`.
- The pinned model is exactly `gpt-5.6-sol`; reasoning effort is exactly `low`.
- Runtime interpreter is exactly `/usr/bin/python3`; minimum version is 3.9.
- Plugin runtime code and test helpers use the Python standard library only.
- `evolver.py` remains one self-contained runtime entrypoint because `python -I` excludes sibling imports.
- The runner uses `--ignore-user-config`, `--ignore-rules`, `--strict-config`, `--ephemeral`, `--sandbox read-only`, `--skip-git-repo-check`, `--json`, `--output-schema`, and `--output-last-message`.
- The runner receives a synthetic text-only skill and case as explicit prompt data; it does not use the installed skill catalog and never executes candidate scripts.
- The child environment contains no variable whose name ends in `_TOKEN`, `_SECRET`, `_PASSWORD`, or `_KEY`, and contains none of `OPENAI_API_KEY`, `CODEX_API_KEY`, `AWS_ACCESS_KEY_ID`, `AWS_SECRET_ACCESS_KEY`, or `GITHUB_TOKEN`.
- Codex control-plane communication is the only network exception. The evaluated skill cannot call tools, candidate scripts, web search, MCP, or other network clients; any corresponding runner event is a failed spike.
- Each invocation is capped at 120 wall-clock seconds, 16 descendant processes, 1 GiB aggregate resident memory, 16 MiB writable output, and 1 MiB serialized explicit input.
- Prompt bytes are fed through nonblocking stdin under that same wall-clock
  deadline. A child that never reads stdin is killed with its process group
  and the group leader is reaped; prompt delivery is never an unbounded
  synchronous prelude to supervision.
- Raw runner JSONL and model output stay under the private data root, mode `0600`; the repository receives only synthetic fixtures and a sanitized release report.
- A PASS requires two independent invocations with the same fixed input and exact expected response.
- The canonical private contract is `<data_root>/reports/runner/runner-contract.json`; prepare must verify its full SHA-256 digest and may not reconstruct it from current machine defaults.
- The private human report is `<data_root>/reports/runner/runner-spike-report.md`.
- This spike does not create staging artifacts, migrate the database, create evaluations, evaluate a real candidate, or modify any installed skill.
- The workspace contains unrelated `n8n/` and `neo4j/` repositories. Stage only the exact Skill Evolver paths listed in each commit step; never run `git add .`.

## Release Boundary

This is a hard gate, not a compatibility claim for other Codex versions.

- `PASS` authorizes the separate `2026-07-26-skill-evolver-evaluate-prepare.md` plan.
- `FAIL` leaves `prepare` and `evaluate` unavailable. Fix or redesign the runner contract, create a new spike artifact, and obtain a new PASS before continuing.
- Installing a different Codex CLI version invalidates the recorded runner contract. Runtime must fail closed; it must not silently update the version field.

## File Structure

| Path | Responsibility |
| --- | --- |
| `skill-evolver/skills/skill-evolver/scripts/evolver.py` | Quality-gate validation, pinned runner command, external supervisor, output validation, contract/report generation, and `runner-spike` command. |
| `skill-evolver/skills/skill-evolver/SKILL.md` | Explicit-only `runner spike` intent and the PASS/FAIL boundary. |
| `skill-evolver/skills/skill-evolver/tests/test_runner_probe.py` | Deterministic quality, version, command, limits, schema, tool-event, and contract tests. |
| `skill-evolver/skills/skill-evolver/tests/fixtures/runner-spike/SKILL.md` | Synthetic text-only skill supplied explicitly to the runner. |
| `skill-evolver/skills/skill-evolver/tests/fixtures/runner-spike/case.json` | Fixed synthetic case and exact expected response. |
| `skill-evolver/skills/skill-evolver/tests/fixtures/runner-spike/model-output.schema.json` | JSON Schema passed to Codex `--output-schema`. |
| `skill-evolver/skills/skill-evolver/tests/fixtures/fake_codex_runner.py` | Standard-library fake binary used only by deterministic supervisor tests. |
| `skill-evolver/docs/release-reports/evaluate-runner-spike.json` | Sanitized committed copy of the actual runner decision and contract digest. |
| `<data_root>/reports/runner/runner-contract.json` | Canonical PASS or FAIL contract consumed by prepare. |
| `<data_root>/reports/runner/runner-spike-report.md` | Private human-readable spike report. |
| `<data_root>/reports/runner/runs/` | Private raw event JSONL, final-message JSON, stderr, and per-run metrics. |

---

### Task 1: Enforce the Read-only Quality and Pinned-Version Preconditions

**Files:**
- Modify: `skill-evolver/skills/skill-evolver/scripts/evolver.py`
- Create: `skill-evolver/skills/skill-evolver/tests/test_runner_probe.py`

**Interfaces:**
- Consumes: `Installation`, `load_installation(path: Path) -> Installation`, `atomic_write_json(path: Path, payload: dict[str, object]) -> None`, `canonical_json_bytes(value: object) -> bytes`, `sha256_json(value: object) -> str`, and `write_json_stdout(payload: dict[str, object]) -> None` from the Read-only MVP.
- Produces: `QualityGateEvidence`, `RunnerSelection`, `is_sha256(value: object) -> bool`, `load_quality_gate(path: Path) -> QualityGateEvidence`, and `verify_runner_version(binary: Path, expected: str) -> str`.

- [ ] **Step 1: Write failing precondition and version tests**

Create `skill-evolver/skills/skill-evolver/tests/test_runner_probe.py`:

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


def quality_report(**overrides: object) -> dict[str, object]:
    value: dict[str, object] = {
        "schema_version": 1,
        "release": "read-only-mvp",
        "generated_at": "2026-07-26T00:00:00Z",
        "decision": "PASS",
        "policy_digest": "1" * 64,
        "adapter_digest": "2" * 64,
        "sample": {
            "reviewed_sessions": 10,
            "reviewed_turns": 30,
            "completed_batches": 2,
            "candidate_count": 4,
            "labeled_candidate_count": 4,
        },
        "metrics": {
            "evaluation_worthy_candidates": 2,
            "evaluation_worth_rate": 0.50,
            "target_attribution_checks": 4,
            "target_misattributions": 0,
            "target_misattribution_rate": 0.0,
            "external_content_adoption_incidents": 0,
        },
        "thresholds": {
            "minimum_reviewed_sessions": 10,
            "minimum_reviewed_turns": 30,
            "evaluation_worth_rate_minimum": 0.5,
            "target_misattribution_rate_maximum": 0.2,
            "external_content_adoption_incidents_maximum": 0,
        },
        "checks": {
            "sample_size": True,
            "all_candidates_labeled": True,
            "evaluation_worth_rate": True,
            "target_misattribution_rate": True,
            "external_content_adoption": True,
            "policy_digest_recorded": True,
            "adapter_digest_recorded": True,
        },
        "next_action": "begin_evaluate_runner_spike",
        "source_batch_ids": [1, 2],
    }
    for key, child in overrides.items():
        if key in value["sample"]:
            value["sample"][key] = child
        elif key in value["metrics"]:
            value["metrics"][key] = child
        else:
            value[key] = child
    return value


class RunnerPreconditionTests(unittest.TestCase):
    def setUp(self) -> None:
        self.runtime = load_runtime()
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.report_path = self.root / "read-only-quality-gate.json"

    def write_report(self, value: object) -> None:
        self.report_path.write_text(
            json.dumps(value, sort_keys=True),
            encoding="utf-8",
        )

    def test_quality_pass_returns_pinned_upstream_digests(self) -> None:
        self.write_report(quality_report())
        result = self.runtime.load_quality_gate(self.report_path)
        self.assertEqual(result.policy_digest, "1" * 64)
        self.assertEqual(result.adapter_digest, "2" * 64)
        self.assertEqual(result.report_digest, self.runtime.sha256_json(quality_report()))

    def test_quality_gate_rejects_each_release_threshold(self) -> None:
        failures = (
            {"decision": "FAIL"},
            {"reviewed_sessions": 9, "reviewed_turns": 29},
            {"evaluation_worth_rate": 0.49},
            {"target_misattribution_rate": 0.21},
            {"external_content_adoption_incidents": 1},
            {"policy_digest": "short"},
            {"adapter_digest": "short"},
        )
        for change in failures:
            with self.subTest(change=change):
                self.write_report(quality_report(**change))
                with self.assertRaisesRegex(ValueError, "read_only_quality_gate_failed"):
                    self.runtime.load_quality_gate(self.report_path)

    def test_quality_gate_rejects_non_object_and_newer_schema(self) -> None:
        for value in ([], quality_report(schema_version=2)):
            with self.subTest(value=value):
                self.write_report(value)
                with self.assertRaises(ValueError):
                    self.runtime.load_quality_gate(self.report_path)

    def test_shared_json_digest_is_key_order_independent(self) -> None:
        left = {"b": [2, 1], "a": {"value": True}}
        right = {"a": {"value": True}, "b": [2, 1]}
        self.assertEqual(
            self.runtime.sha256_json(left),
            self.runtime.sha256_json(right),
        )

    def test_runner_version_accepts_only_exact_01450(self) -> None:
        binary = self.root / "codex"
        binary.write_text("#!/bin/sh\nprintf 'codex-cli 0.145.0\\n'\n", encoding="utf-8")
        binary.chmod(0o700)
        self.assertEqual(
            self.runtime.verify_runner_version(binary, "0.145.0"),
            "codex-cli 0.145.0",
        )
        binary.write_text("#!/bin/sh\nprintf 'codex-cli 0.146.0\\n'\n", encoding="utf-8")
        binary.chmod(0o700)
        with self.assertRaisesRegex(ValueError, "runner_version_mismatch"):
            self.runtime.verify_runner_version(binary, "0.145.0")

    def test_runner_binary_must_be_owned_regular_non_writable_file(self) -> None:
        binary = self.root / "codex"
        binary.write_text("#!/bin/sh\nprintf 'codex-cli 0.145.0\\n'\n", encoding="utf-8")
        binary.chmod(0o722)
        with self.assertRaisesRegex(ValueError, "runner_binary_permissions"):
            self.runtime.verify_runner_version(binary, "0.145.0")


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: Run the focused tests and verify the failure**

Run:

```bash
/usr/bin/python3 -m unittest \
  skill-evolver/skills/skill-evolver/tests/test_runner_probe.py \
  -v
```

Expected: FAIL because `load_quality_gate()` and `verify_runner_version()` are undefined.

- [ ] **Step 3: Add immutable precondition types and digest validation**

Add these imports to `skill-evolver/skills/skill-evolver/scripts/evolver.py`:

```python
import hashlib
import subprocess
from dataclasses import dataclass
```

Add these constants and types below the existing runtime constants:

```python
RUNNER_CONTRACT_SCHEMA = 1
RUNNER_RESULT_SCHEMA = 1
PINNED_RUNNER_VERSION = "0.145.0"
PINNED_RUNNER_BINARY = Path(
    "/opt/homebrew/Caskroom/codex/0.145.0/codex-aarch64-apple-darwin"
)
PINNED_MODEL_ID = "gpt-5.6-sol"
PINNED_REASONING_EFFORT = "low"
SHA256_LENGTH = 64


@dataclass(frozen=True)
class QualityGateEvidence:
    report_digest: str
    policy_digest: str
    adapter_digest: str
    reviewed_sessions: int
    reviewed_turns: int


@dataclass(frozen=True)
class RunnerSelection:
    binary: Path
    version: str
    model_id: str
    reasoning_effort: str
```

Add the canonical helpers:

```python
def is_sha256(value: object) -> bool:
    return (
        isinstance(value, str)
        and len(value) == SHA256_LENGTH
        and all(character in "0123456789abcdef" for character in value)
    )
```

- [ ] **Step 4: Implement the exact Read-only quality gate**

Add:

```python
def load_quality_gate(path: Path) -> QualityGateEvidence:
    if path.is_symlink():
        raise ValueError("quality_gate_symlink")
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as error:
        raise ValueError("read_only_quality_gate_unavailable") from error
    if not isinstance(value, dict) or value.get("schema_version") != 1:
        raise ValueError("read_only_quality_gate_schema")

    sample = value.get("sample")
    metrics = value.get("metrics")
    thresholds = value.get("thresholds")
    checks = value.get("checks")
    if not all(isinstance(item, dict) for item in (sample, metrics, thresholds, checks)):
        raise ValueError("read_only_quality_gate_schema")
    reviewed_sessions = sample.get("reviewed_sessions")
    reviewed_turns = sample.get("reviewed_turns")
    evaluation_worth_rate = metrics.get("evaluation_worth_rate")
    target_misattribution_rate = metrics.get("target_misattribution_rate")
    external_incidents = metrics.get("external_content_adoption_incidents")
    passed = (
        value.get("decision") == "PASS"
        and value.get("release") == "read-only-mvp"
        and value.get("next_action") == "begin_evaluate_runner_spike"
        and isinstance(reviewed_sessions, int)
        and isinstance(reviewed_turns, int)
        and (reviewed_sessions >= 10 or reviewed_turns >= 30)
        and isinstance(evaluation_worth_rate, (int, float))
        and not isinstance(evaluation_worth_rate, bool)
        and float(evaluation_worth_rate) >= 0.50
        and isinstance(target_misattribution_rate, (int, float))
        and not isinstance(target_misattribution_rate, bool)
        and float(target_misattribution_rate) <= 0.20
        and external_incidents == 0
        and thresholds == {
            "minimum_reviewed_sessions": 10,
            "minimum_reviewed_turns": 30,
            "evaluation_worth_rate_minimum": 0.5,
            "target_misattribution_rate_maximum": 0.2,
            "external_content_adoption_incidents_maximum": 0,
        }
        and all(checks.get(key) is True for key in (
            "sample_size",
            "all_candidates_labeled",
            "evaluation_worth_rate",
            "target_misattribution_rate",
            "external_content_adoption",
            "policy_digest_recorded",
            "adapter_digest_recorded",
        ))
        and is_sha256(value.get("policy_digest"))
        and is_sha256(value.get("adapter_digest"))
    )
    if not passed:
        raise ValueError("read_only_quality_gate_failed")
    return QualityGateEvidence(
        report_digest=sha256_json(value),
        policy_digest=str(value["policy_digest"]),
        adapter_digest=str(value["adapter_digest"]),
        reviewed_sessions=reviewed_sessions,
        reviewed_turns=reviewed_turns,
    )
```

- [ ] **Step 5: Implement exact runner binary and version validation**

Add:

```python
def verify_runner_version(binary: Path, expected: str) -> str:
    if binary.is_symlink():
        raise ValueError("runner_binary_symlink")
    canonical = binary.resolve(strict=True)
    info = canonical.stat()
    if (
        not stat.S_ISREG(info.st_mode)
        or info.st_uid not in {0, os.getuid()}
        or stat.S_IMODE(info.st_mode) & 0o022
    ):
        raise ValueError("runner_binary_permissions")
    completed = subprocess.run(
        [str(canonical), "--version"],
        stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=False,
        timeout=10,
        env={
            "HOME": str(Path.home()),
            "PATH": "/usr/bin:/bin",
            "LANG": "C.UTF-8",
        },
    )
    observed = completed.stdout.decode("utf-8", "strict").strip()
    allowed = {f"codex-cli {expected}", f"codex-cli-exec {expected}"}
    if completed.returncode != 0 or observed not in allowed:
        raise ValueError("runner_version_mismatch")
    return observed
```

- [ ] **Step 6: Run the focused tests**

Run:

```bash
/usr/bin/python3 -m unittest \
  skill-evolver/skills/skill-evolver/tests/test_runner_probe.py \
  -v
```

Expected: 6 tests PASS.

- [ ] **Step 7: Commit the hard preconditions**

```bash
git add \
  skill-evolver/skills/skill-evolver/scripts/evolver.py \
  skill-evolver/skills/skill-evolver/tests/test_runner_probe.py
git commit -m "test: gate evaluate runner prerequisites"
```

Expected: only the runtime and focused runner test are staged.

---

### Task 2: Build the External Resource Supervisor and Fixed Runner Invocation

**Files:**
- Modify: `skill-evolver/skills/skill-evolver/scripts/evolver.py`
- Modify: `skill-evolver/skills/skill-evolver/tests/test_runner_probe.py`
- Create: `skill-evolver/skills/skill-evolver/tests/fixtures/fake_codex_runner.py`

**Interfaces:**
- Consumes: `RunnerSelection`, shared `canonical_json_bytes()`/`sha256_json()`, and digest validation from Task 1.
- Produces: `ResourcePolicy`, `ProcessResult`,
  `sanitized_runner_environment(...)`, `build_runner_argv(...)`,
  `process_group_metrics(group_id)`, `kill_and_reap_process_group(...)`,
  nonblocking `run_supervised(...) -> ProcessResult`,
  `load_model_result(path)`, and `load_event_types(path)`.

- [ ] **Step 1: Add deterministic supervisor tests**

Append these imports to `test_runner_probe.py`:

```python
import sys
import time
```

Append this test class before the module's `if __name__ == "__main__"` block:

```python
class RunnerSupervisorTests(unittest.TestCase):
    def setUp(self) -> None:
        self.runtime = load_runtime()
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.fake = (
            Path(__file__).resolve().parent
            / "fixtures"
            / "fake_codex_runner.py"
        )

    def run_fake(self, mode: str, prompt: bytes = b"") -> object:
        output = self.root / mode
        output.mkdir()
        policy = self.runtime.ResourcePolicy(
            wall_seconds=2,
            max_processes=4,
            max_rss_bytes=64 * 1024 * 1024,
            max_output_bytes=1024 * 1024,
            max_input_bytes=1024 * 1024,
        )
        return self.runtime.run_supervised(
            ["/usr/bin/python3", str(self.fake), mode, str(output)],
            output,
            self.runtime.sanitized_runner_environment(
                self.root / "home",
                self.root / "tmp",
            ),
            policy,
            prompt,
        )

    def test_environment_exposes_no_credential_names(self) -> None:
        with mock.patch.dict(
            os.environ,
            {
                "OPENAI_API_KEY": "secret",
                "EXAMPLE_TOKEN": "secret",
                "SAFE_VALUE": "also-not-inherited",
            },
            clear=True,
        ):
            value = self.runtime.sanitized_runner_environment(
                self.root / "home",
                self.root / "tmp",
            )
        self.assertEqual(
            set(value),
            {"CODEX_HOME", "HOME", "LANG", "LC_ALL", "PATH", "TMPDIR"},
        )
        self.assertNotIn("secret", json.dumps(value))

    def test_success_is_measured_without_limit_breach(self) -> None:
        result = self.run_fake("success")
        self.assertEqual(result.returncode, 0)
        self.assertIsNone(result.failure_code)
        self.assertGreaterEqual(result.peak_processes, 1)
        self.assertGreater(result.output_bytes, 0)

    def test_timeout_is_a_failure_not_a_pass(self) -> None:
        result = self.run_fake("timeout")
        self.assertEqual(result.failure_code, "wall_clock_limit")
        self.assertNotEqual(result.returncode, 0)

    def test_child_that_never_reads_stdin_cannot_block_the_deadline(self) -> None:
        started = time.monotonic()
        result = self.run_fake("stdin-block", b"x" * (512 * 1024))
        self.assertEqual(result.failure_code, "wall_clock_limit")
        self.assertLess(time.monotonic() - started, 5)

    def test_child_cannot_outlive_a_successfully_exited_runner(self) -> None:
        result = self.run_fake("orphan")
        self.assertEqual(result.failure_code, "runner_orphan_process")
        group_id = int(
            (self.root / "orphan" / "runner.pid").read_text(encoding="ascii")
        )
        deadline = time.monotonic() + 2
        while (
            self.runtime.process_group_metrics(group_id)[0] != 0
            and time.monotonic() < deadline
        ):
            time.sleep(0.02)
        self.assertEqual(self.runtime.process_group_metrics(group_id)[0], 0)

    def test_output_limit_is_a_failure_not_a_pass(self) -> None:
        output = self.root / "disk"
        output.mkdir()
        policy = self.runtime.ResourcePolicy(
            wall_seconds=2,
            max_processes=4,
            max_rss_bytes=64 * 1024 * 1024,
            max_output_bytes=1024,
            max_input_bytes=1024 * 1024,
        )
        result = self.runtime.run_supervised(
            ["/usr/bin/python3", str(self.fake), "disk", str(output)],
            output,
            self.runtime.sanitized_runner_environment(
                self.root / "home",
                self.root / "tmp",
            ),
            policy,
        )
        self.assertEqual(result.failure_code, "output_disk_limit")

    def test_model_result_and_event_schema_fail_closed(self) -> None:
        final = self.root / "final.json"
        final.write_text(
            '{"schema_version":1,"response_text":"runner-spike-ok"}\n',
            encoding="utf-8",
        )
        events = self.root / "events.jsonl"
        events.write_text(
            '{"type":"thread.started"}\n'
            '{"type":"item.completed","item":{"type":"agent_message"}}\n',
            encoding="utf-8",
        )
        self.assertEqual(
            self.runtime.load_model_result(final)["response_text"],
            "runner-spike-ok",
        )
        self.assertEqual(
            self.runtime.load_event_types(events),
            ("agent_message", "item.completed", "thread.started"),
        )
        events.write_text(
            '{"type":"item.completed","item":{"type":"command_execution"}}\n',
            encoding="utf-8",
        )
        with self.assertRaisesRegex(ValueError, "runner_tool_event"):
            self.runtime.load_event_types(events)

    def test_runner_argv_contains_the_complete_fixed_contract(self) -> None:
        selection = self.runtime.RunnerSelection(
            binary=Path("/fixed/codex"),
            version="0.145.0",
            model_id="gpt-5.6-sol",
            reasoning_effort="low",
        )
        value = self.runtime.build_runner_argv(
            selection,
            self.root / "workspace",
            self.root / "schema.json",
            self.root / "final.json",
        )
        self.assertEqual(value[0], "/fixed/codex")
        for flag in (
            "--ignore-user-config",
            "--ignore-rules",
            "--strict-config",
            "--ephemeral",
            "--sandbox",
            "read-only",
            "--skip-git-repo-check",
            "--json",
            "--output-schema",
            "--output-last-message",
            "--model",
            "gpt-5.6-sol",
        ):
            self.assertIn(flag, value)
        self.assertEqual(value[-1], "-")
```

- [ ] **Step 2: Create the fake runner**

Create `skill-evolver/skills/skill-evolver/tests/fixtures/fake_codex_runner.py`:

```python
#!/usr/bin/env python3
from __future__ import annotations

import json
import os
import subprocess
import sys
import time
from pathlib import Path


def write(path: Path, value: bytes) -> None:
    path.write_bytes(value)
    with path.open("rb") as stream:
        os.fsync(stream.fileno())


def main() -> int:
    mode = sys.argv[1]
    output = Path(sys.argv[2])
    if mode == "stdin-block":
        time.sleep(10)
        return 0
    if mode == "orphan":
        write(output / "runner.pid", str(os.getpid()).encode("ascii"))
        child = subprocess.Popen(
            [sys.executable, "-c", "import time; time.sleep(30)"]
        )
        write(output / "child.pid", str(child.pid).encode("ascii"))
        return 0
    if mode == "success":
        write(output / "events.jsonl", b'{"type":"thread.started"}\n')
        write(
            output / "final.json",
            b'{"schema_version":1,"response_text":"runner-spike-ok"}\n',
        )
        return 0
    if mode == "timeout":
        time.sleep(10)
        return 0
    if mode == "disk":
        with (output / "large.bin").open("wb") as stream:
            for _index in range(1024):
                stream.write(b"x" * 4096)
                stream.flush()
                time.sleep(0.001)
        return 0
    return 3


if __name__ == "__main__":
    raise SystemExit(main())
```

- [ ] **Step 3: Run the supervisor tests and verify the failure**

Run:

```bash
/usr/bin/python3 -m unittest \
  skill-evolver/skills/skill-evolver/tests/test_runner_probe.py \
  -v
```

Expected: FAIL because the supervisor types and functions are undefined.

- [ ] **Step 4: Add resource and result types**

Add these imports to `evolver.py`:

```python
import selectors
import signal
import time
from typing import Mapping
```

Add:

```python
DENIED_EVENT_TYPES = {
    "command_execution",
    "file_change",
    "mcp_tool_call",
    "tool_call",
    "web_search",
}


@dataclass(frozen=True)
class ResourcePolicy:
    wall_seconds: int
    max_processes: int
    max_rss_bytes: int
    max_output_bytes: int
    max_input_bytes: int

    def as_json(self) -> dict[str, int]:
        return {
            "wall_seconds": self.wall_seconds,
            "max_processes": self.max_processes,
            "max_rss_bytes": self.max_rss_bytes,
            "max_output_bytes": self.max_output_bytes,
            "max_input_bytes": self.max_input_bytes,
        }


@dataclass(frozen=True)
class ProcessResult:
    returncode: int
    failure_code: Optional[str]
    wall_milliseconds: int
    peak_processes: int
    peak_rss_bytes: int
    output_bytes: int
```

- [ ] **Step 5: Add the empty-credential environment and exact argv**

Add:

```python
def sanitized_runner_environment(
    temp_home: Path,
    temp_dir: Path,
) -> dict[str, str]:
    temp_home.mkdir(parents=True, exist_ok=True, mode=0o700)
    temp_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
    return {
        "CODEX_HOME": str(temp_home),
        "HOME": str(temp_home),
        "LANG": "C.UTF-8",
        "LC_ALL": "C.UTF-8",
        "PATH": "/usr/bin:/bin",
        "TMPDIR": str(temp_dir),
    }


def build_runner_argv(
    selection: RunnerSelection,
    workspace: Path,
    output_schema: Path,
    final_message: Path,
) -> list[str]:
    return [
        str(selection.binary),
        "exec",
        "--ignore-user-config",
        "--ignore-rules",
        "--strict-config",
        "--ephemeral",
        "--sandbox",
        "read-only",
        "--skip-git-repo-check",
        "--json",
        "--output-schema",
        str(output_schema),
        "--output-last-message",
        str(final_message),
        "--model",
        selection.model_id,
        "-c",
        f'model_reasoning_effort="{selection.reasoning_effort}"',
        "--cd",
        str(workspace),
        "-",
    ]
```

- [ ] **Step 6: Add descendant, memory, and disk measurements**

Add:

```python
def directory_bytes(root: Path) -> int:
    total = 0
    for directory, names, files in os.walk(root):
        names[:] = [
            name
            for name in names
            if not (Path(directory) / name).is_symlink()
        ]
        for name in files:
            path = Path(directory) / name
            if path.is_symlink():
                raise ValueError("runner_output_symlink")
            total += path.stat().st_size
    return total


def process_rows() -> list[tuple[int, int, int, int]]:
    completed = subprocess.run(
        ["/bin/ps", "-axo", "pid=,ppid=,pgid=,rss="],
        stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.DEVNULL,
        check=False,
        timeout=2,
        env={"PATH": "/usr/bin:/bin", "LANG": "C"},
    )
    rows: list[tuple[int, int, int, int]] = []
    for line in completed.stdout.decode("ascii", "ignore").splitlines():
        fields = line.split()
        if len(fields) == 4 and all(field.isdigit() for field in fields):
            rows.append(
                (
                    int(fields[0]), int(fields[1]), int(fields[2]),
                    int(fields[3]) * 1024,
                )
            )
    return rows


def descendant_metrics(root_pid: int) -> tuple[int, int]:
    rows = process_rows()
    selected = {root_pid}
    changed = True
    while changed:
        changed = False
        for pid, parent, _group, _rss in rows:
            if parent in selected and pid not in selected:
                selected.add(pid)
                changed = True
    rss = sum(
        value for pid, _parent, _group, value in rows if pid in selected
    )
    return len(selected), rss


def process_group_metrics(group_id: int) -> tuple[int, int]:
    members = [
        (pid, rss)
        for pid, _parent, group, rss in process_rows()
        if group == group_id
    ]
    return len(members), sum(rss for _pid, rss in members)
```

- [ ] **Step 7: Implement the external supervisor**

Add:

```python
def kill_and_reap_process_group(process: subprocess.Popen) -> int:
    try:
        os.killpg(process.pid, signal.SIGKILL)
    except ProcessLookupError:
        pass
    try:
        return process.wait(timeout=5)
    except subprocess.TimeoutExpired:
        process.kill()
        return process.wait(timeout=5)


def run_supervised(
    argv: list[str],
    output_root: Path,
    environment: Mapping[str, str],
    policy: ResourcePolicy,
    prompt: bytes = b"",
) -> ProcessResult:
    if len(prompt) > policy.max_input_bytes:
        raise ValueError("runner_input_limit")
    output_root.mkdir(parents=True, exist_ok=True, mode=0o700)
    stdout_path = output_root / "events.jsonl"
    stderr_path = output_root / "stderr.txt"
    started = time.monotonic()
    peak_processes = 0
    peak_rss_bytes = 0
    failure_code: Optional[str] = None
    with stdout_path.open("wb") as stdout, stderr_path.open("wb") as stderr:
        process = subprocess.Popen(
            argv,
            stdin=subprocess.PIPE,
            stdout=stdout,
            stderr=stderr,
            cwd=output_root,
            env=dict(environment),
            start_new_session=True,
            bufsize=0,
        )
        peak_processes = 1
        if process.stdin is None:
            kill_and_reap_process_group(process)
            raise RuntimeError("runner_stdin_unavailable")
        input_fd = process.stdin.fileno()
        os.set_blocking(input_fd, False)
        pending = memoryview(prompt)
        sent = 0
        selector = selectors.DefaultSelector()
        if pending:
            selector.register(input_fd, selectors.EVENT_WRITE)
        else:
            process.stdin.close()
        try:
            while process.poll() is None:
                for _key, _mask in selector.select(timeout=0.05):
                    try:
                        sent += os.write(
                            input_fd, pending[sent : sent + 64 * 1024]
                        )
                    except BlockingIOError:
                        pass
                    except BrokenPipeError:
                        failure_code = "runner_stdin_closed"
                    if failure_code is not None or sent == len(pending):
                        selector.unregister(input_fd)
                        process.stdin.close()
                wall = time.monotonic() - started
                count, rss = descendant_metrics(process.pid)
                output_bytes = directory_bytes(output_root)
                peak_processes = max(peak_processes, count)
                peak_rss_bytes = max(peak_rss_bytes, rss)
                if wall > policy.wall_seconds:
                    failure_code = "wall_clock_limit"
                elif count > policy.max_processes:
                    failure_code = "process_limit"
                elif rss > policy.max_rss_bytes:
                    failure_code = "memory_limit"
                elif output_bytes > policy.max_output_bytes:
                    failure_code = "output_disk_limit"
                if failure_code is not None:
                    kill_and_reap_process_group(process)
                    break
            if failure_code is None:
                remaining, remaining_rss = process_group_metrics(process.pid)
                peak_processes = max(peak_processes, remaining)
                peak_rss_bytes = max(peak_rss_bytes, remaining_rss)
                if remaining:
                    failure_code = "runner_orphan_process"
                    returncode = kill_and_reap_process_group(process)
                else:
                    returncode = process.wait(timeout=5)
            else:
                returncode = process.wait(timeout=5)
            if sent != len(pending) and failure_code is None:
                failure_code = "runner_stdin_closed"
        finally:
            selector.close()
            if not process.stdin.closed:
                process.stdin.close()
            if process.poll() is None:
                kill_and_reap_process_group(process)
        stdout.flush()
        stderr.flush()
        os.fsync(stdout.fileno())
        os.fsync(stderr.fileno())
    if failure_code is None and returncode != 0:
        failure_code = "runner_crash"
    output_bytes = directory_bytes(output_root)
    if failure_code is None and output_bytes > policy.max_output_bytes:
        failure_code = "output_disk_limit"
    wall_milliseconds = int((time.monotonic() - started) * 1000)
    return ProcessResult(
        returncode=returncode,
        failure_code=failure_code,
        wall_milliseconds=wall_milliseconds,
        peak_processes=peak_processes,
        peak_rss_bytes=peak_rss_bytes,
        output_bytes=output_bytes,
    )
```

- [ ] **Step 8: Add strict final-message and event validation**

Add:

```python
def walk_type_values(value: object) -> list[str]:
    result: list[str] = []
    if isinstance(value, dict):
        for key, child in value.items():
            if key == "type" and isinstance(child, str):
                result.append(child)
            result.extend(walk_type_values(child))
    elif isinstance(value, list):
        for child in value:
            result.extend(walk_type_values(child))
    return result


def load_model_result(path: Path) -> dict[str, object]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as error:
        raise ValueError("runner_result_schema") from error
    if (
        not isinstance(value, dict)
        or set(value) != {"schema_version", "response_text"}
        or value.get("schema_version") != RUNNER_RESULT_SCHEMA
        or not isinstance(value.get("response_text"), str)
        or len(value["response_text"].encode("utf-8")) > 16_384
    ):
        raise ValueError("runner_result_schema")
    return value


def load_event_types(path: Path) -> tuple[str, ...]:
    values: set[str] = set()
    try:
        with path.open("r", encoding="utf-8") as stream:
            for line in stream:
                if line.strip():
                    event = json.loads(line)
                    if not isinstance(event, dict):
                        raise ValueError("runner_event_schema")
                    values.update(walk_type_values(event))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as error:
        raise ValueError("runner_event_schema") from error
    if values & DENIED_EVENT_TYPES:
        raise ValueError("runner_tool_event")
    return tuple(sorted(values))
```

- [ ] **Step 9: Run all runner tests**

Run:

```bash
/usr/bin/python3 -m unittest \
  skill-evolver/skills/skill-evolver/tests/test_runner_probe.py \
  -v
```

Expected: all precondition and supervisor tests PASS, including bounded
non-reading stdin and fail-closed orphan-process cleanup.

- [ ] **Step 10: Commit the supervisor**

```bash
git add \
  skill-evolver/skills/skill-evolver/scripts/evolver.py \
  skill-evolver/skills/skill-evolver/tests/test_runner_probe.py \
  skill-evolver/skills/skill-evolver/tests/fixtures/fake_codex_runner.py
git commit -m "feat: supervise codex evaluation runner"
```

Expected: only the runtime, focused tests, and fake runner are staged.

---

### Task 3: Produce the Canonical Runner Contract from Two Real Invocations

**Files:**
- Modify: `skill-evolver/skills/skill-evolver/scripts/evolver.py`
- Modify: `skill-evolver/skills/skill-evolver/tests/test_runner_probe.py`
- Create: `skill-evolver/skills/skill-evolver/tests/fixtures/runner-spike/SKILL.md`
- Create: `skill-evolver/skills/skill-evolver/tests/fixtures/runner-spike/case.json`
- Create: `skill-evolver/skills/skill-evolver/tests/fixtures/runner-spike/model-output.schema.json`

**Interfaces:**
- Consumes: Task 1 preconditions and Task 2 supervisor/validators.
- Produces: `RunnerSpikeFixture`, `load_runner_spike_fixture(root: Path) -> RunnerSpikeFixture`, `build_runner_prompt(fixture: RunnerSpikeFixture) -> bytes`, `run_runner_spike(...) -> dict[str, object]`, `render_runner_report(report: dict[str, object]) -> str`, and `runner-spike`.

- [ ] **Step 1: Create the fixed synthetic runner fixtures**

Create `skill-evolver/skills/skill-evolver/tests/fixtures/runner-spike/SKILL.md`:

```markdown
---
name: runner-contract-probe
description: Return the fixed runner contract probe result when explicitly supplied.
---

# Runner Contract Probe

For the request `runner contract check`, return exactly `runner-spike-ok`.
Do not call tools, inspect the workspace, use the network, or add explanation.
```

Create `skill-evolver/skills/skill-evolver/tests/fixtures/runner-spike/case.json`:

```json
{
  "schema_version": 1,
  "case_id": "runner-contract-001",
  "synthetic_input": "runner contract check",
  "expected_response": "runner-spike-ok"
}
```

Create `skill-evolver/skills/skill-evolver/tests/fixtures/runner-spike/model-output.schema.json`:

```json
{
  "$schema": "https://json-schema.org/draft/2020-12/schema",
  "type": "object",
  "additionalProperties": false,
  "required": [
    "schema_version",
    "response_text"
  ],
  "properties": {
    "schema_version": {
      "const": 1
    },
    "response_text": {
      "type": "string",
      "maxLength": 16384
    }
  }
}
```

- [ ] **Step 2: Add failing contract-generation tests**

Append to `test_runner_probe.py`:

```python
class RunnerContractTests(unittest.TestCase):
    def setUp(self) -> None:
        self.runtime = load_runtime()
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.fixture_root = (
            Path(__file__).resolve().parent
            / "fixtures"
            / "runner-spike"
        )

    def test_fixture_loader_pins_all_three_inputs(self) -> None:
        fixture = self.runtime.load_runner_spike_fixture(self.fixture_root)
        self.assertEqual(fixture.case_id, "runner-contract-001")
        self.assertEqual(fixture.expected_response, "runner-spike-ok")
        self.assertEqual(len(fixture.fixture_digest), 64)
        prompt = self.runtime.build_runner_prompt(fixture)
        self.assertIn(b"runner contract check", prompt)
        self.assertIn(b"runner-spike-ok", prompt)
        self.assertLessEqual(len(prompt), 1024 * 1024)

    def test_contract_digest_excludes_only_its_own_field(self) -> None:
        report = {
            "schema_version": 1,
            "decision": "PASS",
            "contract_digest": "a" * 64,
            "runner": {"version": "0.145.0"},
        }
        unsigned = {key: value for key, value in report.items() if key != "contract_digest"}
        self.assertEqual(
            self.runtime.contract_digest(report),
            self.runtime.sha256_json(unsigned),
        )

    def test_failed_invocation_can_never_write_a_pass_contract(self) -> None:
        runs = [
            {"passed": True, "failure_code": None},
            {"passed": False, "failure_code": "runner_result_schema"},
        ]
        report = self.runtime.compose_runner_contract(
            quality=self.runtime.QualityGateEvidence(
                report_digest="1" * 64,
                policy_digest="2" * 64,
                adapter_digest="3" * 64,
                reviewed_sessions=10,
                reviewed_turns=30,
            ),
            selection=self.runtime.RunnerSelection(
                binary=Path("/fixed/codex"),
                version="0.145.0",
                model_id="gpt-5.6-sol",
                reasoning_effort="low",
            ),
            observed_version="codex-cli 0.145.0",
            fixture_digest="4" * 64,
            schema_digest="5" * 64,
            sandbox_digest="6" * 64,
            resource_digest="7" * 64,
            runs=runs,
        )
        self.assertEqual(report["decision"], "FAIL")
        self.assertEqual(report["failure_codes"], ["runner_result_schema"])
```

- [ ] **Step 3: Run the contract tests and verify the failure**

Run:

```bash
/usr/bin/python3 -m unittest \
  skill-evolver/skills/skill-evolver/tests/test_runner_probe.py \
  -v
```

Expected: FAIL because the fixture and contract functions are undefined.

- [ ] **Step 4: Add fixture loading and prompt construction**

Add:

```python
@dataclass(frozen=True)
class RunnerSpikeFixture:
    case_id: str
    synthetic_input: str
    expected_response: str
    skill_text: str
    output_schema: dict[str, object]
    output_schema_path: Path
    fixture_digest: str


MODEL_OUTPUT_SCHEMA: dict[str, object] = {
    "$schema": "https://json-schema.org/draft/2020-12/schema",
    "type": "object",
    "additionalProperties": False,
    "required": ["schema_version", "response_text"],
    "properties": {
        "schema_version": {"const": 1},
        "response_text": {"type": "string", "maxLength": 16_384},
    },
}


def load_runner_spike_fixture(root: Path) -> RunnerSpikeFixture:
    skill_path = root / "SKILL.md"
    case_path = root / "case.json"
    schema_path = root / "model-output.schema.json"
    if any(path.is_symlink() for path in (skill_path, case_path, schema_path)):
        raise ValueError("runner_fixture_symlink")
    skill_text = skill_path.read_text(encoding="utf-8")
    case = json.loads(case_path.read_text(encoding="utf-8"))
    output_schema = json.loads(schema_path.read_text(encoding="utf-8"))
    if (
        not isinstance(case, dict)
        or case.get("schema_version") != 1
        or set(case)
        != {"schema_version", "case_id", "synthetic_input", "expected_response"}
        or not all(
            isinstance(case.get(key), str) and bool(case[key])
            for key in ("case_id", "synthetic_input", "expected_response")
        )
        or output_schema != MODEL_OUTPUT_SCHEMA
    ):
        raise ValueError("runner_fixture_schema")
    digest = sha256_json(
        {
            "skill_sha256": hashlib.sha256(skill_text.encode("utf-8")).hexdigest(),
            "case": case,
            "output_schema": output_schema,
        }
    )
    return RunnerSpikeFixture(
        case_id=str(case["case_id"]),
        synthetic_input=str(case["synthetic_input"]),
        expected_response=str(case["expected_response"]),
        skill_text=skill_text,
        output_schema=output_schema,
        output_schema_path=schema_path.resolve(strict=True),
        fixture_digest=digest,
    )


def build_runner_prompt(fixture: RunnerSpikeFixture) -> bytes:
    value = {
        "contract": {
            "role": "evaluation_subject",
            "skill_source": "explicit_text_input",
            "tools_allowed": [],
            "candidate_code_execution": False,
            "network_allowed_for_subject": False,
            "output": {
                "schema_version": RUNNER_RESULT_SCHEMA,
                "response_text": "the response required by the supplied skill",
            },
        },
        "skill_markdown": fixture.skill_text,
        "case": {
            "case_id": fixture.case_id,
            "synthetic_input": fixture.synthetic_input,
        },
    }
    prompt = canonical_json_bytes(value)
    if len(prompt) > 1024 * 1024:
        raise ValueError("runner_input_limit")
    return prompt
```

- [ ] **Step 5: Add canonical contract composition**

Add:

```python
def contract_digest(report: dict[str, object]) -> str:
    unsigned = {
        key: value
        for key, value in report.items()
        if key != "contract_digest"
    }
    return sha256_json(unsigned)


def compose_runner_contract(
    quality: QualityGateEvidence,
    selection: RunnerSelection,
    observed_version: str,
    fixture_digest: str,
    schema_digest: str,
    sandbox_digest: str,
    resource_digest: str,
    runs: list[dict[str, object]],
) -> dict[str, object]:
    passed = len(runs) == 2 and all(run.get("passed") is True for run in runs)
    failure_codes = sorted(
        {
            str(run["failure_code"])
            for run in runs
            if isinstance(run.get("failure_code"), str)
        }
    )
    report: dict[str, object] = {
        "schema_version": RUNNER_CONTRACT_SCHEMA,
        "decision": "PASS" if passed else "FAIL",
        "quality_gate": {
            "report_digest": quality.report_digest,
            "policy_digest": quality.policy_digest,
            "adapter_digest": quality.adapter_digest,
            "reviewed_sessions": quality.reviewed_sessions,
            "reviewed_turns": quality.reviewed_turns,
        },
        "runner": {
            "kind": "codex_exec",
            "binary": str(selection.binary),
            "required_version": selection.version,
            "observed_version": observed_version,
            "model_id": selection.model_id,
            "reasoning_effort": selection.reasoning_effort,
            "result_schema_version": RUNNER_RESULT_SCHEMA,
        },
        "fixture_digest": fixture_digest,
        "output_schema_digest": schema_digest,
        "sandbox_policy_digest": sandbox_digest,
        "resource_policy_digest": resource_digest,
        "invocation_count": len(runs),
        "runs": runs,
        "failure_codes": failure_codes,
    }
    report["contract_digest"] = contract_digest(report)
    return report
```

- [ ] **Step 6: Implement one real invocation and the two-run spike**

Add:

```python
def run_runner_invocation(
    selection: RunnerSelection,
    fixture: RunnerSpikeFixture,
    run_root: Path,
    policy: ResourcePolicy,
) -> dict[str, object]:
    run_root.mkdir(parents=True, exist_ok=False, mode=0o700)
    workspace = run_root / "workspace"
    workspace.mkdir(mode=0o700)
    final_message = run_root / "final.json"
    environment = sanitized_runner_environment(
        run_root / "codex-home",
        run_root / "tmp",
    )
    argv = build_runner_argv(
        selection,
        workspace,
        fixture.output_schema_path,
        final_message,
    )
    process = run_supervised(
        argv,
        run_root,
        environment,
        policy,
        prompt=build_runner_prompt(fixture),
    )
    failure_code = process.failure_code
    event_types: tuple[str, ...] = ()
    response_matches = False
    if failure_code is None:
        try:
            model_result = load_model_result(final_message)
            event_types = load_event_types(run_root / "events.jsonl")
            response_matches = (
                model_result["response_text"] == fixture.expected_response
            )
            if not response_matches:
                failure_code = "runner_behavior_mismatch"
        except ValueError as error:
            failure_code = str(error)
    return {
        "passed": failure_code is None,
        "failure_code": failure_code,
        "response_matches": response_matches,
        "event_types": list(event_types),
        "returncode": process.returncode,
        "wall_milliseconds": process.wall_milliseconds,
        "peak_processes": process.peak_processes,
        "peak_rss_bytes": process.peak_rss_bytes,
        "output_bytes": process.output_bytes,
    }


def sandbox_policy() -> dict[str, object]:
    return {
        "schema_version": 1,
        "codex_sandbox": "read-only",
        "subject_input": "explicit_text_only",
        "subject_tools_allowed": [],
        "subject_network": "deny",
        "candidate_scripts": "not_executed",
        "control_plane_network": "codex_model_service_only",
        "credential_environment": "empty",
        "user_config": "ignored",
        "project_rules": "ignored",
        "session_persistence": "ephemeral",
    }


def run_runner_spike(
    installation: Installation,
    quality_report: Path,
    fixture_root: Path,
    selection: RunnerSelection,
) -> dict[str, object]:
    quality = load_quality_gate(quality_report)
    observed_version = verify_runner_version(selection.binary, selection.version)
    fixture = load_runner_spike_fixture(fixture_root)
    policy = ResourcePolicy(
        wall_seconds=120,
        max_processes=16,
        max_rss_bytes=1024 * 1024 * 1024,
        max_output_bytes=16 * 1024 * 1024,
        max_input_bytes=1024 * 1024,
    )
    report_root = installation.data_root / "reports" / "runner"
    runs_root = report_root / "runs"
    report_root.mkdir(parents=True, exist_ok=True, mode=0o700)
    runs_root.mkdir(parents=True, exist_ok=True, mode=0o700)
    run_id = f"{time.time_ns()}-{os.getpid()}"
    runs = [
        run_runner_invocation(
            selection,
            fixture,
            runs_root / f"{run_id}-{index}",
            policy,
        )
        for index in (1, 2)
    ]
    report = compose_runner_contract(
        quality=quality,
        selection=selection,
        observed_version=observed_version,
        fixture_digest=fixture.fixture_digest,
        schema_digest=sha256_json(fixture.output_schema),
        sandbox_digest=sha256_json(sandbox_policy()),
        resource_digest=sha256_json(policy.as_json()),
        runs=runs,
    )
    atomic_write_json(report_root / "runner-contract.json", report)
    atomic_write_bytes(
        report_root / "runner-spike-report.md",
        render_runner_report(report).encode("utf-8"),
        mode=0o600,
    )
    return report
```

- [ ] **Step 7: Add a sanitized Markdown renderer**

Add:

```python
def render_runner_report(report: dict[str, object]) -> str:
    runner = report["runner"]
    lines = [
        "# Skill Evolver Evaluate Runner Spike",
        "",
        f"Decision: **{report['decision']}**",
        "",
        f"- runner: `{runner['kind']}`",
        f"- version: `{runner['observed_version']}`",
        f"- model: `{runner['model_id']}`",
        f"- contract digest: `{report['contract_digest']}`",
        f"- invocations: `{report['invocation_count']}`",
        "",
        "## Runs",
        "",
    ]
    for index, run in enumerate(report["runs"], start=1):
        lines.append(
            f"- run {index}: passed={str(run['passed']).lower()}, "
            f"failure={run['failure_code']}, wall_ms={run['wall_milliseconds']}"
        )
    lines.extend(
        [
            "",
            "## Boundary",
            "",
            (
                "Prepare may consume this exact contract digest."
                if report["decision"] == "PASS"
                else "Prepare and evaluate remain disabled."
            ),
            "",
        ]
    )
    return "\n".join(lines)
```

- [ ] **Step 8: Add the command handler and parser**

Add:

```python
def cmd_runner_spike(args: argparse.Namespace) -> int:
    installation = load_installation(Path(args.installation))
    try:
        report = run_runner_spike(
            installation=installation,
            quality_report=Path(args.quality_report),
            fixture_root=Path(args.fixture_root),
            selection=RunnerSelection(
                binary=PINNED_RUNNER_BINARY,
                version=PINNED_RUNNER_VERSION,
                model_id=PINNED_MODEL_ID,
                reasoning_effort=PINNED_REASONING_EFFORT,
            ),
        )
    except (OSError, TypeError, ValueError, subprocess.SubprocessError) as error:
        report = {
            "schema_version": RUNNER_CONTRACT_SCHEMA,
            "decision": "FAIL",
            "failure_codes": [str(error)],
        }
        report_root = installation.data_root / "reports" / "runner"
        report_root.mkdir(parents=True, exist_ok=True, mode=0o700)
        report["contract_digest"] = contract_digest(report)
        atomic_write_json(report_root / "runner-contract.json", report)
        atomic_write_bytes(
            report_root / "runner-spike-report.md",
            render_runner_report_failure(report).encode("utf-8"),
            mode=0o600,
        )
    write_json_stdout(
        {
            "decision": report["decision"],
            "contract_digest": report["contract_digest"],
            "failure_codes": report.get("failure_codes", []),
        }
    )
    return 0 if report["decision"] == "PASS" else 2


def render_runner_report_failure(report: dict[str, object]) -> str:
    return "\n".join(
        [
            "# Skill Evolver Evaluate Runner Spike",
            "",
            "Decision: **FAIL**",
            "",
            f"- contract digest: `{report['contract_digest']}`",
            f"- failure codes: `{json.dumps(report['failure_codes'])}`",
            "",
            "Prepare and evaluate remain disabled.",
            "",
        ]
    )
```

Register inside `build_parser()`:

```python
    runner_spike = commands.add_parser("runner-spike")
    runner_spike.add_argument("--installation", required=True)
    runner_spike.add_argument("--quality-report", required=True)
    runner_spike.add_argument("--fixture-root", required=True)
    runner_spike.set_defaults(handler=cmd_runner_spike)
```

- [ ] **Step 9: Run the deterministic test suite**

Run:

```bash
/usr/bin/python3 -m unittest discover \
  -s skill-evolver/skills/skill-evolver/tests \
  -p 'test_*.py' -v
```

Expected: all tests PASS without contacting the model service.

- [ ] **Step 10: Commit the runner fixtures and contract implementation**

```bash
git add \
  skill-evolver/skills/skill-evolver/scripts/evolver.py \
  skill-evolver/skills/skill-evolver/tests/test_runner_probe.py \
  skill-evolver/skills/skill-evolver/tests/fixtures/runner-spike/SKILL.md \
  skill-evolver/skills/skill-evolver/tests/fixtures/runner-spike/case.json \
  skill-evolver/skills/skill-evolver/tests/fixtures/runner-spike/model-output.schema.json
git commit -m "feat: add pinned codex runner spike"
```

Expected: source, tests, and synthetic fixtures are committed; private runner output is not staged.

---

### Task 4: Run the Hard Gate, Publish the Sanitized Decision, and Disable Drifted Runners

**Files:**
- Modify: `skill-evolver/skills/skill-evolver/SKILL.md`
- Modify: `skill-evolver/skills/skill-evolver/scripts/evolver.py`
- Modify: `skill-evolver/skills/skill-evolver/tests/test_runner_probe.py`
- Create: `skill-evolver/docs/release-reports/evaluate-runner-spike.json`

**Interfaces:**
- Consumes: the complete runner spike from Task 3 and the actual Read-only quality report.
- Produces: `load_runner_contract(installation: Installation) -> dict[str, object]`, a private PASS/FAIL contract, a sanitized committed decision, and the hard precondition consumed by prepare.

- [ ] **Step 1: Add failing contract reload and drift tests**

Append to `test_runner_probe.py`:

```python
class RunnerContractReloadTests(unittest.TestCase):
    def setUp(self) -> None:
        self.runtime = load_runtime()
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        sessions = self.root / "sessions"
        sessions.mkdir(mode=0o700)
        workspace = self.root / "workspace"
        skills = self.root / "skills"
        workspace.mkdir(mode=0o700)
        skills.mkdir(mode=0o700)
        installation_path = self.runtime.initialize_runtime(
            self.root / "data",
            (sessions,),
            {
                "workspace_roots": [str(workspace)],
                "exclude_roots": [],
                "mutable_skill_roots": [str(skills)],
            },
        )
        self.installation = self.runtime.load_installation(installation_path)
        self.contract_path = (
            self.installation.data_root
            / "reports"
            / "runner"
            / "runner-contract.json"
        )
        self.contract_path.parent.mkdir(parents=True, mode=0o700)

    def valid_contract(self) -> dict[str, object]:
        report: dict[str, object] = {
            "schema_version": 1,
            "decision": "PASS",
            "quality_gate": {
                "report_digest": "1" * 64,
                "policy_digest": "2" * 64,
                "adapter_digest": "3" * 64,
                "reviewed_sessions": 10,
                "reviewed_turns": 30,
            },
            "runner": {
                "kind": "codex_exec",
                "binary": str(self.runtime.PINNED_RUNNER_BINARY),
                "required_version": "0.145.0",
                "observed_version": "codex-cli 0.145.0",
                "model_id": "gpt-5.6-sol",
                "reasoning_effort": "low",
                "result_schema_version": 1,
            },
            "fixture_digest": "4" * 64,
            "output_schema_digest": "5" * 64,
            "sandbox_policy_digest": "6" * 64,
            "resource_policy_digest": "7" * 64,
            "invocation_count": 2,
            "runs": [{"passed": True}, {"passed": True}],
            "failure_codes": [],
        }
        report["contract_digest"] = self.runtime.contract_digest(report)
        return report

    def test_reload_requires_digest_and_current_runner_version(self) -> None:
        self.runtime.atomic_write_json(self.contract_path, self.valid_contract())
        with mock.patch.object(
            self.runtime,
            "verify_runner_version",
            return_value="codex-cli 0.145.0",
        ):
            report = self.runtime.load_runner_contract(self.installation)
        self.assertEqual(report["decision"], "PASS")

    def test_tamper_and_version_drift_fail_closed(self) -> None:
        value = self.valid_contract()
        value["runner"]["model_id"] = "different-model"
        self.runtime.atomic_write_json(self.contract_path, value)
        with self.assertRaisesRegex(ValueError, "runner_contract_digest"):
            self.runtime.load_runner_contract(self.installation)

        value = self.valid_contract()
        self.runtime.atomic_write_json(self.contract_path, value)
        with mock.patch.object(
            self.runtime,
            "verify_runner_version",
            side_effect=ValueError("runner_version_mismatch"),
        ):
            with self.assertRaisesRegex(ValueError, "runner_version_mismatch"):
                self.runtime.load_runner_contract(self.installation)
```

- [ ] **Step 2: Implement strict contract reload**

Add to `evolver.py`:

```python
def load_runner_contract(
    installation: Installation,
) -> dict[str, object]:
    path = installation.data_root / "reports" / "runner" / "runner-contract.json"
    if path.is_symlink():
        raise ValueError("runner_contract_symlink")
    value = json.loads(path.read_text(encoding="utf-8"))
    if (
        not isinstance(value, dict)
        or value.get("schema_version") != RUNNER_CONTRACT_SCHEMA
        or value.get("decision") != "PASS"
        or not is_sha256(value.get("contract_digest"))
        or contract_digest(value) != value["contract_digest"]
        or value.get("invocation_count") != 2
        or not isinstance(value.get("runs"), list)
        or not all(run.get("passed") is True for run in value["runs"])
    ):
        raise ValueError("runner_contract_digest")
    runner = value.get("runner")
    if (
        not isinstance(runner, dict)
        or runner.get("kind") != "codex_exec"
        or runner.get("binary") != str(PINNED_RUNNER_BINARY)
        or runner.get("required_version") != PINNED_RUNNER_VERSION
        or runner.get("model_id") != PINNED_MODEL_ID
        or runner.get("reasoning_effort") != PINNED_REASONING_EFFORT
        or runner.get("result_schema_version") != RUNNER_RESULT_SCHEMA
    ):
        raise ValueError("runner_contract_selection")
    verify_runner_version(PINNED_RUNNER_BINARY, PINNED_RUNNER_VERSION)
    return value
```

- [ ] **Step 3: Run deterministic tests before the model-backed spike**

Run:

```bash
/usr/bin/python3 -m unittest discover \
  -s skill-evolver/skills/skill-evolver/tests \
  -p 'test_*.py' -v
```

Expected: all tests PASS.

- [ ] **Step 4: Run the actual pinned Codex spike**

Run:

```bash
/usr/bin/python3 -I \
  skill-evolver/skills/skill-evolver/scripts/evolver.py \
  runner-spike \
  --installation /Users/igyeongseob/.codex/skill-evolver/installation.json \
  --quality-report /Users/igyeongseob/Documents/오픈소스/skill-evolver/docs/release-reports/read-only-quality-gate.json \
  --fixture-root /Users/igyeongseob/Documents/오픈소스/skill-evolver/skills/skill-evolver/tests/fixtures/runner-spike
```

Expected on PASS: exit code `0`, `"decision": "PASS"`, a 64-character `contract_digest`, two passing runs, exact response `runner-spike-ok`, no denied event types, and private files:

```text
/Users/igyeongseob/.codex/skill-evolver/reports/runner/runner-contract.json
/Users/igyeongseob/.codex/skill-evolver/reports/runner/runner-spike-report.md
```

Expected on any unsupported control-plane authentication, version, output schema, event, sandbox, or resource behavior: exit code `2` and `"decision": "FAIL"`. Preserve the FAIL contract and stop; do not edit it into a PASS.

- [ ] **Step 5: Create a sanitized repository decision from the private contract**

On PASS, run:

```bash
/usr/bin/python3 -I \
  skill-evolver/skills/skill-evolver/scripts/evolver.py \
  export-runner-decision \
  --installation /Users/igyeongseob/.codex/skill-evolver/installation.json \
  --output /Users/igyeongseob/Documents/오픈소스/skill-evolver/docs/release-reports/evaluate-runner-spike.json
```

Before running it, add this handler:

```python
def cmd_export_runner_decision(args: argparse.Namespace) -> int:
    installation = load_installation(Path(args.installation))
    contract = load_runner_contract(installation)
    runner = contract["runner"]
    output = {
        "schema_version": 1,
        "decision": "PASS",
        "contract_digest": contract["contract_digest"],
        "runner_kind": runner["kind"],
        "runner_version": runner["required_version"],
        "model_id": runner["model_id"],
        "sandbox_policy_digest": contract["sandbox_policy_digest"],
        "resource_policy_digest": contract["resource_policy_digest"],
        "invocation_count": contract["invocation_count"],
    }
    atomic_write_json(Path(args.output).resolve(), output)
    write_json_stdout(output)
    return 0
```

Register inside `build_parser()`:

```python
    export_runner = commands.add_parser("export-runner-decision")
    export_runner.add_argument("--installation", required=True)
    export_runner.add_argument("--output", required=True)
    export_runner.set_defaults(handler=cmd_export_runner_decision)
```

Expected: the committed report contains no prompt, response, raw event, private data-root path, session identifier, credential name/value, or transcript content.

- [ ] **Step 6: Add the explicit skill intent and gate boundary**

Append to `skill-evolver/skills/skill-evolver/SKILL.md`:

```markdown
## Evaluate runner gate

Run the runner spike only when the user explicitly asks for `$skill-evolver
runner spike`. Use the exact repository quality report and runner fixture paths.

Do not claim runner support unless `runner-spike` exits 0 and
`reports/runner/runner-contract.json` has decision `PASS`.

Before every prepare or evaluate action, run the runtime contract loader. A
missing, failed, tampered, or version-drifted contract disables both actions.
Do not select a newer runner, another model, broader sandbox, credential
environment, tool access, or network policy automatically.
```

- [ ] **Step 7: Verify the private and committed contracts**

Run:

```bash
/usr/bin/python3 -m json.tool \
  /Users/igyeongseob/.codex/skill-evolver/reports/runner/runner-contract.json >/dev/null
/usr/bin/python3 -m json.tool \
  skill-evolver/docs/release-reports/evaluate-runner-spike.json >/dev/null
rg -n \
  'OPENAI_API_KEY|CODEX_API_KEY|Bearer|Authorization|PRIVATE KEY|session_id|turn_id|transcript_path|synthetic_input|response_text' \
  skill-evolver/docs/release-reports/evaluate-runner-spike.json
```

Expected: both JSON validations exit `0`; the privacy scan has no matches and exits `1`.

- [ ] **Step 8: Run final regression tests**

Run:

```bash
/usr/bin/python3 -m unittest discover \
  -s skill-evolver/skills/skill-evolver/tests \
  -p 'test_*.py' -v
```

Expected: all tests PASS.

- [ ] **Step 9: Commit the hard-gate result**

```bash
git add \
  skill-evolver/skills/skill-evolver/SKILL.md \
  skill-evolver/skills/skill-evolver/scripts/evolver.py \
  skill-evolver/skills/skill-evolver/tests/test_runner_probe.py \
  skill-evolver/docs/release-reports/evaluate-runner-spike.json
git commit -m "test: record evaluate runner contract"
```

Expected: the sanitized PASS decision is committed; private raw runner runs and private contract are not staged.

## Completion Criteria

- The actual Read-only quality report independently satisfies every release metric before the runner starts.
- The exact canonical binary reports Codex CLI `0.145.0`; any other version fails closed.
- Two actual `gpt-5.6-sol`/`low` invocations consume the explicit synthetic skill and case and return exactly `runner-spike-ok`.
- Both invocations use ephemeral, ignored-config, ignored-rules, read-only Codex execution and an empty credential-variable environment.
- Event JSONL contains no command, file-change, MCP, tool-call, or web-search event.
- The external supervisor records and enforces 120 seconds, 16 processes, 1 GiB RSS, 16 MiB output, and 1 MiB input.
- Nonblocking stdin delivery shares that deadline; timeout kills the process
  group, and a root process that exits while a group child remains fails as
  `runner_orphan_process` after cleanup.
- Timeout, runner crash, malformed JSON, denied event, response mismatch, and every resource breach are represented as FAIL, never PASS.
- `<data_root>/reports/runner/runner-contract.json` has a self-verifying full SHA-256 `contract_digest`; changing any signed field invalidates it.
- `load_runner_contract()` rechecks the installed runner version on every prepare/evaluate entry.
- The committed release report contains only the decision, digests, pinned identifiers, and invocation count.
- No installed skill, candidate, database schema, staging tree, snapshot, or mutable-skill root was changed.
- Only a PASS authorizes the Prepare implementation plan.
