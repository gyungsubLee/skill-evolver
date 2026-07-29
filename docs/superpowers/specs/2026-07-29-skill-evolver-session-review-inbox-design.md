# Session Review and Candidate Inbox Design

**Date:** 2026-07-29
**Status:** Approved for planning
**Scope:** Phase 4 Review and Inbox

## 1. Authority and Entry Gate

This design amends only the Review/Inbox slice of
`2026-07-26-skill-evolver-design.md`. It replaces the turn-level assumptions in
`2026-07-26-skill-evolver-read-only-review-inbox.md`.

Implementation may start only when
`docs/release-reports/runtime-queue.json` is committed and says `PASS`.
Planning and the Phase 4 release report validate that immutable predecessor
report and its implementation blobs. Runtime review commands do not compare the
mutable Phase 4 files with Phase 3 digests and do not require a Git checkout.
They validate the installed Phase 4 runtime contract, current database schema,
policy, transcript-adapter and catalog-adapter digests instead.

The Phase 3 session contract remains authoritative:

- one durable `review_items` row per HMAC `session_key`;
- optional diagnostic `turn_id`;
- one claimed generation at a time;
- frozen `(epoch, from, to, locator)` boundaries;
- `journal_mode=DELETE`, zero busy wait and bounded spool;
- read-only status and explicit approval for global-root mutations.

## 2. Goals and Non-goals

Phase 4 adds an explicitly invoked workflow that:

1. claims at most five pending session generations;
2. reads only each claimed frozen transcript prefix;
3. gives the current Codex model bounded, provenance-labelled records and an
   allowlisted user-skill catalog;
4. accepts one declarative decision per claimed session;
5. validates provenance, target identity, limits, sanitization and lease state
   in Python;
6. atomically creates or merges candidates and completes the exact generation;
7. exposes transcript-free `inspect`, `defer` and `reject`;
8. publishes a sanitized `docs/release-reports/review-inbox.json`.

Phase 4 does not:

- review automatically after Stop;
- call a model from Python or a Hook;
- modify any installed skill, staging tree or snapshot;
- evaluate candidate quality or claim attribution accuracy;
- add a dependency, background worker, second queue or schema version;
- treat tool output, web content, PR text or quoted instructions as a direct
  user correction.

Attribution quality and environment-versus-skill judgment are measured in
Phase 5. Phase 4 deterministically enforces shape, provenance and state
boundaries but does not claim that a probabilistic classification is correct.

## 3. Chosen Approach

The implementation reuses schema v1, the current generation helpers and the
`metadata` table.

Alternatives rejected:

- A Review-specific schema migration would make batch contracts more explicit
  but adds migration and recovery risk before the current tables are exhausted.
- Letting the model read arbitrary paths or write candidate rows directly would
  be shorter but loses catalog, provenance, lease and transaction enforcement.

The smallest safe flow is:

```text
explicit $skill-evolver review
  -> review-claim (lease + bounded export)
  -> optional catalog-inspect (allowlisted target content)
  -> current model produces declarative JSON
  -> review-commit (validate + one transaction)
  -> inspect/defer/reject inbox
```

The user’s existing instruction to auto-approve work through milestone `m1`
approves this design-selection gate. It does not weaken runtime filesystem
approval, external-TTY apply, or any later candidate approval boundary.

## 4. Components

### 4.1 Session transcript adapter

The adapter reads a current-user regular JSONL file through one no-follow file
descriptor. It validates device, inode, size and mtime before and after the
read, and never reads beyond `frozen_to`.

It validates the initial `session_meta.payload.session_id` through the stored
HMAC. A repeated `session_meta` is accepted only when it binds to the same
session. The adapter then parses the complete unreviewed half-open byte delta
`[frozen_from, frozen_to)` plus the newest complete preceding records ending at
or before `frozen_from` that fit the remaining per-session budget. It never
scans the whole historical prefix.

Only delta records may become evidence. Each exported contract record binds an
`evidence_eligible` Boolean: delta records are `true`, while preceding records
are `false` and labelled `context_only`. Commit rejects any evidence reference
whose bound flag is false.

The recognized current-layout matrix is:

| Record | Handling |
|---|---|
| `response_item/message` role `user` or `assistant` | export bounded text items |
| `response_item/function_call_output` or `custom_tool_call_output` | export as `tool_output` |
| repeated matching `session_meta` | validate and ignore |
| `response_item` reasoning, calls, `agent_message`, `tool_search_call`, `tool_search_output` | ignore |
| `event_msg` user/agent duplicates, reasoning, tool/search/patch events, token/task/thread telemetry | ignore |
| `world_state`, `turn_context`, `inter_agent_communication_metadata`, `compacted` | ignore |
| developer/system messages and encrypted content | ignore |

An unknown record fails closed only when it is evidence-bearing: an unknown
`response_item`, a user/assistant role, or a payload containing message,
content or output fields. Unknown telemetry with none of those fields is
ignored. A sanitized current-layout structural fixture fixes this matrix
without storing transcript text or private paths.

Each session’s combined delta and context is limited to 2 MiB and 100 exported
records; context yields to delta. The entire batch is limited to 8 MiB of
exported canonical JSON. All accepted JSONL records must be complete and
newline-terminated.

Those are outer adapter safety ceilings, not the model transport budget.
`runtime.json` also fixes `model_envelope_max_bytes` at 131,072 bytes. The
canonical envelope includes the claim contract, exported records, catalog,
policy and result-schema instructions. Byte-level tokenization cannot produce
more tokens than UTF-8 bytes, so the envelope consumes at most 131,072 tokens
of the supported 258,400-token context and reserves at least 127,328 tokens for
system/task context and the bounded result.

Fixed envelope inputs are validated before any session is blamed for capacity:

- canonical catalog JSON: at most 49,152 bytes;
- policy UTF-8: at most 8,192 bytes;
- result schema and model instructions: at most 8,192 bytes;
- fixed claim-contract overhead excluding session records: at most 8,192
  bytes.

If one of those bounds or their combined fixed envelope is invalid, the
coordinator fails the batch with sanitized `configuration_envelope_error`,
releases every claimed row to error-free `pending` without cursor advancement,
and removes the batch contract. It does not terminally exclude a session.

Context records are discarded oldest-first before this model-envelope limit is
applied. The exact delta is never truncated. If one session’s delta and fixed
envelope overhead cannot fit, the verified generation is terminally excluded
as `oversized_model_export`; if only the aggregate batch would exceed the
limit, that session and later claimed rows use the same error-free pending
release as the 8-MiB outer cap.

If the exact frozen delta itself exceeds a hard limit, has unsupported
evidence-bearing layout or cannot be decoded as complete JSONL, the adapter
terminally excludes that verified frozen generation as `oversized_session` or
`unsupported_transcript` and advances only to `frozen_to`. It never truncates
the delta and pretends it is complete.

Missing files, identity changes during the read and partial live suffixes are
retryable. They retain `pending`, preserve `reviewed_boundary`, set a sanitized
`error_code`, and become ineligible for ordinary oldest-first claim until a
strictly newer accepted Stop or successful epoch adoption clears the error.

Every exported record receives an opaque batch-local `record_ref` and one
Python-derived source kind:

- `user_direct`;
- `assistant`;
- `tool_output`.

Transcript text remains untrusted data. It is not stored in SQLite, a report or
candidate evidence.

### 4.2 Batch and lease coordinator

`claim_review_batch()` refactors and reuses the existing lease primitives. It
does not introduce parallel lease state.

In one `BEGIN IMMEDIATE` transaction it:

1. recovers expired generation leases without advancing a cursor;
2. selects at most five oldest accepted pending session rows whose retryable
   `error_code` is clear;
3. creates one `review_batches` row with status `preparing`;
4. freezes each selected generation and assigns the batch and trusted owner;
5. stores a seed contract under `metadata`.

The seed contains batch/session refs, frozen tuples, static policy,
transcript-adapter and catalog-adapter digests, plus one dynamic
`catalog_snapshot_digest`, but no record map. Transcript reading occurs only
after this transaction commits.

After bounded reads finish, a second short `BEGIN IMMEDIATE` transaction
revalidates every lease and frozen tuple, replaces the seed with the final claim
contract, and returns its digest. No model runs and no candidate changes during
either transaction.

The final contract contains:

- batch ID and trusted owner digest;
- batch-local `session_ref` values;
- review item ID, expected generation and frozen epoch/from/to;
- frozen locator digest;
- exported record refs, Python-derived provenance, bound `evidence_eligible`
  flags and domain-separated keyed content HMACs;
