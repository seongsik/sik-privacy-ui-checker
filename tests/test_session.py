"""로그인 대기와 세션 저장(설계서 2장 1단계, 7-2) 테스트."""
import inspect
import json

import pytest
from playwright.sync_api import sync_playwright

from scripts import session


def test_refuses_storage_state_inside_workdir(tmp_path, demo):
    work = tmp_path / "work"
    work.mkdir()
    with pytest.raises(ValueError, match="작업 폴더 밖"):
        session.run_login(demo.base, "url:/faq", work / "session.json", work, headless=True)


def test_rejects_unknown_login_condition(tmp_path, demo):
    with pytest.raises(ValueError, match="selector:"):
        session.run_login(demo.base, "enter", tmp_path / "s.json", tmp_path / "work", headless=True)


def test_saves_session_after_url_condition(tmp_path, demo):
    state = tmp_path / "outside" / "session.json"
    work = tmp_path / "work"
    # 사용자가 로그인하는 동작을 데모의 GET 로그인 주소로 대신한다
    session.run_login(demo.base, "url:/faq", state, work, headless=True, start_url=demo.login_url())
    cookies = json.loads(state.read_text(encoding="utf-8"))["cookies"]
    assert any(c["name"] == "SID" for c in cookies)
    assert not list(work.rglob("*.har")) if work.exists() else True


def test_saves_session_after_selector_condition(tmp_path, demo):
    state = tmp_path / "s.json"
    session.run_login(demo.base, "selector:nav.lnb", state, tmp_path / "w", headless=True,
                      start_url=demo.login_url("/my"))
    assert state.exists()


def test_times_out_when_login_never_completes(tmp_path, demo):
    with pytest.raises(TimeoutError):
        session.run_login(demo.base, "selector:nav.lnb", tmp_path / "s.json", tmp_path / "w",
                          headless=True, timeout_ms=1500)


def test_open_context_restores_login(tmp_path, demo):
    state = tmp_path / "s.json"
    session.run_login(demo.base, "url:/faq", state, tmp_path / "w", headless=True, start_url=demo.login_url())
    with sync_playwright() as p:
        browser = p.chromium.launch()
        ctx = session.open_context(browser, state)
        page = ctx.new_page()
        page.goto(demo.url("/my/orders"))
        assert page.url.endswith("/my/orders")
        assert ctx.pages[0].viewport_size == {"width": 1920, "height": 1080}
        browser.close()


def test_no_credential_parameters():
    params = set(inspect.signature(session.run_login).parameters)
    assert not params & {"user", "username", "password", "pw", "passwd", "login_id"}



@pytest.mark.skipif(__import__("os").name == "nt", reason="Windows 는 POSIX 권한 비트를 쓰지 않는다")
def test_session_file_is_owner_only(tmp_path, demo):
    state = tmp_path / "outside" / "session.json"
    session.run_login(demo.base, "url:/faq", state, tmp_path / "w", headless=True, start_url=demo.login_url())
    assert state.stat().st_mode & 0o777 == 0o600
    assert state.parent.stat().st_mode & 0o777 == 0o700
