"""構造の義務（クラス図）の検査（設計 5.1）。AST だけを読み、対象リポジトリを import しない。

クラスが `source_roots` に一意に定義され、各操作がメソッドとして存在し、引数名
（self / cls を除く）と数が順に一致するか。
図に無いクラスやメソッドがあっても問題にしない（open-world）。
モジュール直下のクラス定義だけを数える。
「無い／合わない」を確定できないとき（構文エラーのファイル、同名クラスの重複、ソースに無い基底、
関数内など直下でない定義だけがある、
クラス本体の複合文（if / try / for / while / with / match）・exec・setattr、
`__getattr__`・クラス属性への代入・クラスデコレータ・メタクラス、`*args` / `**kwargs`）は UNKNOWN。

`<<module>>` の義務は、モジュール（bindings の `modules` か、source_roots の `<名前>.py` /
`<名前>/__init__.py` が一意なもの）の直下の `def` / `async def` を操作として同じ規則で照合する。
関数が無いと確定できない動的な定義（`__getattr__`・`exec`・`globals()`・同名への代入・import・
複合文の中の同名の定義）があれば UNKNOWN。
"""

from __future__ import annotations

import ast
from collections.abc import Mapping
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
    nested: dict[str, list[str]] = field(default_factory=dict)  # モジュール直下でないクラス定義
    setattr_targets: dict[str, list[str]] = field(default_factory=dict)  # setattr(名前, ...) の対象
    exec_files: set[str] = field(default_factory=set)
    globals_files: set[str] = field(default_factory=set)
    patched: dict[tuple[str, str], list[str]] = field(default_factory=dict)  # クラス名.属性 = ...
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


def iter_source_files(repo_root: Path, source_roots: tuple[str, ...]) -> list[Path]:
    """構造の検査が読む実装のファイル（仮想環境・依存・テストを除く）。"""
    return _files(repo_root, source_roots)


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
        top = {id(n) for n in tree.body if isinstance(n, ast.ClassDef)}
        for node in ast.walk(tree):
            if isinstance(node, ast.ClassDef):
                if id(node) in top:
                    idx.classes.setdefault(node.name, []).append((src, node))
                else:
                    idx.nested.setdefault(node.name, []).append(f"{rel}:{node.lineno}")
            elif isinstance(node, ast.Call) and isinstance(node.func, ast.Name):
                if node.func.id == "exec":
                    idx.exec_files.add(rel)
                elif node.func.id == "globals":
                    idx.globals_files.add(rel)
                elif node.func.id == "setattr" and node.args and isinstance(node.args[0], ast.Name):
                    idx.setattr_targets.setdefault(node.args[0].id, []).append(rel)
            elif isinstance(node, ast.Assign | ast.AnnAssign):
                targets = node.targets if isinstance(node, ast.Assign) else [node.target]
                for t in targets:
                    if isinstance(t, ast.Name):
                        idx.other_bindings.setdefault(t.id, []).append(rel)
                    elif isinstance(t, ast.Attribute) and isinstance(t.value, ast.Name):
                        idx.patched.setdefault((t.value.id, t.attr), []).append(rel)
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


_COMPOUND = (
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


def _dynamic_reasons(idx: _Index, src: _Source, cls: ast.ClassDef, name: str) -> list[str]:
    """メソッドが直下に無いとき、「無い」と確定できない理由（動的に定義され得る）。"""
    reasons: list[str] = []
    if any(isinstance(s, _COMPOUND) for s in cls.body):
        reasons.append("クラス本体の条件・繰返し・例外処理などの複合文")
    if cls.name in idx.setattr_targets:
        reasons.append(
            f"setattr({cls.name}, ...)（{', '.join(sorted(set(idx.setattr_targets[cls.name])))}）"
        )
    if (cls.name, name) in idx.patched:
        files = ", ".join(sorted(set(idx.patched[(cls.name, name)])))
        reasons.append(f"{cls.name}.{name} への代入（{files}）")
    if src.rel in idx.exec_files:
        reasons.append(f"exec（{src.rel}）")
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
    idx: _Index, src: _Source, cls: ast.ClassDef, name: str, seen: set[int]
) -> tuple[_FuncDef | None, list[str]]:
    """メソッドを基底までたどって探す。戻り値の 2 つ目は「無い」と確定できない理由。"""
    seen.add(id(cls))
    found: _FuncDef | None = None
    for s in cls.body:
        if isinstance(s, _FuncDef) and s.name == name:
            found = s  # 後の定義が勝つ
    if found is not None:
        return found, []
    reasons = _dynamic_reasons(idx, src, cls, name)
    for b in cls.bases:
        if isinstance(b, ast.Name) and b.id == "object":
            continue
        if not isinstance(b, ast.Name) or len(idx.classes.get(b.id, [])) != 1 or b.id in idx.nested:
            reasons.append(f"基底 {ast.unparse(b)} がソース内で一意に定まらない")
            continue
        base_src, base = idx.classes[b.id][0]
        if id(base) in seen:
            continue
        m, rs = _find_method(idx, base_src, base, name, seen)
        if m is not None:
            return m, []
        reasons += rs
    return None, reasons


