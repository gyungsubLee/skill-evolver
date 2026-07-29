> **SUPERSEDED — DO NOT EXECUTE**
>
> Replaced by `docs/superpowers/plans/2026-07-28-skill-evolver-session-runtime-queue.md`.
> No task, command, code block, or completion criterion in this turn-level plan
> may be executed. It is retained only as historical context.

# Skill Evolver Read-only Runtime Queue Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace the temporary feasibility probe with a production `Stop` Hook that records allowed Codex CLI/Desktop turns in a private SQLite queue, falls back to a bounded spool, performs deterministic retention, and exposes transcript-free status.

**Architecture:** Keep one self-contained `evolver.py` entrypoint that validates the fixed installation, config, private data root, Hook envelope, and SQLite schema before writing. The Hook performs only bounded metadata capture and one short `BEGIN IMMEDIATE` dedupe/prune/insert transaction; explicit maintenance commands import spool files, enforce retention/capacity, and summarize health without opening transcripts.

**Tech Stack:** macOS Codex CLI/Desktop, `/usr/bin/python3` 3.9+, Python standard library (`argparse`, `dataclasses`, `fcntl`, `hashlib`, `hmac`, `json`, `os`, `pathlib`, `secrets`, `sqlite3`, `stat`, `tempfile`, `time`, `unittest`), SQLite WAL, Codex plugin `Stop` command Hook.

## Global Constraints

- Source specification: `skill-evolver/docs/superpowers/specs/2026-07-26-skill-evolver-design.md`.
- Strict precondition: `skill-evolver/docs/feasibility-report.json` exists, has `decision == "PASS"`, and its six sanitized fixtures pass the existing feasibility tests.
- Supported surfaces are macOS Codex CLI and Desktop only.
- Runtime interpreter is exactly `/usr/bin/python3`; minimum version is 3.9.
- Runtime code uses the Python standard library and SQLite only; no PostgreSQL, service, worker, network call, or new dependency.
- `evolver.py` remains one self-contained entrypoint because `python -I` excludes sibling modules from `sys.path`.
- The Hook registers only `Stop`, has no matcher, receives at most 64 KiB, emits no stdout/stderr, and exits `0` on every path.
- The Hook stores metadata and a transcript pointer/stat boundary only; it never parses transcript content, calls a model, or writes transcript text.
- Production data root is `/Users/igyeongseob/.codex/skill-evolver`, directory mode `0700`; private files are mode `0600`.
- Runtime ignores `CODEX_HOME`, `SKILL_EVOLVER_DATA`, and `PYTHONPATH`; `installation.json` is the sole locator.
- SQLite schema v1 contains only `review_batches`, `review_items`, `candidates`, `candidate_evidence`, and `metadata`; `candidates` has no `ready_evaluation_id`.
- Default limits are: pending 14 days/200 turns, raw metadata 30 days, event dedupe 180 days, spool 200 files/10 MiB, review 5 sessions/20 turns/8 MiB, lease 600 seconds with 60-second heartbeat.
- Installed user skills, staging, snapshots, prepare/evaluate/apply/undo, and candidate creation are outside this plan.
- Every commit stages exact Skill Evolver paths; never run `git add .` because `n8n/` and `neo4j/` are unrelated nested repositories.

## Preconditions and File Structure

Run this before Task 1:

```bash
/usr/bin/python3 - <<'PY'
import json
from pathlib import Path

root = Path("skill-evolver")
report = json.loads((root / "docs/feasibility-report.json").read_text(encoding="utf-8"))
assert report["decision"] == "PASS", report
fixtures = root / "skills/skill-evolver/tests/fixtures"
expected = {
    "access-cli.structure.json",
    "access-desktop.structure.json",
    "stop-cli.structure.json",
    "stop-desktop.structure.json",
    "transcript-cli.structure.json",
    "transcript-desktop.structure.json",
}
assert expected == {path.name for path in fixtures.glob("*.structure.json")}
print("feasibility-pass")
PY
```

Expected: `feasibility-pass`. Any assertion failure stops this plan; follow the feasibility report's session-level redesign action.

| Path | Responsibility |
| --- | --- |
| `skill-evolver/.codex-plugin/plugin.json` | Production plugin identity/version. |
| `skill-evolver/hooks/hooks.json` | Trusted production `Stop` command using the fixed installation path. |
| `skill-evolver/skills/skill-evolver/SKILL.md` | Explicit-only status contract; Review commands arrive in the next plan. |
| `skill-evolver/skills/skill-evolver/references/runtime.json` | Fixed `installation.json` locator. |
| `skill-evolver/skills/skill-evolver/scripts/evolver.py` | Self-contained installation, schema, enqueue, spool, maintenance, and status runtime. |
| `skill-evolver/skills/skill-evolver/tests/test_capture.py` | Production manifest, storage, Hook, dedupe, spool, retention, and status tests. |
| `skill-evolver/README.md` | Exact production initialization, install, trust, health, and uninstall commands. |

---

### Task 1: Lock the Production Plugin and Explicit-only Status Surface

**Files:**
- Modify: `skill-evolver/.codex-plugin/plugin.json`
- Modify: `skill-evolver/hooks/hooks.json`
- Modify: `skill-evolver/skills/skill-evolver/SKILL.md`
- Modify: `skill-evolver/skills/skill-evolver/references/runtime.json`
- Create: `skill-evolver/skills/skill-evolver/tests/test_capture.py`

**Interfaces:**
- Consumes: Feasibility `PASS` report and the plugin layout proven by the spike.
- Produces: production Hook command `enqueue-stop --installation /Users/igyeongseob/.codex/skill-evolver/installation.json` and explicit user action `$skill-evolver [status]`.

- [ ] **Step 1: Write the failing production metadata test**

Create `skill-evolver/skills/skill-evolver/tests/test_capture.py`:

```python
from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from support import PLUGIN_ROOT, SKILL_ROOT, load_runtime, run_isolated


class ProductionMetadataTests(unittest.TestCase):
    def test_manifest_hook_and_skill_are_production_read_only(self) -> None:
        manifest = json.loads(
            (PLUGIN_ROOT / ".codex-plugin/plugin.json").read_text(encoding="utf-8")
        )
        hooks = json.loads((PLUGIN_ROOT / "hooks/hooks.json").read_text(encoding="utf-8"))
        skill = (SKILL_ROOT / "SKILL.md").read_text(encoding="utf-8")
        command = hooks["hooks"]["Stop"][0]["hooks"][0]["command"]

        self.assertEqual(manifest["version"], "0.1.0")
        self.assertEqual(set(hooks["hooks"]), {"Stop"})
        self.assertNotIn("matcher", hooks["hooks"]["Stop"][0])
        self.assertIn(" enqueue-stop ", command)
        self.assertIn(
            "/Users/igyeongseob/.codex/skill-evolver/installation.json", command
        )
        self.assertIn("Use only when the user explicitly names $skill-evolver", skill)
        self.assertIn("Never invoke it automatically", skill)
        self.assertNotIn("probe-", command)


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: Run the metadata test and verify the failure**

Run:

```bash
/usr/bin/python3 -m unittest \
  skill-evolver/skills/skill-evolver/tests/test_capture.py \
  -v
