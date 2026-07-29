# Skill Evolver Session Runtime Queue Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace the schema-v2 feasibility probe with a production `Stop` Hook that converges repeated Stops into one private SQLite row per session, preserves generation and frozen review boundaries, falls back to a bounded spool, and exposes transcript-free read-only status.

**Architecture:** Keep the established self-contained `evolver.py` entrypoint because `/usr/bin/python3 -I` does not load sibling runtime modules. The Hook validates only its bounded envelope and transcript file stat, derives an HMAC `session_key`, and performs one short session upsert; explicit review later resolves transcript identity and parses only a frozen prefix. SQLite owns session/generation state, lease recovery, evidence uniqueness, retention, and health. Writers use short zero-wait rollback-journal transactions, while a private atomic spool absorbs immediate write contention.

**Tech Stack:** macOS Codex CLI/Desktop, `/usr/bin/python3` 3.9+, Python standard library (`argparse`, `calendar`, `dataclasses`, `fcntl`, `hashlib`, `hmac`, `json`, `os`, `pathlib`, `secrets`, `shutil`, `sqlite3`, `stat`, `tempfile`, `time`, `unittest`, `urllib.parse`), SQLite `journal_mode=DELETE`, Codex plugin matcher-free `Stop` command Hook.

## Global Constraints

- Normative specifications are
  `skill-evolver/docs/superpowers/specs/2026-07-28-skill-evolver-session-capture-amendment-design.md`
  and the non-conflicting parts of
  `skill-evolver/docs/superpowers/specs/2026-07-26-skill-evolver-design.md`.
- Strict entry gate: `skill-evolver/docs/feasibility-report-v2.json` has
  `schema_version == 2`, `decision == "PASS"`, and
  `next_action == "write_session_runtime_queue_plan"`.
- The report predecessor is `docs/feasibility-report.json` with SHA-256
  `ced4503adb44bd041de063c04e0c6c64d0831370fc12e96a920fe97244d8ae15`.
- On both CLI and Desktop, the exact Stop keys are `cwd`, `hook_event_name`,
  `session_id`, and `transcript_path`; `turn_id` is optional diagnostic
  metadata and is never required, keyed, or used as a transcript boundary.
- Both surfaces proved `same_file_identity`, `/payload/session_id`, and
  `/payload/role`. A different-inode epoch is accepted only during explicit
  review after the embedded session ID hashes to the stored `session_key`.
- The durable identity is exactly
  `HMAC-SHA256(identity_key, b"session\0" + session_id.encode("utf-8"))`.
  Raw session ID has a 30-day hard TTL; the HMAC session key has a 180-day
  dedupe TTL.
- There is exactly one `review_items` row per `session_key`. Repeated Stops
  upsert its latest safe locator and observed boundary instead of inserting
  turn rows.
- `observed_at_ns` is captured beside the transcript `fstat`; a delayed Hook or
  out-of-order spool import cannot replace a newer locator/boundary with an
  older observation.
- `transcript_epoch`, `generation`, `observed_boundary`,
  `reviewed_boundary`, `frozen_from`, and `frozen_to` are explicit state.
  The frozen locator, stat identity, epoch, and boundary do not change while a
  review lease is active.
- An expired lease returns the same generation to `pending`, clears the frozen
  state, and never advances `reviewed_boundary`.
- Candidate evidence is unique on `(candidate_id, session_key, signal_type)`;
  another generation of one session never counts as independent evidence.
- Production data root is the canonical
  `/Users/igyeongseob/.codex/skill-evolver`, mode `0700`; private files are
  mode `0600`. Runtime environment variables cannot redirect it.
- Only the main matcher-free `Stop` Hook writes automatically. `SubagentStop`,
  scheduled work, background workers, and automatic review do not exist.
- The Hook reads at most 64 KiB of stdin, reads no transcript bytes, parses no
  transcript record, calls no model, opens no network connection, mutates no
  skill, emits no stdout/stderr, and exits `0` on every path.
- Bare `$skill-evolver` and `$skill-evolver status` use SQLite read-only mode,
  do not import or clean the spool, and do not open transcripts.
- Initialization closes a complete staged `journal_mode=DELETE` database,
  atomically places the root, and proves an immediate SQLite `mode=ro` status
  open that needs no directory write or auxiliary journal file. WAL is
  intentionally excluded: Python's standard library cannot request
  `SQLITE_FCNTL_PERSIST_WAL`, so closing the final writer may remove the files
  on which a read-only open would depend.
- Review and maintenance mutations require approval scoped to the exact command
  and global data root for that invocation. No permanent writable-root grant is
  requested or documented.
- Defaults are 5 review sessions, 200 pending sessions, 14-day pending
  retention, 30-day raw-metadata TTL, 180-day session-key dedupe, and a spool
  capped at 200 files or 10 MiB. Hook-side spool locking waits at most 50 ms;
  fallback scans at most 201 directory entries, and overflow uses a separately
  locked, bounded 64-KiB append-only counter that reports saturation.
- Transcript limits remain 2 MiB and 100 complete JSONL records per session and
  8 MiB per review batch. Candidate limits remain one per session and three new
  fingerprints per batch.
- Runtime is exactly `/usr/bin/python3` 3.9 or newer and uses only the standard
  library plus `sqlite3`; no new dependency, service, PostgreSQL, or sibling
  runtime module is added.
- This plan creates queue primitives and evidence constraints, not model
  review, candidate classification, evaluation, apply, undo, or any installed
  skill mutation.
- Run commands from `/Users/igyeongseob/Documents/오픈소스`. Every commit stages
  only the exact paths listed in that task (including the one repository
  marketplace manifest in Task 4); never use `git add .`.

## Preconditions and File Structure

Run this exact gate before Task 1:

```bash
/usr/bin/python3 - <<'PY'
import json
from pathlib import Path

repo = Path(".")
root = repo / "skill-evolver"
report = json.loads(
    (root / "docs/feasibility-report-v2.json").read_text(encoding="utf-8")
)
assert report["schema_version"] == 2
assert report["decision"] == "PASS"
assert report["next_action"] == "write_session_runtime_queue_plan"
assert report["predecessor"] == {
    "path": "docs/feasibility-report.json",
    "sha256": "ced4503adb44bd041de063c04e0c6c64d0831370fc12e96a920fe97244d8ae15",
}
assert report["checks"] == {
    "cli_asymmetric_access": True,
    "cli_session_stop_contract": True,
    "cli_session_transcript_supported": True,
    "desktop_asymmetric_access": True,
    "desktop_session_stop_contract": True,
    "desktop_session_transcript_supported": True,
}
expected_surface = {
    "binding_modes": ["same_file_identity"],
    "provenance_pointer_paths": ["/payload/role"],
    "session_id_pointer_paths": ["/payload/session_id"],
    "stop_keys": ["cwd", "hook_event_name", "session_id", "transcript_path"],
}
assert report["surfaces"] == {
    "cli": expected_surface,
    "desktop": expected_surface,
}
print("session-runtime-gate-pass")
PY

/usr/bin/python3 -m unittest discover \
  -s skill-evolver/skills/skill-evolver/tests \
  -p 'test*_v2.py' \
  -v
```

Expected: `session-runtime-gate-pass`, followed by all schema-v2 feasibility
tests passing. Any assertion or test failure stops this plan; do not fall back
to the superseded turn-level plan.

| Path | Responsibility |
| --- | --- |
| `.agents/plugins/marketplace.json` | Repo-local `skill-evolver-dev` marketplace source used by the documented install command. |
| `skill-evolver/.codex-plugin/plugin.json` | Production plugin identity and version. |
| `skill-evolver/hooks/hooks.json` | The only automatic writer: one trusted production `Stop` command. |
| `skill-evolver/skills/skill-evolver/SKILL.md` | Read-only status plus exact scoped-approval rules for maintenance and later review. |
| `skill-evolver/skills/skill-evolver/references/runtime.json` | Fixed production `installation.json` locator. |
| `skill-evolver/skills/skill-evolver/scripts/evolver.py` | Self-contained installation, SQLite, session upsert, lease, spool, maintenance, and status runtime. |
| `skill-evolver/skills/skill-evolver/tests/feasibility_probe.py` | Frozen schema-v2 probe runtime used only by historical feasibility regression tests; its depth preserves the probe's repository-root calculation. |
| `skill-evolver/skills/skill-evolver/tests/support.py` | Separate loaders for the installed production runtime and frozen probe fixture. |
| `skill-evolver/skills/skill-evolver/tests/test_capture.py` | Production metadata, session state, privacy, concurrency, spool, and status tests. |
| `skill-evolver/README.md` | Exact initialize, install, status, approval, maintenance, and uninstall operations. |
| `skill-evolver/docs/release-reports/runtime-queue.json` | Generated immutable completion evidence for the downstream Review plan. |

---

### Task 1: Freeze the Passed Probe Harness Before Replacing Its Entrypoint

**Files:**
- Create: `skill-evolver/skills/skill-evolver/tests/feasibility_probe.py`
- Modify: `skill-evolver/skills/skill-evolver/tests/support.py`
- Modify: `skill-evolver/skills/skill-evolver/tests/test_access_probe_v2.py`
- Modify: `skill-evolver/skills/skill-evolver/tests/test_gate.py`
- Modify: `skill-evolver/skills/skill-evolver/tests/test_gate_v2.py`
- Modify: `skill-evolver/skills/skill-evolver/tests/test_installation.py`
- Modify: `skill-evolver/skills/skill-evolver/tests/test_session_stop_v2.py`
- Modify: `skill-evolver/skills/skill-evolver/tests/test_session_transcript_v2.py`
- Modify: `skill-evolver/skills/skill-evolver/tests/test_skeleton.py`
- Modify: `skill-evolver/skills/skill-evolver/tests/test_stop_probe.py`
- Modify: `skill-evolver/skills/skill-evolver/tests/test_transcript_probe.py`

**Interfaces:**
- Consumes: the exact `0.0.2` probe code that generated the immutable schema-v2
  PASS report.
- Produces: `load_runtime()`/`run_isolated()` for production tests and
  `load_probe_runtime()`/`run_probe_isolated()` for the historical feasibility
  suite. The frozen probe fixture is not referenced by plugin metadata or the
  installed Hook.

- [ ] **Step 1: Copy the proven probe verbatim into the test fixtures**

Run:

```bash
cp \
  skill-evolver/skills/skill-evolver/scripts/evolver.py \
  skill-evolver/skills/skill-evolver/tests/feasibility_probe.py
chmod 0600 \
  skill-evolver/skills/skill-evolver/tests/feasibility_probe.py
```

Expected: `cmp` below exits `0`; this step does not edit the original runtime
before freezing it.

```bash
cmp \
  skill-evolver/skills/skill-evolver/scripts/evolver.py \
  skill-evolver/skills/skill-evolver/tests/feasibility_probe.py
```

- [ ] **Step 2: Replace test support with explicit production/probe loaders**

Replace `skill-evolver/skills/skill-evolver/tests/support.py` with:

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
```

- [ ] **Step 3: Point every feasibility test at the frozen fixture**

Run this exact mechanical edit:

```bash
/usr/bin/python3 - <<'PY'
from pathlib import Path

root = Path("skill-evolver/skills/skill-evolver/tests")
names = (
    "test_access_probe_v2.py",
    "test_gate.py",
    "test_gate_v2.py",
    "test_installation.py",
    "test_session_stop_v2.py",
    "test_session_transcript_v2.py",
    "test_skeleton.py",
    "test_stop_probe.py",
    "test_transcript_probe.py",
)
for name in names:
    path = root / name
    text = path.read_text(encoding="utf-8")
    text = text.replace("load_runtime", "load_probe_runtime")
    text = text.replace("run_isolated", "run_probe_isolated")
    if name == "test_stop_probe.py":
        text = text.replace("SCRIPT", "PROBE_SCRIPT")
    path.write_text(text, encoding="utf-8")

skeleton = root / "test_skeleton.py"
text = skeleton.read_text(encoding="utf-8")
reason = "production metadata supersedes the completed probe surface"
for name in (
    "test_manifest_and_hook_are_discoverable",
    "test_skill_is_explicit_only",
    "test_readme_stages_exact_private_v2_gate_inventory",
):
    marker = f"    def {name}("
    text = text.replace(
        marker,
        f'    @unittest.skip("{reason}")\n{marker}',
        1,
    )
skeleton.write_text(text, encoding="utf-8")
PY
```

Expected: the nine feasibility files import only
`load_probe_runtime`, `run_probe_isolated`, and (where needed)
`PROBE_SCRIPT`; `test_capture.py` still imports the production names.

- [ ] **Step 4: Run the frozen feasibility suite**

Run:

```bash
/usr/bin/python3 -m unittest discover \
  -s skill-evolver/skills/skill-evolver/tests \
  -p 'test*.py' \
  -v
```

Expected: all probe behavior tests PASS and exactly the three superseded
probe-surface assertions are skipped. The authoritative report files and
fixtures remain byte-for-byte unchanged.

- [ ] **Step 5: Commit the historical harness**

```bash
git add \
  skill-evolver/skills/skill-evolver/tests/feasibility_probe.py \
  skill-evolver/skills/skill-evolver/tests/support.py \
  skill-evolver/skills/skill-evolver/tests/test_access_probe_v2.py \
  skill-evolver/skills/skill-evolver/tests/test_gate.py \
  skill-evolver/skills/skill-evolver/tests/test_gate_v2.py \
  skill-evolver/skills/skill-evolver/tests/test_installation.py \
  skill-evolver/skills/skill-evolver/tests/test_session_stop_v2.py \
  skill-evolver/skills/skill-evolver/tests/test_session_transcript_v2.py \
  skill-evolver/skills/skill-evolver/tests/test_skeleton.py \
  skill-evolver/skills/skill-evolver/tests/test_stop_probe.py \
  skill-evolver/skills/skill-evolver/tests/test_transcript_probe.py
git commit -m "test(skill-evolver): freeze passed feasibility probe"
```

Expected: only the eleven listed test/harness paths are committed.

---

### Task 2: Build the Private Session Store and Schema v1

**Files:**
- Modify: `skill-evolver/skills/skill-evolver/scripts/evolver.py`
- Create: `skill-evolver/skills/skill-evolver/tests/test_capture.py`

**Interfaces:**
- Consumes: the schema-v2 gate and fixed production locator specified in this
  plan.
- Produces: `Installation`, `Config`,
  `initialize_runtime(data_root, transcript_roots, config) -> Path`,
  `load_installation(path) -> Installation`,
  `load_config(installation) -> Config`, and
  `open_database(installation, read_only=False) -> sqlite3.Connection`.
- Every connection sets `busy_timeout=0`. Writers require
  `journal_mode=DELETE`, and each mutation owns one short explicit transaction
  or one autocommit statement so contention immediately reaches the Hook's
  bounded spool fallback.
- SQLite produces one unique `review_items.session_key`, session generation and
  epoch boundaries, frozen locator state, batch/evidence tables, and no
  turn-count or event-key columns.
- Fixed transcript roots must be current-user-owned, non-world-writable
  directories and must not contain, equal, or sit below the data root after
  component-wise NFC/casefold normalization. Generic `exclude_roots`
  canonicalization remains unchanged.

- [ ] **Step 1: Create failing installation and schema tests**

Create `skill-evolver/skills/skill-evolver/tests/test_capture.py` with:

```python
from __future__ import annotations

import json
import os
import sqlite3
import stat
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from support import PLUGIN_ROOT, SKILL_ROOT, load_runtime, run_isolated


