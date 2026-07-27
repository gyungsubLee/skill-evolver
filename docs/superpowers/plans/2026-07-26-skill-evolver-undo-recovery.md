# Skill Evolver Undo and Recovery Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Restore the verified parent snapshot of one applied version through a manual-terminal-only, full-hash-bound transaction, and deterministically reconcile every interrupted Apply or Undo without guessing.

**Architecture:** Reuse schema v3, target resolution, locking, manifests, snapshots, and same-filesystem swap primitives from Apply/Versioning. Undo treats a selected apply version as immutable lineage: preview proves that its parent snapshot is available, while the TTY executor binds the operation to the exact current target hash. A shared recovery state machine combines the durable journal stage with observed target, rollback, prepared, and snapshot hashes; it either completes one provable transition, restores one provable original, or marks `recovery_required` without mutating files.

**Tech Stack:** macOS, Codex CLI and Desktop, `/usr/bin/python3` 3.9+, Python standard library (`argparse`, `dataclasses`, `fcntl`, `hashlib`, `json`, `multiprocessing`, `os`, `pathlib`, `shutil`, `sqlite3`, `subprocess`, `unittest`), SQLite WAL, POSIX rename and directory `fsync`.

## Global Constraints

- Source specification: `skill-evolver/docs/superpowers/specs/2026-07-26-skill-evolver-design.md`.
- Start only after Feasibility, Read-only MVP, Evaluate, and Apply/Versioning reports contain `decision: PASS`.
- Consume schema v3 exactly. Do not add a table, column, index, dependency, or migration.
- Reuse `ApplyPaths`, `TargetLock`, `apply_paths`, `build_skill_manifest`, `manifest_digest`, `copy_manifest_tree`, `verify_tree`, `validate_installable_text_tree`, `durable_journal_commit`, `rename_and_sync`, `transition_apply_operation`, and `record_apply_version`.
- Runtime remains `skill-evolver/skills/skill-evolver/scripts/evolver.py`; `/usr/bin/python3 -I` 3.9+ and standard library only.
- Undo accepts only an existing `event_type = 'apply'` version and restores that version's `parent_hash` from its immutable snapshot. It never installs the selected version's candidate content.
- A missing, pruned, malformed, symlinked, or hash-mismatched source snapshot makes `undo_available: false`; no target mutation follows.
- Preview is read-only. Undo and recovery mutations run only from an external interactive terminal and never from chat, a Hook, or Stop.
- The executor requires the complete 64-character lowercase current target SHA-256 in both the command and the TTY prompt. Prefixes are invalid.
- Resolve the current target from the allowlisted catalog. Never accept a filesystem target path from an argument, transcript, version row, or report prose.
- Acquire the target lock before recovery, expected-current comparison, capacity checks, or target mutation.
- Take and verify a pre-undo snapshot before preparing or swapping. Prepared and rollback paths are target siblings and must share its filesystem.
- Each rename is followed by target-parent `fsync`, then a durable journal commit.
- A successful undo version records `parent_hash = expected_current_hash`, `content_hash = selected_apply_version.parent_hash`, `event_type = 'undo'`, and the pre-undo snapshot.
- Synchronous validation failure restores the exact pre-undo state and creates no version.
- Recovery is idempotent. One operation produces at most one version, and running recovery twice produces the same state.
- Ambiguous or contradictory filesystem evidence must result in `recovery_required` with no rename, copy, removal, or cleanup.
- Fault injection is a private callable argument used only by tests. Do not expose an environment variable, config key, or CLI flag.
- Keep the five newest available version snapshots per target. Pin every snapshot referenced by a non-terminal operation; pruning changes availability, never lineage rows.
- Do not create Git pushes, pull requests, network calls, production changes, or automatic external actions.
- Commit steps stage only exact Skill Evolver paths; never run `git add .`.

## Execution Preconditions

- [ ] **Verify every upstream release report and baseline test**

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
    Path("skill-evolver/docs/release-reports/apply-versioning.json"),
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

Expected: `upstream gates: PASS`; every existing test PASS. Otherwise stop without opening schema v3 for mutation.

## File Structure

| Path | Responsibility |
| --- | --- |
| `skill-evolver/skills/skill-evolver/scripts/evolver.py` | Undo selection, exact-current confirmation, transaction, generic recovery, snapshot retention, commands, and release gate. |
| `skill-evolver/skills/skill-evolver/tests/test_undo_recovery.py` | Focused availability, drift, lineage, rollback, retention, crash matrix, ambiguity, and command-boundary tests. |
| `skill-evolver/skills/skill-evolver/SKILL.md` | Read-only preview and external-terminal Undo/Recovery boundary. |
| `skill-evolver/docs/release-reports/undo-recovery.json` | Deterministic Undo/Recovery release decision. |

---

### Task 1: Bind Undo Preview to One Version and One Exact Current Hash

**Files:**
- Modify: `skill-evolver/skills/skill-evolver/scripts/evolver.py`
- Create: `skill-evolver/skills/skill-evolver/tests/test_undo_recovery.py`

**Interfaces:**
- Consumes: schema-v3 `versions` and `apply_operations`, catalog target resolution, Apply manifest/snapshot helpers.
- Produces: `VerifiedUndo`, `load_verified_undo`, `build_undo_preview`, `confirm_expected_current_hash`.

- [ ] **Step 1: Write the failing selection and confirmation tests**

Create `test_undo_recovery.py`:

```python
from __future__ import annotations

import json
import multiprocessing
import os
import tempfile
import types
import unittest
import unittest.mock
from pathlib import Path

from support import (
    create_test_installation,
    load_runtime,
    open_test_installation,
    seed_ready_evaluation,
)


def make_applied_fixture(runtime, root: Path):
    shared = create_test_installation(runtime, root)
    seeded = seed_ready_evaluation(
        runtime, shared, evaluation_id=1, candidate_id=1
    )
    runtime.migrate_apply_schema_v3(shared.conn)
    applied = runtime.execute_apply(
        shared.conn,
        shared.installation,
        shared.config,
        int(seeded["evaluation_id"]),
        str(seeded["spec_digest"]),
    )
    return types.SimpleNamespace(
        **seeded,
        **applied,
        conn=shared.conn,
        installation=shared.installation,
        installation_path=shared.installation_path,
        config=shared.config,
        target=shared.target,
    )


class UndoSelectionTests(unittest.TestCase):
    def setUp(self) -> None:
        self.runtime = load_runtime()
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.fixture = make_applied_fixture(self.runtime, self.root)
        self.addCleanup(self.fixture.conn.close)

    def test_preview_names_parent_snapshot_and_exact_current_hash(self) -> None:
        preview = self.runtime.build_undo_preview(
            self.fixture.conn,
            self.fixture.installation,
            self.fixture.config,
            self.fixture.version_id,
        )
        self.assertTrue(preview["undo_available"])
        self.assertEqual(preview["source_version_id"], self.fixture.version_id)
        self.assertEqual(preview["expected_current_hash"], self.fixture.candidate_hash)
        self.assertEqual(preview["desired_hash"], self.fixture.base_hash)
        self.assertNotIn("--target", preview["manual_command"])
        self.assertIn(self.fixture.candidate_hash, preview["manual_command"])

    def test_missing_or_changed_snapshot_is_unavailable(self) -> None:
        version = self.fixture.conn.execute(
            "SELECT snapshot_path FROM versions WHERE id = ?",
            (self.fixture.version_id,),
        ).fetchone()
        snapshot = Path(version["snapshot_path"])
        (snapshot / "SKILL.md").write_text("changed\n", encoding="utf-8")
        preview = self.runtime.build_undo_preview(
            self.fixture.conn,
            self.fixture.installation,
            self.fixture.config,
            self.fixture.version_id,
        )
        self.assertFalse(preview["undo_available"])
        self.assertEqual(preview["reason"], "snapshot_hash_mismatch")
        self.assertNotIn("manual_command", preview)

    def test_undo_rejects_an_undo_event_as_source(self) -> None:
        self.fixture.conn.execute(
            "UPDATE versions SET event_type='undo' WHERE id=?",
            (self.fixture.version_id,),
        )
        with self.assertRaisesRegex(ValueError, "undo_source_must_be_apply"):
            self.runtime.load_verified_undo(
                self.fixture.conn,
                self.fixture.installation,
                self.fixture.config,
                self.fixture.version_id,
            )

    def test_tty_requires_the_same_complete_current_hash(self) -> None:
        current = "c" * 64
        self.runtime.confirm_expected_current_hash(
            current, True, lambda _prompt: current
        )
        with self.assertRaisesRegex(ValueError, "tty_required"):
            self.runtime.confirm_expected_current_hash(
                current, False, lambda _prompt: current
            )
        with self.assertRaisesRegex(ValueError, "current_hash_confirmation_mismatch"):
            self.runtime.confirm_expected_current_hash(
                current, True, lambda _prompt: "c" * 63
            )
```

