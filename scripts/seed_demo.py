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

from examples.shop.seed import SEED_LLM_MODEL, seed  # noqa: E402
from speclino.settings import Settings  # noqa: E402


async def main(repo_dir: Path, artifact_dir: Path) -> None:
    engine = create_async_engine(Settings().database_url)
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
    print(
        "サーバーは SPECLINO_CODE_HOST_KIND=local、SPECLINO_LOCAL_REPOS / SPECLINO_LOCAL_REPO_IDS、"
        f"SPECLINO_LLM_POLICIES_MODEL={SEED_LLM_MODEL} で起動する"
        "（docs/demo-knowledge-registry.md）"
    )


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repo-dir", type=Path, default=Path("build/shop-repo"))
    parser.add_argument(
        "--artifact-dir",
        type=Path,
        default=None,
        help="run の成果物の置き場（既定は SPECLINO_ARTIFACT_DIR。サーバーと同じにする）",
    )
    args = parser.parse_args()
    asyncio.run(main(args.repo_dir, args.artifact_dir or Settings().artifact_dir))
