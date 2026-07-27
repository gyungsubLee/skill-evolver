# Skill Evolver Read-only Quality Gate Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Finish Read-only MVP retention/privacy controls and produce a deterministic PASS/FAIL report after at least 10 reviewed sessions or 30 reviewed turns, with every candidate manually labeled for usefulness and attribution.

**Architecture:** Keep schema v1 unchanged by storing human audit labels, claim-time review contracts, and the latest report digest in `metadata`. Extend deterministic maintenance for candidate/spool aging, implement two-step canonical purge plans whose executor requires a fresh full digest in an external TTY, then calculate the release decision from pseudonymous session/event keys and the exact policy/adapter digest pair recorded by completed batches without copying transcript or evidence text into the report.

**Tech Stack:** macOS, `/usr/bin/python3` 3.9+, Python standard library, SQLite WAL/schema v1, canonical JSON/SHA-256 helpers from the Runtime Queue plan, `unittest`.

## Global Constraints

- Source specification: `skill-evolver/docs/superpowers/specs/2026-07-26-skill-evolver-design.md`.
- Strict preconditions: Feasibility is `PASS`; all `test_capture.py` and `test_review.py` tests pass; SQLite `PRAGMA user_version == 1`.
- Runtime stays self-contained in `evolver.py`; no PostgreSQL, dependency, background worker, automatic model review, or schema-v2 table.
- Fixed raw retention: pending 14 days, all review-item raw metadata 30 days, HMAC event tombstone 180 days, normal/quarantine spool 14 days.
- Candidate retention: deferred becomes stale after 30 days; rejected tombstone is 90 days; terminal evidence and candidate free text are redacted after 90 days.
- Routine retention is automatic and deterministic. Immediate privacy purge is destructive and requires preview, exact `P-xxx@full-SHA-256`, external TTY, full digest re-entry, and a fresh target-set check within 600 seconds.
- Purge never names or traverses a mutable skill root, and active review leases are never purged.
- Quality sample gate is `reviewed_sessions >= 10 OR reviewed_turns >= 30`.
- Quality sampling uses `metadata["review.batch.NNNNNN.contract"]`: HMAC session keys, HMAC event keys, candidate IDs, and the policy/adapter digests captured by `review-claim`; raw `session_id` and `turn_id` are not required after 30-day redaction.
- PASS also requires evaluation-worth rate `>= 0.50`, target-skill misattribution rate `<= 0.20`, zero external-content adoption incidents, every candidate labeled, and non-empty policy/adapter digests.
- Quality labels are explicit human judgments, not model-generated approvals, and do not prepare/evaluate/apply a candidate.
- Report path is exactly `skill-evolver/docs/release-reports/read-only-quality-gate.json`; it contains aggregate counts/digests only.
- A FAIL result requires review policy/adapter correction and a fresh sample; it does not authorize Evaluate planning.
- Every commit stages exact paths only.

## Preconditions and File Structure

Run:

```bash
/usr/bin/python3 -m unittest \
  skill-evolver/skills/skill-evolver/tests/test_capture.py \
  skill-evolver/skills/skill-evolver/tests/test_review.py -v
/usr/bin/python3 - <<'PY'
import json, sqlite3
from pathlib import Path
assert json.loads(Path("skill-evolver/docs/feasibility-report.json").read_text())["decision"] == "PASS"
with sqlite3.connect("/Users/igyeongseob/.codex/skill-evolver/evolver.db") as db:
    assert db.execute("PRAGMA user_version").fetchone()[0] == 1
print("quality-preconditions-pass")
PY
```

Expected: all tests PASS and `quality-preconditions-pass`.

| Path | Responsibility |
| --- | --- |
| `skill-evolver/skills/skill-evolver/scripts/evolver.py` | Aging, purge preview/executor, quality labels, report calculation. |
| `skill-evolver/skills/skill-evolver/SKILL.md` | Explicit purge/quality workflow and no-auto-advance boundary. |
| `skill-evolver/skills/skill-evolver/tests/test_quality_gate.py` | Retention, purge, TTY/digest, label, and PASS/FAIL tests. |
| `skill-evolver/docs/release-reports/read-only-quality-gate.json` | Generated aggregate release evidence. |
| `skill-evolver/README.md` | Exact soak, label, report, and terminal purge operations. |

---

### Task 1: Complete Read-only Retention and Candidate Aging

**Files:**
- Modify: `skill-evolver/skills/skill-evolver/scripts/evolver.py`
- Create: `skill-evolver/skills/skill-evolver/tests/test_quality_gate.py`

**Interfaces:**
- Consumes: `run_maintenance()`, schema-v1 candidates/evidence, and private spool.
- Produces: `redacted_marker(value) -> str` and `run_quality_retention(connection, installation, config, now) -> dict[str, int]`.

- [ ] **Step 1: Write failing retention tests**

Create `skill-evolver/skills/skill-evolver/tests/test_quality_gate.py`:

