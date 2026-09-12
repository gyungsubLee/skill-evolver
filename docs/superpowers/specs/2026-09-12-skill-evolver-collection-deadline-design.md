# Skill Evolver Collection Deadline Visibility

**Status:** Design A approved by the user on 2026-09-12, including design,
planning, TDD implementation and verification in the current project.

**Requirement:** QUALITY-01, Phase 5 remediation; this change cannot complete
the real quality gate.

## Scope and evidence

Q-006 terminalized INVALID for `quality_collection_expired` without a sample.
Installed 0.1.6 reports only INVALID, hiding the deadline and recovery action.
Keep the unchanged-provenance retry guard. Expose information already retained
in schema-v1 metadata through the existing read-only status command.

Option A is approved. Option B (a new immutable empty-sample witness permitting
same-provenance retries) requires a different schema/report design. Option C
(a version-only or documentation-only provenance change) is rejected.

The current repository and execution root is
`/Users/igyeongseob/Develop/10_herness/skill-evolver`. It is a standalone clone
of `https://github.com/gyungsubLee/skill-evolver`, initially at `81740cd`.
The former parent repository's `6b3d803178bb94ab5f9f9d1f3c8a67f2129eb488`
was checked read-only: its runtime files match this clone. Its five additional
handoff/state/Q-006 report files were copied byte-for-byte before updating
current paths. Historical commit IDs and immutable reports remain unchanged.

## Output contract

Keep `schema_version: 1` and every existing status field. Always add:

| Field | Meaning |
| --- | --- |
| `collection_expires_at` | Retained epoch UTC deadline, or null for IDLE/tombstone. It is historical after collection ends. |
| `remaining_seconds` | Only for stored `collecting` state: `max(0, ceil(deadline - now))`; otherwise null. Zero means the collection deadline has elapsed. A positive value does not override INVALID. |
| `invalid_reason` | Stored invalid reason, or a bounded read-only derived reason; otherwise null. Tombstones no longer retain this reason. |
| `next_action` | Advisory enum below; never a command, approval, or claim that mutation preconditions pass. |

Do not add notifications, timers, polling, workers, dependencies, schema
migrations, new commands, label changes, or terminal-report changes.

## Invalid reason precedence

Retained terminal results and persisted INVALID reasons take precedence and
are never reinterpreted under current provenance. For validly parsed active
epochs, evaluate the same reasons as the gate, in this order:

1. Collecting deadline reached: `quality_collection_expired`.
2. Sealed label deadline reached: `quality_label_expired`.
3. Provenance cannot be read/validated: `quality_source_corrupt`.
4. Provenance differs: `quality_provenance_drift`.
5. Sealed witness mismatch or validation failure: `quality_source_corrupt`.
6. Sealed candidate subject differs: `quality_candidate_subject_changed`;
   subject validation failure instead means `quality_source_corrupt`.

Replace the status-only boolean sealed-source helper with a reason-returning
helper. Reuse current provenance, witness and subject validators. Leave gate,
seal, label, predecessor and storage validation unchanged. Malformed epoch,
observation or label inventories continue to raise fail-closed errors rather
than generating guessed status output.

## Advisory actions

| Situation | `next_action` |
| --- | --- |
| IDLE | `open_quality_epoch` |
| Valid COLLECTING with fewer than 10 distinct sessions or no candidate | `collect_real_sessions` |
| Valid COLLECTING with at least 10 distinct sessions and a candidate | `request_quality_seal` |
| AWAITING_LABELS | `request_user_labels` |
| READY_TO_GATE | `run_quality_gate` |
| INVALID without an immutable terminal report | `run_quality_gate` |
| Retained terminal PASS/FAIL/INVALID | Copy the immutable report's `next_action` (`begin_phase_6_evaluate_runner_spike` or `open_changed_quality_epoch`). |
| Tombstone or SUPERSEDED | `inspect_quality_history` |

`request_quality_seal` is only a sample-count hint. The explicit seal still
checks active batches, high-water coverage, audits and all other invariants.
`open_changed_quality_epoch` requires a substantive reviewed change, installed
parity, genuine Stop ingress, exact predecessor approval, unexpired private
lineage, and capacity below the existing eight-epoch cap. It does not assert
those preconditions. A PASS only permits refreshing and then executing the
Phase 6 runner spike; it does not enable Evaluate or Apply.

## Release and project relocation

Publish source version 0.1.7 in the manifest, runtime JSON and both runtime
version pins. Advance quality contract version 2 to 3 and bind these status
semantics in a `status_output` object. Runtime and quality-contract digests
must change because functionality changes. Keep retry fields, database schema,
thresholds, TTLs, Hook, transcript adapter and improvement policy unchanged.

Place the local marketplace at `.agents/plugins/marketplace.json` in this
standalone repository, with plugin source `./`. Update current README and
planning navigation paths; old execution plans and aggregate report provenance
remain historical. The production installation and plugin-data roots do not
move. Re-register the same marketplace name at the current project and install
through the Codex CLI, with filesystem approval as required; do not edit cache
or installed skill files directly.

## Verification and remaining live gate

Use existing temporary-directory unittest fixtures. Prove idle, collecting,
fractional/exact expiry, stored and derived invalidity, sealed labels,
terminal replay and tombstone behavior. Preserve database bytes and forbid
transcript reads around status. Add an expired partial-sample unchanged-retry
regression. Verify contract/version pins, all tests, compilation and diff
checks, then independent review.

Compare source/cache/install across the existing seven-file release set.
Real read-only status may inspect aggregates only. No raw private transcript,
observation, database or spool content is opened by the agent.

Q-006 remains immutable INVALID. Before Q-007, prove a fresh genuine Stop
reaches the installed spool and obtain separate approval for the fully literal
`quality-open` command and exact production data root, with predecessor
`Q-006@c06087eff41522ee6c76dd584a0009aaefccffad572a0e49867a36492014c3d3`.
Every live Skill Evolver mutation requires its own approval. Only the user
enters labels in an external TTY. Fixtures, subagents, repeated generations
and old sessions never count as real quality evidence. Phase 5 stays open and
Phase 6 stays blocked until a genuine successor produces immutable PASS.
