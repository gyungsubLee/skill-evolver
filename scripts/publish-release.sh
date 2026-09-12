#!/usr/bin/env bash
set -euo pipefail

# ponytail: two source-release assets only; workflow concurrency serializes writers.
exec /usr/bin/python3 -I - "$@" <<'PY'
import hashlib
import json
import os
from pathlib import Path
import re
import subprocess
import sys


def fail(message):
    raise SystemExit(message)


def run(*args):
    result = subprocess.run(args, capture_output=True)
    if result.returncode:
        fail(result.stderr.decode(errors="replace").strip() or "Command failed: " + args[0])
    return result.stdout


if len(sys.argv) != 4:
    fail("Usage: publish-release.sh VERSION COMMIT ARTIFACT_DIR")
version, revision, directory = sys.argv[1:]
if not re.fullmatch(r"(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)", version):
    fail("Invalid release version: expected X.Y.Z without leading zeroes")
repo = os.environ.get("GH_REPO", "")
if not re.fullmatch(r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+", repo) or not os.environ.get("GH_TOKEN"):
    fail("GH_REPO and GH_TOKEN are required")
commit = run("git", "rev-parse", "--verify", "--end-of-options", revision + "^{commit}").decode().strip()
if commit != run("git", "rev-parse", "HEAD").decode().strip():
    fail("Release commit does not match checked-out HEAD")
tag = "v" + version
archive_name = "skill-evolver-" + version + ".zip"
artifacts = Path(directory).resolve()
paths = {name: artifacts / name for name in (archive_name, "SHA256SUMS")}
try:
    checksums = {name: hashlib.sha256(path.read_bytes()).hexdigest() for name, path in paths.items()}
    if paths["SHA256SUMS"].read_bytes() != (checksums[archive_name] + "  " + archive_name + "\n").encode():
        fail("Local checksum or archive filename does not match SHA256SUMS")
except OSError as error:
    fail("Cannot read release artifacts: " + str(error))


def release_info():
    # The tag endpoint omits drafts; list every accessible page before deciding absence.
    pages = json.loads(run("gh", "api", "--paginate", "--slurp", "repos/" + repo + "/releases?per_page=100"))
    if not isinstance(pages, list) or not pages or any(not isinstance(page, list) for page in pages):
        fail("Unexpected release listing")
    if any(not isinstance(item, dict) for page in pages for item in page):
        fail("Unexpected release metadata")
    matches = [item for page in pages for item in page if item.get("tag_name") == tag]
    if len(matches) > 1:
        fail("Duplicate releases match tag: " + tag)
    if not matches:
        return None
    info = matches[0]
    if type(info.get("draft")) is not bool or not isinstance(info.get("assets"), list):
        fail("Unexpected release metadata")
    return info


def check_assets(info, allow_missing):
    found = set()
    for asset in info["assets"]:
        name = asset.get("name")
        if name not in paths or name in found or asset.get("state") != "uploaded":
            fail("Unexpected, duplicate, or incomplete release asset")
        asset_id = asset.get("id")
        if type(asset_id) is not int or asset_id < 1:
            fail("Invalid release asset ID")
        content = run("gh", "api", "repos/" + repo + "/releases/assets/" + str(asset_id),
                      "-H", "Accept: application/octet-stream")
        if hashlib.sha256(content).hexdigest() != checksums[name]:
            fail("Conflicting release asset: " + name)
        found.add(name)
    missing = [name for name in paths if name not in found]
    if missing and not allow_missing:
        fail("Published release is missing expected assets")
    return missing


remote = run("git", "ls-remote", "--tags", "origin", "refs/tags/" + tag, "refs/tags/" + tag + "^{}")
refs = dict(line.split()[::-1] for line in remote.decode().splitlines())
remote_commit = refs.get("refs/tags/" + tag + "^{}", refs.get("refs/tags/" + tag))
if remote_commit and remote_commit != commit:
    fail("Existing remote tag points to a different commit: " + tag)
info = release_info()
if info:
    if not remote_commit:
        fail("Existing release has no remote tag")
    missing = check_assets(info, allow_missing=info["draft"])
    if not info["draft"]:
        print("Release already published with identical assets: " + tag)
        raise SystemExit(0)
else:
    if not remote_commit:
        local_tags = run("git", "tag", "--list", tag).decode().strip()
        if local_tags:
            if run("git", "rev-parse", tag + "^{commit}").decode().strip() != commit:
                fail("Existing local tag points to a different commit: " + tag)
        else:
            run("git", "tag", tag, commit)
        run("git", "push", "origin", "refs/tags/" + tag)
    run("gh", "release", "create", tag, "--repo", repo, "--verify-tag", "--target", commit,
        "--title", tag, "--generate-notes", "--draft")
    info = release_info()
    if not info or not info["draft"]:
        fail("New release draft was not found")
    missing = check_assets(info, allow_missing=True)
for name in missing:
    run("gh", "release", "upload", tag, str(paths[name]), "--repo", repo)
info = release_info()
if not info or not info["draft"]:
    fail("Release must remain a draft until assets are verified")
check_assets(info, allow_missing=False)
run("gh", "release", "edit", tag, "--repo", repo, "--draft=false")
published = release_info()
if not published or published["draft"]:
    fail("GitHub release publication was not confirmed")
print("Published verified source release: " + tag)
PY