```python
from __future__ import annotations

import io
import json
import sqlite3
import tempfile
import time
import unittest
from pathlib import Path
from unittest import mock

from support import load_runtime


class QualityTestCase(unittest.TestCase):
    def setUp(self) -> None:
        self.runtime = load_runtime()
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.base = Path(self.temp.name)
        self.sessions = self.base / "sessions"
        self.workspace = self.base / "workspace"
        self.skills = self.base / "skills"
        for path in (self.sessions, self.workspace, self.skills):
            path.mkdir(mode=0o700)
        self.installation_path = self.runtime.initialize_runtime(
            self.base / "data",
            (self.sessions,),
            {
                "workspace_roots": [str(self.workspace)],
                "exclude_roots": [],
                "mutable_skill_roots": [str(self.skills)],
            },
        )
        self.installation = self.runtime.load_installation(self.installation_path)
        self.config = self.runtime.load_config(self.installation)
        self.connection = self.runtime.open_database(self.installation)
        self.addCleanup(self.connection.close)


class RetentionTests(QualityTestCase):
    def test_candidate_text_evidence_and_quarantine_age_deterministically(self) -> None:
        now = 2_000_000_000.0
        old = self.runtime.iso_utc(now - 91 * 86_400)
        cursor = self.connection.execute(
            """
            INSERT INTO candidates(
              fingerprint,target_identity,target_skill,target_path,
              problem_category,target_locator,proposal_intent,
              problem_summary,proposal_summary,validation_plan,risk_level,
              status,first_seen_at,last_seen_at,updated_at,tombstone_until
            ) VALUES('f','user-skill:x','x','/x','p','l','i',
                     'private problem','private proposal','private validation',
                     'low','rejected',?,?,?,?)
            """,
            (old, old, old, old),
        )
        candidate_id = int(cursor.lastrowid)
        self.connection.execute(
            """
            INSERT INTO candidate_evidence(
              candidate_id,review_item_id,session_key,signal_type,
              source_kind,summary,created_at
            ) VALUES(?,NULL,'s','explicit_correction','user_direct',
                     'private evidence',?)
            """,
            (candidate_id, old),
        )
        quarantined = self.installation.spool / "quarantine/broken.json"
        quarantined.write_text("{}\n", encoding="utf-8")
        timestamp = now - 15 * 86_400
        quarantined.touch()
        import os
        os.utime(quarantined, (timestamp, timestamp))

        result = self.runtime.run_quality_retention(
            self.connection, self.installation, self.config, now
        )
        candidate = self.connection.execute(
            "SELECT problem_summary,proposal_summary,validation_plan FROM candidates"
        ).fetchone()
        evidence = self.connection.execute(
            "SELECT summary FROM candidate_evidence"
        ).fetchone()[0]
        self.assertEqual(result["candidate_text_redacted"], 1)
        self.assertEqual(result["evidence_redacted"], 1)
        self.assertEqual(result["quarantine_deleted"], 1)
        self.assertTrue(candidate["problem_summary"].startswith("[redacted sha256:"))
        self.assertTrue(evidence.startswith("[redacted sha256:"))
        self.assertFalse(quarantined.exists())

    def test_deferred_candidate_becomes_stale_after_thirty_days(self) -> None:
        now = 2_000_000_000.0
        old = self.runtime.iso_utc(now - 31 * 86_400)
        self.connection.execute(
            """
            INSERT INTO candidates(
              fingerprint,target_identity,target_skill,problem_category,
              target_locator,proposal_intent,problem_summary,proposal_summary,
              validation_plan,risk_level,status,first_seen_at,last_seen_at,updated_at
            ) VALUES('f','user-skill:x','x','p','l','i','a','b','c','low',
                     'deferred',?,?,?)
            """,
            (old, old, old),
        )
        self.runtime.run_quality_retention(
            self.connection, self.installation, self.config, now
        )
        self.assertEqual(
            self.connection.execute("SELECT status FROM candidates").fetchone()[0],
            "stale",
        )
```

- [ ] **Step 2: Run retention tests and verify the failure**

Run:

```bash
/usr/bin/python3 -m unittest \
  skill-evolver/skills/skill-evolver/tests/test_quality_gate.py -v
```

Expected: FAIL because quality retention functions are undefined.

- [ ] **Step 3: Implement deterministic aging and redaction**

Add:

```python
def redacted_marker(value: str) -> str:
    return f"[redacted sha256:{hashlib.sha256(value.encode('utf-8')).hexdigest()}]"


def run_quality_retention(
    connection: sqlite3.Connection,
    installation: Installation,
    config: Config,
    now: float,
) -> dict[str, int]:
    base = run_maintenance(connection, installation, config, now)
    stale_cutoff = iso_utc(now - config.deferred_candidate_days * 86_400)
    terminal_cutoff = iso_utc(now - 90 * 86_400)
    connection.execute("BEGIN IMMEDIATE")
    try:
        stale = connection.execute(
            """
            UPDATE candidates SET status='stale',updated_at=?
            WHERE status='deferred' AND updated_at < ?
            """,
            (iso_utc(now), stale_cutoff),
        ).rowcount
        evidence_rows = connection.execute(
            """
            SELECT e.candidate_id,e.session_key,e.signal_type,e.summary
            FROM candidate_evidence e JOIN candidates c ON c.id=e.candidate_id
            WHERE c.status IN ('rejected','stale') AND c.updated_at < ?
              AND e.summary NOT LIKE '[redacted sha256:%'
            """,
            (terminal_cutoff,),
        ).fetchall()
        for row in evidence_rows:
            connection.execute(
                """
                UPDATE candidate_evidence SET summary=?
                WHERE candidate_id=? AND session_key=? AND signal_type=?
                """,
                (
                    redacted_marker(str(row["summary"])),
                    row["candidate_id"], row["session_key"], row["signal_type"],
                ),
            )
        candidates = connection.execute(
            """
            SELECT id,problem_summary,proposal_summary,validation_plan
            FROM candidates
            WHERE status IN ('rejected','stale') AND updated_at < ?
              AND problem_summary NOT LIKE '[redacted sha256:%'
            """,
            (terminal_cutoff,),
        ).fetchall()
        for row in candidates:
            connection.execute(
                """
                UPDATE candidates SET problem_summary=?,proposal_summary=?,
                  validation_plan=? WHERE id=?
                """,
                (
                    redacted_marker(str(row["problem_summary"])),
                    redacted_marker(str(row["proposal_summary"])),
                    redacted_marker(str(row["validation_plan"])),
                    row["id"],
                ),
            )
        connection.commit()
    except BaseException:
        connection.rollback()
        raise
    quarantine_deleted = 0
    cutoff = now - config.pending_retention_days * 86_400
    for path in sorted((installation.spool / "quarantine").glob("*")):
        if path.is_file() and not path.is_symlink() and path.stat().st_mtime < cutoff:
            path.unlink()
            quarantine_deleted += 1
    fsync_directory(installation.spool / "quarantine")
    return {
        **base,
        "deferred_staled": stale,
        "evidence_redacted": len(evidence_rows),
        "candidate_text_redacted": len(candidates),
        "quarantine_deleted": quarantine_deleted,
    }
```

- [ ] **Step 4: Run all deterministic tests**

```bash
/usr/bin/python3 -m unittest \
  skill-evolver/skills/skill-evolver/tests/test_capture.py \
  skill-evolver/skills/skill-evolver/tests/test_review.py \
  skill-evolver/skills/skill-evolver/tests/test_quality_gate.py -v
```

Expected: all tests PASS.

- [ ] **Step 5: Commit complete retention**

```bash
git add \
  skill-evolver/skills/skill-evolver/scripts/evolver.py \
  skill-evolver/skills/skill-evolver/tests/test_quality_gate.py
git commit -m "feat: complete read-only retention policy"
```

---

### Task 2: Add Digest-bound Purge Preview and External-TTY Executor

**Files:**
- Modify: `skill-evolver/skills/skill-evolver/scripts/evolver.py`
- Modify: `skill-evolver/skills/skill-evolver/tests/test_quality_gate.py`

