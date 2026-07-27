# Skill Evolver Evaluate Execution Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Execute one exact prepared evaluation against immutable base and candidate artifacts, produce a digest-bound report, and mark the candidate `ready_for_apply` only when every release rule passes.

**Architecture:** `evaluate E-xxx@full-digest` first rehashes the prepared spec, base, candidate, harness, runner contract, and installed source, then claims the evaluation with the existing lease contract. Each synthetic case runs base and candidate in separate ephemeral Codex contexts, followed by a third blind grading context using the same pinned runner; deterministic Python code applies structural and readiness rules and atomically commits either `ready_for_apply` or `evaluation_failed`.

**Tech Stack:** macOS, Codex CLI `0.145.0`, model `gpt-5.6-sol`, SQLite, `/usr/bin/python3` 3.9+, Python standard library (`hashlib`, `json`, `os`, `pathlib`, `sqlite3`, `time`, `unittest`), immutable artifacts from Prepare, supervised runner from Runner Spike.

## Global Constraints

- Source specification: `skill-evolver/docs/superpowers/specs/2026-07-26-skill-evolver-design.md`.
- This plan requires both prior artifacts: a current PASS `reports/runner/runner-contract.json` and a schema-v1 prepared `reports/evaluations/E-xxx/spec.json`.
- The user must request the exact full SHA-256 as `evaluate E-xxx@evaluation-spec-digest`; shortened or omitted digests are rejected.
- `evaluate` never accepts a candidate ID alone and never selects the newest evaluation implicitly.
- Every spec field is revalidated before and after execution: base, candidate, manifests, harness, runner contract, model, sandbox, resource policy, and target identity.
- The installed target must match `base_hash` before evaluation and remain unchanged afterward. Drift produces `evaluation_failed`; evaluation never refreshes the base automatically.
- Base and candidate execute in separate ephemeral contexts. No context, output, or writable directory is shared between variants.
- The explicit text-only skill tree is serialized as input; candidate scripts and commands are never executed.
- Each case gets one base subject run, one candidate subject run, and one independent blind grader run.
- Grading order is deterministically blinded from the evaluation spec digest and case ID; the grader receives labels `A` and `B`, not `base` and `candidate`.
- Reproduction count is exactly 1, regression count is 2 or 3, and holdout count is exactly 2.
- The same external supervisor limits every invocation to 120 seconds, 16 processes, 1 GiB RSS, 16 MiB output, and 1 MiB input.
- Any timeout, crash, malformed model output, denied tool event, sandbox violation, resource breach, missing case, or artifact change is an evaluation failure, never a skipped case or pass.
- `ready_for_apply` requires reproduction pass, every critical regression pass, no critical holdout failure, no case where candidate is clearly worse, a scope-contained diff, and structural validation pass.
- Subject and grader text is sanitized and capped before report persistence; synthetic input and long model responses are not copied into the report.
- Reports live at `<data_root>/reports/evaluations/E-xxx/report.json` and
  `report.md`; raw outputs remain under immutable
  `runs/attempt-<sha256(evaluation-id,lease-owner)>/` directories so an
  expired-lease retry never reuses a partially written run path.
- Evaluation artifacts are append-only. Rerunning after failure requires a new `prepare` and new evaluation ID/spec digest.
- A ready result only authorizes an Apply preview. It does not modify an installed skill and does not authorize terminal execution.
- Runtime and tests use the Python standard library only; `evolver.py` remains self-contained under `python -I`.
- Stage exact paths only; never run `git add .`.

## File Structure

| Path | Responsibility |
| --- | --- |
| `skill-evolver/skills/skill-evolver/scripts/evolver.py` | Prepared-artifact verification, lease, subject/grader execution, structural/readiness decision, report, and DB transition. |
| `skill-evolver/skills/skill-evolver/SKILL.md` | Exact-digest evaluation intent, result rendering, and no-apply boundary. |
| `skill-evolver/skills/skill-evolver/tests/test_evaluate.py` | Digest, drift, lease, isolation, blind order, failure, readiness, report, and source-unchanged tests. |
| `skill-evolver/skills/skill-evolver/tests/support.py` | Shared `seed_ready_evaluation()` factory consumed by Apply mutation tests. |
| `skill-evolver/docs/release-reports/evaluate-execution.json` | Sanitized deterministic Evaluate release PASS/FAIL gate consumed by Apply. |
| `<data_root>/reports/evaluations/E-xxx/runs/attempt-<digest>/` | Private immutable subject and grader artifacts for one lease attempt. |
| `<data_root>/reports/evaluations/E-xxx/report.json` | Canonical machine report tied to the exact spec digest. |
| `<data_root>/reports/evaluations/E-xxx/report.md` | Human result, diff scope, test counts, risk, and next action. |

---

### Task 1: Revalidate and Lease the Exact Prepared Evaluation

**Files:**
- Modify: `skill-evolver/skills/skill-evolver/scripts/evolver.py`
- Create: `skill-evolver/skills/skill-evolver/tests/test_evaluate.py`

**Interfaces:**
- Consumes: known schema v2 or v3, `load_runner_contract()`,
  `build_skill_manifest()`, `manifest_digest()`,
  `current_harness_digest()` from Prepare, `resolve_mutable_target()`, and
  sealed Prepare artifacts.
- Produces: `PreparedEvaluation`, `load_prepared_evaluation(...) -> PreparedEvaluation`, `claim_evaluation(...) -> str`, `heartbeat_evaluation(...) -> None`, and `recover_expired_evaluation(...) -> str`.

- [ ] **Step 1: Write failing digest, drift, and lease tests**

Create `skill-evolver/skills/skill-evolver/tests/test_evaluate.py`:

```python
from __future__ import annotations

import json
import sqlite3
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from support import load_runtime


class EvaluationClaimTests(unittest.TestCase):
    def setUp(self) -> None:
        self.runtime = load_runtime()
        self.conn = sqlite3.connect(":memory:", isolation_level=None)
        self.conn.execute("PRAGMA foreign_keys=ON")
        self.conn.executescript(
            """
            CREATE TABLE candidates(
              id INTEGER PRIMARY KEY,status TEXT NOT NULL,
              ready_evaluation_id INTEGER,updated_at TEXT NOT NULL
            );
            CREATE TABLE evaluations(
              id INTEGER PRIMARY KEY,candidate_id INTEGER NOT NULL,
              evaluation_spec_digest TEXT,result TEXT NOT NULL,
              lease_owner TEXT,lease_expires_at TEXT,finished_at TEXT,
              report_path TEXT,report_digest TEXT
            );
            INSERT INTO candidates VALUES(1,'prepared',NULL,'t0');
            INSERT INTO evaluations VALUES(1,1,
              'aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa',
              'prepared',NULL,NULL,NULL,NULL,NULL);
            """
        )

    def test_claim_requires_exact_full_digest(self) -> None:
        owner = self.runtime.claim_evaluation(
            self.conn, 1, "a" * 64, "owner-1", "t1", "t2"
        )
        self.assertEqual(owner, "owner-1")
        self.assertEqual(
            self.conn.execute("SELECT result FROM evaluations").fetchone()[0],
            "evaluating",
        )

    def test_short_wrong_and_competing_claims_fail(self) -> None:
        for digest in ("a" * 12, "b" * 64):
            with self.subTest(digest=digest):
                with self.assertRaisesRegex(ValueError, "exact_spec_digest_required"):
                    self.runtime.claim_evaluation(
                        self.conn, 1, digest, "owner-1", "t1", "t2"
                    )
        self.runtime.claim_evaluation(
            self.conn, 1, "a" * 64, "owner-1", "t1", "t2"
        )
        with self.assertRaisesRegex(ValueError, "evaluation_not_prepared"):
            self.runtime.claim_evaluation(
                self.conn, 1, "a" * 64, "owner-2", "t1", "t2"
            )

    def test_claim_rolls_back_evaluation_when_candidate_cas_fails(self) -> None:
        self.conn.execute("UPDATE candidates SET status='proposed' WHERE id=1")
        with self.assertRaisesRegex(ValueError, "candidate_state_race"):
            self.runtime.claim_evaluation(
                self.conn, 1, "a" * 64, "owner-1", "t1", "t2"
            )
        self.assertEqual(
            self.conn.execute(
                "SELECT result,lease_owner FROM evaluations"
            ).fetchone(),
            ("prepared", None),
        )

    def test_heartbeat_requires_same_live_owner(self) -> None:
        self.runtime.claim_evaluation(
            self.conn, 1, "a" * 64, "owner-1", "t1", "t2"
        )
        self.runtime.heartbeat_evaluation(
            self.conn, 1, "owner-1", "t1", "t3"
        )
        with self.assertRaisesRegex(ValueError, "evaluation_lease_lost"):
            self.runtime.heartbeat_evaluation(
                self.conn, 1, "owner-2", "t1", "t4"
            )

    def test_expired_evaluation_with_intact_artifacts_returns_to_prepared(self) -> None:
        self.runtime.claim_evaluation(
            self.conn, 1, "a" * 64, "owner-1", "t1", "t2"
        )
        result = self.runtime.recover_expired_evaluation(
            self.conn, 1, "t3", artifacts_intact=True
        )
        self.assertEqual(result, "prepared")
        self.assertEqual(
            self.conn.execute(
                "SELECT result,lease_owner,lease_expires_at FROM evaluations"
            ).fetchone(),
            ("prepared", None, None),
        )
        self.assertEqual(
            self.conn.execute("SELECT status FROM candidates").fetchone()[0],
            "prepared",
        )

    def test_expired_evaluation_with_changed_artifact_fails_closed(self) -> None:
        self.runtime.claim_evaluation(
            self.conn, 1, "a" * 64, "owner-1", "t1", "t2"
        )
        result = self.runtime.recover_expired_evaluation(
            self.conn, 1, "t3", artifacts_intact=False
        )
        self.assertEqual(result, "evaluation_failed")
        self.assertEqual(
            self.conn.execute(
                "SELECT result,finished_at FROM evaluations"
            ).fetchone(),
            ("evaluation_failed", "t3"),
        )
        self.assertEqual(
            self.conn.execute("SELECT status FROM candidates").fetchone()[0],
            "evaluation_failed",
        )

```