class RuntimeStoreTests(unittest.TestCase):
    def setUp(self) -> None:
        self.runtime = load_runtime()
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.base = Path(self.temp.name)
        self.sessions = self.base / "sessions"
        self.excluded = self.base / "excluded"
        for path in (self.sessions, self.excluded):
            path.mkdir(mode=0o700)
        self.config = {
            "capture_paused": False,
            "exclude_roots": [str(self.excluded)],
        }

    def replace_transcript_roots(
        self, installation_path: Path, transcript_roots: tuple[Path, ...]
    ) -> None:
        payload = json.loads(installation_path.read_text(encoding="utf-8"))
        payload["transcript_roots"] = [
            str(path) for path in transcript_roots
        ]
        installation_path.write_text(
            json.dumps(payload), encoding="utf-8"
        )
        installation_path.chmod(0o600)

    def test_initialize_rejects_world_writable_transcript_root_before_writes(
        self,
    ) -> None:
        root = self.base / "world-writable-data"
        self.sessions.chmod(0o777)
        self.addCleanup(self.sessions.chmod, 0o700)

        with self.assertRaisesRegex(
            ValueError, "transcript_root_permissions"
        ):
            self.runtime.initialize_runtime(
                root, (self.sessions,), self.config
            )
        self.assertFalse(root.exists())

    def test_initialize_rejects_wrong_owner_transcript_root_before_writes(
        self,
    ) -> None:
        root = self.base / "wrong-owner-data"
        canonical_transcript = self.sessions.resolve()
        real_stat = Path.stat

        def stat_with_wrong_owner(
            path: Path, *args: object, **kwargs: object
        ):
            info = real_stat(path, *args, **kwargs)
            if path == canonical_transcript:
                return mock.Mock(
                    st_mode=info.st_mode, st_uid=info.st_uid + 1
                )
            return info

        with mock.patch.object(Path, "stat", stat_with_wrong_owner):
            with self.assertRaisesRegex(
                ValueError, "transcript_root_owner"
            ):
                self.runtime.initialize_runtime(
                    root, (self.sessions,), self.config
                )
        self.assertFalse(root.exists())

    def test_initialize_rejects_data_root_inside_transcript_root_before_writes(
        self,
    ) -> None:
        root = self.sessions / "data"
        with self.assertRaisesRegex(ValueError, "data_transcript_overlap"):
            self.runtime.initialize_runtime(
                root, (self.sessions,), self.config
            )
        self.assertFalse(root.exists())

    def test_load_rejects_tampered_transcript_data_root_overlap(self) -> None:
        installation_path = self.runtime.initialize_runtime(
            self.base / "data", (self.sessions,), self.config
        )
        root = installation_path.parent
        transcript_roots = {
            "parent": root.parent,
            "equal": root,
            "child": root / "spool",
        }

        for relation, transcript_root in transcript_roots.items():
            with self.subTest(relation=relation):
                self.replace_transcript_roots(
                    installation_path, (transcript_root,)
                )
                with self.assertRaisesRegex(
                    ValueError, "data_transcript_overlap"
                ):
                    self.runtime.load_installation(installation_path)

    def test_load_rejects_unsafe_fixed_transcript_root(self) -> None:
        installation_path = self.runtime.initialize_runtime(
            self.base / "data", (self.sessions,), self.config
        )
        self.sessions.chmod(0o777)
        self.addCleanup(self.sessions.chmod, 0o700)

        with self.assertRaisesRegex(
            ValueError, "transcript_root_permissions"
        ):
            self.runtime.load_installation(installation_path)

    def test_init_creates_private_one_row_per_session_schema(self) -> None:
        installation_path = self.runtime.initialize_runtime(
            self.base / "data", (self.sessions,), self.config
        )
        installation = self.runtime.load_installation(installation_path)
        config = self.runtime.load_config(installation)
        connection = self.runtime.open_database(installation)
        tables = {
            row[0]
            for row in connection.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            )
        }
        columns = {
            row[1]
            for row in connection.execute("PRAGMA table_info(review_items)")
        }
        indexes = {
            row[1]
            for row in connection.execute("PRAGMA index_list(review_items)")
        }
        connection.close()

        self.assertEqual(config.pending_limit_sessions, 200)
        self.assertEqual(config.review_batch_sessions, 5)
        self.assertEqual(
            tables,
            {
                "review_batches",
                "review_items",
                "candidates",
                "candidate_evidence",
                "metadata",
            },
        )
        for name in (
            "session_key",
            "generation",
            "transcript_epoch",
            "observed_boundary",
            "last_stop_ns",
            "reviewed_boundary",
            "frozen_from",
            "frozen_to",
            "frozen_locator_json",
        ):
            self.assertIn(name, columns)
        self.assertNotIn("event_key", columns)
        self.assertNotIn("turn_count", columns)
        self.assertIn("sqlite_autoindex_review_items_1", indexes)
        self.assertEqual(
            stat.S_IMODE(installation.data_root.stat().st_mode), 0o700
        )
        self.assertEqual(
            stat.S_IMODE(installation.identity_key.stat().st_mode), 0o600
        )
        self.assertEqual(len(installation.identity_key.read_bytes()), 32)

    def test_status_database_is_read_only_openable_immediately_after_init(self) -> None:
        installation_path = self.runtime.initialize_runtime(
            self.base / "data", (self.sessions,), self.config
        )
        installation = self.runtime.load_installation(installation_path)
        expected_entries = {
            "config.json",
            "evolver.db",
            "identity.key",
            "installation.json",
            "spool",
        }
        self.assertEqual(
            {path.name for path in installation.data_root.iterdir()},
            expected_entries,
        )
        connection = None
        installation.data_root.chmod(0o500)
        try:
            connection = self.runtime.open_database(
                installation, read_only=True
            )
            self.assertEqual(
                str(
                    connection.execute(
                        "PRAGMA journal_mode"
                    ).fetchone()[0]
                ).lower(),
                "delete",
            )
            self.assertEqual(
                connection.execute("PRAGMA busy_timeout").fetchone()[0],
                0,
            )
            with self.assertRaises(sqlite3.OperationalError):
                connection.execute(
                    "INSERT INTO metadata(key,value) "
                    "VALUES('forbidden','write')"
                )
        finally:
            if connection is not None:
                connection.close()
            installation.data_root.chmod(0o700)
        self.assertEqual(
            {path.name for path in installation.data_root.iterdir()},
            expected_entries,
        )

    def test_environment_cannot_redirect_installation(self) -> None:
        installation_path = self.runtime.initialize_runtime(
            self.base / "data", (self.sessions,), self.config
        )
        with mock.patch.dict(
            os.environ,
            {
                "CODEX_HOME": "/tmp/redirected",
                "SKILL_EVOLVER_DATA": "/tmp/redirected",
                "PYTHONPATH": "/tmp/redirected",
            },
        ):
            installation = self.runtime.load_installation(installation_path)
        self.assertEqual(installation.data_root, (self.base / "data").resolve())

    def test_failed_schema_creation_rolls_back_version_and_tables(self) -> None:
        installation_path = self.runtime.initialize_runtime(
            self.base / "data", (self.sessions,), self.config
        )
        installation = self.runtime.load_installation(installation_path)
        installation.database.unlink()
        broken = """
        CREATE TABLE should_rollback(id INTEGER PRIMARY KEY);
        CREATE TABLE broken(
        """
        with mock.patch.object(self.runtime, "SCHEMA_SQL", broken):
            with self.assertRaises(sqlite3.Error):
                self.runtime.open_database(installation)
        connection = sqlite3.connect(installation.database)
        tables = {
            row[0]
            for row in connection.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            )
        }
        version = connection.execute("PRAGMA user_version").fetchone()[0]
        connection.close()
        self.assertNotIn("should_rollback", tables)
        self.assertEqual(version, 0)
        self.assertFalse(
            Path(f"{installation.database}-journal").exists()
        )

    def test_newer_schema_and_symlinked_transcript_root_fail_closed(self) -> None:
        installation_path = self.runtime.initialize_runtime(
            self.base / "data", (self.sessions,), self.config
        )
        installation = self.runtime.load_installation(installation_path)
        connection = sqlite3.connect(installation.database)
        connection.execute("PRAGMA user_version = 2")
        connection.close()
        with self.assertRaisesRegex(ValueError, "unsupported_database_schema"):
            self.runtime.open_database(installation)

        link = self.base / "sessions-link"
        link.symlink_to(self.sessions, target_is_directory=True)
        with self.assertRaisesRegex(ValueError, "root_symlink"):
            self.runtime.initialize_runtime(
                self.base / "other-data", (link,), self.config
            )

    def test_existing_unsafe_root_is_rejected_without_permission_repair(self) -> None:
        root = self.base / "unsafe-data"
        root.mkdir(mode=0o755)
        root.chmod(0o755)
        with self.assertRaisesRegex(
            ValueError, "private_directory_permissions"
        ):
            self.runtime.initialize_runtime(
                root, (self.sessions,), self.config
            )
        self.assertEqual(stat.S_IMODE(root.stat().st_mode), 0o755)

    def test_private_files_require_exact_mode_0600(self) -> None:
        installation_path = self.runtime.initialize_runtime(
            self.base / "data", (self.sessions,), self.config
        )
        installation = self.runtime.load_installation(installation_path)
        installation.config_path.chmod(0o400)
        with self.assertRaisesRegex(ValueError, "private_file_permissions"):
            self.runtime.load_installation(installation_path)

    def test_failed_initialization_leaves_no_partial_root_and_retry_works(self) -> None:
        root = self.base / "retry-data"
        broken = """
        CREATE TABLE should_not_survive(id INTEGER PRIMARY KEY);
        CREATE TABLE broken(
        """
        with mock.patch.object(self.runtime, "SCHEMA_SQL", broken):
            with self.assertRaises(sqlite3.Error):
                self.runtime.initialize_runtime(
                    root, (self.sessions,), self.config
                )
        self.assertFalse(root.exists())
        installation_path = self.runtime.initialize_runtime(
            root, (self.sessions,), self.config
        )
        self.assertEqual(
            installation_path.resolve(),
            (root / "installation.json").resolve(),
        )

    def test_failed_post_placement_status_check_removes_root(self) -> None:
        root = self.base / "post-placement-failure"
        real_open_database = self.runtime.open_database

        def fail_read_only(installation, read_only=False):
            if read_only:
                raise sqlite3.OperationalError("status open failed")
            return real_open_database(installation, read_only=read_only)

        with mock.patch.object(
            self.runtime,
            "open_database",
            side_effect=fail_read_only,
        ):
            with self.assertRaisesRegex(
                sqlite3.OperationalError, "status open failed"
            ):
                self.runtime.initialize_runtime(
                    root, (self.sessions,), self.config
                )
        self.assertFalse(root.exists())
        installation_path = self.runtime.initialize_runtime(
            root, (self.sessions,), self.config
        )
        self.assertEqual(
            installation_path.resolve(),
            (root / "installation.json").resolve(),
        )


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: Run the store tests and verify RED**

Run:

```bash
/usr/bin/python3 -m unittest discover \
  -s skill-evolver/skills/skill-evolver/tests \
  -p 'test_capture.py' \
  -v
```

Expected: FAIL because the probe entrypoint has no production store,
`initialize_runtime`, `Config`, or SQLite schema.

- [ ] **Step 3: Replace the probe entrypoint with the production store foundation**

Replace `skill-evolver/skills/skill-evolver/scripts/evolver.py` completely
with:

```python
#!/usr/bin/env python3
from __future__ import annotations

import argparse
import hashlib
import json
import os
import secrets
import shutil
import sqlite3
import stat
import sys
import tempfile
import unicodedata
from dataclasses import dataclass
from pathlib import Path
from typing import Optional, Sequence
from urllib.parse import quote

VERSION = "skill-evolver 0.1.0"
SCHEMA_VERSION = 1
MAX_HOOK_BYTES = 65_536

DEFAULTS = {
    "pending_retention_days": 14,
    "pending_limit_sessions": 200,
    "raw_metadata_ttl_days": 30,
    "session_dedupe_days": 180,
    "spool_limit_files": 200,
    "spool_limit_bytes": 10_485_760,
    "review_batch_sessions": 5,
    "max_transcript_bytes": 2_097_152,
    "max_transcript_records": 100,
    "max_review_batch_bytes": 8_388_608,
    "max_candidates_per_session": 1,
    "max_candidates_per_batch": 3,
    "lease_seconds": 600,
    "lease_heartbeat_seconds": 60,
}

SCHEMA_SQL = """
CREATE TABLE review_batches (
    id INTEGER PRIMARY KEY,
    status TEXT NOT NULL,
    started_at TEXT NOT NULL,
    finished_at TEXT,
    session_count INTEGER NOT NULL DEFAULT 0,
    generation_count INTEGER NOT NULL DEFAULT 0,
    candidate_count INTEGER NOT NULL DEFAULT 0,
    exclusion_counts_json TEXT NOT NULL DEFAULT '{}'
);
CREATE TABLE review_items (
    id INTEGER PRIMARY KEY,
    session_key TEXT NOT NULL UNIQUE,
    raw_session_id TEXT,
    diagnostic_turn_id TEXT,
    generation INTEGER NOT NULL DEFAULT 1 CHECK(generation >= 1),
    transcript_epoch INTEGER NOT NULL DEFAULT 0 CHECK(transcript_epoch >= 0),
    status TEXT NOT NULL,
    binding_status TEXT NOT NULL DEFAULT 'accepted',
    cwd TEXT,
    transcript_path TEXT,
    transcript_size INTEGER,
    transcript_mtime_ns INTEGER,
    transcript_device INTEGER,
    transcript_inode INTEGER,
    observed_boundary INTEGER NOT NULL DEFAULT 0 CHECK(observed_boundary >= 0),
    last_stop_ns INTEGER NOT NULL CHECK(last_stop_ns >= 0),
    reviewed_boundary INTEGER NOT NULL DEFAULT 0 CHECK(reviewed_boundary >= 0),
    frozen_epoch INTEGER,
    frozen_from INTEGER,
    frozen_to INTEGER,
    frozen_locator_json TEXT,
    batch_id INTEGER REFERENCES review_batches(id),
    first_stop_at TEXT NOT NULL,
    last_stop_at TEXT NOT NULL,
    pending_since TEXT,
    review_started_at TEXT,
    reviewed_at TEXT,
    excluded_reason TEXT,
    error_code TEXT,
    lease_owner TEXT,
    lease_expires_at TEXT,
    raw_metadata_expires_at TEXT NOT NULL,
    dedupe_expires_at TEXT NOT NULL,
    raw_redacted_at TEXT
);
CREATE TABLE candidates (
    id INTEGER PRIMARY KEY,
    fingerprint TEXT NOT NULL UNIQUE,
    target_identity TEXT NOT NULL,
    target_skill TEXT NOT NULL,
    target_path TEXT,
    problem_category TEXT NOT NULL,
    target_locator TEXT NOT NULL,
    proposal_intent TEXT NOT NULL,
    conflict_group TEXT,
    problem_summary TEXT NOT NULL,
    proposal_summary TEXT NOT NULL,
    validation_plan TEXT NOT NULL,
    risk_level TEXT NOT NULL,
    status TEXT NOT NULL,
    occurrence_count INTEGER NOT NULL DEFAULT 1,
    first_seen_at TEXT NOT NULL,
    last_seen_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    tombstone_until TEXT
);
CREATE TABLE candidate_evidence (
    candidate_id INTEGER NOT NULL REFERENCES candidates(id),
    review_item_id INTEGER REFERENCES review_items(id) ON DELETE SET NULL,
    session_key TEXT NOT NULL,
    generation INTEGER NOT NULL,
    signal_type TEXT NOT NULL,
    source_kind TEXT NOT NULL,
    summary TEXT NOT NULL,
    created_at TEXT NOT NULL,
    UNIQUE(candidate_id, session_key, signal_type)
);
CREATE TABLE metadata (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL
);
CREATE INDEX idx_review_items_status_pending
    ON review_items(status, pending_since, id);
CREATE INDEX idx_review_items_lease
    ON review_items(status, lease_expires_at);
CREATE INDEX idx_review_items_dedupe
    ON review_items(dedupe_expires_at);
CREATE INDEX idx_candidates_status_updated
    ON candidates(status, updated_at);
"""


@dataclass(frozen=True)
class Installation:
    data_root: Path
    transcript_roots: tuple[Path, ...]
    python: Path
    config_path: Path
    identity_key: Path
    database: Path
    spool: Path


@dataclass(frozen=True)
class Config:
    capture_paused: bool
    exclude_roots: tuple[Path, ...]
    pending_retention_days: int
    pending_limit_sessions: int
    raw_metadata_ttl_days: int
    session_dedupe_days: int
    spool_limit_files: int
    spool_limit_bytes: int
    review_batch_sessions: int
    max_transcript_bytes: int
    max_transcript_records: int
    max_review_batch_bytes: int
    max_candidates_per_session: int
    max_candidates_per_batch: int
    lease_seconds: int
    lease_heartbeat_seconds: int


def canonical_json_bytes(value: object) -> bytes:
    return json.dumps(
        value, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    ).encode("utf-8")


def sha256_json(value: object) -> str:
    return hashlib.sha256(canonical_json_bytes(value)).hexdigest()


def fsync_directory(path: Path) -> None:
    descriptor = os.open(str(path), os.O_RDONLY)
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def atomic_write_bytes(path: Path, value: bytes, mode: int = 0o600) -> None:
    descriptor, temporary = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    temporary_path = Path(temporary)
    try:
        os.fchmod(descriptor, mode)
        with os.fdopen(descriptor, "wb") as stream:
            stream.write(value)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary_path, path)
        os.chmod(path, mode)
        fsync_directory(path.parent)
    except BaseException:
        temporary_path.unlink(missing_ok=True)
        raise


def atomic_write_json(path: Path, payload: object, mode: int = 0o600) -> None:
    atomic_write_bytes(path, canonical_json_bytes(payload) + b"\n", mode)


def private_directory(path: Path) -> Path:
    if path.is_symlink():
        raise ValueError("private_directory_symlink")
    resolved = path.resolve(strict=True)
    info = resolved.stat()
    if (
        not stat.S_ISDIR(info.st_mode)
        or info.st_uid != os.getuid()
        or stat.S_IMODE(info.st_mode) != 0o700
    ):
        raise ValueError("private_directory_permissions")
    return resolved


def private_file(path: Path) -> Path:
    if path.is_symlink():
        raise ValueError("private_file_symlink")
    resolved = path.resolve(strict=True)
    info = resolved.stat()
    if (
        not stat.S_ISREG(info.st_mode)
        or info.st_uid != os.getuid()
        or stat.S_IMODE(info.st_mode) != 0o600
    ):
        raise ValueError("private_file_permissions")
    return resolved


def canonical_roots(values: object, *, allow_empty: bool) -> tuple[Path, ...]:
    if not isinstance(values, list) and not isinstance(values, tuple):
        raise ValueError("invalid_root_list")
    if not allow_empty and not values:
        raise ValueError("invalid_root_list")
    roots: list[Path] = []
    for value in values:
        requested = Path(str(value)).expanduser()
        if requested.is_symlink():
            raise ValueError("root_symlink")
        root = requested.resolve(strict=True)
        if not root.is_dir():
            raise ValueError("invalid_root")
        roots.append(root)
    return tuple(roots)


def validate_transcript_root(path: Path) -> Path:
    requested = path.expanduser()
    if requested.is_symlink():
        raise ValueError("transcript_root_symlink")
    try:
        resolved = requested.resolve(strict=True)
    except (FileNotFoundError, NotADirectoryError):
        raise ValueError("transcript_root_not_directory") from None
    info = resolved.stat()
    if not stat.S_ISDIR(info.st_mode):
        raise ValueError("transcript_root_not_directory")
    if info.st_uid != os.getuid():
        raise ValueError("transcript_root_owner")
    if stat.S_IMODE(info.st_mode) & stat.S_IWOTH:
        raise ValueError("transcript_root_permissions")
    return resolved


def path_identity(path: Path) -> str:
    normalized = unicodedata.normalize(
        "NFC",
        os.path.normpath(str(path)),
    )
    return unicodedata.normalize("NFC", normalized.casefold())


def validate_transcript_separation(
    data_root: Path, transcript_roots: tuple[Path, ...]
) -> None:
    data_parts = Path(path_identity(data_root)).parts
    for transcript_root in transcript_roots:
        transcript_parts = Path(path_identity(transcript_root)).parts
        if (
            data_parts[: len(transcript_parts)] == transcript_parts
            or transcript_parts[: len(data_parts)] == data_parts
        ):
            raise ValueError("data_transcript_overlap")


def initialize_runtime(
    data_root: Path,
    transcript_roots: tuple[Path, ...],
    config: dict[str, object],
) -> Path:
    requested = data_root.expanduser()
    if requested.is_symlink():
        raise ValueError("data_root_symlink")
    root = requested.parent.resolve(strict=True) / requested.name
    fixed_transcripts = tuple(
        validate_transcript_root(path)
        for path in canonical_roots(transcript_roots, allow_empty=False)
    )
    validate_transcript_separation(root, fixed_transcripts)
    if root.exists():
        private_directory(root)
        raise ValueError("runtime_already_initialized")
    excludes = canonical_roots(config.get("exclude_roots", []), allow_empty=True)
    allowed = set(DEFAULTS) | {"capture_paused", "exclude_roots"}
    if set(config) - allowed:
        raise ValueError("invalid_config_keys")
    paused = config.get("capture_paused", False)
    if type(paused) is not bool:
        raise ValueError("invalid_config_capture_paused")
    merged: dict[str, object] = {
        **DEFAULTS,
        "capture_paused": paused,
        "exclude_roots": [str(path) for path in excludes],
    }
    for key in DEFAULTS:
        if key in config:
            merged[key] = config[key]
    for key in DEFAULTS:
        value = merged[key]
        if type(value) is not int or value <= 0:
            raise ValueError(f"invalid_config_{key}")
    if merged["max_candidates_per_session"] != 1:
        raise ValueError("invalid_config_max_candidates_per_session")

    staging = Path(
        tempfile.mkdtemp(prefix=f".{root.name}.", dir=root.parent)
    )
    placed = False
    try:
        staging = private_directory(staging)
        spool = staging / "spool"
        spool.mkdir(mode=0o700)
        private_directory(spool)
        staging_installation = staging / "installation.json"
        installation_payload = {
            "schema_version": 1,
            "data_root": str(staging),
            "transcript_roots": [
                str(path) for path in fixed_transcripts
            ],
            "python": "/usr/bin/python3",
        }
        atomic_write_json(staging_installation, installation_payload)
        atomic_write_json(staging / "config.json", merged)
        atomic_write_bytes(
            staging / "identity.key", secrets.token_bytes(32)
        )
        installation = load_installation(staging_installation)
        connection = open_database(installation)
        connection.close()
        atomic_write_json(
            staging_installation,
            {**installation_payload, "data_root": str(root)},
        )
        fsync_directory(staging)
        os.replace(staging, root)
        placed = True
        fsync_directory(root.parent)

        installation = load_installation(root / "installation.json")
        status_connection = open_database(installation, read_only=True)
        status_connection.close()
        fsync_directory(root)
    except BaseException:
        partial = root if placed else staging
        if partial.exists():
            shutil.rmtree(partial)
        fsync_directory(root.parent)
        raise
    return root / "installation.json"


def load_installation(path: Path) -> Installation:
    installation_path = private_file(path)
    payload = json.loads(installation_path.read_text(encoding="utf-8"))
    if payload.get("schema_version") != 1 or payload.get("python") != "/usr/bin/python3":
        raise ValueError("unsupported_installation")
    root = private_directory(Path(str(payload["data_root"])))
    if installation_path != root / "installation.json":
        raise ValueError("installation_root_mismatch")
    transcript_roots = tuple(
        validate_transcript_root(transcript_root)
        for transcript_root in canonical_roots(
            payload.get("transcript_roots"), allow_empty=False
        )
    )
    validate_transcript_separation(root, transcript_roots)
    installation = Installation(
        data_root=root,
        transcript_roots=transcript_roots,
        python=Path("/usr/bin/python3"),
        config_path=root / "config.json",
        identity_key=root / "identity.key",
        database=root / "evolver.db",
        spool=root / "spool",
    )
    private_file(installation.config_path)
    if len(private_file(installation.identity_key).read_bytes()) != 32:
        raise ValueError("invalid_identity_key")
    private_directory(installation.spool)
    return installation


def load_config(installation: Installation) -> Config:
    payload = json.loads(installation.config_path.read_text(encoding="utf-8"))
    allowed = set(DEFAULTS) | {"capture_paused", "exclude_roots"}
    if not isinstance(payload, dict) or set(payload) != allowed:
        raise ValueError("invalid_config_keys")
    paused = payload["capture_paused"]
    if type(paused) is not bool:
        raise ValueError("invalid_config_capture_paused")
    values: dict[str, int] = {}
    for key in DEFAULTS:
        value = payload[key]
        if type(value) is not int or value <= 0:
            raise ValueError(f"invalid_config_{key}")
        values[key] = value
    if values["max_candidates_per_session"] != 1:
        raise ValueError("invalid_config_max_candidates_per_session")
    return Config(
        capture_paused=paused,
        exclude_roots=canonical_roots(
            payload["exclude_roots"], allow_empty=True
        ),
        **values,
    )


def open_database(
    installation: Installation,
    read_only: bool = False,
) -> sqlite3.Connection:
    if installation.database.exists():
        private_file(installation.database)
    if read_only:
        if not installation.database.exists():
            raise ValueError("database_missing")
        database = (
            f"file:{quote(str(installation.database), safe='/')}?mode=ro"
        )
        connection = sqlite3.connect(
            database,
            uri=True,
            timeout=0,
            isolation_level=None,
        )
    else:
        connection = sqlite3.connect(
            str(installation.database),
            timeout=0,
            isolation_level=None,
        )
        os.chmod(installation.database, 0o600)
    connection.row_factory = sqlite3.Row
    try:
        connection.execute("PRAGMA foreign_keys = ON")
        connection.execute("PRAGMA busy_timeout = 0")
        if read_only:
            connection.execute("PRAGMA query_only = ON")
        version = int(connection.execute("PRAGMA user_version").fetchone()[0])
        if version > SCHEMA_VERSION:
            raise ValueError("unsupported_database_schema")
        mode = str(
            connection.execute(
                "PRAGMA journal_mode"
                if read_only or version
                else "PRAGMA journal_mode = DELETE"
            ).fetchone()[0]
        )
        if mode.lower() != "delete":
            raise ValueError("delete_journal_required")
        if version == 0:
            if read_only:
                raise ValueError("database_uninitialized")
            connection.executescript(
                "BEGIN IMMEDIATE;\n"
                + SCHEMA_SQL
                + f"\nPRAGMA user_version = {SCHEMA_VERSION};\n"
                + "COMMIT;\n"
            )
    except BaseException:
        if connection.in_transaction:
            connection.rollback()
        connection.close()
        raise
    return connection


def write_json_stdout(value: object) -> None:
    sys.stdout.buffer.write(canonical_json_bytes(value) + b"\n")


def cmd_init(args: argparse.Namespace) -> int:
    config = json.loads(Path(args.config).read_text(encoding="utf-8"))
    path = initialize_runtime(
        Path(args.data_root),
        tuple(Path(value) for value in args.transcript_root),
        config,
    )
    write_json_stdout({"status": "initialized", "installation": str(path)})
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="evolver.py")
    parser.add_argument("--version", action="version", version=VERSION)
    commands = parser.add_subparsers(dest="command", required=True)
    init = commands.add_parser("init")
    init.add_argument("--data-root", required=True)
    init.add_argument("--transcript-root", action="append", required=True)
    init.add_argument("--config", required=True)
    init.set_defaults(handler=cmd_init)
    return parser


def main(argv: Optional[Sequence[str]] = None) -> int:
    args = build_parser().parse_args(argv)
    return int(args.handler(args))


if __name__ == "__main__":
    raise SystemExit(main())
```

