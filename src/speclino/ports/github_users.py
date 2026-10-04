"""GitHub のログイン名からユーザー ID を引くポート（S61）。

ログイン名は改名後に他人が取得できるので、承認者は ID で照合する。ID は承認者を保存するときに
引く。設定の保存は web ロールで動き、web は GitHub App の秘密鍵を持たないので、CodeHost とは
別のポートにする（公開のユーザー情報は認証なしで読める）。
"""

from __future__ import annotations

from typing import Protocol


class GitHubUserLookupError(Exception):
    """GitHub に問い合わせられなかった（レート制限・通信の失敗・想定外の応答）。"""


class GitHubUserLookup(Protocol):
    async def user_id(self, login: str) -> int | None:
        """`login` の今のユーザー ID。そのユーザーが無いなら None。

        問い合わせられなければ `GitHubUserLookupError`。ログイン名の形でなければ `ValueError`。
        """
        ...


__all__ = ["GitHubUserLookup", "GitHubUserLookupError"]
