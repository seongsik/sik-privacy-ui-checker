"""메뉴맵 수집(설계서 2장 2단계, 4-2) 테스트: 데모 정답(menu_expected.json)과 비교한다."""
import json
from pathlib import Path

import pytest
from playwright.sync_api import sync_playwright

from scripts.menumap import build_menu_map
from scripts.models import load_menu_map
from scripts.session import new_context

DEMO_DIR = Path(__file__).resolve().parents[1] / "skills" / "privacy-ui-check" / "demo"


@pytest.fixture(scope="module")
def result(demo_server):
    demo_server.reset()
    with sync_playwright() as p:
        browser = p.chromium.launch()
        ctx = new_context(browser)
        ctx.new_page().goto(demo_server.login_url())
        intake = {"target_url": demo_server.base, "allowed_domains": ["127.0.0.1"]}
        data = build_menu_map(ctx, intake, delay_s=0)
        browser.close()
    data["writes"] = demo_server.writes()
    return data


def by_url(menu_map):
    ids = {m["id"]: m["url"] for m in menu_map["menus"]}
    return {
        m["url"]: (m["status"], tuple(m["path"]), ids.get(m.get("sample_of")))
        for m in menu_map["menus"]
    }


def relative(url, base):
    return url[len(base):] if url.startswith(base) else url


def test_menu_tree_matches_answer_key(result, demo_server):
    expected = by_url(load_menu_map(DEMO_DIR / "menu_expected.json"))
    got = {relative(u, demo_server.base): (s, p, relative(o, demo_server.base) if o else None)
           for u, (s, p, o) in by_url(result).items()}
    assert got == expected


def test_result_is_a_valid_menu_map(result):
    load_menu_map(result)


def test_state_changing_links_become_deny_patterns(result):
    assert {"/logout", "/my/address/delete"} <= set(result["deny_patterns"])


def test_reading_menus_writes_nothing(result):
    assert result["writes"] == []


def test_attachments_are_listed_not_visited_as_menus(result, demo_server):
    assert demo_server.url("/files/receipt-1.txt") in result["attachments"]


def test_robots_disallow_paths_become_probes(result):
    assert {"/admin", "/files/"} <= set(result["probes"])


def test_member_pages_are_marked_user_auth(result, demo_server):
    auth = {relative(m["url"], demo_server.base): m["auth"] for m in result["menus"]}
    assert auth["/my/profile"] == "user"
    assert auth["/faq"] == "none"


def test_saved_json_round_trips(result, tmp_path):
    p = tmp_path / "menu_map.json"
    p.write_text(json.dumps(result, ensure_ascii=False), encoding="utf-8")
    assert load_menu_map(p)["menus"]
