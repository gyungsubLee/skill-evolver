# Roadmap: Skill Evolver

## Overview

Skill Evolver는 Phase 1 Feasibility `FAIL`을 Phase 2의 session-level 계약으로 수정해 amended gate `PASS`를 확보했고, Phase 3 bounded Runtime Queue와 Phase 4 explicit Review/Inbox를 구현했다. source/canonical main/cache/installed plugin은 `0.1.5`이며 seven-file source/cache parity는 `PASS`다. `Q-004`는 `quality_provenance_drift`로 terminal `INVALID` 처리됐고 immutable report digest는 `a9d30a50776d7e1ad2b0f56d5be741fda8b9460f46090a88e1e18777e5f0e917`다. Installed `0.1.5`는 `2026-08-11T13:03:28Z`에 그 exact digest에서 `Q-005`를 열었다. `Q-005`는 `COLLECTING`이며 distinct sessions, candidates, labels는 모두 0이고 old batches/rows는 historical evidence일 뿐 observation이 아니다. Phase 6은 Q-005의 최소 10개 distinct real sessions, explicit Review, user-only labels, terminal `PASS`까지 차단되고 mutation, Evaluate, Apply capability는 disabled 상태다. 이후 runner, immutable evaluation, manual apply/versioning, undo/recovery와 hardening gate를 순서대로 통과한다.

## Phases

**Phase Numbering:** 정수 phase는 순차 milestone 작업이며, 긴급 삽입만 소수 phase를 사용한다.

- [x] **Phase 1: Feasibility Spike** - CLI/Desktop probe를 실행하고 deterministic gate decision을 생성했다.
- [x] **Phase 2: Session-Level Capture Design Amendment** - 실패한 turn/root 가정을 session-level queue와 접근 가능한 data-root 계약으로 수정하고 CLI/Desktop에서 재검증했다.
- [x] **Phase 3: Runtime Queue** - amended PASS 계약에 따라 silent, bounded SQLite capture와 status를 제공한다.
- [x] **Phase 4: Review and Inbox** - 명시적 review로 안전한 candidate inbox를 제공한다.
- [ ] **Phase 5: Read-only Quality Gate** - 실제 sample과 attribution 품질로 Evaluate 진입 여부를 결정한다. **(current)**
- [ ] **Phase 6: Evaluate Runner Spike** - pinned runner의 격리·resource·재현성 계약을 검증한다.
- [ ] **Phase 7: Evaluate Prepare** - 변경을 실행하지 않고 immutable candidate와 evaluation spec을 준비한다.
- [ ] **Phase 8: Evaluate Execution** - exact spec digest에 결합된 old-versus-candidate 평가를 수행한다.
- [ ] **Phase 9: Apply and Versioning** - external-terminal 승인 아래 검증된 artifact를 적용하고 lineage를 기록한다.
- [ ] **Phase 10: Undo and Recovery** - exact current hash로 parent snapshot을 복원하고 crash state를 안전하게 조정한다.
- [ ] **Phase 11: Hardening** - 전체 workflow의 safety, behavior, load, recovery와 E2E release gate를 검증한다.

## Gate Order and Failure Routing

| Gate | PASS route | FAIL route |
|------|------------|------------|
| Phase 1 Feasibility | Phase 3은 Phase 2 amended rerun PASS 뒤에만 가능 | Phase 2 session-level design amendment |
| Phase 2 amended Feasibility | Phase 3 Runtime Queue | Phase 2에 머물며 설계·probe 수정; Runtime Queue 차단 |
| Phase 3 Runtime Queue | Phase 4 Review/Inbox | Phase 3 수정; Review 미진입 |
| Phase 4 Review/Inbox | Phase 5 Quality Gate | Phase 4 수정; Quality sample 미진입 |
| Phase 5 Read-only Quality | A successor epoch passes before Phase 6 Runner Spike | Phase 4 transcript adapter 또는 review policy 수정 |
| Phase 6 Runner Spike | Phase 7 Prepare | Prepare/Evaluate 명령 비활성 유지 |
| Phase 7 Prepare | Phase 8 Evaluation | immutable artifact/spec 수정 후 재준비 |
| Phase 8 Evaluation | Phase 9 Apply | candidate와 installed target을 유지하고 Apply 비활성 |
| Phase 9 Apply | Phase 10 Undo/Recovery | rollback; version 미생성 또는 owning apply 단계 수정 |
| Phase 10 Undo/Recovery | Phase 11 Hardening | 모호하면 `recovery_required`, filesystem mutation 중단 |
| Phase 11 Hardening | Release | 실패를 소유한 앞선 phase로 복귀 |

