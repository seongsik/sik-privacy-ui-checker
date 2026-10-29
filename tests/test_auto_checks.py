"""자동 점검 후보(설계서 3-1 auto_checks) 테스트: 탐지기별 양성·음성 예제와 데모 정답표 대조."""
import json
from pathlib import Path

import pytest
from playwright.sync_api import sync_playwright

from scripts import auto_checks as ac
from scripts import session
from scripts.collect import collect
from scripts.criteria import load_criteria
from scripts.models import load_menu_map

DEMO_DIR = Path(__file__).resolve().parents[1] / "skills" / "privacy-ui-check" / "demo"


def rec(dom="", cookies=(), storage=None, har_entries=(), url="https://shop.example.com/x", auth="none",
        title="", path=("화면",)):
    return ac.PageRecord(
        menu={"id": "M1", "url": url, "auth": auth, "path": list(path)},
        dom=dom, cookies=list(cookies), storage=storage or {"localStorage": {}, "sessionStorage": {}},
        headers={"final_url": url, "status": 200, "headers": {}},
        har={"log": {"entries": list(har_entries)}}, title=title,
    )


def keys(cands):
    return {c["key"] for c in cands}


# ── 탐지기별 양성·음성 ──
CASES = [
    ("password-in-url",
     rec('<form method="get" action="/login"><input type="password" name="pw"></form>'),
     rec('<form method="post" action="/login"><input type="password" name="pw"></form>')),
    ("html-comment-pii", rec("<!-- debug: kim@example.com --><p>x</p>"), rec("<!-- layout start --><p>x</p>")),
    ("pii-plain-in-page",
     rec('<input name="ssn" value="900101-1234567" readonly>'),
     rec('<input name="ssn" value="900101-1******" readonly>')),
    ("api-overexposure",
     rec(har_entries=[{"request": {"url": "https://shop.example.com/api/me", "method": "GET"},
                       "response": {"content": {"mimeType": "application/json", "text": '{"card":"4111-1111-1111-1111"}'}}}]),
     rec(har_entries=[{"request": {"url": "https://shop.example.com/api/me", "method": "GET"},
                       "response": {"content": {"mimeType": "application/json", "text": '{"card":"4111-****-****-1111"}'}}}])),
    ("directory-listing", rec("<title>Index of /files/</title><h1>Index of /files/</h1>", title="Index of /files/"),
     rec("<title>자료실</title><h1>자료실</h1>", title="자료실")),
    ("admin-exposed",
     rec('<form><input name="id"><input type="password" name="pw"></form>', url="https://shop.example.com/admin"),
     rec('<form><input name="id"><input type="password" name="pw"></form>', url="https://shop.example.com/login")),
    ("public-page-pii", rec("<p>연락처 010-9876-5432</p>"), rec("<p>고객센터 1588-0000</p>")),
    ("marketing-prechecked",
     rec('<form><label><input type="checkbox" checked> 마케팅 정보 수신에 동의합니다</label></form>'),
     rec('<form><label><input type="checkbox"> 마케팅 정보 수신에 동의합니다</label></form>')),
    ("ssn-collected",
     rec('<form method="post"><label>주민등록번호 <input name="jumin"></label></form>'),
     rec('<form method="post"><label>생년월일 <input name="birth"></label></form>')),
    ("token-in-storage", rec(storage={"localStorage": {"access_token": "eyJ.x.y"}, "sessionStorage": {}}),
     rec(storage={"localStorage": {"theme": "dark"}, "sessionStorage": {}})),
]


@pytest.mark.parametrize("key,positive,negative", CASES, ids=[c[0] for c in CASES])
def test_page_detector_positive_and_negative(key, positive, negative):
    assert key in keys(ac.check_page(positive))
    assert key not in keys(ac.check_page(negative))


def test_logged_in_pages_are_not_public_pii():
    assert "public-page-pii" not in keys(ac.check_page(rec("<p>010-9876-5432</p>", auth="user")))


def test_privacy_policy_page_contacts_are_not_public_pii():
    r = rec("<p>개인정보 보호책임자 kim@example.com</p>", title="개인정보 처리방침", path=("개인정보 처리방침",))
    assert "public-page-pii" not in keys(ac.check_page(r))


