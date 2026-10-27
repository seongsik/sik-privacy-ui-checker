---
name: sdd-deliverables
description: Use when brainstorming takes the architectural path (requirements must be recorded), when a design spec or implementation plan is written or approved (header fields, requirement IDs, approval record), when an architecture-level decision is made (ADR), or when the user mentions 요구사항, 요구사항 정의서, 설계서 승인, ADR, 산출물. Project-scoped deliverables under docs/, Korean body, English paths. Not for bounded/spike work.
---

# sdd-deliverables (산출물·추적성 규칙)

계획서(writing-plans 산출물)가 있는 작업만 대상이다. bounded·spike 작업은 채팅 승인으로 끝나며 산출물을 만들지 않는다.
템플릿은 이 스킬의 `templates/`에 있다. 파일은 프로젝트의 `docs/` 아래에 만들며 프로젝트 소유다.

## 산출물 위치

| 산출물 | 경로 | 템플릿 |
|---|---|---|
| 요구사항 정의서 | `docs/requirements/REQUIREMENTS.md` | `templates/requirements.md` |
| 요구사항 기준선 | `docs/requirements/BASELINE.md` (`progress.sh baseline`이 만드는 스냅샷, 직접 편집 금지) | 없음 (생성 파일) |
| 요구사항 변경요청 | `docs/requirements/CHANGES.md` | `templates/changes.md` |
| 설계서 | `docs/specs/YYYY-MM-DD-<topic>.md` | 헤더 `templates/spec-header.md` + brainstorming 본문 |
| 계획서 (WBS) | `docs/plans/YYYY-MM-DD-<feature>.md` | `writing-plans` 스킬의 `templates/plan.md` (PM용 개요 + 코드 없는 태스크) |
| 결정 기록 | `docs/decisions/ADR-NNN-<slug>.md` | `templates/adr.md` |
| 시험결과서 | `docs/test-reports/<plan-basename>.md` | `progress.sh done`이 생성 (`templates/test-report.md`와 같은 형식) |

brainstorming과 writing-plans의 기본 경로(`docs/superpowers/...`) 대신 위 경로를 쓴다. 두 스킬은 사용자 선호가 기본 경로를 덮어쓴다고 명시한다.

## 절차

1. **요구사항 기록** — brainstorming이 architectural 경로로 들어가면 `REQUIREMENTS.md`가 없을 때 템플릿으로 만들고, 합의된 요구사항을 행으로 추가한 뒤 사용자 확인을 받는다. 기존 행이 바뀌면 바뀐 내용을 사용자에게 알린다. ID는 재사용하지 않는다.
2. **설계서 헤더** — 설계서 첫 줄 아래에 `**요구사항:** REQ-001, REQ-002` 와 `**승인:** (미승인)` 를 넣는다. 요구사항은 쉼표로 구분한 명시적 목록이어야 한다(범위 표기 금지: 스크립트가 읽지 못한다).
3. **계획서 규칙** — 헤더에 `**Spec:**`과 `**승인:** (미승인)`. 태스크 제목은 `### Task N: 제목 [REQ-001, REQ-002] [w=2]` (태그는 제목 끝, 가중치는 생략 가능). 모든 태스크에 요구사항 태그를 단다. 각 태스크의 Commit 단계 메시지는 `Refs: REQ-NNN` trailer로 끝난다.
4. **승인 기록** — 사용자가 채팅에서 설계서 또는 계획서를 승인하면 즉시 `**승인:** <git config user.name> YYYY-MM-DD` 로 바꾸고 commit한다. 승인된 문서를 실질적으로 고치면 `(미승인, YYYY-MM-DD 수정)`으로 되돌리고 재승인을 받는다. 오타 수정은 해당하지 않는다.
5. **ADR** — 아키텍처·인터페이스·의존성 수준 결정은 `templates/adr.md`로 `docs/decisions/ADR-NNN-<slug>.md`에 쓴다(NNN은 기존 최대값 + 1). `agentmemory`의 Decisions에는 한 줄과 ADR 링크만 남긴다. 승인된 ADR은 수정하지 않고 새 ADR로 대체한다.
6. **진행 기록** — 태스크 완료와 공정률은 `progress-report` 스킬이 맡는다. 이 스킬은 문서를 만들고, 그 스킬은 문서를 집계한다.

## 요구사항 변경 절차 (기준선 이후)

기준선은 `progress.sh baseline`이 `REQUIREMENTS.md`를 복사해 둔 `docs/requirements/BASELINE.md`다(헤더의 `**기준선:**` 줄은 참고 정보). 기준선과 달라진 요구사항 행(추가·수정·폐기)이 있으면 `progress.sh check`가 승인된 변경요청을 요구한다(CI 게이트). 순서를 지킨다. **승인 전에는 요구사항 표를 고치지 않는다.**

1. **변경요청 작성** — `CHANGES.md`(없으면 `templates/changes.md`)에 행을 추가한다: `CR-NNN`(기존 최대값 + 1, 재사용 금지), 일자, 요청자, 사유, 영향 요구사항(쉼표 구분 REQ ID), 상태 `제안`.
2. **영향 분석** — `docs/traceability.md`에서 영향 요구사항의 설계서, 계획서(완료/전체 태스크), 커밋 수를 "영향 산출물"에 옮겨 적는다. 진행 중인 계획서가 있으면 그 사실을 사용자에게 알린다.
3. **승인** — 사용자가 채팅에서 승인하면 상태를 `승인`, 승인 열을 `<git config user.name> YYYY-MM-DD`로 채운다. 기각이면 `기각`으로 두고 끝낸다.
4. **반영** — 승인된 뒤에만 `REQUIREMENTS.md` 행을 고치고(출처 열에 CR ID), 영향받는 설계서·계획서를 수정한 뒤 `progress.sh status`를 실행해 `status.json`·`traceability.md`를 같은 commit에 넣는다(요구사항 배열이 바뀌므로 빠뜨리면 CI가 stale로 본다). 승인된 설계서·계획서의 실질 수정은 위 4번 규칙대로 `(미승인, YYYY-MM-DD 수정)`으로 되돌리고 재승인을 받는다.
5. **기준선 갱신** — 변경 묶음의 설계서·계획서가 재승인되면 `progress.sh baseline`을 실행하고 `BASELINE.md`·`REQUIREMENTS.md`·`status.json`을 commit한다. 이때부터 다음 변경은 새 스냅샷과 비교된다.

처음 도입하는 프로젝트는 요구사항 정의서를 commit한 직후 `progress.sh baseline`으로 첫 기준선을 잡는다. 기준선이 없으면 `check`가 비게이트 경고만 낸다.

## 금지

- 비밀 정보(키, 토큰, 접속 문자열)를 어떤 산출물에도 쓰지 않는다 (BASELINE 5).
- `docs/traceability.md`, `docs/progress/status.json`, 시험결과서를 손으로 편집하지 않는다.
- 요구사항 없이 계획서 태스크를 만들지 않는다. 필요하면 먼저 요구사항을 추가한다.
