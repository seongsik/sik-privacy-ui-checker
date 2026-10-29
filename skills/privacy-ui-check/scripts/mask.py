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
import json
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

CELL, PAD = 9, 3
SOLID_KINDS = {"rrn", "card"}
SOLID_COLOR = (120, 128, 140)
BOX_COLOR = (229, 62, 62)

# 화면 글자·입력값·표에서 개인정보 위치를 잰다(문서 기준 px). 이름은 입력칸 이름·라벨, th-td 짝, 표 머리글 열로 찾는다.
MEASURE_PII = r"""() => {
  const pats = [['rrn', /\b\d{6}-[1-4]\d{6}\b/g], ['card', /\b\d{4}[- ]\d{4}[- ]\d{4}[- ]\d{4}\b/g],
                ['mobile', /\b01[016789]-?\d{3,4}-?\d{4}\b/g], ['email', /[\w.+-]+@[\w-]+\.[\w.-]+/g]];
  const nameRe = /(^|_)(name|nm|receiver)($|_)|이름|성명|수령인|받는 ?분|고객명/i;
  const out = [];
  const add = (r, kind) => { if (r.width > 0 && r.height > 0) out.push({x: Math.round(r.left + scrollX),
      y: Math.round(r.top + scrollY), w: Math.ceil(r.width), h: Math.ceil(r.height), kind}); };
  const walker = document.createTreeWalker(document.body, NodeFilter.SHOW_TEXT);
  let n;
  while ((n = walker.nextNode())) {
    const p = n.parentElement;
    if (!p || ['SCRIPT', 'STYLE', 'NOSCRIPT'].includes(p.tagName)) continue;
    for (const [kind, re] of pats) {
      re.lastIndex = 0; let m;
      while ((m = re.exec(n.data))) {
        const range = document.createRange();
        range.setStart(n, m.index); range.setEnd(n, m.index + m[0].length);
        for (const r of range.getClientRects()) add(r, kind);
      }
    }
  }
  for (const el of document.querySelectorAll('input, textarea, select')) {
    if (['hidden', 'password', 'checkbox', 'radio', 'submit', 'button'].includes(el.type) || !el.value) continue;
    let kind = null;
    for (const [k, re] of pats) { re.lastIndex = 0; if (re.test(el.value)) { kind = k; break; } }
    const label = el.labels && el.labels[0] ? el.labels[0].innerText : '';
    if (!kind && (nameRe.test(el.name || '') || nameRe.test(el.id || '') || nameRe.test(label))) kind = 'name';
    if (kind) add(el.getBoundingClientRect(), kind);
  }
  for (const tr of document.querySelectorAll('tr')) {
    const cells = [...tr.children];
    cells.forEach((c, i) => { if (c.tagName === 'TH' && nameRe.test(c.innerText) && cells[i + 1] && cells[i + 1].tagName === 'TD')
      add(cells[i + 1].getBoundingClientRect(), 'name'); });
  }
  for (const table of document.querySelectorAll('table')) {
    const rows = [...table.querySelectorAll('tr')];
    if (!rows.length) continue;
    const head = [...rows[0].children];
    if (head.length < 2 || !head.every(c => c.tagName === 'TH')) continue;
    head.forEach((th, i) => { if (!nameRe.test(th.innerText)) return;
      for (const tr of rows.slice(1)) { const td = tr.children[i]; if (td) add(td.getBoundingClientRect(), 'name'); } });
  }
  return out;
}"""

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


def render_shot(raw_menu_dir: Path, out_dir: Path, name: str, *, preview_dir: Path | None = None,
                extra=(), boxes: list[dict] | None = None, img_prefix: str = "img/") -> dict:
    raw_menu_dir, out_dir = Path(raw_menu_dir), Path(out_dir)
    meta = json.loads((raw_menu_dir / "meta.json").read_text(encoding="utf-8"))
    boxes = meta.get("box_rects", []) if boxes is None else boxes
    missing = [str(b["n"]) for b in boxes if b.get("missing")]
    if missing:
        raise ValueError(f"좌표를 재지 못한 박스: {', '.join(missing)} — 셀렉터를 고쳐 그 화면만 다시 수집한다")
    with Image.open(raw_menu_dir / "screenshot.png") as im:
        img = Image.new("RGB", im.size)
        img.paste(im.convert("RGB"))  # 메타데이터 없는 새 이미지
    for r in meta.get("pii_rects", []):
        (solid if r["kind"] in SOLID_KINDS else pixelate)(img, (r["x"], r["y"], r["w"], r["h"]))
    for rect in extra:
        pixelate(img, tuple(rect))
    for b in boxes:
        draw_box(img, (b["x"], b["y"], b["w"], b["h"]), b["n"])
    out_dir.mkdir(parents=True, exist_ok=True)
    img.save(out_dir / f"{name}.png", "PNG", optimize=True)
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
    a = ap.parse_args()
    shot = render_shot(a.raw_menu, a.out_dir, a.name, preview_dir=a.preview_dir, extra=json.loads(a.extra))
    print(json.dumps(shot, ensure_ascii=False))


if __name__ == "__main__":
    main()