**Interfaces:**
- Consumes: schema-v1 rows, spool/quarantine inventory, and `metadata.next_purge_id`.
- Produces: `purge_targets(...) -> dict[str, object]`, `create_purge_plan(...) -> dict[str, object]`, `execute_purge_plan(...) -> dict[str, object]`, and commands `purge-plan`, `purge-preview`, `purge-execute`.

- [ ] **Step 1: Add failing no-delete-preview, digest, and TTY tests**

Add to `test_quality_gate.py`:

```python
class PurgeTests(QualityTestCase):
    def insert_raw_item(self) -> None:
        now = self.runtime.iso_utc(2_000_000_000.0)
        self.connection.execute(
            """
            INSERT INTO review_items(
              event_key,session_id,turn_id,cwd,transcript_path,status,
              created_at,dedupe_expires_at
            ) VALUES('k','session','turn','/cwd','/transcript','excluded',?,?)
            """,
            (now, self.runtime.iso_utc(2_000_000_000.0 + 180 * 86400)),
        )

    def test_privacy_preview_deletes_nothing_and_exact_execution_redacts(self) -> None:
        self.insert_raw_item()
        plan = self.runtime.create_purge_plan(
            self.connection, self.installation, self.config,
            "privacy", 2_000_000_100.0
        )
        before = self.connection.execute(
            "SELECT session_id FROM review_items"
        ).fetchone()[0]
        self.assertEqual(before, "session")
        original_targets = self.runtime.purge_targets

        def locked_targets(*args):
            self.assertTrue(self.connection.in_transaction)
            return original_targets(*args)

        with mock.patch.object(
            self.runtime, "purge_targets", side_effect=locked_targets
        ):
            result = self.runtime.execute_purge_plan(
                self.connection, self.installation, self.config,
                int(plan["plan"]["id"]), plan["digest"], 2_000_000_200.0
            )
        after = self.connection.execute(
            "SELECT session_id,raw_redacted_at FROM review_items"
        ).fetchone()
        self.assertEqual(result["status"], "purged")
        self.assertIsNone(after["session_id"])
        self.assertIsNotNone(after["raw_redacted_at"])

    def test_wrong_digest_or_changed_targets_deletes_nothing(self) -> None:
        self.insert_raw_item()
        plan = self.runtime.create_purge_plan(
            self.connection, self.installation, self.config,
            "privacy", 2_000_000_100.0
        )
        with self.assertRaisesRegex(ValueError, "purge_digest_mismatch"):
            self.runtime.execute_purge_plan(
                self.connection, self.installation, self.config,
                int(plan["plan"]["id"]), "0" * 64, 2_000_000_200.0
            )
        self.connection.execute(
            """
            INSERT INTO review_items(
              event_key,session_id,turn_id,status,created_at,dedupe_expires_at
            ) VALUES('new','s','t','excluded',?,?)
            """,
            (self.runtime.iso_utc(2_000_000_150.0),
             self.runtime.iso_utc(2_100_000_000.0)),
        )
        with self.assertRaisesRegex(ValueError, "purge_targets_changed"):
            self.runtime.execute_purge_plan(
                self.connection, self.installation, self.config,
                int(plan["plan"]["id"]), plan["digest"], 2_000_000_200.0
            )

    def test_purge_command_rejects_non_tty(self) -> None:
        args = type("Args", (), {
            "installation": str(self.installation_path),
            "plan": 1,
            "plan_digest": "0" * 64,
        })()
        with mock.patch.object(self.runtime.sys, "stdin", io.StringIO()):
            with self.assertRaisesRegex(ValueError, "tty_required"):
                self.runtime.cmd_purge_execute(args)
```

- [ ] **Step 2: Run purge tests and verify the failure**

Run:

```bash
/usr/bin/python3 -m unittest \
  skill-evolver/skills/skill-evolver/tests/test_capture.py \
  skill-evolver/skills/skill-evolver/tests/test_review.py \
  skill-evolver/skills/skill-evolver/tests/test_quality_gate.py -v
```

Expected: FAIL because purge functions are undefined.

- [ ] **Step 3: Implement canonical target inventory and preview**

Add:

```python
def purge_targets(
    connection: sqlite3.Connection,
    installation: Installation,
    config: Config,
    scope: str,
    now: float,
) -> dict[str, object]:
    if scope not in {"expired", "privacy"}:
        raise ValueError("invalid_purge_scope")
    active = connection.execute(
        """
        SELECT COUNT(*) FROM review_items
        WHERE status='reviewing' AND lease_expires_at >= ?
        """,
        (iso_utc(now),),
    ).fetchone()[0]
    if active:
        raise ValueError("active_review_lease")
    if scope == "privacy":
        review_ids = [
            int(row["id"]) for row in connection.execute(
                "SELECT id FROM review_items WHERE raw_redacted_at IS NULL ORDER BY id"
            )
        ]
        evidence = [
            [int(row["candidate_id"]), str(row["session_key"]), str(row["signal_type"])]
            for row in connection.execute(
                """
                SELECT candidate_id,session_key,signal_type
                FROM candidate_evidence
                WHERE summary NOT LIKE '[redacted sha256:%'
                ORDER BY candidate_id,session_key,signal_type
                """
            )
        ]
        spool = [
            str(path.relative_to(installation.data_root))
            for root in (installation.spool, installation.spool / "quarantine")
            for path in sorted(root.glob("*.json"))
            if path.is_file() and not path.is_symlink()
        ]
        delete_ids: list[int] = []
    else:
        review_ids = [
            int(row["id"]) for row in connection.execute(
                """
                SELECT id FROM review_items
                WHERE raw_redacted_at IS NULL AND created_at < ? ORDER BY id
                """,
                (iso_utc(now - config.raw_metadata_ttl_days * 86_400),),
            )
        ]
        delete_ids = [
            int(row["id"]) for row in connection.execute(
                "SELECT id FROM review_items WHERE dedupe_expires_at < ? ORDER BY id",
                (iso_utc(now),),
            )
        ]
        evidence = []
        cutoff = now - config.pending_retention_days * 86_400
        spool = [
            str(path.relative_to(installation.data_root))
            for root in (installation.spool, installation.spool / "quarantine")
            for path in sorted(root.glob("*.json"))
            if path.is_file() and not path.is_symlink() and path.stat().st_mtime < cutoff
        ]
    return {
        "review_item_redactions": review_ids,
        "review_item_deletions": delete_ids,
        "evidence_redactions": evidence,
        "spool_deletions": sorted(set(spool)),
    }


def create_purge_plan(
    connection: sqlite3.Connection,
    installation: Installation,
    config: Config,
    scope: str,
    now: float,
) -> dict[str, object]:
    connection.execute("BEGIN IMMEDIATE")
    try:
        row = connection.execute(
            "SELECT value FROM metadata WHERE key='next_purge_id'"
        ).fetchone()
        plan_id = int(row["value"]) if row else 1
        connection.execute(
            """
            INSERT INTO metadata(key,value) VALUES('next_purge_id',?)
            ON CONFLICT(key) DO UPDATE SET value=excluded.value
            """,
            (str(plan_id + 1),),
        )
        targets = purge_targets(connection, installation, config, scope, now)
        connection.commit()
    except BaseException:
        connection.rollback()
        raise
    plan = {
        "schema_version": 1,
        "id": plan_id,
        "scope": scope,
        "created_at_epoch": int(now),
        "expires_at_epoch": int(now + config.purge_plan_ttl_seconds),
        "targets": targets,
    }
    document = {"plan": plan, "digest": sha256_json(plan)}
    directory = installation.reports / "purge"
    directory.mkdir(mode=0o700, exist_ok=True)
    atomic_write_json(directory / f"P-{plan_id:03d}.json", document)
    return document
```

