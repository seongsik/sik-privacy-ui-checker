"""docs/reference/criteria/*.md 초안을 스킬의 YAML 점검 기준(설계서 4-1)으로 바꾼다.

category·strength·default_risk 는 아래 규칙으로 1차 값을 정한다. 사용자 확인 후 고칠 값은
OVERRIDES 에 적고 다시 실행한다. 사용법: python tools/convert_criteria.py
"""
from __future__ import annotations

import re
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "docs" / "reference" / "criteria"
DST = ROOT / "skills" / "privacy-ui-check" / "references" / "criteria"

# 출력 파일 → 초안 파일
SOURCES = {
    "safe.yaml": ["safe-03-06B.md", "safe-07-18.md"],
    "pipa.yaml": ["pipa.md"],
    "netact.yaml": ["netact.md"],
    "std.yaml": ["std.md"],
}

FIELDS = {
    "근거": "basis",
    "요구사항": "requirement",
    "적용조건": "applies_when",
    "점검유형": "type",
    "점검위치": "location",
    "점검방법": "method",
    "양호기준": "pass",
    "취약기준": "fail",
    "조치방안": "remediation",
    "증적": "evidence",
    "비고": "notes",
}
FIELD_RE = re.compile(r"^- (" + "|".join(FIELDS) + r"):\s?(.*)$")
HEAD_RE = re.compile(r"^### ((?:SAFE|PIPA|NET|STD)-[0-9A-Z-]+)\s+(.*)$")

# 관찰·능동 항목의 취약점 유형(ID 앞부분 정규식, 위에서부터 처음 맞는 것). 서면은 모두 관리체계(서면).
CATEGORY_RULES = [
    (r"^SAFE-06-03-", "노출·출력"),
    (r"^SAFE-(05|06|06B|16|17)-", "인증·접근통제"),
    (r"^SAFE-07-", "암호화·전송"),
    (r"^SAFE-12-", "노출·출력"),
    (r"^PIPA-34B-", "노출·출력"),
    (r"^PIPA-29-", "인증·접근통제"),
    (r"^PIPA-(20B|26|30|31|31B|32B|35|35B|36|37|37B|38)-", "처리방침·권리보장"),
    (r"^PIPA-", "동의·고지"),
    (r"^NET-50", "광고성 정보"),
    (r"^NET-(22B|23B|23E|44E)-", "동의·고지"),
    (r"^NET-32E-", "처리방침·권리보장"),
    (r"^NET-(42|42B|44B|44L|44N)-", "노출·출력"),
    (r"^NET-", "인증·접근통제"),
    (r"^STD-(04|06|07|12|13)-", "동의·고지"),
    (r"^STD-", "처리방침·권리보장"),
]

# 의무 항목의 기본 위험도(유형별). 권고·외부참고와 서면은 하.
RISK_BY_CATEGORY = {
    "인증·접근통제": "상",
    "암호화·전송": "상",
    "노출·출력": "중",
    "동의·고지": "중",
    "처리방침·권리보장": "중",
    "광고성 정보": "중",
    "관리체계(서면)": "하",
}

# 규칙으로 정한 값을 항목별로 덮어쓴다. 사용자 확인 결과도 여기에 적는다.
OVERRIDES: dict[str, dict] = {
    # 노출·출력 중 바로 노출되는 것은 상
    "SAFE-06-03-01": {"default_risk": "상"},
    "SAFE-06-03-02": {"default_risk": "상"},
    "SAFE-06-03-03": {"default_risk": "상"},
    "SAFE-06-03-04": {"default_risk": "상"},
    "SAFE-06-03-05": {"default_risk": "상"},
    "SAFE-12-01-03": {"default_risk": "상"},
    "PIPA-34B-01": {"default_risk": "상"},
    # 인증·접근통제 중 다른 조건이 겹쳐야 노출되는 것은 중
    "SAFE-05-01-01": {"default_risk": "중"},
    "SAFE-05-04-01": {"default_risk": "중"},
    "SAFE-05-05-03": {"default_risk": "중"},
    "SAFE-05-06-03": {"default_risk": "중"},
    "SAFE-06-04-01": {"default_risk": "중"},
    "SAFE-16-03-01": {"default_risk": "중"},
    "SAFE-17-02-01": {"default_risk": "중"},
    "NET-45-01": {"default_risk": "중"},
    "NET-47-02": {"default_risk": "하"},
    "NET-47D-01": {"default_risk": "중"},
    "NET-47D-02": {"default_risk": "중"},
    # 주민등록번호 처리는 상
    "PIPA-24B-01": {"default_risk": "상"},
    "PIPA-24B-02": {"default_risk": "상"},
    "NET-23B-01": {"default_risk": "상"},
}


