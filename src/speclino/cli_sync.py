"""`speclino sync`（S68・S75）。

チームの承認済みの知識（スキル・指示書・仕様）の書き出し計画をサーバから受け取り、MCP の
エージェントと同じ実行スクリプト（`speclino.sync_apply.apply_plan`）でこの checkout に当て、
`.speclino/lock.json` を残す。手元の lock を送るので、サーバはそれを確かめたうえで、外れた知識の
ファイルの削除も計画する。

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
from speclino.domain.knowledge_sync import InstructionsFile, SkillsRoot, SyncPlan
from speclino.sync_apply import LOCK_PATH, apply_plan

TOKEN_ENV = "SPECLINO_SYNC_TOKEN"
URL_ENV = "SPECLINO_URL"
_LOCK_MAX_BYTES = 1024 * 1024

# テストが差し替える（本番は既定の HTTP 通信）
transport_factory: Callable[[], httpx.BaseTransport | None] = lambda: None  # noqa: E731


class SyncFailed(RuntimeError):
    """取得に失敗した（メッセージにトークンを含めない）。"""


def _local_lock(repo_root: Path) -> str | None:
    """手元の lock の本文（無い・symlink・大きすぎる・読めないなら None）。"""
    target = repo_root / LOCK_PATH
    if target.is_symlink() or not target.is_file() or target.stat().st_size > _LOCK_MAX_BYTES:
        return None
    try:
        return target.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return None


def _repo_id_from_lock(lock_json: str | None) -> int | None:
    if lock_json is None:
        return None
    try:
        return KnowledgeLock.model_validate_json(lock_json).github_repo_id
    except (ValidationError, ValueError):
        return None


def fetch_plan(
    url: str,
    token: str,
    repo_id: int,
    lock_json: str | None,
    instructions_file: InstructionsFile | None = None,
    skills_dir: SkillsRoot | None = None,
    agent: str | None = None,
) -> SyncPlan:
    """`POST /api/sync/bundle` を呼んで計画を検証する。"""
    body: dict[str, object] = {"repo_id": repo_id, "lock_json": lock_json}
    if instructions_file is not None:
        body["instructions_file"] = instructions_file
    if skills_dir is not None:
        body["skills_dir"] = skills_dir
    if agent is not None:
        body["agent"] = agent
    with httpx.Client(
        base_url=url.rstrip("/"),
        headers={"Authorization": f"Bearer {token}"},
        timeout=30.0,
        transport=transport_factory(),
    ) as client:
        try:
            res = client.post("/api/sync/bundle", json=body)
        except httpx.HTTPError as e:
            raise SyncFailed(f"接続に失敗した: {type(e).__name__}") from e
    if res.status_code == 401:
        raise SyncFailed(f"トークンが無効か失効している（{TOKEN_ENV} を確かめてください）")
    if res.status_code == 404:
        raise SyncFailed(f"リポジトリ ID {repo_id} はこのトークンの対象ではない")
    if res.status_code != 200:
        raise SyncFailed(f"サーバがエラーを返した: HTTP {res.status_code}")
    try:
        plan = SyncPlan.model_validate_json(res.content)
    except ValidationError as e:
        raise SyncFailed(f"応答の形が正しくない: {e.error_count()} 件の不備") from e
    if plan.github_repo_id != repo_id or plan.lock.github_repo_id != repo_id:
        raise SyncFailed("応答の計画が別のリポジトリのもの")
    return plan


def sync(
    url: Annotated[
        str, typer.Option("--url", help=f"SpecLino のベース URL（既定は {URL_ENV}）")
    ] = "",
    repo: Annotated[Path, typer.Option("--repo", help="書き出す checkout のルート")] = Path("."),
    repo_id: Annotated[
        int | None,
        typer.Option("--repo-id", help="GitHub のリポジトリ ID（既定は手元の lock の値）"),
    ] = None,
    instructions_file: Annotated[
        InstructionsFile | None,
        typer.Option(
            "--instructions-file",
            help=(
                "指示書の本文の書き出し先（省略すると AGENTS.md。"
                "CLAUDE.md があれば @AGENTS.md の橋）"
            ),
        ),
    ] = None,
    skills_dir: Annotated[
        SkillsRoot | None,
        typer.Option(
            "--skills-dir",
            help=(
                "スキルの書き出し先をこのルートだけにする"
                "（省略すると前回のルートに --agent のルートを足す）"
            ),
        ),
    ] = None,
    agent: Annotated[
        str | None,
        typer.Option(
            "--agent",
            help=(
                "使うエージェントの名前（claude を含めば .claude/skills、"
                "それ以外は .agents/skills を足す）"
            ),
        ),
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
    lock_json = _local_lock(repo)
    target_id = repo_id if repo_id is not None else _repo_id_from_lock(lock_json)
    if target_id is None:
        typer.echo("--repo-id に GitHub のリポジトリ ID を指定してください（初回のみ）", err=True)
        raise typer.Exit(code=2)
    try:
        plan = fetch_plan(base, token, target_id, lock_json, instructions_file, skills_dir, agent)
    except SyncFailed as e:
        typer.echo(
            f"同期できなかった（手元のファイルと lock は前のまま）: {e}".replace(token, "***"),
            err=True,
        )
        raise typer.Exit(code=1) from e
    result = apply_plan(repo, plan.model_dump(mode="json"))
    if not result.ok:
        typer.echo(
            f"同期できなかった（手元のファイルと lock は前のまま）: {result.reason}".replace(
                token, "***"
            ),
            err=True,
        )
        raise typer.Exit(code=1)
    typer.echo(
        f"bundle {plan.bundle_generation} を書き出した"
        f"（書き換え {len(result.written)}、削除 {len(result.deleted)}、"
        f"変更なし {len(result.unchanged)}、知識 {len(plan.lock.members)}）"
    )
    for warning in result.warnings:
        typer.echo(f"警告: {warning}", err=True)


__all__ = ["SyncFailed", "fetch_plan", "sync"]
