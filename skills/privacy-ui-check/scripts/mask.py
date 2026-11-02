"""캡처 가림과 번호 박스(설계서 5-4).

수집기가 캡처할 때 잰 좌표(meta.json 의 pii_rects·box_rects, 문서 기준 px)로 원본 스크린샷을 가리고
문제 영역에 번호 박스를 이미지에 직접 그린 배포용 사본을 만든다. 원본은 건드리지 않는다.
  - 고유식별정보(주민등록번호·카드번호)는 불투명 박스, 그 밖의 개인정보는 9px 칸 픽셀화(+3px 여유).
    약한 블러는 복원·판독될 수 있어 쓰지 않는다(사용자 매뉴얼 킷 apply_mosaic.py 와 같은 방식).
  - 이미지 속 글자처럼 DOM 으로 못 잰 곳은 extra 좌표로 더 가린다. 완료 판정은 미리보기를 사람이 직접 보는 것이다.
결과 shot(dict)은 findings.json 의 shots 항목으로 그대로 쓴다(박스 좌표는 % 단위).

사용: python -m scripts.mask --raw-menu <밖>/raw/M06-02 --out-dir <작업 폴더>/img --name V-003_1 \
        --preview-dir <밖>/preview [--extra '[[x,y,w,h], ...]']
"""
from __future__ import annotations

import argparse
import hashlib
import json
import math
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

CELL, PAD = 9, 3
SOLID_KINDS = {"rrn", "card"}
KINDS = SOLID_KINDS | {"mobile", "phone", "email", "name", "address"}
# 측정과 캡처 사이에 화면이 움직이지 않게 애니메이션·전환·커서 깜박임을 멈춘다
FREEZE_CSS = "*,*::before,*::after{animation:none!important;transition:none!important;caret-color:transparent!important}"
DOC_SIZE = "() => [document.documentElement.scrollWidth, document.documentElement.scrollHeight]"
SOLID_COLOR = (120, 128, 140)
BOX_COLOR = (229, 62, 62)

