"""同梱のプリセットが知識として読めること（DB 不要）。"""

from __future__ import annotations

from speclino.domain.knowledge import ArtifactRevision
from speclino.services.knowledge_presets import preset_skills
from speclino.services.knowledge_registry import skill_dir_name


def test_presets_include_mcp_guide_and_name_matches_frontmatter() -> None:
    presets = preset_skills()
    assert "speclino-mcp-guide" in presets
    for name, content in presets.items():
        rev = ArtifactRevision.model_construct(name=name, content=content)
        assert skill_dir_name(rev) == name
