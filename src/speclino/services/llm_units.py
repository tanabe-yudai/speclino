"""LLM 支援の規約で評価する単位を AST で取り出す（S70、設計 6.3 の 1）。

対象リポジトリのコードは import も実行もしない（`ast.parse` で読むだけ）。

LLM に渡す本文と単位の hash は同じ正規形から作る（キャッシュの汚染を防ぐ。同じ hash なら
LLM が見た本文も同じ）: 関数・エンドポイントは `ast.unparse`（コメントと空白の差を除く。
デコレーターと docstring は本文の一部として残す）、文字列リテラルは値そのもの。
`AssessmentUnit.text` にはこの正規形を入れ、表示用の生のソースは `ExtractedUnits.sources` に
別に持つ（LLM には渡さない）。

一意に決まらない単位は評価せず UNKNOWN にする（`ExtractedUnits.problems`）:
同じ修飾名の定義が複数あるもの、条件つき（if / try / for / while / with / match の中）の定義、
関数の中の定義、上限より大きい単位、構文エラーなどで読めないファイル、.py 以外のファイル。
"""

from __future__ import annotations

import ast
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
            source = target.read_text(encoding="utf-8")
            tree = ast.parse(source)
        except (SyntaxError, UnicodeDecodeError, ValueError):
            out.problems.append((rel, "ファイルを読めない（構文エラーなど）"))
            continue
        collector = _Collector(rel, source, params)
        collector.visit(tree)
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


def _unit_problem(cand: _Candidate, duplicated: set[str]) -> str | None:
    for _, reason in cand.chain:
        if reason is not None:
            return reason
    if any(qual in duplicated for qual, _ in cand.chain):
        return DUPLICATE_REASON
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
            if not self.params.call_names or name in self.params.call_names:
                args = [*node.args, *(k.value for k in node.keywords)]
                for arg in args:
                    if isinstance(arg, ast.Constant) and isinstance(arg.value, str) and arg.value:
                        self._add_literal(arg.value)
        self.generic_visit(node)

    def _add_function(self, node: ast.FunctionDef | ast.AsyncFunctionDef, qual: str) -> None:
        start = min([d.lineno for d in node.decorator_list] + [node.lineno])
        raw = "\n".join(self.source.splitlines()[start - 1 : node.end_lineno])
        self.candidates.append(
            _Candidate(
                symbol=f"{self.rel}::{qual}",
                kind="endpoint" if self.params.unit_kind == "endpoint" else "function",
                normalized=ast.unparse(node),
                raw=raw,
                chain=list(self.chain),
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
    "OTHER_LANGUAGE_REASON",
    "ExtractedUnits",
    "extract_units",
    "normalized_hash",
]
