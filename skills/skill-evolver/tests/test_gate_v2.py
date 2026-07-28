from __future__ import annotations

import argparse
import hashlib
import json
import os
import stat
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from support import PLUGIN_ROOT, load_runtime, run_isolated


V2_FIXTURE_NAMES = {
    "session-stop-cli.v2.structure.json",
    "session-stop-desktop.v2.structure.json",
    "session-transcript-cli.v2.structure.json",
    "session-transcript-desktop.v2.structure.json",
    "access-cli.v2.structure.json",
    "access-desktop.v2.structure.json",
}


def stop_fixture(surface: str) -> dict[str, object]:
    keys = ["cwd", "hook_event_name", "session_id", "transcript_path"]
    return {
        "schema_version": 2,
        "surface": surface,
        "observation_count": 2,
        "capture_supported": True,
        "distinct_sessions": True,
        "turn_id_optional": True,
        "hook_event_name": "Stop",
        "payload_shapes_stable": True,
        "payload_keys": keys,
        "field_types": {key: "str" for key in keys},
        "required_fields": {
            key: {"present": True, "type": "str", "valid": True}
            for key in keys
        },
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


def access_fixture(surface: str) -> dict[str, object]:
    return {
        "schema_version": 2,
        "surface": surface,
        "hook_global_read": True,
        "hook_global_write": True,
        "skill_default_read": True,
        "skill_default_write": False,
        "skill_default_write_denied": True,
        "skill_explicit_read": True,
        "skill_explicit_write": True,
        "error_codes": [],
    }


def transcript_fixture(surface: str) -> dict[str, object]:
    return {
        "schema_version": 2,
        "surface": surface,
        "observation_count": 2,
        "supported": True,
        "distinct_sessions": True,
        "layouts_stable": True,
        "format": "jsonl",
        "suffix_ignored": True,
        "read_past_boundary": False,
        "binding_modes": ["same_file_identity"],
        "epoch_reset": False,
        "session_id_pointer_paths": ["/payload/session_id"],
        "provenance_pointer_paths": ["/payload/role"],
        "provenance_values": ["assistant", "tool", "user"],
        "error_codes": [],
    }


class GateV2Tests(unittest.TestCase):
    def setUp(self) -> None:
        self.runtime = load_runtime()
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name).resolve()
        self.predecessor = PLUGIN_ROOT / "docs" / "feasibility-report.json"

    def write_fixtures(self, root: Path) -> None:
        root.mkdir(mode=0o700)
        values = {
            "session-stop-cli.v2.structure.json": stop_fixture("cli"),
            "session-stop-desktop.v2.structure.json": stop_fixture("desktop"),
            "session-transcript-cli.v2.structure.json": transcript_fixture("cli"),
            "session-transcript-desktop.v2.structure.json": transcript_fixture("desktop"),
            "access-cli.v2.structure.json": access_fixture("cli"),
            "access-desktop.v2.structure.json": access_fixture("desktop"),
        }
        for name, value in values.items():
            self.runtime.atomic_write_json(root / name, value)

    def write_report(self, fixtures: Path, output: Path | None = None) -> dict[str, object]:
        output = output or self.root / "output"
        return self.runtime.write_gate_report_v2(
            fixtures,
            self.predecessor,
            output / "feasibility-report-v2.json",
            output / "feasibility-report-v2.md",
        )

    def test_valid_inventory_passes_and_binds_the_predecessor_digest(self) -> None:
        fixtures = self.root / "fixtures"
        self.write_fixtures(fixtures)

        report = self.write_report(fixtures)

        self.assertEqual(report["schema_version"], 2)
        self.assertEqual(report["decision"], "PASS")
        self.assertEqual(report["predecessor"]["path"], "docs/feasibility-report.json")
        self.assertEqual(len(report["predecessor"]["sha256"]), 64)
        self.assertEqual(
            report["predecessor"]["sha256"],
            hashlib.sha256(self.predecessor.read_bytes()).hexdigest(),
        )
        self.assertTrue(all(report["checks"].values()))
        self.assertTrue((self.root / "output" / "feasibility-report-v2.json").is_file())
        self.assertTrue((self.root / "output" / "feasibility-report-v2.md").is_file())

    def test_valid_inventory_writes_both_reports_when_a_surface_fails(self) -> None:
        fixtures = self.root / "fixtures"
        self.write_fixtures(fixtures)
        failed = access_fixture("desktop")
        failed["skill_default_write"] = True
        self.runtime.atomic_write_json(fixtures / "access-desktop.v2.structure.json", failed)

        report = self.write_report(fixtures)

        self.assertEqual(report["decision"], "FAIL")
        self.assertFalse(report["checks"]["desktop_asymmetric_access"])
        self.assertTrue((self.root / "output" / "feasibility-report-v2.json").is_file())
        self.assertTrue((self.root / "output" / "feasibility-report-v2.md").is_file())

    def test_exact_validators_reject_bool_aliases_and_v2_contract_failures(self) -> None:
        stop = stop_fixture("cli")
        stop["observation_count"] = True
        self.assertFalse(self.runtime.session_stop_fixture_v2_passes(stop, "cli"))
        access = access_fixture("cli")
        access["skill_default_write_denied"] = 1
        self.assertFalse(self.runtime.access_fixture_v2_passes(access, "cli"))
        transcript = transcript_fixture("cli")
        transcript["binding_modes"] = ["untrusted-binding"]
        self.assertFalse(self.runtime.session_transcript_fixture_v2_passes(transcript, "cli"))

        bad_access = access_fixture("cli")
        bad_access["skill_default_write"] = True
        self.assertEqual(
            self.runtime.evaluate_feasibility_gate_v2(
                stop_fixture("cli"), stop_fixture("desktop"),
                transcript_fixture("cli"), transcript_fixture("desktop"),
                bad_access, access_fixture("desktop"), "a" * 64,
            )["decision"],
            "FAIL",
        )

    def test_validators_fail_closed_for_unhashable_values_and_dict_subclasses(self) -> None:
        stop = stop_fixture("cli")
        stop["payload_keys"] = [{}]
        self.assertFalse(self.runtime.session_stop_fixture_v2_passes(stop, "cli"))
        transcript = transcript_fixture("cli")
        transcript["binding_modes"] = [{}]
        self.assertFalse(self.runtime.session_transcript_fixture_v2_passes(transcript, "cli"))

        class FixtureDict(dict):
            pass

        self.assertFalse(
            self.runtime.access_fixture_v2_passes(FixtureDict(access_fixture("cli")), "cli")
        )

    def test_private_fixture_root_may_use_normal_read_only_repo_permissions(self) -> None:
        fixtures = self.root / "fixtures"
        self.write_fixtures(fixtures)
        fixtures.chmod(0o755)

        self.assertEqual(self.write_report(fixtures)["decision"], "PASS")
        bad_transcript = transcript_fixture("cli")
        bad_transcript["provenance_values"] = ["user"]
        self.assertEqual(
            self.runtime.evaluate_feasibility_gate_v2(
                stop_fixture("cli"), stop_fixture("desktop"),
                bad_transcript, transcript_fixture("desktop"),
                access_fixture("cli"), access_fixture("desktop"), "a" * 64,
            )["decision"],
            "FAIL",
        )

    def test_inventory_rejects_missing_extra_mislabeled_and_duplicate_aliases_before_output(self) -> None:
        for name in ("missing", "seventh", "unrelated-extra", "mislabeled", "duplicate"):
            with self.subTest(name=name):
                case = self.root / name
                fixtures = case / "fixtures"
                case.mkdir()
                self.write_fixtures(fixtures)
                if name == "missing":
                    (fixtures / "access-cli.v2.structure.json").unlink()
                elif name == "seventh":
                    self.runtime.atomic_write_json(fixtures / "seventh.v2.structure.json", {})
                elif name == "unrelated-extra":
                    self.runtime.atomic_write_json(fixtures / "attacker.json", {})
                elif name == "mislabeled":
                    self.runtime.atomic_write_json(
                        fixtures / "access-desktop.v2.structure.json", access_fixture("cli")
                    )
                else:
                    duplicate = fixtures / "access-copy.v2.structure.json"
                    os.link(fixtures / "access-cli.v2.structure.json", duplicate)
                output = case / "out"
                with self.assertRaises(ValueError):
                    self.write_report(fixtures, output)
                self.assertFalse(output.exists())

    def test_unsafe_fixture_forms_fail_closed_without_output(self) -> None:
        for name in ("non-object", "malformed", "symlink", "non-private", "oversized", "changed"):
            with self.subTest(name=name):
                case = self.root / name
                case.mkdir()
                fixtures = case / "fixtures"
                self.write_fixtures(fixtures)
                target = fixtures / "session-stop-cli.v2.structure.json"
                if name == "non-object":
                    target.write_text("[]", encoding="utf-8")
                    target.chmod(0o600)
                elif name == "malformed":
                    target.write_text("{", encoding="utf-8")
                    target.chmod(0o600)
                elif name == "symlink":
                    linked = case / "linked.json"
                    self.runtime.atomic_write_json(linked, stop_fixture("cli"))
                    target.unlink()
                    target.symlink_to(linked)
                elif name == "non-private":
                    target.chmod(0o644)
                elif name == "oversized":
                    target.write_bytes(b"x" * (self.runtime.MAX_GATE_FIXTURE_BYTES + 1))
                    target.chmod(0o600)
                else:
                    original_fstat = self.runtime.os.fstat
                    calls = 0

                    def changed_fstat(descriptor: int):
                        nonlocal calls
                        calls += 1
                        info = original_fstat(descriptor)
                        if calls > 1:
                            return os.stat_result((
                                info.st_mode, info.st_ino, info.st_dev, info.st_nlink,
                                info.st_uid, info.st_gid, info.st_size, info.st_atime,
                                info.st_mtime + 1, info.st_ctime,
                            ))
                        return info

                    with mock.patch.object(self.runtime.os, "fstat", side_effect=changed_fstat):
                        with self.assertRaises(ValueError):
                            self.write_report(fixtures, case / "out")
                    continue
                with self.assertRaises(ValueError):
                    self.write_report(fixtures, case / "out")
                self.assertFalse((case / "out").exists())

    def test_predecessor_is_private_regular_stable_and_digest_is_checked(self) -> None:
        fixtures = self.root / "fixtures"
        self.write_fixtures(fixtures)
        wrong = self.root / "docs" / "feasibility-report.json"
        wrong.parent.mkdir(mode=0o700)
        wrong.write_bytes(self.predecessor.read_bytes())
        wrong.chmod(0o600)
        with self.assertRaises(ValueError):
            self.runtime.write_gate_report_v2(
                fixtures, wrong, self.root / "out.json", self.root / "out.md"
            )
        original_fstat = self.runtime.os.fstat
        calls = 0

        def changed_fstat(descriptor: int):
            nonlocal calls
            calls += 1
            info = original_fstat(descriptor)
            if calls > 1:
                return os.stat_result((
                    info.st_mode, info.st_ino, info.st_dev, info.st_nlink,
                    info.st_uid, info.st_gid, info.st_size, info.st_atime,
                    info.st_mtime + 1, info.st_ctime,
                ))
            return info

        with mock.patch.object(self.runtime.os, "fstat", side_effect=changed_fstat):
            with self.assertRaises(ValueError):
                self.runtime._v2_predecessor_digest(self.predecessor)

    def test_predecessor_requires_the_canonical_artifact_and_its_declared_digest(self) -> None:
        fixtures = self.root / "fixtures"
        self.write_fixtures(fixtures)
        wrong = self.root / "docs" / "feasibility-report.json"
        wrong.parent.mkdir(mode=0o700)
        wrong.write_bytes(self.predecessor.read_bytes())
        wrong.chmod(0o600)
        with self.assertRaises(ValueError):
            self.runtime.write_gate_report_v2(
                fixtures, wrong, self.root / "out.json", self.root / "out.md"
            )
        with mock.patch.object(
            self.runtime,
            "v2_predecessor_binding",
            return_value=(self.predecessor, "0" * 64),
            create=True,
        ):
            with self.assertRaises(ValueError):
                self.write_report(fixtures)

    def test_external_hardlinks_to_fixtures_or_predecessor_fail_closed(self) -> None:
        fixtures = self.root / "fixtures"
        self.write_fixtures(fixtures)
        external_fixture = self.root / "external-fixture.json"
        os.link(fixtures / "access-cli.v2.structure.json", external_fixture)
        try:
            with self.assertRaises(ValueError):
                self.write_report(fixtures)
        finally:
            external_fixture.unlink()

        external_predecessor = self.root / "external-predecessor.json"
        os.link(self.predecessor, external_predecessor)
        try:
            with self.assertRaises(ValueError):
                self.write_report(fixtures)
        finally:
            external_predecessor.unlink()

    def test_pair_publication_restores_prior_outputs_when_markdown_publish_fails(self) -> None:
        self._assert_publication_failure_rolls_back("replace_markdown")

    def test_pair_publication_restores_prior_outputs_when_markdown_sync_fails(self) -> None:
        self._assert_publication_failure_rolls_back("sync_markdown")

    def _assert_publication_failure_rolls_back(self, point: str) -> None:
        for existing in (False, True):
            with self.subTest(point=point, existing=existing):
                case = self.root / f"{point}-{existing}"
                case.mkdir()
                fixtures = case / "fixtures"
                self.write_fixtures(fixtures)
                output = case / "output"
                output.mkdir(mode=0o700)
                json_path = output / "feasibility-report-v2.json"
                markdown_path = output / "feasibility-report-v2.md"
                old_json = b'{"old":true}\n'
                old_markdown = b"old markdown\n"
                if existing:
                    json_path.write_bytes(old_json)
                    json_path.chmod(0o600)
                    markdown_path.write_bytes(old_markdown)
                    markdown_path.chmod(0o640)
                if point == "replace_markdown":
                    original_replace = self.runtime.os.replace

                    def fail_markdown(source, destination):
                        if Path(destination) == markdown_path:
                            raise OSError("publish secret path")
                        return original_replace(source, destination)

                    patched = mock.patch.object(
                        self.runtime.os, "replace", side_effect=fail_markdown
                    )
                else:
                    original_fsync = self.runtime.fsync_directory
                    calls = 0

                    def fail_after_markdown(directory):
                        nonlocal calls
                        calls += 1
                        if calls == 3:
                            raise OSError("sync secret path")
                        return original_fsync(directory)

                    patched = mock.patch.object(
                        self.runtime, "fsync_directory", side_effect=fail_after_markdown
                    )
                with patched:
                    with self.assertRaises(OSError):
                        self.runtime.write_gate_report_v2(
                            fixtures, self.predecessor, json_path, markdown_path
                        )
                if existing:
                    self.assertEqual(json_path.read_bytes(), old_json)
                    self.assertEqual(markdown_path.read_bytes(), old_markdown)
                    self.assertEqual(stat.S_IMODE(json_path.stat().st_mode), 0o600)
                    self.assertEqual(stat.S_IMODE(markdown_path.stat().st_mode), 0o640)
                else:
                    self.assertFalse(json_path.exists())
                    self.assertFalse(markdown_path.exists())
                self.assertFalse(any(path.name.startswith(".") for path in output.iterdir()))

    def test_output_aliases_with_fixture_predecessor_or_each_other_are_refused(self) -> None:
        fixtures = self.root / "fixtures"
        self.write_fixtures(fixtures)
        for output_json, output_markdown in (
            (fixtures / "access-cli.v2.structure.json", self.root / "out.md"),
            (self.predecessor, self.root / "out.md"),
            (self.root / "same", self.root / "same"),
        ):
            with self.subTest(json=output_json.name):
                with self.assertRaises(ValueError):
                    self.runtime.write_gate_report_v2(
                        fixtures, self.predecessor, output_json, output_markdown
                    )

    def test_invalid_cli_input_is_sanitized_without_paths_or_exception_text(self) -> None:
        sentinel = "secret-path-and-exception"
        result = run_isolated(
            "probe-v2-gate", "--fixture-root", f"/{sentinel}",
            "--predecessor-json", f"/{sentinel}-predecessor",
            "--output-json", f"/{sentinel}-json",
            "--output-markdown", f"/{sentinel}-markdown",
        )
        self.assertEqual(result.returncode, 2)
        rendered = result.stdout.decode() + result.stderr.decode()
        self.assertIn("gate_inputs_invalid", rendered)
        self.assertNotIn(sentinel, rendered)
        self.assertNotIn("FileNotFoundError", rendered)

    def test_parser_wires_v2_gate_with_sanitized_argument_errors(self) -> None:
        parser = self.runtime.build_parser()
        parsed = parser.parse_args([
            "probe-v2-gate", "--fixture-root", "/fixtures",
            "--predecessor-json", "/docs/feasibility-report.json",
            "--output-json", "/report.json", "--output-markdown", "/report.md",
        ])
        self.assertIs(parsed.handler, self.runtime.cmd_probe_v2_gate)


if __name__ == "__main__":
    unittest.main()
