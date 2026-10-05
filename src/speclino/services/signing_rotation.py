"""署名鍵の更新の後始末（#114）: 全ワークスペースの bundle を今の鍵で作り直す。

`speclino rebundle-all` が使う。bundle は不変なので、作り直しは新しい世代を作ること（承認・規約の
有効化と同じ `build_bundle`。ワークスペース行のロックの下で、競合の検査も同じ）。既定では、最新の
bundle が今の鍵の ID で署名されているワークスペースは飛ばす。1 つのワークスペースで失敗しても
（競合など）セーブポイントで巻き戻して残りを続け、結果に残す。

世代が進むので、各リポジトリの lock は古い扱い（REVIEW）になる。`speclino sync` をやり直す
（docs/runbooks/knowledge-registry-cutover.md の鍵の更新）。
"""

from __future__ import annotations

from collections.abc import Sequence

from sqlalchemy.ext.asyncio import AsyncSession

from speclino.db.repositories.bundles import BundleRepository
from speclino.domain.knowledge import RebundleOutcome
from speclino.services import bundle_signing
from speclino.services import knowledge_registry as kr


async def rebundle_all(
    session: AsyncSession, *, force: bool = False, only: Sequence[str] | None = None
) -> list[RebundleOutcome]:
    """bundle を持つ全ワークスペース（`only` があればそのうちの指定分）を今の鍵で作り直す。"""
    current_kid = bundle_signing.current_key_id()
    out: list[RebundleOutcome] = []
    workspaces = await BundleRepository(session).workspaces()
    for workspace in workspaces if only is None else [w for w in workspaces if w in only]:
        latest = await kr.current_bundle(session, workspace)
        previous = {
            "workspace": workspace,
            "previous_generation": latest.generation if latest else None,
            "previous_key_id": latest.key_id if latest else None,
        }
        if not force and latest is not None and latest.key_id == current_kid:
            out.append(RebundleOutcome(**previous, status="skipped", detail="今の鍵で署名済み"))
            continue
        try:
            async with session.begin_nested():
                bundle = await kr.build_bundle(session, workspace)
        except ValueError as e:  # BundleConflict など。このワークスペースだけ巻き戻す
            out.append(RebundleOutcome(**previous, status="failed", detail=str(e)))
            continue
        out.append(RebundleOutcome(**previous, status="rebundled", generation=bundle.generation))
    return out


__all__ = ["rebundle_all"]
