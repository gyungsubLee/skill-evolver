# Skill Evolver Apply and Versioning Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Install one exact Evaluate-PASS skill artifact through a manual-terminal-only, full-digest, drift-safe atomic swap and record durable apply/version lineage.

**Architecture:** Extend the schema-v2 Evaluate runtime to schema v3 with `apply_operations` and `versions`. A non-mutating preview binds the full evaluation spec and report digests to one allowlisted target; an external TTY executor revalidates everything under a target lock, snapshots the base, prepares a sibling candidate, journals each durable rename, validates the installed hash, and only then records a version.

**Tech Stack:** macOS, Codex CLI and Desktop, `/usr/bin/python3` 3.9+, Python standard library (`argparse`, `dataclasses`, `fcntl`, `hashlib`, `hmac`, `json`, `os`, `pathlib`, `sqlite3`, `subprocess`, `unittest`), SQLite WAL, POSIX rename and directory `fsync`.

## Global Constraints

- Source specification: `skill-evolver/docs/superpowers/specs/2026-07-26-skill-evolver-design.md`.
- Start only after Feasibility, Read-only MVP, and Evaluate reports contain `decision: PASS`.
- Input schema is v2; this plan owns the exact v2→v3 migration and only the `apply_operations`, `versions`, and apply/undo indexes.
- Reuse Evaluate interfaces exactly: `build_skill_manifest`, `manifest_digest`, `resolve_mutable_target`, `allowed_text_path`, `structural_validate_skill`, `open_database`, `sha256_json`, and `tests/support.py::seed_ready_evaluation`.
- Runtime remains the single self-contained `skill-evolver/skills/skill-evolver/scripts/evolver.py`; `/usr/bin/python3 -I` 3.9+ and standard library only.
- Apply mode is exactly `manual_terminal`; the skill renders the command but never executes it or requests permission to execute it.
- Preview and executor require the exact `ready_evaluation_id` and complete 64-character lowercase evaluation-spec SHA-256. Prefixes are never accepted.
- Revalidate spec file, report file, base/candidate manifests, harness, runner, model, and sandbox digests before mutation.
- Resolve the target from the allowlisted catalog; never accept a target path from chat, transcript, report prose, or an apply argument.
- Acquire the target lock before final artifact, capacity, and base-drift checks.
- Snapshot and prepared trees must match their manifests and be recursively synced before target mutation.
- Prepared and rollback paths are siblings of the target, guaranteeing same-filesystem renames.
- Require peak bytes plus `max(10%, 1 MiB)` free-space margin on both target and data filesystems.
- Successful apply requires the installed content and manifest hashes to equal the evaluated candidate.
- General apply supports only the non-executable text candidate accepted by Evaluate. Script, Hook, dependency, permission, or security-boundary changes remain rejected.
- Directories in the data root use mode `0700`; files use `0600`; target file modes come only from the candidate manifest.
- Do not create Git commits, pushes, pull requests, dependencies, network calls, or production actions automatically.
- Commit steps stage only exact Skill Evolver paths; never run `git add .`.

## Execution Preconditions

- [ ] **Verify all upstream gates and baseline tests**

Run:

```bash
/usr/bin/python3 - <<'PY'
import json
from pathlib import Path

paths = (
    Path("skill-evolver/docs/feasibility-report.json"),
    Path("skill-evolver/docs/release-reports/read-only-quality-gate.json"),
    Path("skill-evolver/docs/release-reports/evaluate-runner-spike.json"),
    Path("skill-evolver/docs/release-reports/evaluate-execution.json"),
)
for path in paths:
    if json.loads(path.read_text(encoding="utf-8")).get("decision") != "PASS":
        raise SystemExit(f"gate_not_passed:{path}")
print("upstream gates: PASS")
PY
/usr/bin/python3 -m unittest discover \
  -s skill-evolver/skills/skill-evolver/tests \
  -p 'test_*.py' -v
```

Expected: `upstream gates: PASS`; every existing test PASS. Otherwise stop before schema v3.

## File Structure

| Path | Responsibility |
| --- | --- |
| `skill-evolver/skills/skill-evolver/scripts/evolver.py` | Schema v3, exact evaluation selection, preview, TTY confirmation, lock, capacity, snapshot, journal, atomic swap, validation, and versions. |
| `skill-evolver/skills/skill-evolver/tests/test_apply.py` | Focused migration, digest, preview, drift, filesystem, swap, validation rollback, and lineage tests. |
| `skill-evolver/skills/skill-evolver/SKILL.md` | Explicit apply preview and manual-terminal boundary. |
| `skill-evolver/docs/release-reports/apply-versioning.json` | Deterministic Apply/Versioning release decision. |

---

### Task 1: Add Schema-v3 Apply Journal and Version Lineage

**Files:**
- Modify: `skill-evolver/skills/skill-evolver/scripts/evolver.py`
- Create: `skill-evolver/skills/skill-evolver/tests/test_apply.py`

**Interfaces:**
- Consumes: schema-v2 `candidates`, `evaluations`, `metadata`.
- Produces: `migrate_apply_schema_v3(conn)`, `create_apply_operation(conn, values)`, `transition_apply_operation(conn, operation_id, expected, desired)`, `record_apply_version(conn, values)`.

- [ ] **Step 1: Write the failing migration tests**

Create `test_apply.py`:

```python
from __future__ import annotations

import inspect
import sqlite3
import tempfile
import unittest
from pathlib import Path

from support import load_runtime


def v2_database(path: Path) -> sqlite3.Connection:
    conn = sqlite3.connect(path)
    conn.execute("PRAGMA foreign_keys = ON")
    conn.executescript(
        """
        CREATE TABLE candidates (
            id INTEGER PRIMARY KEY,
            target_identity TEXT NOT NULL,
            target_skill TEXT NOT NULL,
            target_path TEXT NOT NULL,
            status TEXT NOT NULL,
            ready_evaluation_id INTEGER,
            updated_at TEXT
        );
        CREATE TABLE evaluations (
            id INTEGER PRIMARY KEY,
            candidate_id INTEGER NOT NULL REFERENCES candidates(id),
            base_hash TEXT, candidate_hash TEXT,
            base_manifest_digest TEXT, candidate_manifest_digest TEXT,
            harness_digest TEXT, evaluation_spec_path TEXT,
            evaluation_spec_digest TEXT UNIQUE, runner_id TEXT, model_id TEXT,
            sandbox_policy_digest TEXT, staging_path TEXT, report_path TEXT,
            report_digest TEXT, result TEXT NOT NULL
        );
        CREATE TABLE metadata (key TEXT PRIMARY KEY, value TEXT NOT NULL);
        PRAGMA user_version = 2;
        """
    )
    return conn


class ApplySchemaTests(unittest.TestCase):
    def setUp(self) -> None:
        self.runtime = load_runtime()
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.conn = v2_database(Path(self.temp.name) / "evolver.db")
        self.addCleanup(self.conn.close)

    def test_v2_migrates_to_v3_once(self) -> None:
        self.runtime.migrate_apply_schema_v3(self.conn)
        self.runtime.migrate_apply_schema_v3(self.conn)
        self.assertEqual(self.conn.execute("PRAGMA user_version").fetchone()[0], 3)
        names = {
            row[0]
            for row in self.conn.execute(
                "SELECT name FROM sqlite_master WHERE type IN ('table','index')"
            )
        }
        self.assertTrue(
            {
                "apply_operations",
                "versions",
                "idx_apply_operations_status_started",
                "idx_versions_target_created",
            }.issubset(names)
        )

    def test_skipped_or_newer_schema_fails_closed(self) -> None:
        for version in (1, 4):
            with self.subTest(version=version):
                self.conn.execute(f"PRAGMA user_version = {version}")
                with self.assertRaisesRegex(
                    ValueError, "apply_migration_requires_schema_v2"
                ):
                    self.runtime.migrate_apply_schema_v3(self.conn)

    def test_failed_migration_rolls_back_schema_and_version(self) -> None:
        self.conn.execute("CREATE TABLE apply_operations(id INTEGER PRIMARY KEY)")
        with self.assertRaises(sqlite3.OperationalError):
            self.runtime.migrate_apply_schema_v3(self.conn)
        self.assertEqual(self.conn.execute("PRAGMA user_version").fetchone()[0], 2)
        self.assertIsNone(
            self.conn.execute(
                "SELECT 1 FROM sqlite_master WHERE type='table' AND name='versions'"
            ).fetchone()
        )

    def test_v3_is_accepted_by_the_shared_database_opener(self) -> None:
        self.assertEqual(self.runtime.KNOWN_DATABASE_SCHEMAS, {1, 2, 3})
```

- [ ] **Step 2: Run the test and verify it fails**

Run:

```bash
/usr/bin/python3 -m unittest \
  skill-evolver/skills/skill-evolver/tests/test_apply.py -v
```

Expected: FAIL because `migrate_apply_schema_v3` is undefined.