# 화면 글자·입력값·표에서 개인정보 위치를 잰다(문서 기준 px). 열린 shadow DOM 안까지 본다(iframe 은 measure_pii 가 따로 잰다).
# 이름은 입력칸 이름·라벨, th-td 짝, 표 머리글 열, 글 속 라벨 뒤 이름으로 찾는다.
MEASURE_PII = r"""(useScroll = true) => {
  // iframe 안에서는 보이는 영역 기준(스크롤 제외)으로 잰다: 캡처에는 iframe 의 현재 보이는 부분만 찍힌다
  const sx = useScroll ? scrollX : 0, sy = useScroll ? scrollY : 0;
  const pats = [['rrn', /\b\d{6}-?[1-8]\d{6}\b/g], ['card', /\b\d{4}[- ]?\d{4}[- ]?\d{4}[- ]?\d{4}\b/g],
                ['mobile', /\b01[016789]-?\d{3,4}-?\d{4}\b/g], ['phone', /\b0(?:2|[3-6]\d|70)-\d{3,4}-\d{4}\b/g],
                ['email', /[\w.+-]+@[\w-]+\.[\w.-]+/g],
                ['address', /(?:서울|부산|대구|인천|광주|대전|울산|세종|경기|강원|충청|충북|충남|전라|전북|전남|경상|경북|경남|제주)[가-힣]*\s+[가-힣]+(?:시|군|구)(?:\s+[가-힣]+(?:구|읍|면|동))?\s+[가-힣0-9]+(?:로|길|동|리)(?:\s*[\d-]+)?/g]];
  const nameRe = /(^|_)(name|nm|receiver)($|_)|이름|성명|수령인|받는 ?분|고객명/i;
  // 라벨 뒤에 흔히 오는 이름 아닌 말(없는, 입력, 변경 …)은 빼서 과잉 가림을 줄인다
  const labelled = /(받는 ?분|수령인|이름|성명|고객명|작성자|신청인|담당자)\s*[:：]?\s*(?!(?:없는|없음|없이|있는|입력|변경|확인|수정|검색|등록|정보|항목|표시|필수|선택)(?![가-힣]))([가-힣]{2,4})(?![가-힣])/g;
  const out = [];
  const add = (r, kind) => { if (r.width > 0 && r.height > 0) out.push({x: Math.round(r.left + sx),
      y: Math.round(r.top + sy), w: Math.ceil(r.width), h: Math.ceil(r.height), kind}); };
  const roots = [document];
  for (let i = 0; i < roots.length; i++)
    for (const el of roots[i].querySelectorAll('*')) if (el.shadowRoot) roots.push(el.shadowRoot);
  for (const root of roots) {
    const walker = document.createTreeWalker(root === document ? document.body : root, NodeFilter.SHOW_TEXT);
    let n;
    while ((n = walker.nextNode())) {
      const p = n.parentElement;
      if (p && ['SCRIPT', 'STYLE', 'NOSCRIPT'].includes(p.tagName)) continue;
      for (const [kind, re] of pats) {
        re.lastIndex = 0; let m;
        while ((m = re.exec(n.data))) {
          const range = document.createRange();
          range.setStart(n, m.index); range.setEnd(n, m.index + m[0].length);
          for (const r of range.getClientRects()) add(r, kind);
        }
      }
      // 글 속 이름: '받는 분 김영희', '작성자: 박민수'처럼 이름 라벨 바로 뒤의 한글 2~4자
      labelled.lastIndex = 0; let m;
      while ((m = labelled.exec(n.data))) {
        const start = m.index + m[0].length - m[2].length;
        const range = document.createRange();
        range.setStart(n, start); range.setEnd(n, start + m[2].length);
        for (const r of range.getClientRects()) add(r, 'name');
      }
    }
    for (const el of root.querySelectorAll('input, textarea, select')) {
      if (['hidden', 'password', 'checkbox', 'radio', 'submit', 'button'].includes(el.type) || !el.value) continue;
      let kind = null;
      for (const [k, re] of pats) { re.lastIndex = 0; if (re.test(el.value)) { kind = k; break; } }
      const label = el.labels && el.labels[0] ? el.labels[0].innerText : '';
      if (!kind && (nameRe.test(el.name || '') || nameRe.test(el.id || '') || nameRe.test(label))) kind = 'name';
      if (kind) add(el.getBoundingClientRect(), kind);
    }
    for (const tr of root.querySelectorAll('tr')) {
      const cells = [...tr.children];
      cells.forEach((c, i) => { if (c.tagName === 'TH' && nameRe.test(c.innerText) && cells[i + 1] && cells[i + 1].tagName === 'TD')
        add(cells[i + 1].getBoundingClientRect(), 'name'); });
    }
    for (const table of root.querySelectorAll('table')) {
      const rows = [...table.querySelectorAll('tr')];
      if (!rows.length) continue;
      const head = [...rows[0].children];
      if (head.length < 2 || !head.every(c => c.tagName === 'TH')) continue;
      head.forEach((th, i) => { if (!nameRe.test(th.innerText)) return;
        for (const tr of rows.slice(1)) { const td = tr.children[i]; if (td) add(td.getBoundingClientRect(), 'name'); } });
    }
  }
  return out;
}"""
# DOM 으로 잴 수 없는 콘텐츠(캔버스·플러그인·임베드). 있으면 사람이 미리보기를 확인했다는 표시 없이 이미지를 만들지 않는다
# DOM 으로 잴 수 없는 콘텐츠(캔버스·플러그인·임베드, 열린 shadow DOM 안 포함)와, 닫힌 shadow DOM 으로 보이는 사용자 정의 요소
# (보이는데 자식·글자·열린 shadowRoot 가 없음). 있으면 사람이 미리보기를 확인했다는 표시 없이 이미지를 만들지 않는다
NONDOM = r"""() => {
  const roots = [document], found = [];
  for (let i = 0; i < roots.length; i++)
    for (const el of roots[i].querySelectorAll('*')) {
      if (el.shadowRoot) roots.push(el.shadowRoot);
      if (['CANVAS', 'EMBED', 'OBJECT'].includes(el.tagName)) found.push(el.tagName.toLowerCase());
      const r = el.getBoundingClientRect();
      if (el.tagName.includes('-') && !el.shadowRoot && el.childElementCount === 0 && !el.textContent.trim()
          && r.width > 0 && r.height > 0) found.push('unreadable-shadow');
    }
  return found;
}"""
_FRAME_BOX = """e => { const r = e.getBoundingClientRect();
  return {x: r.left + scrollX + e.clientLeft, y: r.top + scrollY + e.clientTop, w: e.clientWidth, h: e.clientHeight}; }"""


def _clip_to_box(rect: dict, box: dict) -> dict | None:
    x1, y1 = max(rect["x"], box["x"]), max(rect["y"], box["y"])
    x2, y2 = min(rect["x"] + rect["w"], box["x"] + box["w"]), min(rect["y"] + rect["h"], box["y"] + box["h"])
    if x2 <= x1 or y2 <= y1:
        return None
    return dict(rect, x=round(x1), y=round(y1), w=round(x2 - x1), h=round(y2 - y1))


