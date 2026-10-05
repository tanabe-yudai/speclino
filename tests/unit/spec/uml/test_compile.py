"""compile（図をまたぐ解決・ハッシュ・旧形式・全か無か）。設計 4.2–4.3。"""

from __future__ import annotations

import os
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
from tests.unit.spec.uml.samples import STATES as STATES_MD

ROOT = Path(__file__).resolve().parents[4]
PERF_LIMIT = 3.0 * float(os.environ.get("SPECLINO_PERF_FACTOR", "1"))
"""計算量のテストの時間の上限（秒）。遅い環境では SPECLINO_PERF_FACTOR で倍率を掛ける。"""
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


def test_rejects_partial_advisory_downgrade() -> None:
    """一部の要素だけを advisory の行で参照しても、義務は mandatory のまま。"""
    c = compile_spec(FULL, domain=None, legacy_ir=[]).compiled
    assert c is not None
    # FULL の R2 は cancel-flow#r だけを advisory で参照している
    levels = {t.id: t.level for t in c.traces}
    assert levels["cancel-flow#OrderService.cancel"] == "mandatory"
    every = ", ".join(f"cancel-flow#{e}" for e in ["a", "d", "r", "z", "e1", "e2", "e3", "e4"])
    md = FULL.replace("| cancel-flow#r |", f"| {every} |")
    c2 = compile_spec(md, domain=None, legacy_ir=[]).compiled
    assert c2 is not None
    assert {t.id: t.level for t in c2.traces}["cancel-flow#OrderService.cancel"] == "advisory"


def test_level_from_requirement_rows() -> None:
    c = compile_spec(FULL, domain=None, legacy_ir=[]).compiled
    assert c is not None
    levels = {t.id: t.level for t in c.traces}
    assert levels == {
        "checkout#OrderService.checkout": "mandatory",
        "cancel-flow#OrderService.cancel": "mandatory",
    }
    assert {s.id: s.level for s in c.structure}["vocab#OrderService"] == "mandatory"
    assert c.state_machines[0].level == "mandatory"
    # advisory の行だけが参照するクラス → advisory
    md = FULL.replace(
        "| R2 | advisory | 取消は返金する | cancel-flow#r |",
        "| R2 | advisory | x | vocab#OrderService |",
    )
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
    assert "E_GUARD_TYPE" in [
        d.code for d in compile_spec(bad, domain=domain, legacy_ir=[]).diagnostics
    ]
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
    assert time.perf_counter() - start < PERF_LIMIT


