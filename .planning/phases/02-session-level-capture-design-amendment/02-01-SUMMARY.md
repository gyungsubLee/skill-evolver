---
phase: 02-session-level-capture-design-amendment
plan: "01"
subsystem: feasibility
tags:
  - codex-cli
  - codex-desktop
  - session-capture
  - asymmetric-access
  - frozen-prefix
provides:
  - CLI/Desktop session-level capture contract
  - least-privilege asymmetric global-inbox access contract
  - predecessor-bound deterministic schema-v2 PASS report
  - replacement session Runtime Queue implementation plan
affects:
  - runtime-queue
  - review-and-inbox
  - read-only-quality-gate
tech-stack:
  added: []
  patterns:
    - one active row per session
    - same-file transcript binding
    - frozen-prefix bounded inspection
    - explicit scoped mutation approval
key-files:
  created:
    - docs/superpowers/specs/2026-07-28-skill-evolver-session-capture-amendment-design.md
    - docs/superpowers/plans/2026-07-28-skill-evolver-session-capture-amendment.md
    - docs/feasibility-report-v2.json
    - docs/feasibility-report-v2.md
    - skills/skill-evolver/tests/fixtures/*.v2.structure.json
    - docs/superpowers/plans/2026-07-28-skill-evolver-session-runtime-queue.md
  modified:
    - skills/skill-evolver/scripts/evolver.py
    - skills/skill-evolver/tests
    - README.md
    - skills/skill-evolver/SKILL.md
key-decisions:
  - "The global inbox is retained; access is asymmetric instead of permanently writable by ordinary skill processes."
  - "Session identity and one active row replace required turn identity and one item per Stop."
  - "Review binds to the observed same file and never reads beyond a frozen complete-record prefix."
  - "Only the schema-v2 PASS report authorizes the replacement Runtime Queue plan."
duration: not-recorded
completed: 2026-07-29
status: complete
---

# Phase 2: Session-Level Capture Design Amendment Summary

**Phase 1의 실패 가정을 제거하고 CLI/Desktop에서 검증된 session-level 및 asymmetric-access 계약으로 `GATE-01`을 통과했다.**

## Accomplishments

- Phase 1의 required `turn_id`, contiguous turn span, symmetric fixed-root write 가정을 제거했다.
- one global inbox는 유지하되, Hook read/write, default skill read, default skill write denial, explicit scoped read/write로 권한을 분리했다.
- CLI와 Desktop 각각 서로 독립적인 두 session을 관찰했다. 두 surface 모두 `observation_count: 2`, `distinct_sessions: true`, stable Stop shape와 user/assistant provenance를 기록했다.
- 두 surface 모두 `same_file_identity`에 결합됐고, frozen prefix 뒤 suffix를 읽지 않았다(`read_past_boundary: false`, `suffix_ignored: true`).
- 정확히 여섯 개의 schema-v2 sanitized fixtures가 JSON과 Markdown에서 모두 `PASS`인 authoritative report를 생성했다.
- v2 report를 Phase 1 JSON digest `ced4503adb44bd041de063c04e0c6c64d0831370fc12e96a920fe97244d8ae15`에 결합했다.
- superseded turn-level Runtime Queue plan을 실행 불가로 표시하고, session contract replacement plan을 Task 7 commit `e8fad084e275896622baeea4ffb9d5580b46104b`에 기록했다.
- 현재 full deterministic suite는 `/usr/bin/python3 -m unittest discover -s skills/skill-evolver/tests -p 'test_*.py' -v`에서 `173/173`을 통과한다.

## Exact Gate Evidence

1. `skills/skill-evolver/tests/fixtures/session-stop-cli.v2.structure.json`
2. `skills/skill-evolver/tests/fixtures/session-stop-desktop.v2.structure.json`
3. `skills/skill-evolver/tests/fixtures/access-cli.v2.structure.json`
4. `skills/skill-evolver/tests/fixtures/access-desktop.v2.structure.json`
5. `skills/skill-evolver/tests/fixtures/session-transcript-cli.v2.structure.json`
6. `skills/skill-evolver/tests/fixtures/session-transcript-desktop.v2.structure.json`

Reports:

- `docs/feasibility-report-v2.json`
- `docs/feasibility-report-v2.md`

## Commit Evidence

- Design and execution plan: `2752a68`, `d006b9a`, `dd9c94f`
- Session Stop slice: `dd9c94f..27be125`
- Asymmetric access slice: `27be125..52d9cfb`
- Bounded session transcript slice: `52d9cfb..9338c1f`
- Deterministic v2 gate slice: `9338c1f..431ae3b`
- Probe runbook/hardening slice: `431ae3b..fbc1516`
- Real evidence and staging correction slice: `fbc1516..4dc8ac5`
- Replacement Runtime Queue plan: `e8fad084e275896622baeea4ffb9d5580b46104b`

## Cleanup and Privacy Outcome

Task 6 recorded removal of the development probe plugin and marketplace entry,
followed by scrub of 6 raw observations and 10 ephemeral reports. Fresh
inventory checks confirm that the target plugin/marketplace entries remain
absent and that private `incoming`, `incoming-v2` and `reports` directories are
empty.
Committed evidence passed the raw-marker privacy assertions. This records no
claim that a plugin cache was deleted.

## Gate Outcome

- **Phase goal verification:** passed
- **Product gate decision:** `PASS`
- **Requirement:** `GATE-01` satisfied
- **Authorized next phase:** Phase 3 Runtime Queue
- **Executable downstream plan:** `docs/superpowers/plans/2026-07-28-skill-evolver-session-runtime-queue.md`

## Next Phase Readiness

Phase 3 may implement the replacement session Runtime Queue plan. Required
`turn_id`, per-Stop rows, symmetric default write, and the superseded
`2026-07-26` Runtime Queue plan remain prohibited.
