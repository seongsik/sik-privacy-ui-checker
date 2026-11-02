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
        --raw-dir <밖>/raw --workdir <작업 폴더> [--only M03-02] [--allow-post '/api/.*/search$'] [--boxes boxes.json]
"""
from __future__ import annotations

import argparse
import json
import re
import time
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlparse

from scripts.guard import install_guard, settle
from scripts.redact import scrub_har  # noqa: F401 - collect.scrub_har 로도 쓴다
from scripts.mask import DOC_SIZE, FREEZE_CSS, find_nondom, measure_boxes, measure_pii
from scripts.session import add_browser_option, launch_browser, new_context, private_dir

ARTIFACTS = ("screenshot.png", "dom.html", "network.har", "cookies.json", "storage.json", "headers.json", "meta.json")
WAF_RE = re.compile(r"blocked by waf|web application firewall|request blocked|access denied by security policy", re.I)
CAPTCHA_RE = re.compile(r"captcha|recaptcha|hcaptcha|자동입력 ?방지", re.I)
READ_STORAGE = """() => { const d = s => Object.fromEntries(Object.keys(s).map(k => [k, s.getItem(k)]));
                         return {localStorage: d(localStorage), sessionStorage: d(sessionStorage)}; }"""


class CollectStopped(RuntimeError):
    def __init__(self, reason: str, menu_id: str, detail: str = ""):
        super().__init__(f"수집 중단({reason}) at {menu_id}: {detail}")
        self.reason, self.menu_id, self.detail = reason, menu_id, detail


def _dump(path: Path, data) -> None:
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


def _load_state(raw: Path) -> dict:
    p = raw / "state.json"
    state = json.loads(p.read_text(encoding="utf-8")) if p.exists() else {}
    state.setdefault("completed", [])
    state.setdefault("failed", {})
    state.setdefault("stopped", None)
    return state


LOGIN_PATH = re.compile(r"login|signin|sign-in|sso|auth|logon", re.I)
TITLE_RE = re.compile(r"<title[^>]*>(.*?)</title>|<h1[^>]*>(.*?)</h1>", re.I | re.S)
CHALLENGE_TITLE = re.compile(r"captcha|자동입력 ?방지|보안 ?확인|security check|are you a robot", re.I)


def _check(menu: dict, resp, page, forbidden_streak: int) -> tuple[str | None, int]:
    status = resp.status if resp else 0
    html = page.content()
    if status == 429:
        return "rate-limited", forbidden_streak
    if status >= 400 and WAF_RE.search(html):
        return "waf", forbidden_streak
    # 화면 안의 CAPTCHA 위젯(회원가입 등)은 멈추지 않는다. 보안 확인 화면 자체일 때만 멈춘다
    heads = " ".join(" ".join(m) for m in TITLE_RE.findall(html))
    if (status >= 400 and CAPTCHA_RE.search(html)) or CHALLENGE_TITLE.search(heads):
        return "captcha", forbidden_streak
    if menu.get("auth") == "user" and LOGIN_PATH.search(urlparse(page.url).path)             and not LOGIN_PATH.search(urlparse(menu["url"]).path):
        return "session-expired", forbidden_streak
    forbidden_streak = forbidden_streak + 1 if status == 403 else 0
    if forbidden_streak >= 2:
        return "forbidden-repeated", forbidden_streak
    return None, forbidden_streak


def _capture_with_rects(page, shot_path: Path, boxes: list[dict], tries: int = 3):
    """애니메이션을 멈추고 좌표 측정 → 캡처 → 재측정. 두 측정과 문서 크기가 같아야 안정으로 본다."""
    page.add_style_tag(content=FREEZE_CSS)
    for _ in range(tries):
        size, before = page.evaluate(DOC_SIZE), measure_pii(page)
        box_before = measure_boxes(page, boxes)
        page.screenshot(path=str(shot_path), full_page=True, animations="disabled")
        after, box_after = measure_pii(page), measure_boxes(page, boxes)
        if before == after and box_before == box_after and size == page.evaluate(DOC_SIZE):
            return before, box_before, True
        page.wait_for_timeout(300)
    return after, box_after, False


def _collect_one(browser, storage_state, intake, menu_map, raw_dir: Path, menu: dict, allow_post, boxes,
                 streak: int, state: dict):
    """메뉴 하나를 수집한다. 컨텍스트는 항상 닫고, 임시 HAR 은 항상 가리거나 지운다(가리지 않은 HAR 을 남기지 않는다)."""
    out = raw_dir / menu["id"]
    har_tmp = raw_dir / f".{menu['id']}.har"
    pw_names: list[str] = []
    keep_har = False
    ctx = new_context(browser, Path(storage_state) if menu.get("auth") == "user" and storage_state else None,
                      record_har_path=str(har_tmp), record_har_content="embed")
    try:
        guard = install_guard(ctx, intake, menu_map, allow_post)
        page = ctx.new_page()
        try:
            resp, error = page.goto(menu["url"]), None
        except Exception as e:  # noqa: BLE001 - 차단·연결 실패
            resp, error = None, str(e)
        else:
            settle(page)  # guard 의 '이어서 이동' 화면을 지나 실제 화면까지
            try:
                page.wait_for_load_state("networkidle", timeout=10_000)
            except Exception:  # noqa: BLE001 - 계속 요청하는 화면도 있다
                pass
            page.wait_for_timeout(300)
        left = [b["url"] for b in guard.blocked_log if "out-of-scope" in (b["reason"] or "")]
        if error or left:
            state["failed"][menu["id"]] = (error or f"범위 밖으로 이동: {left[0]}")[:300]
            # 회원 화면이 다른 도메인(SSO 등)으로 보내지면 세션이 끊긴 것으로 본다
            if menu.get("auth") == "user" and left:
                return "left-scope", left[0], streak
            return None, None, streak
        reason, streak = _check(menu, resp, page, streak)
        if reason:
            return reason, page.url, streak

        private_dir(out)
        pw_names = page.eval_on_selector_all("input[type=password]", "els => els.map(e => e.name).filter(Boolean)")
        pii_rects, box_rects, stable = _capture_with_rects(page, out / "screenshot.png", boxes)
        html = page.content()
        (out / "dom.html").write_text(html, encoding="utf-8")
        _dump(out / "cookies.json", [dict(c, value="***") for c in ctx.cookies()])  # 속성만 쓴다
        _dump(out / "storage.json", page.evaluate(READ_STORAGE))
        _dump(out / "headers.json", {"url": menu["url"], "final_url": page.url, "status": resp.status if resp else None,
                                      "headers": resp.all_headers() if resp else {}})
        _dump(out / "meta.json", {
            "menu": menu, "title": page.title(), "error": None, "accessible": bool(resp and resp.ok),
            "captcha_widget": bool(CAPTCHA_RE.search(html)),
            "blocked": guard.blocked_log, "allowed": guard.allowed_log,
            "collected_at": datetime.now(timezone.utc).isoformat(),
            # 가림·번호 박스 좌표(문서 기준 px). rects_stable=False 면 mask 가 이미지를 만들지 않는다
            "pii_rects": pii_rects, "box_rects": box_rects, "rects_stable": stable, "nondom": find_nondom(page),
        })
        keep_har = True
        if menu["id"] not in state["completed"]:
            state["completed"].append(menu["id"])
        state["failed"].pop(menu["id"], None)
        return None, None, streak
    finally:
        ctx.close()  # 여기서 HAR 이 써진다
        if har_tmp.exists():
            if keep_har:
                har = json.loads(har_tmp.read_text(encoding="utf-8"))
                _dump(out / "network.har", scrub_har(har, extra_names=pw_names))  # 화면의 비밀번호 칸 이름도 가린다
            har_tmp.unlink()


def collect(browser, storage_state: Path, intake: dict, menu_map: dict, raw_dir: Path, workdir: Path, *,
            only: list[str] | None = None, allow_post=(), delay_s: float = 1.5,
            boxes: dict[str, list[dict]] | None = None) -> dict:
    """boxes: {메뉴ID: [{n, selector, label}]} — 번호 박스를 달 요소. 그 메뉴를 다시 수집할 때 좌표를 잰다."""
    raw_dir, workdir = Path(raw_dir), Path(workdir)
    if raw_dir.resolve().is_relative_to(workdir.resolve()):
        raise ValueError(f"원본 폴더는 작업 폴더 밖에 둔다: {raw_dir}")
    private_dir(raw_dir)
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
        reason, detail, streak = _collect_one(browser, storage_state, intake, menu_map, raw_dir, menu, allow_post,
                                              (boxes or {}).get(menu["id"], []), streak, state)
        if reason:
            state["stopped"] = {"reason": reason, "menu_id": menu["id"], "url": detail}
            _dump(raw_dir / "state.json", state)
            raise CollectStopped(reason, menu["id"], detail)
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
    ap.add_argument("--boxes", type=Path, help="번호 박스를 달 요소 JSON {메뉴ID: [{n, selector, label}]}")
    ap.add_argument("--headed", action="store_true")
    add_browser_option(ap)
    a = ap.parse_args()
    with sync_playwright() as p:
        browser = launch_browser(p, a.browser, headless=not a.headed)
        try:
            state = collect(browser, a.storage_state, load_intake(a.intake), load_menu_map(a.menu_map),
                            a.raw_dir, a.workdir, only=a.only, allow_post=a.allow_post,
                            boxes=json.loads(a.boxes.read_text(encoding="utf-8")) if a.boxes else None)
            print(f"수집 완료: {len(state['completed'])}개 메뉴 → {a.raw_dir}")
        except CollectStopped as e:
            print(f"수집 중단: {e.reason} ({e.menu_id}, {e.detail}). 원인을 해결한 뒤 같은 명령으로 이어서 실행하세요.")
            raise SystemExit(2)
        finally:
            browser.close()


if __name__ == "__main__":
    main()
