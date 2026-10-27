# privacy-ui-check 스킬 구현 계획

> **For agentic workers:** REQUIRED SUB-SKILL: executing-plans (기본). 계획서의 "실행 방식" 절이 병렬 조건 충족을 명시할 때만 subagent-driven-development. 체크박스(`- [ ]`)로 진행을 기록한다.

**Goal:** 운영 웹서비스를 읽기 전용으로 수집·판정해 조치방안이 담긴 HTML 보안성 검토 리포트를 만드는 privacy-ui-check 스킬을 데모 사이트 기준으로 완성한다.
**요구사항:** REQ-001, REQ-002, REQ-003, REQ-004, REQ-005, REQ-006, REQ-007, REQ-008, REQ-009, REQ-010, REQ-011, REQ-012, REQ-013, REQ-014
**Spec:** docs/specs/2026-10-27-privacy-ui-check.md
**승인:** seongsik 2026-10-27

## 개요

- **배경과 목표:** 설계서(A안: 일괄 수집 후 판정)에 따라 스킬 폴더 `skills/privacy-ui-check/`의 스크립트·기준 데이터·문서를 만든다. 사내 매뉴얼 스킬 킷(user-manual)의 캡처·가림·빌더·검증 코드를 고쳐 쓴다. 모든 기능은 취약점과 함정을 심은 로컬 데모 사이트로 검증한다.
- **범위:** 포함: 설계서 3-1의 스킬 폴더 전부, 데모 사이트, 단위·E2E 테스트. 제외: 실제 사이트 시범 점검(검수 조건 4번, 사용자 주도), 설계서 9장 남은 확인 사항 중 원문 확보(1·2번).
- **검수 조건:**
  1. `.venv/Scripts/python -m pytest -q` 전체 통과.
  2. 데모 E2E(Task 15): 쓰기 로그 0건(승인한 능동 시험 제외), 정답표의 관찰 항목 전부 탐지, 안전 화면 오탐 0, `verify_report.py` 종료코드 0.
  3. 데모 리포트의 개요·상세·인쇄 증거 png를 사용자가 육안으로 확인.
  4. 사용자가 승인한 실제 대상(staging 권장)에서 시범 점검 1회 후 결과 공유(계획 범위 밖, 후속).
- **위험:**
  - 이 PC에는 Python 3.8만 있다(킷은 3.10 이상 필요). Task 1 전에 사용자가 Python 3.10+를 설치해야 한다.
  - Playwright의 WebSocket 차단(`route_web_socket`)은 1.48 이상에서만 된다. Task 1에서 버전을 고정한다.
  - 킷 코드는 매뉴얼용이라 가정이 다를 수 있다(HTML 위 박스 등). 고쳐 쓸 때 설계서 5-4와 다르면 설계서를 따른다.
- **실행 방식:** Native(executing-plans). 의존 없는 태스크가 절반 미만이라 병렬 조건을 충족하지 않는다.

