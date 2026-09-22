"""schemas/ が domain/ から生成され、未定義フィールドを禁止していること。"""

from __future__ import annotations

import json
from pathlib import Path

import jsonschema

from speclino.schemas import SCHEMA_MODELS, export_schemas

REPO_ROOT = Path(__file__).resolve().parents[2]


def _all_objects(node: object) -> list[dict[str, object]]:
    out: list[dict[str, object]] = []
    if isinstance(node, dict):
        if node.get("type") == "object" and "properties" in node:
            out.append(node)
        for v in node.values():
            out.extend(_all_objects(v))
    elif isinstance(node, list):
        for v in node:
            out.extend(_all_objects(v))
    return out


def test_export_and_additional_properties_false(tmp_path: Path) -> None:
    written = export_schemas(tmp_path)
    assert {p.stem.replace(".schema", "") for p in written} == {"expr", *SCHEMA_MODELS}
    for p in written:
        schema = json.loads(p.read_text())
        jsonschema.Draft202012Validator.check_schema(schema)
        for obj in _all_objects(schema):
            assert obj.get("additionalProperties") is False, f"{p.name}: {obj.get('title')}"


def test_committed_schemas_are_up_to_date(tmp_path: Path) -> None:
    """`speclino schemas` の出力と schemas/ の内容が一致する（手編集の検出）。"""
    committed = REPO_ROOT / "schemas"
    if not committed.exists():
        return
    export_schemas(tmp_path)
    for p in tmp_path.glob("*.schema.json"):
        assert (committed / p.name).read_text() == p.read_text(), f"{p.name} が古い: make schemas"
