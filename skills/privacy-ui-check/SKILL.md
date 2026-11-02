---
name: privacy-ui-check
description: 운영 중인 웹서비스를 격리 브라우저에서 읽기 전용으로 탐색해 개인정보보호 기준(개인정보의 안전성 확보조치 기준·안내서, 개인정보 보호법·시행령, 정보통신망법, 표준 개인정보 보호지침) 준수 여부를 점검하고, 취약점마다 조치방안과 증적(개인정보를 가린 캡처 + 번호 박스)을 담은 HTML 보안성 검토 리포트를 만든다. "개인정보 취약점 점검", "안전성 확보조치 점검", "개인정보 보안성 검토 리포트", "웹사이트 개인정보 점검", "privacy-ui-check" 요청 시 사용한다. 운영 데이터는 절대 수정하지 않는다.
---

# privacy-ui-check — 웹서비스 개인정보보호 점검

## 핵심 원칙

1. **운영 데이터를 절대 바꾸지 않는다.** 모든 접속은 쓰기 차단(write_guard)을 건 격리 브라우저로만 한다. 규칙: [references/safety.md](references/safety.md)
2. **아이디·비밀번호는 사용자가 격리 브라우저 창에 직접 입력한다.** 채팅·파일·스크립트 인자로 받지 않는다. 사용자가 채팅에 적으면 쓰지 말고 창에 직접 입력하도록 다시 안내한다.
3. **능동 시험은 항목마다 승인받고, 입력은 사용자가 한다.** 승인하지 않은 항목은 점검불가로 남긴다. 절차: [references/active-tests.md](references/active-tests.md)
4. **다른 사람의 개인정보를 조회하게 되는 시험(IDOR 파라미터 변조 등), 공격형 진단(SQL 삽입·XSS), CAPTCHA 풀기는 하지 않는다.**
5. **원본(스크린샷·DOM·HAR·세션 파일)은 작업 폴더 밖에 둔다.** 리포트에는 가린 사본만 넣는다. 끝나면 원본 삭제를 안내한다.
6. **판정은 증적으로 한다.** 자동 후보(auto_checks)는 출발점일 뿐, 최종 판정·위험도·문구는 수집물과 기준을 대조해 정한다. 기준: [references/judgment.md](references/judgment.md)
7. **검증은 기계 + 육안.** `verify_report.py` 종료코드 0 은 절반이다. 증거 png 와 가린 캡처를 직접 열어 본 뒤에만 완료라고 말한다.

## 준비

- 파이썬 3.10+, 이 스킬 폴더의 의존성: `pip install pillow playwright pyyaml` 후 `python -m playwright install chromium`.
- 모든 명령은 **이 스킬 폴더(`skills/privacy-ui-check/`)에서** `python -m scripts.<모듈>` 로 실행한다.
  파이썬은 의존성을 설치한 환경을 쓴다. 개발 저장소(sik-privacy-ui-checker)에서 연결해 쓰는 경우 `<저장소>\.venv\Scripts\python.exe`.
- **화면이 뜨는 단계(Chromium 설치, 1단계 로그인, 5단계 능동 시험)와 점검 대상에 접속하는 단계는 사용자의 터미널에서 실행한다.**
  Claude 의 명령 실행 환경이 격리(sandbox)돼 있으면 창을 띄우지 못하거나, 거기서 설치한 Chromium·띄운 서버가 사용자 쪽에서 보이지 않는다.
- 모든 브라우저 명령은 `--browser chrome` 으로 **PC 에 설치된 정식 Chrome** 을 새 격리 프로필로 띄울 수 있다(기본 `chromium`).
  사내 보안 프로그램이 Playwright 가 내려받은 Chromium 의 접속을 막을 때(예: `ERR_FILE_NOT_FOUND`) 쓴다. 쓰기 차단·가림은 그대로 동작한다.
- 보안 프로그램이 **자동화 도구가 띄운 브라우저의 네트워크를 통째로 막는 PC**(`--browser chrome` 으로도 공개 사이트까지 실패)에서는
  `--browser cdp` 를 쓴다. 사용자가 터미널에서 Chrome 을 원격 디버깅 포트와 **점검 전용 프로필**로 직접 띄우고, 스크립트가 그 Chrome 에 붙는다:
  ```powershell
  & 'C:\Program Files\Google\Chrome\Application\chrome.exe' --remote-debugging-port=9222 --user-data-dir=<원본 폴더>\cdp-profile --no-first-run
  ```
  점검은 그 Chrome 안의 새 격리 컨텍스트에서 하므로 쓰기 차단·HAR 가림·가림 측정은 그대로 동작한다.
  디버깅 포트가 열린 동안에는 이 PC 의 다른 프로그램도 그 Chrome 을 조종할 수 있다. 평소 프로필로 띄우지 말고, 점검이 끝나면 그 Chrome 을 닫는다.