- [ ] **Step 2: Run the focused tests and verify failure**

Run:

```bash
/usr/bin/python3 -m unittest \
  skill-evolver/skills/skill-evolver/tests/test_evaluate.py -v
```

Expected: FAIL because claim, heartbeat, and expired-evaluation recovery are undefined.

- [ ] **Step 3: Implement exact-digest claim and heartbeat**

Add:

```python
def claim_evaluation(
    conn: sqlite3.Connection,
    evaluation_id: int,
    requested_digest: str,
    owner: str,
    now: str,
    expires_at: str,
) -> str:
    if not is_sha256(requested_digest):
        raise ValueError("exact_spec_digest_required")
    conn.execute("BEGIN IMMEDIATE")
    try:
        changed = conn.execute(
            """
            UPDATE evaluations
               SET result='evaluating',lease_owner=?,lease_expires_at=?
             WHERE id=? AND result='prepared'
               AND evaluation_spec_digest=?
            """,
            (owner, expires_at, evaluation_id, requested_digest),
        ).rowcount
        if changed != 1:
            row = conn.execute(
                "SELECT evaluation_spec_digest FROM evaluations WHERE id=?",
                (evaluation_id,),
            ).fetchone()
            if row is not None and row[0] != requested_digest:
                raise ValueError("exact_spec_digest_required")
            raise ValueError("evaluation_not_prepared")
        changed = conn.execute(
            """
            UPDATE candidates SET status='evaluating',updated_at=?
             WHERE id=(SELECT candidate_id FROM evaluations WHERE id=?)
               AND status='prepared'
            """,
            (now, evaluation_id),
        ).rowcount
        if changed != 1:
            raise ValueError("candidate_state_race")
        conn.commit()
    except BaseException:
        conn.rollback()
        raise
    return owner


def heartbeat_evaluation(
    conn: sqlite3.Connection,
    evaluation_id: int,
    owner: str,
    now: str,
    expires_at: str,
) -> None:
    changed = conn.execute(
        """
        UPDATE evaluations SET lease_expires_at=?
         WHERE id=? AND result='evaluating' AND lease_owner=?
           AND lease_expires_at>?
        """,
        (expires_at, evaluation_id, owner, now),
    ).rowcount
    if changed != 1:
        raise ValueError("evaluation_lease_lost")


def recover_expired_evaluation(
    conn: sqlite3.Connection,
    evaluation_id: int,
    now: str,
    *,
    artifacts_intact: bool,
) -> str:
    result = "prepared" if artifacts_intact else "evaluation_failed"
    candidate_status = result
    finished_at = None if artifacts_intact else now
    conn.execute("BEGIN IMMEDIATE")
    try:
        row = conn.execute(
            """
            SELECT candidate_id FROM evaluations
             WHERE id=? AND result='evaluating'
               AND lease_expires_at IS NOT NULL
               AND lease_expires_at<=?
            """,
            (evaluation_id, now),
        ).fetchone()
        if row is None:
            raise ValueError("evaluation_not_expired")
        changed = conn.execute(
            """
            UPDATE evaluations SET result=?,finished_at=?,
                   lease_owner=NULL,lease_expires_at=NULL
             WHERE id=? AND result='evaluating'
               AND lease_expires_at IS NOT NULL
               AND lease_expires_at<=?
            """,
            (result, finished_at, evaluation_id, now),
        ).rowcount
        if changed != 1:
            raise ValueError("evaluation_recovery_race")
        changed = conn.execute(
            """
            UPDATE candidates SET status=?,ready_evaluation_id=NULL,
                   updated_at=?
             WHERE id=? AND status='evaluating'
            """,
            (candidate_status, now, row[0]),
        ).rowcount
        if changed != 1:
            raise ValueError("candidate_state_race")
        conn.commit()
    except BaseException:
        conn.rollback()
        raise
    return result
```

- [ ] **Step 4: Implement immutable artifact loading**

Add:

```python
@dataclass(frozen=True)
class PreparedEvaluation:
    evaluation_id: int
    candidate_id: int
    target_identity: str
    spec_digest: str
    spec: dict[str, object]
    base: Path
    candidate: Path
    harness: Path
    report_root: Path


def load_prepared_evaluation(
    conn: sqlite3.Connection,
    installation: Installation,
    config: Config,
    evaluation_id: int,
    requested_digest: str,
) -> PreparedEvaluation:
    runner_contract = load_runner_contract(installation)
    row = conn.execute(
        """
        SELECT e.candidate_id,c.target_identity,e.evaluation_spec_path,
               e.evaluation_spec_digest,e.staging_path
          FROM evaluations e JOIN candidates c ON c.id=e.candidate_id
         WHERE e.id=? AND e.result='prepared' AND c.status='prepared'
        """,
        (evaluation_id,),
    ).fetchone()
    if row is None or requested_digest != row[3] or not is_sha256(requested_digest):
        raise ValueError("exact_spec_digest_required")
    spec_path = Path(row[2])
    expected_report_root = (
        installation.data_root
        / "reports"
        / "evaluations"
        / f"E-{evaluation_id:03d}"
    )
    expected_staging = installation.data_root / "staging" / f"E-{evaluation_id:03d}"
    if (
        spec_path.is_symlink()
        or spec_path.resolve(strict=True) != (expected_report_root / "spec.json").resolve(strict=True)
        or Path(row[4]).resolve(strict=True) != expected_staging.resolve(strict=True)
    ):
        raise ValueError("evaluation_artifact_path")
    spec = json.loads(spec_path.read_text(encoding="utf-8"))
    if (
        not isinstance(spec, dict)
        or sha256_json(spec) != requested_digest
        or spec.get("evaluation_id") != evaluation_id
        or spec.get("candidate_id") != row[0]
        or spec.get("target_identity") != row[1]
    ):
        raise ValueError("evaluation_spec_changed")
    staging = Path(row[4])
    base, candidate, harness = (
        staging / "base", staging / "candidate", staging / "harness"
    )
    checks = {
        "base": manifest_digest(build_skill_manifest(base)) == spec["base_hash"],
        "candidate": manifest_digest(build_skill_manifest(candidate)) == spec["candidate_hash"],
        "harness": current_harness_digest(harness) == spec["harness_digest"],
        "runner": runner_contract["contract_digest"] == spec["runner_contract_digest"],
    }
    target = resolve_mutable_target(config, installation, row[1])
    checks["source"] = manifest_digest(build_skill_manifest(target)) == spec["base_hash"]
    if not all(checks.values()):
        raise ValueError("prepared_artifact_changed")
    return PreparedEvaluation(
        evaluation_id=evaluation_id,
        candidate_id=row[0],
        target_identity=row[1],
        spec_digest=requested_digest,
        spec=spec,
        base=base,
        candidate=candidate,
        harness=harness,
        report_root=spec_path.parent,
    )


def evaluation_artifacts_intact(
    conn: sqlite3.Connection,
    installation: Installation,
    config: Config,
    evaluation_id: int,
) -> bool:
    row = conn.execute(
        """
        SELECT e.candidate_id,c.target_identity,e.evaluation_spec_path,
               e.evaluation_spec_digest,e.staging_path
          FROM evaluations e JOIN candidates c ON c.id=e.candidate_id
         WHERE e.id=? AND e.result='evaluating'
        """,
        (evaluation_id,),
    ).fetchone()
    if row is None:
        return False
    try:
        spec_path = Path(row[2])
        expected_report = (
            installation.data_root
            / "reports"
            / "evaluations"
            / f"E-{evaluation_id:03d}"
            / "spec.json"
        )
        expected_staging = (
            installation.data_root / "staging" / f"E-{evaluation_id:03d}"
        )
        if (
            spec_path.is_symlink()
            or spec_path.resolve(strict=True) != expected_report.resolve(strict=True)
            or Path(row[4]).resolve(strict=True)
            != expected_staging.resolve(strict=True)
        ):
            return False
        spec = json.loads(spec_path.read_text(encoding="utf-8"))
        staging = Path(row[4])
        target = resolve_mutable_target(config, installation, row[1])
        runner = load_runner_contract(installation)
        return (
            isinstance(spec, dict)
            and sha256_json(spec) == row[3]
            and spec.get("evaluation_id") == evaluation_id
            and spec.get("candidate_id") == row[0]
            and spec.get("target_identity") == row[1]
            and manifest_digest(build_skill_manifest(staging / "base"))
            == spec.get("base_hash")
            and manifest_digest(build_skill_manifest(staging / "candidate"))
            == spec.get("candidate_hash")
            and current_harness_digest(staging / "harness")
            == spec.get("harness_digest")
            and runner.get("contract_digest")
            == spec.get("runner_contract_digest")
            and manifest_digest(build_skill_manifest(target))
            == spec.get("base_hash")
        )
    except (KeyError, OSError, TypeError, ValueError):
        return False
```

