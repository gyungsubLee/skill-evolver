"""Runnable release checks; every mutation is confined to a temporary tree."""

import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest
import zipfile


ROOT = Path(__file__).resolve().parents[1]
HELPER = ROOT / "scripts/release.py"
MANIFEST = ".codex-plugin/plugin.json"
RUNTIME = "skills/skill-evolver/references/runtime.json"
EVOLVER = "skills/skill-evolver/scripts/evolver.py"
CAPTURE = "skills/skill-evolver/tests/test_capture.py"
PINS = (MANIFEST, RUNTIME, EVOLVER, "README.md", CAPTURE)
REPORTS = (
    "docs/release-reports/runtime-queue.json",
    "docs/release-reports/review-inbox.json",
)


class ReleaseTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name) / "source"
        self.root.mkdir()
        for name in (*PINS, *REPORTS, ".agents/plugins/marketplace.json", "hooks/hooks.json"):
            destination = self.root / name
            destination.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(ROOT / name, destination)
        self.version = json.loads((self.root / MANIFEST).read_text())["version"]

    def run_helper(self, *arguments, succeeds=True, env=None):
        result = subprocess.run(
            [sys.executable, "-I", str(HELPER), *arguments, "--root", str(self.root)],
            text=True, capture_output=True, env=env,
        )
        if succeeds:
            self.assertEqual(result.returncode, 0, result.stderr)
        else:
            self.assertNotEqual(result.returncode, 0)
            self.assertEqual(result.stdout, "")
            self.assertIn("release:", result.stderr)
        return result.stdout

    def snapshot(self):
        return {str(path.relative_to(self.root)): path.read_bytes()
                for path in self.root.rglob("*") if path.is_file()}

    def replace(self, name, old, new):
        path = self.root / name
        path.write_text(path.read_text().replace(old, new))

    def git(self, *arguments):
        return subprocess.check_output(
            ["git", "-C", str(self.root), *arguments], text=True,
            stderr=subprocess.PIPE,
        ).strip()

    def commit(self):
        if not (self.root / ".git").exists():
            self.git("init", "-q")
        self.git("add", ".")
        self.git("-c", "user.name=Release test", "-c", "user.email=release@example.invalid",
                 "-c", "commit.gpgsign=false", "commit", "-qm", "Fixture")
        return self.git("rev-parse", "HEAD")

    def test_check_is_read_only(self):
        before = self.snapshot()
        self.assertEqual(self.run_helper("check"), self.version + "\n")
        self.assertEqual(self.snapshot(), before)

    def test_rejects_malformed_version_without_writes(self):
        original = (self.root / MANIFEST).read_text()
        for malformed in ("01.2.3", "1.2", "v1.2.3", "1.2.3-beta", "1.2.3+build", "１.2.3", "1.2.-3"):
            with self.subTest(version=malformed):
                (self.root / MANIFEST).write_text(original.replace(self.version, malformed))
                before = self.snapshot()
                self.run_helper("bump", "patch", succeeds=False)
                self.assertEqual(self.snapshot(), before)

    def test_every_current_pin_is_checked_before_any_write(self):
        mutations = (
            (RUNTIME, '"version": "' + self.version + '"'),
            (EVOLVER, 'VERSION = "skill-evolver ' + self.version + '"'),
            (EVOLVER, 'payload["version"] != "' + self.version + '"'),
            ("README.md", "Version `" + self.version + "`"),
            (CAPTURE, 'self.assertEqual(manifest["version"], "' + self.version + '")'),
            (CAPTURE, 'self.assertEqual(runtime["version"], "' + self.version + '")'),
            (CAPTURE, '"skill-evolver ' + self.version + '"'),
        )
        for name, old in mutations:
            with self.subTest(pin=old):
                original = (self.root / name).read_bytes()
                self.replace(name, old, old.replace(self.version, "99.0.0"))
                try:
                    before = self.snapshot()
                    self.run_helper("bump", "patch", succeeds=False)
                    self.assertEqual(self.snapshot(), before)
                finally:
                    (self.root / name).write_bytes(original)

    def test_bumps_only_current_pins_for_each_component(self):
        originals = self.snapshot()
        major, minor, patch = map(int, self.version.split("."))
        for part, expected in (("patch", f"{major}.{minor}.{patch + 1}"),
                               ("minor", f"{major}.{minor + 1}.0"),
                               ("major", f"{major + 1}.0.0")):
            with self.subTest(part=part):
                for name, content in originals.items():
                    (self.root / name).write_bytes(content)
                history = self.root / "historical.md"
                history.write_text(f"Historical version {self.version}; retain this.\n")
                with (self.root / "README.md").open("a") as stream:
                    stream.write(f"\nHistorical release `{self.version}`.\n")
                before = self.snapshot()
                self.assertEqual(self.run_helper("bump", part), expected + "\n")
                self.assertEqual(self.run_helper("check"), expected + "\n")
                after = self.snapshot()
                self.assertEqual({name for name in before if before[name] != after[name]}, set(PINS))
                self.assertIn(f"Historical release `{self.version}`.", (self.root / "README.md").read_text())

    def test_rejects_unknown_bump_without_writes(self):
        before = self.snapshot()
        result = subprocess.run([sys.executable, "-I", str(HELPER), "bump", "auto",
                                 "--root", str(self.root)], capture_output=True)
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(self.snapshot(), before)

    def test_required_report_contracts_are_validated_before_bump(self):
        for name in REPORTS:
            for missing in (True, False):
                with self.subTest(report=name, missing=missing):
                    path = self.root / name
                    original = path.read_bytes()
                    if missing:
                        path.unlink()
                    else:
                        path.write_bytes(original + b"\n")
                    try:
                        before = self.snapshot()
                        self.run_helper("bump", "patch", succeeds=False)
                        self.assertEqual(self.snapshot(), before)
                    finally:
                        path.write_bytes(original)

    def test_archive_is_exact_committed_tree_with_stable_digest(self):
        commit = self.commit()
        committed_manifest = (self.root / MANIFEST).read_bytes()
        self.replace(MANIFEST, self.version, "99.0.0")
        (self.root / "untracked-secret.txt").write_text("must not ship")
        output = Path(self.temporary.name) / "dist"
        result = json.loads(self.run_helper("archive", "--output", str(output), "--ref", commit))
        self.assertEqual(result["version"], self.version)
        self.assertEqual(result["commit"], commit)
        archive = Path(result["archive"])
        prefix = f"skill-evolver-{self.version}/"
        self.assertEqual(archive.name, f"skill-evolver-{self.version}.zip")
        with zipfile.ZipFile(archive) as package:
            self.assertEqual(package.read(prefix + MANIFEST), committed_manifest)
            for name in (*REPORTS, ".agents/plugins/marketplace.json", "hooks/hooks.json"):
                self.assertIn(prefix + name, package.namelist())
            self.assertNotIn(prefix + "untracked-secret.txt", package.namelist())
            self.assertTrue(all(name.startswith(prefix) for name in package.namelist()))
        digest = hashlib.sha256(archive.read_bytes()).hexdigest()
        self.assertEqual(Path(result["checksum"]).read_text(), f"{digest}  {archive.name}\n")
        again = json.loads(self.run_helper("archive", "--output", str(output)))
        self.assertEqual(result, again)
        archive.write_bytes(b"conflict")
        checksum = Path(result["checksum"]).read_bytes()
        self.run_helper("archive", "--output", str(output), succeeds=False)
        self.assertEqual(archive.read_bytes(), b"conflict")
        self.assertEqual(Path(result["checksum"]).read_bytes(), checksum)

    def test_invalid_committed_report_never_publishes_archive(self):
        (self.root / REPORTS[0]).unlink()
        self.commit()
        output = Path(self.temporary.name) / "dist"
        self.run_helper("archive", "--output", str(output), succeeds=False)
        self.assertFalse(output.exists())

    def test_archive_bytes_do_not_depend_on_host_timezone(self):
        self.commit()
        artifacts = []
        for index, zone in enumerate(("UTC", "Asia/Seoul", "America/Los_Angeles")):
            output = Path(self.temporary.name) / f"dist-{index}"
            result = json.loads(self.run_helper(
                "archive", "--output", str(output), env={**os.environ, "TZ": zone},
            ))
            artifacts.append((Path(result["archive"]).read_bytes(),
                              Path(result["checksum"]).read_bytes()))
        self.assertEqual(artifacts[0], artifacts[1])
        self.assertEqual(artifacts[0], artifacts[2])


if __name__ == "__main__":
    unittest.main()
