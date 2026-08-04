# Skill Evolver Target Attribution Remediation Design

**Date:** 2026-08-04

**Status:** Approved by the standing milestone-m1 auto-approval instruction

**Requirement:** QUALITY-01 failure remediation

## 1. Outcome

Preserve `Q-003` as an immutable terminal `FAIL`, tighten Phase 4 Review so a
catalog match cannot by itself justify a target skill, and release the already
implemented localization together with this policy correction as `0.1.4`.
Only that changed installation may open successor epoch `Q-004`.

This design does not weaken the Phase 5 thresholds, relabel `C-001`, or unlock
Phase 6 before a successor epoch passes.

It supersedes only the Q-003-PASS release and deployment boundary in
`2026-08-02-skill-evolver-quality-label-localization-design.md`. The completed
localization behavior, label safety contract, and authoring-language decisions
remain approved and unchanged.

## 2. Failure Evidence

`Q-003` sealed 12 distinct real sessions and candidate `C-001`. The user-owned
label recorded:

- `evaluation_worthy=false`;
- `target_correct=false`;
- `external_content_adoption=false`.

The unchanged `0.1.3` gate therefore returned terminal `FAIL` with report
digest
`512aa6cb395e7a4d6c94a51ad2b9950fb8cada381381784370d3edecda01ac1a`.
Sample size, labels, provenance, subject integrity, and external-content
adoption passed. Evaluation-worth and target-misattribution ratios failed.
The canonical next action is `open_changed_quality_epoch`.

## 3. Root Cause

The current Python validator proves that a proposed `target_identity` belongs
to the bounded user-skill catalog. It independently proves that evidence comes
from an eligible record and signal/source pair. It does not bind that evidence
to use of the target skill.

The Review envelope contains user, assistant, and tool-output records plus a
catalog of skill identities and descriptions. The transcript adapter ignores
call records and exposes no stable, trusted skill-invocation event. As a
result, the model could select `user-skill:brainstorming` because its catalog
description appeared related to the work even though the session did not
establish that its instructions caused the rework.

This is a policy-quality failure, not a queue, label, or quality-gate mechanics
failure. Phase 4 deliberately left semantic attribution to the model policy
and Phase 5 exists to measure that judgment.

## 4. Options Considered

### 4.1 Strengthen policy plus one bounded validator invariant — selected

Make causal attribution and reusable value explicit candidate gates. Require a
bounded `catalog-inspect` of the proposed target before producing any
candidate. Exclude ambiguous cases with the existing enums. Add one shared
validator guard limiting a result to three distinct candidate targets so the
workflow's approval bound is deterministic even when results merge existing
fingerprints.

This is the smallest change that follows the designed Phase 5 failure route.
It changes policy and runtime provenance without adding persistence fields or
claiming evidence the transcript does not contain.

### 4.2 Bind explicit user-mentioned targets in the contract

Python could derive exact skill mentions from user records and permit only
those targets. This is stronger than a policy-only rule but would reject valid
improvements for automatically selected skills and proves a mention, not
actual causal use. Reserve it for a later failure if the policy correction is
insufficient.

### 4.3 Capture a trusted skill-invocation event

A future transcript-adapter contract could bind a canonical target identity to
the session. Current Codex transcripts provide no verified stable event for
this purpose, so implementing it now would require a separate feasibility
spike and reopening the Phase 2–4 data contract.

## 5. Review Decision Contract

The database and persisted candidate schemas remain unchanged. Task 4 security
review corrected the earlier assumption that the ephemeral declarative result
shape could remain unchanged: it now contains one top-level
`target_inspection_proofs` map. Its schema version remains 1 because every
result is bound to the exact live contract digest and the validator requires
the exact top-level key set; an older three-key result therefore fails closed.
The exact top-level keys are `schema_version`, `contract_digest`,
`target_inspection_proofs`, and `sessions`.
The Review policy, fixed result instructions, and skill workflow must apply
this decision order:

1. Establish one existing strong signal using the current eligible
   signal/source rules.
