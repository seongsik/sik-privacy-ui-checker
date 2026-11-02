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
from urllib.parse import parse_qsl, unquote, urlparse

SAFE_METHODS = ("GET", "HEAD", "OPTIONS")

# 주소만 열어도 상태가 바뀌기 쉬운 GET 을 낱말로 판단한다. 경로 조각·쿼리를 낱말로 쪼갠다
# (camelCase, _, -, . 기준: actionLogout.do → action·logout, delete_ok → delete·ok, memberDelete → member·delete).
#  - 파괴 낱말은 어디에 있든 막는다.
#  - 변경 낱말은 같은 경로 조각에 화면 낱말(form·view·list …)이 없을 때만 막는다(updateForm.do 는 화면).
#    /my/withdraw, /board/write 같은 안내·입력 화면 이름은 막지 않고, withdrawProc·writeProc 은 proc 으로 막는다.
#  - 쿼리는 동작 키(mode·act·action·cmd·method)의 값에 파괴·변경 낱말이 있으면 막는다.
DESTRUCTIVE = {"logout", "signout", "logoff", "delete", "del", "remove", "destroy", "cancel", "approve",
               "clear", "reset", "send", "drop", "unsubscribe", "purge"}
MUTATING = {"update", "insert", "save", "modify", "submit", "exec", "proc", "process", "ok"}
VIEW = {"view", "form", "page", "list", "detail", "info", "guide", "notice", "main", "index", "search"}
ACTION_KEYS = {"mode", "act", "action", "cmd", "method", "command"}
_EXTS = {"do", "jsp", "php", "asp", "aspx", "action", "json", "html", "htm"}


def _words(text: str) -> set[str]:
    out = set()
    for part in re.split(r"[^0-9A-Za-z]+", text):
        out |= {w.lower() for w in re.findall(r"[A-Z]?[a-z]+|[A-Z]+(?![a-z])|\d+", part)}
    return out


def _segments(url: str) -> tuple[list[str], list[tuple[str, str]]]:
    parsed = urlparse(url)
    path = unquote(parsed.path).split(";")[0]
    segs = [re.sub(r"\.(" + "|".join(_EXTS) + r")$", "", s, flags=re.I) for s in path.split("/") if s]
    return segs, parse_qsl(unquote(parsed.query), keep_blank_values=True)


# 낱말로 쪼개면 흩어지는 합성어(LogOut → log·out, sign-out → sign·out)는 구분자를 뺀 소문자에서 찾는다
COMPOUND = ("logout", "signout", "logoff", "signoff")


def _compact(text: str) -> str:
    return re.sub(r"[^0-9a-z]+", "", text.lower())


def is_destructive(url: str) -> bool:
    """파괴 낱말·합성어만 본다(능동 시험 대상 검사용: 승인 대상은 변경 요청이지만 파괴 요청이면 안 된다)."""
    segs, query = _segments(url)
    for part in segs + [v for _, v in query]:
        if _words(part) & DESTRUCTIVE or any(c in _compact(part) for c in COMPOUND):
            return True
    return any(k.lower() in ACTION_KEYS and "withdraw" in _words(v) for k, v in query)


def is_state_changing(url: str) -> bool:
    if is_destructive(url):
        return True
    segs, query = _segments(url)
    for seg in segs:
        words = _words(seg)
        if words & MUTATING and not words & VIEW:
            return True
    return any(key.lower() in ACTION_KEYS and _words(value) & MUTATING for key, value in query)


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
        # 사용자가 정한 금지 경로: 착수 때 intake 에 적은 것(메뉴맵 수집 중에도 적용) + 확정 메뉴맵의 것
        self.deny = [re.compile(re.escape(_normalize(p)))
                     for p in list(intake.get("deny_patterns", [])) + list(menu_map.get("deny_patterns", []))]
        # 조회성 POST: 경로 전체 일치(fullmatch). 저장한 목록(menu_map.allow_post)과 실행 인자를 합친다
        self.allow_post = [re.compile(p) for p in list(menu_map.get("allow_post", [])) + list(allow_post)]
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
        denied = is_state_changing(url) or any(p.search(_normalize(url)) for p in self.deny)
        if method in SAFE_METHODS:
            # 도메인 밖 GET(SSO 로그아웃 등)에도 금지 규칙을 적용한다
            return "deny-pattern" if denied else None
        if (method == "POST" and self._in_scope(url) and not denied
                and any(p.fullmatch(urlparse(url).path) for p in self.allow_post)):
            return None
        return "write-method"

    def is_allowed_post(self, method: str, url: str) -> bool:
        return method == "POST" and any(p.fullmatch(urlparse(url).path) for p in self.allow_post)

    def _log(self, which: list, method: str, url: str, reason: str | None = None):
        from scripts.redact import scrub_text  # 기록(meta.json)에 비밀번호·토큰이 남지 않게
        which.append({"method": method, "url": scrub_text(url), "reason": scrub_text(reason) if reason else None,
                      "ts": time.time()})

    def is_user_denied(self, url: str) -> bool:
        return any(p.search(_normalize(url)) for p in self.deny)

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
        elif self.is_allowed_post(req.method, req.url):
            # 허용한 조회성 POST 도 무엇이 나갔는지 남긴다(본문은 비밀번호·토큰을 가린 앞부분만)
            from scripts.redact import scrub_text
            self.allowed_log.append({"method": req.method, "url": scrub_text(req.url), "reason": "allow-post",
                                     "ts": time.time(),
                                     "body": scrub_text(req.post_data or "")[:300]})
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
