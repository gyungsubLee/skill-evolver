# Skill Evolver Target Attribution Remediation Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use
> `subagent-driven-development` (recommended) or `executing-plans` to implement
> this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Prevent catalog-only skill attribution, release the completed
localization and attribution correction as `0.1.4`, install the exact tested
source, and open changed-provenance quality epoch `Q-004` from immutable
Q-003 failure evidence.

**Architecture:** Keep the database and persisted candidate schemas unchanged.
Strengthen the model policy and explicit-review workflow, bind each candidate
target to an ephemeral authenticated catalog read, exclude only exact
authenticated catalog responses from recursive transcript export, add one
shared validator guard for at most three distinct candidate targets per batch,
and lock the existing quality retry mechanics with a sparse-candidate
regression. Release and install those changes before the separately approved
Q-004 open.

> **Task 4 security-review correction:** The ephemeral Review result shape now
> adds the exact `target_inspection_proofs` map. `catalog-inspect` requires the
> live batch ID and owner token and returns an installation HMAC bound to batch
> ID, owner-token digest, target identity, and current skill SHA-256.
> `review-commit` verifies exact target coverage and recomputes every proof
> before candidate/evidence writes. Four distinct targets raise
> `too_many_candidate_targets` without rotating or mutating the bound result.
> The proof establishes only the approved target-body read, not source-session
> invocation; invocation and causality remain policy/quality judgments. This
> correction supersedes the earlier “Review result shape unchanged” assumption
> without adding database state or a transcript invocation schema.
> The response now also carries the non-secret `owner_digest`; the transcript
> adapter scans the combined tool output as a stream of top-level JSON
> containers. Each successfully decoded object or array is indivisible: only
> an entire exact canonical seven-key response with a valid content digest and
> HMAC is removed, together with its immediately preceding `Output:` marker.
> A decoded whole top-level response that is semantically authenticated by its
> seven fields, content SHA, and HMAC but whose raw bytes are noncanonical fails
> closed instead of exporting the body and proof.
> The decoder preserves every value of duplicate decoded object keys at every
> nesting level, including Unicode-escaped equivalents, and the bounded scan
> traverses every preserved value. A duplicate-key object containing all seven
> decoded catalog response keys fails closed; other duplicate-key JSON remains
> unchanged ordinary output.
> A decoded non-artifact outer object or array is traversed within fixed
> 4096-node and 64-level bounds. A cryptographically valid nested seven-key
> response fails the transcript closed instead of being exported or surgically
> removed; nested tampered and other lookalikes remain byte-for-byte ordinary
> tool output. On decode failure, an overlap-aware scan tries every raw quote
> as an independent JSON string-token start and accepts only valid tokens
> followed by optional whitespace and a colon as object keys. Each attempt is
> capped at the longest possible raw JSON encoding of one catalog key.
> Balanced-invalid, mismatched, or unclosed outers containing all seven decoded
> catalog response keys fail closed, including Unicode-escaped equivalents;
> string values and malformed objects containing only a subset remain ordinary.
> Every raw object or array start inside a syntax-error span is also retried
> with the duplicate-preserving decoder. Those retries share the existing
> 32-attempt budget and fail closed on authenticated, nested authenticated, or
> duplicate catalog-shaped output and on saturation.
> Unmatched prefix, between-container, suffix, and sibling text remains ordered
> with normal redaction and evidence scope. Top-level scanning and
> syntax-error retries share one 32-attempt object/array parse budget;
> saturation is unsupported transcript.
> Proofs copied into persisted candidate/evidence text fail before SQLite
> writes. Unauthenticated nested values and unambiguous malformed lookalikes
> remain ordinary tool output.

**Tech Stack:** `/usr/bin/python3` 3.9+, Python standard library, SQLite
schema v1, `unittest`, local Codex plugin marketplace, Git worktree.

## Global Constraints

- Work in
  `/Users/igyeongseob/Documents/오픈소스/.worktrees/skill-evolver-quality-label-localization/skill-evolver`
  until the explicit fast-forward integration step.
- Preserve Q-003, C-001, its label, and report digest
  `512aa6cb395e7a4d6c94a51ad2b9950fb8cada381381784370d3edecda01ac1a`
  byte-for-byte.
- Do not weaken QUALITY-01 thresholds: worthy at least `1/2`,
  misattribution at most `1/5`, and external-content adoption exactly `0`.