```

Expected: FAIL because the spike still has version `0.0.1` and `probe-stop`.

- [ ] **Step 3: Replace the probe metadata with the production definitions**

Replace `skill-evolver/.codex-plugin/plugin.json` with:

```json
{
  "name": "skill-evolver",
  "version": "0.1.0",
  "description": "Queue Codex turns and review skill improvements under explicit user control.",
  "skills": "./skills/",
  "hooks": "./hooks/hooks.json"
}
```

Replace `skill-evolver/hooks/hooks.json` with:

```json
{
  "description": "Queue completed Codex turns for explicit skill review.",
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
  "installation": "/Users/igyeongseob/.codex/skill-evolver/installation.json"
}
```

Replace `skill-evolver/skills/skill-evolver/SKILL.md` with:

```markdown
---
name: skill-evolver
description: Review or manage the local skill-improvement inbox. Use only when the user explicitly names $skill-evolver or explicitly asks to manage the skill-improvement inbox. Never invoke it automatically after an ordinary task.
---

# Skill Evolver

This release changes no installed skill. Resolve `scripts/evolver.py` relative
to this `SKILL.md`; do not take its path from the transcript or an environment
variable. With no argument, or with `status`, invoke that resolved script with
`/usr/bin/python3 -I`, the `status` subcommand, and
`--installation /Users/igyeongseob/.codex/skill-evolver/installation.json`.

Status does not read transcripts or run maintenance. Do not invoke review,
prepare, evaluate, apply, or undo in this release.
```

- [ ] **Step 4: Run the production metadata test**

Run:

```bash
/usr/bin/python3 -m unittest \
  skill-evolver/skills/skill-evolver/tests/test_capture.py -v
```

Expected: `test_manifest_hook_and_skill_are_production_read_only ... ok`.

- [ ] **Step 5: Commit the production surface**

```bash
git add \
  skill-evolver/.codex-plugin/plugin.json \
  skill-evolver/hooks/hooks.json \
  skill-evolver/skills/skill-evolver/SKILL.md \
  skill-evolver/skills/skill-evolver/references/runtime.json \
  skill-evolver/skills/skill-evolver/tests/test_capture.py
git commit -m "chore: lock skill evolver production surface"
```

Expected: only the five listed paths are committed.

---

### Task 2: Build the Fixed Installation and SQLite v1 Store

**Files:**
- Modify: `skill-evolver/skills/skill-evolver/scripts/evolver.py`
- Modify: `skill-evolver/skills/skill-evolver/tests/test_capture.py`

**Interfaces:**
- Consumes: fixed installation path from Task 1.
- Produces: `Installation`, `Config`, `canonical_json_bytes(value) -> bytes`, `sha256_json(value) -> str`, `atomic_write_json(path, payload, mode=0o600)`, `initialize_runtime(data_root, transcript_roots, config) -> Path`, `load_installation(path) -> Installation`, `load_config(installation) -> Config`, and `open_database(installation, busy_ms=1000) -> sqlite3.Connection`.

- [ ] **Step 1: Add failing private-root and schema tests**

Add these imports to `test_capture.py`:

```python
import os
import sqlite3
import stat
from unittest import mock
```

Add this test class before the final `unittest.main()` block:

```python
class RuntimeStoreTests(unittest.TestCase):
    def setUp(self) -> None:
        self.runtime = load_runtime()
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.base = Path(self.temp.name)
        self.sessions = self.base / "sessions"
        self.skills = self.base / "skills"
        self.workspace = self.base / "workspace"
        for path in (self.sessions, self.skills, self.workspace):
            path.mkdir(mode=0o700)
        self.config = {
            "workspace_roots": [str(self.workspace)],
            "exclude_roots": [],
            "mutable_skill_roots": [str(self.skills)],
        }

    def test_init_creates_private_schema_v1_and_key(self) -> None:
        path = self.runtime.initialize_runtime(
            self.base / "data", (self.sessions,), self.config
        )
        installation = self.runtime.load_installation(path)
        config = self.runtime.load_config(installation)
        connection = self.runtime.open_database(installation)
        tables = {
            row[0]
            for row in connection.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            )
        }
        connection.close()

        self.assertEqual(config.pending_limit, 200)
        self.assertEqual(config.max_candidates_per_session, 1)
        self.assertEqual(config.snapshots_per_skill, 5)
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
        self.assertEqual(stat.S_IMODE(installation.data_root.stat().st_mode), 0o700)
        self.assertEqual(stat.S_IMODE(installation.identity_key.stat().st_mode), 0o600)
        self.assertEqual(len(installation.identity_key.read_bytes()), 32)

    def test_runtime_ignores_environment_and_rejects_newer_schema(self) -> None:
        path = self.runtime.initialize_runtime(
            self.base / "data", (self.sessions,), self.config
        )
        with mock.patch.dict(
            os.environ,
            {"CODEX_HOME": "/tmp/x", "SKILL_EVOLVER_DATA": "/tmp/y", "PYTHONPATH": "/tmp/z"},
        ):
            installation = self.runtime.load_installation(path)
        connection = sqlite3.connect(installation.database)
        connection.execute("PRAGMA user_version = 2")
        connection.close()
        with self.assertRaisesRegex(ValueError, "unsupported_database_schema"):
            self.runtime.open_database(installation)

    def test_failed_schema_initialization_leaves_no_partial_schema(self) -> None:
        path = self.runtime.initialize_runtime(
            self.base / "data", (self.sessions,), self.config
        )
        installation = self.runtime.load_installation(path)
        for suffix in ("", "-wal", "-shm"):
            Path(f"{installation.database}{suffix}").unlink(missing_ok=True)

        broken_schema = """
        CREATE TABLE should_rollback(id INTEGER PRIMARY KEY);
        CREATE TABLE broken(
        """
        with mock.patch.object(self.runtime, "SCHEMA_SQL", broken_schema):
            with self.assertRaises(sqlite3.Error):
                self.runtime.open_database(installation)

        connection = sqlite3.connect(installation.database)
        tables = {
            row[0]
            for row in connection.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            )
        }
        version = int(connection.execute("PRAGMA user_version").fetchone()[0])
        connection.close()

        self.assertNotIn("should_rollback", tables)
        self.assertEqual(version, 0)

    def test_empty_workspace_roots_is_valid_but_disables_capture(self) -> None:
        path = self.runtime.initialize_runtime(
            self.base / "data",
            (self.sessions,),
            {**self.config, "workspace_roots": []},
        )
        installation = self.runtime.load_installation(path)
        self.assertEqual(self.runtime.load_config(installation).workspace_roots, ())

    def test_candidates_per_session_config_is_pinned_to_one(self) -> None:
        path = self.runtime.initialize_runtime(
            self.base / "data",
            (self.sessions,),
            {**self.config, "max_candidates_per_session": 2},
        )
        installation = self.runtime.load_installation(path)
        with self.assertRaisesRegex(
            ValueError, "invalid_config_max_candidates_per_session"
        ):
            self.runtime.load_config(installation)

    def test_symlinked_config_root_is_rejected(self) -> None:
        link = self.base / "workspace-link"
        link.symlink_to(self.workspace, target_is_directory=True)
        with self.assertRaisesRegex(ValueError, "root_symlink"):
            self.runtime.initialize_runtime(
                self.base / "data",
                (self.sessions,),
                {**self.config, "workspace_roots": [str(link)]},
            )
```

- [ ] **Step 2: Run the store tests and verify the failure**

Run:

```bash
/usr/bin/python3 -m unittest \
  skill-evolver/skills/skill-evolver/tests/test_capture.py \
  -v