| 태스크 | 요구사항 | 가중치 | 의존 | 완료 기준 |
|---|---|---|---|---|
| 1. 개발 환경과 스킬 골격 | REQ-014 | 1 | 없음 | venv·의존성 고정, 골격 폴더, 스모크 테스트 통과 |
| 2. 데이터 모델 | REQ-001, REQ-007, REQ-011, REQ-013 | 2 | 1 | intake·menu_map·findings 검증, 정렬·번호·불일치 경고 |
| 3. 점검 기준 YAML | REQ-006, REQ-009 | 2 | 1 | 273개 항목이 스키마 통과, 7개 유형 배정 |
| 4. 데모 사이트 | REQ-014 | 3 | 1 | 심은 취약점·함정·쓰기 로그·정답표 |
| 5. 쓰기 차단(guard) | REQ-004, REQ-012 | 2 | 4 | 메서드·beacon·WebSocket·금지 GET·도메인 밖 차단, 잠시 허용·원복 |
| 6. 세션과 로그인 대기 | REQ-002 | 1 | 4 | 로그인 감지 후 세션 저장, 자격증명 미보관 |
| 7. 메뉴맵 수집 | REQ-003 | 2 | 2, 5, 6 | 데모 메뉴 트리 일치, 표본·금지 후보 표시 |
| 8. 수집기 | REQ-005, REQ-012, REQ-013 | 3 | 5, 6, 7 | 수집물 6종, HAR 정제, 중단 조건, 재개 |
| 9. 자동 점검 후보 | REQ-007 | 2 | 3, 8 | 데모 수집물에서 기계 탐지 항목 후보 생성 |
| 10. 능동 시험 | REQ-008 | 2 | 5, 8 | 승인 항목만 잠시 허용, 기록, 원복 |
| 11. 가림과 번호 박스 | REQ-010 | 2 | 8 | 픽셀화·불투명 가림, 박스를 이미지에 그림 |
| 12. 리포트 빌더 | REQ-011 | 3 | 2, 11 | 개요 + 취약점별 페이지, 목차 순서, 해시 이동 |
| 13. 리포트 검증기 | REQ-011, REQ-014 | 2 | 12 | 데모 리포트 기계 검증 종료코드 0, 증거 png |
| 14. SKILL.md와 참조 문서 | REQ-001, REQ-007, REQ-008, REQ-009, REQ-012 | 2 | 2~13 | 절차·판정·안전·능동 시험 문서, 링크 무결 |
| 15. 데모 E2E | REQ-014 | 3 | 1~14 | 검수 조건 2 충족 |

## Global Constraints

- 운영 환경이다. 데이터를 절대 수정하지 않는다(설계서 7-1).
- 로그인과 비밀번호 입력은 사용자가 격리 브라우저 창에서 직접 한다. 아이디·비밀번호는 채팅·파일·스크립트 인자에 남기지 않는다(설계서 7-2).
- 로그인 단계에는 write_guard·HAR 기록을 끄고 로그인 뒤에 켠다(설계서 7-2).
- 원본 스크린샷·DOM·HAR·세션 파일은 작업 폴더 밖에 두고 배포물에 넣지 않는다(설계서 3-2).
- 가림은 9px 픽셀화 + 3px 여유, 고유식별정보는 불투명 박스. 블러는 쓰지 않는다(설계서 5-4).
- 번호 박스는 이미지에 직접 그리고 문제점 번호와 1:1(설계서 4-3, 5-4).
- 판정 5종·위험도 3단계·권고 기준값은 설계서 6장 그대로.
- 단일 탭, 순차 수집, 페이지 사이 1~2초 대기. 429·403 연속·WAF·CAPTCHA는 즉시 중단(설계서 7-3).
- IDOR 등 다른 정보주체 정보를 조회하는 시험은 하지 않는다(설계서 1-2).
- 테스트는 데모 사이트(localhost)만 대상으로 한다. 외부 사이트에 접속하는 테스트를 만들지 않는다.

## Review Focus

- 조회를 POST로 하는 사내 시스템에서 write_guard가 화면을 깨뜨릴 때 사용자가 허용 목록을 고르는 흐름 — Task 5·8의 검증(데모에 조회성 POST 화면 포함), 실제 패턴은 검수 조건 4.
- 로그인 화면이 SSO로 다른 도메인을 거치는 경우의 범위 제한 — Task 6 검증은 단일 도메인만 다룬다. 다루는 태스크 없음(시범 점검에서 확인).
- 이미지·캔버스·PDF 뷰어 속 개인정보처럼 DOM이 없는 가림 대상 — Task 11은 좌표 수동 지정만 검증한다. 누락 여부는 사람 육안 검수(검수 조건 3)에 의존.
- Claude의 판정 품질(권고/취약 경계, 위험도 조정) — 스크립트 테스트로 다룰 수 없다. Task 15에서 데모 정답표와 대조하는 스킬 실행 1회로 확인.
- 점검 기준 YAML의 `strength`·`default_risk` 1차 값 — Task 3에서 사용자 확인을 받는다.

---

### Task 1: 개발 환경과 스킬 골격 [REQ-014] [w=1]

