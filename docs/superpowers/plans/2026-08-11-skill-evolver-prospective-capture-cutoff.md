# Skill Evolver Prospective Capture Cutoff Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use
> superpowers:subagent-driven-development (recommended) or
> superpowers:executing-plans to implement this plan task-by-task. Steps use
> checkbox (`- [ ]`) syntax for tracking.

**Goal:** Prevent Stop events captured before a collecting quality epoch from
entering that epoch's Review batches, expose the excluded backlog safely in
status, release `0.1.6`, and replace zero-observation Q-005 with a clean Q-006
prospective cohort.

**Architecture:** Reuse `review_items.first_stop_at` and the validated active
quality epoch. Resolve one optional cutoff inside the caller's SQLite
transaction, then apply the same strict predicate to Review claim and status:
`cutoff is absent OR first_stop_at > cutoff`. Keep capture and maintenance
metadata-only, preserve old rows, and change no database schema. Bind the new
semantics into quality contract v2 so an installed-runtime change necessarily
invalidates the empty Q-005 provenance before Q-006 opens.

**Tech Stack:** `/usr/bin/python3` 3.9+, Python standard library, SQLite schema
v1 with DELETE journaling, `unittest`, local Codex plugin marketplace, Git.

## Global Constraints

- Work in the existing isolated worktree and branch created for this change.
- Approved design:
  `docs/superpowers/specs/2026-08-11-skill-evolver-prospective-capture-cutoff-design.md`.
- Do not run production `maintain` until installed `0.1.6` has opened Q-006.
- Do not run production `review-claim` until the imported backlog is proven
  nonclaimable under Q-006.
- Do not read transcripts, copy spool contents, expose session IDs/paths, or
  inspect private runtime rows. Operational verification uses aggregate CLI
  output only.
- Add no dependency, schema migration, background worker, purge command,
  transcript discriminator, or new public command.
- Keep `SCHEMA_VERSION = 1`, DELETE journaling, current retention, and every
  external-TTY/user-only quality-label boundary unchanged.
- A later Stop must not rehabilitate an old session; eligibility is based only
  on the existing earliest `first_stop_at` value.
- Equal-second events are ineligible. The comparison is strictly `>`.
- Status must retain fixed finite buckets, stored-error precedence, partition
  equations, read-only behavior, and content-free aggregate output.
- Agents must never answer `quality-label` prompts or fabricate real user
  sessions. Q-006 requires ten genuine sessions and user-entered labels.
- Stage explicit files only; never use `git add .`.

## Current Evidence

- Installed/source release is `0.1.5`; Q-005 is collecting with zero sessions,
  candidates, and labels.
- Q-005 started at `2026-08-11T13:03:28Z` with `first_batch_id=24` and exact
  Q-004 predecessor digest
  `a9d30a50776d7e1ad2b0f56d5be741fda8b9460f46090a88e1e18777e5f0e917`.
- Read-only status reported 71 verified spool files before this amendment.
- Review currently claims every otherwise-eligible pending row ordered by
  `pending_since,id`; it has no epoch-time predicate.
- Excluded Review decisions count toward the ten-distinct-session minimum, so
  importing and excluding old rows would contaminate Q-005.
- `upsert_session()` already preserves the earliest signed capture time with
  `MIN(first_stop_at, incoming_created_at)`.
- Last source verification: 524 tests passed with 3 expected skips.

## File Responsibility Map

- `skills/skill-evolver/scripts/evolver.py`: collecting-epoch helper, Review
  predicate, transactional status partition, quality contract v2, release
  identity validation.
- `skills/skill-evolver/tests/test_quality_gate.py`: prospective cohort,
  fail-closed epoch metadata, contract digest, and minimum-sample regressions.
- `skills/skill-evolver/tests/test_capture.py`: status snapshot, privacy,
  partition, empty inventory, and release identity.
- `.codex-plugin/plugin.json`: plugin version `0.1.6`.
- `skills/skill-evolver/references/runtime.json`: runtime version `0.1.6`.
- `README.md`, `skills/skill-evolver/SKILL.md`: user-visible cutoff/status
  behavior and installed command guidance.
- `docs/release-reports/quality/`: immutable Q-005 terminal report produced by
  the actual gate.
- `.planning/PROJECT.md`, `.planning/ROADMAP.md`, `.planning/STATE.md`: actual
  Q-005/Q-006 and installed-release facts after operational rollout.

