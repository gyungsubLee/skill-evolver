# Skill Evolver Session Quality Gate Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: use
> `subagent-driven-development` task by task and
> `verification-before-completion` before every completion claim.

**Goal:** Add a prospective, sealable quality epoch over Phase 4 reviews,
collect append-only external-terminal user attestations, and terminalize one
aggregate content-addressed PASS/FAIL/INVALID report.

**Architecture:** Reuse SQLite schema v1 metadata. Review commits record
bounded observations only while an explicit epoch is collecting.
`quality-seal` atomically freezes a complete high-water source.
`quality-label` is TTY-only and insert-only. `quality-gate` atomically records
one terminal body/digest; repository tooling materializes that body by digest.

**Tech Stack:** `/usr/bin/python3` 3.9, standard library, SQLite
`journal_mode=DELETE`, canonical JSON/SHA-256/HMAC, `unittest`.

## Global constraints

- Run from `/Users/igyeongseob/Documents/오픈소스`.
- Keep `SCHEMA_VERSION = 1` and `SCHEMA_SQL` byte-unchanged.
- No dependency, network/model call, Hook, worker, PostgreSQL, installed-skill
  write, staging write, or snapshot write.
- Do not backfill old batches or count repeated generations as sessions.
- Quality metadata/report must contain no source text or raw identity.
- The agent never runs `quality-label`, including through a PTY.
- Synthetic tests do not satisfy QUALITY-01.
- Stage exact paths only; never use `git add .`.

## Entry gate

- [ ] Full suite: 365 tests, 110 Review tests, 82 capture tests, three skips.
- [ ] Phase 4 report: valid PASS with matching production hashes.
- [ ] Parser: no `quality-*` command; schema version 1.

```bash
cd /Users/igyeongseob/Documents/오픈소스
/usr/bin/python3 -m unittest discover \
  -s skill-evolver/skills/skill-evolver/tests -p 'test_*.py'
/usr/bin/python3 -m json.tool \
  skill-evolver/docs/release-reports/review-inbox.json >/dev/null
git diff --check
```

## Task 1: Prospective epoch and atomic observations

**Files**

- Modify `skill-evolver/skills/skill-evolver/scripts/evolver.py`
- Create `skill-evolver/skills/skill-evolver/tests/test_quality_gate.py`

### 1.1 Failing tests

Test:

- cwd-independent packaged Phase 4 report lookup;
- runtime/quality-contract/identity-key/policy/transcript/catalog digest
  pinning;
- first epoch and exact predecessor rules;
- maximum eight-epoch public lineage and unchanged failed/invalid restart
  rejection;
- old completed batches excluded by first prospective batch ID;
- successful review commit stores final candidate/exclusion decisions;
- epoch-salted repeated-session pseudonym dedupe;
- no active epoch preserves Phase 4 behavior;
- expected drift/100-decision overflow completes Phase 4 and persists
  `invalid`;
- malformed/storage errors roll back candidate, generation, audit, and sample;
- canonical bounded objects contain no forbidden fields.

Run `test_quality_gate.py` and verify missing-interface failures.

### 1.2 Minimum implementation

Add strict bounded helpers for:

- quality contract/runtime digests;
- packaged Phase 4 report;
- epoch/predecessor metadata;
- observation metadata and epoch-salted session HMAC;
- `open_quality_epoch(...)`;
- `record_quality_observation(...)` inside `commit_review_result`;
- `quality-open --installation PATH [--predecessor Q-NNN@DIGEST]`.

Distinguish expected invalidation from exceptional rollback exactly as the
design specifies.

### 1.3 Verify and commit

Run focused, Review, capture, and full suites. Confirm schema/Hook/skill writes
are unchanged.

Commit:

```text
feat(skill-evolver): record quality observations
```

## Task 2: Atomic seal and append-only labels

**Files**

- Modify `skill-evolver/skills/skill-evolver/scripts/evolver.py`
- Modify `skill-evolver/skills/skill-evolver/tests/test_quality_gate.py`

### 2.1 Failing tests

Test:

- seal rejects fewer than 10 distinct sessions and an empty candidate set;
- repeated generations dedupe by epoch-salted pseudonym;
- high-water seal rejects live batches and every missing/extra/mismatched
  completed-batch observation;
- seal freezes ordered observation digests and candidate subject digests;
- post-seal reviews cannot extend the epoch;
- semantic digest covers exactly user-judged sanitized candidate/evidence
  fields, not status clocks or occurrence counters;
- label CLI has no judgment flags, rejects non-TTY, and accepts only three
  yes/no answers plus full digest confirmation;
- candidate change between display and transaction CAS-rejects;
- label is scoped to a sealed sample candidate and insert-only;
- `quality-status` is read-only/transcript-free and lists only missing display
  IDs.

### 2.2 Minimum implementation

Add:

- strict seal/witness validation;
- `quality_candidate_subject_digest(...)`;
- append-only label validation/storage;
- `seal_quality_epoch(...)`;
- `quality-seal --installation PATH`;
- user-controlled external-TTY
  `quality-label --installation PATH C-NNN`;
- read-only `quality-status --installation PATH`.

### 2.3 Verify and commit

Run focused/full suites and byte-compare the database around
`quality-status`.

Commit:

```text
feat(skill-evolver): seal and label quality epochs
```

## Task 3: Single terminal gate, restart lineage, and retention

**Files**

- Modify `skill-evolver/skills/skill-evolver/scripts/evolver.py`
- Modify `skill-evolver/skills/skill-evolver/tests/test_quality_gate.py`

### 3.1 Failing tests

Test:

- named gate replays an already terminal epoch after the active pointer clears;
- gate terminalizes identified invalid/expired/source-corrupt epochs as INVALID
  before checking seal or labels;
- gate refuses only a still-valid unsealed epoch or still-valid sealed epoch
  with incomplete labels;
- one write snapshot prevents concurrent candidate/label mutation;
- exact integer 1/2 and 1/5 boundaries pass; one-unit violations fail;
- any external adoption fails;
- expiry, pinned-digest drift, subject drift, or completeness corruption is
  terminal INVALID;
- first terminal call stores body/digest and clears active; repeats return the
  exact same values;
- one epoch cannot produce competing decisions;
- new epoch requires exact predecessor and failed restart requires changed
  implementation/key/policy/adapter provenance;
- invalid/expired restart has the same changed-provenance rule and the public
  report exposes bounded predecessor state/reason/digest entries;
- collecting 30-day and sealed 14-day deadlines;
- 90-day observation/audit coupling;
- 180-day private epoch/label cleanup leaves only a bounded tombstone;
- report body/digest is deterministic, aggregate-only, and privacy-clean.

### 3.2 Minimum implementation

Add:

- integer-only terminal report builder;
- single terminal CAS in `gate_quality_epoch(...)`;
- terminal-body/digest replay;
- predecessor-chain validation;
- bounded quality retention in `run_maintenance`;
- `quality-gate --installation PATH Q-NNN`.

Runtime emits the body/digest only; it never writes a repository path.

### 3.3 Verify and freeze

Run focused, Review, capture, full discovery, compile, privacy scans, and
`git diff --check`. Obtain independent specification and security reviews.

Commit:

```text
feat(skill-evolver): terminalize quality gate
```

Freeze the reviewed implementation commit before opening the real epoch.

## Task 4: Skill boundary and real quality gate

**Files**

- Modify `skill-evolver/skills/skill-evolver/SKILL.md`
- Modify `skill-evolver/README.md`
- Modify `skill-evolver/skills/skill-evolver/tests/test_quality_gate.py`
- Create
  `skill-evolver/docs/release-reports/quality/Q-NNN-SHA256.json`
- Create Phase 5 summary/verification only after real PASS

### 4.1 Document and test the boundary

Document:

- `quality-open`, `quality-seal`, and `quality-gate` are separately approved
  explicit mutations;
- `quality-status` is read-only;
- the model may explain `inspect` output but never infer or enter a label;
- the user alone runs `quality-label` in an external terminal;
- TTY is an attestation boundary, not identity proof;
- PASS unlocks only Phase 6.

### 4.2 Implementation verification

Run the full suite and two independent reviews. Install the frozen plugin
build. This proves mechanics, not quality.

### 4.3 Hard real-data checkpoint

1. Explicitly open `Q-NNN`.
2. Collect and explicitly review at least 10 distinct real sessions.
3. Seal the epoch.
4. Run read-only status and inspect every missing candidate.
5. Give the user each exact external-terminal label command and wait.
6. Rerun status, then explicitly run the terminal gate.

Never synthesize sessions, use fixtures, infer labels, or treat m1 approval as
a label.

### 4.4 Materialize and certify

For a real PASS:

- create the exact content-addressed report with `apply_patch`;
- recapture the stored terminal body/digest, byte-compare it with the proposed
  file, verify body SHA-256 equals filename, and confirm the privacy scan is
  clean;
- commit the report separately;
- mark QUALITY-01 and Phase 5 complete;
- write Phase 5 SUMMARY and VERIFICATION;
- use `$gsd-progress` only as a PM cross-check;
- begin Phase 6.

Report commit:

```text
docs(skill-evolver): certify read-only quality gate
```

## Completion criteria

- Prospective collection, atomic seal, append-only attestations, single
  terminalization, restart lineage, and retention are fully tested.
- No old batch, repeated generation, mutable label, or post-seal review can
  change the frozen source.
- Runtime/report/policy/adapter provenance is pinned and drift-safe.
- Terminal report is deterministic, aggregate-only, content-addressed, and
  privacy-clean.
- One real externally attested epoch produces PASS. Tests alone cannot
  complete Phase 5.
