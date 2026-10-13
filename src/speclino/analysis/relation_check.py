"""関係の義務（クラス図の関係線）の検査（grammar.md 4.1 節）。

AST だけを読み、対象を import しない。両端の名前の解決は構造の検査（`structure_check`）と同じ
規則（クラスは source_roots で一意なモジュール直下の定義、`<<module>>` は bindings の modules か
一意なファイル）。コードの中の名前は、そのモジュールの import と直下の定義で完全修飾名に解決し、
再エクスポート（`pkg/__init__.py` の `from .mod import X`）はたどる。

- 継承 `A <|-- B`: B の class 文の基底（`Base[T]` は `Base`）に A がある
- コンポジション・集約・関連 `A *-- B` など: A が B 型の属性を持つ。根拠はクラス本体の注釈
  （dataclass・pydantic のフィールドを含む）、メソッドの中の `self.x: T`・`self.x = B(...)`・
  `self.x = <注釈つき引数>`・`self.x = <戻り値の注釈つきの関数の呼出し>`、基底（一意なもの）の
  同じ根拠。A が `<<module>>` ならモジュール直下の変数の注釈・代入。注釈は `B | None`・`list[B]`・
  `Sequence[B]` などの中に B があれば所有とみなし（多重度は照合しない）、`Callable[...]` の
  中は見ない
- 依存 `A ..> B`: A（クラスならその定義のあるモジュール）が B（またはその中の名前）を import
  するか、名前（注釈・呼出し・属性）で参照する

見つかれば VERIFIED_IN_SCOPE。見つからないとき、「無い」と確定できない理由（`__getattr__`・
`setattr`・`exec`・`import *`・型の分からない属性の値・解決できない同名の参照・一意でない基底）が
あれば UNKNOWN、無ければ VIOLATED。注釈の無いリテラル・コンテナ（`[]`・`{}`）とリポジトリ外の
関数の呼出しは B とみなさない（注釈は mypy strict で実行時の値と整合する前提）。
"""

from __future__ import annotations

import ast
from collections.abc import Iterator, Mapping
from dataclasses import dataclass, field
from pathlib import Path

from speclino.analysis.structure_check import (
    _COMPOUND,
    _FuncDef,
    _Index,
    _index,
    _locate_class,
    _locate_module,
    _Source,
    module_qualname,
)
from speclino.conformance.obligation import ObligationContext, ObligationResult
from speclino.domain.common import SourceRef
from speclino.domain.finding import Finding
from speclino.domain.uml import RelationKind, RelationObligation

_DYNAMIC_ATTRS = frozenset({"__getattr__", "__getattribute__", "__setattr__"})
_MAX_REEXPORT_HOPS = 8
_KIND_TEXT: dict[RelationKind, str] = {
    "inheritance": "継承",
    "composition": "コンポジション",
    "aggregation": "集約",
    "association": "関連",
    "dependency": "依存",
}


@dataclass
class _Names:
    """1 つのモジュールの名前 → 完全修飾名（import と直下の定義）。"""

    qual: str
    bound: dict[str, str] = field(default_factory=dict)
    imports: dict[str, str] = field(default_factory=dict)
    ambiguous: set[str] = field(default_factory=set)
    assigned: set[str] = field(default_factory=set)  # import 以外で束縛される名前
    star: bool = False

    def builtin(self, name: str) -> bool:
        """モジュールのどこでも束縛されない名前（組込みを指す）。"""
        return not self.star and name not in self.bound and name not in self.assigned


def _package(qual: str, is_pkg: bool) -> str:
    return qual if is_pkg else qual.rpartition(".")[0]


