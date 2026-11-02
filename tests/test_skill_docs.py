"""SKILL.md·참조 문서 무결성 테스트: 경로·명령 옵션·능동 항목·착수 질문지가 실제 코드와 맞는지."""
import json
import re
from pathlib import Path

import pytest
import yaml

from scripts.criteria import load_criteria
from scripts.models import load_intake

SKILL = Path(__file__).resolve().parents[1] / "skills" / "privacy-ui-check"
DOC = (SKILL / "SKILL.md").read_text(encoding="utf-8")


def test_frontmatter_has_name_and_description():
    front = yaml.safe_load(DOC.split("---")[1])
    assert front["name"] == "privacy-ui-check"
    assert len(front["description"]) > 80


def test_referenced_files_exist():
    docs = [DOC] + [p.read_text(encoding="utf-8") for p in (SKILL / "references").glob("*.md")]
    paths = set()
    for text in docs:
        paths |= set(re.findall(r"\((references/[\w./-]+|templates/[\w./-]+)\)", text))
        paths |= set(re.findall(r"`((?:references|templates|scripts|demo)/[\w./-]+\.(?:md|json|py|yaml))`", text))
    missing = [p for p in sorted(paths) if not (SKILL / p).exists()]
    assert paths and missing == []


def _commands():
    """SKILL.md 의 `python -m scripts.X ...` 명령(줄 이어쓰기 포함)과 그 옵션."""
    blocks = re.findall(r"```bash\n(.*?)```", DOC, re.S)
    for block in blocks:
        for cmd in block.replace("\\\n", " ").splitlines():
            m = re.search(r"python -m (scripts[\w.]+)", cmd)
            if m:
                yield m.group(1), set(re.findall(r"(--[a-z][a-z-]*)", cmd))


@pytest.mark.parametrize("module,flags", list(_commands()), ids=lambda v: v if isinstance(v, str) else "")
def test_documented_commands_use_real_modules_and_flags(module, flags):
    src = (SKILL / (module.replace(".", "/") + ".py")).read_text(encoding="utf-8")
    assert "def main(" in src
    unknown = [f for f in flags if f'"{f}"' not in src]
    assert unknown == [], f"{module}: {unknown}"


def test_skill_text_mentions_flags_used_in_prose():
    # 본문에서 안내하는 옵션도 실제로 있어야 한다
    collect_src = (SKILL / "scripts" / "collect.py").read_text(encoding="utf-8")
    for flag in ("--only", "--allow-post", "--boxes"):
        assert flag in DOC and f'"{flag}"' in collect_src, flag


def test_active_tests_doc_covers_every_active_item():
    text = (SKILL / "references" / "active-tests.md").read_text(encoding="utf-8")
    active = [cid for cid, c in load_criteria().items() if c["type"] == "능동"]
    assert len(active) == 10
    assert [cid for cid in active if cid not in text] == []


def test_intake_questionnaire_matches_template():
    example = load_intake(SKILL / "templates" / "intake.example.json")
    keys = ["target_url", "inspector", "account_role", "allowed_domains"] + \
           [f"conditions.{k}" for k in example["conditions"]] + [f"approval.{k}" for k in ("approver",)]
    assert [k for k in keys if f"`{k}`" not in DOC] == []


def test_boxes_template_shape():
    data = json.loads((SKILL / "templates" / "boxes.example.json").read_text(encoding="utf-8"))
    for menu_id, boxes in data.items():
        assert re.match(r"^M\d{2}-\d{2}$", menu_id)
        assert all({"n", "selector", "label"} <= set(b) for b in boxes)
