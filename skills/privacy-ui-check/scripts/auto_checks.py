"""자동 점검 후보(설계서 3-1, 2장 4단계).

수집물(원본 폴더)에서 기계로 판단할 수 있는 항목의 1차 후보를 만든다. 최종 판정·위험도·문구는 Claude 가 검토해 정한다.
후보 형식: {"key", "item_ids", "menu"(메뉴 ID 또는 COMMON), "verdict_hint", "evidence"[...], "boxes"[{selector,label}]}

사용: python -m scripts.auto_checks --raw-dir <밖>/raw --menu-map menu_map.json --out candidates.json
"""
from __future__ import annotations

import argparse
import json
import re
from dataclasses import dataclass, field
from html.parser import HTMLParser
from pathlib import Path
from urllib.parse import parse_qsl, urlparse

from scripts.redact import is_secret_name

COMMON = "COMMON"

ITEM_IDS = {
    "password-in-url": ["SAFE-07-01-02", "SAFE-07-01-01"],
    "no-https": ["SAFE-07-04-01"],
    "session-cookie-flags": ["SAFE-07-01-03"],
    "token-in-storage": ["SAFE-07-01-03"],
    "html-comment-pii": ["SAFE-06-03-05"],
    "pii-plain-in-page": ["SAFE-07-01-04", "SAFE-12-01-03"],
    "api-overexposure": ["SAFE-12-01-03"],
    "directory-listing": ["SAFE-06-03-03"],
    "admin-exposed": ["SAFE-06-03-02", "SAFE-06-01-01"],
    "public-page-pii": ["SAFE-06-03-04"],
    "marketing-prechecked": ["PIPA-22-02", "NET-50-01"],
    "ssn-collected": ["PIPA-24B-02", "NET-23B-01"],
    "privacy-policy-name": ["PIPA-30-02"],
    "privacy-policy-missing": ["PIPA-30-01"],
}
ADVICE = {"token-in-storage", "privacy-policy-name"}  # 해석·권고 수준

RRN = re.compile(r"\b\d{6}-[1-4]\d{6}\b")
CARD = re.compile(r"\b\d{4}[- ]\d{4}[- ]\d{4}[- ]\d{4}\b")
EMAIL = re.compile(r"[\w.+-]+@[\w-]+\.[\w.-]+")
MOBILE = re.compile(r"\b01[016789]-?\d{3,4}-?\d{4}\b")
SSN_FIELD = re.compile(r"jumin|ssn|rrn|resident|주민등록번호|주민번호|^\d{6}-\d{7}$|^0{6}-0{7}$", re.I)
MARKETING = re.compile(r"마케팅|광고|홍보|이벤트|수신")
TOKEN_KEY = re.compile(r"token|jwt|auth|session|access|refresh", re.I)
SESSION_COOKIE = re.compile(r"sid|sess|session|jsessionid|token|auth", re.I)
ADMIN_PATH = re.compile(r"/(admin|manager|mgr|adm|backoffice)(/|$)", re.I)
POLICY_PAGE = re.compile(r"처리방침|보호정책|privacy", re.I)