def _names_of(src: _Source, qual: str) -> _Names:
    out = _Names(qual)
    is_pkg = Path(src.rel).name == "__init__.py"

    def bind(local: str, target: str, *, imported: bool) -> None:
        prev = out.bound.get(local)
        if prev is not None and prev != target:
            out.ambiguous.add(local)
        out.bound[local] = target
        if imported:
            out.imports[local] = target

    for s in src.tree.body:
        if isinstance(s, ast.ClassDef | ast.FunctionDef | ast.AsyncFunctionDef):
            bind(s.name, f"{qual}.{s.name}", imported=False)
    for node in ast.walk(src.tree):
        if isinstance(node, ast.Import):
            for a in node.names:
                if a.asname:
                    bind(a.asname, a.name, imported=True)
                else:
                    head = a.name.split(".")[0]
                    bind(head, head, imported=True)
        elif isinstance(node, ast.ImportFrom):
            base = node.module or ""
            if node.level:
                pkg = _package(qual, is_pkg)
                for _ in range(node.level - 1):
                    pkg = pkg.rpartition(".")[0]
                base = f"{pkg}.{base}" if base else pkg
            for a in node.names:
                if a.name == "*":
                    out.star = True
                    continue
                bind(a.asname or a.name, f"{base}.{a.name}", imported=True)
    # import 以外の束縛（定義・代入・del・global・引数など）と合わせて 2 つ以上あれば、
    # 実行時にどれを指すか決まらないので解決しない（同じ先の import の重複は 1 つと数える）
    counts: dict[str, int] = {}
    for node in ast.walk(src.tree):
        for name in _bound_names(node):
            counts[name] = counts.get(name, 0) + 1
    out.ambiguous |= {n for n, c in counts.items() if c + (n in out.imports) > 1}
    out.assigned = set(counts)
    return out


def _bound_names(node: ast.AST) -> Iterator[str]:
    """node 自身が束縛（または削除・global 宣言）する名前。import は呼び出し側が先で数える。"""
    if isinstance(node, ast.Name) and not isinstance(node.ctx, ast.Load):
        yield node.id
    elif isinstance(node, ast.ClassDef | ast.FunctionDef | ast.AsyncFunctionDef):
        yield node.name
    elif isinstance(node, ast.Global | ast.Nonlocal):
        yield from node.names
    elif isinstance(node, ast.ExceptHandler | ast.MatchAs | ast.MatchStar):
        if node.name:
            yield node.name
    elif isinstance(node, ast.MatchMapping) and node.rest:
        yield node.rest
    elif isinstance(node, ast.arg):
        yield node.arg


def _chain(node: ast.expr) -> list[str] | None:
    parts: list[str] = []
    while isinstance(node, ast.Attribute):
        parts.append(node.attr)
        node = node.value
    if isinstance(node, ast.Name):
        parts.append(node.id)
        return list(reversed(parts))
    return None


@dataclass(frozen=True)
class _Ref:
    """コードの中の名前の参照。qual が None なら解決できない（組込み・局所変数・曖昧）。"""

    qual: str | None
    text: str
    line: int


