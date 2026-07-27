from __future__ import annotations

import argparse
import io
import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from support import PLUGIN_ROOT, load_runtime


class TtyInput(io.StringIO):
    def isatty(self) -> bool:
        return True


def stop_fixture(surface: str, valid: bool = True) -> dict[str, object]:
    required = {
        name: {"present": True, "type": "str", "valid": valid}
        for name in ("hook_event_name", "session_id", "turn_id", "cwd", "transcript_path")
    }
    return {
        "schema_version": 1,
        "surface": surface,
        "observation_count": 2,
        "capture_supported": True,
        "distinct_turns": True,
        "hook_event_name": "Stop",
        "payload_shapes_stable": True,
        "payload_keys": sorted(required),
        "field_types": {name: "str" for name in required},
        "required_fields": required,
        "capture_error_codes": [],
        "transcript_stat": {
            "present": True,
            "regular": True,
            "owned_by_current_user": True,
            "size_positive": True,
            "has_mtime_ns": True,
            "has_device": True,
            "has_inode": True,
        },
        "shared_nonce_match": True,
    }


def access_fixture(surface: str, passed: bool = True) -> dict[str, object]:
    return {
        "schema_version": 1,
        "surface": surface,
        "read": passed,
        "write": passed,
    }


def transcript_fixture(surface: str, supported: bool = True) -> dict[str, object]:
    if not supported:
        return {
            "schema_version": 1,
            "surface": surface,
            "observation_count": 2,
            "supported": False,
            "layouts_stable": False,
            "error_codes": ["turn_id_not_found"],
        }
    return {
        "schema_version": 1,
        "surface": surface,
        "observation_count": 2,
        "supported": True,
        "layouts_stable": True,
        "format": "jsonl",
        "suffix_ignored": True,
        "read_past_boundary": False,
        "turn_occurrence_counts": [3, 3],
        "turn_record_spans_contiguous": True,
        "turn_id_pointer_paths": ["/payload/turn_id"],
        "provenance_pointer_paths": ["/payload/role"],
        "provenance_values": ["assistant", "tool", "user"],
        "error_codes": [],
    }


