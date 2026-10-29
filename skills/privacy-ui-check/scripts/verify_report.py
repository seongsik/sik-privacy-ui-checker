"""리포트 기계 검증(설계서 8-4).

리포트 HTML 을 실제 브라우저로 열어 확인하고, 사람이 직접 열어 볼 증거 png 를 남긴다.
  - 콘솔 오류·페이지 오류가 없다, 외부 주소로 나가는 요청이 없다(단일 파일)
  - 목차의 모든 항목이 해당 쪽을 보여 준다, 각 상세 쪽의 이미지가 모두 읽힌다
  - 이전·다음 이동, 확대 보기, 인쇄(모든 쪽 표시·상세 쪽 나눔), 모바일 폭 넘침
종료코드 0 = 통과, 1 = 문제 있음(문제 목록 출력). 통과해도 증거 png 를 직접 열어 본 뒤에만 완료라고 말한다.

사용: python -m scripts.verify_report --html <리포트>.html --out-dir <증거 폴더(작업 폴더 밖)>
"""
from __future__ import annotations

import argparse
from pathlib import Path

from playwright.sync_api import sync_playwright

VISIBLE = "els => els.filter(e => e.offsetParent !== null).map(e => e.id)"
BROKEN_IMAGES = "imgs => imgs.filter(i => !(i.complete && i.naturalWidth > 0)).length"


def verify_report(html_path: Path, out_dir: Path) -> tuple[int, list[str]]:
    html_path, out_dir = Path(html_path), Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    problems: list[str] = []
    with sync_playwright() as p:
        browser = p.chromium.launch()
        page = browser.new_page(viewport={"width": 1440, "height": 900})
        page.on("console", lambda m: m.type == "error" and problems.append(f"콘솔 오류: {m.text}"))
        page.on("pageerror", lambda err: problems.append(f"페이지 오류: {err}"))
        page.on("request", lambda r: r.url.startswith(("http://", "https://"))
                and problems.append(f"외부 요청: {r.url}"))
        page.goto(html_path.resolve().as_uri())
        page.wait_for_timeout(200)
        if page.eval_on_selector_all("section.page", VISIBLE) != ["overview"]:
            problems.append("첫 화면이 리포트 개요가 아니다")
        page.screenshot(path=str(out_dir / "01_overview.png"))

        ids = page.eval_on_selector_all("section.page.detail", "els => els.map(e => e.id)")
        toc = page.eval_on_selector_all("nav.toc a.toc-item", "as => as.map(a => a.getAttribute('href').slice(1))")
        for target in toc:
            page.evaluate(f"location.hash = '#{target}'")
            page.wait_for_timeout(50)
            shown = page.eval_on_selector_all("section.page", VISIBLE)
            if shown != [target]:
                problems.append(f"목차 {target}: 쪽이 보이지 않는다(보이는 쪽 {shown})")
                continue
            broken = page.eval_on_selector_all(f"#{target} img", BROKEN_IMAGES)
            if broken:
                problems.append(f"{target}: 읽히지 않는 이미지 {broken}개")
        if toc != ids:
            problems.append(f"목차 순서와 상세 쪽 순서가 다르다: 목차 {toc} / 쪽 {ids}")

        if ids:
            page.evaluate(f"location.hash = '#{ids[0]}'")
            page.wait_for_timeout(100)
            page.screenshot(path=str(out_dir / "02_detail_first.png"), full_page=True)
            if len(ids) > 1:
                page.keyboard.press("ArrowRight")
                page.wait_for_timeout(50)
                if page.eval_on_selector_all("section.page", VISIBLE) != [ids[1]]:
                    problems.append("→ 키로 다음 쪽으로 가지 않는다")
                page.click(f"#{ids[1]} a.prev")
                page.wait_for_timeout(50)
                if page.eval_on_selector_all("section.page", VISIBLE) != [ids[0]]:
                    problems.append("'이전' 링크가 앞 쪽으로 가지 않는다")
            page.evaluate(f"location.hash = '#{ids[-1]}'")
            page.wait_for_timeout(100)
            page.screenshot(path=str(out_dir / "02_detail_last.png"), full_page=True)
            first_img = page.query_selector(f"#{ids[-1]} figure img")
            if first_img:
                first_img.click()
                if not page.is_visible("#zoom"):
                    problems.append("확대 보기가 열리지 않는다")
                page.screenshot(path=str(out_dir / "03_zoom.png"))
                page.keyboard.press("Escape")
                if page.is_visible("#zoom"):
                    problems.append("Esc 로 확대 보기가 닫히지 않는다")

        page.emulate_media(media="print")
        hidden = page.eval_on_selector_all("section.page", "els => els.filter(e => e.offsetParent === null).length")
        if hidden:
            problems.append(f"인쇄에서 보이지 않는 쪽 {hidden}개")
        breaks = page.eval_on_selector_all("section.page.detail",
                                           "els => els.filter(e => getComputedStyle(e).breakBefore !== 'page').length")
        if breaks:
            problems.append(f"인쇄에서 쪽 나눔이 없는 상세 {breaks}개")
        page.screenshot(path=str(out_dir / "04_print.png"), full_page=True)
        page.pdf(path=str(out_dir / "04_print.pdf"), format="A4", print_background=True)
        page.emulate_media(media="screen")

        page.set_viewport_size({"width": 375, "height": 800})
        page.evaluate("location.hash = '#overview'")
        page.wait_for_timeout(100)
        width = page.evaluate("document.documentElement.scrollWidth")
        if width > 376:
            problems.append(f"모바일(375px)에서 가로로 넘친다: {width}px")
        page.screenshot(path=str(out_dir / "05_mobile.png"))
        browser.close()
    return (1 if problems else 0), problems


def main() -> None:
    ap = argparse.ArgumentParser(description="보안성 검토 리포트를 브라우저로 검증하고 증거 png 를 남긴다")
    ap.add_argument("--html", required=True, type=Path)
    ap.add_argument("--out-dir", required=True, type=Path)
    a = ap.parse_args()
    code, problems = verify_report(a.html, a.out_dir)
    for p in problems:
        print(f"[문제] {p}")
    print("통과 — 증거 png 를 직접 열어 확인하세요: " + str(a.out_dir) if code == 0 else f"실패: {len(problems)}건")
    raise SystemExit(code)


if __name__ == "__main__":
    main()
