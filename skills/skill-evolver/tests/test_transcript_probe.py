from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from support import TEST_ROOT, load_runtime


class TranscriptProbeTests(unittest.TestCase):
    def setUp(self) -> None:
        self.runtime = load_runtime()
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        source = TEST_ROOT / "fixtures" / "synthetic-transcript.jsonl"
        lines = source.read_bytes().splitlines(keepends=True)
        self.transcript = self.root / "session.jsonl"
        self.transcript.write_bytes(b"".join(lines[:4]))
        captured = self.transcript.stat()
        self.observation = {
            "schema_version": 1,
            "event": {
                "turn_id": "turn-target",
                "transcript_path": str(self.transcript),
            },
            "transcript_stat": {
                "size": captured.st_size,
                "mtime_ns": captured.st_mtime_ns,
                "device": captured.st_dev,
                "inode": captured.st_ino,
            },
        }
        with self.transcript.open("ab") as stream:
            stream.write(lines[4])

    def test_inspection_reads_only_captured_prefix(self) -> None:
        report = self.runtime.inspect_transcript_structure(self.observation, "cli")

        self.assertTrue(report["supported"])
        self.assertTrue(report["suffix_ignored"])
        self.assertFalse(report["read_past_boundary"])
        self.assertEqual(report["turn_occurrence_count"], 3)
        self.assertEqual(report["turn_record_span"], [1, 3])
        self.assertTrue(report["turn_record_span_contiguous"])
        self.assertEqual(report["provenance_values"], ["assistant", "tool", "user"])
        serialized = json.dumps(report)
        self.assertNotIn("turn-target", serialized)
        self.assertNotIn("target prompt", serialized)
        self.assertNotIn(str(self.root), serialized)

    def test_inode_change_fails_closed(self) -> None:
        replacement = self.root / "replacement.jsonl"
        replacement.write_text("{}\n", encoding="utf-8")
        replacement.replace(self.transcript)
        with self.assertRaisesRegex(ValueError, "transcript_changed"):
            self.runtime.inspect_transcript_structure(self.observation, "cli")

    def test_partial_captured_record_fails_closed(self) -> None:
        self.observation["transcript_stat"]["size"] -= 1
        with self.assertRaisesRegex(ValueError, "captured_prefix_partial_record"):
            self.runtime.inspect_transcript_structure(self.observation, "cli")

    def test_oversized_captured_prefix_fails_before_read(self) -> None:
        self.observation["transcript_stat"]["size"] = 2_097_153
        with self.assertRaisesRegex(ValueError, "oversized_transcript"):
            self.runtime.inspect_transcript_structure(self.observation, "cli")

    def test_unsupported_format_produces_sanitized_failure_report(self) -> None:
        broken = self.root / "broken.jsonl"
        broken.write_bytes(b"not-json\n")
        info = broken.stat()
        observation = {
            "schema_version": 1,
            "event": {"turn_id": "turn-target", "transcript_path": str(broken)},
            "transcript_stat": {
                "size": info.st_size,
                "mtime_ns": info.st_mtime_ns,
                "device": info.st_dev,
                "inode": info.st_ino,
            },
        }
        report = self.runtime.safe_inspect_transcript_structure(observation, "desktop")
        self.assertEqual(
            report,
            {
                "schema_version": 1,
                "surface": "desktop",
                "supported": False,
                "error_code": "unsupported_jsonl",
            },
        )

    def test_promotion_requires_two_observations_with_stable_layout(self) -> None:
        sessions = self.root / "sessions"
        sessions.mkdir(mode=0o700)
        installation_path = self.runtime.initialize_probe(
            self.root / "probe", (sessions,), Path("/usr/bin/python3")
        )
        installation = self.runtime.load_installation(installation_path)
        copied_transcript = sessions / "session.jsonl"
        copied_transcript.write_bytes(self.transcript.read_bytes())
        copied = copied_transcript.stat()
        observation = {
            **self.observation,
            "installation_nonce": installation.nonce,
            "event": {
                **self.observation["event"],
                "transcript_path": str(copied_transcript),
            },
            "transcript_stat": {
                "size": copied.st_size,
                "mtime_ns": copied.st_mtime_ns,
                "device": copied.st_dev,
                "inode": copied.st_ino,
            },
        }
        for name in ("one.json", "two.json"):
            self.runtime.atomic_write_json(
                installation.data_root / "incoming" / name,
                observation,
            )
        self.runtime.atomic_write_json(
            installation.data_root / "reports" / "cli-observation.json",
            {
                "schema_version": 1,
                "surface": "cli",
                "observations": ["one.json", "two.json"],
            },
        )
        report = self.runtime.promote_transcript_structure(
            installation,
            "cli",
            self.root / "transcript.structure.json",
        )
        self.assertTrue(report["supported"])
        self.assertEqual(report["observation_count"], 2)
        self.assertTrue(report["layouts_stable"])


if __name__ == "__main__":
    unittest.main()