The test helper must use the shared Evaluate `seed_ready_evaluation` factory and the real Apply executor. Do not invent a second evaluation, staging, report, or installation format.

- [ ] **Step 2: Run the focused test and verify it fails**

Run:

```bash
/usr/bin/python3 -m unittest \
  skill-evolver/skills/skill-evolver/tests/test_undo_recovery.py -v
```

Expected: FAIL because the Undo selection interfaces are undefined.

- [ ] **Step 3: Implement immutable source-version selection**

Add:

```python
@dataclass(frozen=True)
class VerifiedUndo:
    source_version_id: int
    skill_name: str
    target_identity: str
    target_path: Path
    expected_current_hash: str
    desired_hash: str
    desired_snapshot: Path
    desired_manifest: dict


def confirm_expected_current_hash(expected: str, is_tty: bool, read_line) -> None:
    require_full_sha256(expected, "current_hash")
    if not is_tty:
        raise ValueError("tty_required")
    entered = read_line(
        "Type the complete expected current target SHA-256 to undo: "
    ).strip()
    if not hmac.compare_digest(entered, expected):
        raise ValueError("current_hash_confirmation_mismatch")


def load_verified_undo(
    conn: sqlite3.Connection,
    installation: Installation,
    config: Config,
    version_id: int,
) -> VerifiedUndo:
    row = conn.execute(
        """
        SELECT id,skill_name,target_identity,target_path,content_hash,
               parent_hash,snapshot_path,event_type
          FROM versions WHERE id=?
        """,
        (version_id,),
    ).fetchone()
    if row is None:
        raise ValueError("undo_version_not_found")
    if row["event_type"] != "apply":
        raise ValueError("undo_source_must_be_apply")
    desired_hash = require_full_sha256(
        str(row["parent_hash"] or ""), "undo_parent"
    )
    target = resolve_mutable_target(
        config, installation, str(row["target_identity"])
    )
    snapshot_value = row["snapshot_path"]
    if not snapshot_value:
        raise ValueError("snapshot_pruned")
    try:
        snapshot = require_private_artifact(
            installation.data_root / "snapshots",
            Path(str(snapshot_value)),
            "invalid_snapshot_path",
        )
    except (FileNotFoundError, NotADirectoryError):
        raise ValueError("snapshot_missing")
    if snapshot.is_symlink() or not snapshot.is_dir():
        raise ValueError("invalid_snapshot_path")
    desired_manifest = build_skill_manifest(snapshot)
    if not hmac.compare_digest(
        manifest_digest(desired_manifest), desired_hash
    ):
        raise ValueError("snapshot_hash_mismatch")
    expected_current_hash = manifest_digest(build_skill_manifest(target))
    return VerifiedUndo(
        source_version_id=int(row["id"]),
        skill_name=str(row["skill_name"]),
        target_identity=str(row["target_identity"]),
        target_path=target,
        expected_current_hash=expected_current_hash,
        desired_hash=desired_hash,
        desired_snapshot=snapshot,
        desired_manifest=desired_manifest,
    )
```

The version row's stored `target_path` remains audit data only. Catalog
resolution is authoritative.

- [ ] **Step 4: Implement the non-mutating preview**

Add the complete read-only renderer:

```python
UNDO_UNAVAILABLE_ERRORS = {
    "snapshot_pruned", "snapshot_missing", "invalid_snapshot_path",
    "snapshot_hash_mismatch",
}


def build_undo_preview(
    conn: sqlite3.Connection,
    installation: Installation,
    config: Config,
    version_id: int,
) -> dict:
    try:
        selection = load_verified_undo(
            conn, installation, config, version_id
        )
    except ValueError as error:
        reason = str(error)
        if reason not in UNDO_UNAVAILABLE_ERRORS:
            raise
        return {
            "operation": "undo",
            "source_version_id": version_id,
            "undo_available": False,
            "reason": reason,
        }
    return {
        "operation": "undo",
        "source_version_id": selection.source_version_id,
        "target_identity": selection.target_identity,
        "target_path": str(selection.target_path),
        "expected_current_hash": selection.expected_current_hash,
        "desired_hash": selection.desired_hash,
        "undo_available": True,
        "manual_command": [
            "/usr/bin/python3", "-I", str(Path(__file__).resolve(strict=True)),
            "undo", "--installation",
            str(installation.data_root / "installation.json"),
            "--version", str(selection.source_version_id),
            "--expected-current-hash", selection.expected_current_hash,
        ],
    }
```

For missing or invalid snapshots return `undo_available: false`, a stable `reason`, and no command. The function must not insert a row, acquire a write transaction, create a directory, prune a snapshot, or invoke recovery.

- [ ] **Step 5: Run focused tests and commit**

Run:

```bash
/usr/bin/python3 -m unittest \
  skill-evolver/skills/skill-evolver/tests/test_undo_recovery.py -v
```

Expected: 4 tests PASS.

```bash
git add \
  skill-evolver/skills/skill-evolver/scripts/evolver.py \
  skill-evolver/skills/skill-evolver/tests/test_undo_recovery.py
git commit -m "feat: add hash-bound undo preview"
```

---

### Task 2: Restore the Parent Snapshot Through the Durable Swap

**Files:**
- Modify: `skill-evolver/skills/skill-evolver/scripts/evolver.py`
- Modify: `skill-evolver/skills/skill-evolver/tests/test_undo_recovery.py`

**Interfaces:**
- Consumes: `VerifiedUndo`, schema-v3 journal, Apply path/lock/capacity/swap helpers.
- Produces: `execute_undo`, `finalize_undo_operation`, `restore_original_after_undo_failure`, `prune_version_snapshots`.

- [ ] **Step 1: Add failing success, drift, rollback, and retention tests**

Append:

```python
class UndoTransactionTests(unittest.TestCase):
    def setUp(self) -> None:
        self.runtime = load_runtime()
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.fixture = make_applied_fixture(self.runtime, self.root)
        self.addCleanup(self.fixture.conn.close)

    def test_exact_current_state_is_undone_and_lineage_is_retained(self) -> None:
        result = self.runtime.execute_undo(
            self.fixture.conn,
            self.fixture.installation,
            self.fixture.config,
            self.fixture.version_id,
            self.fixture.candidate_hash,
        )
        operation = self.fixture.conn.execute(
            "SELECT * FROM apply_operations WHERE id = ?",
            (result["operation_id"],),
        ).fetchone()
        version = self.fixture.conn.execute(
            "SELECT * FROM versions WHERE id = ?", (result["version_id"],)
        ).fetchone()
        self.assertEqual(operation["operation_kind"], "undo")
        self.assertEqual(operation["source_version_id"], self.fixture.version_id)
        self.assertEqual(operation["status"], "applied")
        self.assertEqual(version["event_type"], "undo")
        self.assertEqual(version["parent_hash"], self.fixture.candidate_hash)
        self.assertEqual(version["content_hash"], self.fixture.base_hash)
        self.assertEqual(
            self.runtime.manifest_digest(
                self.runtime.build_skill_manifest(self.fixture.target)
            ),
            self.fixture.base_hash,
        )
        self.assertTrue(Path(version["snapshot_path"]).is_dir())

    def test_expected_current_drift_fails_before_operation_creation(self) -> None:
        (self.fixture.target / "SKILL.md").write_text(
            "---\nname: demo\n---\ndrift\n", encoding="utf-8"
        )
        before = self.fixture.conn.execute(
            "SELECT COUNT(*) FROM apply_operations"
        ).fetchone()[0]
        with self.assertRaisesRegex(ValueError, "expected_current_hash_drift"):
            self.runtime.execute_undo(
                self.fixture.conn,
                self.fixture.installation,
                self.fixture.config,
                self.fixture.version_id,
                self.fixture.candidate_hash,
            )
        after = self.fixture.conn.execute(
            "SELECT COUNT(*) FROM apply_operations"
        ).fetchone()[0]
        self.assertEqual(after, before)

    def test_validation_failure_restores_pre_undo_state(self) -> None:
        with unittest.mock.patch.object(
            self.runtime,
            "validate_installable_text_tree",
            side_effect=[
                None,
                ValueError("synthetic_undo_validation_failure"),
            ],
        ) as validator:
            with self.assertRaisesRegex(
                ValueError, "synthetic_undo_validation_failure"
            ):
                self.runtime.execute_undo(
                    self.fixture.conn,
                    self.fixture.installation,
                    self.fixture.config,
                    self.fixture.version_id,
                    self.fixture.candidate_hash,
                )
        self.assertEqual(validator.call_count, 2)
        operation = self.fixture.conn.execute(
            """
            SELECT * FROM apply_operations
            WHERE operation_kind = 'undo' ORDER BY id DESC LIMIT 1
            """
        ).fetchone()
        self.assertEqual(operation["status"], "rolled_back")
        self.assertEqual(
            self.runtime.manifest_digest(
                self.runtime.build_skill_manifest(self.fixture.target)
            ),
            self.fixture.candidate_hash,
        )
        self.assertEqual(
            self.fixture.conn.execute(
                "SELECT COUNT(*) FROM versions WHERE event_type = 'undo'"
            ).fetchone()[0],
            0,
        )

    def test_retention_keeps_five_and_pins_incomplete_operation_snapshots(self) -> None:
        retention_identity = (
            self.fixture.target_identity + ":retention-fixture"
        )
        snapshot_root = (
            self.fixture.installation.data_root / "snapshots" / "retention"
        )
        snapshot_root.mkdir(parents=True)
        paths = []
        for index in range(7):
            path = snapshot_root / str(index)
            path.mkdir()
            paths.append(path)
            status = "validating" if index == 0 else "applied"
            self.fixture.conn.execute(
                """
                INSERT INTO apply_operations (
                    id,operation_kind,candidate_id,evaluation_id,
                    source_version_id,target_identity,target_path,
                    expected_current_hash,desired_hash,snapshot_path,
                    prepared_path,rollback_path,status,started_at,finished_at
                ) VALUES (?, 'apply', ?, ?, NULL, ?, ?, ?, ?, ?, ?, ?, ?,
                          ?, ?)
                """,
                (
                    100 + index, self.fixture.candidate_id,
                    self.fixture.evaluation_id, retention_identity,
                    str(self.fixture.target), format(index, "064x"),
                    format(index + 1, "064x"), str(path),
                    str(self.fixture.target.parent / f".prepared-{index}"),
                    str(self.fixture.target.parent / f".rollback-{index}"),
                    status, f"2026-07-26T00:00:{index:02d}Z",
                    None if status == "validating"
                    else f"2026-07-26T00:00:{index:02d}Z",
                ),
            )
            self.fixture.conn.execute(
                """
                INSERT INTO versions (
                    skill_name, target_identity, target_path, content_hash,
                    parent_hash, operation_id, snapshot_path, event_type, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, 'apply', ?)
                """,
                (
                    "demo", retention_identity, str(self.fixture.target),
                    format(index + 1, "064x"), format(index, "064x"),
                    100 + index, str(path), f"2026-07-26T00:00:{index:02d}Z",
                ),
            )
        self.runtime.prune_version_snapshots(
            self.fixture.conn,
            retention_identity,
            self.fixture.installation,
            keep=5,
        )
        self.assertTrue(paths[0].exists())
        self.assertFalse(paths[1].exists())
        self.assertTrue(all(path.exists() for path in paths[2:]))
```

The dedicated retention identity excludes the fixture's pre-existing lineage
from the ordering. Its oldest snapshot is outside the newest five but remains
pinned by its `validating` operation. This is test setup, not a relaxation of
schema v3.

- [ ] **Step 2: Run the transaction tests and verify they fail**

Run:

```bash
/usr/bin/python3 -m unittest \
  skill-evolver/skills/skill-evolver/tests/test_undo_recovery.py -v
```

Expected: FAIL because `execute_undo` and retention are undefined; the target remains at the applied candidate hash.

- [ ] **Step 3: Implement the hash-bound undo transaction**

Add:

```python
UNDO_DURABLE_ACTIONS = (
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
UNDO_FAULT_POINTS = tuple(
    f"{side}_undo_{action}"
    for action in UNDO_DURABLE_ACTIONS
    for side in ("before", "after")
)


def execute_undo(
    conn: sqlite3.Connection,
    installation: Installation,
    config: Config,
    version_id: int,
    expected_current_hash: str,
    fault_inject=None,
) -> dict:
    expected = require_full_sha256(
        expected_current_hash, "current_hash"
    )
    selection = load_verified_undo(
        conn, installation, config, version_id
    )
    inject = fault_inject if fault_inject is not None else lambda _point: None
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
        selection = load_verified_undo(
            conn, installation, config, version_id
        )
        current_manifest = build_skill_manifest(selection.target_path)
        current_hash = manifest_digest(current_manifest)
        if not hmac.compare_digest(current_hash, expected):
            raise ValueError("expected_current_hash_drift")
        validate_installable_text_tree(
            selection.desired_snapshot, selection.desired_manifest
        )
        conn.execute("BEGIN IMMEDIATE")
        try:
            operation_id = int(
                conn.execute(
                    "SELECT COALESCE(MAX(id),0)+1 FROM apply_operations"
                ).fetchone()[0]
            )
            paths = apply_paths(
                installation.data_root, selection.target_path,
                selection.target_identity, current_hash, operation_id,
            )
            create_apply_operation(
                conn,
                operation_id=operation_id,
                operation_kind="undo",
                candidate_id=None,
                evaluation_id=None,
                source_version_id=selection.source_version_id,
                target_identity=selection.target_identity,
                target_path=selection.target_path,
                expected_current_hash=current_hash,
                desired_hash=selection.desired_hash,
                paths=paths,
                started_at=utc_timestamp(),
            )
        except BaseException:
            conn.rollback()
            raise
        durable_boundary_commit(
            conn, installation.database, "undo",
            "journal_operation_created", inject,
        )
        try:
            for path in (paths.snapshot, paths.prepared, paths.rollback):
                if path.exists() or path.is_symlink():
                    raise ValueError("apply_path_already_exists")
            paths.snapshot.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
            ensure_apply_capacity(
                available_bytes(selection.target_path.parent),
                available_bytes(paths.snapshot.parent),
                manifest_total_bytes(current_manifest),
                manifest_total_bytes(selection.desired_manifest),
                manifest_total_bytes(current_manifest),
            )
            copy_manifest_tree(
                selection.target_path, paths.snapshot, current_manifest
            )
            transition_and_commit_boundary(
                conn, installation.database, operation_id,
                "preflight", "snapshot_ready", "undo",
                "journal_snapshot_ready", inject,
            )
            copy_manifest_tree(
                selection.desired_snapshot, paths.prepared,
                selection.desired_manifest,
            )
            if (
                paths.prepared.stat().st_dev
                != selection.target_path.parent.stat().st_dev
            ):
                raise ValueError("prepared_not_same_filesystem")
            transition_and_commit_boundary(
                conn, installation.database, operation_id,
                "snapshot_ready", "prepared_ready", "undo",
                "journal_prepared_ready", inject,
            )
            transition_and_commit_boundary(
                conn, installation.database, operation_id,
                "prepared_ready", "swap_armed", "undo",
                "journal_swap_armed", inject,
            )
            durable_boundary_rename(
                selection.target_path, paths.rollback, "undo",
                "rename_original_to_rollback", inject,
            )
            transition_and_commit_boundary(
                conn, installation.database, operation_id,
                "swap_armed", "original_renamed", "undo",
                "journal_original_renamed", inject,
            )
            durable_boundary_rename(
                paths.prepared, selection.target_path, "undo",
                "rename_prepared_to_target", inject,
            )
            transition_and_commit_boundary(
                conn, installation.database, operation_id,
                "original_renamed", "candidate_installed", "undo",
                "journal_candidate_installed", inject,
            )
            verify_tree(
                selection.target_path, selection.desired_manifest,
                "installed_undo_hash_mismatch",
            )
            transition_and_commit_boundary(
                conn, installation.database, operation_id,
                "candidate_installed", "validating", "undo",
                "journal_validating", inject,
            )
            validate_installable_text_tree(
                selection.target_path, selection.desired_manifest
            )
            version_result = finalize_undo_operation(
                conn, installation, operation_id, selection,
                paths.snapshot, inject,
            )
            prune_version_snapshots(
                conn, selection.target_identity, installation, keep=5
            )
            return {
                "operation_id": operation_id,
                "version_id": version_result,
                "parent_hash": current_hash,
                "content_hash": selection.desired_hash,
            }
        except BaseException as error:
            conn.rollback()
            row = conn.execute(
                "SELECT status FROM apply_operations WHERE id=?",
                (operation_id,),
            ).fetchone()
            if row is not None and row["status"] in {
                "preflight", "snapshot_ready", "prepared_ready"
            }:
                transition_apply_operation(
                    conn, operation_id, row["status"], "preflight_failed",
                    operation_error_code("undo", error), utc_timestamp(),
                )
                durable_journal_commit(conn, installation.database)
            elif row is not None and row["status"] != "applied":
                restore_original_after_undo_failure(
                    conn, installation, selection, operation_id, paths, error
                )
            raise
```

