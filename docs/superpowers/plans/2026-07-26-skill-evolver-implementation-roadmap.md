# Skill Evolver Implementation Roadmap

**Purpose:** Turn `skill-evolver/docs/superpowers/specs/2026-07-26-skill-evolver-design.md`
into a gate-driven sequence of independently executable implementation plans.

**Roadmap status:** Defined. A document existing does not mean its release gate
has passed or that its implementation is authorized.

**Execution model:** Run exactly one child plan at a time. Preserve the
artifacts and completion report from that plan, evaluate its exit gate, and
only then enter the next child plan. A failed gate routes to a corrective design
amendment; it never authorizes skipping ahead.

## Global Constraints

- Supported production surfaces are macOS Codex CLI and Desktop.
- Runtime is exactly `/usr/bin/python3` version 3.9 or newer.
- Runtime dependencies are limited to the Python standard library and SQLite
  through `sqlite3`.
- The plugin uses one trusted matcher-free `Stop` command Hook.
- The Hook performs no model call, transcript analysis, network access, or
  user-facing chat output.
- `$skill-evolver` runs only when the user explicitly names it or explicitly
  asks to manage the improvement inbox.
- An argument-free `$skill-evolver` invocation is status-only and does not read
  transcripts or import the spool.
- Installed skills are not changed before the Apply release.
- Evaluation never changes the installed skill.
- Actual apply, purge, and undo executors default to `manual_terminal`, require
  a TTY, and require the complete bound digest or current hash.
- PostgreSQL, a background worker, scheduled review, external services, a web
  UI, automatic evaluation, automatic apply, Git commit, push, and pull-request
  creation are outside this roadmap.
- Every child plan inherits the security, privacy, retention, concurrency, and
  completion requirements of the source design.

## Document Roles

- This roadmap defines ordering, inputs, outputs, and go/no-go routing.
- Each child document is a detailed implementation plan with exact file paths,
  interfaces, tests, commands, expected results, and commit boundaries.
- The source design remains the normative product and security specification.
- A child plan may tighten a safety condition but may not weaken a source-design
  gate without a separately reviewed design amendment.

## Plan Sequence

| Order | Release | Plan | Entry gate | Exit artifact |
| ---: | --- | --- | --- | --- |
| 0 | Feasibility | `2026-07-26-skill-evolver-feasibility-spike.md` | Source design reviewed | `skill-evolver/docs/feasibility-report.json` |
| 1 | Read-only MVP | `2026-07-26-skill-evolver-read-only-runtime-queue.md` | Feasibility decision is `PASS` | production capture, SQLite schema v1, queue/status integration report |
| 2 | Read-only MVP | `2026-07-26-skill-evolver-read-only-review-inbox.md` | Runtime/queue integration suite passes | manual review and candidate-inbox report |
| 3 | Read-only MVP | `2026-07-26-skill-evolver-read-only-quality-gate.md` | Review/inbox suite passes with zero target-skill writes | immutable Read-only quality report |
| 4 | Evaluate | `2026-07-26-skill-evolver-evaluate-runner-spike.md` | Read-only quality decision is `PASS` | pinned runner contract and reproducible probe report |
| 5 | Evaluate | `2026-07-26-skill-evolver-evaluate-prepare.md` | Runner spike decision is `PASS` | immutable base, candidate, harness, diff, and evaluation spec |
| 6 | Evaluate | `2026-07-26-skill-evolver-evaluate-execution.md` | Prepare artifacts pass digest and immutability checks | evaluation report bound to the full spec digest |
| 7 | Apply | `2026-07-26-skill-evolver-apply-versioning.md` | Evaluate release completion gate passes | manual apply executor, durable journal, version lineage |
| 8 | Apply | `2026-07-26-skill-evolver-undo-recovery.md` | Apply transaction suite passes without enabling agent apply | hash-bound undo and crash-recovery report |
| 9 | Cross-release | `2026-07-26-skill-evolver-hardening.md` | All preceding plans pass their local gates | `skill-evolver/docs/release-reports/hardening.json` |

## Dependency and Failure Routing

