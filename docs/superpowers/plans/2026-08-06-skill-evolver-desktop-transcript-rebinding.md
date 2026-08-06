# Skill Evolver Desktop Transcript Rebinding Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Recover legitimate first-generation Desktop transcript replacement,
tolerate one append race, expose the queue's truly claimable subset, release
the change as `0.1.5`, and transition quality provenance from Q-004 to Q-005
without reading private transcripts outside explicit Review.

**Architecture:** Keep Hook capture and maintenance metadata-only. Narrowly
allow a strictly newer Stop to converge a pending, never-reviewed first row;
otherwise preserve the existing epoch-binding rules. During explicit Review,
prefer the captured inode, then allow one exact-path replacement only for
generation 1 / epoch 0 / byte 0 after descriptor security checks and initial
`session_meta` HMAC binding. Execute at most two complete bounded reads, with
the retry pinned to the first selected device/inode. Derive status counts from
the existing claim predicate and sanitize every quarantine reason.

**Tech Stack:** `/usr/bin/python3` 3.9+, Python standard library, SQLite schema
v1, `unittest`, local Codex plugin marketplace, Git.

## Global Constraints

- Work in `/Users/igyeongseob/Documents/오픈소스/skill-evolver` on the current
  branch. Preserve unrelated sibling directories `n8n/` and `neo4j/`.
- Add no dependency, database server, schema migration, background worker,
  transcript copy, full-prefix hash, sleep, polling loop, network call, or
  model call.
- Automatic Stop and explicit maintenance remain metadata-only. Only explicit
  Review may open transcript content.
- Keep `SCHEMA_VERSION = 1` and format name `codex-rollout-jsonl-v2`.
- Never change `FrozenTranscript.locator`, its digest, `frozen_from`, or
  `frozen_to` after a claim. A selected read identity is attempt-local state.
- No `pread` may request a byte at or beyond `frozen_to`; every request must
  satisfy `offset + length <= frozen_to`.
- A replacement is exact `frozen.locator.path` only. Never use mutable
  `FrozenTranscript.read_path` as the replacement authority and never scan
  transcript roots for another inode by declared session ID.
- Preserve original-inode header behavior. Only replacement-header failures
  are normalized to retryable `transcript_changed`.
- Keep Review, labels, Apply, Undo, quality gate, and epoch open behind their
  existing independent approval boundaries. Agents must never answer
  `quality-label` prompts.
- Runtime-mutating Q-004 gate, plugin install, and Q-005 open commands each
  require their own explicit approval and fully expanded literal command.
- Do not bulk-revive the existing 58 `transcript_changed` rows or the separate
  `pending_epoch` row. Only a naturally arriving, strictly newer Stop may
  recover an eligible row.
- Stage explicit files only; never use `git add .`.

## Current Evidence

- Approved design:
  `docs/superpowers/specs/2026-08-06-skill-evolver-desktop-transcript-rebinding-design.md`.
- Source head before this plan: `177f345`.
- Source, installed plugin, and current quality provenance are `0.1.4` / Q-004.
- The last full source suite reported 500 passing tests and 3 skips.
- The metadata-only audit found 58 pending rows quarantined as
  `transcript_changed`: 39 current paths had a different inode and 19 original
  inodes had grown. One unrelated `unsupported_transcript` row is out of
  scope.
- Existing claim SQL is the canonical predicate:

```sql
status='pending'
AND binding_status='accepted'
AND error_code IS NULL
AND observed_boundary > reviewed_boundary
```

## File Responsibility Map

- `skills/skill-evolver/scripts/evolver.py`: Stop convergence, exact-path
  replacement binding, bounded reread, adapter contract, queue status, and
  coordinated runtime identity.
- `skills/skill-evolver/tests/test_capture.py`: signed spool convergence,
  strict-state matrix, metadata-only proof, status partition, and version
  identity.
- `skills/skill-evolver/tests/test_review.py`: descriptor security, HMAC
  binding, bounded reread, no-read-past-bound, adapter contract, and integrated
  Review behavior.
- `skills/skill-evolver/tests/test_quality_gate.py`: existing provenance-drift
  and predecessor regressions; modify only if a focused expectation proves
  necessary.
- `.codex-plugin/plugin.json`: plugin release `0.1.5`.
- `skills/skill-evolver/references/runtime.json`: runtime release `0.1.5`.
- `README.md`: user-facing `0.1.5` behavior and status fields.
- `docs/release-reports/quality/`: canonical Q-004 terminal report after the
  separately approved gate.
- `.planning/PROJECT.md`, `.planning/ROADMAP.md`, `.planning/STATE.md`: actual
  installed version and Q-005 state only after those events occur.

---

### Task 1: Converge a newer Stop only for a never-reviewed first row

**Files:**

- Modify: `skills/skill-evolver/tests/test_capture.py`
- Modify: `skills/skill-evolver/scripts/evolver.py`

**Interfaces:**

- Consumes: `upsert_session(connection, event, key, config, now) -> str` and
  the already authenticated `CapturedSessionStop` produced by spool import.
- Produces: an `advanced` first-row transition that stores the newer locator,
  keeps generation/epoch/cursor/pending age, restores `accepted`, and clears
  only a fixed locator-retry error.
- Preserves: stale/equal Stop rejection, same-inode shrink and same-size
  path/mtime rules, later-state `pending_epoch`, capacity accounting, and the
  metadata-only maintenance boundary.

- [ ] **Step 1: Add the RED convergence and exclusion matrix**

Add tests to `GenerationStateTests` covering all of these rows:

```python
eligible = {
    "status": "pending",
    "generation": 1,
    "transcript_epoch": 0,
    "reviewed_boundary": 0,
}
ineligible = (
    {**eligible, "status": "reviewing"},
    {**eligible, "generation": 2},
    {**eligible, "transcript_epoch": 1},
    {**eligible, "reviewed_boundary": 1},
)
```

For the eligible row, seed both practical stale-runtime shapes:

1. stored captured inode plus `error_code='transcript_changed'`;
2. stored replacement inode plus `binding_status='pending_epoch'` and
   `error_code='transcript_rebind_required'`.

Send a strictly newer event and assert:

```python
self.assertEqual(outcome, "advanced")
self.assertEqual(row["generation"], 1)
self.assertEqual(row["transcript_epoch"], 0)
self.assertEqual(row["reviewed_boundary"], 0)
self.assertEqual(row["binding_status"], "accepted")
self.assertIsNone(row["error_code"])
self.assertEqual(row["pending_since"], original_pending_since)
```

Assert every ineligible row keeps the existing `pending_epoch` /
`transcript_rebind_required` behavior. Add stale and equal `observed_at_ns`
cases proving neither can recover the row.