def _compare_params(fn: _FuncDef, op: ClassOperation, qual: str) -> _Check:
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


def _check_operation(
    idx: _Index, src: _Source, cls: ast.ClassDef, op: ClassOperation, cname: str
) -> _Check:
    fn, reasons = _find_method(idx, src, cls, op.name, set())
    qual = f"{cname}.{op.name}"
    if fn is None:
        return _not_found(qual, reasons)
    return _compare_params(fn, op, qual)


def _not_found(qual: str, reasons: list[str]) -> _Check:
    if reasons:
        return _Check(
            "UNKNOWN",
            "unsupported_analysis",
            f"{qual} の有無を確定できない（{'; '.join(reasons)}）",
        )
    return _Check("VIOLATED", "missing_effect", f"{qual} が定義されていない")


def _module_dynamic_reasons(idx: _Index, src: _Source, name: str) -> list[str]:
    """関数が直下に無いとき、「無い」と確定できない理由（モジュールの名前は動的に束縛され得る）。"""
    reasons: list[str] = []
    body = src.tree.body
    if any(isinstance(s, _FuncDef) and s.name == "__getattr__" for s in body):
        reasons.append("モジュールの __getattr__")
    if src.rel in idx.exec_files:
        reasons.append(f"exec（{src.rel}）")
    if src.rel in idx.globals_files:
        reasons.append(f"globals() の呼出し（{src.rel}）")
    for node in ast.walk(src.tree):
        if isinstance(node, ast.Name) and isinstance(node.ctx, ast.Store) and node.id == name:
            reasons.append(f"{name} への代入（{src.rel}:{node.lineno}）")
        elif isinstance(node, ast.alias) and (node.asname or node.name.split(".")[0]) == name:
            reasons.append(f"{name} の import（{src.rel}）")
        elif isinstance(node, ast.alias) and node.name == "*":
            reasons.append(f"import *（{src.rel}）")
        elif isinstance(node, ast.Global) and name in node.names:
            reasons.append(f"global {name}（{src.rel}:{node.lineno}）")
    for s in body:
        if not isinstance(s, _COMPOUND):
            continue
        for node in ast.walk(s):
            if isinstance(node, _FuncDef) and node.name == name:
                reasons.append(f"条件付きなどの複合文の中の定義（{src.rel}:{node.lineno}）")
    return reasons


def _check_module_operation(idx: _Index, src: _Source, op: ClassOperation, mname: str) -> _Check:
    fn: _FuncDef | None = None
    for s in src.tree.body:
        if isinstance(s, _FuncDef) and s.name == op.name:
            fn = s  # 後の定義が勝つ
    qual = f"{mname}.{op.name}"
    reasons = _module_dynamic_reasons(idx, src, op.name)
    if fn is None:
        return _not_found(qual, reasons)
    if fn.decorator_list:
        reasons.append("デコレーター（呼ばれる関数が定義と同じと確定できない）")
    if reasons:
        # 定義があっても、後から名前が別のものに束縛され得れば「この定義が操作」と確定できない
        return _Check(
            "UNKNOWN",
            "unsupported_analysis",
            f"{qual} の定義を確定できない（{'; '.join(reasons)}）",
        )
    return _compare_params(fn, op, qual)


@dataclass(frozen=True)
class Located:
    """クラス・モジュールの解決結果。`missing` が True なら確定的に無い（VIOLATED 相当）。"""

    qualname: str | None
    path: str | None
    problem: str | None
    missing: bool


