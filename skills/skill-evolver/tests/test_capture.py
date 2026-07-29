from __future__ import annotations

import fcntl
import hmac
import json
import os
import socket
import sqlite3
import stat
import subprocess
import tempfile
import threading
import time
import unittest
from dataclasses import replace
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

    def test_load_rejects_noncanonical_fixed_transcript_roots(self) -> None:
        fixed_transcript = self.base / "7"
        fixed_transcript.mkdir(mode=0o700)
        installation_path = self.runtime.initialize_runtime(
            self.base / "data", (fixed_transcript,), self.config
        )
        original_payload = json.loads(
            installation_path.read_text(encoding="utf-8")
        )
        alias_parent = self.base / "transcript-root-alias"
        alias_parent.symlink_to(self.base, target_is_directory=True)
        cases = (
            ("relative", ["."], fixed_transcript),
            ("tilde", ["~"], Path.cwd()),
            ("non_string", [7], self.base),
            (
                "symlinked_ancestor",
                [str(alias_parent / fixed_transcript.name)],
                Path.cwd(),
            ),
        )

        for name, transcript_roots, working_directory in cases:
            with self.subTest(name=name):
                payload = {
                    **original_payload,
                    "transcript_roots": transcript_roots,
                }
                installation_path.write_text(
                    json.dumps(payload), encoding="utf-8"
                )
                installation_path.chmod(0o600)
                previous_cwd = Path.cwd()
                try:
                    os.chdir(working_directory)
                    with mock.patch.dict(
                        os.environ, {"HOME": str(fixed_transcript)}
                    ):
                        with self.assertRaisesRegex(
                            ValueError, "invalid_transcript_roots"
                        ):
                            self.runtime.load_installation(installation_path)
                finally:
                    os.chdir(previous_cwd)

    def test_load_rejects_noncanonical_fixed_data_root(self) -> None:
        root = self.base / "7"
        installation_path = self.runtime.initialize_runtime(
            root, (self.sessions,), self.config
        )
        original_payload = json.loads(
            installation_path.read_text(encoding="utf-8")
        )
        alias_parent = self.base / "data-root-alias"
        alias_parent.symlink_to(self.base, target_is_directory=True)
        cases = (
            ("relative", ".", root),
            ("non_string", 7, self.base),
            ("symlinked_ancestor", str(alias_parent / root.name), Path.cwd()),
        )

        for name, data_root, working_directory in cases:
            with self.subTest(name=name):
                payload = {**original_payload, "data_root": data_root}
                installation_path.write_text(
                    json.dumps(payload), encoding="utf-8"
                )
                installation_path.chmod(0o600)
                previous_cwd = Path.cwd()
                try:
                    os.chdir(working_directory)
                    with self.assertRaisesRegex(
                        ValueError, "invalid_data_root"
                    ):
                        self.runtime.load_installation(installation_path)
                finally:
                    os.chdir(previous_cwd)

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
        self.installation = self.runtime.load_installation(
            self.installation_path
        )
        self.runtime_config = self.runtime.load_config(self.installation)
        self.transcript = self.sessions / "session.jsonl"
        self.transcript.write_text(
            "not-json transcript body\n", encoding="utf-8"
        )
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
        self.assertEqual(
            row["transcript_path"], str(new_event.transcript_path)
        )
        self.assertEqual(
            row["transcript_device"], new_event.transcript_device
        )
        self.assertEqual(row["transcript_inode"], new_event.transcript_inode)
        self.assertEqual(
            row["observed_boundary"], new_event.transcript_size
        )
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

    def test_concurrent_overflow_appends_share_one_hard_cap_decision(
        self,
    ) -> None:
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

    def test_hook_rejects_fifo_without_blocking_or_persistence(self) -> None:
        fifo = self.sessions / "session.fifo"
        os.mkfifo(fifo, 0o600)
        fifo.chmod(0o600)
        payload = {
            **self.payload,
            "transcript_path": str(fifo),
        }

        process = subprocess.run(
            [
                "/usr/bin/python3",
                "-I",
                str(SKILL_ROOT / "scripts" / "evolver.py"),
                "enqueue-stop",
                "--installation",
                str(self.installation_path),
            ],
            input=json.dumps(payload).encode(),
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            check=False,
            timeout=1.0,
        )

        self.assertEqual(stat.S_IMODE(fifo.stat().st_mode), 0o600)
        self.assertEqual(process.returncode, 0)
        self.assertEqual(process.stdout, b"")
        self.assertEqual(process.stderr, b"")
        connection = self.runtime.open_database(
            self.installation, read_only=True
        )
        rows = connection.execute(
            "SELECT COUNT(*) FROM review_items"
        ).fetchone()[0]
        connection.close()
        self.assertEqual(rows, 0)
        self.assertEqual(list(self.installation.spool.iterdir()), [])

    def test_hook_is_silent_network_free_and_never_mutates_a_skill(
        self,
    ) -> None:
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


if __name__ == "__main__":
    unittest.main()
