"""비밀번호·인증 값 가림(HAR, 요청 본문, 증적 문구).

가릴 이름(파라미터·JSON 키·폼 필드):
  - 비밀번호 계열: password·passwd·pwd 포함(j_password, userPassword, password_confirm, pwd2),
    pw·pass 로 끝나는 이름(userPw, loginPw, mbrPw, encPw, userPass, mem_pass), pw·pass 로 시작해 구분자·대문자·숫자가
    오는 이름(pw2, pass_word, passWord), 단어로 쓰인 pw(user_pw)
  - 인증 값 계열: token(access_token 등), secret, 세션 ID, API 키, OTP
  - 화면의 input[type=password] 이름(extra_names) — 사이트마다 다른 이름을 그대로 잡는다
passport_no, keyword, author 같은 이름은 잡지 않는다.
"""
from __future__ import annotations

import re

_SECRET = re.compile(
    r"(password|passwd|pwd|비밀번호|token|secret|sessid|session_?id|jsessionid|api[_\-]?key)"
    r"|(^|[_\-.])pw\d*($|[_\-.])|^otp$|^sid$", re.I)
_SECRET_SUFFIX = re.compile(r"(pw|pass)\d*$", re.I)
_SECRET_PREFIX = re.compile(r"^(pw|pass)(\d|[_\-.]|[A-Z]|$)")  # 원래 대소문자로 본다(passport 는 제외)
SECRET_HEADER = re.compile(r"cookie|authorization|token|auth|session|secret|api-?key", re.I)
_PATH_SESSION = re.compile(r"(;jsessionid=)[^?#&;\s\"'\\]*", re.I)
_PAIR = re.compile(r"((?:^|[?&\s\"'])([\w.\-]+)=)([^&#\s\"'\\]*)")
_JSON = re.compile(r'("([\w.\-]+)"\s*:\s*")((?:[^"\\]|\\.)*)(")')
_MULTIPART = re.compile(r'(name="([^"]+)"\r?\n(?:[^\r\n]+\r?\n)*\r?\n)([^\r\n]*)')


def is_secret_name(name: str, extra_names=()) -> bool:
    return bool(name in extra_names or _SECRET.search(name) or _SECRET_SUFFIX.search(name)
                or _SECRET_PREFIX.match(name))


def scrub_text(text: str, extra_names=()) -> str:
    def mask(m):
        if not is_secret_name(m.group(2), extra_names):
            return m.group(0)
        return m.group(1) + "***" + (m.group(4) if m.re is _JSON else "")
    for pattern in (_PAIR, _JSON, _MULTIPART):
        text = pattern.sub(mask, text)
    return _PATH_SESSION.sub(r"\1***", text)


def scrub_har(node, extra_names=(), _list_of: str = ""):
    """HAR(dict)의 비밀번호·인증 값, 쿠키 값, 인증 헤더 값을 *** 로 바꾼다.
    name/value 쌍(쿠키·헤더·쿼리·폼), URL(;jsessionid= 포함)·urlencoded·JSON·multipart 본문 문자열을 모두 본다."""
    if isinstance(node, dict):
        out = {k: scrub_har(v, extra_names, k) for k, v in node.items()}
        name = out.get("name")
        if isinstance(name, str) and "value" in out and (
                _list_of == "cookies" or is_secret_name(name, extra_names)
                or (_list_of == "headers" and SECRET_HEADER.search(name))):
            out["value"] = "***"
        return out
    if isinstance(node, list):
        return [scrub_har(v, extra_names, _list_of) for v in node]
    if isinstance(node, str):
        return scrub_text(node, extra_names)
    return node
