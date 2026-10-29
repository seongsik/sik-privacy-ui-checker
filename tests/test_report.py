"""리포트 빌더(설계서 5장) 테스트."""
import json
import re
from pathlib import Path

import pytest
from PIL import Image
from playwright.sync_api import sync_playwright

from scripts.report.build_report import DETAIL_FIELDS, build_report

TEMPLATES = Path(__file__).resolve().parents[1] / "skills" / "privacy-ui-check" / "templates"

MENU_MAP = {"menus": [
    {"id": "M01-01", "path": ["로그인"], "url": "https://shop.example.com/login", "status": "visit", "auth": "none"},
    {"id": "M02-01", "path": ["마이페이지", "회원정보 수정"], "url": "https://shop.example.com/my/profile", "status": "visit", "auth": "user"},
    {"id": "M02-02", "path": ["마이페이지", "주문내역"], "url": "https://shop.example.com/my/orders", "status": "visit", "auth": "user"},
    {"id": "M03-01", "path": ["게시판", "문의 2"], "url": "https://shop.example.com/board/2", "status": "sample", "sample_of": "M02-02", "auth": "none"},
    {"id": "M09-01", "path": ["관리"], "url": "https://shop.example.com/admin", "status": "exclude", "reason": "범위 밖", "auth": "admin"},
], "deny_patterns": []}


def finding(title, menu, risk, verdict="취약", shots=1):
    return {
        "no": None, "item_ids": ["SAFE-07-01-02"], "verdict": verdict, "risk": risk, "category": "암호화·전송",
        "title": title, "menu": menu, "url": "https://shop.example.com/login", "params": ["pw"],
        "inputs": "테스트 계정 로그인(비밀번호 값은 기록하지 않음)",
        "shots": [{"file": f"img/{re.sub(r'[^0-9A-Za-z가-힣]', '', title)[:6]}_{i}.png", "width": 400, "height": 200,
                   "boxes": [{"n": 1, "left": 10, "top": 10, "width": 20, "height": 20}]} for i in range(shots)],
        "problems": [{"n": 1, "text": f"{title} 문제"}], "remediation": "POST 본문으로 보낸다.",
        "basis": "안전성 확보조치 기준 제7조제1항(의무)", "evidence_refs": [], "active_test": None,
    }


FINDINGS = [
    finding("공통 헤더 미설정", "COMMON", "중", "권고"),
    finding("회원정보 주민번호 노출", "M02-01", "상"),
    finding("로그인 권고", "M01-01", "상", "권고"),
    finding("로그인 GET 전송", "M01-01", "상", shots=2),
    finding("<script>window.__xss=1</script>", "M01-01", "하"),
]
ORDER = ["로그인 GET 전송", "로그인 권고", "<script>window.__xss=1</script>", "회원정보 주민번호 노출", "공통 헤더 미설정"]


@pytest.fixture(scope="module")
def workdir(tmp_path_factory):
    w = tmp_path_factory.mktemp("work")
    (w / "intake.json").write_text((TEMPLATES / "intake.example.json").read_text(encoding="utf-8"), encoding="utf-8")
    (w / "menu_map.json").write_text(json.dumps(MENU_MAP, ensure_ascii=False), encoding="utf-8")
    others = [{"item_id": "SAFE-04-01-01", "verdict": "점검불가", "reason": "서면 항목", "request": "내부 관리계획 최신본"},
              {"item_id": "SAFE-14-01-01", "verdict": "해당없음", "reason": "공공시스템 아님"},
              {"item_id": "SAFE-05-06-02", "verdict": "양호", "reason": "5회 실패 후 잠금"}]
    (w / "findings.json").write_text(json.dumps({"findings": FINDINGS, "others": others}, ensure_ascii=False),
                                     encoding="utf-8")
    (w / "img").mkdir()
    for f in FINDINGS:
        for s in f["shots"]:
            Image.new("RGB", (400, 200), "white").save(w / s["file"])
    return w


@pytest.fixture(scope="module")
def built(workdir):
    path = build_report(workdir, date="2026-11-06")
    return path, path.read_text(encoding="utf-8")


def test_output_name_and_single_file(built):
    path, html = built
    assert path.name == "REPORT_shop.example.com_20261106.html"
    assert not re.search(r'(src|href)="https?://', html)
    assert html.count("data:image/png;base64,") >= 6


def test_overview_has_six_blocks(built):
    _, html = built
    for block in ("ov-site", "ov-standards", "ov-summary", "ov-list", "ov-coverage", "ov-others"):
        assert f'id="{block}"' in html, block
    assert "내부 관리계획 최신본" in html and "공공시스템 아님" in html


def test_one_detail_page_per_finding_with_all_fields(built):
    _, html = built
    pages = re.findall(r'<section class="page detail" id="(V-\d{3})"', html)
    assert pages == ["V-001", "V-002", "V-003", "V-004", "V-005"]
    for label in DETAIL_FIELDS:
        assert html.count(f">{label}<") >= 5, label


def test_toc_order_is_menu_then_risk_then_verdict(built):
    _, html = built
    toc = html[html.index('<nav class="toc"'):html.index("</nav>")]
    titles = [re.sub(r"<.*?>", "", t) for t in re.findall(r'<a class="toc-item[^"]*" href="#V-\d{3}">.*?<span class="t">(.*?)</span>', toc)]
    import html as h
    assert [h.unescape(t) for t in titles] == ORDER
    assert toc.index("공통(전 화면)") > toc.index("회원정보 수정")


def test_site_text_is_escaped(built):
    _, html = built
    assert "<script>window.__xss=1</script>" not in html
    assert "&lt;script&gt;window.__xss=1&lt;/script&gt;" in html


@pytest.fixture(scope="module")
def page(built):
    path, _ = built
    with sync_playwright() as p:
        b = p.chromium.launch()
        pg = b.new_page(viewport={"width": 1440, "height": 900})
        pg.goto(path.as_uri())
        yield pg
        b.close()


def visible_pages(page):
    return page.eval_on_selector_all("section.page", "els => els.filter(e => e.offsetParent !== null).map(e => e.id)")


def test_overview_shows_first(page):
    assert visible_pages(page) == ["overview"]
    assert page.evaluate("() => window.__xss") is None


def test_hash_and_keyboard_navigation(page):
    page.evaluate("location.hash = '#V-002'")
    page.wait_for_timeout(100)
    assert visible_pages(page) == ["V-002"]
    page.keyboard.press("ArrowRight")
    page.wait_for_timeout(100)
    assert visible_pages(page) == ["V-003"]
    page.click("#V-003 a.prev")
    page.wait_for_timeout(100)
    assert visible_pages(page) == ["V-002"]


def test_overview_list_rows_link_to_details(page):
    page.evaluate("location.hash = '#overview'")
    page.click("#ov-list tbody tr >> nth=3")
    page.wait_for_timeout(100)
    assert visible_pages(page) == ["V-004"]


def test_image_zoom_opens_and_closes(page):
    page.evaluate("location.hash = '#V-001'")
    page.click("#V-001 figure img >> nth=0")
    assert page.is_visible("#zoom")
    page.keyboard.press("Escape")
    assert not page.is_visible("#zoom")