The primary eligible test uses the existing `setUp()` state verbatim:

```python
def test_strictly_newer_stop_refreshes_unreviewed_first_locator(
    self,
) -> None:
    connection = self.runtime.open_database(self.installation)
    before = connection.execute(
        "SELECT * FROM review_items WHERE session_key=?",
        (self.key,),
    ).fetchone()
    replacement = self.sessions / "replacement-first.jsonl"
    replacement.write_bytes(self.transcript.read_bytes())
    raw = json.dumps(
        {**self.payload, "transcript_path": str(replacement)}
    ).encode()
    event = self.runtime.parse_session_stop(
        raw, self.installation, self.runtime_config
    )
    assert event is not None
    event = replace(
        event, observed_at_ns=int(before["last_stop_ns"]) + 1
    )
    connection.execute(
        "UPDATE review_items SET error_code='transcript_changed' "
        "WHERE session_key=?",
        (self.key,),
    )
    connection.commit()

    outcome = self.runtime.upsert_session(
        connection,
        event,
        self.key,
        self.runtime_config,
        2_000_000_001.0,
    )
    row = connection.execute(
        "SELECT * FROM review_items WHERE session_key=?",
        (self.key,),
    ).fetchone()
    connection.close()

    self.assertEqual(outcome, "advanced")
    self.assertEqual(row["transcript_path"], str(replacement))
    self.assertEqual(row["transcript_inode"], replacement.stat().st_ino)
    self.assertEqual(row["generation"], 1)
    self.assertEqual(row["transcript_epoch"], 0)
    self.assertEqual(row["reviewed_boundary"], 0)
    self.assertEqual(row["binding_status"], "accepted")
    self.assertIsNone(row["error_code"])
    self.assertEqual(row["pending_since"], before["pending_since"])
```

Add a separate matrix test with these literal seed/update/expected tuples:

```python
cases = (
    (
        "reviewing",
        {"status": "reviewing"},
        ("reviewing", 1, 0, 0, "pending_epoch"),
    ),
    (
        "generation_2",
        {"generation": 2},
        ("pending", 2, 0, 0, "pending_epoch"),
    ),
    (
        "epoch_1",
        {"transcript_epoch": 1},
        ("pending", 1, 1, 0, "pending_epoch"),
    ),
    (
        "reviewed_1",
        {"reviewed_boundary": 1},
        ("pending", 1, 0, 1, "pending_epoch"),
    ),
)
```

For each tuple, reset the row to the `setUp()` locator and accepted binding,
apply the named SQL column update, create a unique replacement file, send an
event with `observed_at_ns = last_stop_ns + 1`, and assert the complete
expected tuple plus `error_code == "transcript_rebind_required"`. The
same-inode recovery test seeds
`binding_status='pending_epoch', error_code='transcript_rebind_required'` on
the event's own inode and uses `observed_at_ns = last_stop_ns + 1`; the equal-
time companion uses exactly `last_stop_ns` and must remain `pending_epoch`.

- [ ] **Step 2: Prove the RED test fails for the intended reason**

Run the stable file-discovery command:

```bash
/usr/bin/python3 -I -m unittest discover \
  -s skills/skill-evolver/tests -p 'test_capture.py' -v
```

Record that the new eligible expectations fail because the old code returns
`pending_epoch`; do not accept a syntax, fixture, or setup failure as RED.

- [ ] **Step 3: Implement the narrow shared transition**

In `upsert_session()`, calculate persisted state before deciding rebinding and
separate the raw locator condition from the first-row exception:

```python
status = str(row["status"])
generation = int(row["generation"])
transcript_epoch = int(row["transcript_epoch"])
reviewed_boundary = int(row["reviewed_boundary"])

raw_needs_rebind = (
    pending_binding
    or not same_identity
    or shrank
    or same_size_locator_change
)
refreshable_first_generation = (
    status == "pending"
    and generation == 1
    and transcript_epoch == 0
    and reviewed_boundary == 0
)
refreshes_locator = (
    refreshable_first_generation
    and event_time_ns > prior_time_ns
    and (
        not same_identity
        or (
            pending_binding
            and not shrank
            and not same_size_locator_change
        )
    )
)
needs_rebind = raw_needs_rebind and not refreshes_locator
new_work = raw_needs_rebind or (
    same_identity and event.transcript_size > reviewed_boundary
)
```

Do not broaden `refreshes_locator` to same-inode shrink or same-size path/mtime
changes. When `refreshes_locator` is true, clear only `None`,
`transcript_missing`, `transcript_changed`, `transcript_partial`, or
`transcript_rebind_required`; preserve an unknown/tampered stored error.

Use this exact assignment after the existing generation/status transition:

```python
stored_error = row["error_code"]
locator_refresh_errors = (
    TRANSCRIPT_RETRYABLE_CODES | {"transcript_rebind_required"}
)
binding_status = "pending_epoch" if needs_rebind else "accepted"
if needs_rebind:
    error_code = "transcript_rebind_required"
elif refreshes_locator:
    error_code = (
        None
        if stored_error is None or stored_error in locator_refresh_errors
        else str(stored_error)
    )
else:
    error_code = None
```

The strictly-newer requirement remains the existing `stale_observation`
guard plus the explicit `event_time_ns > prior_time_ns` conjunct above. The
extra conjunct is required because the legacy guard permits equal-time growth
on the same inode. Do not add a second clock or provenance field.

- [ ] **Step 4: Add signed spool-to-maintenance integration proof**

In `MaintenanceStatusTests`, exercise the real boundary:

```text
Stop A -> spool_session_stop -> import_spool
replace exact path/inode
Stop B with larger observed_at_ns -> spool_session_stop -> import_spool
```

Patch `_initial_session_meta` and `read_frozen_transcript` to raise if called;
assert both imports succeed, the final row is claimable by the canonical SQL,
and no transcript reader ran. Update
`test_same_second_spool_replay_keeps_newest_different_inode` so its eligible
first-row expectation is `accepted` while keeping its newest-event ordering
assertions.

- [ ] **Step 5: Run focused tests and commit**

```bash
/usr/bin/python3 -I -m unittest discover \
  -s skills/skill-evolver/tests -p 'test_capture.py' -v
git diff --check
```

Commit only the two task files:

```bash
git add skills/skill-evolver/scripts/evolver.py \
  skills/skill-evolver/tests/test_capture.py
git commit -m "fix(skill-evolver): refresh unreviewed Stop locator"
```

---

### Task 2: Bind an eligible exact-path replacement during explicit Review

**Files:**

- Modify: `skills/skill-evolver/tests/test_review.py`
- Modify: `skills/skill-evolver/scripts/evolver.py`

**Interfaces:**