def _timed_codes(md: str) -> list[str]:
    start = time.perf_counter()
    res = compile_spec(md, domain=None, legacy_ir=[])  # 例外（RecursionError 等）を漏らさない
    assert time.perf_counter() - start < PERF_LIMIT
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
    table = (
        "| ID | level | 要求 | 形式化 |\n| --- | --- | --- | --- |\n| R | mandatory | x | - |\n\n"
    )
    md = table * (200 * 1024 // len(table.encode()))
    start = time.perf_counter()
    compile_spec(md, domain=None, legacy_ir=[])
    assert time.perf_counter() - start < PERF_LIMIT


# ---------------------------------------------- Markdown の構造（fix round 2 のプローブ）


def _compiled(md: str):  # type: ignore[no-untyped-def]
    res = compile_spec(md, domain=None, legacy_ir=[])
    assert res.compiled is not None, [(d.code, d.message) for d in res.diagnostics]
    return res.compiled


def _codes_of(md: str) -> list[str]:
    return [d.code for d in compile_spec(md, domain=None, legacy_ir=[]).diagnostics]


@pytest.mark.parametrize(
    "prefix",
    ["- - ```mermaid\n", "1. - ```mermaid\n", "* 1. ```mermaid\n", "> - ```mermaid\n"],
)
def test_rejects_mermaid_fence_in_nested_containers(prefix: str) -> None:
    md = prefix + "  classDiagram\n  ```\n\n" + CLASSES
    assert "E_UNSUPPORTED_SYNTAX" in _codes_of(md)


def test_table_row_without_leading_pipe_is_read_as_row() -> None:
    md = REQUIREMENTS + "R4 | mandatory | 追加の要求 | -\n\n" + CLASSES + SEQUENCE + ACTIVITY
    c = _compiled(md)
    assert [r.id for r in c.requirements][-1] == "R4"


def test_table_row_with_wrong_cell_count_is_error() -> None:
    md = REQUIREMENTS + "R4 | mandatory\n\n" + CLASSES + SEQUENCE + ACTIVITY
    assert "E_REQ_TABLE" in _codes_of(md)


@pytest.mark.parametrize(
    "before",
    [
        "    ```\n    code\n\n",  # 字下げのコードブロック（フェンスではない）
        "> ```\n> code\n\n",  # 引用の中の閉じていないフェンスは引用の終わりで閉じる
        "- ```\n  code\n\n段落\n\n",  # リストの中の閉じていないフェンス＋段落
    ],
)
def test_container_fence_does_not_swallow_following_diagram(before: str) -> None:
    c = _compiled(before + CLASSES)
    assert [k.name for k in c.classes][0] == "OrderService"


def test_quoted_unclosed_fence_then_table_and_diagram() -> None:
    md = "> ```\n> x\n\n" + REQUIREMENTS + "\n" + CLASSES + SEQUENCE + ACTIVITY + STATES_MD
    c = _compiled(md)
    assert [r.id for r in c.requirements] == ["R1", "R2", "R3"]
    assert len(c.traces) == 2


def test_indented_close_inside_mermaid_block_then_swallowed_diagram() -> None:
    # 字下げ 2 の ``` は CommonMark では mermaid ブロックを閉じる。最後の ``` がフェンスを開き、
    # 後ろの図を飲み込む → 読み手の見え方どおりに扱い、飲み込みを知らせる
    md = "```mermaid\n%%@ id: v\nclassDiagram\n    class A\n  ```\n    class B\n```\n\n"
    # 後ろの図の閉じで閉じる場合（図はコードとして見える）→ 警告
    res = compile_spec(md + CLASSES, domain=None, legacy_ir=[])
    assert res.compiled is not None
    assert [k.name for k in res.compiled.classes] == ["A"]
    assert "W_MERMAID_IN_CODE" in [d.code for d in res.diagnostics]
    # 閉じないまま文書の終わりまで伸びる場合 → エラー
    assert "E_UNCLOSED_BLOCK" in _codes_of(md + "```mermaid\n%%@ id: w\nclassDiagram\n")


def test_unclosed_mermaid_fence_is_error() -> None:
    assert _codes_of("```mermaid\n%%@ id: v\nclassDiagram\n    class A\n") == ["E_UNCLOSED_BLOCK"]


def test_two_html_comments_on_one_line() -> None:
    closed = "<!-- a --> <!-- b -->\n\n" + CLASSES
    assert _compiled(closed).classes
    hidden = "<!-- a --> <!--\n" + CLASSES + "-->\n"
    assert "E_UNSUPPORTED_SYNTAX" in _codes_of(hidden)


def test_inline_html_comment_with_mermaid() -> None:
    md = "本文 <!-- ```mermaid --> 続き\n\n" + CLASSES
    assert "E_UNSUPPORTED_SYNTAX" in _codes_of(md)


def test_empty_diagram_error_has_line() -> None:
    res = compile_spec(block("%%@ id: e", "classDiagram"), domain=None, legacy_ir=[])
    (d,) = res.diagnostics
    assert d.code == "E_EMPTY_DIAGRAM" and d.span is not None and d.span.line == 3


@pytest.mark.parametrize(
    "md",
    [
        "> " * 1000 + "```mermaid\nclassDiagram\n```\n",
        "".join("  " * i + "- a\n" for i in range(1000)),
        "".join("> " * i + "x\n" for i in range(1, 1000)),
        "- " * 1000 + "x\n",
    ],
)
def test_deep_markdown_nesting_is_bounded(md: str) -> None:
    start = time.perf_counter()
    res = compile_spec(md[: 250 * 1024], domain=None, legacy_ir=[])
    assert time.perf_counter() - start < PERF_LIMIT
    assert "E_NESTING_TOO_DEEP" in [d.code for d in res.diagnostics]


def test_long_tables_and_brackets_finish_quickly() -> None:
    for md in (
        "| a | b |\n|---|---|\n" + "| x | y |\n" * 25000,
        ("[" * 4000 + "\n") * 60,
        ("[a](" * 1000 + "\n") * 60,
        "|" + "a|" * 60 + "\n|" + "-|" * 60 + "\n" + ("|" + "x|" * 60 + "\n") * 1600,
    ):
        start = time.perf_counter()
        compile_spec(md[: 250 * 1024], domain=None, legacy_ir=[])
        assert time.perf_counter() - start < PERF_LIMIT


def test_table_size_limits() -> None:
    wide = "|" + "a|" * 70 + "\n|" + "-|" * 70 + "\n"
    assert set(_codes_of(wide)) == {"E_TOO_LARGE"}
    long = "| a | b |\n|---|---|\n" + "| x | y |\n" * 5100
    assert _codes_of(long) == ["E_TOO_LARGE"]
    cells = "|" + "a|" * 60 + "\n|" + "-|" * 60 + "\n" + ("|" + "x|" * 60 + "\n") * 400
    assert _codes_of(cells) == ["E_TOO_LARGE"]


# ---------------------------------------------- fix round 3


_REQ_HEAD = "| ID | level | 要求 | 形式化 |\n| --- | --- | --- | --- |\n"
_CLS_A = "```mermaid\n%%@ id: c\nclassDiagram\nclass A {\n  +m()\n}\n```\n\n"


@pytest.mark.parametrize(
    "row", ["| R1 | mandatory | a \\| c#A |\n", "| R1 | mandatory | x \\| - |\n"]
)
def test_rejects_escaped_pipe_in_requirement_table(row: str) -> None:
    res = compile_spec(_CLS_A + _REQ_HEAD + row, domain=None, legacy_ir=[])
    assert res.compiled is None
    assert [d.code for d in res.diagnostics] == ["E_REQ_TABLE"]
    assert "\\|" in res.diagnostics[0].message


def test_bom_is_stripped_before_reading_structure() -> None:
    second = _CLS_A.replace("id: c", "id: d").replace("class A", "class B")
    md = "\ufeff" + _CLS_A + second
    res = compile_spec(md, domain=None, legacy_ir=[])
    assert res.compiled is not None, res.diagnostics
    assert [k.name for k in res.compiled.classes] == ["A", "B"]
    assert res.compiled.classes[0].span.line == 4  # 行番号はずれない
    assert res.compiled.source_hash == content_hash(md)  # source_hash は原文


def test_rejects_emphasized_requirement_header() -> None:
    md = _CLS_A + "| **ID** | level | 要求 | **形式化** |\n| --- | --- | --- | --- |\n"
    md += "| R1 | mandatory | x | - |\n"
    assert _codes_of(md) == ["E_REQ_TABLE"]


def test_thematic_break_is_not_nesting() -> None:
    md = "- - - - - - - - - - - - - - - - - - - - - - - - - - - - - - - - - - -\n\n" + _CLS_A
    assert _compiled(md).classes


# ---------------------------------------------- fix round 4: 事後照合（消えた図と表）

_FENCE = ["```mermaid", "%%@ id: c", "classDiagram", "class A {", "  +m()", "}", "```"]
_TABLE = [
    "| ID | level | 要求 | 形式化 |",
    "| --- | --- | --- | --- |",
    "| R1 | mandatory | x | - |",
]


def _nested_ol(n: int, payload: list[str]) -> str:
    """順序つきリストを字下げで n 段入れ子にする。

    事前の深さの見積りは通るが、markdown-it は深い段の中身を捨てる。
    """
    out = []
    ind = 0
    for _ in range(n):
        out.append(" " * ind + "1. a")
        ind += 3
    return "\n".join(out + [" " * ind + line for line in payload]) + "\n"


def _quote_list(n: int, payload: list[str], hr: bool = False) -> str:
    out = []
    pre = ""
    for _ in range(n):
        out.append(pre + "> a")
        pre += "> "
        out.append(pre + "- b")
        pre += "  "
        if hr:
            out.append(pre + "- - -")
    return "\n".join(out + [pre + line for line in payload]) + "\n"


@pytest.mark.parametrize("payload", [_FENCE, _TABLE], ids=["mermaid", "table"])
@pytest.mark.parametrize(
    "build",
    [
        lambda p: _nested_ol(10, p),
        lambda p: _nested_ol(11, p),
        lambda p: _quote_list(6, p),
        lambda p: _quote_list(6, p, hr=True),
        lambda p: "> - " * 100 + p[0] + "\n" + "\n".join(p[1:]) + "\n",
    ],
    ids=["ol10", "ol11", "quote-list", "quote-list-hr", "q-l-100"],
)
def test_nested_content_dropped_by_parser_is_error(build, payload: list[str]) -> None:  # type: ignore[no-untyped-def]
    md = build(payload)
    start = time.perf_counter()
    res = compile_spec(md, domain=None, legacy_ir=[])
    assert time.perf_counter() - start < PERF_LIMIT
    assert res.compiled is None
    codes = {d.code for d in res.diagnostics}
    assert codes & {"E_UNSUPPORTED_SYNTAX", "E_NESTING_TOO_DEEP", "E_REQ_TABLE"}, codes
    assert all(d.span is not None for d in res.diagnostics)


def test_deep_nesting_reconciliation_is_fast() -> None:
    md = "".join(_nested_ol(11, _FENCE) for _ in range(300))
    start = time.perf_counter()
    res = compile_spec(md[: 250 * 1024], domain=None, legacy_ir=[])
    assert time.perf_counter() - start < PERF_LIMIT
    assert res.compiled is None


@pytest.mark.parametrize("prefix", ["﻿﻿", " ", "​", "⁠"])
def test_invisible_prefix_before_mermaid_fence_is_error(prefix: str) -> None:
    md = prefix + "\n".join(_FENCE) + "\n\n" + "\n".join(_FENCE).replace("id: c", "id: d") + "\n"
    res = compile_spec(md, domain=None, legacy_ir=[])
    assert res.compiled is None
    assert [(d.code, d.span.line if d.span else None) for d in res.diagnostics][0] == (
        "E_UNSUPPORTED_SYNTAX",
        1,
    )


@pytest.mark.parametrize("payload", [_FENCE, _TABLE], ids=["mermaid", "table"])
@pytest.mark.parametrize("depth", [10, 11])
def test_reconciliation_catches_what_precheck_misses(payload: list[str], depth: int) -> None:
    """事前の深さの見積りは通るが markdown-it が中身を捨てる入れ子 → 事後照合のエラー。"""
    from speclino.spec.uml.markdown import _precheck

    md = _nested_ol(depth, payload)
    assert _precheck(md.split("\n")) == []  # 事前検査では止まらない
    res = compile_spec(md, domain=None, legacy_ir=[])
    msgs = [d.message for d in res.diagnostics if d.code == "E_UNSUPPORTED_SYNTAX"]
    assert any("解釈できない" in m for m in msgs), res.diagnostics


# ---------------------------------------------- fix round 5


@pytest.mark.parametrize("opener", ["````mermaid", "``` mermaid", "~~~~mermaid", "~~~\tMermaid"])
def test_reconciliation_catches_fence_variants_in_deep_nesting(opener: str) -> None:
    md = _nested_ol(11, [opener, "classDiagram", "class A", opener[:4].strip()])
    res = compile_spec(md, domain=None, legacy_ir=[])
    msgs = [d.message for d in res.diagnostics if d.code == "E_UNSUPPORTED_SYNTAX"]
    assert any("解釈できない" in m for m in msgs), res.diagnostics


@pytest.mark.parametrize("prefix", ["　", "­", "‎", "‏", " "])
def test_unicode_space_and_format_chars_before_fence_are_error(prefix: str) -> None:
    md = prefix + "\n".join(_FENCE) + "\n\n" + "\n".join(_FENCE).replace("id: c", "id: d") + "\n"
    res = compile_spec(md, domain=None, legacy_ir=[])
    assert res.compiled is None
    assert ("E_UNSUPPORTED_SYNTAX", 1) in [
        (d.code, d.span.line if d.span else None) for d in res.diagnostics
    ]


def test_unexplained_lines_report_remaining_count() -> None:
    md = "\n\n".join(" ```mermaid" for _ in range(25)) + "\n"
    res = compile_spec(md, domain=None, legacy_ir=[])
    msgs = [d.message for d in res.diagnostics if d.code == "E_UNSUPPORTED_SYNTAX"]
    assert len(msgs) == 20 and "ほか 5 件" in msgs[-1]


def _timed(md: str) -> None:
    start = time.perf_counter()
    compile_spec(md, domain=None, legacy_ir=[])
    assert time.perf_counter() - start < PERF_LIMIT


@pytest.mark.parametrize(
    "md",
    [
        ("&" + ">" * 4095 + "\n") * 5000,  # 本文の上限で即座に止まる
        (">" * 4095 + "\n") * 60,  # 上限内: 引用の記号だけの長い行
        ("- " * 2000 + "x\n") * 60,  # リストの記号の繰り返し（先頭の除去が線形であること）
        ("　" * 4000 + "x\n") * 60,  # Zs の繰り返し
        "a\n\n" * 20000,  # 葉のブロック 20,000 個
        "a\n---\n" * 20000,  # 段落と区切りの交互
        "| ID | x |\n\n" * 5000,  # 表の候補の見出し 5,000 個
    ],
    ids=["5000-lines", "gt-lines", "list-markers", "zs", "leaves", "para-hr", "headers"],
)
def test_markdown_processing_is_linear(md: str) -> None:
    _timed(md if len(md.encode()) > 256 * 1024 and md.startswith("&") else md[: 250 * 1024])
