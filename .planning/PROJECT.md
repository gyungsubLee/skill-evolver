# Skill Evolver

## Overview

Skill Evolver는 Codex CLI와 Desktop 작업에서 재사용 가능한 스킬 개선 신호를 안전하게 수집하고, 사용자가 명시적으로 검토·평가·적용·복구를 승인하는 개인용 로컬 개선 루프다.

## Core Value

관찰은 최소한으로 자동화하되 판단과 파일 변경 권한은 사용자에게 남겨, 설치된 스킬을 승인 없이 변경하지 않는 안전한 개선 흐름을 제공한다.

## Target Runtime

- macOS Codex CLI와 Desktop
- `/usr/bin/python3` 3.9 이상
- Python 표준 라이브러리와 `sqlite3`만 사용
- 프로젝트 루트: `/Users/igyeongseob/Develop/10_herness/skill-evolver`
- Git 저장소 루트: `/Users/igyeongseob/Develop/10_herness/skill-evolver`
- 현재 프로젝트 자체가 독립 Git 저장소이며 이전 상위 저장소를 사용하지 않음

## Developer-facing Success Metric

CLI와 Desktop 모두에서 경계가 명확한 session metadata를 수집하고, 수동 review로 재사용 가능한 candidate를 만들며, immutable input 평가와 digest/hash-bound external-terminal apply/undo를 수행한다. 자동 review/apply 또는 승인 전 installed-skill 변경은 없어야 한다.

## Goals

- CLI와 Desktop에서 검증된 session-level capture 및 data-root 계약을 확립한다.
- matcher 없는 중앙 `Stop` Hook이 모델·네트워크 호출 없이 제한된 metadata만 적재하게 한다.
- `$skill-evolver`를 명시적으로 호출했을 때만 transcript review와 candidate 관리를 수행한다.
- 환경 문제, 일회성 요구, 외부 콘텐츠 지시와 스킬 지침 문제를 구분한다.
- immutable base/candidate/harness/spec을 사용해 old-versus-candidate 평가를 수행한다.
- exact digest/hash와 외부 TTY를 요구하는 apply, versioning, undo, crash recovery를 제공한다.
- 각 단계의 PASS artifact가 확인된 경우에만 다음 단계로 진행한다.

## Non-Goals

- PostgreSQL 또는 별도 서버
- background worker나 예약 review
- 자동 candidate 평가 또는 자동 apply/undo
- transcript 본문을 별도 저장소에 복제
- 외부 서비스, 웹 UI 또는 다중 사용자 학습 시스템
- 자동 Git commit, push 또는 pull request
- system, managed, plugin-cache 스킬의 직접 수정

## Constraints

- 인자 없는 `$skill-evolver`는 status-only이며 transcript를 읽지 않는다.
- transcript는 명시적인 `review` 요청 뒤에만 읽는다.
- `prepare`는 candidate content를 실행하지 않으며 installed skill을 변경하지 않는다.
- `evaluate`는 전체 immutable evaluation-spec digest에 결합되고 installed target을 변경하지 않는다.
- apply, privacy purge와 undo mutation은 external TTY와 전체 digest 또는 current hash를 요구한다.
- 모든 경로, identity, artifact, lease, state transition과 schema version은 fail-closed로 검증한다.
- Phase 6은 successor quality epoch의 사용자 전용 외부 TTY label과 Phase 5 `PASS`가 확인될 때까지 시작하지 않는다.

## Key Decisions

| Decision | Rationale | Status |
|----------|-----------|--------|
| 관찰은 각 스킬이 아니라 중앙 matcher-free `Stop` Hook에서 수행한다. | 중복 지침과 누락을 피하고 Hook을 metadata-only로 유지한다. | Inherited |
| 인자 없는 호출은 status-only이고 review는 명시적 호출만 허용한다. | 비용과 개인정보 노출을 제한한다. | Inherited |
| 설치된 스킬 변경은 Evaluate PASS 뒤 external-terminal apply에서만 허용한다. | transcript 오귀속과 자동 영구 변경 위험을 차단한다. | Inherited |
| 로컬 persistence는 Python `sqlite3`와 bounded spool을 사용한다. | 추가 dependency와 운영 서비스를 피한다. | Implemented |
| Phase 1의 작업 완료와 product gate 결과를 분리해 기록한다. | probe는 완료됐지만 결과가 FAIL이므로 다음 구현을 승인하지 않는다. | Recorded 2026-07-27 |
| turn-level capture와 접근 불가능한 fixed-root 가정은 session-level queue/data-root 계약으로 수정한다. | CLI/Desktop에서 네 가지 필수 Feasibility check가 실패했다. | Implemented |
| quality label은 사용자만 외부 TTY에서 입력하고 agent는 답을 추론하거나 대신 입력하지 않는다. | 품질 판정의 독립성과 명시적 승인 경계를 유지한다. | Active gate |
| collecting quality epoch의 시작 시각보다 늦은 `first_stop_at`만 review claim 대상으로 삼는다. | epoch 이전 backlog가 prospective quality cohort에 섞이는 것을 막고 같은 판정을 status에도 적용한다. | Implemented in 0.1.6 |

## Current State

