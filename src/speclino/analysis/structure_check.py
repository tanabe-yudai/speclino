"""構造の義務（クラス図）の検査（設計 5.1）。AST だけを読み、対象リポジトリを import しない。

クラスが `source_roots` に一意に定義され、各操作がメソッドとして存在し、引数名
（self / cls を除く）と数が順に一致するか。
図に無いクラスやメソッドがあっても問題にしない（open-world）。
「無い／合わない」を確定できないとき（構文エラーのファイル、同名クラスの重複、ソースに無い基底、
`__getattr__`・クラス属性への代入・クラスデコレータ・メタクラス、`*args` / `**kwargs`）は UNKNOWN。
"""

from __future__ import annotations

import ast
from dataclasses import dataclass, field
from pathlib import Path

from speclino.conformance.obligation import ObligationContext, ObligationResult
from speclino.conformance.reasons import category_rank, worst
from speclino.domain.common import SourceRef
from speclino.domain.finding import Category, Finding, Verdict
from speclino.domain.uml import ClassOperation, StructureObligation

# 実装ではないディレクトリ（仮想環境・依存・テストの偽物）は読まない
_SKIP_DIRS = frozenset({"node_modules", "__pycache__", "venv", "site-packages", "tests", "test"})
_SELF_NAMES = frozenset({"self", "cls"})
_FuncDef = ast.FunctionDef | ast.AsyncFunctionDef


@dataclass
class _Source:
    rel: str
    tree: ast.Module


@dataclass
class _Index:
    sources: list[_Source] = field(default_factory=list)
    parse_errors: list[str] = field(default_factory=list)
    classes: dict[str, list[tuple[_Source, ast.ClassDef]]] = field(default_factory=dict)
    other_bindings: dict[str, list[str]] = field(default_factory=dict)


def _skip(rel: Path) -> bool:
    return any(p.startswith(".") or p in _SKIP_DIRS for p in rel.parts[:-1]) or rel.name.startswith(
        "test_"
    )


def _files(repo_root: Path, source_roots: tuple[str, ...]) -> list[Path]:
    root = repo_root.resolve()
    seen: set[Path] = set()
    out: list[Path] = []
    for sr in source_roots:
        base = (root / sr).resolve()
        if not base.is_dir() or not base.is_relative_to(root):
            continue
        for f in sorted(base.rglob("*.py")):
            rf = f.resolve()
            if rf in seen or not rf.is_relative_to(root) or _skip(rf.relative_to(root)):
                continue
            seen.add(rf)
            out.append(rf)
    return out


def _index(repo_root: Path, source_roots: tuple[str, ...]) -> _Index:
    root = repo_root.resolve()
    idx = _Index()
    for f in _files(repo_root, source_roots):
        rel = str(f.relative_to(root))
        try:
            tree = ast.parse(f.read_text(encoding="utf-8"), filename=rel)
        except (SyntaxError, UnicodeDecodeError, ValueError):
            idx.parse_errors.append(rel)
            continue
        src = _Source(rel, tree)
        idx.sources.append(src)
        for node in ast.walk(tree):
            if isinstance(node, ast.ClassDef):
                idx.classes.setdefault(node.name, []).append((src, node))
            elif isinstance(node, ast.Assign | ast.AnnAssign):
                targets = node.targets if isinstance(node, ast.Assign) else [node.target]
                for t in targets:
                    if isinstance(t, ast.Name):
                        idx.other_bindings.setdefault(t.id, []).append(rel)
            elif isinstance(node, ast.Import | ast.ImportFrom):
                for a in node.names:
                    name = (a.asname or a.name).split(".")[0]
                    idx.other_bindings.setdefault(name, []).append(rel)
    return idx


@dataclass
class _Check:
    verdict: Verdict
    category: Category
    message: str


def _ref(src: _Source, node: ast.AST, symbol: str) -> SourceRef:
    return SourceRef(
        snapshot_id="repo", symbol=symbol, path=src.rel, line_start=getattr(node, "lineno", None)
    )


def _spec_params(op: ClassOperation) -> list[str]:
    """`qty: int = 1` のような表記から名前だけを取る。"""
    out = []
    for p in op.params:
        name = p.split(":", 1)[0].split("=", 1)[0].strip()
        if name and name not in _SELF_NAMES:
            out.append(name)
    return out


def _params(fn: _FuncDef) -> list[str]:
    a = fn.args
    names = [x.arg for x in [*a.posonlyargs, *a.args, *a.kwonlyargs]]
    return [n for n in names if n not in _SELF_NAMES]


def _dynamic_reasons(cls: ast.ClassDef, name: str) -> list[str]:
    reasons: list[str] = []
    if cls.decorator_list:
        reasons.append("クラスデコレータ")
    if cls.keywords:
        reasons.append("メタクラス等のキーワード")
    for s in cls.body:
        if isinstance(s, _FuncDef) and s.name in ("__getattr__", "__getattribute__"):
            reasons.append(s.name)
        if isinstance(s, ast.Assign | ast.AnnAssign):
            targets = s.targets if isinstance(s, ast.Assign) else [s.target]
            if any(isinstance(t, ast.Name) and t.id == name for t in targets):
                reasons.append(f"クラス属性 {name} への代入")
    return reasons


