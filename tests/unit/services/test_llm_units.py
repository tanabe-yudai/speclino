"""LLM 支援の評価の単位（S70、設計 6.3 の 1）。"""

from __future__ import annotations

from pathlib import Path

from speclino.domain.policy_params import LlmChecklistParams
from speclino.services.llm_units import extract_units

_SRC = '''
from fastapi import APIRouter

router = APIRouter()


class Orders:
    def cancel(self, order_id):
        """注文を取り消す。"""
        raise ValueError("注文が見つかりません。注文番号を確かめてください")


@router.post("/orders")
async def create_order(body):
    return api_error(400, "bad", "入力が不正")


def helper():
    log("debug only")
'''


def _params(kind: str, **over: object) -> LlmChecklistParams:
    return LlmChecklistParams.model_validate(
        {"items": [{"id": "a", "question": "q"}], "unit_kind": kind, **over}
    )


def _write(tmp_path: Path, text: str = _SRC) -> Path:
    (tmp_path / "app").mkdir(exist_ok=True)
    (tmp_path / "app" / "api.py").write_text(text, encoding="utf-8")
    return tmp_path


def test_functions_are_units_with_qualified_symbols(tmp_path: Path) -> None:
    got = extract_units(_write(tmp_path), _params("function"), ["app/**/*.py"])
    assert [u.symbol for u in got.units] == [
        "app/api.py::Orders.cancel",
        "app/api.py::create_order",
        "app/api.py::helper",
    ]
    assert got.units[1].text.startswith('@router.post("/orders")')


def test_whitespace_and_comments_do_not_change_the_hash(tmp_path: Path) -> None:
    first = extract_units(_write(tmp_path), _params("function"), ["app/*.py"]).units
    reformatted = _SRC.replace(
        "    def cancel(self, order_id):",
        "    # コメントを足す\n    def cancel(self,   order_id):  # 末尾のコメント",
    )
    second = extract_units(_write(tmp_path, reformatted), _params("function"), ["app/*.py"]).units
    assert [u.unit_hash for u in first] == [u.unit_hash for u in second]
    changed = _SRC.replace("注文番号を確かめてください", "もう一度")
    third = extract_units(_write(tmp_path, changed), _params("function"), ["app/*.py"]).units
    assert third[0].unit_hash != first[0].unit_hash
    assert third[1].unit_hash == first[1].unit_hash


def test_endpoints_are_only_route_decorated_functions(tmp_path: Path) -> None:
    got = extract_units(_write(tmp_path), _params("endpoint"), ["app/*.py"])
    assert [(u.symbol, u.kind) for u in got.units] == [("app/api.py::create_order", "endpoint")]


def test_string_literals_of_the_named_calls(tmp_path: Path) -> None:
    root = _write(tmp_path)
    got = extract_units(
        root, _params("string_literal", call_names=["ValueError", "api_error"]), ["app/*.py"]
    )
    assert [u.text for u in got.units] == [
        "注文が見つかりません。注文番号を確かめてください",
        "bad",
        "入力が不正",
    ]
    assert got.units[0].symbol == (
        'app/api.py::Orders.cancel::"注文が見つかりません。注文番号を確かめてください"'
    )
    everything = extract_units(root, _params("string_literal"), ["app/*.py"])
    assert "debug only" in [u.text for u in everything.units]


def test_unparseable_files_are_reported_not_skipped(tmp_path: Path) -> None:
    root = _write(tmp_path, "def broken(:\n")
    got = extract_units(root, _params("function"), ["app/*.py"])
    assert got.units == [] and got.unreadable == ["app/api.py"]


def test_params_paths_override_rule_paths(tmp_path: Path) -> None:
    root = _write(tmp_path)
    (tmp_path / "other.py").write_text("def x():\n    pass\n", encoding="utf-8")
    got = extract_units(root, _params("function", paths=["other.py"]), ["app/*.py"])
    assert [u.symbol for u in got.units] == ["other.py::x"]