- [ ] **Step 3: Implement the exact v3 migration**

Add to `evolver.py`:

```python
APPLY_SCHEMA_SQL = """
CREATE TABLE apply_operations (
    id INTEGER PRIMARY KEY,
    operation_kind TEXT NOT NULL,
    candidate_id INTEGER REFERENCES candidates(id),
    evaluation_id INTEGER REFERENCES evaluations(id),
    source_version_id INTEGER REFERENCES versions(id),
    target_identity TEXT NOT NULL,
    target_path TEXT NOT NULL,
    expected_current_hash TEXT NOT NULL,
    desired_hash TEXT NOT NULL,
    snapshot_path TEXT NOT NULL,
    prepared_path TEXT,
    rollback_path TEXT,
    status TEXT NOT NULL,
    error_code TEXT,
    started_at TEXT NOT NULL,
    finished_at TEXT,
    CHECK (
        (operation_kind = 'apply' AND candidate_id IS NOT NULL
         AND evaluation_id IS NOT NULL AND source_version_id IS NULL)
        OR
        (operation_kind = 'undo' AND candidate_id IS NULL
         AND evaluation_id IS NULL AND source_version_id IS NOT NULL)
    )
);
CREATE TABLE versions (
    id INTEGER PRIMARY KEY,
    skill_name TEXT NOT NULL,
    target_identity TEXT NOT NULL,
    target_path TEXT NOT NULL,
    content_hash TEXT NOT NULL,
    parent_hash TEXT,
    operation_id INTEGER NOT NULL UNIQUE REFERENCES apply_operations(id),
    candidate_id INTEGER REFERENCES candidates(id),
    evaluation_id INTEGER REFERENCES evaluations(id),
    snapshot_path TEXT,
    git_commit TEXT,
    event_type TEXT NOT NULL,
    created_at TEXT NOT NULL
);
CREATE INDEX idx_apply_operations_status_started
    ON apply_operations(status, started_at);
CREATE INDEX idx_versions_target_created
    ON versions(target_identity, created_at);
"""


def migrate_apply_schema_v3(conn: sqlite3.Connection) -> None:
    version = conn.execute("PRAGMA user_version").fetchone()[0]
    if version == 3:
        return
    if version != 2:
        raise ValueError("apply_migration_requires_schema_v2")
    try:
        conn.executescript(
            "BEGIN IMMEDIATE;\n"
            + APPLY_SCHEMA_SQL
            + "\nPRAGMA user_version = 3;\nCOMMIT;\n"
        )
    except BaseException:
        conn.rollback()
        raise


# Extend Evaluate/Prepare's fail-closed opener only after this migration exists.
KNOWN_DATABASE_SCHEMAS = {1, 2, 3}
```

Wire this after `migrate_evaluate_schema_v2(conn)` only when Apply release is enabled.

- [ ] **Step 4: Add compare-and-swap journal primitives**

Add:

```python
APPLY_STATES = {
    "preflight", "snapshot_ready", "prepared_ready", "swap_armed",
    "original_renamed", "candidate_installed", "validating", "applied",
    "preflight_failed", "rolled_back", "rollback_failed", "recovery_required",
}


def transition_apply_operation(
    conn: sqlite3.Connection,
    operation_id: int,
    expected: str,
    desired: str,
    error_code: Optional[str] = None,
    finished_at: Optional[str] = None,
) -> None:
    if expected not in APPLY_STATES or desired not in APPLY_STATES:
        raise ValueError("invalid_apply_journal_state")
    cursor = conn.execute(
        """
        UPDATE apply_operations
        SET status = ?, error_code = ?, finished_at = COALESCE(?, finished_at)
        WHERE id = ? AND status = ?
        """,
        (desired, error_code, finished_at, operation_id, expected),
    )
    if cursor.rowcount != 1:
        raise RuntimeError("apply_journal_compare_and_swap_failed")
```

Add the complete insert helpers:

```python
def create_apply_operation(
    conn: sqlite3.Connection,
    *,
    operation_id: int,
    operation_kind: str,
    candidate_id: Optional[int],
    evaluation_id: Optional[int],
    source_version_id: Optional[int],
    target_identity: str,
    target_path: Path,
    expected_current_hash: str,
    desired_hash: str,
    paths,
    started_at: str,
) -> int:
    conn.execute(
        """
        INSERT INTO apply_operations (
            id, operation_kind, candidate_id, evaluation_id, source_version_id,
            target_identity, target_path, expected_current_hash, desired_hash,
            snapshot_path, prepared_path, rollback_path, status, started_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'preflight', ?)
        """,
        (
            operation_id, operation_kind, candidate_id, evaluation_id,
            source_version_id, target_identity, str(target_path),
            expected_current_hash, desired_hash, str(paths.snapshot),
            str(paths.prepared), str(paths.rollback), started_at,
        ),
    )
    return operation_id


def record_apply_version(
    conn: sqlite3.Connection,
    *,
    operation_id: int,
    skill_name: str,
    target_identity: str,
    target_path: Path,
    parent_hash: str,
    content_hash: str,
    candidate_id: Optional[int],
    evaluation_id: Optional[int],
    snapshot_path: Path,
    event_type: str,
    created_at: str,
) -> int:
    if event_type not in {"apply", "undo"}:
        raise ValueError("invalid_version_event")
    cursor = conn.execute(
        """
        INSERT INTO versions (
            skill_name, target_identity, target_path, content_hash, parent_hash,
            operation_id, candidate_id, evaluation_id, snapshot_path,
            event_type, created_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            skill_name, target_identity, str(target_path), content_hash,
            parent_hash, operation_id, candidate_id, evaluation_id,
            str(snapshot_path), event_type, created_at,
        ),
    )
    return int(cursor.lastrowid)
```

- [ ] **Step 5: Run tests and commit**

Run:

```bash
/usr/bin/python3 -m unittest \
  skill-evolver/skills/skill-evolver/tests/test_apply.py -v
```

Expected: 2 tests PASS.

```bash
git add \
  skill-evolver/skills/skill-evolver/scripts/evolver.py \
  skill-evolver/skills/skill-evolver/tests/test_apply.py
git commit -m "feat: add apply journal schema"
```

---

### Task 2: Bind Preview and TTY Confirmation to One Exact Evaluate PASS

**Files:**
- Modify: `skill-evolver/skills/skill-evolver/scripts/evolver.py`
- Modify: `skill-evolver/skills/skill-evolver/tests/test_apply.py`
- Modify: `skill-evolver/skills/skill-evolver/tests/support.py`

**Interfaces:**
- Consumes: Evaluate manifest/catalog helpers and schema-v2 artifact layout `staging/E-xxx/{base,candidate,harness}`, `reports/evaluations/E-xxx/{spec.json,report.json}`.
- Produces: `VerifiedApply`, `require_full_sha256`, `load_verified_apply`, `manifest_diff`, `build_apply_preview`, `confirm_full_digest`.

- [ ] **Step 1: Add failing digest, diff, and TTY tests**

Append to `test_apply.py`:

```python
class ApplySelectionTests(unittest.TestCase):
    def setUp(self) -> None:
        self.runtime = load_runtime()

    def test_only_complete_lowercase_sha256_is_accepted(self) -> None:
        valid = "a" * 64
        self.assertEqual(self.runtime.require_full_sha256(valid, "spec"), valid)
        for invalid in ("a" * 12, "A" * 64, "g" * 64):
            with self.subTest(invalid=invalid):
                with self.assertRaisesRegex(ValueError, "invalid_spec_digest"):
                    self.runtime.require_full_sha256(invalid, "spec")

    def test_tty_requires_the_same_full_digest(self) -> None:
        digest = "b" * 64
        self.runtime.confirm_full_digest(digest, True, lambda _prompt: digest)
        with self.assertRaisesRegex(ValueError, "tty_required"):
            self.runtime.confirm_full_digest(digest, False, lambda _prompt: digest)
        with self.assertRaisesRegex(ValueError, "digest_confirmation_mismatch"):
            self.runtime.confirm_full_digest(digest, True, lambda _prompt: "b" * 63)

    def test_manifest_diff_covers_add_modify_delete_and_mode(self) -> None:
        base = {"entries": [
            {"path": "SKILL.md", "type": "file", "executable": False, "bytes": 3, "sha256": "1" * 64},
            {"path": "old.md", "type": "file", "executable": False, "bytes": 2, "sha256": "2" * 64},
        ]}
        candidate = {"entries": [
            {"path": "SKILL.md", "type": "file", "executable": True, "bytes": 4, "sha256": "3" * 64},
            {"path": "new.md", "type": "file", "executable": False, "bytes": 2, "sha256": "4" * 64},
        ]}
        self.assertEqual(
            self.runtime.manifest_diff(base, candidate),
            {
                "added": ["new.md"],
                "deleted": ["old.md"],
                "modified": ["SKILL.md"],
                "mode_changed": ["SKILL.md"],
            },
        )

    def test_apply_reuses_evaluate_harness_digest_contract(self) -> None:
        visible = {
            "schema_version": 1,
            "cases": [{"case_id": "visible-1"}],
        }
        holdout = {
            "schema_version": 1,
            "cases": [{"case_id": "holdout-1"}],
        }
        with tempfile.TemporaryDirectory() as name:
            harness = Path(name)
            self.runtime.atomic_write_json(
                harness / "visible-cases.json", visible
            )
            self.runtime.atomic_write_json(
                harness / "holdout-cases.json", holdout
            )
            self.assertEqual(
                self.runtime.current_harness_digest(harness),
                self.runtime.sha256_json({
                    "visible": visible,
                    "holdout": holdout,
                }),
            )
        source = inspect.getsource(self.runtime.load_verified_apply)
        self.assertIn("current_harness_digest(harness_path)", source)
        self.assertNotIn("sealed_harness_digest", source)
```