- Keep `SCHEMA_VERSION = 1`, SQLite DDL, persisted candidate shape, evidence
  enums, and exclusion enums unchanged. The contract-bound ephemeral Review
  result has exactly four top-level keys: `schema_version`, `contract_digest`,
  `target_inspection_proofs`, and `sessions`.
- Add no dependency, model call, background worker, transcript copy, or
  automatic Review/Apply behavior.
- A strong signal alone does not justify a target. Uncertain target use is
  `attribution_uncertain`; non-reusable value is `no_reusable_improvement` or
  `one_off`.
- Every candidate target requires one bounded `catalog-inspect`; reuse one
  read for repeated use of the same target in a batch. At most three distinct
  candidate targets and three separately approved target reads are allowed per
  batch. Each read is authenticated with the exact live batch and owner; its
  proof attests to the target-body read, not source-session invocation.
- `quality-label` remains user-only and external-TTY-only. No agent supplies,
  infers, retries, or changes a label.
- `quality-open` requires one separately approved fully expanded command.
  Approval for implementation or milestone m1 does not replace this runtime
  filesystem approval.
- Preserve unrelated untracked main-worktree directories `n8n/` and `neo4j/`.
- Stage explicit paths only; never use `git add .`.

## Current Evidence

- Source branch head before this plan: `013377d`.
- Installed plugin/runtime: `0.1.3`.
- Q-003 state: terminal `FAIL`.
- Q-003 report:
  `docs/release-reports/quality/Q-003-512aa6cb395e7a4d6c94a51ad2b9950fb8cada381381784370d3edecda01ac1a.json`.
- Existing localization implementation commits: `e520b8d`, `5ee8d29`, and
  `68f8660`.
- The former localization release/install tasks are superseded; their completed
  implementation remains the source for this corrective release.

## File Responsibility Map

- `skills/skill-evolver/references/improvement-policy.md`: model-facing causal
  attribution and reusable-value decision order.
- `skills/skill-evolver/scripts/evolver.py`: exact four-key result validation,
  proof generation/verification, authenticated catalog-output transcript
  exclusion, three-distinct-target validator, and coordinated `0.1.4` version
  identity.
- `skills/skill-evolver/SKILL.md`: executable human/agent Review workflow and
  exact per-target approval cardinality.
- `README.md`: user-facing Review and release behavior.
- `skills/skill-evolver/tests/test_review.py`: policy, validator, and
  documentation contract regressions.
- `skills/skill-evolver/tests/test_quality_gate.py`: Q-003-shaped FAIL and
  changed-policy successor characterization.
- `skills/skill-evolver/tests/test_capture.py`: coordinated release identity
  assertions.
- `.codex-plugin/plugin.json`: plugin version `0.1.4`.
- `skills/skill-evolver/references/runtime.json`: runtime version `0.1.4`.
- `.planning/STATE.md`, `.planning/ROADMAP.md`, `.planning/PROJECT.md`: record
  installed 0.1.4 and Q-004 collection only after those facts exist.

---

### Task 1: Fail-closed target attribution contract

**Files:**

- Modify: `skills/skill-evolver/tests/test_review.py`
- Modify: `skills/skill-evolver/references/improvement-policy.md`
- Modify: `skills/skill-evolver/scripts/evolver.py`
- Modify: `skills/skill-evolver/SKILL.md`
- Modify: `README.md`

**Interfaces:**

- Consumes: `validate_declarative_result(payload, contract,
  allowed_target_identities) -> dict[str, object]` and existing exclusion
  values `attribution_uncertain`, `no_reusable_improvement`, and `one_off`.
- Produces: `CANDIDATE_TARGETS_PER_BATCH_MAX = 3`, error
  `too_many_candidate_targets`, and identical causal-attribution wording in
  policy, fixed instructions, skill workflow, and README.
- Changes: the ephemeral result JSON uses the exact four-key top-level shape
  `schema_version`, `contract_digest`, `target_inspection_proofs`, `sessions`.
- Preserves: database and persisted candidate schemas, catalog membership
  checks, evidence checks, candidate fingerprints, and atomic commit behavior.

- [x] **Step 1: Add failing policy and fixed-instruction assertions**

Extend
`ReviewRuntimeContractTests.test_fixed_runtime_reference_and_policy_are_bounded`
with these exact required policy fragments:

```python
for expected in (
    b"A strong signal is necessary but not sufficient",
    b"catalog name, description, or topical similarity",
    b"unambiguously establishes that the exact target was used",
    b"one bounded `catalog-inspect` for each distinct proposed target",
    b"at most three distinct candidate targets",
    b"`attribution_uncertain`",
    b"reusable skill-level instruction",
    b"`no_reusable_improvement`",
    b"`one_off`",
):
    self.assertIn(expected, policy)
```

