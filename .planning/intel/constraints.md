# Constraints

## Skill Evolver 설계
- source: /Users/igyeongseob/Documents/오픈소스/skill-evolver/docs/superpowers/specs/2026-07-26-skill-evolver-design.md
- type: protocol
- content: A central matcher-free Stop Hook records metadata only. An argument-free `$skill-evolver` call is status-only; transcripts are read only after explicit `review`. `prepare` does not execute candidate content, `evaluate` is bound to an immutable full specification digest, and apply or undo mutation requires an exact command and digest/hash entered in an external terminal. Automatic review, evaluation, apply, Git publication, and transcript replication are excluded.

## Skill Evolver Feasibility Spike Implementation Plan
- source: /Users/igyeongseob/Documents/오픈소스/skill-evolver/docs/superpowers/plans/2026-07-26-skill-evolver-feasibility-spike.md
- type: protocol
- content: The spike is limited to current macOS Codex CLI and Desktop Stop observations, fixed-root access, and bounded transcript turn/provenance reconstruction. It requires two independent observations per surface, sanitized structure-only fixtures, and an installed-skill preflight under default `workspace-write` without elevation. If either surface cannot share the private root or cannot map a captured turn to bounded provenance, the gate fails and the design must change to session-level capture before Read-only MVP work begins.

## Skill Evolver Implementation Roadmap
- source: /Users/igyeongseob/Documents/오픈소스/skill-evolver/docs/superpowers/plans/2026-07-26-skill-evolver-implementation-roadmap.md
- type: protocol
- content: Delivery is an ordered ten-plan sequence from Feasibility through Read-only, Evaluate, Apply, Undo, and Hardening. Each child plan requires the exact upstream PASS artifact. A Feasibility failure routes to a session-level capture design amendment and does not authorize Runtime Queue implementation; later quality, runner, evaluation, apply, undo, or hardening failures similarly stop advancement. PostgreSQL, background workers, scheduled review, automatic evaluation/apply, external services, web UI, and automated Git publication remain outside the roadmap.

## Skill Evolver Read-only Runtime Queue Implementation Plan
- source: /Users/igyeongseob/Documents/오픈소스/skill-evolver/docs/superpowers/plans/2026-07-26-skill-evolver-read-only-runtime-queue.md
- type: schema
- content: Runtime Queue requires a Feasibility PASS. It uses the fixed private data root `/Users/igyeongseob/.codex/skill-evolver`, SQLite WAL schema v1, HMAC event deduplication, a 200-file/10-MiB bounded spool, 14-day/200-turn pending limits, and transcript-free status. The Stop Hook accepts at most 64 KiB, emits no output, performs no model or network call, and never mutates installed skills.

## Skill Evolver Read-only Review Inbox Implementation Plan
- source: /Users/igyeongseob/Documents/오픈소스/skill-evolver/docs/superpowers/plans/2026-07-26-skill-evolver-read-only-review-inbox.md
- type: protocol
- content: Review starts only after explicit `$skill-evolver review`. The adapter reads no-follow transcript prefixes within captured device, inode, size, and allowlisted-root boundaries. A batch is limited to 5 sessions, 20 turns, 2 MiB/100 records per session, and 8 MiB total; it creates at most one candidate per session and three new fingerprints per batch. External content, environment failures, one-off work, uncertain attribution, unsupported targets, and privacy failures are excluded.

## Skill Evolver Read-only Quality Gate Implementation Plan
- source: /Users/igyeongseob/Documents/오픈소스/skill-evolver/docs/superpowers/plans/2026-07-26-skill-evolver-read-only-quality-gate.md
- type: nfr
- content: The Read-only quality gate keeps schema v1 and requires deterministic retention, human candidate labels, and digest-bound external-TTY privacy purge. PASS requires at least 10 reviewed sessions or 30 reviewed turns, evaluation-worth rate at least 0.50, target-skill misattribution at most 0.20, zero external-content adoption incidents, every candidate labeled, and recorded policy and adapter digests.

