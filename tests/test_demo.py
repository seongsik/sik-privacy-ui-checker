"""데모 사이트 자체 테스트: 페이지, 쓰기 함정 기록, 정답표 무결성."""
import base64
import json
import os
import socket
import urllib.error
import urllib.request
from pathlib import Path

import pytest

from scripts.criteria import load_criteria
from scripts.models import load_menu_map

DEMO_DIR = Path(__file__).resolve().parents[1] / "skills" / "privacy-ui-check" / "demo"
PAGES = ["/", "/notice", "/faq", "/board", "/board/1", "/search", "/login", "/join", "/find-pw",
         "/privacy", "/terms", "/admin", "/files/", "/robots.txt"]
MEMBER_PAGES = ["/my", "/my/profile", "/my/orders", "/my/orders/1001", "/my/orders/1002",
                "/my/address", "/my/settings", "/my/withdraw", "/api/me"]


def get(url, cookie=None):
    req = urllib.request.Request(url, headers={"Cookie": cookie} if cookie else {})
    return urllib.request.urlopen(req)


def login_cookie(demo):
    # 리다이렉트 응답의 Set-Cookie 를 받기 위해 리다이렉트를 따르지 않는다
    class NoRedirect(urllib.request.HTTPRedirectHandler):
        def redirect_request(self, *a, **k):
            return None
    try:
        urllib.request.build_opener(NoRedirect).open(demo.login_url())
    except urllib.error.HTTPError as e:
        return e.headers["Set-Cookie"].split(";")[0]
    raise AssertionError("로그인 응답이 리다이렉트가 아니다")


@pytest.mark.parametrize("path", PAGES)
def test_public_pages_respond(demo, path):
    assert get(demo.url(path)).status == 200


def test_member_pages_need_login(demo):
    with pytest.raises(urllib.error.HTTPError) as e:
        class NoRedirect(urllib.request.HTTPRedirectHandler):
            def redirect_request(self, *a, **k):
                return None
        urllib.request.build_opener(NoRedirect).open(demo.url("/my"))
    assert e.value.code == 302


@pytest.mark.parametrize("path", MEMBER_PAGES)
def test_member_pages_respond_after_login(demo, path):
    assert get(demo.url(path), login_cookie(demo)).status == 200


def test_post_to_profile_is_logged_as_write(demo):
    req = urllib.request.Request(demo.url("/my/profile"), method="POST", data=b"name=x")
    try:
        urllib.request.urlopen(req)
    except urllib.error.HTTPError:
        pass
    assert any(w["method"] == "POST" and w["path"] == "/my/profile" for w in demo.writes())


@pytest.mark.parametrize("path", ["/logout", "/my/address/delete?id=1", "/uat/uia/actionLogout.do",
                                  "/board/deleteArticle.do?nttId=3", "/order/cancel.do?no=1001", "/my/coupon/9"])
def test_state_changing_get_is_logged(demo, path):
    try:
        get(demo.url(path), login_cookie(demo))
    except urllib.error.HTTPError:
        pass
    assert any(path.split("?")[0].startswith(w["path"]) or w["path"] == path.split("?")[0] for w in demo.writes())


def test_readonly_search_post_is_not_a_write(demo):
    req = urllib.request.Request(demo.url("/api/orders/search"), method="POST", data=b"{}",
                                 headers={"Cookie": login_cookie(demo)})
    with urllib.request.urlopen(req) as r:
        assert json.loads(r.read())["orders"]
    assert demo.writes() == []


def test_websocket_message_is_logged(demo):
    host, port = demo.base.replace("http://", "").split(":")
    with socket.create_connection((host, int(port))) as s:
        key = base64.b64encode(os.urandom(16)).decode()
        s.sendall((f"GET /ws HTTP/1.1\r\nHost: {host}\r\nUpgrade: websocket\r\nConnection: Upgrade\r\n"
                   f"Sec-WebSocket-Key: {key}\r\nSec-WebSocket-Version: 13\r\n\r\n").encode())
        assert b"101" in s.recv(1024)
        payload, mask = b"hello", os.urandom(4)
        s.sendall(bytes([0x81, 0x80 | len(payload)]) + mask + bytes(b ^ mask[i % 4] for i, b in enumerate(payload)))
        s.settimeout(2)
        s.recv(16)  # 서버가 기록 후 돌려주는 응답
    assert any(w["method"] == "WS" and w.get("body") == "hello" for w in demo.writes())


def test_stop_condition_pages(demo):
    for path, code in (("/__rate-limited", 429), ("/__waf", 403)):
        with pytest.raises(urllib.error.HTTPError) as e:
            get(demo.url(path))
        assert e.value.code == code
    assert b"captcha" in get(demo.url("/__captcha")).read().lower()


def test_admin_login_locks_after_five_failures(demo):
    def attempt(pw):
        req = urllib.request.Request(demo.url("/admin/login"), method="POST",
                                     data=f"id=admin&pw={pw}".encode())
        with urllib.request.urlopen(req) as r:
            return r.read().decode()
    for _ in range(5):
        attempt("wrong")
    assert "잠금" in attempt("wrong")


def test_expected_answer_key_uses_known_criteria():
    criteria = load_criteria()
    expected = json.loads((DEMO_DIR / "expected.json").read_text(encoding="utf-8"))
    assert len(expected["vulns"]) >= 18
    assert any(v["type"] == "능동" for v in expected["vulns"])
    for v in expected["vulns"]:
        for cid in v["item_ids"]:
            assert cid in criteria, cid
        assert v["verdict"] in ("취약", "권고")
    assert expected["safe_pages"]


def test_menu_answer_key_is_a_valid_menu_map():
    data = load_menu_map(DEMO_DIR / "menu_expected.json")
    urls = {m["url"] for m in data["menus"]}
    assert {"/my/profile", "/board", "/privacy"} <= urls
    assert "/logout" in data["deny_patterns"]