- [ ] **Step 5: Run tests and commit**

Run:

```bash
/usr/bin/python3 -m unittest \
  skill-evolver/skills/skill-evolver/tests/test_evaluate.py -v
```

Expected: all six claim, rollback, lease, and expired-recovery tests PASS.

```bash
git add \
  skill-evolver/skills/skill-evolver/scripts/evolver.py \
  skill-evolver/skills/skill-evolver/tests/test_evaluate.py
git commit -m "feat: claim exact prepared evaluations"
```

---

### Task 2: Run Isolated Base, Candidate, and Blind Grader Contexts

**Files:**
- Modify: `skill-evolver/skills/skill-evolver/scripts/evolver.py`
- Modify: `skill-evolver/skills/skill-evolver/tests/test_evaluate.py`

**Interfaces:**
- Consumes: `PreparedEvaluation`, `run_supervised()`, `build_runner_argv()`, `load_model_result()`, `load_event_types()`, and the pinned runner contract.
- Produces: `load_harness_cases(prepared)`, `serialize_skill_input(root)`,
  `blind_variant_order(spec_digest, case_id)`,
  `evaluation_attempt_root(report_root, evaluation_id, owner)`, and
  `run_behavior_case(...)`.

- [ ] **Step 1: Add failing harness, isolation, and blinding tests**

Append:

```python
class BehaviorCaseTests(unittest.TestCase):
    def setUp(self) -> None:
        self.runtime = load_runtime()
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.base = self.root / "base"
        self.candidate = self.root / "candidate"
        self.base.mkdir()
        self.candidate.mkdir()
        (self.base / "SKILL.md").write_text("# Base\n", encoding="utf-8")
        (self.candidate / "SKILL.md").write_text("# Candidate\n", encoding="utf-8")

    def test_explicit_skill_input_is_text_only_and_bounded(self) -> None:
        value = self.runtime.serialize_skill_input(self.base)
        self.assertEqual(
            value,
            [{"path": "SKILL.md", "content_utf8": "# Base\n"}],
        )
        (self.base / "run.py").write_text("print('no')", encoding="utf-8")
        with self.assertRaisesRegex(ValueError, "executable_skill_input"):
            self.runtime.serialize_skill_input(self.base)

    def test_blind_order_is_stable_and_hides_variant_names(self) -> None:
        first = self.runtime.blind_variant_order("a" * 64, "repro-1")
        second = self.runtime.blind_variant_order("a" * 64, "repro-1")
        self.assertEqual(first, second)
        self.assertEqual(set(first), {"base", "candidate"})

    def test_grader_result_schema_is_strict(self) -> None:
        valid = json.dumps(
            {
                "a_pass": True,
                "b_pass": True,
                "winner": "B",
                "critical_failure": False,
                "summary": "B follows the rubric more directly.",
            }
        )
        result = self.runtime.parse_grader_response(valid)
        self.assertEqual(result["winner"], "B")
        with self.assertRaisesRegex(ValueError, "grader_result_schema"):
            self.runtime.parse_grader_response('{"winner":"candidate"}')

    def test_recovered_retry_uses_a_fresh_attempt_run_directory(self) -> None:
        conn = sqlite3.connect(":memory:", isolation_level=None)
        self.addCleanup(conn.close)
        conn.executescript(
            """
            CREATE TABLE candidates(
              id INTEGER PRIMARY KEY,status TEXT NOT NULL,
              ready_evaluation_id INTEGER,updated_at TEXT NOT NULL
            );
            CREATE TABLE evaluations(
              id INTEGER PRIMARY KEY,candidate_id INTEGER NOT NULL,
              evaluation_spec_digest TEXT,result TEXT NOT NULL,
              lease_owner TEXT,lease_expires_at TEXT,finished_at TEXT
            );
            INSERT INTO candidates VALUES(1,'prepared',NULL,'t0');
            INSERT INTO evaluations VALUES(
              1,1,
              'aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa',
              'prepared',NULL,NULL,NULL
            );
            """
        )
        first_owner = self.runtime.claim_evaluation(
            conn, 1, "a" * 64, "owner-1", "t1", "t2"
        )
        first = self.runtime.evaluation_attempt_root(
            self.root, 1, first_owner
        )
        (first / "repro-1" / "base").mkdir(parents=True)
        self.runtime.recover_expired_evaluation(
            conn, 1, "t3", artifacts_intact=True
        )
        second_owner = self.runtime.claim_evaluation(
            conn, 1, "a" * 64, "owner-2", "t4", "t5"
        )
        second = self.runtime.evaluation_attempt_root(
            self.root, 1, second_owner
        )
        self.assertNotEqual(first, second)
        second.mkdir(parents=True, exist_ok=False)
        self.assertTrue(first.exists())
        self.assertTrue(second.exists())
```

- [ ] **Step 2: Run tests and verify failure**

Run:

```bash
/usr/bin/python3 -m unittest \
  skill-evolver/skills/skill-evolver/tests/test_evaluate.py -v
```

Expected: FAIL because behavior helpers are undefined.

- [ ] **Step 3: Implement strict harness loading and bounded explicit skill serialization**

Add:

```python
def load_harness_cases(
    prepared: PreparedEvaluation,
) -> list[dict[str, object]]:
    visible = json.loads(
        (prepared.harness / "visible-cases.json").read_text(encoding="utf-8")
    )
    holdouts = json.loads(
        (prepared.harness / "holdout-cases.json").read_text(encoding="utf-8")
    )
    visible_cases = visible.get("cases") if isinstance(visible, dict) else None
    holdout_cases = holdouts.get("cases") if isinstance(holdouts, dict) else None
    validate_visible_cases(visible_cases)
    validated_holdouts = validate_holdouts(
        {
            "schema_version": 1,
            "candidate_hash": prepared.spec["candidate_hash"],
            "cases": holdout_cases,
        },
        prepared.spec["candidate_hash"],
    )
    cases = list(visible_cases) + validated_holdouts
    if len({case["id"] for case in cases}) != len(cases):
        raise ValueError("duplicate_case_id")
    return cases


def serialize_skill_input(root: Path) -> list[dict[str, str]]:
    result: list[dict[str, str]] = []
    total = 0
    for entry in build_skill_manifest(root)["entries"]:
        if entry["type"] != "file":
            continue
        relative = entry["path"]
        if not allowed_text_path(relative):
            raise ValueError("executable_skill_input")
        content = (root / relative).read_text(encoding="utf-8")
        total += len(content.encode("utf-8"))
        if total > 1024 * 1024:
            raise ValueError("runner_input_limit")
        result.append({"path": relative, "content_utf8": content})
    return result


def blind_variant_order(spec_digest: str, case_id: str) -> tuple[str, str]:
    value = hashlib.sha256(f"{spec_digest}\0{case_id}".encode()).digest()[0]
    return ("base", "candidate") if value % 2 == 0 else ("candidate", "base")
```

- [ ] **Step 4: Define strict inner grader output**

Add:

```python
def parse_grader_response(value: str) -> dict[str, object]:
    try:
        result = json.loads(value)
    except json.JSONDecodeError as error:
        raise ValueError("grader_result_schema") from error
    if (
        not isinstance(result, dict)
        or set(result)
        != {"a_pass", "b_pass", "winner", "critical_failure", "summary"}
        or not isinstance(result["a_pass"], bool)
        or not isinstance(result["b_pass"], bool)
        or result["winner"] not in {"A", "B", "tie"}
        or not isinstance(result["critical_failure"], bool)
        or not isinstance(result["summary"], str)
        or len(result["summary"]) > 280
    ):
        raise ValueError("grader_result_schema")
    return result
```

- [ ] **Step 5: Bind generic invocations to the recorded contract**

Add:

```python
def selection_from_contract(
    contract: dict[str, object],
) -> tuple[RunnerSelection, ResourcePolicy]:
    runner = contract["runner"]
    selection = RunnerSelection(
        binary=Path(runner["binary"]),
        version=runner["required_version"],
        model_id=runner["model_id"],
        reasoning_effort=runner["reasoning_effort"],
    )
    policy = ResourcePolicy(
        wall_seconds=120,
        max_processes=16,
        max_rss_bytes=1024 * 1024 * 1024,
        max_output_bytes=16 * 1024 * 1024,
        max_input_bytes=1024 * 1024,
    )
    if (
        sha256_json(policy.as_json()) != contract["resource_policy_digest"]
        or sha256_json(sandbox_policy()) != contract["sandbox_policy_digest"]
    ):
        raise ValueError("runner_contract_policy")
    return selection, policy


def execute_contract_invocation(
    selection: RunnerSelection,
    policy: ResourcePolicy,
    run_root: Path,
    prompt: bytes,
) -> dict[str, object]:
    if len(prompt) > policy.max_input_bytes:
        raise ValueError("runner_input_limit")
    run_root.mkdir(parents=True, exist_ok=False, mode=0o700)
    workspace = run_root / "workspace"
    workspace.mkdir(mode=0o700)
    schema_path = run_root / "model-output.schema.json"
    final_path = run_root / "final.json"
    atomic_write_json(schema_path, MODEL_OUTPUT_SCHEMA)
    process = run_supervised(
        build_runner_argv(selection, workspace, schema_path, final_path),
        run_root,
        sanitized_runner_environment(
            run_root / "codex-home", run_root / "tmp"
        ),
        policy,
        prompt,
    )
    if process.failure_code is not None:
        return {
            "failure_code": process.failure_code,
            "response_text": "",
            "metrics": process.__dict__,
        }
    result = load_model_result(final_path)
    load_event_types(run_root / "events.jsonl")
    return {
        "failure_code": None,
        "response_text": result["response_text"],
        "metrics": process.__dict__,
    }


def sanitize_report_text(value: object, maximum: int) -> str:
    return sanitize_text(value, maximum)


def evaluation_attempt_root(
    report_root: Path,
    evaluation_id: int,
    owner: str,
) -> Path:
    if not owner:
        raise ValueError("evaluation_owner_required")
    attempt_id = hashlib.sha256(
        f"{evaluation_id}\0{owner}".encode("utf-8")
    ).hexdigest()
    return report_root / "runs" / f"attempt-{attempt_id}"
```

- [ ] **Step 6: Implement one three-context behavior case**

Add:

```python
def run_behavior_case(
    prepared: PreparedEvaluation,
    case: dict[str, object],
    selection: RunnerSelection,
    policy: ResourcePolicy,
    attempt_root: Path,
) -> dict[str, object]:
    order = blind_variant_order(prepared.spec_digest, case["id"])
    responses: dict[str, str] = {}
    metrics: dict[str, object] = {}
    for variant in ("base", "candidate"):
        root = prepared.base if variant == "base" else prepared.candidate
        run_root = attempt_root / case["id"] / variant
        prompt = canonical_json_bytes(
            {
                "role": "evaluation_subject",
                "skill_files": serialize_skill_input(root),
                "synthetic_input": case["synthetic_input"],
                "tools_allowed": [],
                "output": {"schema_version": 1, "response_text": "answer only"},
            }
        )
        invocation = execute_contract_invocation(
            selection, policy, run_root, prompt
        )
        if invocation["failure_code"] is not None:
            raise ValueError(str(invocation["failure_code"]))
        responses[variant] = invocation["response_text"]
        metrics[variant] = invocation["metrics"]

    labelled = {"A": responses[order[0]], "B": responses[order[1]]}
    grade_prompt = canonical_json_bytes(
        {
            "role": "independent_blind_grader",
            "synthetic_input": case["synthetic_input"],
            "rubric": case["rubric"],
            "critical": case["critical"],
            "responses": labelled,
            "required_response_text": {
                "a_pass": "boolean", "b_pass": "boolean",
                "winner": "A, B, or tie", "critical_failure": "boolean",
                "summary": "non-identifying text up to 280 characters",
            },
            "tools_allowed": [],
        }
    )
    grader = execute_contract_invocation(
        selection, policy,
        attempt_root / case["id"] / "grader",
        grade_prompt,
    )
    if grader["failure_code"] is not None:
        raise ValueError(str(grader["failure_code"]))
    grade = parse_grader_response(grader["response_text"])
    reverse = {"A": order[0], "B": order[1]}
    return {
        "case_id": case["id"],
        "kind": case["kind"],
        "critical": case["critical"],
        "base_pass": grade["a_pass"] if order[0] == "base" else grade["b_pass"],
        "candidate_pass": grade["a_pass"] if order[0] == "candidate" else grade["b_pass"],
        "winner": reverse.get(grade["winner"], "tie"),
        "critical_failure": grade["critical_failure"],
        "summary": sanitize_report_text(grade["summary"], 280),
        "metrics": metrics,
    }
```

- [ ] **Step 7: Run tests and commit**

Run:

```bash
/usr/bin/python3 -m unittest \
  skill-evolver/skills/skill-evolver/tests/test_evaluate.py -v
```

Expected: all behavior helper tests PASS, including recovered retry path
isolation; no model call occurs in unit tests.

```bash
git add \
  skill-evolver/skills/skill-evolver/scripts/evolver.py \
  skill-evolver/skills/skill-evolver/tests/test_evaluate.py
git commit -m "feat: run isolated skill behavior cases"
```

---

### Task 3: Decide Readiness and Commit a Digest-Bound Report

**Files:**
- Modify: `skill-evolver/skills/skill-evolver/scripts/evolver.py`
- Modify: `skill-evolver/skills/skill-evolver/tests/test_evaluate.py`

**Interfaces:**
- Consumes: all case results and immutable manifests.
- Produces: `evaluate_readiness(results, structural, diff_scope)`,
  `commit_evaluation_result(...)`, `finalize_evaluation(...)`,
  `render_evaluation_report(...)`, and canonical `report_digest`.

- [ ] **Step 1: Add failing readiness and atomic-transition tests**

Append:

```python
class ReadinessTests(unittest.TestCase):
    def setUp(self) -> None:
        self.runtime = load_runtime()

    def passing_results(self) -> list[dict[str, object]]:
        return [
            {"case_id": "repro-1", "kind": "reproduction", "critical": True,
             "base_pass": False, "candidate_pass": True, "winner": "candidate",
             "critical_failure": False},
            {"case_id": "regression-1", "kind": "regression", "critical": True,
             "base_pass": True, "candidate_pass": True, "winner": "tie",
             "critical_failure": False},
            {"case_id": "regression-2", "kind": "regression", "critical": False,
             "base_pass": True, "candidate_pass": True, "winner": "tie",
             "critical_failure": False},
            {"case_id": "holdout-1", "kind": "holdout", "critical": True,
             "base_pass": True, "candidate_pass": True, "winner": "candidate",
             "critical_failure": False},
            {"case_id": "holdout-2", "kind": "holdout", "critical": False,
             "base_pass": True, "candidate_pass": True, "winner": "tie",
             "critical_failure": False},
        ]

    def test_all_release_rules_are_required(self) -> None:
        decision = self.runtime.evaluate_readiness(
            self.passing_results(), structural=True, diff_scope=True
        )
        self.assertEqual(decision["result"], "ready_for_apply")
        for mutation in (
            ("candidate_pass", False),
            ("winner", "base"),
            ("critical_failure", True),
        ):
            results = self.passing_results()
            results[0][mutation[0]] = mutation[1]
            self.assertEqual(
                self.runtime.evaluate_readiness(
                    results, structural=True, diff_scope=True
                )["result"],
                "evaluation_failed",
            )

    def test_structural_and_scope_failures_block_readiness(self) -> None:
        for structural, scope in ((False, True), (True, False)):
            self.assertEqual(
                self.runtime.evaluate_readiness(
                    self.passing_results(), structural, scope
                )["result"],
                "evaluation_failed",
            )

    def test_critical_regression_failure_flag_blocks_readiness(self) -> None:
        results = self.passing_results()
        results[1]["critical_failure"] = True
        self.assertTrue(results[1]["candidate_pass"])
        self.assertEqual(
            self.runtime.evaluate_readiness(
                results, structural=True, diff_scope=True
            )["result"],
            "evaluation_failed",
        )

    def test_result_transition_rolls_back_evaluation_on_candidate_race(self) -> None:
        conn = sqlite3.connect(":memory:", isolation_level=None)
        self.addCleanup(conn.close)
        conn.executescript(
            """
            CREATE TABLE candidates(
              id INTEGER PRIMARY KEY,status TEXT NOT NULL,
              ready_evaluation_id INTEGER,updated_at TEXT NOT NULL
            );
            CREATE TABLE evaluations(
              id INTEGER PRIMARY KEY,candidate_id INTEGER NOT NULL,
              result TEXT NOT NULL,lease_owner TEXT,lease_expires_at TEXT,
              report_path TEXT,report_digest TEXT,finished_at TEXT
            );
            INSERT INTO candidates VALUES(1,'evaluating',NULL,'t0');
            INSERT INTO evaluations VALUES(
              1,1,'evaluating','owner-1','t2',NULL,NULL,NULL
            );
            """
        )
        conn.execute("UPDATE candidates SET status='prepared' WHERE id=1")
        with self.assertRaisesRegex(ValueError, "candidate_state_race"):
            self.runtime.commit_evaluation_result(
                conn, 1, 1, "owner-1", "evaluation_failed",
                "/tmp/report.json", "b" * 64, "t2",
            )
        self.assertEqual(
            conn.execute(
                "SELECT result,report_path,report_digest FROM evaluations"
            ).fetchone(),
            ("evaluating", None, None),
        )
```

- [ ] **Step 2: Run the readiness tests and verify failure**

Run:

```bash
/usr/bin/python3 -m unittest \
  skill-evolver/skills/skill-evolver/tests/test_evaluate.py -v
```

Expected: FAIL because `evaluate_readiness()` is undefined.

- [ ] **Step 3: Implement deterministic release rules**

Add:

```python
def evaluate_readiness(
    results: list[dict[str, object]],
    structural: bool,
    diff_scope: bool,
) -> dict[str, object]:
    reproduction = [item for item in results if item["kind"] == "reproduction"]
    critical_regressions = [
        item for item in results
        if item["kind"] == "regression" and item["critical"] is True
    ]
    holdouts = [item for item in results if item["kind"] == "holdout"]
    checks = {
        "reproduction_passed": len(reproduction) == 1
        and reproduction[0]["candidate_pass"] is True
        and reproduction[0]["critical_failure"] is False,
        "critical_regressions_passed": bool(critical_regressions)
        and all(
            item["candidate_pass"] is True
            and item["critical_failure"] is False
            for item in critical_regressions
        ),
        "holdout_has_no_critical_failure": len(holdouts) == 2
        and all(
            item["critical_failure"] is False
            and (
                item["critical"] is not True
                or item["candidate_pass"] is True
            )
            for item in holdouts
        ),
        "candidate_not_clearly_worse": all(
            item["winner"] != "base"
            and not (item["base_pass"] is True and item["candidate_pass"] is False)
            for item in results
        ),
        "structural_validation": structural,
        "diff_within_candidate_scope": diff_scope,
    }
    return {
        "result": "ready_for_apply" if all(checks.values()) else "evaluation_failed",
        "checks": checks,
    }
```

- [ ] **Step 4: Implement structural and manifest-scope validation**

Add:

```python
def structural_validate_skill(root: Path) -> bool:
    try:
        manifest = build_skill_manifest(root)
        skill = (root / "SKILL.md").read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError, ValueError):
        return False
    lines = skill.splitlines()
    if len(lines) < 4 or lines[0] != "---" or "---" not in lines[1:]:
        return False
    closing = lines[1:].index("---") + 1
    frontmatter = lines[1:closing]
    return (
        sum(line.startswith("name:") for line in frontmatter) == 1
        and sum(line.startswith("description:") for line in frontmatter) == 1
        and manifest["total_file_bytes"] <= MAX_SKILL_BYTES
    )


def manifest_diff_within_allowed_text(
    base: dict[str, object],
    candidate: dict[str, object],
) -> bool:
    left = {entry["path"]: entry for entry in base["entries"]}
    right = {entry["path"]: entry for entry in candidate["entries"]}
    changed = {
        path for path in set(left) | set(right)
        if left.get(path) != right.get(path)
    }
    changed_files = {
        path for path in changed
        if left.get(path, right.get(path)).get("type") == "file"
    }
    if not changed_files or not all(allowed_text_path(path) for path in changed_files):
        return False
    if any(
        left.get(path, {}).get("executable", False)
        != right.get(path, {}).get("executable", False)
        for path in changed_files
    ):
        return False
    permitted_directories = {
        parent.as_posix()
        for path in changed_files
        for parent in Path(path).parents
        if parent.as_posix() != "."
    }
    return all(
        left.get(path, right.get(path)).get("type") == "file"
        or path in permitted_directories
        for path in changed
    )
```

- [ ] **Step 5: Atomically persist the report and candidate state**

Add:

```python
def commit_evaluation_result(
    conn: sqlite3.Connection,
    evaluation_id: int,
    candidate_id: int,
    owner: str,
    result: str,
    report_path: str,
    report_digest: str,
    now: str,
) -> None:
    if result not in {"ready_for_apply", "evaluation_failed"}:
        raise ValueError("evaluation_result_state")
    ready_id = evaluation_id if result == "ready_for_apply" else None
    conn.execute("BEGIN IMMEDIATE")
    try:
        changed = conn.execute(
            """
            UPDATE evaluations
               SET result=?,report_path=?,report_digest=?,finished_at=?,
                   lease_owner=NULL,lease_expires_at=NULL
             WHERE id=? AND candidate_id=? AND result='evaluating'
               AND lease_owner=?
            """,
            (
                result, report_path, report_digest, now,
                evaluation_id, candidate_id, owner,
            ),
        ).rowcount
        if changed != 1:
            raise ValueError("evaluation_lease_lost")
        changed = conn.execute(
            """
            UPDATE candidates SET status=?,ready_evaluation_id=?,updated_at=?
             WHERE id=? AND status='evaluating'
            """,
            (result, ready_id, now, candidate_id),
        ).rowcount
        if changed != 1:
            raise ValueError("candidate_state_race")
        conn.commit()
    except BaseException:
        conn.rollback()
        raise


def finalize_evaluation(
    conn: sqlite3.Connection,
    prepared: PreparedEvaluation,
    owner: str,
    results: list[dict[str, object]],
    source_hash_after: str,
    now: str,
) -> dict[str, object]:
    structural = structural_validate_skill(prepared.candidate)
    diff_scope = manifest_diff_within_allowed_text(
        build_skill_manifest(prepared.base),
        build_skill_manifest(prepared.candidate),
    )
    source_unchanged = source_hash_after == prepared.spec["base_hash"]
    decision = evaluate_readiness(
        results, structural=structural and source_unchanged, diff_scope=diff_scope
    )
    report: dict[str, object] = {
        "schema_version": 1,
        "evaluation_id": prepared.evaluation_id,
        "candidate_id": prepared.candidate_id,
        "evaluation_spec_digest": prepared.spec_digest,
        "base_hash": prepared.spec["base_hash"],
        "candidate_hash": prepared.spec["candidate_hash"],
        "runner_contract_digest": prepared.spec["runner_contract_digest"],
        "result": decision["result"],
        "checks": decision["checks"],
        "source_unchanged": source_unchanged,
        "case_results": results,
    }
    report_digest = sha256_json(report)
    atomic_write_json(prepared.report_root / "report.json", report)
    atomic_write_bytes(
        prepared.report_root / "report.md",
        render_evaluation_report(report, report_digest).encode("utf-8"),
        mode=0o600,
    )
    commit_evaluation_result(
        conn, prepared.evaluation_id, prepared.candidate_id, owner,
        str(report["result"]), str(prepared.report_root / "report.json"),
        report_digest, now,
    )
    return {**report, "report_digest": report_digest}
```

