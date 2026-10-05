"""compile（図をまたぐ解決・ハッシュ・旧形式・全か無か）。設計 4.2–4.3。"""

from __future__ import annotations

import time
from pathlib import Path

import pytest

from speclino.domain.domain_spec import DomainSpec
from speclino.domain.knowledge import BODY_MAX_BYTES
from speclino.spec.loader import content_hash, load_spec_ir
from speclino.spec.uml.compile import (
    PARSER_VERSION,
    compile_spec,
    compile_with_coverage,
    ir_hash_of,
)
from tests.unit.spec.uml.samples import ACTIVITY, CLASSES, FULL, REQUIREMENTS, SEQUENCE, block

ROOT = Path(__file__).resolve().parents[4]
LEGACY = load_spec_ir(ROOT / "specs/ir/gate_decide.spec-ir.json")

# 正常な文書と、さまざまな誤りを含む文書（網羅のテストに使う）
CORPUS = [
    FULL,
    FULL.replace("[m2] charge(order)", "[m2] 課金"),
    FULL.replace("alt [g1] before.order.qty > 0", "par [g1] 並行"),
    FULL.replace("S->>I: [m1]", "S-xI: [m1]"),
    FULL.replace("[e2] before.order.paid", "[e2] 支払い済み"),
    FULL.replace("    end\n", ""),
    FULL.replace("classDiagram", "erDiagram"),
    FULL.replace("%%@ id: vocab", "%%{init: {}}%%"),
    FULL.replace("| R3 | mandatory |", "| R3 | must |"),
    FULL.replace("cancel-flow#r", "cancel-flow#nothing"),
    FULL + "```mermaid\n%%@ id: x\nsequenceDiagram\n",
    FULL.replace("    r -->|[e4]| z", '    r -->|[e4] "x" & y| z'),
]


@pytest.mark.parametrize("md", CORPUS)
def test_every_token_is_classified(md: str) -> None:
    res, coverage = compile_with_coverage(md, domain=None, legacy_ir=[])
    assert coverage.tokens, "トークンが 1 つも無い"
    assert coverage.unclassified() == []
    has_error = any(d.severity == "error" for d in res.diagnostics)
    marks = set(coverage.marks.values())
    assert ("error" in marks) <= has_error  # error に分類したトークンがあれば必ずエラーの診断がある
    assert (res.compiled is None) == has_error


def test_errors_have_spans() -> None:
    for md in CORPUS[1:]:
        res = compile_spec(md, domain=None, legacy_ir=[])
        assert res.compiled is None
        assert all(d.span is not None for d in res.diagnostics), md


def test_deterministic_hash() -> None:
    a = compile_spec(FULL, domain=None, legacy_ir=[LEGACY]).compiled
    b = compile_spec(FULL, domain=None, legacy_ir=[LEGACY]).compiled
    assert a is not None and b is not None
    assert a.model_dump_json() == b.model_dump_json()
    assert a.parser_version == PARSER_VERSION == "uml-0.1"
    assert a.source_hash == content_hash(FULL)
    assert a.ir_hash == ir_hash_of(a)
    # 見た目だけの違い（コメント・空白・別名）では ir_hash は変わらず、source_hash は変わる
    cosmetic = FULL.replace("    %% 注文まわりの語彙\n", "").replace("    S->>I", "  S->>I")
    c = compile_spec(cosmetic, domain=None, legacy_ir=[LEGACY]).compiled
    assert c is not None
    assert c.ir_hash == a.ir_hash and c.source_hash != a.source_hash
    # 意味が変われば ir_hash も変わる
    changed = FULL.replace("before.order.qty > 0", "before.order.qty > 1")
    d = compile_spec(changed, domain=None, legacy_ir=[LEGACY]).compiled
    assert d is not None and d.ir_hash != a.ir_hash


def test_requirement_row_without_reference_is_unformalized() -> None:
    c = compile_spec(FULL, domain=None, legacy_ir=[]).compiled
    assert c is not None
    rows = {r.id: r for r in c.requirements}
    assert rows["R3"].formalized_by == []
    assert rows["R1"].formalized_by == ["checkout#m1", "checkout#m2"]
    empty = FULL.replace("| R3 | mandatory | 監査ログを残す | - |", "| R3 | mandatory | 監査 |  |")
    c2 = compile_spec(empty, domain=None, legacy_ir=[]).compiled
    assert c2 is not None and c2.requirements[2].formalized_by == []


