"""Publication regression checks; only temporary Git repositories and a stub gh."""

import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest


SCRIPT = Path(__file__).with_name("publish-release.sh")
GH_STUB = r'''import base64, json, os, pathlib, sys
path = pathlib.Path(os.environ["GH_STUB_STATE"])
state = json.loads(path.read_text())
args = sys.argv[1:]
state["calls"].append(args)
def save():
    path.write_text(json.dumps(state))
def stop(message, code=1):
    save()
    print(message, file=sys.stderr)
    raise SystemExit(code)
if args[0] == "api":
    endpoint = next(a for a in args[1:] if a.startswith("repos/"))
    if "/releases/assets/" in endpoint:
        asset_id = int(endpoint.rsplit("/", 1)[1])
        asset = next(a for a in state["release"]["assets"] if a["id"] == asset_id)
        save()
        sys.stdout.buffer.write(base64.b64decode(asset["content"]))
    elif "/releases/tags/" in endpoint:
        # The tag endpoint exposes published releases, never drafts.
        published = state["release"] and not state["release"]["draft"]
        status = state.get("api_error") or (200 if published else 404)
        body = state["release"] if status == 200 else {"message": "Not Found"}
        save()
        print("HTTP/2.0 " + str(status) + "\r\nContent-Type: application/json\r\n\r\n" + json.dumps(body))
        if status != 200:
            raise SystemExit(1)
    elif endpoint.endswith("/releases?per_page=100"):
        if state.get("api_error"):
            stop("HTTP " + str(state["api_error"]))
        # A match is deliberately on the second page, including for drafts.
        pages = [[], [state["release"]]] if state["release"] else [[]]
        if state.get("duplicate_release"):
            pages.append([state["release"]])
        save()
        print(json.dumps(pages if "--paginate" in args and "--slurp" in args else pages[0]))
    else:
        stop("unexpected API endpoint")
elif args[:2] == ["release", "create"]:
    if state["release"]:
        stop("release already exists")
    if "--draft" not in args or "--verify-tag" not in args:
        stop("must create verified draft")
    state["release"] = {"id": 1, "tag_name": args[2], "draft": True, "assets": []}
    save()
elif args[:2] == ["release", "upload"]:
    asset_path = pathlib.Path(args[3])
    if asset_path.name == state.get("fail_upload"):
        stop("upload failed")
    if any(a["name"] == asset_path.name for a in state["release"]["assets"]):
        stop("asset already exists")
    state["release"]["assets"].append({"id": len(state["release"]["assets"]) + 1,
        "name": asset_path.name, "state": "uploaded",
        "content": base64.b64encode(asset_path.read_bytes()).decode()})
    save()
elif args[:2] == ["release", "edit"]:
    if "--draft=false" not in args:
        stop("unexpected edit")
    state["release"]["draft"] = False
    save()
else:
    stop("unexpected gh command: " + repr(args))
'''


class PublishReleaseTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.repo = self.root / "repo"
        self.repo.mkdir()
        self.env = dict(os.environ, GH_REPO="example/skill-evolver", GH_TOKEN="fixture-token",
                        GH_STUB_STATE=str(self.root / "gh-state.json"))
        self.git("init", "--bare", str(self.root / "remote.git"))
        self.git("init")
        self.git("config", "user.name", "Release Test")
        self.git("config", "user.email", "release@example.invalid")
        (self.repo / "source.txt").write_text("fixture\n")
        self.git("add", "source.txt")
        self.git("commit", "-m", "fixture")
        self.commit = self.git("rev-parse", "HEAD").strip()
        self.git("remote", "add", "origin", str(self.root / "remote.git"))
        self.artifacts = self.root / "artifacts"
        self.artifacts.mkdir()
        self.zip_name = "skill-evolver-1.2.3.zip"
        self.archive = self.artifacts / self.zip_name
        self.archive.write_bytes(b"deterministic source archive fixture")
        digest = hashlib.sha256(self.archive.read_bytes()).hexdigest()
        (self.artifacts / "SHA256SUMS").write_text(f"{digest}  {self.zip_name}\n")
        binary = self.root / "bin"
        binary.mkdir()
        gh = binary / "gh"
        gh.write_text(f"#!{sys.executable}\n" + GH_STUB)
        gh.chmod(0o755)
        self.env["PATH"] = str(binary) + os.pathsep + self.env["PATH"]
        self.save_state({"release": None, "calls": []})

    def git(self, *args):
        return subprocess.check_output(["git", *args], cwd=self.repo, env=self.env,
                                       stderr=subprocess.DEVNULL, text=True)

    def state(self):
        return json.loads(Path(self.env["GH_STUB_STATE"]).read_text())

    def save_state(self, state):
        Path(self.env["GH_STUB_STATE"]).write_text(json.dumps(state))

    def publish(self, version="1.2.3", commit=None):
        return subprocess.run(["bash", str(SCRIPT), version, commit or self.commit,
                               str(self.artifacts)], cwd=self.repo, env=self.env,
                              capture_output=True, text=True)

    def assert_success(self, result):
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

    def test_new_release_publishes_only_after_both_assets(self):
        self.assert_success(self.publish())
        state = self.state()
        self.assertFalse(state["release"]["draft"])
        self.assertEqual({a["name"] for a in state["release"]["assets"]},
                         {self.zip_name, "SHA256SUMS"})
        self.assertIn(self.commit, self.git("ls-remote", "origin", "refs/tags/v1.2.3"))

    def test_conflicting_remote_tag_stops_without_release_writes(self):
        self.git("commit", "--allow-empty", "-m", "other commit")
        other = self.git("rev-parse", "HEAD").strip()
        self.git("tag", "v1.2.3", other)
        self.git("push", "origin", "refs/tags/v1.2.3")
        self.git("checkout", "--detach", self.commit)
        result = self.publish()
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("tag", result.stderr.lower())
        self.assertEqual(self.state()["calls"], [])
        self.assertIn(other, self.git("ls-remote", "origin", "refs/tags/v1.2.3"))

    def test_existing_annotated_tag_without_release_resumes(self):
        self.git("tag", "-a", "v1.2.3", "-m", "existing tag")
        self.git("push", "origin", "refs/tags/v1.2.3")
        before = self.git("ls-remote", "origin", "refs/tags/v1.2.3*")
        self.assert_success(self.publish())
        self.assertEqual(before, self.git("ls-remote", "origin", "refs/tags/v1.2.3*"))

    def test_identical_published_release_is_read_only_retry(self):
        self.assert_success(self.publish())
        state = self.state()
        state["calls"] = []
        self.save_state(state)
        self.assert_success(self.publish())
        self.assertTrue(all(call[0] == "api" for call in self.state()["calls"]))

    def test_failed_upload_stays_draft_and_retry_finishes(self):
        state = self.state()
        state["fail_upload"] = "SHA256SUMS"
        self.save_state(state)
        result = self.publish()
        self.assertNotEqual(result.returncode, 0)
        state = self.state()
        self.assertIsNotNone(state["release"], result.stderr)
        self.assertTrue(state["release"]["draft"])
        self.assertEqual([a["name"] for a in state["release"]["assets"]], [self.zip_name])
        del state["fail_upload"]
        self.save_state(state)
        self.assert_success(self.publish())
        self.assertFalse(self.state()["release"]["draft"])

    def test_conflicting_existing_assets_are_never_overwritten(self):
        self.assert_success(self.publish())
        for draft in (True, False):
            with self.subTest(draft=draft):
                state = self.state()
                state["release"]["draft"] = draft
                state["release"]["assets"][0]["content"] = "d3Jvbmc="
                state["calls"] = []
                self.save_state(state)
                result = self.publish()
                self.assertNotEqual(result.returncode, 0)
                self.assertIn("asset", result.stderr.lower())
                self.assertTrue(all(c[0] == "api" for c in self.state()["calls"]))

    def test_published_release_with_missing_asset_fails(self):
        self.assert_success(self.publish())
        state = self.state()
        state["release"]["assets"].pop()
        state["calls"] = []
        self.save_state(state)
        result = self.publish()
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("asset", result.stderr.lower())
        self.assertTrue(all(c[0] == "api" for c in self.state()["calls"]))

    def test_api_authentication_error_does_not_create_release(self):
        self.save_state({"release": None, "calls": [], "api_error": 403})
        result = self.publish()
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("403", result.stderr)
        self.assertTrue(all(c[0] == "api" for c in self.state()["calls"]))

    def test_duplicate_tag_matches_across_pages_fail_without_writes(self):
        self.git("tag", "v1.2.3")
        self.git("push", "origin", "refs/tags/v1.2.3")
        self.save_state({"release": {"id": 1, "tag_name": "v1.2.3", "draft": True, "assets": []},
                         "duplicate_release": True, "calls": []})
        result = self.publish()
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("duplicate", result.stderr.lower())
        self.assertTrue(all(c[0] == "api" for c in self.state()["calls"]))

    def test_invalid_local_inputs_cannot_push_a_tag(self):
        for version in ("01.2.3", "1.2", "1.2.3;echo unsafe"):
            with self.subTest(version=version):
                result = self.publish(version=version)
                self.assertNotEqual(result.returncode, 0)
                self.assertIn("version", result.stderr.lower())
        self.archive.write_bytes(b"changed")
        result = self.publish()
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("checksum", result.stderr.lower())
        self.assertEqual(self.git("ls-remote", "origin", "refs/tags/v1.2.3"), "")
        self.assertEqual(self.state()["calls"], [])


if __name__ == "__main__":
    unittest.main()
