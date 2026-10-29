"""데모몰: 개인정보 취약점과 쓰기 함정을 일부러 심은 연습용 웹사이트(설계서 8-1).

모든 개인정보는 가짜 값이다. 정답표는 같은 폴더의 expected.json, 메뉴 정답은 menu_expected.json.
서버가 받은 쓰기 요청(GET·HEAD·OPTIONS 외 메서드, 상태를 바꾸는 GET, WebSocket 메시지)은
GET /__writes 로 확인하고 POST /__reset 으로 비운다(테스트 전용, 화면에는 링크가 없다).

실행: python demo/server.py --port 8765
"""
from __future__ import annotations

import argparse
import base64
import hashlib
import json
import secrets
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlparse

DEMO_USER, DEMO_PASS = "hong", "demo-pass"  # 데모 전용 테스트 계정
ME = {
    "name": "홍길동",
    "email": "hong@example.com",
    "phone": "010-1234-5678",
    "ssn": "900101-1234567",
    "card": "4111-1111-1111-1111",
    "account": "110-123-456789",
    "address": "서울특별시 중구 세종대로 110",
}
ORDERS = [
    {"id": 1001, "item": "무선 키보드", "receiver": "홍길동", "phone": "010-1234-5678"},
    {"id": 1002, "item": "마우스 패드", "receiver": "홍길동", "phone": "010-1234-5678"},
    {"id": 1003, "item": "USB 허브", "receiver": "홍길동", "phone": "010-1234-5678"},
]
STATE_CHANGING_GET = ("/logout", "/my/address/delete")
READONLY_POST = ("/api/orders/search",)
WS_GUID = "258EAFA5-E914-47DA-95CA-C5AB0DC85B11"

WRITES: list[dict] = []
SESSIONS: set[str] = set()
ADMIN_FAILS = {"count": 0}
LOCK = threading.Lock()


def mask_phone(p: str) -> str:
    return p[:4] + "****" + p[8:]


def layout(title: str, body: str, member: bool = False, head: str = "") -> str:
    gnb = "".join(
        f'<li><a href="{u}">{t}</a></li>'
        for u, t in (("/", "홈"), ("/notice", "공지사항"), ("/board", "게시판"), ("/faq", "FAQ"), ("/search", "주문조회"), ("/my", "마이페이지"))
    )
    lnb = ""
    if member:
        lnb = '<nav class="lnb"><ul>' + "".join(
            f'<li><a href="{u}">{t}</a></li>'
            for u, t in (("/my", "마이페이지"), ("/my/profile", "회원정보 수정"), ("/my/orders", "주문내역"),
                         ("/my/address", "배송지 관리"), ("/my/settings", "마케팅 수신 설정"), ("/my/withdraw", "회원탈퇴"))
        ) + "</ul></nav>"
    footer = "".join(
        f'<a href="{u}">{t}</a> '
        for u, t in (("/privacy", "개인정보 보호정책"), ("/terms", "이용약관"), ("/login", "로그인"),
                     ("/join", "회원가입"), ("/find-pw", "비밀번호 찾기"))
    )
    return f"""<!doctype html><html lang="ko"><head><meta charset="utf-8"><title>{title} - 데모몰</title>{head}
<style>body{{font-family:sans-serif;margin:0}}header,footer{{background:#f3f3f3;padding:12px 24px}}
nav.gnb ul,nav.lnb ul{{display:flex;gap:16px;list-style:none;padding:0;margin:0}}main{{padding:24px}}
table{{border-collapse:collapse}}td,th{{border:1px solid #ccc;padding:6px 10px}}</style></head>
<body><header><strong>데모몰</strong><nav class="gnb"><ul>{gnb}</ul></nav>
<a href="/logout" class="logout">로그아웃</a></header>{lnb}<main><h1>{title}</h1>{body}</main>
<footer>{footer}</footer></body></html>"""


