"""쓰기 차단(설계서 7-1) 테스트: 데모 함정이 서버에 닿지 않는지 실제 브라우저로 확인한다."""
import pytest
from playwright.sync_api import Error as PlaywrightError
from playwright.sync_api import sync_playwright

from scripts.guard import Guard, install_guard
from scripts.session import new_context

INTAKE = {"target_url": "https://shop.example.com", "allowed_domains": ["shop.example.com"]}
BASE = "https://shop.example.com"


@pytest.fixture(scope="module")
def browser():
    with sync_playwright() as p:
        b = p.chromium.launch()
        yield b
        b.close()


@pytest.mark.parametrize("path", [
    "/LOGOUT", "/user/logout.do", "/member/delete.jsp?id=1", "/%6Cogout", "/logout;jsessionid=abc",
    "/board/Delete.action?no=3", "/MY/address/delete?id=1", "/a?ACTION=Delete",
])
def test_deny_patterns_survive_case_encoding_and_suffixes(path):
    g = Guard(INTAKE, {"deny_patterns": ["/my/address/delete"]})
    assert g.decide("GET", BASE + path, True) == "deny-pattern"


@pytest.mark.parametrize("path", ["/my/withdraw", "/catalog/removed-items", "/member/updateForm.do",
                                  "/board/writeForm.do", "/model/list.do", "/board/list.do?searchKeyword=kim"])
def test_view_pages_with_action_words_are_not_denied(path):
    assert Guard(INTAKE, {}).decide("GET", BASE + path, True) is None


def test_deny_patterns_apply_to_out_of_scope_gets():
    assert Guard(INTAKE, {}).decide("GET", "https://sso.example.net/logout", False) == "deny-pattern"


def test_install_refuses_context_without_blocked_service_workers(browser):
    ctx = browser.new_context()
    with pytest.raises(ValueError, match="service_workers"):
        install_guard(ctx, INTAKE, {})
    ctx.close()


REGISTER_SW = """async () => { try { await navigator.serviceWorker.register('/sw.js'); } catch (e) {}
                     await new Promise(r => setTimeout(r, 300));
                     return (await navigator.serviceWorker.getRegistrations()).length; }"""


def test_inspection_context_blocks_service_worker_registration(browser, demo):
    ctx = new_context(browser)
    page = ctx.new_page()
    page.goto(demo.url("/faq"))
    assert page.evaluate(REGISTER_SW) == 0
    ctx.close()


def test_plain_context_would_register_service_worker(browser, demo):
    # 위 테스트가 실제로 실패할 수 있음을 보이는 대조군
    ctx = browser.new_context()
    page = ctx.new_page()
    page.goto(demo.url("/faq"))
    assert page.evaluate(REGISTER_SW) == 1
    ctx.close()


def test_out_of_scope_iframe_navigation_is_blocked(browser, demo):
    ctx, page, guard = make(browser, demo)
    page.goto(demo.url("/faq"))
    page.evaluate("() => { const f = document.createElement('iframe'); f.src = 'http://example.invalid/x'; document.body.append(f); }")
    page.wait_for_timeout(500)
    assert any(e["reason"] == "out-of-scope" for e in guard.blocked_log)
    ctx.close()


def make(browser, demo, **kw):
    context = new_context(browser)
    page = context.new_page()
    page.goto(demo.login_url())  # 로그인은 guard 전에(설계서 7-2)
    intake = {"target_url": demo.base, "allowed_domains": ["127.0.0.1"]}
    menu_map = {"menus": [], "deny_patterns": ["/my/address/delete"]}
    guard = install_guard(context, intake, menu_map, **kw)
    return context, page, guard


def test_form_post_is_blocked(browser, demo):
    ctx, page, guard = make(browser, demo)
    page.goto(demo.url("/my/profile"))
    page.click("text=저장")
    page.wait_for_timeout(300)
    assert demo.writes() == []
    assert any(e["method"] == "POST" and "/my/profile" in e["url"] for e in guard.blocked_log)
    ctx.close()


def test_beacon_is_blocked(browser, demo):
    ctx, page, guard = make(browser, demo)
    page.goto(demo.url("/my"))
    page.wait_for_timeout(500)
    assert not any(w["path"] == "/api/log" for w in demo.writes())
    assert any("/api/log" in e["url"] for e in guard.blocked_log)
    ctx.close()


def test_websocket_never_reaches_server(browser, demo):
    ctx, page, guard = make(browser, demo)
    page.goto(demo.url("/board"))
    page.wait_for_timeout(500)
    assert not any(w["method"] == "WS" for w in demo.writes())
    assert any(e["method"] == "WS" for e in guard.blocked_log)
    ctx.close()


@pytest.mark.parametrize("path", ["/logout", "/my/address/delete?id=1"])
def test_state_changing_get_is_blocked(browser, demo, path):
    ctx, page, guard = make(browser, demo)
    with pytest.raises(PlaywrightError):
        page.goto(demo.url(path))
    assert demo.writes() == []
    assert any(e["reason"] == "deny-pattern" for e in guard.blocked_log)
    ctx.close()