- Consumes: immutable `FrozenTranscript`, installed transcript roots,
  `_initial_session_meta()`, and the existing same-inode relocation scan.
- Produces: one opened descriptor with selected `(device, inode)`, resolved
  path, and a `rebound` flag; replacement is possible only for generation 1,
  frozen epoch 0, and `frozen_from == 0`.
- Preserves: captured-inode and same-inode relocation behavior, frozen claim
  digest, numeric boundary, existing terminal/partial header semantics, and
  retryable `transcript_changed` compatibility.

- [ ] **Step 1: Add RED identity, boundary, and HMAC tests**

Extend `FrozenTranscriptIdentityTests` with:

- an exact captured-path replacement with matching first `session_meta` that
  exports records while preserving `frozen_to` and the locator digest;
- a replacement whose bytes below `frozen_to` differ, accepted only in the
  eligible first state;
- malformed, missing, partial, and HMAC-mismatched replacement headers, all
  mapped to retryable `transcript_changed`;
- independent rejections for generation 2, frozen epoch 1, and
  `frozen_from > 0`, including epoch adoption that reset the DB cursor to zero;
- leaf symlink, parent symlink/swap, FIFO or other non-regular file, mocked
  wrong UID, outside-root path, and file shorter than `frozen_to`;
- a same-session replacement at a different path, proving no replacement
  search occurs.

The primary success test is:

```python
def test_first_generation_exact_path_replacement_is_session_bound(
    self,
) -> None:
    session_id = "replacement-bound"
    header = self.fixture_lines[0].replace(
        b"fixture-session", session_id.encode()
    )
    delta = self.fixture_lines[5]
    connection, transcript, frozen = self.capture_and_claim(
        [header, delta],
        reviewed_boundary=0,
        session_id=session_id,
    )
    original_digest = self.runtime.transcript_locator_digest(
        frozen.locator
    )
    preserved = self.base / "preserved-captured-inode.jsonl"
    transcript.rename(preserved)
    replacement_delta = delta.replace(b"correction", b"reflection")
    self.assertEqual(len(replacement_delta), len(delta))
    transcript.write_bytes(header + replacement_delta)
    try:
        exported = self.runtime.read_frozen_transcript(
            self.installation,
            frozen,
            self.config,
            self.review,
        )
        self.assertEqual(
            [record.text for record in exported.records],
            ["sanitized direct reflection"],
        )
        self.assertEqual(
            self.runtime.transcript_locator_digest(frozen.locator),
            original_digest,
        )
        self.assertEqual(frozen.frozen_to, len(header) + len(delta))
        self.assertNotEqual(
            (transcript.stat().st_dev, transcript.stat().st_ino),
            (frozen.locator.device, frozen.locator.inode),
        )
    finally:
        connection.close()
```

For the mismatched-header companion, replace the replacement header's session
ID, call the same reader, and assert the caught `TranscriptAdapterError` has
`code == "transcript_changed"` and `retryable is True`. Use
`dataclasses.replace(frozen, generation=2)`,
`dataclasses.replace(frozen, transcript_epoch=1)`, and
`dataclasses.replace(frozen, frozen_from=1)` for the three independent state
rejections.

Keep the existing
`test_same_inode_relocation_is_accepted_but_replacement_is_changed` unchanged:
it has `frozen_from > 0` and is the later-state compatibility proof.

- [ ] **Step 2: Run the exact replacement test against 0.1.4**

```bash
/usr/bin/python3 -I -m unittest discover \
  -s skills/skill-evolver/tests -p 'test_review.py' -v
```

Confirm the new matching-session replacement fails as
`transcript_changed`. Preserve this terminal output in the implementation
handoff; it is the required old-adapter RED evidence.

- [ ] **Step 3: Add a single eligibility predicate**

Near the frozen transcript helpers, add only the shared decision needed by
open and tests:

```python
def _replacement_rebinding_allowed(
    frozen: FrozenTranscript,
) -> bool:
    return (
        frozen.generation == 1
        and frozen.transcript_epoch == 0
        and frozen.frozen_from == 0
    )
```

Do not consult the live DB cursor or mutate the frozen locator.

- [ ] **Step 4: Open the replacement descriptor-relatively and fail closed**

Keep `_open_matching_transcript()` and `_find_relocated_transcript()` pinned to
the captured identity. Change `_open_matching_transcript()` to return
`(descriptor_or_none, identity_mismatch)`: missing is `(None, False)`, a
regular/current-owner descriptor with the wrong identity is closed and
returned as `(None, True)`, and every path/root/type/owner security failure
still raises `transcript_changed`.

Add one exact-path replacement opener that:

1. chooses an installed canonical root containing `frozen.locator.path`, gets
   a lexical `relative_to(root)` path, and rejects an empty component or any
   component equal to `.` or `..`;
2. opens the root and each child directory with `O_DIRECTORY | O_NOFOLLOW`;
3. opens the leaf relative to the final directory with
   `O_RDONLY | O_NONBLOCK | O_NOFOLLOW`;
4. `fstat`s every opened object and requires directories/regular file,
   current UID, and a leaf size at least `frozen_to`;
5. returns the descriptor's actual `(st_dev, st_ino)` without writing it back
   to `FrozenTranscript`.

The resulting open contract is logically:

```python
def _open_frozen_transcript(
    installation: Installation,
    frozen: FrozenTranscript,
) -> tuple[int, Path, tuple[int, int], bool]:
    """Return descriptor, selected path, selected identity, rebound."""
```

Opening order is captured inode at the current read path, existing captured-
inode relocation, then eligible replacement at exactly
`frozen.locator.path`. Catch only `transcript_missing` from the relocation scan
before trying the exact-path replacement. Security failures and relocation
scan saturation are `transcript_changed` and must not fall through. If
eligibility is false, return `transcript_changed` for a known direct identity
mismatch and retain `transcript_missing` for a genuinely missing captured
inode. Never fall through to session-ID search.

Parameterize descriptor stability with the selected identity and binding mode
so the next call does not accidentally re-compare a replacement to the frozen
inode:

```python
def _stable_frozen_stat(
    info: os.stat_result,
    frozen: FrozenTranscript,
    selected_identity: tuple[int, int],
    rebound: bool,
) -> tuple[int, int, int, int]:
    if (
        not stat.S_ISREG(info.st_mode)
        or info.st_uid != os.getuid()
        or (info.st_dev, info.st_ino) != selected_identity
        or info.st_size < frozen.frozen_to
        or (
            not rebound
            and info.st_size == frozen.locator.size
            and info.st_mtime_ns != frozen.locator.mtime_ns
        )
    ):
        raise _transcript_error("transcript_changed")
    return (
        info.st_dev,
        info.st_ino,
        info.st_size,
        info.st_mtime_ns,
    )
```

