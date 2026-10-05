"""Markdown から `mermaid` ブロックと要求の表を切り出す（grammar.md 1 章・8 章）。

ブロック構造（フェンス・リスト・引用・HTML・表）の解釈は CommonMark 準拠の `markdown-it-py` に任せ、
読み手に見える構造と SpecLino が読む構造を一致させる。
正規形でない図や表は黙って読み飛ばさずエラーにする。
インラインの解析は使わない（`[` の連続などで二次の時間がかかるため。表のセルは生の内容で足りる）。
"""

from __future__ import annotations

from markdown_it import MarkdownIt
from markdown_it.token import Token as MdToken

from speclino.spec.uml.limits import (
    MAX_CONTAINER_DEPTH,
    MAX_PIPE_LINES,
    MAX_PIPES_PER_LINE,
    MAX_TOTAL_PIPES,
)
from speclino.spec.uml.types import (
    MarkdownError,
    MarkdownParts,
    MermaidBlock,
    RequirementTable,
    Token,
)

REQ_HEADER = ("ID", "level", "要求", "形式化")
_CANONICAL_FENCE = "```mermaid"
_MD_NESTING = 4 * MAX_CONTAINER_DEPTH + 8
"""markdown-it の入れ子の上限。これを超えた内容は黙って捨てられるので、コンテナの深さを先に数えて
`MAX_CONTAINER_DEPTH` で止め、ここには十分な余裕を持たせる。"""


def _parser() -> MarkdownIt:
    md = MarkdownIt("commonmark", {"maxNesting": _MD_NESTING}).enable("table")
    md.disable(["inline", "text_join"])  # ブロックだけを解析する
    return md


_MD = _parser()


def _cells(line: str, line_no: int) -> list[Token]:
    """表の 1 行を列ごとの CELL トークンにする（先頭・末尾の `|` は省略可）。

    column は列の中身の開始位置（空なら `|` の直後）。
    """
    body = line.rstrip()
    stripped = body.lstrip()
    start = len(body) - len(stripped)
    if stripped.startswith("|"):
        start += 1
    end = len(body) - 1 if body.endswith("|") and len(body) - 1 >= start else len(body)
    cells: list[Token] = []
    pos = start
    while True:
        nxt = body.find("|", pos, end)
        if nxt == -1:
            nxt = end
        raw = body[pos:nxt]
        lead = len(raw) - len(raw.lstrip())
        cells.append(Token("CELL", raw.strip(), line_no, pos + lead + 1))
        pos = nxt + 1
        if nxt >= end:
            break
    return cells


def _strip_quote(line: str) -> str:
    """行頭の空白と引用の `>` を取り除く。"""
    i = 0
    while i < len(line) and line[i] in " \t>":
        i += 1
    return line[i:]


def _fence_open(line: str) -> tuple[str, int, str] | None:
    """フェンスの開きに見える行か（コンテナの接頭辞・リストの記号を除いて見る）。"""
    s = _strip_quote(line)
    while len(s) >= 2 and s[0] in "-*+" and s[1] in " \t":
        s = _strip_quote(s[2:])
    if not s or s[0] not in "`~":
        return None
    ch = s[0]
    n = 0
    while n < len(s) and s[n] == ch:
        n += 1
    if n < 3:
        return None
    return ch, n, s[n:].strip()


def _is_mermaid(info: str) -> bool:
    words = info.split()
    return bool(words) and words[0].lower() == "mermaid"


def _mermaid_line(line: str) -> bool:
    fo = _fence_open(line)
    return fo is not None and _is_mermaid(fo[2])


def _loose_cells(line: str) -> list[str]:
    s = _strip_quote(line).strip()
    if s.startswith("|"):
        s = s[1:]
    if s.endswith("|"):
        s = s[:-1]
    return [c.strip() for c in s.split("|")]


def _plain(cell: str) -> str:
    """見出しのセルから強調記号（`*` `_` `` ` ``）と前後の空白を除く（候補の判定だけに使う）。"""
    return cell.strip().strip("*_`").strip()


def _is_req_header(cells: list[str]) -> bool:
    """要求の表の候補: 見出しに `形式化` があるか、最初の列が（大文字小文字を無視して）`ID`。

    強調された見出し（`**ID**`）も候補にする。受け付けるかどうかは見出しの完全一致で決める。
    """
    plain = [_plain(c) for c in cells]
    return bool(plain) and ("形式化" in plain or plain[0].lower() == "id")


def _is_req_candidate(line: str) -> bool:
    return "|" in line and _is_req_header(_loose_cells(line))


