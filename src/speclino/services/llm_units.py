"""LLM 支援の規約で評価する単位を AST で取り出す（S70、設計 6.3 の 1）。

対象リポジトリのコードは import も実行もしない（`ast.parse` で読むだけ）。単位の hash は
空白とコメントを除いた正規形から作るので、整形やコメントだけの変更では変わらない:
関数・エンドポイントは `ast.unparse`（デコレーターと docstring は本文の一部として含む）、
文字列リテラルは値そのもの。
"""

from __future__ import annotations

import ast
import json
from dataclasses import dataclass, field
from pathlib import Path

from speclino.domain.assessment import AssessmentUnit
from speclino.domain.policy_params import LlmChecklistParams
from speclino.spec import content_hash
from speclino.verifiers.snapshot import DirectorySnapshot

_ENDPOINT_METHODS = frozenset(
    {"get", "post", "put", "patch", "delete", "head", "options", "api_route", "route"}
)
# 根拠の引用に使う本文の上限（LLM に渡すプロンプトの大きさも抑える）
MAX_UNIT_TEXT = 6000


@dataclass
class ExtractedUnits:
    units: list[AssessmentUnit] = field(default_factory=list)
    # 読めなかった（構文エラー・文字コード・ルート外）ファイル。黙って飛ばさず UNKNOWN にする
    unreadable: list[str] = field(default_factory=list)


def normalized_hash(normalized: str) -> str:
    return content_hash(normalized)


def extract_units(
    root: Path, params: LlmChecklistParams, default_paths: list[str]
) -> ExtractedUnits:
    """`params.paths`（空なら `default_paths`）に一致する .py から単位を取り出す。順は決定的。"""
    snapshot = DirectorySnapshot(root=root)
    patterns = params.paths or default_paths
    files = sorted({p for pattern in patterns for p in snapshot.glob(pattern) if p.endswith(".py")})
    out = ExtractedUnits()
    seen: set[str] = set()
    base = root.resolve()
    for rel in files:
        target = (root / rel).resolve()
        if not target.is_relative_to(base):
            out.unreadable.append(rel)
            continue
        try:
            source = target.read_text(encoding="utf-8")
            tree = ast.parse(source)
        except (SyntaxError, UnicodeDecodeError, ValueError):
            out.unreadable.append(rel)
            continue
        for unit in _units_of(rel, source, tree, params):
            if unit.symbol not in seen:
                seen.add(unit.symbol)
                out.units.append(unit)
    return out


def _units_of(
    rel: str, source: str, tree: ast.Module, params: LlmChecklistParams
) -> list[AssessmentUnit]:
    collector = _Collector(rel, source, params)
    collector.visit(tree)
    return collector.units


class _Collector(ast.NodeVisitor):
    def __init__(self, rel: str, source: str, params: LlmChecklistParams) -> None:
        self.rel = rel
        self.source = source
        self.params = params
        self.scope: list[str] = []
        self.units: list[AssessmentUnit] = []

    def _qual(self, name: str | None = None) -> str:
        parts = [*self.scope, name] if name else self.scope
        return ".".join(parts) or "<module>"

    def visit_ClassDef(self, node: ast.ClassDef) -> None:
        self.scope.append(node.name)
        self.generic_visit(node)
        self.scope.pop()

    def _visit_function(self, node: ast.FunctionDef | ast.AsyncFunctionDef) -> None:
        kind = self.params.unit_kind
        if kind == "function" or (kind == "endpoint" and _is_endpoint(node)):
            self._add_function(node, kind)
        self.scope.append(node.name)
        self.generic_visit(node)
        self.scope.pop()

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

    def _add_function(self, node: ast.FunctionDef | ast.AsyncFunctionDef, kind: str) -> None:
        start = min([d.lineno for d in node.decorator_list] + [node.lineno])
        lines = self.source.splitlines()[start - 1 : node.end_lineno]
        text = "\n".join(lines)[:MAX_UNIT_TEXT] or node.name
        self.units.append(
            AssessmentUnit(
                symbol=f"{self.rel}::{self._qual(node.name)}",
                kind="endpoint" if kind == "endpoint" else "function",
                text=text,
                unit_hash=normalized_hash(ast.unparse(node)),
            )
        )

    def _add_literal(self, value: str) -> None:
        unit_hash = normalized_hash(value)
        label = json.dumps(value, ensure_ascii=False)
        if len(label) > 80:
            # 切り詰めた見出しが同じ別の文字列を取り違えない（hash の先頭で区別する）
            label = f"{label[:60]}…#{unit_hash[7:19]}"
        self.units.append(
            AssessmentUnit(
                symbol=f"{self.rel}::{self._qual()}::{label}",
                kind="string_literal",
                text=value[:MAX_UNIT_TEXT],
                unit_hash=unit_hash,
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


__all__ = ["MAX_UNIT_TEXT", "ExtractedUnits", "extract_units", "normalized_hash"]