The callback is not reachable from argument parsing, configuration, or the
environment.

- [ ] **Step 4: Make finalization idempotent and lineage-exact**

Add:

```python
def finalize_undo_operation(
    conn: sqlite3.Connection,
    installation: Installation,
    operation_id: int,
    selection: VerifiedUndo,
    pre_undo_snapshot: Path,
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
            if operation["status"] != "applied":
                transition_apply_operation(
                    conn, operation_id, operation["status"], "applied",
                    finished_at=utc_timestamp(),
                )
            conn.commit()
            durable_journal_commit(conn, installation.database)
            return int(existing[0]["id"])
        if operation["status"] not in {
            "candidate_installed", "validating"
        }:
            raise ValueError("undo_not_finalizable")
        version_id = record_apply_version(
            conn,
            operation_id=operation_id,
            skill_name=selection.skill_name,
            target_identity=selection.target_identity,
            target_path=selection.target_path,
            parent_hash=str(operation["expected_current_hash"]),
            content_hash=str(operation["desired_hash"]),
            candidate_id=None,
            evaluation_id=None,
            snapshot_path=pre_undo_snapshot,
            event_type="undo",
            created_at=utc_timestamp(),
        )
        transition_apply_operation(
            conn, operation_id, operation["status"], "applied",
            finished_at=utc_timestamp(),
        )
        durable_boundary_commit(
            conn, installation.database, "undo",
            "journal_finalized", fault_inject,
        )
        return version_id
    except BaseException:
        conn.rollback()
        raise
```

The Apply/Versioning schema must enforce one version per operation with its `UNIQUE` constraint on `versions.operation_id`; keep the query guard for readable recovery errors.

- [ ] **Step 5: Restore synchronous failures and prune safely**

Add:

```python
def restore_original_after_undo_failure(
    conn: sqlite3.Connection,
    installation: Installation,
    selection: VerifiedUndo,
    operation_id: int,
    paths: ApplyPaths,
    original_error: BaseException,
) -> None:
    try:
        operation = conn.execute(
            "SELECT * FROM apply_operations WHERE id=?", (operation_id,)
        ).fetchone()
        target_hash = tree_hash_or_state(selection.target_path)
        rollback_hash = tree_hash_or_state(paths.rollback)
        if (
            target_hash == operation["desired_hash"]
            and rollback_hash == operation["expected_current_hash"]
        ):
            failed = paths.prepared.with_name(paths.prepared.name + ".failed")
            if failed.exists() or failed.is_symlink():
                raise ValueError("failed_undo_path_exists")
            rename_and_sync(selection.target_path, failed)
            target_hash = "absent"
        if (
            target_hash == "absent"
            and rollback_hash == operation["expected_current_hash"]
        ):
            rename_and_sync(paths.rollback, selection.target_path)
        if tree_hash_or_state(selection.target_path) != operation[
            "expected_current_hash"
        ]:
            raise ValueError("undo_restore_hash_mismatch")
        transition_apply_operation(
            conn, operation_id, operation["status"], "rolled_back",
            operation_error_code("undo", original_error), utc_timestamp(),
        )
        durable_journal_commit(conn, installation.database)
    except BaseException as rollback_error:
        conn.rollback()
        operation = conn.execute(
            "SELECT * FROM apply_operations WHERE id=?", (operation_id,)
        ).fetchone()
        if operation["status"] not in {
            "applied", "rolled_back", "rollback_failed"
        }:
            transition_apply_operation(
                conn, operation_id, operation["status"], "rollback_failed",
                operation_error_code("rollback", rollback_error),
                utc_timestamp(),
            )
            durable_journal_commit(conn, installation.database)
        raise RuntimeError("undo_rollback_failed") from rollback_error


def require_snapshot_delete_path(
    installation: Installation, value: str
) -> Path:
    path = Path(value)
    if path.is_symlink():
        raise ValueError("snapshot_prune_path_invalid")
    root = (installation.data_root / "snapshots").resolve(strict=True)
    canonical = path.resolve(strict=True)
    try:
        relative = canonical.relative_to(root)
    except ValueError:
        raise ValueError("snapshot_prune_path_invalid")
    if not relative.parts or canonical == root or not canonical.is_dir():
        raise ValueError("snapshot_prune_path_invalid")
    return canonical


def prune_version_snapshots(
    conn: sqlite3.Connection,
    target_identity: str,
    installation: Installation,
    keep: int = 5,
) -> int:
    if keep < 1:
        raise ValueError("invalid_snapshot_retention")
    rows = conn.execute(
        """
        SELECT id,snapshot_path FROM versions
         WHERE target_identity=? AND snapshot_path IS NOT NULL
         ORDER BY created_at DESC,id DESC
        """,
        (target_identity,),
    ).fetchall()
    pinned = {
        str(row["snapshot_path"])
        for row in conn.execute(
            """
            SELECT snapshot_path FROM apply_operations
             WHERE target_identity=? AND status IN (
               'preflight','snapshot_ready','prepared_ready','swap_armed',
               'original_renamed','candidate_installed','validating',
               'rollback_failed','recovery_required'
             )
            """,
            (target_identity,),
        )
        if row["snapshot_path"]
    }
    pinned.update(
        str(row["snapshot_path"])
        for row in conn.execute(
            """
            SELECT v.snapshot_path
              FROM apply_operations o
              JOIN versions v ON v.id=o.source_version_id
             WHERE o.target_identity=? AND o.status IN (
               'preflight','snapshot_ready','prepared_ready','swap_armed',
               'original_renamed','candidate_installed','validating',
               'rollback_failed','recovery_required'
             ) AND v.snapshot_path IS NOT NULL
            """,
            (target_identity,),
        )
    )
    retained = 0
    removed = 0
    for row in rows:
        value = str(row["snapshot_path"])
        if value in pinned or retained < keep:
            if value not in pinned:
                retained += 1
            continue
        path = require_snapshot_delete_path(installation, value)
        shutil.rmtree(path)
        fsync_directory(path.parent)
        conn.execute(
            "UPDATE versions SET snapshot_path=NULL WHERE id=?",
            (row["id"],),
        )
        removed += 1
    conn.commit()
    durable_journal_commit(conn, installation.database)
    return removed
```

- [ ] **Step 6: Run focused and regression tests, then commit**

Run:

```bash
/usr/bin/python3 -m unittest \
  skill-evolver/skills/skill-evolver/tests/test_undo_recovery.py -v
/usr/bin/python3 -m unittest discover \
  -s skill-evolver/skills/skill-evolver/tests \
  -p 'test_*.py' -v
```

Expected: all tests PASS; success creates one undo version, drift creates no operation, failure restores the exact current hash, and retention leaves five available unpinned snapshots.

```bash
git add \
  skill-evolver/skills/skill-evolver/scripts/evolver.py \
  skill-evolver/skills/skill-evolver/tests/test_undo_recovery.py
git commit -m "feat: restore applied skill versions"
```

---

### Task 3: Reconcile Crashes Without Guessing and Release Undo/Recovery

**Files:**
- Modify: `skill-evolver/skills/skill-evolver/scripts/evolver.py`
- Modify: `skill-evolver/skills/skill-evolver/tests/test_undo_recovery.py`
- Modify: `skill-evolver/skills/skill-evolver/SKILL.md`
- Create: `skill-evolver/docs/release-reports/undo-recovery.json`

**Interfaces:**
- Consumes: all schema-v3 operations, target lock, catalog resolution, manifests, durable rename/journal primitives.
- Produces: `ObservedApplyState`, `observe_apply_state`, `recover_operation`, `recover_incomplete_operations`, `undo-preview`, `undo`, `recover`, `undo-recovery-gate`.

