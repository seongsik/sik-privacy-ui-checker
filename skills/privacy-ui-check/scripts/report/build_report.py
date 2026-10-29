"""보안성 검토 리포트 빌더(설계서 5장).

작업 폴더의 intake.json, menu_map.json, findings.json, img/ 로 CSS·JS·이미지를 내장한 단일 HTML 을 만든다.
리포트 개요 1쪽 + 취약·권고 하나당 상세 1쪽. 좌측 목차는 사이트 메뉴 아래 위험도 내림차순(같은 위험도는 취약 → 권고),
특정 메뉴에 속하지 않는 항목은 마지막 "공통(전 화면)" 아래에 둔다. 주소 해시(#overview, #V-003)로 쪽을 바꾼다.
사이트에서 온 글자는 모두 이스케이프한다.

사용: python -m scripts.report.build_report --workdir <작업 폴더> [--date 2026-11-06]
"""
from __future__ import annotations

import argparse
import base64
import html
import json
from collections import Counter, OrderedDict
from datetime import date as _date
from pathlib import Path
from urllib.parse import urlparse

from scripts.models import (COMMON_MENU, DETAIL_VERDICTS, OTHER_VERDICTS, RISKS, load_findings, load_intake,
                            load_menu_map, sort_and_number, validate_findings)

HERE = Path(__file__).resolve().parent
DETAIL_FIELDS = ("취약점 번호", "취약점 유형", "취약점명", "위험도", "화면 메뉴명", "URL", "파라미터", "입력사항",
                 "캡처 화면", "문제점", "조치방안", "근거")
STANDARDS = [
    ("개인정보의 안전성 확보조치 기준", "개인정보보호위원회고시 제2026-9호", "2026. 7. 1. 시행"),
    ("개인정보의 안전성 확보조치 기준 안내서", "개인정보보호위원회", "2025. 11."),
    ("개인정보 보호법 · 같은 법 시행령", "법률 제21445호 · 대통령령 제36671호", "2026. 3. 10. · 2026. 9. 10."),
    ("정보통신망 이용촉진 및 정보보호 등에 관한 법률 · 시행령", "2026. 9. 29. 개정 법률 기준", ""),
    ("표준 개인정보 보호지침", "개인정보보호위원회고시 제2025-4호", "2025. 4. 11."),
]
STANDARD_NOTES = [
    "안내서(2025.11.)가 2026.10.31.~11.1. 시행으로 적은 조항(제4조①12·13호, 제5조①⑥, 제6조②, 제8조 개정분)은 "
    "현행 고시 부칙과 대조해 시행 여부를 확인해야 한다.",
]
STATUS_LABEL = {"visit": "방문", "sample": "표본(대표 화면으로 점검)", "exclude": "제외", "blocked": "접근 안 함(상태 변경 링크)"}
RISK_CLASS = {"상": "r-high", "중": "r-mid", "하": "r-low"}
e = html.escape


def _circled(n: int) -> str:
    return chr(0x2460 + n - 1) if 1 <= n <= 20 else f"({n})"


def _img_data(workdir: Path, rel: str) -> str:
    data = base64.b64encode((workdir / rel).read_bytes()).decode()
    return f"data:image/png;base64,{data}"


def _menu_label(menu_id: str, menus: dict) -> str:
    return "공통(전 화면)" if menu_id == COMMON_MENU else " › ".join(menus[menu_id]["path"])


def _badge(risk: str) -> str:
    return f'<span class="badge {RISK_CLASS[risk]}">{e(risk)}</span>'


def _toc(findings: list[dict], menus: dict) -> str:
    groups: "OrderedDict[str, list]" = OrderedDict()
    for f in findings:
        groups.setdefault(f["menu"], []).append(f)
    parts = ['<nav class="toc" aria-label="목차">', '<a class="toc-head" href="#overview">리포트 개요</a>',
             '<div class="toc-head static">검토결과 상세</div>']
    for menu_id, items in groups.items():
        parts.append(f'<div class="toc-menu">{e(_menu_label(menu_id, menus))}</div>')
        for f in items:
            parts.append(f'<a class="toc-item" href="#{f["no"]}">{_badge(f["risk"])}'
                         f'<span class="no">{f["no"]}</span><span class="t">{e(f["title"])}</span></a>')
    parts.append("</nav>")
    return "\n".join(parts)


