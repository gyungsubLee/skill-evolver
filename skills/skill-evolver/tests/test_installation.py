from __future__ import annotations

import json
import os
import stat
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from support import load_probe_runtime


class InstallationTests(unittest.TestCase):
    def setUp(self) -> None:
        self.runtime = load_probe_runtime()
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name) / "probe"
        self.transcripts = Path(self.temp.name) / "sessions"
        self.transcripts.mkdir(mode=0o755)
        self.transcripts.chmod(0o755)

    def replace_transcript_roots(
        self, installation_path: Path, transcript_roots: tuple[Path, ...]
    ) -> None:
        payload = json.loads(installation_path.read_text(encoding="utf-8"))
        payload["transcript_roots"] = [str(path) for path in transcript_roots]
        installation_path.write_text(json.dumps(payload), encoding="utf-8")
        installation_path.chmod(0o600)

    def test_initialize_creates_private_canonical_installation(self) -> None:
        installation_path = self.runtime.initialize_probe(
            self.root, (self.transcripts,), Path("/usr/bin/python3")
        )
        installation = self.runtime.load_installation(installation_path)

        self.assertEqual(installation.data_root, self.root.resolve())
        self.assertEqual(installation.transcript_roots, (self.transcripts.resolve(),))
        self.assertEqual(installation.python, Path("/usr/bin/python3"))
        self.assertEqual(stat.S_IMODE(self.root.stat().st_mode), 0o700)
        self.assertEqual(stat.S_IMODE(self.transcripts.stat().st_mode), 0o755)
        self.assertEqual(stat.S_IMODE(installation_path.stat().st_mode), 0o600)
        self.assertEqual(stat.S_IMODE((self.root / "nonce.json").stat().st_mode), 0o600)
        self.assertTrue((self.root / "incoming").is_dir())
        self.assertTrue((self.root / "reports").is_dir())

    def test_initialize_rejects_existing_state_without_replacing_nonce(self) -> None:
        self.runtime.initialize_probe(
            self.root, (self.transcripts,), Path("/usr/bin/python3")
        )
        nonce_path = self.root / "nonce.json"
        original_nonce = nonce_path.read_bytes()

        with self.assertRaisesRegex(ValueError, "existing_installation"):
            self.runtime.initialize_probe(
                self.root, (self.transcripts,), Path("/usr/bin/python3")
            )

        self.assertEqual(nonce_path.read_bytes(), original_nonce)

    def test_load_ignores_environment_overrides(self) -> None:
        installation_path = self.runtime.initialize_probe(
            self.root, (self.transcripts,), Path("/usr/bin/python3")
        )
        with mock.patch.dict(
            os.environ,
            {
                "CODEX_HOME": "/attacker",
                "SKILL_EVOLVER_DATA": "/attacker",
                "PYTHONPATH": "/attacker",
            },
        ):
            installation = self.runtime.load_installation(installation_path)
        self.assertEqual(installation.data_root, self.root.resolve())

    def test_symlink_installation_is_rejected(self) -> None:
        installation_path = self.runtime.initialize_probe(
            self.root, (self.transcripts,), Path("/usr/bin/python3")
        )
        link = Path(self.temp.name) / "installation-link.json"
        link.symlink_to(installation_path)
        with self.assertRaisesRegex(ValueError, "installation_symlink"):
            self.runtime.load_installation(link)

    def test_copied_or_hardlinked_installation_locator_is_rejected(self) -> None:
        for kind in ("copy", "hardlink"):
            with self.subTest(kind=kind):
                root = Path(self.temp.name) / f"probe-{kind}"
                installation_path = self.runtime.initialize_probe(
                    root, (self.transcripts,), Path("/usr/bin/python3")
                )
                alternate = Path(self.temp.name) / f"{kind}-installation.json"
                if kind == "copy":
                    alternate.write_bytes(installation_path.read_bytes())
                    alternate.chmod(0o600)
                else:
                    os.link(installation_path, alternate)

                with self.assertRaisesRegex(ValueError, "installation_location"):
                    self.runtime.load_installation(alternate)

    def test_initialize_rejects_unsafe_transcript_roots_before_writes(self) -> None:
        target = Path(self.temp.name) / "transcript-target"
        target.mkdir(mode=0o700)
        symlink = Path(self.temp.name) / "transcript-link"
        symlink.symlink_to(target, target_is_directory=True)
        regular_file = Path(self.temp.name) / "transcript.jsonl"
        regular_file.write_text("{}\n", encoding="utf-8")
        world_writable = Path(self.temp.name) / "world-writable"
        world_writable.mkdir(mode=0o700)
        world_writable.chmod(0o777)
        cases = (
            ("symlink", symlink, "transcript_root_symlink"),
            ("file", regular_file, "transcript_root_not_directory"),
            ("world_writable", world_writable, "transcript_root_permissions"),
        )

        for name, transcript_root, error in cases:
            with self.subTest(name=name):
                root = Path(self.temp.name) / f"probe-{name}"
                with self.assertRaisesRegex(ValueError, error):
                    self.runtime.initialize_probe(
                        root, (transcript_root,), Path("/usr/bin/python3")
                    )
                self.assertFalse(root.exists())

    def test_initialize_rejects_wrong_owner_transcript_root_before_writes(self) -> None:
        transcript_root = Path(self.temp.name) / "foreign-sessions"
        transcript_root.mkdir(mode=0o700)
        canonical_transcript = transcript_root.resolve()
        real_stat = Path.stat

        def stat_with_wrong_owner(path: Path, *args: object, **kwargs: object):
            info = real_stat(path, *args, **kwargs)
            if path == canonical_transcript:
                return mock.Mock(st_mode=info.st_mode, st_uid=info.st_uid + 1)
            return info

        with mock.patch.object(Path, "stat", stat_with_wrong_owner):
            with self.assertRaisesRegex(ValueError, "transcript_root_owner"):
                self.runtime.initialize_probe(
                    self.root, (transcript_root,), Path("/usr/bin/python3")
                )
        self.assertFalse(self.root.exists())

    def test_load_rejects_unsafe_fixed_transcript_roots(self) -> None:
        target = Path(self.temp.name) / "fixed-transcript-target"
        target.mkdir(mode=0o700)
        symlink = Path(self.temp.name) / "fixed-transcript-link"
        symlink.symlink_to(target, target_is_directory=True)
        regular_file = Path(self.temp.name) / "fixed-transcript.jsonl"
        regular_file.write_text("{}\n", encoding="utf-8")
        world_writable = Path(self.temp.name) / "fixed-world-writable"
        world_writable.mkdir(mode=0o700)
        world_writable.chmod(0o777)
        cases = (
            ("symlink", symlink, "transcript_root_symlink"),
            ("file", regular_file, "transcript_root_not_directory"),
            ("world_writable", world_writable, "transcript_root_permissions"),
        )

        for name, transcript_root, error in cases:
            with self.subTest(name=name):
                root = Path(self.temp.name) / f"load-probe-{name}"
                installation_path = self.runtime.initialize_probe(
                    root, (self.transcripts,), Path("/usr/bin/python3")
                )
                self.replace_transcript_roots(
                    installation_path, (transcript_root,)
                )
                with self.assertRaisesRegex(ValueError, error):
                    self.runtime.load_installation(installation_path)

    def test_load_rejects_wrong_owner_fixed_transcript_root(self) -> None:
        transcript_root = Path(self.temp.name) / "fixed-foreign-sessions"
        transcript_root.mkdir(mode=0o700)
        installation_path = self.runtime.initialize_probe(
            self.root, (self.transcripts,), Path("/usr/bin/python3")
        )
        self.replace_transcript_roots(installation_path, (transcript_root,))
        canonical_transcript = transcript_root.resolve()
        real_stat = Path.stat

        def stat_with_wrong_owner(path: Path, *args: object, **kwargs: object):
            info = real_stat(path, *args, **kwargs)
            if path == canonical_transcript:
                return mock.Mock(st_mode=info.st_mode, st_uid=info.st_uid + 1)
            return info

        with mock.patch.object(Path, "stat", stat_with_wrong_owner):
            with self.assertRaisesRegex(ValueError, "transcript_root_owner"):
                self.runtime.load_installation(installation_path)

    def test_load_rejects_invalid_transcript_roots_schema(self) -> None:
        canonical_parent = Path(self.temp.name) / "canonical-parent"
        canonical_parent.mkdir(mode=0o700)
        canonical_transcript = canonical_parent / "sessions"
        canonical_transcript.mkdir(mode=0o755)
        alias_parent = Path(self.temp.name) / "alias-parent"
        alias_parent.symlink_to(canonical_parent, target_is_directory=True)
        cases = (
            ("empty", []),
            ("not_sequence", str(self.transcripts)),
            ("non_string", [7]),
            ("relative", ["."]),
            ("tilde", ["~"]),
            (
                "symlinked_ancestor",
                [str(alias_parent / canonical_transcript.name)],
            ),
        )
        for name, transcript_roots in cases:
            with self.subTest(name=name):
                root = Path(self.temp.name) / f"schema-probe-{name}"
                installation_path = self.runtime.initialize_probe(
                    root, (self.transcripts,), Path("/usr/bin/python3")
                )
                payload = json.loads(
                    installation_path.read_text(encoding="utf-8")
                )
                payload["transcript_roots"] = transcript_roots
                installation_path.write_text(
                    json.dumps(payload), encoding="utf-8"
                )
                installation_path.chmod(0o600)

                with self.assertRaises(Exception) as raised:
                    self.runtime.load_installation(installation_path)
                self.assertIsInstance(raised.exception, ValueError)
                self.assertEqual(
                    str(raised.exception), "invalid_transcript_roots"
                )

    def test_load_rejects_noncanonical_fixed_data_root(self) -> None:
        installation_path = self.runtime.initialize_probe(
            self.root, (self.transcripts,), Path("/usr/bin/python3")
        )
        original_payload = json.loads(
            installation_path.read_text(encoding="utf-8")
        )
        alias_parent = Path(self.temp.name) / "data-root-alias"
        alias_parent.symlink_to(Path(self.temp.name), target_is_directory=True)
        cases = (
            ("relative", "."),
            ("symlinked_ancestor", str(alias_parent / self.root.name)),
        )

        for name, data_root in cases:
            with self.subTest(name=name):
                payload = {**original_payload, "data_root": data_root}
                installation_path.write_text(
                    json.dumps(payload), encoding="utf-8"
                )
                installation_path.chmod(0o600)
                previous_cwd = Path.cwd()
                try:
                    if name == "relative":
                        os.chdir(self.root)
                    with self.assertRaisesRegex(ValueError, "invalid_data_root"):
                        self.runtime.load_installation(installation_path)
                finally:
                    os.chdir(previous_cwd)

    def test_initialize_rejects_transcript_data_root_overlap_before_writes(
        self,
    ) -> None:
        parent_transcript = Path(self.temp.name) / "parent-transcript"
        parent_transcript.mkdir(mode=0o700)
        parent_root = parent_transcript / "probe"
        equal_root = Path(self.temp.name) / "equal-root"
        equal_root.mkdir(mode=0o700)
        child_root = Path(self.temp.name) / "child-root"
        child_root.mkdir(mode=0o700)
        child_transcript = child_root / "sessions"
        child_transcript.mkdir(mode=0o700)
        cases = (
            ("data_inside_transcript", parent_root, parent_transcript),
            ("equal", equal_root, equal_root),
            ("transcript_inside_data", child_root, child_transcript),
        )

        for name, root, transcript_root in cases:
            with self.subTest(name=name):
                with self.assertRaisesRegex(ValueError, "data_transcript_overlap"):
                    self.runtime.initialize_probe(
                        root, (transcript_root,), Path("/usr/bin/python3")
                    )
                for child in (
                    "installation.json",
                    "nonce.json",
                    "incoming",
                    "reports",
                ):
                    self.assertFalse((root / child).exists())

    def test_load_rejects_transcript_data_root_overlap(self) -> None:
        for relation in ("parent", "equal", "child"):
            with self.subTest(relation=relation):
                root = Path(self.temp.name) / f"overlap-probe-{relation}"
                installation_path = self.runtime.initialize_probe(
                    root, (self.transcripts,), Path("/usr/bin/python3")
                )
                canonical_root = installation_path.parent
                transcript_root = {
                    "parent": canonical_root.parent,
                    "equal": canonical_root,
                    "child": canonical_root / "incoming",
                }[relation]
                self.replace_transcript_roots(
                    installation_path, (transcript_root,)
                )
                with self.assertRaisesRegex(ValueError, "data_transcript_overlap"):
                    self.runtime.load_installation(installation_path)

    def test_case_variant_transcript_overlap_is_rejected(self) -> None:
        base = Path(self.temp.name)
        equal_root = base / "CaseEqual"
        equal_root.mkdir(mode=0o700)
        parent_transcript = base / "CaseParent"
        parent_transcript.mkdir(mode=0o700)
        child_root = base / "CaseChild"
        child_root.mkdir(mode=0o700)
        (child_root / "Sessions").mkdir(mode=0o700)
        cases = (
            (
                "equal",
                equal_root,
                equal_root.with_name("caseequal"),
                equal_root,
                equal_root.with_name("caseequal"),
            ),
            (
                "parent",
                parent_transcript / "Probe",
                parent_transcript.with_name("caseparent"),
                parent_transcript,
                parent_transcript.with_name("caseparent"),
            ),
            (
                "child",
                child_root,
                child_root.with_name("casechild") / "Sessions",
                child_root,
                child_root.with_name("casechild"),
            ),
        )

        for name, data_root, transcript_root, actual, case_variant in cases:
            with self.subTest(name=name):
                if case_variant.exists():
                    self.assertTrue(actual.samefile(case_variant))
                with self.assertRaisesRegex(ValueError, "data_transcript_overlap"):
                    self.runtime.validate_transcript_separation(
                        data_root, (transcript_root,)
                    )

    def test_case_variant_separated_paths_keep_component_boundaries(self) -> None:
        base = Path(self.temp.name)
        prefix_parent = base / "Prefix"
        (prefix_parent / "Bar").mkdir(parents=True, mode=0o700)
        (prefix_parent / "Barley").mkdir(mode=0o700)
        (base / "Root" / "Probe").mkdir(parents=True, mode=0o700)
        (base / "Rooted" / "Probe").mkdir(parents=True, mode=0o700)
        cases = (
            (
                base / "prefix" / "BAR",
                base / "PREFIX" / "barley",
            ),
            (
                base / "root" / "PROBE",
                base / "ROOTED" / "probe",
            ),
        )

        for data_root, transcript_root in cases:
            with self.subTest(
                data_root=data_root.name,
                transcript_root=transcript_root.name,
            ):
                if data_root.exists() and transcript_root.exists():
                    self.assertFalse(data_root.samefile(transcript_root))
                self.runtime.validate_transcript_separation(
                    data_root, (transcript_root,)
                )

    def test_symlink_data_root_is_rejected_before_initialization(self) -> None:
        target = Path(self.temp.name) / "target"
        target.mkdir(mode=0o700)
        link = Path(self.temp.name) / "probe-link"
        link.symlink_to(target, target_is_directory=True)
        with self.assertRaisesRegex(ValueError, "data_root_symlink"):
            self.runtime.initialize_probe(
                link, (self.transcripts,), Path("/usr/bin/python3")
            )
        self.assertEqual(list(target.iterdir()), [])

    def test_symlinked_private_child_is_rejected_before_initialization(self) -> None:
        for child in ("incoming", "reports"):
            with self.subTest(child=child):
                root = Path(self.temp.name) / f"probe-{child}"
                root.mkdir(mode=0o700)
                target = Path(self.temp.name) / f"target-{child}"
                target.mkdir(mode=0o700)
                (root / child).symlink_to(target, target_is_directory=True)
                with self.assertRaisesRegex(ValueError, "data_child_symlink"):
                    self.runtime.initialize_probe(
                        root, (self.transcripts,), Path("/usr/bin/python3")
                    )
                self.assertEqual(list(target.iterdir()), [])

    def test_world_writable_data_root_is_rejected(self) -> None:
        installation_path = self.runtime.initialize_probe(
            self.root, (self.transcripts,), Path("/usr/bin/python3")
        )
        self.root.chmod(0o777)
        with self.assertRaisesRegex(ValueError, "data_root_permissions"):
            self.runtime.load_installation(installation_path)

    def test_newer_schema_is_rejected(self) -> None:
        installation_path = self.runtime.initialize_probe(
            self.root, (self.transcripts,), Path("/usr/bin/python3")
        )
        payload = json.loads(installation_path.read_text(encoding="utf-8"))
        payload["schema_version"] = 2
        installation_path.write_text(json.dumps(payload), encoding="utf-8")
        installation_path.chmod(0o600)
        with self.assertRaisesRegex(ValueError, "unsupported_installation_schema"):
            self.runtime.load_installation(installation_path)

    def test_tampered_python_path_is_rejected(self) -> None:
        installation_path = self.runtime.initialize_probe(
            self.root, (self.transcripts,), Path("/usr/bin/python3")
        )
        payload = json.loads(installation_path.read_text(encoding="utf-8"))
        payload["python"] = "/tmp/python3"
        installation_path.write_text(json.dumps(payload), encoding="utf-8")
        installation_path.chmod(0o600)
        with self.assertRaisesRegex(ValueError, "unsupported_python"):
            self.runtime.load_installation(installation_path)

    def test_symlinked_nonce_is_rejected_on_load(self) -> None:
        installation_path = self.runtime.initialize_probe(
            self.root, (self.transcripts,), Path("/usr/bin/python3")
        )
        nonce = self.root / "nonce.json"
        nonce.unlink()
        external = Path(self.temp.name) / "external-nonce.json"
        external.write_text('{"nonce":"attacker"}', encoding="utf-8")
        external.chmod(0o600)
        nonce.symlink_to(external)
        with self.assertRaisesRegex(ValueError, "nonce_symlink"):
            self.runtime.load_installation(installation_path)

    def test_private_child_swapped_to_symlink_is_rejected_on_load(self) -> None:
        installation_path = self.runtime.initialize_probe(
            self.root, (self.transcripts,), Path("/usr/bin/python3")
        )
        reports = self.root / "reports"
        reports.rmdir()
        external = Path(self.temp.name) / "external-reports"
        external.mkdir(mode=0o700)
        reports.symlink_to(external, target_is_directory=True)
        with self.assertRaisesRegex(ValueError, "data_child_symlink"):
            self.runtime.load_installation(installation_path)

    def test_read_only_private_child_is_rejected_on_load(self) -> None:
        installation_path = self.runtime.initialize_probe(
            self.root, (self.transcripts,), Path("/usr/bin/python3")
        )
        (self.root / "incoming").chmod(0o500)
        with self.assertRaisesRegex(ValueError, "data_child_permissions"):
            self.runtime.load_installation(installation_path)

    def test_symlinked_observation_is_rejected(self) -> None:
        installation_path = self.runtime.initialize_probe(
            self.root, (self.transcripts,), Path("/usr/bin/python3")
        )
        external = Path(self.temp.name) / "external.json"
        external.write_text("{}", encoding="utf-8")
        external.chmod(0o600)
        (self.root / "incoming" / "observation.json").symlink_to(external)
        installation = self.runtime.load_installation(installation_path)
        with self.assertRaisesRegex(ValueError, "observation_symlink"):
            self.runtime.observation_paths(installation)

    def test_directory_observation_is_rejected(self) -> None:
        installation_path = self.runtime.initialize_probe(
            self.root, (self.transcripts,), Path("/usr/bin/python3")
        )
        (self.root / "incoming" / "directory.json").mkdir(mode=0o700)
        installation = self.runtime.load_installation(installation_path)
        with self.assertRaisesRegex(ValueError, "observation_not_regular"):
            self.runtime.observation_paths(installation)

    def test_tampered_nonce_value_is_rejected(self) -> None:
        installation_path = self.runtime.initialize_probe(
            self.root, (self.transcripts,), Path("/usr/bin/python3")
        )
        nonce = self.root / "nonce.json"
        nonce.write_text('{"schema_version":1,"nonce":""}', encoding="utf-8")
        nonce.chmod(0o600)
        with self.assertRaisesRegex(ValueError, "invalid_nonce"):
            self.runtime.load_installation(installation_path)


if __name__ == "__main__":
    unittest.main()