- [ ] **Step 2: Run tests and verify they fail**

Run:

```bash
/usr/bin/python3 -m unittest \
  skill-evolver/skills/skill-evolver/tests/test_apply.py -v
```

Expected: FAIL for undefined digest, TTY, and diff helpers.

- [ ] **Step 3: Implement strict digest, TTY, and diff helpers**

Add:

```python
def require_full_sha256(value: str, label: str) -> str:
    if len(value) != 64 or any(character not in "0123456789abcdef" for character in value):
        raise ValueError(f"invalid_{label}_digest")
    return value


def confirm_full_digest(expected: str, is_tty: bool, reader) -> None:
    expected = require_full_sha256(expected, "spec")
    if not is_tty:
        raise ValueError("tty_required")
    if not hmac.compare_digest(
        reader("Type the full evaluation spec SHA-256 to apply: ").strip(),
        expected,
    ):
        raise ValueError("digest_confirmation_mismatch")


def manifest_diff(base: dict, candidate: dict) -> dict:
    old = {entry["path"]: entry for entry in base["entries"]}
    new = {entry["path"]: entry for entry in candidate["entries"]}
    shared = sorted(set(old) & set(new))
    return {
        "added": sorted(set(new) - set(old)),
        "deleted": sorted(set(old) - set(new)),
        "modified": [
            path for path in shared
            if (
                old[path]["type"],
                old[path].get("bytes"),
                old[path].get("sha256"),
            )
            != (
                new[path]["type"],
                new[path].get("bytes"),
                new[path].get("sha256"),
            )
        ],
        "mode_changed": [
            path for path in shared
            if old[path].get("executable") != new[path].get("executable")
        ],
    }
```

- [ ] **Step 4: Implement exact evaluation loading**

Add the complete loader. Reuse Evaluate Execution's
`current_harness_digest(harness)`, whose contract hashes both complete wrapper
objects as `{"visible": visible, "holdout": holdout}`. Do not create an Apply
variant. Evaluate hashes canonical JSON and canonical manifests, not serialized
JSON file bytes:

```python
@dataclass(frozen=True)
class VerifiedApply:
    evaluation_id: int
    candidate_id: int
    skill_name: str
    target_identity: str
    target_path: Path
    base_hash: str
    candidate_hash: str
    base_manifest: dict
    candidate_manifest: dict
    candidate_path: Path
    evaluation_spec_digest: str
    report_digest: str


def load_json_object(path: Path, error: str) -> dict:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError(error)
    return value


def require_private_artifact(root: Path, path: Path, error: str) -> Path:
    if path.is_symlink():
        raise ValueError(error)
    canonical_root = root.resolve(strict=True)
    canonical = path.resolve(strict=True)
    try:
        canonical.relative_to(canonical_root)
    except ValueError:
        raise ValueError(error)
    return canonical


def load_verified_apply(
    conn: sqlite3.Connection,
    installation: Installation,
    config: Config,
    evaluation_id: int,
    spec_digest: str,
    recovery_operation_id: Optional[int] = None,
) -> VerifiedApply:
    requested = require_full_sha256(spec_digest, "spec")
    row = conn.execute(
        """
        SELECT e.*, c.target_identity, c.target_skill,
               c.status AS candidate_status, c.ready_evaluation_id
          FROM evaluations e JOIN candidates c ON c.id=e.candidate_id
         WHERE e.id=?
        """,
        (evaluation_id,),
    ).fetchone()
    if row is None or row["result"] != "ready_for_apply":
        raise ValueError("evaluation_not_ready_for_apply")
    normal_state = (
        recovery_operation_id is None
        and row["candidate_status"] == "ready_for_apply"
        and row["ready_evaluation_id"] == evaluation_id
    )
    recovery_state = False
    if recovery_operation_id is not None:
        operation = conn.execute(
            """
            SELECT id FROM apply_operations
             WHERE id=? AND operation_kind='apply' AND candidate_id=?
               AND evaluation_id=? AND status IN (
                 'swap_armed','original_renamed',
                 'candidate_installed','validating'
               )
            """,
            (
                recovery_operation_id, row["candidate_id"],
                evaluation_id,
            ),
        ).fetchone()
        recovery_state = (
            operation is not None
            and row["candidate_status"] == "applying"
            and row["ready_evaluation_id"] == evaluation_id
        )
    if (
        not (normal_state or recovery_state)
        or not hmac.compare_digest(
            str(row["evaluation_spec_digest"] or ""), requested
        )
    ):
        raise ValueError("evaluation_not_ready_for_apply")

    reports_root = installation.data_root / "reports" / "evaluations"
    staging_root = installation.data_root / "staging"
    spec_path = require_private_artifact(
        reports_root, Path(row["evaluation_spec_path"]), "invalid_spec_path"
    )
    report_path = require_private_artifact(
        reports_root, Path(row["report_path"]), "invalid_report_path"
    )
    staging = require_private_artifact(
        staging_root, Path(row["staging_path"]), "invalid_staging_path"
    )
    spec = load_json_object(spec_path, "invalid_evaluation_spec")
    report = load_json_object(report_path, "invalid_evaluation_report")
    report_digest = require_full_sha256(
        str(row["report_digest"] or ""), "report"
    )
    if (
        not hmac.compare_digest(sha256_json(spec), requested)
        or not hmac.compare_digest(sha256_json(report), report_digest)
    ):
        raise ValueError("evaluation_artifact_digest_mismatch")

    base_path = require_private_artifact(
        staging, staging / "base", "invalid_base_path"
    )
    candidate_path = require_private_artifact(
        staging, staging / "candidate", "invalid_candidate_path"
    )
    harness_path = require_private_artifact(
        staging, staging / "harness", "invalid_harness_path"
    )
    base_manifest = build_skill_manifest(base_path)
    candidate_manifest = build_skill_manifest(candidate_path)
    target = resolve_mutable_target(
        config, installation, str(row["target_identity"])
    )
    expected = {
        "evaluation_id": evaluation_id,
        "candidate_id": int(row["candidate_id"]),
        "target_identity": str(row["target_identity"]),
        "base_hash": manifest_digest(base_manifest),
        "candidate_hash": manifest_digest(candidate_manifest),
        "base_manifest_digest": manifest_digest(base_manifest),
        "candidate_manifest_digest": manifest_digest(candidate_manifest),
        "harness_digest": current_harness_digest(harness_path),
        "runner_id": row["runner_id"],
        "model_id": row["model_id"],
        "sandbox_policy_digest": row["sandbox_policy_digest"],
    }
    if any(spec.get(key) != value for key, value in expected.items()):
        raise ValueError("evaluation_spec_binding_mismatch")
    if any(row[key] != expected[key] for key in (
        "base_hash", "candidate_hash", "base_manifest_digest",
        "candidate_manifest_digest", "harness_digest", "runner_id",
        "model_id", "sandbox_policy_digest",
    )):
        raise ValueError("evaluation_row_binding_mismatch")
    if (
        report.get("evaluation_id") != evaluation_id
        or report.get("candidate_id") != row["candidate_id"]
        or report.get("evaluation_spec_digest") != requested
        or report.get("base_hash") != expected["base_hash"]
        or report.get("candidate_hash") != expected["candidate_hash"]
        or report.get("result") != "ready_for_apply"
    ):
        raise ValueError("evaluation_report_binding_mismatch")
    return VerifiedApply(
        evaluation_id=evaluation_id,
        candidate_id=int(row["candidate_id"]),
        skill_name=str(row["target_skill"]),
        target_identity=str(row["target_identity"]),
        target_path=target,
        base_hash=str(expected["base_hash"]),
        candidate_hash=str(expected["candidate_hash"]),
        base_manifest=base_manifest,
        candidate_manifest=candidate_manifest,
        candidate_path=candidate_path,
        evaluation_spec_digest=requested,
        report_digest=report_digest,
    )
```

- [ ] **Step 5: Build the non-mutating preview**

Implement:

```python
def build_apply_preview(
    conn, installation, config, evaluation_id: int, spec_digest: str,
    installation_path: Path, script_path: Path,
) -> dict:
    selected = load_verified_apply(
        conn, installation, config, evaluation_id, spec_digest
    )
    current = build_skill_manifest(selected.target_path)
    return {
        "schema_version": 1,
        "action": "apply_preview",
        "will_modify_files": False,
        "evaluation_id": selected.evaluation_id,
        "candidate_id": selected.candidate_id,
        "target_identity": selected.target_identity,
        "target_path": str(selected.target_path),
        "expected_current_hash": selected.base_hash,
        "desired_hash": selected.candidate_hash,
        "evaluation_spec_digest": selected.evaluation_spec_digest,
        "report_digest": selected.report_digest,
        "drifted": manifest_digest(current) != selected.base_hash,
        "diff": manifest_diff(selected.base_manifest, selected.candidate_manifest),
        "manual_command": [
            "/usr/bin/python3", "-I", str(script_path.resolve(strict=True)), "apply",
            "--installation", str(installation_path.resolve(strict=True)),
            "--evaluation", str(selected.evaluation_id),
            "--spec-digest", selected.evaluation_spec_digest,
        ],
    }
```

- [ ] **Step 6: Run focused/full tests and commit**

Run:

```bash
/usr/bin/python3 -m unittest \
  skill-evolver/skills/skill-evolver/tests/test_apply.py -v
/usr/bin/python3 -m unittest discover \
  -s skill-evolver/skills/skill-evolver/tests \
  -p 'test_*.py' -v
```

Expected: all tests PASS.

```bash
git add \
  skill-evolver/skills/skill-evolver/scripts/evolver.py \
  skill-evolver/skills/skill-evolver/tests/test_apply.py
git commit -m "feat: bind apply to evaluated artifact"
```

---

### Task 3: Snapshot, Journal, and Atomically Swap the Target

**Files:**
- Modify: `skill-evolver/skills/skill-evolver/scripts/evolver.py`
- Modify: `skill-evolver/skills/skill-evolver/tests/test_apply.py`

**Interfaces:**
- Consumes: `VerifiedApply`, upstream `build_skill_manifest`, `manifest_digest`, `allowed_text_path`, `structural_validate_skill`, and `fsync_directory`.
- Produces: shared test fixtures, `copy_manifest_tree`, `validate_installable_text_tree`, `ApplyPaths`, `TargetLock`, `ensure_apply_capacity`, `rename_and_sync`, `execute_apply`, and `restore_original_after_apply_failure`.

- [ ] **Step 1: Add concrete shared fixtures and failing mutation tests**

Append these helpers to `tests/support.py` (and add its `dataclass`, `json`,
`shutil`, and `sqlite3` imports):

```python
@dataclass(frozen=True)
class RuntimeFixture:
    installation_path: Path
    installation: object
    config: object
    conn: sqlite3.Connection
    target: Path
    target_identity: str


def create_test_installation(runtime, root: Path) -> RuntimeFixture:
    sessions = root / "sessions"
    workspace = root / "workspace"
    skills = root / "skills"
    target = skills / "demo"
    for path in (sessions, workspace, target):
        path.mkdir(mode=0o700, parents=True)
    (target / "SKILL.md").write_text(
        "---\nname: demo\ndescription: demo skill\n---\n\nBase.\n",
        encoding="utf-8",
    )
    installation_path = runtime.initialize_runtime(
        root / "data",
        (sessions,),
        {
            "workspace_roots": [str(workspace)],
            "exclude_roots": [],
            "mutable_skill_roots": [str(skills)],
        },
    )
    installation = runtime.load_installation(installation_path)
    config = runtime.load_config(installation)
    conn = runtime.open_database(installation)
    runtime.migrate_evaluate_schema_v2(conn)
    return RuntimeFixture(
        installation_path, installation, config, conn, target.resolve(),
        "user-skill:demo",
    )


def open_test_installation(runtime, root: Path) -> RuntimeFixture:
    installation_path = root / "data" / "installation.json"
    installation = runtime.load_installation(installation_path)
    config = runtime.load_config(installation)
    conn = runtime.open_database(installation)
    target = runtime.resolve_mutable_target(
        config, installation, "user-skill:demo"
    )
    return RuntimeFixture(
        installation_path, installation, config, conn, target,
        "user-skill:demo",
    )


```

Use Evaluate Execution's existing
`seed_ready_evaluation(runtime, fixture, evaluation_id=1, candidate_id=1)`;
do not redefine or fork its artifact format.

In `test_apply.py`, import
`create_test_installation, load_runtime, seed_ready_evaluation`, then append:

```python
class ApplyFilesystemTests(unittest.TestCase):
    def setUp(self) -> None:
        self.runtime = load_runtime()
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)

    def test_apply_fault_boundary_matrix_is_exact(self) -> None:
        actions = (
            "journal_operation_created",
            "journal_snapshot_ready",
            "journal_prepared_ready",
            "journal_swap_armed",
            "rename_original_to_rollback",
            "journal_original_renamed",
            "rename_prepared_to_target",
            "journal_candidate_installed",
            "journal_validating",
            "journal_finalized",
        )
        self.assertEqual(
            self.runtime.APPLY_FAULT_POINTS,
            tuple(
                f"{side}_apply_{action}"
                for action in actions
                for side in ("before", "after")
            ),
        )

    def test_prepared_and_rollback_are_target_siblings(self) -> None:
        target = self.root / "skills" / "demo"
        target.mkdir(parents=True)
        paths = self.runtime.apply_paths(
            self.root / "data", target, "user-skill:demo", "a" * 64, 7
        )
        self.assertEqual(paths.prepared.parent, target.parent)
        self.assertEqual(paths.rollback.parent, target.parent)
        self.assertNotEqual(paths.snapshot.parent, target.parent)

    def test_capacity_includes_peak_and_margin(self) -> None:
        with self.assertRaisesRegex(ValueError, "insufficient_target_space"):
            self.runtime.ensure_apply_capacity(
                2_000_000, 10_000_000, 1_000_000, 1_000_000, 1_000_000
            )
        self.runtime.ensure_apply_capacity(
            3_048_576, 3_048_576, 1_000_000, 1_000_000, 1_000_000
        )

    def test_verified_apply_installs_exact_hash_and_records_lineage(self) -> None:
        fixture = make_ready_apply_fixture(self.runtime, self.root)
        result = self.runtime.execute_apply(
            fixture.conn,
            fixture.installation,
            fixture.config,
            fixture.evaluation_id,
            fixture.spec_digest,
        )
        operation = fixture.conn.execute(
            "SELECT * FROM apply_operations WHERE id = ?", (result["operation_id"],)
        ).fetchone()
        version = fixture.conn.execute(
            "SELECT * FROM versions WHERE id = ?", (result["version_id"],)
        ).fetchone()
        self.assertEqual(
            self.runtime.manifest_digest(
                self.runtime.build_skill_manifest(fixture.target)
            ),
            fixture.candidate_hash,
        )
        self.assertEqual(operation["status"], "applied")
        self.assertEqual(version["event_type"], "apply")
        self.assertEqual(version["parent_hash"], fixture.base_hash)
        self.assertEqual(version["content_hash"], fixture.candidate_hash)
        self.assertEqual(version["evaluation_id"], fixture.evaluation_id)
        self.assertTrue(Path(version["snapshot_path"]).is_dir())

    def test_validation_failure_restores_base_before_returning(self) -> None:
        fixture = make_ready_apply_fixture(self.runtime, self.root)
        with unittest.mock.patch.object(
            self.runtime,
            "validate_installable_text_tree",
            side_effect=[
                None,
                ValueError("synthetic_validation_failure"),
            ],
        ) as validator:
            with self.assertRaisesRegex(ValueError, "synthetic_validation_failure"):
                self.runtime.execute_apply(
                    fixture.conn,
                    fixture.installation,
                    fixture.config,
                    fixture.evaluation_id,
                    fixture.spec_digest,
                )
        self.assertEqual(validator.call_count, 2)
        operation = fixture.conn.execute(
            "SELECT * FROM apply_operations ORDER BY id DESC LIMIT 1"
        ).fetchone()
        candidate = fixture.conn.execute(
            "SELECT status FROM candidates WHERE id = ?", (fixture.candidate_id,)
        ).fetchone()
        self.assertEqual(
            self.runtime.manifest_digest(
                self.runtime.build_skill_manifest(fixture.target)
            ),
            fixture.base_hash,
        )
        self.assertEqual(operation["status"], "rolled_back")
        self.assertEqual(candidate["status"], "apply_failed")
        self.assertEqual(
            fixture.conn.execute("SELECT COUNT(*) FROM versions").fetchone()[0],
            0,
        )

    def test_lock_rechecks_base_drift_before_operation(self) -> None:
        fixture = make_ready_apply_fixture(self.runtime, self.root)
        (fixture.target / "SKILL.md").write_text(
            "---\nname: demo\ndescription: demo skill\n---\n\nDrift.\n",
            encoding="utf-8",
        )
        with self.assertRaisesRegex(ValueError, "apply_base_drift"):
            self.runtime.execute_apply(
                fixture.conn, fixture.installation, fixture.config,
                fixture.evaluation_id, fixture.spec_digest,
            )
        self.assertEqual(
            fixture.conn.execute(
                "SELECT COUNT(*) FROM apply_operations"
            ).fetchone()[0],
            0,
        )
```