`_stable_frozen_descriptor_stat()` receives and forwards the same two new
arguments. Original and relocated captured-inode reads pass `rebound=False`;
only a selected different inode passes `rebound=True`.

- [ ] **Step 5: Normalize only replacement-header errors**

After `_initial_session_meta()` and before any record is exportable, retain
the error until the header stability check has completed:

```python
header_error: Optional[TranscriptAdapterError] = None
try:
    _initial_session_meta(descriptor, installation, frozen)
except TranscriptAdapterError as error:
    header_error = error
after_header = _stable_frozen_descriptor_stat(
    descriptor, frozen, selected_identity, rebound
)
if after_header != before:
    raise _transcript_error("transcript_changed")
if header_error is not None:
    if rebound:
        raise _transcript_error("transcript_changed") from None
    raise header_error
```

This preserves data-change precedence. A replacement result is not returned
until the initial HMAC check succeeds.

- [ ] **Step 6: Add immutable adapter-contract fields**

Add the exact replacement object to `transcript_adapter_contract()` and assert
its entire value in `test_transcript_adapter_digest_is_static`:

```python
"replacement_rebinding": {
    "path": "exact-frozen-locator",
    "eligibility": "generation-1-epoch-0-from-0",
    "session_binding": "initial-session-meta-hmac",
    "frozen_to": "preserve-signed-numeric-boundary",
    "historical_prefix_identity": False,
    "later_state": "strict-device-inode",
    "stop_convergence": {
        "event_order": "strictly-newer-observed-at-ns",
        "eligibility": "pending-generation-1-epoch-0-reviewed-0",
        "session_binding": "signed-stop-session-key",
        "transcript_read": False,
    },
},
```

Assert that changing this object changes `transcript_adapter_digest()`. Task 3
adds `read_stability` in the same commit as the behavior it describes.

- [ ] **Step 7: Run focused identity/contract tests and commit**

```bash
/usr/bin/python3 -I -m unittest discover \
  -s skills/skill-evolver/tests -p 'test_review.py' -v
git diff --check
```

```bash
git add skills/skill-evolver/scripts/evolver.py \
  skills/skill-evolver/tests/test_review.py
git commit -m "fix(skill-evolver): rebind first transcript generation"
```

---

### Task 3: Retry one pure append race and discard the first result

**Files:**

- Modify: `skills/skill-evolver/tests/test_review.py`
- Modify: `skills/skill-evolver/scripts/evolver.py`

**Interfaces:**

- Consumes: the selected path/identity/rebound result from Task 2.
- Produces: one stable `TranscriptExport` after one or two complete bounded
  attempts.
- Preserves: a maximum of two opens, all byte/record limits, retryable failure
  cursor semantics, and no rebind/relocation on retry.

- [ ] **Step 1: Split the old append expectation into RED success/failure**

Replace only the append-during-read subcase of
`test_missing_and_during_read_change_are_retryable` with these tests:

```text
test_same_inode_growth_rereads_once_and_succeeds
test_growth_on_both_attempts_is_retryable_changed
test_retry_identity_swap_is_retryable_changed
```

Instrument `os.open`, `os.fstat`, and `_read_exact_at` narrowly enough to
assert:

- exactly two complete open/read attempts after first-attempt growth;
- no records or buffers derived from attempt 1 survive;
- retry uses the first selected path and `(device, inode)`;
- retry does not invoke replacement rebinding or relocation;
- double growth and identity swap are `transcript_changed`;
- every read call satisfies `offset + length <= frozen_to`.

Keep same-size mtime mutation, shrink, partial boundary, and pre-existing
suffix tests strict.

The one-growth success case starts from the existing subcase and changes only
the expected outcome:

```python
def test_same_inode_growth_rereads_once_and_succeeds(self) -> None:
    session_id = "changing-during-read"
    header = self.fixture_lines[0].replace(
        b"fixture-session", session_id.encode()
    )
    delta = self.fixture_lines[5]
    connection, transcript, frozen = self.capture_and_claim(
        [header, delta],
        reviewed_boundary=len(header),
        session_id=session_id,
    )
    real_pread = os.pread
    real_reopen = self.runtime._reopen_selected_transcript
    appended = False
    reopen_count = 0

    def append_once(descriptor: int, length: int, offset: int) -> bytes:
        nonlocal appended
        self.assertLessEqual(offset + length, frozen.frozen_to)
        result = real_pread(descriptor, length, offset)
        if not appended:
            appended = True
            with transcript.open("ab") as stream:
                stream.write(b'{"type":"event_msg","payload":{}}\n')
        return result

    def count_reopen(*args, **kwargs):
        nonlocal reopen_count
        reopen_count += 1
        return real_reopen(*args, **kwargs)

    try:
        with mock.patch.object(
            self.runtime.os, "pread", side_effect=append_once
        ), mock.patch.object(
            self.runtime,
            "_reopen_selected_transcript",
            side_effect=count_reopen,
        ):
            exported = self.runtime.read_frozen_transcript(
                self.installation,
                frozen,
                self.config,
                self.review,
            )
        self.assertTrue(appended)
        self.assertEqual(reopen_count, 1)
        self.assertEqual(
            [record.text for record in exported.records],
            ["sanitized direct correction"],
        )
    finally:
        connection.close()
```

The double-growth test resets the append guard after the tracked reopen and
asserts retryable `transcript_changed`; the identity-swap test replaces the
path immediately before `_reopen_selected_transcript` and asserts the same
error without another call to `_open_frozen_transcript`.

- [ ] **Step 2: Extract one complete bounded attempt**

Move the current header, delta, reverse-context, parse, limits, and final-stat
logic into one private helper. It returns the parsed attempt plus before/after
stat tuples, but never commits a cursor:

```python
@dataclass(frozen=True)
class _TranscriptReadAttempt:
    records: tuple[TranscriptRecord, ...]
    delta_source_bytes: int
    context_source_bytes: int
    canonical_records_bytes: int
    before: tuple[int, int, int, int]
    after: tuple[int, int, int, int]
    error: Optional[TranscriptAdapterError]
```

If a local tuple is shorter and equally clear, prefer it; do not create a new
module or public interface. Preserve the existing rule that stat instability
overrides a captured parsing error. The helper takes `selected_identity` and
`rebound`, records the stat immediately before the header, and records another
stat immediately after the header. If that intermediate stat changed, return
an attempt with empty records and that changed tuple without parsing the
delta. The coordinator then applies the same first-attempt growth rule. This
covers growth during header I/O as well as growth during delta/context I/O.

- [ ] **Step 3: Add the two-attempt coordinator**

Use this exact retry decision:

```python
def _same_identity_growth(
    before: tuple[int, int, int, int],
    after: tuple[int, int, int, int],
) -> bool:
    return (
        after[:2] == before[:2]
        and after[2] > before[2]
        and after[3] >= before[3]
    )
```

On attempt 1 pure growth, discard the attempt object, close the descriptor,
reopen the same selected path with the selected identity, and reread the full
bounded operation once. Require retry-open size and mtime to be at least the
first attempt's final size and mtime, then require attempt 2
`before == after`; otherwise raise `transcript_changed`. Any first-attempt
delta other than pure growth fails without retry. If an attempt has both a
parse/header error and stat instability, classify stat instability first so
the existing data-change precedence remains intact.

Do not copy attempt-1 records into an outer list. Construct `TranscriptExport`
only from the stable final attempt.

The retry opener has this closed interface:

```python
def _reopen_selected_transcript(
    path: Path,
    installation: Installation,
    frozen: FrozenTranscript,
    selected_identity: tuple[int, int],
    minimum_stat: tuple[int, int, int, int],
) -> int:
    """Exact-path reopen; no relocation and no replacement rebinding."""
```

It uses the descriptor-relative no-follow opener from Task 2, requires the
exact selected identity, and requires size/mtime at least `minimum_stat`.

Add the following exact contract object in this task, and assert both its full
value and digest participation:

```python
"read_stability": {
    "attempts_max": 2,
    "first_retry": "same-device-inode-size-growth-only",
    "discard_first_attempt": True,
    "retry_identity": "pin-first-selected-device-inode",
    "retry_rebinding": False,
    "sleep_or_poll": False,
},
```

- [ ] **Step 4: Run Review tests and commit**

```bash
/usr/bin/python3 -I -m unittest discover \
  -s skills/skill-evolver/tests -p 'test_review.py' -v
git diff --check
```

```bash
git add skills/skill-evolver/scripts/evolver.py \
  skills/skill-evolver/tests/test_review.py
git commit -m "fix(skill-evolver): retry one transcript append race"
```

---

### Task 4: Expose claimable and quarantined queue inventory safely

**Files:**

- Modify: `skills/skill-evolver/tests/test_capture.py`
- Modify: `skills/skill-evolver/scripts/evolver.py`

**Interfaces:**

- Consumes: read-only `review_items` rows and the exact claim predicate used by
  `_prepare_review_batch()`.
- Produces: `claimable_sessions`, `quarantined_sessions`, and
  `quarantined_by_error` in `queue_status()`.
- Preserves: all existing status keys, aggregate timestamps, read-only DB
  behavior, and zero transcript filesystem reads.

- [ ] **Step 1: Add RED partition, precedence, privacy, and immutability test**

Create pending rows for:

1. one normal claimable generation;
2. one `transcript_changed` row;
3. one pending binding with a stored error, counted under the stored error;
4. one pending binding without an error, counted as `binding_pending`;
5. one accepted empty generation, counted as `empty_generation`;
6. one arbitrary/private-looking DB error, counted as `unknown_error`.

Assert:

```python
self.assertEqual(
    status["pending_sessions"],
    status["claimable_sessions"] + status["quarantined_sessions"],
)
self.assertEqual(
    status["quarantined_sessions"],
    sum(status["quarantined_by_error"].values()),
)
self.assertEqual(status["quarantined_by_error"]["binding_pending"], 1)
self.assertEqual(status["quarantined_by_error"]["empty_generation"], 1)
self.assertEqual(status["quarantined_by_error"]["unknown_error"], 1)
```

Require `binding_pending` and `empty_generation` keys even at zero. Serialize
the result and assert it contains no session ID, path, device, inode,
per-session timestamp, transcript text, or arbitrary stored error. Compare DB
bytes and `connection.total_changes` before/after the status call.

- [ ] **Step 2: Implement one bounded classification query**

Use the canonical predicate unchanged for `claimable_sessions`. Run one SQLite
statement so a concurrent Stop cannot split the partition across snapshots.
Classify every pending row exactly once with precedence claimable, stored
error, binding, empty:

```sql
WITH classified AS (
  SELECT pending_since,
    CASE
      WHEN binding_status='accepted'
       AND error_code IS NULL
       AND observed_boundary>reviewed_boundary
      THEN 'claimable'
      WHEN error_code IN (?,?,?,?,?) THEN error_code
      WHEN error_code IS NOT NULL THEN 'unknown_error'
      WHEN binding_status!='accepted' THEN 'binding_pending'
      ELSE 'empty_generation'
    END AS bucket
  FROM review_items
  WHERE status='pending'
)
SELECT bucket,COUNT(*) AS count,
       (SELECT MIN(pending_since) FROM classified) AS oldest
FROM classified
GROUP BY bucket
```

Bind the five parameters from this closed set, with no additional values:

```python
QUEUE_STATUS_ERROR_CODES = tuple(
    sorted(
        TRANSCRIPT_RETRYABLE_CODES
        | {
            "transcript_rebind_required",
            "session_binding_unavailable",
        }
    )
)
```

The closed set is `session_binding_unavailable`, `transcript_changed`,
`transcript_missing`, `transcript_partial`, and
`transcript_rebind_required`. The `CASE` collapses every other stored string
to `unknown_error`; never use arbitrary DB text as an output key.

Initialize fixed buckets before accumulating:

```python
quarantined_by_error = {
    **{code: 0 for code in QUEUE_STATUS_ERROR_CODES},
    "binding_pending": 0,
    "empty_generation": 0,
    "unknown_error": 0,
}
```

Build a local `counts` map from the returned rows, remove the `claimable`
bucket into `claimable_sessions`, and sum every remaining bucket into
`quarantined_sessions`. `pending_sessions` is the sum of all returned row
counts; `oldest` comes from the query's repeated aggregate value. Raise
`sqlite3.DatabaseError("invalid_queue_status_partition")` if either partition
equation is false. Status must not stat or open a transcript.

Initialize the empty result before iterating so a zero-row grouped result is
well-defined:

```python
counts: dict[str, int] = {}
oldest: Optional[str] = None
for row in queue_rows:
    counts[str(row["bucket"])] = int(row["count"])
    if row["oldest"] is not None:
        oldest = str(row["oldest"])
pending_sessions = sum(counts.values())
claimable_sessions = counts.pop("claimable", 0)
```

Add an empty-database status case that asserts `pending_sessions == 0`,
`claimable_sessions == 0`, `quarantined_sessions == 0`,
`oldest_pending_age_seconds is None`, and all fixed quarantine buckets are
present with zero values.

- [ ] **Step 3: Run focused capture/status tests and commit**

```bash
/usr/bin/python3 -I -m unittest discover \
  -s skills/skill-evolver/tests -p 'test_capture.py' -v
git diff --check
```

