from __future__ import annotations

import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from support import TEST_ROOT, load_runtime


class SessionTranscriptV2Tests(unittest.TestCase):
    def setUp(self) -> None:
        self.runtime = load_runtime()
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.sessions = self.root / "sessions"
        self.sessions.mkdir(mode=0o700)
        self.transcript = self.sessions / "session.jsonl"
        self.lines = (TEST_ROOT / "fixtures" / "synthetic-session-v2.jsonl").read_bytes().splitlines(keepends=True)
        self.transcript.write_bytes(b"".join(self.lines[:4]))
        installation_path = self.runtime.initialize_probe(
            self.root / "probe", (self.sessions,), Path("/usr/bin/python3")
        )
        self.installation = self.runtime.load_installation(installation_path)
        captured = self.transcript.stat()
        self.observation = {
            "schema_version": 2,
            "received_at_ns": 1,
            "installation_nonce": self.installation.nonce,
            "shape": self.runtime.summarize_session_hook_shape(
                {
                    "hook_event_name": "Stop",
                    "session_id": "session-one",
                    "transcript_path": str(self.transcript.resolve()),
                    "cwd": str(self.root),
                }
            ),
            "event": {
                "hook_event_name": "Stop",
                "session_id": "session-one",
                "turn_id": None,
                "transcript_path": str(self.transcript.resolve()),
                "cwd": str(self.root),
            },
            "transcript_stat": {
                "size": captured.st_size,
                "mtime_ns": captured.st_mtime_ns,
                "device": captured.st_dev,
                "inode": captured.st_ino,
                "regular": True,
                "owned_by_current_user": True,
            },
        }
        with self.transcript.open("ab") as stream:
            stream.write(self.lines[4])

    def inspect(self, observation: dict[str, object] | None = None) -> dict[str, object]:
        return self.runtime.inspect_session_structure_v2(
            observation or self.observation, "cli", self.installation
        )

    def test_inspection_reads_only_frozen_prefix_without_turn_data(self) -> None:
        report = self.inspect()

        self.assertTrue(report["supported"])
        self.assertEqual(report["format"], "jsonl")
        self.assertEqual(report["binding_mode"], "same_file_identity")
        self.assertFalse(report["read_past_boundary"])
        self.assertTrue(report["suffix_ignored"])
        self.assertEqual(report["provenance_values"], ["assistant", "tool", "user"])
        serialized = json.dumps(report)
        self.assertNotIn("turn", serialized)
        for secret in ("session-one", "private prompt", "private output", "private answer", str(self.root)):
            self.assertNotIn(secret, serialized)

    def test_session_pointers_and_provenance_layouts_are_sanitized(self) -> None:
        report = self.inspect()
        self.assertEqual(report["session_id_pointer_paths"], ["/payload/session_id"])
        self.assertEqual(
            report["provenance_pointer_paths"], ["/payload/role"]
        )

    def test_noncontiguous_turn_ids_do_not_affect_session_structure(self) -> None:
        body = b"".join(
            json.dumps(
                {"payload": {"session_id": "session-one", "turn_id": turn, "role": role}}
            ).encode() + b"\n"
            for turn, role in (("one", "user"), ("three", "assistant"))
        )
        self.transcript.write_bytes(body)
        info = self.transcript.stat()
        observation = {
            **self.observation,
            "transcript_stat": {
                **self.observation["transcript_stat"],
                "size": info.st_size,
                "mtime_ns": info.st_mtime_ns,
                "device": info.st_dev,
                "inode": info.st_ino,
            },
        }

        report = self.inspect(observation)

        self.assertTrue(report["supported"])
        self.assertNotIn("turn", json.dumps(report))

    def test_partial_blank_invalid_and_oversized_prefixes_fail_closed(self) -> None:
        cases: list[tuple[str, bytes | None, int]] = [
            ("partial", None, -1),
            ("blank", b'{"payload":{"session_id":"session-one","role":"user"}}\n\n', 0),
            ("invalid", b"not-json\n", 0),
            ("oversized", None, self.runtime.MAX_TRANSCRIPT_PROBE_BYTES + 1),
        ]
        for name, body, adjustment in cases:
            with self.subTest(name=name):
                if body is not None:
                    self.transcript.write_bytes(body)
                    info = self.transcript.stat()
                    observation = {**self.observation, "transcript_stat": {**self.observation["transcript_stat"], "size": info.st_size, "mtime_ns": info.st_mtime_ns, "device": info.st_dev, "inode": info.st_ino}}
                else:
                    observation = {**self.observation, "transcript_stat": {**self.observation["transcript_stat"], "size": self.observation["transcript_stat"]["size"] + adjustment}}
                with self.assertRaises(ValueError):
                    self.inspect(observation)

    def test_symlink_and_outside_root_fail_closed_without_leaking_paths(self) -> None:
        outside = self.root / "outside.jsonl"
        outside.write_bytes(self.transcript.read_bytes())
        linked = self.sessions / "linked.jsonl"
        linked.symlink_to(self.transcript)
        for path in (outside, linked):
            with self.subTest(path=path.name):
                observation = {**self.observation, "event": {**self.observation["event"], "transcript_path": str(path)}}
                report = self.runtime.safe_inspect_session_structure_v2(
                    observation, "cli", self.installation
                )
                self.assertFalse(report["supported"])
                self.assertEqual(report["error_code"], "session_transcript_unavailable")
                self.assertNotIn(str(path), json.dumps(report))

    def test_lexical_parent_escape_and_intermediate_symlink_fail_closed(self) -> None:
        outside = self.root / "outside"
        outside.mkdir(mode=0o700)
        escaped = outside / "session.jsonl"
        escaped.write_bytes(self.transcript.read_bytes())
        nested = self.sessions / "nested"
        nested.symlink_to(outside, target_is_directory=True)
        for path in (
            str(self.sessions / ".." / "outside" / "session.jsonl"),
            f"{self.sessions.resolve()}/./session.jsonl",
            f"{self.sessions.resolve()}//session.jsonl",
            nested / "session.jsonl",
        ):
            with self.subTest(path=str(path)):
                observation = {
                    **self.observation,
                    "event": {
                        **self.observation["event"],
                        "transcript_path": str(path),
                    },
                }
                report = self.runtime.safe_inspect_session_structure_v2(
                    observation, "cli", self.installation
                )
                self.assertFalse(report["supported"])
                self.assertEqual(report["error_code"], "session_transcript_unavailable")
                self.assertNotIn(str(path), json.dumps(report))

    def test_same_size_rewrite_after_capture_fails_closed(self) -> None:
        self.transcript.write_bytes(b"".join(self.lines[:4]))
        os.utime(
            self.transcript,
            ns=(
                self.transcript.stat().st_atime_ns,
                self.observation["transcript_stat"]["mtime_ns"] + 1,
            ),
        )

        with self.assertRaisesRegex(ValueError, "transcript_changed"):
            self.inspect()

    def test_changed_size_during_read_fails_closed(self) -> None:
        original_fstat = self.runtime.os.fstat
        calls = 0

        def shrinking(descriptor: int) -> os.stat_result:
            nonlocal calls
            calls += 1
            info = original_fstat(descriptor)
            if calls > 1:
                return os.stat_result((
                    info.st_mode, info.st_ino, info.st_dev, info.st_nlink,
                    info.st_uid, info.st_gid, 0, info.st_atime, info.st_mtime,
                    info.st_ctime,
                ))
            return info

        with mock.patch.object(self.runtime.os, "fstat", side_effect=shrinking):
            with self.assertRaisesRegex(ValueError, "transcript_changed"):
                self.inspect()

    def test_same_size_change_during_read_fails_closed(self) -> None:
        original_fstat = self.runtime.os.fstat
        original_read = self.runtime.os.read
        changed = False

        def changing_read(descriptor: int, size: int) -> bytes:
            nonlocal changed
            chunk = original_read(descriptor, size)
            changed = True
            return chunk

        def changed_stat(descriptor: int) -> os.stat_result:
            info = original_fstat(descriptor)
            if changed:
                return os.stat_result((
                    info.st_mode, info.st_ino, info.st_dev, info.st_nlink,
                    info.st_uid, info.st_gid, info.st_size, info.st_atime,
                    info.st_mtime + 1, info.st_ctime,
                ))
            return info

        with mock.patch.object(self.runtime.os, "read", side_effect=changing_read), mock.patch.object(
            self.runtime.os, "fstat", side_effect=changed_stat
        ):
            with self.assertRaisesRegex(ValueError, "transcript_changed"):
                self.inspect()

    def test_directory_swap_before_final_open_cannot_read_outside_file(self) -> None:
        nested = self.sessions / "nested"
        nested.mkdir(mode=0o700)
        target = nested / "session.jsonl"
        target.write_bytes(self.transcript.read_bytes())
        observed = {**self.observation, "event": {**self.observation["event"], "transcript_path": str(target.resolve())}}
        info = target.stat()
        observed["transcript_stat"] = {
            **observed["transcript_stat"], "size": info.st_size,
            "mtime_ns": info.st_mtime_ns, "device": info.st_dev, "inode": info.st_ino,
        }
        outside = self.root / "outside"
        outside.mkdir(mode=0o700)
        (outside / "session.jsonl").write_text("outside-secret\n", encoding="utf-8")
        original_open = self.runtime.os.open
        swapped = False

        def swap_before_final(name, flags, *args, **kwargs):
            nonlocal swapped
            if name == "session.jsonl" and "dir_fd" in kwargs and not swapped:
                swapped = True
                nested.rename(self.sessions / "moved")
                nested.symlink_to(outside, target_is_directory=True)
            return original_open(name, flags, *args, **kwargs)

        with mock.patch.object(self.runtime.os, "open", side_effect=swap_before_final):
            report = self.runtime.safe_inspect_session_structure_v2(
                observed, "cli", self.installation
            )
        self.assertNotIn("outside-secret", json.dumps(report))
        self.assertFalse(report["supported"])
        self.assertEqual(report["error_code"], "session_transcript_unavailable")

    def test_same_inode_rename_resolves_under_fixed_roots(self) -> None:
        renamed = self.sessions / "nested" / "renamed.jsonl"
        renamed.parent.mkdir(mode=0o700)
        self.transcript.replace(renamed)

        report = self.inspect()

        self.assertTrue(report["supported"])
        self.assertEqual(report["binding_mode"], "same_inode_lookup")
        self.assertFalse(report["epoch_reset"])

    def test_different_inode_replacement_needs_embedded_session_binding(self) -> None:
        replacement = self.sessions / "replacement.jsonl"
        replacement.write_bytes(self.transcript.read_bytes())
        replacement.replace(self.transcript)

        report = self.inspect()

        self.assertTrue(report["supported"])
        self.assertEqual(report["binding_mode"], "embedded_session_id")
        self.assertTrue(report["epoch_reset"])

    def test_different_inode_replacement_without_embedded_session_fails(self) -> None:
        replacement = self.sessions / "replacement.jsonl"
        replacement.write_text('{"payload":{"session_id":"other","role":"user"}}\n', encoding="utf-8")
        replacement.replace(self.transcript)

        with self.assertRaisesRegex(ValueError, "session_binding_unavailable"):
            self.inspect()

    def test_same_inode_lookup_respects_exact_entry_limit(self) -> None:
        renamed = self.sessions / "renamed.jsonl"
        self.transcript.replace(renamed)
        self.assertEqual(self.runtime.MAX_TRANSCRIPT_LOOKUP_ENTRIES, 4096)
        with self.assertRaisesRegex(ValueError, "session_binding_unavailable"):
            self.runtime.resolve_session_transcript(
                self.observation, self.installation, max_entries=0
            )

    def test_promotion_uses_exactly_two_distinct_session_observations(self) -> None:
        self.runtime.mark_session_surface_boundary(self.installation, "cli")
        for name in ("session-one", "session-two"):
            transcript = self.sessions / f"{name}.jsonl"
            transcript.write_text(
                "\n".join(
                    json.dumps({"payload": {"session_id": name, "role": role}})
                    for role in ("user", "assistant")
                ) + "\n",
                encoding="utf-8",
            )
            payload = {
                "hook_event_name": "Stop", "session_id": name,
                "transcript_path": str(transcript), "cwd": str(self.root),
            }
            self.runtime.capture_session_stop(self.installation, json.dumps(payload).encode())

        report = self.runtime.promote_session_transcript_v2(
            self.installation, "cli", self.root / "session-transcript-cli.v2.structure.json"
        )

        self.assertTrue(report["supported"])
        self.assertTrue(report["distinct_sessions"])
        self.assertTrue(report["layouts_stable"])
        self.assertNotIn("session-one", json.dumps(report))
        self.assertNotIn("session-two", json.dumps(report))

    def test_promotion_rejects_duplicate_raw_session_ids(self) -> None:
        self.runtime.mark_session_surface_boundary(self.installation, "cli")
        for index in range(2):
            payload = {
                "hook_event_name": "Stop",
                "session_id": "session-one",
                "transcript_path": str(self.transcript.resolve()),
                "cwd": str(self.root),
            }
            self.runtime.capture_session_stop(self.installation, json.dumps(payload).encode())

        report = self.runtime.promote_session_transcript_v2(
            self.installation,
            "cli",
            self.root / "duplicate-session-transcript-cli.v2.structure.json",
        )

        self.assertFalse(report["supported"])
        self.assertFalse(report["distinct_sessions"])
        self.assertEqual(report["error_codes"], ["session_binding_unavailable"])
        self.assertNotIn("session-one", json.dumps(report))


if __name__ == "__main__":
    unittest.main()
