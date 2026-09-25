"""到達関数群のソースハッシュ（S41c 増分解析）。

behavior の入口（`bindings.behaviors[behavior_id].entrypoint`）から静的な直接呼出しを、
`path_analyzer` の深さ上限とは違って**制限なしで**推移的にたどり、到達した関数を含む
**モジュールのソース全体**を正規化して連結したハッシュを作る。`services.check_pipeline.run_check`
はこのハッシュが base と head で一致し、かつ入力（仕様・domain・bindings・contracts）の
内容ハッシュも一致する behavior だけ、base の Code IR を再利用する。

関数本体だけでなく**モジュール全体**（import・最上位の代入・クラス定義を含む）をハッシュに
含めるのは、解析器（`path_analyzer` / `frontend.python.adapters`）が関数の呼出し先解決や
応答判定で、関数本体の外側にある環境（import 束縛など）を参照するため。例えば
`from fastapi import Response` を `from builtins import print as Response` に変えると、
`Response(status_code=204)` という関数本体のテキストは1文字も変わらないのに、解決される
呼出し先（`fastapi.Response` → `builtins.print`）が変わり、`fastapi_outcome` の応答判定が
変わる。関数本体の文字列だけをハッシュにしていると、この変化を見落として誤って再利用して
しまう（S41c 最初の実装で見つかった穴）。同様の理由で、モジュール最上位の定数（現状の解析器は
未解決にするだけで参照しないが、将来解決するようになったときにも、モジュール全体を含めていれば
到達ハッシュ側の追随が要らない）もここに含まれる。

過大近似（広く到達性を見積もる）は安全側に倒れる: 同じモジュール内の無関係な関数が変わっても
再解析される側に倒れるだけで、実際に意味が変わった箇所を見落として誤って再利用することはない。
解決できない呼出し（`self.xxx` のような属性チェーンなど、実行時にしか決まらないもの）は、
その呼出し式の文字列をハッシュに含める（変われば再解析される側に倒す）。

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
    module = index.module(fn.module)
    assert module is not None
    if fn.module not in modules:
        # モジュール全体を正規化して含める（理由はモジュール docstring）。ast.unparse は
        # 正規化された表現を返すので、コメントと空白の差では変わらない
        modules[fn.module] = ast.unparse(module.tree)
    for node in ast.walk(fn.node):
        if not isinstance(node, ast.Call):
            continue
        callee = _resolve_call(node, module, index)
        if callee is None:
            unresolved.add(ast.unparse(node))
            continue
        _walk(callee, index, functions, modules, unresolved)


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
    return ReachabilityDigest(source_hash=source_hash, functions=func_names, unresolved_calls=calls)


__all__ = ["reachable_source_hash"]
