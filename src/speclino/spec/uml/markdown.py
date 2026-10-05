"""Markdown から `mermaid` ブロックと要求の表を切り出す（grammar.md 1 章・8 章）。"""

from __future__ import annotations

from speclino.spec.uml.types import (
    MarkdownError,
    MarkdownParts,
    MermaidBlock,
    RequirementTable,
    Token,
)

REQ_HEADER = ("ID", "level", "要求", "形式化")
_SEP_CHARS = frozenset("|-: \t")


def _is_separator(line: str) -> bool:
    """`| --- | :-: |` 形式の区切り行か（正規表現を使わない 1 回の走査）。"""
    s = line.strip()
    return s.startswith("|") and "-" in s and all(c in _SEP_CHARS for c in s)


def _cells(line: str, line_no: int) -> list[Token]:
    """`| a | b |` を列ごとの CELL トークンにする。

    column は列の中身の開始位置（空なら `|` の直後）。
    """
    body = line.rstrip()
    start = body.index("|") + 1
    end = len(body) - 1 if body.endswith("|") else len(body)
    cells: list[Token] = []
    pos = start
    while pos <= end:
        nxt = body.find("|", pos, end)
        if nxt == -1:
            nxt = end
        raw = body[pos:nxt]
        lead = len(raw) - len(raw.lstrip())
        cells.append(Token("CELL", raw.strip(), line_no, pos + lead + 1))
        pos = nxt + 1
        if nxt == end:
            break
    return cells


def _strip_quote(line: str) -> str:
    """行頭の空白と引用の `>` を取り除く（コンテナの中のブロックも見落とさないため）。"""
    i = 0
    while i < len(line) and line[i] in " \t>":
        i += 1
    return line[i:]


def _strip_list_marker(s: str) -> str:
    if len(s) >= 2 and s[0] in "-*+" and s[1] in " \t":
        return _strip_quote(s[2:])
    i = 0
    while i < len(s) and i < 9 and s[i].isdigit():
        i += 1
    if 0 < i < len(s) - 1 and s[i] in ".)" and s[i + 1] in " \t":
        return _strip_quote(s[i + 2 :])
    return s


def _fence_open(line: str) -> tuple[str, int, str] | None:
    """CommonMark のコードフェンスの開き（``` / ~~~ を 3 つ以上）。

    字下げ・引用・リストの中も見る。
    """
    s = _strip_list_marker(_strip_quote(line))
    if not s or s[0] not in "`~":
        return None
    ch = s[0]
    n = 0
    while n < len(s) and s[n] == ch:
        n += 1
    if n < 3:
        return None
    info = s[n:].strip()
    if ch == "`" and "`" in info:
        return None
    return ch, n, info


def _is_fence_close(line: str, ch: str, count: int) -> bool:
    """閉じのフェンス: 開きと同じ文字が同数以上で、後ろに空白しか無い（言語名は付かない）。"""
    s = _strip_quote(line).rstrip()
    return len(s) >= count and s == ch * len(s)


def _is_mermaid(info: str) -> bool:
    words = info.split()
    return bool(words) and words[0].lower() == "mermaid"


def _loose_cells(line: str) -> list[str]:
    s = _strip_quote(line).strip()
    if s.startswith("|"):
        s = s[1:]
    if s.endswith("|"):
        s = s[:-1]
    return [c.strip() for c in s.split("|")]


def _is_req_candidate(line: str) -> bool:
    """要求の表の候補: 見出しに `形式化` があるか、最初の列が（大文字小文字を無視して）`ID`。"""
    if "|" not in line:
        return False
    cells = _loose_cells(line)
    return "形式化" in cells or cells[0].lower() == "id"


_CANONICAL_FENCE = "```mermaid"