def _overview(intake: dict, menu_map: dict, data: dict, findings: list[dict], menus: dict) -> str:
    ap = intake.get("approval", {})
    site_rows = [("대상", intake["target_url"]), ("점검 기간", ap.get("period", "")), ("점검자", intake.get("inspector", "")),
                 ("승인자", ap.get("approver", "")), ("범위", ap.get("scope", "")),
                 ("점검 계정 역할", intake.get("account_role", "")),
                 ("점검 방식", "Playwright 격리 브라우저에서 읽기 전용 수집(쓰기 요청 네트워크 차단), "
                            "능동 시험은 항목별 승인 후 사용자 직접 입력")]
    site = "".join(f"<tr><th>{e(k)}</th><td>{e(str(v))}</td></tr>" for k, v in site_rows)

    std = intake.get("standards") or [{"name": n, "edition": ed, "date": d} for n, ed, d in STANDARDS]
    std_rows = "".join(f"<tr><td>{e(s['name'])}</td><td>{e(s['edition'])}</td><td>{e(s['date'])}</td></tr>" for s in std)
    notes = "".join(f"<li>{e(n)}</li>" for n in STANDARD_NOTES)

    verdicts = Counter([f["verdict"] for f in findings] + [o["verdict"] for o in data["others"]])
    risks = Counter(f["risk"] for f in findings)
    v_cells = "".join(f'<div class="stat"><b>{verdicts.get(v, 0)}</b><span>{v}</span></div>'
                      for v in DETAIL_VERDICTS + OTHER_VERDICTS)
    r_cells = "".join(f'<div class="stat {RISK_CLASS[r]}"><b>{risks.get(r, 0)}</b><span>위험도 {r}</span></div>' for r in RISKS)

    rows = "".join(
        f'<tr data-href="#{f["no"]}"><td>{f["no"]}</td><td>{_badge(f["risk"])}</td><td>{e(f["verdict"])}</td>'
        f'<td>{e(f["category"])}</td><td>{e(f["title"])}</td><td>{e(_menu_label(f["menu"], menus))}</td></tr>'
        for f in findings)

    cov_count = Counter(m["status"] for m in menu_map["menus"])
    cov_sum = " · ".join(f"{STATUS_LABEL[s]} {cov_count.get(s, 0)}" for s in STATUS_LABEL)
    cov_rows = "".join(
        f'<tr><td>{e(" › ".join(m["path"]))}</td><td>{e(STATUS_LABEL[m["status"]])}</td>'
        f'<td>{e(m.get("reason") or (("대표: " + _menu_label(m["sample_of"], menus)) if m.get("sample_of") else ""))}</td></tr>'
        for m in menu_map["menus"])

    others = [o for o in data["others"] if o["verdict"] in ("점검불가", "해당없음")]
    other_rows = "".join(
        f'<tr><td>{e(o["item_id"])}</td><td>{e(o["verdict"])}</td><td>{e(o.get("reason", ""))}</td>'
        f'<td>{e(o.get("request", ""))}</td></tr>' for o in others)

    return f"""<section class="page" id="overview">
<h1>보안성 검토 리포트</h1><p class="sub">{e(urlparse(intake["target_url"]).hostname or "")} · 개인정보보호 점검</p>
<h2 id="ov-site">대상 사이트 정보</h2><table class="kv">{site}</table>
<h2 id="ov-standards">적용 법령·가이드</h2><table><thead><tr><th>원문</th><th>판</th><th>시행·발행</th></tr></thead>
<tbody>{std_rows}</tbody></table><ul class="notes">{notes}</ul>
<h2 id="ov-summary">결과 요약</h2><div class="stats">{v_cells}</div><div class="stats">{r_cells}</div>
<h2>취약점 목록</h2><table id="ov-list" class="list"><thead><tr><th>번호</th><th>위험도</th><th>판정</th><th>유형</th>
<th>취약점명</th><th>화면 메뉴명</th></tr></thead><tbody>{rows}</tbody></table>
<h2 id="ov-coverage">점검 커버리지</h2><p>{e(cov_sum)}</p><table><thead><tr><th>메뉴</th><th>상태</th><th>사유·비고</th></tr></thead>
<tbody>{cov_rows}</tbody></table>
<h2 id="ov-others">점검불가·해당없음</h2><table><thead><tr><th>기준 ID</th><th>판정</th><th>사유</th><th>요청 자료</th></tr></thead>
<tbody>{other_rows}</tbody></table>
</section>"""