---

### Task 1: Enforce prospective eligibility at Review claim

**Files:**

- Modify: `skills/skill-evolver/tests/test_quality_gate.py`
- Modify: `skills/skill-evolver/scripts/evolver.py`

**Interfaces:**

- Add private
  `_collecting_quality_started_at(connection: sqlite3.Connection) -> Optional[str]`.
- Consume the helper only inside `_prepare_review_batch()`'s existing
  `BEGIN IMMEDIATE` transaction.
- Advance `quality_contract_payload()["version"]` to `2` and add the exact
  `prospective_capture` object from the approved design.

- [ ] **Step 1: Add RED contract and eligibility tests**

In `ProspectiveQualityEpochTests`, reuse `open_epoch()`, `insert_pending()`,
and inherited Review fixtures. Import `replace` from `dataclasses` and add this
local signed-spool helper rather than assuming a nonexistent fixture API:

```python
def spool_stop(
    self, raw_session_id: str, captured_at: float
) -> tuple[object, str]:
    plugin_data = self.base / "capture-plugin-data"
    plugin_data.mkdir(mode=0o700, exist_ok=True)
    spool = plugin_data / "stop-spool"
    spool.mkdir(mode=0o700, exist_ok=True)
    capture = replace(self.installation, spool=spool)
    transcript = self.sessions / f"{raw_session_id}.jsonl"
    transcript.write_text('{"payload":{"role":"user"}}\n', encoding="utf-8")
    event = self.runtime.parse_session_stop(
        json.dumps(
            {
                "hook_event_name": "Stop",
                "session_id": raw_session_id,
                "cwd": str(self.workspace),
                "transcript_path": str(transcript),
            }
        ).encode(),
        self.installation,
        self.config,
    )
    assert event is not None
    event = replace(event, observed_at_ns=int(captured_at * 1_000_000_000))
    key = self.runtime.session_key(self.installation, raw_session_id)
    assert self.runtime.spool_session_stop(
        capture, self.config, event, key, captured_at, coalesce=True
    )
    return capture, key
```

Use the returned capture installation with the existing literal call shape:

```python
self.runtime.run_maintenance(
    self.connection,
    self.installation,
    self.config,
    imported_at,
    capture_installation=capture,
)
```

Add tests proving:

1. A Stop spooled before `open_epoch()` and imported by maintenance afterward
   remains `pending`, while the next Review claim is empty.
2. An older ineligible row does not consume the batch limit or block a row
   whose `first_stop_at` is one second after the epoch start.
3. A row with `first_stop_at == epoch["started_at"]` is ineligible.
4. A second, later Stop on a pre-open row preserves its earliest time and does
   not make it claimable.
5. With no active collecting epoch, the same row remains claimable under the
   existing behavior.
6. Nine pre-open rows plus one post-open row produce a contract containing
   exactly the post-open session. After committing that one-session result,
   sealing fails with the existing insufficient-sample error.
7. A malformed active pointer and a malformed active epoch each make claim
   fail before owner-token or batch allocation. Assert the review sequence,
   row status/generation, and transaction state are unchanged.
8. `quality_contract_payload()` equals version 2 plus this exact object and
   its digest equals `sha256_json(payload)`:

```python
{
    "source": "review-items-first-stop-at",
    "cutoff": "active-collecting-epoch-started-at",
    "comparison": "strictly-after-utc-second",
    "same_second": "exclude",
    "later_stop": "preserve-earliest",
    "pre_cutoff_disposition": (
        "pending-unclaimable-while-epoch-collecting"
    ),
}
```

- [ ] **Step 2: Run the focused RED suite**

```bash
/usr/bin/python3 -I -m unittest discover \
  -s skills/skill-evolver/tests -p 'test_quality_gate.py' -v
```

Accept only failures caused by missing prospective filtering and contract v2.
Fixture, syntax, or private-path failures are not valid RED evidence.

- [ ] **Step 3: Add the smallest shared helper and claim predicate**

Place the helper immediately after `active_quality_epoch()`:

```python
def _collecting_quality_started_at(
    connection: sqlite3.Connection,
) -> Optional[str]:
    epoch = active_quality_epoch(connection)
    if epoch is None or epoch["state"] != "collecting":
        return None
    return str(epoch["started_at"])
```

