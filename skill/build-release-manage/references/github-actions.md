# GitHub Actions operations

Use these examples after resolving variables from repository files and API responses. Check local `gh --help` and the target GitHub host's documentation when behavior differs; workflow schemas and hosted/Enterprise features evolve. Commands here illustrate operations, not authorization to execute them.

The examples use `repo=OWNER/REPO` on github.com. For GitHub Enterprise, use the observed host in CLI `--repo HOST/OWNER/REPO` and pass `--hostname HOST` to `gh api`; keep `OWNER/REPO` in REST paths. Do not let a remembered/default host select a different repository.

## Select the correct run

Read repository identity and workflows before choosing an operation:

```bash
git rev-parse --show-toplevel
git status --short --branch
git remote -v
gh repo view --json nameWithOwner,defaultBranchRef,url
gh workflow list --repo "$repo"
gh workflow view "$workflow" --repo "$repo" --ref "$ref" --yaml
gh run list --repo "$repo" --workflow "$workflow" --commit "$sha" --json databaseId,workflowDatabaseId,headSha,headBranch,event,status,conclusion,url
gh run view "$run_id" --repo "$repo" --json databaseId,workflowDatabaseId,headSha,headBranch,event,attempt,status,conclusion,jobs,url
```

Use `gh pr view` to establish the intended PR head when relevant. A pull request run can test a merge commit; distinguish the PR head, event SHA, and checked-out build SHA from the workflow and logs. Do not silently replace one with another. Filter by event and ref as well as SHA when multiple runs match. Inspect returned fields rather than selecting the first result by workflow name.

Capture the attempt before reading evidence; reruns preserve a run ID while incrementing the attempt:

```bash
gh run view "$run_id" --repo "$repo" --attempt "$attempt" --log-failed
gh api "repos/$repo/actions/runs/$run_id/attempts/$attempt/jobs?per_page=100" --paginate
gh run view --repo "$repo" --job "$job_id" --log
```

A nonzero CLI exit can mean a failed run or a failed query. Read stderr and metadata. Empty jobs/logs do not prove an infrastructure outage or a green build. If a GitHub check links to an external CI provider, report that boundary and use the provider only when an available, authorized tool supports it.

## Retry versus new work

For a safe, authorized retry of existing failed jobs:

```bash
gh run rerun "$run_id" --repo "$repo" --failed
gh run view "$run_id" --repo "$repo" --json attempt,headSha,status,conclusion,url
```

Verify that the attempt advanced and the SHA remains the intended one. A run created before a code fix still uses its original SHA when rerun. Poll the explicit identity with reasonable intervals and progress updates; do not repeatedly rerun pending jobs.

For a new authorized release candidate, read the current workflow inputs first. This example applies only when the actual workflow declares a `bump` input with the selected value:

```bash
gh workflow run "$workflow" --repo "$repo" --ref "$ref" -f "bump=$bump"
```

Record dispatch time/ref/input and resolve the resulting run from returned data or a matching run query. Do not assume CLI success means the workflow finished or always returns a run ID. If dispatch times out, inspect matching runs and candidate branches/PRs before repeating it. If several candidates remain indistinguishable, resolve the ambiguity before another mutation.

## Explain absent checks accurately

Inspect trigger filters, workflow presence on the required branch, event actor/token, repository Actions policy, required reviews/environments, and actual run status. Keep these states distinct:

| Evidence | Conclusion/action |
| --- | --- |
| Completed failed job with logs | Diagnose that failure; fix or safe retry within scope. |
| Waiting for workflow/environment approval | Identify the exact approval; do not bypass it. |
| No matching run after a successful query | Inspect triggers and event rules; do not call it passing. |
| Auth, permission, network, or API error | State what could not be read; do not convert it to no checks. |
| Pending/cancelled/skipped run | Report its actual state and required next step. |

As documented on 2026-09-13, events produced with a repository's `GITHUB_TOKEN` generally do not trigger further runs, with documented exceptions including dispatch events. Bot-created pull-request `opened`, `synchronize`, and `reopened` events can create runs requiring approval. Verify the current target host/version and run evidence before deciding that checks were suppressed. Do not substitute a PAT, change repository permissions, or approve a run merely to make status appear green.

## Verify artifacts and recover a release

List artifacts from the selected run, not a repository-wide filename search:

```bash
gh api "repos/$repo/actions/runs/$run_id/artifacts?per_page=100" --paginate
gh run download "$run_id" --repo "$repo" --name "$artifact_name" --dir "$artifact_dir"
```

Record artifact ID/name, expiry, digest when supplied, run/SHA, and producing attempt from upload logs or provenance. The artifact API may not expose attempt identity, and `gh run download` is not an attempt selector. If the available evidence cannot prove that mapping, say so. A checksum manifest for an inner build ZIP is different from the digest of GitHub's outer artifact ZIP; compare each digest to its own bytes. Inspect downloads in a temporary directory without executing bundled code.

Before release recovery, inspect existing tags and all accessible releases, including drafts:

```bash
git ls-remote --tags "$remote" "refs/tags/$tag" "refs/tags/$tag^{}"
gh api "repos/$repo/releases?per_page=100" --paginate --slurp
```

For an annotated tag, the tag object's SHA is not the release commit; verify its peeled commit or follow the Git tag API until a commit object is reached. A draft may require appropriate access to appear. A tag-specific published-release lookup is insufficient to establish that no draft exists. Query failure is not absence.

Resume through the existing publisher only if it verifies tag/version/commit identity, validates every existing asset's bytes, uploads only missing draft assets, and refuses conflicts. Never use overwrite/clobber, delete/recreate, or a new bump to conceal a failed publication. For a published release, verify and report its existing assets; use a new version for corrected bytes under the project's release policy.

## Sources

Primary command and API references checked 2026-09-13:

- [GitHub CLI run list](https://cli.github.com/manual/gh_run_list), [run view and attempts](https://cli.github.com/manual/gh_run_view), [rerun](https://cli.github.com/manual/gh_run_rerun), [workflow dispatch](https://cli.github.com/manual/gh_workflow_run), and [artifact download](https://cli.github.com/manual/gh_run_download).
- [Workflow triggers and token event rules](https://docs.github.com/en/actions/how-tos/write-workflows/choose-when-workflows-run/trigger-a-workflow).
- [Actions artifact API](https://docs.github.com/en/rest/actions/artifacts), [release API and drafts](https://docs.github.com/en/rest/releases/releases), and [annotated tag API](https://docs.github.com/en/rest/git/tags).

External skill source review, with instructions independently written here:

- [OpenAI gh-fix-ci at `7796342`](https://github.com/openai/skills/blob/77963424cd7687fd52e5fcfdd3f08d826ab9b1ab/skills/.curated/gh-fix-ci/SKILL.md): useful failed-check/log inspection and external-provider boundaries. Official curated source and helper inspected; dedicated execution-test evidence was not found. Its separate approval step is not imported as an extra gate over existing user authorization.
- [Sentry iterate-pr at `aaa8515`](https://github.com/getsentry/skills/blob/aaa85152005936dddd0ea1451d791ff234f04771/skills/iterate-pr/SKILL.md) and [scenario specification](https://github.com/getsentry/skills/blob/aaa85152005936dddd0ea1451d791ff234f04771/skills/iterate-pr/SPEC.md): useful iteration and no-check distinctions. Source and scenarios were inspected, which does not establish executed evaluation results. This skill selects exact run/SHA/attempt and retains query errors; it does not adopt workflow-name-only failure selection or blind retry limits.