**목표:** Python 3.10+ venv와 고정된 의존성, 스킬·테스트 골격을 만든다.
**대상:** 생성 `requirements.txt`, `pytest.ini`, `skills/privacy-ui-check/`(빈 하위 폴더 구조, 설계서 3-1), `tests/test_smoke.py`, 수정 `.gitignore`, `CLAUDE.md`(Commands 절).
**의존:** 없음.
**인터페이스:** 후행 태스크에 `.venv/Scripts/python`, `skills/privacy-ui-check/scripts/` 패키지 경로를 준다.
**완료 기준:**
- playwright(1.48 이상)·pillow·pyyaml·pytest 버전이 고정되고 chromium이 설치된다.
- `.venv/`, 작업 원본 폴더 패턴이 `.gitignore`에 있다.
**검증:** `.venv/Scripts/python -m pytest -q tests/test_smoke.py`
Expected: `1 passed` 이상, 실패 0

- [ ] 테스트 작성, 실패 확인
- [ ] 구현
- [ ] 검증 명령 통과
- [ ] Commit (`Refs: REQ-014`)

### Task 2: 데이터 모델 [REQ-001, REQ-007, REQ-011, REQ-013] [w=2]

**목표:** intake·menu_map·findings 파일의 읽기·검증·정렬 로직을 한 모듈로 만든다.
**대상:** 생성 `skills/privacy-ui-check/scripts/models.py`, `skills/privacy-ui-check/templates/intake.example.json`, `templates/findings.example.json`, `tests/test_models.py`.
**의존:** Task 1.
**인터페이스:** 후행에 `load_intake`, `load_menu_map`, `load_findings`, `sort_and_number`, `validate_findings`를 준다.
**완료 기준:**
- 설계서 4-2·4-3 형식을 검증하고, URL·승인 정보가 없는 intake를 거부한다.
- 정렬·번호가 설계서 4-3 규칙(메뉴 → 위험도 → 취약·권고)을 따른다.
- 박스 수와 문제점 수가 다르면 경고, `inputs`에 비밀번호 계열 키워드 값이 있으면 오류.
**검증:** `.venv/Scripts/python -m pytest -q tests/test_models.py`
Expected: 실패 0

- [ ] 테스트 작성, 실패 확인
- [ ] 구현
- [ ] 검증 명령 통과
- [ ] Commit (`Refs: REQ-001, REQ-007, REQ-011, REQ-013`)

### Task 3: 점검 기준 YAML [REQ-006, REQ-009] [w=2]

**목표:** `docs/reference/criteria/` 초안을 설계서 4-1 형식의 YAML로 바꾸고 스키마로 검증한다.
**대상:** 생성 `tools/convert_criteria.py`, `skills/privacy-ui-check/references/criteria/{safe,pipa,netact,std}.yaml`, `skills/privacy-ui-check/scripts/criteria.py`, `tests/test_criteria.py`.
**의존:** Task 1.
**인터페이스:** 후행에 `load_criteria`(ID → 항목)를 준다.
**완료 기준:**
- 273개 항목이 모두 스키마를 통과하고 ID 중복이 없다. 모든 항목에 조치방안이 있다.
- 모든 항목에 7개 유형 중 하나, `strength`, `default_risk`가 있다. 1차 값은 사용자 확인을 받는다(확인 결과를 commit 메시지에 적는다).
**검증:** `.venv/Scripts/python -m pytest -q tests/test_criteria.py`
Expected: 실패 0, 출력에 `273 items`

- [ ] 테스트 작성, 실패 확인
- [ ] 구현
- [ ] 검증 명령 통과
- [ ] Commit (`Refs: REQ-006, REQ-009`)

### Task 4: 데모 사이트 [REQ-014] [w=3]

**목표:** 취약점·함정·가짜 개인정보를 심은 localhost 웹앱과 정답표를 만든다(설계서 8-1).
**대상:** 생성 `skills/privacy-ui-check/demo/`(서버, 페이지, `expected.json`, `menu_expected.json`), `tests/conftest.py`(데모 서버 fixture), `tests/test_demo.py`.
**의존:** Task 1.
**인터페이스:** 후행에 데모 서버 fixture(`demo_server`), 쓰기 로그 조회 엔드포인트, 정답표 경로를 준다.
**완료 기준:**
- 관찰 취약점 약 20개 + 능동 1개 이상, 안전 화면, 조회성 POST 화면이 있고 `expected.json`에 기준 ID와 기대 판정이 있다.
- 함정(저장 POST, 삭제 GET, 로그아웃, beacon, WebSocket)이 서버 쓰기 로그에 남는다.
**검증:** `.venv/Scripts/python -m pytest -q tests/test_demo.py`
Expected: 실패 0