- [ ] **Step 4: Implement exact revalidation, deletion, and TTY command**

Add:

```python
def load_purge_document(
    installation: Installation,
    plan_id: int,
) -> dict[str, object]:
    path = installation.reports / "purge" / f"P-{plan_id:03d}.json"
    value = json.loads(private_file(path).read_text(encoding="utf-8"))
    if sha256_json(value["plan"]) != value.get("digest"):
        raise ValueError("purge_plan_tampered")
    return value


def execute_purge_plan(
    connection: sqlite3.Connection,
    installation: Installation,
    config: Config,
    plan_id: int,
    digest: str,
    now: float,
) -> dict[str, object]:
    document = load_purge_document(installation, plan_id)
    plan = document["plan"]
    if digest != document["digest"]:
        raise ValueError("purge_digest_mismatch")
    if now > int(plan["expires_at_epoch"]):
        raise ValueError("purge_plan_expired")
    lock_path = installation.spool / ".lock"
    if lock_path.is_symlink():
        raise ValueError("purge_lock_symlink")
    lock_path.touch(mode=0o600, exist_ok=True)
    with private_file(lock_path).open("r+b") as spool_lock:
        fcntl.flock(spool_lock.fileno(), fcntl.LOCK_EX)
        connection.execute("BEGIN IMMEDIATE")
        try:
            current = purge_targets(
                connection, installation, config, str(plan["scope"]), now
            )
            if current != plan["targets"]:
                raise ValueError("purge_targets_changed")
            spool_paths = []
            for relative in current["spool_deletions"]:
                relative_path = Path(relative)
                if relative_path.is_absolute() or ".." in relative_path.parts:
                    raise ValueError("purge_path_escape")
                path = installation.data_root / relative_path
                if path.is_symlink():
                    raise ValueError("purge_path_escape")
                canonical = path.resolve(strict=True)
                if not within(canonical, (installation.spool,)):
                    raise ValueError("purge_path_escape")
                spool_paths.append(canonical)
            for item_id in current["review_item_redactions"]:
                connection.execute(
                    """
                    UPDATE review_items SET session_id=NULL,turn_id=NULL,cwd=NULL,
                      transcript_path=NULL,transcript_size=NULL,
                      transcript_mtime_ns=NULL,transcript_device=NULL,
                      transcript_inode=NULL,error_code=NULL,raw_redacted_at=?
                    WHERE id=?
                    """,
                    (iso_utc(now), item_id),
                )
            for candidate_id, session_key, signal_type in current["evidence_redactions"]:
                row = connection.execute(
                    """
                    SELECT summary FROM candidate_evidence
                    WHERE candidate_id=? AND session_key=? AND signal_type=?
                    """,
                    (candidate_id, session_key, signal_type),
                ).fetchone()
                if row:
                    connection.execute(
                        """
                        UPDATE candidate_evidence SET summary=?
                        WHERE candidate_id=? AND session_key=? AND signal_type=?
                        """,
                        (redacted_marker(str(row["summary"])),
                         candidate_id, session_key, signal_type),
                    )
            for item_id in current["review_item_deletions"]:
                connection.execute("DELETE FROM review_items WHERE id=?", (item_id,))
            connection.commit()
        except BaseException:
            connection.rollback()
            raise
        for path in spool_paths:
            path.unlink()
        fsync_directory(installation.spool)
        fsync_directory(installation.spool / "quarantine")
    connection.execute("PRAGMA wal_checkpoint(TRUNCATE)")
    result = {
        "schema_version": 1,
        "plan_id": f"P-{plan_id:03d}",
        "plan_digest": digest,
        "status": "purged",
        "counts": {key: len(value) for key, value in current.items()},
        "finished_at": iso_utc(now),
    }
    atomic_write_json(
        installation.reports / "purge" / f"P-{plan_id:03d}-result.json",
        result,
    )
    return result


def cmd_purge_plan(args: argparse.Namespace) -> int:
    installation = load_installation(Path(args.installation))
    config = load_config(installation)
    connection = open_database(installation)
    try:
        result = create_purge_plan(
            connection, installation, config, args.scope, time.time()
        )
    finally:
        connection.close()
    write_json_stdout(result)
    return 0


def cmd_purge_preview(args: argparse.Namespace) -> int:
    installation = load_installation(Path(args.installation))
    plan_id = parse_display_id(args.plan.split("@", 1)[0], "P")
    document = load_purge_document(installation, plan_id)
    if args.plan != f"P-{plan_id:03d}@{document['digest']}":
        raise ValueError("purge_digest_mismatch")
    write_json_stdout({
        **document,
        "manual_command": (
            f"/usr/bin/python3 -I {Path(__file__).resolve()} purge-execute "
            f"--installation {args.installation} --plan {plan_id} "
            f"--plan-digest {document['digest']}"
        ),
    })
    return 0


def cmd_purge_execute(args: argparse.Namespace) -> int:
    if not sys.stdin.isatty():
        raise ValueError("tty_required")
    typed = input("Type the full purge plan digest: ")
    if typed != args.plan_digest:
        raise ValueError("purge_digest_mismatch")
    installation = load_installation(Path(args.installation))
    config = load_config(installation)
    connection = open_database(installation)
    try:
        result = execute_purge_plan(
            connection, installation, config, args.plan,
            args.plan_digest, time.time()
        )
    finally:
        connection.close()
    write_json_stdout(result)
    return 0
```

