# Skill Evolver Collection Deadline Implementation Plan

> **For agentic workers:** Use `implement` with `test-driven-development` for
> each task and `verification-before-completion` before completion claims.
> The user already approved Design A and this implementation sequence.

**Goal:** Expose collection deadlines and actionable read-only quality status
in source release 0.1.7, using the current standalone project path.

**Architecture:** Reuse schema-v1 epochs and existing validators. Replace the
status-only boolean source check with bounded reason derivation; extend all
quality-status output shapes. Preserve the gate and retry implementations.

**Tech Stack:** `/usr/bin/python3 -I` (Python 3.9), standard library SQLite,
unittest, canonical JSON and SHA-256. No dependency changes.

## Global Constraints

- Work in `/Users/igyeongseob/Develop/10_herness/skill-evolver`.
- Keep `SCHEMA_VERSION = 1` and `SCHEMA_SQL` byte-unchanged.
- Keep quality gate, predecessor guard, TTLs, thresholds, transcript adapter,
  improvement policy and Hook unchanged.
- All four new fields are present for every successful status shape.
- Status remains read-only and transcript-free; diagnostics never mutate an
  epoch or authorize any action.
- Every live Skill Evolver mutation needs separate exact-command/data-root
  approval. Never invoke real `quality-label`, including through an agent PTY.
- No fixtures, subagents, old sessions or repeated generations as real samples.
- No Phase 5 SUMMARY/VERIFICATION or Phase 6 implementation before real PASS.
- Stage only exact changed files; do not push or create a PR.

## Task 1: Restore standalone project navigation and marketplace

**Files:** `.planning/{HANDOFF,PROJECT,STATE,ROADMAP}.md`,
`.planning/intel/SYNTHESIS.md`, `README.md`,
`.agents/plugins/marketplace.json`, `skills/skill-evolver/tests/test_capture.py`.

**Interfaces:** marketplace root becomes the project root; source is `./`.
Production installation and plugin-data roots retain their exact current values.

- [x] Read HANDOFF, Start Here and Phase 5 documents; verify old commit and
  GitHub baseline. Copy the five missing historical records, including the
  immutable Q-006 report, without modifying report bytes.
- [x] Run baseline discovery. Result: 538 run, 3 skipped, one error because
  `ProductionSurfaceTests` reads the former parent marketplace path. Focused
  reproduction confirms FileNotFoundError. Other baseline tests pass.
- [ ] Update the existing production-surface test to read
  `PLUGIN_ROOT / ".agents/plugins/marketplace.json"` and expect source `./`.
  Change README command-path expectation to the current project. Run:

```bash
/usr/bin/python3 -I -m unittest discover -s skills/skill-evolver/tests -p test_capture.py -k ProductionSurfaceTests
```

  Expected RED until the missing root marketplace and current README exist.
- [ ] Add the repository-owned marketplace (filesystem approval if needed):

```json
{
  "name": "skill-evolver-dev",
  "interface": {"displayName": "Skill Evolver Development"},
  "plugins": [{
    "name": "skill-evolver",
    "source": {"source": "local", "path": "./"},
    "policy": {"installation": "AVAILABLE", "authentication": "ON_INSTALL"},
    "category": "Developer Tools"
  }]
}
```

- [ ] Replace old absolute execution/navigation paths in current README,
  PROJECT, HANDOFF and synthesis links. Record standalone Git root and old
  commit provenance without rewriting historical plans or immutable reports.
  README marketplace-add uses the current project itself.
- [ ] Repeat the focused command; expect two production-surface tests passing.

## Task 2: Add status diagnostics through TDD

**Files:** `skills/skill-evolver/scripts/evolver.py`,
`skills/skill-evolver/tests/test_quality_gate.py`.

**Interfaces:** preserve `quality_status(connection, installation, now)` and
`cmd_quality_status(args)`. Replace `_quality_sealed_source_current` (only
quality_status calls it) with `_quality_status_invalid_reason(connection,
installation, epoch, now) -> Optional[str]`.

- [ ] Extend the existing fixtures/tests with the spec's output assertions.
  Use this exact collecting boundary matrix:

```python
deadline = self.runtime.parse_iso_utc(epoch["collection_expires_at"])
for checked_at, remaining, status, reason in (
    (deadline - 1, 1, "COLLECTING", None),
    (deadline - 0.25, 1, "COLLECTING", None),
    (deadline, 0, "INVALID", "quality_collection_expired"),
    (deadline + 1, 0, "INVALID", "quality_collection_expired"),
):
    with self.subTest(checked_at=checked_at):
        before = self.installation.database.read_bytes()
        actual = self.runtime.quality_status(
            self.connection, self.installation, checked_at
        )
        self.assertEqual(actual["collection_expires_at"], epoch["collection_expires_at"])
        self.assertEqual(actual["remaining_seconds"], remaining)
        self.assertEqual(actual["status"], status)
        self.assertEqual(actual["invalid_reason"], reason)
        self.assertEqual(actual["next_action"], "run_quality_gate" if reason else "collect_real_sessions")
        self.assertEqual(before, self.installation.database.read_bytes())
```

  Also cover IDLE nulls; collecting counts and advisory seal; unreadable
  provenance vs mismatched provenance; expiry precedence; stored INVALID with
  no terminal; sealed label expiry, witness corruption, subject mismatch and
  subject parse failure; missing/complete labels; retained terminal PASS,
  FAIL and INVALID; tombstone nulls/history action. Reuse `seal_sample`,
  `collect_ten`, `answers` and existing retention helpers. Preserve malformed
  inventory fail-closed behavior. Extend the CLI/read-only test with exact
  new keys, database-byte invariance and forbidden transcript reads.
