from __future__ import annotations

import json
import os
import stat
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from support import load_runtime


class InstallationTests(unittest.TestCase):
    def setUp(self) -> None:
        self.runtime = load_runtime()
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name) / "probe"
        self.transcripts = Path(self.temp.name) / "sessions"
        self.transcripts.mkdir(mode=0o700)

    def test_initialize_creates_private_canonical_installation(self) -> None:
        installation_path = self.runtime.initialize_probe(
            self.root, (self.transcripts,), Path("/usr/bin/python3")
        )
        installation = self.runtime.load_installation(installation_path)

        self.assertEqual(installation.data_root, self.root.resolve())
        self.assertEqual(installation.transcript_roots, (self.transcripts.resolve(),))
        self.assertEqual(installation.python, Path("/usr/bin/python3"))
        self.assertEqual(stat.S_IMODE(self.root.stat().st_mode), 0o700)
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
