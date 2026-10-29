"""쓰기 차단(설계서 7-1) 테스트: 데모 함정이 서버에 닿지 않는지 실제 브라우저로 확인한다."""
import pytest
from playwright.sync_api import Error as PlaywrightError
from playwright.sync_api import sync_playwright

from scripts.guard import install_guard


@pytest.fixture(scope="module")
def browser():
    with sync_playwright() as p:
        b = p.chromium.launch()
        yield b
        b.close()


def make(browser, demo, **kw):
    context = browser.new_context()
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
