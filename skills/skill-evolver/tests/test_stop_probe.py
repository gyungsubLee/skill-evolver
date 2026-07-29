from __future__ import annotations

import argparse
import json
import os
import stat
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from support import PROBE_SCRIPT, load_probe_runtime, run_probe_isolated


class StopProbeTests(unittest.TestCase):
    def setUp(self) -> None:
        self.runtime = load_probe_runtime()
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.base = Path(self.temp.name)
        self.sessions = self.base / "sessions"
        self.sessions.mkdir(mode=0o700)
        self.transcript = self.sessions / "session.jsonl"
        self.transcript.write_text(
            '{"type":"message","turn_id":"turn-1","content":"transcript-body-secret"}\n',
            encoding="utf-8",
        )
        self.installation_path = self.runtime.initialize_probe(
            self.base / "probe", (self.sessions,), Path("/usr/bin/python3")
        )
        self.installation = self.runtime.load_installation(self.installation_path)
        self.payload = {
            "hook_event_name": "Stop",
            "session_id": "session-secret",
            "turn_id": "turn-secret",
            "transcript_path": str(self.transcript),
            "cwd": str(self.base),
            "model": "probe-model",
        }

    def test_capture_records_metadata_not_transcript_body(self) -> None:
        with mock.patch("socket.socket", side_effect=AssertionError("network forbidden")):
            observation = self.runtime.capture_stop(
                self.installation, json.dumps(self.payload).encode()
            )

        stored = json.loads(observation.read_text(encoding="utf-8"))
        self.assertEqual(stored["event"]["session_id"], "session-secret")
        self.assertEqual(stored["event"]["turn_id"], "turn-secret")
        self.assertEqual(stored["transcript_stat"]["size"], self.transcript.stat().st_size)
        self.assertNotIn("transcript-body-secret", observation.read_text(encoding="utf-8"))
        self.assertEqual(stat.S_IMODE(observation.stat().st_mode), 0o600)

    def test_invalid_event_is_persisted_as_bounded_failure(self) -> None:
        self.payload["hook_event_name"] = "SubagentStop"
        observation = self.runtime.capture_stop(
            self.installation, json.dumps(self.payload).encode()
        )
        stored = json.loads(observation.read_text(encoding="utf-8"))
        self.assertEqual(stored["capture_error_code"], "not_stop_event")
        self.assertNotIn("event", stored)
        self.assertFalse(stored["shape"]["required_fields"]["hook_event_name"]["valid"])

    def test_missing_turn_and_null_transcript_are_persisted_as_invalid_shape(self) -> None:
        self.payload.pop("turn_id")
        self.payload["transcript_path"] = None
        observation = self.runtime.capture_stop(
            self.installation, json.dumps(self.payload).encode()
        )
        stored = json.loads(observation.read_text(encoding="utf-8"))
        required = stored["shape"]["required_fields"]
        self.assertFalse(required["turn_id"]["valid"])
        self.assertFalse(required["transcript_path"]["valid"])
        self.assertEqual(stored["capture_error_code"], "invalid_turn_id")

    def test_wrong_required_field_type_is_persisted_as_invalid_shape(self) -> None:
        self.payload["session_id"] = 7
        observation = self.runtime.capture_stop(
            self.installation, json.dumps(self.payload).encode()
        )
        stored = json.loads(observation.read_text(encoding="utf-8"))
        field = stored["shape"]["required_fields"]["session_id"]
        self.assertEqual(field["type"], "int")
        self.assertFalse(field["valid"])
        self.assertEqual(stored["capture_error_code"], "invalid_session_id")

    def test_transcript_outside_fixed_roots_is_recorded_as_failure(self) -> None:
        outside = self.base / "outside.jsonl"
        outside.write_text("{}\n", encoding="utf-8")
        self.payload["transcript_path"] = str(outside)
        observation = self.runtime.capture_stop(
            self.installation, json.dumps(self.payload).encode()
        )
        stored = json.loads(observation.read_text(encoding="utf-8"))
        self.assertEqual(stored["capture_error_code"], "transcript_outside_roots")
        self.assertNotIn(str(outside), observation.read_text(encoding="utf-8"))

    def test_hook_command_is_silent_for_valid_and_oversized_input(self) -> None:
        valid = run_probe_isolated(
            "probe-stop",
            "--installation",
            str(self.installation_path),
            stdin=json.dumps(self.payload).encode(),
        )
        oversized = run_probe_isolated(
            "probe-stop",
            "--installation",
            str(self.installation_path),
            stdin=b"x" * 65_537,
        )
        for result in (valid, oversized):
            self.assertEqual(result.returncode, 0)
            self.assertEqual(result.stdout, b"")
            self.assertEqual(result.stderr, b"")

    def test_fifo_transcript_fails_open_without_blocking(self) -> None:
        fifo = self.sessions / "blocked.fifo"
        os.mkfifo(fifo, 0o600)
        self.payload["transcript_path"] = str(fifo)

        result = subprocess.run(
            [
                "/usr/bin/python3",
                "-I",
                str(PROBE_SCRIPT),
                "probe-stop",
                "--installation",
                str(self.installation_path),
            ],
            input=json.dumps(self.payload).encode(),
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            check=False,
            timeout=2,
        )

        self.assertEqual(result.returncode, 0)
        self.assertEqual(result.stdout, b"")
        self.assertEqual(result.stderr, b"")
        observation = self.runtime.observation_paths(self.installation)[-1]
        stored = json.loads(observation.read_text(encoding="utf-8"))
        self.assertEqual(stored["capture_error_code"], "transcript_not_regular")

    def test_promoted_fixture_contains_structure_only(self) -> None:
        self.runtime.mark_surface_boundary(self.installation, "cli")
        self.runtime.capture_stop(self.installation, json.dumps(self.payload).encode())
        second = {**self.payload, "turn_id": "turn-secret-2"}
        self.runtime.capture_stop(self.installation, json.dumps(second).encode())
        output = self.base / "stop.structure.json"
        report = self.runtime.promote_surface_stop(self.installation, "cli", output)
        serialized = output.read_text(encoding="utf-8")

        self.assertEqual(report["surface"], "cli")
        self.assertEqual(report["observation_count"], 2)
        self.assertTrue(report["capture_supported"])
        self.assertTrue(report["payload_shapes_stable"])
        self.assertTrue(report["distinct_turns"])
        self.assertTrue(report["required_fields"]["turn_id"]["valid"])
        self.assertTrue(report["transcript_stat"]["has_device"])
        self.assertTrue(report["transcript_stat"]["has_inode"])
        self.assertNotIn("session-secret", serialized)
        self.assertNotIn("turn-secret", serialized)
        self.assertNotIn(str(self.base), serialized)
        self.assertNotIn("probe-model", serialized)

    def test_duplicate_turn_delivery_is_not_independent(self) -> None:
        self.runtime.mark_surface_boundary(self.installation, "cli")
        self.runtime.capture_stop(self.installation, json.dumps(self.payload).encode())
        self.runtime.capture_stop(self.installation, json.dumps(self.payload).encode())
        report = self.runtime.promote_surface_stop(
            self.installation,
            "cli",
            self.base / "stop.structure.json",
        )
        self.assertFalse(report["distinct_turns"])
        self.assertFalse(report["capture_supported"])

    def test_surface_promotion_writes_failure_for_wrong_observation_count(self) -> None:
        self.runtime.mark_surface_boundary(self.installation, "cli")
        self.runtime.capture_stop(self.installation, json.dumps(self.payload).encode())
        output = self.base / "stop.structure.json"
        report = self.runtime.promote_surface_stop(
            self.installation,
            "cli",
            output,
        )
        self.assertFalse(report["capture_supported"])
        self.assertEqual(report["capture_error_codes"], ["surface_observation_count"])
        self.assertTrue(output.is_file())

    def test_surface_promotion_sanitizes_malformed_observations(self) -> None:
        self.runtime.mark_surface_boundary(self.installation, "cli")
        for name in ("malformed-one.json", "malformed-two.json"):
            self.runtime.atomic_write_json(
                self.installation.data_root / "incoming" / name,
                {"schema_version": 1},
            )
        output = self.base / "stop.structure.json"

        report = self.runtime.promote_surface_stop(self.installation, "cli", output)

        self.assertFalse(report["capture_supported"])
        self.assertEqual(
            report["capture_error_codes"],
            ["surface_observation_unavailable"],
        )
        self.assertTrue(output.is_file())
        args = argparse.Namespace(
            installation=str(self.installation_path),
            surface="cli",
            output=str(output),
        )
        with mock.patch.object(self.runtime, "write_json_stdout"):
            self.assertEqual(self.runtime.cmd_probe_promote_stop(args), 2)

    def test_promote_stop_command_returns_two_for_failure_fixture(self) -> None:
        self.runtime.mark_surface_boundary(self.installation, "cli")
        args = argparse.Namespace(
            installation=str(self.installation_path),
            surface="cli",
            output=str(self.base / "stop.structure.json"),
        )
        with mock.patch.object(self.runtime, "write_json_stdout"):
            self.assertEqual(self.runtime.cmd_probe_promote_stop(args), 2)

    def test_skill_preflight_requires_current_challenge_and_round_trips_nonce(self) -> None:
        self.runtime.arm_skill_preflight(self.installation, "cli")
        result = self.runtime.run_skill_preflight(self.installation, "cli")
        report = self.runtime.promote_skill_preflight(
            self.installation,
            "cli",
            self.base / "access.structure.json",
        )
        self.assertEqual(result, {"surface": "cli", "read": True, "write": True})
        self.assertEqual(report["schema_version"], 1)
        self.assertEqual(
            {key: report[key] for key in ("surface", "read", "write")},
            result,
        )

    def test_rearming_invalidates_the_previous_skill_response(self) -> None:
        self.runtime.arm_skill_preflight(self.installation, "cli")
        self.runtime.run_skill_preflight(self.installation, "cli")
        self.runtime.arm_skill_preflight(self.installation, "cli")
        report = self.runtime.promote_skill_preflight(
            self.installation,
            "cli",
            self.base / "access.structure.json",
        )
        self.assertEqual(
            report,
            {
                "schema_version": 1,
                "surface": "cli",
                "read": False,
                "write": False,
            },
        )

    def test_preflight_promotion_rejects_invalid_challenge_contract(self) -> None:
        reports = self.installation.data_root / "reports"
        valid = {
            "schema_version": 1,
            "surface": "cli",
            "installation_nonce": self.installation.nonce,
            "challenge": "current-challenge",
        }
        missing_challenge = {
            key: value for key, value in valid.items() if key != "challenge"
        }
        cases = {
            "missing": (missing_challenge, missing_challenge),
            "non_string": ({**valid, "challenge": 7}, {**valid, "challenge": 7}),
            "empty": ({**valid, "challenge": ""}, {**valid, "challenge": ""}),
            "wrong_schema": (valid, {**valid, "schema_version": 2}),
        }
        for name, (challenge, response) in cases.items():
            with self.subTest(name=name):
                self.runtime.atomic_write_json(
                    reports / "cli-skill-challenge.json",
                    challenge,
                )
                self.runtime.atomic_write_json(
                    reports / "cli-skill-response.json",
                    response,
                )
                report = self.runtime.promote_skill_preflight(
                    self.installation,
                    "cli",
                    self.base / f"access-{name}.structure.json",
                )
                self.assertFalse(report["read"])
                self.assertFalse(report["write"])


if __name__ == "__main__":
    unittest.main()
