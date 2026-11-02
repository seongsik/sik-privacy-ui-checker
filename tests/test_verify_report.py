"""리포트 검증기(설계서 8-4) 테스트."""
import json
import re

import pytest
from PIL import Image

import test_report as sample
from scripts.mask import add_to_manifest
from scripts.report.build_report import build_report
from scripts.verify_report import verify_report


def make_workdir(path):
    path.mkdir(parents=True, exist_ok=True)
    (path / "intake.json").write_text((sample.TEMPLATES / "intake.example.json").read_text(encoding="utf-8"),
                                      encoding="utf-8")
    (path / "menu_map.json").write_text(json.dumps(sample.MENU_MAP, ensure_ascii=False), encoding="utf-8")
    (path / "findings.json").write_text(json.dumps({"findings": sample.FINDINGS, "others": []}, ensure_ascii=False),
                                        encoding="utf-8")
    (path / "img").mkdir(exist_ok=True)
    for f in sample.FINDINGS:
        for s in f["shots"]:
            Image.new("RGB", (400, 200), "white").save(path / s["file"])
            add_to_manifest(path / "img", path / s["file"])
    return build_report(path, date="2026-11-06")


@pytest.fixture
def report(tmp_path):
    return make_workdir(tmp_path / "work")


def test_good_report_passes_and_leaves_evidence(report, tmp_path):
    ev = tmp_path / "evidence"
    code, problems = verify_report(report, ev)
    assert (code, problems) == (0, [])
    names = {p.name for p in ev.glob("*.png")}
    for prefix in ("01_overview", "02_detail_first", "02_detail_last", "03_zoom", "04_print", "05_mobile"):
        assert any(n.startswith(prefix) for n in names), prefix


def test_broken_image_fails(report, tmp_path):
    html = report.read_text(encoding="utf-8")
    report.write_text(html.replace("data:image/png;base64,", "data:image/png;base64,AAAA", 1), encoding="utf-8")
    code, problems = verify_report(report, tmp_path / "ev")
    assert code == 1 and any("이미지" in p for p in problems)


def test_toc_link_without_page_fails(report, tmp_path):
    html = report.read_text(encoding="utf-8")
    report.write_text(html.replace('id="V-003"', 'id="V-999"', 1), encoding="utf-8")
    code, problems = verify_report(report, tmp_path / "ev")
    assert code == 1 and any("V-003" in p for p in problems)


def test_console_error_fails(report, tmp_path):
    html = report.read_text(encoding="utf-8")
    report.write_text(html.replace("</body>", "<script>throw new Error('boom')</script></body>"), encoding="utf-8")
    code, problems = verify_report(report, tmp_path / "ev")
    assert code == 1 and any("boom" in p for p in problems)


def test_external_request_fails(report, tmp_path):
    html = report.read_text(encoding="utf-8")
    report.write_text(html.replace("</body>", '<img src="http://example.invalid/x.png"></body>'), encoding="utf-8")
    code, problems = verify_report(report, tmp_path / "ev")
    assert code == 1 and any("외부" in p for p in problems)


def test_mobile_overflow_fails(report, tmp_path):
    html = report.read_text(encoding="utf-8")
    report.write_text(re.sub(r"</style>", "#overview{min-width:900px}</style>", html, count=1), encoding="utf-8")
    code, problems = verify_report(report, tmp_path / "ev")
    assert code == 1 and any("모바일" in p for p in problems)