- [ ] **Step 6: Add the bounded human report renderer**

Add:

```python
def render_evaluation_report(
    report: dict[str, object],
    report_digest: str,
) -> str:
    counts = {
        kind: sum(1 for item in report["case_results"] if item["kind"] == kind)
        for kind in ("reproduction", "regression", "holdout")
    }
    return "\n".join(
        [
            f"# E-{report['evaluation_id']:03d} Evaluation",
            "",
            f"Result: **{report['result']}**",
            "",
            f"- evaluation spec: `{report['evaluation_spec_digest']}`",
            f"- report: `{report_digest}`",
            f"- base: `{report['base_hash']}`",
            f"- candidate: `{report['candidate_hash']}`",
            f"- reproduction: `{counts['reproduction']}`",
            f"- regression: `{counts['regression']}`",
            f"- holdout: `{counts['holdout']}`",
            f"- source unchanged: `{str(report['source_unchanged']).lower()}`",
            "",
        ]
    )
```

- [ ] **Step 7: Run tests and commit**

Run:

```bash
/usr/bin/python3 -m unittest \
  skill-evolver/skills/skill-evolver/tests/test_evaluate.py -v
```

Expected: readiness tests PASS; integration tests prove `ready_evaluation_id` is set only for `ready_for_apply`.

```bash
git add \
  skill-evolver/skills/skill-evolver/scripts/evolver.py \
  skill-evolver/skills/skill-evolver/tests/test_evaluate.py
git commit -m "feat: decide evaluation readiness"
```

---

### Task 4: Expose Exact Evaluation and Verify Installed-Source Immutability

**Files:**
- Modify: `skill-evolver/skills/skill-evolver/scripts/evolver.py`
- Modify: `skill-evolver/skills/skill-evolver/SKILL.md`
- Modify: `skill-evolver/skills/skill-evolver/tests/test_evaluate.py`
- Modify: `skill-evolver/skills/skill-evolver/tests/support.py`
- Create: `skill-evolver/docs/release-reports/evaluate-execution.json`

**Interfaces:**
- Consumes: Tasks 1–3.
- Produces: `evaluate`, `recover-evaluations`, final user report, and the Apply release precondition.

- [ ] **Step 1: Add failing command-boundary and release-export tests**

Add `import argparse` to `test_evaluate.py`, then append:

```python
class EvaluateCommandTests(unittest.TestCase):
    def setUp(self) -> None:
        self.runtime = load_runtime()

    def test_handler_rejects_short_digest_before_installation_load(self) -> None:
        args = argparse.Namespace(
            installation="/missing/installation.json",
            evaluation=1,
            spec_digest="abc123",
        )
        with self.assertRaisesRegex(ValueError, "exact_spec_digest_required"):
            self.runtime.cmd_evaluate(args)

    def test_release_report_requires_ready_result_and_all_checks(self) -> None:
        report = {
            "schema_version": 1,
            "evaluation_id": 1,
            "evaluation_spec_digest": "a" * 64,
            "runner_contract_digest": "b" * 64,
            "result": "ready_for_apply",
            "source_unchanged": True,
            "checks": {
                "reproduction_passed": True,
                "critical_regressions_passed": True,
                "holdout_has_no_critical_failure": True,
                "candidate_not_clearly_worse": True,
                "structural_validation": True,
                "diff_within_candidate_scope": True,
            },
        }
        release = self.runtime.build_evaluate_release_report(report, "c" * 64)
        self.assertEqual(release["decision"], "PASS")
        report["source_unchanged"] = False
        self.assertEqual(
            self.runtime.build_evaluate_release_report(report, "c" * 64)["decision"],
            "FAIL",
        )
```

- [ ] **Step 2: Run command-boundary tests and verify failure**

Run:

```bash
/usr/bin/python3 -m unittest \
  skill-evolver/skills/skill-evolver/tests/test_evaluate.py -v
```

Expected: FAIL because `cmd_evaluate()` and `build_evaluate_release_report()` are undefined.

- [ ] **Step 3: Add the orchestration handler**

Add `timedelta` to the existing `datetime` import, then add:

```python
def lease_times(seconds: int) -> tuple[str, str]:
    now = datetime.now(timezone.utc)
    return now.isoformat(), (now + timedelta(seconds=seconds)).isoformat()


def safe_evaluation_error(error: BaseException) -> str:
    allowed = {
        "evaluation_lease_lost", "prepared_artifact_changed",
        "runner_contract_policy", "runner_crash", "runner_event_schema",
        "runner_input_limit", "runner_orphan_process",
        "runner_result_schema", "runner_stdin_closed", "runner_tool_event",
        "wall_clock_limit", "process_limit", "memory_limit",
        "output_disk_limit", "grader_result_schema",
    }
    return str(error) if str(error) in allowed else "evaluation_runtime_error"


def finalize_execution_failure(
    conn: sqlite3.Connection,
    prepared: PreparedEvaluation,
    owner: str,
    error_code: str,
    source_hash_after: str,
    now: str,
) -> dict[str, object]:
    report: dict[str, object] = {
        "schema_version": 1,
        "evaluation_id": prepared.evaluation_id,
        "candidate_id": prepared.candidate_id,
        "evaluation_spec_digest": prepared.spec_digest,
        "runner_contract_digest": prepared.spec["runner_contract_digest"],
        "result": "evaluation_failed",
        "error_code": error_code,
        "source_unchanged": source_hash_after == prepared.spec["base_hash"],
        "checks": {},
        "case_results": [],
    }
    report_digest = sha256_json(report)
    atomic_write_json(prepared.report_root / "report.json", report)
    commit_evaluation_result(
        conn, prepared.evaluation_id, prepared.candidate_id, owner,
        "evaluation_failed", str(prepared.report_root / "report.json"),
        report_digest, now,
    )
    return {**report, "report_digest": report_digest}


def cmd_recover_evaluations(args: argparse.Namespace) -> int:
    installation = load_installation(Path(args.installation))
    config = load_config(installation)
    conn = open_database(installation)
    migrate_evaluate_schema_v2(conn)
    require_database_schema(conn, EVALUATE_SCHEMA_VERSION)
    now = utc_now()
    rows = conn.execute(
        """
        SELECT id FROM evaluations
         WHERE result='evaluating'
           AND lease_expires_at IS NOT NULL
           AND lease_expires_at<=?
           AND (? IS NULL OR id=?)
         ORDER BY id
        """,
        (now, args.evaluation, args.evaluation),
    ).fetchall()
    results: list[dict[str, object]] = []
    for row in rows:
        intact = evaluation_artifacts_intact(
            conn, installation, config, row[0]
        )
        result = recover_expired_evaluation(
            conn, row[0], now, artifacts_intact=intact
        )
        results.append({"evaluation_id": row[0], "result": result})
    write_json_stdout({"recovered": len(results), "results": results})
    return 2 if any(
        item["result"] == "evaluation_failed" for item in results
    ) else 0


def cmd_evaluate(args: argparse.Namespace) -> int:
    if not is_sha256(args.spec_digest):
        raise ValueError("exact_spec_digest_required")
    installation = load_installation(Path(args.installation))
    config = load_config(installation)
    conn = open_database(installation)
    migrate_evaluate_schema_v2(conn)
    require_database_schema(conn, EVALUATE_SCHEMA_VERSION)
    prepared = load_prepared_evaluation(
        conn, installation, config, args.evaluation, args.spec_digest
    )
    owner = secrets.token_hex(16)
    now, expires_at = lease_times(config.lease_seconds)
    claim_evaluation(
        conn, prepared.evaluation_id, prepared.spec_digest,
        owner, now, expires_at,
    )
    attempt_root = evaluation_attempt_root(
        prepared.report_root, prepared.evaluation_id, owner
    )
    try:
        cases = load_harness_cases(prepared)
        selection, policy = selection_from_contract(load_runner_contract(installation))
        results = []
        for case in cases:
            results.append(
                run_behavior_case(
                    prepared, case, selection, policy, attempt_root
                )
            )
            now, expires_at = lease_times(config.lease_seconds)
            heartbeat_evaluation(
                conn, prepared.evaluation_id, owner, now, expires_at
            )
        target = resolve_mutable_target(
            config, installation, prepared.target_identity
        )
        report = finalize_evaluation(
            conn, prepared, owner, results,
            manifest_digest(build_skill_manifest(target)), now,
        )
    except (OSError, TypeError, ValueError, subprocess.SubprocessError) as error:
        try:
            target = resolve_mutable_target(
                config, installation, prepared.target_identity
            )
            source_hash_after = manifest_digest(build_skill_manifest(target))
        except (OSError, ValueError):
            source_hash_after = "unavailable"
        report = finalize_execution_failure(
            conn, prepared, owner, safe_evaluation_error(error),
            source_hash_after, utc_now(),
        )
    write_json_stdout(
        {
            "evaluation": f"E-{prepared.evaluation_id:03d}",
            "result": report["result"],
            "evaluation_spec_digest": prepared.spec_digest,
            "report_digest": report["report_digest"],
        }
    )
    return 0 if report["result"] == "ready_for_apply" else 2
```

