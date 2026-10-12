"""ワークスペース全体の仕様の、図ごとの対象・対象外（設計 5.1.1）。

ワークスペース全体の仕様はすべてのリポジトリに当たるが、図がそのリポジトリの話でないこともある。
図の要素（クラス・`<<module>>`・入口の操作・状態の属性）がリポジトリに 1 つも解決できない図は
「対象外」とし、その図の義務を出さない（偽の VIOLATED を出さない）。1 つでも解決できれば従来どおり
照合する（欠けている要素は VIOLATED のまま）。プロジェクトに絞った仕様には使わない。

図ごとの判定はこのモジュールだけで行う。義務の種類を足すときは `diagram_elements` と `narrow`
の両方に足す。要素の解決（コードを読む部分）は呼び出し側が `resolves` で渡す。
"""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Callable, Collection
from typing import Literal

from speclino.domain.uml import CallLabel, CompiledSpec

ElementKind = Literal["class", "module", "entry", "attribute"]
Element = tuple[ElementKind, str]


def obligation_diagram(obligation_id: str) -> str:
    """義務 ID（`<図 ID>#<要素>`、遷移は `<図 ID>`）の図 ID。"""
    return obligation_id.split("#", 1)[0]


def diagram_elements(compiled: CompiledSpec) -> dict[str, list[Element]]:
    """義務を持つ図ごとの要素。

    - クラス図: 操作を持つクラスと関係の両端のクラス（`<<module>>` はモジュール）
    - シーケンス図・アクティビティ図: 入口の操作と、入口・呼び出しの持ち主のクラス
    - 状態遷移図: 状態を持つ属性
    """
    stereotypes = {c.name: c.stereotype for c in compiled.classes}

    def owner(operation: str) -> Element | None:
        name = operation.rpartition(".")[0]
        kind = stereotypes.get(name)
        if kind == "class":
            return ("class", name)
        if kind == "module":
            return ("module", name)
        return None

    def cls(name: str) -> Element:
        return ("module", name) if stereotypes.get(name) == "module" else ("class", name)

    out: dict[str, list[Element]] = defaultdict(list)
    for st in compiled.structure:
        out[obligation_diagram(st.id)].append(cls(st.class_name))
    for rel in compiled.relations:
        out[obligation_diagram(rel.id)].extend([cls(rel.source), cls(rel.target)])
    for tr in compiled.traces:
        elements = out[obligation_diagram(tr.id)]
        elements.append(("entry", tr.entry_operation))
        ops = [tr.entry_operation] + [
            t.label.operation for t in tr.automaton.transitions if isinstance(t.label, CallLabel)
        ]
        elements.extend(e for e in map(owner, ops) if e is not None)
    for sm in compiled.state_machines:
        out[obligation_diagram(sm.id)].append(("attribute", sm.attribute))
    return {d: list(dict.fromkeys(els)) for d, els in out.items()}


def out_of_scope_diagrams(
    compiled: CompiledSpec, resolves: Callable[[ElementKind, str], bool]
) -> list[str]:
    """要素が 1 つも解決できない図の ID（名前の順）。"""
    return sorted(
        d
        for d, elements in diagram_elements(compiled).items()
        if not any(resolves(kind, name) for kind, name in elements)
    )


def narrow(compiled: CompiledSpec, diagrams: Collection[str]) -> CompiledSpec:
    """対象外の図の義務を除いた変換結果（要求の行・クラス・旧形式はそのまま）。"""
    if not diagrams:
        return compiled
    drop = set(diagrams)

    def keep(obligation_id: str) -> bool:
        return obligation_diagram(obligation_id) not in drop

    return compiled.model_copy(
        update={
            "structure": [s for s in compiled.structure if keep(s.id)],
            "relations": [r for r in compiled.relations if keep(r.id)],
            "traces": [t for t in compiled.traces if keep(t.id)],
            "state_machines": [m for m in compiled.state_machines if keep(m.id)],
        }
    )


__all__ = [
    "Element",
    "ElementKind",
    "diagram_elements",
    "narrow",
    "obligation_diagram",
    "out_of_scope_diagrams",
]