def measure_pii(page) -> list[dict]:
    """메인 문서와 모든 iframe(다른 도메인 포함)의 개인정보 위치를 메인 문서 좌표로 잰다.
    iframe 안은 보이는 영역 좌표로 재고, iframe 의 보이는 상자 밖으로 나가는 부분은 잘라 낸다."""
    rects = list(page.evaluate(MEASURE_PII))
    for frame in page.frames[1:]:
        x = y = 0.0
        box = None
        f = frame
        while f.parent_frame:
            b = f.frame_element().evaluate(_FRAME_BOX)
            if box is None:
                box = dict(b)
            else:
                box["x"], box["y"] = box["x"] + b["x"], box["y"] + b["y"]
            x, y, f = x + b["x"], y + b["y"], f.parent_frame
        for r in frame.evaluate(MEASURE_PII, False):
            clipped = _clip_to_box(dict(r, x=r["x"] + x, y=r["y"] + y), box)
            if clipped:
                rects.append(clipped)
    return rects


def find_nondom(page) -> list[str]:
    found = []
    for frame in page.frames:
        try:
            found += frame.evaluate(NONDOM)
        except Exception:  # noqa: BLE001 - 읽을 수 없는 프레임도 DOM 밖으로 본다
            found.append("unreadable-iframe")
    return found

_BOX_RECT = "e => { const r = e.getBoundingClientRect(); return {x: Math.round(r.left + scrollX), y: Math.round(r.top + scrollY), w: Math.ceil(r.width), h: Math.ceil(r.height)}; }"


def measure_boxes(page, boxes: list[dict]) -> list[dict]:
    """[{n, selector, label}] → [{n, label, x, y, w, h} 또는 {n, label, missing: True}]"""
    out = []
    for b in boxes or []:
        el = page.query_selector(b["selector"])
        rect = el.evaluate(_BOX_RECT) if el else None
        if not rect or rect["w"] == 0 or rect["h"] == 0:
            out.append({"n": b["n"], "label": b.get("label", ""), "selector": b["selector"], "missing": True})
        else:
            out.append({"n": b["n"], "label": b.get("label", ""), "selector": b["selector"], **rect})
    return out


def _valid_rect(img, rect) -> tuple:
    """가릴 좌표 검사: 유한한 숫자(bool 제외), 양의 너비·높이, 이미지와 겹침. 아니면 ValueError(조용히 건너뛰지 않는다)."""
    if len(rect) != 4 or not all(isinstance(v, (int, float)) and not isinstance(v, bool) and math.isfinite(v) for v in rect):
        raise ValueError(f"잘못된 가림 좌표(숫자 아님): {rect}")
    x, y, w, h = rect
    if w <= 0 or h <= 0:
        raise ValueError(f"잘못된 가림 좌표(크기 0 이하): {rect}")
    if x >= img.width or y >= img.height or x + w <= 0 or y + h <= 0:
        raise ValueError(f"가림 좌표가 이미지 밖이다: {rect} (이미지 {img.width}x{img.height})")
    return rect


def _clip(img, rect, pad):
    x, y, w, h = rect
    return max(0, x - pad), max(0, y - pad), min(img.width, x + w + pad), min(img.height, y + h + pad)