def test_reference_to_unknown_element_is_error() -> None:
    for bad in ("cancel-flow#nothing", "nodiagram#r", "checkout#r1"):
        res = compile_spec(FULL.replace("cancel-flow#r", bad), domain=None, legacy_ir=[])
        assert [d.code for d in res.diagnostics] == ["E_REF_UNKNOWN"], bad
    res = compile_spec(FULL.replace("cancel-flow#r", "ir:NOPE"), domain=None, legacy_ir=[])
    assert [d.code for d in res.diagnostics] == ["E_REF_UNKNOWN"]


def test_level_from_requirement_rows() -> None:
    c = compile_spec(FULL, domain=None, legacy_ir=[]).compiled
    assert c is not None
    levels = {t.id: t.level for t in c.traces}
    assert levels == {
        "checkout#OrderService.checkout": "mandatory",
        "cancel-flow#OrderService.cancel": "advisory",
    }
    assert {s.id: s.level for s in c.structure}["vocab#OrderService"] == "mandatory"
    assert c.state_machines[0].level == "mandatory"
    # advisory の行だけが参照するクラス → advisory
    md = FULL.replace("| R2 | advisory | 取消は返金する | cancel-flow#r |", "| R2 | advisory | x | vocab#OrderService |")
    c2 = compile_spec(md, domain=None, legacy_ir=[]).compiled
    assert c2 is not None
    assert {s.id: s.level for s in c2.structure}["vocab#OrderService"] == "advisory"


def test_legacy_ir_passthrough() -> None:
    md = REQUIREMENTS.replace("cancel-flow#r", f"ir:{LEGACY.rules[0].id}").replace(
        "checkout#m1, checkout#m2", "-"
    )
    c = compile_spec(md, domain=None, legacy_ir=[LEGACY]).compiled
    assert c is not None
    assert c.legacy == [LEGACY]
    assert c.legacy[0].model_dump() == LEGACY.model_dump()
    assert c.traces == [] and c.classes == []


def test_legacy_ir_is_validated_with_domain() -> None:
    domain = DomainSpec()  # 変数が無いので旧形式の IR の参照は解決できない
    res = compile_spec("", domain=domain, legacy_ir=[LEGACY])
    assert res.compiled is None
    assert any(d.code.startswith("E_LEGACY_") for d in res.diagnostics)


def test_guards_are_type_checked_with_domain() -> None:
    domain = DomainSpec(
        enums={"OrderStatus": ["PENDING", "PAID", "CANCELLED"]},
        opaque_types=[],
        variables={"order.qty": "integer", "order.paid": "boolean", "order.status": "OrderStatus"},
    )
    ok = compile_spec(FULL, domain=domain, legacy_ir=[])
    assert ok.compiled is not None, ok.diagnostics
    bad = FULL.replace("before.order.qty > 0", "before.order.paid > 0")
    assert "E_GUARD_TYPE" in [d.code for d in compile_spec(bad, domain=domain, legacy_ir=[]).diagnostics]
    mismatch = DomainSpec(enums={"OrderStatus": ["PENDING"]})
    codes = [d.code for d in compile_spec(CLASSES, domain=mismatch, legacy_ir=[]).diagnostics]
    assert codes == ["E_ENUM_MISMATCH"]


def test_class_names_are_unique_across_diagrams() -> None:
    other = CLASSES.replace("%%@ id: vocab", "%%@ id: vocab2")
    codes = [d.code for d in compile_spec(CLASSES + other, domain=None, legacy_ir=[]).diagnostics]
    assert "E_DUPLICATE_ID" in codes


def test_header_errors() -> None:
    def codes(md: str) -> list[str]:
        return [d.code for d in compile_spec(md, domain=None, legacy_ir=[]).diagnostics]

    assert "E_HEADER" in codes(block("classDiagram"))
    assert "E_HEADER" in codes(block("%%@ id: v", "%%@ closed: true", "classDiagram"))
    assert "E_HEADER" in codes(block("%%@ id: 1v", "classDiagram"))
    assert "E_HEADER" in codes(CLASSES + SEQUENCE.replace("OrderService.checkout", "checkout"))
    assert "E_HEADER" in codes(CLASSES + ACTIVITY.replace("closed: true", "closed: yes"))


# ---------------------------------------------------------------- 入力の大きさと計算量


def test_too_large_body_is_rejected_without_parsing() -> None:
    res = compile_spec("a" * (BODY_MAX_BYTES + 1), domain=None, legacy_ir=[])
    assert [d.code for d in res.diagnostics] == ["E_TOO_LARGE"]


