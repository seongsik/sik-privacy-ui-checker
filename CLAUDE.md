@.claude/shared/BASELINE.md

# Project-specific instructions

<!-- Rules for this repository only. sik-sdd-foundation never overwrites this file.
     Keep the first line's import. A rule that relaxes or overrides a BASELINE rule must state
     its reason and date on the same line, so reviewers can see the standard being bypassed. -->

## Commands
- Build:
- Test:
- Lint / typecheck:

## Conventions

## Documents
- 산출물 본문은 한국어, 경로·파일명은 영문.
- 설계서: `docs/specs/YYYY-MM-DD-<topic>.md`, 계획서: `docs/plans/YYYY-MM-DD-<feature>.md` (brainstorming·writing-plans 기본 경로 대신 이 경로를 쓴다).
- 요구사항: `docs/requirements/REQUIREMENTS.md`. 계획서 태스크 제목에 `[REQ-NNN]` 태그, 커밋 메시지에 `Refs: REQ-NNN` trailer.
- 요구사항 변경은 `docs/requirements/CHANGES.md`에 변경요청을 먼저 쓰고 승인 후 반영. CI 게이트: `.github/workflows/sdd-check.yml`(progress-report 스킬의 `templates/sdd-check.yml`).
