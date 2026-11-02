"""로그인 대기와 세션 저장(설계서 2장 1단계, 7-2).

화면에 보이는 격리 브라우저를 띄워 대상 사이트를 열고, 사용자가 그 창에서 직접 로그인하기를 기다린다.
로그인 완료는 `selector:<CSS>`(로그인 뒤에만 보이는 요소) 또는 `url:<문자열>`(로그인 뒤 주소에 포함)로 감지한다.
아이디·비밀번호는 받지 않는다. 이 단계에는 쓰기 차단과 HAR 기록을 켜지 않는다(로그인 요청 본문을 남기지 않기 위해).
세션 파일에는 로그인 쿠키가 있으므로 작업 폴더 밖에만 저장한다.

사용: python -m scripts.session --url <대상> --login-wait selector:nav --storage-state <밖>/session.json --workdir <작업 폴더>
"""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path

from playwright.sync_api import TimeoutError as PlaywrightTimeout
from playwright.sync_api import sync_playwright

VIEWPORT = {"width": 1920, "height": 1080}
SW_BLOCKED_MARK = "_privacy_ui_check_sw_blocked"


def private_dir(path: Path) -> Path:
    """소유자만 읽고 쓰는 폴더(POSIX 0700). 세션·원본 캡처처럼 가리지 않은 정보가 들어가는 곳에 쓴다."""
    path.mkdir(parents=True, exist_ok=True, mode=0o700)
    if os.name != "nt":
        os.chmod(path, 0o700)
    return path


def write_private(path: Path, text: str) -> None:
    """소유자만 읽고 쓰는 파일(POSIX 0600)로 쓴다."""
    private_dir(Path(path).parent)
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as f:
        f.write(text)
    if os.name != "nt":
        os.chmod(path, 0o600)


def _parse_condition(cond: str) -> tuple[str, str]:
    kind, _, value = cond.partition(":")
    if kind not in ("selector", "url") or not value:
        raise ValueError(f"로그인 감지 조건은 'selector:<CSS>' 또는 'url:<문자열>': {cond}")
    return kind, value


def _check_outside(storage_state: Path, workdir: Path) -> None:
    if storage_state.resolve().is_relative_to(workdir.resolve()):
        raise ValueError(f"세션 파일은 작업 폴더 밖에 둔다: {storage_state}")


def new_context(browser, storage_state: Path | None = None, **options):
    """점검용 컨텍스트. Service Worker 는 막는다(그 요청은 route 로 가로챌 수 없어 쓰기 차단을 우회한다).
    options 는 HAR 기록 같은 Playwright 컨텍스트 옵션을 더할 때 쓴다."""
    ctx = browser.new_context(
        viewport=VIEWPORT,
        device_scale_factor=1,
        service_workers="block",
        storage_state=str(storage_state) if storage_state else None,
        **options,
    )
    setattr(ctx, SW_BLOCKED_MARK, True)
    return ctx


def open_context(browser, storage_state: Path):
    """저장한 세션으로 점검용 컨텍스트를 연다."""
    return new_context(browser, Path(storage_state))


def run_login(target_url: str, login_wait: str, storage_state: Path, workdir: Path, *,
              headless: bool = False, timeout_ms: int = 600_000, start_url: str | None = None) -> Path:
    """사용자가 로그인할 때까지 기다렸다가 세션을 저장한다. 시간 안에 끝나지 않으면 TimeoutError."""
    kind, value = _parse_condition(login_wait)
    storage_state, workdir = Path(storage_state), Path(workdir)
    _check_outside(storage_state, workdir)
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=headless)
        try:
            ctx = new_context(browser)
            page = ctx.new_page()
            page.goto(start_url or target_url)
            try:
                if kind == "selector":
                    page.wait_for_selector(value, timeout=timeout_ms)
                else:
                    page.wait_for_url(lambda u: value in u, timeout=timeout_ms)
            except PlaywrightTimeout as e:
                raise TimeoutError(f"{timeout_ms / 1000:.0f}초 안에 로그인이 확인되지 않았다({login_wait})") from e
            write_private(storage_state, json.dumps(ctx.storage_state(), ensure_ascii=False))
        finally:
            browser.close()
    return storage_state


def main() -> None:
    ap = argparse.ArgumentParser(description="격리 브라우저에서 사용자가 직접 로그인하면 세션을 저장한다")
    ap.add_argument("--url", required=True)
    ap.add_argument("--login-wait", required=True, help="selector:<CSS> 또는 url:<문자열>")
    ap.add_argument("--storage-state", required=True, type=Path, help="작업 폴더 밖 경로")
    ap.add_argument("--workdir", required=True, type=Path)
    ap.add_argument("--timeout", type=int, default=600, help="초")
    a = ap.parse_args()
    print("열린 브라우저 창에서 직접 로그인해 주세요. 아이디·비밀번호는 채팅이나 파일에 적지 않습니다.")
    path = run_login(a.url, a.login_wait, a.storage_state, a.workdir, timeout_ms=a.timeout * 1000)
    print(f"세션 저장: {path} (로그인 쿠키가 들어 있습니다. 점검이 끝나면 삭제하세요.)")


if __name__ == "__main__":
    main()
