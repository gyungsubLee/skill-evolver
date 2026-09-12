#!/usr/bin/env python3
"""Check/bump exact release pins and archive an immutable Git revision."""

import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import runpy
import subprocess
import sys
import tempfile
import zipfile


ROOT = Path(__file__).resolve().parents[1]
MANIFEST = ".codex-plugin/plugin.json"
EVOLVER = "skills/skill-evolver/scripts/evolver.py"
VERSION = r"(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)"
PIN_PATTERNS = {
    MANIFEST: (r'^  "version": "(?P<version>[^"\n]+)",$',),
    "skills/skill-evolver/references/runtime.json": (
        r'^  "version": "(?P<version>[^"\n]+)",$',
    ),
    EVOLVER: (
        r'^VERSION = "skill-evolver (?P<version>[^"\n]+)"$',
        r'^        or payload\["version"\] != "(?P<version>[^"\n]+)"$',
    ),
    "README.md": (r'^Version `(?P<version>[^`\n]+)`',),
    "skills/skill-evolver/tests/test_capture.py": (
        r'^        self.assertEqual\(manifest\["version"\], "(?P<version>[^"\n]+)"\)$',
        r'^        self.assertEqual\(runtime\["version"\], "(?P<version>[^"\n]+)"\)$',
        r'^            "skill-evolver (?P<version>[^"\n]+)",$',
    ),
}


def valid_version(value):
    if not isinstance(value, str) or re.fullmatch(VERSION, value) is None:
        raise ValueError("version must be X.Y.Z with no leading zeroes")
    return value


def checked_sources(root):
    sources = {name: (root / name).read_bytes().decode("utf-8") for name in PIN_PATTERNS}
    version = valid_version(json.loads(sources[MANIFEST])["version"])
    spans = {}
    for name, patterns in PIN_PATTERNS.items():
        spans[name] = []
        for pattern in patterns:
            matches = list(re.finditer(pattern, sources[name], re.MULTILINE))
            if len(matches) != 1 or matches[0]["version"] != version:
                raise ValueError(f"missing, ambiguous or inconsistent current version in {name}")
            spans[name].append(matches[0].span("version"))
    # These existing validators read only packaged runtime/report files, never live data.
    runtime = runpy.run_path(str(root / EVOLVER))
    runtime["load_review_runtime"]()
    runtime["phase4_release_report_digest"]()
    return version, sources, spans


def bump(root, part):
    old, sources, spans = checked_sources(root)
    components = list(map(int, old.split(".")))
    index = ("major", "minor", "patch").index(part)
    components[index] += 1
    components[index + 1:] = [0] * (2 - index)
    version = ".".join(map(str, components))
    replacements = {}
    for name, source in sources.items():
        for start, end in sorted(spans[name], reverse=True):
            source = source[:start] + version + source[end:]
        replacements[name] = source.encode("utf-8")
    # Complete all validation and prepare all edits before the first write.
    for name, encoded in replacements.items():
        (root / name).write_bytes(encoded)
    return version


def git(root, *arguments):
    return subprocess.check_output(["git", "-C", str(root), *arguments],
                                   stderr=subprocess.PIPE, env={**os.environ, "TZ": "UTC"})


def archive(root, output, ref):
    if Path(git(root, "rev-parse", "--show-toplevel").decode().strip()).resolve() != root:
        raise ValueError("archive root must be the Git repository root")
    commit = git(root, "rev-parse", "--verify", "--end-of-options", ref + "^{commit}").decode().strip()
    version = valid_version(json.loads(git(root, "show", f"{commit}:{MANIFEST}"))["version"])
    prefix = f"skill-evolver-{version}"
    with tempfile.TemporaryDirectory() as temporary:
        temporary = Path(temporary)
        package = temporary / (prefix + ".zip")
        git(root, "archive", "--format=zip", "--prefix=" + prefix + "/",
            "--output=" + str(package), commit)
        with zipfile.ZipFile(package) as zipped:
            zipped.extractall(temporary / "extracted")
        checked_sources(temporary / "extracted" / prefix)
        encoded = package.read_bytes()
    checksum = f"{hashlib.sha256(encoded).hexdigest()}  {prefix}.zip\n".encode()
    output = output.resolve()
    artifacts = {output / (prefix + ".zip"): encoded, output / "SHA256SUMS": checksum}
    for path, content in artifacts.items():
        if path.is_symlink() or (path.exists() and (not path.is_file() or path.read_bytes() != content)):
            raise ValueError(f"conflicting output: {path}")
    output.mkdir(parents=True, exist_ok=True)
    for path, content in artifacts.items():
        if not path.exists():
            with path.open("xb") as stream:
                stream.write(content)
    return {"version": version, "commit": commit,
            "archive": str(output / (prefix + ".zip")), "checksum": str(output / "SHA256SUMS")}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    check = commands.add_parser("check")
    prepare = commands.add_parser("bump")
    prepare.add_argument("part", choices=("patch", "minor", "major"))
    package = commands.add_parser("archive")
    package.add_argument("--output", type=Path, required=True)
    package.add_argument("--ref", default="HEAD")
    for command in (check, prepare, package):
        command.add_argument("--root", type=Path, default=ROOT)
    args = parser.parse_args()
    root = args.root.resolve()
    try:
        if args.command == "check":
            print(checked_sources(root)[0])
        elif args.command == "bump":
            print(bump(root, args.part))
        else:
            print(json.dumps(archive(root, args.output, args.ref), sort_keys=True))
    except (OSError, ValueError, KeyError, subprocess.CalledProcessError, zipfile.BadZipFile) as error:
        parser.exit(1, f"release: {error}\n")


if __name__ == "__main__":
    sys.dont_write_bytecode = True
    main()