class _Resolver:
    """モジュールをまたいで名前を完全修飾名にし、再エクスポートをたどる。"""

    def __init__(self, idx: _Index, source_roots: tuple[str, ...]) -> None:
        self.source_roots = source_roots
        self.by_qual: dict[str, _Source] = {}
        for s in idx.sources:
            q = module_qualname(s.rel, source_roots)
            if q is not None:
                self.by_qual.setdefault(q, s)
        self._names: dict[str, _Names] = {}

    def names(self, qual: str) -> _Names | None:
        if qual not in self._names:
            src = self.by_qual.get(qual)
            if src is None:
                return None
            self._names[qual] = _names_of(src, qual)
        return self._names[qual]

    def names_of_source(self, src: _Source) -> _Names | None:
        q = module_qualname(src.rel, self.source_roots)
        return self.names(q) if q is not None else None

    def top_level(self, qual: str) -> ast.stmt | None:
        """完全修飾名がリポジトリのモジュール直下の class / def ならその文（後の定義が勝つ）。"""
        mod, _, name = qual.rpartition(".")
        src = self.by_qual.get(mod)
        if src is None:
            return None
        found: ast.stmt | None = None
        for s in src.tree.body:
            if (
                isinstance(s, ast.ClassDef | ast.FunctionDef | ast.AsyncFunctionDef)
                and s.name == name
            ):
                found = s
        return found

    def rebound(self, qual: str) -> bool:
        """完全修飾名の最後の名前が、そのモジュールで再束縛され得る（定義が 1 つに決まらない）。"""
        mod, _, name = qual.rpartition(".")
        names = self.names(mod) if mod in self.by_qual else None
        return names is not None and name in names.ambiguous

    def in_repo(self, qual: str) -> bool:
        parts = qual.split(".")
        return any(".".join(parts[:i]) in self.by_qual for i in range(len(parts), 0, -1))

    def canonical(self, qual: str) -> str:
        """`pkg.X` が `pkg/__init__.py` の `from .mod import X` なら `pkg.mod.X` にたどる。"""
        for _ in range(_MAX_REEXPORT_HOPS):
            parts = qual.split(".")
            for i in range(len(parts) - 1, 0, -1):
                mod = ".".join(parts[:i])
                if mod in self.by_qual:
                    names = self.names(mod)
                    if names is None or parts[i] in names.ambiguous:
                        return qual
                    nxt = names.imports.get(parts[i])
                    if nxt is None:
                        return qual
                    qual = ".".join([nxt, *parts[i + 1 :]])
                    break
            else:
                return qual
        return qual

    def refs(self, node: ast.AST, names: _Names, *, annotation: bool) -> Iterator[_Ref]:
        """式・文の中の名前の参照（属性の連なりは最長のものだけ）。注釈の中の文字列は式として読む。"""
        if isinstance(node, ast.Constant):
            if annotation and isinstance(node.value, str):
                try:
                    parsed = ast.parse(node.value, mode="eval").body
                except SyntaxError:
                    return
                yield from self.refs(parsed, names, annotation=True)
            return
        if isinstance(node, ast.Name | ast.Attribute):
            chain = _chain(node)
            if chain is not None:
                if isinstance(node, ast.Name) and not isinstance(node.ctx, ast.Load):
                    return
                head = chain[0]
                base = names.bound.get(head) if head not in names.ambiguous else None
                qual = self.canonical(".".join([base, *chain[1:]])) if base else None
                if qual is not None and self.rebound(qual):
                    qual = None
                yield _Ref(qual, ".".join(chain), getattr(node, "lineno", 0))
                return
        if isinstance(node, ast.Subscript) and annotation:
            value = _chain(node.value)
            if value is not None and value[-1] == "Callable":
                return  # 呼出しの型の引数・戻り値は所有ではない
        if isinstance(node, ast.arg):
            if node.annotation is not None:
                yield from self.refs(node.annotation, names, annotation=True)
            return
        for name, child in ast.iter_fields(node):
            items = child if isinstance(child, list) else [child]
            for it in items:
                if not isinstance(it, ast.AST):
                    continue
                is_ann = annotation or (
                    name in ("annotation", "returns") and isinstance(node, ast.AnnAssign | _FuncDef)
                )
                yield from self.refs(it, names, annotation=is_ann)


def _hits(qual: str | None, target: str) -> bool:
    return qual is not None and (qual == target or qual.startswith(target + "."))


@dataclass
class _Search:
    found: str | None = None
    found_line: int | None = None
    reasons: list[str] = field(default_factory=list)