def split_markdown(text: str) -> MarkdownParts:
    lines = text.split("\n")
    blocks: list[MermaidBlock] = []
    tables: list[RequirementTable] = []
    errors: list[MarkdownError] = []
    table_tokens: list[Token] = []
    in_comment = False
    i = 0
    while i < len(lines):
        line = lines[i]
        line_no = i + 1
        if in_comment:
            fo = _fence_open(line)
            if fo is not None and _is_mermaid(fo[2]):
                errors.append(
                    MarkdownError(
                        "E_UNSUPPORTED_SYNTAX",
                        "HTML コメントの中の mermaid ブロックは書けない",
                        line_no,
                    )
                )
            elif _is_req_candidate(line):
                errors.append(
                    MarkdownError("E_REQ_TABLE", "HTML コメントの中に要求の表は書けない", line_no)
                )
            if "-->" in line:
                in_comment = False
            i += 1
            continue
        fo = _fence_open(line)
        if fo is not None:
            ch, count, info = fo
            j = i + 1
            while j < len(lines) and not _is_fence_close(lines[j], ch, count):
                j += 1
            canonical = line.rstrip() == _CANONICAL_FENCE
            if _is_mermaid(info) and not canonical:
                errors.append(
                    MarkdownError(
                        "E_UNSUPPORTED_SYNTAX",
                        "mermaid ブロックは行頭の ```mermaid だけで開く"
                        "（字下げ・引用・リスト・~~~・4 つ以上の ` ・大文字・追加の語は不可）",
                        line_no,
                    )
                )
            elif canonical:
                if j >= len(lines):
                    errors.append(
                        MarkdownError("E_UNCLOSED_BLOCK", "mermaid ブロックが閉じていない", line_no)
                    )
                else:
                    body = [(k + 1, lines[k]) for k in range(i + 1, j)]
                    blocks.append(MermaidBlock(fence_line=line_no, lines=body))
            i = j + 1
            continue
        start = line.find("<!--")
        if start >= 0 and "-->" not in line[start + 4 :]:
            in_comment = True
            i += 1
            continue
        if _is_req_candidate(line):
            i = _requirement_table(lines, i, tables, errors, table_tokens)
            continue
        if line.lstrip().startswith("|"):
            # 要求の表でない表は本文として読み飛ばす
            j = i + 1
            while j < len(lines) and lines[j].lstrip().startswith("|"):
                j += 1
            i = j
            continue
        i += 1
    return MarkdownParts(blocks=blocks, tables=tables, errors=errors, table_tokens=table_tokens)


def _requirement_table(
    lines: list[str],
    i: int,
    tables: list[RequirementTable],
    errors: list[MarkdownError],
    table_tokens: list[Token],
) -> int:
    """要求の表の候補を読み、次に読む行の位置を返す。正規形でなければ E_REQ_TABLE。"""
    line = lines[i]
    line_no = i + 1
    j = i + 1
    while j < len(lines) and "|" in lines[j] and lines[j].strip():
        j += 1
    reason = None
    if not line.startswith("|"):
        reason = "要求の表は行頭の `|` から書く（字下げ・引用・先頭の `|` の省略は不可）"
    elif tuple(_loose_cells(line)) != REQ_HEADER:
        reason = f"要求の表の見出しは | {' | '.join(REQ_HEADER)} | でなければならない"
    elif i + 1 >= len(lines) or not _is_separator(lines[i + 1]):
        reason = "要求の表に区切り行が無い"
    if reason is not None:
        errors.append(MarkdownError("E_REQ_TABLE", reason, line_no))
        return j
    header = _cells(line, line_no)
    rows: list[list[Token]] = []
    k = i + 2
    while k < len(lines) and lines[k].lstrip().startswith("|"):
        rows.append(_cells(lines[k], k + 1))
        k += 1
    tables.append(RequirementTable(header=header, rows=rows))
    table_tokens.extend(header)
    table_tokens.extend(t for r in rows for t in r)
    return k


__all__ = ["REQ_HEADER", "split_markdown"]