- 폴더 두 개를 정한다:
  - **작업 폴더** `<사이트>_<날짜>/` — intake.json, menu_map.json, findings.json, img/, 리포트(배포 대상)
  - **원본 폴더** — 작업 폴더 **밖**(예: `<작업 폴더>_raw/`). 세션 파일, raw/, preview/, evidence/ 를 여기에 둔다.
- 처음이면 데모 사이트로 한 번 돌려 본다: `python demo/server.py --port 8765` → http://127.0.0.1:8765 (테스트 계정 hong / demo-pass, 정답표 `demo/expected.json`).

## 단계별 절차

### 0. 착수 — 묻고 기록한다
**대상 URL과 점검 승인 정보(승인자·범위·기간)가 없으면 시작하지 않는다.** 다음을 사용자에게 묻고 `templates/intake.example.json` 을 복사해 `intake.json` 에 적는다.

| 키 | 묻는 것 |
|---|---|
| `target_url` | 점검 대상 주소 |
| `approval.approver` / `scope` / `period` | 점검 승인자, 범위(제외 영역 포함), 기간 |
| `inspector`, `account_role` | 점검자, 점검에 쓸 테스트 계정의 역할 |
| `allowed_domains` | 점검 범위 도메인. 하위 도메인까지 포함되므로(`corp.kr` 이면 `*.corp.kr` 전부) 상위 도메인 대신 정확한 호스트를 적는다 |
| `deny_patterns` | 이 사이트에서 열기만 해도 상태가 바뀌는 주소(예: 자체 로그아웃·삭제 처리 경로). 메뉴맵 수집 중에도 막는다 |
| `conditions.users_over_1m_daily` | 전년도 말 직전 3개월 일평균 이용자 100만 명 이상인가(인터넷망 차단) |
| `conditions.subjects_over_100k` | 정보주체 10만 명 이상(대기업·중견·공공) 또는 100만 명 이상(중소)인가 |
| `conditions.public_institution` / `conditions.public_system` | 공공기관인가, 공공시스템으로 지정됐는가 |
| `conditions.children_under_14` | 만 14세 미만 아동이 가입할 수 있는가 |
| `conditions.sends_marketing` | 광고성 정보를 보내는가 |
| `conditions.has_test_account` | 능동 시험용 테스트 계정이 있는가(없으면 능동 시험은 모두 점검불가) |

### 1. 로그인 — 사용자가 직접
```bash
python -m scripts.session --url <대상> --login-wait selector:<로그인 뒤에만 보이는 요소> \
  --storage-state <원본 폴더>/session.json --workdir <작업 폴더> [--browser chrome]
```
- 열린 창에서 사용자가 로그인한다. 로그인 단계에는 쓰기 차단·HAR 기록이 꺼져 있다.
- **로그인 뒤 처음 도착하는 화면은 쓰기 요청(로그 전송 등)이 없는 단순한 화면이 되도록 안내한다**(차단이 켜지기 전이다).
- 세션 파일에는 로그인 쿠키가 있다: 작업 폴더 밖, 저장소 제외, 끝나면 삭제.

### 2. 메뉴맵 — 자동 수집 후 사용자 확인
```bash
python -m scripts.menumap --intake <작업 폴더>/intake.json --storage-state <원본 폴더>/session.json \
  --out <작업 폴더>/menu_map.json
```
- GET 으로만 링크를 읽는다. 결과의 메뉴 트리를 사용자에게 보여 주고 확인받는다:
  점검할 메뉴(`visit`), 반복 화면 표본(`sample` + `sample_of`), 제외(`exclude` + 사유), 상태 변경 링크(`blocked`, `deny_patterns`).
- 결과의 `probes`(robots.txt 의 숨은 경로)와 `attachments`(첨부 파일)를 보여 주고, 사용자가 동의한 숨은 경로는 `"path": ["숨은 경로", "<경로>"]` 메뉴로 추가한다.
- 확정한 menu_map.json 의 `path` 가 리포트 좌측 목차의 뼈대다.