Register:

```python
    purge_plan = commands.add_parser("purge-plan")
    purge_plan.add_argument("--installation", required=True)
    purge_plan.add_argument("--scope", choices=("expired", "privacy"), default="expired")
    purge_plan.set_defaults(handler=cmd_purge_plan)
    purge_preview = commands.add_parser("purge-preview")
    purge_preview.add_argument("--installation", required=True)
    purge_preview.add_argument("plan")
    purge_preview.set_defaults(handler=cmd_purge_preview)
    purge_execute = commands.add_parser("purge-execute")
    purge_execute.add_argument("--installation", required=True)
    purge_execute.add_argument("--plan", type=int, required=True)
    purge_execute.add_argument("--plan-digest", required=True)
    purge_execute.set_defaults(handler=cmd_purge_execute)
```

- [ ] **Step 5: Run purge and regression tests**

Run:

```bash
/usr/bin/python3 -m unittest \
  skill-evolver/skills/skill-evolver/tests/test_capture.py \
  skill-evolver/skills/skill-evolver/tests/test_review.py \
  skill-evolver/skills/skill-evolver/tests/test_quality_gate.py -v
```

Expected: all tests PASS; preview leaves raw fields intact, wrong digest/changed targets/non-TTY fail before deletion, and exact execution redacts then checkpoints WAL.

- [ ] **Step 6: Commit manual purge**

```bash
git add \
  skill-evolver/skills/skill-evolver/scripts/evolver.py \
  skill-evolver/skills/skill-evolver/tests/test_quality_gate.py
git commit -m "feat: add digest-bound privacy purge"
```

---

### Task 3: Record Human Candidate Labels and Generate the PASS/FAIL Report

**Files:**
- Modify: `skill-evolver/skills/skill-evolver/scripts/evolver.py`
- Modify: `skill-evolver/skills/skill-evolver/tests/test_quality_gate.py`
- Create: `skill-evolver/docs/release-reports/read-only-quality-gate.json`

**Interfaces:**
- Consumes: completed `review_batches`, `metadata["review.batch.NNNNNN.contract"]`, and `metadata["quality.label.C-xxx"]`.
- Produces: `set_quality_label(...)`, `quality_report(...) -> dict[str, object]`, `write_quality_report(...)`, commands `quality-label` and `quality-gate`, and the exact nested JSON contract below.

- [ ] **Step 1: Add failing PASS/FAIL report tests**

Add to `test_quality_gate.py`:

```python
class GateTests(QualityTestCase):
    def seed_sample(self, sessions: int, turns: int) -> None:
        now = self.runtime.iso_utc(2_000_000_000.0)
        policy = "a" * 64
        adapter = "b" * 64
        self.connection.execute(
            """
            INSERT INTO review_batches(
              id,status,started_at,finished_at,session_count,turn_count,candidate_count
            ) VALUES(1,'completed',?,?,?, ?,1)
            """,
            (now, now, sessions, turns),
        )
        for number in range(turns):
            self.connection.execute(
                """
                INSERT INTO review_items(
                  event_key,session_id,turn_id,status,batch_id,created_at,
                  reviewed_at,dedupe_expires_at
                ) VALUES(?,?,?,'excluded',1,?,?,?)
                """,
                (f"k-{number}", f"s-{number % sessions}", f"t-{number}",
                 now, now, self.runtime.iso_utc(2_100_000_000.0)),
            )
        self.connection.execute(
            """
            INSERT INTO candidates(
              id,fingerprint,target_identity,target_skill,problem_category,
              target_locator,proposal_intent,problem_summary,proposal_summary,
              validation_plan,risk_level,status,first_seen_at,last_seen_at,updated_at
            ) VALUES(1,'f','user-skill:x','x','p','l','i','a','b','c','low',
                     'proposed',?,?,?)
            """,
            (now, now, now),
        )
        contract = {
            "schema_version": 1,
            "batch_id": 1,
            "owner": "owner-a",
            "policy_digest": policy,
            "adapter_digest": adapter,
            "started_at": now,
            "finished_at": now,
            "reviewed_session_keys": [
                f"{number + 1:064x}" for number in range(sessions)
            ],
            "reviewed_event_keys": [
                f"{number + 1_000:064x}" for number in range(turns)
            ],
            "candidate_ids": [1],
            "released_review_item_count": 0,
        }
        self.connection.execute(
            "INSERT INTO metadata(key,value) VALUES(?,?)",
            (
                self.runtime.review_contract_key(1),
                self.runtime.canonical_json_bytes(contract).decode("utf-8"),
            ),
        )

    def test_go_report_uses_exact_nested_contract_and_digests(self) -> None:
        self.seed_sample(10, 10)
        self.runtime.set_quality_label(
            self.connection, 1, True, True, False, 2_000_000_100.0
        )
        self.connection.execute(
            """
            UPDATE review_items
            SET session_id=NULL,turn_id=NULL,cwd=NULL,transcript_path=NULL,
              raw_redacted_at=?
            """,
            (self.runtime.iso_utc(2_000_000_150.0),),
        )
        report = self.runtime.quality_report(
            self.connection, 2_000_000_200.0
        )
        self.assertEqual(report["decision"], "PASS")
        self.assertEqual(report["sample"]["reviewed_sessions"], 10)
        self.assertEqual(report["metrics"]["evaluation_worth_rate"], 1.0)
        self.assertEqual(report["policy_digest"], "a" * 64)
        self.assertEqual(report["adapter_digest"], "b" * 64)
        self.assertEqual(report["sample"]["reviewed_turns"], 10)
        self.assertTrue(all(report["checks"].values()))

    def test_latest_contract_pair_excludes_older_policy_samples(self) -> None:
        self.seed_sample(10, 30)
        now = self.runtime.iso_utc(2_000_000_100.0)
        self.connection.execute(
            """
            INSERT INTO review_batches(
              id,status,started_at,finished_at,session_count,turn_count,candidate_count
            ) VALUES(2,'completed',?,?,1,1,0)
            """,
            (now, now),
        )
        contract = {
            "schema_version": 1,
            "batch_id": 2,
            "owner": "owner-b",
            "policy_digest": "c" * 64,
            "adapter_digest": "d" * 64,
            "started_at": now,
            "finished_at": now,
            "reviewed_session_keys": ["f" * 64],
            "reviewed_event_keys": ["e" * 64],
            "candidate_ids": [],
            "released_review_item_count": 0,
        }
        self.connection.execute(
            "INSERT INTO metadata(key,value) VALUES(?,?)",
            (
                self.runtime.review_contract_key(2),
                self.runtime.canonical_json_bytes(contract).decode("utf-8"),
            ),
        )
        report = self.runtime.quality_report(
            self.connection, 2_000_000_200.0
        )
        self.assertEqual(report["policy_digest"], "c" * 64)
        self.assertEqual(report["adapter_digest"], "d" * 64)
        self.assertEqual(report["source_batch_ids"], [2])
        self.assertEqual(report["sample"]["reviewed_sessions"], 1)
        self.assertEqual(report["decision"], "FAIL")

    def test_no_go_for_small_sample_misattribution_or_external_adoption(self) -> None:
        self.seed_sample(9, 29)
        self.runtime.set_quality_label(
            self.connection, 1, True, False, True, 2_000_000_100.0
        )
        report = self.runtime.quality_report(
            self.connection, 2_000_000_200.0
        )
        self.assertEqual(report["decision"], "FAIL")
        self.assertFalse(report["checks"]["sample_size"])
        self.assertFalse(report["checks"]["target_misattribution_rate"])
        self.assertFalse(report["checks"]["external_content_adoption"])
        self.assertEqual(report["next_action"], "improve_review_policy_and_adapter")
```

