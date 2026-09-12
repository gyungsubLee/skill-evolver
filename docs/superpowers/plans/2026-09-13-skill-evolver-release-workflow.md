# Skill Evolver release workflow implementation plan

**Goal:** Prepare version PRs from Actions and publish tested source releases
after a version change reaches main.

**Architecture:** A standard-library version/archive helper, a small shell
publisher, and CI/release workflows. Existing runtime loading and quality
contracts remain unchanged. Work proceeds in the current project on branch
`codex/release-workflow`, including the already verified 0.1.7 changes.

**Technology:** Python 3.9+, unittest, Git, Bash, GitHub CLI, macOS 15 hosted
runners and commit-pinned official checkout/upload-artifact actions.

## Constraints

- Keep source version 0.1.7 during workflow setup; no initial release or tag.
- No new runtime dependency or private installation/data/cache mutation.
- Preserve historical reports, quality guard, runtime and capture contracts.
- User approved the written design and explicitly requested immediate
  implementation; do not add design or execution-choice approval stops.
- Check every remote change and CI result before claiming completion.

## Task 1 — Version and source archive helper

Owned files: `scripts/release.py`, `scripts/test_release.py`.

- [x] Write and run failing unittest checks in temporary repositories for
  strict versions, all three bump modes, inconsistent pins/no writes,
  historical evidence preservation, committed archive identity and required
  frozen report validation.
- [x] Implement `check [--root PATH]` and `bump patch|minor|major [--root PATH]`;
  stdout is only X.Y.Z. Preflight the four production pins, current README
  declaration and production test expectations before editing exact spans.
- [x] Implement `archive --output DIR [--ref HEAD]`; validate an extracted
  Git archive, preserve hidden files, and write a deterministic versioned ZIP
  plus SHA256SUMS. Return JSON version/commit/archive/checksum paths.
- [x] Run `/usr/bin/python3 -I -m unittest discover -s scripts -p 'test_release.py'`.

## Task 2 — Safe repeatable publication

Owned files: `scripts/publish-release.sh`, `scripts/test_publish_release.py`.

- [x] Write failing checks using temporary local Git remotes and a stub gh:
  conflicting tags/assets, missing-release recovery, published retry and
  upload failure retaining a draft.
- [x] Implement `bash scripts/publish-release.sh VERSION COMMIT ARTIFACT_DIR`;
  validate HEAD, checksum and remote tag identity. Create a draft, add only
  missing assets, compare existing bytes, and publish after complete upload.
  Do not overwrite tags/assets or classify API failures as missing releases.
- [x] Run `/usr/bin/python3 -I -m unittest discover -s scripts -p 'test_publish_release.py'`.

## Task 3 — Workflows, documentation and integration

Owned files: `.github/workflows/ci.yml`, `.github/workflows/release.yml`,
`README.md`, this plan/spec and `.planning/HANDOFF.md`.

- [x] Configure read-only macOS CI for PR/main/manual runs: check version,
  run both unittest directories, validate/archive HEAD and upload an artifact
  identified by version, run number and commit without rewriting source.
- [x] Configure release preparation on manual main dispatch with a constrained
  patch/minor/major choice. Run checks, create a non-overwriting release branch
  and PR using narrowly scoped GITHUB_TOKEN permissions; never auto-merge.
- [x] Configure main push publication only after the workflow already existed
  in the previous revision and the manifest version increases. Retest exact
  HEAD, archive it and call the publisher in the same workflow. Serialize
  releases. Initial workflow installation and ordinary commits skip publishing.
- [x] Document the Actions button, PR merge, CI approval, artifacts and separate
  local installation. Keep the latest README release declaration unchanged.
- [x] Run helper/publisher tests, full runtime suite, version check, Bash and
  actionlint validation; independently review implementation and permissions.
- [ ] Commit exact paths, push the feature branch and create an integration PR
  containing the current 0.1.7 baseline. Verify the PR's actual GitHub CI.
- [x] Enable only the repository permission needed for Actions-created PRs,
  preserving the default read-only token and branch protections.
- [ ] Integrate verified changes into main and verify main CI plus bootstrap
  release skip. Verify Actions manual entry exists; do not launch a real bump
  merely as a test. Record evidence and leave Phase 5 quality work pending.

## Evidence

Local verification on 2026-09-13:

- Baseline runtime suite: 554 tests run, 551 passed, 3 historical skips.
- Final runtime suite: 554 tests run in 73.904 seconds, 551 passed, 3 skips.
- Release tools: 19 tests passed in 29.777 seconds. Initial helper and publisher
  RED runs demonstrated missing behavior before implementation.
- Independent review identified and verified fixes for draft release lookup,
  concurrency queue replacement, and timezone-dependent ZIP bytes. Five
  focused regressions passed; final review found no remaining P1/P2 issues.
- Packaging uses UTC for identical ZIP bytes across UTC, Seoul and Los Angeles.
  Draft lookup scans every release-list page and rejects duplicate tag matches.
- Bash syntax, Python compilation and diff whitespace checks passed. Actionlint
  1.7.12 passed except its unsupported `concurrency.queue` key, which is
  documented by GitHub; only that exact diagnostic was ignored. Actual GitHub
  workflow acceptance remains part of the remote verification below.
- Repository Actions is enabled. The PR-creation setting is enabled while
  default GITHUB_TOKEN permissions remain read-only. Existing rulesets unchanged.
- Runtime/manifest/runtime-reference/Hook/policy/SKILL bytes match `eef580e`.
  Version remains 0.1.7; no tag, release or local plugin update was created.

Build CI does not constitute a real Phase 5 quality sample.
