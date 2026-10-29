"""점검 기준 YAML(설계서 4-1) 스키마 테스트."""
import re

import pytest

from scripts.criteria import CRITERIA_DIR, load_criteria
from scripts.models import CATEGORIES, RISKS

ID_RE = re.compile(r"^(SAFE|PIPA|NET|STD)-\d{2}[A-Z]?(-\d{2})?-\d{2}$")
TEXT_FIELDS = ("title", "requirement", "applies_when", "method", "pass", "fail", "remediation", "evidence", "notes")


@pytest.fixture(scope="module")
def criteria():
    return load_criteria()


def test_all_items_loaded(criteria):
    print(f"{len(criteria)} items")
    assert len(criteria) == 273


def test_ids_follow_rule(criteria):
    bad = [i for i in criteria if not ID_RE.match(i)]
    assert bad == []


def test_every_item_has_required_text(criteria):
    missing = [(i, k) for i, c in criteria.items() for k in TEXT_FIELDS if not c.get(k)]
    assert missing == []


def test_every_item_has_basis_list(criteria):
    assert all(isinstance(c["basis"], list) and c["basis"] for c in criteria.values())


def test_enumerated_fields(criteria):
    for i, c in criteria.items():
        assert c["type"] in ("관찰", "능동", "서면"), i
        assert c["category"] in CATEGORIES, i
        assert c["strength"] in ("의무", "권고", "외부참고"), i
        assert c["default_risk"] in RISKS, i


def test_written_items_use_management_category(criteria):
    assert all(c["category"] == "관리체계(서면)" for c in criteria.values() if c["type"] == "서면")


def test_active_items_count(criteria):
    assert sum(c["type"] == "능동" for c in criteria.values()) == 10


def test_duplicate_ids_are_rejected(tmp_path):
    (tmp_path / "a.yaml").write_text(
        "- {id: SAFE-05-06-01, title: t}\n- {id: SAFE-05-06-01, title: t}\n", encoding="utf-8"
    )
    with pytest.raises(ValueError, match="SAFE-05-06-01"):
        load_criteria(tmp_path)


def test_default_dir_is_inside_skill():
    assert CRITERIA_DIR.parts[-3:] == ("privacy-ui-check", "references", "criteria")
