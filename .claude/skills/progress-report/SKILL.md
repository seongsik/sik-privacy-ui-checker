---
name: progress-report
description: Use right after a plan task is completed (after Superpowers task-done succeeds) to tick the task and keep its test evidence, when the user asks for 공정률, 진척, 진행률, progress, 추적 매트릭스, or when resuming a plan (run check). Weighted progress from plan checkboxes via scripts/progress.sh; writes only under docs/. Not for work without a plan file.
---

# progress-report (공정률·추적 매트릭스)

스크립트: `bash .claude/skills/progress-report/scripts/progress.sh <command>` (프로젝트 루트 어디서든 실행 가능).
산출 규칙과 파일 형식은 `sdd-deliverables` 스킬과 같은 설계(docs/specs/2026-10-24-sdd-extension.md in the standard)를 따른다.

| 명령 | 언제 | 결과 |
|---|---|---|
| `done <plan> <N>` | 태스크 N의 `task-done`이 성공한 직후 | 계획서 체크박스 체크, `docs/test-reports/<plan>.md`에 증적 행, status 갱신 |
| `status` | 체크박스를 손으로 고쳤거나 요구사항·계획서를 편집한 뒤 | `docs/progress/status.json`, `docs/traceability.md` 갱신 (변경 시에만 씀) |
| `report` | 공정률·진척 보고 요청 | `docs/progress/YYYY-MM-DD-report.md` |
| `check` | 계획서 실행을 시작·재개할 때 | 불일치 경고, 있으면 exit 1 |
| `check --ci` | CI(pull request)에서 | 같은 경고를 출력하되 게이트 항목(태그 없는 태스크, 미정의 요구사항, 미승인 설계서·계획서, 오래된 status.json, ledger 완료인데 미체크, 변경요청 없는 요구사항 변경, CLAUDE.md 없음·BASELINE import 누락)에서만 exit 1. 백로그 요구사항·체크박스 없는 태스크·제안 상태 CR·기준선 없음(또는 구 형식)·CLAUDE.md Documents 절 없음은 통과 |
| `baseline` | 변경요청 묶음이 끝나 설계서·계획서가 재승인된 뒤 | `REQUIREMENTS.md`를 `docs/requirements/BASELINE.md`로 복사(스냅샷)하고 헤더의 `**기준선:**` 줄을 갱신한 뒤 `status`를 실행. 이후 바뀐 요구사항은 승인된 CR이 있어야 `check`를 통과. 스냅샷은 git 이력과 무관하므로 squash merge·rebase·얕은 clone에서도 동작한다. 두 파일을 함께 commit |

"오래된 status.json" 판정은 계획서·요구사항에서 계산되는 부분(`overall`, `plans`, `requirements`의 진행 값)만 비교한다. 디렉터리 이름, 표준 버전, 기준선, CR 집계, 날짜, 커밋 수가 달라진 것만으로는 stale이 아니며, 그런 필드는 다음 `status`·`done` 때 따라온다.
| `status --tsv` | 외부 집계용 | 한 줄 TSV, 파일 안 씀 |

## 절차

1. **태스크 완료** — `executing-plans`/`subagent-driven-development`의 `task-done`이 성공하면 `progress.sh done <plan> <N>`을 실행하고, 바뀐 계획서·시험결과서·`status.json`·`traceability.md`를 그 태스크의 커밋에 포함한다. 태스크 커밋이 이미 끝났으면 `chore(progress): Task N 완료 기록` 커밋 하나를 만든다. 커밋 메시지는 태스크의 `Refs:` trailer를 유지한다.
2. **보고 요청** — `progress.sh report`를 실행하고 요약 표(공정률, 태스크 완료/전체, 미추적 수)와 파일 경로를 답한다. 숫자는 보고서 값을 그대로 쓰고 새로 계산하지 않는다.
3. **HTML 요청** — 사용자가 그림·HTML을 원하면 보고서 Markdown을 입력으로 `visual-explainer` 스킬의 data-table 템플릿을 써서 `.agent-visuals/progress-YYYY-MM-DD.html`을 만든다. 스크립트는 HTML을 만들지 않는다.
4. **시작·재개** — 계획서 실행을 시작하거나 재개할 때 `progress.sh check`를 한 번 실행해 경고를 사용자에게 알린다. 경고를 고치는 것은 사용자의 선택이다.
5. **CI 게이트 제안** — 프로젝트에 계획서가 처음 생기고 `.github/workflows/sdd-check.yml`이 없으면, 이 스킬의 `templates/sdd-check.yml`을 그 경로에 추가할지 **한 번만** 묻는다. 사용자가 거절하면 다시 묻지 않는다(`.github/`는 프로젝트 소유라 표준이 자동 설치하지 않는다). 템플릿은 `fetch-depth: 0`으로 전체 이력을 받아 설치된 사본으로 `check --ci`를 실행한다.
6. **기준선 갱신** — 변경요청(CHANGES.md)이 승인되고 그에 따른 요구사항·설계서·계획서 수정과 재승인이 끝나면 `progress.sh baseline`을 실행하고 그 변경을 commit한다. 변경 절차 자체는 `sdd-deliverables` 스킬이 맡는다.

## 산출 규칙 요약

- 태스크 완료 = 체크박스가 1개 이상이고 모두 체크. 부분 완료 없음.
- 가중치 = 제목의 `[w=N]`, 없으면 1. 공정률 = Σ완료 가중치 / Σ전체 가중치.
- 집계에서 빼려면 계획서를 `docs/plans/archive/`로 옮긴다(하위 디렉터리는 읽지 않는다).
- `status.json`, `traceability.md`, 시험결과서는 손으로 편집하지 않는다.
