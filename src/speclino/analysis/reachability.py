"""到達関数群のソースハッシュ（S41c 増分解析）。

behavior の入口（`bindings.behaviors[behavior_id].entrypoint`）から静的な直接呼出しを、
`path_analyzer` の深さ上限とは違って**制限なしで**推移的にたどり、到達した関数を含む
**モジュールのソース全体**と、そのモジュールが import する（リポジトリ内で解決できる）
モジュール群を**推移的に**正規化して連結したハッシュを作る。`services.check_pipeline.run_check`
はこのハッシュが base と head で一致し、かつ入力（仕様・domain・bindings・contracts）の
内容ハッシュも一致する behavior だけ、base の Code IR を再利用する。

関数本体だけでなく**モジュール全体**（import・最上位の代入・クラス定義を含む）をハッシュに
含めるのは、解析器（`path_analyzer` / `frontend.python.adapters`）が関数の呼出し先解決や
応答判定で、関数本体の外側にある環境（import 束縛など）を参照するため。例えば
`from fastapi import Response` を `from builtins import print as Response` に変えると、
`Response(status_code=204)` という関数本体のテキストは1文字も変わらないのに、解決される
呼出し先（`fastapi.Response` → `builtins.print`）が変わり、`fastapi_outcome` の応答判定が
変わる（S41c 最初の実装で見つかった穴、1件目）。

さらに、到達した関数を「呼ぶ」わけではなく import して**属性参照**するだけのモジュール
（列挙型・定数・データクラス等）も、解析器は `RepoIndex.enum_values()` などで参照する。
例えば `examples/orders/app/models.py` の `OrderStatus.PENDING` を `WAITING` に改名すると、
呼び出す関数は1つも変わらないのに `order.status != OrderStatus.PENDING` の属性参照が解決
できなくなる（S41c 最初の実装で見つかった穴、2件目）。解析器がモジュールを読む経路は
import 経由しかないため、到達した各モジュールから `ast.Import` / `ast.ImportFrom`
（相対 import・`from x import *`・`__init__.py` 経由の再エクスポートを含む）を**推移的**に
たどり、リポジトリ内で解決できるモジュールを漏れなくハッシュに含める。同じ理由で、現状の
解析器が参照しないモジュール定数（`LIMIT = 1` のような Name）についても、将来解決する
ようになったときに到達ハッシュ側の追随が不要になる（モジュール全体を含んでいるため）。

過大近似（広く到達性を見積もる）は安全側に倒れる: 同じモジュール内の無関係な関数が変わっても、
無関係な import 先モジュールが変わっても、再解析される側に倒れるだけで、実際に意味が変わった
箇所を見落として誤って再利用することはない。解決できない呼出し（`self.xxx` のような属性
チェーンなど、実行時にしか決まらないもの）は、その呼出し式の文字列をハッシュに含める
（変われば再解析される側に倒す）。

`path_analyzer` と同様、対象リポジトリのモジュールを import しない。ast の静的な走査だけを行う。
"""

from __future__ import annotations

import ast
from pathlib import Path

from speclino.domain.bindings import Bindings
from speclino.domain.code_ir import ReachabilityDigest
from speclino.frontend.python.index import (
    FunctionInfo,
    ModuleInfo,
    RepoIndex,
    SymbolNotFound,
    attribute_chain,
)
from speclino.spec.loader import content_hash

_FIELD_SEP = "\x1f"  # (名前, 内容) の区切り
_ITEM_SEP = "\x1e"  # 項目間の区切り


