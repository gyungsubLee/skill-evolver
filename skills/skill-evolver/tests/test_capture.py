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
from argparse import Namespace
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

    def test_atomic_write_does_not_chmod_published_path_by_name(self) -> None:
        parent = self.base / "atomic-parent"
        parent.mkdir(mode=0o700)
        target = parent / "payload.json"
        with mock.patch.object(
            os,
            "chmod",
            side_effect=AssertionError("path chmod race"),
        ):
            self.runtime.atomic_write_bytes(target, b"payload\n")
        self.assertEqual(target.read_bytes(), b"payload\n")
        self.assertEqual(stat.S_IMODE(target.stat().st_mode), 0o600)

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

    def test_plugin_spool_requires_exact_private_nonoverlapping_root(
        self,
    ) -> None:
        installation_path = self.runtime.initialize_runtime(
            self.base / "data", (self.sessions,), self.config
        )
        installation = self.runtime.load_installation(installation_path)
        expected = (
            installation.data_root.parent
            / "plugins/data/skill-evolver-skill-evolver-dev"
        )
        expected.mkdir(mode=0o700, parents=True)
        runtime = replace(
            self.runtime.load_review_runtime(),
            plugin_data=expected,
        )
        with mock.patch.object(
            self.runtime, "load_review_runtime", return_value=runtime
        ):
            bound = self.runtime.plugin_spool_installation(
                installation, expected, create=True
            )
            self.assertEqual(bound.spool, expected / "stop-spool")
            self.assertEqual(stat.S_IMODE(bound.spool.stat().st_mode), 0o700)
            for rejected in (
                Path("relative"),
                installation.data_root,
                self.sessions,
            ):
                with self.subTest(rejected=rejected):
                    with self.assertRaisesRegex(
                        ValueError, "invalid_plugin_data"
                    ):
                        self.runtime.plugin_spool_installation(
                            installation, rejected, create=False
                        )

    def test_plugin_spool_creation_fsyncs_plugin_data_parent(self) -> None:
        installation_path = self.runtime.initialize_runtime(
            self.base / "data", (self.sessions,), self.config
        )
        installation = self.runtime.load_installation(installation_path)
        expected = (
            installation.data_root.parent
            / "plugins/data/skill-evolver-skill-evolver-dev"
        )
        expected.mkdir(mode=0o700, parents=True)
        runtime = replace(
            self.runtime.load_review_runtime(),
            plugin_data=expected,
        )
        synced: list[tuple[int, int]] = []

        def record_fsync(descriptor: int) -> None:
            info = os.fstat(descriptor)
            synced.append((info.st_dev, info.st_ino))

        with mock.patch.object(
            self.runtime, "load_review_runtime", return_value=runtime
        ), mock.patch.object(
            self.runtime.os, "fsync", side_effect=record_fsync
        ):
            self.runtime.plugin_spool_installation(
                installation, expected, create=True
            )

        parent = expected.stat()
        self.assertIn((parent.st_dev, parent.st_ino), synced)

    def test_plugin_spool_rejects_symlink_without_touching_sentinel(
        self,
    ) -> None:
        installation_path = self.runtime.initialize_runtime(
            self.base / "data", (self.sessions,), self.config
        )
        installation = self.runtime.load_installation(installation_path)
        expected = (
            installation.data_root.parent
            / "plugins/data/skill-evolver-skill-evolver-dev"
        )
        expected.mkdir(mode=0o700, parents=True)
        sentinel = expected / "unrelated.json"
        sentinel.write_bytes(b"unrelated content")
        sentinel.chmod(0o600)
        runtime = replace(
            self.runtime.load_review_runtime(),
            plugin_data=expected,
        )
        alias = self.base / "plugin-data-alias"
        alias.symlink_to(expected, target_is_directory=True)
        with mock.patch.object(
            self.runtime, "load_review_runtime", return_value=runtime
        ):
            for rejected in (alias,):
                with self.assertRaisesRegex(
                    ValueError, "invalid_plugin_data"
                ):
                    self.runtime.plugin_spool_installation(
                        installation, rejected, create=False
                    )
                self.assertEqual(sentinel.read_bytes(), b"unrelated content")

            expected.chmod(0o755)
            with self.assertRaisesRegex(ValueError, "invalid_plugin_data"):
                self.runtime.plugin_spool_installation(
                    installation, expected, create=False
                )
            self.assertEqual(sentinel.read_bytes(), b"unrelated content")
            expected.chmod(0o700)

            spool = expected / "stop-spool"
            spool.mkdir(mode=0o700)
            spool.rmdir()
            spool.symlink_to(sentinel)
            with self.assertRaisesRegex(ValueError, "invalid_plugin_data"):
                self.runtime.plugin_spool_installation(
                    installation, expected, create=True
                )
            self.assertEqual(sentinel.read_bytes(), b"unrelated content")

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
        self.assertEqual(config.deferred_to_stale_days, 30)
        self.assertEqual(config.rejected_tombstone_days, 90)
        self.assertEqual(
            config.terminal_candidate_retention_days, 90
        )
        self.assertEqual(
            {
                "deferred_to_stale_days": (
                    config.deferred_to_stale_days
                ),
                "rejected_tombstone_days": (
                    config.rejected_tombstone_days
                ),
                "terminal_candidate_retention_days": (
                    config.terminal_candidate_retention_days
                ),
            },
            {
                "deferred_to_stale_days": 30,
                "rejected_tombstone_days": 90,
                "terminal_candidate_retention_days": 90,
            },
        )
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

    def test_load_config_accepts_only_three_legacy_missing_keys(
        self,
    ) -> None:
        installation_path = self.runtime.initialize_runtime(
            self.base / "legacy-candidate-retention",
            (self.sessions,),
            self.config,
        )
        installation = self.runtime.load_installation(
            installation_path
        )
        payload = json.loads(
            installation.config_path.read_text(encoding="utf-8")
        )
        self.assertEqual(
            self.runtime.LEGACY_OPTIONAL_CONFIG_KEYS,
            {
                "deferred_to_stale_days",
                "rejected_tombstone_days",
                "terminal_candidate_retention_days",
            },
        )
        for key in self.runtime.LEGACY_OPTIONAL_CONFIG_KEYS:
            payload.pop(key)
        installation.config_path.write_text(
            json.dumps(payload), encoding="utf-8"
        )
        installation.config_path.chmod(0o600)
        loaded = self.runtime.load_config(installation)
        self.assertEqual(loaded.deferred_to_stale_days, 30)
        self.assertEqual(loaded.rejected_tombstone_days, 90)
        self.assertEqual(
            loaded.terminal_candidate_retention_days, 90
        )

    def test_load_config_rejects_old_missing_or_unknown_keys(
        self,
    ) -> None:
        self.assertEqual(
            self.runtime.LEGACY_OPTIONAL_CONFIG_KEYS,
            {
                "deferred_to_stale_days",
                "rejected_tombstone_days",
                "terminal_candidate_retention_days",
            },
        )
        mutations = tuple(
            (f"missing-{key}", key, None)
            for key in self.runtime.DEFAULTS
            if key
            not in {
                "deferred_to_stale_days",
                "rejected_tombstone_days",
                "terminal_candidate_retention_days",
            }
        ) + (("unknown", "unexpected_retention_days", 7),)
        for name, key, value in mutations:
            with self.subTest(name=name):
                installation_path = self.runtime.initialize_runtime(
                    self.base / name,
                    (self.sessions,),
                    self.config,
                )
                installation = self.runtime.load_installation(
                    installation_path
                )
                payload = json.loads(
                    installation.config_path.read_text(
                        encoding="utf-8"
                    )
                )
                if value is None:
                    payload.pop(key)
                else:
                    payload[key] = value
                installation.config_path.write_text(
                    json.dumps(payload), encoding="utf-8"
                )
                installation.config_path.chmod(0o600)
                with self.assertRaisesRegex(
                    ValueError, "^invalid_config_keys$"
                ):
                    self.runtime.load_config(installation)

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

    def test_initialize_rejects_spool_caps_above_hard_max_without_writes(
        self,
    ) -> None:
        limits = {
            "spool_limit_files": 201,
            "spool_limit_bytes": 10_485_761,
        }
        for key, value in limits.items():
            with self.subTest(key=key):
                root = self.base / f"too-large-{key}"
                with self.assertRaisesRegex(
                    ValueError, f"invalid_config_{key}"
                ):
                    self.runtime.initialize_runtime(
                        root,
                        (self.sessions,),
                        {**self.config, key: value},
                    )
                self.assertFalse(root.exists())

    def test_load_config_rejects_tampered_spool_caps_above_hard_max(
        self,
    ) -> None:
        limits = {
            "spool_limit_files": 201,
            "spool_limit_bytes": 10_485_761,
        }
        for key, value in limits.items():
            with self.subTest(key=key):
                installation_path = self.runtime.initialize_runtime(
                    self.base / f"tampered-{key}",
                    (self.sessions,),
                    self.config,
                )
                installation = self.runtime.load_installation(
                    installation_path
                )
                payload = json.loads(
                    installation.config_path.read_text(encoding="utf-8")
                )
                payload[key] = value
                installation.config_path.write_text(
                    json.dumps(payload), encoding="utf-8"
                )
                installation.config_path.chmod(0o600)
                with self.assertRaisesRegex(
                    ValueError, f"invalid_config_{key}"
                ):
                    self.runtime.load_config(installation)


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

    def plugin_runtime(self) -> tuple[Path, object]:
        plugin_data = (
            self.installation.data_root.parent
            / "plugins/data/skill-evolver-skill-evolver-dev"
        )
        plugin_data.mkdir(mode=0o700, parents=True)
        runtime = replace(
            self.runtime.load_review_runtime(),
            plugin_data=plugin_data,
        )
        return plugin_data, runtime

    def test_hook_uses_plugin_data_when_canonical_store_is_read_only(
        self,
    ) -> None:
        plugin_data, runtime = self.plugin_runtime()
        before = self.installation.database.read_bytes()
        args = Namespace(
            installation=str(self.installation_path),
            plugin_data=str(plugin_data),
        )
        stdin = mock.Mock()
        stdin.buffer.read.return_value = json.dumps(self.payload).encode()
        with (
            mock.patch.object(
                self.runtime, "load_review_runtime", return_value=runtime
            ),
            mock.patch.object(self.runtime.sys, "stdin", stdin),
            mock.patch.object(
                self.runtime,
                "open_database",
                side_effect=AssertionError("Hook must not open SQLite"),
            ),
        ):
            self.assertEqual(self.runtime.cmd_enqueue_stop(args), 0)
        payloads = list((plugin_data / "stop-spool").glob("*.json"))
        self.assertEqual(len(payloads), 1)
        self.assertEqual(self.installation.database.read_bytes(), before)
        self.assertEqual(list(self.installation.spool.iterdir()), [])
        self.assertNotIn(
            self.transcript.read_bytes().strip(),
            payloads[0].read_bytes(),
        )

    def test_plugin_ingress_coalesces_repeated_session_stops(self) -> None:
        plugin_data, runtime = self.plugin_runtime()
        bound = None
        with mock.patch.object(
            self.runtime, "load_review_runtime", return_value=runtime
        ):
            bound = self.runtime.plugin_spool_installation(
                self.installation, plugin_data, create=True
            )
        raw = json.dumps(self.payload).encode()
        self.assertEqual(
            self.runtime.enqueue_stop(
                bound, self.runtime_config, raw, spool_only=True
            ),
            "spooled",
        )
        first = next(bound.spool.glob("*.json")).read_bytes()
        with self.transcript.open("ab") as stream:
            stream.write(b"new-boundary-without-transcript-read\n")
        self.assertEqual(
            self.runtime.enqueue_stop(
                bound, self.runtime_config, raw, spool_only=True
            ),
            "spooled",
        )
        payloads = list(bound.spool.glob("*.json"))
        self.assertEqual(len(payloads), 1)
        self.assertNotEqual(payloads[0].read_bytes(), first)

    def test_spool_writer_publishes_mode_0600_under_restrictive_umask(
        self,
    ) -> None:
        event = self.runtime.parse_session_stop(
            json.dumps(self.payload).encode(),
            self.installation,
            self.runtime_config,
        )
        assert event is not None
        key = self.runtime.session_key(
            self.installation, event.session_id
        )
        lock = self.installation.spool / ".lock"
        lock.write_bytes(b"")
        lock.chmod(0o600)
        previous = os.umask(0o777)
        try:
            self.assertTrue(
                self.runtime.spool_session_stop(
                    self.installation,
                    self.runtime_config,
                    event,
                    key,
                    time.time(),
                )
            )
        finally:
            os.umask(previous)

        payload = next(self.installation.spool.glob("*.json"))
        self.assertEqual(stat.S_IMODE(payload.stat().st_mode), 0o600)

    def test_plugin_ingress_does_not_follow_spool_swapped_after_binding(
        self,
    ) -> None:
        plugin_data, runtime = self.plugin_runtime()
        with mock.patch.object(
            self.runtime, "load_review_runtime", return_value=runtime
        ):
            bound = self.runtime.plugin_spool_installation(
                self.installation, plugin_data, create=True
            )
        original = plugin_data / "original-stop-spool"
        bound.spool.rename(original)
        outside = self.base / "outside-capture"
        outside.mkdir(mode=0o700)
        sentinel = outside / "sentinel"
        sentinel.write_bytes(b"outside-capture")
        sentinel.chmod(0o600)
        bound.spool.symlink_to(outside, target_is_directory=True)
        before = {
            path.name: path.read_bytes()
            for path in outside.iterdir()
        }
        event = self.runtime.parse_session_stop(
            json.dumps(self.payload).encode(),
            bound,
            self.runtime_config,
        )
        assert event is not None
        key = self.runtime.session_key(bound, event.session_id)

        with self.assertRaises(OSError):
            self.runtime.spool_session_stop(
                bound,
                self.runtime_config,
                event,
                key,
                time.time(),
                coalesce=True,
            )

        self.assertEqual(
            {
                path.name: path.read_bytes()
                for path in outside.iterdir()
            },
            before,
        )

    def test_plugin_ingress_rejects_stale_replacement(self) -> None:
        plugin_data, runtime = self.plugin_runtime()
        with mock.patch.object(
            self.runtime, "load_review_runtime", return_value=runtime
        ):
            bound = self.runtime.plugin_spool_installation(
                self.installation, plugin_data, create=True
            )
        event = self.runtime.parse_session_stop(
            json.dumps(self.payload).encode(),
            bound,
            self.runtime_config,
        )
        assert event is not None
        key = self.runtime.session_key(bound, event.session_id)
        newer = replace(event, observed_at_ns=event.observed_at_ns + 1)
        self.assertTrue(
            self.runtime.spool_session_stop(
                bound,
                self.runtime_config,
                newer,
                key,
                time.time(),
                coalesce=True,
            )
        )
        payload = next(bound.spool.glob("*.json"))
        before = payload.read_bytes()
        self.assertTrue(
            self.runtime.spool_session_stop(
                bound,
                self.runtime_config,
                event,
                key,
                time.time(),
                coalesce=True,
            )
        )
        self.assertEqual(payload.read_bytes(), before)

    def test_plugin_ingress_replaces_at_full_payload_cap_with_sidecars(
        self,
    ) -> None:
        plugin_data, runtime = self.plugin_runtime()
        with mock.patch.object(
            self.runtime, "load_review_runtime", return_value=runtime
        ):
            bound = self.runtime.plugin_spool_installation(
                self.installation, plugin_data, create=True
            )
        event = self.runtime.parse_session_stop(
            json.dumps(self.payload).encode(),
            bound,
            self.runtime_config,
        )
        assert event is not None
        key = self.runtime.session_key(bound, event.session_id)
        self.assertTrue(
            self.runtime.spool_session_stop(
                bound,
                self.runtime_config,
                event,
                key,
                time.time(),
                coalesce=True,
            )
        )
        for index in range(
            self.runtime.HARD_LIMITS["spool_limit_files"] - 1
        ):
            filler = bound.spool / f"filler-{index:03d}.json"
            filler.write_bytes(b"")
            filler.chmod(0o600)
        overflow = bound.spool / "overflow.events"
        overflow.write_bytes(b"1\n")
        overflow.chmod(0o600)
        before_overflow = overflow.read_bytes()
        replacement = replace(
            event,
            observed_at_ns=event.observed_at_ns + 1,
            transcript_size=event.transcript_size + 1,
        )

        spooled = self.runtime.spool_session_stop(
            bound,
            self.runtime_config,
            replacement,
            key,
            time.time(),
            coalesce=True,
        )

        self.assertTrue(spooled)
        self.assertEqual(
            len(list(bound.spool.glob("*.json"))),
            self.runtime.HARD_LIMITS["spool_limit_files"],
        )
        self.assertEqual(len(list(bound.spool.iterdir())), 202)
        self.assertEqual(overflow.read_bytes(), before_overflow)

    def test_plugin_ingress_equal_timestamp_larger_boundary_replaces(
        self,
    ) -> None:
        plugin_data, runtime = self.plugin_runtime()
        with mock.patch.object(
            self.runtime, "load_review_runtime", return_value=runtime
        ):
            bound = self.runtime.plugin_spool_installation(
                self.installation, plugin_data, create=True
            )
        event = self.runtime.parse_session_stop(
            json.dumps(self.payload).encode(),
            bound,
            self.runtime_config,
        )
        assert event is not None
        key = self.runtime.session_key(bound, event.session_id)
        self.assertTrue(
            self.runtime.spool_session_stop(
                bound,
                self.runtime_config,
                event,
                key,
                time.time(),
                coalesce=True,
            )
        )
        payload = next(bound.spool.glob("*.json"))
        before = payload.read_bytes()

        self.assertTrue(
            self.runtime.spool_session_stop(
                bound,
                self.runtime_config,
                replace(event, transcript_size=event.transcript_size + 1),
                key,
                time.time(),
                coalesce=True,
            )
        )
        self.assertNotEqual(payload.read_bytes(), before)

    def test_plugin_ingress_equal_timestamp_different_identity_does_not_replace(
        self,
    ) -> None:
        plugin_data, runtime = self.plugin_runtime()
        with mock.patch.object(
            self.runtime, "load_review_runtime", return_value=runtime
        ):
            bound = self.runtime.plugin_spool_installation(
                self.installation, plugin_data, create=True
            )
        event = self.runtime.parse_session_stop(
            json.dumps(self.payload).encode(),
            bound,
            self.runtime_config,
        )
        assert event is not None
        key = self.runtime.session_key(bound, event.session_id)
        self.assertTrue(
            self.runtime.spool_session_stop(
                bound,
                self.runtime_config,
                event,
                key,
                time.time(),
                coalesce=True,
            )
        )
        payload = next(bound.spool.glob("*.json"))
        before = payload.read_bytes()

        self.assertTrue(
            self.runtime.spool_session_stop(
                bound,
                self.runtime_config,
                replace(
                    event,
                    transcript_inode=event.transcript_inode + 1,
                    transcript_size=event.transcript_size + 1,
                ),
                key,
                time.time(),
                coalesce=True,
            )
        )
        self.assertEqual(payload.read_bytes(), before)

    def test_plugin_ingress_equal_timestamp_same_boundary_does_not_replace(
        self,
    ) -> None:
        plugin_data, runtime = self.plugin_runtime()
        with mock.patch.object(
            self.runtime, "load_review_runtime", return_value=runtime
        ):
            bound = self.runtime.plugin_spool_installation(
                self.installation, plugin_data, create=True
            )
        event = self.runtime.parse_session_stop(
            json.dumps(self.payload).encode(),
            bound,
            self.runtime_config,
        )
        assert event is not None
        key = self.runtime.session_key(bound, event.session_id)
        self.assertTrue(
            self.runtime.spool_session_stop(
                bound,
                self.runtime_config,
                event,
                key,
                time.time(),
                coalesce=True,
            )
        )
        payload = next(bound.spool.glob("*.json"))
        before = payload.read_bytes()

        self.assertTrue(
            self.runtime.spool_session_stop(
                bound,
                self.runtime_config,
                event,
                key,
                time.time(),
                coalesce=True,
            )
        )
        self.assertEqual(payload.read_bytes(), before)

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

    def test_overflow_counter_rejects_hardlink_without_touching_target(
        self,
    ) -> None:
        outside = self.base / "outside-overflow"
        outside.write_bytes(b"outside-overflow-content")
        outside.chmod(0o600)
        counter = self.installation.spool / "overflow.events"
        os.link(outside, counter)

        with self.assertRaisesRegex(
            ValueError, "spool_overflow_permissions"
        ):
            self.runtime.record_spool_overflow(self.installation)

        self.assertEqual(
            outside.read_bytes(), b"outside-overflow-content"
        )

    def test_overflow_counter_fifo_fails_fast_without_reader(self) -> None:
        counter = self.installation.spool / "overflow.events"
        os.mkfifo(counter, 0o600)
        errors: list[BaseException] = []

        def record() -> None:
            try:
                self.runtime.record_spool_overflow(self.installation)
            except BaseException as error:
                errors.append(error)

        worker = threading.Thread(target=record, daemon=True)
        worker.start()
        worker.join(timeout=0.25)
        blocked = worker.is_alive()
        if blocked:
            reader = os.open(counter, os.O_RDONLY | os.O_NONBLOCK)
            try:
                worker.join(timeout=1)
            finally:
                os.close(reader)

        self.assertFalse(blocked)
        self.assertFalse(worker.is_alive())
        self.assertEqual(len(errors), 1)
        self.assertIsInstance(errors[0], ValueError)
        self.assertEqual(str(errors[0]), "spool_overflow_permissions")

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
                "--plugin-data",
                "/Users/igyeongseob/.codex/plugins/data/"
                "skill-evolver-skill-evolver-dev",
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
                "--plugin-data",
                "/Users/igyeongseob/.codex/plugins/data/"
                "skill-evolver-skill-evolver-dev",
                stdin=json.dumps(self.payload).encode(),
            ),
            run_isolated(
                "enqueue-stop",
                "--installation",
                "/missing/installation.json",
                "--plugin-data",
                "/Users/igyeongseob/.codex/plugins/data/"
                "skill-evolver-skill-evolver-dev",
                stdin=b"{",
            ),
            run_isolated(
                "enqueue-stop",
                "--installation",
                str(self.installation_path),
                "--plugin-data",
                "/Users/igyeongseob/.codex/plugins/data/"
                "skill-evolver-skill-evolver-dev",
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
        self.assertEqual(manifest["version"], "0.1.3")
        self.assertEqual(runtime["version"], "0.1.3")
        self.assertEqual(
            load_runtime().VERSION,
            "skill-evolver 0.1.3",
        )
        self.assertEqual(set(hooks["hooks"]), {"Stop"})
        self.assertNotIn("matcher", hooks["hooks"]["Stop"][0])
        self.assertIn(" enqueue-stop ", command)
        self.assertIn(' --plugin-data "$PLUGIN_DATA"', command)
        self.assertNotIn("probe-", command)
        self.assertEqual(
            runtime["installation"],
            "/Users/igyeongseob/.codex/skill-evolver/installation.json",
        )
        self.assertEqual(
            runtime["plugin_data"],
            "/Users/igyeongseob/.codex/plugins/data/"
            "skill-evolver-skill-evolver-dev",
        )
        self.assertNotIn(
            "Status and `maintain` are unavailable in this release",
            skill,
        )
        self.assertIn("- No argument or `status`: run", skill)
        self.assertIn("- `maintain`: show the exact", skill)
        self.assertIn("Status is read-only", skill)
        self.assertIn("No persistent writable-root grant", skill)
        self.assertIn("Never invoke after an ordinary task", skill)
        self.assertIn("stop-spool", skill)
        self.assertIn("canonical and plugin-data roots", skill)
        self.assertNotIn("SubagentStop", json.dumps(hooks))

    def test_readme_uses_v2_gate_and_scoped_mutation_approval(self) -> None:
        readme = (PLUGIN_ROOT / "README.md").read_text(encoding="utf-8")
        evolver = (
            "/Users/igyeongseob/Documents/오픈소스/skill-evolver/"
            "skills/skill-evolver/scripts/evolver.py"
        )
        self.assertIn("docs/feasibility-report-v2.json", readme)
        self.assertIn('"next_action": "write_session_runtime_queue_plan"', readme)
        self.assertIn(
            "/Users/igyeongseob/.codex/skill-evolver/installation.json",
            readme,
        )
        self.assertEqual(readme.count(evolver), 3)
        for command in ("init", "status", "maintain"):
            self.assertIn(f"{evolver} {command}", readme)
        self.assertNotIn(
            "\n  skill-evolver/skills/skill-evolver/scripts/evolver.py ",
            readme,
        )
        self.assertIn("Status needs no write approval", readme)
        self.assertIn("approve only this exact command and data root", readme)
        self.assertIn("200 pending sessions", readme)
        self.assertIn("one row per session", readme)
        self.assertNotIn("200 turns", readme)
        self.assertIn("There is no `SubagentStop` registration.", readme)


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
        self.transcript.write_text(
            '{"payload":{"role":"user"}}\n', encoding="utf-8"
        )
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

    def insert_candidate(
        self,
        connection: sqlite3.Connection,
        fingerprint: str,
        now: float,
    ) -> int:
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
                fingerprint,
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
        return int(cursor.lastrowid)

    def test_stop_during_review_preserves_frozen_locator_and_reopens_generation(
        self,
    ) -> None:
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
        self.assertEqual(
            json.loads(during["frozen_locator_json"]), frozen_locator
        )

        connection.execute("BEGIN IMMEDIATE")
        completed = self.runtime.complete_review_generation(
            connection,
            self.key,
            "owner-a",
            "reviewed",
            None,
            now + 20,
        )
        connection.commit()
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

    def test_heartbeat_is_persistent_owned_live_and_monotonic(self) -> None:
        now = 2_000_000_000.0
        connection = self.runtime.open_database(self.installation)
        observer = self.runtime.open_database(self.installation)
        self.runtime.claim_review_generation(
            connection, self.key, "owner-a", now, self.runtime_config
        )
        expected_expiry = self.runtime.iso_utc(
            now + 10 + self.runtime_config.lease_seconds
        )
        self.assertTrue(
            self.runtime.heartbeat_review_generation(
                connection,
                self.key,
                "owner-a",
                now + 10,
                self.runtime_config,
            )
        )
        persisted = observer.execute(
            "SELECT lease_expires_at FROM review_items WHERE session_key=?",
            (self.key,),
        ).fetchone()
        self.assertEqual(persisted["lease_expires_at"], expected_expiry)

        self.assertTrue(
            self.runtime.heartbeat_review_generation(
                connection,
                self.key,
                "owner-a",
                now + 5,
                self.runtime_config,
            )
        )
        self.assertFalse(
            self.runtime.heartbeat_review_generation(
                connection,
                self.key,
                "owner-b",
                now + 11,
                self.runtime_config,
            )
        )
        self.assertFalse(
            self.runtime.heartbeat_review_generation(
                connection,
                self.key,
                "owner-a",
                now + self.runtime_config.lease_seconds + 11,
                self.runtime_config,
            )
        )
        persisted = observer.execute(
            "SELECT lease_expires_at FROM review_items WHERE session_key=?",
            (self.key,),
        ).fetchone()
        observer.close()
        connection.close()
        self.assertEqual(persisted["lease_expires_at"], expected_expiry)

    def test_stop_timestamp_precedes_transcript_snapshot(self) -> None:
        order: list[str] = []
        real_open = self.runtime.os.open

        def tracked_open(*args: object, **kwargs: object) -> int:
            order.append("open")
            return real_open(*args, **kwargs)

        def tracked_time_ns() -> int:
            order.append("time")
            return 123_456_789

        with mock.patch.object(
            self.runtime.os, "open", side_effect=tracked_open
        ), mock.patch.object(
            self.runtime.time, "time_ns", side_effect=tracked_time_ns
        ):
            event = self.runtime.parse_session_stop(
                self.raw, self.installation, self.runtime_config
            )

        assert event is not None
        self.assertEqual(event.observed_at_ns, 123_456_789)
        self.assertEqual(order, ["time", "open"])

    def test_same_size_mtime_change_requires_epoch_binding(self) -> None:
        connection = self.runtime.open_database(self.installation)
        before = self.transcript.stat()
        os.utime(
            self.transcript,
            ns=(before.st_atime_ns, before.st_mtime_ns + 1_000_000_000),
        )
        event = self.runtime.parse_session_stop(
            self.raw, self.installation, self.runtime_config
        )
        assert event is not None
        self.assertEqual(event.transcript_inode, before.st_ino)
        self.assertEqual(event.transcript_size, before.st_size)
        self.runtime.upsert_session(
            connection,
            event,
            self.key,
            self.runtime_config,
            2_000_000_001.0,
        )
        row = connection.execute(
            """
            SELECT binding_status,error_code
            FROM review_items WHERE session_key=?
            """,
            (self.key,),
        ).fetchone()
        connection.close()
        self.assertEqual(
            tuple(row),
            ("pending_epoch", "transcript_rebind_required"),
        )

    def test_same_size_path_change_requires_epoch_binding(self) -> None:
        moved = self.sessions / "moved.jsonl"
        before = self.transcript.stat()
        self.transcript.rename(moved)
        event = self.runtime.parse_session_stop(
            json.dumps({**self.payload, "transcript_path": str(moved)}).encode(),
            self.installation,
            self.runtime_config,
        )
        assert event is not None
        self.assertEqual(event.transcript_inode, before.st_ino)
        self.assertEqual(event.transcript_size, before.st_size)
        connection = self.runtime.open_database(self.installation)
        self.runtime.upsert_session(
            connection,
            event,
            self.key,
            self.runtime_config,
            2_000_000_001.0,
        )
        row = connection.execute(
            """
            SELECT binding_status,error_code
            FROM review_items WHERE session_key=?
            """,
            (self.key,),
        ).fetchone()
        connection.close()
        self.assertEqual(
            tuple(row),
            ("pending_epoch", "transcript_rebind_required"),
        )

    def test_different_inode_needs_explicit_embedded_binding_before_epoch_reset(
        self,
    ) -> None:
        now = 2_000_000_000.0
        connection = self.runtime.open_database(self.installation)
        self.runtime.claim_review_generation(
            connection, self.key, "owner-a", now, self.runtime_config
        )
        connection.execute("BEGIN IMMEDIATE")
        self.runtime.complete_review_generation(
            connection, self.key, "owner-a", "reviewed", None, now + 1
        )
        connection.commit()
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
        self.assertEqual(
            tuple(before),
            (
                0,
                self.transcript.stat().st_size,
                "pending_epoch",
                2,
            ),
        )
        with self.assertRaisesRegex(
            ValueError, "transcript_binding_required"
        ):
            self.runtime.claim_review_generation(
                connection, self.key, "owner-b", now + 3, self.runtime_config
            )
        with self.assertRaisesRegex(
            ValueError, "embedded_session_mismatch"
        ):
            self.runtime.adopt_transcript_epoch(
                connection,
                self.key,
                "0" * 64,
                event.transcript_path,
                event.transcript_mtime_ns,
                event.transcript_device,
                event.transcript_inode,
                event.transcript_size,
                "embedded_session_id",
                now + 4,
            )
        replacement_info = replacement.stat()
        os.utime(
            replacement,
            ns=(
                replacement_info.st_atime_ns,
                event.transcript_mtime_ns + 1_000_000_000,
            ),
        )
        with self.assertRaisesRegex(ValueError, "epoch_locator_changed"):
            self.runtime.adopt_transcript_epoch(
                connection,
                self.key,
                self.key,
                event.transcript_path,
                event.transcript_mtime_ns,
                event.transcript_device,
                event.transcript_inode,
                event.transcript_size,
                "embedded_session_id",
                now + 5,
            )
        refreshed = self.runtime.parse_session_stop(
            json.dumps(
                {**self.payload, "transcript_path": str(replacement)}
            ).encode(),
            self.installation,
            self.runtime_config,
        )
        assert refreshed is not None
        self.runtime.upsert_session(
            connection,
            refreshed,
            self.key,
            self.runtime_config,
            now + 5,
        )
        epoch = self.runtime.adopt_transcript_epoch(
            connection,
            self.key,
            self.key,
            refreshed.transcript_path,
            refreshed.transcript_mtime_ns,
            refreshed.transcript_device,
            refreshed.transcript_inode,
            refreshed.transcript_size,
            "embedded_session_id",
            now + 6,
        )
        claim = self.runtime.claim_review_generation(
            connection, self.key, "owner-b", now + 7, self.runtime_config
        )
        connection.close()
        self.assertEqual(epoch, 1)
        self.assertEqual(claim["transcript_epoch"], 1)
        self.assertEqual(claim["review_from"], 0)
        self.assertEqual(claim["review_to"], refreshed.transcript_size)

    def test_evidence_requires_owned_live_generation_transaction(self) -> None:
        now = 2_000_000_000.0
        connection = self.runtime.open_database(self.installation)
        row = connection.execute(
            "SELECT id FROM review_items WHERE session_key=?",
            (self.key,),
        ).fetchone()
        review_item_id = int(row["id"])
        candidate_id = self.insert_candidate(connection, "a" * 64, now)

        with self.assertRaisesRegex(
            ValueError, "active_review_transaction_required"
        ):
            self.runtime.record_candidate_evidence(
                connection,
                candidate_id,
                review_item_id,
                "owner-a",
                1,
                "verification_failure",
                "user_direct",
                "no transaction",
                now,
            )

        connection.execute("BEGIN IMMEDIATE")
        with self.assertRaisesRegex(
            ValueError, "review_evidence_lease_unavailable"
        ):
            self.runtime.record_candidate_evidence(
                connection,
                candidate_id,
                review_item_id,
                "owner-a",
                1,
                "verification_failure",
                "user_direct",
                "pending row",
                now,
            )
        connection.rollback()

        claim = self.runtime.claim_review_generation(
            connection, self.key, "owner-a", now, self.runtime_config
        )
        connection.execute("BEGIN IMMEDIATE")
        invalid_claims = (
            ("owner-a", True, now + 1),
            ("owner-b", int(claim["generation"]), now + 1),
            ("owner-a", int(claim["generation"]) + 1, now + 1),
            (
                "owner-a",
                int(claim["generation"]),
                now + self.runtime_config.lease_seconds + 1,
            ),
        )
        for owner, generation, observed_at in invalid_claims:
            with self.subTest(
                owner=owner,
                generation=generation,
                observed_at=observed_at,
            ):
                with self.assertRaisesRegex(
                    ValueError, "review_evidence_lease_unavailable"
                ):
                    self.runtime.record_candidate_evidence(
                        connection,
                        candidate_id,
                        review_item_id,
                        owner,
                        generation,
                        "verification_failure",
                        "user_direct",
                        "invalid lease",
                        observed_at,
                    )
        connection.rollback()
        connection.close()

    def test_evidence_and_completion_commit_or_rollback_together(self) -> None:
        now = 2_000_000_000.0
        connection = self.runtime.open_database(self.installation)
        claim = self.runtime.claim_review_generation(
            connection, self.key, "owner-a", now, self.runtime_config
        )
        row = connection.execute(
            "SELECT id FROM review_items WHERE session_key=?",
            (self.key,),
        ).fetchone()
        review_item_id = int(row["id"])

        connection.execute("BEGIN IMMEDIATE")
        candidate_id = self.insert_candidate(connection, "b" * 64, now)
        self.assertTrue(
            self.runtime.record_candidate_evidence(
                connection,
                candidate_id,
                review_item_id,
                "owner-a",
                int(claim["generation"]),
                "verification_failure",
                "user_direct",
                "rollback evidence",
                now + 1,
            )
        )
        self.runtime.complete_review_generation(
            connection, self.key, "owner-a", "reviewed", None, now + 2
        )
        connection.rollback()
        rolled_back = connection.execute(
            """
            SELECT
              (SELECT COUNT(*) FROM candidates) AS candidates,
              (SELECT COUNT(*) FROM candidate_evidence) AS evidence,
              status
            FROM review_items WHERE session_key=?
            """,
            (self.key,),
        ).fetchone()
        self.assertEqual(tuple(rolled_back), (0, 0, "reviewing"))

        connection.execute("BEGIN IMMEDIATE")
        candidate_id = self.insert_candidate(connection, "b" * 64, now)
        self.assertTrue(
            self.runtime.record_candidate_evidence(
                connection,
                candidate_id,
                review_item_id,
                "owner-a",
                int(claim["generation"]),
                "verification_failure",
                "user_direct",
                "committed evidence",
                now + 3,
            )
        )
        completed = self.runtime.complete_review_generation(
            connection, self.key, "owner-a", "reviewed", None, now + 4
        )
        connection.commit()
        committed = connection.execute(
            """
            SELECT
              (SELECT COUNT(*) FROM candidates) AS candidates,
              (SELECT COUNT(*) FROM candidate_evidence) AS evidence,
              status
            FROM review_items WHERE session_key=?
            """,
            (self.key,),
        ).fetchone()
        connection.close()
        self.assertEqual(completed["status"], "reviewed")
        self.assertEqual(tuple(committed), (1, 1, "reviewed"))

    def test_completion_reopens_when_current_locator_differs(self) -> None:
        now = 2_000_000_000.0
        connection = self.runtime.open_database(self.installation)
        claim = self.runtime.claim_review_generation(
            connection, self.key, "owner-a", now, self.runtime_config
        )
        connection.execute(
            """
            UPDATE review_items
            SET transcript_mtime_ns=transcript_mtime_ns+1
            WHERE session_key=?
            """,
            (self.key,),
        )
        connection.execute("BEGIN IMMEDIATE")
        completed = self.runtime.complete_review_generation(
            connection,
            self.key,
            "owner-a",
            "reviewed",
            None,
            now + 1,
        )
        connection.commit()
        row = connection.execute(
            "SELECT status,generation FROM review_items WHERE session_key=?",
            (self.key,),
        ).fetchone()
        connection.close()
        self.assertEqual(completed["status"], "pending")
        self.assertEqual(completed["reviewed_boundary"], claim["review_to"])
        self.assertEqual(tuple(row), ("pending", 2))

    def test_concurrent_stop_clears_exclusion_when_reopened(self) -> None:
        now = 2_000_000_000.0
        connection = self.runtime.open_database(self.installation)
        self.runtime.claim_review_generation(
            connection, self.key, "owner-a", now, self.runtime_config
        )
        connection.execute(
            """
            UPDATE review_items SET excluded_reason='prior exclusion'
            WHERE session_key=?
            """,
            (self.key,),
        )
        with self.transcript.open("ab") as stream:
            stream.write(b'{"payload":{"role":"assistant"}}\n')
        event = self.runtime.parse_session_stop(
            self.raw, self.installation, self.runtime_config
        )
        assert event is not None
        self.runtime.upsert_session(
            connection, event, self.key, self.runtime_config, now + 1
        )
        during = connection.execute(
            """
            SELECT status,excluded_reason
            FROM review_items WHERE session_key=?
            """,
            (self.key,),
        ).fetchone()
        self.assertEqual(tuple(during), ("reviewing", "prior exclusion"))
        connection.execute("BEGIN IMMEDIATE")
        completed = self.runtime.complete_review_generation(
            connection,
            self.key,
            "owner-a",
            "excluded",
            "current generation excluded",
            now + 2,
        )
        connection.commit()
        row = connection.execute(
            """
            SELECT status,excluded_reason
            FROM review_items WHERE session_key=?
            """,
            (self.key,),
        ).fetchone()
        connection.close()
        self.assertEqual(completed["status"], "pending")
        self.assertEqual(tuple(row), ("pending", None))

    def test_later_stop_clears_completed_exclusion_when_reopened(self) -> None:
        now = 2_000_000_000.0
        connection = self.runtime.open_database(self.installation)
        self.runtime.claim_review_generation(
            connection, self.key, "owner-a", now, self.runtime_config
        )
        connection.execute("BEGIN IMMEDIATE")
        completed = self.runtime.complete_review_generation(
            connection,
            self.key,
            "owner-a",
            "excluded",
            "not a reusable skill issue",
            now + 1,
        )
        connection.commit()
        excluded = connection.execute(
            """
            SELECT status,generation,excluded_reason
            FROM review_items WHERE session_key=?
            """,
            (self.key,),
        ).fetchone()
        self.assertEqual(completed["status"], "excluded")
        self.assertEqual(
            tuple(excluded),
            ("excluded", 1, "not a reusable skill issue"),
        )

        with self.transcript.open("ab") as stream:
            stream.write(b'{"payload":{"role":"assistant"}}\n')
        event = self.runtime.parse_session_stop(
            self.raw, self.installation, self.runtime_config
        )
        assert event is not None
        self.runtime.upsert_session(
            connection, event, self.key, self.runtime_config, now + 2
        )
        reopened = connection.execute(
            """
            SELECT status,generation,excluded_reason
            FROM review_items WHERE session_key=?
            """,
            (self.key,),
        ).fetchone()
        connection.close()
        self.assertEqual(tuple(reopened), ("pending", 2, None))

    def test_duplicate_and_stale_stop_preserve_terminal_exclusion(self) -> None:
        now = 2_000_000_000.0
        connection = self.runtime.open_database(self.installation)
        self.runtime.claim_review_generation(
            connection, self.key, "owner-a", now, self.runtime_config
        )
        connection.execute("BEGIN IMMEDIATE")
        self.runtime.complete_review_generation(
            connection,
            self.key,
            "owner-a",
            "excluded",
            "terminal exclusion",
            now + 1,
        )
        connection.commit()

        duplicate = self.runtime.parse_session_stop(
            self.raw, self.installation, self.runtime_config
        )
        assert duplicate is not None
        self.assertEqual(
            self.runtime.upsert_session(
                connection,
                duplicate,
                self.key,
                self.runtime_config,
                now + 2,
            ),
            "duplicate",
        )
        self.assertEqual(
            self.runtime.upsert_session(
                connection,
                replace(duplicate, observed_at_ns=0),
                self.key,
                self.runtime_config,
                now + 3,
            ),
            "stale",
        )
        row = connection.execute(
            """
            SELECT status,generation,excluded_reason
            FROM review_items WHERE session_key=?
            """,
            (self.key,),
        ).fetchone()
        connection.close()
        self.assertEqual(
            tuple(row),
            ("excluded", 1, "terminal exclusion"),
        )

    def test_candidate_evidence_is_unique_across_session_generations(
        self,
    ) -> None:
        now = 2_000_000_000.0
        connection = self.runtime.open_database(self.installation)
        first_claim = self.runtime.claim_review_generation(
            connection, self.key, "owner-a", now, self.runtime_config
        )
        row = connection.execute(
            "SELECT id FROM review_items WHERE session_key=?",
            (self.key,),
        ).fetchone()
        candidate_id = self.insert_candidate(connection, "f" * 64, now)
        connection.execute("BEGIN IMMEDIATE")
        first = self.runtime.record_candidate_evidence(
            connection,
            candidate_id,
            int(row["id"]),
            "owner-a",
            int(first_claim["generation"]),
            "verification_failure",
            "user_direct",
            "sanitized evidence",
            now,
        )
        self.runtime.complete_review_generation(
            connection, self.key, "owner-a", "reviewed", None, now + 1
        )
        connection.commit()

        with self.transcript.open("ab") as stream:
            stream.write(b'{"payload":{"role":"assistant"}}\n')
        event = self.runtime.parse_session_stop(
            self.raw, self.installation, self.runtime_config
        )
        assert event is not None
        self.runtime.upsert_session(
            connection, event, self.key, self.runtime_config, now + 2
        )
        second_claim = self.runtime.claim_review_generation(
            connection, self.key, "owner-b", now + 3, self.runtime_config
        )
        connection.execute("BEGIN IMMEDIATE")
        second = self.runtime.record_candidate_evidence(
            connection,
            candidate_id,
            int(row["id"]),
            "owner-b",
            int(second_claim["generation"]),
            "verification_failure",
            "user_direct",
            "same session later generation",
            now + 4,
        )
        self.runtime.complete_review_generation(
            connection, self.key, "owner-b", "reviewed", None, now + 5
        )
        connection.commit()
        count = connection.execute(
            "SELECT COUNT(*) FROM candidate_evidence"
        ).fetchone()[0]
        connection.close()
        self.assertTrue(first)
        self.assertFalse(second)
        self.assertEqual(count, 1)


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
        self.installation = self.runtime.load_installation(
            self.installation_path
        )
        self.runtime_config = self.runtime.load_config(self.installation)
        self.transcript = self.sessions / "session.jsonl"
        self.transcript.write_text(
            '{"payload":{"role":"user"}}\n', encoding="utf-8"
        )

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

    def capture_installation(self):
        plugin_data = (
            self.installation.data_root.parent
            / "plugins/data/skill-evolver-skill-evolver-dev"
        )
        plugin_data.mkdir(mode=0o700, parents=True)
        spool = plugin_data / "stop-spool"
        spool.mkdir(mode=0o700)
        return replace(self.installation, spool=spool)

    def test_maintenance_imports_plugin_data_spool_idempotently(
        self,
    ) -> None:
        capture = self.capture_installation()
        event = replace(
            self.event("plugin-session"),
            observed_at_ns=2_000_000_000_000_000_000,
        )
        key = self.runtime.session_key(
            self.installation, event.session_id
        )
        self.assertTrue(
            self.runtime.spool_session_stop(
                capture,
                self.runtime_config,
                event,
                key,
                2_000_000_000.0,
                coalesce=True,
            )
        )
        source = next(capture.spool.glob("*.json"))
        replay = source.read_bytes()
        connection = self.runtime.open_database(self.installation)
        first = self.runtime.run_maintenance(
            connection,
            self.installation,
            self.runtime_config,
            2_000_000_001.0,
            capture_installation=capture,
        )
        source.write_bytes(replay)
        source.chmod(0o600)
        second = self.runtime.run_maintenance(
            connection,
            self.installation,
            self.runtime_config,
            2_000_000_002.0,
            capture_installation=capture,
        )
        rows = connection.execute(
            "SELECT COUNT(*) FROM review_items"
        ).fetchone()[0]
        connection.close()
        self.assertEqual(first["spool_imported"], 1)
        self.assertEqual(second["spool_imported"], 0)
        self.assertEqual(second["spool_duplicates"], 1)
        self.assertEqual(rows, 1)
        self.assertEqual(list(capture.spool.glob("*.json")), [])

    def test_status_reports_unimported_plugin_data_capture_read_only(
        self,
    ) -> None:
        capture = self.capture_installation()
        now = time.time()
        event = self.event("unimported-plugin-session")
        key = self.runtime.session_key(
            self.installation, event.session_id
        )
        self.assertTrue(
            self.runtime.spool_session_stop(
                capture,
                self.runtime_config,
                event,
                key,
                now,
                coalesce=True,
            )
        )
        database_before = self.installation.database.read_bytes()
        spool_before = {
            path.name: path.read_bytes()
            for path in capture.spool.iterdir()
            if path.is_file()
        }
        connection = self.runtime.open_database(
            self.installation, read_only=True
        )
        with mock.patch.object(
            self.runtime,
            "import_spool",
            side_effect=AssertionError("status import"),
        ), mock.patch.object(
            self.runtime,
            "run_maintenance",
            side_effect=AssertionError("status mutation"),
        ):
            status = self.runtime.queue_status(
                connection, capture, now + 1
            )
        connection.close()
        self.assertEqual(status["pending_sessions"], 0)
        self.assertEqual(status["spool"]["files"], 1)
        self.assertEqual(status["spool"]["verified_files"], 1)
        self.assertTrue(status["spool"]["available"])
        self.assertEqual(
            self.installation.database.read_bytes(), database_before
        )
        self.assertEqual(
            {
                path.name: path.read_bytes()
                for path in capture.spool.iterdir()
                if path.is_file()
            },
            spool_before,
        )

    def test_status_counts_only_verified_private_single_link_payloads(
        self,
    ) -> None:
        capture = self.capture_installation()
        now = time.time()
        event = self.event("verified-status-session")
        key = self.runtime.session_key(
            self.installation, event.session_id
        )
        self.assertTrue(
            self.runtime.spool_session_stop(
                capture,
                self.runtime_config,
                event,
                key,
                now,
                coalesce=True,
            )
        )
        malformed = capture.spool / "malformed.json"
        malformed.write_bytes(b"{not-json\n")
        malformed.chmod(0o600)
        outside = self.base / "status-outside.json"
        outside.write_bytes(next(capture.spool.glob("session-*.json")).read_bytes())
        outside.chmod(0o600)
        (capture.spool / "symlinked.json").symlink_to(outside)
        hardlink_source = self.base / "status-hardlink-source.json"
        hardlink_source.write_bytes(outside.read_bytes())
        hardlink_source.chmod(0o600)
        os.link(hardlink_source, capture.spool / "hardlinked.json")

        def snapshot() -> list[tuple[str, int, int, bytes]]:
            result: list[tuple[str, int, int, bytes]] = []
            for path in sorted(capture.spool.iterdir()):
                info = os.lstat(path)
                value = (
                    os.readlink(path).encode()
                    if stat.S_ISLNK(info.st_mode)
                    else path.read_bytes()
                    if stat.S_ISREG(info.st_mode)
                    else b""
                )
                result.append(
                    (path.name, info.st_mode, info.st_nlink, value)
                )
            return result

        database_before = self.installation.database.read_bytes()
        spool_before = snapshot()
        connection = self.runtime.open_database(
            self.installation, read_only=True
        )
        status = self.runtime.queue_status(connection, capture, now)
        connection.close()

        self.assertEqual(status["spool"]["files"], 3)
        self.assertEqual(status["spool"]["verified_files"], 1)
        self.assertEqual(
            self.installation.database.read_bytes(), database_before
        )
        self.assertEqual(snapshot(), spool_before)

    def test_maintenance_does_not_follow_spool_swapped_after_binding(
        self,
    ) -> None:
        capture = self.capture_installation()
        original = capture.spool.with_name("original-stop-spool")
        capture.spool.rename(original)
        outside = self.base / "outside-maintenance"
        outside.mkdir(mode=0o700)
        sentinel = outside / "sentinel.json"
        sentinel.write_bytes(b"outside-maintenance")
        sentinel.chmod(0o600)
        capture.spool.symlink_to(outside, target_is_directory=True)
        before = sentinel.stat()
        connection = self.runtime.open_database(self.installation)
        try:
            with self.assertRaises(OSError):
                self.runtime.import_spool(
                    connection,
                    capture,
                    self.runtime_config,
                    time.time(),
                )
        finally:
            connection.close()

        after = sentinel.stat()
        self.assertEqual(sentinel.read_bytes(), b"outside-maintenance")
        self.assertEqual(
            (after.st_dev, after.st_ino, after.st_mtime_ns),
            (before.st_dev, before.st_ino, before.st_mtime_ns),
        )

    def test_maintenance_removes_only_exact_private_writer_temps(
        self,
    ) -> None:
        exact = [
            self.installation.spool
            / f".spool-write-{index:032x}.tmp"
            for index in range(3)
        ]
        for path in exact:
            path.write_bytes(b"stale")
            path.chmod(0o600)
        exact[-1].chmod(0o000)
        preserved = [
            self.installation.spool / ".spool-write-short.tmp",
            self.installation.spool
            / ".spool-write-0000000000000000000000000000000G.tmp",
            self.installation.spool
            / ".spool-write-00000000000000000000000000000002.tmpx",
        ]
        for path in preserved:
            path.write_bytes(b"preserve")
            path.chmod(0o600)
        wrong_mode = (
            self.installation.spool
            / ".spool-write-00000000000000000000000000000003.tmp"
        )
        wrong_mode.write_bytes(b"preserve")
        wrong_mode.chmod(0o644)
        hardlink_source = self.base / "writer-temp-hardlink"
        hardlink_source.write_bytes(b"preserve")
        hardlink_source.chmod(0o600)
        hardlinked = (
            self.installation.spool
            / ".spool-write-00000000000000000000000000000004.tmp"
        )
        os.link(hardlink_source, hardlinked)
        symlinked = (
            self.installation.spool
            / ".spool-write-00000000000000000000000000000005.tmp"
        )
        symlinked.symlink_to(hardlink_source)

        connection = self.runtime.open_database(self.installation)
        result = self.runtime.run_maintenance(
            connection,
            self.installation,
            self.runtime_config,
            time.time(),
        )
        connection.close()

        self.assertEqual(result.get("spool_writer_temps_deleted"), 3)
        self.assertTrue(all(not path.exists() for path in exact))
        self.assertTrue(all(path.exists() for path in preserved))
        self.assertTrue(wrong_mode.exists())
        self.assertTrue(hardlinked.exists())
        self.assertTrue(symlinked.is_symlink())
        self.assertEqual(hardlink_source.read_bytes(), b"preserve")

    def test_maintenance_recovers_scan_full_writer_temp_inventory(
        self,
    ) -> None:
        writer_temps = self.runtime.MAX_SPOOL_SCAN_ENTRIES - 2
        for index in range(writer_temps):
            path = (
                self.installation.spool
                / f".spool-write-{index:032x}.tmp"
            )
            path.write_bytes(b"stale")
            path.chmod(0o600)
        unrelated = self.installation.spool / "unrelated"
        unrelated.write_bytes(b"preserve")
        unrelated.chmod(0o600)

        connection = self.runtime.open_database(self.installation)
        result = self.runtime.import_spool(
            connection,
            self.installation,
            self.runtime_config,
            time.time(),
        )
        connection.close()

        self.assertEqual(
            result.get("spool_writer_temps_deleted"),
            writer_temps,
        )
        self.assertEqual(result["spool_scan_saturated"], 0)
        self.assertEqual(unrelated.read_bytes(), b"preserve")
        self.assertEqual(
            [
                path
                for path in self.installation.spool.iterdir()
                if path.name.startswith(".spool-write-")
            ],
            [],
        )

    def test_writer_temp_cleanup_revalidates_private_file_before_unlink(
        self,
    ) -> None:
        temporary = (
            self.installation.spool
            / ".spool-write-00000000000000000000000000000000.tmp"
        )
        temporary.write_bytes(b"preserve")
        temporary.chmod(0o600)
        parent_descriptor, spool_descriptor = (
            self.runtime.open_spool_directories(self.installation)
        )
        real_stat = os.stat
        target_stats = 0

        def make_nonprivate_before_second_stat(
            target: object, *args: object, **kwargs: object
        ):
            nonlocal target_stats
            if (
                target == temporary.name
                and kwargs.get("dir_fd") == spool_descriptor
            ):
                target_stats += 1
                if target_stats == 2:
                    temporary.chmod(0o644)
            return real_stat(target, *args, **kwargs)

        try:
            with mock.patch.object(
                self.runtime.os,
                "stat",
                side_effect=make_nonprivate_before_second_stat,
            ), self.runtime.open_locked_spool(spool_descriptor):
                deleted = self.runtime.cleanup_spool_writer_temps(
                    spool_descriptor
                )
        finally:
            os.close(spool_descriptor)
            os.close(parent_descriptor)

        self.assertEqual(deleted, 0)
        self.assertEqual(temporary.read_bytes(), b"preserve")
        self.assertEqual(stat.S_IMODE(temporary.stat().st_mode), 0o644)

    def test_raw_transcript_content_is_never_persisted_in_ingress_or_database(
        self,
    ) -> None:
        sentinel = b"transcript-body-private-7e8c0ff27da5453b"
        self.transcript.write_bytes(sentinel)
        capture = self.capture_installation()
        runtime = replace(
            self.runtime.load_review_runtime(),
            plugin_data=capture.spool.parent,
        )
        payload = {
            "hook_event_name": "Stop",
            "session_id": "public-privacy-session",
            "cwd": str(self.workspace),
            "transcript_path": str(self.transcript),
        }
        stdin = mock.Mock()
        stdin.buffer.read.return_value = json.dumps(payload).encode()
        with mock.patch.object(
            self.runtime, "load_review_runtime", return_value=runtime
        ), mock.patch.object(
            self.runtime.sys, "stdin", stdin
        ), mock.patch.object(
            self.runtime, "write_json_stdout"
        ):
            self.assertEqual(
                self.runtime.cmd_enqueue_stop(
                    Namespace(
                        installation=str(self.installation_path),
                        plugin_data=str(capture.spool.parent),
                    )
                ),
                0,
            )
            self.assertEqual(
                self.runtime.cmd_maintain(
                    Namespace(
                        installation=str(self.installation_path),
                        plugin_data=str(capture.spool.parent),
                    )
                ),
                0,
            )
        connection = self.runtime.open_database(
            self.installation, read_only=True
        )
        rows = connection.execute(
            "SELECT COUNT(*) FROM review_items"
        ).fetchone()[0]
        connection.close()
        self.assertEqual(rows, 1)
        self.assertEqual(list(capture.spool.glob("*.json")), [])
        for root in (self.installation.data_root, capture.spool.parent):
            for path in root.rglob("*"):
                if path.is_file() and not path.is_symlink():
                    self.assertNotIn(sentinel, path.read_bytes(), path)

    def test_status_command_opens_read_only_immediately_after_init(self) -> None:
        skill = (SKILL_ROOT / "SKILL.md").read_text(encoding="utf-8")
        self.assertIn(
            "- No argument or `status`: run `status --installation", skill
        )
        self.assertIn(
            "- `maintain`: show the exact `maintain --installation", skill
        )
        self.assertNotIn("Status and `maintain` are unavailable", skill)
        database_before = self.installation.database.read_bytes()
        root_entries_before = sorted(
            path.name for path in self.installation.data_root.iterdir()
        )
        spool_entries_before = sorted(
            path.name for path in self.installation.spool.iterdir()
        )
        plugin_data = (
            self.installation.data_root.parent
            / "plugins/data/skill-evolver-skill-evolver-dev"
        )
        runtime = replace(
            self.runtime.load_review_runtime(), plugin_data=plugin_data
        )
        open_database = self.runtime.open_database
        read_only_modes: list[bool] = []

        def tracked_open_database(
            installation, *, read_only: bool = False
        ):
            read_only_modes.append(read_only)
            return open_database(installation, read_only=read_only)

        captured: list[dict[str, object]] = []
        with mock.patch.object(
            self.runtime, "load_review_runtime", return_value=runtime
        ), mock.patch.object(
            self.runtime,
            "open_database",
            side_effect=tracked_open_database,
        ), mock.patch.object(
            self.runtime,
            "write_json_stdout",
            side_effect=captured.append,
        ):
            result = self.runtime.cmd_status(
                Namespace(
                    installation=str(self.installation_path),
                    plugin_data=str(plugin_data),
                )
            )
        self.assertEqual(result, 0)
        self.assertEqual(read_only_modes, [True])
        status = captured[0]
        self.assertEqual(status["pending_sessions"], 0)
        self.assertEqual(status["spool"]["files"], 0)
        self.assertFalse(status["spool"]["available"])
        self.assertFalse(plugin_data.exists())
        self.assertEqual(
            self.installation.database.read_bytes(),
            database_before,
        )
        self.assertEqual(
            sorted(
                path.name for path in self.installation.data_root.iterdir()
            ),
            root_entries_before,
        )
        self.assertEqual(
            sorted(path.name for path in self.installation.spool.iterdir()),
            spool_entries_before,
        )

    def test_handlers_reject_wrong_plugin_data_before_touching_pinned_root(
        self,
    ) -> None:
        capture = self.capture_installation()
        sentinel = capture.spool / "pinned.json"
        sentinel.write_bytes(b"pinned\n")
        sentinel.chmod(0o600)
        runtime = replace(
            self.runtime.load_review_runtime(),
            plugin_data=capture.spool.parent,
        )
        wrong = self.base / "wrong-plugin-data"

        with mock.patch.object(
            self.runtime, "load_review_runtime", return_value=runtime
        ), mock.patch.object(
            self.runtime,
            "open_database",
            side_effect=AssertionError("database touched"),
        ):
            for handler in (
                self.runtime.cmd_status,
                self.runtime.cmd_maintain,
            ):
                with self.subTest(handler=handler.__name__):
                    with self.assertRaisesRegex(
                        ValueError, "invalid_plugin_data"
                    ):
                        handler(
                            Namespace(
                                installation=str(self.installation_path),
                                plugin_data=str(wrong),
                            )
                        )
                    self.assertEqual(sentinel.read_bytes(), b"pinned\n")

    def test_spool_import_converges_by_session_and_deletes_invalid_payload(
        self,
    ) -> None:
        now = 2_000_000_000.0
        event = replace(
            self.event(), observed_at_ns=int(now * 1_000_000_000)
        )
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
        outside.write_text(
            '{"private":"do-not-follow"}\n', encoding="utf-8"
        )
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

    def test_maintenance_drains_plugin_and_legacy_spools(self) -> None:
        now = 2_000_000_000.0
        capture = self.capture_installation()
        for installation, session_id in (
            (capture, "plugin-spool-session"),
            (self.installation, "legacy-spool-session"),
        ):
            event = replace(
                self.event(session_id),
                observed_at_ns=int(now * 1_000_000_000),
            )
            key = self.runtime.session_key(
                self.installation, event.session_id
            )
            self.assertTrue(
                self.runtime.spool_session_stop(
                    installation,
                    self.runtime_config,
                    event,
                    key,
                    now,
                    coalesce=True,
                )
            )
        connection = self.runtime.open_database(self.installation)
        result = self.runtime.run_maintenance(
            connection,
            self.installation,
            self.runtime_config,
            now + 1,
            capture_installation=capture,
        )
        rows = connection.execute(
            "SELECT COUNT(*) FROM review_items"
        ).fetchone()[0]
        connection.close()
        self.assertEqual(result["spool_imported"], 2)
        self.assertEqual(rows, 2)
        self.assertEqual(list(capture.spool.glob("*.json")), [])
        self.assertEqual(
            list(self.installation.spool.glob("*.json")), []
        )

    def test_spool_import_is_bounded_and_does_not_read_transcripts(self) -> None:
        now = 2_000_000_000.0
        event = replace(
            self.event("no-transcript-read"),
            observed_at_ns=int(now * 1_000_000_000),
        )
        key = self.runtime.session_key(self.installation, event.session_id)
        self.assertTrue(
            self.runtime.spool_session_stop(
                self.installation, self.runtime_config, event, key, now
            )
        )
        self.transcript.unlink()
        connection = self.runtime.open_database(self.installation)
        imported = self.runtime.import_spool(
            connection, self.installation, self.runtime_config, now + 1
        )
        self.assertEqual(imported["spool_imported"], 1)

        self.transcript.write_text(
            '{"payload":{"role":"user"}}\n', encoding="utf-8"
        )
        saturated = replace(
            self.event("saturated-spool"),
            observed_at_ns=int((now + 1) * 1_000_000_000),
        )
        saturated_key = self.runtime.session_key(
            self.installation, saturated.session_id
        )
        self.assertTrue(
            self.runtime.spool_session_stop(
                self.installation,
                self.runtime_config,
                saturated,
                saturated_key,
                now + 1,
            )
        )
        existing = len(list(self.installation.spool.iterdir()))
        for index in range(
            self.runtime.MAX_SPOOL_SCAN_ENTRIES - existing
        ):
            entry = self.installation.spool / f"{index:03d}.tmp"
            entry.write_bytes(b"")
            entry.chmod(0o600)
        saturated_result = self.runtime.import_spool(
            connection, self.installation, self.runtime_config, now + 2
        )
        self.assertEqual(saturated_result["spool_scan_saturated"], 1)
        self.assertEqual(saturated_result["spool_imported"], 1)
        self.assertEqual(
            len(list(self.installation.spool.glob("*.json"))), 0
        )
        connection.close()

    def test_import_admits_full_payload_cap_with_known_sidecars(self) -> None:
        now = 2_000_000_000.0
        event = replace(
            self.event("full-spool"),
            observed_at_ns=int(now * 1_000_000_000),
        )
        key = self.runtime.session_key(self.installation, event.session_id)
        payload = self.runtime.spooled_stop_payload(
            self.installation, event, key, self.runtime_config
        )
        lock = self.installation.spool / ".lock"
        lock.write_bytes(b"")
        lock.chmod(0o600)
        overflow = self.installation.spool / "overflow.events"
        overflow.write_bytes(b"1\n")
        overflow.chmod(0o600)
        for index in range(200):
            path = self.installation.spool / f"{index:03d}.json"
            path.write_text(json.dumps(payload), encoding="utf-8")
            path.chmod(0o600)

        connection = self.runtime.open_database(self.installation)
        result = self.runtime.import_spool(
            connection, self.installation, self.runtime_config, now + 1
        )
        connection.close()
        self.assertEqual(result["spool_imported"], 1)
        self.assertEqual(result["spool_duplicates"], 199)
        self.assertEqual(list(self.installation.spool.glob("*.json")), [])

    def test_saturated_import_drains_near_cap_junk_and_hook_resumes(
        self,
    ) -> None:
        now = 2_000_000_000.0
        event = replace(
            self.event("near-cap-session"),
            observed_at_ns=int(now * 1_000_000_000),
        )
        key = self.runtime.session_key(self.installation, event.session_id)
        payload = self.runtime.spooled_stop_payload(
            self.installation, event, key, self.runtime_config
        )
        lock = self.installation.spool / ".lock"
        lock.write_bytes(b"")
        lock.chmod(0o600)
        overflow = self.installation.spool / "overflow.events"
        overflow.write_bytes(b"1\n")
        overflow.chmod(0o600)
        crash = self.installation.spool / ".crash-partial"
        crash.write_bytes(b"")
        crash.chmod(0o600)
        for index in range(200):
            path = self.installation.spool / f"{index:03d}.json"
            path.write_text(json.dumps(payload), encoding="utf-8")
            path.chmod(0o600)

        blocked = replace(
            self.event("blocked-at-cap"),
            observed_at_ns=int((now + 1) * 1_000_000_000),
        )
        blocked_key = self.runtime.session_key(
            self.installation, blocked.session_id
        )
        self.assertFalse(
            self.runtime.spool_session_stop(
                self.installation,
                self.runtime_config,
                blocked,
                blocked_key,
                now + 1,
            )
        )
        self.assertEqual(
            len(list(self.installation.spool.glob("*.json"))), 200
        )

        connection = self.runtime.open_database(self.installation)
        saturation: list[int] = []
        for _ in range(3):
            if not list(self.installation.spool.glob("*.json")):
                break
            result = self.runtime.import_spool(
                connection,
                self.installation,
                self.runtime_config,
                now + 2,
            )
            saturation.append(result["spool_scan_saturated"])
        connection.close()
        self.assertIn(1, saturation)
        self.assertEqual(list(self.installation.spool.glob("*.json")), [])
        self.assertTrue(
            self.runtime.spool_session_stop(
                self.installation,
                self.runtime_config,
                blocked,
                blocked_key,
                now + 3,
            )
        )
        self.assertEqual(
            len(list(self.installation.spool.glob("*.json"))), 1
        )

    def test_spool_import_rejects_tampered_expiry_and_future_capture(
        self,
    ) -> None:
        now = 2_000_000_000.0
        event = replace(
            self.event("tampered-expiry"),
            observed_at_ns=int(now * 1_000_000_000),
        )
        key = self.runtime.session_key(self.installation, event.session_id)
        self.assertTrue(
            self.runtime.spool_session_stop(
                self.installation, self.runtime_config, event, key, now
            )
        )
        expiry_path = next(self.installation.spool.glob("*.json"))
        expiry_payload = json.loads(expiry_path.read_text(encoding="utf-8"))
        expiry_payload["expires_at"] = self.runtime.iso_utc(
            now + 365 * 86_400
        )
        expiry_path.write_text(
            json.dumps(expiry_payload), encoding="utf-8"
        )
        expiry_path.chmod(0o600)

        future = replace(
            self.event("future-capture"),
            observed_at_ns=int(
                (
                    now
                    + self.runtime.MAX_SPOOL_FUTURE_SKEW_SECONDS
                    + 1
                )
                * 1_000_000_000
            ),
        )
        future_key = self.runtime.session_key(
            self.installation, future.session_id
        )
        future_body = self.runtime.spooled_stop_payload(
            self.installation,
            future,
            future_key,
            self.runtime_config,
        )
        future_path = self.installation.spool / "future.json"
        future_path.write_text(
            json.dumps(future_body), encoding="utf-8"
        )
        future_path.chmod(0o600)

        connection = self.runtime.open_database(self.installation)
        result = self.runtime.import_spool(
            connection, self.installation, self.runtime_config, now
        )
        rows = connection.execute(
            "SELECT COUNT(*) FROM review_items"
        ).fetchone()[0]
        connection.close()
        self.assertEqual(result["spool_invalid_deleted"], 2)
        self.assertEqual(result["spool_imported"], 0)
        self.assertEqual(rows, 0)

    def test_signed_spool_uses_stricter_retention_after_config_change(
        self,
    ) -> None:
        now = 2_000_000_000.0
        shorter = self.runtime.Config(
            **{
                **self.runtime_config.__dict__,
                "pending_retention_days": 7,
            }
        )
        longer = self.runtime.Config(
            **{
                **self.runtime_config.__dict__,
                "pending_retention_days": 30,
            }
        )

        def write_signed(raw_session_id: str, name: str) -> None:
            event = replace(
                self.event(raw_session_id),
                observed_at_ns=int(now * 1_000_000_000),
            )
            key = self.runtime.session_key(
                self.installation, event.session_id
            )
            path = self.installation.spool / name
            path.write_text(
                json.dumps(
                    self.runtime.spooled_stop_payload(
                        self.installation,
                        event,
                        key,
                        self.runtime_config,
                    )
                ),
                encoding="utf-8",
            )
            path.chmod(0o600)

        connection = self.runtime.open_database(self.installation)
        write_signed("config-live", "config-live.json")
        live = self.runtime.import_spool(
            connection, self.installation, shorter, now + 1
        )
        self.assertEqual(live["spool_imported"], 1)
        self.assertEqual(live["spool_invalid_deleted"], 0)
        self.assertEqual(live["spool_expired"], 0)

        write_signed("config-shorter", "config-shorter.json")
        shortened = self.runtime.import_spool(
            connection,
            self.installation,
            shorter,
            now + shorter.pending_retention_days * 86_400,
        )
        self.assertEqual(shortened["spool_imported"], 0)
        self.assertEqual(shortened["spool_invalid_deleted"], 0)
        self.assertEqual(shortened["spool_expired"], 1)

        write_signed("config-longer", "config-longer.json")
        not_extended = self.runtime.import_spool(
            connection,
            self.installation,
            longer,
            now + self.runtime_config.pending_retention_days * 86_400,
        )
        connection.close()
        self.assertEqual(not_extended["spool_imported"], 0)
        self.assertEqual(not_extended["spool_invalid_deleted"], 0)
        self.assertEqual(not_extended["spool_expired"], 1)

    def test_small_future_capture_cannot_extend_database_ttls(self) -> None:
        now = 2_000_000_000.0
        future = replace(
            self.event("small-future-capture"),
            observed_at_ns=int(
                (
                    now
                    + self.runtime.MAX_SPOOL_FUTURE_SKEW_SECONDS
                    - 1
                )
                * 1_000_000_000
            ),
        )
        key = self.runtime.session_key(
            self.installation, future.session_id
        )
        payload = self.runtime.spooled_stop_payload(
            self.installation,
            future,
            key,
            self.runtime_config,
        )
        path = self.installation.spool / "small-future.json"
        path.write_text(json.dumps(payload), encoding="utf-8")
        path.chmod(0o600)

        connection = self.runtime.open_database(self.installation)
        result = self.runtime.import_spool(
            connection, self.installation, self.runtime_config, now
        )
        row = connection.execute(
            """
            SELECT pending_since,raw_metadata_expires_at,dedupe_expires_at
            FROM review_items WHERE session_key=?
            """,
            (key,),
        ).fetchone()
        connection.close()
        self.assertEqual(result["spool_imported"], 1)
        self.assertEqual(row["pending_since"], self.runtime.iso_utc(now))
        self.assertEqual(
            row["raw_metadata_expires_at"],
            self.runtime.iso_utc(
                now
                + self.runtime_config.raw_metadata_ttl_days * 86_400
            ),
        )
        self.assertEqual(
            row["dedupe_expires_at"],
            self.runtime.iso_utc(
                now + self.runtime_config.session_dedupe_days * 86_400
            ),
        )

    def test_spool_adapter_rejects_oversize_nonstring_and_noncanonical_fields(
        self,
    ) -> None:
        now = 2_000_000_000.0
        event = replace(
            self.event("bounded-adapter"),
            observed_at_ns=int(now * 1_000_000_000),
        )
        key = self.runtime.session_key(self.installation, event.session_id)
        base = self.runtime.spooled_stop_payload(
            self.installation, event, key, self.runtime_config
        )
        mutations = [
            {
                "raw_session_id": "s" * 513,
                "session_key": self.runtime.session_key(
                    self.installation, "s" * 513
                ),
            },
            {"diagnostic_turn_id": "t" * 513},
            {"cwd": "/" + "c" * 4_096},
            {"transcript_path": 7},
            {
                "transcript_path": str(
                    self.sessions / ".." / "outside.jsonl"
                )
            },
        ]
        for index, mutation in enumerate(mutations):
            payload = {**base, **mutation}
            body = {
                name: value
                for name, value in payload.items()
                if name != "payload_hmac"
            }
            payload["payload_hmac"] = self.runtime.spool_payload_hmac(
                self.installation, body
            )
            path = self.installation.spool / f"invalid-{index}.json"
            path.write_text(json.dumps(payload), encoding="utf-8")
            path.chmod(0o600)

        connection = self.runtime.open_database(self.installation)
        result = self.runtime.import_spool(
            connection, self.installation, self.runtime_config, now
        )
        count = connection.execute(
            "SELECT COUNT(*) FROM review_items"
        ).fetchone()[0]
        connection.close()
        self.assertEqual(result["spool_invalid_deleted"], len(mutations))
        self.assertEqual(count, 0)
        self.assertEqual(list(self.installation.spool.glob("*.json")), [])

    def test_spool_import_preserves_payload_on_transient_read_error(self) -> None:
        now = 2_000_000_000.0
        event = replace(
            self.event("transient-read"),
            observed_at_ns=int(now * 1_000_000_000),
        )
        key = self.runtime.session_key(self.installation, event.session_id)
        self.assertTrue(
            self.runtime.spool_session_stop(
                self.installation, self.runtime_config, event, key, now
            )
        )
        path = next(self.installation.spool.glob("*.json"))
        real_open = os.open

        def fail_target_open(
            target: object, flags: int, *args: object, **kwargs: object
        ) -> int:
            if (
                target == path.name
                and kwargs.get("dir_fd") is not None
            ):
                raise OSError(5, "transient read failure")
            return real_open(target, flags, *args, **kwargs)

        connection = self.runtime.open_database(self.installation)
        with mock.patch.object(
            self.runtime.os, "open", side_effect=fail_target_open
        ), self.assertRaises(OSError):
            self.runtime.import_spool(
                connection, self.installation, self.runtime_config, now
            )
        rows = connection.execute(
            "SELECT COUNT(*) FROM review_items"
        ).fetchone()[0]
        connection.close()
        self.assertTrue(path.exists())
        self.assertEqual(rows, 0)

    def test_spool_import_deletes_nonregular_and_pathological_json(
        self,
    ) -> None:
        directory = self.installation.spool / "directory.json"
        directory.mkdir(mode=0o700)
        nested = self.installation.spool / "nested.json"
        nested.write_text("[" * 2_000 + "]" * 2_000, encoding="utf-8")
        nested.chmod(0o600)
        overflow = self.installation.spool / "overflow-time.json"
        event = replace(
            self.event("overflow-time"),
            observed_at_ns=2_000_000_000_000_000_000,
        )
        key = self.runtime.session_key(self.installation, event.session_id)
        overflow_payload = self.runtime.spooled_stop_payload(
            self.installation, event, key, self.runtime_config
        )
        overflow_payload["created_at_ns"] = 10**27
        overflow_body = {
            name: value
            for name, value in overflow_payload.items()
            if name != "payload_hmac"
        }
        overflow_payload["payload_hmac"] = (
            self.runtime.spool_payload_hmac(
                self.installation, overflow_body
            )
        )
        overflow.write_text(
            json.dumps(overflow_payload),
            encoding="utf-8",
        )
        overflow.chmod(0o600)
        oversized_stat = self.installation.spool / "overflow-stat.json"
        oversized_payload = self.runtime.spooled_stop_payload(
            self.installation, event, key, self.runtime_config
        )
        oversized_payload["transcript_size"] = 10**30
        oversized_body = {
            name: value
            for name, value in oversized_payload.items()
            if name != "payload_hmac"
        }
        oversized_payload["payload_hmac"] = (
            self.runtime.spool_payload_hmac(
                self.installation, oversized_body
            )
        )
        oversized_stat.write_text(
            json.dumps(oversized_payload), encoding="utf-8"
        )
        oversized_stat.chmod(0o600)

        connection = self.runtime.open_database(self.installation)
        result = self.runtime.import_spool(
            connection,
            self.installation,
            self.runtime_config,
            2_000_000_000.0,
        )
        connection.close()
        self.assertEqual(result["spool_invalid_deleted"], 4)
        self.assertFalse(directory.exists())
        self.assertFalse(nested.exists())
        self.assertFalse(overflow.exists())
        self.assertFalse(oversized_stat.exists())

    def test_spool_import_rejects_hardlinked_transcript_without_reading(
        self,
    ) -> None:
        sensitive = self.sessions / "sensitive.json"
        sensitive.write_text('{"private":"transcript"}\n', encoding="utf-8")
        sensitive.chmod(0o600)
        linked = self.installation.spool / "hardlink.json"
        os.link(sensitive, linked)
        sensitive_inode = sensitive.stat().st_ino
        real_read = os.read

        def reject_sensitive_read(descriptor: int, size: int) -> bytes:
            if os.fstat(descriptor).st_ino == sensitive_inode:
                raise AssertionError("transcript content read")
            return real_read(descriptor, size)

        connection = self.runtime.open_database(self.installation)
        with mock.patch.object(
            self.runtime.os, "read", side_effect=reject_sensitive_read
        ):
            result = self.runtime.import_spool(
                connection,
                self.installation,
                self.runtime_config,
                2_000_000_000.0,
            )
        connection.close()
        self.assertEqual(result["spool_invalid_deleted"], 1)
        self.assertFalse(linked.exists())
        self.assertEqual(
            sensitive.read_text(encoding="utf-8"),
            '{"private":"transcript"}\n',
        )

    def test_swap_before_read_preserves_replacement_target(self) -> None:
        now = 2_000_000_000.0
        event = replace(
            self.event("swap-before-read"),
            observed_at_ns=int(now * 1_000_000_000),
        )
        key = self.runtime.session_key(self.installation, event.session_id)
        self.assertTrue(
            self.runtime.spool_session_stop(
                self.installation, self.runtime_config, event, key, now
            )
        )
        path = next(self.installation.spool.glob("*.json"))
        outside = self.base / "outside-private.json"
        outside.write_text('{"outside":"preserve"}\n', encoding="utf-8")
        outside.chmod(0o600)
        real_open = os.open
        swapped = False

        def swap_then_open(
            target: object, flags: int, *args: object, **kwargs: object
        ) -> int:
            nonlocal swapped
            if (
                not swapped
                and target == path.name
                and kwargs.get("dir_fd") is not None
            ):
                swapped = True
                path.unlink()
                path.symlink_to(outside)
            return real_open(target, flags, *args, **kwargs)

        connection = self.runtime.open_database(self.installation)
        with mock.patch.object(
            self.runtime.os, "open", side_effect=swap_then_open
        ):
            result = self.runtime.import_spool(
                connection, self.installation, self.runtime_config, now
            )
        rows = connection.execute(
            "SELECT COUNT(*) FROM review_items"
        ).fetchone()[0]
        connection.close()
        self.assertEqual(result["spool_preserved"], 1)
        self.assertEqual(rows, 0)
        self.assertTrue(path.is_symlink())
        self.assertEqual(
            outside.read_text(encoding="utf-8"),
            '{"outside":"preserve"}\n',
        )

    def test_swap_before_unlink_keeps_replacement_after_import(self) -> None:
        now = 2_000_000_000.0
        event = replace(
            self.event("swap-before-unlink"),
            observed_at_ns=int(now * 1_000_000_000),
        )
        key = self.runtime.session_key(self.installation, event.session_id)
        self.assertTrue(
            self.runtime.spool_session_stop(
                self.installation, self.runtime_config, event, key, now
            )
        )
        path = next(self.installation.spool.glob("*.json"))
        real_upsert = self.runtime.upsert_session

        def upsert_then_swap(*args: object, **kwargs: object) -> str:
            outcome = real_upsert(*args, **kwargs)
            path.unlink()
            path.write_text('{"replacement":true}\n', encoding="utf-8")
            path.chmod(0o600)
            return outcome

        connection = self.runtime.open_database(self.installation)
        with mock.patch.object(
            self.runtime,
            "upsert_session",
            side_effect=upsert_then_swap,
        ):
            result = self.runtime.import_spool(
                connection, self.installation, self.runtime_config, now
            )
        connection.close()
        self.assertEqual(result["spool_imported"], 1)
        self.assertEqual(result["spool_preserved"], 1)
        self.assertTrue(path.exists())
        self.assertEqual(
            path.read_text(encoding="utf-8"),
            '{"replacement":true}\n',
        )

    def test_spool_lock_swap_never_opens_or_touches_outside_target(
        self,
    ) -> None:
        now = 2_000_000_000.0
        event = replace(
            self.event("lock-swap"),
            observed_at_ns=int(now * 1_000_000_000),
        )
        key = self.runtime.session_key(self.installation, event.session_id)
        lock_path = self.installation.spool / ".lock"
        lock_path.write_bytes(b"")
        lock_path.chmod(0o600)
        outside = self.base / "outside-lock"
        outside.write_bytes(b"outside-lock-content")
        outside.chmod(0o600)
        before = outside.stat()
        real_open = os.open
        swapped = False

        def swap_lock_then_open(
            target: object, flags: int, *args: object, **kwargs: object
        ) -> int:
            nonlocal swapped
            if (
                not swapped
                and target == lock_path.name
                and kwargs.get("dir_fd") is not None
            ):
                swapped = True
                lock_path.unlink()
                lock_path.symlink_to(outside)
            return real_open(target, flags, *args, **kwargs)

        with mock.patch.object(
            self.runtime.os, "open", side_effect=swap_lock_then_open
        ), self.assertRaises(OSError):
            self.runtime.spool_session_stop(
                self.installation,
                self.runtime_config,
                event,
                key,
                now,
            )
        after = outside.stat()
        self.assertEqual(outside.read_bytes(), b"outside-lock-content")
        self.assertEqual(after.st_mtime_ns, before.st_mtime_ns)
        self.assertTrue(lock_path.is_symlink())

    def test_import_fsync_does_not_hold_spool_lock(self) -> None:
        now = 2_000_000_000.0
        first = replace(
            self.event("fsync-import"),
            observed_at_ns=int(now * 1_000_000_000),
        )
        first_key = self.runtime.session_key(
            self.installation, first.session_id
        )
        self.assertTrue(
            self.runtime.spool_session_stop(
                self.installation,
                self.runtime_config,
                first,
                first_key,
                now,
            )
        )
        second = replace(
            self.event("fsync-enqueue"),
            observed_at_ns=int((now + 1) * 1_000_000_000),
        )
        second_key = self.runtime.session_key(
            self.installation, second.session_id
        )
        entered = threading.Event()
        release = threading.Event()
        real_fsync = os.fsync
        spool_info = self.installation.spool.stat()
        errors: list[BaseException] = []
        import_results: list[dict[str, int]] = []
        hook_results: list[bool] = []

        def blocking_fsync(descriptor: int) -> None:
            info = os.fstat(descriptor)
            if (
                threading.current_thread().name == "spool-importer"
                and (info.st_dev, info.st_ino)
                == (spool_info.st_dev, spool_info.st_ino)
                and not entered.is_set()
            ):
                entered.set()
                self.assertTrue(release.wait(1))
            real_fsync(descriptor)

        def importing() -> None:
            connection = self.runtime.open_database(self.installation)
            try:
                import_results.append(
                    self.runtime.import_spool(
                        connection,
                        self.installation,
                        self.runtime_config,
                        now + 1,
                    )
                )
            except BaseException as error:
                errors.append(error)
            finally:
                connection.close()

        def enqueueing() -> None:
            try:
                hook_results.append(
                    self.runtime.spool_session_stop(
                        self.installation,
                        self.runtime_config,
                        second,
                        second_key,
                        now + 1,
                    )
                )
            except BaseException as error:
                errors.append(error)

        with mock.patch.object(
            self.runtime.os,
            "fsync",
            side_effect=blocking_fsync,
        ):
            importer = threading.Thread(
                target=importing, name="spool-importer"
            )
            importer.start()
            self.assertTrue(entered.wait(1))
            enqueuer = threading.Thread(
                target=enqueueing, name="spool-enqueuer"
            )
            enqueuer.start()
            enqueuer.join(timeout=0.25)
            self.assertFalse(enqueuer.is_alive())
            self.assertEqual(hook_results, [True])
            release.set()
            importer.join(timeout=2)

        self.assertFalse(importer.is_alive())
        self.assertEqual(errors, [])
        self.assertEqual(import_results[0]["spool_imported"], 1)

    def test_enqueue_and_import_coordinate_through_spool_lock(self) -> None:
        now = 2_000_000_000.0
        first = replace(
            self.event("importing-session"),
            observed_at_ns=int(now * 1_000_000_000),
        )
        first_key = self.runtime.session_key(
            self.installation, first.session_id
        )
        self.assertTrue(
            self.runtime.spool_session_stop(
                self.installation,
                self.runtime_config,
                first,
                first_key,
                now,
            )
        )
        first_path = next(self.installation.spool.glob("*.json"))
        first_inode = first_path.stat().st_ino
        second = replace(
            self.event("concurrent-session"),
            observed_at_ns=int((now + 1) * 1_000_000_000),
        )
        second_key = self.runtime.session_key(
            self.installation, second.session_id
        )
        entered = threading.Event()
        release = threading.Event()
        real_read = os.read

        def blocking_read(descriptor: int, size: int) -> bytes:
            if os.fstat(descriptor).st_ino == first_inode:
                entered.set()
                self.assertTrue(release.wait(1))
            return real_read(descriptor, size)

        import_results: list[dict[str, int]] = []
        hook_results: list[bool] = []
        errors: list[BaseException] = []

        def importing() -> None:
            connection = self.runtime.open_database(self.installation)
            try:
                import_results.append(
                    self.runtime.import_spool(
                        connection,
                        self.installation,
                        self.runtime_config,
                        now + 1,
                    )
                )
            except BaseException as error:
                errors.append(error)
            finally:
                connection.close()

        def enqueueing() -> None:
            try:
                hook_results.append(
                    self.runtime.spool_session_stop(
                        self.installation,
                        self.runtime_config,
                        second,
                        second_key,
                        now + 1,
                    )
                )
            except BaseException as error:
                errors.append(error)

        with mock.patch.object(
            self.runtime.os, "read", side_effect=blocking_read
        ):
            importer = threading.Thread(target=importing)
            importer.start()
            self.assertTrue(entered.wait(1))
            enqueuer = threading.Thread(target=enqueueing)
            enqueuer.start()
            time.sleep(0.01)
            self.assertTrue(enqueuer.is_alive())
            release.set()
            importer.join(timeout=2)
            enqueuer.join(timeout=2)

        self.assertFalse(importer.is_alive())
        self.assertFalse(enqueuer.is_alive())
        self.assertEqual(errors, [])
        self.assertEqual(import_results[0]["spool_imported"], 1)
        self.assertEqual(hook_results, [True])
        self.assertEqual(
            len(list(self.installation.spool.glob("*.json"))), 1
        )

    def test_reverse_filename_order_preserves_oldest_ttls_and_newest_locator(
        self,
    ) -> None:
        now = 2_000_000_000.0
        old = replace(
            self.event("ordered-session"),
            observed_at_ns=int(now * 1_000_000_000),
        )
        replacement = self.sessions / "ordered-new.jsonl"
        replacement.write_text(
            '{"payload":{"role":"assistant"}}\n', encoding="utf-8"
        )
        new_payload = {
            "hook_event_name": "Stop",
            "session_id": old.session_id,
            "cwd": str(self.workspace),
            "transcript_path": str(replacement),
        }
        new = self.runtime.parse_session_stop(
            json.dumps(new_payload).encode(),
            self.installation,
            self.runtime_config,
        )
        assert new is not None
        new = replace(
            new, observed_at_ns=int((now + 1) * 1_000_000_000)
        )
        key = self.runtime.session_key(self.installation, old.session_id)
        for name, event in (("a-new.json", new), ("z-old.json", old)):
            payload = self.runtime.spooled_stop_payload(
                self.installation, event, key, self.runtime_config
            )
            path = self.installation.spool / name
            path.write_text(json.dumps(payload), encoding="utf-8")
            path.chmod(0o600)

        connection = self.runtime.open_database(self.installation)
        result = self.runtime.import_spool(
            connection, self.installation, self.runtime_config, now + 2
        )
        row = connection.execute(
            """
            SELECT transcript_path,last_stop_ns,first_stop_at,pending_since,
              raw_metadata_expires_at,dedupe_expires_at
            FROM review_items WHERE session_key=?
            """,
            (key,),
        ).fetchone()
        connection.close()
        self.assertEqual(result["spool_imported"], 1)
        self.assertEqual(result["spool_duplicates"], 1)
        self.assertEqual(row["transcript_path"], str(new.transcript_path))
        self.assertEqual(row["last_stop_ns"], new.observed_at_ns)
        self.assertEqual(row["first_stop_at"], self.runtime.iso_utc(now))
        self.assertEqual(row["pending_since"], self.runtime.iso_utc(now))
        self.assertEqual(
            row["raw_metadata_expires_at"],
            self.runtime.iso_utc(
                now
                + self.runtime_config.raw_metadata_ttl_days * 86_400
            ),
        )
        self.assertEqual(
            row["dedupe_expires_at"],
            self.runtime.iso_utc(
                now + self.runtime_config.session_dedupe_days * 86_400
            ),
        )

    def test_later_older_replay_tightens_ttls_without_regressing_locator(
        self,
    ) -> None:
        now = 2_000_000_000.0
        old = replace(
            self.event("later-old-session"),
            observed_at_ns=int(now * 1_000_000_000),
        )
        replacement = self.sessions / "later-new.jsonl"
        replacement.write_text(
            '{"payload":{"role":"assistant"}}\n', encoding="utf-8"
        )
        new_payload = {
            "hook_event_name": "Stop",
            "session_id": old.session_id,
            "cwd": str(self.workspace),
            "transcript_path": str(replacement),
        }
        new = self.runtime.parse_session_stop(
            json.dumps(new_payload).encode(),
            self.installation,
            self.runtime_config,
        )
        assert new is not None
        new = replace(
            new, observed_at_ns=int((now + 100) * 1_000_000_000)
        )
        key = self.runtime.session_key(self.installation, old.session_id)

        newer_path = self.installation.spool / "newer.json"
        newer_path.write_text(
            json.dumps(
                self.runtime.spooled_stop_payload(
                    self.installation, new, key, self.runtime_config
                )
            ),
            encoding="utf-8",
        )
        newer_path.chmod(0o600)
        connection = self.runtime.open_database(self.installation)
        self.runtime.import_spool(
            connection, self.installation, self.runtime_config, now + 101
        )

        older_path = self.installation.spool / "older.json"
        older_path.write_text(
            json.dumps(
                self.runtime.spooled_stop_payload(
                    self.installation, old, key, self.runtime_config
                )
            ),
            encoding="utf-8",
        )
        older_path.chmod(0o600)
        self.runtime.import_spool(
            connection, self.installation, self.runtime_config, now + 102
        )
        row = connection.execute(
            """
            SELECT transcript_path,last_stop_ns,first_stop_at,pending_since,
              raw_metadata_expires_at,dedupe_expires_at
            FROM review_items WHERE session_key=?
            """,
            (key,),
        ).fetchone()
        connection.close()
        self.assertEqual(row["transcript_path"], str(new.transcript_path))
        self.assertEqual(row["last_stop_ns"], new.observed_at_ns)
        self.assertEqual(row["first_stop_at"], self.runtime.iso_utc(now))
        self.assertEqual(row["pending_since"], self.runtime.iso_utc(now))
        self.assertEqual(
            row["raw_metadata_expires_at"],
            self.runtime.iso_utc(
                now
                + self.runtime_config.raw_metadata_ttl_days * 86_400
            ),
        )
        self.assertEqual(
            row["dedupe_expires_at"],
            self.runtime.iso_utc(
                now + self.runtime_config.session_dedupe_days * 86_400
            ),
        )

    def test_older_replay_tightens_expired_tombstone_without_resurrection(
        self,
    ) -> None:
        now = 2_000_000_000.0
        limited = self.runtime.Config(
            **{
                **self.runtime_config.__dict__,
                "pending_limit_sessions": 1,
            }
        )
        newer = replace(
            self.event("expired-replay"),
            observed_at_ns=int((now + 100) * 1_000_000_000),
        )
        key = self.runtime.session_key(
            self.installation, newer.session_id
        )
        connection = self.runtime.open_database(self.installation)
        self.runtime.upsert_session(
            connection, newer, key, limited, now + 100
        )
        blocker = replace(
            self.event("capacity-blocker"),
            observed_at_ns=int((now + 101) * 1_000_000_000),
        )
        self.runtime.upsert_session(
            connection,
            blocker,
            self.runtime.session_key(
                self.installation, blocker.session_id
            ),
            limited,
            now + 101,
        )

        older = replace(
            newer, observed_at_ns=int(now * 1_000_000_000)
        )
        path = self.installation.spool / "older-expired.json"
        path.write_text(
            json.dumps(
                self.runtime.spooled_stop_payload(
                    self.installation, older, key, limited
                )
            ),
            encoding="utf-8",
        )
        path.chmod(0o600)
        result = self.runtime.import_spool(
            connection, self.installation, limited, now + 102
        )
        row = connection.execute(
            """
            SELECT status,raw_session_id,diagnostic_turn_id,cwd,
              transcript_path,transcript_size,transcript_mtime_ns,
              transcript_device,transcript_inode,last_stop_ns,first_stop_at,
              raw_metadata_expires_at,dedupe_expires_at,raw_redacted_at
            FROM review_items WHERE session_key=?
            """,
            (key,),
        ).fetchone()
        connection.close()

        self.assertEqual(result["spool_duplicates"], 1)
        self.assertEqual(row["status"], "expired")
        for name in (
            "raw_session_id",
            "diagnostic_turn_id",
            "cwd",
            "transcript_path",
            "transcript_size",
            "transcript_mtime_ns",
            "transcript_device",
            "transcript_inode",
        ):
            self.assertIsNone(row[name])
        self.assertEqual(row["last_stop_ns"], newer.observed_at_ns)
        self.assertEqual(row["first_stop_at"], self.runtime.iso_utc(now))
        self.assertEqual(
            row["raw_metadata_expires_at"],
            self.runtime.iso_utc(
                now + 100 + limited.raw_metadata_ttl_days * 86_400
            ),
        )
        self.assertEqual(
            row["dedupe_expires_at"],
            self.runtime.iso_utc(
                now + limited.session_dedupe_days * 86_400
            ),
        )
        self.assertIsNotNone(row["raw_redacted_at"])

    def test_old_replay_does_not_lower_later_generation_pending_since(
        self,
    ) -> None:
        now = 2_000_000_000.0
        original = replace(
            self.event("later-generation"),
            observed_at_ns=int(now * 1_000_000_000),
        )
        key = self.runtime.session_key(
            self.installation, original.session_id
        )
        connection = self.runtime.open_database(self.installation)
        self.runtime.upsert_session(
            connection, original, key, self.runtime_config, now
        )
        self.runtime.claim_review_generation(
            connection, key, "generation-one", now + 1, self.runtime_config
        )
        connection.execute("BEGIN IMMEDIATE")
        self.runtime.complete_review_generation(
            connection,
            key,
            "generation-one",
            "reviewed",
            None,
            now + 2,
        )
        connection.commit()
        with self.transcript.open("ab") as stream:
            stream.write(b'{"payload":{"role":"assistant"}}\n')
        newer = self.runtime.parse_session_stop(
            json.dumps(
                {
                    "hook_event_name": "Stop",
                    "session_id": original.session_id,
                    "cwd": str(self.workspace),
                    "transcript_path": str(self.transcript),
                }
            ).encode(),
            self.installation,
            self.runtime_config,
        )
        assert newer is not None
        newer = replace(
            newer, observed_at_ns=int((now + 100) * 1_000_000_000)
        )
        self.runtime.upsert_session(
            connection, newer, key, self.runtime_config, now + 100
        )

        replay = replace(
            original, observed_at_ns=int((now - 10) * 1_000_000_000)
        )
        replay_path = self.installation.spool / "old-generation.json"
        replay_path.write_text(
            json.dumps(
                self.runtime.spooled_stop_payload(
                    self.installation,
                    replay,
                    key,
                    self.runtime_config,
                )
            ),
            encoding="utf-8",
        )
        replay_path.chmod(0o600)
        self.runtime.import_spool(
            connection, self.installation, self.runtime_config, now + 101
        )
        row = connection.execute(
            """
            SELECT generation,pending_since,last_stop_ns,first_stop_at
            FROM review_items WHERE session_key=?
            """,
            (key,),
        ).fetchone()
        connection.close()
        self.assertEqual(row["generation"], 2)
        self.assertEqual(
            row["pending_since"], self.runtime.iso_utc(now + 100)
        )
        self.assertEqual(row["last_stop_ns"], newer.observed_at_ns)
        self.assertEqual(
            row["first_stop_at"], self.runtime.iso_utc(now - 10)
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
        self.assertEqual(
            row["transcript_path"], str(second.transcript_path)
        )
        self.assertEqual(row["transcript_inode"], second.transcript_inode)
        self.assertEqual(row["observed_boundary"], second.transcript_size)
        self.assertEqual(
            row["last_stop_ns"], 2_000_000_000_250_000_002
        )
        self.assertEqual(row["binding_status"], "pending_epoch")

    def test_maintenance_expires_pending_and_spool_raw_data_then_dedupe_row(
        self,
    ) -> None:
        now = 2_000_000_000.0
        event = self.event()
        key = self.runtime.session_key(self.installation, event.session_id)
        connection = self.runtime.open_database(self.installation)
        self.runtime.upsert_session(
            connection, event, key, self.runtime_config, now
        )
        spooled = replace(
            self.event("spooled-session"),
            observed_at_ns=int(now * 1_000_000_000),
        )
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
            now + 14 * 86_400,
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
            now + 180 * 86_400,
        )
        remaining = connection.execute(
            "SELECT COUNT(*) FROM review_items WHERE session_key=?",
            (key,),
        ).fetchone()[0]
        connection.close()
        self.assertEqual(deleted["dedupe_deleted"], 1)
        self.assertEqual(remaining, 0)

    def test_dedupe_expiry_removes_session_evidence_but_keeps_aggregate(
        self,
    ) -> None:
        now = 2_000_000_000.0
        event = self.event("evidence-retention")
        key = self.runtime.session_key(self.installation, event.session_id)
        connection = self.runtime.open_database(self.installation)
        self.runtime.upsert_session(
            connection, event, key, self.runtime_config, now
        )
        review_item = connection.execute(
            """
            SELECT id,dedupe_expires_at
            FROM review_items WHERE session_key=?
            """,
            (key,),
        ).fetchone()
        review_item_id = int(review_item["id"])
        dedupe_expires_at = review_item["dedupe_expires_at"]
        candidate = connection.execute(
            """
            INSERT INTO candidates(
              fingerprint,target_identity,target_skill,target_path,
              problem_category,target_locator,proposal_intent,conflict_group,
              problem_summary,proposal_summary,validation_plan,risk_level,
              status,occurrence_count,first_seen_at,last_seen_at,updated_at
            ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
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
                4,
                self.runtime.iso_utc(now),
                self.runtime.iso_utc(now),
                self.runtime.iso_utc(now),
            ),
        )
        candidate_id = int(candidate.lastrowid)
        connection.execute(
            "INSERT INTO metadata(key,value) VALUES(?,?)",
            (
                self.runtime.candidate_session_link_key(
                    self.installation, key
                ),
                self.runtime.canonical_json_bytes(
                    self.runtime.candidate_session_link_value(
                        candidate_id, dedupe_expires_at
                    )
                ).decode("utf-8"),
            ),
        )
        connection.execute(
            """
            INSERT INTO candidate_evidence(
              candidate_id,review_item_id,session_key,generation,signal_type,
              source_kind,summary,created_at
            ) VALUES(?,?,?,?,?,?,?,?)
            """,
            (
                candidate_id,
                review_item_id,
                key,
                1,
                "verification_failure",
                "tool_output",
                "summary",
                self.runtime.iso_utc(now),
            ),
        )

        result = self.runtime.run_maintenance(
            connection,
            self.installation,
            self.runtime_config,
            now + self.runtime_config.session_dedupe_days * 86_400,
        )
        remaining_keys = int(
            connection.execute(
                """
                SELECT
                  (SELECT COUNT(*) FROM review_items WHERE session_key=?)
                  + (SELECT COUNT(*) FROM candidate_evidence
                     WHERE session_key=?)
                """,
                (key, key),
            ).fetchone()[0]
        )
        evidence_rows = int(
            connection.execute(
                "SELECT COUNT(*) FROM candidate_evidence"
            ).fetchone()[0]
        )
        candidate = connection.execute(
            "SELECT occurrence_count FROM candidates WHERE id=?",
            (candidate_id,),
        ).fetchone()
        aggregate = self.runtime.load_candidate_evidence_aggregate(
            connection, candidate_id
        )
        connection.close()

        self.assertEqual(result["dedupe_deleted"], 1)
        self.assertEqual(remaining_keys, 0)
        self.assertEqual(evidence_rows, 0)
        self.assertEqual(candidate["occurrence_count"], 4)
        self.assertEqual(
            aggregate["counts"],
            [
                {
                    "signal_type": "verification_failure",
                    "source_kind": "tool_output",
                    "count": 1,
                }
            ],
        )

    def test_maintenance_recovers_lease_and_redacts_reviewed_raw_metadata(
        self,
    ) -> None:
        now = 2_000_000_000.0
        event = self.event("reviewed-session")
        reviewed_key = self.runtime.session_key(
            self.installation, event.session_id
        )
        connection = self.runtime.open_database(self.installation)
        self.runtime.upsert_session(
            connection, event, reviewed_key, self.runtime_config, now
        )
        connection.execute(
            """
            UPDATE review_items
            SET status='reviewed',pending_since=NULL,reviewed_at=?
            WHERE session_key=?
            """,
            (self.runtime.iso_utc(now + 1), reviewed_key),
        )
        leased = self.event("leased-session")
        leased_key = self.runtime.session_key(
            self.installation, leased.session_id
        )
        self.runtime.upsert_session(
            connection, leased, leased_key, self.runtime_config, now
        )
        self.runtime.claim_review_generation(
            connection, leased_key, "maintenance-owner", now, self.runtime_config
        )

        status_connection = self.runtime.open_database(
            self.installation, read_only=True
        )
        before = self.runtime.queue_status(
            status_connection,
            self.installation,
            now + self.runtime_config.raw_metadata_ttl_days * 86_400,
        )
        status_connection.close()
        self.assertEqual(
            before["raw_metadata_cleanup"]["overdue_sessions"], 2
        )
        result = self.runtime.run_maintenance(
            connection,
            self.installation,
            self.runtime_config,
            now + self.runtime_config.raw_metadata_ttl_days * 86_400,
        )
        reviewed = connection.execute(
            """
            SELECT status,raw_session_id,transcript_path,raw_redacted_at
            FROM review_items WHERE session_key=?
            """,
            (reviewed_key,),
        ).fetchone()
        leased_row = connection.execute(
            """
            SELECT status,lease_owner,frozen_to
            FROM review_items WHERE session_key=?
            """,
            (leased_key,),
        ).fetchone()
        connection.close()
        self.assertEqual(result["leases_recovered"], 1)
        self.assertEqual(result["raw_redacted"], 1)
        self.assertEqual(reviewed["status"], "reviewed")
        self.assertIsNone(reviewed["raw_session_id"])
        self.assertIsNone(reviewed["transcript_path"])
        self.assertIsNotNone(reviewed["raw_redacted_at"])
        self.assertEqual(tuple(leased_row), ("expired", None, None))

    def test_raw_cleanup_is_set_based_above_sqlite_parameter_limits(
        self,
    ) -> None:
        now = 2_000_000_000.0
        connection = self.runtime.open_database(self.installation)
        connection.executemany(
            """
            INSERT INTO review_items(
              session_key,raw_session_id,status,last_stop_ns,first_stop_at,
              last_stop_at,reviewed_at,raw_metadata_expires_at,
              dedupe_expires_at
            ) VALUES(?,?,'reviewed',0,?,?,?,?,?)
            """,
            [
                (
                    f"bulk-{index}",
                    f"private-{index}",
                    self.runtime.iso_utc(now - 1),
                    self.runtime.iso_utc(now - 1),
                    self.runtime.iso_utc(now - 1),
                    self.runtime.iso_utc(now),
                    self.runtime.iso_utc(now + 180 * 86_400),
                )
                for index in range(1_100)
            ],
        )
        result = self.runtime.run_maintenance(
            connection,
            self.installation,
            self.runtime_config,
            now,
        )
        remaining_raw = connection.execute(
            """
            SELECT COUNT(*) FROM review_items
            WHERE raw_session_id IS NOT NULL OR raw_redacted_at IS NULL
            """
        ).fetchone()[0]
        connection.close()
        self.assertEqual(result["raw_redacted"], 1_100)
        self.assertEqual(remaining_raw, 0)

    def test_maintenance_rejects_caller_owned_review_transaction(self) -> None:
        now = 2_000_000_000.0
        event = replace(
            self.event("caller-owned-spool"),
            observed_at_ns=int(now * 1_000_000_000),
        )
        key = self.runtime.session_key(self.installation, event.session_id)
        self.assertTrue(
            self.runtime.spool_session_stop(
                self.installation, self.runtime_config, event, key, now
            )
        )
        spooled = next(self.installation.spool.glob("*.json"))
        connection = self.runtime.open_database(self.installation)
        connection.execute("BEGIN IMMEDIATE")
        connection.execute(
            "INSERT INTO metadata(key,value) VALUES('caller-owned','1')"
        )

        with self.assertRaisesRegex(ValueError, "active_transaction"):
            self.runtime.run_maintenance(
                connection,
                self.installation,
                self.runtime_config,
                now,
            )

        self.assertTrue(connection.in_transaction)
        self.assertTrue(spooled.exists())
        connection.rollback()
        marker = connection.execute(
            "SELECT value FROM metadata WHERE key='caller-owned'"
        ).fetchone()
        connection.close()
        self.assertIsNone(marker)

    def test_status_reports_sessions_generations_leases_spool_and_cleanup_read_only(
        self,
    ) -> None:
        now = 2_000_000_000.0
        capture = self.capture_installation()
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
        waiting = capture.spool / "waiting.json"
        waiting.write_text("{}\n", encoding="utf-8")
        waiting.chmod(0o600)
        overflow = capture.spool / "overflow.events"
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
            read_only, capture, now + 10
        )
        read_only.close()
        self.assertEqual(status["pending_sessions"], 1)
        self.assertEqual(status["pending_generations"], 1)
        self.assertEqual(status["generation_count_total"], 3)
        self.assertEqual(status["leases"], {"active": 0, "expired": 0})
        self.assertTrue(status["spool"]["available"])
        self.assertEqual(status["spool"]["files"], 1)
        self.assertEqual(status["spool"]["bytes"], waiting.stat().st_size)
        self.assertEqual(status["spool"]["overflow_total"], 32_767)
        self.assertTrue(status["spool"]["overflow_counter_saturated"])
        self.assertTrue(waiting.exists())

        captured: list[dict[str, object]] = []
        runtime = replace(
            self.runtime.load_review_runtime(),
            plugin_data=capture.spool.parent,
        )
        with mock.patch.object(
            self.runtime,
            "run_maintenance",
            side_effect=AssertionError("status mutation"),
        ), mock.patch.object(
            self.runtime,
            "import_spool",
            side_effect=AssertionError("status import"),
        ), mock.patch.object(
            self.runtime, "load_review_runtime", return_value=runtime
        ), mock.patch.object(
            self.runtime,
            "write_json_stdout",
            side_effect=captured.append,
        ):
            result = self.runtime.cmd_status(
                Namespace(
                    installation=str(self.installation_path),
                    plugin_data=str(capture.spool.parent),
                )
            )
        self.assertEqual(result, 0)
        self.assertEqual(captured[0]["pending_sessions"], 1)
        self.assertEqual(captured[0]["spool"]["files"], 1)
        self.assertTrue(captured[0]["spool"]["available"])
        self.assertTrue(waiting.exists())

    def test_status_reports_bounded_partial_inventory_when_saturated(
        self,
    ) -> None:
        for index in range(self.runtime.MAX_SPOOL_SCAN_ENTRIES):
            path = self.installation.spool / f"{index:03d}.json"
            path.write_text("{}\n", encoding="utf-8")
            path.chmod(0o600)
        before = sorted(
            path.name for path in self.installation.spool.iterdir()
        )
        connection = self.runtime.open_database(
            self.installation, read_only=True
        )
        status = self.runtime.queue_status(
            connection, self.installation, 2_000_000_000.0
        )
        connection.close()
        self.assertEqual(status["spool"]["files"], 200)
        self.assertTrue(status["spool"]["scan_saturated"])
        self.assertEqual(
            sorted(path.name for path in self.installation.spool.iterdir()),
            before,
        )

    def test_status_admits_full_payload_cap_with_known_sidecars(self) -> None:
        lock = self.installation.spool / ".lock"
        lock.write_bytes(b"")
        lock.chmod(0o600)
        overflow = self.installation.spool / "overflow.events"
        overflow.write_bytes(b"1\n")
        overflow.chmod(0o600)
        for index in range(200):
            path = self.installation.spool / f"{index:03d}.json"
            path.write_text("{}\n", encoding="utf-8")
            path.chmod(0o600)
        connection = self.runtime.open_database(
            self.installation, read_only=True
        )
        status = self.runtime.queue_status(
            connection, self.installation, 2_000_000_000.0
        )
        connection.close()
        self.assertEqual(status["spool"]["files"], 200)
        self.assertFalse(status["spool"]["scan_saturated"])

    def test_parser_exposes_exact_status_and_maintain_commands(self) -> None:
        plugin_data = (
            "/Users/igyeongseob/.codex/plugins/data/"
            "skill-evolver-skill-evolver-dev"
        )
        enqueue = self.runtime.build_parser().parse_args(
            [
                "enqueue-stop",
                "--installation",
                str(self.installation_path),
                "--plugin-data",
                plugin_data,
            ]
        )
        status = self.runtime.build_parser().parse_args(
            [
                "status",
                "--installation",
                str(self.installation_path),
                "--plugin-data",
                plugin_data,
            ]
        )
        maintain = self.runtime.build_parser().parse_args(
            [
                "maintain",
                "--installation",
                str(self.installation_path),
                "--plugin-data",
                plugin_data,
            ]
        )
        for command in ("enqueue-stop", "status", "maintain"):
            with self.subTest(command=command), self.assertRaises(SystemExit):
                self.runtime.build_parser().parse_args(
                    [command, "--installation", str(self.installation_path)]
                )
        self.assertEqual(enqueue.plugin_data, plugin_data)
        self.assertEqual(status.plugin_data, plugin_data)
        self.assertEqual(maintain.plugin_data, plugin_data)
        self.assertIs(enqueue.handler, self.runtime.cmd_enqueue_stop)
        self.assertIs(status.handler, self.runtime.cmd_status)
        self.assertIs(maintain.handler, self.runtime.cmd_maintain)


if __name__ == "__main__":
    unittest.main()
