# Skill Evolver

## Overview

Skill Evolver는 Codex CLI와 Desktop 작업에서 재사용 가능한 스킬 개선 신호를 안전하게 수집하고, 사용자가 명시적으로 검토·평가·적용·복구를 승인하는 개인용 로컬 개선 루프다.

## Core Value

관찰은 최소한으로 자동화하되 판단과 파일 변경 권한은 사용자에게 남겨, 설치된 스킬을 승인 없이 변경하지 않는 안전한 개선 흐름을 제공한다.

## Target Runtime

- macOS Codex CLI와 Desktop
- `/usr/bin/python3` 3.9 이상
- Python 표준 라이브러리와 `sqlite3`만 사용
- 프로젝트 루트: `/Users/igyeongseob/Documents/오픈소스/skill-evolver`
- Git worktree 루트: `/Users/igyeongseob/Documents/오픈소스`
- `skill-evolver/` 안에 중첩 `.git`을 만들지 않음

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
- Phase 1 Feasibility 결과가 FAIL이므로 session-level 설계와 data-root 접근 계약이 PASS로 재검증될 때까지 Runtime Queue 구현은 금지한다.

## Key Decisions

| Decision | Rationale | Status |
|----------|-----------|--------|
| 관찰은 각 스킬이 아니라 중앙 matcher-free `Stop` Hook에서 수행한다. | 중복 지침과 누락을 피하고 Hook을 metadata-only로 유지한다. | Inherited |
| 인자 없는 호출은 status-only이고 review는 명시적 호출만 허용한다. | 비용과 개인정보 노출을 제한한다. | Inherited |
| 설치된 스킬 변경은 Evaluate PASS 뒤 external-terminal apply에서만 허용한다. | transcript 오귀속과 자동 영구 변경 위험을 차단한다. | Inherited |
| 로컬 persistence는 Python `sqlite3`와 bounded spool을 사용한다. | 추가 dependency와 운영 서비스를 피한다. | Planned |
| Phase 1의 작업 완료와 product gate 결과를 분리해 기록한다. | probe는 완료됐지만 결과가 FAIL이므로 다음 구현을 승인하지 않는다. | Recorded 2026-07-27 |
| turn-level capture와 접근 불가능한 fixed-root 가정은 session-level queue/data-root 계약으로 수정한다. | CLI/Desktop에서 네 가지 필수 Feasibility check가 실패했다. | Current direction |

## Current State

- Phase 1 Feasibility Spike 작업은 2026-07-27 완료됐다.
- product gate는 `FAIL`이다: `cli_skill_data_root`, `desktop_skill_data_root`, `cli_transcript_supported`, `desktop_transcript_supported`.
- 현재 단계는 Phase 2 Session-Level Capture Design Amendment다.
- Runtime Queue와 이후 단계는 amended Feasibility report가 `PASS`가 될 때까지 차단된다.

## Sources of Truth

- `docs/superpowers/specs/2026-07-26-skill-evolver-design.md`
- `docs/superpowers/plans/2026-07-26-skill-evolver-implementation-roadmap.md`
- `docs/feasibility-report.md`