def test_site_detectors():
    http = rec(url="http://shop.example.com/", cookies=[{"name": "SID", "httpOnly": False, "secure": False}])
    https = rec(url="https://shop.example.com/", cookies=[{"name": "SID", "httpOnly": True, "secure": True}])
    assert {"no-https", "session-cookie-flags"} <= keys(ac.check_site([http]))
    assert not {"no-https", "session-cookie-flags"} & keys(ac.check_site([https]))
    named = rec('<footer><a href="/privacy">개인정보 보호정책</a></footer>')
    ok = rec('<footer><a href="/privacy">개인정보 처리방침</a></footer>')
    assert "privacy-policy-name" in keys(ac.check_site([named]))
    assert "privacy-policy-name" not in keys(ac.check_site([ok]))


def test_candidates_reference_known_criteria():
    criteria = load_criteria()
    for key, ids in ac.ITEM_IDS.items():
        for cid in ids:
            assert cid in criteria, (key, cid)


# ── 데모 대조 ──
@pytest.fixture(scope="module")
def demo_candidates(demo_server, tmp_path_factory):
    out = tmp_path_factory.mktemp("outside")
    state = out / "session.json"
    session.run_login(demo_server.base, "url:/faq", state, out / "w", headless=True, start_url=demo_server.login_url())
    mm = load_menu_map(DEMO_DIR / "menu_expected.json")
    for m in mm["menus"]:
        m["url"] = demo_server.url(m["url"])
    for i, probe in enumerate(("/admin", "/files/"), 1):  # 확정 단계에서 사용자가 추가하는 숨은 경로
        mm["menus"].append({"id": f"P{i:02d}", "path": ["숨은 경로", probe], "url": demo_server.url(probe),
                            "status": "visit", "auth": "none"})
    intake = {"target_url": demo_server.base, "allowed_domains": ["127.0.0.1"]}
    work = tmp_path_factory.mktemp("work")
    with sync_playwright() as p:
        browser = p.chromium.launch()
        collect(browser, state, intake, mm, out / "raw", work, delay_s=0)
        browser.close()
    return mm, ac.run(out / "raw", mm)


def test_demo_auto_vulns_are_all_candidates(demo_candidates, demo_server):
    mm, cands = demo_candidates
    url_of = {m["id"]: m["url"] for m in mm["menus"]}
    expected = json.loads((DEMO_DIR / "expected.json").read_text(encoding="utf-8"))
    missing = []
    for v in expected["vulns"]:
        if not v["auto"]:
            continue
        want = "COMMON" if v["menu_url"] == "COMMON" else demo_server.url(v["menu_url"])
        hit = [c for c in cands if (c["menu"] if c["menu"] == "COMMON" else url_of[c["menu"]]) == want
               and set(c["item_ids"]) & set(v["item_ids"])]
        if not hit:
            missing.append(v["key"])
    assert missing == []


def test_demo_safe_pages_have_no_candidates(demo_candidates, demo_server):
    mm, cands = demo_candidates
    url_of = {m["id"]: m["url"] for m in mm["menus"]}
    expected = json.loads((DEMO_DIR / "expected.json").read_text(encoding="utf-8"))
    safe = {demo_server.url(p) for p in expected["safe_pages"]}
    assert [c for c in cands if c["menu"] != "COMMON" and url_of[c["menu"]] in safe] == []


def test_candidates_carry_evidence_and_box_targets(demo_candidates):
    _, cands = demo_candidates
    for c in cands:
        assert c["evidence"], c["key"]
        assert c["verdict_hint"] in ("취약", "권고")


FULL_VALUES = ("900101-1234567", "4111-1111-1111-1111", "kim@example.com", "010-9876-5432")


@pytest.mark.parametrize("r", [
    rec("<!-- debug: kim@example.com 010-9876-5432 --><p>x</p>"),
    rec('<input name="ssn" value="900101-1234567"><p>카드 4111-1111-1111-1111</p>'),
    rec("<p>연락처 010-9876-5432, kim@example.com</p>"),
])
def test_evidence_never_carries_full_personal_values(r):
    text = json.dumps(ac.check_page(r), ensure_ascii=False)
    assert not [v for v in FULL_VALUES if v in text]