Inside `_prepare_review_batch()`, after `BEGIN IMMEDIATE` and lease recovery,
resolve `cutoff` once. Change only the row-selection query:

```sql
AND (? IS NULL OR first_stop_at > ?)
```

Bind `(cutoff, cutoff, limit)`. Do not filter after `LIMIT`, mutate old rows,
or allocate a batch before the validated helper succeeds.

- [ ] **Step 4: Bind the behavior into quality contract v2**

Change `quality_contract_payload()["version"]` from `1` to `2` and add the
exact `prospective_capture` object tested in Step 1. Do not change schema
version, thresholds, retention, or session HMAC domains.

- [ ] **Step 5: Run GREEN and invariant checks**

```bash
/usr/bin/python3 -I -m unittest discover \
  -s skills/skill-evolver/tests -p 'test_quality_gate.py' -v
/usr/bin/python3 -I -m unittest discover \
  -s skills/skill-evolver/tests -p 'test_review.py' -v
git diff --check
```

Review every caller of `_prepare_review_batch()` and confirm the predicate is
not duplicated at a higher layer.

- [ ] **Step 6: Commit Task 1**

```bash
git add skills/skill-evolver/scripts/evolver.py \
  skills/skill-evolver/tests/test_quality_gate.py
git diff --cached --check
git commit -m "fix(skill-evolver): isolate prospective quality sessions"
```

---

### Task 2: Make status use the identical cutoff and one DB snapshot

**Files:**

- Modify: `skills/skill-evolver/tests/test_capture.py`
- Modify: `skills/skill-evolver/scripts/evolver.py`

**Interfaces:**

- `queue_status()` continues to return its existing public object plus a fixed
  `quarantined_by_error.pre_quality_epoch` integer.
- It rejects a caller-owned transaction, owns one read transaction for epoch
  resolution and all DB aggregates, then closes that transaction before spool
  inspection and return.

- [ ] **Step 1: Add RED status partition tests**

Extend `MaintenanceStatusTests` with otherwise-claimable rows whose earliest
Stop is before, equal to, and after a collecting epoch start. Include rows that
overlap the cutoff with a stored allowlisted error, unknown error, pending
binding, and empty generation. Assert this precedence exactly:

```text
claimable -> allowlisted error -> unknown_error -> binding_pending
          -> empty_generation -> pre_quality_epoch
```

Assert:

- only the strictly post-start row is claimable;
- only otherwise-claimable old/equal rows enter `pre_quality_epoch`;
- stored failure rows retain their existing buckets;
- `pending = claimable + quarantined` and quarantine bucket sums match;
- `pre_quality_epoch` exists with zero in an empty inventory;
- serialized output contains no session key, ID, path, inode, timestamp,
  transcript content, or raw stored error;
- database bytes and `connection.total_changes` are unchanged.

- [ ] **Step 2: Add RED transaction/fail-closed tests**

Add tests proving:

1. `queue_status()` raises `active_transaction` without committing or rolling
   back a caller-owned transaction.
2. The collecting-epoch helper executes while `connection.in_transaction` is
   true, all DB aggregates are read before the transaction closes, and spool
   inventory executes after it closes.
3. With a second connection attempting an epoch-pointer transition after the
   cutoff is read, DELETE journaling cannot commit that transition during the
   status snapshot; status reports one coherent cohort.
4. A corrupt active pointer/record makes status fail, returns no partial
   payload, and changes neither database bytes nor queue rows.

- [ ] **Step 3: Run the focused RED suite**

```bash
/usr/bin/python3 -I -m unittest discover \
  -s skills/skill-evolver/tests -p 'test_capture.py' -v
```

The new tests must fail only because status currently lacks the cutoff bucket
and transaction ownership.

- [ ] **Step 4: Implement one transactional status snapshot**

At the start of `queue_status()`:

1. reject `connection.in_transaction`;
2. execute `BEGIN`;
3. resolve the cutoff once with `_collecting_quality_started_at()`;
4. run classification and every database aggregate in the same `try` block;
5. commit the read transaction before config/spool filesystem inspection;
6. roll back on every exception.

In the classification CTE, apply the same claim predicate as Task 1. Preserve
stored error and binding precedence, explicitly classify
`observed_boundary <= reviewed_boundary` as `empty_generation`, then classify
the remaining cutoff-rejected row as `pre_quality_epoch`. Add the fixed zero
bucket to the returned dictionary. Do not stat or open transcript paths.

