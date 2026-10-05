"""LLM 支援の規約で評価する単位を AST で取り出す（S70、設計 6.3 の 1）。

対象リポジトリのコードは import も実行もしない（`ast.parse` で読むだけ）。

LLM に渡す本文と単位の hash は同じ正規形から作る（キャッシュの汚染を防ぐ。同じ hash なら
LLM が見た本文も同じ）: 関数・エンドポイントは、すべての str / bytes の定数（docstring・f-string の
定数部分を含む）を `<str len=N>` / `<bytes len=N>` の目印に、6 桁を超える数値を `<num>` に置き換えた
AST の `ast.unparse`（コメントと空白の差を除く。#114 裁定 37: 値は送らず、名前・構造・呼出しだけで
判定させる）。文字列リテラルの単位は値そのもので、規約の `call_names` の呼出しに渡されたものだけ
（`call_names` の無い string_literal の規約は評価しない）。
`AssessmentUnit.text` にはこの正規形を入れ、表示用の生のソースは `ExtractedUnits.sources` に
別に持つ（LLM には渡さない）。

一意に決まらない単位は評価せず UNKNOWN にする（`ExtractedUnits.problems`）:
同じ修飾名の定義が複数あるもの、条件つき（if / try / for / while / with / match の中）の定義、
関数の中の定義、上限より大きい単位、構文エラーなどで読めないファイル、.py 以外のファイル。
"""

from __future__ import annotations

import ast
import copy
import json
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path
from typing import Literal

from speclino.domain.assessment import AssessmentUnit
from speclino.domain.policy_params import LlmChecklistParams
from speclino.spec import content_hash
from speclino.verifiers.snapshot import DirectorySnapshot

_ENDPOINT_METHODS = frozenset(
    {"get", "post", "put", "patch", "delete", "head", "options", "api_route", "route"}
)
# 単位の正規形の上限（文字数）。超える単位は切り詰めずに UNKNOWN にする（切り詰めると、
# 見ていない部分の変更で hash だけが変わる・同じ本文で別の判断になるといったずれが起きる）
MAX_UNIT_TEXT = 6000

_CONDITIONAL = (
    ast.If,
    ast.Try,
    ast.TryStar,
    ast.For,
    ast.AsyncFor,
    ast.While,
    ast.With,
    ast.AsyncWith,
    ast.Match,
)

UnitKind = Literal["function", "string_literal", "endpoint"]

DUPLICATE_REASON = "同名の定義が複数あり一意に決まらない"
CONDITIONAL_REASON = "条件つきの定義（if / try などの中）で一意に決まらない"
NESTED_REASON = "関数の中の定義で一意に決まらない"
OTHER_LANGUAGE_REASON = "対象外の言語（LLM 支援の単位は Python だけ）"
NO_CALL_NAMES_REASON = (
    "string_literal の規約に params.call_names が無い（送る文字列を呼出しで限らないと評価しない）"
)
TOO_DEEP_REASON = "入れ子が深すぎて正規形を作れない"
# ファイルの大きさの上限（#114 fix round 3）。超えるファイルは読まずに UNKNOWN
MAX_FILE_BYTES = 1024 * 1024
FILE_TOO_LARGE_REASON = f"ファイルが大きすぎる（{MAX_FILE_BYTES} バイト超）"
# 元のソースの範囲がこれを超える単位は、置き換え・正規形を作らずに UNKNOWN（正規形は上限の
# MAX_UNIT_TEXT を超えるのが確実なので、deepcopy と unparse の費用を払わない）
MAX_UNIT_SOURCE = 4 * MAX_UNIT_TEXT


@dataclass
class ExtractedUnits:
    units: list[AssessmentUnit] = field(default_factory=list)
    # 評価できないもの: (パスまたはシンボル, 理由)。黙って飛ばさず UNKNOWN にする
    problems: list[tuple[str, str]] = field(default_factory=list)
    # シンボル → 生のソース（表示用。LLM には渡さない）
    sources: dict[str, str] = field(default_factory=dict)


def normalized_hash(normalized: str) -> str:
    return content_hash(normalized)