```mermaid
flowchart TD
    F["0. Feasibility Spike"] --> FG{"PASS?"}
    FG -->|No| FR["Amend design for session-level capture"]
    FG -->|Yes| Q["1. Runtime and Queue"]
    Q --> R["2. Review and Inbox"]
    R --> G["3. Read-only Quality Gate"]
    G --> GG{"Quality PASS?"}
    GG -->|No| GR["Revise transcript adapter or review policy"]
    GG -->|Yes| RS["4. Runner Spike"]
    RS --> RG{"Runner PASS?"}
    RG -->|No| RR["Keep prepare/evaluate disabled"]
    RG -->|Yes| P["5. Prepare"]
    P --> E["6. Evaluation Execution"]
    E --> EG{"Evaluate PASS?"}
    EG -->|No| ER["Keep apply disabled"]
    EG -->|Yes| A["7. Apply and Versioning"]
    A --> U["8. Undo and Recovery"]
    U --> H["9. Cross-release Hardening"]
    H --> HG{"Release-ready?"}
    HG -->|No| HR["Repair the owning child plan"]
    HG -->|Yes| DONE["Release"]
```

Failure routes have these fixed meanings:

- Feasibility failure creates a design amendment for session-level capture.
  It does not create or execute the Read-only runtime plan.
- Read-only quality failure returns to the transcript adapter or improvement
  policy. It does not activate runner, prepare, or evaluate commands.
- Runner failure leaves `prepare` and `evaluate` unavailable in the installed
  skill and records the unsupported contract.
- Evaluation failure leaves the candidate and installed target unchanged and
  does not activate apply.
- Apply or undo recovery ambiguity stops file mutation, preserves journal and
  sibling paths, and reports `recovery_required`.

## Stable Repository Layout

The Feasibility plan first creates the plugin tree. Later plans evolve that
same tree into production code:

```text
skill-evolver/
├── .codex-plugin/
│   └── plugin.json
├── hooks/
│   └── hooks.json
├── docs/
│   ├── feasibility-report.json
│   ├── feasibility-report.md
│   └── release-reports/
└── skills/
    └── skill-evolver/
        ├── SKILL.md
        ├── references/
        │   ├── improvement-policy.md
        │   └── runtime.json
        ├── scripts/
        │   └── evolver.py
        ├── evals/
        │   └── evals.json
        └── tests/
            ├── fixtures/
            ├── support.py
            ├── test_installation.py
            ├── test_capture.py
            ├── test_review.py
            ├── test_quality_gate.py
            ├── test_runner_probe.py
            ├── test_prepare.py
            ├── test_evaluate.py
            ├── test_apply.py
            ├── test_undo_recovery.py
            └── test_hardening.py
```

`evolver.py` remains a self-contained standard-library entrypoint because
isolated mode (`python -I`) does not provide sibling runtime imports. Test files
are split by release responsibility, but production commands share that
entrypoint and its validated installation locator.

## Runtime Data and Schema Evolution

The canonical private data root remains outside the plugin cache:

```text
skill-evolver/
├── installation.json
├── config.json
├── identity.key
├── evolver.db
├── spool/
│   └── quarantine/
├── staging/
├── snapshots/
└── reports/
```

Database migrations are release-bound:

| Schema version | Owning plan | Tables and columns activated |
| ---: | --- | --- |
| 1 | Runtime/Queue | `review_batches`, `review_items`, `candidates`, `candidate_evidence`, `metadata`; no `ready_evaluation_id` |
| 2 | Evaluate/Prepare | `evaluations` and nullable `candidates.ready_evaluation_id` |
| 3 | Apply/Versioning | `apply_operations`, `versions`, apply/undo indexes |

Opening the database always enables foreign keys, confirms WAL mode, checks the
exact schema version, and fails closed for an unknown newer version or failed
migration.

## Cross-Plan Interface Contracts

### Feasibility → Runtime/Queue

The Runtime/Queue plan consumes:

- `skill-evolver/docs/feasibility-report.json` with decision `PASS`
- the sanitized CLI and Desktop Stop fixtures
- sanitized transcript pointer-path and provenance layouts
- proof that the Hook and explicit skill process share the fixed private root

The report values are treated as runtime adapter inputs. Implementers do not
guess field names or broaden filesystem access if a required value is absent.

### Runtime/Queue → Review/Inbox

The capture plan produces:

- validated `installation.json`, `config.json`, and `identity.key`
- SQLite schema version 1
- HMAC event-key deduplication
- pending `review_items` bounded to the Hook-time transcript stat
- atomic spool fallback and explicit mutating-command import
- retention/capacity primitives
- status-only health snapshot

The Review/Inbox plan must use these records and must not introduce another
queue or copy transcript bodies into SQLite.

### Review/Inbox → Read-only Quality Gate

The review plan produces:

- lease-bound review batches
- a fail-closed transcript adapter
- allowlisted target identities
- validated structured model-result JSON
- deterministic exclusion codes, fingerprints, evidence merging, defer, reject,
  and inspect behavior
- at most one candidate per session and three new candidates per batch

The quality plan measures these exact outputs and does not rewrite them to make
the gate pass.

### Read-only Quality Gate → Evaluate Runner

The immutable quality report records:

- reviewed session and turn counts
- user-marked evaluation-worth count
- target-skill attribution errors
- external-content adoption incidents
- the exact review policy and transcript adapter digests

Evaluate work starts only when at least 10 sessions or 30 turns were reviewed,
evaluation-worth rate is at least 50%, target-skill misattribution is at most
20%, and external-content adoption incidents equal zero.

### Runner → Prepare → Evaluate

The runner spike fixes:

- runner kind, executable path, exact version, and supported result schema
- model ID and evaluation parameters
- temporary Codex home and credential policy
- read-only base/candidate inputs and one writable output root
- tool, network, process, time, memory, and disk policies
- deterministic failure mapping for timeout, crash, schema, and sandbox errors

Prepare hashes that contract into every evaluation spec. Evaluation runs only
the exact immutable spec digest shown to the user.

### Evaluate → Apply → Undo

Apply consumes the candidate's `ready_evaluation_id`, full evaluation spec
digest, report digest, base/candidate manifests, and current target hash.
Versioning records the resulting content and parent hashes. Undo consumes a
specific version event and an exact expected-current hash; it restores the
parent snapshot, not an ambiguous version label.

## Release Gates

### Feasibility gate

- Two independent real Stop observations exist for CLI and Desktop.
- The Hook-time transcript prefix maps turn ID and provenance without reading
  past the captured boundary.
- The Hook and explicit skill process share the fixed private root under the
  default workspace-write policy.

### Read-only gate

- Hook capture is idempotent and silent.
- Status does not read transcripts or mutate the spool.
- Review respects the 5-session, 20-turn, 2-MiB-per-session, 100-message, and
  8-MiB-per-batch limits.
- No installed skill, staging tree, or snapshot is modified.
- The quality thresholds in the preceding interface contract pass.

### Evaluate gate

- Prepare executes no candidate content.
- Runner contract passes with pinned binary/version and fail-closed resource
  enforcement.
- Base, candidate, harness, runner, model, sandbox, and result schema are bound
  by one canonical evaluation spec digest.
- Evaluation leaves the installed target hash unchanged.

### Apply gate

- Chat produces preview and an exact manual-terminal command only.
- Executor requires a TTY and the complete digest.
- Target lock, drift check, capacity check, snapshot, journal, same-filesystem
  swap, post-apply validation, and rollback all pass.
- Undo requires the complete expected-current hash and uses the same durable
  transaction and recovery machinery.

### Final hardening gate

- Deterministic suites, probabilistic behavior evaluations, prompt-injection
  fixtures, fault-injection matrix, and long-running capture/review soak pass.
- No gate weakens manual approval, immutable artifacts, data minimization, or
  fail-closed path and digest validation.

## Source-Spec Coverage

| Source design sections | Owning child plans |
| --- | --- |
| 1–6: goals, non-goals, decisions, delivery gates, terminology | Roadmap and every child plan |
| 7.2–8: plugin, data root, Stop Hook | Feasibility and Runtime/Queue |
| 9, 10, 11.1: intents, status, review, inspect | Review/Inbox |
| 11.2, 12.1–12.2: approval split, prepare, manifests | Evaluate/Prepare |
| 12.3–12.5: runner and evaluation | Runner Spike and Evaluation Execution |
| 13: apply, version, undo | Apply/Versioning and Undo/Recovery |
| 14–15: states and release migrations | Runtime/Queue, Review/Inbox, Evaluate/Prepare, Apply/Versioning |
| 16–19: retention, configuration, security, concurrency | Runtime/Queue, Quality Gate, every mutating plan |
| 20–22: tests, operations, completion | Quality Gate and Hardening |
| 23: implementation order | This roadmap |
| 24–25: rejected alternatives and official constraints | Global constraints and every child plan |

## Execution Record

Each child plan owns one immutable completion report under
`skill-evolver/docs/release-reports/` or the more specific path named by that
plan. No additional central workflow database or roadmap-state file is added.

Do not hand-edit a gate from FAIL to PASS. A rerun creates a new report that
refers to the repair commit and supersedes the earlier report by digest. The
next plan checks the exact upstream report named in its entry gate.
