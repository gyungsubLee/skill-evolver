from __future__ import annotations

import argparse
import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from support import load_runtime, run_isolated


class AccessProbeV2Tests(unittest.TestCase):
    def setUp(self) -> None:
        self.runtime = load_runtime()
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.output_root = self.root.resolve()
        self.sessions = self.root / "sessions"
        self.sessions.mkdir(mode=0o700)
        self.transcript = self.sessions / "session.jsonl"
        self.transcript.write_text("body\n", encoding="utf-8")
        installation_path = self.runtime.initialize_probe(
            self.root / "probe", (self.sessions,), Path("/usr/bin/python3")
        )
        self.installation = self.runtime.load_installation(installation_path)

    def capture_two_stops(self) -> None:
        self.runtime.mark_session_surface_boundary(self.installation, "cli")
        for session in ("access-session-one", "access-session-two"):
            self.runtime.capture_session_stop(
                self.installation,
                json.dumps(
                    {
                        "hook_event_name": "Stop",
                        "session_id": session,
                        "transcript_path": str(self.transcript),
                        "cwd": str(self.root),
                    }
                ).encode(),
            )

    def test_default_access_reads_challenge_and_records_write_denial(self) -> None:
        self.runtime.arm_access_v2(self.installation, "cli")
        original_write = self.runtime.atomic_write_json

        def deny_global(path, payload, mode=0o600):
            if path.parent == self.installation.data_root / "reports":
                raise PermissionError("denied")
            return original_write(path, payload, mode)

        with mock.patch.object(
            self.runtime, "atomic_write_json", side_effect=deny_global
        ):
            result = self.runtime.run_default_access_v2(
                self.installation, "cli", self.output_root / "default.json"
            )
        self.assertEqual(
            result,
            {
                "schema_version": 2,
                "surface": "cli",
                "challenge_read": True,
                "global_write": False,
                "write_denied": True,
                "challenge_digest": mock.ANY,
                "error_codes": [],
            },
        )

    def test_default_access_reports_read_failure_separately(self) -> None:
        result = self.runtime.run_default_access_v2(
            self.installation, "cli", self.output_root / "default.json"
        )
        self.assertEqual(result["challenge_read"], False)
        self.assertFalse(result["global_write"])
        self.assertFalse(result["write_denied"])
        self.assertEqual(result["challenge_digest"], None)
        self.assertEqual(result["error_codes"], ["access_challenge_unavailable"])
        self.assertNotIn(str(self.installation.data_root), json.dumps(result))

    def test_explicit_access_writes_and_round_trips_current_challenge(self) -> None:
        armed = self.runtime.arm_access_v2(self.installation, "cli")
        result = self.runtime.run_explicit_access_v2(self.installation, "cli")
        self.assertEqual(
            result,
            {
                "schema_version": 2,
                "surface": "cli",
                "challenge_read": True,
                "global_write": True,
                "write_denied": False,
                "challenge_digest": armed["challenge_digest"],
                "error_codes": [],
            },
        )
        challenge = self.runtime.load_access_challenge_v2(self.installation, "cli")
        self.assertNotIn(str(challenge["challenge"]), json.dumps(result))
        self.assertNotIn(self.installation.nonce, json.dumps(result))

    def test_promotion_writes_the_exact_access_inventory(self) -> None:
        self.runtime.arm_access_v2(self.installation, "cli")
        default = self.output_root / "default.json"
        original_write = self.runtime.atomic_write_json

        def deny_global(path, payload, mode=0o600):
            if path.parent == self.installation.data_root / "reports":
                raise PermissionError("denied")
            return original_write(path, payload, mode)

        with mock.patch.object(
            self.runtime, "atomic_write_json", side_effect=deny_global
        ):
            self.runtime.run_default_access_v2(self.installation, "cli", default)
        self.runtime.run_explicit_access_v2(self.installation, "cli")
        self.capture_two_stops()
        report = self.runtime.promote_access_v2(
            self.installation, "cli", default, self.output_root / "access.json"
        )
        self.assertEqual(
            report,
            {
                "schema_version": 2,
                "surface": "cli",
                "hook_global_read": True,
                "hook_global_write": True,
                "skill_default_read": True,
                "skill_default_write": False,
                "skill_default_write_denied": True,
                "skill_explicit_read": True,
                "skill_explicit_write": True,
                "error_codes": [],
            },
        )

    def test_rearming_invalidates_prior_default_and_explicit_responses(self) -> None:
        self.runtime.arm_access_v2(self.installation, "cli")
        default = self.output_root / "default.json"
        self.runtime.run_default_access_v2(self.installation, "cli", default)
        self.runtime.run_explicit_access_v2(self.installation, "cli")
        self.runtime.arm_access_v2(self.installation, "cli")
        self.capture_two_stops()
        report = self.runtime.promote_access_v2(
            self.installation, "cli", default, self.output_root / "access.json"
        )
        self.assertFalse(report["skill_default_read"])
        self.assertFalse(report["skill_explicit_read"])
        self.assertEqual(report["error_codes"], ["access_evidence_unavailable"])

    def test_challenge_response_mismatches_fail_closed_and_stay_sanitized(self) -> None:
        self.runtime.arm_access_v2(self.installation, "cli")
        default = self.output_root / "default.json"
        self.runtime.run_default_access_v2(self.installation, "cli", default)
        self.runtime.run_explicit_access_v2(self.installation, "cli")
        self.capture_two_stops()
        challenge = self.installation.data_root / "reports" / "cli-v2-access-challenge.json"
        response = self.installation.data_root / "reports" / "cli-v2-explicit-response.json"
        cases = {
            "surface": (challenge, lambda value: value.update({"surface": "desktop"})),
            "schema": (challenge, lambda value: value.update({"schema_version": 1})),
            "nonce": (challenge, lambda value: value.update({"installation_nonce": "nonce-secret"})),
            "challenge": (challenge, lambda value: value.update({"challenge": "challenge-secret"})),
            "digest": (response, lambda value: value.update({"challenge_digest": "digest-secret"})),
        }
        for name, (path, tamper) in cases.items():
            with self.subTest(name=name):
                original = json.loads(path.read_text(encoding="utf-8"))
                tamper(original)
                self.runtime.atomic_write_json(path, original)
                report = self.runtime.promote_access_v2(
                    self.installation, "cli", default, self.output_root / f"{name}.json"
                )
                self.assertEqual(report["error_codes"], ["access_evidence_unavailable"])
                rendered = json.dumps(report)
                for secret in ("nonce-secret", "challenge-secret", "digest-secret", str(path)):
                    self.assertNotIn(secret, rendered)
                self.runtime.arm_access_v2(self.installation, "cli")
                self.runtime.run_default_access_v2(self.installation, "cli", default)
                self.runtime.run_explicit_access_v2(self.installation, "cli")

    def test_commands_register_and_promotion_requires_stop_evidence(self) -> None:
        parser = self.runtime.build_parser()
        commands = {
            "probe-v2-arm-access": parser.parse_args(
                ["probe-v2-arm-access", "--installation", "/installation.json", "--surface", "cli"]
            ),
            "probe-v2-default-access": parser.parse_args(
                [
                    "probe-v2-default-access", "--installation", "/installation.json",
                    "--surface", "cli", "--output", "/default.json",
                ]
            ),
            "probe-v2-explicit-access": parser.parse_args(
                ["probe-v2-explicit-access", "--installation", "/installation.json", "--surface", "cli"]
            ),
        }
        self.assertIs(commands["probe-v2-arm-access"].handler, self.runtime.cmd_probe_v2_arm_access)
        self.assertIs(commands["probe-v2-default-access"].handler, self.runtime.cmd_probe_v2_default_access)
        self.assertIs(commands["probe-v2-explicit-access"].handler, self.runtime.cmd_probe_v2_explicit_access)
        promoted = parser.parse_args(
            [
                "probe-v2-promote-access", "--installation", "/installation.json",
                "--surface", "cli", "--default-response", "/response.json", "--output", "/report.json",
            ]
        )
        self.assertIs(promoted.handler, self.runtime.cmd_probe_v2_promote_access)

    def test_access_commands_sanitize_invalid_installation_errors(self) -> None:
        with mock.patch.object(self.runtime, "write_json_stdout") as write_json:
            result = self.runtime.cmd_probe_v2_arm_access(
                argparse.Namespace(installation="/private-installation-secret", surface="cli")
            )
        self.assertEqual(result, 2)
        write_json.assert_called_once_with(
            {
                "schema_version": 2,
                "surface": "cli",
                "armed": False,
                "challenge_digest": None,
                "error_codes": ["access_evidence_unavailable"],
            }
        )
        self.assertNotIn("private-installation-secret", json.dumps(write_json.call_args.args[0]))

    def test_access_parsers_hide_invalid_surface_values(self) -> None:
        sentinel = "surface-sentinel-secret"
        commands = {
            "probe-v2-arm-access": ("--installation", "/installation-secret"),
            "probe-v2-default-access": (
                "--installation", "/installation-secret", "--output", "/output-secret",
            ),
            "probe-v2-explicit-access": ("--installation", "/installation-secret"),
            "probe-v2-promote-access": (
                "--installation", "/installation-secret", "--default-response", "/response-secret",
                "--output", "/output-secret",
            ),
        }
        for command, arguments in commands.items():
            with self.subTest(command=command):
                result = run_isolated(command, *arguments, "--surface", sentinel)
                output = (result.stdout + result.stderr).decode("utf-8")
                self.assertNotEqual(result.returncode, 0)
                self.assertIn("invalid_access_surface", output)
                self.assertNotIn(sentinel, output)
                self.assertNotIn("installation-secret", output)
                self.assertNotIn("output-secret", output)
                self.assertNotIn("response-secret", output)

    def test_promotion_rejects_missing_stop_evidence(self) -> None:
        self.runtime.arm_access_v2(self.installation, "cli")
        default = self.output_root / "default.json"
        original_write = self.runtime.atomic_write_json

        def deny_global(path, payload, mode=0o600):
            if path.parent == self.installation.data_root / "reports":
                raise PermissionError("denied")
            return original_write(path, payload, mode)

        with mock.patch.object(
            self.runtime, "atomic_write_json", side_effect=deny_global
        ):
            self.runtime.run_default_access_v2(self.installation, "cli", default)
        self.runtime.run_explicit_access_v2(self.installation, "cli")
        report = self.runtime.promote_access_v2(
            self.installation, "cli", default, self.output_root / "access.json"
        )
        self.assertEqual(report["error_codes"], ["access_evidence_unavailable"])


if __name__ == "__main__":
    unittest.main()