### 3. 수집 — 쓰기 차단 아래에서
```bash
python -m scripts.collect --intake <작업 폴더>/intake.json --menu-map <작업 폴더>/menu_map.json \
  --storage-state <원본 폴더>/session.json --raw-dir <원본 폴더>/raw --workdir <작업 폴더>
```
- 메뉴마다 스크린샷·DOM·HAR(비밀번호·토큰 가림)·쿠키 속성·저장소·응답 헤더·meta(막힌 요청, 가림·박스 좌표)를 남긴다.
- **종료코드 2(중단)** 이면 이유를 사용자에게 알린다: `rate-limited`·`waf`·`captcha`·`forbidden-repeated` 는 사이트 보호 장치다 — 재시도하지 말고 사용자 결정을 받는다. `session-expired` 는 1단계 로그인을 다시 하고 같은 명령으로 이어 간다.
- 조회 화면이 "네트워크 오류"로 깨지면 meta.json 의 `blocked` 목록을 보여 주고, 사용자가 조회용이라고 확인한 POST 경로만 menu_map.json 의 `allow_post`(경로 전체 일치 정규식 목록)에 저장해 그 메뉴를 다시 수집한다(`--only <메뉴ID>`; 한 번만 시험할 땐 `--allow-post`).
  허용한 POST 는 meta.json 의 `allowed` 에 본문 앞부분(비밀번호·토큰 가림)과 함께 남는다 — 같은 경로로 쓰기 요청이 나갔는지 확인한다.
  한 주소로 조회·저장을 모두 처리하는 사이트(*.xp, 단일 .do 디스패처, /graphql)는 허용하지 말고 그 화면을 점검불가로 둔다.

### 4. 판정 — 자동 후보 + 검토
```bash
python -m scripts.auto_checks --raw-dir <원본 폴더>/raw --menu-map <작업 폴더>/menu_map.json \
  --out <작업 폴더>/candidates.json
```
- 후보마다 수집물(dom.html, network.har, screenshot.png)을 직접 확인하고 `references/criteria/*.yaml` 의 양호·취약 기준으로 판정한다. 오탐은 버린다.
- 자동 후보가 없는 관찰 항목(동의 화면 구성, 처리방침 기재 사항, 마스킹 일관성, 권리행사 경로 등)은 기준의 `type: 관찰` 항목을 메뉴별로 훑어 직접 판정한다.
- 판정·위험도 규칙: [references/judgment.md](references/judgment.md). 결과는 `templates/findings.example.json` 형식으로 `findings.json` 에 적는다.
- 상세 페이지를 만들 취약·권고마다 번호 박스를 달 요소를 정하고, 그 화면만 박스 좌표와 함께 다시 수집한다:
  `templates/boxes.example.json` 형식 파일을 만들어 `--only <메뉴ID> --boxes <파일>` 로 수집한다.

### 5. 능동 시험 — 항목별 승인
- [references/active-tests.md](references/active-tests.md) 의 항목마다 **승인 문구를 그대로 보여 주고** 사용자 승인을 받는다. 승인 문구·승인자·최대 시도 횟수를 정한다.
```bash
python -m scripts.active_test --intake <작업 폴더>/intake.json --menu-map <작업 폴더>/menu_map.json \
  --storage-state <원본 폴더>/session.json --item SAFE-05-06-01 --approval "<승인 문구>" --approved-by "<승인자>" \
  --start-url <시작 화면> --target GET <로그인 처리 주소> --max-attempts 6 --raw-dir <원본 폴더>/raw --workdir <작업 폴더>
```
- 열린 창에서 사용자가 직접 입력한다. 승인한 대상 요청만 최대 횟수까지 서버로 간다. 출력 record 를 해당 finding 의 `active_test` 에 넣는다.

### 6. 나머지 판정
- `type: 서면` 항목은 `others` 에 `점검불가` + `request`(요청 자료)로, intake 의 적용조건에 해당하지 않는 항목은 `해당없음` + 사유로 적는다. 양호로 확인한 항목도 `others` 에 `양호` 로 남긴다.

