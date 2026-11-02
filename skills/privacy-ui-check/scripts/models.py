"""점검 작업 폴더의 데이터 파일(intake·menu_map·findings)을 읽고 검증·정렬한다.

형식은 설계서 4-2(menu_map), 4-3(findings)과 REQ-001(intake)을 따른다.
검증 실패는 ModelError, 고칠 수 있지만 리포트 품질에 영향을 주는 것은 경고 문자열로 돌려준다.
"""
from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

DETAIL_VERDICTS = ("취약", "권고")
OTHER_VERDICTS = ("양호", "점검불가", "해당없음")
RISKS = ("상", "중", "하")
CATEGORIES = (
    "인증·접근통제",
    "암호화·전송",
    "노출·출력",
    "동의·고지",
    "처리방침·권리보장",
    "광고성 정보",
    "관리체계(서면)",
)
MENU_STATUSES = ("visit", "sample", "exclude", "blocked")
COMMON_MENU = "COMMON"

# "비밀번호: 값", "pw=값"처럼 비밀번호 계열 키 뒤에 실제 값이 붙은 경우만 잡는다.
# "비밀번호 값은 기록하지 않음" 같은 서술은 통과한다.
_SECRET_VALUE = re.compile(r"(password|passwd|pwd|pw|비밀번호|패스워드)\s*[:=]\s*\S+", re.IGNORECASE)


class ModelError(ValueError):
    """데이터 파일이 형식을 어겼다."""


def _read(src: str | Path | dict) -> dict:
    if isinstance(src, dict):
        return src
    return json.loads(Path(src).read_text(encoding="utf-8"))


def _require(obj: dict, key: str, where: str) -> Any:
    value = obj.get(key)
    if value in (None, "", [], {}):
        raise ModelError(f"{where}: '{key}' 가 없다")
    return value


def load_intake(src: str | Path | dict) -> dict:
    data = _read(src)
    url = _require(data, "target_url", "intake")
    if not re.match(r"^https?://", url):
        raise ModelError(f"intake: target_url 은 http(s) 주소여야 한다: {url}")
    approval = _require(data, "approval", "intake")
    for key in ("approver", "scope", "period"):
        _require(approval, key, "intake.approval")
    return data


def load_menu_map(src: str | Path | dict) -> dict:
    data = _read(src)
    menus = _require(data, "menus", "menu_map")
    seen = set()
    for m in menus:
        mid = _require(m, "id", "menu_map.menus")
        where = f"menu_map.menus[{mid}]"
        if mid in seen:
            raise ModelError(f"{where}: id 중복")
        seen.add(mid)
        _require(m, "path", where)
        if m.get("status") not in MENU_STATUSES:
            raise ModelError(f"{where}: status 는 {MENU_STATUSES} 중 하나: {m.get('status')}")
        if m["status"] in ("exclude", "blocked"):
            _require(m, "reason", where)
        if m["status"] == "sample":
            _require(m, "sample_of", where)
    data.setdefault("deny_patterns", [])
    return data


def load_findings(src: str | Path | dict) -> dict:
    data = _read(src)
    data.setdefault("findings", [])
    data.setdefault("others", [])
    validate_findings(data)
    return data


def _box_count(f: dict) -> int:
    return sum(len(s.get("boxes", [])) for s in f.get("shots", []))


def validate_findings(data: dict) -> list[str]:
    """형식 오류는 ModelError, 박스·문제점 불일치는 경고로 돌려준다."""
    warnings: list[str] = []
    for f in data.get("findings", []):
        title = f.get("title", "?")
        where = f"findings[{title}]"
        for key in ("item_ids", "title", "menu", "remediation", "basis"):
            _require(f, key, where)
        if f.get("verdict") not in DETAIL_VERDICTS:
            raise ModelError(f"{where}: verdict 는 {DETAIL_VERDICTS} 중 하나: {f.get('verdict')}")
        if f.get("risk") not in RISKS:
            raise ModelError(f"{where}: risk 는 {RISKS} 중 하나: {f.get('risk')}")
        if f.get("category") not in CATEGORIES:
            raise ModelError(f"{where}: category 는 7개 유형 중 하나: {f.get('category')}")
        if _SECRET_VALUE.search(f.get("inputs") or ""):
            raise ModelError(f"{where}: inputs 에 비밀번호 값을 적지 않는다")
        box_ns = sorted(b["n"] for s in f.get("shots", []) for b in s.get("boxes", []))
        problem_ns = sorted(p["n"] for p in f.get("problems", []))
        if box_ns != problem_ns:
            warnings.append(
                f"{where}: 박스 번호 {box_ns} 와 문제점 번호 {problem_ns} 가 다르다(박스 {_box_count(f)}개)"
            )
    for o in data.get("others", []):
        if o.get("verdict") not in OTHER_VERDICTS:
            raise ModelError(f"others[{o.get('item_id')}]: verdict 는 {OTHER_VERDICTS} 중 하나: {o.get('verdict')}")
    return warnings


def sort_and_number(findings: list[dict], menu_map: dict) -> list[dict]:
    """메뉴 순서 → 위험도 내림차순 → 취약·권고 순으로 정렬하고 V-001부터 번호를 매긴다."""
    order = {m["id"]: i for i, m in enumerate(menu_map["menus"])}
    order[COMMON_MENU] = len(order)
    for f in findings:
        if f["menu"] not in order:
            raise ModelError(f"findings[{f.get('title')}]: menu_map 에 없는 메뉴 {f['menu']}")
    out = sorted(
        (dict(f) for f in findings),
        key=lambda f: (order[f["menu"]], RISKS.index(f["risk"]), DETAIL_VERDICTS.index(f["verdict"]), f["title"]),
    )
    for i, f in enumerate(out, 1):
        f["no"] = f"V-{i:03d}"
    return out