Add the complete local wrapper:

```python
def make_ready_apply_fixture(runtime, root: Path):
    shared = create_test_installation(runtime, root)
    seeded = seed_ready_evaluation(
        runtime, shared, evaluation_id=1, candidate_id=1
    )
    runtime.migrate_apply_schema_v3(shared.conn)
    return types.SimpleNamespace(
        **seeded,
        conn=shared.conn,
        installation=shared.installation,
        installation_path=shared.installation_path,
        config=shared.config,
        target=shared.target,
    )
```

Add `import types` at the top of `test_apply.py`.

- [ ] **Step 2: Run every mutation test and verify it fails**

Run:

```bash
/usr/bin/python3 -m unittest \
  skill-evolver/skills/skill-evolver/tests/test_apply.py -v
```

Expected: FAIL before any target mutation because the filesystem helpers and
`execute_apply` are undefined.

- [ ] **Step 3: Implement paths, lock, and capacity**

Add:

```python
@dataclass(frozen=True)
class ApplyPaths:
    snapshot: Path
    prepared: Path
    rollback: Path


def apply_paths(data_root, target, identity, base_hash, operation_id):
    key = hashlib.sha256(identity.encode("utf-8")).hexdigest()
    return ApplyPaths(
        data_root / "snapshots" / key / f"{base_hash}-op-{operation_id}",
        target.parent / f".{target.name}.skill-evolver-prepared-{operation_id}",
        target.parent / f".{target.name}.skill-evolver-rollback-{operation_id}",
    )


class TargetLock:
    def __init__(self, data_root: Path, identity: str) -> None:
        key = hashlib.sha256(identity.encode("utf-8")).hexdigest()
        self.path = data_root / "locks" / f"{key}.lock"
        self.descriptor = None

    def __enter__(self):
        self.path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        self.descriptor = os.open(
            str(self.path), os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW, 0o600
        )
        fcntl.flock(self.descriptor, fcntl.LOCK_EX)
        return self

    def __exit__(self, kind, value, traceback) -> None:
        fcntl.flock(self.descriptor, fcntl.LOCK_UN)
        os.close(self.descriptor)


def ensure_apply_capacity(
    target_available, data_available, current_bytes, candidate_bytes, snapshot_bytes
) -> None:
    target_peak = current_bytes + candidate_bytes
    data_peak = snapshot_bytes
    if target_available < target_peak + max(target_peak // 10, 1_048_576):
        raise ValueError("insufficient_target_space")
    if data_available < data_peak + max(data_peak // 10, 1_048_576):
        raise ValueError("insufficient_data_space")
```

- [ ] **Step 4: Implement verified snapshot and sibling preparation**

Add complete manifest-bound copy and validation helpers. They intentionally
normalize only the executable bit represented by Evaluate's manifest:

```python
def available_bytes(path: Path) -> int:
    value = os.statvfs(path)
    return int(value.f_bavail * value.f_frsize)


def manifest_total_bytes(manifest: dict) -> int:
    return int(manifest["total_file_bytes"])


def verify_tree(path: Path, expected: dict, error: str) -> dict:
    actual = build_skill_manifest(path)
    if not hmac.compare_digest(
        manifest_digest(actual), manifest_digest(expected)
    ):
        raise ValueError(error)
    return actual


def copy_manifest_tree(source: Path, destination: Path, expected: dict) -> None:
    if destination.exists() or destination.is_symlink():
        raise ValueError("copy_destination_exists")
    before = build_skill_manifest(source)
    if not hmac.compare_digest(
        manifest_digest(before), manifest_digest(expected)
    ):
        raise ValueError("copy_source_hash_mismatch")
    destination.mkdir(mode=0o700, parents=True)
    directories = [
        entry for entry in expected["entries"] if entry["type"] == "directory"
    ]
    files = [
        entry for entry in expected["entries"] if entry["type"] == "file"
    ]
    for entry in directories:
        (destination / entry["path"]).mkdir(mode=0o700, parents=True)
    for entry in files:
        source_file = source / entry["path"]
        destination_file = destination / entry["path"]
        destination_file.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        content = source_file.read_bytes()
        if (
            len(content) != entry["bytes"]
            or hashlib.sha256(content).hexdigest() != entry["sha256"]
        ):
            raise ValueError("copy_source_changed")
        descriptor = os.open(
            str(destination_file),
            os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW,
            0o700 if entry["executable"] else 0o600,
        )
        try:
            with os.fdopen(descriptor, "wb", closefd=False) as stream:
                stream.write(content)
                stream.flush()
                os.fsync(stream.fileno())
        finally:
            os.close(descriptor)
        os.chmod(
            destination_file, 0o700 if entry["executable"] else 0o600
        )
    for directory in sorted(
        (path for path in destination.rglob("*") if path.is_dir()),
        key=lambda path: len(path.parts),
        reverse=True,
    ):
        fsync_directory(directory)
    fsync_directory(destination)
    fsync_directory(destination.parent)
    verify_tree(destination, expected, "copied_tree_hash_mismatch")
    if not hmac.compare_digest(
        manifest_digest(build_skill_manifest(source)),
        manifest_digest(expected),
    ):
        raise ValueError("copy_source_changed")


def validate_installable_text_tree(root: Path, expected: dict) -> None:
    actual = verify_tree(root, expected, "installed_manifest_mismatch")
    for entry in actual["entries"]:
        if entry["type"] == "file" and (
            entry["executable"] or not allowed_text_path(entry["path"])
        ):
            raise ValueError("high_risk_unsupported")
    if not structural_validate_skill(root):
        raise ValueError("installed_structural_validation_failed")


def prepare_apply_snapshot(
    selection: VerifiedApply,
    paths: ApplyPaths,
) -> None:
    for path in (paths.snapshot, paths.prepared, paths.rollback):
        if path.exists() or path.is_symlink():
            raise ValueError("apply_path_already_exists")
    paths.snapshot.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    ensure_apply_capacity(
        available_bytes(selection.target_path.parent),
        available_bytes(paths.snapshot.parent),
        manifest_total_bytes(selection.base_manifest),
        manifest_total_bytes(selection.candidate_manifest),
        manifest_total_bytes(selection.base_manifest),
    )
    copy_manifest_tree(
        selection.target_path, paths.snapshot, selection.base_manifest
    )
    verify_tree(
        paths.snapshot, selection.base_manifest, "snapshot_hash_mismatch"
    )


def prepare_apply_candidate(
    selection: VerifiedApply,
    paths: ApplyPaths,
) -> None:
    if (
        not paths.snapshot.is_dir()
        or paths.prepared.exists() or paths.prepared.is_symlink()
        or paths.rollback.exists() or paths.rollback.is_symlink()
    ):
        raise ValueError("apply_path_state_invalid")
    copy_manifest_tree(
        selection.candidate_path, paths.prepared, selection.candidate_manifest
    )
    if paths.prepared.stat().st_dev != selection.target_path.parent.stat().st_dev:
        raise ValueError("prepared_not_same_filesystem")
    verify_tree(
        paths.prepared, selection.candidate_manifest,
        "prepared_hash_mismatch",
    )
```

- [ ] **Step 5: Implement durable journal commits and renames**

Add:

```python
def durable_journal_commit(conn, database_path: Path) -> None:
    conn.commit()
    for path in (Path(f"{database_path}-wal"), database_path):
        if path.is_file():
            descriptor = os.open(str(path), os.O_RDONLY)
            try:
                os.fsync(descriptor)
            finally:
                os.close(descriptor)
    fsync_directory(database_path.parent)


def rename_and_sync(source: Path, destination: Path) -> None:
    os.rename(source, destination)
    fsync_directory(destination.parent)


APPLY_DURABLE_ACTIONS = (
    "journal_operation_created",
    "journal_snapshot_ready",
    "journal_prepared_ready",
    "journal_swap_armed",
    "rename_original_to_rollback",
    "journal_original_renamed",
    "rename_prepared_to_target",
    "journal_candidate_installed",
    "journal_validating",
    "journal_finalized",
)
APPLY_FAULT_POINTS = tuple(
    f"{side}_apply_{action}"
    for action in APPLY_DURABLE_ACTIONS
    for side in ("before", "after")
)


def durable_boundary_commit(
    conn: sqlite3.Connection,
    database_path: Path,
    operation_kind: str,
    action: str,
    fault_inject,
) -> None:
    fault_inject(f"before_{operation_kind}_{action}")
    durable_journal_commit(conn, database_path)
    fault_inject(f"after_{operation_kind}_{action}")


def durable_boundary_rename(
    source: Path,
    destination: Path,
    operation_kind: str,
    action: str,
    fault_inject,
) -> None:
    fault_inject(f"before_{operation_kind}_{action}")
    rename_and_sync(source, destination)
    fault_inject(f"after_{operation_kind}_{action}")


def transition_and_commit_boundary(
    conn: sqlite3.Connection,
    database_path: Path,
    operation_id: int,
    expected: str,
    desired: str,
    operation_kind: str,
    action: str,
    fault_inject,
) -> None:
    conn.execute("BEGIN IMMEDIATE")
    try:
        transition_apply_operation(
            conn, operation_id, expected, desired
        )
        durable_boundary_commit(
            conn, database_path, operation_kind, action, fault_inject
        )
    except BaseException:
        conn.rollback()
        raise
```

