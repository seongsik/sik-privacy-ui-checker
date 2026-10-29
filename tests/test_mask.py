"""가림과 번호 박스(설계서 5-4, 4-3 shots) 테스트."""
import json

import pytest
from PIL import Image
from playwright.sync_api import sync_playwright

from scripts import mask
from scripts import session
from scripts.collect import collect

PAGE = """<html><body style="margin:0;font:16px sans-serif">
<p id="t">연락처 010-1234-5678 / hong@example.com</p>
<p>주민번호 900101-1234567 카드 4111-1111-1111-1111</p>
<input name="name" value="홍길동">
<table><tr><th>받는 분</th><td>김영희</td></tr></table>
<table><tr><th>상품</th><th>받는 분</th></tr><tr><td>키보드</td><td>이철수</td></tr></table>
<p>공지: 서비스 점검 안내</p>
</body></html>"""


@pytest.fixture(scope="module")
def measured():
    with sync_playwright() as p:
        b = p.chromium.launch()
        page = b.new_page(viewport={"width": 800, "height": 600})
        page.set_content(PAGE)
        rects = page.evaluate(mask.MEASURE_PII)
        boxes = mask.measure_boxes(page, [{"n": 1, "selector": "#t", "label": "연락처"},
                                          {"n": 2, "selector": "#nope", "label": "없음"}])
        b.close()
    return rects, boxes


def test_measures_each_personal_value_kind(measured):
    rects, _ = measured
    kinds = [r["kind"] for r in rects]
    for k in ("mobile", "email", "rrn", "card"):
        assert k in kinds, k
    assert kinds.count("name") >= 3  # 이름 입력칸, th-td 짝, 받는 분 열


def test_does_not_measure_ordinary_text(measured):
    rects, _ = measured
    assert all(r["w"] > 0 and r["h"] > 0 for r in rects)
    assert len(rects) <= 9


def test_measure_boxes_reports_missing(measured):
    _, boxes = measured
    assert boxes[0]["n"] == 1 and boxes[0]["w"] > 0
    assert boxes[1]["missing"] is True


def blank(w=200, h=100):
    img = Image.new("RGB", (w, h), "white")
    for x in range(w):  # 글자 대신 세로 줄무늬
        if x % 3 == 0:
            for y in range(h):
                img.putpixel((x, y), (0, 0, 0))
    return img


def test_pixelate_makes_uniform_cells_inside_and_keeps_outside():
    img = blank()
    before_outside = img.getpixel((190, 90))
    mask.pixelate(img, (18, 18, 54, 36), cell=9, pad=0)
    cell = {img.getpixel((18 + dx, 18 + dy)) for dx in range(9) for dy in range(9)}
    assert len(cell) == 1
    assert img.getpixel((190, 90)) == before_outside


def test_solid_covers_rect_with_padding():
    img = blank()
    mask.solid(img, (20, 20, 40, 20), pad=3)
    colors = {img.getpixel((x, y)) for x in range(17, 63) for y in range(17, 43)}
    assert colors == {mask.SOLID_COLOR}


def test_draw_box_paints_red_border():
    img = Image.new("RGB", (200, 100), "white")
    mask.draw_box(img, (50, 30, 60, 30), 1)
    assert img.getpixel((50, 45)) == mask.BOX_COLOR


def test_render_shot_masks_boxes_and_reports_percentages(tmp_path):
    raw = tmp_path / "raw" / "M1"
    raw.mkdir(parents=True)
    blank(400, 200).save(raw / "screenshot.png")
    (raw / "meta.json").write_text(json.dumps({
        "pii_rects": [{"x": 10, "y": 10, "w": 50, "h": 20, "kind": "email"},
                      {"x": 100, "y": 10, "w": 80, "h": 20, "kind": "rrn"}],
        "box_rects": [{"n": 1, "x": 200, "y": 100, "w": 100, "h": 40, "label": "x"}],
    }), encoding="utf-8")
    shot = mask.render_shot(raw, tmp_path / "img", "V-001_1", preview_dir=tmp_path / "preview",
                            extra=[(300, 150, 20, 20)])
    out = Image.open(tmp_path / "img" / "V-001_1.png")
    assert out.getpixel((140, 20)) == mask.SOLID_COLOR       # 고유식별정보는 불투명
    assert out.getpixel((200, 120)) == mask.BOX_COLOR        # 박스를 이미지에 그림
    assert shot["file"] == "img/V-001_1.png" and (shot["width"], shot["height"]) == (400, 200)
    assert shot["boxes"] == [{"n": 1, "left": 50.0, "top": 50.0, "width": 25.0, "height": 20.0}]
    assert (tmp_path / "preview" / "V-001_1.png").exists()
    assert Image.open(tmp_path / "img" / "V-001_1.png").info == {} or "exif" not in Image.open(tmp_path / "img" / "V-001_1.png").info