class RelationChecker:
    """1 回の検査の中で関係の義務を照合する。索引（全ファイルの AST）は最初に 1 回だけ作る。"""

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
        self._resolver: _Resolver | None = None

    def _ready(self) -> tuple[_Index, _Resolver]:
        if self._idx is None or self._resolver is None:
            self._idx = _index(self.repo_root, self.source_roots)
            self._resolver = _Resolver(self._idx, self.source_roots)
        return self._idx, self._resolver

    def check(self, rel: RelationObligation, stereotypes: Mapping[str, str]) -> list[Finding]:
        idx, res = self._ready()
        ctx = ObligationContext(
            requirement_id=rel.id,
            behavior_id=rel.id.split("#", 1)[0],
            level=rel.level,
            claim_scope="command_invocation",
            analysis_scope=f"AST（source_roots: {', '.join(self.source_roots)}）",
        )
        kind_text = _KIND_TEXT[rel.kind]
        expected = f"{rel.source} → {rel.target}（{kind_text}）"
        ends: dict[str, tuple[_Source, ast.ClassDef | None, str]] = {}
        for name in (rel.source, rel.target):
            module = stereotypes.get(name) == "module"
            if module:
                loc, src = _locate_module(
                    idx, name, self.repo_root, self.source_roots, self.modules
                )
                cls: ast.ClassDef | None = None
            else:
                loc, src, cls = _locate_class(idx, name, self.source_roots)
            if src is None or loc.problem is not None or loc.qualname is None:
                problem = loc.problem or f"{name} の完全修飾名を確定できない"
                if loc.missing:
                    return [
                        ctx.finding(
                            ObligationResult(
                                "VIOLATED",
                                "missing_effect",
                                f"{kind_text}の端: {problem}",
                                expected=expected,
                                actual="定義なし",
                            )
                        )
                    ]
                return [ctx.unknown(f"{kind_text}の端を確定できない（{problem}）")]
            ends[name] = (src, cls, res.canonical(loc.qualname))
        src_file, src_cls, src_qual = ends[rel.source]
        _, _, tgt_qual = ends[rel.target]
        mod_qual = module_qualname(src_file.rel, self.source_roots)
        names = res.names(mod_qual) if mod_qual is not None else None
        if names is None:
            return [ctx.unknown(f"{rel.source} のモジュール名を確定できない（{src_file.rel}）")]
        target_name = rel.target
        if rel.kind == "inheritance":
            assert src_cls is not None
            search = _inheritance(res, names, src_cls, tgt_qual, target_name)
        elif rel.kind == "dependency":
            search = _dependency(idx, res, names, src_file, tgt_qual, target_name)
        elif src_cls is None:
            search = _module_owns(idx, res, names, src_file, tgt_qual, target_name)
        else:
            search = _class_owns(idx, res, names, src_file, src_cls, tgt_qual, target_name, set())
        ref = SourceRef(
            snapshot_id="repo",
            symbol=src_qual,
            path=src_file.rel,
            line_start=search.found_line or (src_cls.lineno if src_cls is not None else None),
        )
        if search.found is not None:
            return [
                ctx.finding(
                    ObligationResult(
                        "VERIFIED_IN_SCOPE",
                        "conformant",
                        f"{rel.source} → {rel.target} の{kind_text}を確認: {search.found}",
                        expected=expected,
                    ),
                    code_refs=[ref],
                )
            ]
        if search.reasons:
            return [
                ctx.finding(
                    ObligationResult(
                        "UNKNOWN",
                        "unsupported_analysis",
                        f"{rel.source} → {rel.target} の{kind_text}の根拠が見つからないが、無いとも"
                        f"確定できない（{'; '.join(dict.fromkeys(search.reasons))}）",
                        expected=expected,
                    ),
                    code_refs=[ref],
                )
            ]
        return [
            ctx.finding(
                ObligationResult(
                    "VIOLATED",
                    "missing_effect",
                    _violation(rel, src_qual, src_file.rel, tgt_qual, module=src_cls is None),
                    expected=expected,
                    actual="根拠なし",
                ),
                code_refs=[ref],
            )
        ]


def _violation(
    rel: RelationObligation, src_qual: str, path: str, tgt_qual: str, *, module: bool
) -> str:
    if rel.kind == "inheritance":
        return f"{src_qual}（{path}）の基底に {tgt_qual} が無い"
    if rel.kind == "dependency":
        return (
            f"{rel.source} の定義のあるモジュール（{path}）に、{tgt_qual} の import も名前の参照"
            "（注釈・呼出し・属性）も無い"
        )
    if module:
        return (
            f"モジュール {src_qual}（{path}）に {tgt_qual} 型の変数が無い"
            "（直下の変数の注釈と、構築・戻り値の注釈つきの呼出しの代入を確認）"
        )
    return (
        f"{src_qual}（{path}）に {tgt_qual} 型の属性が無い（クラス本体・メソッドの注釈、"
        "self.x への構築・注釈つき引数・戻り値の注釈つきの呼出しの代入を確認）"
    )


def _unresolved_same_name(refs: list[_Ref], target_name: str) -> list[str]:
    return [
        f"解決できない名前 {r.text}（{r.line} 行）"
        for r in refs
        if r.qual is None and r.text.split(".")[-1] == target_name
    ]


def _inheritance(
    res: _Resolver, names: _Names, cls: ast.ClassDef, target: str, target_name: str
) -> _Search:
    out = _Search()
    for b in cls.bases:
        expr = b.value if isinstance(b, ast.Subscript) else b
        if _chain(expr) is None:
            out.reasons.append(f"式の基底 {ast.unparse(b)}")
            continue
        refs = list(res.refs(expr, names, annotation=False))
        if any(r.qual == target for r in refs):
            out.found = f"class {cls.name}({', '.join(ast.unparse(x) for x in cls.bases)})"
            out.found_line = cls.lineno
            return out
        out.reasons += _unresolved_same_name(refs, target_name)
    if names.star:
        out.reasons.append("import *")
    return out