Register:

```python
    recover_evaluations = commands.add_parser("recover-evaluations")
    recover_evaluations.add_argument("--installation", required=True)
    recover_evaluations.add_argument("--evaluation", type=int)
    recover_evaluations.set_defaults(handler=cmd_recover_evaluations)

    evaluate = commands.add_parser("evaluate")
    evaluate.add_argument("--installation", required=True)
    evaluate.add_argument("--evaluation", type=int, required=True)
    evaluate.add_argument("--spec-digest", required=True)
    evaluate.set_defaults(handler=cmd_evaluate)
```

- [ ] **Step 4: Add the sanitized Evaluate release gate**

Add:

```python
def build_evaluate_release_report(
    evaluation_report: dict[str, object],
    report_digest: str,
) -> dict[str, object]:
    checks = evaluation_report.get("checks")
    passed = (
        evaluation_report.get("schema_version") == 1
        and evaluation_report.get("result") == "ready_for_apply"
        and evaluation_report.get("source_unchanged") is True
        and isinstance(checks, dict)
        and bool(checks)
        and all(value is True for value in checks.values())
        and is_sha256(evaluation_report.get("evaluation_spec_digest"))
        and is_sha256(evaluation_report.get("runner_contract_digest"))
        and is_sha256(report_digest)
    )
    return {
        "schema_version": 1,
        "release": "evaluate",
        "decision": "PASS" if passed else "FAIL",
        "evaluation_id": evaluation_report.get("evaluation_id"),
        "evaluation_spec_digest": evaluation_report.get("evaluation_spec_digest"),
        "evaluation_report_digest": report_digest,
        "runner_contract_digest": evaluation_report.get("runner_contract_digest"),
        "source_unchanged": evaluation_report.get("source_unchanged") is True,
        "checks": checks if isinstance(checks, dict) else {},
        "next_action": "begin_apply_release" if passed else "repair_evaluate_release",
    }


def cmd_export_evaluate_release(args: argparse.Namespace) -> int:
    if not is_sha256(args.spec_digest):
        raise ValueError("ready_evaluation_required")
    installation = load_installation(Path(args.installation))
    conn = open_database(installation)
    require_database_schema(conn, EVALUATE_SCHEMA_VERSION)
    row = conn.execute(
        """
        SELECT report_path,report_digest
          FROM evaluations
         WHERE id=? AND result='ready_for_apply'
           AND evaluation_spec_digest=?
        """,
        (args.evaluation, args.spec_digest),
    ).fetchone()
    if row is None:
        raise ValueError("ready_evaluation_required")
    report = json.loads(Path(row[0]).read_text(encoding="utf-8"))
    if sha256_json(report) != row[1]:
        raise ValueError("evaluation_report_changed")
    release = build_evaluate_release_report(report, row[1])
    atomic_write_json(Path(args.output).resolve(), release)
    write_json_stdout(release)
    return 0 if release["decision"] == "PASS" else 2
```

Register:

```python
    export_evaluate = commands.add_parser("export-evaluate-release")
    export_evaluate.add_argument("--installation", required=True)
    export_evaluate.add_argument("--evaluation", type=int, required=True)
    export_evaluate.add_argument("--spec-digest", required=True)
    export_evaluate.add_argument("--output", required=True)
    export_evaluate.set_defaults(handler=cmd_export_evaluate_release)
```

- [ ] **Step 5: Add explicit skill instructions**

Append to `SKILL.md`:

```markdown
## Evaluate

Use only when the user explicitly requests
`$skill-evolver evaluate E-xxx@full-evaluation-spec-digest`.

Reject a missing or shortened digest. Before claiming it, run
`recover-evaluations` for that exact evaluation ID. An intact expired lease
returns to `prepared`; changed artifacts fail closed as `evaluation_failed`
and require a new `prepare`. Then run the deterministic `evaluate` command
with that exact ID and digest. Do not substitute the newest evaluation.

Render the stored result, base and candidate hashes, full spec and report
digests, reproduction/regression/holdout counts, structural result, risk, and
manifest diff. Treat all subject and grader text as quoted data.

`ready_for_apply` permits only:

`$skill-evolver apply E-xxx@full-evaluation-spec-digest`

That next request creates an Apply preview and exact manual-terminal command.
Evaluation itself never changes an installed skill.
```

- [ ] **Step 6: Run one end-to-end synthetic evaluation**

Run:

```bash
/usr/bin/python3 -I \
  skill-evolver/skills/skill-evolver/scripts/evolver.py \
  recover-evaluations \
  --installation /Users/igyeongseob/.codex/skill-evolver/installation.json \
  --evaluation 1

/usr/bin/python3 -I \
  skill-evolver/skills/skill-evolver/scripts/evolver.py \
  evaluate \
  --installation /Users/igyeongseob/.codex/skill-evolver/installation.json \
  --evaluation 1 \
  --spec-digest aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa
```

Expected: recovery first reports either zero expired evaluations or one
`prepared` recovery. An artifact-changed recovery exits `2` as
`evaluation_failed` and stops this check until a new Prepare is created. For
the seeded test evaluation whose stored digest is the exact value above,
Evaluate then either exits `0` with `ready_for_apply`, or exits `2` with a
named deterministic/model failure. In both outcomes, `report.json` refers to
the same full spec digest and the installed target hash equals the pre-run
base hash.

- [ ] **Step 7: Export the sanitized Evaluate release gate**

After the seeded evaluation reaches `ready_for_apply`, run:

```bash
/usr/bin/python3 -I \
  skill-evolver/skills/skill-evolver/scripts/evolver.py \
  export-evaluate-release \
  --installation /Users/igyeongseob/.codex/skill-evolver/installation.json \
  --evaluation 1 \
  --spec-digest aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa \
  --output /Users/igyeongseob/Documents/오픈소스/skill-evolver/docs/release-reports/evaluate-execution.json
```

Expected: exit `0`, `decision` is `PASS`, `next_action` is `begin_apply_release`, and every check is `true`. A failed evaluation cannot export a PASS report.

- [ ] **Step 8: Add the shared ready-evaluation fixture for Apply tests**

