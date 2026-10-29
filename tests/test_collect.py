"""수집기(설계서 2장 3단계, 3-2, 7-2~7-4) 테스트."""
import json

import pytest
from playwright.sync_api import sync_playwright

from scripts import session
from scripts.collect import ARTIFACTS, CollectStopped, collect, scrub_har

INTAKE_EXTRA = {"allowed_domains": ["127.0.0.1"]}


@pytest.fixture(scope="module")
def storage(demo_server, tmp_path_factory):
    path = tmp_path_factory.mktemp("outside") / "session.json"
    session.run_login(demo_server.base, "url:/faq", path, tmp_path_factory.mktemp("w"), headless=True,
                      start_url=demo_server.login_url())
    return path


@pytest.fixture(scope="module")
def browser(storage):  # 로그인(별도 Playwright)을 먼저 끝낸다
    with sync_playwright() as p:
        b = p.chromium.launch()
        yield b
        b.close()


def menu(mid, url, status="visit", auth="none", **kw):
    return dict(id=mid, path=[mid], url=url, status=status, auth=auth, **kw)


def run(browser, storage, demo, menus, tmp_path, **kw):
    intake = {"target_url": demo.base, **INTAKE_EXTRA}
    raw, work = tmp_path / "raw", tmp_path / "work"
    work.mkdir(exist_ok=True)
    collect(browser, storage, intake, {"menus": menus, "deny_patterns": []}, raw, work, delay_s=0, **kw)
    return raw


def test_collects_six_artifacts_per_visited_menu(browser, storage, demo, tmp_path):
    menus = [menu("M1", demo.url("/faq")), menu("M2", demo.url("/my/profile"), auth="user"),
             menu("M3", demo.url("/board/2"), "sample", sample_of="M1"),
             menu("M4", demo.url("/logout"), "blocked", reason="x")]
    raw = run(browser, storage, demo, menus, tmp_path)
    for mid in ("M1", "M2"):
        assert {p.name for p in (raw / mid).iterdir()} >= set(ARTIFACTS), mid
    assert not (raw / "M3").exists() and not (raw / "M4").exists()
    assert "900101-1234567" in (raw / "M2" / "dom.html").read_text(encoding="utf-8")
    cookies = json.loads((raw / "M2" / "cookies.json").read_text(encoding="utf-8"))
    assert any(c["name"] == "SID" and c["httpOnly"] is False for c in cookies)
    assert demo.writes() == []


def test_public_menus_are_collected_without_login(browser, storage, demo, tmp_path):
    raw = run(browser, storage, demo, [menu("M1", demo.url("/faq"))], tmp_path)
    cookies = json.loads((raw / "M1" / "cookies.json").read_text(encoding="utf-8"))
    assert not any(c["name"] == "SID" for c in cookies)


def test_storage_and_blocked_requests_are_recorded(browser, storage, demo, tmp_path):
    raw = run(browser, storage, demo, [menu("M1", demo.url("/my"), auth="user")], tmp_path)
    store = json.loads((raw / "M1" / "storage.json").read_text(encoding="utf-8"))
    assert "access_token" in store["localStorage"]
    meta = json.loads((raw / "M1" / "meta.json").read_text(encoding="utf-8"))
    assert any("/api/log" in b["url"] for b in meta["blocked"])
    assert demo.writes() == []


def test_har_has_no_password_values(browser, storage, demo, tmp_path):
    raw = run(browser, storage, demo, [menu("M1", demo.login_url("/faq"))], tmp_path)
    har = (raw / "M1" / "network.har").read_text(encoding="utf-8")
    assert "demo-pass" not in har
    assert "pw=***" in har


def test_scrub_har_masks_query_and_post_bodies():
    har = {"log": {"entries": [{"request": {
        "url": "https://x/login?id=a&password=s3cret",
        "queryString": [{"name": "password", "value": "s3cret"}],
        "postData": {"text": "id=a&passwd=s3cret", "params": [{"name": "passwd", "value": "s3cret"}]},
    }}]}}
    text = json.dumps(scrub_har(har))
    assert "s3cret" not in text


def test_refuses_raw_dir_inside_workdir(browser, storage, demo, tmp_path):
    with pytest.raises(ValueError, match="작업 폴더 밖"):
        collect(browser, storage, {"target_url": demo.base}, {"menus": []}, tmp_path / "raw", tmp_path)


@pytest.mark.parametrize("path,reason", [("/__rate-limited", "rate-limited"), ("/__waf", "waf"),
                                          ("/__captcha", "captcha")])
def test_stops_on_protection_pages(browser, storage, demo, tmp_path, path, reason):
    menus = [menu("M1", demo.url("/faq")), menu("M2", demo.url(path)), menu("M3", demo.url("/notice"))]
    with pytest.raises(CollectStopped) as e:
        run(browser, storage, demo, menus, tmp_path)
    assert e.value.reason == reason and e.value.menu_id == "M2"
    assert not (tmp_path / "raw" / "M3").exists()


def test_stops_when_session_expires(browser, demo, tmp_path):
    # 이 테스트만의 세션을 만들어 끝낸다(다른 테스트의 세션은 그대로)
    ctx = browser.new_context()
    ctx.new_page().goto(demo.login_url())
    own = tmp_path / "own-session.json"
    ctx.storage_state(path=str(own))
    ctx.close()
    menus = [menu("M1", demo.url("/my/profile"), auth="user"), menu("M2", demo.url("/__expire"), auth="user"),
             menu("M3", demo.url("/my/orders"), auth="user")]
    with pytest.raises(CollectStopped) as e:
        run(browser, own, demo, menus, tmp_path)
    assert e.value.reason == "session-expired" and e.value.menu_id == "M3"


def test_resume_skips_completed_menus(browser, storage, demo, tmp_path):
    menus = [menu("M1", demo.url("/faq")), menu("M2", demo.url("/__captcha")), menu("M3", demo.url("/notice"))]
    with pytest.raises(CollectStopped):
        run(browser, storage, demo, menus, tmp_path)
    first = (tmp_path / "raw" / "M1" / "meta.json").stat().st_mtime_ns
    menus[1] = menu("M2", demo.url("/__captcha"), "exclude", reason="사용자 제외")
    run(browser, storage, demo, menus, tmp_path)
    assert (tmp_path / "raw" / "M3" / "meta.json").exists()
    assert (tmp_path / "raw" / "M1" / "meta.json").stat().st_mtime_ns == first


def test_only_recollects_named_menu(browser, storage, demo, tmp_path):
    menus = [menu("M1", demo.url("/faq")), menu("M2", demo.url("/notice"))]
    run(browser, storage, demo, menus, tmp_path)
    before = (tmp_path / "raw" / "M2" / "meta.json").stat().st_mtime_ns
    run(browser, storage, demo, menus, tmp_path, only=["M1"])
    assert (tmp_path / "raw" / "M2" / "meta.json").stat().st_mtime_ns == before
