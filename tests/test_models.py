"""데이터 모델(intake·menu_map·findings) 검증과 정렬 규칙 테스트. 설계서 4-2, 4-3."""
import copy
import json

import pytest

from scripts.models import (
    ModelError,
    load_findings,
    load_intake,
    load_menu_map,
    sort_and_number,
    validate_findings,
)

INTAKE = {
    "target_url": "https://shop.example.com",
    "approval": {"approver": "정보보호팀장", "scope": "회원 영역 전체", "period": "2026-11-02~2026-11-06"},
    "conditions": {"users_over_1m": False, "public_system": False},
}

MENU_MAP = {
    "menus": [
        {"id": "M01-01", "path": ["로그인"], "url": "/login", "status": "visit", "auth": "none"},
        {"id": "M03-02", "path": ["마이페이지", "회원정보 수정"], "url": "/my/profile", "status": "visit", "auth": "user"},
        {"id": "M04-01", "path": ["게시판", "상세"], "url": "/board/1", "status": "sample", "sample_of": "M04-00", "auth": "user"},
        {"id": "M09-01", "path": ["관리"], "url": "/admin", "status": "exclude", "reason": "범위 밖", "auth": "admin"},
    ],
    "deny_patterns": ["/logout"],
}


def finding(title, menu, risk, verdict="취약", boxes=1, problems=1):
    return {
        "no": None,
        "item_ids": ["SAFE-07-01-02"],
        "verdict": verdict,
        "risk": risk,
        "category": "암호화·전송",
        "title": title,
        "menu": menu,
        "url": "https://shop.example.com/login",
        "params": [],
        "inputs": "테스트 계정 로그인(비밀번호 값은 기록하지 않음)",
        "shots": [{"file": "img/a.png", "boxes": [{"n": i + 1, "left": 1, "top": 1, "width": 1, "height": 1} for i in range(boxes)]}],
        "problems": [{"n": i + 1, "text": "문제"} for i in range(problems)],
        "remediation": "조치",
        "basis": "근거(의무)",
        "evidence_refs": [],
        "active_test": None,
    }


def test_intake_requires_target_url():
    bad = copy.deepcopy(INTAKE)
    del bad["target_url"]
    with pytest.raises(ModelError, match="target_url"):
        load_intake(bad)


def test_intake_requires_approval_fields():
    bad = copy.deepcopy(INTAKE)
    bad["approval"].pop("approver")
    with pytest.raises(ModelError, match="approver"):
        load_intake(bad)


def test_intake_rejects_non_http_url():
    bad = dict(INTAKE, target_url="ftp://shop.example.com")
    with pytest.raises(ModelError, match="target_url"):
        load_intake(bad)


def test_intake_loads_from_file(tmp_path):
    p = tmp_path / "intake.json"
    p.write_text(json.dumps(INTAKE, ensure_ascii=False), encoding="utf-8")
    assert load_intake(p)["target_url"] == INTAKE["target_url"]


def test_menu_map_rejects_unknown_status():
    bad = copy.deepcopy(MENU_MAP)
    bad["menus"][0]["status"] = "maybe"
    with pytest.raises(ModelError, match="status"):
        load_menu_map(bad)


def test_menu_map_requires_reason_for_exclude():
    bad = copy.deepcopy(MENU_MAP)
    bad["menus"][3].pop("reason")
    with pytest.raises(ModelError, match="reason"):
        load_menu_map(bad)


def test_menu_map_requires_sample_of_for_sample():
    bad = copy.deepcopy(MENU_MAP)
    bad["menus"][2]["sample_of"] = None
    with pytest.raises(ModelError, match="sample_of"):
        load_menu_map(bad)


def test_sort_orders_by_menu_then_risk_then_verdict():
    items = [
        finding("공통 헤더", "COMMON", "상"),
        finding("수정 화면 하", "M03-02", "하"),
        finding("로그인 권고 상", "M01-01", "상", verdict="권고"),
        finding("로그인 중", "M01-01", "중"),
        finding("로그인 취약 상", "M01-01", "상"),
    ]
    out = sort_and_number(items, MENU_MAP)
    assert [f["title"] for f in out] == [
        "로그인 취약 상",
        "로그인 권고 상",
        "로그인 중",
        "수정 화면 하",
        "공통 헤더",
    ]
    assert [f["no"] for f in out] == ["V-001", "V-002", "V-003", "V-004", "V-005"]


def test_sort_rejects_unknown_menu():
    with pytest.raises(ModelError, match="M77"):
        sort_and_number([finding("x", "M77-01", "상")], MENU_MAP)


def test_validate_warns_when_boxes_and_problems_differ():
    data = {"findings": [finding("x", "M01-01", "상", boxes=2, problems=1)], "others": []}
    warnings = validate_findings(data)
    assert any("박스" in w and "x" in w for w in warnings)


def test_validate_passes_matching_boxes_and_problems():
    data = {"findings": [finding("x", "M01-01", "상", boxes=2, problems=2)], "others": []}
    assert validate_findings(data) == []


@pytest.mark.parametrize("text", ["pw=Secret123!", "비밀번호: qwer1234", "password = hunter2"])
def test_validate_rejects_password_values_in_inputs(text):
    f = finding("x", "M01-01", "상")
    f["inputs"] = text
    with pytest.raises(ModelError, match="inputs"):
        validate_findings({"findings": [f], "others": []})


def test_validate_rejects_detail_verdict_outside_vulnerable_or_advice():
    f = finding("x", "M01-01", "상", verdict="양호")
    with pytest.raises(ModelError, match="verdict"):
        validate_findings({"findings": [f], "others": []})


def test_validate_rejects_unknown_risk_and_category():
    f = finding("x", "M01-01", "최상")
    with pytest.raises(ModelError, match="risk"):
        validate_findings({"findings": [f], "others": []})
    f = finding("x", "M01-01", "상")
    f["category"] = "기타"
    with pytest.raises(ModelError, match="category"):
        validate_findings({"findings": [f], "others": []})


def test_validate_rejects_others_with_detail_verdict():
    data = {"findings": [], "others": [{"item_id": "SAFE-04-01-01", "verdict": "취약", "reason": "x"}]}
    with pytest.raises(ModelError, match="others"):
        validate_findings(data)


def test_example_templates_are_valid():
    from pathlib import Path

    tdir = Path(__file__).resolve().parents[1] / "skills" / "privacy-ui-check" / "templates"
    load_intake(tdir / "intake.example.json")
    data = load_findings(tdir / "findings.example.json")
    assert data["findings"]
