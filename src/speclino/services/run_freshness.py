"""古い実行の結果を公開しない（AC-011 / FR 017）。

`should_publish` は SpecLino 自身の解析対象（specs/SPEC-RUN-001.md、R-RUN-01〜03）なので
対応構文だけで書く。各出口は bindings.yaml で操作（publish / mark_superseded / mark_not_latest）
に束縛する。
"""

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
        return _mark_superseded()
    is_latest = _is_latest(run_id, latest_run_id)
    if not is_latest:
        return _mark_not_latest(latest_run_id)
    return _publish()


def _is_latest(run_id: UUID, latest_run_id: UUID | None) -> bool:
    """最新 run が分からない（None）ときは最新とみなす。"""
    return latest_run_id is None or latest_run_id == run_id


def _mark_superseded() -> Freshness:
    return Freshness("superseded", "push により新しい run に置き換えられた")


def _mark_not_latest(latest_run_id: UUID | None) -> Freshness:
    return Freshness("not_latest", f"最新の run は {latest_run_id}")


def _publish() -> Freshness:
    return Freshness("publish", "最新の run")


__all__ = ["Freshness", "PublishDecision", "should_publish"]