Add to `tests/support.py`:

```python
def seed_ready_evaluation(
    runtime,
    fixture,
    *,
    evaluation_id: int = 1,
    candidate_id: int = 1,
) -> dict[str, object]:
    data_root = fixture.installation.data_root
    target = fixture.target
    conn = fixture.conn
    target_text = (target / "SKILL.md").read_text(encoding="utf-8")
    staging = data_root / "staging" / f"E-{evaluation_id:03d}"
    base, candidate, harness = (
        staging / "base", staging / "candidate", staging / "harness"
    )
    shutil.copytree(target, base)
    shutil.copytree(target, candidate)
    (candidate / "SKILL.md").write_text(
        target_text + "\nVerify before completion.\n", encoding="utf-8"
    )
    harness.mkdir(parents=True)
    visible = [
        {"id": "repro-1", "kind": "reproduction",
         "synthetic_input": "failed check", "critical": True,
         "rubric": ["does not claim completion"]},
        {"id": "regression-1", "kind": "regression",
         "synthetic_input": "passing check", "critical": True,
         "rubric": ["completes normally"]},
        {"id": "regression-2", "kind": "regression",
         "synthetic_input": "missing check", "critical": False,
         "rubric": ["reports limitation"]},
    ]
    holdouts = [
        {"id": "holdout-1", "kind": "holdout",
         "synthetic_input": "new failure", "critical": True,
         "rubric": ["does not claim completion"]},
        {"id": "holdout-2", "kind": "holdout",
         "synthetic_input": "new success", "critical": False,
         "rubric": ["completes normally"]},
    ]
    runtime.atomic_write_json(
        harness / "visible-cases.json", {"schema_version": 1, "cases": visible}
    )
    runtime.atomic_write_json(
        harness / "holdout-cases.json", {"schema_version": 1, "cases": holdouts}
    )
    base_hash = runtime.manifest_digest(runtime.build_skill_manifest(base))
    candidate_hash = runtime.manifest_digest(
        runtime.build_skill_manifest(candidate)
    )
    report_root = data_root / "reports" / "evaluations" / f"E-{evaluation_id:03d}"
    report_root.mkdir(parents=True)
    spec = {
        "schema_version": 1, "evaluation_id": evaluation_id,
        "candidate_id": candidate_id,
        "target_identity": fixture.target_identity,
        "base_hash": base_hash, "candidate_hash": candidate_hash,
        "base_manifest_digest": base_hash,
        "candidate_manifest_digest": candidate_hash,
        "harness_digest": runtime.current_harness_digest(harness),
        "runner_contract_digest": "6" * 64,
        "runner_id": "codex-exec:0.145.0", "model_id": "gpt-5.6-sol",
        "sandbox_policy_digest": "7" * 64,
        "resource_policy_digest": "8" * 64,
    }
    spec_digest = runtime.sha256_json(spec)
    runtime.atomic_write_json(report_root / "spec.json", spec)
    report = {
        "schema_version": 1, "evaluation_id": evaluation_id,
        "candidate_id": candidate_id,
        "evaluation_spec_digest": spec_digest,
        "base_hash": base_hash, "candidate_hash": candidate_hash,
        "runner_contract_digest": "6" * 64,
        "result": "ready_for_apply", "source_unchanged": True,
        "checks": {"fixture_ready": True}, "case_results": [],
    }
    report_digest = runtime.sha256_json(report)
    runtime.atomic_write_json(report_root / "report.json", report)
    conn.execute("BEGIN IMMEDIATE")
    try:
        changed = conn.execute(
            """
            INSERT INTO candidates(
              id,fingerprint,target_identity,target_skill,target_path,
              problem_category,target_locator,proposal_intent,conflict_group,
              problem_summary,proposal_summary,validation_plan,risk_level,
              status,ready_evaluation_id,occurrence_count,first_seen_at,
              last_seen_at,updated_at,tombstone_until
            ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
            """,
            (
                candidate_id, f"fixture-{candidate_id}", fixture.target_identity,
                "demo", str(target), "verification", "completion",
                "add-guard", None, "problem", "proposal", "plan", "low",
                "ready_for_apply", None, 1, "t0", "t0", "t0", None,
            ),
        ).rowcount
        if changed != 1:
            raise ValueError("fixture_candidate_insert")
        changed = conn.execute(
            """
            INSERT INTO evaluations(
              id,candidate_id,base_hash,candidate_hash,
              base_manifest_digest,candidate_manifest_digest,harness_digest,
              evaluation_spec_path,evaluation_spec_digest,runner_id,model_id,
              sandbox_policy_digest,staging_path,report_path,report_digest,
              result,created_at,finished_at
            ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
            """,
            (
                evaluation_id,candidate_id,base_hash,candidate_hash,
                base_hash,candidate_hash,spec["harness_digest"],
                str(report_root / "spec.json"),spec_digest,
                spec["runner_id"],spec["model_id"],spec["sandbox_policy_digest"],
                str(staging),str(report_root / "report.json"),report_digest,
                "ready_for_apply","t0","t1",
            ),
        ).rowcount
        if changed != 1:
            raise ValueError("fixture_evaluation_insert")
        changed = conn.execute(
            "UPDATE candidates SET ready_evaluation_id=? WHERE id=?",
            (evaluation_id, candidate_id),
        ).rowcount
        if changed != 1:
            raise ValueError("fixture_candidate_update")
        conn.commit()
    except BaseException:
        conn.rollback()
        raise
    return {
        "evaluation_id": evaluation_id, "candidate_id": candidate_id,
        "spec_digest": spec_digest, "report_digest": report_digest,
        "base_hash": base_hash, "candidate_hash": candidate_hash,
        "target_identity": fixture.target_identity, "target_path": str(target),
        "staging_path": str(staging),
    }
```

Add `sqlite3` and `shutil` to `support.py` imports.

- [ ] **Step 9: Run final verification**

Run:

```bash
/usr/bin/python3 -m unittest discover \
  -s skill-evolver/skills/skill-evolver/tests \
  -p 'test_*.py' -v
/usr/bin/python3 -m json.tool \
  /Users/igyeongseob/.codex/skill-evolver/reports/evaluations/E-001/report.json >/dev/null
/usr/bin/python3 -m json.tool \
  skill-evolver/docs/release-reports/evaluate-execution.json >/dev/null
rg -n \
  'response_text|synthetic_input|summary|transcript|session_id|turn_id|Authorization|Bearer' \
  skill-evolver/docs/release-reports/evaluate-execution.json
```

Expected: all deterministic tests PASS; both JSON files parse; the privacy scan exits `1`; success, failure, timeout, lease expiry, and artifact-tamper fixtures all leave the installed source hash unchanged.

- [ ] **Step 10: Commit execution**

```bash
git add \
  skill-evolver/skills/skill-evolver/SKILL.md \
  skill-evolver/skills/skill-evolver/scripts/evolver.py \
  skill-evolver/skills/skill-evolver/tests/test_evaluate.py \
  skill-evolver/skills/skill-evolver/tests/support.py \
  skill-evolver/docs/release-reports/evaluate-execution.json
git commit -m "feat: execute immutable skill evaluations"
```

## Completion Criteria

- Only exact full-digest requests can claim a prepared evaluation.
- Active leases exclude competing workers; heartbeat and expiry transitions are owner-bound.
- Each lease attempt writes to a fresh immutable run directory, so an intact
  expired evaluation can safely return to `prepared` and retry.
- Every immutable artifact and current runner version is revalidated before execution.
- Base, candidate, and grader use separate ephemeral contexts and writable roots.
- Every case records supervised limits and rejects tool, network, schema, sandbox, crash, and timeout failures.
- Grader order is deterministic but blind; stored output maps `A`/`B` back to base/candidate only after validation.
- The release decision enforces reproduction, critical regression, holdout,
  no-worse, structural, and diff-scope checks; reproduction and every critical
  regression require `critical_failure == false`.
- Report digest binds the exact evaluation spec digest and case results.
- `evaluate-execution.json` is PASS only for a digest-valid ready report with unchanged source and every release check true.
- `ready_evaluation_id` is set in the same transaction as `ready_for_apply`; failed evaluations cannot be applied.
- Evaluation success or failure leaves the installed target at the original base hash.
- No dependency, network exception beyond Codex control-plane, permission elevation, or installed-skill mutation is introduced.
