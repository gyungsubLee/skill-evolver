from __future__ import annotations

import json
import tempfile
import unittest
from argparse import Namespace
from pathlib import Path
from unittest import mock

from support import PLUGIN_ROOT, load_runtime, run_isolated


class SkeletonTests(unittest.TestCase):
    def test_manifest_and_hook_are_discoverable(self) -> None:
        manifest = json.loads(
            (PLUGIN_ROOT / ".codex-plugin" / "plugin.json").read_text(encoding="utf-8")
        )
        hooks = json.loads((PLUGIN_ROOT / "hooks" / "hooks.json").read_text(encoding="utf-8"))
        runtime = json.loads(
            (PLUGIN_ROOT / "skills" / "skill-evolver" / "references" / "runtime.json").read_text(
                encoding="utf-8"
            )
        )

        self.assertEqual(manifest["name"], "skill-evolver")
        self.assertEqual(manifest["version"], "0.0.2")
        self.assertEqual(manifest["skills"], "./skills/")
        self.assertEqual(manifest["hooks"], "./hooks/hooks.json")
        self.assertEqual(runtime["schema_version"], 1)
        self.assertEqual(runtime["version"], "0.0.2")
        self.assertEqual(
            runtime["installation"],
            "/Users/igyeongseob/.codex/skill-evolver-feasibility-v2/installation.json",
        )
        self.assertEqual(set(hooks["hooks"]), {"Stop"})
        self.assertEqual(len(hooks["hooks"]["Stop"]), 1)
        group = hooks["hooks"]["Stop"][0]
        self.assertEqual(set(group), {"hooks"})
        self.assertNotIn("matcher", group)
        self.assertEqual(len(group["hooks"]), 1)
        self.assertEqual(
            group["hooks"][0],
            {
                "type": "command",
                "command": (
                    "/usr/bin/python3 -I \"$PLUGIN_ROOT/skills/skill-evolver/scripts/"
                    "evolver.py\" probe-v2-stop --installation \"/Users/igyeongseob/"
                    ".codex/skill-evolver-feasibility-v2/installation.json\""
                ),
                "timeout": 2,
            },
        )
        self.assertNotIn("SubagentStop", hooks["hooks"])

    def test_skill_is_explicit_only(self) -> None:
        skill = (PLUGIN_ROOT / "skills" / "skill-evolver" / "SKILL.md").read_text(
            encoding="utf-8"
        )
        self.assertIn("only when the user explicitly names $skill-evolver", skill)
        self.assertIn("Never invoke after an ordinary task", skill)
        self.assertIn("status and list do not open transcripts", skill)
        self.assertIn("default preflight runs without elevation", skill)
        self.assertIn("must not write the v2 root by default", skill)
        self.assertNotIn("After every task", skill)

    def test_runtime_works_under_isolated_python(self) -> None:
        result = run_isolated("--version")
        self.assertEqual(result.returncode, 0, result.stderr.decode())
        self.assertEqual(result.stdout.decode().strip(), "skill-evolver feasibility 0.0.2")

    def test_v2_transcript_promotion_parser_accepts_only_its_contract(self) -> None:
        parser = load_runtime().build_parser()
        args = parser.parse_args(
            [
                "probe-v2-promote-transcript",
                "--installation", "/private/installation.json",
                "--surface", "cli",
                "--output", "/private/session-transcript-cli.v2.structure.json",
            ]
        )
        self.assertEqual(args.command, "probe-v2-promote-transcript")
        self.assertEqual(args.installation, "/private/installation.json")
        self.assertEqual(args.surface, "cli")
        self.assertEqual(args.output, "/private/session-transcript-cli.v2.structure.json")

    def test_v2_status_and_list_read_only_validated_observation_metadata(self) -> None:
        runtime = load_runtime()
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            sessions = root / "sessions"
            sessions.mkdir(mode=0o700)
            installation_path = runtime.initialize_probe(
                root / "probe", (sessions,), Path("/usr/bin/python3")
            )
            installation = runtime.load_installation(installation_path)
            observation = installation.data_root / "incoming-v2" / "1-2-deadbeef.json"
            runtime.atomic_write_json(observation, {"session_id": "raw-session-secret"})
            expected_status = {
                "status": "ready",
                "shared_nonce_present": True,
                "observation_count": 1,
                "latest_observation": observation.name,
            }
            expected_list = {"observations": [{"name": observation.name, "size": observation.stat().st_size}]}
            args = Namespace(installation=str(installation_path))
            with mock.patch.object(runtime, "read_bounded_private_json", side_effect=AssertionError("content read")), mock.patch.object(
                runtime, "read_exact_prefix", side_effect=AssertionError("transcript read")
            ):
                for handler, expected in (
                    (runtime.cmd_probe_v2_status, expected_status),
                    (runtime.cmd_probe_v2_list, expected_list),
                ):
                    captured: list[dict[str, object]] = []
                    with mock.patch.object(
                        runtime,
                        "write_json_stdout",
                        side_effect=captured.append,
                    ):
                        self.assertEqual(handler(args), 0)
                    self.assertEqual(captured, [expected])

    def test_scrub_removes_v2_raw_state_and_validates_it_before_any_deletion(self) -> None:
        runtime = load_runtime()
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            sessions = root / "sessions"
            sessions.mkdir(mode=0o700)
            installation_path = runtime.initialize_probe(
                root / "probe", (sessions,), Path("/usr/bin/python3")
            )
            installation = runtime.load_installation(installation_path)
            incoming = installation.data_root / "incoming"
            incoming_v2 = installation.data_root / "incoming-v2"
            reports = installation.data_root / "reports"
            raw = incoming / "raw.json"
            v2_raw = incoming_v2 / "1-2-deadbeef.json"
            v2_orphan = incoming_v2 / ".1-2-deadbeef.json.crash"
            removable = [
                raw,
                v2_raw,
                v2_orphan,
                reports / "cli-v2-session-boundary.json",
                reports / "cli-v2-access-challenge.json",
                reports / ".cli-v2-access-challenge.json.crash",
                reports / "cli-v2-default-response.json",
                reports / ".cli-v2-default-response.json.crash",
                reports / "cli-v2-explicit-response.json",
                reports / ".cli-v2-explicit-response.json.crash",
                reports / "cli-v2-access.lock",
            ]
            for path in removable:
                runtime.atomic_write_json(path, {"raw_id_or_path": "private-value"})
            preserved = [incoming_v2 / ".keep", reports / "gate-v2.json"]
            for path in preserved:
                runtime.atomic_write_json(path, {"keep": True})

            result = runtime.scrub_probe_raw(installation, "DELETE-FEASIBILITY-RAW")

            self.assertEqual(result, {"observations_deleted": 3, "ephemeral_reports_deleted": 8})
            self.assertTrue(all(not path.exists() for path in removable))
            self.assertTrue(all(path.exists() for path in preserved))

            runtime.atomic_write_json(raw, {"raw": True})
            invalid_v2 = incoming_v2 / "bad.json"
            invalid_v2.mkdir(mode=0o700)
            with self.assertRaisesRegex(ValueError, "scrub_target_not_regular"):
                runtime.scrub_probe_raw(installation, "DELETE-FEASIBILITY-RAW")
            self.assertTrue(raw.exists())
            self.assertTrue(invalid_v2.exists())

    def test_probe_stop_is_silent_and_fail_open(self) -> None:
        malformed = run_isolated("probe-stop", "--installation", "/missing/file", stdin=b"{")
        self.assertEqual(malformed.returncode, 0)
        self.assertEqual(malformed.stdout, b"")
        self.assertEqual(malformed.stderr, b"")


if __name__ == "__main__":
    unittest.main()
