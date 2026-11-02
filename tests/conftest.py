"""공용 fixture: 데모 사이트 서버(설계서 8-1)."""
import json
import threading
import urllib.request
from dataclasses import dataclass

import pytest

from demo.server import make_server

DEMO_USER = "hong"
DEMO_PASS = "demo-pass"  # 데모 사이트 전용 테스트 계정(demo/server.py 의 시드 값)


@dataclass
class Demo:
    base: str

    def url(self, path: str) -> str:
        return self.base + path

    def writes(self) -> list[dict]:
        with urllib.request.urlopen(self.url("/__writes")) as r:
            return json.loads(r.read())

    def reset(self) -> None:
        req = urllib.request.Request(self.url("/__reset"), method="POST", data=b"")
        urllib.request.urlopen(req).close()

    def login_url(self, next_path: str = "/faq") -> str:
        # 기본 착지 화면은 쓰기 함정이 없는 /faq: 로그인 단계에는 guard 가 꺼져 있으므로(설계서 7-2)
        return self.url(f"/login/submit?id={DEMO_USER}&pw={DEMO_PASS}&next={next_path}")


@pytest.fixture(scope="session")
def demo_server():
    server = make_server("127.0.0.1", 0)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    demo = Demo(f"http://127.0.0.1:{server.server_address[1]}")
    yield demo
    server.shutdown()


@pytest.fixture
def demo(demo_server):
    demo_server.reset()
    return demo_server