def reachable_source_hash(
    root: Path,
    behavior_id: str,
    bindings: Bindings,
    source_roots: tuple[str, ...] = (".", "src"),
) -> ReachabilityDigest:
    """`behavior_id` の入口から到達するモジュール群のソースハッシュを作る。"""
    index = RepoIndex(root, source_roots)
    binding = bindings.behaviors.get(behavior_id)
    if binding is None:
        return _digest({}, set(), {f"<binding 無し: {behavior_id}>"})
    try:
        entry = index.function(binding.entrypoint)
    except SymbolNotFound:
        return _digest({}, set(), {f"<entrypoint を解決できない: {binding.entrypoint}>"})

    functions: set[str] = set()
    modules: dict[str, str] = {}
    unresolved: set[str] = set()
    _walk(entry, index, functions, modules, unresolved)
    return _digest(modules, functions, unresolved)


def attribute_writes(
    root: Path, attr: str, source_roots: tuple[str, ...] = (".", "src")
) -> list[str]:
    """source_roots の全モジュールの中で、名前 attr の属性に書き得る場所（`path:line`）。

    状態遷移の検査で、状態の変数を対応に持たない behavior がその属性を書き得るかの過大近似に使う
    （どの behavior から到達するかは見ない。確実に広めに見積もる）。拾うもの:
    `<式>.attr = …`（累算・注釈つき・分解代入の中を含む）、`setattr(x, "attr", v)`・
    `object.__setattr__(x, "attr", v)`、`x.__dict__["attr"] = v`。名前が定数でない `setattr` と
    `__dict__` への代入は、どの属性にも書き得るので「（名前が定数でない）」として拾う。
    """
    from speclino.analysis.structure_check import iter_source_files

    resolved = root.resolve()
    out: list[str] = []
    for f in iter_source_files(root, source_roots):
        try:
            tree = ast.parse(f.read_text(encoding="utf-8"))
        except (SyntaxError, UnicodeDecodeError, ValueError):
            out.append(f"{f.relative_to(resolved)}（構文エラーで読めない）")
            continue
        rel = str(f.relative_to(resolved))
        for node in ast.walk(tree):
            line = getattr(node, "lineno", 0)
            if isinstance(node, ast.Call):
                name = _setattr_name(node)
                if name is not None and name in (attr, ""):
                    suffix = "" if name else "（名前が定数でない）"
                    out.append(f"{rel}:{line}{suffix}")
                continue
            targets: list[ast.expr] = []
            if isinstance(node, ast.Assign):
                targets = list(node.targets)
            elif isinstance(node, ast.AnnAssign | ast.AugAssign):
                targets = [node.target]
            for t in targets:
                for x in ast.walk(t):
                    if isinstance(x, ast.Attribute) and x.attr == attr:
                        out.append(f"{rel}:{line}")
                    elif (
                        isinstance(x, ast.Subscript)
                        and isinstance(x.value, ast.Attribute)
                        and x.value.attr == "__dict__"
                    ):
                        key = x.slice
                        if not (isinstance(key, ast.Constant) and isinstance(key.value, str)):
                            out.append(f"{rel}:{line}（名前が定数でない）")
                        elif key.value == attr:
                            out.append(f"{rel}:{line}")
    return sorted(set(out))


def _setattr_name(call: ast.Call) -> str | None:
    """`setattr(x, n, v)` / `object.__setattr__(x, n, v)` の n。

    定数でなければ ""、該当しなければ None。
    """
    func = call.func
    is_setattr = (isinstance(func, ast.Name) and func.id == "setattr") or (
        isinstance(func, ast.Attribute) and func.attr == "__setattr__"
    )
    if not is_setattr or len(call.args) < 2:
        return None
    n = call.args[1]
    return n.value if isinstance(n, ast.Constant) and isinstance(n.value, str) else ""


def _walk(
    fn: FunctionInfo,
    index: RepoIndex,
    functions: set[str],
    modules: dict[str, str],
    unresolved: set[str],
) -> None:
    if fn.qualname in functions:
        return
    functions.add(fn.qualname)
    module = _touch_module(fn.module, index, modules)
    assert module is not None  # fn がこのモジュールから解決された以上、必ず存在する
    for node in ast.walk(fn.node):
        if not isinstance(node, ast.Call):
            continue
        callee = _resolve_call(node, module, index)
        if callee is None:
            unresolved.add(ast.unparse(node))
            continue
        _walk(callee, index, functions, modules, unresolved)