2. Identify the exact skill whose instructions were used to produce the
   behavior. Never infer use from a catalog name, description, topical
   similarity, or from the fact that a skill would have been useful.
3. If the session does not unambiguously establish that exact target was used,
   return `excluded: attribution_uncertain`.
4. A declarative batch result may contain at most three distinct candidate
   target identities. This is independent of the existing limit of three new
   fingerprints; Python rejects a larger result before mutation.
5. Before returning a candidate, use one separately approved bounded
   `catalog-inspect` for each distinct proposed target, with the exact live
   batch ID and owner token. Reuse that inspected content for candidates with
   the same target in the same live batch. The read returns an installation
   HMAC bound to the batch ID, owner-token digest, target identity, and current
   skill SHA-256, plus the non-secret owner digest needed to authenticate the
   exact response during later transcript parsing. Copy exactly one proof per distinct target into
   `target_inspection_proofs`; missing and extra entries fail closed. The new
   distinct-target validator bound permits at most three separately approved
   commands per batch. Confirm that an existing instruction,
   omission, or ambiguity in each target plausibly caused the observed
   behavior and that the proposed change belongs in that skill.
6. If target inspection is unavailable or does not establish that connection,
   return `excluded: attribution_uncertain`.
7. Confirm the proposal is a reusable skill-level instruction that would
   prevent recurrence in materially different future tasks. A generic best
   practice, project-only preference, or one-session wording improvement is
   `excluded: no_reusable_improvement` or `excluded: one_off` as applicable.
8. Only then emit one candidate. A strong signal alone never authorizes target
   selection.

The policy and fixed instructions repeat the same gates so the standalone
model input and the human-operated skill workflow cannot diverge.

## 6. Safety and Data Boundaries

- No new database column, transcript copy, background worker, dependency, or
  automatic model call is introduced.
- `catalog-inspect` remains read-only, bounded, allowlisted, and separately
  approved per distinct target, at most three times per batch. It opens SQLite
  read-only to authenticate the live batch and owner before reading a target.
  Its content remains untrusted analysis data.
- The inspection proof attests only that the exact target body was read for
  the live batch. It does not prove that the skill was invoked in the source
  session; the existing actual-use and causal-attribution policy remains the
  semantic gate.
- `review-commit` recomputes every proof against both the preflight and live
  transaction snapshots. It stores no proof, owner token, or target body in
  candidate, evidence, audit, or schema state. Every validated proof is also a
  persisted-text deny marker, so copying it into candidate, classification, or
  evidence text fails before SQLite writes.
- The transcript adapter scans combined tool output as top-level JSON
  containers. A successfully decoded object or array is indivisible and is
  removed only when that entire raw container has the exact canonical
  seven-key shape (`schema_version`, `batch_id`, `owner_digest`,
  `target_identity`, `skill_sha256`, `inspection_proof`, `content`), bounded
  UTF-8 content, matching content SHA-256, and a valid installation HMAC.
  A decoded non-artifact outer object or array is traversed iteratively within
  fixed 4096-node and 64-level bounds. Any cryptographically valid nested exact
  seven-key response fails the transcript closed as `unsupported_transcript`;
  it is neither exported nor surgically deleted. Nested tampered and general
  lookalikes remain byte-for-byte ordinary tool output. For a decode failure,
  a quote/escape-aware bracket scan preserves an entire balanced invalid outer
  container only when it contains no canonical catalog start. A
  balanced-invalid, mismatched, or unclosed ambiguous outer containing that
  start fails closed.
- Only authenticated top-level containers and their immediately preceding
  `Output:` markers are removed. Unmatched prefix, between-container, suffix,
  and sibling text remains ordered and receives normal owner-token redaction,
  evidence eligibility, and scope. The scan makes at most 32 top-level
  object/array parse attempts and fails closed as `unsupported_transcript` on
  saturation.
  Tampered content/proofs, noncanonical JSON, extra or missing keys, and
  general lookalikes remain ordinary tool output under the existing redaction
  and transcript limits. Fragment boundaries are concatenated before this
  scan: fragments that form one top-level authenticated response are removed,
  while fragments that form an outer object or array containing an
  authenticated nested response fail closed. This changes the transcript
  adapter contract/digest without adding a transcript invocation schema.