### 7. 증적 — 가림과 번호 박스
```bash
python -m scripts.mask --raw-menu <원본 폴더>/raw/<메뉴ID> --out-dir <작업 폴더>/img --name <V번호>_1 \
  --preview-dir <원본 폴더>/preview
```
- 출력 JSON 을 그 finding 의 `shots` 에 넣는다. 박스 번호와 `problems` 번호를 1:1 로 맞춘다(한 finding 의 여러 캡처에 걸쳐 번호가 겹치지 않게).
- **미리보기와 가린 사본을 전부 원본 크기로 직접 연다.** 이미지·캔버스 속 글자처럼 못 잡은 곳은 `--extra '[[x,y,w,h]]'` 로 더 가리고 다시 확인한다. 좌표가 흔들렸다(`rects_stable=false`)는 오류가 나면 그 화면만 다시 수집한다.

### 8. 리포트와 검증
```bash
python -m scripts.report.build_report --workdir <작업 폴더>
python -m scripts.verify_report --html <작업 폴더>/REPORT_<사이트>_<날짜>.html --out-dir <원본 폴더>/evidence
```
- 빌드 경고(박스·문제점 불일치)를 0 으로 만든다. 검증기 종료코드 0 필수.
- 증거 png(개요, 첫·마지막 상세, 확대, 인쇄, 모바일)와 인쇄 PDF 를 직접 열어 본다. 보고 형식: `육안 검수: N장 — <경로> — <본 것>`.

### 9. 정리
- 사용자에게 원본 폴더(세션 파일·raw·preview·evidence) 삭제를 안내한다. 배포는 작업 폴더의 리포트(와 필요하면 img/)만.

## 합리화 차단표

| 이런 생각 | 대신 |
|---|---|
| "이 버튼은 조회 같으니 눌러도 되겠지" | 쓰기 차단이 막은 요청 목록을 사용자에게 보여 주고 허용 여부를 받는다 |
| "비밀번호를 채팅으로 받으면 빠르다" | 격리 브라우저 창에 사용자가 직접 입력한다 |
| "잠금 시험 한 번쯤은 승인 없이" | 항목별 승인 없이는 점검불가로 남긴다 |
| "다른 회원 번호로 바꿔 보면 IDOR 를 확인할 수 있다" | 하지 않는다. 서면·담당자 확인 항목으로 남긴다 |
| "자동 후보가 있으니 그대로 취약" | 수집물을 직접 열어 기준과 대조한 뒤 판정한다 |
| "법정 수치가 없으니 취약으로 해 두자" | 권고로 두고 근거에 "외부 참고"를 적는다 |
| "가림 N곳 처리됐으니 끝" | 배포 이미지를 원본 크기로 전부 연다 |
| "CAPTCHA·WAF 가 떴지만 잠깐 기다렸다 다시" | 멈추고 사용자에게 알린다 |
| "검증기가 초록이니 완료" | 증거 png 를 직접 본 기록을 남긴 뒤 완료라고 말한다 |

## 완료 조건

- [ ] intake.json(승인 정보 포함), 확정 menu_map.json, findings.json, img/, REPORT_*.html 이 작업 폴더에 있다
- [ ] 쓰기 차단 아래에서만 수집했고, 능동 시험은 승인 기록이 findings 에 있다
- [ ] 모든 취약·권고에 근거·조치방안·가린 캡처·번호 박스가 있고, 서면 항목은 요청 자료와 함께 점검불가로 남았다
- [ ] 빌드 경고 0, `verify_report.py` 종료코드 0, 증거 png·인쇄 PDF 육안 검수 기록
- [ ] 원본·세션 파일이 작업 폴더 밖에 있고 삭제를 안내했다, 아이디·비밀번호가 어디에도 남지 않았다

## 함께 보는 파일

| 파일 | 내용 |
|---|---|
| [references/judgment.md](references/judgment.md) | 판정 5종, 위험도 상·중·하, 권고 기준값 |
| [references/safety.md](references/safety.md) | 쓰기 차단·금지 동작·중단 조건 |
| [references/active-tests.md](references/active-tests.md) | 능동 시험 항목별 절차와 승인 문구 |
| `references/criteria/*.yaml` | 점검 기준 273개 항목 |
| `templates/intake.example.json` | 착수 기록 형식 |
| `templates/findings.example.json` | 판정 결과 형식 |
| `templates/boxes.example.json` | 번호 박스를 달 요소 지정 형식 |
| `demo/` | 연습용 데모 사이트와 정답표 |