def test_fetch_put_and_delete_are_blocked(browser, demo):
    ctx, page, guard = make(browser, demo)
    page.goto(demo.url("/faq"))
    page.evaluate("""async () => { for (const m of ['PUT','DELETE','PATCH']) {
        try { await fetch('/my/profile', {method: m, body: m === 'DELETE' ? null : 'x'}); } catch (e) {} } }""")
    assert demo.writes() == []
    assert {e["method"] for e in guard.blocked_log} >= {"PUT", "DELETE", "PATCH"}
    ctx.close()


def test_readonly_post_blocked_by_default(browser, demo):
    ctx, page, guard = make(browser, demo)
    page.goto(demo.url("/search"))
    page.wait_for_selector("text=네트워크 오류")
    assert any("/api/orders/search" in e["url"] for e in guard.blocked_log)
    ctx.close()


def test_readonly_post_passes_when_allowed(browser, demo):
    ctx, page, guard = make(browser, demo, allow_post=[r"/api/orders/search$"])
    page.goto(demo.url("/search"))
    page.wait_for_selector("table.orders tr")
    assert demo.writes() == []
    ctx.close()


def test_temporary_allow_then_reblock(browser, demo):
    ctx, page, guard = make(browser, demo)
    page.goto(demo.url("/admin"))
    with guard.temporarily_allow("POST", demo.url("/admin/login")):
        page.click("form[action=\"/admin/login\"] button")
        page.wait_for_load_state()
    assert [w["path"] for w in demo.writes()] == ["/admin/login"]
    assert guard.allowed_log and guard.allowed_log[0]["method"] == "POST"
    page.goto(demo.url("/admin"))
    page.click("form[action=\"/admin/login\"] button")
    page.wait_for_timeout(300)
    assert len(demo.writes()) == 1
    ctx.close()


def test_navigation_outside_scope_is_blocked(browser, demo):
    ctx, page, guard = make(browser, demo)
    with pytest.raises(PlaywrightError):
        page.goto("http://example.invalid/")
    assert any(e["reason"] == "out-of-scope" for e in guard.blocked_log)
    ctx.close()


@pytest.mark.parametrize("to", ["/logout", "/my/address/delete?id=1"])
def test_redirect_to_state_changing_get_is_blocked(browser, demo, to):
    ctx, page, guard = make(browser, demo)
    try:
        page.goto(demo.url("/go?to=" + to))
    except PlaywrightError:
        pass
    assert demo.writes() == []
    assert any(e["reason"] == "deny-pattern" for e in guard.blocked_log)
    ctx.close()


def test_safe_redirect_still_works_and_keeps_session(browser, demo):
    ctx, page, guard = make(browser, demo)
    page.goto(demo.url("/go?to=/my/orders"))
    assert page.url.endswith("/my/orders")
    assert page.locator("table.orders").count() == 1
    ctx.close()


def test_redirect_trap_writes_without_guard(browser, demo):
    # 대조군: guard 가 없으면 /go → /logout 이 서버에 닿는다(위 차단 테스트가 실제로 실패할 수 있음)
    ctx = new_context(browser)
    page = ctx.new_page()
    page.goto(demo.url("/go?to=/logout"))
    assert any(w["path"] == "/logout" for w in demo.writes())
    ctx.close()


@pytest.mark.parametrize("path", [
    "/uat/uia/actionLogout.do", "/member/logoutProc.do", "/board/deleteArticle.do?id=3", "/bbs/delete_ok.php?no=1",
    "/order/cancel.do?no=1", "/mypage/withdrawProc.do", "/cart/clear", "/a?mode=del_ok", "/a?act=delete_ok",
    "/mail/sendMail.do", "/member/updateProc.do", "/board/insertArticle.do", "/member/memberDelete.jsp",
])
def test_legacy_style_action_urls_are_denied(path):
    assert Guard(INTAKE, {}).decide("GET", BASE + path, True) == "deny-pattern"


def test_intake_deny_patterns_apply_before_menu_map_exists():
    g = Guard(dict(INTAKE, deny_patterns=["/custom/zap"]), {})
    assert g.decide("GET", BASE + "/custom/zap?x=1", True) == "deny-pattern"


def test_allow_post_is_full_match_and_still_deny_checked():
    g = Guard(INTAKE, {}, allow_post=[r"/api/orders/search", r"/api/.*"])
    assert g.decide("POST", BASE + "/api/orders/search", False) is None
    assert g.decide("POST", BASE + "/api/orders/search/x", False) is None  # 두 번째 패턴 /api/.* 에 맞음
    assert g.decide("POST", BASE + "/api/member/delete", False) == "write-method"
    g2 = Guard(INTAKE, {}, allow_post=[r"/api/orders/search"])
    assert g2.decide("POST", BASE + "/api/orders/searchAndDelete", False) == "write-method"


def test_allowed_readonly_posts_are_logged(browser, demo):
    ctx, page, guard = make(browser, demo, allow_post=[r"/api/orders/search"])
    page.goto(demo.url("/search"))
    page.wait_for_selector("table.orders tr")
    assert any(e["method"] == "POST" and e["reason"] == "allow-post" for e in guard.allowed_log)
    ctx.close()