class Handler(BaseHTTPRequestHandler):
    server_version = "DemoMall/1.0"

    def log_message(self, *args):  # 테스트 출력을 깨끗하게
        pass

    # ── 응답 도우미 ──
    def send(self, body: str, code: int = 200, ctype: str = "text/html; charset=utf-8", headers: dict | None = None):
        data = body.encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(data)))
        for k, v in (headers or {}).items():
            self.send_header(k, v)
        self.end_headers()
        if self.command != "HEAD":
            self.wfile.write(data)

    def redirect(self, to: str, headers: dict | None = None):
        self.send_response(302)
        self.send_header("Location", to)
        self.send_header("Content-Length", "0")
        for k, v in (headers or {}).items():
            self.send_header(k, v)
        self.end_headers()

    def logged_in(self) -> bool:
        cookie = self.headers.get("Cookie", "")
        return any(c.strip().startswith("SID=") and c.strip()[4:] in SESSIONS for c in cookie.split(";"))

    def record(self, body: str | None = None, method: str | None = None):
        with LOCK:
            WRITES.append({"method": method or self.command, "path": urlparse(self.path).path, "body": body})

    def read_body(self) -> str:
        n = int(self.headers.get("Content-Length") or 0)
        return self.rfile.read(n).decode("utf-8", "replace") if n else ""

    # ── 메서드 ──
    def do_HEAD(self):
        self.do_GET()

    def do_GET(self):
        url = urlparse(self.path)
        path, q = url.path, parse_qs(url.query)
        if path == "/__writes":
            with LOCK:
                return self.send(json.dumps(WRITES, ensure_ascii=False), ctype="application/json")
        if path == "/ws":
            return self.websocket()
        if path.startswith(STATE_CHANGING_GET):
            self.record()
        if path == "/__rate-limited":
            return self.send("Too Many Requests", 429, "text/plain")
        if path == "/__waf":
            return self.send("<h1>Request blocked by WAF</h1>", 403)
        if path == "/__captcha":
            return self.send(layout("보안 확인", '<div class="g-recaptcha captcha">자동입력 방지 문자를 입력하세요</div>'))
        if path == "/go":  # 리다이렉트로 상태 변경 GET 에 닿는 함정
            return self.redirect(q.get("to", ["/"])[0])
        if path == "/__expire":  # 요청한 쪽의 세션만 끝낸다
            for c in self.headers.get("Cookie", "").split(";"):
                SESSIONS.discard(c.strip()[4:]) if c.strip().startswith("SID=") else None
            return self.send("expired", ctype="text/plain")
        route = PUBLIC.get(path)
        if route:
            return route(self, q)
        if path.startswith("/board/"):
            return h_board_other(self, path)
        if path.startswith("/files/") and path != "/files/":
            return self.send("-- backup dump --\n", ctype="text/plain")
        member = MEMBER.get(path)
        if member is None and path.startswith("/my/orders/"):
            member = order_detail
        if member is None and path.startswith("/my/address/delete"):
            member = lambda h, q: h.redirect("/my/address")  # noqa: E731
        if member:
            if not self.logged_in():
                return self.redirect("/login?next=" + path)
            return member(self, q)
        if path == "/logout":
            return self.redirect("/", {"Set-Cookie": "SID=; Max-Age=0; Path=/"})
        self.send(layout("없는 페이지", "<p>페이지를 찾을 수 없습니다.</p>"), 404)

    def do_POST(self):
        path = urlparse(self.path).path
        body = self.read_body()
        if path == "/__reset":
            with LOCK:
                WRITES.clear()  # 세션은 지우지 않는다(만료 시험은 /__expire)
                ADMIN_FAILS["count"] = 0
            return self.send("ok", ctype="text/plain")
        if path in READONLY_POST:
            return self.send(json.dumps({"orders": [dict(o, phone=mask_phone(o["phone"])) for o in ORDERS]},
                                        ensure_ascii=False), ctype="application/json")
        self.record(body)
        if path == "/admin/login":
            form = parse_qs(body)
            with LOCK:
                if ADMIN_FAILS["count"] >= 5:
                    return self.send(layout("관리자 로그인", "<p>계정이 잠금 상태입니다. 관리자에게 문의하세요.</p>"))
                ADMIN_FAILS["count"] += 1
            return self.send(layout("관리자 로그인", f"<p>아이디 또는 비밀번호가 올바르지 않습니다({ADMIN_FAILS['count']}/5).</p>"))
        self.send(layout("처리 완료", "<p>저장되었습니다.</p>"))

    do_PUT = do_PATCH = do_DELETE = do_POST

    # ── WebSocket(쓰기 함정) ──
    def websocket(self):
        key = self.headers.get("Sec-WebSocket-Key", "")
        accept = base64.b64encode(hashlib.sha1((key + WS_GUID).encode()).digest()).decode()
        self.send_response(101)
        self.send_header("Upgrade", "websocket")
        self.send_header("Connection", "Upgrade")
        self.send_header("Sec-WebSocket-Accept", accept)
        self.end_headers()
        self.wfile.flush()
        try:
            head = self.rfile.read(2)
            if len(head) < 2:
                return
            length = head[1] & 0x7F
            if length == 126:
                length = int.from_bytes(self.rfile.read(2), "big")
            mask = self.rfile.read(4)
            data = bytes(b ^ mask[i % 4] for i, b in enumerate(self.rfile.read(length)))
            self.record(data.decode("utf-8", "replace"), method="WS")
            self.wfile.write(bytes([0x81, 2]) + b"ok")
            self.wfile.flush()
        except OSError:
            pass


