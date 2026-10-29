"""개발 환경 스모크 테스트: 고정한 의존성과 스킬 패키지 경로가 쓸 수 있는 상태인지 확인한다."""
import importlib
from pathlib import Path

SKILL_DIR = Path(__file__).resolve().parents[1] / "skills" / "privacy-ui-check"


def test_playwright_supports_websocket_routing():
    from importlib.metadata import version

    major, minor = (int(p) for p in version("playwright").split(".")[:2])
    assert (major, minor) >= (1, 48), "route_web_socket 은 Playwright 1.48 이상이 필요하다"


def test_chromium_launches_headless():
    from playwright.sync_api import sync_playwright

    with sync_playwright() as p:
        browser = p.chromium.launch()
        page = browser.new_page()
        page.set_content("<p id='ok'>ok</p>")
        assert page.inner_text("#ok") == "ok"
        browser.close()


def test_image_and_yaml_libraries_import():
    for name in ("PIL.Image", "yaml"):
        importlib.import_module(name)


def test_skill_scripts_package_is_importable():
    assert (SKILL_DIR / "SKILL.md").parent.is_dir()
    importlib.import_module("scripts")
    for sub in ("references/criteria", "templates", "demo", "scripts/report"):
        assert (SKILL_DIR / sub).is_dir(), sub