def _detail(f: dict, i: int, findings: list[dict], menus: dict, workdir: Path) -> str:
    prev_no = findings[i - 1]["no"] if i > 0 else "overview"
    next_no = findings[i + 1]["no"] if i + 1 < len(findings) else None
    info = [("취약점 번호", f["no"]), ("취약점 유형", f["category"]), ("취약점명", f["title"]),
            ("위험도", None), ("화면 메뉴명", _menu_label(f["menu"], menus)), ("URL", f.get("url") or "-"),
            ("파라미터", ", ".join(f.get("params") or []) or "-"), ("입력사항", f.get("inputs") or "-")]
    rows = "".join(f"<tr><th>{k}</th><td>{_badge(f['risk']) + ' ' + e(f['verdict']) if v is None else e(str(v))}</td></tr>"
                   for k, v in info)
    shots = f.get("shots", [])
    figs = "".join(f'<figure><img src="{_img_data(workdir, s["file"])}" alt="{e(f["title"])} 캡처 {n}/{len(shots)}">'
                   f"<figcaption>캡처 {n}/{len(shots)}</figcaption></figure>" for n, s in enumerate(shots, 1))
    problems = "".join(f'<li><span class="num">{_circled(p["n"])}</span>{e(p["text"])}</li>' for p in f["problems"])
    active = ""
    if f.get("active_test"):
        a = f["active_test"]
        active = (f'<p class="active">능동 시험: {e(a.get("approval", ""))} (승인 {e(a.get("approved_by", ""))}, '
                  f'{e(str(a.get("attempts", "")))}/{e(str(a.get("max_attempts", "")))}회)</p>')
    nxt = f'<a class="next" href="#{next_no}">다음 ›</a>' if next_no else '<span></span>'
    return f"""<section class="page detail" id="{f["no"]}">
<div class="head">{_badge(f["risk"])}<span class="verdict">{e(f["verdict"])}</span><span class="no">{f["no"]}</span>
<h1>{e(f["title"])}</h1><p class="sub">{e(f["category"])}</p></div>
<table class="kv">{rows}</table>
<h2>캡처 화면</h2>{figs or '<p class="muted">캡처 없음</p>'}
<h2>문제점</h2><ol class="problems">{problems}</ol>{active}
<h2>조치방안</h2><div class="text">{e(f["remediation"])}</div>
<h2>근거</h2><div class="text">{e(f["basis"])}<br><span class="muted">기준 ID: {e(", ".join(f["item_ids"]))}</span></div>
<div class="pager"><a class="prev" href="#{prev_no}">‹ 이전</a>{nxt}</div>
</section>"""


def build_report(workdir: Path, *, date: str | None = None, out: Path | None = None) -> Path:
    workdir = Path(workdir)
    intake = load_intake(workdir / "intake.json")
    menu_map = load_menu_map(workdir / "menu_map.json")
    data = load_findings(workdir / "findings.json")
    for w in validate_findings(data):
        print(f"[경고] {w}")
    findings = sort_and_number(data["findings"], menu_map)
    menus = {m["id"]: m for m in menu_map["menus"]}
    day = (date or _date.today().isoformat()).replace("-", "")
    host = urlparse(intake["target_url"]).hostname or "site"
    out = out or workdir / f"REPORT_{host}_{day}.html"
    body = "\n".join([_overview(intake, menu_map, data, findings, menus)]
                     + [_detail(f, i, findings, menus, workdir) for i, f in enumerate(findings)])
    page = f"""<!doctype html><html lang="ko"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1"><title>보안성 검토 리포트 - {e(host)}</title>
<style>{(HERE / "report.css").read_text(encoding="utf-8")}</style></head>
<body><div class="layout">{_toc(findings, menus)}<main>{body}</main></div>
<div id="zoom" hidden><img alt="확대 보기"></div>
<script>{(HERE / "report.js").read_text(encoding="utf-8")}</script></body></html>"""
    out.write_text(page, encoding="utf-8")
    return out


def main() -> None:
    ap = argparse.ArgumentParser(description="작업 폴더로 보안성 검토 리포트(단일 HTML)를 만든다")
    ap.add_argument("--workdir", required=True, type=Path)
    ap.add_argument("--date", help="리포트 날짜 YYYY-MM-DD(기본 오늘)")
    a = ap.parse_args()
    print(build_report(a.workdir, date=a.date))


if __name__ == "__main__":
    main()