- [ ] **Step 5: Run GREEN and regression suites**

```bash
/usr/bin/python3 -I -m unittest discover \
  -s skills/skill-evolver/tests -p 'test_capture.py' -v
/usr/bin/python3 -I -m unittest discover \
  -s skills/skill-evolver/tests -p 'test_quality_gate.py' -v
/usr/bin/python3 -I -m unittest discover \
  -s skills/skill-evolver/tests -p 'test_review.py' -v
git diff --check
```

- [ ] **Step 6: Commit Task 2**

```bash
git add skills/skill-evolver/scripts/evolver.py \
  skills/skill-evolver/tests/test_capture.py
git diff --cached --check
git commit -m "fix(skill-evolver): report pre-epoch queue isolation"
```

---

### Task 3: Release source identity `0.1.6`

**Files:**

- Modify: `.codex-plugin/plugin.json`
- Modify: `skills/skill-evolver/references/runtime.json`
- Modify: `skills/skill-evolver/scripts/evolver.py`
- Modify: `skills/skill-evolver/tests/test_capture.py`
- Modify: `README.md`
- Modify: `skills/skill-evolver/SKILL.md`

- [ ] **Step 1: Add the RED release identity expectation**

Change the existing production identity test to require all of:

```text
manifest version = 0.1.6
runtime reference version = 0.1.6
evolver.VERSION = skill-evolver 0.1.6
runtime compatibility payload version = 0.1.6
```

Run `test_capture.py` and record the expected `0.1.5` mismatch.

- [ ] **Step 2: Update the minimal release surfaces**

Synchronize the four release identities. Update README/SKILL status guidance
to explain that during a collecting epoch:

- only sessions whose earliest Stop is strictly after epoch start are
  claimable;
- otherwise-claimable older rows appear in aggregate
  `pre_quality_epoch`;
- status remains read-only and Review remains explicit.

Do not document a purge, automatic Review, or automatic label/apply path.

- [ ] **Step 3: Run the complete source release verification**

```bash
/usr/bin/python3 -I -m unittest discover \
  -s skills/skill-evolver/tests -p 'test_*.py' -v
/usr/bin/python3 -I -m py_compile \
  skills/skill-evolver/scripts/evolver.py
/usr/bin/python3 -I skills/skill-evolver/scripts/evolver.py --version
/usr/bin/python3 -I -c \
  'import json; from pathlib import Path; [json.loads(Path(p).read_text()) for p in (".codex-plugin/plugin.json", "skills/skill-evolver/references/runtime.json", "hooks/hooks.json")]'
git diff --check
```

Expected version output: `skill-evolver 0.1.6`. Record exact test and skip
counts; historical skips must remain the known three only.

- [ ] **Step 4: Request independent specification and security/privacy review**

Review from the last documentation commit through Task 3 head. Require no
Critical/Important/Minor findings and explicitly check:

- claim/status predicate identity;
- equal-second exclusion;
- transaction snapshot ownership;
- error-bucket precedence and no private output;
- no capture/maintenance transcript reads;
- contract/runtime provenance change;
- unchanged user-only label boundary.

Fix findings with RED tests before release.

- [ ] **Step 5: Commit Task 3**

```bash
git add .codex-plugin/plugin.json README.md \
  skills/skill-evolver/SKILL.md \
  skills/skill-evolver/references/runtime.json \
  skills/skill-evolver/scripts/evolver.py \
  skills/skill-evolver/tests/test_capture.py
git diff --cached --check
git commit -m "chore(skill-evolver): release prospective cutoff 0.1.6"
```

---

### Task 4: Merge the verified source branch into canonical main

**Files:** None beyond conflict-free Git metadata.

- [ ] **Step 1: Verify branch range and clean worktree**

```bash
git status --short
git diff --check main...HEAD
git log --oneline main..HEAD
```

Require only the approved design, plan, implementation, tests, release files,
and documentation.

- [ ] **Step 2: Fast-forward canonical main**

From `/Users/igyeongseob/Documents/오픈소스`, verify the main worktree contains
no overlapping user changes, then use a fast-forward-only merge. Never reset,
force, or discard sibling work.

- [ ] **Step 3: Re-run canonical verification**

Run the full test, compile, JSON, version, and diff checks from Task 3 against
canonical main before any installed-runtime mutation.

