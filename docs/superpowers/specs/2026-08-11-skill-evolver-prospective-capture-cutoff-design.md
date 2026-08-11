# Skill Evolver Prospective Capture Cutoff Design

**Status:** Approved on 2026-08-11 under the user's M1 automatic-approval directive.

## Problem

Quality epochs currently bind only `first_batch_id`. A verified Stop captured
before an epoch can be imported afterward, claimed in a new batch, and counted
as a distinct quality session. Marking that session as excluded does not solve
the problem because excluded decisions count toward the ten-session minimum.
Aborting a batch returns the same oldest rows to pending, and the CLI has no
selective spool purge command.

Q-005 opened with zero observations while status reported 71 verified spool
files from earlier work. Importing that backlog before a capture-time cutoff
would make the prospective cohort untrustworthy.

## Decision

Apply one metadata-only cutoff at the Review claim seam. While a quality epoch
is actively `collecting`, a pending session is claimable only when its
persisted earliest Stop time is strictly later than the epoch start:

```text
review_items.first_stop_at > active_quality_epoch.started_at
```

Both timestamps use the existing fixed-width UTC-second representation. An
equal timestamp is excluded because second-resolution data cannot prove which
event happened first. A later Stop never makes an old session eligible because
`first_stop_at` already converges with `MIN(first_stop_at, incoming_stop)`.

When there is no collecting epoch, Review preserves its existing behavior.

## Alternatives Rejected

### Import-time quarantine

Adding an eligibility state during `maintain` would require a schema or state
transition change, complicate replay and recovery, and duplicate quality
epoch knowledge in the capture module.

### Destructive spool cutover

A purge command would add a new destructive TTY boundary, delete signed
metadata, require crash recovery, and still need a precise cohort selector.
The claim cutoff preserves evidence and lets ordinary retention remove old
rows later.

## Module and Seam

Add one private helper in `evolver.py`:

```python
def _collecting_quality_started_at(
    connection: sqlite3.Connection,
) -> Optional[str]:
    """Return the active collecting epoch start, otherwise None."""
```

The helper uses the existing `active_quality_epoch()` validation. Invalid
quality metadata fails closed. It returns a canonical timestamp only for
state `collecting`; sealed, terminal, or absent epochs do not add a cutoff.

Both callers resolve the cutoff inside the same SQLite transaction that reads
or claims queue rows. `_prepare_review_batch()` already owns a write
transaction. `queue_status()` must reject a caller-owned transaction, open a
read transaction, resolve the epoch, compute every database aggregate from
that snapshot, and close the transaction before returning. A concurrent epoch
transition therefore cannot combine one epoch's cutoff with another snapshot's
queue counts.

Two internal callers consume the same value:

1. `_prepare_review_batch()` extends the existing claim SQL with the strict
   `first_stop_at` predicate.
2. `queue_status()` classifies rows with that identical predicate so its
   `claimable_sessions` count remains truthful.

No new public command, schema field, module, dependency, or transcript read is
introduced.

## Status Contract

`queue_status()` retains every current field and fixed quarantine bucket. It
adds one fixed aggregate bucket:

```text
pre_quality_epoch
```

Classification precedence remains fail-closed:

1. claimable under the complete Review predicate and active cutoff;
2. stored allowlisted error;
3. unknown stored error;
4. binding pending;
5. empty generation;
6. pre-quality-epoch capture.

Thus a row with a real stored failure is not relabeled as merely old, and a
row without unread bytes remains `empty_generation`. Every pending row still
belongs to exactly one bucket, preserving:

```text
pending_sessions = claimable_sessions + quarantined_sessions
quarantined_sessions = sum(quarantined_by_error.values())
```

## Quality Contract

Advance `quality_contract_payload()["version"]` from `1` to `2` and add:

```json
{
  "prospective_capture": {
    "source": "review-items-first-stop-at",
    "cutoff": "active-collecting-epoch-started-at",
    "comparison": "strictly-after-utc-second",
    "same_second": "exclude",
    "later_stop": "preserve-earliest",
    "pre_cutoff_disposition": "pending-unclaimable-while-epoch-collecting"
  }
}
```

The contract digest and runtime digest therefore change together. Persistence
schema remains version 1.

## Data Flow

1. `maintain` authenticates and imports every live verified spool entry using
   the existing metadata-only path.
2. Old rows retain their original `first_stop_at` and remain pending.
3. Review and status read the active collecting epoch start inside their
   existing database snapshot.
4. Otherwise-claimable pre-cutoff rows remain unclaimable and appear in the
   aggregate `pre_quality_epoch` status bucket. Rows with stored failures keep
   their higher-precedence existing error buckets.
5. A genuinely new session whose earliest Stop is later than the cutoff can be
   claimed normally.
6. Existing retention can eventually remove old pending metadata. If the
   epoch leaves `collecting` first, the cutoff no longer applies and ordinary
   Review behavior resumes; completed batches remain outside the next epoch
   when that successor opens.

## Failure Handling and Privacy

- Invalid quality metadata fails the Review/status operation; it never falls
  back to an unbounded claim.
- No spool payload, transcript, session ID, path, or per-session timestamp is
  emitted by status.
- The cutoff never rewrites, deletes, or advances old rows.
- Same-second ambiguity is resolved toward exclusion.
- Host-misclassified subagent sessions cannot be inferred from paths or
  transcript content. Adding a subagent discriminator remains out of scope
  until the host supplies a trusted payload field.

## Verification

Required RED/GREEN coverage:

1. A pre-open Stop imported after epoch open remains pending and unclaimed.
2. A post-open session is claimed even when older ineligible rows sort first.
3. A later post-open Stop does not make a pre-open session eligible.
4. A same-second first Stop is conservatively excluded.
5. Status partitions pre-cutoff rows into `pre_quality_epoch` without private
   output or database mutation.
6. The exact quality contract v2 object participates in its digest.
7. With nine pre-open rows and one post-open row, only the post-open row enters
   the batch, the distinct-session count is one, and sealing cannot satisfy the
   prospective ten-session requirement.
8. A damaged active epoch record or pointer makes both Review claim and status
   fail without allocating a batch ID or changing any queue row.
9. A concurrent epoch transition cannot make status combine a cutoff from one
   epoch with queue aggregates from another SQLite snapshot.
10. Existing capture, Review, quality, privacy, and full release suites remain
   green.

## Rollout

Release the amendment as `0.1.6`. Before installing it, terminalize the
zero-observation Q-005 epoch as `INVALID / quality_provenance_drift` and
preserve its content-addressed report. Install and verify source/cache parity,
then open Q-006 from the exact Q-005 terminal digest. Only after Q-006 opens
may maintenance import the existing spool backlog. Status must then show that
pre-Q-006 rows are quarantined from Review before any quality claim runs.

Q-006 still requires ten distinct real user sessions, explicit Review,
user-only external-TTY labels, and terminal `PASS`; this amendment does not
weaken any quality threshold or authorize Phase 6.