def _value_type(
    res: _Resolver, names: _Names, value: ast.expr | None, params: dict[str, ast.expr | None]
) -> tuple[list[_Ref] | None, str | None]:
    """代入の右辺の型の根拠（注釈・構築・戻り値の注釈）の参照。型の分からない値は (None, 理由)。"""
    if value is None:
        return [], None
    if isinstance(value, ast.Name) and value.id in params:
        ann = params[value.id]
        if ann is None:
            return None, f"注釈の無い引数 {value.id}"
        return list(res.refs(ann, names, annotation=True)), None
    if isinstance(value, ast.Await):
        value = value.value
    if isinstance(value, ast.Call):
        refs = list(res.refs(value.func, names, annotation=False))
        if len(refs) != 1:
            return None, f"呼出し {ast.unparse(value.func)}"
        q = refs[0].qual
        if q is None:
            return None, f"解決できない呼出し {refs[0].text}"
        if not res.in_repo(q):
            return [], None  # リポジトリ外の関数・クラスは図のクラスを返さない（注釈の前提）
        node = res.top_level(q)
        if isinstance(node, ast.ClassDef):
            return [refs[0]], None  # 構築
        if isinstance(node, _FuncDef):
            mod = res.names(q.rpartition(".")[0])
            if node.returns is None or mod is None:
                return None, f"戻り値の注釈の無い {q}"
            return list(res.refs(node.returns, mod, annotation=True)), None
        return None, f"型の分からない呼出し {q}"
    if isinstance(
        value,
        ast.Constant
        | ast.List
        | ast.Dict
        | ast.Set
        | ast.Tuple
        | ast.ListComp
        | ast.DictComp
        | ast.SetComp
        | ast.JoinedStr,
    ):
        return [], None
    return None, f"型の分からない値 {ast.unparse(value)[:40]}"


# 第 1 引数がインスタンスのままのデコレーター
_INSTANCE_DECORATORS = frozenset(
    {
        "builtins.property",
        "functools.cached_property",
        "abc.abstractmethod",
        "typing.override",
        "typing_extensions.override",
    }
)
_ACCESSORS = frozenset({"setter", "getter", "deleter"})


def _decorator_qual(d: ast.expr, res: _Resolver, names: _Names) -> str | None:
    chain = _chain(d)
    if chain is None:
        return None
    if len(chain) == 1 and names.builtin(chain[0]):
        return f"builtins.{chain[0]}"
    refs = list(res.refs(d, names, annotation=False))
    return refs[0].qual if len(refs) == 1 else None


def _method_self(
    fn: _FuncDef, cls: ast.ClassDef, res: _Resolver, names: _Names
) -> tuple[str | None, str | None]:
    """インスタンスを受ける第 1 引数の名前と、所有の根拠にできない理由。

    staticmethod / classmethod（完全修飾名で解決）は根拠にしない（理由なし）。解決できない
    デコレーターが付いたメソッドは、第 1 引数が何か分からないので根拠にせず理由を返す。
    """
    methods = {x.name for x in cls.body if isinstance(x, _FuncDef)}
    for d in fn.decorator_list:
        chain = _chain(d)
        if chain is not None and len(chain) == 2 and chain[0] in methods and chain[1] in _ACCESSORS:
            continue  # 同じクラスの property の setter など
        q = _decorator_qual(d, res, names)
        if q in ("builtins.staticmethod", "builtins.classmethod"):
            return None, None
        if q not in _INSTANCE_DECORATORS:
            return None, f"{cls.name}.{fn.name} のデコレーター {ast.unparse(d)} を解決できない"
    a = fn.args
    ps = [*a.posonlyargs, *a.args]
    return (ps[0].arg, None) if ps else (None, None)


def _params(fn: _FuncDef) -> dict[str, ast.expr | None]:
    a = fn.args
    ps = [*a.posonlyargs, *a.args, *a.kwonlyargs]
    return {p.arg: p.annotation for p in ps[1:]}


_EXITS = (ast.Return, ast.Raise, ast.Continue, ast.Break)


