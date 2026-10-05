"""字句解析（全域）と Markdown の切り出し（grammar.md 1–2 章）。"""

from __future__ import annotations

import pytest

from speclino.spec.uml.lexer import tokenize_line
from speclino.spec.uml.markdown import split_markdown


def _kinds(text: str) -> list[tuple[str, str]]:
    return [(t.kind, t.text) for t in tokenize_line(text, 1)]


def test_lexer_is_total_and_keeps_positions() -> None:
    toks = tokenize_line('  A->>B: [m1] 予約 "x" ?', 7)
    assert [(t.kind, t.text) for t in toks] == [
        ("NAME", "A"),
        ("ARROW", "->>"),
        ("NAME", "B"),
        ("PUNCT", ":"),
        ("PUNCT", "["),
        ("NAME", "m1"),
        ("PUNCT", "]"),
        ("TEXT", "予約"),
        ("OTHER", '"'),
        ("NAME", "x"),
        ("OTHER", '"'),
        ("OTHER", "?"),
    ]
    assert toks[0].line == 7 and toks[0].column == 3
    assert toks[1].column == 4


def test_lexer_longest_match_arrows() -> None:
    assert _kinds("A-->>B") == [("NAME", "A"), ("ARROW", "-->>"), ("NAME", "B")]
    assert _kinds("A-xB") == [("NAME", "A"), ("ARROW", "-x"), ("NAME", "B")]
    assert _kinds("a --> |[e1]| b")[1] == ("ARROW", "-->")
    assert _kinds("x <= -3") == [("NAME", "x"), ("PUNCT", "<="), ("PUNCT", "-"), ("INT", "3")]
    assert _kinds("<<enumeration>>") == [
        ("PUNCT", "<<"),
        ("NAME", "enumeration"),
        ("PUNCT", ">>"),
    ]


def test_lexer_dotted_name_is_one_token() -> None:
    assert _kinds("before.order.status == OrderStatus.PENDING") == [
        ("NAME", "before.order.status"),
        ("PUNCT", "=="),
        ("NAME", "OrderStatus.PENDING"),
    ]


def test_split_markdown_blocks_and_lines() -> None:
    md = "# t\n\n```mermaid\n%%@ id: d1\nclassDiagram\n```\n\n```python\n| ID | level |\n```\n"
    parts = split_markdown(md)
    assert parts.errors == []
    assert len(parts.blocks) == 1
    assert parts.blocks[0].fence_line == 3
    assert parts.blocks[0].lines == [(4, "%%@ id: d1"), (5, "classDiagram")]
    assert parts.tables == []


def test_unclosed_mermaid_block_is_error() -> None:
    parts = split_markdown("```mermaid\nclassDiagram\n")
    assert [e.code for e in parts.errors] == ["E_UNCLOSED_BLOCK"]


def test_requirement_table() -> None:
    md = (
        "| ID | level | 要求 | 形式化 |\n"
        "| --- | --- | --- | --- |\n"
        "| R1 | mandatory | 在庫を確保する | seq#m1, ir:R-9 |\n"
        "| R2 | advisory | 記録する |  |\n"
        "\n"
        "| a | b |\n|---|---|\n| 1 | 2 |\n"
    )
    parts = split_markdown(md)
    assert parts.errors == []
    assert len(parts.tables) == 1
    rows = parts.tables[0].rows
    assert [[c.text for c in r] for r in rows] == [
        ["R1", "mandatory", "在庫を確保する", "seq#m1, ir:R-9"],
        ["R2", "advisory", "記録する", ""],
    ]
    assert rows[0][3].line == 3 and rows[0][3].column > 1


def test_requirement_table_with_wrong_header_is_error() -> None:
    md = "| ID | level | 要求 |\n| --- | --- | --- |\n| R1 | mandatory | x |\n"
    assert [e.code for e in split_markdown(md).errors] == ["E_REQ_TABLE"]


_BODY = "%%@ id: d1\nclassDiagram\n    class A\n"


@pytest.mark.parametrize(
    "md",
    [
        "~~~mermaid\n" + _BODY + "~~~\n",
        "```mermaid title\n" + _BODY + "```\n",
        "  ```mermaid\n" + _BODY + "  ```\n",
        "    ```mermaid\n" + _BODY + "    ```\n",
        "````mermaid\n" + _BODY + "````\n",
        "> ```mermaid\n> %%@ id: d1\n> classDiagram\n> ```\n",
        "```Mermaid\n" + _BODY + "```\n",
        "- ```mermaid\n  %%@ id: d1\n  classDiagram\n  ```\n",
        "1. ```mermaid\n   classDiagram\n   ```\n",
        "<!--\n```mermaid\n" + _BODY + "```\n-->\n",
    ],
)
def test_rejects_non_canonical_mermaid_fence(md: str) -> None:
    parts = split_markdown(md)
    assert parts.blocks == []
    assert "E_UNSUPPORTED_SYNTAX" in [e.code for e in parts.errors]


def test_fence_close_follows_commonmark() -> None:
    # 言語名つきの ``` では閉じない。同じ文字で同数以上なら閉じる
    parts = split_markdown("```mermaid\nclassDiagram\n``` foo\n````\n")
    assert len(parts.blocks) == 1
    assert parts.blocks[0].lines == [(2, "classDiagram"), (3, "``` foo")]
    # ~~~ のブロックの中の ```mermaid は本文（コード）として読まない
    parts = split_markdown("~~~\n```mermaid\nclassDiagram\n```\n~~~\n")
    assert parts.blocks == [] and parts.errors == []


@pytest.mark.parametrize(
    "md",
    [
        "| id | level | 要求 | 形式化 |\n| --- | --- | --- | --- |\n| R1 | mandatory | x | - |\n",
        "ID | level | 要求 | 形式化\n--- | --- | --- | ---\nR1 | mandatory | x | -\n",
        "> | ID | level | 要求 | 形式化 |\n> | --- | --- | --- | --- |\n"
        "> | R1 | mandatory | x | - |\n",
        "  | ID | level | 要求 | 形式化 |\n  | --- | --- | --- | --- |\n",
        "| 要求 | 形式化 |\n| --- | --- |\n| x | a#b |\n",
        "| ID | 要求 |\n| --- | --- |\n| R1 | x |\n",
        "<!--\n| ID | level | 要求 | 形式化 |\n| --- | --- | --- | --- |\n-->\n",
    ],
)
def test_rejects_non_canonical_requirement_table(md: str) -> None:
    parts = split_markdown(md)
    assert parts.tables == []
    assert "E_REQ_TABLE" in [e.code for e in parts.errors]