In the same test, require these exact fixed instructions:

```python
for expected in (
    (
        "A strong signal alone does not justify a candidate or "
        "target."
    ),
    (
        "Never infer target use from a catalog name, description, "
        "topical similarity, or because a skill would have been "
        "useful."
    ),
    (
        "Return attribution_uncertain unless the session "
        "unambiguously establishes that the exact target was used "
        "and one separately approved bounded catalog-inspect "
        "confirms that the change belongs in that skill."
    ),
    (
        "Use at most three distinct candidate targets per batch; "
        "reuse one inspected target body for repeated targets."
    ),
    (
        "Return no_reusable_improvement or one_off unless the "
        "proposal is a reusable skill-level instruction for "
        "materially different future tasks."
    ),
):
    self.assertIn(expected, instructions)
```

- [x] **Step 2: Add the failing distinct-target validator test**

Add this method to `ReviewCandidateValidationTests`:

```python
def test_more_than_three_distinct_candidate_targets_fail_closed(
    self,
) -> None:
    sessions = []
    decisions = []
    targets = set()
    template = self.payload["sessions"][0]
    for index, digit in enumerate(("3", "4", "5", "6"), start=1):
        session_ref = f"S-{digit * 64}"
        record_ref = f"{session_ref}-R-001"
        target_identity = f"user-skill:target-{index}"
        targets.add(target_identity)
        sessions.append(
            {
                "session_ref": session_ref,
                "review_item_id": 20 + index,
                "expected_generation": 1,
                "frozen_epoch": 0,
                "frozen_from": 0,
                "frozen_to": 70,
                "frozen_locator_digest": digit * 64,
                "records": [
                    {
                        "record_ref": record_ref,
                        "source_kind": "user_direct",
                        "evidence_eligible": True,
                        "content_hmac": digit * 64,
                    }
                ],
            }
        )
        decision = copy.deepcopy(template)
        decision["session_ref"] = session_ref
        decision["target_identity"] = target_identity
        decision["evidence"][0]["record_ref"] = record_ref
        decisions.append(decision)

    contract = {**self.contract, "sessions": sessions}
    payload = {
        "schema_version": 1,
        "contract_digest": self.runtime.sha256_json(contract),
        "target_inspection_proofs": {
            target: "9" * 64 for target in targets
        },
        "sessions": decisions,
    }
    self.runtime._validate_review_contract(contract, 7, "final")

    with self.assertRaisesRegex(
        ValueError, "too_many_candidate_targets"
    ):
        self.runtime.validate_declarative_result(
            payload, contract, frozenset(targets)
        )
```

This test deliberately uses four valid catalog identities and four eligible
same-session records. It must fail only on the new shared target-count guard.

- [x] **Step 3: Add failing documentation-boundary assertions**

Extend `ReviewDocumentationTests` so both `SKILL.md` and README must contain:

```python
for phrase in (
    "A strong signal alone never authorizes target selection.",
    "Never infer target use from catalog similarity",
    "per distinct proposed target",
    "at most three distinct candidate targets per batch",
    "inspected body for repeated targets",
    "attribution_uncertain",
):
    self.assertIn(phrase, text)
```

Keep the existing assertions that command shapes are not approvals and every
actual target read receives its own exact approval.

- [x] **Step 4: Run Review tests and verify RED**

Run:

```bash
/usr/bin/python3 -I -m unittest discover \
  -s skills/skill-evolver/tests -p 'test_review.py' -v
```

Expected: failures name missing causal-attribution text and
`too_many_candidate_targets`; no unrelated test fails.

- [x] **Step 5: Add the minimal shared validator guard**

Beside the current result bounds in `evolver.py`, add:

```python
CANDIDATE_TARGETS_PER_BATCH_MAX = 3
```

After `normalized_sessions` is complete and before constructing `normalized`,
add:

```python
candidate_targets = {
    item["target_identity"]
    for item in normalized_sessions
    if item["decision"] == "candidate"
}
if len(candidate_targets) > CANDIDATE_TARGETS_PER_BATCH_MAX:
    raise ValueError("too_many_candidate_targets")
```

Do not change `CANDIDATE_RESULT_KEYS`, the review contract, or SQLite DDL.
Keep `RESULT_TOP_LEVEL_KEYS` at its corrected exact four-key set containing
`schema_version`, `contract_digest`, `target_inspection_proofs`, and
`sessions`.

- [x] **Step 6: Strengthen the model-facing policy**