def _live(body: list[ast.stmt]) -> Iterator[ast.stmt]:
    """静的に到達し得る文（入れ子の文を含む）。定数が偽の if / while の本体、定数が真の if の
    else、`while 定数の真` の else、同じブロックの return・raise・continue・break の後は除く。
    定数以外の条件の枝は到達し得るものとして残す。"""
    for st in body:
        yield st
        if isinstance(st, ast.If | ast.While) and isinstance(st.test, ast.Constant):
            truthy = bool(st.test.value)
            yield from _live(st.body if truthy else st.orelse)
        else:
            for _, value in ast.iter_fields(st):
                if not isinstance(value, list):
                    continue
                stmts = [v for v in value if isinstance(v, ast.stmt)]
                yield from _live(stmts)
                for v in value:
                    if isinstance(v, ast.ExceptHandler | ast.match_case):
                        yield from _live(v.body)
        if isinstance(st, _EXITS):
            return


def _class_owns(
    idx: _Index,
    res: _Resolver,
    names: _Names,
    src: _Source,
    cls: ast.ClassDef,
    target: str,
    target_name: str,
    seen: set[int],
) -> _Search:
    seen.add(id(cls))
    out = _Search()
    for s in cls.body:
        if isinstance(s, ast.AnnAssign) and isinstance(s.target, ast.Name):
            refs = list(res.refs(s.annotation, names, annotation=True))
            if any(r.qual == target for r in refs):
                out.found = f"{cls.name}.{s.target.id}: {ast.unparse(s.annotation)}"
                out.found_line = s.lineno
                return out
            out.reasons += _unresolved_same_name(refs, target_name)
        elif isinstance(s, _FuncDef) and s.name in _DYNAMIC_ATTRS:
            out.reasons.append(f"{cls.name}.{s.name}")
    for s in cls.body:
        if not isinstance(s, _FuncDef):
            continue
        for call in ast.walk(s):
            if (
                isinstance(call, ast.Call)
                and isinstance(call.func, ast.Name)
                and call.func.id == "setattr"
            ):
                out.reasons.append(f"setattr（{src.rel}:{call.lineno}）")
        me, why_not = _method_self(s, cls, res, names)
        if why_not is not None:
            out.reasons.append(why_not)
        if me is None:
            continue
        params = _params(s)
        for node in _live(s.body):
            target_attr: ast.Attribute | None = None
            value: ast.expr | None = None
            ann: ast.expr | None = None
            if isinstance(node, ast.AnnAssign):
                if isinstance(node.target, ast.Attribute):
                    target_attr, ann, value = node.target, node.annotation, node.value
            elif isinstance(node, ast.Assign) and len(node.targets) == 1:
                t = node.targets[0]
                if isinstance(t, ast.Attribute):
                    target_attr, value = t, node.value
            if not (
                target_attr is not None
                and isinstance(target_attr.value, ast.Name)
                and target_attr.value.id == me
            ):
                continue
            where = f"{cls.name}.{s.name} の self.{target_attr.attr}"
            if ann is not None:
                refs = list(res.refs(ann, names, annotation=True))
                if any(r.qual == target for r in refs):
                    out.found = f"{where}: {ast.unparse(ann)}"
                    out.found_line = target_attr.lineno
                    return out
                out.reasons += _unresolved_same_name(refs, target_name)
                continue
            vrefs, why = _value_type(res, names, value, params)
            if vrefs is None:
                out.reasons.append(f"{where} = {why}")
                continue
            if any(r.qual == target for r in vrefs):
                out.found = f"{where} = {ast.unparse(value) if value is not None else ''}"[:200]
                out.found_line = target_attr.lineno
                return out
            out.reasons += _unresolved_same_name(vrefs, target_name)
    if src.rel in idx.exec_files:
        out.reasons.append(f"exec（{src.rel}）")
    if cls.name in idx.setattr_targets:
        out.reasons.append(f"setattr({cls.name}, ...)")
    for b in cls.bases:
        expr = b.value if isinstance(b, ast.Subscript) else b
        if isinstance(expr, ast.Name) and expr.id == "object" and names.builtin("object"):
            continue
        refs = list(res.refs(expr, names, annotation=False)) if _chain(expr) else []
        q = refs[0].qual if len(refs) == 1 else None
        if q is not None and not res.in_repo(q):
            continue  # リポジトリ外の基底（BaseModel・Enum など）は図のクラスの属性を持たない
        base = res.top_level(q) if q is not None else None
        base_mod = q.rpartition(".")[0] if q is not None else ""
        base_names = res.names(base_mod) if base_mod in res.by_qual else None
        if not isinstance(base, ast.ClassDef) or base_names is None:
            out.reasons.append(f"基底 {ast.unparse(b)} がソース内で一意に定まらない")
            continue
        base_src = res.by_qual[base_mod]
        if id(base) in seen:
            continue
        sub = _class_owns(idx, res, base_names, base_src, base, target, target_name, seen)
        if sub.found is not None:
            return sub
        out.reasons += sub.reasons
    return out


