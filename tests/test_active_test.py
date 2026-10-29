"""능동 시험(설계서 4-3 active_test, 7-1의 6번) 테스트."""
import json

import pytest
from playwright.sync_api import Error as PlaywrightError
from playwright.sync_api import sync_playwright

from scripts.active_test import run_active_test

APPROVAL = {"text": "관리자 로그인 실패 잠금을 테스트 계정으로 최대 6회 시험한다", "approved_by": "점검 승인자"}


@pytest.fixture(scope="module")
def browser():
    with sync_playwright() as p:
        b = p.chromium.launch()
        yield b
        b.close()


def intake(demo):
    return {"target_url": demo.base, "allowed_domains": ["127.0.0.1"]}


def wrong_admin_logins(n):
    def drive(page):
        admin_url = page.url
        for _ in range(n):
            page.fill('input[name="id"]', "admin")
            page.fill('input[name="pw"]', "wrong-pass")
            page.click('form[action="/admin/login"] button')
            page.wait_for_load_state()
            for _ in range(3):  # 한도를 넘은 시도는 막혀 오류 화면 이동과 겹칠 수 있다
                try:
                    page.goto(admin_url)
                    break
                except PlaywrightError:
                    page.wait_for_timeout(300)
    return drive


def run(browser, demo, tmp_path, **kw):
    args = dict(item_id="SAFE-05-06-02", approval=APPROVAL, start_url=demo.url("/admin"),
                targets=[("POST", demo.url("/admin/login"))], max_attempts=6,
                raw_dir=tmp_path / "raw", workdir=tmp_path / "work", driver=wrong_admin_logins(7))
    args.update(kw)
    return run_active_test(browser, None, intake(demo), {"menus": [], "deny_patterns": []}, **args)


def test_refuses_without_approval(browser, demo, tmp_path):
    with pytest.raises(ValueError, match="승인"):
        run(browser, demo, tmp_path, approval={"text": ""})


def test_only_approved_requests_reach_server_up_to_the_limit(browser, demo, tmp_path):
    record = run(browser, demo, tmp_path)
    assert [w["path"] for w in demo.writes()] == ["/admin/login"] * 6
    assert record["attempts"] == 6 and record["max_attempts"] == 6
    assert record["over_limit_blocked"] >= 1
    assert record["allowed"] == [{"method": "POST", "url": demo.url("/admin/login")}]
    assert record["approval"] == APPROVAL["text"] and record["approved_by"] == APPROVAL["approved_by"]
    assert record["approved_at"]


def test_observes_lockout_message(browser, demo, tmp_path):
    record = run(browser, demo, tmp_path)
    assert any("잠금" in o["text"] for o in record["observations"])
    assert (tmp_path / "raw" / "ACT-SAFE-05-06-02" / record["final_screenshot"]).exists()
    assert [o["attempt"] for o in record["observations"]] == list(range(1, 7))


def test_other_writes_stay_blocked_during_the_test(browser, demo, tmp_path):
    def drive(page):
        page.evaluate("() => fetch('/my/profile', {method: 'POST', body: 'x'}).catch(() => {})")
        page.wait_for_timeout(200)
    run(browser, demo, tmp_path, driver=drive)
    assert demo.writes() == []


def test_har_has_no_typed_password(browser, demo, tmp_path):
    run(browser, demo, tmp_path)
    har = (tmp_path / "raw" / "ACT-SAFE-05-06-02" / "network.har").read_text(encoding="utf-8")
    assert "wrong-pass" not in har


def test_get_login_attempts_are_counted_without_writes(browser, demo, tmp_path):
    def drive(page):
        for _ in range(6):
            page.fill('input[name="id"]', "hong")
            page.fill('input[name="pw"]', "wrong-pass")
            page.click('form[action="/login/submit"] button')
            page.wait_for_load_state()
            page.goto(demo.url("/login"))
    record = run(browser, demo, tmp_path, item_id="SAFE-05-06-01", start_url=demo.url("/login"),
                 targets=[("GET", demo.url("/login/submit"))], driver=drive)
    assert record["attempts"] == 6
    assert not any("잠금" in o["text"] for o in record["observations"])
    assert demo.writes() == []
    json.dumps(record)  # findings.json 에 그대로 넣을 수 있어야 한다


def test_refuses_raw_dir_inside_workdir(browser, demo, tmp_path):
    with pytest.raises(ValueError, match="작업 폴더 밖"):
        run(browser, demo, tmp_path, raw_dir=tmp_path / "work" / "raw")


@pytest.mark.parametrize("target", [("POST", "http://example.invalid/login"), ("GET", "/logout")])
def test_refuses_out_of_scope_or_denied_targets(browser, demo, tmp_path, target):
    method, url = target
    url = url if url.startswith("http") else demo.url(url)
    with pytest.raises(ValueError, match="대상"):
        run(browser, demo, tmp_path, targets=[(method, url)])


def test_redirect_from_target_goes_back_through_the_guard(browser, demo, tmp_path):
    def drive(page):
        try:
            page.goto(demo.url("/go?to=/logout"))
        except PlaywrightError:
            pass
    run(browser, demo, tmp_path, item_id="SAFE-05-06-01", start_url=demo.url("/faq"),
        targets=[("GET", demo.url("/go"))], driver=drive)
    assert demo.writes() == []