- policy, transcript-adapter, catalog-adapter and dynamic catalog-snapshot
  digests;
- creation and lease expiry.

It contains no raw session ID, `session_key` or transcript text. The
`session_ref` uses the `"review-session\0"` HMAC domain over batch ID,
review-item ID and generation; record content uses the separate
`"review-record\0"` domain.

Retryable locator failures use `fail_review_generation()` to clear the lease
and frozen state, preserve `reviewed_boundary`, retain `pending`, and set a
sanitized `error_code`. Deterministic exact-prefix failures use the existing
caller-owned completion transaction with outcome `excluded` and advance only
the verified frozen generation. Neither path guesses content.

The finalization transaction updates `review_batches.session_count` and
`generation_count` to the successfully exported sessions. If none remain, it
sets the batch to `failed`, deletes the seed/final contract and writes only a
sanitized audit record. Otherwise it sets the batch to `ready`.

Exports are packed in oldest-claim order. If the next individually valid
session would exceed the 8-MiB batch cap, that row and all later claimed rows
are released to ordinary error-free `pending`, with no cursor advancement or
exclusion. They remain eligible for the next batch. The audit increments
`batch_capacity_released`; aggregate batch pressure never becomes a permanent
session error.

Heartbeat extends only live rows owned by the exact batch owner. `review-abort`
clears the same owner’s leases without cursor advancement. Both are explicit
mutations.

Batch terminal states are `completed`, `aborted`, `expired` and `failed`.
Abort, zero-survivor finalization and expired-lease recovery update the batch,
clear all matching row membership, and delete seed/final contract metadata in
the same transaction. Partial export removes failed rows from membership before
storing the final contract. Each terminal path writes one aggregate audit
record with static policy/adapter digests, the claim-time catalog snapshot
digest, counts and `finished_at`; maintenance deletes terminal
`review_batches` rows and their audit metadata after 90 days.

Expired-lease recovery first captures affected batch IDs, then clears row
leases, closes those batches and removes contracts in the same
`BEGIN IMMEDIATE`. A seed-stage crash therefore cannot leave permanent batch or
metadata state.

### 4.3 Trusted user-skill catalog

`references/runtime.json` gains one fixed `mutable_skill_roots` list. For this
installation it contains `/Users/igyeongseob/.codex/skills`.

The catalog:

- scans only direct child skill directories under a canonical allowed root;
- rejects symlinks and non-current-user entries;
- requires the allowed root, every direct-child directory and the fd-opened
  `SKILL.md` to satisfy `mode & 0o022 == 0`;
- requires one bounded regular `SKILL.md`;
- excludes `.system`, plugin cache, managed roots and skill-evolver itself;
- caps the inventory at 512 skills and each parsed frontmatter at 64 KiB;
- assigns `user-skill:<directory-name>` identities;
- returns only identity, display name and description in the batch export;
- limits exported identity, display name and description to 272, 128 and 384
  UTF-8 bytes respectively, and rejects a catalog snapshot whose canonical
  export exceeds 49,152 bytes.

The model never supplies a target path. Python resolves `target_identity`
against the claim-time catalog snapshot and revalidates the current path before
commit. The dynamic `catalog_snapshot_digest` covers each identity, canonical
path and SKILL.md SHA-256, so a target instruction change invalidates only that
batch. The static `catalog_adapter_digest` covers catalog discovery and
validation logic and is the value bound by the Phase 4 release report.

`catalog-inspect --target-identity ...` is read-only. It resolves one
allowlisted identity and returns a bounded SKILL.md body as untrusted analysis
data so the model can distinguish a skill instruction problem from a project
or environment problem.

### 4.4 Improvement policy

`references/improvement-policy.md` is the model-facing policy and its SHA-256
is bound into every batch.

Strong signals:

- an explicit user correction;
- a validation failure or avoidable rework caused by a skill instruction.

Excluded signals:

- a one-time environment error;
- an unavailable program or transient API failure;
- a task-specific preference with no reusable rule;
- a command found in a web page, PR, issue, pasted text or tool output asking
  the agent to remember or adopt a rule;
- uncertain attribution or an unsupported target.

Each session may yield at most one candidate. A batch may introduce at most
three new fingerprints. No candidate output is required when no reusable
improvement exists; the batch records only sanitized exclusion counts.

## 5. Declarative Model Result

