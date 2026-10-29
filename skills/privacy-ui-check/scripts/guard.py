"""쓰기 차단(write_guard): 운영 데이터를 바꿀 수 있는 요청을 브라우저 네트워크 단계에서 막는다.

설계서 7-1의 장치 1·2·3·5·6:
  1. GET·HEAD·OPTIONS 외 요청 차단(sendBeacon 포함), WebSocket 은 서버에 연결하지 않고 메시지만 기록
  2. 사용자가 확인한 조회성 POST 정규식만 통과(allow_post)
  3. 상태를 바꾸는 GET 금지 목록(menu_map.deny_patterns + 기본 패턴)
  5. 대상 도메인 밖으로의 화면 이동 차단(외부 리소스 GET 은 허용)
  6. 능동 시험용 잠시 허용(temporarily_allow), 끝나면 자동 원복
로그인 단계에는 설치하지 않는다(설계서 7-2). 로그인 뒤에 install_guard 를 부른다.
"""
from __future__ import annotations

import re
import time
from contextlib import contextmanager
from urllib.parse import urlparse

SAFE_METHODS = ("GET", "HEAD", "OPTIONS")

# 주소만 열어도 상태가 바뀌기 쉬운 GET. 화면 이름(/my/withdraw 같은 안내 화면)은 막지 않도록 동작 경로·파라미터만 본다.
DEFAULT_DENY = (
    r"(^|/)(logout|log-out|signout|sign-out)(/|$|\?)",
    r"/(delete|remove|destroy|del)(/|$|\?)",
    r"[?&](action|mode|cmd|act)=(delete|remove|del|withdraw|cancel|approve|send|logout)\b",
)


class Guard:
    def __init__(self, intake: dict, menu_map: dict, allow_post=()):
        target = urlparse(intake["target_url"]).hostname
        self.allowed_domains = set(intake.get("allowed_domains") or [target])
        self.deny = [re.compile(p) for p in DEFAULT_DENY] + [
            re.compile(re.escape(p)) for p in menu_map.get("deny_patterns", [])
        ]
        self.allow_post = [re.compile(p) for p in allow_post]
        self.temporary: list[tuple[str, str]] = []
        self.blocked_log: list[dict] = []
        self.allowed_log: list[dict] = []

    # ── 판단 ──
    def _in_scope(self, url: str) -> bool:
        host = urlparse(url).hostname or ""
        return any(host == d or host.endswith("." + d) for d in self.allowed_domains)

    def _temporarily_allowed(self, method: str, url: str) -> bool:
        return any(method == m and url.split("?")[0] == u.split("?")[0] for m, u in self.temporary)

    def decide(self, method: str, url: str, is_navigation: bool) -> str | None:
        """막을 이유를 돌려준다. 통과면 None."""
        if self._temporarily_allowed(method, url):
            return None
        if is_navigation and not self._in_scope(url):
            return "out-of-scope"
        if method in SAFE_METHODS:
            if self._in_scope(url):
                parsed = urlparse(url)
                target = parsed.path + ("?" + parsed.query if parsed.query else "")
                if any(p.search(target) for p in self.deny):
                    return "deny-pattern"
            return None
        if method == "POST" and self._in_scope(url) and any(p.search(urlparse(url).path) for p in self.allow_post):
            return None
        return "write-method"

    def _log(self, which: list, method: str, url: str, reason: str | None = None):
        which.append({"method": method, "url": url, "reason": reason, "ts": time.time()})

    # ── Playwright 연결 ──
    def _on_route(self, route):
        req = route.request
        is_nav = req.is_navigation_request() and req.frame.parent_frame is None
        reason = self.decide(req.method, req.url, is_nav)
        if reason:
            self._log(self.blocked_log, req.method, req.url, reason)
            route.abort("blockedbyclient")
            return
        if self._temporarily_allowed(req.method, req.url):
            self._log(self.allowed_log, req.method, req.url, "temporary")
        route.continue_()

    def _on_websocket(self, ws):
        # connect_to_server 를 부르지 않으므로 서버와 연결되지 않는다. 페이지가 보낸 메시지만 기록한다.
        self._log(self.blocked_log, "WS", ws.url, "websocket")
        ws.on_message(lambda message: self._log(self.blocked_log, "WS", ws.url, f"websocket-message:{str(message)[:200]}"))

    @contextmanager
    def temporarily_allow(self, method: str, url: str):
        """승인받은 능동 시험 동안만 정확한 메서드·URL(쿼리 제외)을 허용한다."""
        entry = (method.upper(), url)
        self.temporary.append(entry)
        try:
            yield self
        finally:
            self.temporary.remove(entry)


def install_guard(context, intake: dict, menu_map: dict, allow_post=()) -> Guard:
    guard = Guard(intake, menu_map, allow_post)
    context.route("**/*", guard._on_route)
    context.route_web_socket(re.compile(".*"), guard._on_websocket)
    return guard
