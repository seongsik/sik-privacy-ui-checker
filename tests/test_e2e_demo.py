"""데모 E2E(설계서 8-3, 검수 조건 2): 스킬 절차 전체를 데모몰에 돌린다.

사람이 하는 일은 이렇게 대신한다: 로그인(GET 로그인 주소), 메뉴맵 확인(menumap 결과 + 숨은 경로 추가),
능동 시험 승인·입력(driver), 판정(자동 후보를 기준 문구로 그대로 판정 — Claude 의 검토 품질은 이 테스트 밖).
"""
import json
from pathlib import Path

import pytest
from PIL import Image
from playwright.sync_api import sync_playwright

from scripts import auto_checks, mask, session
from scripts.active_test import run_active_test
from scripts.collect import collect
from scripts.criteria import load_criteria
from scripts.menumap import build_menu_map
from scripts.models import COMMON_MENU, load_findings
from scripts.report.build_report import build_report
from scripts.verify_report import verify_report

DEMO_DIR = Path(__file__).resolve().parents[1] / "skills" / "privacy-ui-check" / "demo"
FULL_PII = ("900101-1234567", "4111-1111-1111-1111", "hong@example.com", "010-1234-5678",
            "010-9876-5432", "younghee@example.com", "110-123-456789")


def draft_findings(cands, criteria, menu_map):
    """자동 후보 → findings 초안(기준 항목의 제목·유형·위험도·조치방안·근거를 그대로 쓴다)."""
    home = next(m["id"] for m in menu_map["menus"] if m["path"] == ["홈"])
    out = []
    for c in cands:
        item = criteria[c["item_ids"][0]]
        shot_menu = home if c["menu"] == COMMON_MENU else c["menu"]
        selectors = c["boxes"] or [{"selector": "h1", "label": "해당 화면"}]
        out.append({
            "no": None, "item_ids": c["item_ids"], "verdict": c["verdict_hint"], "risk": item["default_risk"]
            if c["verdict_hint"] == "취약" else "하", "category": item["category"], "title": item["title"],
            "menu": c["menu"], "url": next(m["url"] for m in menu_map["menus"] if m["id"] == shot_menu),
            "params": [], "inputs": "-", "_shot_menu": shot_menu, "_selectors": selectors,
            "problems": [{"n": i, "text": c["evidence"][min(i - 1, len(c["evidence"]) - 1)]}
                         for i in range(1, len(selectors) + 1)],
            "remediation": item["remediation"], "basis": "; ".join(item["basis"]) + f" ({item['strength']})",
            "evidence_refs": [], "active_test": None,
        })
    return out


@pytest.fixture(scope="module")
def run(demo_server, tmp_path_factory):
    demo_server.reset()
    root = tmp_path_factory.mktemp("e2e")
    work, outside = root / "demo_20261106", root / "demo_20261106_raw"
    work.mkdir()
    state = outside / "session.json"
    intake = {"target_url": demo_server.base, "allowed_domains": ["127.0.0.1"],
              "approval": {"approver": "데모 승인자", "scope": "데모몰 전체", "period": "2026-11-06"},
              "inspector": "E2E", "account_role": "일반 회원 테스트 계정",
              "conditions": {"has_test_account": True, "public_system": False}}
    (work / "intake.json").write_text(json.dumps(intake, ensure_ascii=False), encoding="utf-8")

    # 1. 로그인(사용자 대신 GET 로그인 주소) 2. 메뉴맵 + 사용자 확인(숨은 경로 추가)
    session.run_login(demo_server.base, "url:/faq", state, work, headless=True, start_url=demo_server.login_url())
    with sync_playwright() as p:
        browser = p.chromium.launch()
        mm = build_menu_map(session.open_context(browser, state), intake, delay_s=0)
        for i, probe in enumerate(("/admin", "/files/"), 1):
            mm["menus"].append({"id": f"M90-{i:02d}", "path": ["숨은 경로", probe], "url": demo_server.url(probe),
                                "status": "visit", "auth": "none"})
        menu_map = {"menus": mm["menus"], "deny_patterns": mm["deny_patterns"]}
        (work / "menu_map.json").write_text(json.dumps(menu_map, ensure_ascii=False), encoding="utf-8")

        # 3. 수집 4. 자동 후보 → 판정 초안
        collect(browser, state, intake, menu_map, outside / "raw", work, delay_s=0)
        cands = auto_checks.run(outside / "raw", menu_map)
        findings = draft_findings(cands, load_criteria(), menu_map)

        # 5. 능동 시험(승인 + 사용자 입력 대신 driver): 이용자 로그인 실패 제한
        login_menu = next(m for m in menu_map["menus"] if m["url"] == demo_server.url("/login"))

        def wrong_logins(page):
            for _ in range(6):
                page.fill('input[name="id"]', "hong")
                page.fill('input[name="pw"]', "wrong-pass")
                page.click('form[action="/login/submit"] button')
                page.wait_for_load_state()
                page.goto(demo_server.url("/login"))
        record = run_active_test(
            browser, None, intake, menu_map, item_id="SAFE-05-06-01",
            approval={"text": "테스트 계정 hong 으로 틀린 비밀번호를 최대 6회 입력한다", "approved_by": "데모 승인자"},
            start_url=demo_server.url("/login"), targets=[("GET", demo_server.url("/login/submit"))],
            max_attempts=6, raw_dir=outside / "raw", workdir=work, driver=wrong_logins)
        item = load_criteria()["SAFE-05-06-01"]
        if not any("잠금" in o["text"] for o in record["observations"]):
            findings.append({
                "no": None, "item_ids": ["SAFE-05-06-01"], "verdict": "취약", "risk": "상", "category": item["category"],
                "title": item["title"], "menu": login_menu["id"], "url": login_menu["url"], "params": ["pw"],
                "inputs": "테스트 계정으로 틀린 비밀번호 6회(값은 기록하지 않음)", "_shot_menu": login_menu["id"],
                "_selectors": [{"selector": "form", "label": "로그인 폼"}],
                "problems": [{"n": 1, "text": "6회 연속 실패 후에도 잠금·지연 없이 계속 시도할 수 있음"}],
                "remediation": item["remediation"], "basis": "; ".join(item["basis"]), "evidence_refs": [],
                "active_test": record})

        # 번호 박스: 화면마다 필요한 요소를 모아 그 화면만 다시 수집
        boxes: dict[str, list] = {}
        for f in findings:
            for s in f["_selectors"]:
                lst = boxes.setdefault(f["_shot_menu"], [])
                if s["selector"] not in [b["selector"] for b in lst]:
                    lst.append({"n": len(lst) + 1, "selector": s["selector"], "label": s["label"]})
        collect(browser, state, intake, menu_map, outside / "raw", work, delay_s=0, only=list(boxes), boxes=boxes)
        browser.close()

    # 7. 가림·번호 박스
    shots_checked = []
    for i, f in enumerate(findings, 1):
        meta = json.loads((outside / "raw" / f["_shot_menu"] / "meta.json").read_text(encoding="utf-8"))
        wanted = [s["selector"] for s in f.pop("_selectors")]
        mine = [dict(b, n=k) for k, sel in enumerate(wanted, 1) for b in meta["box_rects"] if b["selector"] == sel]
        shot = mask.render_shot(outside / "raw" / f["_shot_menu"], work / "img", f"F{i:02d}_1",
                                preview_dir=outside / "preview", boxes=mine)
        f["shots"] = [shot]
        shots_checked.append((f.pop("_shot_menu"), shot))
    (work / "findings.json").write_text(json.dumps({"findings": findings, "others": [
        {"item_id": "SAFE-04-01-01", "verdict": "점검불가", "reason": "서면 항목", "request": "내부 관리계획 최신본"}]},
        ensure_ascii=False), encoding="utf-8")

    # 8. 리포트·검증
    report = build_report(work, date="2026-11-06")
    code, problems = verify_report(report, outside / "evidence")
    return {"demo": demo_server, "menu_map": menu_map, "cands": cands, "report": report, "verify": (code, problems),
            "writes": demo_server.writes(), "outside": outside, "work": work, "shots": shots_checked}


