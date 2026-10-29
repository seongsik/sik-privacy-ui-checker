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
from urllib.parse import unquote, urlparse

SAFE_METHODS = ("GET", "HEAD", "OPTIONS")

# 주소만 열어도 상태가 바뀌기 쉬운 GET. 화면 이름(/my/withdraw 같은 안내 화면)은 막지 않도록 동작 경로·파라미터만 본다.
# 정규화(_normalize)한 소문자 경로에 적용한다. 서버 확장자(.do, .jsp …)가 붙은 동작 경로도 잡는다.
_EXT = r"(\.(do|jsp|php|asp|aspx|action|json|html?))?"
DEFAULT_DENY = (
    r"(^|/)(logout|log-out|signout|sign-out)" + _EXT + r"(/|$|\?)",
    r"/(delete|remove|destroy|del)" + _EXT + r"(/|$|\?)",
    r"[?&](action|mode|cmd|act)=(delete|remove|del|withdraw|cancel|approve|send|logout)\b",
)


def _normalize(url: str) -> str:
    """deny 비교용: 퍼센트 디코드, 경로 파라미터(;jsessionid=…) 제거, 소문자."""
    parsed = urlparse(url)
    path = unquote(parsed.path).split(";")[0]
    query = unquote(parsed.query)
    return (path + ("?" + query if query else "")).lower()


class Guard:
    def __init__(self, intake: dict, menu_map: dict, allow_post=()):
        target = urlparse(intake["target_url"]).hostname
        self.allowed_domains = set(intake.get("allowed_domains") or [target])
        self.deny = [re.compile(p) for p in DEFAULT_DENY] + [
            re.compile(re.escape(_normalize(p))) for p in menu_map.get("deny_patterns", [])
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
            # 도메인 밖 GET(SSO 로그아웃 등)에도 금지 목록을 적용한다
            if any(p.search(_normalize(url)) for p in self.deny):
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
        is_nav = req.is_navigation_request()  # iframe 이동도 범위 검사(외부 iframe 안 쓰기 요청 차단)
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
    """context 는 session.new_context/open_context 로 만든 것이어야 한다(Service Worker 차단).
    Service Worker 가 보내는 요청은 context.route 로 가로챌 수 없어 쓰기 차단을 우회하기 때문이다."""
    from scripts.session import SW_BLOCKED_MARK

    if not getattr(context, SW_BLOCKED_MARK, False):
        raise ValueError("service_workers='block' 인 컨텍스트가 필요하다: session.new_context 로 만든다")
    guard = Guard(intake, menu_map, allow_post)
    context.route("**/*", guard._on_route)
    context.route_web_socket(re.compile(".*"), guard._on_websocket)
    return guard