```bash
git add skills/skill-evolver/scripts/evolver.py \
  skills/skill-evolver/tests/test_capture.py
git commit -m "feat(skill-evolver): expose claimable review inventory"
```

---

### Task 5: Prove the full flow and release source version 0.1.5

**Files:**

- Modify: `skills/skill-evolver/tests/test_review.py`
- Modify: `skills/skill-evolver/tests/test_capture.py`
- Modify: `skills/skill-evolver/scripts/evolver.py`
- Modify: `.codex-plugin/plugin.json`
- Modify: `skills/skill-evolver/references/runtime.json`
- Modify: `README.md`

**Interfaces:**

- Consumes: Tasks 1–4 and existing batch Review primitives.
- Produces: coordinated `skill-evolver 0.1.5` source and one real-boundary
  integration regression.
- Preserves: `no_exportable_sessions`, existing missing/partial/unsupported
  behavior, quality thresholds, and all 0.1.4 persistence formats.

- [ ] **Step 1: Add the full signed Stop-to-Review regression**

Use existing test fixtures but do not mock `read_frozen_transcript`:

```text
signed Stop -> spool -> replace exact captured path -> import/maintain
-> prepare Review batch -> bounded read -> matching HMAC -> export
```

Assert the successful envelope contains only records below `frozen_to`. Add a
mismatched replacement companion proving the batch returns
`no_exportable_sessions`, row status returns to `pending`,
`reviewed_boundary == 0`, and `error_code == 'transcript_changed'`.

Add this method to `ReviewBatchIntegrationTests`:

```python
def test_spooled_stop_replaced_before_import_exports_first_generation(
    self,
) -> None:
    now = 2_000_000_000.0
    session_id = "spooled-replacement"
    header = (
        TEST_ROOT / "fixtures/review-current-layout.jsonl"
    ).read_bytes().splitlines(keepends=True)[0].replace(
        b"fixture-session", session_id.encode()
    )
    delta = (
        TEST_ROOT / "fixtures/review-current-layout.jsonl"
    ).read_bytes().splitlines(keepends=True)[5]
    transcript = self.sessions / f"{session_id}.jsonl"
    transcript.write_bytes(header + delta)
    payload = {
        "hook_event_name": "Stop",
        "session_id": session_id,
        "cwd": str(self.workspace),
        "transcript_path": str(transcript),
    }
    event = self.runtime.parse_session_stop(
        json.dumps(payload).encode(), self.installation, self.config
    )
    assert event is not None
    event = replace(
        event, observed_at_ns=int(now * 1_000_000_000)
    )
    key = self.runtime.session_key(self.installation, session_id)
    self.assertTrue(
        self.runtime.spool_session_stop(
            self.installation,
            self.config,
            event,
            key,
            now,
            coalesce=True,
        )
    )
    preserved = self.base / "captured-before-import.jsonl"
    transcript.rename(preserved)
    replacement_delta = delta.replace(b"correction", b"reflection")
    transcript.write_bytes(header + replacement_delta)

    connection = self.runtime.open_database(self.installation)
    imported = self.runtime.import_spool(
        connection, self.installation, self.config, now + 1
    )
    real_read = self.runtime._read_exact_at

    def bounded_read(descriptor: int, start: int, length: int) -> bytes:
        self.assertLessEqual(start + length, event.transcript_size)
        return real_read(descriptor, start, length)

    with self.fixed_review_inputs(), mock.patch.object(
        self.runtime, "_read_exact_at", side_effect=bounded_read
    ):
        result = self.runtime.claim_review_batch(
            connection,
            self.installation,
            self.config,
            now + 2,
        )
    contract = self.runtime.load_review_contract(
        connection, int(result["batch_id"]), "final"
    )
    connection.close()

    self.assertEqual(imported["spool_imported"], 1)
    self.assertEqual(result["status"], "ready")
    self.assertEqual(len(contract["sessions"]), 1)
    self.assertEqual(contract["sessions"][0]["frozen_from"], 0)
    self.assertEqual(
        contract["sessions"][0]["frozen_to"], event.transcript_size
    )
    self.assertTrue(contract["sessions"][0]["records"])
```

The mismatch method uses the same setup but replaces
`b"spooled-replacement"` with the same-length
`b"spooled-replacemenx"` in the replacement header. Wrap
`claim_review_batch` in
`assertRaisesRegex(ValueError, "no_exportable_sessions")`, then query and
assert the exact row tuple
`("pending", 0, "transcript_changed", None)` for
`status, reviewed_boundary, error_code, batch_id`.

- [ ] **Step 2: Coordinate every release identity**

Change all live declarations together:

```text
.codex-plugin/plugin.json                              0.1.5
skills/skill-evolver/references/runtime.json           0.1.5
skills/skill-evolver/scripts/evolver.py VERSION        skill-evolver 0.1.5
load_review_runtime() exact expected version           0.1.5
README.md user-facing current version                  0.1.5
test_capture.py manifest/runtime/loader assertions     0.1.5
```

Do not change schema version or adapter format name.

Update the README's Review section with these exact user-visible facts:

```markdown
- A replaced transcript inode is eligible only for generation 1, transcript
  epoch 0, and a frozen range starting at byte 0, at the exact captured path,
  after initial session metadata binds to the captured session.
- The captured `frozen_to` remains the numeric upper read bound. Rebinding does
  not prove that replacement bytes below that bound are historically identical
  to the inode observed by Stop.
- `status` separates `pending_sessions` into `claimable_sessions` and
  `quarantined_sessions`, with sanitized aggregate
  `quarantined_by_error` counts.
```

- [ ] **Step 3: Run focused and complete verification**

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
/usr/bin/python3 -m json.tool .codex-plugin/plugin.json
/usr/bin/python3 -m json.tool hooks/hooks.json
/usr/bin/python3 -m json.tool \
  skills/skill-evolver/references/runtime.json
/usr/bin/python3 -I skills/skill-evolver/scripts/evolver.py --version
git diff --check
```

Expected version output: `skill-evolver 0.1.5`.

- [ ] **Step 4: Commit the release source**

```bash
git add .codex-plugin/plugin.json README.md \
  skills/skill-evolver/references/runtime.json \
  skills/skill-evolver/scripts/evolver.py \
  skills/skill-evolver/tests/test_capture.py \
  skills/skill-evolver/tests/test_review.py
