# Skill Evolver Session Handoff

Updated: 2026-09-12

## Resume Point

The project is in M1 Phase 5 of 11. Phases 1 through 4 are complete. Q-006 is
immutable terminal `INVALID` for `quality_collection_expired`, and Phase 6 is
blocked. Design A was approved, implemented, tested and installed as 0.1.7 on
2026-09-12. Source/cache parity is verified. Resume by proving fresh genuine
post-install Stop ingress, then requesting exact-command approval to open
Q-007. Never open a successor against unchanged predecessor provenance.

Repository root: `/Users/igyeongseob/Develop/10_herness/skill-evolver`

Git repository root: `/Users/igyeongseob/Develop/10_herness/skill-evolver` (standalone clone)

The former local parent commit `6b3d803178bb94ab5f9f9d1f3c8a67f2129eb488`
was verified read-only. GitHub's standalone history starts this task at
`81740cd75d5a15820b2ff76a1b1b10908241215f`, with identical runtime content.
Five missing handoff/state/Q-006 report files were recovered before editing
current navigation. The old parent repository is no longer an execution root.
Historical plans retain their original paths and commit IDs as evidence;
resolve any future command against this current root before execution.

Installed plugin: `skill-evolver 0.1.7`

Frozen implementation commit: `4d535cb`

Historical Q-006 implementation commit: `980118fcddce4f8b5271f82638c3f9d131bf0e7f`

## Product Shape Inherited From Hermes

Hermes Agent supplied the self-improvement-loop idea, but this project keeps
the permanent-change boundary manual:

```text
Codex Stop
  -> signed metadata-only plugin-data spool
  -> explicit maintain into private SQLite
  -> explicit $skill-evolver review
  -> candidate inbox
  -> future prepare/evaluate
  -> user-only external-TTY apply/undo
```

This is option 2 from the original discussion plus only the collection layer
of option 3. The Stop Hook does not invoke a model, review a transcript, emit a
chat message, or modify a skill. PostgreSQL, a background worker, scheduled
review, automatic evaluation, and automatic apply are non-goals.

## Design And Implementation History

1. The 2026-07-26 base design centralized metadata collection and required
   explicit review/evaluate/apply.
2. Phase 1 completed its probes but the product gate was `FAIL`: turn identity
   and symmetric fixed-root access did not work across CLI and Desktop.
3. Phase 2 changed the unit to a session generation with asymmetric access;
   the amended cross-surface gate passed.
4. Phase 3 implemented a bounded standard-library SQLite queue and signed
   plugin-data spool.
5. Phase 4 implemented bounded explicit Review and the candidate inbox.
6. Codex 0.146 transcript support, Korean-first quality labels, causal target
   attribution, Desktop transcript rebinding, and prospective epoch cutoff
   were added through releases 0.1.3 to 0.1.6.
7. Q-003 was the only labeled quality run. Its candidate was user-labeled
   `evaluation_worthy=false`, `target_correct=false`, and
   `external_content_adoption=false`, so the gate failed and attribution was
   tightened.
8. Q-004 and Q-005 invalidated on provenance drift. Q-006 opened under 0.1.6
   but collected no eligible real sessions before its 30-day expiry.

## Reference Index

Use this index as the navigation source. Read **Start Here** and the current
Phase 5 sources before acting. Open historical or future-phase sources only
when the current decision depends on them.

### Start Here

