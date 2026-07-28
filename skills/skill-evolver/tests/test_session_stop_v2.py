from __future__ import annotations

import argparse
import json
import os
import stat
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from support import load_runtime, run_isolated


class SessionStopV2Tests(unittest.TestCase):
    def setUp(self) -> None:
        self.runtime = load_runtime()
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.sessions = self.root / "sessions"
        self.sessions.mkdir(mode=0o700)
        self.transcript = self.sessions / "session.jsonl"
        self.transcript.write_text("transcript-body-secret\n", encoding="utf-8")
        installation_path = self.runtime.initialize_probe(
            self.root / "probe", (self.sessions,), Path("/usr/bin/python3")
        )
        self.installation = self.runtime.load_installation(installation_path)
        self.payload = {
            "hook_event_name": "Stop",
            "session_id": "session-secret",
            "transcript_path": str(self.transcript),
            "cwd": str(self.root),
        }

    def capture(self, payload: dict[str, object] | None = None) -> dict[str, object]:
        observation = self.runtime.capture_session_stop(
            self.installation, json.dumps(payload or self.payload).encode()
        )
        return json.loads(observation.read_text(encoding="utf-8"))

    def test_v2_capture_accepts_missing_turn_and_stores_no_transcript_body(self) -> None:
        observation = self.runtime.capture_session_stop(
            self.installation, json.dumps(self.payload).encode()
        )
        stored = json.loads(observation.read_text(encoding="utf-8"))
        self.assertEqual(stored["schema_version"], 2)
        self.assertEqual(stored["event"]["session_id"], "session-secret")
        self.assertIsNone(stored["event"]["turn_id"])
        self.assertNotIn("transcript-body-secret", observation.read_text(encoding="utf-8"))
        self.assertEqual(stat.S_IMODE(observation.stat().st_mode), 0o600)

    def test_v2_capture_keeps_a_bounded_present_turn_id(self) -> None:
        stored = self.capture({**self.payload, "turn_id": "turn-secret"})
        self.assertEqual(stored["event"]["turn_id"], "turn-secret")

    def test_v2_capture_persists_allowlisted_errors_without_metadata_values(self) -> None:
        outside = self.root / "outside.jsonl"
        outside.write_text("outside-secret\n", encoding="utf-8")
        fifo = self.sessions / "blocked.fifo"
        os.mkfifo(fifo, 0o600)
        target = self.sessions / "target.jsonl"
        target.write_text("target-secret\n", encoding="utf-8")
        symlink = self.sessions / "linked.jsonl"
        symlink.symlink_to(target)
        cases = {
            "missing_session": ({key: value for key, value in self.payload.items() if key != "session_id"}, "invalid_session_id"),
            "missing_transcript": ({key: value for key, value in self.payload.items() if key != "transcript_path"}, "invalid_transcript_path"),
            "subagent": ({**self.payload, "hook_event_name": "SubagentStop"}, "not_stop_event"),
            "fifo": ({**self.payload, "transcript_path": str(fifo)}, "transcript_not_regular"),
            "symlink": ({**self.payload, "transcript_path": str(symlink)}, "transcript_symlink"),
            "outside": ({**self.payload, "transcript_path": str(outside)}, "transcript_outside_roots"),
        }
        for name, (payload, expected) in cases.items():
            with self.subTest(name=name):
                stored = self.capture(payload)
                self.assertEqual(stored["capture_error_code"], expected)
                self.assertIn(expected, self.runtime.SESSION_CAPTURE_ERROR_CODES)
                self.assertNotIn("event", stored)
                self.assertNotIn("outside-secret", json.dumps(stored))

    def test_v2_capture_records_wrong_owner_as_sanitized_error(self) -> None:
        original_fstat = self.runtime.os.fstat

        def foreign_owner(descriptor: int) -> os.stat_result:
            info = original_fstat(descriptor)
            return os.stat_result((
                info.st_mode, info.st_ino, info.st_dev, info.st_nlink,
                os.getuid() + 1, info.st_gid, info.st_size, info.st_atime,
                info.st_mtime, info.st_ctime,
            ))

        with mock.patch.object(self.runtime.os, "fstat", side_effect=foreign_owner):
            stored = self.capture()
        self.assertEqual(stored["capture_error_code"], "transcript_owner")
        self.assertNotIn("session-secret", json.dumps(stored))

    def test_v2_capture_persists_oversized_input_as_an_allowlisted_error(self) -> None:
        observation = self.runtime.capture_session_stop(
            self.installation, b"x" * 65_537
        )
        stored = json.loads(observation.read_text(encoding="utf-8"))
        self.assertEqual(stored["capture_error_code"], "hook_input_too_large")
        self.assertIn(stored["capture_error_code"], self.runtime.SESSION_CAPTURE_ERROR_CODES)
        self.assertNotIn("event", stored)

    def test_v2_stop_command_is_silent_for_valid_and_oversized_input(self) -> None:
        valid = run_isolated(
            "probe-v2-stop", "--installation", str(self.installation.data_root / "installation.json"),
            stdin=json.dumps(self.payload).encode(),
        )
        oversized = run_isolated(
            "probe-v2-stop", "--installation", str(self.installation.data_root / "installation.json"),
            stdin=b"x" * 65_537,
        )
        for result in (valid, oversized):
            self.assertEqual(result.returncode, 0)
            self.assertEqual(result.stdout, b"")
            self.assertEqual(result.stderr, b"")

    def test_v2_promotion_accepts_distinct_sessions_without_raw_ids(self) -> None:
        self.runtime.mark_session_surface_boundary(self.installation, "cli")
        self.capture()
        self.capture({**self.payload, "session_id": "session-secret-two"})
        report = self.runtime.promote_session_stop_v2(
            self.installation, "cli", self.root / "session-stop-cli.v2.structure.json"
        )
        self.assertEqual(report["schema_version"], 2)
        self.assertEqual(report["observation_count"], 2)
        self.assertTrue(report["distinct_sessions"])
        self.assertTrue(report["capture_supported"])
        self.assertTrue(report["turn_id_optional"])
        self.assertNotIn("session-secret", json.dumps(report))

    def test_v2_promotion_rejects_duplicate_sessions(self) -> None:
        self.runtime.mark_session_surface_boundary(self.installation, "cli")
        self.capture()
        self.capture()
        report = self.runtime.promote_session_stop_v2(
            self.installation, "cli", self.root / "session-stop-cli.v2.structure.json"
        )
        self.assertFalse(report["distinct_sessions"])
        self.assertFalse(report["capture_supported"])

    def test_v2_promotion_command_returns_two_for_an_incomplete_fixture(self) -> None:
        self.runtime.mark_session_surface_boundary(self.installation, "cli")
        args = argparse.Namespace(
            installation=str(self.installation.data_root / "installation.json"),
            surface="cli",
            output=str(self.root / "session-stop-cli.v2.structure.json"),
        )
        with mock.patch.object(self.runtime, "write_json_stdout"):
            self.assertEqual(self.runtime.cmd_probe_v2_promote_stop(args), 2)


if __name__ == "__main__":
    unittest.main()