- [ ] **Step 4: Run the store tests and verify GREEN**

Run:

```bash
/usr/bin/python3 -m unittest discover \
  -s skill-evolver/skills/skill-evolver/tests \
  -p 'test_capture.py' \
  -v
```

Expected: all store tests PASS, including a staged `journal_mode=DELETE`
database with zero busy timeout, an immediate `mode=ro` open while its directory
is not writable, no auxiliary files created by that status open, read-only
SQLite rejecting a write, exact private-file modes, schema rollback leaving
`user_version == 0`, cleanup after both pre- and post-placement failures,
current-owner/non-world-writable transcript roots, and data/transcript
separation on both initialization and installation load.

- [ ] **Step 5: Commit the store**

```bash
git add \
  skill-evolver/skills/skill-evolver/scripts/evolver.py \
  skill-evolver/skills/skill-evolver/tests/test_capture.py
git commit -m "feat(skill-evolver): add private session store"
```

Expected: only the runtime and focused test file are committed.

---

### Task 3: Upsert HMAC Sessions and Fall Back to a Bounded Spool

**Files:**
- Modify: `skill-evolver/skills/skill-evolver/scripts/evolver.py`
- Modify: `skill-evolver/skills/skill-evolver/tests/test_capture.py`

**Interfaces:**
- Consumes: `Installation`, `Config`, fixed transcript roots, Hook stdin, and
  writable `open_database()`.
- Produces: `CapturedSessionStop`,
  `parse_session_stop(raw, installation, config) -> Optional[CapturedSessionStop]`,
  `session_key(installation, session_id) -> str`,
  `upsert_session(connection, event, key, config, now) -> str`,
  `spool_session_stop(installation, config, event, key, now) -> bool`,
  and the
  silent `enqueue-stop` command.
- The Hook observes transcript path/stat only. It never reads transcript bytes
  and never increments `transcript_epoch`.
- SQLite write contention is not waited out: `busy_timeout=0` makes the Hook
  attempt one short transaction before using the bounded spool.
- Spool fallback streams at most 201 directory entries before failing
  conservatively, validates only the bounded selected JSON files, and records
  overflow under a separate bounded nonblocking lock on the counter itself.

- [ ] **Step 1: Add failing identity, upsert, capacity, spool, and Hook tests**

Add these imports to `test_capture.py`:

```python
import fcntl
import hmac
import socket
import threading
import time
from dataclasses import replace
```

Add this class before the final `unittest.main()` block:

```python
class SessionCaptureTests(unittest.TestCase):
    def setUp(self) -> None:
        self.runtime = load_runtime()
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.base = Path(self.temp.name)
        self.sessions = self.base / "sessions"
        self.excluded = self.base / "excluded"
        for path in (self.sessions, self.excluded):
            path.mkdir(mode=0o700)
        self.config = {
            "capture_paused": False,
            "exclude_roots": [str(self.excluded)],
        }
        self.workspace = self.base / "workspace"
        self.workspace.mkdir(mode=0o700)
        self.installation_path = self.runtime.initialize_runtime(
            self.base / "data", (self.sessions,), self.config
        )
        self.installation = self.runtime.load_installation(self.installation_path)
        self.runtime_config = self.runtime.load_config(self.installation)
        self.transcript = self.sessions / "session.jsonl"
        self.transcript.write_text("not-json transcript body\n", encoding="utf-8")
        self.payload = {
            "hook_event_name": "Stop",
            "session_id": "raw-session-1",
            "cwd": str(self.workspace),
            "transcript_path": str(self.transcript),
        }

    def test_missing_turn_id_uses_exact_session_hmac(self) -> None:
        event = self.runtime.parse_session_stop(
            json.dumps(self.payload).encode(),
            self.installation,
            self.runtime_config,
        )
        assert event is not None
        expected = hmac.new(
            self.installation.identity_key.read_bytes(),
            b"session\0raw-session-1",
            "sha256",
        ).hexdigest()
        self.assertIsNone(event.diagnostic_turn_id)
        self.assertEqual(
            self.runtime.session_key(self.installation, event.session_id),
            expected,
        )

    def test_repeated_stops_converge_on_one_session_row(self) -> None:
        first_size = self.transcript.stat().st_size
        raw = json.dumps(self.payload).encode()
        self.assertEqual(
            self.runtime.enqueue_stop(
                self.installation, self.runtime_config, raw
            ),
            "inserted",
        )
        with self.transcript.open("ab") as stream:
            stream.write(b"still-not-json\n")
        second_size = self.transcript.stat().st_size
        self.assertEqual(
            self.runtime.enqueue_stop(
                self.installation, self.runtime_config, raw
            ),
            "advanced",
        )
        connection = self.runtime.open_database(self.installation)
        rows = connection.execute("SELECT * FROM review_items").fetchall()
        connection.close()
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["generation"], 1)
        self.assertEqual(rows[0]["transcript_epoch"], 0)
        self.assertEqual(rows[0]["reviewed_boundary"], 0)
        self.assertEqual(rows[0]["observed_boundary"], second_size)
        self.assertGreater(second_size, first_size)
        database_text = self.installation.database.read_bytes().decode(
            "utf-8", errors="ignore"
        )
        self.assertNotIn("not-json transcript body", database_text)

    def test_pending_capacity_is_sessions_not_stop_count(self) -> None:
        limited = self.runtime.Config(
            **{
                **self.runtime_config.__dict__,
                "pending_limit_sessions": 1,
            }
        )
        first = json.dumps(self.payload).encode()
        self.runtime.enqueue_stop(self.installation, limited, first)
        self.runtime.enqueue_stop(self.installation, limited, first)
        second = json.dumps(
            {**self.payload, "session_id": "raw-session-2"}
        ).encode()
        self.runtime.enqueue_stop(self.installation, limited, second)
        connection = self.runtime.open_database(self.installation)
        counts = dict(
            connection.execute(
                "SELECT status,COUNT(*) FROM review_items GROUP BY status"
            ).fetchall()
        )
        expired = connection.execute(
            """
            SELECT raw_session_id,diagnostic_turn_id,cwd,transcript_path,
              transcript_size,transcript_mtime_ns,transcript_device,
              transcript_inode,raw_redacted_at
            FROM review_items WHERE status='expired'
            """
        ).fetchone()
        connection.close()
        self.assertEqual(counts, {"expired": 1, "pending": 1})
        self.assertTrue(
            all(expired[name] is None for name in expired.keys()[:-1])
        )
        self.assertIsNotNone(expired["raw_redacted_at"])

    def test_concurrent_new_sessions_cannot_exceed_capacity(self) -> None:
        limited = self.runtime.Config(
            **{
                **self.runtime_config.__dict__,
                "pending_limit_sessions": 1,
            }
        )
        events = []
        for raw_session_id in ("concurrent-a", "concurrent-b"):
            event = self.runtime.parse_session_stop(
                json.dumps(
                    {**self.payload, "session_id": raw_session_id}
                ).encode(),
                self.installation,
                limited,
            )
            assert event is not None
            events.append(
                (
                    event,
                    self.runtime.session_key(
                        self.installation, event.session_id
                    ),
                )
            )
        barrier = threading.Barrier(2)
        failures: list[BaseException] = []
        locked: list[sqlite3.OperationalError] = []

        def capture(event, key) -> None:
            connection = self.runtime.open_database(self.installation)
            try:
                barrier.wait(timeout=2)
                self.runtime.upsert_session(
                    connection, event, key, limited, 2_000_000_000.0
                )
            except sqlite3.OperationalError as error:
                if "locked" in str(error).lower():
                    locked.append(error)
                else:
                    failures.append(error)
            except BaseException as error:
                failures.append(error)
            finally:
                connection.close()

        workers = [
            threading.Thread(target=capture, args=item, daemon=True)
            for item in events
        ]
        for worker in workers:
            worker.start()
        for worker in workers:
            worker.join(timeout=4)
        self.assertTrue(all(not worker.is_alive() for worker in workers))
        self.assertEqual(failures, [])
        self.assertLessEqual(len(locked), 1)
        connection = self.runtime.open_database(self.installation)
        pending = connection.execute(
            "SELECT COUNT(*) FROM review_items WHERE status='pending'"
        ).fetchone()[0]
        connection.close()
        self.assertEqual(pending, 1)

    def test_older_same_session_observation_cannot_regress_locator(self) -> None:
        old_event = self.runtime.parse_session_stop(
            json.dumps(self.payload).encode(),
            self.installation,
            self.runtime_config,
        )
        assert old_event is not None
        with self.transcript.open("ab") as stream:
            stream.write(b"newer-boundary\n")
        new_event = self.runtime.parse_session_stop(
            json.dumps(self.payload).encode(),
            self.installation,
            self.runtime_config,
        )
        assert new_event is not None
        key = self.runtime.session_key(
            self.installation, old_event.session_id
        )
        connection = self.runtime.open_database(self.installation)
        self.runtime.upsert_session(
            connection,
            new_event,
            key,
            self.runtime_config,
            2_000_000_000.0,
        )
        outcome = self.runtime.upsert_session(
            connection,
            old_event,
            key,
            self.runtime_config,
            2_000_000_010.0,
        )
        row = connection.execute(
            """
            SELECT transcript_path,transcript_device,transcript_inode,
              observed_boundary,last_stop_ns
            FROM review_items WHERE session_key=?
            """,
            (key,),
        ).fetchone()
        connection.close()
        self.assertEqual(outcome, "stale")
        self.assertEqual(row["transcript_path"], str(new_event.transcript_path))
        self.assertEqual(row["transcript_device"], new_event.transcript_device)
        self.assertEqual(row["transcript_inode"], new_event.transcript_inode)
        self.assertEqual(row["observed_boundary"], new_event.transcript_size)
        self.assertEqual(row["last_stop_ns"], new_event.observed_at_ns)

    def test_writer_contention_immediately_uses_bounded_spool(self) -> None:
        limited = self.runtime.Config(
            **{
                **self.runtime_config.__dict__,
                "spool_limit_files": 1,
            }
        )
        blocker = self.runtime.open_database(self.installation)
        self.assertEqual(
            blocker.execute("PRAGMA busy_timeout").fetchone()[0],
            0,
        )
        blocker.execute("BEGIN IMMEDIATE")
        started = time.monotonic()
        try:
            first = self.runtime.enqueue_stop(
                self.installation,
                limited,
                json.dumps(self.payload).encode(),
            )
            second = self.runtime.enqueue_stop(
                self.installation,
                limited,
                json.dumps(
                    {**self.payload, "session_id": "raw-session-2"}
                ).encode(),
            )
        finally:
            blocker.rollback()
            blocker.close()
        self.assertLess(time.monotonic() - started, 0.25)
        self.assertEqual(first, "spooled")
        self.assertEqual(second, "overflow")
        self.assertEqual(
            len(list(self.installation.spool.glob("*.json"))), 1
        )
        self.assertEqual(
            (self.installation.spool / "overflow.events").read_text(
                encoding="ascii"
            ),
            "1\n",
        )

    def test_overflow_counter_never_exceeds_hard_cap(self) -> None:
        counter = self.installation.spool / "overflow.events"
        counter.write_bytes(
            b"1\n" * (self.runtime.MAX_OVERFLOW_EVENT_BYTES // 2 - 1)
            + b"1"
        )
        counter.chmod(0o600)
        self.runtime.record_spool_overflow(self.installation)
        self.assertEqual(
            counter.stat().st_size,
            self.runtime.MAX_OVERFLOW_EVENT_BYTES - 1,
        )

    def test_concurrent_overflow_appends_share_one_hard_cap_decision(self) -> None:
        counter = self.installation.spool / "overflow.events"
        counter.write_bytes(
            b"1\n" * (self.runtime.MAX_OVERFLOW_EVENT_BYTES // 2 - 1)
        )
        counter.chmod(0o600)
        barrier = threading.Barrier(16)
        failures: list[BaseException] = []

        def overflow() -> None:
            try:
                barrier.wait(timeout=2)
                self.runtime.record_spool_overflow(self.installation)
            except BaseException as error:
                failures.append(error)

        workers = [
            threading.Thread(target=overflow, daemon=True) for _ in range(16)
        ]
        for worker in workers:
            worker.start()
        for worker in workers:
            worker.join(timeout=4)
        self.assertTrue(all(not worker.is_alive() for worker in workers))
        self.assertEqual(failures, [])
        self.assertEqual(
            counter.stat().st_size,
            self.runtime.MAX_OVERFLOW_EVENT_BYTES,
        )

    def test_spool_directory_scan_fails_boundedly_at_entry_limit(self) -> None:
        event = self.runtime.parse_session_stop(
            json.dumps(self.payload).encode(),
            self.installation,
            self.runtime_config,
        )
        assert event is not None
        key = self.runtime.session_key(self.installation, event.session_id)
        for index in range(self.runtime.MAX_SPOOL_SCAN_ENTRIES):
            path = self.installation.spool / f"junk-{index:03d}"
            path.write_bytes(b"")
            path.chmod(0o600)
        started = time.monotonic()
        spooled = self.runtime.spool_session_stop(
            self.installation,
            self.runtime_config,
            event,
            key,
            2_000_000_000.0,
        )
        self.assertFalse(spooled)
        self.assertLess(time.monotonic() - started, 0.25)
        self.assertEqual(
            list(self.installation.spool.glob("*.json")),
            [],
        )

    def test_spool_size_scan_rejects_nonprivate_json_file(self) -> None:
        event = self.runtime.parse_session_stop(
            json.dumps(self.payload).encode(),
            self.installation,
            self.runtime_config,
        )
        assert event is not None
        key = self.runtime.session_key(self.installation, event.session_id)
        unsafe = self.installation.spool / "unsafe.json"
        unsafe.write_bytes(b"{}\n")
        unsafe.chmod(0o644)
        with self.assertRaisesRegex(
            ValueError, "private_file_permissions"
        ):
            self.runtime.spool_session_stop(
                self.installation,
                self.runtime_config,
                event,
                key,
                2_000_000_000.0,
            )

    def test_spool_lock_contention_returns_within_hook_budget(self) -> None:
        event = self.runtime.parse_session_stop(
            json.dumps(self.payload).encode(),
            self.installation,
            self.runtime_config,
        )
        assert event is not None
        key = self.runtime.session_key(self.installation, event.session_id)
        lock_path = self.installation.spool / ".lock"
        lock_path.touch(mode=0o600)
        lock_path.chmod(0o600)
        with lock_path.open("r+b") as lock:
            fcntl.flock(lock.fileno(), fcntl.LOCK_EX)
            started = time.monotonic()
            spooled = self.runtime.spool_session_stop(
                self.installation,
                self.runtime_config,
                event,
                key,
                2_000_000_000.0,
            )
            elapsed = time.monotonic() - started
        self.assertFalse(spooled)
        self.assertLess(elapsed, 0.25)
        self.assertEqual(
            (self.installation.spool / "overflow.events").read_text(
                encoding="ascii"
            ),
            "1\n",
        )

    def test_hook_is_silent_network_free_and_never_mutates_a_skill(self) -> None:
        skill = self.base / "target-skill.md"
        skill.write_text("unchanged\n", encoding="utf-8")
        with mock.patch.object(
            socket, "socket", side_effect=AssertionError("network forbidden")
        ):
            result = self.runtime.enqueue_stop(
                self.installation,
                self.runtime_config,
                json.dumps(self.payload).encode(),
            )
        self.assertEqual(result, "inserted")
        self.assertEqual(skill.read_text(encoding="utf-8"), "unchanged\n")

        processes = (
            run_isolated(
                "enqueue-stop",
                "--installation",
                str(self.installation_path),
                stdin=json.dumps(self.payload).encode(),
            ),
            run_isolated(
                "enqueue-stop",
                "--installation",
                "/missing/installation.json",
                stdin=b"{",
            ),
            run_isolated(
                "enqueue-stop",
                "--installation",
                str(self.installation_path),
                stdin=b"x" * 65_537,
            ),
        )
        for process in processes:
            self.assertEqual(process.returncode, 0)
            self.assertEqual(process.stdout, b"")
            self.assertEqual(process.stderr, b"")
```