def pixelate(img: Image.Image, rect, cell: int = CELL, pad: int = PAD) -> None:
    left, top, right, bottom = _clip(img, rect, pad)
    if right - left < 2 or bottom - top < 2:
        return
    region = img.crop((left, top, right, bottom))
    small = region.resize((max(1, (right - left) // cell), max(1, (bottom - top) // cell)), Image.BILINEAR)
    img.paste(small.resize(region.size, Image.NEAREST), (left, top))


def solid(img: Image.Image, rect, pad: int = PAD) -> None:
    left, top, right, bottom = _clip(img, rect, pad)
    ImageDraw.Draw(img).rectangle([left, top, right - 1, bottom - 1], fill=SOLID_COLOR)


def _font(size: int):
    for cand in ("arialbd.ttf", "C:/Windows/Fonts/arialbd.ttf", "DejaVuSans-Bold.ttf",
                 "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf", "/Library/Fonts/Arial Bold.ttf"):
        try:
            return ImageFont.truetype(cand, size)
        except OSError:
            continue
    return ImageFont.load_default()


def draw_box(img: Image.Image, rect, n: int) -> None:
    x, y, w, h = rect
    d = ImageDraw.Draw(img)
    d.rectangle([x, y, x + w, y + h], outline=BOX_COLOR, width=4)
    r = 15
    cx, cy = max(r, x), max(r, y)
    d.ellipse([cx - r, cy - r, cx + r, cy + r], fill=BOX_COLOR)
    d.text((cx, cy), str(n), fill="white", font=_font(18), anchor="mm")


MANIFEST = "manifest.json"


def add_to_manifest(img_dir: Path, image: Path) -> None:
    """가림 처리한 이미지의 sha256 을 img/manifest.json 에 적는다. 리포트는 여기 있는 이미지만 넣는다."""
    img_dir, image = Path(img_dir), Path(image)
    path = img_dir / MANIFEST
    data = json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}
    data[image.name] = hashlib.sha256(image.read_bytes()).hexdigest()
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


def render_shot(raw_menu_dir: Path, out_dir: Path, name: str, *, preview_dir: Path | None = None,
                extra=(), boxes: list[dict] | None = None, img_prefix: str = "img/",
                reviewed_nondom: bool = False) -> dict:
    raw_menu_dir, out_dir = Path(raw_menu_dir), Path(out_dir)
    meta = json.loads((raw_menu_dir / "meta.json").read_text(encoding="utf-8"))
    # 가림은 실패하면 닫힌다: 측정이 없거나, 흔들렸거나, 모양이 틀리면 이미지를 만들지 않는다
    if "pii_rects" not in meta:
        raise ValueError("meta.json 에 pii_rects 가 없다 — 이 화면을 다시 수집한다")
    if meta.get("nondom") and not reviewed_nondom:
        raise ValueError(f"DOM 으로 잴 수 없는 콘텐츠가 있다({', '.join(sorted(set(meta['nondom'])))}) — 미리보기를 "
                         "원본 크기로 확인하고 필요한 곳을 extra 로 가린 뒤 --reviewed-nondom 으로 다시 만든다")
    if meta.get("rects_stable") is False:
        raise ValueError("캡처 전후 가림 좌표가 달랐다 — 화면이 멈춘 상태에서 다시 수집한다")
    boxes = meta.get("box_rects", []) if boxes is None else boxes
    missing = [str(b["n"]) for b in boxes if b.get("missing")]
    if missing:
        raise ValueError(f"좌표를 재지 못한 박스: {', '.join(missing)} — 셀렉터를 고쳐 그 화면만 다시 수집한다")
    with Image.open(raw_menu_dir / "screenshot.png") as im:
        img = Image.new("RGB", im.size)
        img.paste(im.convert("RGB"))  # 메타데이터 없는 새 이미지
    for r in meta["pii_rects"]:
        if r.get("kind") not in KINDS:
            raise ValueError(f"잘못된 가림 좌표(종류): {r}")
        rect = _valid_rect(img, (r.get("x"), r.get("y"), r.get("w"), r.get("h")))
        (solid if r["kind"] in SOLID_KINDS else pixelate)(img, rect)
    for rect in extra:
        pixelate(img, _valid_rect(img, tuple(rect)))
    for b in boxes:
        draw_box(img, (b["x"], b["y"], b["w"], b["h"]), b["n"])
    out_dir.mkdir(parents=True, exist_ok=True)
    img.save(out_dir / f"{name}.png", "PNG", optimize=True)
    add_to_manifest(out_dir, out_dir / f"{name}.png")
    if preview_dir:
        Path(preview_dir).mkdir(parents=True, exist_ok=True)
        img.resize((img.width // 2, img.height // 2)).save(Path(preview_dir) / f"{name}.png")
    pct = lambda v, total: round(v / total * 100, 2)  # noqa: E731
    return {"file": f"{img_prefix}{name}.png", "width": img.width, "height": img.height,
            "boxes": [{"n": b["n"], "left": pct(b["x"], img.width), "top": pct(b["y"], img.height),
                       "width": pct(b["w"], img.width), "height": pct(b["h"], img.height)} for b in boxes]}


def main() -> None:
    ap = argparse.ArgumentParser(description="원본 캡처를 가리고 번호 박스를 그린 배포용 사본을 만든다")
    ap.add_argument("--raw-menu", required=True, type=Path, help="원본 폴더의 메뉴 폴더(<raw>/<메뉴ID>)")
    ap.add_argument("--out-dir", required=True, type=Path, help="작업 폴더의 img/")
    ap.add_argument("--name", required=True, help="출력 이름(예: V-003_1)")
    ap.add_argument("--preview-dir", type=Path)
    ap.add_argument("--extra", default="[]", help="추가로 가릴 좌표 JSON [[x,y,w,h], ...]")
    ap.add_argument("--reviewed-nondom", action="store_true",
                    help="캔버스·임베드 같은 DOM 밖 콘텐츠를 미리보기로 확인했다(필요한 곳은 --extra 로 가림)")
    a = ap.parse_args()
    shot = render_shot(a.raw_menu, a.out_dir, a.name, preview_dir=a.preview_dir, extra=json.loads(a.extra),
                       reviewed_nondom=a.reviewed_nondom)
    print(json.dumps(shot, ensure_ascii=False))


if __name__ == "__main__":
    main()
