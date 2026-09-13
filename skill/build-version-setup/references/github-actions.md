# GitHub Actions setup reference

Use this reference when implementing GitHub Actions. Resolve the repository and branch from the current checkout. The commands below inspect configuration; substitute values only after discovering them.

```bash
git rev-parse --show-toplevel
git status --short --branch
git remote -v
gh repo view --json nameWithOwner,defaultBranchRef,url
gh workflow list
```

Read `.github/workflows` and existing release configuration before proposing files. Pass `--repo OWNER/REPO` to repository-aware `gh` commands after resolving the remote when more than one repository could be targeted. For GitHub Enterprise, use the observed host in `--repo HOST/OWNER/REPO` and pass `--hostname HOST` to `gh api`; REST paths still contain only `OWNER/REPO`. Check authentication without printing tokens. A GitHub Actions permission error and a local Git push missing OAuth `workflow` scope are different problems; diagnose the actual response before changing credentials.

## Keep version and build identity separate

Use the project's release/version tool. If it lacks one, a small helper can check and update exact current-version fields, leaving historical records untouched. The release version belongs in the package metadata; `GITHUB_RUN_ID`, `GITHUB_RUN_NUMBER`, `GITHUB_RUN_ATTEMPT` and the tested SHA belong in build metadata. Include attempt identity when artifacts from reruns could collide. This is an identifier scheme, not a mandate to rewrite the product's version format.

Archive tracked source with `git archive` when source is the deliverable. For compiled packages, use the actual build/pack command in a clean checkout of the selected revision, including the lockfile and required submodule/LFS inputs. Test the artifact itself, not only the source tree. Make source archives deterministic when identical retries must reproduce bytes; use native package integrity checks where appropriate. A CI artifact retention window is not a permanent release archive.

## Connect events deliberately

Read the current [GitHub trigger documentation](https://docs.github.com/en/actions/how-tos/write-workflows/choose-when-workflows-run/trigger-a-workflow) for the target GitHub host/version. At the 2026-09-13 review, `GITHUB_TOKEN`-created push/tag events normally do not trigger another workflow. Dispatch events are exceptions. Bot-created PR `opened`, `synchronize` and `reopened` events can produce runs awaiting approval; a repository writer starts them from the PR page. Do not misdiagnose an approval wait as a test failure or assume an older server behaves identically.

Publish in the validated workflow, use a supported dispatch, or retain the repository's existing authorized GitHub App strategy. Do not silently add a PAT to bypass trigger rules. For manual dispatch, the workflow must be present on the default branch before the UI entry point is usable.

For version-change publication, unchanged versions should skip. On first workflow installation, prevent an unintended release of the existing version. For tag-driven projects, verify the existing tag convention instead of adding a second version-change trigger.

## Permissions and retry behavior

- Give CI `contents: read` unless its actual task requires more. Grant `contents: write` and `pull-requests: write` only to jobs that need them; avoid repository-wide write defaults.
- Check the repository option allowing Actions to create PRs if the chosen flow creates them. A narrow configuration update can be part of authorized setup. Preserve branch protections and organizational constraints.
- Do not execute untrusted PR code under a privileged `pull_request_target` or downstream artifact-consumer context. Pass dynamic shell input through environment variables or structured arguments and validate it at the boundary.
- Serialize publishers without canceling an active publication. Check the current platform's concurrency semantics; `cancel-in-progress: false` alone does not guarantee that every pending release is retained.
- Retry only against the same tested revision. Peel annotated tags before comparing commits. A matching draft can accept missing assets only after existing asset content is verified; a conflicting tag or published asset must stop. Never force-move a release tag or use an asset overwrite flag as recovery.

When custom publishing is unavoidable, leave a small test covering an identical retry, a partial draft, a tag mismatch, an asset conflict and an API error. Use mocks or a disposable fixture, not a real public release as the test.

## Verify the platform result

Use an installed workflow validator, shell syntax checks and the project's tests. Read every validator diagnostic. If the validator lags a documented platform feature, identify the exact incompatibility and verify GitHub accepts the real file; do not disable validation wholesale.

Once authorized changes reach GitHub, inspect the run selected by workflow, event, branch/PR and commit:

```bash
gh run list --repo "$build_repo" --commit "$build_sha" --limit 20 \
  --json databaseId,workflowName,event,headSha,status,conclusion,url
gh run view "$build_run_id" --repo "$build_repo" --json headSha,event,status,conclusion,jobs,url
gh api "repos/$build_repo/actions/runs/$build_run_id/artifacts"
```

Set `build_repo`, `build_sha` and `build_run_id` from observed values. PR merge runs require checking their PR/merge revision relationship. For canceled, skipped or waiting runs, explain the actual state; none is proof that a build passed. Finish with the relevant run URL and artifact identity, and separate locally validated publication logic from a release that was actually published.

## Sources and limits

External skill reviewed on 2026-09-13: Microsoft's [beachball-change-file](https://github.com/microsoft/beachball/blob/e21fe2054d8cf47074a385e412d67b2379be8abd/skills/beachball-change-file/SKILL.md), supplied by the versioning tool's own maintainers. Its useful pattern is to discover the real repository and let its own change/version checker decide what is valid. The original skill and repository workflow were inspected; the repository's tool tests are not evidence that the skill itself passed a behavioral evaluation. This skill does not install Beachball or adopt its package layout, change-file policy or pre-1.0 rules in other projects.

The Skill Evolver workflow examined on 2026-09-13 is a worked example, not a required template: [verified source commit](https://github.com/gyungsubLee/skill-evolver/tree/b6b4c8b305c8f7977cd2755f74ed85453d1f0435), [CI evidence](https://github.com/gyungsubLee/skill-evolver/actions/runs/34704995747). Its source packaging and partial-release tests inform the invariants above; macOS, its file paths, version and retention period are project choices. CI passed; actual release publication was not exercised in that rollout.

Provider contracts: [build variables](https://docs.github.com/en/actions/reference/workflows-and-actions/variables), [artifact retention](https://docs.github.com/en/actions/tutorials/store-and-share-data), [manual dispatch](https://docs.github.com/en/actions/how-tos/manage-workflow-runs/manually-run-a-workflow), [workflow permissions](https://docs.github.com/en/actions/reference/workflows-and-actions/workflow-syntax#permissions).