- [ ] **Step 2: Run capture tests and verify RED**

Run:

```bash
/usr/bin/python3 -m unittest discover \
  -s skill-evolver/skills/skill-evolver/tests \
  -p 'test_capture.py' \
  -v
```

Expected: FAIL because `CapturedSessionStop`, `parse_session_stop`,
`session_key`, `upsert_session`, and `enqueue-stop` do not exist.

- [ ] **Step 3: Add envelope validation and exact HMAC identity**

Add these imports beside the existing imports in `evolver.py`:

```python
import fcntl
import hmac
import time
```

Add below `Config`:

```python
@dataclass(frozen=True)
class CapturedSessionStop:
    session_id: str
    diagnostic_turn_id: Optional[str]
    cwd: Path
    transcript_path: Path
    transcript_size: int
    transcript_mtime_ns: int
    transcript_device: int
    transcript_inode: int
    observed_at_ns: int
```

Add these functions before `write_json_stdout()`:

```python
def within(path: Path, roots: tuple[Path, ...]) -> bool:
    for root in roots:
        try:
            if os.path.commonpath((str(path), str(root))) == str(root):
                return True
        except ValueError:
            continue
    return False


def bounded_string(
    payload: dict[str, object],
    name: str,
    maximum: int,
    *,
    required: bool = True,
) -> Optional[str]:
    value = payload.get(name)
    if value is None and not required:
        return None
    if (
        not isinstance(value, str)
        or not value
        or len(value.encode("utf-8")) > maximum
    ):
        raise ValueError(f"invalid_{name}")
    return value


def parse_session_stop(
    raw: bytes,
    installation: Installation,
    config: Config,
) -> Optional[CapturedSessionStop]:
    if len(raw) > MAX_HOOK_BYTES:
        raise ValueError("hook_input_too_large")
    payload = json.loads(raw)
    if not isinstance(payload, dict) or payload.get("hook_event_name") != "Stop":
        raise ValueError("not_stop_event")
    if config.capture_paused:
        return None
    cwd_text = bounded_string(payload, "cwd", 4_096)
    assert cwd_text is not None
    cwd_value = Path(cwd_text).expanduser()
    if cwd_value.is_symlink():
        raise ValueError("cwd_symlink")
    cwd = cwd_value.resolve(strict=True)
    if within(cwd, config.exclude_roots):
        return None
    transcript_text = bounded_string(payload, "transcript_path", 4_096)
    assert transcript_text is not None
    transcript_value = Path(transcript_text).expanduser()
    if transcript_value.is_symlink():
        raise ValueError("transcript_symlink")
    transcript = transcript_value.resolve(strict=True)
    if not within(transcript, installation.transcript_roots):
        raise ValueError("transcript_outside_roots")
    descriptor = os.open(
        str(transcript),
        os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0),
    )
    try:
        info = os.fstat(descriptor)
        observed_at_ns = time.time_ns()
    finally:
        os.close(descriptor)
    if not stat.S_ISREG(info.st_mode) or info.st_uid != os.getuid():
        raise ValueError("transcript_owner_or_type")
    raw_session_id = bounded_string(payload, "session_id", 512)
    assert raw_session_id is not None
    return CapturedSessionStop(
        session_id=raw_session_id,
        diagnostic_turn_id=bounded_string(
            payload, "turn_id", 512, required=False
        ),
        cwd=cwd,
        transcript_path=transcript,
        transcript_size=info.st_size,
        transcript_mtime_ns=info.st_mtime_ns,
        transcript_device=info.st_dev,
        transcript_inode=info.st_ino,
        observed_at_ns=observed_at_ns,
    )


def session_key(installation: Installation, session_id: str) -> str:
    return hmac.new(
        installation.identity_key.read_bytes(),
        b"session\0" + session_id.encode("utf-8"),
        "sha256",
    ).hexdigest()


def iso_utc(epoch: float) -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(epoch))
```

- [ ] **Step 4: Add transactional session upsert and capacity expiry**

Add immediately after `iso_utc()`:

```python
RAW_CLEAR_ASSIGNMENTS = """
raw_session_id=NULL,
diagnostic_turn_id=NULL,
cwd=NULL,
transcript_path=NULL,
transcript_size=NULL,
transcript_mtime_ns=NULL,
transcript_device=NULL,
transcript_inode=NULL,
error_code=NULL
"""


def expire_session_ids(
    connection: sqlite3.Connection,
    ids: list[int],
    reason: str,
    now: float,
) -> int:
    if not ids:
        return 0
    marks = ",".join("?" for _ in ids)
    changed = connection.execute(
        f"""
        UPDATE review_items
        SET status='expired', excluded_reason=?, reviewed_at=?,
            raw_redacted_at=?, {RAW_CLEAR_ASSIGNMENTS}
        WHERE id IN ({marks}) AND status='pending'
        """,
        (reason, iso_utc(now), iso_utc(now), *ids),
    ).rowcount
    if changed != len(ids):
        raise sqlite3.IntegrityError("session_expiry_race")
    return changed


def reserve_pending_session(
    connection: sqlite3.Connection,
    config: Config,
    now: float,
) -> int:
    count = int(
        connection.execute(
            "SELECT COUNT(*) FROM review_items WHERE status='pending'"
        ).fetchone()[0]
    )
    needed = max(count - config.pending_limit_sessions + 1, 0)
    if not needed:
        return 0
    ids = [
        int(row["id"])
        for row in connection.execute(
            """
            SELECT id FROM review_items
            WHERE status='pending'
            ORDER BY pending_since, id
            LIMIT ?
            """,
            (needed,),
        )
    ]
    changed = expire_session_ids(connection, ids, "capacity", now)
    connection.execute(
        """
        INSERT INTO metadata(key,value) VALUES('capacity_expired_count',?)
        ON CONFLICT(key) DO UPDATE SET
          value=CAST(CAST(value AS INTEGER)+excluded.value AS TEXT)
        """,
        (str(changed),),
    )
    return changed


def upsert_session(
    connection: sqlite3.Connection,
    event: CapturedSessionStop,
    key: str,
    config: Config,
    now: float,
) -> str:
    now_text = iso_utc(now)
    event_time_ns = event.observed_at_ns
    if type(event_time_ns) is not int or event_time_ns < 0:
        raise ValueError("invalid_observed_at_ns")
    connection.execute("BEGIN IMMEDIATE")
    try:
        row = connection.execute(
            "SELECT * FROM review_items WHERE session_key=?",
            (key,),
        ).fetchone()
        outcome = "duplicate"
        if row is None:
            reserve_pending_session(connection, config, now)
            connection.execute(
                """
                INSERT INTO review_items(
                  session_key,raw_session_id,diagnostic_turn_id,generation,
                  transcript_epoch,status,binding_status,cwd,transcript_path,
                  transcript_size,transcript_mtime_ns,transcript_device,
                  transcript_inode,observed_boundary,last_stop_ns,
                  reviewed_boundary,
                  first_stop_at,last_stop_at,pending_since,
                  raw_metadata_expires_at,dedupe_expires_at
                ) VALUES(
                  ?,?,?,1,0,'pending','accepted',?,?,?,?,?,?,?,?,0,?,?,?,?,?
                )
                """,
                (
                    key,
                    event.session_id,
                    event.diagnostic_turn_id,
                    str(event.cwd),
                    str(event.transcript_path),
                    event.transcript_size,
                    event.transcript_mtime_ns,
                    event.transcript_device,
                    event.transcript_inode,
                    event.transcript_size,
                    event_time_ns,
                    now_text,
                    now_text,
                    now_text,
                    iso_utc(now + config.raw_metadata_ttl_days * 86_400),
                    iso_utc(now + config.session_dedupe_days * 86_400),
                ),
            )
            outcome = "inserted"
        elif row["status"] != "expired" and row["raw_redacted_at"] is None:
            same_identity = (
                int(row["transcript_device"]) == event.transcript_device
                and int(row["transcript_inode"]) == event.transcript_inode
            )
            observed_boundary = int(row["observed_boundary"])
            prior_time_ns = int(row["last_stop_ns"])
            # ponytail: an equal clock tick keeps the current locator unless
            # the same inode grew; add a per-process sequence only if future
            # platforms cannot provide sufficient timestamp precision.
            stale_observation = event_time_ns < prior_time_ns or (
                event_time_ns == prior_time_ns
                and (
                    not same_identity
                    or event.transcript_size <= observed_boundary
                )
            )
            if stale_observation:
                outcome = "stale"
            else:
                shrank = same_identity and (
                    event.transcript_size < observed_boundary
                )
                pending_binding = row["binding_status"] == "pending_epoch"
                needs_rebind = pending_binding or not same_identity or shrank
                new_work = needs_rebind or (
                    same_identity
                    and event.transcript_size > int(row["reviewed_boundary"])
                )
                status = str(row["status"])
                generation = int(row["generation"])
                pending_since = row["pending_since"]
                if status not in {"pending", "reviewing"} and new_work:
                    reserve_pending_session(connection, config, now)
                    status = "pending"
                    generation += 1
                    pending_since = now_text
                elif status == "pending" and pending_since is None:
                    pending_since = now_text
                binding_status = (
                    "pending_epoch" if needs_rebind else "accepted"
                )
                error_code = (
                    "transcript_rebind_required" if needs_rebind else None
                )
                connection.execute(
                    """
                    UPDATE review_items
                    SET raw_session_id=?,diagnostic_turn_id=?,generation=?,
                        status=?,binding_status=?,cwd=?,transcript_path=?,
                        transcript_size=?,transcript_mtime_ns=?,
                        transcript_device=?,transcript_inode=?,
                        observed_boundary=?,last_stop_ns=?,last_stop_at=?,
                        pending_since=?,error_code=?
                    WHERE session_key=?
                    """,
                    (
                        event.session_id,
                        event.diagnostic_turn_id,
                        generation,
                        status,
                        binding_status,
                        str(event.cwd),
                        str(event.transcript_path),
                        event.transcript_size,
                        event.transcript_mtime_ns,
                        event.transcript_device,
                        event.transcript_inode,
                        event.transcript_size,
                        event_time_ns,
                        now_text,
                        pending_since,
                        error_code,
                        key,
                    ),
                )
                outcome = "advanced" if new_work else "duplicate"
        connection.execute(
            """
            INSERT INTO metadata(key,value) VALUES('last_hook_success_at',?)
            ON CONFLICT(key) DO UPDATE SET value=MAX(value,excluded.value)
            """,
            (now_text,),
        )
        pending = int(
            connection.execute(
                "SELECT COUNT(*) FROM review_items WHERE status='pending'"
            ).fetchone()[0]
        )
        if pending > config.pending_limit_sessions:
            raise sqlite3.IntegrityError("pending_session_capacity")
        connection.commit()
        return outcome
    except BaseException:
        connection.rollback()
        raise
```

- [ ] **Step 5: Add bounded spool fallback and the silent Hook command**

Add after `upsert_session()`:

```python
def spooled_stop_payload(
    event: CapturedSessionStop,
    key: str,
    config: Config,
    now: float,
) -> dict[str, object]:
    return {
        "schema_version": 1,
        "session_key": key,
        "raw_session_id": event.session_id,
        "diagnostic_turn_id": event.diagnostic_turn_id,
        "cwd": str(event.cwd),
        "transcript_path": str(event.transcript_path),
        "transcript_size": event.transcript_size,
        "transcript_mtime_ns": event.transcript_mtime_ns,
        "transcript_device": event.transcript_device,
        "transcript_inode": event.transcript_inode,
        "created_at": iso_utc(event.observed_at_ns / 1_000_000_000),
        "created_at_ns": event.observed_at_ns,
        "expires_at": iso_utc(now + config.pending_retention_days * 86_400),
    }


OVERFLOW_EVENT = b"1\n"
MAX_OVERFLOW_EVENT_BYTES = 65_536
MAX_SPOOL_SCAN_ENTRIES = 201


def record_spool_overflow(installation: Installation) -> None:
    path = installation.spool / "overflow.events"
    descriptor = os.open(
        str(path),
        os.O_WRONLY
        | os.O_APPEND
        | os.O_CREAT
        | getattr(os, "O_NOFOLLOW", 0),
        0o600,
    )
    try:
        info = os.fstat(descriptor)
        if (
            not stat.S_ISREG(info.st_mode)
            or info.st_uid != os.getuid()
            or stat.S_IMODE(info.st_mode) != 0o600
        ):
            raise ValueError("spool_overflow_permissions")
        if not acquire_spool_lock(descriptor, timeout_seconds=0.005):
            return
        info = os.fstat(descriptor)
        if info.st_size + len(OVERFLOW_EVENT) <= MAX_OVERFLOW_EVENT_BYTES:
            os.write(descriptor, OVERFLOW_EVENT)
            os.fsync(descriptor)
    finally:
        os.close(descriptor)


def acquire_spool_lock(descriptor: int, timeout_seconds: float = 0.05) -> bool:
    deadline = time.monotonic() + timeout_seconds
    while True:
        try:
            fcntl.flock(
                descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB
            )
            return True
        except BlockingIOError:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                return False
            time.sleep(min(0.005, remaining))


def spool_session_stop(
    installation: Installation,
    config: Config,
    event: CapturedSessionStop,
    key: str,
    now: float,
) -> bool:
    if type(event.observed_at_ns) is not int or event.observed_at_ns < 0:
        raise ValueError("invalid_observed_at_ns")
    lock_path = installation.spool / ".lock"
    if lock_path.is_symlink():
        raise ValueError("spool_lock_symlink")
    lock_path.touch(mode=0o600, exist_ok=True)
    with private_file(lock_path).open("r+b") as lock:
        if not acquire_spool_lock(lock.fileno()):
            record_spool_overflow(installation)
            return False
        files: list[Path] = []
        with os.scandir(installation.spool) as entries:
            for scanned, entry in enumerate(entries, start=1):
                # Seeing the 201st entry is enough to fail conservatively; do
                # not stat it or continue through an attacker-inflated spool.
                if scanned >= MAX_SPOOL_SCAN_ENTRIES:
                    record_spool_overflow(installation)
                    return False
                if not entry.name.endswith(".json"):
                    continue
                files.append(Path(entry.path))
                if len(files) >= config.spool_limit_files:
                    record_spool_overflow(installation)
                    return False
        total = 0
        for path in files:
            if path.is_symlink():
                raise ValueError("spool_payload_symlink")
            total += private_file(path).stat().st_size
        encoded = (
            canonical_json_bytes(
                spooled_stop_payload(event, key, config, now)
            )
            + b"\n"
        )
        if (
            len(files) >= config.spool_limit_files
            or total + len(encoded) > config.spool_limit_bytes
        ):
            record_spool_overflow(installation)
            return False
        destination = installation.spool / (
            f"{time.time_ns()}-{os.getpid()}-{secrets.token_hex(4)}.json"
        )
        atomic_write_bytes(destination, encoded)
        return True


def enqueue_stop(
    installation: Installation,
    config: Config,
    raw: bytes,
) -> str:
    event = parse_session_stop(raw, installation, config)
    if event is None:
        return "ignored"
    now = time.time()
    key = session_key(installation, event.session_id)
    try:
        connection = open_database(installation)
        try:
            return upsert_session(
                connection,
                event,
                key,
                config,
                now,
            )
        finally:
            connection.close()
    except sqlite3.Error:
        return (
            "spooled"
            if spool_session_stop(
                installation,
                config,
                event,
                key,
                now,
            )
            else "overflow"
        )


def cmd_enqueue_stop(args: argparse.Namespace) -> int:
    try:
        installation = load_installation(Path(args.installation))
        config = load_config(installation)
        raw = sys.stdin.buffer.read(MAX_HOOK_BYTES + 1)
        enqueue_stop(installation, config, raw)
    except Exception:
        pass
    return 0
```

Add this parser block before `return parser` in `build_parser()`:

```python
    enqueue = commands.add_parser("enqueue-stop")
    enqueue.add_argument("--installation", required=True)
    enqueue.set_defaults(handler=cmd_enqueue_stop)
```

- [ ] **Step 6: Run capture tests and verify GREEN**

Run:

```bash
/usr/bin/python3 -m unittest discover \
  -s skill-evolver/skills/skill-evolver/tests \
  -p 'test_capture.py' \
  -v
```

Expected: all tests PASS. Missing `turn_id` queues successfully, repeated Stops
produce one session row, zero-wait real writer contention uses the bounded spool,
capacity is one pending session in the focused test, the spool caps at one file
there, its directory scan stops at entry 201, unsafe spool files fail closed,
concurrent overflow appends never exceed 64 KiB, invalid JSONL transcript
content is never parsed, and every Hook subprocess is silent with exit code
`0`.

- [ ] **Step 7: Commit session capture**

```bash
git add \
  skill-evolver/skills/skill-evolver/scripts/evolver.py \
  skill-evolver/skills/skill-evolver/tests/test_capture.py
git commit -m "feat(skill-evolver): upsert bounded stop sessions"
```

Expected: only the runtime and its capture tests are committed.

---

### Task 4: Activate the Explicit Production Surface

**Files:**
- Modify: `.agents/plugins/marketplace.json`
- Modify: `skill-evolver/.codex-plugin/plugin.json`
- Modify: `skill-evolver/hooks/hooks.json`
- Modify: `skill-evolver/skills/skill-evolver/SKILL.md`
- Modify: `skill-evolver/skills/skill-evolver/references/runtime.json`
- Modify: `skill-evolver/skills/skill-evolver/tests/test_capture.py`

**Interfaces:**
- Consumes: exact schema-v2 report fields checked above.
- Produces: plugin version `0.1.0`, automatic command `enqueue-stop`, fixed
  installation locator
  `/Users/igyeongseob/.codex/skill-evolver/installation.json`, read-only
  `status`, and exact-command approval for `maintain` and later `review`.

- [ ] **Step 1: Add the failing production-surface test**

Add this class before the existing final `unittest.main()` block in
`skill-evolver/skills/skill-evolver/tests/test_capture.py`:

```python
class ProductionSurfaceTests(unittest.TestCase):
    def test_only_main_stop_is_an_automatic_writer(self) -> None:
        manifest = json.loads(
            (PLUGIN_ROOT / ".codex-plugin/plugin.json").read_text(encoding="utf-8")
        )
        hooks = json.loads(
            (PLUGIN_ROOT / "hooks/hooks.json").read_text(encoding="utf-8")
        )
        skill = (SKILL_ROOT / "SKILL.md").read_text(encoding="utf-8")
        runtime = json.loads(
            (SKILL_ROOT / "references/runtime.json").read_text(encoding="utf-8")
        )
        command = hooks["hooks"]["Stop"][0]["hooks"][0]["command"]
        marketplace = json.loads(
            (
                PLUGIN_ROOT.parent / ".agents/plugins/marketplace.json"
            ).read_text(encoding="utf-8")
        )

        self.assertEqual(marketplace["name"], "skill-evolver-dev")
        self.assertEqual(
            marketplace["plugins"],
            [
                {
                    "name": "skill-evolver",
                    "source": {
                        "source": "local",
                        "path": "./skill-evolver",
                    },
                    "policy": {
                        "installation": "AVAILABLE",
                        "authentication": "ON_INSTALL",
                    },
                    "category": "Developer Tools",
                }
            ],
        )
        self.assertEqual(manifest["version"], "0.1.0")
        self.assertEqual(set(hooks["hooks"]), {"Stop"})
        self.assertNotIn("matcher", hooks["hooks"]["Stop"][0])
        self.assertIn(" enqueue-stop ", command)
        self.assertNotIn("probe-", command)
        self.assertEqual(
            runtime["installation"],
            "/Users/igyeongseob/.codex/skill-evolver/installation.json",
        )
        self.assertIn("Status is read-only", skill)
        self.assertIn("exact command and global data root", skill)
        self.assertIn("No persistent writable-root grant", skill)
        self.assertIn("Never invoke after an ordinary task", skill)
        self.assertNotIn("SubagentStop", json.dumps(hooks))
```

- [ ] **Step 2: Run the test and verify RED**

Run:

```bash
/usr/bin/python3 -m unittest discover \
  -s skill-evolver/skills/skill-evolver/tests \
  -p 'test_capture.py' \
  -v
```

Expected: FAIL because the checked-in plugin is still the `0.0.2` schema-v2
probe, its Hook command is `probe-v2-stop`, and the repo marketplace still has
the old authentication policy.

- [ ] **Step 3: Replace the marketplace and four production metadata files**

Replace `.agents/plugins/marketplace.json` with:

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
        "authentication": "ON_INSTALL"
      },
      "category": "Developer Tools"
    }
  ]
}
```

Replace `skill-evolver/.codex-plugin/plugin.json` with:

```json
{
  "name": "skill-evolver",
  "version": "0.1.0",
  "description": "Queue bounded Codex sessions for explicit skill-improvement review.",
  "skills": "./skills/",
  "hooks": "./hooks/hooks.json"
}
```

Replace `skill-evolver/hooks/hooks.json` with:

```json
{
  "description": "Upsert bounded Stop metadata into the private Skill Evolver session queue.",
  "hooks": {
    "Stop": [
      {
        "hooks": [
          {
            "type": "command",
            "command": "/usr/bin/python3 -I \"$PLUGIN_ROOT/skills/skill-evolver/scripts/evolver.py\" enqueue-stop --installation \"/Users/igyeongseob/.codex/skill-evolver/installation.json\"",
            "timeout": 2
          }
        ]
      }
    ]
  }
}
```

Replace `skill-evolver/skills/skill-evolver/references/runtime.json` with:

```json
{
  "schema_version": 1,
  "version": "0.1.0",
  "installation": "/Users/igyeongseob/.codex/skill-evolver/installation.json"
}
```

Replace `skill-evolver/skills/skill-evolver/SKILL.md` with:

```markdown
---
name: skill-evolver
description: Inspect or explicitly manage the local skill-improvement inbox. Use only when the user explicitly names $skill-evolver or explicitly asks to manage that inbox. Never invoke after an ordinary task.
---

# Skill Evolver

This release changes no installed skill. Resolve `scripts/evolver.py` relative
to this file and run it only with `/usr/bin/python3 -I`. Read
`references/runtime.json`; never take the installation locator from an
environment variable, transcript, or model output.

- No argument or `status`: run `status --installation
  /Users/igyeongseob/.codex/skill-evolver/installation.json`.
  Status is read-only: it opens SQLite in `mode=ro`, reads only aggregate queue
  and spool metadata, does not import or delete spool files, and does not open
  transcripts.
- `maintain`: show the exact `maintain --installation
  /Users/igyeongseob/.codex/skill-evolver/installation.json` command,
  then request approval scoped to that exact command and global data root for
  this invocation. Do not run it before approval.
- `review`: is unavailable until the Review/Inbox plan is installed. That
  mutating workflow must use the same exact-command, exact-root approval rule.

No persistent writable-root grant is permitted. Do not request broad access,
run scheduled maintenance, call a model automatically, mutate a skill, or
silently substitute another command.
```

- [ ] **Step 4: Run the production-surface test and verify GREEN**

Run:

```bash
/usr/bin/python3 -m unittest discover \
  -s skill-evolver/skills/skill-evolver/tests \
  -p 'test_capture.py' \
  -v
```

Expected: all existing store/capture tests still pass and
`test_only_main_stop_is_an_automatic_writer ... ok`.

- [ ] **Step 5: Commit the production surface**

```bash
git add \
  .agents/plugins/marketplace.json \
  skill-evolver/.codex-plugin/plugin.json \
  skill-evolver/hooks/hooks.json \
  skill-evolver/skills/skill-evolver/SKILL.md \
  skill-evolver/skills/skill-evolver/references/runtime.json \
  skill-evolver/skills/skill-evolver/tests/test_capture.py
git commit -m "chore(skill-evolver): activate session capture surface"
```

Expected: only the six listed paths are committed. From the repository root,
`codex plugin marketplace add /Users/igyeongseob/Documents/오픈소스 --json`
now resolves `.agents/plugins/marketplace.json`, whose local source resolves
the checked-in `./skill-evolver` plugin.

---

### Task 5: Freeze Generations, Recover Leases, and Deduplicate Session Evidence

**Files:**
- Modify: `skill-evolver/skills/skill-evolver/scripts/evolver.py`
- Modify: `skill-evolver/skills/skill-evolver/tests/test_capture.py`

**Interfaces:**
- Consumes: one session row, accepted transcript identity, and explicit review
  ownership.
- Produces:
  `adopt_transcript_epoch(connection, session_key_value, embedded_session_key, device, inode, boundary, binding_mode, now) -> int`,
  `claim_review_generation(connection, session_key_value, owner, now, config) -> dict[str, object]`,
  `heartbeat_review_generation(connection, session_key_value, owner, now, config) -> bool`,
  `complete_review_generation(connection, session_key_value, owner, outcome, reason, now) -> dict[str, object]`,
  `recover_expired_review_leases(connection, now) -> int`, and
  `record_candidate_evidence(...) -> bool`.
- `adopt_transcript_epoch` receives the HMAC of the embedded transcript session
  ID from the later explicit Review adapter. It never parses a transcript and
  rejects a nonmatching key or any binding mode other than
  `embedded_session_id`.

- [ ] **Step 1: Add failing frozen-boundary, epoch, lease, and evidence tests**

Add this class before the final `unittest.main()` block in `test_capture.py`:

```python
class GenerationStateTests(unittest.TestCase):
    def setUp(self) -> None:
        self.runtime = load_runtime()
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.base = Path(self.temp.name)
        self.sessions = self.base / "sessions"
        self.excluded = self.base / "excluded"
        for path in (self.sessions, self.excluded):
            path.mkdir(mode=0o700)
        self.config = {
            "capture_paused": False,
            "exclude_roots": [str(self.excluded)],
        }
        self.workspace = self.base / "workspace"
        self.workspace.mkdir(mode=0o700)
        self.installation_path = self.runtime.initialize_runtime(
            self.base / "data", (self.sessions,), self.config
        )
        self.installation = self.runtime.load_installation(self.installation_path)
        self.runtime_config = self.runtime.load_config(self.installation)
        self.transcript = self.sessions / "session.jsonl"
        self.transcript.write_text('{"payload":{"role":"user"}}\n', encoding="utf-8")
        self.payload = {
            "hook_event_name": "Stop",
            "session_id": "generation-session",
            "cwd": str(self.workspace),
            "transcript_path": str(self.transcript),
        }
        self.raw = json.dumps(self.payload).encode()
        self.runtime.enqueue_stop(
            self.installation, self.runtime_config, self.raw
        )
        self.key = self.runtime.session_key(
            self.installation, self.payload["session_id"]
        )

    def test_stop_during_review_preserves_frozen_locator_and_reopens_generation(self) -> None:
        now = 2_000_000_000.0
        connection = self.runtime.open_database(self.installation)
        claim = self.runtime.claim_review_generation(
            connection, self.key, "owner-a", now, self.runtime_config
        )
        frozen_to = claim["review_to"]
        frozen_locator = claim["locator"]

        with self.transcript.open("ab") as stream:
            stream.write(b'{"payload":{"role":"assistant"}}\n')
        event = self.runtime.parse_session_stop(
            self.raw, self.installation, self.runtime_config
        )
        assert event is not None
        self.runtime.upsert_session(
            connection,
            event,
            self.key,
            self.runtime_config,
            now + 10,
        )
        during = connection.execute(
            """
            SELECT status,observed_boundary,frozen_to,frozen_locator_json
            FROM review_items WHERE session_key=?
            """,
            (self.key,),
        ).fetchone()
        self.assertEqual(during["status"], "reviewing")
        self.assertGreater(during["observed_boundary"], frozen_to)
        self.assertEqual(during["frozen_to"], frozen_to)
        self.assertEqual(json.loads(during["frozen_locator_json"]), frozen_locator)

        completed = self.runtime.complete_review_generation(
            connection,
            self.key,
            "owner-a",
            "reviewed",
            None,
            now + 20,
        )
        self.assertEqual(completed["status"], "pending")
        self.assertEqual(completed["generation"], 2)
        self.assertEqual(completed["reviewed_boundary"], frozen_to)
        second = self.runtime.claim_review_generation(
            connection, self.key, "owner-b", now + 30, self.runtime_config
        )
        connection.close()
        self.assertEqual(second["generation"], 2)
        self.assertEqual(second["review_from"], frozen_to)
        self.assertGreater(second["review_to"], frozen_to)

    def test_expired_lease_requeues_without_cursor_advancement(self) -> None:
        now = 2_000_000_000.0
        connection = self.runtime.open_database(self.installation)
        claim = self.runtime.claim_review_generation(
            connection, self.key, "owner-a", now, self.runtime_config
        )
        self.assertGreater(claim["review_to"], 0)
        recovered = self.runtime.recover_expired_review_leases(
            connection, now + self.runtime_config.lease_seconds + 1
        )
        row = connection.execute(
            """
            SELECT status,generation,reviewed_boundary,frozen_from,frozen_to,
              frozen_locator_json,lease_owner
            FROM review_items WHERE session_key=?
            """,
            (self.key,),
        ).fetchone()
        connection.close()
        self.assertEqual(recovered, 1)
        self.assertEqual(row["status"], "pending")
        self.assertEqual(row["generation"], 1)
        self.assertEqual(row["reviewed_boundary"], 0)
        self.assertIsNone(row["frozen_from"])
        self.assertIsNone(row["frozen_to"])
        self.assertIsNone(row["frozen_locator_json"])
        self.assertIsNone(row["lease_owner"])

    def test_different_inode_needs_explicit_embedded_binding_before_epoch_reset(self) -> None:
        now = 2_000_000_000.0
        connection = self.runtime.open_database(self.installation)
        self.runtime.claim_review_generation(
            connection, self.key, "owner-a", now, self.runtime_config
        )
        self.runtime.complete_review_generation(
            connection, self.key, "owner-a", "reviewed", None, now + 1
        )
        replacement = self.sessions / "replacement.jsonl"
        replacement.write_text(
            '{"payload":{"session_id":"generation-session","role":"user"}}\n',
            encoding="utf-8",
        )
        event = self.runtime.parse_session_stop(
            json.dumps(
                {**self.payload, "transcript_path": str(replacement)}
            ).encode(),
            self.installation,
            self.runtime_config,
        )
        assert event is not None
        self.runtime.upsert_session(
            connection,
            event,
            self.key,
            self.runtime_config,
            now + 2,
        )
        before = connection.execute(
            """
            SELECT transcript_epoch,reviewed_boundary,binding_status,generation
            FROM review_items WHERE session_key=?
            """,
            (self.key,),
        ).fetchone()
        self.assertEqual(tuple(before), (0, self.transcript.stat().st_size, "pending_epoch", 2))
        with self.assertRaisesRegex(ValueError, "transcript_binding_required"):
            self.runtime.claim_review_generation(
                connection, self.key, "owner-b", now + 3, self.runtime_config
            )
        with self.assertRaisesRegex(ValueError, "embedded_session_mismatch"):
            self.runtime.adopt_transcript_epoch(
                connection,
                self.key,
                "0" * 64,
                event.transcript_device,
                event.transcript_inode,
                event.transcript_size,
                "embedded_session_id",
                now + 4,
            )
        epoch = self.runtime.adopt_transcript_epoch(
            connection,
            self.key,
            self.key,
            event.transcript_device,
            event.transcript_inode,
            event.transcript_size,
            "embedded_session_id",
            now + 5,
        )
        claim = self.runtime.claim_review_generation(
            connection, self.key, "owner-b", now + 6, self.runtime_config
        )
        connection.close()
        self.assertEqual(epoch, 1)
        self.assertEqual(claim["transcript_epoch"], 1)
        self.assertEqual(claim["review_from"], 0)
        self.assertEqual(claim["review_to"], event.transcript_size)

    def test_candidate_evidence_is_unique_across_session_generations(self) -> None:
        now = 2_000_000_000.0
        connection = self.runtime.open_database(self.installation)
        row = connection.execute(
            "SELECT id FROM review_items WHERE session_key=?",
            (self.key,),
        ).fetchone()
        cursor = connection.execute(
            """
            INSERT INTO candidates(
              fingerprint,target_identity,target_skill,target_path,
              problem_category,target_locator,proposal_intent,conflict_group,
              problem_summary,proposal_summary,validation_plan,risk_level,
              status,first_seen_at,last_seen_at,updated_at
            ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
            """,
            (
                "f" * 64,
                "skill:test",
                "test",
                None,
                "verification",
                "completion claim",
                "require verification",
                None,
                "summary",
                "proposal",
                "run test",
                "low",
                "proposed",
                self.runtime.iso_utc(now),
                self.runtime.iso_utc(now),
                self.runtime.iso_utc(now),
            ),
        )
        candidate_id = int(cursor.lastrowid)
        first = self.runtime.record_candidate_evidence(
            connection,
            candidate_id,
            int(row["id"]),
            "verification_failure",
            "user_direct",
            "sanitized evidence",
            now,
        )
        connection.execute(
            "UPDATE review_items SET generation=2 WHERE id=?",
            (int(row["id"]),),
        )
        second = self.runtime.record_candidate_evidence(
            connection,
            candidate_id,
            int(row["id"]),
            "verification_failure",
            "user_direct",
            "same session later generation",
            now + 1,
        )
        count = connection.execute(
            "SELECT COUNT(*) FROM candidate_evidence"
        ).fetchone()[0]
        connection.close()
        self.assertTrue(first)
        self.assertFalse(second)
        self.assertEqual(count, 1)