## Phase Details

### Phase 1: Feasibility Spike
**Goal**: production 구현 전에 CLI와 Desktop의 실제 Stop, data-root와 transcript 계약을 probe해 deterministic 진입 결정을 제공한다.
**Depends on**: Nothing (first phase)
**Requirements**: FEAS-01
**Entry Gate**: Source design reviewed
**Gate Result**: **FAIL** — `docs/feasibility-report.md`
**Success Criteria** (what must be TRUE):
  1. 개발자가 CLI와 Desktop의 sanitized Stop observation과 구조 차이를 확인할 수 있다.
  2. deterministic report가 통과한 네 check와 실패한 `cli_skill_data_root`, `desktop_skill_data_root`, `cli_transcript_supported`, `desktop_transcript_supported`를 구분한다.
  3. FAIL 결과가 session-level amendment를 지시하고 Runtime Queue를 승인하지 않는다.
**Plans**: 1 historical plan (registered separately)

Plans:
- [x] 01-01: Feasibility probe와 deterministic report 생성 — completed 2026-07-27

### Phase 2: Session-Level Capture Design Amendment
**Goal**: turn-level mapping과 inaccessible fixed-root 가정을 제거하고 CLI/Desktop 양쪽에서 재검증 가능한 session-level capture/data-root 계약을 확립한다.
**Depends on**: Phase 1
**Requirements**: GATE-01
**Entry Gate**: Phase 1 product gate is FAIL
**Exit Gate**: Amended Feasibility rerun is PASS on CLI and Desktop
**Gate Result**: **PASS** — `docs/feasibility-report-v2.{json,md}`
**Failure Route**: Phase 2에 머물며 Phase 3을 계속 차단한다.
**Success Criteria** (what must be TRUE):
  1. 개발자가 turn ID 없이도 bounded session을 중복 없이 queue하고 review할 수 있는 계약을 설명할 수 있다.
  2. Hook은 global root를 read/write하고 default skill은 read-only로 접근하며, skill write는 default에서 거부되고 explicit scoped approval에서만 허용된다.
  3. CLI와 Desktop 각각의 재실행 report가 amended session provenance와 access contract를 검증한다.
  4. 재실행 decision이 PASS이기 전에는 Runtime Queue 구현이 시작되지 않는다.
**Plans**: 1 historical plan (registered after execution)

Plans:
- [x] 02-01: Session-level capture/data-root 설계 수정과 cross-surface Feasibility rerun — completed 2026-07-29

### Phase 3: Runtime Queue
**Goal**: amended PASS 계약에 맞춰 허용된 workspace의 bounded session metadata를 silent하고 idempotent하게 queue하고 transcript-free health를 제공한다.
**Depends on**: Phase 2
**Requirements**: CAPT-01
**Entry Gate**: Amended Feasibility decision is PASS
**Exit Gate**: Runtime Queue integration suite and canonical report are PASS
**Gate Result**: **PASS** — `docs/release-reports/runtime-queue.json`
**Failure Route**: Phase 3을 수정하고 Phase 4로 진행하지 않는다.
**Success Criteria** (what must be TRUE):
  1. CLI와 Desktop의 trusted Stop Hook이 사용자 채팅 출력, model call 또는 network 없이 session metadata를 queue한다.
  2. 동일 event 재전달은 하나로 유지되고 DB lock 시 bounded spool이 보존된다.
  3. status는 transcript를 읽거나 spool을 mutate하지 않고 pending, overflow, expiry와 Hook health를 보여준다.
  4. installed skill, staging과 snapshot은 변경되지 않는다.
