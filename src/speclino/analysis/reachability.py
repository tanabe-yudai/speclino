"""到達関数群のソースハッシュ（S41c 増分解析）。

behavior の入口（`bindings.behaviors[behavior_id].entrypoint`）から静的な直接呼出しを、
`path_analyzer` の深さ上限とは違って**制限なしで**推移的にたどり、到達した関数のソースを
正規化して連結したハッシュを作る。`services.check_pipeline.run_check` はこのハッシュが
base と head で一致し、かつ入力（仕様・domain・bindings・contracts）の内容ハッシュも一致する
behavior だけ、base の Code IR を再利用する。

過大近似（広く到達性を見積もる）は安全側に倒れる: 到達関数を漏れなく含めるほど再利用の機会が
減るだけで、実際には変わった関数を見落として誤って再利用することはない。解決できない呼出し
（`self.xxx` のような属性チェーンなど、実行時にしか決まらないもの）は、その呼出し式の文字列を
そのままハッシュに含める（変われば再解析される側に倒す）。

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

_FIELD_SEP = "\x1f"  # 関数ごとの (qualname, source) の区切り
_ITEM_SEP = "\x1e"  # 項目間の区切り


def reachable_source_hash(
    root: Path,
    behavior_id: str,
    bindings: Bindings,
    source_roots: tuple[str, ...] = (".", "src"),
) -> ReachabilityDigest:
    """`behavior_id` の入口から到達する関数群のソースハッシュを作る。"""
    index = RepoIndex(root, source_roots)
    binding = bindings.behaviors.get(behavior_id)
    if binding is None:
        return _digest({}, {f"<binding 無し: {behavior_id}>"})
    try:
        entry = index.function(binding.entrypoint)
    except SymbolNotFound:
        return _digest({}, {f"<entrypoint を解決できない: {binding.entrypoint}>"})

    visited: dict[str, str] = {}
    unresolved: set[str] = set()
    _walk(entry, index, visited, unresolved)
    return _digest(visited, unresolved)


def _walk(
    fn: FunctionInfo, index: RepoIndex, visited: dict[str, str], unresolved: set[str]
) -> None:
    if fn.qualname in visited:
        return
    # ast.unparse は正規化された表現を返すので、コメントと空白の差では変わらない
    visited[fn.qualname] = ast.unparse(fn.node)
    module = index.module(fn.module)
    assert module is not None
    for node in ast.walk(fn.node):
        if not isinstance(node, ast.Call):
            continue
        callee = _resolve_call(node, module, index)
        if callee is None:
            unresolved.add(ast.unparse(node))
            continue
        _walk(callee, index, visited, unresolved)


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


def _digest(functions: dict[str, str], unresolved_calls: set[str]) -> ReachabilityDigest:
    names = sorted(functions)
    calls = sorted(unresolved_calls)
    parts = [f"{name}{_FIELD_SEP}{functions[name]}" for name in names]
    parts += [f"?{_FIELD_SEP}{call}" for call in calls]
    raw = _ITEM_SEP.join(parts)
    source_hash = content_hash(raw)
    return ReachabilityDigest(source_hash=source_hash, functions=names, unresolved_calls=calls)


__all__ = ["reachable_source_hash"]
