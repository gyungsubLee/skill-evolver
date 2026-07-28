from __future__ import annotations

import json
import unittest

from support import PLUGIN_ROOT, load_runtime, run_isolated


class SkeletonTests(unittest.TestCase):
    def test_manifest_and_hook_are_discoverable(self) -> None:
        manifest = json.loads(
            (PLUGIN_ROOT / ".codex-plugin" / "plugin.json").read_text(encoding="utf-8")
        )
        hooks = json.loads((PLUGIN_ROOT / "hooks" / "hooks.json").read_text(encoding="utf-8"))

        self.assertEqual(manifest["name"], "skill-evolver")
        self.assertEqual(manifest["version"], "0.0.2")
        self.assertEqual(manifest["skills"], "./skills/")
        self.assertEqual(manifest["hooks"], "./hooks/hooks.json")
        self.assertEqual(set(hooks["hooks"]), {"Stop"})
        group = hooks["hooks"]["Stop"][0]
        self.assertNotIn("matcher", group)
        self.assertEqual(group["hooks"][0]["type"], "command")
        self.assertEqual(group["hooks"][0]["timeout"], 2)
        self.assertIn("probe-v2-stop", group["hooks"][0]["command"])
        self.assertIn(
            "/Users/igyeongseob/.codex/skill-evolver-feasibility-v2/installation.json",
            group["hooks"][0]["command"],
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

    def test_probe_stop_is_silent_and_fail_open(self) -> None:
        malformed = run_isolated("probe-stop", "--installation", "/missing/file", stdin=b"{")
        self.assertEqual(malformed.returncode, 0)
        self.assertEqual(malformed.stdout, b"")
        self.assertEqual(malformed.stderr, b"")


if __name__ == "__main__":
    unittest.main()