**Plans**: 1 plan

Plans:
- [x] 03-01: Amended Runtime Queue와 schema v1 capture/status — completed 2026-07-29

### Phase 4: Review and Inbox
**Goal**: 사용자가 명시적으로 요청한 bounded review에서 재사용 가능한 candidate를 만들고 inspect, defer와 reject로 관리한다.
**Depends on**: Phase 3
**Requirements**: REVIEW-01
**Entry Gate**: Runtime Queue integration suite passes
**Exit Gate**: Review/Inbox suite and canonical report are PASS
**Gate Result**: **PASS** — `docs/release-reports/review-inbox.json`
**Failure Route**: Phase 4 adapter, policy 또는 validation을 수정한다.
**Success Criteria** (what must be TRUE):
  1. 인자 없는 호출과 status/inspect/defer/reject는 transcript를 열지 않고, explicit `review`만 bounded context를 읽는다.
  2. review는 최대 5개 distinct sessions, session별 100 records·2 MiB와 batch 8 MiB 상한을 지키며 지원하지 않거나 변경된 transcript를 추측하지 않는다.
  3. 사용자 교정과 skill-caused failure만 candidate가 되고 환경·one-off·external·uncertain 신호는 정확한 exclusion으로 남는다.
  4. candidate는 allowlisted user skill에만 귀속되고 session당 1개, batch당 신규 3개를 넘지 않는다.
**Plans**: 1 plan

Plans:
- [x] 04-01: Transcript adapter, bounded review와 candidate inbox — completed 2026-07-30

### Phase 5: Read-only Quality Gate
**Goal**: human label과 실제 review sample로 candidate 품질과 안전성을 측정해 Evaluate 진입 여부를 결정한다.
**Depends on**: Phase 4
**Requirements**: QUALITY-01
**Entry Gate**: Review/Inbox suite passes with zero target-skill writes
**Gate Result**: **Q-005 COLLECTING — 0.1.5 INSTALLED** — `Q-004` terminalized
`INVALID` for `quality_provenance_drift` with immutable report digest
`a9d30a50776d7e1ad2b0f56d5be741fda8b9460f46090a88e1e18777e5f0e917`.
The matching content-addressed aggregate body is preserved at
`docs/release-reports/quality/Q-004-a9d30a50776d7e1ad2b0f56d5be741fda8b9460f46090a88e1e18777e5f0e917.json`.
Installed `0.1.5` opened `Q-005` at `2026-08-11T13:03:28Z` from exact
predecessor `Q-004@a9d30a50776d7e1ad2b0f56d5be741fda8b9460f46090a88e1e18777e5f0e917`;
`first_batch_id` is 24. Current `Q-005/COLLECTING` has zero distinct sessions,
candidates, and labels. Old batches and rows remain historical evidence and
do not count as Q-005 observations. Phase 6 stays blocked until Q-005 has at
least ten distinct real sessions, explicit Review, user-only labels, and
terminal `PASS`; mutation, Runner, Prepare, Evaluate, and Apply remain
disabled.
**Failure Route**: Phase 4 transcript adapter 또는 improvement policy로 돌아간다.
**Success Criteria** (what must be TRUE):
  1. 개발자가 최소 10 sessions 또는 30 review items에 대한 complete label set을 확인할 수 있다.
  2. report가 evaluation-worth rate 0.50 이상과 target misattribution 0.20 이하를 입증한다.
  3. external-content adoption은 0건이고 policy/adapter digest가 report에 고정된다.
  4. FAIL이면 Runner, Prepare와 Evaluate는 비활성 상태로 남는다.
**Plans**: 1 plan