- [ ] 테스트 작성, 실패 확인
- [ ] 구현
- [ ] 검증 명령 통과
- [ ] Commit (`Refs: REQ-014`)

### Task 5: 쓰기 차단(guard) [REQ-004, REQ-012] [w=2]

**목표:** 설계서 7-1의 1·2·3·5·6번 장치를 브라우저 컨텍스트에 거는 모듈을 만든다.
**대상:** 생성 `skills/privacy-ui-check/scripts/guard.py`, `tests/test_guard.py`.
**의존:** Task 4.
**인터페이스:** 후행에 `install_guard(context, intake, menu_map)`, `temporarily_allow(method, url)`(컨텍스트 매니저), `blocked_log`를 준다.
**완료 기준:**
- 데모 함정 5종을 실행해도 서버 쓰기 로그가 0건이고, 막힌 요청이 `blocked_log`에 남는다.
- 허용 목록의 조회성 POST만 통과하고, `temporarily_allow`가 끝나면 다시 막힌다.
**검증:** `.venv/Scripts/python -m pytest -q tests/test_guard.py`
Expected: 실패 0

- [ ] 테스트 작성, 실패 확인
- [ ] 구현
- [ ] 검증 명령 통과
- [ ] Commit (`Refs: REQ-004, REQ-012`)

### Task 6: 세션과 로그인 대기 [REQ-002] [w=1]

**목표:** 화면 표시 브라우저를 띄워 로그인 완료를 감지하고 세션을 작업 폴더 밖에 저장한다.
**대상:** 생성 `skills/privacy-ui-check/scripts/session.py`(킷 capture.py 로그인부 개작), `tests/test_session.py`.
**의존:** Task 4.
**인터페이스:** 후행에 `--storage-state` 파일 경로 규약과 `open_context(storage_state)`를 준다.
**완료 기준:**
- `selector:`·`url:` 감지로 로그인 뒤 세션 파일이 생기고, 작업 폴더 안 경로를 주면 거부한다.
- 아이디·비밀번호를 받는 인자가 없다. 로그인 단계에는 guard·HAR이 꺼져 있다.
**검증:** `.venv/Scripts/python -m pytest -q tests/test_session.py`
Expected: 실패 0

- [ ] 테스트 작성, 실패 확인
- [ ] 구현
- [ ] 검증 명령 통과
- [ ] Commit (`Refs: REQ-002`)

### Task 7: 메뉴맵 수집 [REQ-003] [w=2]

**목표:** 클릭 없이 메뉴 트리를 읽어 `menu_map.json` 초안을 만든다.
**대상:** 생성 `skills/privacy-ui-check/scripts/menumap.py`, `tests/test_menumap.py`.
**의존:** Task 2, 5, 6.
**완료 기준:**
- 데모에서 만든 메뉴 트리가 `menu_expected.json`과 같다.
- 반복 화면이 `sample`로 묶이고, 로그아웃·삭제 링크가 금지 후보로 표시된다.
**검증:** `.venv/Scripts/python -m pytest -q tests/test_menumap.py`
Expected: 실패 0

- [ ] 테스트 작성, 실패 확인
- [ ] 구현
- [ ] 검증 명령 통과
- [ ] Commit (`Refs: REQ-003`)

### Task 8: 수집기 [REQ-005, REQ-012, REQ-013] [w=3]

