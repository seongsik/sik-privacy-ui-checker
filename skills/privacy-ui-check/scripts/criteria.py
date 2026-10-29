"""점검 기준 YAML(설계서 4-1)을 읽는다."""
from __future__ import annotations

from pathlib import Path

import yaml

CRITERIA_DIR = Path(__file__).resolve().parents[1] / "references" / "criteria"


def load_criteria(directory: Path | None = None) -> dict[str, dict]:
    """기준 ID → 항목. 여러 파일에 같은 ID가 있으면 ValueError."""
    out: dict[str, dict] = {}
    for path in sorted((directory or CRITERIA_DIR).glob("*.yaml")):
        for item in yaml.safe_load(path.read_text(encoding="utf-8")) or []:
            cid = item["id"]
            if cid in out:
                raise ValueError(f"점검 기준 ID 중복: {cid} ({path.name})")
            out[cid] = item
    return out