def _container_depth(line: str) -> int:
    """行頭のコンテナ（引用 `>`・リストの記号・字下げ）の深さの見積り（多めに数える）。"""
    if all(c in "-*_ \t" for c in line) and line.strip():
        return 0  # 水平線（`- - - -`）はリストではない
    depth = 0
    indent = 0
    i = 0
    n = len(line)
    while i < n:
        c = line[i]
        if c == " ":
            indent += 1
            i += 1
        elif c == "\t":
            indent += 4
            i += 1
        elif c == ">":
            depth += 1
            i += 1
        elif c in "-*+" and i + 1 < n and line[i + 1] in " \t":
            depth += 1
            i += 2
        elif c.isdigit():
            j = i
            while j < n and line[j].isdigit():
                j += 1
            if j < n - 1 and line[j] in ".)" and line[j + 1] in " \t":
                depth += 1
                i = j + 2
            else:
                break
        else:
            break
    if depth == 0:
        return 0  # コンテナを開かない行（本文・コードの字下げ）は数えない
    return depth + indent // 2


def _precheck(lines: list[str]) -> list[MarkdownError]:
    errors: list[MarkdownError] = []
    for k, line in enumerate(lines):
        if _container_depth(line) > MAX_CONTAINER_DEPTH:
            msg = f"引用・リストの入れ子は {MAX_CONTAINER_DEPTH} 段まで"
            errors.append(MarkdownError("E_NESTING_TOO_DEEP", msg, k + 1))
        elif line.count("|") > MAX_PIPES_PER_LINE:
            msg = f"1 行の `|` は {MAX_PIPES_PER_LINE} 個まで"
            errors.append(MarkdownError("E_TOO_LARGE", msg, k + 1))
        if len(errors) >= 20:
            break
    pipe_lines = sum(1 for line in lines if "|" in line)
    if not errors and pipe_lines > MAX_PIPE_LINES:
        msg = f"`|` を含む行は {MAX_PIPE_LINES} 行まで（{pipe_lines} 行ある）"
        errors.append(MarkdownError("E_TOO_LARGE", msg, 1))
    total = sum(line.count("|") for line in lines)
    if not errors and total > MAX_TOTAL_PIPES:
        msg = f"文書の `|` は {MAX_TOTAL_PIPES} 個まで（表のセルの総数の上限）"
        errors.append(MarkdownError("E_TOO_LARGE", msg, 1))
    return errors