def test_no_write_reached_the_server(run):
    assert run["writes"] == []


def test_every_auto_detectable_planted_flaw_is_found(run):
    demo, mm, cands = run["demo"], run["menu_map"], run["cands"]
    url_of = {m["id"]: m["url"] for m in mm["menus"]}
    expected = json.loads((DEMO_DIR / "expected.json").read_text(encoding="utf-8"))
    missing = []
    for v in expected["vulns"]:
        if not v["auto"]:
            continue
        want = COMMON_MENU if v["menu_url"] == COMMON_MENU else demo.url(v["menu_url"])
        if not any((c["menu"] if c["menu"] == COMMON_MENU else url_of[c["menu"]]) == want
                   and set(c["item_ids"]) & set(v["item_ids"]) for c in cands):
            missing.append(v["key"])
    assert missing == []


def test_safe_pages_have_no_findings(run):
    demo, mm = run["demo"], run["menu_map"]
    url_of = {m["id"]: m["url"] for m in mm["menus"]}
    expected = json.loads((DEMO_DIR / "expected.json").read_text(encoding="utf-8"))
    safe = {demo.url(p) for p in expected["safe_pages"]}
    findings = load_findings(run["work"] / "findings.json")["findings"]
    assert [f["title"] for f in findings if f["menu"] != COMMON_MENU and url_of[f["menu"]] in safe] == []


def test_active_lockout_finding_is_recorded(run):
    findings = load_findings(run["work"] / "findings.json")["findings"]
    act = [f for f in findings if f["active_test"]]
    assert act and act[0]["active_test"]["attempts"] == 6


def test_report_verifies_clean(run):
    assert run["verify"] == (0, [])


def test_report_carries_no_full_personal_values(run):
    html = run["report"].read_text(encoding="utf-8")
    assert [v for v in FULL_PII if v in html] == []


def test_every_measured_pii_region_is_masked_in_report_images(run):
    for menu_id, shot in run["shots"]:
        raw_dir = run["outside"] / "raw" / menu_id
        meta = json.loads((raw_dir / "meta.json").read_text(encoding="utf-8"))
        before = Image.open(raw_dir / "screenshot.png").convert("RGB")
        after = Image.open(run["work"] / shot["file"]).convert("RGB")
        for r in meta["pii_rects"]:
            box = (r["x"], r["y"], r["x"] + r["w"], r["y"] + r["h"])
            assert before.crop(box).tobytes() != after.crop(box).tobytes(), (menu_id, r)


def test_free_text_name_on_board_is_measured_for_masking(run):
    board = next(m["id"] for m in run["menu_map"]["menus"] if m["url"] == run["demo"].url("/board/1"))
    meta = json.loads((run["outside"] / "raw" / board / "meta.json").read_text(encoding="utf-8"))
    assert any(r["kind"] == "name" for r in meta["pii_rects"])  # '받는 분 김영희'