git commit -m "chore(skill-evolver): release transcript rebinding 0.1.5"
```

---

### Task 6: Terminalize Q-004 before installation and open exact successor Q-005

**Files:**

- Create after command output exists: the Q-004 report file whose basename is
  `Q-004-`, followed by the returned 64-character report digest and `.json`.
- Modify after facts exist: `.planning/PROJECT.md`
- Modify after facts exist: `.planning/ROADMAP.md`
- Modify after facts exist: `.planning/STATE.md`
- Verify unchanged pending requirement: `.planning/REQUIREMENTS.md`

**Interfaces:**

- Consumes: committed and fully tested source `0.1.5`, current installed
  Q-004, and its returned immutable terminal report digest.
- Produces: Q-004 `INVALID/quality_provenance_drift`, installed/cache/source
  `0.1.5` parity, and Q-005 `collecting` with an exact predecessor digest.
- Preserves: Q-004 history, old quarantined rows, user-only labels, and
  `QUALITY-01` pending until Q-005 terminal PASS.

- [ ] **Step 1: Obtain separate approval and terminalize Q-004 once**

Before installing `0.1.5`, present this fully expanded command for approval:

```bash
/usr/bin/python3 -I \
  /Users/igyeongseob/Documents/오픈소스/skill-evolver/skills/skill-evolver/scripts/evolver.py \
  quality-gate \
  --installation /Users/igyeongseob/.codex/skill-evolver/installation.json \
  Q-004
```

Expected exit is `2`; that is a successful terminal non-PASS outcome, not a
technical failure. Require:

```text
decision = INVALID
invalid_reason = quality_provenance_drift
report_digest = one returned 64-character lowercase hex value
```

Do not run `quality-seal`; provenance drift is evaluated first.

- [ ] **Step 2: Materialize and verify the exact Q-004 report**

Write only the returned aggregate report body to the filename formed by
concatenating `Q-004-`, the returned digest, and `.json` under
`docs/release-reports/quality/`. Use `apply_patch`, not shell redirection.
Then execute a literal canonical-JSON SHA-256 check whose path and expected
digest are copied exactly from Step 1. Do not use a shell variable, shell
glob, command substitution, or guessed digest to choose the file for writing
or staging. The verifier below intentionally checks every committed quality
report rather than selecting one mutable target.

After the file exists, this repository-wide verifier checks every quality
report against the digest embedded in its filename, including the new Q-004
report without inserting a dynamic shell value:

```bash
/usr/bin/python3 -I -c 'import hashlib,json; from pathlib import Path; root=Path("/Users/igyeongseob/Documents/오픈소스/skill-evolver/docs/release-reports/quality"); paths=sorted(root.glob("Q-*.json")); triples=[(p,p.stem.rsplit("-",1)[1],hashlib.sha256(json.dumps(json.loads(p.read_text(encoding="utf-8")),sort_keys=True,separators=(",",":"),ensure_ascii=False).encode()).hexdigest()) for p in paths]; bad=[(str(p),expected,actual) for p,expected,actual in triples if expected!=actual]; assert not bad,bad; print("quality-report-digests: PASS",len(triples))'
```

Expected: `quality-report-digests: PASS 2`.

Privacy scan:

```bash
rg -n '"(raw_session_id|transcript_path|owner_token|session_key)"\s*:' \
  /Users/igyeongseob/Documents/오픈소스/skill-evolver/docs/release-reports/quality
```

The command must return no match in the new report.

- [ ] **Step 3: Obtain separate approval and install 0.1.5**

```bash
codex plugin add skill-evolver@skill-evolver-dev --json
codex plugin list --json
```

Never edit or delete cache contents directly. Verify cached version:

```bash
/usr/bin/python3 -I \
  /Users/igyeongseob/.codex/plugins/cache/skill-evolver-dev/skill-evolver/0.1.5/skills/skill-evolver/scripts/evolver.py \
  --version
```

Expected: `skill-evolver 0.1.5`.

- [ ] **Step 4: Prove source/cache parity**

Compare SHA-256 for these exact production files:

```text
.codex-plugin/plugin.json
hooks/hooks.json
README.md
skills/skill-evolver/SKILL.md
skills/skill-evolver/references/runtime.json
skills/skill-evolver/references/improvement-policy.md
skills/skill-evolver/scripts/evolver.py
```

Every source/cache pair must match. Verify the installed plugin listing also
reports `0.1.5`.

Run this exact parity check:

```bash
/usr/bin/python3 -I -c 'import hashlib; from pathlib import Path; source=Path("/Users/igyeongseob/Documents/오픈소스/skill-evolver"); cache=Path("/Users/igyeongseob/.codex/plugins/cache/skill-evolver-dev/skill-evolver/0.1.5"); names=(".codex-plugin/plugin.json","hooks/hooks.json","README.md","skills/skill-evolver/SKILL.md","skills/skill-evolver/references/runtime.json","skills/skill-evolver/references/improvement-policy.md","skills/skill-evolver/scripts/evolver.py"); mismatches=[name for name in names if hashlib.sha256((source/name).read_bytes()).digest()!=hashlib.sha256((cache/name).read_bytes()).digest()]; assert not mismatches,mismatches; print("source-cache-parity: PASS",len(names))'
```

Expected: `source-cache-parity: PASS 7`.

- [ ] **Step 5: Obtain separate approval and open Q-005 from the literal digest**

After Step 1 supplies the exact digest, present one literal command. Its
executable is
`/Users/igyeongseob/.codex/plugins/cache/skill-evolver-dev/skill-evolver/0.1.5/skills/skill-evolver/scripts/evolver.py`;
its arguments are `quality-open`, the installation path
`/Users/igyeongseob/.codex/skill-evolver/installation.json`, and a predecessor
formed by concatenating `Q-004@` with the returned digest. The command shown
for approval must contain the full literal digest, with no interpolation.
Require exit `0`, epoch `Q-005`, exact predecessor, and state `collecting`.

Generate, but do not execute, that exact command with this read-only command:

```bash
/usr/bin/python3 -I -c 'import shlex; from pathlib import Path; root=Path("/Users/igyeongseob/Documents/오픈소스/skill-evolver/docs/release-reports/quality"); paths=list(root.glob("Q-004-*.json")); assert len(paths)==1,paths; digest=paths[0].stem.rsplit("-",1)[1]; assert len(digest)==64 and all(c in "0123456789abcdef" for c in digest),digest; command=["/usr/bin/python3","-I","/Users/igyeongseob/.codex/plugins/cache/skill-evolver-dev/skill-evolver/0.1.5/skills/skill-evolver/scripts/evolver.py","quality-open","--installation","/Users/igyeongseob/.codex/skill-evolver/installation.json","--predecessor","Q-004@"+digest]; print(shlex.join(command))'
```

The printed line is the fully expanded approval request and contains both
required flags, `--installation` and `--predecessor`. Run it only after that
literal line receives separate approval.

- [ ] **Step 6: Verify installed read-only state and update planning facts**

Run:

```bash
/usr/bin/python3 -I \
  /Users/igyeongseob/.codex/plugins/cache/skill-evolver-dev/skill-evolver/0.1.5/skills/skill-evolver/scripts/evolver.py \
  quality-status \
  --installation /Users/igyeongseob/.codex/skill-evolver/installation.json