- [ ] **Step 1: Add the failing true-crash and ambiguity tests**

Append:

```python
def seed_interrupted_undo_for_test(runtime, fixture) -> int:
    selection = runtime.load_verified_undo(
        fixture.conn, fixture.installation, fixture.config,
        fixture.version_id,
    )
    current_manifest = runtime.build_skill_manifest(fixture.target)
    operation_id = fixture.conn.execute(
        "SELECT COALESCE(MAX(id),0)+1 FROM apply_operations"
    ).fetchone()[0]
    paths = runtime.apply_paths(
        fixture.installation.data_root, fixture.target,
        fixture.target_identity, fixture.candidate_hash, operation_id,
    )
    fixture.conn.execute("BEGIN IMMEDIATE")
    runtime.create_apply_operation(
        fixture.conn,
        operation_id=operation_id,
        operation_kind="undo",
        candidate_id=None,
        evaluation_id=None,
        source_version_id=fixture.version_id,
        target_identity=fixture.target_identity,
        target_path=fixture.target,
        expected_current_hash=fixture.candidate_hash,
        desired_hash=fixture.base_hash,
        paths=paths,
        started_at="2026-07-26T01:00:00Z",
    )
    fixture.conn.execute(
        "UPDATE apply_operations SET status='candidate_installed' WHERE id=?",
        (operation_id,),
    )
    fixture.conn.commit()
    paths.snapshot.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    runtime.copy_manifest_tree(
        fixture.target, paths.snapshot, current_manifest
    )
    runtime.copy_manifest_tree(
        selection.desired_snapshot, paths.prepared,
        selection.desired_manifest,
    )
    runtime.rename_and_sync(fixture.target, paths.rollback)
    runtime.rename_and_sync(paths.prepared, fixture.target)
    return operation_id


def make_ready_fixture(runtime, root: Path):
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


def crash_apply_at_point(
    root_text: str,
    evaluation_id: int,
    spec_digest: str,
    point: str,
) -> None:
    runtime = load_runtime()
    shared = open_test_installation(runtime, Path(root_text))

    def terminate(observed: str) -> None:
        if observed == point:
            os._exit(97)

    runtime.execute_apply(
        shared.conn,
        shared.installation,
        shared.config,
        evaluation_id,
        spec_digest,
        fault_inject=terminate,
    )


def crash_undo_at_point(
    root_text: str,
    version_id: int,
    current_hash: str,
    point: str,
) -> None:
    runtime = load_runtime()
    root = Path(root_text)
    shared = open_test_installation(runtime, root)

    def terminate(observed: str) -> None:
        if observed == point:
            os._exit(97)

    runtime.execute_undo(
        shared.conn, shared.installation, shared.config, version_id, current_hash,
        fault_inject=terminate,
    )


class RecoveryTests(unittest.TestCase):
    def setUp(self) -> None:
        self.runtime = load_runtime()

    def test_fault_boundary_matrices_are_exact(self) -> None:
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
        for operation_kind, points in (
            ("apply", self.runtime.APPLY_FAULT_POINTS),
            ("undo", self.runtime.UNDO_FAULT_POINTS),
        ):
            with self.subTest(operation_kind=operation_kind):
                self.assertEqual(
                    points,
                    tuple(
                        f"{side}_{operation_kind}_{action}"
                        for action in actions
                        for side in ("before", "after")
                    ),
                )

    def assert_matrix_recovers(
        self,
        operation_kind: str,
        points: tuple[str, ...],
    ) -> None:
        rename_action = "rename_prepared_to_target"
        for point in points:
            with self.subTest(point=point):
                with tempfile.TemporaryDirectory() as name:
                    root = Path(name)
                    if operation_kind == "apply":
                        fixture = make_ready_fixture(self.runtime, root)
                        expected_hash = fixture.base_hash
                        desired_hash = fixture.candidate_hash
                        child = crash_apply_at_point
                        child_args = (
                            str(root),
                            fixture.evaluation_id,
                            fixture.spec_digest,
                            point,
                        )
                    else:
                        fixture = make_applied_fixture(self.runtime, root)
                        expected_hash = fixture.candidate_hash
                        desired_hash = fixture.base_hash
                        child = crash_undo_at_point
                        child_args = (
                            str(root),
                            fixture.version_id,
                            fixture.candidate_hash,
                            point,
                        )
                    fixture.conn.close()
                    process = multiprocessing.get_context("fork").Process(
                        target=child,
                        args=child_args,
                    )
                    process.start()
                    process.join(10)
                    self.assertEqual(process.exitcode, 97)
                    shared = open_test_installation(self.runtime, root)
                    self.runtime.recover_incomplete_operations(
                        shared.conn, shared.installation, shared.config
                    )
                    second = self.runtime.recover_incomplete_operations(
                        shared.conn, shared.installation, shared.config
                    )
                    target = self.runtime.resolve_mutable_target(
                        shared.config, shared.installation,
                        fixture.target_identity,
                    )
                    prefix = f"_{operation_kind}_"
                    side, action = point.split(prefix, 1)
                    actions = (
                        self.runtime.APPLY_DURABLE_ACTIONS
                        if operation_kind == "apply"
                        else self.runtime.UNDO_DURABLE_ACTIONS
                    )
                    desired_was_installed = (
                        actions.index(action) > actions.index(rename_action)
                        or (
                            action == rename_action
                            and side == "after"
                        )
                    )
                    self.assertEqual(
                        self.runtime.manifest_digest(
                            self.runtime.build_skill_manifest(target)
                        ),
                        desired_hash if desired_was_installed else expected_hash,
                    )
                    operation = shared.conn.execute(
                        """
                        SELECT * FROM apply_operations
                        WHERE operation_kind = ? ORDER BY id DESC LIMIT 1
                        """,
                        (operation_kind,),
                    ).fetchone()
                    if operation is None:
                        self.assertEqual(
                            point,
                            f"before_{operation_kind}_journal_operation_created",
                        )
                    else:
                        self.assertIn(
                            operation["status"],
                            {"applied", "rolled_back", "preflight_failed"},
                        )
                        self.assertEqual(
                            shared.conn.execute(
                                """
                                SELECT COUNT(*) FROM versions
                                WHERE operation_id = ?
                                """,
                                (operation["id"],),
                            ).fetchone()[0],
                            1 if operation["status"] == "applied" else 0,
                        )
                    self.assertEqual(second["changed"], 0)
                    shared.conn.close()

    def test_apply_fault_boundary_matrix_recovers_idempotently(self) -> None:
        self.assert_matrix_recovers(
            "apply", self.runtime.APPLY_FAULT_POINTS
        )

    def test_undo_fault_boundary_matrix_recovers_idempotently(self) -> None:
        self.assert_matrix_recovers(
            "undo", self.runtime.UNDO_FAULT_POINTS
        )

    def test_ambiguous_hash_tuple_marks_recovery_required_without_rename(self) -> None:
        with tempfile.TemporaryDirectory() as name:
            root = Path(name)
            fixture = make_applied_fixture(self.runtime, root)
            operation_id = seed_interrupted_undo_for_test(
                self.runtime, fixture
            )
            operation = fixture.conn.execute(
                "SELECT * FROM apply_operations WHERE id = ?", (operation_id,)
            ).fetchone()
            rollback = Path(operation["rollback_path"])
            (rollback / "SKILL.md").write_text("ambiguous\n", encoding="utf-8")
            before_target = self.runtime.manifest_digest(
                self.runtime.build_skill_manifest(fixture.target)
            )
            before_rollback = self.runtime.manifest_digest(
                self.runtime.build_skill_manifest(rollback)
            )
            self.runtime.recover_operation(
                fixture.conn, fixture.installation, fixture.config, operation_id
            )
            after = fixture.conn.execute(
                "SELECT status FROM apply_operations WHERE id = ?",
                (operation_id,),
            ).fetchone()[0]
            self.assertEqual(after, "recovery_required")
            self.assertEqual(
                self.runtime.manifest_digest(
                    self.runtime.build_skill_manifest(fixture.target)
                ),
                before_target,
            )
            self.assertEqual(
                self.runtime.manifest_digest(
                    self.runtime.build_skill_manifest(rollback)
                ),
                before_rollback,
            )
            fixture.conn.close()
```

The local seeder belongs in `test_undo_recovery.py`, not production.

Also append the command-boundary tests before implementing handlers:

```python
class UndoCommandTests(unittest.TestCase):
    def setUp(self) -> None:
        self.runtime = load_runtime()

    def test_undo_refuses_non_tty_before_loading_installation(self) -> None:
        args = types.SimpleNamespace(
            installation="/does/not/load",
            version=1,
            expected_current_hash="c" * 64,
        )
        with unittest.mock.patch.object(
            self.runtime.sys.stdin, "isatty", return_value=False
        ):
            with unittest.mock.patch.object(
                self.runtime, "load_installation",
                side_effect=AssertionError("must not load installation"),
            ):
                with self.assertRaisesRegex(ValueError, "tty_required"):
                    self.runtime.cmd_undo(args)

    def test_recover_refuses_non_tty_before_loading_installation(self) -> None:
        args = types.SimpleNamespace(installation="/does/not/load")
        with unittest.mock.patch.object(
            self.runtime.sys.stdin, "isatty", return_value=False
        ):
            with unittest.mock.patch.object(
                self.runtime, "load_installation",
                side_effect=AssertionError("must not load installation"),
            ):
                with self.assertRaisesRegex(ValueError, "tty_required"):
                    self.runtime.cmd_recover(args)

    def test_preview_never_invokes_recovery(self) -> None:
        with tempfile.TemporaryDirectory() as name:
            fixture = make_applied_fixture(self.runtime, Path(name))
            args = types.SimpleNamespace(
                installation=str(fixture.installation_path),
                version=fixture.version_id,
            )
            with unittest.mock.patch.object(
                self.runtime, "recover_incomplete_operations",
                side_effect=AssertionError("preview must remain read-only"),
            ):
                self.assertEqual(self.runtime.cmd_undo_preview(args), 0)
            fixture.conn.close()

    def test_release_report_requires_suite_schema_and_clean_journal(self) -> None:
        with tempfile.TemporaryDirectory() as name:
            fixture = make_applied_fixture(self.runtime, Path(name))
            passed = self.runtime.build_undo_recovery_release_report(
                fixture.conn, set(self.runtime.UNDO_REQUIRED_TEST_IDS)
            )
            failed = self.runtime.build_undo_recovery_release_report(
                fixture.conn, set(self.runtime.UNDO_REQUIRED_TEST_IDS[1:])
            )
            self.assertEqual(passed["decision"], "PASS")
            self.assertEqual(failed["decision"], "FAIL")
            fixture.conn.close()

    def test_release_parser_rejects_skipped_required_test(self) -> None:
        skipped = next(
            test_id for test_id in self.runtime.UNDO_REQUIRED_TEST_IDS
            if test_id.endswith(
                "test_undo_fault_boundary_matrix_recovers_idempotently"
            )
        )
        output = (
            f"test_name ({skipped}) ... skipped 'synthetic skip'\n"
        ).encode("utf-8")
        passed = self.runtime.parse_verbose_ok_test_ids(
            output, self.runtime.UNDO_REQUIRED_TEST_IDS
        )
        self.assertNotIn(skipped, passed)
        with tempfile.TemporaryDirectory() as name:
            fixture = make_applied_fixture(self.runtime, Path(name))
            report = self.runtime.build_undo_recovery_release_report(
                fixture.conn,
                set(self.runtime.UNDO_REQUIRED_TEST_IDS) - {skipped},
            )
            self.assertEqual(report["decision"], "FAIL")
            fixture.conn.close()
```

- [ ] **Step 2: Run recovery tests and verify they fail**

Run:

```bash
/usr/bin/python3 -m unittest \
  skill-evolver/skills/skill-evolver/tests/test_undo_recovery.py -v
```

Expected: FAIL because observation and recovery interfaces are undefined. Each child exits within 10 seconds; no test hangs.

- [ ] **Step 3: Implement the observed-state classifier**

Add:

```python
@dataclass(frozen=True)
class ObservedApplyState:
    target: str
    rollback: str
    prepared: str
    snapshot: str


def classify_path_hash(path: Path, expected: str, desired: str) -> str:
    if not path.exists() and not path.is_symlink():
        return "absent"
    if path.is_symlink() or not path.is_dir():
        return "other"
    digest = manifest_digest(build_skill_manifest(path))
    if hmac.compare_digest(digest, expected):
        return "expected"
    if hmac.compare_digest(digest, desired):
        return "desired"
    return "other"


def observe_apply_state(
    installation: Installation,
    config: Config,
    operation,
) -> tuple[Path, ApplyPaths, ObservedApplyState]:
    target = resolve_mutable_target(
        config, installation, str(operation["target_identity"])
    )
    paths = apply_paths(
        installation.data_root, target, str(operation["target_identity"]),
        str(operation["expected_current_hash"]), int(operation["id"]),
    )
    stored = (
        str(operation["snapshot_path"]),
        str(operation["prepared_path"]),
        str(operation["rollback_path"]),
    )
    expected_paths = (
        str(paths.snapshot), str(paths.prepared), str(paths.rollback)
    )
    if stored != expected_paths:
        raise ValueError("operation_path_binding_mismatch")
    return target, paths, ObservedApplyState(
        target=classify_path_hash(
            target, operation["expected_current_hash"],
            operation["desired_hash"],
        ),
        rollback=classify_path_hash(
            paths.rollback, operation["expected_current_hash"],
            operation["desired_hash"],
        ),
        prepared=classify_path_hash(
            paths.prepared, operation["expected_current_hash"],
            operation["desired_hash"],
        ),
        snapshot=classify_path_hash(
            paths.snapshot, operation["expected_current_hash"],
            operation["desired_hash"],
        ),
    )
```

The exact recomputed paths make stored operation paths audit inputs rather than
filesystem authority. Observation performs no mutation.

- [ ] **Step 4: Implement the deterministic recovery table**

Add the complete state machine:

```python
TERMINAL_APPLY_STATES = {
    "applied", "preflight_failed", "rolled_back"
}


def finish_recovery_without_install(
    conn: sqlite3.Connection,
    installation: Installation,
    operation,
    desired_status: str,
) -> None:
    conn.execute("BEGIN IMMEDIATE")
    try:
        if operation["operation_kind"] == "apply":
            conn.execute(
                """
                UPDATE candidates
                   SET status='apply_failed',ready_evaluation_id=NULL,
                       updated_at=?
                 WHERE id=? AND status IN ('ready_for_apply','applying')
                """,
                (utc_timestamp(), operation["candidate_id"]),
            )
        transition_apply_operation(
            conn, operation["id"], operation["status"], desired_status,
            "crash_recovered", utc_timestamp(),
        )
        conn.commit()
        durable_journal_commit(conn, installation.database)
    except BaseException:
        conn.rollback()
        raise


def mark_recovery_required(
    conn: sqlite3.Connection,
    installation: Installation,
    operation,
    error_code: str,
) -> dict:
    if operation["status"] not in {
        "rollback_failed", "recovery_required"
    }:
        transition_apply_operation(
            conn, operation["id"], operation["status"],
            "recovery_required", error_code,
        )
        durable_journal_commit(conn, installation.database)
        return {"operation_id": operation["id"], "changed": 1,
                "status": "recovery_required"}
    return {"operation_id": operation["id"], "changed": 0,
            "status": operation["status"]}


def recover_operation(
    conn: sqlite3.Connection,
    installation: Installation,
    config: Config,
    operation_id: int,
) -> dict:
    initial = conn.execute(
        "SELECT * FROM apply_operations WHERE id=?", (operation_id,)
    ).fetchone()
    if initial is None:
        raise ValueError("operation_not_found")
    if initial["status"] in TERMINAL_APPLY_STATES:
        return {"operation_id": operation_id, "changed": 0,
                "status": initial["status"]}
    with TargetLock(installation.data_root, initial["target_identity"]):
        operation = conn.execute(
            "SELECT * FROM apply_operations WHERE id=?", (operation_id,)
        ).fetchone()
        if operation["status"] in TERMINAL_APPLY_STATES:
            return {"operation_id": operation_id, "changed": 0,
                    "status": operation["status"]}
        if operation["status"] in {
            "rollback_failed", "recovery_required"
        }:
            return {"operation_id": operation_id, "changed": 0,
                    "status": operation["status"]}
        try:
            target, paths, observed = observe_apply_state(
                installation, config, operation
            )
        except (OSError, ValueError):
            return mark_recovery_required(
                conn, installation, operation,
                "operation_path_or_hash_ambiguous",
            )

        pre_swap = (
            observed.target == "expected"
            and observed.rollback == "absent"
            and observed.prepared in {"absent", "desired"}
            and observed.snapshot in {"absent", "expected"}
        )
        if pre_swap and operation["status"] in {
            "preflight", "snapshot_ready", "prepared_ready", "swap_armed"
        }:
            finish_recovery_without_install(
                conn, installation, operation, "preflight_failed"
            )
            return {"operation_id": operation_id, "changed": 1,
                    "status": "preflight_failed"}

        if (
            observed.target == "absent"
            and observed.rollback == "expected"
        ):
            rename_and_sync(paths.rollback, target)
            if tree_hash_or_state(target) != operation["expected_current_hash"]:
                return mark_recovery_required(
                    conn, installation, operation,
                    "restored_target_hash_mismatch",
                )
            finish_recovery_without_install(
                conn, installation, operation, "rolled_back"
            )
            return {"operation_id": operation_id, "changed": 1,
                    "status": "rolled_back"}

        installable = (
            observed.target == "desired"
            and observed.rollback == "expected"
            and operation["status"] in {
                "swap_armed", "original_renamed",
                "candidate_installed", "validating",
            }
        )
        if installable and operation["operation_kind"] == "apply":
            evaluation = conn.execute(
                """
                SELECT evaluation_spec_digest FROM evaluations
                 WHERE id=? AND candidate_id=? AND result='ready_for_apply'
                """,
                (
                    operation["evaluation_id"],
                    operation["candidate_id"],
                ),
            ).fetchone()
            if evaluation is None:
                return mark_recovery_required(
                    conn, installation, operation,
                    "ready_evaluation_missing",
                )
            selection = load_verified_apply(
                conn, installation, config,
                int(operation["evaluation_id"]),
                str(evaluation["evaluation_spec_digest"]),
                recovery_operation_id=operation_id,
            )
            if (
                selection.base_hash != operation["expected_current_hash"]
                or selection.candidate_hash != operation["desired_hash"]
            ):
                return mark_recovery_required(
                    conn, installation, operation,
                    "apply_binding_mismatch",
                )
            validate_installable_text_tree(
                target, selection.candidate_manifest
            )
            version_id = finalize_apply_operation(
                conn, installation, selection, operation_id, paths.snapshot
            )
            return {"operation_id": operation_id, "version_id": version_id,
                    "changed": 1, "status": "applied"}

        if installable and operation["operation_kind"] == "undo":
            selection = load_verified_undo(
                conn, installation, config,
                int(operation["source_version_id"]),
            )
            if selection.desired_hash != operation["desired_hash"]:
                return mark_recovery_required(
                    conn, installation, operation, "undo_binding_mismatch"
                )
            validate_installable_text_tree(
                target, selection.desired_manifest
            )
            version_id = finalize_undo_operation(
                conn, installation, operation_id, selection, paths.snapshot
            )
            return {"operation_id": operation_id, "version_id": version_id,
                    "changed": 1, "status": "applied"}

        return mark_recovery_required(
            conn, installation, operation, "filesystem_tuple_ambiguous"
        )


def recover_incomplete_operations(
    conn: sqlite3.Connection,
    installation: Installation,
    config: Config,
) -> dict:
    ids = [
        int(row["id"])
        for row in conn.execute(
            """
            SELECT id FROM apply_operations
             WHERE status IN (
               'preflight','snapshot_ready','prepared_ready','swap_armed',
               'original_renamed','candidate_installed','validating',
               'rollback_failed','recovery_required'
             ) ORDER BY id
            """
        )
    ]
    results = [
        recover_operation(conn, installation, config, operation_id)
        for operation_id in ids
    ]
    return {
        "operations": results,
        "changed": sum(int(item["changed"]) for item in results),
        "recovery_required": [
            item["operation_id"] for item in results
            if item["status"] in {"rollback_failed", "recovery_required"}
        ],
    }
```

Pre-swap interrupted Apply is conservatively marked `apply_failed`; it must be
prepared and evaluated again. No recovery path reuses an unverified candidate.

- [ ] **Step 5: Wire manual-terminal commands and skill instructions**

Add these handlers before registering parsers:

```python
def load_command_runtime(value: str):
    installation = load_installation(Path(value))
    config = load_config(installation)
    conn = open_database(installation)
    if conn.execute("PRAGMA user_version").fetchone()[0] != 3:
        conn.close()
        raise ValueError("undo_requires_schema_v3")
    return installation, config, conn


def cmd_undo_preview(args: argparse.Namespace) -> int:
    installation, config, conn = load_command_runtime(args.installation)
    try:
        preview = build_undo_preview(
            conn, installation, config, args.version
        )
        preview["incomplete_operation_ids"] = [
            int(row["id"])
            for row in conn.execute(
                """
                SELECT id FROM apply_operations
                 WHERE status IN (
                   'preflight','snapshot_ready','prepared_ready','swap_armed',
                   'original_renamed','candidate_installed','validating',
                   'rollback_failed','recovery_required'
                 ) ORDER BY id
                """
            )
        ]
        write_json_stdout(preview)
        return 0 if preview["undo_available"] else 2
    finally:
        conn.close()


def cmd_undo(args: argparse.Namespace) -> int:
    expected = require_full_sha256(
        args.expected_current_hash, "current_hash"
    )
    confirm_expected_current_hash(expected, sys.stdin.isatty(), input)
    installation, config, conn = load_command_runtime(args.installation)
    try:
        recovery = recover_incomplete_operations(
            conn, installation, config
        )
        if recovery["recovery_required"]:
            raise ValueError("recovery_required")
        result = execute_undo(
            conn, installation, config, args.version, expected
        )
        write_json_stdout(result)
        return 0
    finally:
        conn.close()


def cmd_recover(args: argparse.Namespace) -> int:
    if not sys.stdin.isatty():
        raise ValueError("tty_required")
    installation, config, conn = load_command_runtime(args.installation)
    try:
        ids = [
            int(row["id"])
            for row in conn.execute(
                """
                SELECT id FROM apply_operations
                 WHERE status IN (
                   'preflight','snapshot_ready','prepared_ready','swap_armed',
                   'original_renamed','candidate_installed','validating',
                   'rollback_failed','recovery_required'
                 ) ORDER BY id
                """
            )
        ]
        sys.stderr.write(
            "Incomplete operation IDs: "
            + json.dumps(ids, separators=(",", ":")) + "\n"
        )
        if input("Type RECOVER to reconcile these operations: ").strip() != "RECOVER":
            raise ValueError("recovery_confirmation_mismatch")
        result = recover_incomplete_operations(
            conn, installation, config
        )
        write_json_stdout(result)
        return 0 if not result["recovery_required"] else 2
    finally:
        conn.close()


    undo_preview = commands.add_parser("undo-preview")
    undo_preview.add_argument("--installation", required=True)
    undo_preview.add_argument("--version", required=True, type=int)
    undo_preview.set_defaults(handler=cmd_undo_preview)

    undo = commands.add_parser("undo")
    undo.add_argument("--installation", required=True)
    undo.add_argument("--version", required=True, type=int)
    undo.add_argument("--expected-current-hash", required=True)
    undo.set_defaults(handler=cmd_undo)

    recover = commands.add_parser("recover")
    recover.add_argument("--installation", required=True)
    recover.set_defaults(handler=cmd_recover)
```

Both mutating handlers check `sys.stdin.isatty()` before loading an installation. `cmd_undo` confirms the same full expected-current hash, then runs recovery before selection and execution. `cmd_recover` prints the incomplete operation IDs and requires the user to type `RECOVER` before calling recovery. `undo-preview` reports incomplete operation IDs but never recovers them.

Append to `SKILL.md`:

```markdown
## Undo and recovery

For `$skill-evolver undo V-001`, run only `undo-preview --version 1`.
Show whether the selected apply version's exact parent snapshot is available,
the current and desired complete hashes, and the exact `manual_command`.

Never run an undo or recovery command, never request permission to run it, and
never provide a target path. The user must run the command in an external
interactive terminal and type the complete expected-current hash. Drift,
missing snapshots, non-apply source versions, and ambiguous recovery evidence
are hard stops. `status` and previews may report recovery needs but cannot
rename, restore, prune, or otherwise mutate files.
```

- [ ] **Step 6: Add and run the deterministic release gate**

Reuse Apply/Versioning's `parse_verbose_ok_test_ids`; only an exact verbose
`(<full test id>) ... ok` line is PASS evidence. Add the complete suite-backed
report and handler:

```python
def build_undo_recovery_release_report(
    conn: sqlite3.Connection,
    passed_test_ids: set[str],
) -> dict:
    schema_v3 = conn.execute(
        "PRAGMA user_version"
    ).fetchone()[0] == 3
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
    required = {
        "full_current_hash_required": {
            "test_undo_recovery.UndoSelectionTests."
            "test_tty_requires_the_same_complete_current_hash"
        },
        "manual_terminal_only": {
            "test_undo_recovery.UndoCommandTests."
            "test_undo_refuses_non_tty_before_loading_installation",
            "test_undo_recovery.UndoCommandTests."
            "test_recover_refuses_non_tty_before_loading_installation",
        },
        "parent_snapshot_verified": {
            "test_undo_recovery.UndoSelectionTests."
            "test_missing_or_changed_snapshot_is_unavailable"
        },
        "undo_lineage_exact": {
            "test_undo_recovery.UndoTransactionTests."
            "test_exact_current_state_is_undone_and_lineage_is_retained"
        },
        "validation_failure_restores": {
            "test_undo_recovery.UndoTransactionTests."
            "test_validation_failure_restores_pre_undo_state"
        },
        "fault_boundary_matrices_exact": {
            "test_undo_recovery.RecoveryTests."
            "test_fault_boundary_matrices_are_exact"
        },
        "apply_fault_boundary_matrix_reconciled": {
            "test_undo_recovery.RecoveryTests."
            "test_apply_fault_boundary_matrix_recovers_idempotently"
        },
        "undo_fault_boundary_matrix_reconciled": {
            "test_undo_recovery.RecoveryTests."
            "test_undo_fault_boundary_matrix_recovers_idempotently"
        },
        "ambiguous_state_refused": {
            "test_undo_recovery.RecoveryTests."
            "test_ambiguous_hash_tuple_marks_recovery_required_without_rename"
        },
        "snapshot_retention_and_pins": {
            "test_undo_recovery.UndoTransactionTests."
            "test_retention_keeps_five_and_pins_incomplete_operation_snapshots"
        },
        "skipped_required_test_rejected": {
            "test_undo_recovery.UndoCommandTests."
            "test_release_parser_rejects_skipped_required_test"
        },
    }
    checks = {
        "schema_v3_unchanged": schema_v3,
        **{
            name: ids.issubset(passed_test_ids)
            for name, ids in required.items()
        },
        "no_incomplete_operations": incomplete == 0,
    }
    return {
        "schema_version": 1,
        "release": "undo-recovery",
        "decision": "PASS" if all(checks.values()) else "FAIL",
        "checks": checks,
        "fault_boundary_matrices": {
            "apply": list(APPLY_FAULT_POINTS),
            "undo": list(UNDO_FAULT_POINTS),
        },
    }


UNDO_REQUIRED_TEST_IDS = (
    "test_undo_recovery.RecoveryTests."
    "test_ambiguous_hash_tuple_marks_recovery_required_without_rename",
    "test_undo_recovery.RecoveryTests."
    "test_apply_fault_boundary_matrix_recovers_idempotently",
    "test_undo_recovery.RecoveryTests."
    "test_fault_boundary_matrices_are_exact",
    "test_undo_recovery.RecoveryTests."
    "test_undo_fault_boundary_matrix_recovers_idempotently",
    "test_undo_recovery.UndoCommandTests."
    "test_recover_refuses_non_tty_before_loading_installation",
    "test_undo_recovery.UndoCommandTests."
    "test_release_parser_rejects_skipped_required_test",
    "test_undo_recovery.UndoCommandTests."
    "test_undo_refuses_non_tty_before_loading_installation",
    "test_undo_recovery.UndoSelectionTests."
    "test_missing_or_changed_snapshot_is_unavailable",
    "test_undo_recovery.UndoSelectionTests."
    "test_tty_requires_the_same_complete_current_hash",
    "test_undo_recovery.UndoTransactionTests."
    "test_exact_current_state_is_undone_and_lineage_is_retained",
    "test_undo_recovery.UndoTransactionTests."
    "test_retention_keeps_five_and_pins_incomplete_operation_snapshots",
    "test_undo_recovery.UndoTransactionTests."
    "test_validation_failure_restores_pre_undo_state",
)


def cmd_undo_recovery_gate(args: argparse.Namespace) -> int:
    tests = Path(__file__).resolve(strict=True).parent.parent / "tests"
    completed = subprocess.run(
        [
            "/usr/bin/python3", "-m", "unittest", "discover",
            "-s", str(tests), "-p", "test_undo_recovery.py", "-v",
        ],
        stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        timeout=300,
        check=False,
    )
    installation, _config, conn = load_command_runtime(args.installation)
    try:
        passed_ids = (
            parse_verbose_ok_test_ids(
                completed.stdout, UNDO_REQUIRED_TEST_IDS
            )
            if completed.returncode == 0 else set()
        )
        report = build_undo_recovery_release_report(conn, passed_ids)
        report["focused_test_output_sha256"] = hashlib.sha256(
            completed.stdout
        ).hexdigest()
        atomic_write_json(Path(args.output), report)
        return 0 if report["decision"] == "PASS" else 2
    finally:
        conn.close()


    undo_gate = commands.add_parser("undo-recovery-gate")
    undo_gate.add_argument("--installation", required=True)
    undo_gate.add_argument("--output", required=True)
    undo_gate.set_defaults(handler=cmd_undo_recovery_gate)
```

Place all four registration blocks inside the existing `build_parser()` just
before its `return parser`.

Run:

```bash
/usr/bin/python3 -m unittest \
  skill-evolver/skills/skill-evolver/tests/test_undo_recovery.py -v
/usr/bin/python3 -m unittest discover \
  -s skill-evolver/skills/skill-evolver/tests \
  -p 'test_*.py' -v
/usr/bin/python3 -I \
  skill-evolver/skills/skill-evolver/scripts/evolver.py \
  undo-recovery-gate \
  --installation /Users/igyeongseob/.codex/skill-evolver/installation.json \
  --output /Users/igyeongseob/Documents/오픈소스/skill-evolver/docs/release-reports/undo-recovery.json
/usr/bin/python3 -m json.tool \
  skill-evolver/docs/release-reports/undo-recovery.json >/dev/null
```

Expected: all tests PASS; every injected child exits `97`; recovery leaves no incomplete operation; report decision is `PASS`.

- [ ] **Step 7: Verify non-TTY refusal and commit**

Run:

```bash
/bin/echo cccccccccccccccccccccccccccccccccccccccccccccccccccccccccccccccc |
  /usr/bin/python3 -I \
  /Users/igyeongseob/Documents/오픈소스/skill-evolver/skills/skill-evolver/scripts/evolver.py \
  undo \
  --installation /Users/igyeongseob/.codex/skill-evolver/installation.json \
  --version 1 \
  --expected-current-hash cccccccccccccccccccccccccccccccccccccccccccccccccccccccccccccccc
```

Expected: non-zero exit with `tty_required`; no recovery, operation, snapshot, sibling, version, pruning, or target mutation.

```bash
git add \
  skill-evolver/skills/skill-evolver/scripts/evolver.py \
  skill-evolver/skills/skill-evolver/tests/test_undo_recovery.py \
  skill-evolver/skills/skill-evolver/SKILL.md \
  skill-evolver/docs/release-reports/undo-recovery.json
git commit -m "feat: release deterministic undo recovery"
```

## Completion Criteria

- Every upstream report was PASS before work began; schema v3 remains unchanged.
- Preview accepts one apply version, resolves its live allowlisted target, verifies its parent snapshot, and performs no mutation.
- The external executor rejects non-TTY input, partial hashes, drift, unavailable snapshots, non-apply source versions, and catalog mismatch.
- Target lock precedes recovery and the final expected-current comparison.
- Pre-undo snapshot and desired prepared tree are hash-verified and durably synced before same-filesystem renames.
- Undo records the exact before/after lineage and one version per operation.
- Synchronous validation failure restores the exact pre-undo target and creates no version.
- Every fault point is exercised by a real child-process exit and converges to one provable terminal state.
- Recovery is idempotent; a second pass changes nothing and never duplicates a version.
- Every unrecognized filesystem tuple becomes `recovery_required` without mutation.
- Five newest available snapshots and every active-operation reference are retained; pruned history remains visible with `undo_available: false`.
- Chat-visible status and previews never invoke recovery or cleanup.
- `skill-evolver/docs/release-reports/undo-recovery.json` parses and reports PASS.