def extract_units(
    root: Path, params: LlmChecklistParams, default_paths: list[str]
) -> ExtractedUnits:
    """`params.paths`（空なら `default_paths`）に一致するファイルから単位を取り出す。順は決定的。"""
    if params.unit_kind == "string_literal" and not params.call_names:
        return ExtractedUnits(problems=[("params.call_names", NO_CALL_NAMES_REASON)])
    snapshot = DirectorySnapshot(root=root)
    patterns = params.paths or default_paths
    files = sorted({p for pattern in patterns for p in snapshot.glob(pattern)})
    out = ExtractedUnits()
    seen: set[str] = set()
    base = root.resolve()
    for rel in files:
        if not rel.endswith(".py"):
            out.problems.append((rel, OTHER_LANGUAGE_REASON))
            continue
        target = (root / rel).resolve()
        if not target.is_relative_to(base):
            out.problems.append((rel, "リポジトリの外を指すファイル"))
            continue
        try:
            if target.stat().st_size > MAX_FILE_BYTES:
                out.problems.append((rel, FILE_TOO_LARGE_REASON))
                continue
            source = target.read_text(encoding="utf-8")
            tree = ast.parse(source)
        except (SyntaxError, UnicodeDecodeError, ValueError, OSError):
            out.problems.append((rel, "ファイルを読めない（構文エラーなど）"))
            continue
        except (RecursionError, MemoryError):
            out.problems.append((rel, TOO_DEEP_REASON))
            continue
        collector = _Collector(rel, source, params)
        try:
            collector.visit(tree)
        except (RecursionError, MemoryError):
            # 入れ子が深すぎて辿れない（run を落とさず、このファイルを UNKNOWN にする）
            out.problems.append((rel, TOO_DEEP_REASON))
            continue
        dup = {q for q, n in Counter(collector.defs).items() if n > 1}
        for cand in collector.candidates:
            if cand.symbol in seen:
                continue
            seen.add(cand.symbol)
            reason = _unit_problem(cand, dup)
            if reason is not None:
                out.problems.append((cand.symbol, reason))
                continue
            out.units.append(
                AssessmentUnit(
                    symbol=cand.symbol,
                    kind=cand.kind,
                    text=cand.normalized,
                    unit_hash=normalized_hash(cand.normalized),
                )
            )
            out.sources[cand.symbol] = cand.raw
    return out


@dataclass
class _Candidate:
    symbol: str
    kind: UnitKind
    normalized: str
    raw: str
    # 囲む定義（自身を含む）の (修飾名, 一意に決まらない理由)
    chain: list[tuple[str, str | None]]
    # 単位そのものの問題（正規形を作れないなど）
    problem: str | None = None


def _unit_problem(cand: _Candidate, duplicated: set[str]) -> str | None:
    for _, reason in cand.chain:
        if reason is not None:
            return reason
    if any(qual in duplicated for qual, _ in cand.chain):
        return DUPLICATE_REASON
    if cand.problem is not None:
        return cand.problem
    if len(cand.normalized) > MAX_UNIT_TEXT:
        return f"単位が大きすぎる（{len(cand.normalized)} 文字 > {MAX_UNIT_TEXT}）"
    return None


class _Collector(ast.NodeVisitor):
    def __init__(self, rel: str, source: str, params: LlmChecklistParams) -> None:
        self.rel = rel
        self.source = source
        self.params = params
        self.scope: list[str] = []
        self.chain: list[tuple[str, str | None]] = []
        self.conditional = 0
        self.in_function = 0
        self.defs: list[str] = []
        self.candidates: list[_Candidate] = []
        self.lines = source.splitlines()
        # 各行の先頭の文字位置（単位の元のソースの大きさを、切り出さずに求める）
        self.offsets = [0]
        for line in self.lines:
            self.offsets.append(self.offsets[-1] + len(line) + 1)

    def _qual(self, name: str | None = None) -> str:
        parts = [*self.scope, name] if name else self.scope
        return ".".join(parts) or "<module>"

    def _reason(self) -> str | None:
        if self.in_function:
            return NESTED_REASON
        if self.conditional:
            return CONDITIONAL_REASON
        return None

    def generic_visit(self, node: ast.AST) -> None:
        if isinstance(node, _CONDITIONAL):
            self.conditional += 1
            super().generic_visit(node)
            self.conditional -= 1
        else:
            super().generic_visit(node)

    def visit_ClassDef(self, node: ast.ClassDef) -> None:
        qual = self._qual(node.name)
        self.defs.append(qual)
        self.chain.append((qual, self._reason()))
        self.scope.append(node.name)
        self.generic_visit(node)
        self.scope.pop()
        self.chain.pop()

    def _visit_function(self, node: ast.FunctionDef | ast.AsyncFunctionDef) -> None:
        qual = self._qual(node.name)
        self.defs.append(qual)
        self.chain.append((qual, self._reason()))
        kind = self.params.unit_kind
        if kind == "function" or (kind == "endpoint" and _is_endpoint(node)):
            self._add_function(node, qual)
        self.scope.append(node.name)
        self.in_function += 1
        # 関数の本体の if などは、その中の文字列の場所を曖昧にしない（入れ子の定義は NESTED）
        saved, self.conditional = self.conditional, 0
        self.generic_visit(node)
        self.conditional = saved
        self.in_function -= 1
        self.scope.pop()
        self.chain.pop()

    visit_FunctionDef = _visit_function
    visit_AsyncFunctionDef = _visit_function

    def visit_Call(self, node: ast.Call) -> None:
        if self.params.unit_kind == "string_literal":
            name = _call_name(node.func)
            if name in self.params.call_names:
                args = [*node.args, *(k.value for k in node.keywords)]
                for arg in args:
                    if isinstance(arg, ast.Constant) and isinstance(arg.value, str) and arg.value:
                        self._add_literal(arg.value)
        self.generic_visit(node)

    def _add_function(self, node: ast.FunctionDef | ast.AsyncFunctionDef, qual: str) -> None:
        start = min([d.lineno for d in node.decorator_list] + [node.lineno])
        end = node.end_lineno or node.lineno
        span = self.offsets[min(end, len(self.lines))] - self.offsets[start - 1]
        # 評価しない単位（入れ子・条件つき・元のソースが大きすぎる）は、置き換え・正規形を作らない
        # （#114 fix round 3: 大きな入れ子のファイルで deepcopy と unparse を繰り返さない）
        problem = next((r for _, r in self.chain if r is not None), None)
        if problem is None and span > MAX_UNIT_SOURCE:
            problem = f"単位が大きすぎる（元のソース {span} 文字 > {MAX_UNIT_SOURCE}）"
        if problem is not None:
            self.candidates.append(
                _Candidate(
                    symbol=f"{self.rel}::{qual}",
                    kind="endpoint" if self.params.unit_kind == "endpoint" else "function",
                    normalized="",
                    raw="",
                    chain=list(self.chain),
                    problem=problem,
                )
            )
            return
        raw = "\n".join(self.lines[start - 1 : end])
        try:
            normalized, problem = ast.unparse(redact(node)), None
        except (RecursionError, MemoryError):
            # 正規形を作れない単位は送らず UNKNOWN（run は落とさない）
            normalized, problem = "", TOO_DEEP_REASON
        self.candidates.append(
            _Candidate(
                symbol=f"{self.rel}::{qual}",
                kind="endpoint" if self.params.unit_kind == "endpoint" else "function",
                normalized=normalized,
                raw=raw,
                chain=list(self.chain),
                problem=problem,
            )
        )

    def _add_literal(self, value: str) -> None:
        label = json.dumps(value, ensure_ascii=False)
        if len(label) > 80:
            # 切り詰めた見出しが同じ別の文字列を取り違えない（hash の先頭で区別する）
            label = f"{label[:60]}…#{normalized_hash(value)[7:19]}"
        self.candidates.append(
            _Candidate(
                symbol=f"{self.rel}::{self._qual()}::{label}",
                kind="string_literal",
                normalized=value,
                raw=value,
                chain=list(self.chain),
            )
        )