Plans:
- [ ] 05-01: Read-only sample 수집, human labeling과 immutable quality report

### Phase 6: Evaluate Runner Spike
**Goal**: pinned Codex runner가 immutable evaluation을 재현 가능하고 제한된 sandbox에서 실행할 수 있음을 입증한다.
**Depends on**: Phase 5
**Requirements**: RUNNER-01
**Entry Gate**: Read-only Quality decision is PASS
**Failure Route**: Prepare와 Evaluate를 비활성 상태로 유지한다.
**Success Criteria** (what must be TRUE):
  1. 개발자가 pinned executable, version, model, sandbox, credential와 result-schema contract를 확인할 수 있다.
  2. 두 independent fixed-input invocation이 exact expected result를 반환한다.
  3. timeout, process, memory, input과 output 제한 위반이 deterministic failure로 기록된다.
  4. FAIL report는 unsupported runner contract를 남기고 Prepare를 승인하지 않는다.
**Plans**: 1 plan

Plans:
- [ ] 06-01: Pinned runner contract와 reproducibility probe

### Phase 7: Evaluate Prepare
**Goal**: installed source를 바꾸거나 candidate를 실행하지 않고 exact evaluation input과 diff를 immutable하게 준비한다.
**Depends on**: Phase 6
**Requirements**: PREP-01
**Entry Gate**: Runner Spike decision is PASS
**Failure Route**: partial artifact를 폐기하고 Phase 7에서 재준비한다.
**Success Criteria** (what must be TRUE):
  1. 사용자가 allowlisted target의 complete base-versus-candidate diff와 visible cases를 검토할 수 있다.
  2. text-only declarative operation만 적용되고 executable, Hook, dependency 또는 security-boundary 변경은 거부된다.
  3. base, candidate, harness, holdouts와 runner contract가 하나의 canonical full spec digest로 봉인된다.
  4. 성공과 실패 모두 installed source content/hash를 변경하지 않는다.
**Plans**: 1 plan

Plans:
- [ ] 07-01: Schema v2, immutable staging과 evaluation-spec sealing

### Phase 8: Evaluate Execution
**Goal**: 사용자가 본 exact spec digest로 격리된 old-versus-candidate behavior 결과를 얻고 Apply readiness를 결정한다.
**Depends on**: Phase 7
**Requirements**: EVAL-01
**Entry Gate**: Prepared artifacts pass digest and immutability checks
**Failure Route**: candidate와 target을 변경하지 않고 Apply를 비활성 상태로 유지한다.
**Success Criteria** (what must be TRUE):
  1. 전체 spec SHA-256가 일치할 때만 evaluation lease가 시작된다.
  2. base, candidate와 blind grader가 서로 분리된 ephemeral context에서 실행된다.
  3. artifact drift, malformed result, denial, crash와 resource breach는 모두 fail-closed 결과가 된다.
  4. PASS report는 exact spec/report digest에 결합되고 installed target hash는 그대로다.
**Plans**: 1 plan

Plans:
- [ ] 08-01: Immutable-spec evaluation과 deterministic report

### Phase 9: Apply and Versioning
**Goal**: 사용자가 검증된 artifact를 exact digest로 직접 승인해 drift-safe하게 설치하고 version lineage를 확인할 수 있게 한다.
**Depends on**: Phase 8
**Requirements**: APPLY-01
**Entry Gate**: Evaluate release completion gate is PASS
**Failure Route**: rollback하고 version을 만들지 않으며 Phase 9 transaction을 수정한다.
**Success Criteria** (what must be TRUE):
  1. chat의 apply 요청은 mutation 없이 preview와 exact manual-terminal command만 보여준다.
  2. external TTY가 전체 evaluation ID와 digest를 확인하고 allowlisted target lock 뒤 drift와 capacity를 재검증한다.
  3. snapshot, journaled same-filesystem swap, directory fsync와 post-apply validation이 모두 성공해야 version이 기록된다.
  4. 실패 시 exact original hash로 복구되고 evaluated non-executable text 외 변경은 설치되지 않는다.
