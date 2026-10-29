"""화면 수집기(설계서 2장 3단계, 3-2, 7-2~7-4).

확정한 메뉴(status=visit)마다 새 점검 컨텍스트를 열고 쓰기 차단을 건 뒤 GET 으로 화면을 열어
원본 폴더(작업 폴더 밖) <raw>/<메뉴ID>/ 에 다음을 남긴다.
  screenshot.png  전체 화면 캡처
  dom.html        렌더링된 DOM
  network.har     네트워크 기록(본문 포함, 비밀번호 계열 값은 *** 로 바꿈)
  cookies.json    쿠키와 속성(HttpOnly·Secure·SameSite)
  storage.json    localStorage·sessionStorage
  headers.json    문서 응답 상태·헤더·최종 주소
  meta.json       메뉴 정보, 막힌 요청, 수집 시각
auth=none 메뉴는 로그인 없이, auth=user 메뉴는 저장한 세션으로 연다.
429, WAF 차단 화면, CAPTCHA, 403 연속, 세션 만료를 만나면 멈추고(CollectStopped) 다음 실행 때 그 메뉴부터 이어 간다.

사용: python -m scripts.collect --intake intake.json --menu-map menu_map.json --storage-state <밖>/session.json \
        --raw-dir <밖>/raw --workdir <작업 폴더> [--only M03-02] [--allow-post '/api/.*/search$']
"""
from __future__ import annotations

import argparse
import json
import re
import time
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlparse

from scripts.guard import install_guard
from scripts.session import new_context

ARTIFACTS = ("screenshot.png", "dom.html", "network.har", "cookies.json", "storage.json", "headers.json", "meta.json")
# 비밀번호 계열 이름: password·passwd·pwd 를 포함하거나(j_password, userPassword, password_confirm, pwd2),
# pw 가 단어로 쓰인 것(pw, user_pw, pw2), pass 단독. passport 같은 이름은 잡지 않는다.
SECRET_NAME = re.compile(r"(password|passwd|pwd|비밀번호)|(^|[_\-.])pw\d*($|[_\-.])|^pass$", re.I)
SECRET_HEADERS = {"cookie", "set-cookie", "authorization", "proxy-authorization"}
_PAIR = re.compile(r"((?:^|[?&\s\"'])([\w.\-]+)=)([^&#\s\"'\\]*)")
_JSON = re.compile(r'("([\w.\-]+)"\s*:\s*")((?:[^"\\]|\\.)*)(")')
_MULTIPART = re.compile(r'(name="([^"]+)"\r?\n(?:[^\r\n]+\r?\n)*\r?\n)([^\r\n]*)')
WAF_RE = re.compile(r"blocked by waf|web application firewall|request blocked|access denied by security policy", re.I)
CAPTCHA_RE = re.compile(r"captcha|recaptcha|hcaptcha|자동입력 ?방지", re.I)
READ_STORAGE = """() => { const d = s => Object.fromEntries(Object.keys(s).map(k => [k, s.getItem(k)]));
                         return {localStorage: d(localStorage), sessionStorage: d(sessionStorage)}; }"""


class CollectStopped(RuntimeError):
    def __init__(self, reason: str, menu_id: str, detail: str = ""):
        super().__init__(f"수집 중단({reason}) at {menu_id}: {detail}")
        self.reason, self.menu_id, self.detail = reason, menu_id, detail


def _scrub_text(text: str) -> str:
    def mask(m):
        return m.group(1) + "***" + (m.group(4) if m.re is _JSON else "") if SECRET_NAME.search(m.group(2)) else m.group(0)
    for pattern in (_PAIR, _JSON, _MULTIPART):
        text = pattern.sub(mask, text)
    return text


def scrub_har(node, _in_cookies: bool = False):
    """HAR(dict)의 비밀번호 계열 값, 쿠키 값, 인증 헤더 값을 *** 로 바꾼다.
    name/value 쌍, URL·urlencoded·JSON·multipart 본문 문자열을 모두 본다."""
    if isinstance(node, dict):
        out = {k: scrub_har(v, k == "cookies") for k, v in node.items()}
        name = out.get("name")
        if isinstance(name, str) and "value" in out and (
                _in_cookies or SECRET_NAME.search(name) or name.lower() in SECRET_HEADERS):
            out["value"] = "***"
        return out
    if isinstance(node, list):
        return [scrub_har(v, _in_cookies) for v in node]
    if isinstance(node, str):
        return _scrub_text(node)
    return node


def _dump(path: Path, data) -> None:
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


def _load_state(raw: Path) -> dict:
    p = raw / "state.json"
    return json.loads(p.read_text(encoding="utf-8")) if p.exists() else {"completed": [], "stopped": None}


