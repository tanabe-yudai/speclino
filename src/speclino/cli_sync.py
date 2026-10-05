"""`speclino sync`（S68、design/2026-10-05-team-knowledge-registry.md 7.1）。

チームの承認済みの知識（スキル・指示書・仕様）を取得して、この checkout に書き出し、
`.speclino/lock.json` を残す。書き出しの検証・置き換えは `services/knowledge_sync.write_bundle`。

- サーバの URL は `--url` か環境変数 `SPECLINO_URL`
- トークンは環境変数 `SPECLINO_SYNC_TOKEN` だけ（引数では受け付けない。シェルの履歴に残るため）。
  エラー表示にもトークンを出さない
- リポジトリ ID は `--repo-id`、無ければ手元の lock の `github_repo_id`
"""

from __future__ import annotations

import os
from collections.abc import Callable
from pathlib import Path
from typing import Annotated

import httpx
import typer
from pydantic import ValidationError

from speclino.domain.knowledge import KnowledgeLock
from speclino.domain.knowledge_sync import SyncPayload
from speclino.services.knowledge_sync import LOCK_PATH, SyncRejected, write_bundle

TOKEN_ENV = "SPECLINO_SYNC_TOKEN"
URL_ENV = "SPECLINO_URL"

# テストが差し替える（本番は既定の HTTP 通信）
transport_factory: Callable[[], httpx.BaseTransport | None] = lambda: None  # noqa: E731


class SyncFailed(RuntimeError):
    """取得に失敗した（メッセージにトークンを含めない）。"""


def _repo_id_from_lock(repo_root: Path) -> int | None:
    target = repo_root / LOCK_PATH
    if target.is_symlink() or not target.is_file():
        return None
    try:
        return KnowledgeLock.model_validate_json(target.read_bytes()).github_repo_id
    except (ValidationError, ValueError):
        return None


def fetch_payload(url: str, token: str, repo_id: int) -> SyncPayload:
    """`GET /api/sync/bundle` を呼んで応答を検証する。"""
    with httpx.Client(
        base_url=url.rstrip("/"),
        headers={"Authorization": f"Bearer {token}"},
        timeout=30.0,
        transport=transport_factory(),
    ) as client:
        try:
            res = client.get("/api/sync/bundle", params={"repo_id": repo_id})
        except httpx.HTTPError as e:
            raise SyncFailed(f"接続に失敗した: {type(e).__name__}") from e
    if res.status_code == 401:
        raise SyncFailed(f"トークンが無効か失効している（{TOKEN_ENV} を確かめてください）")
    if res.status_code == 404:
        raise SyncFailed(f"リポジトリ ID {repo_id} はこのトークンの対象ではない")
    if res.status_code != 200:
        raise SyncFailed(f"サーバがエラーを返した: HTTP {res.status_code}")
    try:
        payload = SyncPayload.model_validate_json(res.content)
    except ValidationError as e:
        raise SyncFailed(f"応答の形が正しくない: {e.error_count()} 件の不備") from e
    if payload.lock.github_repo_id != repo_id:
        raise SyncFailed("応答の lock が別のリポジトリのもの")
    return payload


def sync(
    url: Annotated[
        str, typer.Option("--url", help=f"SpecLino のベース URL（既定は {URL_ENV}）")
    ] = "",
    repo: Annotated[Path, typer.Option("--repo", help="書き出す checkout のルート")] = Path("."),
    repo_id: Annotated[
        int | None,
        typer.Option("--repo-id", help="GitHub のリポジトリ ID（既定は手元の lock の値）"),
    ] = None,
) -> None:
    """承認済みのスキル・指示書・仕様を取得して書き出し、.speclino/lock.json を更新する。"""
    token = os.environ.get(TOKEN_ENV, "")
    if not token:
        typer.echo(
            f"環境変数 {TOKEN_ENV} に sync トークン（slsync_...）を設定してください。"
            "トークンは引数では受け付けない（シェルの履歴に残るため）",
            err=True,
        )
        raise typer.Exit(code=2)
    base = url or os.environ.get(URL_ENV, "")
    if not base:
        typer.echo(f"--url か {URL_ENV} に SpecLino のベース URL を指定してください", err=True)
        raise typer.Exit(code=2)
    if not repo.is_dir():
        typer.echo(f"--repo がディレクトリではない: {repo}", err=True)
        raise typer.Exit(code=2)
    target_id = repo_id if repo_id is not None else _repo_id_from_lock(repo)
    if target_id is None:
        typer.echo("--repo-id に GitHub のリポジトリ ID を指定してください（初回のみ）", err=True)
        raise typer.Exit(code=2)
    try:
        payload = fetch_payload(base, token, target_id)
        kept = write_bundle(repo, payload.files, payload.lock)
    except (SyncFailed, SyncRejected, OSError) as e:
        typer.echo(
            f"同期できなかった（手元のファイルと lock は前のまま）: {e}".replace(token, "***"),
            err=True,
        )
        raise typer.Exit(code=1) from e
    for path in kept:
        typer.echo(
            f"警告: {path} は手元で変更されているため消さずに残した（不要なら手で消してください）",
            err=True,
        )
    typer.echo(
        f"bundle {payload.lock.bundle_generation} を書き出した"
        f"（ファイル {len(payload.files)}、知識 {len(payload.lock.members)}）"
    )


__all__ = ["SyncFailed", "fetch_payload", "sync"]
