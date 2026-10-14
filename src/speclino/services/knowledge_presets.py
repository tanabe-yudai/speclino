"""新しいワークスペースに最初から入れておく知識（プリセット）。

本文は `src/speclino/presets/skills/<name>/SKILL.md` に同梱する。ワークスペースを作るのと同じ
トランザクションで承認済みとして入れ、bundle を作るので、作った直後から MCP・sync で配られる。
入れた後は通常の知識と同じで、改訂・取り下げは画面で行う。
"""

from __future__ import annotations

from pathlib import Path

from sqlalchemy.ext.asyncio import AsyncSession

from speclino.domain.knowledge import ArtifactScope, SkillContent
from speclino.services import knowledge_registry

PRESET_ACTOR = "preset"
PRESET_SKILLS_DIR = Path(__file__).resolve().parents[1] / "presets" / "skills"


def preset_skills() -> dict[str, SkillContent]:
    """同梱の skill（名前 → 内容）。どれもワークスペース全体に当てる。"""
    return {
        path.parent.name: SkillContent(
            kind="skill",
            skill_md=path.read_text(encoding="utf-8"),
            scope=ArtifactScope(workspace=True),
        )
        for path in sorted(PRESET_SKILLS_DIR.glob("*/SKILL.md"))
    }


async def install_presets(session: AsyncSession, workspace: str) -> None:
    """作ったばかりの `workspace` にプリセットを承認済みで入れ、bundle を作る。"""
    for name, content in preset_skills().items():
        await knowledge_registry.install_preset(
            session, workspace=workspace, name=name, content=content, actor=PRESET_ACTOR
        )
    await knowledge_registry.build_bundle(session, workspace)