- 현재 source/cache/installed plugin은 `0.1.7`이며 frozen implementation
  commit은 `4d535cb`다. 전체 554 tests 실행, 551 통과, 기존 3 skip, 실패
  0건이며 독립 코드·보안 검토는 CLEAN이다. 2026-09-12에 `4d535cb`의 7개
  production file과 packaged Phase 4 report의 source/cache parity를 확인했다.
  이후 소스 README에 릴리스 자동화 안내를 추가했으며 설치본은 갱신하지 않았다. Marketplace와 plugin
  source 모두 현재 프로젝트 경로를 사용한다. 설치 후 실제 Stop ingress
  증명과 Q-007 exact-command 승인이 다음 단계다.
- Phase 1의 최초 Feasibility `FAIL`은 Phase 2 session-level amendment로 보완됐고 amended report는 `PASS`다.
- Phase 3 Runtime Queue와 Phase 4 Review/Inbox는 canonical report `PASS`로 완료됐다.
- 이전 `0.1.6` release의 source/cache/installed parity는 7개
  production file에서 `PASS`다. Frozen implementation commit은
  `980118fcddce4f8b5271f82638c3f9d131bf0e7f`이고 release test suite는
  538 passed, 3 skipped로 기록돼 있다. 0.1.7 구현 전까지 뒤따른 commit은
  품질 cohort와 운영 상태를 기록하는 docs-only 변경이었다.
- `Q-004`는 `quality_provenance_drift`로 terminal `INVALID` 처리됐고
  immutable aggregate report digest는
  `a9d30a50776d7e1ad2b0f56d5be741fda8b9460f46090a88e1e18777e5f0e917`다.
- `Q-005`는 source `0.1.6` provenance 변경으로
  `quality_provenance_drift` terminal `INVALID` 처리됐다. sessions,
  candidates, labels는 모두 0이었고 immutable aggregate report digest는
  `781dbac0ba4e8e601ce6475e408204865127e5fd16a961e66cf47afc38a94c2f`다.
- 현재 단계는 Phase 5 Read-only Quality Gate다. Installed `0.1.6`이
  `2026-08-11T14:53:34Z`에 exact predecessor
  `Q-005@781dbac0ba4e8e601ce6475e408204865127e5fd16a961e66cf47afc38a94c2f`
  에서 `Q-006`을 열었다. Q-006은 표본 없이
  `2026-09-10T14:53:34Z`에 만료됐고, 2026-09-12에
  `quality_collection_expired` terminal `INVALID`로 확정됐다. sessions,
  candidates, labels는 모두 0이고 immutable report digest는
  `c06087eff41522ee6c76dd584a0009aaefccffad572a0e49867a36492014c3d3`다.
  당시 quality-contract digest는
  `d2479583d755d8196e05df5e63b5af12d52c5d3738c70a24555d8a9a5c81cc3b`,
  runtime digest는
  `69090bd71cb89b6d4889dd9119c344fc3f0c96efd953c80211f677a0b744c334`,
  transcript-adapter digest는
  `0fc96fa4a58f06221736200f2d4b7ec393269c42fc488c5aebd7822eac74509b`다.
- 2026-09-12 maintenance는 만료 pending 128건, 만료 spool 171건과 보존
  기간이 지난 raw metadata 22건을 정리했다. 후보, label, quality
  observation은 삭제하지 않았다. 정리 뒤 spool, pending, claimable,
  quarantined, cleanup overdue는 모두 0이다.
- 동일 provenance successor는 열 수 없다. 의도적 만료를 통한 표본
  cherry-pick을 막는 기존 guard는 유지한다. Phase 5 안에서 수집 deadline,
  남은 시간, 무효 사유와 advisory next_action을 노출하는 설계 A를 승인받아
  source `0.1.7`/quality contract v3으로 구현·설치했다. 실제 Stop 증명과
  exact-command 승인 뒤 successor epoch를 열어야 한다.
- Phase 6 Evaluate Runner Spike는 successor epoch의 최소 10개 distinct
  real sessions, explicit Review, user-only labels, terminal `PASS` 전까지
  차단되며 mutation, evaluate, apply capability는 계속 disabled다.

## Sources of Truth

- `docs/superpowers/specs/2026-07-26-skill-evolver-design.md`
- `docs/superpowers/plans/2026-07-26-skill-evolver-implementation-roadmap.md`
- `docs/feasibility-report.md`
- `docs/feasibility-report-v2.md`
- `docs/release-reports/runtime-queue.json`
- `docs/release-reports/review-inbox.json`
- `docs/superpowers/specs/2026-07-30-skill-evolver-session-quality-gate-design.md`
- `docs/release-reports/quality/Q-003-512aa6cb395e7a4d6c94a51ad2b9950fb8cada381381784370d3edecda01ac1a.json`
- `docs/release-reports/quality/Q-004-a9d30a50776d7e1ad2b0f56d5be741fda8b9460f46090a88e1e18777e5f0e917.json`
- `docs/release-reports/quality/Q-005-781dbac0ba4e8e601ce6475e408204865127e5fd16a961e66cf47afc38a94c2f.json`
- `docs/release-reports/quality/Q-006-c06087eff41522ee6c76dd584a0009aaefccffad572a0e49867a36492014c3d3.json`
- `docs/superpowers/specs/2026-08-04-skill-evolver-target-attribution-remediation-design.md`
- `docs/superpowers/specs/2026-08-11-skill-evolver-prospective-capture-cutoff-design.md`
- `docs/superpowers/specs/2026-09-12-skill-evolver-collection-deadline-design.md`
- `docs/superpowers/plans/2026-09-12-skill-evolver-collection-deadline.md`
