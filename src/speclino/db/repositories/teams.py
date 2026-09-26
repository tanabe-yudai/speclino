"""`teams` の repository（統治の穴修正: 承認者の許可集合とチーム単位の排他）。

承認者の許可集合を `policy_revisions` に持たせない理由は
`speclino.db.models.TeamRecord` の docstring 参照。ここでは DB 操作だけを担い、
何を検証するか・監査に何を残すかは `services/policy_governance.py` に委ねる。
"""

from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from speclino.db.models import TeamRecord


class TeamRepository:
    """`teams` テーブルの読み書きを担う。"""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def list_approvers(self, team: str) -> list[str]:
        """team の承認者 ID 一覧。team 行が無ければ空リスト（＝誰も承認できない、安全側）。"""
        stmt = select(TeamRecord.approvers).where(TeamRecord.team == team)
        row = (await self._session.execute(stmt)).scalar_one_or_none()
        return list(row) if row is not None else []

    async def set_approvers(self, team: str, approvers: list[str]) -> None:
        """team の承認者一覧を置き換える（無ければ作る）。テスト・将来の管理API向け。"""
        stmt = (
            pg_insert(TeamRecord)
            .values(team=team, approvers=approvers)
            .on_conflict_do_update(index_elements=[TeamRecord.team], set_={"approvers": approvers})
        )
        await self._session.execute(stmt)
        await self._session.flush()

    async def ensure_and_lock(self, team: str) -> None:
        """team 行が無ければ作り、行ロックを取る（改訂操作全体の排他用）。

        `INSERT ... ON CONFLICT DO NOTHING` は行の存在を保証するだけで、衝突した場合に
        その行のロックを保持し続けない。続けて `SELECT ... FOR UPDATE` することで、この
        呼び出し以降このトランザクションが commit/rollback するまで、同じ team に対する
        `ensure_and_lock` を直列化する。

        呼び出し元は `services/policy_governance.py` の `seed_from_package` /
        `create_draft` / `_approve`（`approve` の実装本体）の3つ全て（統治の穴修正
        #2, #4）。当初は「2つの seed が同時に『まだ改訂が無い』と判定して両方進んで
        しまう」のを防ぐためだけに導入したが、それだけでは seed と通常の下書き作成→承認
        の間のレースは塞げなかった（seed が「改訂0件」を確認した直後に、ロックを取らない
        通常経路が割り込んで承認済みの改訂を作ってしまい、seed がそれを superseded に
        できてしまう）ため、`create_draft` / `_approve` もここを呼ぶようにして、team 単位
        のあらゆる改訂操作をこの1つの行ロックで直列化している。呼び出し側は 1 トランザク
        ションの中でロック取得の直後に必要な確認・書き込みを行い、そのまま同じ
        トランザクションで完了させる必要がある（ロックの取得順序はチーム行→改訂行に
        統一し、デッドロックを避ける）。
        """
        insert_stmt = (
            pg_insert(TeamRecord)
            .values(team=team, approvers=[])
            .on_conflict_do_nothing(index_elements=[TeamRecord.team])
        )
        await self._session.execute(insert_stmt)
        await self._session.execute(
            select(TeamRecord.team).where(TeamRecord.team == team).with_for_update()
        )


__all__ = ["TeamRepository"]