```

Require current epoch `Q-005`, state `collecting`, and the exact Q-004
predecessor digest. Then update `.planning/PROJECT.md`,
`.planning/ROADMAP.md`, and `.planning/STATE.md` with only observed facts:
installed `0.1.5`, exact Q-004 digest, Q-005 collecting, and mutation
capability still disabled. Keep `QUALITY-01` pending in
`.planning/REQUIREMENTS.md`.

- [ ] **Step 7: Commit provenance report and planning state**

Stage the exact report filename and changed planning files only, then commit:

```bash
git add .planning/PROJECT.md .planning/ROADMAP.md .planning/STATE.md
```

Run one additional `git add` invocation whose sole argument is the complete
Q-004 report path created in Step 2, including the returned digest literally.
Verify `git diff --cached --name-only` lists only those four files. Then run:

```bash
git commit -m "docs(skill-evolver): open rebinding quality epoch"
```

---

### Task 7: Collect production proof without auto-labeling

**Files:**

- Modify only after evidence exists: `.planning/STATE.md`
- Create only from aggregate outputs: the Q-005 report file whose basename is
  `Q-005-`, followed by the returned 64-character report digest and `.json`.

**Interfaces:**

- Consumes: post-open real completed sessions on installed `0.1.5`.
- Produces: explicit Review results and user-only quality labels for at least
  ten distinct sessions, followed by the immutable Q-005 gate.
- Preserves: no fixture/repeated-generation counting, no automatic labels,
  and Phase 6 disabled until Q-005 terminal PASS.

- [ ] **Step 1: Collect only post-open real sessions**

Collect at least one fresh Desktop one-turn task, one Desktop multi-turn task,
and one CLI task, then continue until Q-005 has ten distinct real completed
sessions. Old quarantined generations and fixtures do not count.

Use installed read-only status to observe inventory:

```bash
/usr/bin/python3 -I \
  /Users/igyeongseob/.codex/plugins/cache/skill-evolver-dev/skill-evolver/0.1.5/skills/skill-evolver/scripts/evolver.py \
  status \
  --installation /Users/igyeongseob/.codex/skill-evolver/installation.json \
  --plugin-data /Users/igyeongseob/.codex/plugins/data/skill-evolver-skill-evolver-dev
```

For each intentional import, obtain approval for this exact command before
running it once:

```bash
/usr/bin/python3 -I \
  /Users/igyeongseob/.codex/plugins/cache/skill-evolver-dev/skill-evolver/0.1.5/skills/skill-evolver/scripts/evolver.py \
  maintain \
  --installation /Users/igyeongseob/.codex/skill-evolver/installation.json \
  --plugin-data /Users/igyeongseob/.codex/plugins/data/skill-evolver-skill-evolver-dev
```

- [ ] **Step 2: Review through separately approved batches**

Use `status` to distinguish claimable and quarantined counts. Claim no more
than the configured batch limit. A stale-locator failure after `0.1.5` is a
regression to diagnose, not a reason to relabel or bulk-revive old rows.

Obtain separate approval before each claim:

```bash
/usr/bin/python3 -I \
  /Users/igyeongseob/.codex/plugins/cache/skill-evolver-dev/skill-evolver/0.1.5/skills/skill-evolver/scripts/evolver.py \
  review-claim \
  --installation /Users/igyeongseob/.codex/skill-evolver/installation.json
```

For a `ready` response, keep its decimal batch ID and raw owner token only in
current-turn memory. Construct each `catalog-inspect`, heartbeat, commit, or
abort command with those exact returned values and request a separate approval
for every invocation. Never persist or echo the owner token. A retry result
supplies a new bound result path and must be completed from that new path.

- [ ] **Step 3: Stop at every user-only quality label**

Show the localized candidate/evidence summary. The user alone supplies
`evaluation_worthy`, `target_correct`, `external_content_adoption`, and the
exact confirmation token. Never infer or automate those answers.

For each actual candidate, give the user one external-terminal
`quality-label` command containing the exact cached 0.1.5 script path,
installation path, and returned `C-NNN` display ID. Do not include judgment
flags. The user types lowercase `yes` or `no` and the displayed confirmation
token; the agent does not run the command through a PTY.

- [ ] **Step 4: Gate Q-005 and record only immutable aggregate evidence**

After ten distinct labeled sessions, obtain separate approval and seal once:

```bash
/usr/bin/python3 -I \
  /Users/igyeongseob/.codex/plugins/cache/skill-evolver-dev/skill-evolver/0.1.5/skills/skill-evolver/scripts/evolver.py \
  quality-seal \
  --installation /Users/igyeongseob/.codex/skill-evolver/installation.json
```

`quality-seal` seals the active epoch and accepts no positional epoch. Confirm
with read-only `quality-status` immediately before sealing that the active
epoch is Q-005.

Then obtain a new approval and gate once:

```bash
/usr/bin/python3 -I \
  /Users/igyeongseob/.codex/plugins/cache/skill-evolver-dev/skill-evolver/0.1.5/skills/skill-evolver/scripts/evolver.py \
  quality-gate \
  --installation /Users/igyeongseob/.codex/skill-evolver/installation.json \
  Q-005
```

Materialize the returned aggregate report under the digest-bearing Q-005
filename, rerun the canonical report verifier from Task 6 expecting three
reports, privacy-scan it, and update GSD state. Enable later mutation work only
if Q-005 is terminal `PASS`.

## Final Verification Checklist

- [ ] First-row newer-Stop recovery is limited to pending generation 1,
  transcript epoch 0, reviewed boundary 0, and strictly newer time.
- [ ] Maintenance proof performs no transcript read.
- [ ] Review replacement is exact frozen path, descriptor-safe, first-state
  only, and HMAC-bound before export.
- [ ] Frozen locator, claim digest, cursor, and numeric `frozen_to` never
  change during rebinding.
- [ ] One pure append race causes exactly one full retry pinned to the first
  selected identity; every other instability quarantines.
- [ ] Status partitions pending rows exactly once and leaks no arbitrary error
  or private locator value.
- [ ] Full tests, byte-bound assertions, JSON checks, compile check, and
  `git diff --check` pass.
- [ ] Q-004 terminalizes before install; source/cache/installed `0.1.5` match;
  Q-005 predecessor uses the exact returned Q-004 digest.
- [ ] No label is agent-generated and `QUALITY-01` remains pending until Q-005
  terminal PASS.