- [ ] **Step 2: Run gate tests and verify the failure**

Run:

```bash
/usr/bin/python3 -m unittest \
  skill-evolver/skills/skill-evolver/tests/test_capture.py \
  skill-evolver/skills/skill-evolver/tests/test_review.py \
  skill-evolver/skills/skill-evolver/tests/test_quality_gate.py -v
```

Expected: FAIL because label/report functions are undefined.

- [ ] **Step 3: Implement metadata-backed human labels**

Add:

```python
def set_quality_label(
    connection: sqlite3.Connection,
    candidate_id: int,
    evaluation_worthy: bool,
    target_correct: bool,
    external_content_adoption: bool,
    now: float,
) -> dict[str, object]:
    if connection.execute(
        "SELECT 1 FROM candidates WHERE id=?", (candidate_id,)
    ).fetchone() is None:
        raise ValueError("candidate_not_found")
    label = {
        "schema_version": 1,
        "candidate_id": candidate_id,
        "evaluation_worthy": evaluation_worthy,
        "target_correct": target_correct,
        "external_content_adoption": external_content_adoption,
        "labeled_at": iso_utc(now),
    }
    connection.execute(
        """
        INSERT INTO metadata(key,value) VALUES(?,?)
        ON CONFLICT(key) DO UPDATE SET value=excluded.value
        """,
        (
            f"quality.label.C-{candidate_id:03d}",
            canonical_json_bytes(label).decode("utf-8"),
        ),
    )
    return label


def yes_no(value: str) -> bool:
    if value not in {"yes", "no"}:
        raise ValueError("expected_yes_or_no")
    return value == "yes"


def cmd_quality_label(args: argparse.Namespace) -> int:
    installation = load_installation(Path(args.installation))
    connection = open_database(installation)
    try:
        result = set_quality_label(
            connection,
            parse_display_id(args.candidate, "C"),
            yes_no(args.evaluation_worthy),
            yes_no(args.target_correct),
            yes_no(args.external_content_adoption),
            time.time(),
        )
    finally:
        connection.close()
    write_json_stdout(result)
    return 0
```

- [ ] **Step 4: Implement the exact aggregate quality contract**

Add:

```python
def quality_report(
    connection: sqlite3.Connection,
    now: float,
) -> dict[str, object]:
    completed_ids = [
        int(row["id"])
        for row in connection.execute(
            "SELECT id FROM review_batches WHERE status='completed' ORDER BY id"
        )
    ]
    contracts, unbound_batch_ids = [], []
    for batch_id in completed_ids:
        try:
            contract = load_review_contract(connection, batch_id)
            session_keys = contract["reviewed_session_keys"]
            event_keys = contract["reviewed_event_keys"]
            candidate_ids = contract["candidate_ids"]
            if (
                contract.get("finished_at") is None
                or not isinstance(session_keys, list)
                or not isinstance(event_keys, list)
                or not isinstance(candidate_ids, list)
                or any(not isinstance(value, str) or len(value) != 64 for value in session_keys)
                or any(not isinstance(value, str) or len(value) != 64 for value in event_keys)
                or any(not isinstance(value, int) for value in candidate_ids)
            ):
                raise ValueError("review_contract_incomplete")
            contracts.append(contract)
        except (KeyError, TypeError, ValueError, json.JSONDecodeError):
            unbound_batch_ids.append(batch_id)
    if contracts:
        latest = max(contracts, key=lambda value: int(value["batch_id"]))
        policy = str(latest["policy_digest"])
        adapter = str(latest["adapter_digest"])
        selected = [
            value for value in contracts
            if value["policy_digest"] == policy
            and value["adapter_digest"] == adapter
        ]
    else:
        policy, adapter, selected = "", "", []
    batch_ids = sorted(int(value["batch_id"]) for value in selected)
    reviewed_session_keys = {
        key for value in selected for key in value["reviewed_session_keys"]
    }
    reviewed_event_keys = {
        key for value in selected for key in value["reviewed_event_keys"]
    }
    candidate_ids = sorted(
        {candidate_id for value in selected for candidate_id in value["candidate_ids"]}
    )
    candidate_id_set = set(candidate_ids)
    stored_labels = {}
    for row in connection.execute(
        "SELECT key,value FROM metadata WHERE key LIKE 'quality.label.C-%'"
    ):
        value = json.loads(row["value"])
        stored_labels[int(value["candidate_id"])] = value
    labels = {
        candidate_id: stored_labels[candidate_id]
        for candidate_id in candidate_ids
        if candidate_id in stored_labels
    }
    worthy = sum(bool(value["evaluation_worthy"]) for value in labels.values())
    attribution_checks = len(labels)
    misattributions = sum(not bool(value["target_correct"]) for value in labels.values())
    manual_external = {
        candidate_id for candidate_id, value in labels.items()
        if bool(value["external_content_adoption"])
    }
    if candidate_ids:
        marks = ",".join("?" for _ in candidate_ids)
        stored_external = {
            int(row["candidate_id"]) for row in connection.execute(
                f"""
                SELECT DISTINCT candidate_id FROM candidate_evidence
                WHERE source_kind='external_content' AND candidate_id IN ({marks})
                """,
                candidate_ids,
            )
        }
    else:
        stored_external = set()
    candidate_count = len(candidate_ids)
    worth_rate = worthy / candidate_count if candidate_count else 0.0
    mis_rate = misattributions / attribution_checks if attribution_checks else 0.0
    sample_values = {
        "reviewed_sessions": len(reviewed_session_keys),
        "reviewed_turns": len(reviewed_event_keys),
        "completed_batches": len(batch_ids),
        "candidate_count": candidate_count,
        "labeled_candidate_count": len(labels),
    }
    metrics = {
        "evaluation_worthy_candidates": worthy,
        "evaluation_worth_rate": round(worth_rate, 6),
        "target_attribution_checks": attribution_checks,
        "target_misattributions": misattributions,
        "target_misattribution_rate": round(mis_rate, 6),
        "external_content_adoption_incidents": len(manual_external | stored_external),
    }
    thresholds = {
        "minimum_reviewed_sessions": 10,
        "minimum_reviewed_turns": 30,
        "evaluation_worth_rate_minimum": 0.5,
        "target_misattribution_rate_maximum": 0.2,
        "external_content_adoption_incidents_maximum": 0,
    }
    checks = {
        "sample_size": (
            sample_values["reviewed_sessions"] >= 10
            or sample_values["reviewed_turns"] >= 30
        ),
        "all_candidates_labeled": set(labels) == candidate_id_set,
        "evaluation_worth_rate": worth_rate >= 0.5,
        "target_misattribution_rate": mis_rate <= 0.2 and attribution_checks == candidate_count,
        "external_content_adoption": metrics["external_content_adoption_incidents"] == 0,
        "policy_digest_recorded": len(policy) == 64 and not unbound_batch_ids,
        "adapter_digest_recorded": len(adapter) == 64 and not unbound_batch_ids,
    }
    decision = "PASS" if all(checks.values()) else "FAIL"
    return {
        "schema_version": 1,
        "release": "read-only-mvp",
        "generated_at": iso_utc(now),
        "policy_digest": policy,
        "adapter_digest": adapter,
        "sample": sample_values,
        "metrics": metrics,
        "thresholds": thresholds,
        "checks": checks,
        "decision": decision,
        "next_action": (
            "begin_evaluate_runner_spike"
            if decision == "PASS"
            else "improve_review_policy_and_adapter"
        ),
        "source_batch_ids": batch_ids,
    }


def write_quality_report(
    connection: sqlite3.Connection,
    config: Config,
    output: Path,
    now: float,
) -> dict[str, object]:
    canonical = output.expanduser().resolve()
    if not within(canonical.parent, config.workspace_roots):
        raise ValueError("quality_report_outside_workspace")
    report = quality_report(connection, now)
    canonical.parent.mkdir(parents=True, exist_ok=True)
    atomic_write_json(canonical, report, mode=0o644)
    digest = sha256_json(report)
    for key, value in {
        "quality.last_report_digest": digest,
        "quality.last_report_decision": report["decision"],
        "quality.last_report_generated_at": report["generated_at"],
        "quality.policy_digest": report["policy_digest"],
        "quality.adapter_digest": report["adapter_digest"],
    }.items():
        connection.execute(
            """
            INSERT INTO metadata(key,value) VALUES(?,?)
            ON CONFLICT(key) DO UPDATE SET value=excluded.value
            """,
            (key, str(value)),
        )
    return {**report, "report_digest": digest}


def cmd_quality_gate(args: argparse.Namespace) -> int:
    installation = load_installation(Path(args.installation))
    config = load_config(installation)
    connection = open_database(installation)
    try:
        result = write_quality_report(
            connection, config, Path(args.output), time.time()
        )
    finally:
        connection.close()
    write_json_stdout(result)
    return 0 if result["decision"] == "PASS" else 2
```

Register:

```python
    quality_label = commands.add_parser("quality-label")
    quality_label.add_argument("--installation", required=True)
    quality_label.add_argument("candidate")
    quality_label.add_argument("--evaluation-worthy", choices=("yes", "no"), required=True)
    quality_label.add_argument("--target-correct", choices=("yes", "no"), required=True)
    quality_label.add_argument(
        "--external-content-adoption", choices=("yes", "no"), required=True
    )
    quality_label.set_defaults(handler=cmd_quality_label)
    quality_gate = commands.add_parser("quality-gate")
    quality_gate.add_argument("--installation", required=True)
    quality_gate.add_argument("--output", required=True)
    quality_gate.set_defaults(handler=cmd_quality_gate)
```

- [ ] **Step 5: Run quality and regression tests**

Run:

```bash
/usr/bin/python3 -m unittest \
  skill-evolver/skills/skill-evolver/tests/test_capture.py \
  skill-evolver/skills/skill-evolver/tests/test_review.py \
  skill-evolver/skills/skill-evolver/tests/test_quality_gate.py -v
```

Expected: all tests PASS; passing and failing gate cases match all exact nested fields.

- [ ] **Step 6: Generate the real report after the required sample exists**

After manually reviewing at least 10 distinct sessions or 30 turns and labeling every candidate, run:

```bash
/usr/bin/python3 -I \
  skill-evolver/skills/skill-evolver/scripts/evolver.py quality-gate \
  --installation /Users/igyeongseob/.codex/skill-evolver/installation.json \
  --output /Users/igyeongseob/Documents/오픈소스/skill-evolver/docs/release-reports/read-only-quality-gate.json
```

Expected PASS: exit `0`, `"decision":"PASS"`, every `checks` value is `true`, and `"next_action":"begin_evaluate_runner_spike"`.

Expected FAIL: exit `2`, at least one named check is `false`, and `"next_action":"improve_review_policy_and_adapter"`. Keep the report and do not begin Evaluate implementation.

- [ ] **Step 7: Commit report implementation and generated evidence**

```bash
git add \
  skill-evolver/skills/skill-evolver/scripts/evolver.py \
  skill-evolver/skills/skill-evolver/tests/test_quality_gate.py \
  skill-evolver/docs/release-reports/read-only-quality-gate.json
git commit -m "test: record read-only skill evolver quality gate"
```

Expected: the report contains no summary/evidence/transcript text or raw identifier.

---