def module_qualname(rel_path: str, source_roots: tuple[str, ...]) -> str | None:
    """repo_root からの相対パスを、それを含む最も長い source_root からのモジュール名にする。"""
    parts = Path(rel_path).parts
    best: tuple[str, ...] | None = None
    for sr in source_roots:
        sr_parts = tuple(p for p in Path(sr).parts if p != ".")
        if parts[: len(sr_parts)] == sr_parts and (best is None or len(sr_parts) > len(best)):
            best = sr_parts
    if best is None:
        return None
    rest = list(parts[len(best) :])
    if not rest or not rest[-1].endswith(".py"):
        return None
    rest[-1] = rest[-1][: -len(".py")]
    if rest[-1] == "__init__":
        rest.pop()
    return ".".join(rest) or None


def _locate_class(
    idx: _Index, name: str, source_roots: tuple[str, ...]
) -> tuple[Located, _Source | None, ast.ClassDef | None]:
    found = idx.classes.get(name, [])
    if not found:
        reasons = []
        if idx.parse_errors:
            reasons.append(f"構文エラーで読めないファイル: {', '.join(idx.parse_errors)}")
        if name in idx.nested:
            where = ", ".join(idx.nested[name])
            reasons.append(f"モジュール直下でない定義（関数内など）だけがある: {where}")
        if name in idx.other_bindings:
            files = sorted(set(idx.other_bindings[name]))
            reasons.append(f"名前 {name} が代入または import で定義される: {', '.join(files)}")
        if reasons:
            problem = f"クラス {name} の有無を確定できない（{'; '.join(reasons)}）"
            return Located(None, None, problem, False), None, None
        problem = f"クラス {name} が source_roots に定義されていない"
        return Located(None, None, problem, True), None, None
    if len(found) > 1:
        where = ", ".join(f"{s.rel}:{c.lineno}" for s, c in found)
        problem = f"クラス {name} が複数ある（{where}）。どれか確定できない"
        return Located(None, None, problem, False), None, None
    if name in idx.nested:
        where = (
            ", ".join(f"{s.rel}:{c.lineno}" for s, c in found) + ", " + ", ".join(idx.nested[name])
        )
        problem = f"クラス {name} に条件付き・入れ子の定義があり一意に決まらない（{where}）"
        return Located(None, None, problem, False), None, None
    src, cls = found[0]
    mod = module_qualname(src.rel, source_roots)
    qual = f"{mod}.{name}" if mod else name
    return Located(qual, src.rel, None, False), src, cls


def locate_class(name: str, repo_root: Path, source_roots: tuple[str, ...]) -> Located:
    """モジュール直下で一意に定義されたクラスを探す。入れ子・代入・import・構文エラーは確定できない。"""
    return _locate_class(_index(repo_root, source_roots), name, source_roots)[0]


def _module_file(repo_root: Path, source_roots: tuple[str, ...], qual: str) -> str | None:
    root = repo_root.resolve()
    rel = Path(*qual.split("."))
    for sr in source_roots:
        base = root / sr
        for cand in (base / rel.with_suffix(".py"), base / rel / "__init__.py"):
            if cand.is_file() and cand.resolve().is_relative_to(root):
                return str(cand.resolve().relative_to(root))
    return None


def _locate_module(
    idx: _Index,
    name: str,
    repo_root: Path,
    source_roots: tuple[str, ...],
    modules: Mapping[str, str] | None,
) -> tuple[Located, _Source | None]:
    if modules and name in modules:
        qual = modules[name]
        rel = _module_file(repo_root, source_roots, qual)
        if rel is None:
            problem = f"bindings の modules が指すモジュール {qual} が無い"
            return Located(None, None, problem, True), None
        src = next((s for s in idx.sources if s.rel == rel), None)
        if src is None:
            try:
                tree = ast.parse((repo_root / rel).read_text(encoding="utf-8"), filename=rel)
            except (SyntaxError, UnicodeDecodeError, ValueError):
                problem = f"モジュール {qual}（{rel}）が構文エラーで読めない"
                return Located(qual, rel, problem, False), None
            src = _Source(rel, tree)
        return Located(qual, rel, None, False), src

    def matches(rel: str) -> bool:
        p = Path(rel)
        return p.name == f"{name}.py" or (p.name == "__init__.py" and p.parent.name == name)

    cands = [s for s in idx.sources if matches(s.rel)]
    broken = [r for r in idx.parse_errors if matches(r)]
    if not cands and not broken:
        problem = f"モジュール {name} が source_roots に無い"
        return Located(None, None, problem, True), None
    if len(cands) + len(broken) > 1:
        where = ", ".join([s.rel for s in cands] + broken)
        problem = (
            f"モジュール {name} の候補が複数ある（{where}）。"
            "bindings.yaml の modules に完全修飾名を書く"
        )
        return Located(None, None, problem, False), None
    if broken:
        problem = f"モジュール {name}（{broken[0]}）が構文エラーで読めない"
        return Located(None, broken[0], problem, False), None
    src = cands[0]
    return Located(module_qualname(src.rel, source_roots), src.rel, None, False), src