Insert this decision rule after the existing strong-signal list in
`improvement-policy.md`:

```markdown
A strong signal is necessary but not sufficient for a candidate. Never infer
target use from a catalog name, description, or topical similarity, or merely
because a skill would have been useful. The session must unambiguously
establish that the exact target was used to produce the behavior.

Before returning any candidate, inspect one bounded `catalog-inspect` result
for each distinct proposed target. Reuse the same inspected body when sessions
in the live batch propose the same target. A batch may contain at most three
distinct candidate targets. The inspected target must contain an instruction,
omission, or ambiguity that plausibly caused the behavior, and the proposed
change must belong in that skill. If use or causality is uncertain, target
inspection is unavailable, or the inspected target does not support the
change, return `attribution_uncertain`.

The proposal must be a reusable skill-level instruction that prevents the
same failure in materially different future tasks. Use
`no_reusable_improvement` for a generic or non-actionable proposal and
`one_off` for a project-only preference or one-session wording request.
```

Keep every existing untrusted-content, language-selection, evidence-source,
privacy, and no-mutation rule.

- [x] **Step 7: Mirror the rule in fixed result instructions**

Append these strings to `REVIEW_RESULT_SCHEMA_INSTRUCTIONS["instructions"]`
before the authoring-language rules:

```python
"A strong signal alone does not justify a candidate or target.",
(
    "Never infer target use from a catalog name, description, "
    "topical similarity, or because a skill would have been useful."
),
(
    "Return attribution_uncertain unless the session unambiguously "
    "establishes that the exact target was used and one separately "
    "approved bounded catalog-inspect confirms that the change "
    "belongs in that skill."
),
(
    "Use at most three distinct candidate targets per batch; reuse "
    "one inspected target body for repeated targets."
),
(
    "Return no_reusable_improvement or one_off unless the proposal "
    "is a reusable skill-level instruction for materially different "
    "future tasks."
),
```

- [x] **Step 8: Make target inspection mandatory and bounded in the skill**

Replace the optional single-target wording in `SKILL.md` Explicit review with
this exact workflow:

```markdown
3. Analyze the envelope without guessing a target. A strong signal alone never
   authorizes target selection. Never infer target use from catalog similarity.
   If the session does not unambiguously establish that an exact target was
   used, exclude it as `attribution_uncertain` without inspecting a skill.
4. Before emitting any candidate, request one separately approved
   catalog-inspect per distinct proposed target, at most three distinct
   candidate targets per batch, and reuse the inspected body for repeated targets.
   If an exact target read is declined, unavailable, or does not show that the
   change belongs in that skill, exclude it as `attribution_uncertain` or abort
   the live batch. Never inspect an unrelated path.
5. Produce exactly one declarative session decision for every returned
   `session_ref`. A reusable skill-level candidate must remain useful in
   materially different future tasks; otherwise use
   `no_reusable_improvement` or `one_off`.
```

Renumber the later result-write, commit, and abort steps without changing their
owner-token or exact-command boundaries. Update the overview sentence to say
“up to three separately approved bounded catalog-inspect results, one per
distinct proposed target.”

- [x] **Step 9: Mirror the workflow in README**

In `README.md` Explicit session review, replace the singular optional wording
with:

```markdown
A strong signal alone never authorizes target selection. Never infer target
use from catalog similarity. Before any candidate, request one separately
approved catalog-inspect per distinct proposed target, with at most three
distinct candidate targets per batch, and reuse the inspected body for
repeated targets. If use or causality is unclear, the exact target read is not
approved, or the proposed change does not belong in the inspected skill,
exclude it as attribution_uncertain. Generic or project-only improvements use
no_reusable_improvement or one_off.
```

Retain the existing statement that `catalog-inspect` is read-only and one
actual command approves only one exact target.

- [x] **Step 10: Verify Task 1 GREEN and safety scans**

Run:

```bash
/usr/bin/python3 -I -m unittest discover \
  -s skills/skill-evolver/tests -p 'test_review.py' -v
/usr/bin/python3 -I -m py_compile \
  skills/skill-evolver/scripts/evolver.py
rg -n "sk-[A-Za-z0-9_-]{16,}|gh[pousr]_[A-Za-z0-9]{20,}|BEGIN [A-Z ]*PRIVATE KEY" \
  skills/skill-evolver README.md
git diff --check
```

Expected: Review suite `OK`; compile and diff checks succeed; privacy scan
shows only deliberate test/policy patterns and no credential value.

- [x] **Step 11: Commit Task 1**