# ── 공개 화면 ──
def home(h, q):
    h.send(layout("홈", "<p>데모몰에 오신 것을 환영합니다.</p>"))


def notice(h, q):  # 안전 화면
    h.send(layout("공지사항", "<table><tr><th>번호</th><th>제목</th></tr><tr><td>1</td><td>배송 일정 안내</td></tr>"
                          "<tr><td>2</td><td>설 연휴 고객센터 운영 안내</td></tr><tr><td>3</td><td>서비스 점검 안내</td></tr></table>"))


def faq(h, q):  # 안전 화면
    h.send(layout("FAQ", "<dl><dt>배송은 얼마나 걸리나요?</dt><dd>평균 2일입니다.</dd><dt>교환은 어떻게 하나요?</dt><dd>마이페이지에서 신청합니다.</dd></dl>"))


def terms(h, q):  # 안전 화면
    h.send(layout("이용약관", "<p>제1조(목적) 이 약관은 데모몰 이용에 관한 사항을 정한다.</p>"))


def board(h, q):
    rows = "".join(f'<tr><td>{i}</td><td><a href="/board/{i}">문의 {i}</a></td></tr>' for i in (1, 2, 3))
    h.send(layout("게시판", f"<table><tr><th>번호</th><th>제목</th></tr>{rows}</table>",
                  head="<script>addEventListener('load',()=>{const w=new WebSocket('ws://'+location.host+'/ws');"
                       "w.onopen=()=>w.send('board-view');});</script>"))


def board_post(h, q):  # 취약: 게시글로 다른 회원 연락처 노출
    h.send(layout("배송 문의", "<p>배송지 변경 부탁드립니다. 받는 분 김영희 010-9876-5432, younghee@example.com</p>"
                              '<p>첨부: <a href="/files/receipt-1.txt">receipt-1.txt</a></p>'))


def h_board_other(h, path):  # 1번 글과 같은 틀의 반복 화면(표본 대상)
    n = path.rsplit("/", 1)[1]
    if n not in ("2", "3"):
        return h.send(layout("없는 페이지", "<p>페이지를 찾을 수 없습니다.</p>"), 404)
    h.send(layout(f"문의 {n}", "<p>상품 재입고 일정이 궁금합니다.</p>"))


def search(h, q):  # 조회성 POST 화면
    h.send(layout("주문조회", '<div id="result">조회 중…</div>',
                  head="<script>addEventListener('load',async()=>{try{const r=await fetch('/api/orders/search',"
                       "{method:'POST',body:'{}'});const d=await r.json();document.getElementById('result').innerHTML="
                       "'<table class=\"orders\">'+d.orders.map(o=>'<tr><td>'+o.id+'</td><td>'+o.item+'</td><td>'+o.phone+'</td></tr>').join('')+'</table>';}"
                       "catch(e){document.getElementById('result').textContent='네트워크 오류';}});</script>"))