def _module_owns(
    idx: _Index, res: _Resolver, names: _Names, src: _Source, target: str, target_name: str
) -> _Search:
    out = _Search()
    for s in src.tree.body:
        if isinstance(s, ast.AnnAssign) and isinstance(s.target, ast.Name):
            refs = list(res.refs(s.annotation, names, annotation=True))
            if any(r.qual == target for r in refs):
                out.found = f"{s.target.id}: {ast.unparse(s.annotation)}"
                out.found_line = s.lineno
                return out
            out.reasons += _unresolved_same_name(refs, target_name)
        elif isinstance(s, ast.Assign) and all(isinstance(t, ast.Name) for t in s.targets):
            vrefs, why = _value_type(res, names, s.value, {})
            label = ", ".join(t.id for t in s.targets if isinstance(t, ast.Name))
            if vrefs is None:
                out.reasons.append(f"{label} = {why}")
                continue
            if any(r.qual == target for r in vrefs):
                out.found = f"{label} = {ast.unparse(s.value)}"[:200]
                out.found_line = s.lineno
                return out
            out.reasons += _unresolved_same_name(vrefs, target_name)
        elif isinstance(s, _COMPOUND):
            for node in ast.walk(s):
                if isinstance(node, ast.Assign | ast.AnnAssign) and any(
                    isinstance(t, ast.Name)
                    for t in (node.targets if isinstance(node, ast.Assign) else [node.target])
                ):
                    out.reasons.append(f"複合文の中の代入（{src.rel}:{node.lineno}）")
                    break
        elif isinstance(s, _FuncDef) and s.name == "__getattr__":
            out.reasons.append("モジュールの __getattr__")
    if src.rel in idx.exec_files:
        out.reasons.append(f"exec（{src.rel}）")
    if src.rel in idx.globals_files:
        out.reasons.append(f"globals()（{src.rel}）")
    return out


def _dependency(
    idx: _Index, res: _Resolver, names: _Names, src: _Source, target: str, target_name: str
) -> _Search:
    out = _Search()
    is_pkg = Path(src.rel).name == "__init__.py"
    for node in ast.walk(src.tree):
        quals: list[str] = []
        if isinstance(node, ast.Import):
            quals = [a.name for a in node.names]
        elif isinstance(node, ast.ImportFrom):
            base = node.module or ""
            if node.level:
                pkg = _package(names.qual, is_pkg)
                for _ in range(node.level - 1):
                    pkg = pkg.rpartition(".")[0]
                base = f"{pkg}.{base}" if base else pkg
            quals = [base] + [f"{base}.{a.name}" for a in node.names if a.name != "*"]
        for q in quals:
            if _hits(res.canonical(q), target) or _hits(q, target):
                line = getattr(node, "lineno", None)
                out.found = f"import（{src.rel}:{line}）{ast.unparse(node)}"[:200]
                out.found_line = line
                return out
    refs = list(res.refs(src.tree, names, annotation=False))
    for r in refs:
        if _hits(r.qual, target):
            out.found = f"{r.text} の参照（{src.rel}:{r.line}）"
            out.found_line = r.line
            return out
    out.reasons += _unresolved_same_name(refs, target_name)
    if names.star:
        out.reasons.append(f"import *（{src.rel}）")
    if src.rel in idx.exec_files:
        out.reasons.append(f"exec（{src.rel}）")
    for node in ast.walk(src.tree):
        if isinstance(node, ast.Call):
            c = _chain(node.func)
            if c is not None and c[-1] in ("import_module", "__import__"):
                out.reasons.append(f"動的な import（{src.rel}:{node.lineno}）")
    return out


__all__ = ["RelationChecker"]