**목표:** 확정 메뉴마다 수집물 6종(스크린샷, DOM, HAR, 쿠키 속성, 저장소, 응답 헤더)을 guard 아래 모은다.
**대상:** 생성 `skills/privacy-ui-check/scripts/collect.py`(킷 capture.py 확장), `tests/test_collect.py`.
**의존:** Task 5, 6, 7.
**인터페이스:** 후행에 원본 폴더 구조(메뉴 ID별 수집물)와 `collect --only <메뉴ID>`(재수집)를 준다.
**완료 기준:**
- 데모 확정 메뉴 전부의 수집물이 원본 폴더(작업 폴더 밖)에 생기고, HAR에 비밀번호 계열 값이 없다.
- 데모의 429·WAF·CAPTCHA·세션 만료 화면에서 멈추고 사유를 출력하며, 다시 실행하면 끊긴 메뉴부터 이어 간다.
**검증:** `.venv/Scripts/python -m pytest -q tests/test_collect.py`
Expected: 실패 0

- [ ] 테스트 작성, 실패 확인
- [ ] 구현
- [ ] 검증 명령 통과
- [ ] Commit (`Refs: REQ-005, REQ-012, REQ-013`)

### Task 9: 자동 점검 후보 [REQ-007] [w=2]

**목표:** 수집물에서 기계로 판정할 수 있는 항목의 1차 후보를 기준 ID와 함께 만든다(설계서 3-1 auto_checks).
**대상:** 생성 `skills/privacy-ui-check/scripts/auto_checks.py`, `tests/test_auto_checks.py`.
**의존:** Task 3, 8.
**인터페이스:** 후행에 `candidates.json`(기준 ID, 메뉴, 근거 위치, 박스 대상 셀렉터)을 준다.
**완료 기준:**
- 탐지기마다 양성·음성 예제가 테스트에 있다.
- 데모 수집물에서 정답표의 "자동 탐지 가능" 항목을 모두 후보로 내고, 안전 화면에서는 후보가 없다.
**검증:** `.venv/Scripts/python -m pytest -q tests/test_auto_checks.py`
Expected: 실패 0

- [ ] 테스트 작성, 실패 확인
- [ ] 구현
- [ ] 검증 명령 통과
- [ ] Commit (`Refs: REQ-007`)

### Task 10: 능동 시험 [REQ-008] [w=2]

**목표:** 승인된 항목에서만 해당 요청을 잠시 허용하고, 사용자 입력 과정을 기록한다.
**대상:** 생성 `skills/privacy-ui-check/scripts/active_test.py`, `tests/test_active_test.py`.
**의존:** Task 5, 8.
**완료 기준:**
- 승인 정보 없이는 실행을 거부하고, 실행 결과가 findings의 `active_test` 형식(설계서 4-3)으로 기록된다.
- 데모 인증 실패 시험에서 허용한 요청만 쓰기 로그에 남고, 끝나면 다시 차단된다. 최대 시도 횟수를 넘으면 멈춘다.
**검증:** `.venv/Scripts/python -m pytest -q tests/test_active_test.py`
Expected: 실패 0

- [ ] 테스트 작성, 실패 확인
- [ ] 구현
- [ ] 검증 명령 통과
- [ ] Commit (`Refs: REQ-008`)

### Task 11: 가림과 번호 박스 [REQ-010] [w=2]

**목표:** 원본 캡처에 가림과 번호 박스를 적용해 배포용 이미지와 미리보기를 만든다.
**대상:** 생성 `skills/privacy-ui-check/scripts/mask.py`(킷 apply_mosaic.py 개작), `tests/test_mask.py`.
**의존:** Task 8.
**완료 기준:**
- 측정한 가림 좌표 영역의 픽셀이 원본과 달라지고(픽셀화·불투명), 수동 좌표(`--extra`)도 적용된다.
- 번호 박스와 번호가 이미지에 그려지고, 박스 좌표가 findings의 `boxes`로 기록된다.
**검증:** `.venv/Scripts/python -m pytest -q tests/test_mask.py`
Expected: 실패 0

- [ ] 테스트 작성, 실패 확인
- [ ] 구현
- [ ] 검증 명령 통과
- [ ] Commit (`Refs: REQ-010`)

### Task 12: 리포트 빌더 [REQ-011] [w=3]