- [Project definition](PROJECT.md)
- [Requirements and phase traceability](REQUIREMENTS.md)
- [M1 roadmap and gates](ROADMAP.md)
- [Current execution state](STATE.md)
- [Planning-document synthesis](intel/SYNTHESIS.md)
- [Ingested-document conflicts and precedence](INGEST-CONFLICTS.md)
- [Project README](../README.md)
- [Upstream Hermes Agent](https://github.com/NousResearch/hermes-agent)

### Origin And Overall Architecture

- [Hermes-derived Skill Evolver design](../docs/superpowers/specs/2026-07-26-skill-evolver-design.md)
- [Original eleven-stage implementation roadmap](../docs/superpowers/plans/2026-07-26-skill-evolver-implementation-roadmap.md)

### Phase 1 — Feasibility Spike

- [Original feasibility execution plan](../docs/superpowers/plans/2026-07-26-skill-evolver-feasibility-spike.md)
- [GSD Phase 1 plan](phases/01-feasibility-spike/01-01-PLAN.md)
- [GSD Phase 1 summary](phases/01-feasibility-spike/01-01-SUMMARY.md)
- [GSD Phase 1 verification](phases/01-feasibility-spike/01-VERIFICATION.md)
- [Initial FAIL report](../docs/feasibility-report.md)
- [Initial FAIL report data](../docs/feasibility-report.json)

### Phase 2 — Session-Level Capture Amendment

- [Session-level amendment design](../docs/superpowers/specs/2026-07-28-skill-evolver-session-capture-amendment-design.md)
- [Session-level amendment plan](../docs/superpowers/plans/2026-07-28-skill-evolver-session-capture-amendment.md)
- [GSD Phase 2 plan](phases/02-session-level-capture-design-amendment/02-01-PLAN.md)
- [GSD Phase 2 summary](phases/02-session-level-capture-design-amendment/02-01-SUMMARY.md)
- [GSD Phase 2 verification](phases/02-session-level-capture-design-amendment/02-VERIFICATION.md)
- [Amended PASS report](../docs/feasibility-report-v2.md)
- [Amended PASS report data](../docs/feasibility-report-v2.json)

### Phase 3 — Runtime Queue

- [Session runtime queue plan](../docs/superpowers/plans/2026-07-28-skill-evolver-session-runtime-queue.md)
- [Plugin-data spool design](../docs/superpowers/specs/2026-07-30-skill-evolver-plugin-data-spool-design.md)
- [Plugin-data spool plan](../docs/superpowers/plans/2026-07-30-skill-evolver-plugin-data-spool.md)
- [GSD Phase 3 plan](phases/03-runtime-queue/03-01-PLAN.md)
- [GSD Phase 3 summary](phases/03-runtime-queue/03-01-SUMMARY.md)
- [GSD Phase 3 verification](phases/03-runtime-queue/03-VERIFICATION.md)
- [Runtime Queue PASS report](../docs/release-reports/runtime-queue.json)

### Phase 4 — Review And Inbox

- [Session Review/Inbox design](../docs/superpowers/specs/2026-07-29-skill-evolver-session-review-inbox-design.md)
- [Session Review/Inbox plan](../docs/superpowers/plans/2026-07-29-skill-evolver-session-review-inbox.md)
- [Review contract adapters plan](../docs/superpowers/plans/2026-07-29-skill-evolver-review-contract-adapters.md)
- [Review batch export plan](../docs/superpowers/plans/2026-07-29-skill-evolver-review-batch-export.md)
- [Candidate inbox plan](../docs/superpowers/plans/2026-07-29-skill-evolver-review-candidate-inbox.md)
- [Review surface release plan](../docs/superpowers/plans/2026-07-29-skill-evolver-review-surface-release.md)
- [GSD Phase 4 plan](phases/04-review-and-inbox/04-01-PLAN.md)
- [GSD Phase 4 summary](phases/04-review-and-inbox/04-01-SUMMARY.md)
- [GSD Phase 4 verification](phases/04-review-and-inbox/04-VERIFICATION.md)
- [Review/Inbox PASS report](../docs/release-reports/review-inbox.json)

### Phase 5 — Read-Only Quality Gate And Remediations

- [Approved collection-deadline design](../docs/superpowers/specs/2026-09-12-skill-evolver-collection-deadline-design.md)
- [Collection-deadline implementation plan and execution evidence](../docs/superpowers/plans/2026-09-12-skill-evolver-collection-deadline.md)
- [Quality gate design](../docs/superpowers/specs/2026-07-30-skill-evolver-session-quality-gate-design.md)
- [Quality gate implementation plan](../docs/superpowers/plans/2026-07-30-skill-evolver-session-quality-gate.md)
- [GSD Phase 5 plan](phases/05-read-only-quality-gate/05-01-PLAN.md)
- [Codex 0.146 transcript adapter design](../docs/superpowers/specs/2026-07-31-skill-evolver-codex-0146-transcript-adapter-design.md)
- [Codex 0.146 transcript adapter plan](../docs/superpowers/plans/2026-07-31-skill-evolver-codex-0146-transcript-adapter.md)
- [Korean-first quality-label design](../docs/superpowers/specs/2026-08-02-skill-evolver-quality-label-localization-design.md)
- [Korean-first quality-label plan](../docs/superpowers/plans/2026-08-02-skill-evolver-quality-label-localization.md)
- [Target-attribution remediation design](../docs/superpowers/specs/2026-08-04-skill-evolver-target-attribution-remediation-design.md)
- [Target-attribution remediation plan](../docs/superpowers/plans/2026-08-04-skill-evolver-target-attribution-remediation.md)
- [Desktop transcript rebinding design](../docs/superpowers/specs/2026-08-06-skill-evolver-desktop-transcript-rebinding-design.md)
- [Desktop transcript rebinding plan](../docs/superpowers/plans/2026-08-06-skill-evolver-desktop-transcript-rebinding.md)
- [Prospective capture cutoff design](../docs/superpowers/specs/2026-08-11-skill-evolver-prospective-capture-cutoff-design.md)
- [Prospective capture cutoff plan](../docs/superpowers/plans/2026-08-11-skill-evolver-prospective-capture-cutoff.md)
- [Q-003 FAIL report](../docs/release-reports/quality/Q-003-512aa6cb395e7a4d6c94a51ad2b9950fb8cada381381784370d3edecda01ac1a.json)
- [Q-004 INVALID report](../docs/release-reports/quality/Q-004-a9d30a50776d7e1ad2b0f56d5be741fda8b9460f46090a88e1e18777e5f0e917.json)
- [Q-005 INVALID report](../docs/release-reports/quality/Q-005-781dbac0ba4e8e601ce6475e408204865127e5fd16a961e66cf47afc38a94c2f.json)
- [Q-006 expiry INVALID report](../docs/release-reports/quality/Q-006-c06087eff41522ee6c76dd584a0009aaefccffad572a0e49867a36492014c3d3.json)

Q-001 and Q-002 have no standalone report files in the repository; their
terminal lineage is preserved in the later immutable reports. Phase 5 has no
SUMMARY or VERIFICATION file because it has not passed.

### Future Phase Plans — Refresh Before Execution

These plans define intended scope but predate the current runtime. Phase 6 in
particular pins obsolete Codex 0.145 behavior and must be refreshed after Phase
5 passes.

- [Phase 6 — Evaluate Runner Spike](../docs/superpowers/plans/2026-07-26-skill-evolver-evaluate-runner-spike.md)
- [Phase 7 — Evaluate Prepare](../docs/superpowers/plans/2026-07-26-skill-evolver-evaluate-prepare.md)
- [Phase 8 — Evaluate Execution](../docs/superpowers/plans/2026-07-26-skill-evolver-evaluate-execution.md)
- [Phase 9 — Apply And Versioning](../docs/superpowers/plans/2026-07-26-skill-evolver-apply-versioning.md)
- [Phase 10 — Undo And Recovery](../docs/superpowers/plans/2026-07-26-skill-evolver-undo-recovery.md)
- [Phase 11 — Hardening](../docs/superpowers/plans/2026-07-26-skill-evolver-hardening.md)

### Superseded Initial Plans — Historical Evidence Only

- [Initial turn-level Runtime Queue plan](../docs/superpowers/plans/2026-07-26-skill-evolver-read-only-runtime-queue.md)
- [Initial turn-level Review/Inbox plan](../docs/superpowers/plans/2026-07-26-skill-evolver-read-only-review-inbox.md)
- [Initial read-only Quality Gate plan](../docs/superpowers/plans/2026-07-26-skill-evolver-read-only-quality-gate.md)

### Installed Runtime Contract

- [Plugin manifest](../.codex-plugin/plugin.json)
- [Stop Hook definition](../hooks/hooks.json)
- [Skill operating instructions](../skills/skill-evolver/SKILL.md)
- [Runtime source contract](../skills/skill-evolver/references/runtime.json)
- [Improvement review policy](../skills/skill-evolver/references/improvement-policy.md)
- [Runtime implementation](../skills/skill-evolver/scripts/evolver.py)

## GSD Milestone

M1 includes all eleven phases and the final milestone audit:

| Phase | State | Gate |
|---|---|---|
| 1. Feasibility Spike | Complete | Initial product gate `FAIL`; input to Phase 2 |
| 2. Session-Level Capture Amendment | Complete | `PASS` |
| 3. Runtime Queue | Complete | `PASS` |
| 4. Review and Inbox | Complete | `PASS` |
| 5. Read-only Quality Gate | In progress | Q-006 `INVALID`; successor required |
| 6. Evaluate Runner Spike | Blocked | Requires Phase 5 `PASS` |
| 7. Evaluate Prepare | Not started | Requires Phase 6 `PASS` |
| 8. Evaluate Execution | Not started | Requires Phase 7 `PASS` |
| 9. Apply and Versioning | Not started | Requires Phase 8 `PASS` |
| 10. Undo and Recovery | Not started | Requires Phase 9 `PASS` |
| 11. Hardening | Not started | Requires Phase 10 `PASS` |

The current GSD completion measure is 4 of 11 phases, not 80 percent. The
80-percent figure seen previously counted four completed plans among only the
five plans registered so far.

## Latest Runtime Evidence

- 0.1.7 source/cache/installed identity verified at the current project path.
- Seven production file pairs and packaged Phase 4 report match.
- Full suite: 554 run, 551 passed, 3 historical skips, zero failures.
- Focused quality RED: 109 run, 30 expected assertion failures; GREEN: 109 passed.
- Independent specification/code quality and security/privacy reviews: CLEAN.
- Runtime SHA-256: `ad354b892dba9ac8bbea68aa95697e183617d28e99e2f232105d5350cb7bac8b`.
- Quality contract v3 SHA-256: `bb603f49e949b7c7d3fb0c0b768fa711ab52c378e33fc593c82b162888f1a540`.
- Installed read-only status at `2026-09-12T11:44:39Z`: spool 0, pending 0,
  claimable 0, quarantined 0, cleanup overdue 0. Fresh Stop proof is pending.
- Installed quality-status: Q-006 INVALID; collection deadline
  `2026-09-10T14:53:34Z`; remaining seconds null; invalid reason
  `quality_collection_expired`; action `open_changed_quality_epoch`.
- No live maintenance, Review, quality-open/seal/label/gate command ran in this
  task. Plugin registration/install used the Codex CLI after filesystem approval.

Historical Q-006 terminal and cleanup evidence:

- [Q-006 terminal report](../docs/release-reports/quality/Q-006-c06087eff41522ee6c76dd584a0009aaefccffad572a0e49867a36492014c3d3.json)
- Report digest:
  `c06087eff41522ee6c76dd584a0009aaefccffad572a0e49867a36492014c3d3`
- Decision: `INVALID`
- Reason: `quality_collection_expired`
- Sample: 0 sessions, 0 candidates, 0 labels
- 2026-09-12 maintenance: expired pending 128; expired spool 171; redacted
  overdue raw metadata 22; candidates/labels/quality observations deleted 0.
- Post-maintenance aggregate: spool 0, pending 0, claimable 0, quarantined 0,
  cleanup overdue 0.

Only aggregates and immutable reports were inspected. Raw transcript,
observation, database, and spool contents were not opened.

## Exact Next Work

1. Design A, its plan, TDD implementation, independent reviews and 0.1.7
   source/cache/install verification are complete. The unchanged-provenance
   guard remains unchanged. Option B was not selected; version-only churn
   remains rejected.
2. Prove a fresh genuine post-install Stop reaches the authenticated spool.
   The last read-only installed check had `spool.verified_files=0`, so ingress
   is not yet proved. Do not manufacture a session or invoke enqueue-stop for
   this proof. Use a normal user task and read-only aggregate status afterward.
3. Only after that proof, obtain separate approval for this exact invocation
   and data root `/Users/igyeongseob/.codex/skill-evolver`:

```bash
/usr/bin/python3 -I /Users/igyeongseob/.codex/plugins/cache/skill-evolver-dev/skill-evolver/0.1.7/skills/skill-evolver/scripts/evolver.py quality-open --installation /Users/igyeongseob/.codex/skill-evolver/installation.json --predecessor Q-006@c06087eff41522ee6c76dd584a0009aaefccffad572a0e49867a36492014c3d3
```

4. Collect at least ten genuine distinct post-open sessions, run separately
   approved explicit Review, seal, and have the user enter every label in an
   external terminal. Every lifecycle command retains separate approval.
5. Run the immutable quality gate. Only real PASS completes Phase 5.
6. Refresh the old Phase 6 runner plan for the current Codex runtime before
   implementation. The installed CLI checked in this task is 0.154.0; the old
   plan pins obsolete 0.145 behavior. Do not infer adapter compatibility from
   CLI version alone.

Rejected shortcut: exempting every `quality_collection_expired` predecessor
from the unchanged-provenance guard. The terminal report does not prove that no
unsealed observations existed, so that shortcut would allow intentional expiry
to cherry-pick a new sample.

## Safety Boundaries

- Never inspect or quote raw private session content for status or handoff.
- Never count fixtures, subagents, repeated generations, or historical rows as
  real quality sessions.
- Never let the agent enter `quality-label` answers; the user does this in an
  external TTY.
- Every Skill Evolver mutation command needs separate approval for its exact
  literal command and data root.
- Never auto-apply a candidate or edit an installed user skill before a future
  evaluation `PASS` and explicit external-TTY apply.

## New Session Bootstrap

Use this prompt in the new Codex task:

> Continue Skill Evolver M1 from
> `/Users/igyeongseob/Develop/10_herness/skill-evolver/.planning/HANDOFF.md`.
> Read the handoff's **Reference Index**, then fully read **Start Here** and
> the current **Phase 5** sources before acting. Use historical and future
> links as phase-scoped evidence rather than loading them all blindly. Verify current Git,
> installed plugin, read-only `status`, and `quality-status` without opening
> raw session/spool/database content. Design A is implemented and version 0.1.7
> is installed from the current project. Verify fresh genuine post-install Stop
> ingress, then obtain separate approval for the exact Q-007 opening command
> recorded above. Do not weaken the unchanged-provenance guard,
> do not run Review when claimable is zero, and do not enter user quality
> labels.
