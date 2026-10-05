"""Markdown から `mermaid` ブロックと要求の表を切り出す（grammar.md 1 章・8 章）。

ブロック構造（フェンス・リスト・引用・HTML・表）の解釈は CommonMark 準拠の `markdown-it-py` に任せ、
読み手に見える構造と SpecLino が読む構造を一致させる。
正規形でない図や表は黙って読み飛ばさずエラーにする。
インラインの解析は使わない（`[` の連続などで二次の時間がかかるため。表のセルは生の内容で足りる）。
"""

from __future__ import annotations

import unicodedata

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
_LEAF_BLOCKS = frozenset(
    {"fence", "code_block", "html_block", "table_open", "paragraph_open", "heading_open"}
)
"""行を説明できる葉のブロック。

コンテナ（引用・リスト）の map は中で捨てられた内容まで覆うので使わない。
"""


def _parser() -> MarkdownIt:
    # maxNesting は既定のまま。超えた入れ子の中身は markdown-it が黙って捨てるが、
    # 事後照合（_unexplained_lines）で図と要求の表が消えていないことを確かめる
    md = MarkdownIt("commonmark").enable("table")
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


def _is_invisible(c: str) -> bool:
    """行頭から除く文字: 空白・タブ・引用の `>`・Unicode の Zs と Cf。

    Zs は空白類、Cf は BOM・ゼロ幅・方向制御など。
    """
    return c in " \t>" or unicodedata.category(c) in ("Zs", "Cf")


def _skip_prefix(line: str) -> int:
    """行頭のコンテナの接頭辞（見えない文字・`>`・リストの記号）の後ろの位置。

    index を進める 1 回の走査（再スライスしない）。
    """
    n = len(line)
    i = 0
    while i < n:
        c = line[i]
        if _is_invisible(c):
            i += 1
        elif c in "-*+" and i + 1 < n and line[i + 1] in " \t":
            i += 2
        elif c.isdigit():
            j = i
            while j < n and j - i < 9 and line[j].isdigit():
                j += 1
            if j < n - 1 and line[j] in ".)" and line[j + 1] in " \t":
                i = j + 2
            else:
                return i
        else:
            return i
    return i


def _fence_open(line: str) -> tuple[str, int, str] | None:
    """フェンスの開きに見える行か（コンテナの接頭辞・リストの記号を除いて見る）。"""
    i = _skip_prefix(line)
    if i >= len(line) or line[i] not in "`~":
        return None
    ch = line[i]
    j = i
    while j < len(line) and line[j] == ch:
        j += 1
    if j - i < 3:
        return None
    return ch, j - i, line[j:].strip()


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


_CODE_BLOCKS = frozenset({"fence", "code_block", "html_block"})


def _is_mermaid_like(line: str) -> bool:
    """接頭辞を除いた後が「` か ~ が 3 個以上 → 空白・タブ 0 個以上 → mermaid」か。

    mermaid は大文字小文字を無視する。O(行長)。
    """
    n = len(line)
    i = _skip_prefix(line)
    if i >= n or line[i] not in "`~":
        return False
    ch = line[i]
    j = i
    while j < n and line[j] == ch:
        j += 1
    if j - i < 3:
        return False
    while j < n and line[j] in " \t":
        j += 1
    return line[j : j + 7].lower() == "mermaid"


def _is_header_like(line: str) -> bool:
    if "|" not in line:
        return False
    return _is_req_header(_loose_cells(line[_skip_prefix(line) :]))


def _unexplained_lines(lines: list[str], toks: list[MdToken]) -> list[int]:
    """図の開き・要求の表の見出しに見えるのに、それとして説明できない行（0 始まり）。O(行数)。

    - 図の開きに見える行は、フェンス（受理・エラー・閉じたコードの中）・字下げのコード・HTML の
      map の中でなければならない。段落になっている（NBSP・ゼロ幅文字・2 つ目の BOM の後ろ）
      なら、読み手には図に見えないが書き手の意図は図なので知らせる
    - 要求の表の見出しに見える行は、どれかの葉のブロック（表・段落を含む）の map の中に必要
    入れ子が深すぎて markdown-it が中身を捨てると、その行はどの葉のブロックにもならない。
    事前の深さの見積りと markdown-it の解釈がずれても、図と要求の表が黙って消えないようにする。
    """
    n = len(lines)
    # 被覆は差分配列（[start, end) ごとに +1 / -1 → 累積和）で O(行数 + トークン数)
    leaf_diff = [0] * (n + 1)
    code_diff = [0] * (n + 1)
    for t in toks:
        if t.type in _LEAF_BLOCKS and t.map is not None:
            start, end = max(0, t.map[0]), min(n, t.map[1])
            leaf_diff[start] += 1
            leaf_diff[end] -= 1
            if t.type in _CODE_BLOCKS:
                code_diff[start] += 1
                code_diff[end] -= 1
    out: list[int] = []
    leaf = 0
    code = 0
    for k, line in enumerate(lines):
        leaf += leaf_diff[k]
        code += code_diff[k]
        if code > 0:
            continue
        if _is_mermaid_like(line) or (leaf == 0 and _is_header_like(line)):
            out.append(k)
    return out


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
    unexplained = _unexplained_lines(lines, toks)
    shown = unexplained[:20]
    for idx, k in enumerate(shown):
        msg = "Markdown の構造を解釈できない（入れ子が深すぎる等）。図・要求の表として読めない"
        if idx == len(shown) - 1 and len(unexplained) > len(shown):
            msg += f"（ほか {len(unexplained) - len(shown)} 件）"
        sc.error("E_UNSUPPORTED_SYNTAX", msg, k)
    return MarkdownParts(
        blocks=sc.blocks,
        tables=sc.tables,
        errors=sc.errors,
        table_tokens=sc.table_tokens,
        warnings=sc.warnings,
    )


__all__ = ["REQ_HEADER", "split_markdown"]