def parse_blocks(text: str) -> list[dict]:
    items, cur, field = [], None, None
    for line in text.splitlines():
        head = HEAD_RE.match(line)
        if head:
            cur = {"id": head.group(1), "title": head.group(2).strip()}
            items.append(cur)
            field = None
            continue
        if cur is None:
            continue
        if line.startswith("## ") or line.strip() == "---":
            cur, field = None, None
            continue
        m = FIELD_RE.match(line)
        if m:
            field = FIELDS[m.group(1)]
            cur[field] = m.group(2).strip()
        elif field and line.strip():
            cur[field] += "\n" + line.strip()
    return items


def _split(value: str, sep: str) -> list[str]:
    if value in ("", "-"):
        return []
    return [v.strip() for v in value.split(sep) if v.strip()]


def strength_of(notes: str) -> str:
    head = notes[:60]
    if "외부 참고" in head and "법령 의무" not in head:
        return "외부참고"
    if "법령 의무" in head or "의무" in head.split(".")[0]:
        return "의무"
    if any(k in head for k in ("권고", "안내서 예시", "작성지침")):
        return "권고"
    return "의무"


def normalize(raw: dict) -> dict:
    type_text = raw.get("type", "")
    kind = type_text[:2]
    kind = {"관찰": "관찰", "능동": "능동", "서면": "서면"}[kind]
    item = {
        "id": raw["id"],
        "title": raw["title"],
        "basis": _split(raw.get("basis", ""), ";"),
        "requirement": raw.get("requirement", ""),
        "applies_when": raw.get("applies_when", ""),
        "type": kind,
        "type_note": type_text[len(type_text.split(".")[0]) + 1 :].strip() or None,
        "location": _split(raw.get("location", ""), ","),
        "method": raw.get("method", ""),
        "pass": raw.get("pass", ""),
        "fail": raw.get("fail", ""),
        "remediation": raw.get("remediation", ""),
        "evidence": raw.get("evidence", ""),
        "notes": raw.get("notes", ""),
    }
    if kind == "서면":
        item["category"] = "관리체계(서면)"
    else:
        item["category"] = next(c for pat, c in CATEGORY_RULES if re.match(pat, item["id"]))
    item["strength"] = strength_of(item["notes"])
    if kind == "서면" or item["strength"] != "의무":
        item["default_risk"] = "하"
    else:
        item["default_risk"] = RISK_BY_CATEGORY[item["category"]]
    item.update(OVERRIDES.get(item["id"], {}))
    return item


def main() -> None:
    DST.mkdir(parents=True, exist_ok=True)
    every = []
    for out, sources in SOURCES.items():
        items = []
        for name in sources:
            items += [normalize(b) for b in parse_blocks((SRC / name).read_text(encoding="utf-8"))]
        (DST / out).write_text(
            yaml.safe_dump(items, allow_unicode=True, sort_keys=False, width=1000), encoding="utf-8"
        )
        every += items
        print(f"{out}: {len(items)}")
    print(f"total: {len(every)}")
    write_review_table(every)


def write_review_table(items: list[dict]) -> None:
    """사용자 확인용 분류표. 변환할 때마다 다시 만든다(손으로 고치지 않고 OVERRIDES 를 고친다)."""
    rows = [
        "# 점검 기준 분류표 (사용자 확인용)",
        "",
        "`tools/convert_criteria.py`가 만든다. 값을 바꾸려면 이 파일이 아니라 스크립트의 `OVERRIDES`를 고치고 다시 실행한다.",
        "서면 항목은 모두 `관리체계(서면)`·`하`라서 표에서 뺐다.",
        "",
        "| ID | 항목명 | 점검유형 | 취약점 유형 | 근거 강도 | 기본 위험도 |",
        "|---|---|---|---|---|---|",
    ]
    for c in items:
        if c["type"] != "서면":
            rows.append(
                f"| {c['id']} | {c['title']} | {c['type']} | {c['category']} | {c['strength']} | {c['default_risk']} |"
            )
    (SRC / "classification.md").write_text("\n".join(rows) + "\n", encoding="utf-8")


if __name__ == "__main__":
    main()
