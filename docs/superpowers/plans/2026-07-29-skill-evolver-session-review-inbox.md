# Skill Evolver Session Review Inbox Execution Index

This file is an execution index, not a substitute for the zero-context
implementation plans linked below.

## Authority

- Design:
  `docs/superpowers/specs/2026-07-29-skill-evolver-session-review-inbox-design.md`
  at commit `ac184f4`.
- Entry gate:
  `docs/release-reports/runtime-queue.json` is `PASS`, SHA-256
  `c551176e5925a4c18ed54875ca28dcd089c0de2b38a873819fe219a8dcf29674`.
- The superseded 2026-07-26 turn-level plan must never be executed.
- Phase 4 keeps SQLite schema v1, the standard library and the single-file
  runtime. It does not change a Hook, invoke a model from Python, mutate an
  installed skill or claim attribution quality.

## Ordered implementation plans

Execute each plan with `subagent-driven-development`, including its independent
review gate, before starting the next:

1. `2026-07-29-skill-evolver-review-contract-adapters.md`
   - backward-compatible fixed contract and policy;
   - trusted user-skill catalog;
   - current-layout frozen transcript adapter.
2. `2026-07-29-skill-evolver-review-batch-export.md`
   - caller-owned generation claim and batch lifecycle;
   - bounded complete model envelope;
   - private, batch-bound result allocation and retry.
3. `2026-07-29-skill-evolver-review-candidate-inbox.md`
   - strict declarative result validation;
   - atomic candidate/evidence/generation commit;
   - 30/90/180-day candidate privacy and recurrence.
4. `2026-07-29-skill-evolver-review-surface-release.md`
   - explicit CLI and `$skill-evolver` orchestration;
   - transcript-free inbox operations;
   - full verification and canonical Phase 4 release report.

All paths above are relative to this directory.

## Cross-plan hard gates

The next plan may start only when the preceding plan’s targeted tests, the
unchanged Phase 3 regression suite and independent review are clean.

The four plans must collectively prove:

- fixed runtime maxima reject hostile legacy config;
- catalog inspect is byte-bounded and catalog roots/files reject group or world
  writes;
- same-inode relocation is distinct from an identity change;
- context records are cryptographically evidence-ineligible;
- fixed-envelope, individual-session and aggregate-batch overflow have three
  different failure paths;
- raw owner tokens never enter SQLite, contracts or audits;
- result files are bound to one batch and foreign files are preserved;
- invalid model output retains the lease/contract and receives a new
  Python-allocated retry file;
- raw-metadata TTL, expiry, abort and every other terminal path close the batch
  and delete its contract atomically;
- commit revalidates owner, lease, generation, epoch, frozen bounds, locator,
  current static digests and the claim-time dynamic catalog digest;
- deterministic secret redaction succeeds only when no secret-like value
  remains;
- claim, abort, commit and maintenance all use the bounded result cleanup;
- candidate/evidence/recurrence/generation/batch changes are one transaction;
- aging implementation and its 30/90/180-day tests ship in the same plan;
- full discovery has zero failures or errors and only the three existing
  historical probe skips.

## Phase exit

Phase 4 is complete only after
`docs/release-reports/review-inbox.json` is committed with `decision: PASS` and
an independent implementation review is clean. That PASS authorizes only the
Phase 5 read-only quality sample; it does not authorize evaluation or apply.