- [ ] Add a partial-sample regression: open, commit one synthetic batch,
  expire and gate INVALID, then call open with exact predecessor under the
  same provenance and require `quality_predecessor_provenance_unchanged`.
  The database fixture stays in its temporary directory; this is not a real
  quality run.
- [ ] Run focused tests; confirm failures are missing new fields, then code:

```bash
/usr/bin/python3 -I -m unittest discover -s skills/skill-evolver/tests -p test_quality_gate.py
```

- [ ] Replace the status-only helper with the precise reason derivation:

```python
def _quality_status_invalid_reason(connection, installation, epoch, now):
    state = epoch["state"]
    if state not in {"collecting", "sealed"}:
        return epoch["invalid_reason"]
    if state == "collecting" and now >= parse_iso_utc(epoch["collection_expires_at"]):
        return "quality_collection_expired"
    sealed = epoch["sealed"]
    if state == "sealed" and now >= parse_iso_utc(sealed["label_expires_at"]):
        return "quality_label_expired"
    try:
        if not _quality_epoch_provenance_current(installation, epoch):
            return "quality_provenance_drift"
        if state == "sealed":
            if not quality_sealed_witness_current(connection, epoch):
                return "quality_source_corrupt"
            if not all(
                quality_candidate_subject_digest(connection, int(item["candidate_id"]))
                == item["subject_digest"] for item in sealed["candidates"]
            ):
                return "quality_candidate_subject_changed"
    except ValueError:
        return "quality_source_corrupt"
    return None
```

  Keep production type annotations and existing formatting. In status, call
  it once after IDLE/tombstone branches. Replace duplicate expiry/source
  checks with `invalid_reason is not None`. Add `math` from the standard
  library. Full-epoch output adds:

```python
"collection_expires_at": epoch["collection_expires_at"],
"remaining_seconds": (
    max(0, math.ceil(parse_iso_utc(epoch["collection_expires_at"]) - now))
    if state == "collecting" else None
),
"invalid_reason": invalid_reason,
"next_action": next_action,
```

  IDLE adds all three nulls and `open_quality_epoch`; tombstones add all three
  nulls and `inspect_quality_history`. Derive the full-epoch action as:

```python
terminal = epoch.get("terminal")
if type(terminal) is dict:
    next_action = terminal["body"]["next_action"]
elif status == "COLLECTING":
    # ponytail: counts are advisory; quality-seal checks the full witness.
    next_action = (
        "request_quality_seal"
        if len(session_refs) >= 10 and candidate_ids
        else "collect_real_sessions"
    )
else:
    next_action = {
        "AWAITING_LABELS": "request_user_labels",
        "READY_TO_GATE": "run_quality_gate",
        "INVALID": "run_quality_gate",
        "SUPERSEDED": "inspect_quality_history",
    }[status]
```

- [ ] Bind behavior in `quality_contract_payload`: version 3 and
  `status_output` containing the four field semantics and advisory action
  mapping. Add an exact payload assertion and update the previous version-2
  assertion. Existing prospective capture payload remains unchanged.
- [ ] Re-run the quality module and inspect the complete diff. Gate,
  predecessor, schema and transcript adapter must be unchanged.

## Task 3: Release metadata, verification and live handoff

**Files:** `.codex-plugin/plugin.json`,
`skills/skill-evolver/references/runtime.json`,
`skills/skill-evolver/scripts/evolver.py`,
`skills/skill-evolver/tests/test_capture.py`, `skills/skill-evolver/SKILL.md`,
`README.md`, current planning state.

**Interfaces:** all source version pins agree on 0.1.7; no change to runtime
installation/data paths or live quality state.

- [ ] Change the three existing `ProductionSurfaceTests` version assertions
  to 0.1.7. Run that test to prove RED.
- [ ] Set manifest/runtime JSON version to `0.1.7`, `VERSION` to
  `skill-evolver 0.1.7`, and `load_review_runtime`'s strict version to `0.1.7`.
- [ ] Document the four fields, nullable countdown, advisory actions and
  distinction between projected INVALID and a stored immutable terminal in
  README and SKILL. Use current paths for execution examples.
- [ ] Run verification:

```bash
/usr/bin/python3 -I -m unittest discover -s skills/skill-evolver/tests -p 'test_*.py'
/usr/bin/python3 -I -m py_compile skills/skill-evolver/scripts/evolver.py
/usr/bin/python3 -m json.tool .codex-plugin/plugin.json
/usr/bin/python3 -m json.tool skills/skill-evolver/references/runtime.json
/usr/bin/python3 -m json.tool .agents/plugins/marketplace.json
/usr/bin/python3 -I skills/skill-evolver/scripts/evolver.py --version
git diff --check
```

- [ ] Obtain independent code/specification and security/privacy review.
  Fix substantive findings with focused RED/GREEN evidence. Record actual
  full-suite counts and baseline error resolution.
- [ ] Verify immutable report digests and unchanged guard/gate/schema/Hook.
  Commit migration/docs and verified implementation with exact paths.
- [ ] Prepare exact marketplace registration and plugin-install commands for
  separate filesystem approval. Do not silently alter installed caches.
- [ ] After approved installation, compare seven source/cache pairs: manifest,
  hooks, README, SKILL, runtime JSON, improvement policy and evolver.py;
  separately check packaged Phase 4 report and installed version/source path.
- [ ] Read-only status/quality-status verification. Require fresh genuine Stop
  ingress before requesting the literal Q-007 open command. No maintain,
  Review, quality-open, label or gate runs implicitly.
- [ ] Update HANDOFF/STATE/PROJECT/ROADMAP with exact completed work and pending
  approval/real-data steps. Keep M1 at 4/11 phases and Phase 5 incomplete.

## Execution evidence

Append verified results here after execution. Local runtime implementation,
deployment and genuine quality PASS are separate checkpoints.