- Python continues to validate catalog membership, record references,
  signal/source pairs, the three-distinct-target bound, digests, and atomic
  commit behavior.
- `Q-003`, `C-001`, and its label remain insert-only historical evidence.
- Phase 5 ratios and the zero external-content-adoption limit are unchanged.
- Candidate review remains explicit; apply and undo remain external-TTY-only.

## 7. Tests

The corrective implementation adds the smallest runnable checks that protect
the changed contract:

1. Review policy and fixed instructions both require actual-use evidence,
   prohibit catalog-similarity attribution, require one target inspection, and
   map uncertainty to `attribution_uncertain`.
2. Both inputs require reusable skill-level value and map non-reusable cases to
   existing exclusion enums.
3. A declarative result with four distinct candidate targets raises the
   bounded deterministic `too_many_candidate_targets` error without rotating
   the result or changing its binding, files, or database state, including
   when every fingerprint already exists.
4. The skill workflow makes `catalog-inspect` mandatory once per distinct
   candidate target, caps it at three separately approved reads per batch, and
   allows an exclusion without reading target content.
5. Missing, extra, forged, or replayed inspection proofs fail closed before
   candidate or evidence writes, while a wrong owner is rejected before the
   target file is read.
6. A proof copied into any persisted candidate/evidence text rotates the
   invalid result without candidate persistence or proof leakage, while a
   normal valid result still completes.
7. Every exact authenticated top-level direct, wrapped, fragmented, or
   repeated catalog container is removed from transcript export while
   unmatched text remains eligible. Valid, noncanonical, and oversized outer
   objects or arrays containing an authenticated nested response fail closed,
   while nested tampered lookalikes remain byte-for-byte. Balanced-invalid,
   mismatched, or unclosed ambiguous outers containing a catalog start,
   nested-scan saturation, and container-attempt saturation also fail closed.
8. A one-candidate quality fixture labeled `false/false/false` terminalizes as
   `FAIL`, preserves the exact thresholds, and returns
   `open_changed_quality_epoch`.
9. A failed predecessor cannot open an unchanged successor; a policy/runtime
   provenance change can open `Q-004` using the exact `Q-003` report digest.
10. Existing Review, quality, capture, privacy, and full regression suites remain
   green.

The tests validate the deterministic contract text and lifecycle mechanics;
they do not pretend to unit-test a model's semantic judgment.

## 8. Release and Successor Epoch

The former localization design and plan required `Q-003 PASS` before releasing
`0.1.4`. That precondition is now impossible. This design supersedes that
release boundary and the old plan's release/install tasks; it does not silently
reinterpret them or alter the already implemented localization contract.

The replacement execution plan will:

1. implement and test this policy correction on the existing source branch;
2. release `0.1.4` containing both the completed localization work and this
   correction;
3. install it with source/cache parity and verify the changed policy/runtime
   digests;
4. open `Q-004` with predecessor
   `Q-003@512aa6cb395e7a4d6c94a51ad2b9950fb8cada381381784370d3edecda01ac1a`;
5. collect at least ten new real sessions, run explicit Review under the new
   policy, seal the epoch, obtain user-only external-TTY labels, and run the
   separately approved quality gate;
6. unlock Phase 6 only if `Q-004` returns terminal `PASS`.

If `Q-004` again fails target attribution, stop policy iteration and route to
the explicit-target contract option or a dedicated invocation-event
feasibility spike.

## 9. Acceptance Criteria

- A catalog-only association cannot qualify as a candidate under the Review
  policy or skill workflow.
- Every candidate has one inspected target and an explicit causal,
  skill-level, reusable rationale.
- Ambiguous attribution safely becomes an exclusion rather than a guessed
  candidate.
- Q-003 remains immutable and auditable as a terminal failure.
- The changed `0.1.4` installation is the provenance source for Q-004.
- Phase 6 remains disabled until a real successor sample passes all unchanged
  QUALITY-01 thresholds.