def test_too_long_line_is_rejected() -> None:
    res = compile_spec("x" * 5000, domain=None, legacy_ir=[])
    assert [d.code for d in res.diagnostics] == ["E_LINE_TOO_LONG"]


_KB100 = 100 * 1024


@pytest.mark.parametrize(
    "md",
    [
        " " * 4000 + "\n" * 1,
        "\n".join([" " * 4000] * 25),
        "\n".join(["|" * 4000] * 25),
        "\n".join(["| ID | level |" + "|" * 3900] + ["| --- |" + "-|" * 1900] * 24),
        "```mermaid\n" + "\n".join(["[" * 4000] * 25),
        "```mermaid\n%%@ id: a\nsequenceDiagram\n" + "\n".join(["alt [g] " + "(" * 3900] * 25),
        "```mermaid\n%%@ id: a\nflowchart TD\n" + "\n".join(["a{" + "{" * 3990] * 25),
        "```\n" * (_KB100 // 4),
        "```mermaid\n%%@ id: a\nsequenceDiagram\n" + "    loop [l]\n" * (_KB100 // 14),
        "```mermaid\n%%@ id: a\nclassDiagram\n" + "class A{\n" * (_KB100 // 9),
    ],
)
def test_pathological_input_finishes_quickly(md: str) -> None:
    assert len(md) >= 4000
    start = time.perf_counter()
    compile_spec(md, domain=None, legacy_ir=[])
    assert time.perf_counter() - start < 1.0


def _timed_codes(md: str) -> list[str]:
    start = time.perf_counter()
    res = compile_spec(md, domain=None, legacy_ir=[])  # 例外（RecursionError 等）を漏らさない
    assert time.perf_counter() - start < 1.0
    assert res.compiled is None
    return [d.code for d in res.diagnostics]


def _seq_doc(*body: str) -> str:
    return CLASSES + block(
        "%%@ id: s",
        "%%@ entry: OrderService.checkout",
        "sequenceDiagram",
        "    participant I as InventoryService",
        *body,
    )


def test_deeply_nested_alt_is_bounded() -> None:
    n = 990  # 文の数の上限（2000）より先に入れ子の上限に当たる数
    body = [f"alt [g{i}] before.x" for i in range(n)] + ["I->>I: [m] x"] + ["end"] * n
    assert "E_NESTING_TOO_DEEP" in _timed_codes(_seq_doc(*body))


def test_deeply_nested_guard_parentheses_are_bounded() -> None:
    guard = "(" * 1000 + "before.x" + ")" * 1000
    codes = _timed_codes(_seq_doc(f"alt [g] {guard}", "I->>I: [m] x", "end"))
    assert "E_NESTING_TOO_DEEP" in codes
    chain = " - ".join(["before.x"] * 100) + " > 0"
    assert "E_NESTING_TOO_DEEP" in _timed_codes(_seq_doc(f"alt [g] {chain}", "I->>I: [m] x", "end"))
    nots = "not " * 1000 + "before.x"
    assert "E_NESTING_TOO_DEEP" in _timed_codes(_seq_doc(f"alt [g] {nots}", "I->>I: [m] x", "end"))


def test_many_messages_are_bounded() -> None:
    # 10 万行は本文の上限で、上限内に収まる数千行は図ごとの文の数の上限で打ち切る
    many = [f"I->>I: [m{i}] x" for i in range(100_000)]
    assert _timed_codes(_seq_doc(*many)) == ["E_TOO_LARGE"]
    some = [f"I->>I: [m{i}] x" for i in range(10_000)]
    assert "E_TOO_LARGE" in _timed_codes(_seq_doc(*some))


def test_too_many_diagrams_and_tokens() -> None:
    diagrams = "".join(block(f"%%@ id: d{i}", "classDiagram") for i in range(65))
    assert _timed_codes(diagrams) == ["E_TOO_LARGE"]
    tokens = block("%%@ id: d", "classDiagram", *(["+ - # ~ " * 500] * 70))
    assert "E_TOO_LARGE" in _timed_codes(tokens)


def test_many_requirement_tables_finish_quickly() -> None:
    table = "| ID | level | 要求 | 形式化 |\n| --- | --- | --- | --- |\n| R | mandatory | x | - |\n\n"
    md = table * (200 * 1024 // len(table.encode()))
    start = time.perf_counter()
    compile_spec(md, domain=None, legacy_ir=[])
    assert time.perf_counter() - start < 1.0