# ── DOM 읽기 ──
class _Dom(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.comments, self.texts, self.scripts, self.links, self.inputs, self.forms = [], [], [], [], [], []
        self.title, self.h1 = "", ""
        self._stack, self._label, self._label_inputs, self._link = [], None, [], None

    def handle_comment(self, data):
        self.comments.append(data.strip())

    def handle_starttag(self, tag, attrs):
        a = {k: (v or "") for k, v in attrs}
        if tag not in ("input", "br", "img", "meta", "link"):
            self._stack.append(tag)
        if tag == "form":
            self.forms.append({"method": a.get("method", "get").lower(), "action": a.get("action", ""), "inputs": []})
        elif tag == "label":
            self._label, self._label_inputs = [], []
        elif tag == "a":
            self._link = {"href": a.get("href", ""), "text": ""}
        elif tag == "input":
            inp = dict(a, _label="", _form=len(self.forms) - 1 if "form" in self._stack else None)
            self.inputs.append(inp)
            if self.forms and "form" in self._stack:
                self.forms[-1]["inputs"].append(inp)
            if self._label is not None:
                self._label_inputs.append(inp)

    def handle_endtag(self, tag):
        if tag == "label" and self._label is not None:
            text = " ".join(self._label).strip()
            for inp in self._label_inputs:
                inp["_label"] = text
            self._label = None
        elif tag == "a" and self._link is not None:
            self.links.append(self._link)
            self._link = None
        if tag in self._stack:
            while self._stack and self._stack.pop() != tag:
                pass

    def handle_data(self, data):
        cur = self._stack[-1] if self._stack else ""
        if cur == "script":
            self.scripts.append(data)
            return
        if cur == "style":
            return
        if cur == "title":
            self.title += data
        if cur == "h1":
            self.h1 += data
        if data.strip():
            self.texts.append(data.strip())
            if self._label is not None:
                self._label.append(data.strip())
            if self._link is not None:
                self._link["text"] += data.strip()


@dataclass
class PageRecord:
    menu: dict
    dom: str = ""
    cookies: list = field(default_factory=list)
    storage: dict = field(default_factory=dict)
    headers: dict = field(default_factory=dict)
    har: dict = field(default_factory=dict)
    title: str = ""

    @classmethod
    def from_raw(cls, folder: Path, menu: dict) -> "PageRecord":
        read = lambda n: json.loads((folder / n).read_text(encoding="utf-8"))  # noqa: E731
        meta = read("meta.json")
        return cls(menu=menu, dom=(folder / "dom.html").read_text(encoding="utf-8"), cookies=read("cookies.json"),
                   storage=read("storage.json"), headers=read("headers.json"), har=read("network.har"),
                   title=meta.get("title", ""))

    @property
    def url(self) -> str:
        return self.headers.get("final_url") or self.menu["url"]

    def parsed(self) -> _Dom:
        if not hasattr(self, "_dom"):
            d = _Dom()
            d.feed(self.dom)
            self._dom = d
        return self._dom


def mask_pii(text: str) -> str:
    """증적 문구에 들어갈 개인정보를 부분 가림한다(candidates.json 은 작업 폴더에 남는다)."""
    text = RRN.sub(lambda m: m.group(0)[:8] + "******", text)
    text = CARD.sub(lambda m: m.group(0)[:4] + "-****-****-" + m.group(0)[-4:], text)
    text = MOBILE.sub(lambda m: m.group(0)[:3] + "-****-" + m.group(0)[-4:], text)
    return EMAIL.sub(lambda m: m.group(0)[:2] + "***@" + m.group(0).split("@", 1)[1], text)


def _cand(key, menu, evidence, boxes=()):
    return {"key": key, "item_ids": ITEM_IDS[key], "menu": menu,
            "verdict_hint": "권고" if key in ADVICE else "취약",
            "evidence": [mask_pii(e) for e in evidence], "boxes": list(boxes)}


def _input_selector(inp: dict) -> str:
    for attr in ("id", "name"):
        if inp.get(attr):
            return f'input[{attr}="{inp[attr]}"]'
    return "input"


# ── 화면 단위 탐지 ──
def check_page(r: PageRecord) -> list[dict]:
    d, mid, out = r.parsed(), r.menu["id"], []

    get_pw_forms = [f for f in d.forms if f["method"] == "get" and any(i.get("type") == "password" for i in f["inputs"])]
    pw_urls = [e["request"]["url"] for e in r.har.get("log", {}).get("entries", [])
               if any(is_secret_name(k) for k, _ in parse_qsl(urlparse(e["request"]["url"]).query))]
    if get_pw_forms or pw_urls:
        out.append(_cand("password-in-url", mid,
                         [f"GET 폼 action={f['action']}" for f in get_pw_forms] + [f"요청 URL: {u}" for u in pw_urls[:3]],
                         [{"selector": f'form[action="{f["action"]}"]', "label": "GET 로그인 폼"} for f in get_pw_forms]))

    hits = [c for c in d.comments if EMAIL.search(c) or MOBILE.search(c) or RRN.search(c)]
    if hits:
        out.append(_cand("html-comment-pii", mid, [f"HTML 주석: {c[:80]}" for c in hits]))

    plain = [(i, i.get("value", "")) for i in d.inputs if RRN.search(i.get("value", "")) or CARD.search(i.get("value", ""))]
    script_hits = [s for s in d.scripts if RRN.search(s) or CARD.search(s)]
    text_hits = [t for t in d.texts if RRN.search(t) or CARD.search(t)]
    if plain or script_hits or text_hits:
        out.append(_cand("pii-plain-in-page", mid,
                         [f"입력값 {_input_selector(i)} (type={i.get('type', 'text')})" for i, _ in plain]
                         + ["JS 변수에 주민등록번호·카드번호 원문"] * bool(script_hits)
                         + [f"화면 글자: {t[:40]}" for t in text_hits],
                         [{"selector": _input_selector(i), "label": "평문 고유식별정보"} for i, _ in plain
                          if i.get("type") != "hidden"]))

    api = []
    for e in r.har.get("log", {}).get("entries", []):
        content = (e.get("response") or {}).get("content") or {}
        if "json" in (content.get("mimeType") or "") and (RRN.search(content.get("text") or "") or CARD.search(content.get("text") or "")):
            api.append(e["request"]["url"])
    if api:
        out.append(_cand("api-overexposure", mid, [f"API 응답 원문: {u}" for u in dict.fromkeys(api)]))

    if (d.title or r.title).strip().lower().startswith("index of") or d.h1.strip().lower().startswith("index of"):
        out.append(_cand("directory-listing", mid, [f"디렉터리 목록: {r.url}"], [{"selector": "h1", "label": "디렉터리 목록"}]))

    has_pw = any(i.get("type") == "password" for i in d.inputs)
    if ADMIN_PATH.search(urlparse(r.url).path) and has_pw and r.menu.get("auth") != "user":
        out.append(_cand("admin-exposed", mid, [f"로그인 없이 열리는 관리자 로그인 화면: {r.url}"],
                         [{"selector": "form", "label": "관리자 로그인"}]))

    is_policy = POLICY_PAGE.search(d.title or r.title or "") or any(POLICY_PAGE.search(p) for p in r.menu.get("path", []))
    if r.menu.get("auth") != "user" and not is_policy:
        pii = [t for t in d.texts if MOBILE.search(t) or EMAIL.search(t)]
        if pii:
            out.append(_cand("public-page-pii", mid, [f"공개 화면 글자: {t[:60]}" for t in pii]))

    pre = [i for i in d.inputs if i.get("type") == "checkbox" and "checked" in i
           and MARKETING.search(i["_label"]) and "동의" in i["_label"]]
    if pre:
        out.append(_cand("marketing-prechecked", mid, [f"미리 체크된 동의: {i['_label'][:60]}" for i in pre],
                         [{"selector": _input_selector(i), "label": "미리 체크된 마케팅 동의"} for i in pre]))

    ssn = [i for i in d.inputs if i.get("type", "text") in ("text", "tel", "number", "")
           and "readonly" not in i and "disabled" not in i and not i.get("value")
           and any(SSN_FIELD.search(i.get(k, "")) for k in ("name", "id", "placeholder", "_label"))]
    if ssn:
        out.append(_cand("ssn-collected", mid, [f"주민등록번호 입력칸: {_input_selector(i)}" for i in ssn],
                         [{"selector": _input_selector(i), "label": "주민등록번호 입력"} for i in ssn]))

    keys = [f"{area}.{k}" for area, kv in r.storage.items() for k, v in kv.items()
            if TOKEN_KEY.search(k) or str(v).startswith("eyJ") or EMAIL.search(str(v))]
    if keys:
        out.append(_cand("token-in-storage", mid, [f"브라우저 저장소: {k}" for k in keys]))
    return out


# ── 사이트 단위 탐지 ──
def check_site(records: list[PageRecord]) -> list[dict]:
    out = []
    http = [r.menu["id"] for r in records if r.url.startswith("http://")]
    if http:
        out.append(_cand("no-https", COMMON, [f"평문 HTTP 화면 {len(http)}개: {', '.join(http[:5])}"]))

    https_site = any(r.url.startswith("https://") for r in records)
    weak = {}
    for r in records:
        for c in r.cookies:
            if SESSION_COOKIE.search(c.get("name", "")) and (not c.get("httpOnly") or (https_site and not c.get("secure"))):
                weak[c["name"]] = c
    if weak:
        out.append(_cand("session-cookie-flags", COMMON,
                         [f"쿠키 {n}: HttpOnly={c.get('httpOnly')}, Secure={c.get('secure')}" for n, c in weak.items()]))

    policy_links = {re.sub(r"\s+", " ", l["text"]).strip() for r in records for l in r.parsed().links
                    if "개인정보" in l["text"] and re.search(r"방침|정책", l["text"])}
    if not policy_links:
        out.append(_cand("privacy-policy-missing", COMMON, ["어느 화면에서도 개인정보 처리방침 링크를 찾지 못함"]))
    elif "개인정보 처리방침" not in policy_links:
        out.append(_cand("privacy-policy-name", COMMON, [f"링크 이름: {', '.join(sorted(policy_links))}"]))
    return out


def run(raw_dir: Path, menu_map: dict) -> list[dict]:
    records = []
    for m in menu_map["menus"]:
        folder = Path(raw_dir) / m["id"]
        if m["status"] == "visit" and (folder / "meta.json").exists():
            records.append(PageRecord.from_raw(folder, m))
    cands = [c for r in records for c in check_page(r)]
    return cands + check_site(records)


def main() -> None:
    from scripts.models import load_menu_map

    ap = argparse.ArgumentParser(description="수집물에서 자동 점검 후보를 만든다")
    ap.add_argument("--raw-dir", required=True, type=Path)
    ap.add_argument("--menu-map", required=True, type=Path)
    ap.add_argument("--out", required=True, type=Path)
    a = ap.parse_args()
    cands = run(a.raw_dir, load_menu_map(a.menu_map))
    a.out.write_text(json.dumps(cands, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"후보 {len(cands)}개 → {a.out}")


if __name__ == "__main__":
    main()
