from __future__ import annotations

import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

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

    def prepare_promotable_surface(self):
        workspace = Path(tempfile.mkdtemp(dir=self.root))
        sessions = workspace / "sessions"
        sessions.mkdir(mode=0o700)
        installation_path = self.runtime.initialize_probe(
            workspace / "probe", (sessions,), Path("/usr/bin/python3")
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
        mapping_path = installation.data_root / "reports" / "cli-observation.json"
        self.runtime.atomic_write_json(
            mapping_path,
            {
                "schema_version": 1,
                "surface": "cli",
                "observations": ["one.json", "two.json"],
            },
        )
        return installation, mapping_path

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

    def test_dynamic_dictionary_keys_are_redacted_from_pointer_reports(self) -> None:
        raw_key = "secret-id-/Users/example/private~path"
        report = self.runtime.discover_turn_structure(
            [
                {"payload": {raw_key: {"turn_id": "turn-target", "role": "user"}}},
                {"payload": {raw_key: {"turn_id": "turn-target", "role": "assistant"}}},
            ],
            "turn-target",
        )

        serialized = json.dumps(report)
        self.assertNotIn(raw_key, serialized)
        self.assertNotIn("/Users/", serialized)
        self.assertEqual(
            report["turn_id_pointer_paths"],
            ["/payload/_redacted_0/turn_id"],
        )
        self.assertEqual(
            report["provenance_pointer_paths"],
            ["/payload/_redacted_0/role"],
        )

    def test_blank_captured_jsonl_line_is_unsupported(self) -> None:
        transcript = self.root / "blank.jsonl"
        transcript.write_bytes(
            b'{"turn_id":"turn-target","role":"user"}\n\n'
            b'{"turn_id":"turn-target","role":"assistant"}\n'
        )
        info = transcript.stat()
        observation = {
            "event": {"turn_id": "turn-target", "transcript_path": str(transcript)},
            "transcript_stat": {
                "size": info.st_size,
                "mtime_ns": info.st_mtime_ns,
                "device": info.st_dev,
                "inode": info.st_ino,
            },
        }

        with self.assertRaisesRegex(ValueError, "unsupported_jsonl"):
            self.runtime.inspect_transcript_structure(observation, "cli")

    def test_non_private_or_symlinked_mapping_is_not_read(self) -> None:
        for kind in ("symlink", "wrong-mode"):
            with self.subTest(kind=kind):
                installation, mapping_path = self.prepare_promotable_surface()
                if kind == "symlink":
                    replacement = self.root / "valid-mapping.json"
                    replacement.write_bytes(mapping_path.read_bytes())
                    mapping_path.unlink()
                    mapping_path.symlink_to(replacement)
                else:
                    os.chmod(mapping_path, 0o644)
                output = self.root / f"mapping-{kind}.structure.json"
                original_read_text = Path.read_text

                def guarded_read_text(path, *args, **kwargs):
                    if path == mapping_path:
                        raise AssertionError("mapping target read")
                    return original_read_text(path, *args, **kwargs)

                with mock.patch.object(Path, "read_text", guarded_read_text):
                    report = self.runtime.promote_transcript_structure(
                        installation, "cli", output
                    )
                self.assertFalse(report["supported"])
                self.assertEqual(report["error_codes"], ["surface_observation_unavailable"])

    def test_invalid_mapped_observations_are_not_read(self) -> None:
        for kind in ("symlink", "directory", "wrong-mode"):
            with self.subTest(kind=kind):
                installation, _mapping_path = self.prepare_promotable_surface()
                observation_path = installation.data_root / "incoming" / "one.json"
                if kind == "symlink":
                    replacement = self.root / "valid-observation.json"
                    replacement.write_bytes(observation_path.read_bytes())
                    observation_path.unlink()
                    observation_path.symlink_to(replacement)
                elif kind == "directory":
                    observation_path.unlink()
                    observation_path.mkdir(mode=0o700)
                else:
                    os.chmod(observation_path, 0o644)
                output = self.root / f"observation-{kind}.structure.json"
                original_read_text = Path.read_text

                def guarded_read_text(path, *args, **kwargs):
                    if path == observation_path:
                        raise AssertionError("observation target read")
                    return original_read_text(path, *args, **kwargs)

                with mock.patch.object(Path, "read_text", guarded_read_text):
                    report = self.runtime.promote_transcript_structure(
                        installation, "cli", output
                    )
                self.assertFalse(report["supported"])
                self.assertEqual(report["error_codes"], ["surface_observation_unavailable"])

    def test_promotion_rejects_different_redacted_wrapper_layouts(self) -> None:
        workspace = Path(tempfile.mkdtemp(dir=self.root))
        sessions = workspace / "sessions"
        sessions.mkdir(mode=0o700)
        installation_path = self.runtime.initialize_probe(
            workspace / "probe", (sessions,), Path("/usr/bin/python3")
        )
        installation = self.runtime.load_installation(installation_path)
        raw_keys = ("private-wrapper-one", "private-wrapper-two")
        for index, raw_key in enumerate(raw_keys, start=1):
            transcript = sessions / f"session-{index}.jsonl"
            transcript.write_text(
                "\n".join(
                    json.dumps({"payload": {raw_key: {"turn_id": "turn-target", "role": role}}})
                    for role in ("user", "assistant")
                )
                + "\n",
                encoding="utf-8",
            )
            info = transcript.stat()
            self.runtime.atomic_write_json(
                installation.data_root / "incoming" / f"{index}.json",
                {
                    "installation_nonce": installation.nonce,
                    "event": {
                        "turn_id": "turn-target",
                        "transcript_path": str(transcript),
                    },
                    "transcript_stat": {
                        "size": info.st_size,
                        "mtime_ns": info.st_mtime_ns,
                        "device": info.st_dev,
                        "inode": info.st_ino,
                    },
                },
            )
        self.runtime.atomic_write_json(
            installation.data_root / "reports" / "cli-observation.json",
            {"schema_version": 1, "surface": "cli", "observations": ["1.json", "2.json"]},
        )

        report = self.runtime.promote_transcript_structure(
            installation, "cli", workspace / "layout.structure.json"
        )

        self.assertFalse(report["layouts_stable"])
        self.assertFalse(report["supported"])
        self.assertEqual(report["error_codes"], ["layout_unstable"])
        serialized = json.dumps(report)
        for raw_key in raw_keys:
            self.assertNotIn(raw_key, serialized)
        self.assertNotIn("hash", serialized)
        self.assertNotIn("digest", serialized)

    def test_promotion_requires_two_observations_with_stable_layout(self) -> None:
        installation, _mapping_path = self.prepare_promotable_surface()
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
