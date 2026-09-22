"""古い実行の結果を公開しない（AC-011 / FR 017）。"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal
from uuid import UUID

from speclino.domain.run import ExecState

PublishDecision = Literal["publish", "superseded", "not_latest"]


@dataclass(frozen=True)
class Freshness:
    decision: PublishDecision
    reason: str

    @property
    def publish(self) -> bool:
        return self.decision == "publish"


def should_publish(
    run_id: UUID,
    exec_state: ExecState,
    latest_run_id: UUID | None,
) -> Freshness:
    """完了した run の結果を PR の現在の結果として採用してよいか。

    - superseded にされた run（push 後に古くなった）は履歴に残すだけで公開しない。
    - 同じ PR の最新 run でなければ公開しない（完了順の逆転）。
    """
    if exec_state == "superseded":
        return Freshness("superseded", "push により新しい run に置き換えられた")
    if latest_run_id is not None and latest_run_id != run_id:
        return Freshness("not_latest", f"最新の run は {latest_run_id}")
    return Freshness("publish", "最新の run")