def _find_method(
    idx: _Index, cls: ast.ClassDef, name: str, seen: set[int]
) -> tuple[_FuncDef | None, list[str]]:
    """メソッドを基底までたどって探す。戻り値の 2 つ目は「無い」と確定できない理由。"""
    seen.add(id(cls))
    found: _FuncDef | None = None
    for s in cls.body:
        if isinstance(s, _FuncDef) and s.name == name:
            found = s  # 後の定義が勝つ
    if found is not None:
        return found, []
    reasons = _dynamic_reasons(cls, name)
    for b in cls.bases:
        if isinstance(b, ast.Name) and b.id == "object":
            continue
        if not isinstance(b, ast.Name) or len(idx.classes.get(b.id, [])) != 1:
            reasons.append(f"基底 {ast.unparse(b)} がソース内で一意に定まらない")
            continue
        base = idx.classes[b.id][0][1]
        if id(base) in seen:
            continue
        m, rs = _find_method(idx, base, name, seen)
        if m is not None:
            return m, []
        reasons += rs
    return None, reasons


def _check_operation(idx: _Index, cls: ast.ClassDef, op: ClassOperation, cname: str) -> _Check:
    fn, reasons = _find_method(idx, cls, op.name, set())
    qual = f"{cname}.{op.name}"
    if fn is None:
        if reasons:
            return _Check(
                "UNKNOWN",
                "unsupported_analysis",
                f"{qual} の有無を確定できない（{'; '.join(reasons)}）",
            )
        return _Check("VIOLATED", "missing_effect", f"{qual} が定義されていない")
    want, got = _spec_params(op), _params(fn)
    if want == got:
        return _Check("VERIFIED_IN_SCOPE", "conformant", f"{qual}({', '.join(got)})")
    if fn.args.vararg or fn.args.kwarg:
        return _Check(
            "UNKNOWN",
            "unsupported_analysis",
            f"{qual} は可変長引数を持つため引数（期待 {want}、実装 {got}）を確定できない",
        )
    return _Check(
        "VIOLATED",
        "argument_mismatch",
        f"{qual} の引数が図と違う（期待 ({', '.join(want)})、実装 ({', '.join(got)})）",
    )


def check_structure(
    ob: StructureObligation,
    repo_root: Path,
    source_roots: tuple[str, ...],
    *,
    behavior_id: str | None = None,
) -> list[Finding]:
    """構造の義務 1 つを検査し、Finding を 1 つ返す。behavior_id の既定は義務 ID の図 ID 部分。"""
    ctx = ObligationContext(
        requirement_id=ob.id,
        behavior_id=behavior_id or ob.id.split("#", 1)[0],
        level=ob.level,
        claim_scope="command_invocation",
        analysis_scope=f"AST（source_roots: {', '.join(source_roots)}）",
    )
    idx = _index(repo_root, source_roots)
    found = idx.classes.get(ob.class_name, [])
    if not found:
        reasons = []
        if idx.parse_errors:
            reasons.append(f"構文エラーで読めないファイル: {', '.join(idx.parse_errors)}")
        if ob.class_name in idx.other_bindings:
            files = sorted(set(idx.other_bindings[ob.class_name]))
            reasons.append(
                f"名前 {ob.class_name} が代入または import で定義される: {', '.join(files)}"
            )
        if reasons:
            return [
                ctx.unknown(f"クラス {ob.class_name} の有無を確定できない（{'; '.join(reasons)}）")
            ]
        return [
            ctx.finding(
                ObligationResult(
                    "VIOLATED",
                    "missing_effect",
                    f"クラス {ob.class_name} が source_roots に定義されていない",
                    expected=f"クラス {ob.class_name}",
                    actual="定義なし",
                )
            )
        ]
    if len(found) > 1:
        where = ", ".join(f"{s.rel}:{c.lineno}" for s, c in found)
        return [ctx.unknown(f"クラス {ob.class_name} が複数ある（{where}）。どれか確定できない")]
    src, cls = found[0]
    code_refs = [_ref(src, cls, ob.class_name)]
    checks = [_check_operation(idx, cls, op, ob.class_name) for op in ob.operations]
    expected = f"クラス {ob.class_name}" + (
        "（" + ", ".join(f"{o.name}({', '.join(_spec_params(o))})" for o in ob.operations) + "）"
        if ob.operations
        else ""
    )
    verdict: Verdict = worst([c.verdict for c in checks]) if checks else "VERIFIED_IN_SCOPE"
    if verdict == "VERIFIED_IN_SCOPE":
        return [
            ctx.finding(
                ObligationResult(
                    "VERIFIED_IN_SCOPE",
                    "conformant",
                    f"クラス {ob.class_name} と {len(checks)} 個の操作が図と一致",
                    expected=expected,
                ),
                code_refs=code_refs,
            )
        ]
    bad = sorted(
        (c for c in checks if c.verdict == verdict), key=lambda c: category_rank(c.category)
    )
    return [
        ctx.finding(
            ObligationResult(
                verdict,
                bad[0].category,
                "; ".join(c.message for c in bad),
                expected=expected,
                actual="; ".join(c.message for c in checks),
            ),
            code_refs=code_refs,
        )
    ]


__all__ = ["check_structure"]