class _Scanner:
    def __init__(self, lines: list[str]) -> None:
        self.lines = lines
        self.blocks: list[MermaidBlock] = []
        self.tables: list[RequirementTable] = []
        self.errors: list[MarkdownError] = []
        self.warnings: list[MarkdownError] = []
        self.table_tokens: list[Token] = []

    def error(self, code: str, message: str, line_index: int) -> None:
        self.errors.append(MarkdownError(code, message, line_index + 1))

    # -------------------------------------------------------------- フェンス

    def _closed(self, tok: MdToken) -> bool:
        start, end = tok.map or (0, 0)
        if end - 1 <= start:
            return False
        last = _strip_quote(self.lines[end - 1]).rstrip()
        ch = tok.markup[0]
        return len(last) >= len(tok.markup) and last == ch * len(last)

    def fence(self, tok: MdToken) -> None:
        start, end = tok.map or (0, 0)
        closed = self._closed(tok)
        if _is_mermaid(tok.info):
            canonical = (
                tok.level == 0
                and tok.markup == "```"
                and tok.info == "mermaid"
                and self.lines[start].startswith(_CANONICAL_FENCE)
            )
            if not canonical:
                self.error(
                    "E_UNSUPPORTED_SYNTAX",
                    "mermaid ブロックは行頭の ```mermaid だけで開く"
                    "（字下げ・引用・リスト・~~~・4 つ以上の ` ・大文字・追加の語は不可）",
                    start,
                )
            if not closed:
                self.error("E_UNCLOSED_BLOCK", "mermaid ブロックが閉じていない", start)
            if canonical and closed:
                body = [(k + 1, self.lines[k]) for k in range(start + 1, end - 1)]
                self.blocks.append(MermaidBlock(fence_line=start + 1, lines=body))
            return
        if closed:
            # 閉じたコードの中の ```mermaid は読み手にもコードとして見える（説明の例など）。
            # 図としては読まないが、意図した図が飲み込まれた可能性を警告で知らせる
            for k in range(start + 1, end - 1):
                if _mermaid_line(self.lines[k]):
                    self.warnings.append(
                        MarkdownError(
                            "W_MERMAID_IN_CODE",
                            f"{start + 1} 行目のコードブロックの中の mermaid は図として読まない",
                            k + 1,
                        )
                    )
                    break
            return
        # 閉じていないフェンスは文書（コンテナ）の終わりまで伸び、後ろの図や表を飲み込む
        for k in range(start + 1, end):
            line = self.lines[k]
            if _mermaid_line(line) or _is_req_candidate(line):
                self.error(
                    "E_UNCLOSED_BLOCK",
                    f"{start + 1} 行目のコードブロックが閉じておらず、"
                    "後ろの図・要求の表を飲み込んでいる",
                    k,
                )
                return

    def code_block(self, tok: MdToken) -> None:
        start, end = tok.map or (0, 0)
        for k in range(start, end):
            if _mermaid_line(self.lines[k]):
                self.error(
                    "E_UNSUPPORTED_SYNTAX", "字下げのコードブロックの中の mermaid は読めない", k
                )
                return

    # -------------------------------------------------------------- HTML

    def html(self, content: str, line_index: int) -> None:
        if "mermaid" in content.lower():
            self.error("E_UNSUPPORTED_SYNTAX", "HTML の中の mermaid は読めない", line_index)
        elif content.count("<!--") > content.count("-->"):
            self.error(
                "E_UNSUPPORTED_SYNTAX",
                "閉じていない HTML コメント（後ろの内容が表示から隠れる）",
                line_index,
            )
        for k, line in enumerate(content.split("\n")):
            if _is_req_candidate(line):
                self.error("E_REQ_TABLE", "HTML の中に要求の表は書けない", line_index + k)
                return

    def paragraph(self, tok: MdToken) -> None:
        """GFM が表と認めない「表らしい行の並び」に要求の表の見出しがあればエラー。"""
        start = (tok.map or (0, 0))[0]
        for k, line in enumerate(tok.content.split("\n")):
            if "<!--" in line:
                self.html(line[line.index("<!--") :], start + k)
            if line.count("|") >= 2 and _is_req_candidate(line):
                self.error(
                    "E_REQ_TABLE",
                    "要求の表として読めない（見出しの次に区切り行が必要。列の数を揃える）",
                    start + k,
                )

    # -------------------------------------------------------------- 表

    def table(self, toks: list[MdToken], i: int) -> int:
        """`table_open` から読み、`table_close` の次の位置を返す。"""
        open_tok = toks[i]
        start = (open_tok.map or (0, 0))[0]
        header: list[str] = []
        rows: list[int] = []
        in_head = False
        j = i + 1
        while j < len(toks) and toks[j].type != "table_close":
            t = toks[j]
            if t.type == "thead_open":
                in_head = True
            elif t.type == "thead_close":
                in_head = False
            elif t.type == "inline" and in_head:
                header.append(t.content.strip())
            elif t.type == "tr_open" and not in_head and t.map is not None:
                rows.append(t.map[0])
            j += 1
        if not _is_req_header(header):
            return j + 1
        reason = None
        if open_tok.level != 0:
            reason = "要求の表は引用・リストの外に書く"
        elif not self.lines[start].startswith("|"):
            reason = "要求の表は行頭の `|` から書く（字下げ・先頭の `|` の省略は不可）"
        elif tuple(header) != REQ_HEADER:
            reason = f"要求の表の見出しは | {' | '.join(REQ_HEADER)} | でなければならない"
        elif any("\\|" in self.lines[k] for k in [start, *rows]):
            # GFM は \| を列の区切りにしないが、列の数え方が読み手とずれるので拒否する
            reason = "要求の表のセルに \\| は使えない"
        if reason is not None:
            self.error("E_REQ_TABLE", reason, start)
            return j + 1
        head_cells = _cells(self.lines[start], start + 1)
        body = [_cells(self.lines[k], k + 1) for k in rows]
        self.tables.append(RequirementTable(header=head_cells, rows=body))
        self.table_tokens.extend(head_cells)
        self.table_tokens.extend(t for r in body for t in r)
        return j + 1


def split_markdown(text: str) -> MarkdownParts:
    lines = text.split("\n")
    pre = _precheck(lines)
    if pre:
        return MarkdownParts(blocks=[], tables=[], errors=pre, table_tokens=[])
    toks = _MD.parse(text)
    sc = _Scanner(lines)
    i = 0
    while i < len(toks):
        t = toks[i]
        if t.type == "fence":
            sc.fence(t)
        elif t.type == "code_block":
            sc.code_block(t)
        elif t.type == "html_block":
            sc.html(t.content, (t.map or (0, 0))[0])
        elif t.type == "table_open":
            i = sc.table(toks, i)
            continue
        elif t.type == "inline" and i > 0 and toks[i - 1].type == "paragraph_open":
            sc.paragraph(t)
        i += 1
    return MarkdownParts(
        blocks=sc.blocks,
        tables=sc.tables,
        errors=sc.errors,
        table_tokens=sc.table_tokens,
        warnings=sc.warnings,
    )


__all__ = ["REQ_HEADER", "split_markdown"]
