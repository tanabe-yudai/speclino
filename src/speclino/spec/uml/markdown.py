"""Markdown から `mermaid` ブロックと要求の表を切り出す（grammar.md 1 章・8 章）。"""

from __future__ import annotations

import re

from speclino.spec.uml.types import (
    MarkdownError,
    MarkdownParts,
    MermaidBlock,
    RequirementTable,
    Token,
)

REQ_HEADER = ("ID", "level", "要求", "形式化")
_FENCE_RE = re.compile(r"^```(.*)$")
_SEP_RE = re.compile(r"^\|[\s\-:|]+\|?\s*$")


def _cells(line: str, line_no: int) -> list[Token]:
    """`| a | b |` を列ごとの CELL トークンにする。column は列の中身の開始位置（空なら `|` の直後）。"""
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


def split_markdown(text: str) -> MarkdownParts:
    lines = text.split("\n")
    blocks: list[MermaidBlock] = []
    tables: list[RequirementTable] = []
    errors: list[MarkdownError] = []
    table_tokens: list[Token] = []
    i = 0
    while i < len(lines):
        line = lines[i]
        line_no = i + 1
        fence = _FENCE_RE.match(line)
        if fence:
            lang = fence.group(1).strip()
            j = i + 1
            while j < len(lines) and not lines[j].startswith("```"):
                j += 1
            if j >= len(lines):
                if lang == "mermaid":
                    errors.append(
                        MarkdownError("E_UNCLOSED_BLOCK", "mermaid ブロックが閉じていない", line_no)
                    )
                i = len(lines)
                continue
            if lang == "mermaid":
                body = [(k + 1, lines[k]) for k in range(i + 1, j)]
                blocks.append(MermaidBlock(fence_line=line_no, lines=body))
            i = j + 1
            continue
        if line.lstrip().startswith("|"):
            header = _cells(line, line_no)
            names = tuple(c.text for c in header)
            if names[:2] == REQ_HEADER[:2]:
                j = i + 1
                if j >= len(lines) or not _SEP_RE.match(lines[j].strip()):
                    errors.append(
                        MarkdownError("E_REQ_TABLE", "要求の表に区切り行が無い", line_no)
                    )
                    i = j
                    continue
                if names != REQ_HEADER:
                    errors.append(
                        MarkdownError(
                            "E_REQ_TABLE",
                            f"要求の表の見出しは | {' | '.join(REQ_HEADER)} | でなければならない",
                            line_no,
                        )
                    )
                rows: list[list[Token]] = []
                j += 1
                while j < len(lines) and lines[j].lstrip().startswith("|"):
                    rows.append(_cells(lines[j], j + 1))
                    j += 1
                if names == REQ_HEADER:
                    tables.append(RequirementTable(header_line=line_no, rows=rows))
                    table_tokens.extend(header)
                    table_tokens.extend(t for r in rows for t in r)
                i = j
                continue
            # 要求の表でない表は本文として読み飛ばす
            j = i + 1
            while j < len(lines) and lines[j].lstrip().startswith("|"):
                j += 1
            i = j
            continue
        i += 1
    return MarkdownParts(blocks=blocks, tables=tables, errors=errors, table_tokens=table_tokens)


__all__ = ["REQ_HEADER", "split_markdown"]
