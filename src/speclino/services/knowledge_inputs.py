"""bundle → 検査器への入力（S65、design/2026-10-05-team-knowledge-registry.md 5 章・8 章）。

- run は開始時に bundle を 1 つ固定する。worker は `make_knowledge_resolver` で
  `ensure_bundle` → `resolve_for_project` を 1 回だけ行い、その `ResolvedKnowledge` だけを使う
- 仕様は承認時にサーバが作った `content.compiled` をそのまま使う（ここで変換し直さない）
- 図の操作とコードの対応は D9: 名前の一致を既定にし、`.speclino/bindings.yaml` で上書きする

対応の規則（`OperationResolver`）:

- 操作 `<Class>.<method>`: bindings の operations に同じキーがあればそのシンボル、無ければシンボルが
  `.<Class>.<method>` で終わる（または一致する）operation が 1 つだけのときそのシンボル。最後に
  解析器が実際に使う操作 ID（同じシンボルのキーのうち最後のもの）にそろえる（別名のキーがあっても
  Code IR のイベントと同じ ID になる）。見つからない・複数なら None（UNKNOWN）
- 入口 `<Class>.<method>`: behaviors に同じキーがあればそれ、無ければ entrypoint が
  `.<Class>.<method>` で終わる behavior が 1 つだけのときそれ。見つからなければ None（UNKNOWN）

DB に触れるのは `make_knowledge_resolver` の返す関数だけ（knowledge_registry は関数の中で
import する。check_pipeline ← knowledge_registry の循環を避けるため）。
"""

from __future__ import annotations

import hashlib
from collections.abc import Awaitable, Callable, Iterable
from dataclasses import dataclass

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from speclino.domain.bindings import Bindings
from speclino.domain.code_ir import CodeIR
from speclino.domain.domain_spec import DomainSpec
from speclino.domain.knowledge import Bundle, ResolvedKnowledge, SpecContent
from speclino.domain.uml import CompiledSpec

KnowledgeResolver = Callable[[str, str], Awaitable[ResolvedKnowledge]]


def bundle_hash(bundle: Bundle) -> str:
    """manifest に残す bundle のハッシュ。

    署名の対象と同じ正規ペイロード（`knowledge_registry.bundle_payload`）に署名を足したものの
    sha256。署名と別の正規化を持たない（同じ bundle は署名の検証と同じ bytes で同一視する）。
    """
    from speclino.services.knowledge_registry import bundle_payload

    digest = hashlib.sha256(bundle_payload(bundle))
    digest.update(b"\n" + bundle.signature.encode("utf-8", "surrogatepass"))
    return "sha256:" + digest.hexdigest()


def _tail_matches(symbol: str, name: str) -> bool:
    return symbol == name or symbol.endswith("." + name)


@dataclass(frozen=True)
class OperationResolver:
    """図の名前（`<Class>.<method>`）を bindings の操作 ID・behavior ID に写す。"""

    bindings: Bindings

    def operation(self, name: str) -> str | None:
        explicit = self.bindings.operations.get(name)
        if explicit is not None:
            symbol = explicit.symbol
        else:
            symbols = {
                ob.symbol
                for ob in self.bindings.operations.values()
                if _tail_matches(ob.symbol, name)
            }
            if len(symbols) != 1:
                return None
            symbol = next(iter(symbols))
        return self._analyzer_operation_id(symbol)

    def _analyzer_operation_id(self, symbol: str) -> str:
        """解析器が Code IR のイベントに付ける操作 ID。

        path_analyzer は `{ob.symbol: (op_id, ob) for op_id, ob in operations.items()}` で引くので、
        同じシンボルに別名のキーが複数あれば最後のものになる。それに合わせる（ずれると「呼んで
        いない」の偽の VIOLATED になる。test_check_pipeline_bundle で解析器との一致を確かめる）。
        """
        return [op_id for op_id, ob in self.bindings.operations.items() if ob.symbol == symbol][-1]

    def behavior(self, entry: str) -> str | None:
        if entry in self.bindings.behaviors:
            return entry
        candidates = [
            bid
            for bid, b in sorted(self.bindings.behaviors.items())
            if _tail_matches(b.entrypoint, entry)
        ]
        return candidates[0] if len(candidates) == 1 else None

    def known_operations(self, codes: Iterable[CodeIR]) -> frozenset[str]:
        """bindings の操作 ID と、Code IR に現れた操作 ID。"""
        seen = {
            e.operation
            for code in codes
            for path in code.paths
            for e in path.events
            if e.operation is not None
        }
        return frozenset({*self.bindings.operations, *seen})


@dataclass(frozen=True)
class SpecUnit:
    """bundle の spec の改訂 1 つ（検査の単位）。"""

    name: str
    artifact_id: str
    revision: int
    content_hash: str
    compiled: CompiledSpec | None
    domain: DomainSpec | None
    markdown: str


def spec_units(knowledge: ResolvedKnowledge) -> list[SpecUnit]:
    """プロジェクトに当たる spec の改訂を、artifact 名の順に並べる（決定的な順序）。"""
    units: list[SpecUnit] = []
    for rev in sorted(knowledge.specs, key=lambda r: (r.name, r.artifact_id)):
        content = rev.content
        if not isinstance(content, SpecContent):
            continue
        units.append(
            SpecUnit(
                name=rev.name,
                artifact_id=rev.artifact_id,
                revision=rev.revision,
                content_hash=rev.content_hash,
                compiled=content.compiled,
                domain=content.domain,
                markdown=content.markdown,
            )
        )
    return units


def make_knowledge_resolver(factory: async_sessionmaker[AsyncSession]) -> KnowledgeResolver:
    """worker 用: (workspace, project_id) → その時点の bundle をプロジェクトに当てた結果。

    1 トランザクションで `ensure_bundle`（S62 より前のワークスペースは初回に作る）→
    `resolve_for_project`。署名やメンバーのハッシュが合わなければ ValueError（run は失敗）。
    """

    async def resolve(workspace: str, project_id: str) -> ResolvedKnowledge:
        from speclino.services import knowledge_registry as kr

        async with factory() as session, session.begin():
            bundle = await kr.ensure_bundle(session, workspace)
            return await kr.resolve_for_project(session, bundle, project_id)

    return resolve


__all__ = [
    "KnowledgeResolver",
    "OperationResolver",
    "SpecUnit",
    "bundle_hash",
    "make_knowledge_resolver",
    "spec_units",
]