```bash
git add \
  skills/skill-evolver/tests/test_review.py \
  skills/skill-evolver/references/improvement-policy.md \
  skills/skill-evolver/scripts/evolver.py \
  skills/skill-evolver/SKILL.md \
  README.md
git commit -m "fix(skill-evolver): require causal target attribution"
```

---

### Task 2: Characterize Q-003-shaped failure and successor retry

**Files:**

- Modify: `skills/skill-evolver/tests/test_quality_gate.py`
- Modify production files: none

**Interfaces:**

- Consumes: existing `gate_quality_epoch(...)`,
  `current_quality_provenance(...)`, and `open_quality_epoch(...)` behavior.
- Produces: one regression joining sparse candidate aggregation,
  `false/false/false` terminal FAIL, exact predecessor binding, unchanged
  provenance rejection, and changed-policy successor creation.

- [x] **Step 1: Add the sparse failed-sample helper**

Add to `QualityTerminalGateTests`:

```python
def fail_one_candidate_sample(
    self, now: float = 2_000_000_000.0
) -> dict[str, object]:
    self.runtime.open_quality_epoch(
        self.connection,
        self.installation,
        now,
        predecessor=None,
    )
    for offset in (1, 3):
        claim = self.claim(5, now + offset)
        result_path = self.write_result(
            claim,
            self.result_payload(claim),
        )
        self.commit(claim, result_path, now + offset + 1)

    sealed = self.runtime.seal_quality_epoch(
        self.connection, self.installation, now + 5
    )
    self.assertEqual(sealed["sealed"]["distinct_session_count"], 10)
    self.assertEqual(sealed["sealed"]["candidate_count"], 1)

    prepared = self.runtime.prepare_quality_label(
        self.connection,
        self.installation,
        "C-001",
        now + 6,
    )
    self.runtime.commit_quality_label(
        self.connection,
        self.installation,
        "C-001",
        str(prepared["epoch_id"]),
        str(prepared["seal_digest"]),
        str(prepared["subject_digest"]),
        {
            "evaluation_worthy": False,
            "target_correct": False,
            "external_content_adoption": False,
        },
        now + 7,
    )
    return self.runtime.gate_quality_epoch(
        self.connection,
        self.installation,
        "Q-001",
        now + 8,
    )
```

- [x] **Step 2: Add the joined terminal and successor regression**

Add to the same class:

```python
def test_sparse_failed_sample_requires_changed_policy_successor(
    self,
) -> None:
    now = 2_000_000_000.0
    terminal = self.fail_one_candidate_sample(now)
    body = terminal["body"]

    self.assertEqual(body["decision"], "FAIL")
    self.assertIsNone(body["invalid_reason"])
    self.assertEqual(
        body["next_action"], "open_changed_quality_epoch"
    )
    self.assertEqual(
        body["sample"],
        {
            "distinct_session_count": 10,
            "candidate_count": 1,
            "attested_label_count": 1,
            "batch_count": 2,
        },
    )
    self.assertEqual(
        body["metrics"],
        {
            "evaluation_worthy_candidates": 0,
            "target_misattributions": 1,
            "external_content_adoption_incidents": 0,
        },
    )
    self.assertFalse(body["checks"]["evaluation_worthy_ratio"])
    self.assertFalse(body["checks"]["target_misattribution_ratio"])
    self.assertTrue(body["checks"]["external_content_adoption"])

    predecessor = f"Q-001@{terminal['report_digest']}"
    with self.assertRaisesRegex(
        ValueError, "quality_predecessor_provenance_unchanged"
    ):
        self.runtime.open_quality_epoch(
            self.connection,
            self.installation,
            now + 9,
            predecessor=predecessor,
        )

    current = self.runtime.current_quality_provenance(
        self.installation
    )
    with mock.patch.object(
        self.runtime,
        "current_quality_provenance",
        return_value={**current, "policy_digest": "1" * 64},
    ):
        successor = self.runtime.open_quality_epoch(
            self.connection,
            self.installation,
            now + 9,
            predecessor=predecessor,
        )

    self.assertEqual(successor["epoch_id"], "Q-002")
    self.assertEqual(
        successor["predecessor"],
        {
            "epoch_id": "Q-001",
            "terminal_state": "failed",
            "terminal_report_digest": terminal["report_digest"],
        },
    )
```

- [x] **Step 3: Run the characterization test**

Run:

```bash
/usr/bin/python3 -I -m unittest discover \
  -s skills/skill-evolver/tests -p 'test_quality_gate.py' -v
```