### Task 4: Expose the Operational Quality and Purge Boundary

**Files:**
- Modify: `skill-evolver/skills/skill-evolver/SKILL.md`
- Modify: `skill-evolver/README.md`
- Modify: `skill-evolver/skills/skill-evolver/tests/test_quality_gate.py`

**Interfaces:**
- Consumes: purge and quality commands from Tasks 2 and 3.
- Produces: explicit user procedures; no new runtime interface.

- [ ] **Step 1: Add a failing skill-boundary test**

Add to `GateTests`:

```python
    def test_skill_requires_manual_labels_and_external_tty_purge(self) -> None:
        text = (Path(__file__).resolve().parents[1] / "SKILL.md").read_text()
        self.assertIn("quality label C-xxx", text)
        self.assertIn("Never run `purge-execute` for the user", text)
        self.assertIn("PASS does not itself authorize evaluation", text)
```

- [ ] **Step 2: Run the boundary test and verify the failure**

Run:

```bash
/usr/bin/python3 -m unittest \
  skill-evolver/skills/skill-evolver/tests/test_capture.py \
  skill-evolver/skills/skill-evolver/tests/test_review.py \
  skill-evolver/skills/skill-evolver/tests/test_quality_gate.py -v
```

Expected: FAIL because the quality/purge boundary is not yet in `SKILL.md`.

- [ ] **Step 3: Append the exact skill procedures**

Append to `skill-evolver/skills/skill-evolver/SKILL.md`:

```markdown
## Read-only quality and privacy

- `quality label C-xxx`: first run `inspect C-xxx`, ask the user for the three
  explicit judgments, then run `quality-label C-xxx` with
  `--evaluation-worthy yes|no`, `--target-correct yes|no`, and
  `--external-content-adoption yes|no`. Never infer a label.
- `quality gate`: require every candidate to be labeled; run `quality-gate`
  with the fixed workspace report path
  `/Users/igyeongseob/Documents/오픈소스/skill-evolver/docs/release-reports/read-only-quality-gate.json`.
  PASS does not itself authorize evaluation; it only unlocks the separate runner
  spike plan.
- `purge` or `purge expired`: run `purge-plan --scope expired`; show its exact
  targets and `P-xxx@digest`. Delete nothing.
- `purge privacy`: run `purge-plan --scope privacy`; show its exact targets and
  `P-xxx@digest`. Delete nothing.
- `purge P-xxx@digest`: run `purge-preview`; show the exact terminal command.
  Never run `purge-execute` for the user. The user must use an external TTY and
  type the full digest.
```

- [ ] **Step 4: Append the operational commands to README**

Append to `skill-evolver/README.md`:

````markdown
## Read-only release quality gate

Inspect and label every candidate with explicit user judgments:

```bash
/usr/bin/python3 -I skill-evolver/skills/skill-evolver/scripts/evolver.py quality-label \
  --installation /Users/igyeongseob/.codex/skill-evolver/installation.json \
  C-001 --evaluation-worthy yes --target-correct yes \
  --external-content-adoption no
```

Generate the aggregate report only after 10 distinct reviewed sessions or 30
reviewed turns:

```bash
/usr/bin/python3 -I skill-evolver/skills/skill-evolver/scripts/evolver.py quality-gate \
  --installation /Users/igyeongseob/.codex/skill-evolver/installation.json \
  --output /Users/igyeongseob/Documents/오픈소스/skill-evolver/docs/release-reports/read-only-quality-gate.json
```

Privacy purge is preview-first:

```bash
/usr/bin/python3 -I skill-evolver/skills/skill-evolver/scripts/evolver.py purge-plan \
  --installation /Users/igyeongseob/.codex/skill-evolver/installation.json \
  --scope privacy
```

Pass the exact `P-xxx@full-digest` returned by that command to `purge-preview`.
Run only the exact executor command printed by `purge-preview` in an external
terminal within 10 minutes, then type the same full digest. A changed target set
requires a new preview.
````

- [ ] **Step 5: Run final validation and privacy scans**

```bash
/usr/bin/python3 -m unittest \
  skill-evolver/skills/skill-evolver/tests/test_capture.py \
  skill-evolver/skills/skill-evolver/tests/test_review.py \
  skill-evolver/skills/skill-evolver/tests/test_quality_gate.py -v
/usr/bin/python3 -m json.tool \
  skill-evolver/docs/release-reports/read-only-quality-gate.json >/dev/null
rg -n \
  'session_id|turn_id|transcript_path|problem_summary|proposal_summary|validation_plan|evidence|Authorization|Bearer|PRIVATE KEY|/Users/igyeongseob/.codex/sessions' \
  skill-evolver/docs/release-reports/read-only-quality-gate.json
```

Expected: all tests PASS, JSON validation exits `0`, and privacy scan returns no matches with exit `1`.

- [ ] **Step 6: Commit the operational boundary**

```bash
git add \
  skill-evolver/skills/skill-evolver/SKILL.md \
  skill-evolver/skills/skill-evolver/tests/test_quality_gate.py \
  skill-evolver/README.md
git commit -m "docs: define skill evolver read-only release boundary"
```

## Completion Criteria

- Routine retention handles pending/capacity/raw/dedupe limits plus 30-day deferred aging, 90-day terminal text/evidence redaction, and 14-day quarantine deletion.
- Purge preview writes an immutable canonical plan and deletes nothing.
- Purge execution acquires the spool lock and `BEGIN IMMEDIATE`, then recomputes the target set under that write lock; it fails before mutation for non-TTY, wrong/full-digest mismatch, expiry, changed target set, active lease, tampered plan, symlink, or root escape.
- Successful privacy purge redacts listed raw/evidence fields, removes only listed spool files, checkpoints/truncates WAL, and records a bounded result.
- Every candidate has a human quality label in `metadata`; no new table or schema version is introduced.
- Report sample passes at 10 distinct sessions or 30 turns; it does not require both.
- PASS requires evaluation-worth `>= 50%`, misattribution `<= 20%`, zero external adoption, all labels, and claim-time policy/adapter digests on every completed source batch.
- The sample uses only the latest completed batch's exact policy/adapter digest pair and pseudonymous contract keys, and remains reproducible after raw session/turn metadata is redacted.
- Report uses the exact nested contract and fixed path, contains aggregate data only, and records its digest in metadata.
- FAIL routes to policy/adapter improvement; PASS routes only to a separate Evaluate runner spike.
- Installed skills, staging, snapshots, evaluations, apply, and undo remain untouched.