---

### Task 5: Terminalize Q-005 and open Q-006 on installed `0.1.6`

**Files:**

- Add after actual gate:
  `docs/release-reports/quality/Q-005-<terminal-digest>.json`
- Modify after actual transition: `.planning/PROJECT.md`
- Modify after actual transition: `.planning/ROADMAP.md`
- Modify after actual transition: `.planning/STATE.md`

**Safety:** Every command uses fully expanded literal paths. Capture aggregate
JSON only; do not inspect the private database, spool bodies, or transcripts.

- [ ] **Step 1: Gate Q-005 with changed source before plugin install**

Run canonical source `0.1.6` against the installed Q-005 installation:

```bash
/usr/bin/python3 -I \
  /Users/igyeongseob/Documents/오픈소스/skill-evolver/skills/skill-evolver/scripts/evolver.py \
  quality-gate \
  --installation /Users/igyeongseob/.codex/skill-evolver/installation.json \
  Q-005
```

Require exit `2`, terminal `INVALID`, reason `quality_provenance_drift`, zero
sample/labels, and a literal 64-hex report digest. If any condition differs,
stop before install.

- [ ] **Step 2: Materialize and verify the immutable Q-005 report**

Use `apply_patch` to add only the returned `body` object, not the outer
`{"body": ..., "report_digest": ...}` wrapper, as
`Q-005-<digest>.json`. Run this exact canonical-body verifier:

```bash
/usr/bin/python3 -I -c 'import hashlib,json; from pathlib import Path; root=Path("/Users/igyeongseob/Documents/오픈소스/skill-evolver/docs/release-reports/quality"); paths=sorted(root.glob("Q-*.json")); triples=[(p,p.stem.rsplit("-",1)[1],hashlib.sha256(json.dumps(json.loads(p.read_text(encoding="utf-8")),sort_keys=True,separators=(",",":"),ensure_ascii=False).encode()).hexdigest()) for p in paths]; bad=[(str(p),expected,actual) for p,expected,actual in triples if expected!=actual]; assert not bad,bad; print("quality-report-digests: PASS",len(triples))'
```

Expected: `quality-report-digests: PASS 3`.

Run this privacy scan:

```bash
rg -n '"(raw_session_id|transcript_path|owner_token|session_key)"\s*:' \
  /Users/igyeongseob/Documents/오픈소스/skill-evolver/docs/release-reports/quality
```

It must return no match in the new report. Any match blocks installation.

- [ ] **Step 3: Install and prove `0.1.6` parity**

```bash
codex plugin add skill-evolver@skill-evolver-dev --json
codex plugin list --json
/usr/bin/python3 -I \
  /Users/igyeongseob/.codex/plugins/cache/skill-evolver-dev/skill-evolver/0.1.6/skills/skill-evolver/scripts/evolver.py \
  --version
```

Hash and compare these exact production files:

```text
.codex-plugin/plugin.json
hooks/hooks.json
README.md
skills/skill-evolver/SKILL.md
skills/skill-evolver/references/runtime.json
skills/skill-evolver/references/improvement-policy.md
skills/skill-evolver/scripts/evolver.py
```

Run:

```bash
/usr/bin/python3 -I -c 'import hashlib; from pathlib import Path; source=Path("/Users/igyeongseob/Documents/오픈소스/skill-evolver"); cache=Path("/Users/igyeongseob/.codex/plugins/cache/skill-evolver-dev/skill-evolver/0.1.6"); names=(".codex-plugin/plugin.json","hooks/hooks.json","README.md","skills/skill-evolver/SKILL.md","skills/skill-evolver/references/runtime.json","skills/skill-evolver/references/improvement-policy.md","skills/skill-evolver/scripts/evolver.py"); mismatches=[name for name in names if hashlib.sha256((source/name).read_bytes()).digest()!=hashlib.sha256((cache/name).read_bytes()).digest()]; assert not mismatches,mismatches; print("source-cache-parity: PASS",len(names))'
```

Require `source-cache-parity: PASS 7` and installed/cached version `0.1.6`.

- [ ] **Step 4: Open Q-006 from the exact Q-005 terminal digest**

Use cached installed `0.1.6` and the literal predecessor:

```bash
/usr/bin/python3 -I \
  /Users/igyeongseob/.codex/plugins/cache/skill-evolver-dev/skill-evolver/0.1.6/skills/skill-evolver/scripts/evolver.py \
  quality-open \
  --installation /Users/igyeongseob/.codex/skill-evolver/installation.json \
  --predecessor Q-005@<exact-terminal-digest>
```

Require `Q-006`, state `collecting`, the exact Q-005 predecessor, and zero
observations/candidates/labels. Run installed read-only `quality-status` and
record only its aggregate result.

- [ ] **Step 5: Record observed rollout facts**

Update PROJECT/ROADMAP/STATE with actual version, source head, report digest,
Q-006 start, first batch ID, and provenance digests. Leave REQUIREMENTS.md and
`QUALITY-01` pending. Commit only the report and three planning files.

---

### Task 6: Import backlog safely under Q-006, without Review

**Files:**

- Modify only if observed counts add durable context: `.planning/STATE.md`

- [ ] **Step 1: Capture installed aggregate status before maintenance**

Run cached `0.1.6 status` read-only and record aggregate pending, claimable,
quarantined, `pre_quality_epoch`, error buckets, and verified spool counts.

```bash
/usr/bin/python3 -I \
  /Users/igyeongseob/.codex/plugins/cache/skill-evolver-dev/skill-evolver/0.1.6/skills/skill-evolver/scripts/evolver.py \
  status \
  --installation /Users/igyeongseob/.codex/skill-evolver/installation.json \
  --plugin-data /Users/igyeongseob/.codex/plugins/data/skill-evolver-skill-evolver-dev
```

- [ ] **Step 2: Run one installed maintenance pass**

Only after Q-006 is confirmed collecting, run cached `0.1.6 maintain` with the
literal installation and plugin-data roots. This imports signed metadata; it
must not read transcript content.

```bash
/usr/bin/python3 -I \
  /Users/igyeongseob/.codex/plugins/cache/skill-evolver-dev/skill-evolver/0.1.6/skills/skill-evolver/scripts/evolver.py \
  maintain \
  --installation /Users/igyeongseob/.codex/skill-evolver/installation.json \
  --plugin-data /Users/igyeongseob/.codex/plugins/data/skill-evolver-skill-evolver-dev
```

- [ ] **Step 3: Prove old backlog isolation before any Review**

Run read-only status again:

```bash
/usr/bin/python3 -I \
  /Users/igyeongseob/.codex/plugins/cache/skill-evolver-dev/skill-evolver/0.1.6/skills/skill-evolver/scripts/evolver.py \
  status \
  --installation /Users/igyeongseob/.codex/skill-evolver/installation.json \
  --plugin-data /Users/igyeongseob/.codex/plugins/data/skill-evolver-skill-evolver-dev
```

Require:

- verified spool count decreases according to maintenance results;
- imported otherwise-claimable pre-Q-006 rows increase
  `pre_quality_epoch`, not `claimable_sessions`;
- stored transcript/binding failures retain their named buckets;
- partition equations hold;
- no private fields appear.

If any old cohort is claimable, stop without `review-claim` and treat it as a
release defect.

- [ ] **Step 4: Record the safe handoff**

Update STATE only with aggregate evidence if it materially changes the resume
point. Q-006 remains collecting and Phase 6 remains blocked.

---

### Task 7: Complete Phase 5 quality gate with genuine Q-006 sessions

**Files:** Runtime quality artifacts and planning documents only after actual
user work supplies the cohort.

- [ ] Collect at least ten distinct real user sessions whose earliest Stop is
  strictly after Q-006 opened. Fixtures, subagents, repeated generations, and
  empty synthetic tasks do not count.
- [ ] Explicitly invoke Review and commit complete results for the fresh
  cohort. Do not include `pre_quality_epoch` rows.
- [ ] Seal Q-006 only after the minimum distinct-session and candidate gates
  pass.
- [ ] Have the user enter every `quality-label` answer in an external terminal;
  the agent may explain fields but may not enter or infer answers.
- [ ] Run immutable `quality-gate`. Phase 5/QUALITY-01 completes only on
  terminal `PASS`; FAIL or INVALID opens a changed-provenance successor
  instead of weakening thresholds.
- [ ] Update the canonical report and GSD planning state with actual evidence,
  then hand off to a fresh Phase 6 design and implementation plan. M1 remains
  open until Phases 6 through 11 and the milestone audit also complete.