**Plans**: 1 plan

Plans:
- [ ] 09-01: Schema v3 apply journal, atomic swap와 version lineage

### Phase 10: Undo and Recovery
**Goal**: 사용자가 exact current hash로 verified parent snapshot을 복원하고 interrupted Apply/Undo가 추측 없이 조정되도록 한다.
**Depends on**: Phase 9
**Requirements**: UNDO-01
**Entry Gate**: Apply transaction suite passes without enabling agent apply
**Failure Route**: ambiguous evidence이면 `recovery_required`를 기록하고 mutation을 중단한다.
**Success Criteria** (what must be TRUE):
  1. undo preview는 source apply version, desired parent hash와 exact current hash를 보여주되 파일을 변경하지 않는다.
  2. external TTY confirmation, lock, pre-undo snapshot과 durable swap 뒤에만 parent snapshot이 설치된다.
  3. recovery를 반복 실행해도 하나의 provable terminal state와 최대 하나의 version만 남는다.
  4. 모호하거나 상충하는 filesystem evidence에서는 rename, copy, remove 또는 cleanup이 발생하지 않는다.
**Plans**: 1 plan

Plans:
- [ ] 10-01: Hash-bound Undo, crash reconciliation과 snapshot retention

### Phase 11: Hardening
**Goal**: 전체 release가 explicit-only safety, attribution, trigger, bounded load, crash recovery와 disposable E2E 계약을 만족함을 입증한다.
**Depends on**: Phase 10
**Requirements**: SAFE-01, HARD-01
**Entry Gate**: All preceding release reports are PASS
**Failure Route**: 실패 check를 소유한 phase로 돌아가며 Hardening에서 우회 patch를 만들지 않는다.
**Success Criteria** (what must be TRUE):
  1. 일반 작업은 skill-evolver를 자동 trigger하지 않고 명시적 요청만 review/evaluate/apply 흐름을 시작한다.
  2. trigger 및 behavior repetitions이 attribution 0.80 이상, prompt-injection adoption 0건과 high-risk auto-apply 0건을 입증한다.
  3. 10,000 Stop deliveries와 8 workers의 soak에서도 queue, retention, lease와 capacity 한계가 유지된다.
  4. 모든 journal/rename crash boundary와 disposable end-to-end lifecycle이 production skill이나 data root를 건드리지 않고 통과한다.
  5. canonical PASS report가 모든 upstream digest와 safety evidence를 고정한다.
**Plans**: 1 plan

Plans:
- [ ] 11-01: Trigger/behavior evaluation, soak, fault matrix와 disposable E2E

## Progress

**Execution Order:** 1 → 2 → 3 → 4 → 5 → 6 → 7 → 8 → 9 → 10 → 11. Gate FAIL은 위 failure route를 따르며 phase를 건너뛰지 않는다.

| Phase | Plans Complete | Status | Completed |
|-------|----------------|--------|-----------|
| 1. Feasibility Spike | 1/1 | Complete (gate FAIL) | 2026-07-27 |
| 2. Session-Level Capture Design Amendment | 1/1 | Complete (gate PASS) | 2026-07-29 |
| 3. Runtime Queue | 1/1 | Complete (gate PASS) | 2026-07-29 |
| 4. Review and Inbox | 1/1 | Complete (gate PASS) | 2026-07-30 |
| 5. Read-only Quality Gate | 0/1 | Q-005 COLLECTING; 0.1.5 installed | - |
| 6. Evaluate Runner Spike | 0/1 | Not started | - |
| 7. Evaluate Prepare | 0/1 | Not started | - |
| 8. Evaluate Execution | 0/1 | Not started | - |
| 9. Apply and Versioning | 0/1 | Not started | - |
| 10. Undo and Recovery | 0/1 | Not started | - |
| 11. Hardening | 0/1 | Not started | - |