Expected: `OK`. This is a characterization test for existing correct quality
mechanics. If it fails, stop and diagnose the shared lifecycle; do not add
production code or weaken a threshold merely to satisfy the test.

- [x] **Step 4: Commit Task 2**

```bash
git add skills/skill-evolver/tests/test_quality_gate.py
git commit -m "test(skill-evolver): cover failed quality successor"
```

---

### Task 3: Release corrective version 0.1.4

**Files:**

- Modify: `skills/skill-evolver/tests/test_capture.py`
- Modify: `skills/skill-evolver/scripts/evolver.py`
- Modify: `.codex-plugin/plugin.json`
- Modify: `skills/skill-evolver/references/runtime.json`
- Modify: `README.md`

**Interfaces:**

- Consumes: committed Tasks 1–2 and the already implemented localization.
- Produces: one source release where manifest, runtime reference, loader check,
  and CLI all identify `0.1.4`.
- Preserves: SQLite schema v1 and Q-003 external runtime state.

- [x] **Step 1: Change only the release assertions to 0.1.4**

In `ProductionSurfaceTests.test_only_main_stop_is_an_automatic_writer`, set:

```python
self.assertEqual(manifest["version"], "0.1.4")
self.assertEqual(runtime["version"], "0.1.4")
self.assertEqual(
    load_runtime().VERSION,
    "skill-evolver 0.1.4",
)
```

- [x] **Step 2: Run capture tests and verify RED**

```bash
/usr/bin/python3 -I -m unittest discover \
  -s skills/skill-evolver/tests -p 'test_capture.py' -v
```

Expected: FAIL only at the three `0.1.4` assertions because source identity is
still `0.1.3`.

- [x] **Step 3: Bump every fixed release identity together**

Change `evolver.py`:

```python
VERSION = "skill-evolver 0.1.4"
```

In `load_review_runtime()`, change the exact accepted reference version:

```python
or payload["version"] != "0.1.4"
```

Change `.codex-plugin/plugin.json` and
`skills/skill-evolver/references/runtime.json`:

```json
"version": "0.1.4"
```

Replace the README release paragraph with:

```markdown
Version `0.1.4` recognizes the text and control response items emitted by
Codex `0.146.0`, defaults the user-only quality-label display to Korean, keeps
`--locale en`, preserves new candidate summaries in the direct user's
language, and requires causal target attribution plus bounded target
inspection before a candidate.
```

- [x] **Step 4: Run focused and full verification**

```bash
/usr/bin/python3 -I -m unittest discover \
  -s skills/skill-evolver/tests -p 'test_review.py' -v
/usr/bin/python3 -I -m unittest discover \
  -s skills/skill-evolver/tests -p 'test_quality_gate.py' -v
/usr/bin/python3 -I -m unittest discover \
  -s skills/skill-evolver/tests -p 'test_capture.py' -v
/usr/bin/python3 -I -m unittest discover \
  -s skills/skill-evolver/tests -p 'test_*.py' -v
/usr/bin/python3 -I -m py_compile \
  skills/skill-evolver/scripts/evolver.py
/usr/bin/python3 -m json.tool .codex-plugin/plugin.json >/dev/null
/usr/bin/python3 -m json.tool \
  skills/skill-evolver/references/runtime.json >/dev/null
/usr/bin/python3 -I skills/skill-evolver/scripts/evolver.py --version
git diff --check
```

Expected: every suite is `OK` with only the three documented historical skips;
compile/JSON/diff checks succeed; CLI prints `skill-evolver 0.1.4`.

- [x] **Step 5: Commit Task 3**

```bash
git add \
  skills/skill-evolver/tests/test_capture.py \
  skills/skill-evolver/scripts/evolver.py \
  .codex-plugin/plugin.json \
  skills/skill-evolver/references/runtime.json \
  README.md
git commit -m "chore(skill-evolver): release attribution fix 0.1.4"
```

---

### Task 4: Review, integrate, install, and verify the tested release

**Files and state:**

- Review only: every Task 1–3 source file and commit.
- Fast-forward: `/Users/igyeongseob/Documents/오픈소스` main worktree.
- External mutation: Codex plugin registry/cache through `codex plugin add`.
- Verify cache:
  `/Users/igyeongseob/.codex/plugins/cache/skill-evolver-dev/skill-evolver/0.1.4`.

**Interfaces:**

- Consumes: clean feature worktree and all passing Task 3 checks.
- Produces: main/source/cache parity at version `0.1.4` and changed Q-003 retry
  provenance.