```

Expected: FAIL because the production installation and store interfaces do not exist.

- [ ] **Step 3: Replace `evolver.py` with the production store foundation**

Replace `skill-evolver/skills/skill-evolver/scripts/evolver.py` with:

```python
#!/usr/bin/env python3
from __future__ import annotations

import argparse
import calendar
import hashlib
import json
import os
import secrets
import sqlite3
import stat
import sys
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Optional, Sequence

VERSION = "skill-evolver 0.1.0"
SCHEMA_VERSION = 1
MAX_HOOK_BYTES = 65_536

DEFAULTS = {
    "pending_retention_days": 14,
    "pending_limit": 200,
    "raw_metadata_ttl_days": 30,
    "event_dedupe_days": 180,
    "spool_limit_files": 200,
    "spool_limit_bytes": 10_485_760,
    "max_transcript_bytes": 2_097_152,
    "max_transcript_messages": 100,
    "max_review_batch_bytes": 8_388_608,
    "review_batch_sessions": 5,
    "review_batch_turns": 20,
    "max_candidates_per_session": 1,
    "max_candidates_per_batch": 3,
    "lease_seconds": 600,
    "lease_heartbeat_seconds": 60,
    "purge_plan_ttl_seconds": 600,
    "deferred_candidate_days": 30,
    "rejected_tombstone_days": 90,
    "snapshots_per_skill": 5,
}

