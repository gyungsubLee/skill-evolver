# Skill Evolver Session Quality Gate Design

**Date:** 2026-07-30

**Status:** Approved for implementation under the user's m1 auto-approval

**Requirement:** QUALITY-01

**Supersedes:** The quality portion of the 2026-07-26 turn-level plan

## 1. Goal

Measure the usefulness and attribution safety of candidates produced by the
session-level Review/Inbox before enabling the Evaluate Runner spike.

The gate is prospective. Existing completed Phase 4 batches cannot be
backfilled: finalization deleted their review contracts and retained only
aggregate audits without session membership or candidate IDs.

## 2. Non-goals

- No automatic review, model call, inferred label, candidate execution,
  evaluation, apply, undo, or installed-skill write.
- No transcript/evidence text, raw session ID, path, record reference, owner
  value, or target-skill content in quality metadata or reports.
- No PostgreSQL, schema-v2 table, dependency, worker, or Hook change.
- No user-triggered destructive privacy purge. Phase 4 and this design retain
  their automatic bounded cleanup; immediate purge requires a separate
  requirement.
- No quality claim from fixtures, old batches, or m1 implementation approval.

## 3. Lifecycle

```text
quality-open -> COLLECTING
review-commit -> atomic bounded observations
quality-seal -> SEALED source/high-water witness
user quality-label -> append-only attestations
quality-gate -> one terminal PASS, FAIL, or INVALID body/digest
repository release step -> content-addressed JSON
quality-open --predecessor -> next epoch when eligible
```

The stored states are `collecting`, `sealed`, `passed`, `failed`, `invalid`,
and `superseded`. One active epoch exists at most. `quality-gate` clears the
active pointer when it records a terminal body and digest.

An epoch cannot be abandoned while collecting or sealed. A new epoch requires
the exact predecessor `Q-NNN@<terminal-report-digest>`. Every failed or invalid
predecessor requires a changed policy, adapter, quality-contract, runtime, or
identity-key fingerprint. IDs, terminal states, bounded reason codes, and
terminal report digests remain in a public predecessor chain. At most eight
epochs may exist in one lineage. This makes selective sample restart visible
and prevents unchanged retries of unfavorable or deliberately expired input.

## 4. Pinned provenance

`quality-open` locates the packaged Phase 4 report relative to the installed
plugin root, independent of process cwd. It pins:

- Phase 4 release-report SHA-256;
- current runtime script SHA-256;
- a canonical `quality_contract_digest`;
- SHA-256 fingerprint of the random installation identity key;
- improvement-policy digest;
- transcript-adapter digest;
- catalog-adapter digest;
- first prospective batch ID and start time;
- optional predecessor epoch and terminal-report digest.

Every observation, seal, label, and gate recomputes the runtime, contract,
identity-key fingerprint, policy, and adapter digests. Key rotation therefore
invalidates the epoch instead of changing the session-deduplication domain.
Drift never yields PASS.

## 5. Private schema-v1 metadata

All objects are canonical, bounded JSON in the existing `metadata` table.
`SCHEMA_VERSION` and `SCHEMA_SQL` remain unchanged.

### 5.1 Epoch

`quality.epoch.Q-NNN` contains provenance, lifecycle timestamps, state,
prospective first batch, optional sealed high-water witness, optional terminal
body/digest, and predecessor lineage. `quality.active_epoch` contains only the
active display ID.

Collection expires after 30 days. A sealed epoch must be labeled and gated
within 14 days. Expiry makes the next `quality-gate` terminalize it as
`INVALID`; it does not silently open a replacement.

### 5.2 Per-batch observation

`quality.epoch.Q-NNN.batch.B` is written only for a successful review commit
while the epoch is collecting. It is part of the candidate/generation/audit
`BEGIN IMMEDIATE` transaction and contains at most five final decisions.

Each decision contains:

- an epoch-salted pseudonym
  `HMAC(identity_key, "quality-session\0" || epoch_id || "\0" || session_key)`;
- final outcome `candidate` or `excluded`;
- candidate integer ID or an allowlisted exclusion reason.

The observation also binds epoch/batch IDs, review-audit digest,
policy/transcript/catalog adapter digests, catalog snapshot digest, and finish
time. It contains no source text or raw identity.

Expected digest drift or the 100-decision epoch cap atomically marks the epoch
`invalid` while allowing the independent Phase 4 review to complete.
Malformed state, non-canonical data, or an unexpected storage failure raises
and rolls back the whole review transaction. There is no successful
collecting review with a silently missing observation.

Observations are deleted with their Phase 4 audits at 90 days. Collection and
sealing deadlines are shorter, so a nonterminal epoch cannot legitimately
cross that boundary.

### 5.3 Atomic seal

`quality-seal` runs under `BEGIN IMMEDIATE` and refuses:

- fewer than 10 distinct epoch-salted session pseudonyms;
- an empty unique-candidate set;
- a live review batch at or below the proposed high-water ID;
- any completed prospective batch without one exact observation;
- malformed, duplicate, over-capacity, audit-mismatched, or drifted data.

The sealed witness stores:

- high-water batch ID;
- ordered observation digests and their aggregate digest;
- distinct-session and candidate counts;
- each unique sample candidate ID and semantic subject digest;
- seal timestamp and seal digest.

Review commits after sealing do not enter the epoch. The high-water witness and
candidate subject list never change.

### 5.4 User-attested label

`quality.epoch.Q-NNN.label.C-NNN` is insert-only and contains:

- epoch/candidate IDs and the sealed semantic subject digest;
- three complete booleans: `evaluation_worthy`, `target_correct`, and
  `external_content_adoption`;
- attestation time.

The subject digest covers only the sanitized fields the user judges:
target identity, classification, problem/proposal/validation text, risk, and
bounded evidence view. It excludes status clocks and occurrence counters.

`quality-label` accepts no judgment flags. It prints the sanitized candidate,
requires a TTY, asks three yes/no questions, and requires
`C-NNN@<full-sealed-subject-digest>`. Inside the write transaction it
recomputes the current subject and CAS-rejects display/confirmation races.
An existing label cannot be changed or replaced.

The skill must tell the user to run this command in a user-controlled external
terminal and must never run it through an agent PTY. `isatty()` is only a
mechanical guard; the report calls these **user-attested labels**, not
cryptographically proven identities.

## 6. Deterministic gate

`quality-status` is read-only and transcript-free. It reports only aggregate
state and display IDs still requiring labels.

`quality-gate --installation PATH Q-NNN` is an explicit terminal mutation. It
acquires `BEGIN IMMEDIATE`, validates one named epoch from one SQLite snapshot,
then stores exactly one terminal report body and SHA-256. The named form also
replays that same body/digest after the active pointer has been cleared.

Gate precedence is exact:

1. an already terminal named epoch replays its stored body/digest;
2. an identified epoch already marked invalid, expired, or found to have
   source/provenance corruption terminalizes `INVALID` regardless of seal or
   labels;
3. a still-valid collecting epoch is refused until sealed;
4. a still-valid sealed epoch is refused until every label exists;
5. a valid sealed and completely labeled epoch terminalizes PASS or FAIL.

Malformed epoch identity/state that cannot be safely identified raises rather
than fabricating a terminal record.

PASS requires:

- at least 10 distinct reviewed sessions;
- at least one unique sample candidate;
- one current complete label per unique candidate;
- worthy candidates / candidates at least 1/2;
- misattributed candidates / candidates at most 1/5;
- external-content adoption incidents equal zero;
- complete high-water observation coverage and matching provenance.

Ratios use integer cross multiplication, never rounded floats.

Before all labels exist, `quality-status` reports `AWAITING_LABELS` and a
still-valid sealed epoch is not terminalized. Complete labels below a threshold
yield `FAIL`. Provenance, expiry, subject drift, source corruption, or
completeness failure takes the earlier INVALID branch.

## 7. Immutable release report

Runtime never guesses or writes a source-repository path. `quality-gate`
returns the already stored canonical terminal body plus its digest.

The repository release step creates exactly:

`docs/release-reports/quality/Q-NNN-<terminal-body-sha256>.json`

using `apply_patch`, verifies body SHA-256 equals the filename, and commits it
separately. The body has a deterministic source-derived timestamp and contains
only:

- epoch/decision/next action;
- Phase 4, runtime, quality-contract, policy, and adapter digests;
- bounded predecessor lineage entries (epoch ID, terminal state/reason, report
  digest) and their aggregate digest;
- aggregate sample, label, metric, threshold, and check values;
- aggregate observation and label-set digests;
- the user-attestation limitation.

It contains no session pseudonym, candidate ID/text, evidence, path, owner,
record, or transcript field. Exactly one terminal body digest is allowed per
epoch, so competing PASS/FAIL reports cannot be produced.

Release verification must compare the materialized file byte-for-byte with the
canonical terminal body returned from the private epoch and must separately
verify its stored digest and filename. A merely self-consistent JSON file is
not release evidence.

PASS authorizes Phase 6 only. It never authorizes evaluation or apply.

## 8. Retention

- collecting epoch deadline: 30 days;
- sealed label/gate deadline: 14 days;
- observations/audits: Phase 4's 90-day coupled deletion;
- terminal, invalid, or superseded private epoch/label data: 180 days;
- after 180 days, maintenance removes private observations/labels/epoch body
  and keeps a bounded tombstone containing epoch ID, terminal state,
  terminal-report digest, end time, and predecessor digest;
- committed aggregate release report: retained by Git.

Maintenance is bounded and fail-closed. It never evicts an active valid epoch
for capacity.

## 9. Test and release boundary

Tests prove prospective collection, Phase 4 transaction semantics, high-water
completeness, seal immutability, repeated-generation dedupe, drift/overflow
invalidation, candidate CAS, append-only labels, exact integer thresholds,
single terminalization, restart lineage, retention, cwd-independent packaged
provenance, bounded scans, and report privacy.

Synthetic tests prove mechanics only. QUALITY-01 remains incomplete until one
real sealed epoch, external-terminal user attestations, and a committed PASS
report exist.
