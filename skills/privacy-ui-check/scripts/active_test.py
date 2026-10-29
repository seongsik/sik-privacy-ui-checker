"""능동 시험(설계서 2장 5단계, 4-3 active_test, 7-1의 6번).

승인받은 항목 하나를 화면에 보이는 격리 브라우저에서 시험한다. 입력은 사용자가 그 창에서 직접 한다.
- 쓰기 차단은 그대로 켜 두고, 승인받은 대상 요청(메서드 + URL, 쿼리 제외)만 최대 시도 횟수까지 통과시킨다.
  횟수를 넘은 대상 요청과 그 밖의 모든 쓰기 요청은 막힌다.
- 시도마다 응답 상태와 화면 글자 일부를 기록하고, 끝나면 최종 화면을 캡처한다.
- HAR 은 비밀번호·인증 값을 가린 뒤 원본 폴더(작업 폴더 밖) ACT-<기준ID>/ 에 남긴다.
결과(dict)는 findings.json 의 active_test 에 그대로 넣는다.

사용: python -m scripts.active_test --intake intake.json --menu-map menu_map.json --storage-state <밖>/session.json \
        --item SAFE-05-06-01 --approval "…승인 문구…" --approved-by "승인자" --start-url https://…/login \
        --target GET https://…/login/submit --max-attempts 6 --raw-dir <밖>/raw --workdir <작업 폴더>
"""
from __future__ import annotations

import argparse
import json
import re
import time
from datetime import datetime, timezone
from pathlib import Path

from scripts.collect import scrub_har
from scripts.guard import Guard, install_guard
from scripts.session import new_context

TAG = re.compile(r"<script.*?</script>|<style.*?</style>|<[^>]+>", re.S | re.I)


def _same_target(method: str, url: str, targets) -> bool:
    return any(method == m.upper() and url.split("?")[0] == u.split("?")[0] for m, u in targets)