Add the complete executor:

```python
INCOMPLETE_APPLY_STATES = {
    "preflight", "snapshot_ready", "prepared_ready", "swap_armed",
    "original_renamed", "candidate_installed", "validating",
    "rollback_failed", "recovery_required",
}


def utc_timestamp() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


def operation_error_code(prefix: str, error: BaseException) -> str:
    return f"{prefix}_{type(error).__name__.lower()}"


def finalize_apply_operation(
    conn: sqlite3.Connection,
    installation: Installation,
    selection: VerifiedApply,
    operation_id: int,
    snapshot_path: Path,
    fault_inject=lambda _point: None,
) -> int:
    conn.execute("BEGIN IMMEDIATE")
    try:
        existing = conn.execute(
            "SELECT id FROM versions WHERE operation_id=?",
            (operation_id,),
        ).fetchall()
        operation = conn.execute(
            "SELECT * FROM apply_operations WHERE id=?", (operation_id,)
        ).fetchone()
        if len(existing) > 1:
            raise ValueError("duplicate_operation_versions")
        if len(existing) == 1:
            conn.commit()
            return int(existing[0]["id"])
        if operation["status"] not in {
            "candidate_installed", "validating"
        }:
            raise ValueError("apply_not_finalizable")
        version_id = record_apply_version(
            conn,
            operation_id=operation_id,
            skill_name=selection.skill_name,
            target_identity=selection.target_identity,
            target_path=selection.target_path,
            parent_hash=selection.base_hash,
            content_hash=selection.candidate_hash,
            candidate_id=selection.candidate_id,
            evaluation_id=selection.evaluation_id,
            snapshot_path=snapshot_path,
            event_type="apply",
            created_at=utc_timestamp(),
        )
        changed = conn.execute(
            """
            UPDATE candidates
               SET status='applied',ready_evaluation_id=NULL,updated_at=?
             WHERE id=? AND status='applying'
            """,
            (utc_timestamp(), selection.candidate_id),
        ).rowcount
        if changed != 1:
            raise ValueError("candidate_apply_finalize_race")
        transition_apply_operation(
            conn, operation_id, operation["status"], "applied",
            finished_at=utc_timestamp(),
        )
        durable_boundary_commit(
            conn, installation.database, "apply",
            "journal_finalized", fault_inject,
        )
        return version_id
    except BaseException:
        conn.rollback()
        raise


def execute_apply(
    conn: sqlite3.Connection,
    installation: Installation,
    config: Config,
    evaluation_id: int,
    spec_digest: str,
    fault_inject=None,
) -> dict:
    inject = fault_inject if fault_inject is not None else lambda _point: None
    selection = load_verified_apply(
        conn, installation, config, evaluation_id, spec_digest
    )
    operation_id = None
    paths = None
    with TargetLock(installation.data_root, selection.target_identity):
        blocked = conn.execute(
            """
            SELECT id FROM apply_operations
             WHERE target_identity=? AND status IN (
               'preflight','snapshot_ready','prepared_ready','swap_armed',
               'original_renamed','candidate_installed','validating',
               'rollback_failed','recovery_required'
             ) LIMIT 1
            """,
            (selection.target_identity,),
        ).fetchone()
        if blocked is not None:
            raise ValueError("incomplete_operation_requires_recovery")
        selection = load_verified_apply(
            conn, installation, config, evaluation_id, spec_digest
        )
        current_manifest = build_skill_manifest(selection.target_path)
        if not hmac.compare_digest(
            manifest_digest(current_manifest), selection.base_hash
        ):
            raise ValueError("apply_base_drift")
        validate_installable_text_tree(
            selection.candidate_path, selection.candidate_manifest
        )
        conn.execute("BEGIN IMMEDIATE")
        try:
            operation_id = int(
                conn.execute(
                    "SELECT COALESCE(MAX(id), 0) + 1 FROM apply_operations"
                ).fetchone()[0]
            )
            paths = apply_paths(
                installation.data_root, selection.target_path,
                selection.target_identity, selection.base_hash, operation_id,
            )
            create_apply_operation(
                conn,
                operation_id=operation_id,
                operation_kind="apply",
                candidate_id=selection.candidate_id,
                evaluation_id=selection.evaluation_id,
                source_version_id=None,
                target_identity=selection.target_identity,
                target_path=selection.target_path,
                expected_current_hash=selection.base_hash,
                desired_hash=selection.candidate_hash,
                paths=paths,
                started_at=utc_timestamp(),
            )
        except BaseException:
            conn.rollback()
            raise
        durable_boundary_commit(
            conn, installation.database, "apply",
            "journal_operation_created", inject,
        )
        try:
            prepare_apply_snapshot(selection, paths)
            transition_and_commit_boundary(
                conn, installation.database, operation_id,
                "preflight", "snapshot_ready", "apply",
                "journal_snapshot_ready", inject,
            )
            prepare_apply_candidate(selection, paths)
            transition_and_commit_boundary(
                conn, installation.database, operation_id,
                "snapshot_ready", "prepared_ready", "apply",
                "journal_prepared_ready", inject,
            )
            conn.execute("BEGIN IMMEDIATE")
            changed = conn.execute(
                """
                UPDATE candidates SET status='applying',updated_at=?
                 WHERE id=? AND status='ready_for_apply'
                   AND ready_evaluation_id=?
                """,
                (
                    utc_timestamp(), selection.candidate_id,
                    selection.evaluation_id,
                ),
            ).rowcount
            if changed != 1:
                raise ValueError("candidate_apply_state_race")
            transition_apply_operation(
                conn, operation_id, "prepared_ready", "swap_armed"
            )
            durable_boundary_commit(
                conn, installation.database, "apply",
                "journal_swap_armed", inject,
            )
            durable_boundary_rename(
                selection.target_path, paths.rollback, "apply",
                "rename_original_to_rollback", inject,
            )
            transition_and_commit_boundary(
                conn, installation.database, operation_id,
                "swap_armed", "original_renamed", "apply",
                "journal_original_renamed", inject,
            )
            durable_boundary_rename(
                paths.prepared, selection.target_path, "apply",
                "rename_prepared_to_target", inject,
            )
            transition_and_commit_boundary(
                conn, installation.database, operation_id,
                "original_renamed", "candidate_installed", "apply",
                "journal_candidate_installed", inject,
            )
            verify_tree(
                selection.target_path, selection.candidate_manifest,
                "installed_candidate_hash_mismatch",
            )
            transition_and_commit_boundary(
                conn, installation.database, operation_id,
                "candidate_installed", "validating", "apply",
                "journal_validating", inject,
            )
            validate_installable_text_tree(
                selection.target_path, selection.candidate_manifest
            )
            version_id = finalize_apply_operation(
                conn, installation, selection, operation_id, paths.snapshot,
                inject,
            )
            return {
                "operation_id": operation_id,
                "version_id": version_id,
                "parent_hash": selection.base_hash,
                "content_hash": selection.candidate_hash,
            }
        except BaseException as error:
            conn.rollback()
            status = conn.execute(
                "SELECT status FROM apply_operations WHERE id=?",
                (operation_id,),
            ).fetchone()[0]
            if status in {"preflight", "snapshot_ready", "prepared_ready"}:
                transition_apply_operation(
                    conn, operation_id, status, "preflight_failed",
                    operation_error_code("apply", error), utc_timestamp(),
                )
                durable_journal_commit(conn, installation.database)
            else:
                restore_original_after_apply_failure(
                    conn, installation, selection, operation_id, paths, error
                )
            raise
```

- [ ] **Step 6: Implement immediate validation-failure restoration**

Add the complete synchronous restoration:

```python
def tree_hash_or_state(path: Path) -> str:
    if not path.exists() and not path.is_symlink():
        return "absent"
    if path.is_symlink() or not path.is_dir():
        return "other"
    return manifest_digest(build_skill_manifest(path))


def restore_original_after_apply_failure(
    conn: sqlite3.Connection,
    installation: Installation,
    selection: VerifiedApply,
    operation_id: int,
    paths: ApplyPaths,
    original_error: BaseException,
) -> None:
    try:
        target_hash = tree_hash_or_state(selection.target_path)
        rollback_hash = tree_hash_or_state(paths.rollback)
        if (
            target_hash == selection.candidate_hash
            and rollback_hash == selection.base_hash
        ):
            failed = paths.prepared.with_name(paths.prepared.name + ".failed")
            if failed.exists() or failed.is_symlink():
                raise ValueError("failed_candidate_path_exists")
            rename_and_sync(selection.target_path, failed)
            target_hash = "absent"
        if target_hash == "absent" and rollback_hash == selection.base_hash:
            rename_and_sync(paths.rollback, selection.target_path)
        verify_tree(
            selection.target_path, selection.base_manifest,
            "restored_base_hash_mismatch",
        )
        row = conn.execute(
            "SELECT status FROM apply_operations WHERE id=?", (operation_id,)
        ).fetchone()
        conn.execute("BEGIN IMMEDIATE")
        conn.execute(
            """
            UPDATE candidates
               SET status='apply_failed',ready_evaluation_id=NULL,updated_at=?
             WHERE id=? AND status='applying'
            """,
            (utc_timestamp(), selection.candidate_id),
        )
        transition_apply_operation(
            conn, operation_id, row["status"], "rolled_back",
            operation_error_code("apply", original_error), utc_timestamp(),
        )
        conn.commit()
        durable_journal_commit(conn, installation.database)
    except BaseException as rollback_error:
        conn.rollback()
        row = conn.execute(
            "SELECT status FROM apply_operations WHERE id=?", (operation_id,)
        ).fetchone()
        if row is not None and row["status"] not in {
            "applied", "rolled_back", "rollback_failed"
        }:
            transition_apply_operation(
                conn, operation_id, row["status"], "rollback_failed",
                operation_error_code("rollback", rollback_error),
                utc_timestamp(),
            )
            durable_journal_commit(conn, installation.database)
        raise RuntimeError("apply_rollback_failed") from rollback_error
```

Failures before `swap_armed` leave the candidate `ready_for_apply`. Do not
implement crash reconciliation here; the Undo/Recovery plan owns it.

- [ ] **Step 7: Run tests and commit**

Run:

```bash
/usr/bin/python3 -m unittest \
  skill-evolver/skills/skill-evolver/tests/test_apply.py -v
/usr/bin/python3 -m unittest discover \
  -s skill-evolver/skills/skill-evolver/tests \
  -p 'test_*.py' -v
```

Expected: all tests PASS.

```bash
git add \
  skill-evolver/skills/skill-evolver/scripts/evolver.py \
  skill-evolver/skills/skill-evolver/tests/test_apply.py
git commit -m "feat: atomically apply evaluated skills"
```

---

### Task 4: Wire Commands, Skill Boundary, and Apply Release Report

**Files:**
- Modify: `skill-evolver/skills/skill-evolver/scripts/evolver.py`
- Modify: `skill-evolver/skills/skill-evolver/tests/test_apply.py`
- Modify: `skill-evolver/skills/skill-evolver/SKILL.md`
- Create: `skill-evolver/docs/release-reports/apply-versioning.json`

**Interfaces:**
- Consumes: preview and executor from Tasks 2–3.
- Produces: `apply-preview`, `apply`, `apply-release-gate`.

- [ ] **Step 1: Add failing command-boundary tests**

Append:

```python
class ApplyCommandTests(unittest.TestCase):
    def setUp(self) -> None:
        self.runtime = load_runtime()

    def test_executor_refuses_non_tty_before_loading_installation(self) -> None:
        with unittest.mock.patch.object(self.runtime.sys.stdin, "isatty", return_value=False):
            with unittest.mock.patch.object(
                self.runtime, "load_installation",
                side_effect=AssertionError("must not load installation"),
            ):
                with self.assertRaisesRegex(ValueError, "tty_required"):
                    self.runtime.cmd_apply(unittest.mock.Mock())

    def test_preview_never_invokes_executor(self) -> None:
        with tempfile.TemporaryDirectory() as name:
            fixture = make_ready_apply_fixture(self.runtime, Path(name))
            args = types.SimpleNamespace(
                installation=str(fixture.installation_path),
                evaluation=fixture.evaluation_id,
                spec_digest=fixture.spec_digest,
            )
            with unittest.mock.patch.object(
                self.runtime, "execute_apply",
                side_effect=AssertionError("preview must not execute apply"),
            ):
                self.assertEqual(self.runtime.cmd_apply_preview(args), 0)
            fixture.conn.close()

    def test_release_requires_focused_suite_and_clean_schema_v3(self) -> None:
        with tempfile.TemporaryDirectory() as name:
            fixture = make_ready_apply_fixture(self.runtime, Path(name))
            passed = self.runtime.build_apply_release_report(
                fixture.conn, set(self.runtime.APPLY_REQUIRED_TEST_IDS)
            )
            failed = self.runtime.build_apply_release_report(
                fixture.conn,
                set(self.runtime.APPLY_REQUIRED_TEST_IDS[1:]),
            )
            self.assertEqual(passed["decision"], "PASS")
            self.assertEqual(failed["decision"], "FAIL")
            fixture.conn.close()

    def test_release_parser_rejects_skipped_required_test(self) -> None:
        skipped = next(
            test_id for test_id in self.runtime.APPLY_REQUIRED_TEST_IDS
            if test_id.endswith(
                "test_executor_refuses_non_tty_before_loading_installation"
            )
        )
        output = (
            f"test_name ({skipped}) ... skipped 'synthetic skip'\n"
        ).encode("utf-8")
        passed = self.runtime.parse_verbose_ok_test_ids(
            output, self.runtime.APPLY_REQUIRED_TEST_IDS
        )
        self.assertNotIn(skipped, passed)
        with tempfile.TemporaryDirectory() as name:
            fixture = make_ready_apply_fixture(self.runtime, Path(name))
            report = self.runtime.build_apply_release_report(
                fixture.conn,
                set(self.runtime.APPLY_REQUIRED_TEST_IDS) - {skipped},
            )
            self.assertEqual(report["decision"], "FAIL")
            fixture.conn.close()
```

Import `unittest.mock`.

- [ ] **Step 2: Implement parsers and handlers**

Add complete handlers, then register them:

```python
def load_apply_command_runtime(value: str):
    installation_path = Path(value)
    installation = load_installation(installation_path)
    config = load_config(installation)
    conn = open_database(installation)
    if conn.execute("PRAGMA user_version").fetchone()[0] != 3:
        conn.close()
        raise ValueError("apply_requires_schema_v3")
    return installation_path, installation, config, conn


def cmd_apply_preview(args: argparse.Namespace) -> int:
    installation_path, installation, config, conn = (
        load_apply_command_runtime(args.installation)
    )
    try:
        preview = build_apply_preview(
            conn, installation, config, args.evaluation, args.spec_digest,
            installation_path, Path(__file__),
        )
        write_json_stdout(preview)
        return 2 if preview["drifted"] else 0
    finally:
        conn.close()


def cmd_apply(args: argparse.Namespace) -> int:
    if not sys.stdin.isatty():
        raise ValueError("tty_required")
    requested = require_full_sha256(args.spec_digest, "spec")
    confirm_full_digest(requested, True, input)
    _path, installation, config, conn = load_apply_command_runtime(
        args.installation
    )
    try:
        result = execute_apply(
            conn, installation, config, args.evaluation, requested
        )
        write_json_stdout(result)
        return 0
    finally:
        conn.close()


    preview = commands.add_parser("apply-preview")
    preview.add_argument("--installation", required=True)
    preview.add_argument("--evaluation", required=True, type=int)
    preview.add_argument("--spec-digest", required=True)
    preview.set_defaults(handler=cmd_apply_preview)

    apply_command = commands.add_parser("apply")
    apply_command.add_argument("--installation", required=True)
    apply_command.add_argument("--evaluation", required=True, type=int)
    apply_command.add_argument("--spec-digest", required=True)
    apply_command.set_defaults(handler=cmd_apply)
```

`cmd_apply_preview` loads installation/config/DB, prints `build_apply_preview`, and exits `2` on drift. `cmd_apply` checks `sys.stdin.isatty()` before loading anything, requires and prompts for the same full digest, then invokes `execute_apply` and prints operation/version IDs and hashes.

- [ ] **Step 3: Add the explicit skill instructions**

Append to `SKILL.md`:

```markdown
## Apply

An apply request must include one evaluation ID and the full 64-character
lowercase evaluation-spec digest. Run `apply-preview` only. Show the canonical
target, report digest, expected and desired hashes, every added, modified,
deleted, and mode-changed path, plus the exact `manual_command`.

Never run `manual_command`, never request permission to run it, and never pipe
the digest into it. The user must run it in a terminal outside Codex and type
the complete digest at the TTY prompt. Refuse prefixes, drift, non-ready state,
or any spec, report, manifest, harness, runner, model, or sandbox mismatch.
```

