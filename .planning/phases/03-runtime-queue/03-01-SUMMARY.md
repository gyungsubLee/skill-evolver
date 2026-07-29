---
phase: 03-runtime-queue
plan: "01"
subsystem: runtime-queue
tags:
  - sqlite
  - stop-hook
  - session-capture
  - bounded-spool
  - privacy
provides:
  - one-row-per-session metadata queue
  - generation and frozen-prefix review boundary
  - bounded authenticated spool and retention maintenance
  - transcript-free read-only status
  - canonical Runtime Queue PASS report
affects:
  - review-and-inbox
  - read-only-quality-gate
tech-stack:
  added: []
  patterns:
    - HMAC session identity
    - caller-owned SQLite transactions
    - fd-pinned no-follow file handling
    - portable journal_mode DELETE
key-files:
  created:
    - skills/skill-evolver/tests/feasibility_probe.py
    - skills/skill-evolver/tests/test_capture.py
    - docs/release-reports/runtime-queue.json
  modified:
    - ../.agents/plugins/marketplace.json
    - .codex-plugin/plugin.json
    - skills/skill-evolver/scripts/evolver.py
    - skills/skill-evolver/references/runtime.json
    - skills/skill-evolver/SKILL.md
    - hooks/hooks.json
    - README.md
key-decisions:
  - "One durable row represents one session; turn_id is diagnostic and optional."
  - "Stop is metadata-only; transcript inspection remains explicit Phase 4 work."
  - "Status is read-only, while maintenance requires an exact-command scoped approval."
  - "SQLite uses journal_mode=DELETE and zero busy wait; contention falls back to a bounded authenticated spool."
duration: not-recorded
completed: 2026-07-29
status: complete
---

# Phase 3: Runtime Queue Summary

**Phase 2의 session contract를 silent, bounded, privacy-aware Runtime Queue로
구현하고 `CAPT-01`을 충족했다.**

## Accomplishments

- HMAC `session_key`와 one-row-per-session upsert를 구현했다.
- optional `turn_id`, generation, transcript epoch, observed/reviewed/frozen
  boundary와 lease recovery 계약을 고정했다.
- 하나의 matcher-free main `Stop` Hook만 production에 등록했다. Hook은
  transcript bytes, model과 network를 사용하지 않는다.
- SQLite contention을 authenticated, fd-pinned, file/byte-count bounded spool로
  흡수하고 overflow도 blocking FIFO와 hardlink를 거부한다.
- 14-day pending/spool, 30-day raw metadata, 180-day session-key retention과
  candidate evidence HMAC 제거를 같은 maintenance 경계에서 처리한다.
- status가 transcript와 spool payload를 열거나 변경하지 않음을 검증했다.
- 운영 runbook과 canonical
  `docs/release-reports/runtime-queue.json` PASS report를 게시했다.

## Commit Evidence

- Frozen feasibility harness baseline: `50b7727`
- Runtime foundation through generation state: `3ca61ec` … `4dd5bd8`
- Maintenance/privacy implementation: `0e8c409`
- Maintenance plan hardening: `8f9a0f1`
- Operations runbook and regression: `d7fb9b9`
- Completion report: `bdb3a94`
- Operations plan synchronization: `9fc3a68`

## Verification Outcome

- Fresh full suite: `253` tests ran: `250` passed and exactly `3` historical
  probe assertions skipped.
- Completion report: `PASS`, `12/12` checks true, `7/7` production digests
  matched implementation commit `d7fb9b9`.
- Phase 2 gate and immutable predecessor digest matched.
- Independent code/report reviews: `CLEAN`.

## Gate Outcome

- **Phase goal verification:** passed
- **Requirement:** `CAPT-01` satisfied
- **Authorized next phase:** Phase 4 Review and Inbox

## Next Phase Readiness

Phase 4 may read only a claimed frozen session generation under explicit scoped
approval. It must reuse the existing claim, heartbeat, epoch adoption, evidence
and completion helpers; required turn identity, per-Stop rows and duplicate
lease implementations remain prohibited.
