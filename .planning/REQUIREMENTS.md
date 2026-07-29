# Requirements: Skill Evolver

**Defined:** 2026-07-27
**Core Value:** 승인 없는 스킬 변경 없이 안전하고 명시적인 개선 루프를 제공한다.

## v1 Requirements

### Safety and Control

- [ ] **SAFE-01**: 전체 workflow는 metadata-only Hook, explicit-only review, non-executing prepare, immutable-spec evaluation, external-TTY digest/hash-bound apply·purge·undo라는 안전 경계를 유지해야 하며 자동 review, evaluation, apply와 Git publication을 수행하지 않아야 한다.

### Feasibility and Gate Routing

- [x] **FEAS-01**: macOS Codex CLI와 Desktop 각각에서 bounded Stop observation, skill-process data-root access와 transcript structure를 probe하고, 성공 여부와 무관하게 sanitized fixture와 deterministic PASS/FAIL report를 생성해야 한다.
- [x] **GATE-01**: Feasibility FAIL 시 turn-level 및 inaccessible fixed-root 가정을 session-level queue/data-root 계약으로 수정하고 두 surface에서 재검증해야 하며, PASS 전에는 Runtime Queue를 승인하지 않아야 한다.

### Read-only MVP

- [ ] **CAPT-01**: amended Feasibility PASS 뒤 중앙 Stop Hook은 승인된 workspace의 bounded session metadata를 SQLite schema v1 queue에 silent·idempotent하게 저장하고, bounded spool·retention·transcript-free status를 제공하며 모델·네트워크 호출이나 installed-skill mutation을 하지 않아야 한다.
- [ ] **REVIEW-01**: 명시적인 `$skill-evolver review`만 allowlisted transcript context를 fail-closed로 읽고, 5-session/20-item 및 byte/record 상한, lease, exclusion, secret sanitization, session당 candidate 1개와 batch당 신규 fingerprint 3개 제한을 적용해야 한다.
- [ ] **QUALITY-01**: Read-only quality gate는 최소 10 sessions 또는 30 review items, evaluation-worth rate 0.50 이상, target misattribution 0.20 이하, external-content adoption 0건, 전 candidate human label과 policy/adapter digest를 확인해야 한다.

### Evaluate

- [ ] **RUNNER-01**: quality PASS 뒤 pinned Codex runner는 고정 binary/model/sandbox와 120초·16 process·1 GiB RSS·16 MiB output·1 MiB input 상한에서 두 번의 독립 fixed-input run으로 같은 expected result를 내야 한다.
- [ ] **PREP-01**: Prepare는 schema v2에서 allowlisted user skill의 torn-copy-safe immutable base를 만들고, 최대 8개의 SHA-bound declarative text operation만 적용해 base, candidate, harness, holdout, runner, model, sandbox와 resource contract를 하나의 canonical spec으로 봉인하며 source를 변경하지 않아야 한다.
- [ ] **EVAL-01**: Evaluation은 전체 prepared-spec SHA-256과 모든 immutable artifact 및 source drift를 재검증하고, base·candidate·blind grader를 격리 실행하며 모든 malformed, changed, denied, crashed 또는 resource-breaching case를 fail-closed로 처리하고 installed skill을 변경하지 않아야 한다.

### Apply, Undo, and Release

- [ ] **APPLY-01**: upstream PASS 뒤 apply는 schema v3 journal/version lineage를 사용하고, chat에서는 preview만 제공하며 external TTY에서 전체 evaluation ID/digest를 확인한 뒤 allowlisted target lock, drift/capacity check, snapshot, same-filesystem durable swap, validation과 rollback을 수행해야 한다.
- [ ] **UNDO-01**: Undo는 apply version의 검증된 parent snapshot을 전체 expected-current hash에 결합해 복원하고, crash recovery는 journal과 filesystem evidence가 명확할 때만 전이하며 모호하면 mutation 없이 `recovery_required`를 기록해야 한다.
- [ ] **HARD-01**: 모든 upstream PASS 뒤 hardening은 8 synthetic fixtures, 9 trigger/9 non-trigger cases 각 3회, behavior fixtures 최소 3회, attribution accuracy 0.80 이상, injection·high-risk auto-apply 0건, 10,000-event/8-worker soak, crash matrix와 disposable E2E를 통과해야 한다.

## Out of Scope

- PostgreSQL, background/scheduled worker, 외부 서비스와 웹 UI
- 자동 review/evaluation/apply/undo
- transcript 원문 복제
- 자동 Git commit, push 또는 pull request

## Traceability

| Requirement | Phase | Status |
|-------------|-------|--------|
| FEAS-01 | Phase 1 | Complete |
| GATE-01 | Phase 2 | Complete |
| CAPT-01 | Phase 3 | Pending |
| REVIEW-01 | Phase 4 | Pending |
| QUALITY-01 | Phase 5 | Pending |
| RUNNER-01 | Phase 6 | Pending |
| PREP-01 | Phase 7 | Pending |
| EVAL-01 | Phase 8 | Pending |
| APPLY-01 | Phase 9 | Pending |
| UNDO-01 | Phase 10 | Pending |
| SAFE-01 | Phase 11 | Pending |
| HARD-01 | Phase 11 | Pending |

**Coverage:** 12/12 v1 requirements mapped exactly once.
