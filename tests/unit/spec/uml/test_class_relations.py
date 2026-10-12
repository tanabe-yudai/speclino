"""クラス図の関係線（grammar.md 4.1 節）: 構文・正規化・ID・参照・拒否・往復。"""

from __future__ import annotations

import pytest

from speclino.domain.uml import CompiledSpec
from speclino.spec.uml.compile import compile_spec, semantic_dump
from speclino.spec.uml.render import render
from tests.unit.spec.uml.samples import FULL, block

_CLASSES = (
    "    class Base",
    "    class Child",
    "    class Part",
    "    class Kind {",
    "        <<enumeration>>",
    "        A",
    "    }",
    "    class registry {",
    "        <<module>>",
    "        approve(a)",
    "    }",
)


def _md(*relations: str, req: str = "") -> str:
    head = "| ID | level | 要求 | 形式化 |\n| --- | --- | --- | --- |\n" + req + "\n" if req else ""
    return head + block("%%@ id: v", "classDiagram", *_CLASSES, *relations)


def _ok(md: str) -> CompiledSpec:
    res = compile_spec(md, domain=None, legacy_ir=[])
    assert res.compiled is not None, [(d.code, d.message) for d in res.diagnostics]
    assert res.diagnostics == []
    return res.compiled


def _codes(md: str) -> list[str]:
    res = compile_spec(md, domain=None, legacy_ir=[])
    assert res.compiled is None
    return [d.code for d in res.diagnostics]


def test_five_kinds_are_normalized_to_source_and_target() -> None:
    c = _ok(
        _md(
            "    Base <|-- Child : [r1] 継承",
            "    Child *-- Part : [r2]",
            "    Child o-- Part : [r3] 集約",
            "    registry --> Part : [r4]",
            "    registry ..> Kind : [r5] 使う",
        )
    )
    assert [(r.id, r.element_id, r.kind, r.source, r.target) for r in c.relations] == [
        ("v#r1", "r1", "inheritance", "Child", "Base"),
        ("v#r2", "r2", "composition", "Child", "Part"),
        ("v#r3", "r3", "aggregation", "Child", "Part"),
        ("v#r4", "r4", "association", "registry", "Part"),
        ("v#r5", "r5", "dependency", "registry", "Kind"),
    ]
    assert all(r.level == "mandatory" for r in c.relations)


def test_relation_may_precede_class_declaration() -> None:
    md = block("%%@ id: v", "classDiagram", "    A ..> B : [r1]", "    class A", "    class B")
    assert [r.id for r in _ok(md).relations] == ["v#r1"]


def test_multiplicity_and_label_text_are_display_only() -> None:
    plain = _ok(_md("    Child --> Part : [r1]"))
    decorated = _ok(_md('    Child "1" --> "0..*" Part : [r1] 部品を参照する'))
    assert semantic_dump(plain) == semantic_dump(decorated)
    assert plain.ir_hash == decorated.ir_hash
    for mult in ('"*"', '"n"', '"1..n"', '"0..1"', '"12"'):
        _ok(_md(f"    Child {mult} --> Part"))


def test_relation_without_id_is_checked_but_not_referenceable() -> None:
    c = _ok(_md("    Child --> Part", "    Child ..> Part"))
    assert [(r.id, r.element_id, r.level) for r in c.relations] == [
        ("v#Child-association-Part", None, "mandatory"),
        ("v#Child-dependency-Part", None, "mandatory"),
    ]
    assert "E_REF_UNKNOWN" in _codes(
        _md("    Child --> Part", req="| R1 | advisory | x | v#Child-association-Part |")
    )


def test_level_follows_referencing_rows() -> None:
    c = _ok(
        _md(
            "    Child --> Part : [r1]",
            "    Child ..> Part : [r2]",
            req="| R1 | advisory | 参照 | v#r1 |\n| R2 | mandatory | 依存 | v#r2 |",
        )
    )
    assert [(r.id, r.level) for r in c.relations] == [("v#r1", "advisory"), ("v#r2", "mandatory")]


def test_no_relations_keeps_the_ir_unchanged() -> None:
    c = _ok(FULL)
    assert c.relations == []
    assert "relations" not in c.model_dump(mode="json")
    assert "relations" not in semantic_dump(c)


@pytest.mark.parametrize(
    "line",
    [
        "    Child --|> Base",
        "    Part --* Child",
        "    Part --o Child",
        "    Part <-- Child",
        "    Part <.. Child",
        "    Child ..|> Base",
        "    Base <|.. Child",
        "    Child -- Part",
        "    Child .. Part",
        "    Child <|--|> Part",
        "    Child * -- Part",
        "    Child o -- Part",
        "    Child Part",
    ],
)
def test_rejects_other_arrows(line: str) -> None:
    assert _codes(_md(line)) == ["E_UNKNOWN_ARROW"]


def test_rejects_undeclared_end() -> None:
    assert _codes(_md("    Child --> Missing : [r1]")) == ["E_UNKNOWN_CLASS"]
    assert _codes(_md("    Missing <|-- Child")) == ["E_UNKNOWN_CLASS"]


@pytest.mark.parametrize(
    "line",
    [
        "    registry <|-- Child",  # 継承の端にモジュール
        "    Kind <|-- Child",  # 継承の端に列挙
        "    Child *-- registry",  # 所有の target にモジュール
        "    Kind --> Part",  # 列挙は source にならない
        "    Kind ..> Part",
    ],
)
def test_rejects_wrong_end_stereotype(line: str) -> None:
    assert _codes(_md(line)) == ["E_RELATION_END"]


def test_rejects_duplicate_ids() -> None:
    assert _codes(_md("    Child --> Part : [r1]", "    Child ..> Part : [r1]")) == [
        "E_DUPLICATE_ID"
    ]
    assert _codes(_md("    Child --> Part : [Base]")) == ["E_DUPLICATE_ID"]
    assert _codes(_md("    Child --> Part", "    Child --> Part")) == ["E_DUPLICATE_ID"]
    # ID があれば同じ端・種類の関係を並べられる
    _ok(_md("    Child --> Part : [r1]", "    Child --> Part : [r2]"))


@pytest.mark.parametrize(
    ("line", "code"),
    [
        ('    Child "x" --> Part', "E_UNEXPECTED_TOKEN"),
        ('    Child "1 --> Part', "E_UNEXPECTED_TOKEN"),
        ('    Child "*..1" --> Part', "E_UNEXPECTED_TOKEN"),
        ("    Child --> Part : [r1.x]", "E_UNEXPECTED_TOKEN"),
        ("    Child --> Part extra", "E_UNEXPECTED_TOKEN"),
        ("    Child : +int x", "E_UNSUPPORTED_SYNTAX"),
        ('    note for Child "メモ"', "E_UNSUPPORTED_SYNTAX"),
    ],
)
def test_rejects_malformed_relation_lines(line: str, code: str) -> None:
    assert code in _codes(_md(line))


def test_render_roundtrip_keeps_relations() -> None:
    md = _md(
        "    Base <|-- Child : [r1] 継承",
        '    Child "1" *-- "*" Part : [r2]',
        "    Child o-- Part",
        "    registry --> Part",
        "    registry ..> Kind : [r5]",
        req="| R1 | advisory | 参照 | v#r1 |",
    )
    c = _ok(md)
    again = _ok(render(c))
    assert semantic_dump(again) == semantic_dump(c)
    assert again.ir_hash == c.ir_hash
