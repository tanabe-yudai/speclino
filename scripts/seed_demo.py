"""ショップ題材をローカルの DB に用意する（docs/demo-knowledge-registry.md）。

    SPECLINO_DATABASE_URL=postgresql+asyncpg://... \\
      uv run python scripts/seed_demo.py --repo-dir build/shop-repo

マイグレーション済みの空の DB に対して 1 回だけ実行する（同じワークスペースがあれば止まる）。
LLM は呼ばない。本体は examples/shop/seed.py。
"""

from __future__ import annotations

import argparse
import asyncio
import sys
from pathlib import Path

from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from examples.shop.seed import (  # noqa: E402
    GITHUB_REPO_ID,
    SEED_LLM_MODEL,
    UnsafeSeedTarget,
    check_seed_target,
    seed,
)
from speclino.settings import Settings  # noqa: E402


async def main(repo_dir: Path, artifact_dir: Path, settings: Settings) -> None:
    engine = create_async_engine(settings.database_url)
    try:
        result = await seed(
            async_sessionmaker(engine, expire_on_commit=False),
            repo_dir=repo_dir.resolve(),
            artifact_dir=artifact_dir.resolve(),
        )
    finally:
        await engine.dispose()
    print(f"ワークスペース: {result.workspace}（bundle 世代 {result.bundle_generation}）")
    print(f"プロジェクト: {result.project_id}")
    for name, aid in result.artifacts.items():
        print(f"  {name}: {aid}")
    print(f"リポジトリ: {result.repo_dir}")
    for branch, run_id in result.runs.items():
        print(f"  {branch}: /runs/{run_id}")
    print(f"リポジトリ ID: {GITHUB_REPO_ID}（SPECLINO_LOCAL_REPO_IDS に使う）")
    print(
        "サーバーは SPECLINO_CODE_HOST_KIND=local、SPECLINO_LOCAL_REPOS / SPECLINO_LOCAL_REPO_IDS、"
        f"SPECLINO_LLM_POLICIES_MODEL={SEED_LLM_MODEL} で起動する"
        "（docs/demo-knowledge-registry.md）"
    )


def run(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repo-dir", type=Path, default=Path("build/shop-repo"))
    parser.add_argument(
        "--artifact-dir",
        type=Path,
        default=None,
        help="run の成果物の置き場（既定は SPECLINO_ARTIFACT_DIR。サーバーと同じにする）",
    )
    parser.add_argument(
        "--allow-remote-db",
        action="store_true",
        help="DB のホストがローカルでなくても seed する（本番 env は常に拒否）",
    )
    args = parser.parse_args(argv)
    settings = Settings()
    try:
        check_seed_target(settings, allow_remote_db=args.allow_remote_db)
    except UnsafeSeedTarget as e:
        print(f"拒否した: {e}", file=sys.stderr)
        return 2
    asyncio.run(main(args.repo_dir, args.artifact_dir or settings.artifact_dir, settings))
    return 0


if __name__ == "__main__":
    sys.exit(run())