def _touch_module(module_name: str, index: RepoIndex, modules: dict[str, str]) -> ModuleInfo | None:
    """モジュールをハッシュ対象に加え、その import 先も推移的に加える。

    解析器がモジュールを読む経路は import 経由しかないため、これで「解析器が参照し得る
    関数外の入力」（列挙型・定数・データクラス等）を漏れなく覆う。循環 import は `modules`
    に先に登録してから再帰するため無限ループにならない。
    """
    if module_name in modules:
        return index.module(module_name)
    module = index.module(module_name)
    if module is None:
        return None
    # ast.unparse は正規化された表現を返すので、コメントと空白の差では変わらない
    modules[module_name] = ast.unparse(module.tree)
    for target in _import_targets(module):
        _touch_module(target, index, modules)
    return module


def _import_targets(module: ModuleInfo) -> set[str]:
    """このモジュールが import する、モジュール名の候補集合（関数内の import も含め全体を走査）。

    候補のうちリポジトリ内で実在しないもの（標準ライブラリ・外部パッケージ）は
    `_touch_module` 側の `index.module(...) is None` チェックで自然に無視される。
    """
    targets: set[str] = set()
    for node in ast.walk(module.tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                targets.add(alias.name)
        elif isinstance(node, ast.ImportFrom):
            base = _from_import_base(module, node)
            if base is None:
                continue
            targets.add(base)
            # `from pkg import sub` は sub がサブモジュールのことも、pkg（__init__.py）で
            # 定義された名前のこともある。両方候補にし、実在しない方は素通りさせる
            for alias in node.names:
                if alias.name != "*":
                    targets.add(f"{base}.{alias.name}")
    return targets


def _from_import_base(module: ModuleInfo, node: ast.ImportFrom) -> str | None:
    """`from X import Y`（相対 import を含む）の X を絶対ドット名にする。

    CPython の `importlib._bootstrap._resolve_name`（`__package__` の計算を含む）と同じ規則。
    """
    if node.level == 0:
        return node.module
    is_package = module.path.name == "__init__.py"
    package = module.name if is_package else module.name.rpartition(".")[0]
    bits = package.rsplit(".", node.level - 1)
    base = bits[0] if bits and bits[0] else ""
    if node.module:
        base = f"{base}.{node.module}" if base else node.module
    return base or None


def _resolve_call(call: ast.Call, module: ModuleInfo, index: RepoIndex) -> FunctionInfo | None:
    """呼出し先がリポジトリ内の関数として静的に解決できればそれを返す。

    `self.xxx` のように根がモジュール内の import／定義でない属性チェーンは、
    `module.resolve_name` がそのまま返すため `index.function` で見つからず None になる
    （`symbolic_state.ExprBuilder.callee_qualname` と同じ判定基準）。
    """
    chain = attribute_chain(call.func)
    if chain is None:
        return None
    head = module.resolve_name(chain[0])
    candidate = ".".join([head, *chain[1:]])
    try:
        return index.function(candidate)
    except SymbolNotFound:
        return None


def _digest(
    modules: dict[str, str], functions: set[str], unresolved_calls: set[str]
) -> ReachabilityDigest:
    module_names = sorted(modules)
    func_names = sorted(functions)
    calls = sorted(unresolved_calls)
    parts = [f"{name}{_FIELD_SEP}{modules[name]}" for name in module_names]
    parts += [f"?{_FIELD_SEP}{call}" for call in calls]
    raw = _ITEM_SEP.join(parts)
    source_hash = content_hash(raw)
    return ReachabilityDigest(
        source_hash=source_hash, functions=func_names, modules=module_names, unresolved_calls=calls
    )


__all__ = ["attribute_writes", "reachable_source_hash"]