- [ ] **Step 4: Implement the deterministic release gate**

Add `import re` to `evolver.py`. Parse only complete verbose unittest PASS
lines; a skipped test has process exit code zero but is not release evidence.

Add:

```python
def parse_verbose_ok_test_ids(
    output: bytes,
    required_test_ids: tuple[str, ...],
) -> set[str]:
    text = output.decode("utf-8", "replace")
    return {
        test_id
        for test_id in required_test_ids
        if re.search(
            rf"^[^\r\n]*\({re.escape(test_id)}\) \.\.\. ok\r?$",
            text,
            re.MULTILINE,
        )
    }


def build_apply_release_report(
    conn: sqlite3.Connection,
    passed_test_ids: set[str],
) -> dict:
    incomplete = conn.execute(
        """
        SELECT COUNT(*) FROM apply_operations
         WHERE status IN (
           'preflight','snapshot_ready','prepared_ready','swap_armed',
           'original_renamed','candidate_installed','validating',
           'rollback_failed','recovery_required'
         )
        """
    ).fetchone()[0]
    evidence = {
        "manual_terminal_only": {
            "test_apply.ApplyCommandTests."
            "test_executor_refuses_non_tty_before_loading_installation"
        },
        "full_digest_required": {
            "test_apply.ApplySelectionTests."
            "test_only_complete_lowercase_sha256_is_accepted",
            "test_apply.ApplySelectionTests."
            "test_tty_requires_the_same_full_digest",
        },
        "evaluate_harness_digest_contract_reused": {
            "test_apply.ApplySelectionTests."
            "test_apply_reuses_evaluate_harness_digest_contract",
        },
        "target_lock_and_drift_check": {
            "test_apply.ApplyFilesystemTests."
            "test_lock_rechecks_base_drift_before_operation",
        },
        "snapshot_and_same_filesystem_swap": {
            "test_apply.ApplyFilesystemTests."
            "test_prepared_and_rollback_are_target_siblings",
            "test_apply.ApplyFilesystemTests."
            "test_capacity_includes_peak_and_margin",
        },
        "installed_hash_verified": {
            "test_apply.ApplyFilesystemTests."
            "test_validation_failure_restores_base_before_returning",
        },
        "version_lineage_recorded": {
            "test_apply.ApplyFilesystemTests."
            "test_verified_apply_installs_exact_hash_and_records_lineage",
        },
        "complete_apply_fault_boundary_matrix": {
            "test_apply.ApplyFilesystemTests."
            "test_apply_fault_boundary_matrix_is_exact",
        },
        "skipped_required_test_rejected": {
            "test_apply.ApplyCommandTests."
            "test_release_parser_rejects_skipped_required_test",
        },
    }
    checks = {
        "schema_v3": conn.execute(
            "PRAGMA user_version"
        ).fetchone()[0] == 3,
        "no_incomplete_apply_operations": incomplete == 0,
        **{
            name: required.issubset(passed_test_ids)
            for name, required in evidence.items()
        },
    }
    return {
        "schema_version": 1,
        "release": "apply-versioning",
        "decision": "PASS" if all(checks.values()) else "FAIL",
        "checks": checks,
        "fault_boundary_matrix": list(APPLY_FAULT_POINTS),
    }


APPLY_REQUIRED_TEST_IDS = tuple(sorted({
    value for values in {
        "manual": {
            "test_apply.ApplyCommandTests."
            "test_executor_refuses_non_tty_before_loading_installation"
        },
        "digest": {
            "test_apply.ApplySelectionTests."
            "test_only_complete_lowercase_sha256_is_accepted",
            "test_apply.ApplySelectionTests."
            "test_tty_requires_the_same_full_digest",
        },
        "harness_contract": {
            "test_apply.ApplySelectionTests."
            "test_apply_reuses_evaluate_harness_digest_contract"
        },
        "drift": {
            "test_apply.ApplyFilesystemTests."
            "test_lock_rechecks_base_drift_before_operation"
        },
        "paths": {
            "test_apply.ApplyFilesystemTests."
            "test_prepared_and_rollback_are_target_siblings",
            "test_apply.ApplyFilesystemTests."
            "test_capacity_includes_peak_and_margin",
        },
        "validation": {
            "test_apply.ApplyFilesystemTests."
            "test_validation_failure_restores_base_before_returning"
        },
        "lineage": {
            "test_apply.ApplyFilesystemTests."
            "test_verified_apply_installs_exact_hash_and_records_lineage"
        },
        "fault_matrix": {
            "test_apply.ApplyFilesystemTests."
            "test_apply_fault_boundary_matrix_is_exact"
        },
        "release_parser": {
            "test_apply.ApplyCommandTests."
            "test_release_parser_rejects_skipped_required_test"
        },
    }.values() for value in values
}))


def cmd_apply_release_gate(args: argparse.Namespace) -> int:
    tests = Path(__file__).resolve(strict=True).parent.parent / "tests"
    completed = subprocess.run(
        [
            "/usr/bin/python3", "-m", "unittest", "discover",
            "-s", str(tests), "-p", "test_apply.py", "-v",
        ],
        stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        timeout=300,
        check=False,
    )
    _path, _installation, _config, conn = load_apply_command_runtime(
        args.installation
    )
    try:
        passed_ids = (
            parse_verbose_ok_test_ids(
                completed.stdout, APPLY_REQUIRED_TEST_IDS
            )
            if completed.returncode == 0 else set()
        )
        report = build_apply_release_report(conn, passed_ids)
        report["focused_test_output_sha256"] = hashlib.sha256(
            completed.stdout
        ).hexdigest()
        atomic_write_json(Path(args.output), report)
        return 0 if report["decision"] == "PASS" else 2
    finally:
        conn.close()


    apply_gate = commands.add_parser("apply-release-gate")
    apply_gate.add_argument("--installation", required=True)
    apply_gate.add_argument("--output", required=True)
    apply_gate.set_defaults(handler=cmd_apply_release_gate)
```

Place all three registration blocks inside the existing `build_parser()` just
before its `return parser`.

- [ ] **Step 5: Run final verification**

Run:

```bash
/usr/bin/python3 -m unittest \
  skill-evolver/skills/skill-evolver/tests/test_apply.py -v
/usr/bin/python3 -m unittest discover \
  -s skill-evolver/skills/skill-evolver/tests \
  -p 'test_*.py' -v
/usr/bin/python3 -I \
  skill-evolver/skills/skill-evolver/scripts/evolver.py \
  apply-release-gate \
  --installation /Users/igyeongseob/.codex/skill-evolver/installation.json \
  --output /Users/igyeongseob/Documents/오픈소스/skill-evolver/docs/release-reports/apply-versioning.json
/usr/bin/python3 -m json.tool \
  skill-evolver/docs/release-reports/apply-versioning.json >/dev/null
```

Expected: all tests PASS; gate exits `0`; report says `apply-versioning` and `PASS`.

- [ ] **Step 6: Verify manual-terminal refusal**

Run:

```bash
/bin/echo aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa |
  /usr/bin/python3 -I \
  /Users/igyeongseob/Documents/오픈소스/skill-evolver/skills/skill-evolver/scripts/evolver.py \
  apply \
  --installation /Users/igyeongseob/.codex/skill-evolver/installation.json \
  --evaluation 1 \
  --spec-digest aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa
```

Expected: non-zero exit with `tty_required`; no operation row, state change, snapshot, sibling, or target change.

- [ ] **Step 7: Commit the release**

```bash
git add \
  skill-evolver/skills/skill-evolver/scripts/evolver.py \
  skill-evolver/skills/skill-evolver/tests/test_apply.py \
  skill-evolver/skills/skill-evolver/SKILL.md \
  skill-evolver/docs/release-reports/apply-versioning.json
git commit -m "feat: release manual skill apply"
```

## Completion Criteria

- Upstream reports were PASS before migration.
- Schema migration is exactly v2→v3 and preserves upstream data.
- Preview revalidates and binds the exact ready evaluation, full spec/report digests, all manifests, harness, runner, model, sandbox, target, and diff without mutation.
- Executor rejects non-TTY stdin and every non-full digest.
- Lock precedes final drift and capacity checks.
- Snapshot and sibling prepared tree are verified and durably synced before mutation.
- Both target renames are same-parent operations followed by directory `fsync` and journal commit.
- Installed target hashes equal the evaluated candidate before version commit.
- Apply version records operation, target, candidate, evaluation, parent/content hashes, and snapshot.
- Synchronous validation failure restores the verified base, records `rolled_back`, and prevents unchanged re-apply.
- Crash reconciliation, fault injection, undo, and stale sibling cleanup are reserved for `2026-07-26-skill-evolver-undo-recovery.md`.
- `skill-evolver/docs/release-reports/apply-versioning.json` parses and reports PASS.
