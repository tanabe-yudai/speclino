"""承認者の照合（S61、design/2026-10-04-spec-approval-by-pr.md 4 章）。

GitHub のログイン名は改名後に他人が取得できるので、`approver_ids`（GitHub のユーザー ID）
だけで照合する。ID の無い設定（ID を保存する前のもの）では誰も承認者にしない（フェイルクローズ）。
設定画面で保存し直すか、管理 CLI の `backfill-approver-ids` で ID を補う。
保存値が型に合わなければ誰も承認者にしない（`parse_stored`）。
"""

from __future__ import annotations

from collections.abc import Mapping

from speclino.services.project_settings import approver_ids_missing, parse_stored


def is_approver(settings: Mapping[str, object], *, user_id: int | None, login: str) -> bool:
    """`projects.settings` の保存値 `settings` で、その人が承認者か。

    `login` は照合に使わない（監査の表示用に受け取るだけ）。`user_id` が無ければ承認者にしない。
    """
    stored = parse_stored(dict(settings))
    if stored.invalid or user_id is None:
        return False
    return user_id in stored.settings.approver_ids


__all__ = ["approver_ids_missing", "is_approver"]
