"""메뉴맵 초안 수집(설계서 2장 2단계, 4-2).

로그인된 점검 컨텍스트로 대상 사이트를 GET 으로만 돌며 링크를 읽는다. 버튼·폼은 누르지 않는다.
- 첫 화면의 링크가 최상위 메뉴, 다른 화면에서 처음 나온 링크는 그 화면의 하위 메뉴가 된다(너비 우선).
- 경로 숫자만 다른 링크(게시글·주문 상세)는 묶어서 첫 번째만 visit, 나머지는 sample 로 둔다.
- 쓰기 차단의 금지 패턴에 걸리는 링크는 blocked(사유 포함)로 두고 deny_patterns 에 넣는다. 방문하지 않는다.
- 파일 링크(첨부)는 메뉴가 아니라 attachments 로, robots.txt 의 Disallow 경로는 probes 로 돌려준다.
결과는 사용자가 확인·수정한 뒤 menu_map.json 으로 확정한다.

사용: python -m scripts.menumap --intake intake.json --storage-state <밖>/session.json --out menu_map.json
"""
from __future__ import annotations

import argparse
import json
import re
import time
from collections import OrderedDict
from pathlib import Path
from urllib.parse import urldefrag, urljoin, urlparse

from scripts.guard import Guard, install_guard

FILE_EXT = re.compile(r"\.(txt|pdf|zip|sql|hwp|hwpx|docx?|xlsx?|pptx?|csv|jpe?g|png|gif|bak|tar|gz|7z)$", re.I)
READ_LINKS = """() => [...document.querySelectorAll('a[href]')].map(a => ({
    href: a.href, text: (a.innerText || a.title || '').trim().replace(/\\s+/g, ' ') }))"""


def _template(url: str) -> str:
    p = urlparse(url)
    return re.sub(r"\d+", "{n}", p.path) + ("?" + re.sub(r"=\d+", "={n}", p.query) if p.query else "")


def _rel(url: str) -> str:
    p = urlparse(url)
    return p.path + ("?" + p.query if p.query else "")


def _is_member_only(anon, url: str) -> bool:
    resp = anon.get(url, max_redirects=0)
    location = resp.headers.get("location", "")
    return resp.status in (301, 302, 303, 307, 401, 403) and (resp.status in (401, 403) or "login" in location.lower())


def build_menu_map(context, intake: dict, *, delay_s: float = 1.0, max_pages: int = 300) -> dict:
    base = intake["target_url"].rstrip("/")
    checker = Guard(intake, {})
    guard = install_guard(context, intake, {})
    page = context.new_page()

    found: "OrderedDict[str, dict]" = OrderedDict()  # url → {path, text}
    blocked: dict[str, dict] = {}
    attachments: list[str] = []
    queue = [(base + "/", [])]
    visited: set[str] = set()

    while queue and len(visited) < max_pages:
        url, parent = queue.pop(0)
        if url in visited:
            continue
        visited.add(url)
        try:
            page.goto(url)
        except Exception:  # noqa: BLE001 - 열리지 않는 화면은 메뉴맵 확인 단계에서 사람이 본다
            continue
        for link in page.evaluate(READ_LINKS):
            href = urldefrag(urljoin(page.url, link["href"]))[0]
            if not href.startswith(("http://", "https://")) or not checker._in_scope(href):
                continue
            if FILE_EXT.search(urlparse(href).path):
                if href not in attachments:
                    attachments.append(href)
                continue
            if href in found or href in blocked:
                continue
            text = link["text"] or _rel(href)
            path = parent + [text]  # 첫 화면(parent=[])의 링크는 최상위 메뉴
            if checker.decide("GET", href, True) == "deny-pattern":
                blocked[href] = {"path": path, "text": text}
                continue
            found[href] = {"path": path}
            queue.append((href, path))
        if delay_s:
            time.sleep(delay_s)
    page.close()

    # 첫 화면 자신(홈)은 nav 의 '홈' 링크로 들어온다. 없으면 추가한다.
    found.setdefault(base + "/", {"path": ["홈"]})

    menus, groups, top_index = [], {}, OrderedDict()
    anon_ctx = context.browser.new_context()
    anon = anon_ctx.request
    for url, info in found.items():
        top = info["path"][0]
        top_index.setdefault(top, len(top_index) + 1)
        key = (len(info["path"]), _template(url))
        menu = {"id": None, "path": info["path"], "url": url, "status": "visit",
                "auth": "user" if _is_member_only(anon, url) else "none"}
        if key in groups and _template(url) != _rel(url):
            menu["status"], menu["sample_of"] = "sample", groups[key]
        else:
            groups.setdefault(key, url)
        menus.append(menu)
    for url, info in blocked.items():
        top_index.setdefault(info["path"][0], len(top_index) + 1)
        menus.append({"id": None, "path": info["path"], "url": url, "status": "blocked",
                      "reason": f"상태 변경 링크({info['text']})", "auth": "user"})
    anon_ctx.close()

    counters: dict[int, int] = {}
    url_to_id = {}
    for m in menus:
        t = top_index[m["path"][0]]
        counters[t] = counters.get(t, 0) + 1
        m["id"] = f"M{t:02d}-{counters[t]:02d}"
        url_to_id[m["url"]] = m["id"]
    for m in menus:
        if m["status"] == "sample":
            m["sample_of"] = url_to_id[m["sample_of"]]

    deny = [urlparse(u).path for u in blocked]
    return {
        "menus": menus,
        "deny_patterns": list(dict.fromkeys(deny)),
        "attachments": attachments,
        "probes": _robots_probes(context, base),
        "blocked_requests": guard.blocked_log,
    }


def _robots_probes(context, base: str) -> list[str]:
    resp = context.request.get(base + "/robots.txt")
    if not resp.ok:
        return []
    return [line.split(":", 1)[1].strip() for line in resp.text().splitlines()
            if line.lower().startswith("disallow:") and line.split(":", 1)[1].strip()]


def main() -> None:
    from playwright.sync_api import sync_playwright

    from scripts.models import load_intake
    from scripts.session import open_context

    ap = argparse.ArgumentParser(description="메뉴맵 초안을 만든다(읽기 전용)")
    ap.add_argument("--intake", required=True, type=Path)
    ap.add_argument("--storage-state", required=True, type=Path)
    ap.add_argument("--out", required=True, type=Path)
    ap.add_argument("--headed", action="store_true")
    a = ap.parse_args()
    intake = load_intake(a.intake)
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=not a.headed)
        data = build_menu_map(open_context(browser, a.storage_state), intake)
        browser.close()
    a.out.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"메뉴 {len(data['menus'])}개, 금지 {len(data['deny_patterns'])}개, 첨부 {len(data['attachments'])}개 → {a.out}")


if __name__ == "__main__":
    main()