def login(h, q):  # 취약: GET 폼으로 비밀번호가 URL에 실림
    h.send(layout("로그인", '<form method="get" action="/login/submit"><label>아이디 <input name="id"></label>'
                           '<label>비밀번호 <input type="password" name="pw"></label><button>로그인</button></form>'))


def login_submit(h, q):  # 취약: 실패 횟수 제한 없음
    if q.get("id", [""])[0] == DEMO_USER and q.get("pw", [""])[0] == DEMO_PASS:
        sid = secrets.token_hex(16)
        SESSIONS.add(sid)
        return h.redirect(q.get("next", ["/my"])[0], {"Set-Cookie": f"SID={sid}; Path=/"})  # 취약: HttpOnly·Secure 없음
    h.send(layout("로그인", "<p>아이디 또는 비밀번호가 올바르지 않습니다.</p>"))


def join(h, q):  # 취약: 동의 미구분, 거부권 미고지, 마케팅 사전 체크, 주민번호 수집, 약한 비밀번호 규칙
    h.send(layout("회원가입", """<form method="post" action="/join">
<label>이름 <input name="name"></label><label>주민등록번호 <input name="jumin" placeholder="000000-0000000"></label>
<label>비밀번호 <input type="password" name="pw"></label><p class="help">비밀번호는 4자 이상 입력하세요.</p>
<fieldset><legend>약관 동의</legend>
<label><input type="checkbox" name="agree_all" required> 이용약관, 개인정보 수집·이용(필수), 제3자 제공(선택)에 모두 동의합니다.</label>
<label><input type="checkbox" name="marketing" checked> 마케팅 정보 수신(SMS·이메일)에 동의합니다.</label>
</fieldset><button>가입하기</button></form>"""))


def find_pw(h, q):
    h.send(layout("비밀번호 찾기", '<form method="post" action="/find-pw"><label>이메일 <input name="email"></label><button>재설정 메일 받기</button></form>'))


def privacy(h, q):  # 취약: 개인정보 보호책임자 공개 누락
    h.send(layout("개인정보 보호정책", "<h2>1. 수집 항목</h2><p>이름, 연락처, 이메일, 주소</p><h2>2. 보유 기간</h2><p>회원 탈퇴 시까지</p>"
                                  "<h2>3. 제3자 제공</h2><p>배송업체에 이름·연락처·주소를 제공합니다.</p>"))


def admin(h, q):  # 취약: 관리자 로그인 화면 외부 노출
    h.send(layout("관리자 로그인", '<form method="post" action="/admin/login"><input name="id"><input type="password" name="pw"><button>로그인</button></form>'))


def files_index(h, q):  # 취약: 디렉터리 리스팅
    h.send("<html><head><title>Index of /files/</title></head><body><h1>Index of /files/</h1><ul>"
           '<li><a href="backup_members_20260901.sql">backup_members_20260901.sql</a></li>'
           '<li><a href="receipt-1.txt">receipt-1.txt</a></li></ul></body></html>')


def service_worker(h, q):  # Service Worker 우회 시험용(쓰기 요청을 route 밖에서 보낼 수 있음)
    h.send("self.addEventListener('install',()=>fetch('/api/log',{method:'POST',body:'sw'}));", ctype="text/javascript")


def robots(h, q):
    h.send("User-agent: *\nDisallow: /admin\nDisallow: /files/\n", ctype="text/plain")


# ── 회원 화면 ──
def mypage(h, q):  # 취약: 주석에 이메일, beacon(쓰기 함정)
    h.send(layout("마이페이지", f"<!-- debug user: {ME['email']} -->"
                            f"<p>{ME['name'][0]}*{ME['name'][2]} 님, 반갑습니다.</p>", member=True,
                  head="<script>addEventListener('load',()=>{navigator.sendBeacon('/api/log','view=my');"
                       "localStorage.setItem('access_token','eyJhbGciOiJIUzI1NiJ9.demo.token');"
                       f"localStorage.setItem('user_email','{ME['email']}');}});</script>"))