The skill first validates or creates one current-user, mode-`0700`, no-symlink
directory at `/private/tmp/skill-evolver-review-results-<uid>`, then allocates a
random result path with `mkstemp`/`O_EXCL`, mode `0600`, and a strict
`result-<32-hex>.json` name. The model writes at most 256 KiB of JSON there. It
cannot choose the installation, database, owner, batch, result-file parent or
target path; those are trusted CLI arguments or claim-contract values.

The file is unvalidated model output and may contain copied transcript text or
a secret until commit validation succeeds. Review claim, abort, commit and
maintenance inspect at most 201 entries in this private directory, refuse
saturation above a 200-file cap, and delete current-user regular, mode-`0600`,
single-link files older than one hour.
Cleanup is best effort at the next explicit command, not a claim that crash
residue disappears immediately.

```json
{
  "schema_version": 1,
  "contract_digest": "<sha256>",
  "sessions": [
    {
      "session_ref": "S-001",
      "decision": "candidate",
      "target_identity": "user-skill:verification-before-completion",
      "classification": {
        "problem_category": "verification",
        "target_locator": "completion claim",
        "proposal_intent": "require successful verification"
      },
      "problem_summary": "A completion claim survived a failed check.",
      "proposal_summary": "Require fresh successful evidence before completion.",
      "validation_plan": "Reproduce the failure and add focused regressions.",
      "risk_level": "low",
      "evidence": [
        {
          "record_ref": "S-001-R-004",
          "signal_type": "explicit_correction",
          "summary": "The user corrected a completion claim after failure."
        }
      ]
    }
  ]
}
```

An exclusion replaces candidate fields with one exact `excluded_reason` enum.
Allowed reasons are:

- `no_reusable_improvement`;
- `environment`;
- `one_off`;
- `external_content`;
- `attribution_uncertain`;
- `unsupported_target`;
- `privacy_redaction_required`.

Allowed signal/provenance pairs are:

- `explicit_correction` or `unnecessary_rework` from `user_direct`;
- `verification_failure` from `tool_output`.

Assistant records may provide context but cannot be the only strong evidence.
Whether a valid verification failure was caused by the target skill is the
model-policy judgment that Phase 5 measures.

Python rejects unknown or missing keys, duplicate sessions, invalid enums,
oversized input, more than one result per claimed session, more than one
candidate per session, more than three new fingerprints, unknown record refs,
context-only evidence refs, invalid signal/provenance pairs, multiline or
explicit quote-block syntax, secret-like text and catalog drift.

Each candidate result may reference at most three evidence records. The
validated canonical result is limited to 32 KiB; the 256-KiB temporary-file cap
is only an outer bound for rejecting unvalidated model output.

Before any mutation, the set of result `session_ref` values must equal the
final-contract set exactly. Any unknown, omitted or duplicated session rejects
the entire result and leaves every lease and batch contract intact for retry or
abort.

Free-text limits are:

- evidence and problem/proposal summary: 280 Unicode characters each;
- target locator and proposal intent: 160 characters each;
- validation plan: 500 characters.

All free text receives Unicode/newline normalization and deterministic secret
redaction. A secret-like value remaining in a required candidate field rejects
the whole result without changing the batch.

Because the persisted contract intentionally retains keyed HMACs rather than
transcript text, Python cannot prove semantic substring copying. The model
policy prohibits direct quotation; Python enforces short single-paragraph
summaries and rejects explicit quote markup, while Phase 5 audits the resulting
sample for copied or external content. Phase 4 does not overclaim a
content-comparison guarantee it cannot implement.

## 6. Atomic Candidate Commit

`review-commit` opens the bounded result file with no-follow, owner/mode/link
checks, reads it once, verifies the same inode before unlinking it, and starts
`BEGIN IMMEDIATE`. Every success or validation-failure path removes the private
temporary result. A crash may leave bounded unvalidated model output until the
next namespace cleanup; it is not treated as a sanitized summary.

Inside that one caller-owned transaction it:

1. reloads the exact live batch and claim contract;
2. checks owner, lease, generation, epoch, frozen bounds and locator digest;
3. validates the result and current static policy/transcript-adapter/
   catalog-adapter digests plus the claim-time catalog snapshot digest;
4. resolves every target from the trusted catalog;
5. normalizes fingerprint inputs;
6. resolves the fingerprint without inserting it and loads the
   domain-separated candidate-session link for the current `session_key`;