```

- [ ] **Step 2: Run generation tests and verify RED**

Run:

```bash
/usr/bin/python3 -m unittest discover \
  -s skill-evolver/skills/skill-evolver/tests \
  -p 'test_capture.py' \
  -v
```

Expected: FAIL because the epoch adoption, review lease, completion, recovery,
and evidence functions do not exist.

- [ ] **Step 3: Implement explicit epoch adoption and immutable review claims**

Add after `upsert_session()` in `evolver.py`:

```python
def adopt_transcript_epoch(
    connection: sqlite3.Connection,
    session_key_value: str,
    embedded_session_key: str,
    device: int,
    inode: int,
    boundary: int,
    binding_mode: str,
    now: float,
) -> int:
    if binding_mode != "embedded_session_id":
        raise ValueError("invalid_epoch_binding_mode")
    if not hmac.compare_digest(session_key_value, embedded_session_key):
        raise ValueError("embedded_session_mismatch")
    connection.execute("BEGIN IMMEDIATE")
    try:
        row = connection.execute(
            "SELECT * FROM review_items WHERE session_key=?",
            (session_key_value,),
        ).fetchone()
        if row is None or row["status"] != "pending":
            raise ValueError("session_not_pending")
        if row["binding_status"] != "pending_epoch":
            raise ValueError("epoch_adoption_not_required")
        if (
            int(row["transcript_device"]) != device
            or int(row["transcript_inode"]) != inode
            or int(row["observed_boundary"]) != boundary
        ):
            raise ValueError("epoch_locator_changed")
        epoch = int(row["transcript_epoch"]) + 1
        connection.execute(
            """
            UPDATE review_items
            SET transcript_epoch=?,reviewed_boundary=0,
                binding_status='accepted',error_code=NULL,pending_since=?
            WHERE session_key=?
            """,
            (epoch, iso_utc(now), session_key_value),
        )
        connection.commit()
        return epoch
    except BaseException:
        connection.rollback()
        raise


def _recover_expired_review_leases(
    connection: sqlite3.Connection,
    now: float,
) -> int:
    return connection.execute(
        """
        UPDATE review_items
        SET status='pending',batch_id=NULL,review_started_at=NULL,
            frozen_epoch=NULL,frozen_from=NULL,frozen_to=NULL,
            frozen_locator_json=NULL,lease_owner=NULL,lease_expires_at=NULL,
            pending_since=COALESCE(pending_since,?)
        WHERE status='reviewing' AND lease_expires_at < ?
        """,
        (iso_utc(now), iso_utc(now)),
    ).rowcount


def recover_expired_review_leases(
    connection: sqlite3.Connection,
    now: float,
) -> int:
    connection.execute("BEGIN IMMEDIATE")
    try:
        changed = _recover_expired_review_leases(connection, now)
        connection.commit()
        return changed
    except BaseException:
        connection.rollback()
        raise


def claim_review_generation(
    connection: sqlite3.Connection,
    session_key_value: str,
    owner: str,
    now: float,
    config: Config,
) -> dict[str, object]:
    if not owner or len(owner.encode("utf-8")) > 128:
        raise ValueError("invalid_lease_owner")
    connection.execute("BEGIN IMMEDIATE")
    try:
        _recover_expired_review_leases(connection, now)
        row = connection.execute(
            "SELECT * FROM review_items WHERE session_key=?",
            (session_key_value,),
        ).fetchone()
        if row is None or row["status"] != "pending":
            raise ValueError("session_not_pending")
        if row["binding_status"] != "accepted":
            raise ValueError("transcript_binding_required")
        review_from = int(row["reviewed_boundary"])
        review_to = int(row["observed_boundary"])
        if review_to <= review_from:
            raise ValueError("empty_generation")
        locator = {
            "path": str(row["transcript_path"]),
            "size": int(row["transcript_size"]),
            "mtime_ns": int(row["transcript_mtime_ns"]),
            "device": int(row["transcript_device"]),
            "inode": int(row["transcript_inode"]),
        }
        changed = connection.execute(
            """
            UPDATE review_items
            SET status='reviewing',review_started_at=?,frozen_epoch=?,
                frozen_from=?,frozen_to=?,frozen_locator_json=?,
                lease_owner=?,lease_expires_at=?
            WHERE session_key=? AND status='pending'
            """,
            (
                iso_utc(now),
                int(row["transcript_epoch"]),
                review_from,
                review_to,
                canonical_json_bytes(locator).decode("utf-8"),
                owner,
                iso_utc(now + config.lease_seconds),
                session_key_value,
            ),
        ).rowcount
        if changed != 1:
            raise sqlite3.IntegrityError("review_claim_race")
        connection.commit()
        return {
            "session_key": session_key_value,
            "generation": int(row["generation"]),
            "transcript_epoch": int(row["transcript_epoch"]),
            "review_from": review_from,
            "review_to": review_to,
            "locator": locator,
            "lease_owner": owner,
            "lease_expires_at": iso_utc(now + config.lease_seconds),
        }
    except BaseException:
        connection.rollback()
        raise
```

- [ ] **Step 4: Implement heartbeat, completion, and evidence uniqueness**

Add after `claim_review_generation()`:

```python
def heartbeat_review_generation(
    connection: sqlite3.Connection,
    session_key_value: str,
    owner: str,
    now: float,
    config: Config,
) -> bool:
    changed = connection.execute(
        """
        UPDATE review_items
        SET lease_expires_at=?
        WHERE session_key=? AND status='reviewing' AND lease_owner=?
          AND lease_expires_at>=?
        """,
        (
            iso_utc(now + config.lease_seconds),
            session_key_value,
            owner,
            iso_utc(now),
        ),
    ).rowcount
    return changed == 1


def complete_review_generation(
    connection: sqlite3.Connection,
    session_key_value: str,
    owner: str,
    outcome: str,
    reason: Optional[str],
    now: float,
) -> dict[str, object]:
    if outcome not in {"reviewed", "excluded"}:
        raise ValueError("invalid_review_outcome")
    if outcome == "excluded" and not reason:
        raise ValueError("missing_exclusion_reason")
    connection.execute("BEGIN IMMEDIATE")
    try:
        row = connection.execute(
            """
            SELECT * FROM review_items
            WHERE session_key=? AND status='reviewing' AND lease_owner=?
              AND lease_expires_at>=?
            """,
            (session_key_value, owner, iso_utc(now)),
        ).fetchone()
        if row is None:
            raise ValueError("review_lease_unavailable")
        frozen_to = int(row["frozen_to"])
        new_work = (
            row["binding_status"] != "accepted"
            or int(row["transcript_epoch"]) != int(row["frozen_epoch"])
            or int(row["observed_boundary"]) > frozen_to
        )
        status = "pending" if new_work else outcome
        generation = int(row["generation"]) + int(new_work)
        pending_since = iso_utc(now) if new_work else None
        connection.execute(
            """
            UPDATE review_items
            SET status=?,generation=?,reviewed_boundary=?,reviewed_at=?,
                pending_since=?,excluded_reason=?,batch_id=NULL,
                review_started_at=NULL,frozen_epoch=NULL,frozen_from=NULL,
                frozen_to=NULL,frozen_locator_json=NULL,lease_owner=NULL,
                lease_expires_at=NULL
            WHERE session_key=?
            """,
            (
                status,
                generation,
                frozen_to,
                iso_utc(now),
                pending_since,
                reason if outcome == "excluded" else None,
                session_key_value,
            ),
        )
        connection.commit()
        return {
            "session_key": session_key_value,
            "status": status,
            "generation": generation,
            "reviewed_boundary": frozen_to,
        }
    except BaseException:
        connection.rollback()
        raise


def record_candidate_evidence(
    connection: sqlite3.Connection,
    candidate_id: int,
    review_item_id: int,
    signal_type: str,
    source_kind: str,
    summary: str,
    now: float,
) -> bool:
    row = connection.execute(
        "SELECT session_key,generation FROM review_items WHERE id=?",
        (review_item_id,),
    ).fetchone()
    if row is None:
        raise ValueError("review_item_missing")
    changed = connection.execute(
        """
        INSERT OR IGNORE INTO candidate_evidence(
          candidate_id,review_item_id,session_key,generation,signal_type,
          source_kind,summary,created_at
        ) VALUES(?,?,?,?,?,?,?,?)
        """,
        (
            candidate_id,
            review_item_id,
            str(row["session_key"]),
            int(row["generation"]),
            signal_type,
            source_kind,
            summary,
            iso_utc(now),
        ),
    ).rowcount
    return changed == 1
```

- [ ] **Step 5: Run generation tests and verify GREEN**

Run:

```bash
/usr/bin/python3 -m unittest discover \
  -s skill-evolver/skills/skill-evolver/tests \
  -p 'test_capture.py' \
  -v
```

Expected: all tests PASS. A concurrent Stop changes only the latest observed
state, completion advances only to the frozen boundary and opens generation 2,
lease expiry leaves the cursor at zero, different inode stays in epoch 0 until
the matching embedded session key is supplied, and repeated evidence from
generation 2 does not create an independent occurrence.

- [ ] **Step 6: Commit generation state**

```bash
git add \
  skill-evolver/skills/skill-evolver/scripts/evolver.py \
  skill-evolver/skills/skill-evolver/tests/test_capture.py