class GateTests(unittest.TestCase):
    def setUp(self) -> None:
        self.runtime = load_runtime()
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name).resolve()

    def write_fixtures(self, root: Path) -> None:
        root.mkdir()
        values = {
            "stop-cli.structure.json": stop_fixture("cli"),
            "stop-desktop.structure.json": stop_fixture("desktop"),
            "transcript-cli.structure.json": transcript_fixture("cli"),
            "transcript-desktop.structure.json": transcript_fixture("desktop"),
            "access-cli.structure.json": access_fixture("cli"),
            "access-desktop.structure.json": access_fixture("desktop"),
        }
        for name, value in values.items():
            self.runtime.atomic_write_json(root / name, value)

    def create_installation(self):
        sessions = self.root / "sessions"
        sessions.mkdir(mode=0o700)
        installation_path = self.runtime.initialize_probe(
            self.root / "probe", (sessions,), Path("/usr/bin/python3")
        )
        return installation_path, self.runtime.load_installation(installation_path)

    def test_gate_passes_only_when_both_surfaces_pass_every_check(self) -> None:
        report = self.runtime.evaluate_feasibility_gate(
            stop_fixture("cli"),
            stop_fixture("desktop"),
            transcript_fixture("cli"),
            transcript_fixture("desktop"),
            access_fixture("cli"),
            access_fixture("desktop"),
        )
        self.assertEqual(report["decision"], "PASS")
        self.assertTrue(all(report["checks"].values()))
        self.assertTrue(
            all(not values for values in report["schema_differences"].values())
        )
        markdown = self.runtime.render_gate_markdown(report)
        self.assertIn("CLI and Desktop", markdown)
        self.assertIn("CLI/Desktop schema differences", markdown)

    def test_skill_records_the_final_gate_boundary(self) -> None:
        skill = (
            PLUGIN_ROOT / "skills" / "skill-evolver" / "SKILL.md"
        ).read_text(encoding="utf-8")
        self.assertIn("## Gate boundary", skill)
        self.assertIn("A `PASS` report authorizes writing a separate Read-only MVP", skill)
        self.assertIn("A `FAIL` report requires a design amendment", skill)

    def test_gate_failure_names_the_failed_surface(self) -> None:
        report = self.runtime.evaluate_feasibility_gate(
            stop_fixture("cli"),
            stop_fixture("desktop"),
            transcript_fixture("cli"),
            transcript_fixture("desktop", supported=False),
            access_fixture("cli"),
            access_fixture("desktop"),
        )
        self.assertEqual(report["decision"], "FAIL")
        self.assertFalse(report["checks"]["desktop_transcript_supported"])
        self.assertEqual(
            report["next_action"],
            "amend_design_for_session_level_queue",
        )

    def test_gate_rejects_duplicated_or_mislabeled_surface_fixture(self) -> None:
        report = self.runtime.evaluate_feasibility_gate(
            stop_fixture("cli"),
            stop_fixture("cli"),
            transcript_fixture("cli"),
            transcript_fixture("cli"),
            access_fixture("cli"),
            access_fixture("cli"),
        )
        self.assertEqual(report["decision"], "FAIL")
        self.assertFalse(report["checks"]["desktop_stop_contract"])
        self.assertFalse(report["checks"]["desktop_transcript_supported"])
        self.assertFalse(report["checks"]["desktop_skill_data_root"])

    def test_gate_rejects_duplicate_turn_delivery(self) -> None:
        duplicate = stop_fixture("cli")
        duplicate["distinct_turns"] = False
        report = self.runtime.evaluate_feasibility_gate(
            duplicate,
            stop_fixture("desktop"),
            transcript_fixture("cli"),
            transcript_fixture("desktop"),
            access_fixture("cli"),
            access_fixture("desktop"),
        )
        self.assertEqual(report["decision"], "FAIL")
        self.assertFalse(report["checks"]["cli_stop_contract"])

    def test_validators_reject_incomplete_metadata_and_bool_integer_aliases(self) -> None:
        incomplete = stop_fixture("cli")
        incomplete["required_fields"]["turn_id"] = {"valid": True}
        self.assertFalse(self.runtime.stop_fixture_passes(incomplete, "cli"))

        bool_count = transcript_fixture("cli")
        bool_count["turn_occurrence_counts"] = [True, 3]
        self.assertFalse(self.runtime.transcript_fixture_passes(bool_count, "cli"))

        bool_schema = access_fixture("cli")
        bool_schema["schema_version"] = True
        self.assertFalse(self.runtime.access_fixture_passes(bool_schema, "cli"))

        no_suffix_boundary = transcript_fixture("cli")
        no_suffix_boundary["suffix_ignored"] = False
        self.assertFalse(
            self.runtime.transcript_fixture_passes(no_suffix_boundary, "cli")
        )

        unhashable_path = transcript_fixture("cli")
        unhashable_path["turn_id_pointer_paths"] = [{}]
        self.assertFalse(
            self.runtime.transcript_fixture_passes(unhashable_path, "cli")
        )

        extra_stop_field = stop_fixture("cli")
        extra_stop_field["raw_prompt"] = "private"
        self.assertFalse(
            self.runtime.stop_fixture_passes(extra_stop_field, "cli")
        )

        unsafe_type = stop_fixture("cli")
        unsafe_type["payload_keys"].append("model")
        unsafe_type["field_types"]["model"] = "Authorization"
        self.assertFalse(
            self.runtime.stop_fixture_passes(unsafe_type, "cli")
        )

    def test_write_gate_report_creates_both_outputs_and_exit_codes(self) -> None:
        fixtures = self.root / "fixtures"
        self.write_fixtures(fixtures)
        output_json = self.root / "report.json"
        output_markdown = self.root / "report.md"
        args = argparse.Namespace(
            fixture_root=str(fixtures),
            output_json=str(output_json),
            output_markdown=str(output_markdown),
        )
        with mock.patch.object(self.runtime, "write_json_stdout"):
            self.assertEqual(self.runtime.cmd_probe_gate(args), 0)
        self.assertEqual(json.loads(output_json.read_text())["decision"], "PASS")
        self.assertIn("Decision: **PASS**", output_markdown.read_text())

        self.runtime.atomic_write_json(
            fixtures / "access-desktop.structure.json",
            access_fixture("desktop", passed=False),
        )
        with mock.patch.object(self.runtime, "write_json_stdout"):
            self.assertEqual(self.runtime.cmd_probe_gate(args), 2)
        self.assertEqual(json.loads(output_json.read_text())["decision"], "FAIL")

    def test_parser_wires_gate_and_scrub_commands(self) -> None:
        parser = self.runtime.build_parser()
        gate = parser.parse_args(
            [
                "probe-gate",
                "--fixture-root",
                "/fixtures",
                "--output-json",
                "/report.json",
                "--output-markdown",
                "/report.md",
            ]
        )
        scrub = parser.parse_args(
            ["probe-scrub", "--installation", "/installation.json"]
        )
        self.assertIs(gate.handler, self.runtime.cmd_probe_gate)
        self.assertIs(scrub.handler, self.runtime.cmd_probe_scrub)

    def test_non_object_gate_fixture_fails_closed(self) -> None:
        fixtures = self.root / "fixtures"
        fixtures.mkdir()
        (fixtures / "stop-cli.structure.json").write_text("[]", encoding="utf-8")
        report = self.runtime.write_gate_report(
            fixtures,
            self.root / "report.json",
            self.root / "report.md",
        )
        self.assertEqual(report["decision"], "FAIL")
        self.assertEqual(report["next_action"], "amend_design_for_session_level_queue")

    def test_malformed_json_gate_fixture_fails_closed(self) -> None:
        fixtures = self.root / "fixtures"
        fixtures.mkdir()
        (fixtures / "stop-cli.structure.json").write_text("{", encoding="utf-8")
        report = self.runtime.write_gate_report(
            fixtures,
            self.root / "report.json",
            self.root / "report.md",
        )
        self.assertEqual(report["decision"], "FAIL")
        self.assertFalse(report["checks"]["gate_inputs_valid"])

    def test_symlink_gate_fixture_fails_closed(self) -> None:
        fixtures = self.root / "fixtures"
        self.write_fixtures(fixtures)
        linked = self.root / "linked-stop.json"
        self.runtime.atomic_write_json(linked, stop_fixture("cli"))
        target = fixtures / "stop-cli.structure.json"
        target.unlink()
        target.symlink_to(linked)

        report = self.runtime.write_gate_report(
            fixtures,
            self.root / "report.json",
            self.root / "report.md",
        )

        self.assertEqual(report["decision"], "FAIL")
        self.assertFalse(report["checks"]["gate_inputs_valid"])

    def test_deeply_nested_gate_fixture_fails_closed(self) -> None:
        fixtures = self.root / "fixtures"
        self.write_fixtures(fixtures)
        nested = '{"nested":' * 1_100 + "0" + "}" * 1_100
        (fixtures / "stop-cli.structure.json").write_text(
            nested,
            encoding="utf-8",
        )

        report = self.runtime.write_gate_report(
            fixtures,
            self.root / "report.json",
            self.root / "report.md",
        )

        self.assertEqual(report["decision"], "FAIL")
        self.assertFalse(report["checks"]["gate_inputs_valid"])

    def test_invalid_gate_fixture_files_fail_closed_without_blocking(self) -> None:
        for case in ("missing", "directory", "fifo", "oversized"):
            with self.subTest(case=case):
                case_root = self.root / case
                case_root.mkdir()
                fixtures = case_root / "fixtures"
                self.write_fixtures(fixtures)
                invalid = fixtures / "stop-cli.structure.json"
                invalid.unlink()
                if case == "directory":
                    invalid.mkdir()
                elif case == "fifo":
                    os.mkfifo(invalid)
                elif case == "oversized":
                    invalid.write_bytes(
                        b"x" * (self.runtime.MAX_GATE_FIXTURE_BYTES + 1)
                    )

                report = self.runtime.write_gate_report(
                    fixtures,
                    case_root / "report.json",
                    case_root / "report.md",
                )

                self.assertEqual(report["decision"], "FAIL")
                self.assertFalse(report["checks"]["gate_inputs_valid"])

    def test_gate_report_suppresses_private_strings_from_invalid_fixtures(self) -> None:
        fixtures = self.root / "fixtures"
        self.write_fixtures(fixtures)
        unsafe = stop_fixture("cli")
        unsafe["payload_keys"].append("Authorization")
        unsafe["field_types"]["Authorization"] = "str"
        self.runtime.atomic_write_json(fixtures / "stop-cli.structure.json", unsafe)
        output_json = self.root / "report.json"
        output_markdown = self.root / "report.md"

        report = self.runtime.write_gate_report(
            fixtures,
            output_json,
            output_markdown,
        )

        self.assertEqual(report["decision"], "FAIL")
        self.assertNotIn("Authorization", output_json.read_text(encoding="utf-8"))
        self.assertNotIn("Authorization", output_markdown.read_text(encoding="utf-8"))

    def test_gate_rejects_output_symlink_without_touching_target(self) -> None:
        fixtures = self.root / "fixtures"
        self.write_fixtures(fixtures)
        outside = self.root / "outside.json"
        outside.write_text('{"unchanged":true}\n', encoding="utf-8")
        output_json = self.root / "report.json"
        output_json.symlink_to(outside)

        with self.assertRaisesRegex(ValueError, "report_output_symlink"):
            self.runtime.write_gate_report(
                fixtures,
                output_json,
                self.root / "report.md",
            )

        self.assertTrue(output_json.is_symlink())
        self.assertEqual(
            outside.read_text(encoding="utf-8"),
            '{"unchanged":true}\n',
        )

    def test_gate_rejects_output_beneath_symlinked_ancestor(self) -> None:
        fixtures = self.root / "fixtures"
        self.write_fixtures(fixtures)
        outside = self.root / "outside"
        nested = outside / "nested"
        nested.mkdir(parents=True)
        linked = self.root / "linked"
        linked.symlink_to(outside, target_is_directory=True)

        with self.assertRaisesRegex(ValueError, "report_output_parent_symlink"):
            self.runtime.write_gate_report(
                fixtures,
                linked / "nested" / "report.json",
                self.root / "report.md",
            )

        self.assertEqual(list(nested.iterdir()), [])

    def test_gate_rejects_aliased_report_outputs(self) -> None:
        fixtures = self.root / "fixtures"
        self.write_fixtures(fixtures)
        output = self.root / "report"

        with self.assertRaisesRegex(ValueError, "report_output_alias"):
            self.runtime.write_gate_report(fixtures, output, output)

    def test_gate_rejects_case_variant_report_output_aliases(self) -> None:
        fixtures = self.root / "fixtures"
        self.write_fixtures(fixtures)

        with self.assertRaisesRegex(ValueError, "report_output_alias"):
            self.runtime.write_gate_report(
                fixtures,
                self.root / "Report",
                self.root / "report",
            )

        self.assertFalse((self.root / "Report").exists())
        self.assertFalse((self.root / "report").exists())

    def test_gate_rejects_case_variant_fixture_output_alias(self) -> None:
        fixtures = self.root / "fixtures"
        self.write_fixtures(fixtures)
        fixture = fixtures / "stop-cli.structure.json"
        original = fixture.read_bytes()

        with self.assertRaisesRegex(ValueError, "report_output_alias"):
            self.runtime.write_gate_report(
                fixtures,
                fixtures / "STOP-CLI.STRUCTURE.JSON",
                self.root / "report.md",
            )

        self.assertEqual(fixture.read_bytes(), original)
        self.assertFalse((self.root / "report.md").exists())

    def test_scrub_deletes_only_private_raw_observations(self) -> None:
        installation_path, installation = self.create_installation()
        raw = installation.data_root / "incoming" / "raw.json"
        mapping = installation.data_root / "reports" / "cli-observation.json"
        boundary = installation.data_root / "reports" / "cli-boundary.json"
        challenge = installation.data_root / "reports" / "cli-skill-challenge.json"
        response = installation.data_root / "reports" / "cli-skill-response.json"
        committed_report = installation.data_root / "reports" / "gate.json"
        self.runtime.atomic_write_json(raw, {"secret": "raw"})
        self.runtime.atomic_write_json(mapping, {"observation": "raw.json"})
        self.runtime.atomic_write_json(boundary, {"after": None})
        self.runtime.atomic_write_json(challenge, {"challenge": "private"})
        self.runtime.atomic_write_json(response, {"challenge": "private"})
        self.runtime.atomic_write_json(committed_report, {"decision": "PASS"})

        result = self.runtime.scrub_probe_raw(
            installation,
            "DELETE-FEASIBILITY-RAW",
        )

        self.assertEqual(
            result,
            {"observations_deleted": 1, "ephemeral_reports_deleted": 4},
        )
        self.assertFalse(raw.exists())
        self.assertFalse(mapping.exists())
        self.assertFalse(boundary.exists())
        self.assertFalse(challenge.exists())
        self.assertFalse(response.exists())
        self.assertTrue(committed_report.exists())
        self.assertTrue(installation_path.exists())

    def test_scrub_validates_every_target_before_deleting_any(self) -> None:
        _installation_path, installation = self.create_installation()
        raw = installation.data_root / "incoming" / "raw.json"
        valid_report = installation.data_root / "reports" / "cli-observation.json"
        invalid_report = installation.data_root / "reports" / "desktop-boundary.json"
        self.runtime.atomic_write_json(raw, {"secret": "raw"})
        self.runtime.atomic_write_json(valid_report, {"observation": "raw.json"})
        invalid_report.mkdir(mode=0o700)

        with self.assertRaisesRegex(ValueError, "scrub_target_not_regular"):
            self.runtime.scrub_probe_raw(
                installation,
                "DELETE-FEASIBILITY-RAW",
            )

        self.assertTrue(raw.exists())
        self.assertTrue(valid_report.exists())
        self.assertTrue(invalid_report.exists())

    def test_scrub_rejects_wrong_confirmation(self) -> None:
        _installation_path, installation = self.create_installation()
        with self.assertRaisesRegex(ValueError, "confirmation_mismatch"):
            self.runtime.scrub_probe_raw(installation, "DELETE")

    def test_scrub_revalidates_private_data_root(self) -> None:
        _installation_path, installation = self.create_installation()
        raw = installation.data_root / "incoming" / "raw.json"
        self.runtime.atomic_write_json(raw, {"secret": "raw"})
        installation.data_root.chmod(0o755)

        with self.assertRaisesRegex(ValueError, "data_root_permissions"):
            self.runtime.scrub_probe_raw(
                installation,
                "DELETE-FEASIBILITY-RAW",
            )

        self.assertTrue(raw.exists())

    def test_scrub_command_rejects_non_tty_stdin(self) -> None:
        installation_path, _installation = self.create_installation()
        args = argparse.Namespace(installation=str(installation_path))
        with mock.patch.object(self.runtime.sys, "stdin", io.StringIO()):
            with self.assertRaisesRegex(ValueError, "tty_required"):
                self.runtime.cmd_probe_scrub(args)

    def test_scrub_command_bounds_tty_confirmation_input(self) -> None:
        installation_path, installation = self.create_installation()
        args = argparse.Namespace(installation=str(installation_path))
        raw = installation.data_root / "incoming" / "raw.json"
        self.runtime.atomic_write_json(raw, {"secret": "raw"})
        typed = TtyInput("DELETE-FEASIBILITY-RAW-with-extra-input\n")
        with mock.patch.object(self.runtime.sys, "stdin", typed):
            with mock.patch.object(self.runtime.sys, "stdout", io.StringIO()):
                with self.assertRaisesRegex(ValueError, "confirmation_mismatch"):
                    self.runtime.cmd_probe_scrub(args)
        self.assertLess(typed.tell(), len(typed.getvalue()))
        self.assertTrue(raw.exists())


if __name__ == "__main__":
    unittest.main()