def profile(h, q):  # 취약: 주민번호·카드번호 평문, JS 변수, 저장 버튼(쓰기 함정)
    h.send(layout("회원정보 수정", f"""<form method="post" action="/my/profile">
<label>이름 <input name="name" value="{ME['name']}"></label>
<label>휴대전화 <input name="phone" value="{ME['phone']}"></label>
<label>주민등록번호 <input name="ssn" value="{ME['ssn']}" readonly></label>
<input type="hidden" name="card" value="{ME['card']}">
<button>저장</button></form>""", member=True,
                  head=f"<script>window.__USER__={json.dumps(ME, ensure_ascii=False)};"
                       "addEventListener('load',()=>fetch('/api/me').then(r=>r.json()).then(u=>{"
                       "document.querySelector('h1').dataset.user=u.name;}));</script>"))


def orders(h, q):  # 안전 화면(목록은 마스킹)
    rows = "".join(f'<tr><td><a href="/my/orders/{o["id"]}">{o["id"]}</a></td><td>{o["item"]}</td>'
                   f'<td>{o["receiver"][0]}*{o["receiver"][2]}</td><td>{mask_phone(o["phone"])}</td></tr>' for o in ORDERS)
    h.send(layout("주문내역", f"<table class='orders'><tr><th>주문번호</th><th>상품</th><th>받는 분</th><th>연락처</th></tr>{rows}</table>", member=True))


def order_detail(h, q):  # 취약: 목록과 달리 상세에서 마스킹하지 않음
    oid = int(h.path.rsplit("/", 1)[1]) if h.path.rsplit("/", 1)[1].isdigit() else 0
    o = next((o for o in ORDERS if o["id"] == oid), None)
    if not o:
        return h.send(layout("주문 상세", "<p>주문이 없습니다.</p>", member=True), 404)
    h.send(layout("주문 상세", f"<table><tr><th>주문번호</th><td>{o['id']}</td></tr><tr><th>상품</th><td>{o['item']}</td></tr>"
                            f"<tr><th>받는 분</th><td>{o['receiver']}</td></tr><tr><th>연락처</th><td>{o['phone']}</td></tr></table>", member=True))


def address(h, q):
    h.send(layout("배송지 관리", f"<table><tr><td>집</td><td>{ME['address']}</td>"
                             '<td><a href="/my/address/delete?id=1">삭제</a></td></tr></table>', member=True))


def settings(h, q):
    h.send(layout("마케팅 수신 설정", '<form method="post" action="/my/settings"><label><input type="checkbox" name="sms" checked> SMS 수신</label>'
                                 "<button>저장</button></form>", member=True))


def withdraw(h, q):
    h.send(layout("회원탈퇴", '<form method="post" action="/my/withdraw"><p>탈퇴하면 주문 내역이 삭제됩니다.</p><button>탈퇴하기</button></form>', member=True))


def api_me(h, q):  # 취약: API 과다 응답
    h.send(json.dumps(ME, ensure_ascii=False), ctype="application/json")


PUBLIC = {
    "/": home, "/notice": notice, "/faq": faq, "/terms": terms, "/board": board, "/board/1": board_post,
    "/search": search, "/login": login, "/login/submit": login_submit, "/join": join, "/find-pw": find_pw,
    "/privacy": privacy, "/admin": admin, "/files/": files_index, "/robots.txt": robots, "/sw.js": service_worker,
}
MEMBER = {
    "/my": mypage, "/my/profile": profile, "/my/orders": orders, "/my/address": address,
    "/my/settings": settings, "/my/withdraw": withdraw, "/api/me": api_me,
}


def make_server(host: str = "127.0.0.1", port: int = 8765) -> ThreadingHTTPServer:
    server = ThreadingHTTPServer((host, port), Handler)
    server.daemon_threads = True
    return server


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--port", type=int, default=8765)
    args = ap.parse_args()
    srv = make_server(port=args.port)
    print(f"데모몰: http://127.0.0.1:{srv.server_address[1]}")
    srv.serve_forever()