7. if the session already belongs to another candidate, converts this decision
   to the deterministic internal exclusion `candidate_limit`; otherwise it
   inserts the new candidate or loads the matching existing candidate;
8. calls `record_candidate_evidence()` for each validated signal and tracks
   whether at least one unique evidence row was inserted;
9. when the candidate-session link was absent, requires at least one evidence
   insert and then writes the link with the review row’s dedupe expiry;
10. leaves a newly inserted candidate at `occurrence_count=1`, and increments an
    existing candidate exactly once only when step 9 creates the link; that new
    distinct-session evidence also atomically moves `stale` to `proposed` and
    restores redacted candidate text from the validated result;
11. calls `complete_review_generation()` for every accepted result;
12. updates batch candidate/exclusion counts and commits.

Any failure rolls back candidate, evidence, completion and batch changes
together. A Stop arriving during review can reopen the same row only through
the existing generation completion contract.

Candidate evidence remains unique by candidate, session and signal, but
recurrence is unique by candidate and session. A second signal or later
generation from the same session cannot inflate `occurrence_count` or create a
different candidate.

The recurrence authority is a metadata link keyed by
`HMAC(identity_key, "candidate-session\0" || session_key)`, not evidence-row
presence. Its value contains only candidate ID and the review row’s dedupe
expiry. The link is created in the candidate transaction and deleted at the
same 180-day identity boundary. It preserves the one-candidate-per-session
invariant after evidence text is redacted without extending session identity
retention. After 180 days the base session dedupe guarantee intentionally ends.

After success, the full claim contract is deleted and replaced with a sanitized
batch audit record containing only static policy/adapter digests, the dynamic
snapshot digest and aggregate counts.

### 6.1 Candidate aging and privacy

The backward-compatible config defaults add:

- deferred-to-stale: 30 days;
- rejected fingerprint tombstone: 90 days;
- terminal candidate text/evidence retention: 90 days.

Maintenance performs these set-based transitions in its existing explicit
transaction:

1. `deferred` older than 30 days becomes `stale`.
2. When `rejected` or `stale` has been terminal for 90 days, evidence is
   reduced to signal/source aggregate counts under
   `metadata.candidate.<id>.evidence_aggregate`, individual evidence rows are
   deleted, and problem/proposal/validation text is replaced with
   `redacted:<sha256>` markers.
3. Before any session reaches the 180-day HMAC deletion boundary, remaining
   evidence signal/source counts are merged into the same aggregate metadata;
   only then are rows carrying that `session_key` and its candidate-session
   recurrence link deleted.

Aggregate metadata contains no session key, record ref, transcript pointer or
free text.

`reject` sets `tombstone_until`. A matching fingerprint during the tombstone
may update distinct-session occurrence and last-seen evidence but remains
rejected. After the tombstone expires, a new strong distinct-session signal may
move the existing row back to `proposed`, replace redacted text from the new
validated result, and clear the tombstone. A new strong distinct-session signal
likewise moves `stale` to `proposed` and restores redacted text in the candidate
commit transaction. `resume C-xxx` is an explicit compare-and-swap from
`deferred` to `proposed`; stale or rejected candidates require new review
evidence rather than manual resurrection.

## 7. Inbox Commands and Access

The skill stays explicit-only. Ordinary tasks never invoke it.

| Intent | Transcript read | DB write | Approval |
|---|---:|---:|---|
| no argument / `status` | no | no | none |
| `inspect C-xxx` | no | no | none |
| `catalog-inspect` | target skill only | no | exact read command |
| `review` claim/export | frozen transcript only | yes | exact command and data root |
| `review` heartbeat/commit/abort | no | yes | exact command and data root |
| `defer C-xxx` / `resume C-xxx` / `reject C-xxx` | no | yes | exact command and data root |

`inspect` renders candidate and evidence summaries without `session_key`,
transcript pointer or raw target path.

`defer` is a compare-and-swap from `proposed` to `deferred`; `resume` is the
inverse transition while the candidate is still deferred. `reject` is a
compare-and-swap from `proposed`, `deferred` or `prepared` to `rejected` and
sets a 90-day fingerprint tombstone. Unsupported or stale transitions fail
without mutation.

No command requests a permanent writable-root grant. Python does not invoke a
model, and chat never applies a candidate.