git commit -m "feat(skill-evolver): freeze session review generations"
```

Expected: only the two listed files are committed.

---

### Task 6: Import Spool, Enforce Privacy Retention, and Expose Read-only Status

**Files:**
- Modify: `skill-evolver/skills/skill-evolver/scripts/evolver.py`
- Modify: `skill-evolver/skills/skill-evolver/tests/test_capture.py`

**Interfaces:**
- Consumes: private spool payloads, session rows, `Config`, and explicit
  maintenance authorization.
- Produces:
  `import_spool(connection, installation, config, now) -> dict[str, int]`,
  `run_maintenance(connection, installation, config, now) -> dict[str, int]`,
  `queue_status(connection, installation, now) -> dict[str, object]`,
  explicit mutating command `maintain`, and read-only command `status`.
- Maintenance deletes malformed or expired raw spool payloads instead of
  preserving an unbounded quarantine. Status only stats spool files and never
  imports, removes, or opens their JSON content.

- [ ] **Step 1: Add failing spool, retention, privacy, and status tests**

Add this import to `test_capture.py`:

```python
from argparse import Namespace
```

Add this class before the final `unittest.main()` block:

```python
class MaintenanceStatusTests(unittest.TestCase):
    def setUp(self) -> None:
        self.runtime = load_runtime()
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.base = Path(self.temp.name)
        self.sessions = self.base / "sessions"
        self.excluded = self.base / "excluded"
        for path in (self.sessions, self.excluded):
            path.mkdir(mode=0o700)
        self.config = {
            "capture_paused": False,
            "exclude_roots": [str(self.excluded)],
        }
        self.workspace = self.base / "workspace"
        self.workspace.mkdir(mode=0o700)
        self.installation_path = self.runtime.initialize_runtime(
            self.base / "data", (self.sessions,), self.config
        )
        self.installation = self.runtime.load_installation(self.installation_path)
        self.runtime_config = self.runtime.load_config(self.installation)
        self.transcript = self.sessions / "session.jsonl"
        self.transcript.write_text('{"payload":{"role":"user"}}\n', encoding="utf-8")

    def event(self, raw_session_id: str = "maintenance-session"):
        payload = {
            "hook_event_name": "Stop",
            "session_id": raw_session_id,
            "cwd": str(self.workspace),
            "transcript_path": str(self.transcript),
        }
        event = self.runtime.parse_session_stop(
            json.dumps(payload).encode(),
            self.installation,
            self.runtime_config,
        )
        assert event is not None
        return event

    def test_status_command_opens_read_only_immediately_after_init(self) -> None:
        database_before = self.installation.database.read_bytes()
        process = run_isolated(
            "status",
            "--installation",
            str(self.installation_path),
        )
        self.assertEqual(process.returncode, 0)
        self.assertEqual(process.stderr, b"")
        status = json.loads(process.stdout)
        self.assertEqual(status["pending_sessions"], 0)
        self.assertEqual(status["spool"]["files"], 0)
        self.assertEqual(
            self.installation.database.read_bytes(),
            database_before,
        )

    def test_spool_import_converges_by_session_and_deletes_invalid_payload(self) -> None:
        now = 2_000_000_000.0
        event = self.event()
        key = self.runtime.session_key(self.installation, event.session_id)
        self.assertTrue(
            self.runtime.spool_session_stop(
                self.installation, self.runtime_config, event, key, now
            )
        )
        self.assertTrue(
            self.runtime.spool_session_stop(
                self.installation, self.runtime_config, event, key, now + 1
            )
        )
        invalid = self.installation.spool / "invalid.json"
        invalid.write_text('{"raw_session_id":"secret"}\n', encoding="utf-8")
        invalid.chmod(0o600)
        outside = self.base / "outside.json"
        outside.write_text('{"private":"do-not-follow"}\n', encoding="utf-8")
        linked = self.installation.spool / "linked.json"
        linked.symlink_to(outside)

        connection = self.runtime.open_database(self.installation)
        result = self.runtime.import_spool(
            connection,
            self.installation,
            self.runtime_config,
            now + 2,
        )
        rows = connection.execute(
            "SELECT COUNT(*) FROM review_items"
        ).fetchone()[0]
        connection.close()
        self.assertEqual(result["spool_imported"], 1)
        self.assertEqual(result["spool_duplicates"], 1)
        self.assertEqual(result["spool_invalid_deleted"], 2)
        self.assertEqual(rows, 1)
        self.assertEqual(list(self.installation.spool.glob("*.json")), [])
        self.assertEqual(
            outside.read_text(encoding="utf-8"),
            '{"private":"do-not-follow"}\n',
        )

    def test_same_second_spool_replay_keeps_newest_different_inode(self) -> None:
        now = 2_000_000_000.25
        first = self.event("same-second-session")
        replacement = self.sessions / "replacement.jsonl"
        replacement.write_text(
            '{"payload":{"role":"assistant"}}\n', encoding="utf-8"
        )
        payload = {
            "hook_event_name": "Stop",
            "session_id": first.session_id,
            "cwd": str(self.workspace),
            "transcript_path": str(replacement),
        }
        second = self.runtime.parse_session_stop(
            json.dumps(payload).encode(),
            self.installation,
            self.runtime_config,
        )
        assert second is not None
        first = replace(
            first, observed_at_ns=2_000_000_000_250_000_001
        )
        second = replace(
            second, observed_at_ns=2_000_000_000_250_000_002
        )
        key = self.runtime.session_key(self.installation, first.session_id)
        self.assertTrue(
            self.runtime.spool_session_stop(
                self.installation,
                self.runtime_config,
                first,
                key,
                now,
            )
        )
        self.assertTrue(
            self.runtime.spool_session_stop(
                self.installation,
                self.runtime_config,
                second,
                key,
                now,
            )
        )
        connection = self.runtime.open_database(self.installation)
        result = self.runtime.import_spool(
            connection,
            self.installation,
            self.runtime_config,
            now + 1,
        )
        row = connection.execute(
            """
            SELECT transcript_path,transcript_inode,observed_boundary,
              last_stop_ns,binding_status
            FROM review_items WHERE session_key=?
            """,
            (key,),
        ).fetchone()
        connection.close()
        self.assertEqual(result["spool_imported"], 1)
        self.assertEqual(result["spool_duplicates"], 1)
        self.assertEqual(row["transcript_path"], str(second.transcript_path))
        self.assertEqual(row["transcript_inode"], second.transcript_inode)
        self.assertEqual(row["observed_boundary"], second.transcript_size)
        self.assertEqual(
            row["last_stop_ns"], 2_000_000_000_250_000_002
        )
        self.assertEqual(row["binding_status"], "pending_epoch")

    def test_maintenance_expires_pending_and_spool_raw_data_then_dedupe_row(self) -> None:
        now = 2_000_000_000.0
        event = self.event()
        key = self.runtime.session_key(self.installation, event.session_id)
        connection = self.runtime.open_database(self.installation)
        self.runtime.upsert_session(
            connection, event, key, self.runtime_config, now
        )
        spooled = self.event("spooled-session")
        spooled_key = self.runtime.session_key(
            self.installation, spooled.session_id
        )
        self.runtime.spool_session_stop(
            self.installation,
            self.runtime_config,
            spooled,
            spooled_key,
            now,
        )

        expired = self.runtime.run_maintenance(
            connection,
            self.installation,
            self.runtime_config,
            now + 14 * 86_400 + 1,
        )
        row = connection.execute(
            """
            SELECT status,raw_session_id,diagnostic_turn_id,cwd,transcript_path,
              transcript_size,transcript_mtime_ns,transcript_device,
              transcript_inode,raw_redacted_at
            FROM review_items WHERE session_key=?
            """,
            (key,),
        ).fetchone()
        self.assertEqual(expired["pending_expired"], 1)
        self.assertEqual(expired["spool_expired"], 1)
        self.assertEqual(row["status"], "expired")
        self.assertTrue(
            all(row[name] is None for name in row.keys()[1:-1])
        )
        self.assertIsNotNone(row["raw_redacted_at"])
        self.assertEqual(list(self.installation.spool.glob("*.json")), [])

        deleted = self.runtime.run_maintenance(
            connection,
            self.installation,
            self.runtime_config,
            now + 181 * 86_400,
        )
        remaining = connection.execute(
            "SELECT COUNT(*) FROM review_items WHERE session_key=?",
            (key,),
        ).fetchone()[0]
        connection.close()
        self.assertEqual(deleted["dedupe_deleted"], 1)
        self.assertEqual(remaining, 0)

    def test_status_reports_sessions_generations_leases_spool_and_cleanup_read_only(self) -> None:
        now = 2_000_000_000.0
        event = self.event()
        key = self.runtime.session_key(self.installation, event.session_id)
        connection = self.runtime.open_database(self.installation)
        self.runtime.upsert_session(
            connection, event, key, self.runtime_config, now
        )
        connection.execute(
            "UPDATE review_items SET generation=3 WHERE session_key=?",
            (key,),
        )
        connection.close()
        waiting = self.installation.spool / "waiting.json"
        waiting.write_text("{}\n", encoding="utf-8")
        waiting.chmod(0o600)
        overflow = self.installation.spool / "overflow.events"
        overflow.write_bytes(
            b"1\n" * (self.runtime.MAX_OVERFLOW_EVENT_BYTES // 2 - 1)
            + b"1"
        )
        overflow.chmod(0o600)
        self.transcript.unlink()

        read_only = self.runtime.open_database(
            self.installation, read_only=True
        )
        status = self.runtime.queue_status(
            read_only, self.installation, now + 10
        )
        read_only.close()
        self.assertEqual(status["pending_sessions"], 1)
        self.assertEqual(status["pending_generations"], 1)
        self.assertEqual(status["generation_count_total"], 3)
        self.assertEqual(status["leases"], {"active": 0, "expired": 0})
        self.assertEqual(status["spool"]["files"], 1)
        self.assertEqual(status["spool"]["bytes"], waiting.stat().st_size)
        self.assertEqual(status["spool"]["overflow_total"], 32_767)
        self.assertTrue(status["spool"]["overflow_counter_saturated"])
        self.assertTrue(waiting.exists())

        captured: list[dict[str, object]] = []
        with mock.patch.object(
            self.runtime,
            "run_maintenance",
            side_effect=AssertionError("status mutation"),
        ), mock.patch.object(
            self.runtime,
            "import_spool",
            side_effect=AssertionError("status import"),
        ), mock.patch.object(
            self.runtime,
            "write_json_stdout",
            side_effect=captured.append,
        ):
            result = self.runtime.cmd_status(
                Namespace(installation=str(self.installation_path))
            )
        self.assertEqual(result, 0)
        self.assertEqual(captured[0]["pending_sessions"], 1)
        self.assertTrue(waiting.exists())
```

- [ ] **Step 2: Run maintenance/status tests and verify RED**

Run:

```bash
/usr/bin/python3 -m unittest discover \
  -s skill-evolver/skills/skill-evolver/tests \
  -p 'test_capture.py' \
  -v
```

Expected: FAIL because `import_spool`, `run_maintenance`, `queue_status`,
`maintain`, and `status` do not exist.

- [ ] **Step 3: Implement bounded spool import without transcript reads**

Add this import beside the imports in `evolver.py`:

```python
import calendar
```

Add after `spool_session_stop()`:

```python
def parse_iso_utc(value: str) -> float:
    return float(calendar.timegm(time.strptime(value, "%Y-%m-%dT%H:%M:%SZ")))


def event_from_spool(
    payload: object,
    installation: Installation,
) -> tuple[CapturedSessionStop, str, float, float]:
    if not isinstance(payload, dict) or payload.get("schema_version") != 1:
        raise ValueError("invalid_spool_schema")
    required = {
        "schema_version",
        "session_key",
        "raw_session_id",
        "diagnostic_turn_id",
        "cwd",
        "transcript_path",
        "transcript_size",
        "transcript_mtime_ns",
        "transcript_device",
        "transcript_inode",
        "created_at",
        "created_at_ns",
        "expires_at",
    }
    if set(payload) != required:
        raise ValueError("invalid_spool_fields")
    raw_session_id = payload["raw_session_id"]
    diagnostic = payload["diagnostic_turn_id"]
    if not isinstance(raw_session_id, str) or not raw_session_id:
        raise ValueError("invalid_spool_session")
    if diagnostic is not None and not isinstance(diagnostic, str):
        raise ValueError("invalid_spool_diagnostic")
    cwd = Path(str(payload["cwd"]))
    transcript = Path(str(payload["transcript_path"]))
    if not cwd.is_absolute() or not transcript.is_absolute():
        raise ValueError("invalid_spool_path")
    if not within(transcript, installation.transcript_roots):
        raise ValueError("invalid_spool_transcript_root")
    integers = [
        payload["transcript_size"],
        payload["transcript_mtime_ns"],
        payload["transcript_device"],
        payload["transcript_inode"],
    ]
    if any(type(value) is not int or value < 0 for value in integers):
        raise ValueError("invalid_spool_stat")
    created_at_ns = payload["created_at_ns"]
    if type(created_at_ns) is not int or created_at_ns < 0:
        raise ValueError("invalid_spool_created_at_ns")
    created_at = created_at_ns / 1_000_000_000
    if iso_utc(created_at) != payload["created_at"]:
        raise ValueError("inconsistent_spool_created_at")
    key = str(payload["session_key"])
    if not hmac.compare_digest(
        key, session_key(installation, raw_session_id)
    ):
        raise ValueError("invalid_spool_session_key")
    return (
        CapturedSessionStop(
            session_id=raw_session_id,
            diagnostic_turn_id=diagnostic,
            cwd=cwd,
            transcript_path=transcript,
            transcript_size=int(payload["transcript_size"]),
            transcript_mtime_ns=int(payload["transcript_mtime_ns"]),
            transcript_device=int(payload["transcript_device"]),
            transcript_inode=int(payload["transcript_inode"]),
            observed_at_ns=created_at_ns,
        ),
        key,
        created_at,
        parse_iso_utc(str(payload["expires_at"])),
    )


def import_spool(
    connection: sqlite3.Connection,
    installation: Installation,
    config: Config,
    now: float,
) -> dict[str, int]:
    imported = duplicates = invalid = expired = 0
    for path in sorted(installation.spool.glob("*.json")):
        try:
            if path.is_symlink():
                raise ValueError("spool_payload_symlink")
            private_file(path)
            if path.stat().st_size > MAX_HOOK_BYTES:
                raise ValueError("spool_payload_too_large")
            payload = json.loads(path.read_text(encoding="utf-8"))
            (
                event,
                key,
                created_at,
                expires_at,
            ) = event_from_spool(payload, installation)
            if now > expires_at:
                path.unlink()
                expired += 1
                continue
            outcome = upsert_session(
                connection,
                event,
                key,
                config,
                created_at,
            )
            imported += int(outcome == "inserted")
            duplicates += int(outcome != "inserted")
            path.unlink()
        except sqlite3.Error:
            raise
        except (KeyError, OSError, TypeError, ValueError, json.JSONDecodeError):
            path.unlink(missing_ok=True)
            invalid += 1
    fsync_directory(installation.spool)
    return {
        "spool_imported": imported,
        "spool_duplicates": duplicates,
        "spool_invalid_deleted": invalid,
        "spool_expired": expired,
    }
```

- [ ] **Step 4: Implement retention, raw cleanup, and dedupe deletion**

Add after `import_spool()`:

```python
def redact_raw_metadata(
    connection: sqlite3.Connection,
    ids: list[int],
    now: float,
) -> int:
    if not ids:
        return 0
    marks = ",".join("?" for _ in ids)
    return connection.execute(
        f"""
        UPDATE review_items
        SET status=CASE
              WHEN status IN ('pending','reviewing') THEN 'expired'
              ELSE status
            END,
            excluded_reason=CASE
              WHEN status IN ('pending','reviewing')
              THEN COALESCE(excluded_reason,'raw_metadata_ttl')
              ELSE excluded_reason
            END,
            reviewed_at=CASE
              WHEN status IN ('pending','reviewing') THEN ?
              ELSE reviewed_at
            END,
            batch_id=NULL,review_started_at=NULL,frozen_epoch=NULL,
            frozen_from=NULL,frozen_to=NULL,frozen_locator_json=NULL,
            lease_owner=NULL,lease_expires_at=NULL,raw_redacted_at=?,
            {RAW_CLEAR_ASSIGNMENTS}
        WHERE id IN ({marks}) AND raw_redacted_at IS NULL
        """,
        (iso_utc(now), iso_utc(now), *ids),
    ).rowcount


def run_maintenance(
    connection: sqlite3.Connection,
    installation: Installation,
    config: Config,
    now: float,
) -> dict[str, int]:
    counts = import_spool(connection, installation, config, now)
    leases_recovered = recover_expired_review_leases(connection, now)
    connection.execute("BEGIN IMMEDIATE")
    try:
        retention_ids = [
            int(row["id"])
            for row in connection.execute(
                """
                SELECT id FROM review_items
                WHERE status='pending' AND pending_since < ?
                ORDER BY pending_since,id
                """,
                (
                    iso_utc(
                        now - config.pending_retention_days * 86_400
                    ),
                ),
            )
        ]
        pending_expired = expire_session_ids(
            connection, retention_ids, "retention", now
        )
        pending_count = int(
            connection.execute(
                "SELECT COUNT(*) FROM review_items WHERE status='pending'"
            ).fetchone()[0]
        )
        capacity_ids = [
            int(row["id"])
            for row in connection.execute(
                """
                SELECT id FROM review_items
                WHERE status='pending'
                ORDER BY pending_since,id
                LIMIT ?
                """,
                (max(pending_count - config.pending_limit_sessions, 0),),
            )
        ]
        capacity_expired = expire_session_ids(
            connection, capacity_ids, "capacity", now
        )
        raw_ids = [
            int(row["id"])
            for row in connection.execute(
                """
                SELECT id FROM review_items
                WHERE raw_redacted_at IS NULL
                  AND raw_metadata_expires_at < ?
                """,
                (iso_utc(now),),
            )
        ]
        raw_redacted = redact_raw_metadata(connection, raw_ids, now)
        dedupe_deleted = connection.execute(
            """
            DELETE FROM review_items
            WHERE dedupe_expires_at < ?
              AND status NOT IN ('pending','reviewing')
            """,
            (iso_utc(now),),
        ).rowcount
        connection.execute(
            """
            INSERT INTO metadata(key,value) VALUES('last_maintenance_at',?)
            ON CONFLICT(key) DO UPDATE SET value=excluded.value
            """,
            (iso_utc(now),),
        )
        if capacity_expired:
            connection.execute(
                """
                INSERT INTO metadata(key,value)
                VALUES('capacity_expired_count',?)
                ON CONFLICT(key) DO UPDATE SET
                  value=CAST(CAST(value AS INTEGER)+excluded.value AS TEXT)
                """,
                (str(capacity_expired),),
            )
        connection.commit()
    except BaseException:
        connection.rollback()
        raise
    return {
        **counts,
        "leases_recovered": leases_recovered,
        "pending_expired": pending_expired,
        "capacity_expired": capacity_expired,
        "raw_redacted": raw_redacted,
        "dedupe_deleted": dedupe_deleted,
    }
```

- [ ] **Step 5: Implement transcript-free status and commands**

Add after `run_maintenance()`:

```python
def spool_inventory(installation: Installation) -> tuple[int, int]:
    count = total = 0
    for path in installation.spool.glob("*.json"):
        try:
            if path.is_symlink():
                continue
            private_file(path)
            total += path.stat().st_size
            count += 1
        except (FileNotFoundError, ValueError):
            continue
    return count, total


def queue_status(
    connection: sqlite3.Connection,
    installation: Installation,
    now: float,
) -> dict[str, object]:
    pending = connection.execute(
        """
        SELECT COUNT(*) AS sessions,MIN(pending_since) AS oldest
        FROM review_items WHERE status='pending'
        """
    ).fetchone()
    generations = int(
        connection.execute(
            "SELECT COALESCE(SUM(generation),0) FROM review_items"
        ).fetchone()[0]
    )
    status_counts = {
        str(row["status"]): int(row["count"])
        for row in connection.execute(
            """
            SELECT status,COUNT(*) AS count
            FROM review_items GROUP BY status
            """
        )
    }
    leases = connection.execute(
        """
        SELECT
          COALESCE(SUM(CASE WHEN lease_expires_at>=? THEN 1 ELSE 0 END),0)
            AS active,
          COALESCE(SUM(CASE WHEN lease_expires_at<? THEN 1 ELSE 0 END),0)
            AS expired
        FROM review_items WHERE status='reviewing'
        """,
        (iso_utc(now), iso_utc(now)),
    ).fetchone()
    binding_failures = int(
        connection.execute(
            """
            SELECT COUNT(*) FROM review_items
            WHERE binding_status='pending_epoch'
               OR error_code IN (
                 'transcript_rebind_required',
                 'session_binding_unavailable'
               )
            """
        ).fetchone()[0]
    )
    overdue_raw = int(
        connection.execute(
            """
            SELECT COUNT(*) FROM review_items
            WHERE raw_redacted_at IS NULL
              AND raw_metadata_expires_at < ?
            """,
            (iso_utc(now),),
        ).fetchone()[0]
    )
    metadata = {
        str(row["key"]): str(row["value"])
        for row in connection.execute(
            """
            SELECT key,value FROM metadata
            WHERE key IN (
              'last_hook_success_at',
              'last_maintenance_at',
              'capacity_expired_count'
            )
            """
        )
    }
    spool_files, spool_bytes = spool_inventory(installation)
    overflow_path = installation.spool / "overflow.events"
    try:
        overflow_bytes = (
            private_file(overflow_path).stat().st_size
            if overflow_path.exists() and not overflow_path.is_symlink()
            else 0
        )
    except (OSError, ValueError):
        overflow_bytes = 0
    oldest = pending["oldest"]
    return {
        "schema_version": SCHEMA_VERSION,
        "pending_sessions": int(pending["sessions"]),
        "pending_generations": int(pending["sessions"]),
        "generation_count_total": generations,
        "oldest_pending_age_seconds": (
            max(0, int(now - parse_iso_utc(str(oldest))))
            if oldest is not None
            else None
        ),
        "sessions_by_status": status_counts,
        "leases": {
            "active": int(leases["active"]),
            "expired": int(leases["expired"]),
        },
        "binding_failures": binding_failures,
        "spool": {
            "files": spool_files,
            "bytes": spool_bytes,
            "overflow_total": overflow_bytes // len(OVERFLOW_EVENT),
            "overflow_counter_saturated": (
                overflow_bytes + len(OVERFLOW_EVENT)
                > MAX_OVERFLOW_EVENT_BYTES
            ),
        },
        "last_hook_success_at": metadata.get("last_hook_success_at"),
        "raw_metadata_cleanup": {
            "overdue_sessions": overdue_raw,
            "last_maintenance_at": metadata.get("last_maintenance_at"),
        },
        "capacity_expired_total": int(
            metadata.get("capacity_expired_count", "0")
        ),
        "checked_at": iso_utc(now),
    }


def cmd_maintain(args: argparse.Namespace) -> int:
    installation = load_installation(Path(args.installation))
    config = load_config(installation)
    connection = open_database(installation)
    try:
        result = run_maintenance(
            connection, installation, config, time.time()
        )
    finally:
        connection.close()
    write_json_stdout(result)
    return 0


def cmd_status(args: argparse.Namespace) -> int:
    installation = load_installation(Path(args.installation))
    connection = open_database(installation, read_only=True)
    try:
        result = queue_status(connection, installation, time.time())
    finally:
        connection.close()
    write_json_stdout(result)
    return 0
```

Add these blocks before `return parser` in `build_parser()`:

```python
    maintain = commands.add_parser("maintain")
    maintain.add_argument("--installation", required=True)
    maintain.set_defaults(handler=cmd_maintain)
    status = commands.add_parser("status")
    status.add_argument("--installation", required=True)
    status.set_defaults(handler=cmd_status)
```

- [ ] **Step 6: Run maintenance/status tests and verify GREEN**

Run:

```bash
/usr/bin/python3 -m unittest discover \
  -s skill-evolver/skills/skill-evolver/tests \
  -p 'test_capture.py' \
  -v
```

Expected: all tests PASS. Two spooled Stops converge to one session, malformed
spool JSON is deleted, 14-day pending/spool data is removed, 180-day dedupe is
removed, status succeeds read-only immediately after initialization, and status
reports session/generation/lease/spool/privacy health after the transcript file
itself has been deleted, including saturation when no complete overflow record
fits below the 64-KiB cap.

- [ ] **Step 7: Commit maintenance and status**

```bash
git add \
  skill-evolver/skills/skill-evolver/scripts/evolver.py \
  skill-evolver/skills/skill-evolver/tests/test_capture.py
git commit -m "feat(skill-evolver): maintain session queue privacy"
```

Expected: only the two listed files are committed.

---

### Task 7: Prove the End-to-end Boundary and Publish Runtime Queue Evidence

**Files:**
- Modify: `skill-evolver/README.md`
- Modify: `skill-evolver/skills/skill-evolver/tests/test_capture.py`
- Create: `skill-evolver/docs/release-reports/runtime-queue.json`

**Interfaces:**
- Consumes: Tasks 1–6, the immutable schema-v2 PASS report, and the complete
  production plus frozen-probe test suite.
- Produces: exact production operations and a sanitized report bound to the
  implementation commit and production file digests.

- [ ] **Step 1: Add the failing operations-boundary test**

Add this method to `ProductionSurfaceTests` before the final
`unittest.main()` block in `test_capture.py`:

```python
    def test_readme_uses_v2_gate_and_scoped_mutation_approval(self) -> None:
        readme = (PLUGIN_ROOT / "README.md").read_text(encoding="utf-8")
        self.assertIn("docs/feasibility-report-v2.json", readme)
        self.assertIn('"next_action": "write_session_runtime_queue_plan"', readme)
        self.assertIn(
            "/Users/igyeongseob/.codex/skill-evolver/installation.json",
            readme,
        )
        self.assertIn("Status needs no write approval", readme)
        self.assertIn("approve only this exact command and data root", readme)
        self.assertIn("200 pending sessions", readme)
        self.assertIn("one row per session", readme)
        self.assertNotIn("200 turns", readme)
        self.assertIn("There is no `SubagentStop` registration.", readme)
```

- [ ] **Step 2: Run the operations test and verify RED**

Run:

```bash
/usr/bin/python3 -m unittest discover \
  -s skill-evolver/skills/skill-evolver/tests \
  -p 'test_capture.py' \
  -v
```

Expected: FAIL because the current README still documents the feasibility
probe rather than production session queue operations.

- [ ] **Step 3: Replace README with the exact production runbook**

Replace `skill-evolver/README.md` with:

````markdown
# Skill Evolver Session Queue

This Read-only MVP records one row per session. It does not run a model,
analyze a transcript in the Hook, create a candidate automatically, or change
an installed skill.

## Gate

`docs/feasibility-report-v2.json` must contain:

```json
{
  "schema_version": 2,
  "decision": "PASS",
  "next_action": "write_session_runtime_queue_plan"
}
```

Do not initialize production from the older report or the superseded
turn-level plan.

## Initialize

The fixed production root is `/Users/igyeongseob/.codex/skill-evolver`.
Creating it is an explicit write outside ordinary workspaces. Run this from a
user-controlled terminal, or approve only this exact command and data root.

Save `/private/tmp/skill-evolver-config.json` as:

```json
{
  "capture_paused": false,
  "exclude_roots": []
}
```

Then run:

```bash
/usr/bin/python3 -I \
  skill-evolver/skills/skill-evolver/scripts/evolver.py init \
  --data-root /Users/igyeongseob/.codex/skill-evolver \
  --transcript-root /Users/igyeongseob/.codex/sessions \
  --transcript-root /Users/igyeongseob/.codex/archived_sessions \
  --config /private/tmp/skill-evolver-config.json
```

The private root is mode `0700`; `installation.json`, `config.json`,
`identity.key`, SQLite, and spool files are mode `0600`. Runtime environment
variables cannot redirect the installation.

## Install and inspect

```bash
codex plugin marketplace add \
  /Users/igyeongseob/Documents/오픈소스 --json
codex plugin add skill-evolver@skill-evolver-dev --json
```

Use `/hooks` in CLI and Desktop. Trust only the one matcher-free `Stop` command
shown in `hooks/hooks.json`. There is no `SubagentStop` registration. The Hook
validates a bounded envelope, stats the transcript, computes an HMAC
`session_key`, and upserts SQLite or the bounded spool. It reads no transcript
bytes, calls no model or network, writes no skill, prints nothing, and exits
`0`.

Defaults are 200 pending sessions, 14-day pending retention, 30-day raw
metadata cleanup, 180-day session-key dedupe, and 200 spool files or 10 MiB.
Repeated Stops remain one row per session.

## Read-only status

Status needs no write approval:

```bash
/usr/bin/python3 -I \
  skill-evolver/skills/skill-evolver/scripts/evolver.py status \
  --installation \
  /Users/igyeongseob/.codex/skill-evolver/installation.json
```

It reports pending sessions and generations, oldest age, active/expired
leases, binding failures, excluded/expired counts, spool files/bytes/overflow,
last successful Hook time, and raw-metadata cleanup health. It opens SQLite in
read-only mode, never imports the spool, and never opens a transcript.

## Explicit maintenance

Maintenance imports spool files, recovers expired leases without advancing a
cursor, enforces session capacity and retention, clears raw metadata, and
removes expired HMAC dedupe rows. Before running it, approve only this exact
command and data root for this invocation:

```bash
/usr/bin/python3 -I \
  skill-evolver/skills/skill-evolver/scripts/evolver.py maintain \
  --installation \
  /Users/igyeongseob/.codex/skill-evolver/installation.json
```

Do not grant later ordinary tasks permanent write access. Explicit review in
the next release uses the same exact-command, exact-root approval rule.

To start with capture disabled, set `"capture_paused": true` in the
initialization config. Changing `config.json` later is also an explicit
global-root mutation and must use a user-controlled terminal or separately
approved exact command.

## Uninstall

Uninstalling stops new Hook writes but preserves the private inbox:

```bash
codex plugin remove skill-evolver@skill-evolver-dev --json
codex plugin marketplace remove skill-evolver-dev --json
```
````

- [ ] **Step 4: Run production and historical suites**

Run the production tests:

```bash
/usr/bin/python3 -m unittest discover \
  -s skill-evolver/skills/skill-evolver/tests \
  -p 'test_capture.py' \
  -v
```

Expected: all production tests PASS.

Run complete discovery:

```bash
/usr/bin/python3 -m unittest discover \
  -s skill-evolver/skills/skill-evolver/tests \
  -p 'test*.py' \
  -v
```

Expected: all production and frozen feasibility behavior tests PASS; exactly
the three obsolete probe-metadata/README assertions from Task 1 are skipped.
No other test is skipped or fails.

- [ ] **Step 5: Commit operations**

```bash
git add \
  skill-evolver/README.md \
  skill-evolver/skills/skill-evolver/tests/test_capture.py
git commit -m "docs(skill-evolver): document session queue operations"
```

Expected: only README and the operations-boundary test are committed.

- [ ] **Step 6: Generate the sanitized completion report from a passing suite**

Run:

```bash
/usr/bin/python3 - <<'PY'
import hashlib
import json
import subprocess
from pathlib import Path

repo = Path(".")
root = repo / "skill-evolver"
command = [
    "/usr/bin/python3",
    "-m",
    "unittest",
    "discover",
    "-s",
    str(root / "skills/skill-evolver/tests"),
    "-p",
    "test*.py",
    "-v",
]
completed = subprocess.run(
    command,
    stdout=subprocess.PIPE,
    stderr=subprocess.STDOUT,
    text=True,
    check=False,
)
assert completed.returncode == 0, completed.stdout
expected_skip_methods = (
    "test_manifest_and_hook_are_discoverable",
    "test_skill_is_explicit_only",
    "test_readme_stages_exact_private_v2_gate_inventory",
)
skip_lines = [
    line for line in completed.stdout.splitlines()
    if " ... skipped " in line
]
assert len(skip_lines) == 3, skip_lines
for method in expected_skip_methods:
    assert sum(method in line for line in skip_lines) == 1, (
        method,
        skip_lines,
    )
upstream = json.loads(
    (root / "docs/feasibility-report-v2.json").read_text(encoding="utf-8")
)
assert upstream["schema_version"] == 2
assert upstream["decision"] == "PASS"
assert upstream["next_action"] == "write_session_runtime_queue_plan"
tracked = (
    ".agents/plugins/marketplace.json",
    "skill-evolver/.codex-plugin/plugin.json",
    "skill-evolver/hooks/hooks.json",
    "skill-evolver/skills/skill-evolver/SKILL.md",
    "skill-evolver/skills/skill-evolver/references/runtime.json",
    "skill-evolver/skills/skill-evolver/scripts/evolver.py",
    "skill-evolver/skills/skill-evolver/tests/test_capture.py",
)
digests = {
    path: hashlib.sha256((repo / path).read_bytes()).hexdigest()
    for path in tracked
}
commit = subprocess.run(
    ["git", "rev-parse", "HEAD"],
    stdout=subprocess.PIPE,
    stderr=subprocess.PIPE,
    text=True,
    check=True,
).stdout.strip()
report = {
    "schema_version": 1,
    "decision": "PASS",
    "implementation_commit": commit,
    "upstream": {
        "path": "docs/feasibility-report-v2.json",
        "schema_version": 2,
        "decision": "PASS",
        "next_action": "write_session_runtime_queue_plan",
        "predecessor_sha256": (
            "ced4503adb44bd041de063c04e0c6c64d0831370fc12e96a920fe97244d8ae15"
        ),
    },
    "checks": {
        "hmac_session_identity": True,
        "one_row_per_session": True,
        "optional_turn_id": True,
        "frozen_generation_boundary": True,
        "stop_during_review": True,
        "lease_recovery_without_cursor_advance": True,
        "epoch_requires_explicit_embedded_binding": True,
        "candidate_evidence_unique_by_session": True,
        "session_capacity_retention_status": True,
        "bounded_spool_and_privacy_cleanup": True,
        "status_read_only": True,
        "hook_metadata_only": True,
    },
    "tests": {
        "command": " ".join(command),
        "result": "PASS",
        "obsolete_probe_surface_assertions_skipped": 3,
        "skipped_test_methods": list(expected_skip_methods),
    },
    "production_sha256": digests,
}
destination = root / "docs/release-reports/runtime-queue.json"
destination.parent.mkdir(parents=True, exist_ok=True)
destination.write_text(
    json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
    encoding="utf-8",
)
PY
```

Expected: the report says `PASS`, binds the current implementation commit and
seven production digests, contains no raw session ID, transcript path/content,
prompt, response, token, credential, or private data-root contents.

- [ ] **Step 7: Validate and commit the completion report**

Run:

```bash
/usr/bin/python3 -m json.tool \
  skill-evolver/docs/release-reports/runtime-queue.json >/dev/null

/usr/bin/python3 - <<'PY'
import json
from pathlib import Path

path = Path("skill-evolver/docs/release-reports/runtime-queue.json")
report = json.loads(path.read_text(encoding="utf-8"))
assert report["schema_version"] == 1
assert report["decision"] == "PASS"
assert all(report["checks"].values())
raw = path.read_text(encoding="utf-8")
for forbidden in (
    "raw_session_id",
    "diagnostic_turn_id",
    "transcript_path",
    "Authorization",
    "Bearer ",
    "PRIVATE KEY",
):
    assert forbidden not in raw, forbidden
print("runtime-queue-report-pass")
PY

git diff --check
```

Expected: `runtime-queue-report-pass`; JSON validation and
`git diff --check` exit `0`.

Commit:

```bash
git add skill-evolver/docs/release-reports/runtime-queue.json
git commit -m "docs(skill-evolver): record runtime queue completion"
```

Expected: only the generated report is committed.

## Final Verification

Run:

```bash
/usr/bin/python3 - <<'PY'
import subprocess

command = [
    "/usr/bin/python3",
    "-m",
    "unittest",
    "discover",
    "-s",
    "skill-evolver/skills/skill-evolver/tests",
    "-p",
    "test*.py",
    "-v",
]
completed = subprocess.run(
    command,
    stdout=subprocess.PIPE,
    stderr=subprocess.STDOUT,
    text=True,
    check=False,
)
print(completed.stdout, end="")
assert completed.returncode == 0
expected = {
    "test_manifest_and_hook_are_discoverable",
    "test_skill_is_explicit_only",
    "test_readme_stages_exact_private_v2_gate_inventory",
}
skip_lines = [
    line for line in completed.stdout.splitlines()
    if " ... skipped " in line
]
assert len(skip_lines) == 3, skip_lines
assert all(
    sum(method in line for line in skip_lines) == 1
    for method in expected
), skip_lines
PY

/usr/bin/python3 -m json.tool \
  .agents/plugins/marketplace.json >/dev/null
/usr/bin/python3 -m json.tool \
  skill-evolver/.codex-plugin/plugin.json >/dev/null
/usr/bin/python3 -m json.tool \
  skill-evolver/hooks/hooks.json >/dev/null
/usr/bin/python3 -m json.tool \
  skill-evolver/skills/skill-evolver/references/runtime.json >/dev/null
/usr/bin/python3 -m json.tool \
  skill-evolver/docs/release-reports/runtime-queue.json >/dev/null

git diff --check
git status --short
```

Expected:

- discovery exits `0`, with exactly the three documented historical surface
  skips and no other skip or failure;
- every JSON command exits `0`;
- `git diff --check` exits `0`;
- `git status --short` contains no Runtime Queue implementation path;
- no private file under `/Users/igyeongseob/.codex/skill-evolver` is staged.

## Completion Criteria

- The exact schema-v2 PASS report is the entry gate; the immutable predecessor
  digest and all CLI/Desktop surface fields are checked rather than guessed.
- The installed plugin registers only one main `Stop` Hook. The completed
  feasibility probe remains test-only and cannot be invoked by plugin metadata.
- Missing `turn_id` is accepted. HMAC `session_key` uses the exact
  `"session\0"` domain separator, and one SQLite row represents one session.
- Repeated Stops upsert the latest safe stat boundary. Observation time is
  captured beside `fstat`, so delayed Hooks and same-second spool replay cannot
  regress it. A Stop during review cannot change the frozen
  locator/epoch/from/to tuple.
- Successful review or explicit exclusion advances only to `frozen_to`;
  later bytes reopen the same session as the next generation.
- Lease expiry clears owner and frozen state, returns the row to `pending`, and
  leaves `reviewed_boundary` unchanged.
- A different inode leaves `transcript_epoch` unchanged and marks binding
  pending. Only explicit review with a matching embedded session HMAC adopts
  the next epoch and resets `reviewed_boundary` to zero.
- Candidate evidence uniqueness ignores generation and prevents one session
  from becoming a second independent occurrence.
- Capacity, retention, batch, health, and completion evidence are expressed in
  sessions and generations, never Stop/turn counts.
- Spool is bounded at 200 files/10 MiB, its fallback scans no more than 201
  directory entries, its lock wait is bounded, and its separately serialized
  64-KiB overflow counter cannot exceed its cap and discloses saturation.
  Malformed and 14-day-expired raw spool payloads are deleted, raw row metadata
  is cleared by 30 days, and HMAC dedupe rows are removed after 180 days.
- Initialization closes a complete staged `journal_mode=DELETE` database,
  atomically places it, and proves an immediate `mode=ro` status open without a
  directory write or auxiliary journal file. All connections use zero busy
  timeout; short writer transactions fail immediately into the bounded Hook
  spool. Status stats but does not import the spool, opens no transcript, and
  reports pending sessions/generations, oldest age, leases,
  excluded/expired/binding failures, spool count/bytes/overflow, last Hook
  success, and raw cleanup health.
- Review and maintenance writes require approval for the exact invocation and
  global root. No persistent write grant is created.
- The Hook has no model call, transcript parsing/read, network access, skill
  mutation, stdout, or stderr path and exits `0` on malformed/oversized input.
- Full production plus frozen-feasibility discovery passes, and the sanitized
  release report binds the implementation commit and production digests.