def test_render_shot_refuses_box_without_rect(tmp_path):
    raw = tmp_path / "M1"
    raw.mkdir()
    blank().save(raw / "screenshot.png")
    (raw / "meta.json").write_text(json.dumps({"pii_rects": [], "box_rects": [{"n": 1, "missing": True, "label": "x"}]}),
                                   encoding="utf-8")
    with pytest.raises(ValueError, match="1"):
        mask.render_shot(raw, tmp_path / "img", "V-001_1")


def test_collect_measures_pii_and_requested_boxes(demo, tmp_path):
    state = tmp_path / "outside" / "s.json"
    session.run_login(demo.base, "url:/faq", state, tmp_path / "w", headless=True, start_url=demo.login_url())
    menus = [{"id": "M1", "path": ["회원정보 수정"], "url": demo.url("/my/profile"), "status": "visit", "auth": "user"}]
    work = tmp_path / "work"
    work.mkdir()
    with sync_playwright() as p:
        b = p.chromium.launch()
        collect(b, state, {"target_url": demo.base, "allowed_domains": ["127.0.0.1"]}, {"menus": menus},
                tmp_path / "raw", work, delay_s=0,
                boxes={"M1": [{"n": 1, "selector": 'input[name="ssn"]', "label": "주민등록번호"}]})
        b.close()
    meta = json.loads((tmp_path / "raw" / "M1" / "meta.json").read_text(encoding="utf-8"))
    assert any(r["kind"] == "rrn" for r in meta["pii_rects"])
    assert meta["box_rects"][0]["n"] == 1 and meta["box_rects"][0]["w"] > 0


def test_render_shot_fails_closed_without_measurements(tmp_path):
    raw = tmp_path / "M1"
    raw.mkdir()
    blank().save(raw / "screenshot.png")
    (raw / "meta.json").write_text(json.dumps({"box_rects": []}), encoding="utf-8")
    with pytest.raises(ValueError, match="pii_rects"):
        mask.render_shot(raw, tmp_path / "img", "V-001_1")


@pytest.mark.parametrize("bad", [{"x": "10", "y": 0, "w": 5, "h": 5, "kind": "email"},
                                 {"x": 10, "y": 0, "w": 5, "h": 5, "kind": "weird"}])
def test_render_shot_rejects_malformed_rects(tmp_path, bad):
    raw = tmp_path / "M1"
    raw.mkdir()
    blank().save(raw / "screenshot.png")
    (raw / "meta.json").write_text(json.dumps({"pii_rects": [bad], "box_rects": []}), encoding="utf-8")
    with pytest.raises(ValueError):
        mask.render_shot(raw, tmp_path / "img", "V-001_1")


def test_render_shot_refuses_unstable_measurements(tmp_path):
    raw = tmp_path / "M1"
    raw.mkdir()
    blank().save(raw / "screenshot.png")
    (raw / "meta.json").write_text(json.dumps({"pii_rects": [], "box_rects": [], "rects_stable": False}), encoding="utf-8")
    with pytest.raises(ValueError, match="다시 수집"):
        mask.render_shot(raw, tmp_path / "img", "V-001_1")


def _collect_one(demo, tmp_path, url):
    work = tmp_path / "work"
    work.mkdir(exist_ok=True)
    menus = [{"id": "M1", "path": ["x"], "url": demo.url(url), "status": "visit", "auth": "none"}]
    with sync_playwright() as p:
        b = p.chromium.launch()
        collect(b, None, {"target_url": demo.base, "allowed_domains": ["127.0.0.1"]}, {"menus": menus},
                tmp_path / "raw", work, delay_s=0)
        b.close()
    return json.loads((tmp_path / "raw" / "M1" / "meta.json").read_text(encoding="utf-8"))


def test_collect_marks_stable_measurements(demo, tmp_path):
    assert _collect_one(demo, tmp_path, "/board/1")["rects_stable"] is True


def test_collect_marks_moving_page_unstable(demo, tmp_path):
    assert _collect_one(demo, tmp_path, "/__moving")["rects_stable"] is False


@pytest.mark.parametrize("bad", [
    {"x": True, "y": 0, "w": 5, "h": 5, "kind": "email"},
    {"x": float("nan"), "y": 0, "w": 5, "h": 5, "kind": "email"},
    {"x": 10, "y": 0, "w": 0, "h": 5, "kind": "email"},
    {"x": 10, "y": 0, "w": 5, "h": -3, "kind": "email"},
    {"x": 5000, "y": 5000, "w": 10, "h": 10, "kind": "email"},
])
def test_render_shot_rejects_degenerate_or_offimage_rects(tmp_path, bad):
    raw = tmp_path / "M1"
    raw.mkdir()
    blank().save(raw / "screenshot.png")
    (raw / "meta.json").write_text(json.dumps({"pii_rects": [bad], "box_rects": []}), encoding="utf-8")
    with pytest.raises(ValueError):
        mask.render_shot(raw, tmp_path / "img", "V-001_1")