def _check(menu: dict, resp, page, forbidden_streak: int) -> tuple[str | None, int]:
    status = resp.status if resp else 0
    html = page.content()
    if status == 429:
        return "rate-limited", forbidden_streak
    if WAF_RE.search(html):
        return "waf", forbidden_streak
    if CAPTCHA_RE.search(html):
        return "captcha", forbidden_streak
    if menu.get("auth") == "user" and "login" in urlparse(page.url).path.lower() \
            and "login" not in urlparse(menu["url"]).path.lower():
        return "session-expired", forbidden_streak
    forbidden_streak = forbidden_streak + 1 if status == 403 else 0
    if forbidden_streak >= 2:
        return "forbidden-repeated", forbidden_streak
    return None, forbidden_streak


def collect(browser, storage_state: Path, intake: dict, menu_map: dict, raw_dir: Path, workdir: Path, *,
            only: list[str] | None = None, allow_post=(), delay_s: float = 1.5) -> dict:
    raw_dir, workdir = Path(raw_dir), Path(workdir)
    if raw_dir.resolve().is_relative_to(workdir.resolve()):
        raise ValueError(f"원본 폴더는 작업 폴더 밖에 둔다: {raw_dir}")
    raw_dir.mkdir(parents=True, exist_ok=True)
    state = _load_state(raw_dir)
    targets = [m for m in menu_map["menus"] if m["status"] == "visit"]
    if only:
        targets = [m for m in targets if m["id"] in only]
    else:
        targets = [m for m in targets if m["id"] not in state["completed"]]

    streak = 0
    for i, menu in enumerate(targets):
        if i and delay_s:
            time.sleep(delay_s)
        out = raw_dir / menu["id"]
        har_tmp = raw_dir / f".{menu['id']}.har"
        ctx = new_context(browser, Path(storage_state) if menu.get("auth") == "user" else None,
                          record_har_path=str(har_tmp), record_har_content="embed")
        guard = install_guard(ctx, intake, menu_map, allow_post)
        page = ctx.new_page()
        try:
            resp = page.goto(menu["url"])
        except Exception as e:  # noqa: BLE001 - 차단·연결 실패는 접근 불가로 기록
            resp, error = None, str(e)
        else:
            error = None
            try:
                page.wait_for_load_state("networkidle", timeout=10_000)
            except Exception:  # noqa: BLE001 - 계속 요청하는 화면도 있다
                pass
            page.wait_for_timeout(300)
        reason, streak = _check(menu, resp, page, streak) if resp else (None, streak)
        if reason:
            ctx.close()
            har_tmp.unlink(missing_ok=True)
            state["stopped"] = {"reason": reason, "menu_id": menu["id"], "url": page.url}
            _dump(raw_dir / "state.json", state)
            raise CollectStopped(reason, menu["id"], page.url)

        out.mkdir(parents=True, exist_ok=True)
        page.screenshot(path=str(out / "screenshot.png"), full_page=True)
        (out / "dom.html").write_text(page.content(), encoding="utf-8")
        _dump(out / "cookies.json", [dict(c, value="***") for c in ctx.cookies()])  # 속성만 쓴다
        _dump(out / "storage.json", page.evaluate(READ_STORAGE))
        _dump(out / "headers.json", {
            "url": menu["url"], "final_url": page.url,
            "status": resp.status if resp else None,
            "headers": resp.all_headers() if resp else {},
        })
        _dump(out / "meta.json", {
            "menu": menu, "title": page.title(), "error": error,
            "accessible": bool(resp and resp.ok),
            "blocked": guard.blocked_log, "collected_at": datetime.now(timezone.utc).isoformat(),
        })
        ctx.close()  # 여기서 HAR 이 써진다
        har = json.loads(har_tmp.read_text(encoding="utf-8"))
        _dump(out / "network.har", scrub_har(har))
        har_tmp.unlink()

        if menu["id"] not in state["completed"]:
            state["completed"].append(menu["id"])
        state["stopped"] = None
        _dump(raw_dir / "state.json", state)
    return state


def main() -> None:
    from playwright.sync_api import sync_playwright

    from scripts.models import load_intake, load_menu_map

    ap = argparse.ArgumentParser(description="확정 메뉴를 쓰기 차단 아래에서 수집한다")
    ap.add_argument("--intake", required=True, type=Path)
    ap.add_argument("--menu-map", required=True, type=Path)
    ap.add_argument("--storage-state", required=True, type=Path)
    ap.add_argument("--raw-dir", required=True, type=Path)
    ap.add_argument("--workdir", required=True, type=Path)
    ap.add_argument("--only", nargs="*")
    ap.add_argument("--allow-post", nargs="*", default=[], help="사용자가 확인한 조회성 POST 경로 정규식")
    ap.add_argument("--headed", action="store_true")
    a = ap.parse_args()
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=not a.headed)
        try:
            state = collect(browser, a.storage_state, load_intake(a.intake), load_menu_map(a.menu_map),
                            a.raw_dir, a.workdir, only=a.only, allow_post=a.allow_post)
            print(f"수집 완료: {len(state['completed'])}개 메뉴 → {a.raw_dir}")
        except CollectStopped as e:
            print(f"수집 중단: {e.reason} ({e.menu_id}, {e.detail}). 원인을 해결한 뒤 같은 명령으로 이어서 실행하세요.")
            raise SystemExit(2)
        finally:
            browser.close()


if __name__ == "__main__":
    main()