MAX_PLAIN_DIGITS = 6


class _Redactor(ast.NodeTransformer):
    """str / bytes の定数を長さだけの目印に、大きな数値を `<num>` に置き換える。"""

    def visit_JoinedStr(self, node: ast.JoinedStr) -> ast.AST:
        # f-string の定数部分は Constant のまま目印の文字列にする（unparse できる形）
        values: list[ast.expr] = []
        for v in node.values:
            if isinstance(v, ast.Constant) and isinstance(v.value, str):
                values.append(ast.Constant(value=f"<str len={len(v.value)}>"))
            else:
                values.append(self.visit(v))
        node.values = values
        return node

    def visit_Constant(self, node: ast.Constant) -> ast.AST:
        value = node.value
        if isinstance(value, str):
            return ast.Name(id=f"<str len={len(value)}>", ctx=ast.Load())
        if isinstance(value, bytes):
            return ast.Name(id=f"<bytes len={len(value)}>", ctx=ast.Load())
        if isinstance(value, (int, float, complex)) and not isinstance(value, bool):
            digits = sum(c.isdigit() for c in repr(value))
            if digits > MAX_PLAIN_DIGITS:
                return ast.Name(id="<num>", ctx=ast.Load())
        return node


def redact(node: ast.AST) -> ast.AST:
    """LLM に送る前に値を目印へ置き換えた写し（元の木は変えない）。"""
    redacted: ast.AST = _Redactor().visit(copy.deepcopy(node))
    return redacted


def _call_name(func: ast.expr) -> str | None:
    if isinstance(func, ast.Name):
        return func.id
    if isinstance(func, ast.Attribute):
        return func.attr
    return None


def _is_endpoint(node: ast.FunctionDef | ast.AsyncFunctionDef) -> bool:
    return any(
        isinstance(deco, ast.Call)
        and isinstance(deco.func, ast.Attribute)
        and deco.func.attr in _ENDPOINT_METHODS
        for deco in node.decorator_list
    )


__all__ = [
    "CONDITIONAL_REASON",
    "DUPLICATE_REASON",
    "MAX_UNIT_TEXT",
    "NESTED_REASON",
    "NO_CALL_NAMES_REASON",
    "OTHER_LANGUAGE_REASON",
    "redact",
    "FILE_TOO_LARGE_REASON",
    "MAX_FILE_BYTES",
    "TOO_DEEP_REASON",
    "ExtractedUnits",
    "extract_units",
    "normalized_hash",
]
