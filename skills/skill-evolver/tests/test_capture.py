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