**목표:** findings와 이미지로 설계서 5장의 단일 HTML 리포트를 만든다.
**대상:** 생성 `skills/privacy-ui-check/scripts/report/`(빌더, CSS, JS — 킷 builder 개작), `tests/test_report.py`.
**의존:** Task 2, 11.
**인터페이스:** 후행에 `build_report.py --workdir <작업 폴더>`와 출력 파일 이름 규약을 준다.
**완료 기준:**
- 개요 6개 블록(설계서 5-2)과 취약·권고마다 상세 페이지 하나가 생기고, 상세 필드 12개가 모두 있다.
- 좌측 목차가 메뉴 → 위험도 → 취약·권고 순이고 "공통(전 화면)"이 마지막이다. 해시로 페이지가 바뀐다.
**검증:** `.venv/Scripts/python -m pytest -q tests/test_report.py`
Expected: 실패 0

- [ ] 테스트 작성, 실패 확인
- [ ] 구현
- [ ] 검증 명령 통과
- [ ] Commit (`Refs: REQ-011`)

### Task 13: 리포트 검증기 [REQ-011, REQ-014] [w=2]

**목표:** 리포트를 브라우저로 열어 설계서 8-4 항목을 검사하고 증거 png를 남긴다.
**대상:** 생성 `skills/privacy-ui-check/scripts/verify_report.py`(킷 verify_manual.py 개작), `tests/test_verify_report.py`.
**의존:** Task 12.
**완료 기준:**
- 정상 리포트는 종료코드 0, 이미지 누락·목차 깨짐을 넣은 리포트는 종료코드 1.
- 개요·상세·확대·인쇄·모바일 증거 png가 지정 폴더에 생긴다.
**검증:** `.venv/Scripts/python -m pytest -q tests/test_verify_report.py`
Expected: 실패 0

- [ ] 테스트 작성, 실패 확인
- [ ] 구현
- [ ] 검증 명령 통과
- [ ] Commit (`Refs: REQ-011, REQ-014`)

### Task 14: SKILL.md와 참조 문서 [REQ-001, REQ-007, REQ-008, REQ-009, REQ-012] [w=2]

**목표:** 스킬 절차(설계서 2장), 판정 기준(6장), 안전 규칙(7장), 능동 시험 절차를 문서로 쓴다.
**대상:** 생성 `skills/privacy-ui-check/SKILL.md`, `references/judgment.md`, `references/safety.md`, `references/active-tests.md`, `templates/scenario.example.json`, `tests/test_skill_docs.py`.
**의존:** Task 2~13.
**완료 기준:**
- SKILL.md frontmatter(name, description)가 있고, 본문이 참조하는 스크립트·문서 경로가 모두 존재한다.
- `active-tests.md`가 기준 데이터의 능동 항목 10개를 모두 다룬다. 착수 질문지가 intake 스키마와 맞는다.
**검증:** `.venv/Scripts/python -m pytest -q tests/test_skill_docs.py`
Expected: 실패 0

- [ ] 테스트 작성, 실패 확인
- [ ] 구현
- [ ] 검증 명령 통과
- [ ] Commit (`Refs: REQ-001, REQ-007, REQ-008, REQ-009, REQ-012`)

### Task 15: 데모 E2E [REQ-014] [w=3]

**목표:** 데모 사이트에 스킬 절차 전체(수집 → 자동 후보 → 판정 → 가림 → 리포트 → 검증)를 돌려 검수 조건 2를 확인한다.
**대상:** 생성 `tests/test_e2e_demo.py`, 수정 필요 시 앞 태스크 산출물.
**의존:** Task 1~14.
**완료 기준:**
- 쓰기 로그 0건(승인한 능동 시험 제외), 정답표 관찰 항목 전부 탐지, 안전 화면 오탐 0, `verify_report.py` 종료코드 0.
- Claude가 SKILL.md대로 데모를 한 번 점검한 결과를 정답표와 대조해 차이를 사용자에게 보고하고, 증거 png 육안 검수 기록을 남긴다.
**검증:** `.venv/Scripts/python -m pytest -q tests/test_e2e_demo.py`
Expected: 실패 0

- [ ] 테스트 작성, 실패 확인
- [ ] 구현
- [ ] 검증 명령 통과
- [ ] Commit (`Refs: REQ-014`)
