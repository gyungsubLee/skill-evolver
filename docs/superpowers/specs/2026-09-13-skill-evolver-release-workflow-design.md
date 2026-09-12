# Skill Evolver GitHub Actions version and release workflow

Status: approved on 2026-09-13; implementation authorized without further
design checkpoints. Workflow setup preserves the current 0.1.7 version.

## Goal and current evidence

Manage release versions and source archives from the current standalone
repository, `/Users/igyeongseob/Develop/10_herness/skill-evolver`.

The working branch contains verified source 0.1.7 at implementation commit
`4d535cb`, followed by documentation commit `eef580e`. GitHub's main branch
does not yet contain that work. Neither the local tree nor GitHub's default
branch contains a workflow directory; the GitHub Releases collection is empty.
This Python standard-library plugin has no separate compilation/build system.

There are four production version literals: plugin manifest, runtime JSON,
CLI VERSION, and the strict runtime JSON version check. The current README
and production-surface tests also mention the current version. Historical
release reports and planning evidence must retain their original values.

## Options

| Option | User operation | Trade-off |
| --- | --- | --- |
| A — explicit release preparation, recommended | Choose patch/minor/major in Actions, then review and merge the generated version PR | Controls release timing; uses GitHub Actions, GitHub CLI and a small standard-library helper |
| B — Release Please | Write Conventional Commits, then merge the generated release PR | Automates version selection and changelog generation; introduces a third-party action and its release conventions |
| C — tag-triggered release | Update versions locally and push a matching tag | Fewest moving parts, but leaves version synchronization manual |

## Recommended behavior

1. PR and main-branch CI checks all version pins against the plugin manifest,
   runs the existing complete unittest suite on a macOS hosted runner, and
   validates the release archive contents. Use the existing `/usr/bin/python3`
   interpreter contract; do not assume setup-python changes child interpreters.
2. A manually dispatched release workflow accepts patch, minor or major and
   prepares a PR against main. It reads main's current version, synchronizes
   the exact current-version locations, runs validation, and commits the
   resulting files to a release branch. It does not merge its own PR. Existing
   release branches are not force-pushed or overwritten.
3. When the version change reaches main, validate that committed revision
   again, create the matching `vX.Y.Z` tag, and create a GitHub Release with
   generated notes, a source ZIP and its SHA-256 checksum. Package the tested
   commit without editing its files during packaging. Publish in this workflow
   directly rather than relying on a bot-created tag to trigger another run.
4. Unchanged versions do not create releases. Merely merging the initial
   workflow setup does not retroactively publish 0.1.7. Re-running a publication
   may finish a missing release only when the existing tag identifies the same
   validated commit; conflicting tags or assets stop the run.
5. The release version is SemVer. CI run number and commit SHA are build
   identifiers in the Actions summary/artifact metadata, not edits to the
   production version or runtime bytes.

Use a small version check/bump helper instead of changing how the runtime
loads its version. Preserve strict runtime checks. A narrow test for that
helper must reject inconsistent pins and malformed versions, verify the
three bump choices, and prove historical evidence is not rewritten.

Archive the committed source tree using Git's archive support, preserving
hidden plugin/marketplace files and the required Phase 3/4 report files:
`docs/release-reports/runtime-queue.json` and `review-inbox.json`.
Verify the extracted archive can load its runtime and frozen report contracts.
This is a source release of the current macOS configuration, not a portability
change to its fixed installation paths.

## Repository setup and boundaries

The workflow must reach the default branch before its Actions button is
available. Integration must include the current 0.1.7 work before using it as
the release baseline. Existing main is not overwritten or force-pushed.

Use the repository GITHUB_TOKEN with read-only CI permissions and narrowly
scoped contents/pull-request write permissions in release jobs. Verify the
repository allows Actions to create PRs. GitHub may require approval before
running CI on a PR created with GITHUB_TOKEN; do not silently provision a
personal token or weaken repository protections to avoid that approval.

Release jobs serialize publication, validate inputs and tag/commit identity,
and fail on inconsistent versions or tests. A failed build does not publish
an artifact as a successful release. Verify both local checks and an actual
GitHub CI run before claiming remote workflow setup is complete.

Installed plugin caches, private data, Stop capture and quality lifecycle
commands are outside this workflow. CI uses synthetic fixtures only and does
not create real Phase 5 quality evidence. Runtime version text contributes to
the existing provenance digest; release automation neither changes that guard
nor treats a version bump as proof of a quality improvement. Installing a
release remains a separate explicit local operation.

## Primary references

- [GitHub manual workflow execution](https://docs.github.com/en/actions/how-tos/manage-workflow-runs/manually-run-a-workflow)
- [GitHub workflow triggers and token behavior](https://docs.github.com/en/actions/how-tos/write-workflows/choose-when-workflows-run/trigger-a-workflow)
- [GitHub CLI release creation](https://cli.github.com/manual/gh_release_create)
- [Release Please action](https://github.com/googleapis/release-please-action)
