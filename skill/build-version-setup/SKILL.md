---
name: build-version-setup
description: Set up or repair build versioning, CI artifacts, and release automation, especially GitHub Actions. Use for 빌드 버전 세팅, 버전 자동화, or initial release workflow setup. For operating an established pipeline, use build-release-manage.
---

# Build Version Setup

Make the current project's version and build outputs traceable using its existing toolchain. Deliver working configuration, a short operating guide, and verification evidence. A setup request alone does not request an actual release or deployment.

## Discover before choosing

Inspect the working directory, repository instructions, uncommitted changes, remotes and default branch. Read manifests, lockfiles, build/test scripts, existing workflows, release configuration, and current version declarations. Trace what consumers actually install or download. Do not assume `main`, npm, SemVer, a compiled binary, or the path of a previous project.

Identify:

- The authoritative release version and any derived files that must agree with it.
- The existing versioning tool and release trigger, including monorepo or prerelease conventions.
- Build/test commands, runner constraints, package contents, and expected output format.
- Where CI artifacts and durable releases belong, and which actions the user has authorized.

Distinguish version declarations from historical changelogs, migration fixtures, frozen reports and provenance records. Never replace every occurrence of an old version string.

## Reuse the smallest working strategy

Keep a working Changesets, Release Please, semantic-release, ecosystem-native version command, or equivalent setup. Repair missing checks rather than introducing a second release controller. Use installed tools and native platform features before custom scripts or dependencies.

When no convention exists, explain the proposed version source and release trigger briefly, then implement within the user's scope. A manually prepared version PR is a useful default when the user wants control over release timing. Automatic commit-derived releases require a suitable commit convention; do not impose that migration silently. Ask only if a missing product decision materially changes publication behavior.

Keep release version distinct from build identity. Record version, workflow/run identity, attempt, and exact commit with artifacts. GitHub run numbers belong to one workflow; retries retain the number and increase the attempt. Avoid committing a production version bump on every CI run unless the product explicitly needs that behavior. Native mobile/store build counters have separate constraints: inspect those contracts first.

For GitHub Actions, read [the setup reference](references/github-actions.md) for event behavior, permissions and remote validation. Respect another requested CI provider instead of switching it to GitHub.

## Implement the release path

1. Reuse or add version consistency validation. If custom bump logic is necessary, validate all targets before editing; cover supported increments, inconsistent inputs and preservation of historical evidence with a small runnable check.
2. Run the project's real build and required tests in CI. Package the tested revision and required hidden/configuration files, not arbitrary working-tree contents. Check the unpacked deliverable using the existing loader, entrypoint or package validator.
3. Name artifacts so their version and source revision are recoverable. Set an appropriate retention period explicitly. Add checksums or the ecosystem's native integrity metadata to durable releases.
4. When release automation is in scope, connect preparation, review and publication using the established trigger. For artifact-only setup, retain the existing release integration and document its entry point. Validate tag/version/commit agreement, serialize publication, and define retry behavior before giving jobs write permissions. Existing tags or published assets with conflicting content must fail without replacement.
5. Update the existing contributor or release guide with the actual entry point, required inputs, checks and artifact locations. Explain which operation creates a version PR and which publishes it.

Use narrowly scoped writes only in trusted release jobs. Keep untrusted PR builds away from publication credentials. Pin third-party actions to reviewed full commit SHAs and check their supported runner/runtime versions when adding or updating them.

When offline, prefer an existing repository pin or inspect an available trusted local reference. Record the provenance and leave current remote compatibility unverified; never invent a commit SHA or claim a remote check ran.

## Verify and finish

Run the relevant version, build, test, packaging and workflow validation commands. Inspect the actual results; a YAML parse alone does not validate GitHub Actions expressions or event behavior. Test bump logic in an isolated copy when a real bump was not requested.

If remote setup is authorized, integrate through the repository's normal process, confirm the workflow is available on the default branch, and verify a real CI run for the intended revision. A PR run may test a generated merge commit; record that relationship rather than claiming it tested an unrelated SHA. Confirm setup did not accidentally publish the existing version. Do not dispatch a publishing workflow merely to prove its button exists.

If remote access or integration is outside scope, complete and validate the local files and describe the specific unverified remote step. Preserve existing protections and credential policy. Ask for missing authorization only after the proposed change is concrete; do not re-ask for an already authorized action.

Report the version source, build identity, setup changes, entry point for the next release, test/run evidence and any remaining limitation. For later status checks, release preparation or recovery, use `$build-release-manage` when available; this skill does not require it to complete setup.