## Skill Evolver Evaluate Runner Spike Implementation Plan
- source: /Users/igyeongseob/Documents/오픈소스/skill-evolver/docs/superpowers/plans/2026-07-26-skill-evolver-evaluate-runner-spike.md
- type: nfr
- content: The runner spike starts only after the Read-only quality report passes its sample and attribution thresholds. It pins Codex CLI 0.145.0, model `gpt-5.6-sol`, reasoning effort `low`, read-only sandbox flags, credential stripping, fixed JSON output, and an external supervisor. Each invocation is limited to 120 seconds, 16 descendant processes, 1 GiB resident memory, 16 MiB writable output, and 1 MiB input. Two independent fixed-input runs must return the exact expected result before Prepare is authorized.

## Skill Evolver Evaluate Prepare Implementation Plan
- source: /Users/igyeongseob/Documents/오픈소스/skill-evolver/docs/superpowers/plans/2026-07-26-skill-evolver-evaluate-prepare.md
- type: protocol
- content: Prepare requires both a Read-only quality PASS and a current runner-contract PASS. It migrates schema v1 to v2, resolves only allowlisted user-owned targets, creates a torn-copy-safe immutable base, applies at most eight SHA-bound declarative text operations, and seals base, candidate, harness, holdouts, runner, model, sandbox, and resource inputs into one canonical evaluation specification. Candidate content is never executed, unsupported executable or security-boundary changes fail closed, and the installed source must remain unchanged.

## Skill Evolver Evaluate Execution Implementation Plan
- source: /Users/igyeongseob/Documents/오픈소스/skill-evolver/docs/superpowers/plans/2026-07-26-skill-evolver-evaluate-execution.md
- type: protocol
- content: Evaluation requires the exact full prepared-spec SHA-256, revalidates all immutable artifacts and installed-source drift, and leases one evaluation. Base, candidate, and blind grader run in separate ephemeral contexts. Each invocation is bounded to 120 seconds, 16 processes, 1 GiB RSS, 16 MiB output, and 1 MiB input. Any missing, malformed, changed, denied, crashed, or resource-breaching case fails closed; readiness never modifies the installed skill.

## Skill Evolver Apply and Versioning Implementation Plan
- source: /Users/igyeongseob/Documents/오픈소스/skill-evolver/docs/superpowers/plans/2026-07-26-skill-evolver-apply-versioning.md
- type: protocol
- content: Apply starts only after upstream PASS reports and owns the schema-v2-to-v3 apply journal and version lineage. Chat produces a non-mutating preview; mutation is manual-terminal-only and bound to the complete evaluation ID and digest. The executor resolves an allowlisted target, locks it, checks drift and capacity, verifies snapshots and manifests, performs journaled same-filesystem renames with directory fsync, validates the result, and rolls back on failure. General apply accepts only evaluated non-executable text changes.

## Skill Evolver Undo and Recovery Implementation Plan
- source: /Users/igyeongseob/Documents/오픈소스/skill-evolver/docs/superpowers/plans/2026-07-26-skill-evolver-undo-recovery.md
- type: protocol
- content: Undo consumes schema v3 without migration and restores an apply version's verified parent snapshot, not the selected version's candidate content. Mutation requires an external TTY and the complete expected-current SHA-256. Locking, pre-undo snapshot, same-filesystem swap, durable journal, validation, and rollback are mandatory. Ambiguous recovery evidence produces `recovery_required` without filesystem mutation; at most five available snapshots per target are retained while lineage remains.

## Skill Evolver Hardening Implementation Plan
- source: /Users/igyeongseob/Documents/오픈소스/skill-evolver/docs/superpowers/plans/2026-07-26-skill-evolver-hardening.md
- type: nfr
- content: Hardening requires every upstream release report to PASS and adds no production schema, dependency, Hook, permission, or network path. It uses exactly eight synthetic transcript fixtures, 9 should-trigger and 9 should-not-trigger cases run three times, behavior fixtures run at least three times, attribution accuracy of at least 0.80, and zero prompt-injection adoption or high-risk auto-apply. It also runs a 10,000-event soak with 8 workers, crash-boundary recovery suites, and a disposable end-to-end lifecycle before emitting the canonical hardening report.