def run_active_test(browser, storage_state, intake: dict, menu_map: dict, *, item_id: str, approval: dict,
                    start_url: str, targets: list[tuple[str, str]], max_attempts: int,
                    raw_dir: Path, workdir: Path, driver=None, timeout_s: int = 600, done_text: str | None = None) -> dict:
    """driver: 테스트에서 사람 입력을 대신하는 함수(page) — 실제 점검에서는 None(사용자가 직접 입력)."""
    if not (approval.get("text") and approval.get("approved_by")):
        raise ValueError("능동 시험은 승인 문구와 승인자가 있어야 한다")
    raw_dir, workdir = Path(raw_dir), Path(workdir)
    if raw_dir.resolve().is_relative_to(workdir.resolve()):
        raise ValueError(f"원본 폴더는 작업 폴더 밖에 둔다: {raw_dir}")
    out = raw_dir / f"ACT-{item_id}"
    out.mkdir(parents=True, exist_ok=True)
    har_tmp = out / ".network.har"

    record = {
        "item_id": item_id, "approval": approval["text"], "approved_by": approval["approved_by"],
        "approved_at": approval.get("approved_at") or datetime.now(timezone.utc).isoformat(),
        "allowed": [{"method": m.upper(), "url": u} for m, u in targets],
        "max_attempts": max_attempts, "attempts": 0, "over_limit_blocked": 0, "observations": [],
    }

    checker = Guard(intake, menu_map)
    for m, u in targets:
        if not checker._in_scope(u) or checker.decide("GET", u, False) == "deny-pattern":
            raise ValueError(f"시험 대상이 점검 범위 밖이거나 금지 목록에 걸린다: {m} {u}")

    ctx = new_context(browser, Path(storage_state) if storage_state else None,
                      record_har_path=str(har_tmp), record_har_content="embed")
    guard = install_guard(ctx, intake, menu_map)

    def on_target(route):
        req = route.request
        if not _same_target(req.method, req.url, targets):
            route.fallback()  # 쓰기 차단(guard)이 판단한다
            return
        if record["attempts"] >= max_attempts:
            record["over_limit_blocked"] += 1
            route.abort("blockedbyclient")
            return
        record["attempts"] += 1
        # 승인 대상만 직접 보낸다. 리다이렉트는 따라가지도, 3xx 로 브라우저에 넘기지도 않는다
        # (fulfill 한 3xx 의 다음 이동은 route 를 거치지 않는다). 이동할 곳만 적은 화면으로 답한다.
        resp = route.fetch(max_redirects=0)
        location = resp.headers.get("location")
        if 300 <= resp.status < 400 and location is not None:
            record["observations"].append({"attempt": record["attempts"], "method": req.method, "status": resp.status,
                                           "text": f"리다이렉트(따라가지 않음): {location[:200]}"})
            route.fulfill(status=200, content_type="text/plain; charset=utf-8",
                          body=f"[점검 도구] 서버가 {resp.status} 로 다른 주소를 가리켰습니다(따라가지 않음): {location}")
            return
        text = re.sub(r"\s+", " ", TAG.sub(" ", resp.text())).strip()
        record["observations"].append({"attempt": record["attempts"], "method": req.method,
                                       "status": resp.status, "text": text[:300]})
        route.fulfill(response=resp)

    ctx.route("**/*", on_target)  # 나중에 건 route 가 먼저 실행된다
    page = ctx.new_page()
    page.goto(start_url)
    if driver:
        driver(page)
    else:
        print(f"[{item_id}] 열린 창에서 승인한 시험을 직접 진행하세요. 최대 {max_attempts}회까지만 서버로 전달됩니다.")
        deadline, seen = time.time() + timeout_s, 0
        while time.time() < deadline and record["attempts"] < max_attempts:
            page.wait_for_timeout(500)
            if record["attempts"] > seen:
                seen = record["attempts"]
                shot = f"attempt-{seen}.png"
                page.screenshot(path=str(out / shot))
                record["observations"][-1]["screenshot"] = shot
            if done_text and done_text in page.content():
                break
    page.wait_for_timeout(300)
    page.screenshot(path=str(out / "final.png"), full_page=True)
    record["final_screenshot"] = "final.png"
    record["blocked_requests"] = len(guard.blocked_log)
    ctx.close()

    (out / "network.har").write_text(
        json.dumps(scrub_har(json.loads(har_tmp.read_text(encoding="utf-8"))), ensure_ascii=False), encoding="utf-8")
    har_tmp.unlink()
    (out / "record.json").write_text(json.dumps(record, ensure_ascii=False, indent=2), encoding="utf-8")
    return record


def main() -> None:
    from playwright.sync_api import sync_playwright

    from scripts.models import load_intake, load_menu_map

    ap = argparse.ArgumentParser(description="승인받은 능동 시험 하나를 진행하고 기록한다")
    ap.add_argument("--intake", required=True, type=Path)
    ap.add_argument("--menu-map", required=True, type=Path)
    ap.add_argument("--storage-state", type=Path)
    ap.add_argument("--item", required=True)
    ap.add_argument("--approval", required=True, help="사용자가 승인한 시험 내용 문구")
    ap.add_argument("--approved-by", required=True)
    ap.add_argument("--start-url", required=True)
    ap.add_argument("--target", nargs=2, action="append", required=True, metavar=("METHOD", "URL"))
    ap.add_argument("--max-attempts", type=int, required=True)
    ap.add_argument("--done-text")
    ap.add_argument("--raw-dir", required=True, type=Path)
    ap.add_argument("--workdir", required=True, type=Path)
    a = ap.parse_args()
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=False)
        record = run_active_test(
            browser, a.storage_state, load_intake(a.intake), load_menu_map(a.menu_map), item_id=a.item,
            approval={"text": a.approval, "approved_by": a.approved_by}, start_url=a.start_url,
            targets=[tuple(t) for t in a.target], max_attempts=a.max_attempts,
            raw_dir=a.raw_dir, workdir=a.workdir, done_text=a.done_text)
        browser.close()
    print(json.dumps({k: record[k] for k in ("item_id", "attempts", "over_limit_blocked")}, ensure_ascii=False))


if __name__ == "__main__":
    main()