def locate_module(
    name: str,
    repo_root: Path,
    source_roots: tuple[str, ...],
    modules: Mapping[str, str] | None = None,
) -> Located:
    """モジュール名を解決する。bindings の対応があればそれを、無ければ一意なファイルを探す。"""
    idx = _index(repo_root, source_roots)
    return _locate_module(idx, name, repo_root, source_roots, modules)[0]


class SymbolLocator:
    """1 回の検査の中で、図の名前（クラス・モジュール）を何度も解決するための索引の持ち主。

    索引（全ファイルの AST）は最初の解決で 1 回だけ作り、結果も名前ごとに覚える。
    """

    def __init__(
        self,
        repo_root: Path,
        source_roots: tuple[str, ...],
        modules: Mapping[str, str] | None = None,
    ) -> None:
        self.repo_root = repo_root
        self.source_roots = source_roots
        self.modules = dict(modules or {})
        self._idx: _Index | None = None
        self._cache: dict[tuple[bool, str], Located] = {}

    def locate(self, name: str, *, module: bool) -> Located:
        key = (module, name)
        if key not in self._cache:
            if self._idx is None:
                self._idx = _index(self.repo_root, self.source_roots)
            self._cache[key] = (
                _locate_module(self._idx, name, self.repo_root, self.source_roots, self.modules)[0]
                if module
                else _locate_class(self._idx, name, self.source_roots)[0]
            )
        return self._cache[key]


def _summary(kind: str, name: str, ob: StructureObligation) -> str:
    ops = ", ".join(f"{o.name}({', '.join(_spec_params(o))})" for o in ob.operations)
    return f"{kind} {name}" + (f"（{ops}）" if ob.operations else "")


def check_structure(
    ob: StructureObligation,
    repo_root: Path,
    source_roots: tuple[str, ...],
    *,
    behavior_id: str | None = None,
    module: bool = False,
    modules: Mapping[str, str] | None = None,
) -> list[Finding]:
    """構造の義務 1 つを検査し、Finding を 1 つ返す。behavior_id の既定は義務 ID の図 ID 部分。

    `module=True` のとき `ob.class_name` をモジュール名として、モジュール直下の関数を照合する。
    """
    ctx = ObligationContext(
        requirement_id=ob.id,
        behavior_id=behavior_id or ob.id.split("#", 1)[0],
        level=ob.level,
        claim_scope="command_invocation",
        analysis_scope=f"AST（source_roots: {', '.join(source_roots)}）",
    )
    idx = _index(repo_root, source_roots)
    name = ob.class_name
    kind = "モジュール" if module else "クラス"
    src: _Source | None
    cls: ast.ClassDef | None = None
    if module:
        loc, src = _locate_module(idx, name, repo_root, source_roots, modules)
    else:
        loc, src, cls = _locate_class(idx, name, source_roots)
    if src is None or loc.problem is not None:
        problem = loc.problem or f"{kind} {name} を確定できない"
        if not loc.missing:
            return [ctx.unknown(problem)]
        return [
            ctx.finding(
                ObligationResult(
                    "VIOLATED",
                    "missing_effect",
                    problem,
                    expected=f"{kind} {name}",
                    actual="定義なし",
                )
            )
        ]
    if module:
        code_refs = [_ref(src, src.tree, name)]
        checks = [_check_module_operation(idx, src, op, name) for op in ob.operations]
    else:
        assert cls is not None
        code_refs = [_ref(src, cls, name)]
        checks = [_check_operation(idx, src, cls, op, name) for op in ob.operations]
    expected = _summary(kind, name, ob)
    verdict: Verdict = worst([c.verdict for c in checks]) if checks else "VERIFIED_IN_SCOPE"
    if verdict == "VERIFIED_IN_SCOPE":
        return [
            ctx.finding(
                ObligationResult(
                    "VERIFIED_IN_SCOPE",
                    "conformant",
                    f"{kind} {name} と {len(checks)} 個の{'関数' if module else '操作'}が図と一致",
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


__all__ = [
    "SymbolLocator",
    "Located",
    "check_structure",
    "locate_class",
    "locate_module",
    "module_qualname",
]