## 8. Failure and Recovery Rules

- Invalid model JSON: no DB mutation; the live lease may be retried or aborted.
- Missing, changed or partial transcript: clear that generation lease without
  cursor advancement, set a retryable error and skip it until a newer accepted
  Stop or epoch adoption.
- Oversized, malformed or unsupported exact frozen delta: complete that
  verified generation as a terminal exclusion without reading past
  `frozen_to`.
- Individually oversized model envelope: complete that verified generation as
  `oversized_model_export`; aggregate envelope pressure releases that row and
  later claimed rows error-free for the next batch.
- Catalog, policy, schema or fixed-contract envelope overflow: fail the batch as
  `configuration_envelope_error`, release all claimed rows error-free and
  advance no cursor.
- Lease expiry: recovery returns rows to pending without cursor advancement and
  atomically closes the batch and removes its contract.
- Candidate/catalog drift: roll back the entire result.
- More than three new fingerprints: reject the entire result so the model can
  return a compliant batch; do not silently choose winners.
- Concurrent Stop: commit the frozen generation, then let existing completion
  logic reopen a later generation.
- DB busy: fail the explicit review command promptly; the Stop Hook’s spool
  fallback is not used for review state.
- Process crash: SQLite transaction rollback plus same-transaction batch
  cleanup on lease expiry/abort provides recovery; no second journal is
  introduced.

## 9. Tests and Exit Report

Phase 4 adds `tests/test_review.py` and focused surface assertions covering:

- Phase 3 report entry gate;
- sanitized current-layout matrix, repeated matching headers, delta-plus-context
  parsing and provenance;
- frozen boundary, suffix non-read, same-inode relocation and explicit epoch
  adoption;
- retryable partial/changed ineligibility and strictly newer Stop recovery;
- terminal malformed/unknown-evidence/oversized/over-record exclusion;
- five-session and 8-MiB caps with error-free overflow release;
- 128-KiB complete model-envelope budget with worst-case high-entropy JSON,
  terminal individual overflow and error-free aggregate overflow;
- catalog-only and policy-only fixed-envelope overflow with batch failure and
  zero session exclusion;
- competing owner, heartbeat, abort, partial export, zero survivor and expiry
  cleanup without cursor advancement;
- strict exact-session result coverage, result-temp namespace cleanup and
  record-ref provenance, including context-only evidence rejection;
- catalog allowlist, symlink/owner/group-write/world-write mode bounds and
  target revalidation;
- one candidate per session across fingerprints/generations and three new
  fingerprints per batch;
- atomic rollback and concurrent Stop reopening;
- new-candidate count initialization, distinct-session recurrence and
  same-session multi-signal/generation dedupe;
- candidate-session link retention after 90-day evidence redaction and deletion
  at the 180-day identity boundary;
- stale revival only on new strong distinct-session evidence with text
  restoration;
- secret/external/uncertain/unsupported rejection;
- 30/90/180-day candidate aging, aggregate preservation and text redaction;
- static catalog-adapter versus dynamic catalog-snapshot digest separation;
- read-only status/inspect and transcript-free defer/resume/reject;
- no target skill, staging or snapshot writes.

The full discovery suite must pass with only the three existing historical
probe skips.

`docs/release-reports/review-inbox.json` binds:

- the Runtime Queue report digest;
- the implementation commit;
- production, policy, transcript-adapter and catalog-adapter digests;
- deterministic test results and exact skips;
- command/read-write boundary checks;
- zero installed-skill, staging and snapshot writes.

It makes no quality-gate PASS claim. A committed Phase 4 `PASS` authorizes only
Phase 5 sample collection and labeling.

## 10. Completion Criteria

Phase 4 is complete only when:

1. the old turn-level Review plan is marked superseded;
2. all Review behavior uses session generations and frozen byte boundaries;
3. only explicit review reads a transcript;
4. Python validates every target, record reference, limit and transition;
5. candidate/evidence/generation completion is one transaction;
6. deterministic transcript failures cannot starve newer pending sessions;
7. batch contracts are removed on every terminal path;
8. candidate aging preserves only bounded aggregate evidence after retention;
9. inspect is read-only and mutation commands require scoped approval;
10. the current full suite and independent review are clean;
11. the canonical Review/Inbox report is committed with decision `PASS`.