- [ ] **Step 1: Run independent read-only reviews**

Dispatch two reviewers:

1. Compare Tasks 1–3 with
   `docs/superpowers/specs/2026-08-04-skill-evolver-target-attribution-remediation-design.md`.
2. Audit untrusted-content handling, exact approval cardinality, target-count
   validation before mutation, Q-003 immutability, and label ownership.

Apply only verified findings. Every behavioral correction starts with a
failing focused regression.

- [ ] **Step 2: Re-run the full verification after review**

Repeat every Task 3 Step 4 command and require a clean worktree afterward.
Record the release commit returned by:

```bash
git rev-parse HEAD
git status --short
```

Expected: one 40-hex commit and no worktree output.

- [ ] **Step 3: Verify fast-forward integration is safe**

Run from the main worktree:

```bash
git -C /Users/igyeongseob/Documents/오픈소스 status --short --branch
git -C /Users/igyeongseob/Documents/오픈소스 merge-base --is-ancestor \
  main codex/skill-evolver-quality-label-localization
```

Expected: main has only the pre-existing untracked `n8n/` and `neo4j/`, and
the ancestry check exits `0`. Any tracked change or non-fast-forward state
stops this task for inspection.

- [ ] **Step 4: Fast-forward main without touching unrelated files**

```bash
git -C /Users/igyeongseob/Documents/오픈소스 merge --ff-only \
  codex/skill-evolver-quality-label-localization
```

Expected: fast-forward succeeds; `n8n/` and `neo4j/` remain untracked and
unchanged.

- [ ] **Step 5: Install the local marketplace release**

Request filesystem approval for exactly:

```bash
codex plugin add skill-evolver@skill-evolver-dev --json
```

Run it once from `/Users/igyeongseob/Documents/오픈소스`.
Expected JSON: plugin `skill-evolver`, marketplace `skill-evolver-dev`, version
`0.1.4`, installed/enabled true.

- [ ] **Step 6: Verify installed identity and source/cache parity**

```bash
codex plugin list --json
shasum -a 256 \
  /Users/igyeongseob/Documents/오픈소스/skill-evolver/skills/skill-evolver/scripts/evolver.py \
  /Users/igyeongseob/.codex/plugins/cache/skill-evolver-dev/skill-evolver/0.1.4/skills/skill-evolver/scripts/evolver.py
shasum -a 256 \
  /Users/igyeongseob/Documents/오픈소스/skill-evolver/skills/skill-evolver/references/improvement-policy.md \
  /Users/igyeongseob/.codex/plugins/cache/skill-evolver-dev/skill-evolver/0.1.4/skills/skill-evolver/references/improvement-policy.md
shasum -a 256 \
  /Users/igyeongseob/Documents/오픈소스/skill-evolver/skills/skill-evolver/SKILL.md \
  /Users/igyeongseob/.codex/plugins/cache/skill-evolver-dev/skill-evolver/0.1.4/skills/skill-evolver/SKILL.md
```

Expected: plugin list reports `0.1.4`; each source/cache pair prints identical
digests. The script digest differs from Q-003 runtime digest
`70d46d16cd0c9a299f7563e878b975a1a40c0095367f4a316aff7c5428280207`,
and the policy digest differs from Q-003 policy digest
`2a90a7c6d9fa39f31d41d2efe9de47bbb5a3de832caac3863fe10310c18ee362`.

- [ ] **Step 7: Verify installed CLI and terminal history without judgments**

```bash
/usr/bin/python3 -I \
  /Users/igyeongseob/.codex/plugins/cache/skill-evolver-dev/skill-evolver/0.1.4/skills/skill-evolver/scripts/evolver.py \
  --version
/usr/bin/python3 -I \
  /Users/igyeongseob/.codex/plugins/cache/skill-evolver-dev/skill-evolver/0.1.4/skills/skill-evolver/scripts/evolver.py \
  quality-label --help
/usr/bin/python3 -I \
  /Users/igyeongseob/.codex/plugins/cache/skill-evolver-dev/skill-evolver/0.1.4/skills/skill-evolver/scripts/evolver.py \
  quality-status \
  --installation /Users/igyeongseob/.codex/skill-evolver/installation.json
```

Expected: version is `0.1.4`; label help shows `--locale {ko,en}`; read-only
quality status still reports immutable Q-003 `FAIL`. Do not invoke
`quality-label`.

---

### Task 5: Open changed-provenance Q-004 and record the checkpoint

**Files and state:**