SCHEMA_SQL = """
CREATE TABLE review_batches (
    id INTEGER PRIMARY KEY,
    status TEXT NOT NULL,
    started_at TEXT NOT NULL,
    finished_at TEXT,
    session_count INTEGER NOT NULL DEFAULT 0,
    turn_count INTEGER NOT NULL DEFAULT 0,
    candidate_count INTEGER NOT NULL DEFAULT 0,
    exclusion_counts_json TEXT NOT NULL DEFAULT '{}'
);
CREATE TABLE review_items (
    id INTEGER PRIMARY KEY,
    event_key TEXT NOT NULL UNIQUE,
    session_id TEXT,
    turn_id TEXT,
    cwd TEXT,
    transcript_path TEXT,
    transcript_size INTEGER,
    transcript_mtime_ns INTEGER,
    transcript_device INTEGER,
    transcript_inode INTEGER,
    status TEXT NOT NULL,
    excluded_reason TEXT,
    batch_id INTEGER REFERENCES review_batches(id),
    created_at TEXT NOT NULL,
    review_started_at TEXT,
    reviewed_at TEXT,
    error_code TEXT,
    lease_owner TEXT,
    lease_expires_at TEXT,
    raw_redacted_at TEXT,
    dedupe_expires_at TEXT NOT NULL
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
CREATE INDEX idx_review_items_status_created
    ON review_items(status, created_at);
CREATE INDEX idx_review_items_session
    ON review_items(session_id, created_at);
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
    reports: Path


@dataclass(frozen=True)
class Config:
    workspace_roots: tuple[Path, ...]
    exclude_roots: tuple[Path, ...]
    mutable_skill_roots: tuple[Path, ...]
    pending_retention_days: int
    pending_limit: int
    raw_metadata_ttl_days: int
    event_dedupe_days: int
    spool_limit_files: int
    spool_limit_bytes: int
    max_transcript_bytes: int
    max_transcript_messages: int
    max_review_batch_bytes: int
    review_batch_sessions: int
    review_batch_turns: int
    max_candidates_per_session: int
    max_candidates_per_batch: int
    lease_seconds: int
    lease_heartbeat_seconds: int
    purge_plan_ttl_seconds: int
    deferred_candidate_days: int
    rejected_tombstone_days: int
    snapshots_per_skill: int


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
        or stat.S_IMODE(info.st_mode) & 0o077
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
        or stat.S_IMODE(info.st_mode) & 0o077
    ):
        raise ValueError("private_file_permissions")
    return resolved


def canonical_roots(values: object, allow_empty: bool) -> tuple[Path, ...]:
    if not isinstance(values, list) or (not allow_empty and not values):
        raise ValueError("invalid_root_list")
    roots = []
    for value in values:
        requested = Path(str(value)).expanduser()
        if requested.is_symlink():
            raise ValueError("root_symlink")
        root = requested.resolve(strict=True)
        if not root.is_dir():
            raise ValueError("invalid_root")
        roots.append(root)
    return tuple(roots)


def initialize_runtime(
    data_root: Path,
    transcript_roots: tuple[Path, ...],
    config: dict[str, object],
) -> Path:
    requested = data_root.expanduser()
    if requested.is_symlink():
        raise ValueError("data_root_symlink")
    root = requested.parent.resolve(strict=True) / requested.name
    root.mkdir(mode=0o700)
    root.chmod(0o700)
    root = private_directory(root)
    for name in ("spool", "spool/quarantine", "reports"):
        child = root / name
        child.mkdir(parents=True, mode=0o700)
        child.chmod(0o700)
        private_directory(child)
    merged = {**DEFAULTS, **config}
    canonical_roots(merged["workspace_roots"], allow_empty=True)
    canonical_roots(merged["exclude_roots"], allow_empty=True)
    canonical_roots(merged["mutable_skill_roots"], allow_empty=False)
    fixed_transcripts = tuple(path.resolve(strict=True) for path in transcript_roots)
    installation_path = root / "installation.json"
    atomic_write_json(
        installation_path,
        {
            "schema_version": 1,
            "data_root": str(root),
            "transcript_roots": [str(path) for path in fixed_transcripts],
            "python": "/usr/bin/python3",
        },
    )
    atomic_write_json(root / "config.json", merged)
    atomic_write_bytes(root / "identity.key", secrets.token_bytes(32))
    installation = load_installation(installation_path)
    connection = open_database(installation)
    connection.close()
    return installation_path


def load_installation(path: Path) -> Installation:
    installation_path = private_file(path)
    payload = json.loads(installation_path.read_text(encoding="utf-8"))
    if payload.get("schema_version") != 1 or payload.get("python") != "/usr/bin/python3":
        raise ValueError("unsupported_installation")
    root = private_directory(Path(str(payload["data_root"])))
    transcript_roots = canonical_roots(payload["transcript_roots"], allow_empty=False)
    installation = Installation(
        data_root=root,
        transcript_roots=transcript_roots,
        python=Path("/usr/bin/python3"),
        config_path=root / "config.json",
        identity_key=root / "identity.key",
        database=root / "evolver.db",
        spool=root / "spool",
        reports=root / "reports",
    )
    private_file(installation.config_path)
    key = private_file(installation.identity_key).read_bytes()
    if len(key) != 32:
        raise ValueError("invalid_identity_key")
    private_directory(installation.spool)
    private_directory(installation.spool / "quarantine")
    private_directory(installation.reports)
    return installation


def load_config(installation: Installation) -> Config:
    payload = json.loads(installation.config_path.read_text(encoding="utf-8"))
    values = {key: payload.get(key, value) for key, value in DEFAULTS.items()}
    for key, value in values.items():
        if not isinstance(value, int) or value <= 0:
            raise ValueError(f"invalid_config_{key}")
    if values["max_candidates_per_session"] != 1:
        raise ValueError("invalid_config_max_candidates_per_session")
    return Config(
        workspace_roots=canonical_roots(payload["workspace_roots"], allow_empty=True),
        exclude_roots=canonical_roots(payload["exclude_roots"], allow_empty=True),
        mutable_skill_roots=canonical_roots(
            payload["mutable_skill_roots"], allow_empty=False
        ),
        **values,
    )


def open_database(
    installation: Installation,
    busy_ms: int = 1_000,
) -> sqlite3.Connection:
    if installation.database.exists():
        private_file(installation.database)
    connection = sqlite3.connect(
        str(installation.database), timeout=busy_ms / 1_000, isolation_level=None
    )
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA foreign_keys = ON")
    connection.execute(f"PRAGMA busy_timeout = {int(busy_ms)}")
    version = int(connection.execute("PRAGMA user_version").fetchone()[0])
    if version > SCHEMA_VERSION:
        connection.close()
        raise ValueError("unsupported_database_schema")
    if version == 0:
        try:
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
    connection.execute("PRAGMA journal_mode = WAL")
    os.chmod(installation.database, 0o600)
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

- [ ] **Step 4: Run the store tests**

Run:

```bash
/usr/bin/python3 -m unittest \
  skill-evolver/skills/skill-evolver/tests/test_capture.py -v
```

Expected: all metadata and runtime-store tests PASS, including failed initialization
leaving neither a partial table nor a nonzero `user_version`.

- [ ] **Step 5: Commit the fixed store**

```bash
git add \
  skill-evolver/skills/skill-evolver/scripts/evolver.py \
  skill-evolver/skills/skill-evolver/tests/test_capture.py
git commit -m "feat: add skill evolver sqlite v1 store"
```

Expected: the commit contains the self-contained store and its tests.

---

### Task 3: Enqueue Allowed Stops with HMAC Dedupe and Bounded Spool

**Files:**
- Modify: `skill-evolver/skills/skill-evolver/scripts/evolver.py`
- Modify: `skill-evolver/skills/skill-evolver/tests/test_capture.py`

**Interfaces:**
- Consumes: `Installation`, `Config`, `open_database()`, fixed transcript roots, and Hook stdin.
- Produces: `CapturedEvent`,
  `parse_hook_event(raw, installation, config) -> Optional[CapturedEvent]`,
  `event_key(installation, session_id, turn_id) -> str`,
  transactional
  `write_review_item(connection, event, key, config, now) -> bool`,
  `spool_event(installation, config, event, key, now) -> bool`, and silent
  `enqueue-stop`.

- [ ] **Step 1: Add failing enqueue, dedupe, allowlist, and spool tests**

Add these imports to `test_capture.py`:

```python
import json
import socket
import threading
```

Add this class before the final `unittest.main()` block:

```python
class CaptureTests(RuntimeStoreTests):
    def setUp(self) -> None:
        super().setUp()
        self.installation_path = self.runtime.initialize_runtime(
            self.base / "data", (self.sessions,), self.config
        )
        self.installation = self.runtime.load_installation(self.installation_path)
        self.runtime_config = self.runtime.load_config(self.installation)
        self.transcript = self.sessions / "session.jsonl"
        self.transcript.write_text('{"message":"private body"}\n', encoding="utf-8")
        self.payload = {
            "hook_event_name": "Stop",
            "session_id": "session-1",
            "turn_id": "turn-1",
            "cwd": str(self.workspace),
            "transcript_path": str(self.transcript),
        }

    def test_same_stop_is_inserted_once_without_transcript_body(self) -> None:
        raw = json.dumps(self.payload).encode()
        for _ in range(2):
            self.runtime.enqueue_stop(self.installation, self.runtime_config, raw)
        connection = self.runtime.open_database(self.installation)
        rows = connection.execute("SELECT * FROM review_items").fetchall()
        connection.close()
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["status"], "pending")
        self.assertNotIn("private body", self.installation.database.read_bytes().decode(
            "utf-8", errors="ignore"
        ))

    def test_excluded_or_empty_workspace_does_not_capture(self) -> None:
        excluded_path = self.base / "excluded"
        excluded_path.mkdir(mode=0o700)
        excluded = self.runtime.Config(
            **{
                **self.runtime_config.__dict__,
                "exclude_roots": (excluded_path,),
            }
        )
        self.payload["cwd"] = str(excluded_path)
        self.assertEqual(
            self.runtime.enqueue_stop(
                self.installation, excluded, json.dumps(self.payload).encode()
            ),
            "ignored",
        )
        disabled = self.runtime.Config(
            **{**self.runtime_config.__dict__, "workspace_roots": ()}
        )
        self.payload["cwd"] = str(self.workspace)
        self.assertEqual(
            self.runtime.enqueue_stop(
                self.installation, disabled, json.dumps(self.payload).encode()
            ),
            "ignored",
        )

    def test_database_lock_falls_back_to_private_spool(self) -> None:
        with mock.patch.object(
            self.runtime,
            "write_review_item",
            side_effect=sqlite3.OperationalError("database is locked"),
        ):
            result = self.runtime.enqueue_stop(
                self.installation,
                self.runtime_config,
                json.dumps(self.payload).encode(),
            )
        self.assertEqual(result, "spooled")
        self.assertEqual(len(list(self.installation.spool.glob("*.json"))), 1)

    def test_capture_enforces_pending_cap_before_new_insert(self) -> None:
        limited = self.runtime.Config(
            **{**self.runtime_config.__dict__, "pending_limit": 1}
        )
        self.runtime.enqueue_stop(
            self.installation, limited, json.dumps(self.payload).encode()
        )
        self.payload["turn_id"] = "turn-2"
        self.runtime.enqueue_stop(
            self.installation, limited, json.dumps(self.payload).encode()
        )
        connection = self.runtime.open_database(self.installation)
        counts = dict(
            connection.execute(
                "SELECT status,COUNT(*) FROM review_items GROUP BY status"
            ).fetchall()
        )
        expired = connection.execute(
            """
            SELECT session_id,turn_id,cwd,transcript_path,transcript_size,
              transcript_mtime_ns,transcript_device,transcript_inode,
              raw_redacted_at
            FROM review_items WHERE status='expired'
            """
        ).fetchone()
        connection.close()
        self.assertEqual(counts, {"expired": 1, "pending": 1})
        self.assertTrue(all(expired[key] is None for key in expired.keys()[:-1]))
        self.assertIsNotNone(expired["raw_redacted_at"])

    def test_two_concurrent_captures_cannot_exceed_pending_cap(self) -> None:
        limited = self.runtime.Config(
            **{**self.runtime_config.__dict__, "pending_limit": 1}
        )
        events = []
        for turn_id in ("turn-a", "turn-b"):
            payload = {**self.payload, "turn_id": turn_id}
            event = self.runtime.parse_hook_event(
                json.dumps(payload).encode(), self.installation, limited
            )
            assert event is not None
            events.append(
                (
                    event,
                    self.runtime.event_key(
                        self.installation, event.session_id, event.turn_id
                    ),
                )
            )
        barrier = threading.Barrier(2)
        failures = []

        def capture(event, key) -> None:
            connection = self.runtime.open_database(
                self.installation, busy_ms=2_000
            )
            try:
                barrier.wait(timeout=2)
                self.runtime.write_review_item(
                    connection, event, key, limited, 2_000_000_000.0
                )
            except BaseException as error:
                failures.append(error)
            finally:
                connection.close()

        workers = [
            threading.Thread(target=capture, args=value, daemon=True)
            for value in events
        ]
        for worker in workers:
            worker.start()
        for worker in workers:
            worker.join(timeout=4)
        self.assertTrue(all(not worker.is_alive() for worker in workers))
        self.assertEqual(failures, [])
        connection = self.runtime.open_database(self.installation)
        counts = dict(
            connection.execute(
                "SELECT status,COUNT(*) FROM review_items GROUP BY status"
            ).fetchall()
        )
        connection.close()
        self.assertEqual(counts, {"expired": 1, "pending": 1})

    def test_hook_process_is_silent_fail_open_and_network_free(self) -> None:
        with mock.patch.object(socket, "socket", side_effect=AssertionError("network")):
            self.assertEqual(
                self.runtime.enqueue_stop(
                    self.installation,
                    self.runtime_config,
                    json.dumps(self.payload).encode(),
                ),
                "queued",
            )
        result = run_isolated(
            "enqueue-stop",
            "--installation",
            str(self.installation_path),
            stdin=json.dumps(self.payload).encode(),
        )
        malformed = run_isolated(
            "enqueue-stop", "--installation", "/missing/installation.json", stdin=b"{"
        )
        oversized = run_isolated(
            "enqueue-stop",
            "--installation",
            str(self.installation_path),
            stdin=b"x" * 65_537,
        )
        for process in (result, malformed, oversized):
            self.assertEqual(process.returncode, 0)
            self.assertEqual(process.stdout, b"")
            self.assertEqual(process.stderr, b"")
```

- [ ] **Step 2: Run the capture tests and verify the failure**

Run:

```bash
/usr/bin/python3 -m unittest \
  skill-evolver/skills/skill-evolver/tests/test_capture.py -v
```

Expected: FAIL because `enqueue_stop()` and its supporting interfaces are undefined.

- [ ] **Step 3: Add capture, HMAC, SQLite insert, and spool implementation**

Add these imports near the top of `evolver.py`:

```python
import fcntl
import hmac
import time
```

Add below `Config`:

```python
@dataclass(frozen=True)
class CapturedEvent:
    session_id: str
    turn_id: str
    cwd: Path
    transcript_path: Path
    transcript_size: int
    transcript_mtime_ns: int
    transcript_device: int
    transcript_inode: int
```

Add these functions before `write_json_stdout()`:

```python
def within(path: Path, roots: tuple[Path, ...]) -> bool:
    return any(
        os.path.commonpath((str(path), str(root))) == str(root)
        for root in roots
    )


def bounded_string(payload: dict[str, object], name: str, maximum: int) -> str:
    value = payload.get(name)
    if not isinstance(value, str) or not value or len(value.encode("utf-8")) > maximum:
        raise ValueError(f"invalid_{name}")
    return value


def parse_hook_event(
    raw: bytes,
    installation: Installation,
    config: Config,
) -> Optional[CapturedEvent]:
    if len(raw) > MAX_HOOK_BYTES:
        raise ValueError("hook_input_too_large")
    payload = json.loads(raw)
    if not isinstance(payload, dict) or payload.get("hook_event_name") != "Stop":
        raise ValueError("not_stop_event")
    cwd_value = Path(bounded_string(payload, "cwd", 4_096)).expanduser()
    if cwd_value.is_symlink():
        return None
    cwd = cwd_value.resolve(strict=True)
    if (
        not config.workspace_roots
        or not within(cwd, config.workspace_roots)
        or within(cwd, config.exclude_roots)
    ):
        return None
    transcript_value = Path(
        bounded_string(payload, "transcript_path", 4_096)
    ).expanduser()
    if transcript_value.is_symlink():
        raise ValueError("transcript_symlink")
    transcript = transcript_value.resolve(strict=True)
    if not within(transcript, installation.transcript_roots):
        raise ValueError("transcript_outside_roots")
    descriptor = os.open(str(transcript), os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0))
    try:
        info = os.fstat(descriptor)
    finally:
        os.close(descriptor)
    if not stat.S_ISREG(info.st_mode) or info.st_uid != os.getuid():
        raise ValueError("transcript_owner_or_type")
    return CapturedEvent(
        session_id=bounded_string(payload, "session_id", 512),
        turn_id=bounded_string(payload, "turn_id", 512),
        cwd=cwd,
        transcript_path=transcript,
        transcript_size=info.st_size,
        transcript_mtime_ns=info.st_mtime_ns,
        transcript_device=info.st_dev,
        transcript_inode=info.st_ino,
    )


def event_key(
    installation: Installation,
    session_id: str,
    turn_id: str,
) -> str:
    message = session_id.encode("utf-8") + b"\0" + turn_id.encode("utf-8")
    return hmac.new(
        installation.identity_key.read_bytes(), message, "sha256"
    ).hexdigest()


def iso_utc(epoch: float) -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(epoch))


def write_review_item(
    connection: sqlite3.Connection,
    event: CapturedEvent,
    key: str,
    config: Config,
    now: float,
) -> bool:
    connection.execute("BEGIN IMMEDIATE")
    try:
        duplicate = connection.execute(
            "SELECT 1 FROM review_items WHERE event_key=?",
            (key,),
        ).fetchone() is not None
        prune_pending_for_capture(
            connection, config, now, reserve=0 if duplicate else 1
        )
        inserted = False
        if not duplicate:
            changed = connection.execute(
                """
                INSERT INTO review_items (
                    event_key, session_id, turn_id, cwd, transcript_path,
                    transcript_size, transcript_mtime_ns, transcript_device,
                    transcript_inode, status, created_at, dedupe_expires_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 'pending', ?, ?)
                """,
                (
                    key,
                    event.session_id,
                    event.turn_id,
                    str(event.cwd),
                    str(event.transcript_path),
                    event.transcript_size,
                    event.transcript_mtime_ns,
                    event.transcript_device,
                    event.transcript_inode,
                    iso_utc(now),
                    iso_utc(now + 180 * 86_400),
                ),
            ).rowcount
            if changed != 1:
                raise sqlite3.IntegrityError("capture_insert_race")
            inserted = True
        changed = connection.execute(
            """
            INSERT INTO metadata(key, value) VALUES('last_hook_success_at', ?)
            ON CONFLICT(key) DO UPDATE SET value=excluded.value
            """,
            (iso_utc(now),),
        ).rowcount
        if changed != 1:
            raise sqlite3.IntegrityError("capture_metadata_race")
        pending = int(
            connection.execute(
                "SELECT COUNT(*) FROM review_items WHERE status='pending'"
            ).fetchone()[0]
        )
        if pending > config.pending_limit:
            raise sqlite3.IntegrityError("pending_capacity_invariant")
        connection.commit()
        return inserted
    except BaseException:
        connection.rollback()
        raise


def event_payload(event: CapturedEvent, key: str, now: float) -> dict[str, object]:
    return {
        "schema_version": 1,
        "event_key": key,
        "session_id": event.session_id,
        "turn_id": event.turn_id,
        "cwd": str(event.cwd),
        "transcript_path": str(event.transcript_path),
        "transcript_size": event.transcript_size,
        "transcript_mtime_ns": event.transcript_mtime_ns,
        "transcript_device": event.transcript_device,
        "transcript_inode": event.transcript_inode,
        "created_at": iso_utc(now),
        "dedupe_expires_at": iso_utc(now + 180 * 86_400),
    }


def prune_pending_for_capture(
    connection: sqlite3.Connection,
    config: Config,
    now: float,
    reserve: int,
) -> int:
    if reserve not in {0, 1}:
        raise ValueError("capture_reserve")
    connection.execute(
        """
        UPDATE review_items
        SET status='expired',excluded_reason='retention',reviewed_at=?,
          session_id=NULL,turn_id=NULL,cwd=NULL,transcript_path=NULL,
          transcript_size=NULL,transcript_mtime_ns=NULL,
          transcript_device=NULL,transcript_inode=NULL,error_code=NULL,
          raw_redacted_at=?
        WHERE status='pending' AND created_at < ?
        """,
        (
            iso_utc(now),
            iso_utc(now),
            iso_utc(now - config.pending_retention_days * 86_400),
        ),
    )
    overflow = connection.execute(
        """
        SELECT id FROM review_items WHERE status='pending'
        ORDER BY created_at,id
        LIMIT MAX(
          (SELECT COUNT(*) FROM review_items WHERE status='pending') - ? + ?,
          0
        )
        """,
        (config.pending_limit, reserve),
    ).fetchall()
    if overflow:
        ids = [int(row["id"]) for row in overflow]
        marks = ",".join("?" for _ in ids)
        changed = connection.execute(
            f"""
            UPDATE review_items
            SET status='expired',excluded_reason='capacity',reviewed_at=?,
              session_id=NULL,turn_id=NULL,cwd=NULL,transcript_path=NULL,
              transcript_size=NULL,transcript_mtime_ns=NULL,
              transcript_device=NULL,transcript_inode=NULL,error_code=NULL,
              raw_redacted_at=?
            WHERE id IN ({marks}) AND status='pending'
            """,
            (iso_utc(now), iso_utc(now), *ids),
        ).rowcount
        if changed != len(ids):
            raise sqlite3.IntegrityError("capture_prune_race")
        changed = connection.execute(
            """
            INSERT INTO metadata(key,value) VALUES('capacity_expired_count',?)
            ON CONFLICT(key) DO UPDATE SET
              value=CAST(CAST(value AS INTEGER)+excluded.value AS TEXT)
            """,
            (str(len(ids)),),
        ).rowcount
        if changed != 1:
            raise sqlite3.IntegrityError("capture_capacity_metadata_race")
    return len(overflow)


def increment_overflow(installation: Installation) -> None:
    path = installation.spool / "overflow.count"
    current = int(path.read_text(encoding="ascii")) if path.exists() else 0
    atomic_write_bytes(path, f"{current + 1}\n".encode("ascii"))


def spool_event(
    installation: Installation,
    config: Config,
    event: CapturedEvent,
    key: str,
    now: float,
) -> bool:
    lock_path = installation.spool / ".lock"
    lock_path.touch(mode=0o600, exist_ok=True)
    with lock_path.open("r+b") as lock:
        fcntl.flock(lock.fileno(), fcntl.LOCK_EX)
        files = list(installation.spool.glob("*.json"))
        total = sum(path.stat().st_size for path in files)
        encoded = canonical_json_bytes(event_payload(event, key, now)) + b"\n"
        if len(files) >= config.spool_limit_files or total + len(encoded) > config.spool_limit_bytes:
            increment_overflow(installation)
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
    event = parse_hook_event(raw, installation, config)
    if event is None:
        return "ignored"
    now = time.time()
    key = event_key(installation, event.session_id, event.turn_id)
    try:
        connection = open_database(installation, busy_ms=75)
        try:
            write_review_item(connection, event, key, config, now)
        finally:
            connection.close()
        return "queued"
    except sqlite3.Error:
        return "spooled" if spool_event(
            installation, config, event, key, now
        ) else "overflow"


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

Add this parser before `return parser` in `build_parser()`:

```python
    enqueue = commands.add_parser("enqueue-stop")
    enqueue.add_argument("--installation", required=True)
    enqueue.set_defaults(handler=cmd_enqueue_stop)
```

- [ ] **Step 4: Run the capture tests**

Run:

```bash
/usr/bin/python3 -m unittest \
  skill-evolver/skills/skill-evolver/tests/test_capture.py -v
```

Expected: all tests PASS, including exactly one queue row for duplicate input and silent exit `0` for malformed/oversized input.

- [ ] **Step 5: Commit capture and spool**

```bash
git add \
  skill-evolver/skills/skill-evolver/scripts/evolver.py \
  skill-evolver/skills/skill-evolver/tests/test_capture.py
git commit -m "feat: enqueue bounded stop metadata"
```

Expected: only runtime and focused capture tests are committed.

---

### Task 4: Import Spool, Enforce Queue Retention, and Report Health

**Files:**
- Modify: `skill-evolver/skills/skill-evolver/scripts/evolver.py`
- Modify: `skill-evolver/skills/skill-evolver/tests/test_capture.py`
- Modify: `skill-evolver/README.md`

**Interfaces:**
- Consumes: SQLite v1, private spool files, and `Config`.
- Produces: `import_spool(connection, installation, config, now) -> dict[str, int]`, `run_maintenance(connection, installation, config, now) -> dict[str, int]`, `queue_status(connection, installation, now) -> dict[str, object]`, and commands `maintain` and `status`.

- [ ] **Step 1: Add failing spool import, retention, and status tests**

Add to `CaptureTests`:

```python
    def test_pending_expiration_clears_every_raw_metadata_field(self) -> None:
        now = 2_000_000_000.0
        event = self.runtime.parse_hook_event(
            json.dumps(self.payload).encode(),
            self.installation,
            self.runtime_config,
        )
        assert event is not None
        key = self.runtime.event_key(
            self.installation, event.session_id, event.turn_id
        )
        connection = self.runtime.open_database(self.installation)
        self.runtime.write_review_item(
            connection, event, key, self.runtime_config, now
        )
        result = self.runtime.run_maintenance(
            connection, self.installation, self.runtime_config, now + 15 * 86_400
        )
        row = connection.execute(
            """
            SELECT status,session_id,turn_id,cwd,transcript_path,transcript_size,
              transcript_mtime_ns,transcript_device,transcript_inode,error_code,
              raw_redacted_at
            FROM review_items
            """
        ).fetchone()
        connection.close()
        self.assertEqual(result["pending_expired"], 1)
        self.assertEqual(row["status"], "expired")
        for key in (
            "session_id", "turn_id", "cwd", "transcript_path", "transcript_size",
            "transcript_mtime_ns", "transcript_device", "transcript_inode",
            "error_code",
        ):
            self.assertIsNone(row[key])
        self.assertIsNotNone(row["raw_redacted_at"])

    def test_spool_retention_uses_utc_epoch_at_fourteen_day_boundary(self) -> None:
        now = 2_000_000_000.0
        event = self.runtime.parse_hook_event(
            json.dumps(self.payload).encode(),
            self.installation,
            self.runtime_config,
        )
        assert event is not None
        key = self.runtime.event_key(
            self.installation, event.session_id, event.turn_id
        )
        self.runtime.spool_event(
            self.installation, self.runtime_config, event, key, now
        )
        connection = self.runtime.open_database(self.installation)
        with mock.patch.object(
            self.runtime.time,
            "mktime",
            side_effect=AssertionError("local timezone conversion forbidden"),
        ):
            at_boundary = self.runtime.import_spool(
                connection, self.installation, self.runtime_config,
                now + 14 * 86_400
            )
        self.assertEqual(at_boundary["spool_imported"], 1)
        self.assertEqual(at_boundary["spool_expired"], 0)

        second = self.runtime.CapturedEvent(
            **{**event.__dict__, "turn_id": "turn-after-boundary"}
        )
        second_key = self.runtime.event_key(
            self.installation, second.session_id, second.turn_id
        )
        self.runtime.spool_event(
            self.installation, self.runtime_config, second, second_key, now + 100
        )
        after_boundary = self.runtime.import_spool(
            connection, self.installation, self.runtime_config,
            now + 100 + 14 * 86_400 + 1
        )
        connection.close()
        self.assertEqual(after_boundary["spool_imported"], 0)
        self.assertEqual(after_boundary["spool_expired"], 1)

    def test_status_does_not_import_spool_or_open_transcript(self) -> None:
        self.runtime.enqueue_stop(
            self.installation,
            self.runtime_config,
            json.dumps(self.payload).encode(),
        )
        (self.installation.spool / "waiting.json").write_text(
            "{}\n", encoding="utf-8"
        )
        with mock.patch.object(Path, "open", side_effect=AssertionError("transcript read")):
            connection = self.runtime.open_database(self.installation)
            status = self.runtime.queue_status(
                connection, self.installation, time.time()
            )
            connection.close()
        self.assertEqual(status["pending_turns"], 1)
        self.assertEqual(status["pending_sessions"], 1)
        self.assertEqual(status["spool_files"], 1)
        self.assertTrue((self.installation.spool / "waiting.json").exists())
```

Add `import time` to the test imports.

- [ ] **Step 2: Run the tests and verify the failure**

Run:

```bash
/usr/bin/python3 -m unittest \
  skill-evolver/skills/skill-evolver/tests/test_capture.py -v
```

Expected: FAIL because `run_maintenance()` and `queue_status()` do not exist.

- [ ] **Step 3: Implement spool import and deterministic maintenance**

Add before `cmd_enqueue_stop()`:

```python
def import_spool(
    connection: sqlite3.Connection,
    installation: Installation,
    config: Config,
    now: float,
) -> dict[str, int]:
    imported = duplicate = quarantined = expired = 0
    for path in sorted(installation.spool.glob("*.json")):
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
            created = calendar.timegm(
                time.strptime(payload["created_at"], "%Y-%m-%dT%H:%M:%SZ")
            )
            if now - created > config.pending_retention_days * 86_400:
                path.unlink()
                expired += 1
                continue
            event = CapturedEvent(
                session_id=str(payload["session_id"]),
                turn_id=str(payload["turn_id"]),
                cwd=Path(str(payload["cwd"])),
                transcript_path=Path(str(payload["transcript_path"])),
                transcript_size=int(payload["transcript_size"]),
                transcript_mtime_ns=int(payload["transcript_mtime_ns"]),
                transcript_device=int(payload["transcript_device"]),
                transcript_inode=int(payload["transcript_inode"]),
            )
            inserted = write_review_item(
                connection, event, str(payload["event_key"]), config, created
            )
            imported += int(inserted)
            duplicate += int(not inserted)
            path.unlink()
        except (KeyError, OSError, TypeError, ValueError, json.JSONDecodeError):
            destination = installation.spool / "quarantine" / path.name
            os.replace(path, destination)
            quarantined += 1
    fsync_directory(installation.spool)
    return {
        "spool_imported": imported,
        "spool_duplicates": duplicate,
        "spool_quarantined": quarantined,
        "spool_expired": expired,
    }


def run_maintenance(
    connection: sqlite3.Connection,
    installation: Installation,
    config: Config,
    now: float,
) -> dict[str, int]:
    counts = import_spool(connection, installation, config, now)
    pending_cutoff = iso_utc(now - config.pending_retention_days * 86_400)
    raw_cutoff = iso_utc(now - config.raw_metadata_ttl_days * 86_400)
    dedupe_cutoff = iso_utc(now)
    connection.execute("BEGIN IMMEDIATE")
    try:
        expired = connection.execute(
            """
            UPDATE review_items
            SET status='expired',excluded_reason='retention',reviewed_at=?,
              session_id=NULL,turn_id=NULL,cwd=NULL,transcript_path=NULL,
              transcript_size=NULL,transcript_mtime_ns=NULL,
              transcript_device=NULL,transcript_inode=NULL,error_code=NULL,
              raw_redacted_at=?
            WHERE status='pending' AND created_at < ?
            """,
            (iso_utc(now), iso_utc(now), pending_cutoff),
        ).rowcount
        overflow = connection.execute(
            """
            SELECT id FROM review_items
            WHERE status='pending'
            ORDER BY created_at DESC, id DESC
            LIMIT -1 OFFSET ?
            """,
            (config.pending_limit,),
        ).fetchall()
        capacity = 0
        if overflow:
            ids = [int(row["id"]) for row in overflow]
            marks = ",".join("?" for _ in ids)
            capacity = connection.execute(
                f"""
                UPDATE review_items
                SET status='expired',excluded_reason='capacity',reviewed_at=?,
                  session_id=NULL,turn_id=NULL,cwd=NULL,transcript_path=NULL,
                  transcript_size=NULL,transcript_mtime_ns=NULL,
                  transcript_device=NULL,transcript_inode=NULL,error_code=NULL,
                  raw_redacted_at=?
                WHERE id IN ({marks})
                """,
                (iso_utc(now), iso_utc(now), *ids),
            ).rowcount
        redacted = connection.execute(
            """
            UPDATE review_items
            SET session_id=NULL, turn_id=NULL, cwd=NULL, transcript_path=NULL,
                transcript_size=NULL, transcript_mtime_ns=NULL,
                transcript_device=NULL, transcript_inode=NULL,
                error_code=NULL, raw_redacted_at=?
            WHERE raw_redacted_at IS NULL AND created_at < ?
            """,
            (iso_utc(now), raw_cutoff),
        ).rowcount
        deleted = connection.execute(
            "DELETE FROM review_items WHERE dedupe_expires_at < ?",
            (dedupe_cutoff,),
        ).rowcount
        if capacity:
            connection.execute(
                """
                INSERT INTO metadata(key, value) VALUES('capacity_expired_count', ?)
                ON CONFLICT(key) DO UPDATE SET
                    value=CAST(CAST(value AS INTEGER) + excluded.value AS TEXT)
                """,
                (str(capacity),),
            )
        connection.commit()
    except BaseException:
        connection.rollback()
        raise
    return {
        **counts,
        "pending_expired": expired,
        "capacity_expired": capacity,
        "raw_redacted": redacted,
        "dedupe_deleted": deleted,
    }


def queue_status(
    connection: sqlite3.Connection,
    installation: Installation,
    now: float,
) -> dict[str, object]:
    pending = connection.execute(
        """
        SELECT COUNT(*) AS turns, COUNT(DISTINCT session_id) AS sessions,
               MIN(created_at) AS oldest
        FROM review_items WHERE status='pending'
        """
    ).fetchone()
    candidate_rows = connection.execute(
        "SELECT status, COUNT(*) AS count FROM candidates GROUP BY status"
    ).fetchall()
    metadata = {
        row["key"]: row["value"]
        for row in connection.execute(
            """
            SELECT key, value FROM metadata
            WHERE key IN ('last_hook_success_at', 'capacity_expired_count')
            """
        )
    }
    overflow_path = installation.spool / "overflow.count"
    overflow = (
        int(overflow_path.read_text(encoding="ascii"))
        if overflow_path.exists()
        else 0
    )
    return {
        "schema_version": 1,
        "pending_sessions": int(pending["sessions"] or 0),
        "pending_turns": int(pending["turns"] or 0),
        "oldest_pending_at": pending["oldest"],
        "candidate_counts": {
            str(row["status"]): int(row["count"]) for row in candidate_rows
        },
        "spool_files": len(list(installation.spool.glob("*.json"))),
        "spool_overflow": overflow,
        "capacity_expired": int(metadata.get("capacity_expired_count", "0")),
        "last_hook_success_at": metadata.get("last_hook_success_at"),
        "checked_at": iso_utc(now),
    }


def cmd_maintain(args: argparse.Namespace) -> int:
    installation = load_installation(Path(args.installation))
    config = load_config(installation)
    connection = open_database(installation)
    try:
        result = run_maintenance(connection, installation, config, time.time())
    finally:
        connection.close()
    write_json_stdout(result)
    return 0


def cmd_status(args: argparse.Namespace) -> int:
    installation = load_installation(Path(args.installation))
    connection = open_database(installation)
    try:
        result = queue_status(connection, installation, time.time())
    finally:
        connection.close()
    write_json_stdout(result)
    return 0
```

Add before `return parser`:

```python
    maintain = commands.add_parser("maintain")
    maintain.add_argument("--installation", required=True)
    maintain.set_defaults(handler=cmd_maintain)
    status = commands.add_parser("status")
    status.add_argument("--installation", required=True)
    status.set_defaults(handler=cmd_status)
```

- [ ] **Step 4: Run all capture tests**

Run:

```bash
/usr/bin/python3 -m unittest \
  skill-evolver/skills/skill-evolver/tests/test_capture.py -v
```

Expected: all tests PASS; status leaves the spool untouched.

- [ ] **Step 5: Replace the README with exact production operations**

Replace `skill-evolver/README.md` with:

````markdown
# Skill Evolver Read-only MVP

Prerequisite: `docs/feasibility-report.json` says `PASS`.

Create a private initialization config:

```json
{
  "workspace_roots": ["/Users/igyeongseob/Documents/오픈소스"],
  "exclude_roots": [],
  "mutable_skill_roots": ["/Users/igyeongseob/.codex/skills"]
}
```

Save it as `/private/tmp/skill-evolver-config.json`, then initialize:

```bash
/usr/bin/python3 -I skill-evolver/skills/skill-evolver/scripts/evolver.py init \
  --data-root /Users/igyeongseob/.codex/skill-evolver \
  --transcript-root /Users/igyeongseob/.codex/sessions \
  --transcript-root /Users/igyeongseob/.codex/archived_sessions \
  --config /private/tmp/skill-evolver-config.json
```

Install and inspect the exact Hook before trusting it:

```bash
codex plugin marketplace add /Users/igyeongseob/Documents/오픈소스 --json
codex plugin add skill-evolver@skill-evolver-dev --json
```

Run `/hooks` in CLI and Desktop and verify the command matches
`hooks/hooks.json`. Status is read-only and does not import spool files:

```bash
/usr/bin/python3 -I skill-evolver/skills/skill-evolver/scripts/evolver.py status \
  --installation /Users/igyeongseob/.codex/skill-evolver/installation.json
```

Run maintenance explicitly before review:

```bash
/usr/bin/python3 -I skill-evolver/skills/skill-evolver/scripts/evolver.py maintain \
  --installation /Users/igyeongseob/.codex/skill-evolver/installation.json
```

Uninstalling the plugin stops new captures but preserves the private database:

```bash
codex plugin remove skill-evolver@skill-evolver-dev --json
codex plugin marketplace remove skill-evolver-dev --json
```
````

- [ ] **Step 6: Initialize and smoke-test the real production root**

After saving the exact JSON configuration from Step 5 at
`/private/tmp/skill-evolver-config.json`, run:

```bash
/usr/bin/python3 -I skill-evolver/skills/skill-evolver/scripts/evolver.py init \
  --data-root /Users/igyeongseob/.codex/skill-evolver \
  --transcript-root /Users/igyeongseob/.codex/sessions \
  --transcript-root /Users/igyeongseob/.codex/archived_sessions \
  --config /private/tmp/skill-evolver-config.json
```

Expected: exit `0` and JSON with `"status":"initialized"` and
`"installation":"/Users/igyeongseob/.codex/skill-evolver/installation.json"`.

Then run:

```bash
/usr/bin/python3 -I \
  skill-evolver/skills/skill-evolver/scripts/evolver.py \
  status \
  --installation /Users/igyeongseob/.codex/skill-evolver/installation.json
```

Expected JSON contains `"schema_version":1`, `"pending_turns":0`, `"spool_files":0`, and `"candidate_counts":{}`.

- [ ] **Step 7: Install, trust, and verify one event twice**

Install with the README commands. In both CLI and Desktop, use `/hooks` to inspect and trust the exact Hook. Complete one ordinary turn in an allowed workspace, then replay its sanitized Hook fixture twice through a temporary runtime initialized by the test suite:

```bash
/usr/bin/python3 -m unittest \
  skill-evolver/skills/skill-evolver/tests/test_capture.py \
  -v
```

Expected: all tests PASS, including duplicate delivery producing one `review_items` row and Hook subprocesses producing no output.

- [ ] **Step 8: Commit runtime queue and operations**

```bash
git add \
  skill-evolver/skills/skill-evolver/scripts/evolver.py \
  skill-evolver/skills/skill-evolver/tests/test_capture.py \
  skill-evolver/README.md
git commit -m "feat: complete read-only runtime queue"
```

Expected: only the three listed files are staged; private root `/Users/igyeongseob/.codex/skill-evolver` is never committed.

## Completion Criteria

- Feasibility is `PASS`; this plan did not guess transcript fields or bypass its gate.
- CLI and Desktop use one trusted production `Stop` Hook and the same fixed private installation.
- Duplicate `(session_id, turn_id)` delivery creates one pending row through HMAC `event_key`.
- Disallowed workspaces, excluded roots, empty `workspace_roots`, invalid transcript roots, symlinks, oversized input, and malformed input create no queue item.
- Hook invocation performs no transcript parsing/model/network work, writes no stdout/stderr, and always exits `0`.
- SQLite schema initialization is one explicit transaction; a failed DDL leaves no partial table and `user_version == 0`. Successful opens use WAL, foreign keys, a short Hook busy timeout, and fail closed for schemas newer than v1.
- Hook HMAC dedupe, oldest-pending capacity pruning, insert, and success metadata update share one `BEGIN IMMEDIATE` transaction; checked row counts and a final pending-count invariant prevent concurrent captures from exceeding 200, while lock/error paths retain the bounded spool fallback.
- DB contention creates at most 200 spool files/10 MiB; overflow is counted and visible in status.
- Explicit maintenance imports spool with UTC-safe 14-day boundary handling, quarantines malformed files, expires 14-day pending rows while clearing all raw metadata, caps pending at 200 with the same clearing rule, redacts other raw metadata after 30 days, and deletes dedupe rows after 180 days.
- Bare `$skill-evolver` and `status` read SQLite/spool counts only and do not import spool or open transcript files.
- No installed skill, staging tree, snapshot, candidate, evaluation, or apply state is changed.