- External mutation: `/Users/igyeongseob/.codex/skill-evolver/evolver.db`.
- Modify after successful open: `.planning/STATE.md`.
- Modify after successful open: `.planning/ROADMAP.md`.
- Modify after successful open: `.planning/PROJECT.md`.

**Interfaces:**

- Consumes: installed/parity-verified `0.1.4`, Q-003 report digest
  `512aa6cb395e7a4d6c94a51ad2b9950fb8cada381381784370d3edecda01ac1a`,
  and separately approved `quality-open`.
- Produces: Q-004 in `collecting` state with exact failed predecessor.
- Does not produce: real sessions, candidates, labels, seal, or quality PASS.

- [ ] **Step 1: Recheck immutable predecessor read-only**

Run the installed `quality-status` command from Task 4 Step 7 and verify:

```json
{
  "epoch_id": "Q-003",
  "status": "FAIL"
}
```

The actual response may include additional aggregate fields. It must have no
active collecting epoch and must not report Q-003 as invalid or mutable.

- [ ] **Step 2: Present and separately approve the exact Q-004 open**

The only allowed command is:

```bash
/usr/bin/python3 -I /Users/igyeongseob/.codex/plugins/cache/skill-evolver-dev/skill-evolver/0.1.4/skills/skill-evolver/scripts/evolver.py quality-open \
  --installation /Users/igyeongseob/.codex/skill-evolver/installation.json \
  --predecessor Q-003@512aa6cb395e7a4d6c94a51ad2b9950fb8cada381381784370d3edecda01ac1a
```

Request approval for that literal command and exact installation data root,
then run it once. Do not use a shell variable, command substitution, shortened
digest, or guessed epoch.

Expected: exit `0`, `epoch_id` exactly `Q-004`, state `collecting`, predecessor
state `failed`, and the exact Q-003 report digest.

- [ ] **Step 3: Verify Q-004 read-only**

```bash
/usr/bin/python3 -I \
  /Users/igyeongseob/.codex/plugins/cache/skill-evolver-dev/skill-evolver/0.1.4/skills/skill-evolver/scripts/evolver.py \
  quality-status \
  --installation /Users/igyeongseob/.codex/skill-evolver/installation.json
```

Expected: `epoch_id=Q-004`, `status=COLLECTING`, zero or naturally observed
initial sample counts, and no mutation of Q-003.

- [ ] **Step 4: Record only verified checkpoint facts**

Update GSD documents with these facts after Step 3 succeeds:

```markdown
- Installed source/cache identity is 0.1.4 with causal-attribution policy and
  at most three distinct candidate targets per review batch.
- Q-004 opened from exact failed predecessor
  Q-003@512aa6cb395e7a4d6c94a51ad2b9950fb8cada381381784370d3edecda01ac1a
  and is COLLECTING.
- Phase 6 remains blocked until Q-004 has at least ten distinct real sessions,
  explicit Review, user-only labels, and terminal PASS.
```

Do not copy transcript text, owner tokens, candidate text, or labels into GSD
documents.

- [ ] **Step 5: Commit the checkpoint**

```bash
git add .planning/STATE.md .planning/ROADMAP.md .planning/PROJECT.md
git commit -m "docs(skill-evolver): open corrected quality epoch"
```

Run `git status --short` and require only the main worktree's unrelated
untracked `n8n/` and `neo4j/` outside this project.

- [ ] **Step 6: Fast-forward the checkpoint commit into main**

```bash
git -C /Users/igyeongseob/Documents/오픈소스 merge-base --is-ancestor \
  main codex/skill-evolver-quality-label-localization
git -C /Users/igyeongseob/Documents/오픈소스 merge --ff-only \
  codex/skill-evolver-quality-label-localization
```

Expected: both commands exit `0`; main contains the Q-004 checkpoint commit;
the feature worktree is clean; `n8n/` and `neo4j/` remain untouched.

## Completion Checkpoint and Next Plan

This plan is complete when `0.1.4` is installed with source/cache parity and
Q-004 is read-only verified as `COLLECTING` from the exact Q-003 failed
predecessor.

Do not create synthetic tasks or repeated empty generations to finish
QUALITY-01. After at least ten distinct real post-open sessions exist, create
a dated `skill-evolver-q004-real-quality-gate` plan using the actual batch
state. That later plan must use runtime-issued batch
IDs, owner tokens, result paths, candidate IDs, seal digest, and missing-label
set; none can be guessed or represented by a placeholder now. Only the user
runs each resulting `quality-label` command. Phase 6 starts only after Q-004
terminal `PASS`.
