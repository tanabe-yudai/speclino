from datetime import UTC, datetime, timedelta

import pytest
from pydantic import ValidationError

from speclino.domain.knowledge import (
    ArtifactRevision,
    ArtifactScope,
    Bundle,
    BundleMember,
    InstructionsContent,
    KnowledgeLock,
    SkillContent,
    SpecContent,
)


def test_scope_requires_exactly_one_form() -> None:
    assert ArtifactScope(workspace=True).workspace
    assert ArtifactScope(projects=["p1"]).projects == ["p1"]
    with pytest.raises(ValidationError):
        ArtifactScope()
    with pytest.raises(ValidationError):
        ArtifactScope(workspace=True, projects=["p1"])


def test_spec_scope_must_name_projects() -> None:
    with pytest.raises(ValidationError):
        SpecContent(kind="spec", markdown="# x", scope=ArtifactScope(workspace=True))


def test_skill_file_paths_are_relative_and_inside() -> None:
    ok = SkillContent(
        kind="skill",
        skill_md="---\nname: a\ndescription: b\n---\n",
        files={"ref/x.md": "y"},
        scope=ArtifactScope(workspace=True),
    )
    assert "ref/x.md" in ok.files
    for bad in ["../x", "/etc/passwd", "a/../../b", "a\\b", ""]:
        with pytest.raises(ValidationError):
            SkillContent(
                kind="skill",
                skill_md="---\nname: a\ndescription: b\n---\n",
                files={bad: "y"},
                scope=ArtifactScope(workspace=True),
            )


def test_managed_marker_is_rejected_in_bodies() -> None:
    with pytest.raises(ValidationError):
        InstructionsContent(
            kind="instructions",
            target="agents_md",
            body="x <!-- speclino:end --> y",
            scope=ArtifactScope(workspace=True),
        )


def test_bundle_and_lock_roundtrip() -> None:
    m = BundleMember(
        artifact_id="a1", kind="skill", name="deploy", revision=2, content_hash="sha256:" + "0" * 64
    )
    b = Bundle(
        workspace="w",
        generation=3,
        policy_revision=5,
        policy_hash=None,
        members=[m],
        scope_hash="sha256:" + "1" * 64,
        signature="sig",
    )
    lock = KnowledgeLock(
        workspace="w",
        github_repo_id=1,
        bundle_generation=3,
        members=[m],
        files={"AGENTS.md": "sha256:" + "2" * 64},
        issued_at=b.created_at,
        expires_at=b.created_at + timedelta(hours=1),
        signature="sig",
    )
    assert KnowledgeLock.model_validate_json(lock.model_dump_json()) == lock


_SKILL_MD = "---\nname: a\ndescription: b\n---\n"
_WS = ArtifactScope(workspace=True)


@pytest.mark.parametrize(
    "bad",
    [
        "",
        "a\x00b",
        "a\\b",
        "/abs",
        "C:/x",
        "c:x",
        "../x",
        "a/../../b",
        "a/../b",
        "./a",
        "a/./b",
        "a//b",
        "a/",
        "..",
        "SKILL.md",
        "skill.md",
        "Skill.MD",
    ],
)
def test_skill_path_rejections(bad: str) -> None:
    with pytest.raises(ValidationError):
        SkillContent(kind="skill", skill_md=_SKILL_MD, files={bad: "y"}, scope=_WS)


def test_skill_path_allows_dotdot_prefixed_names() -> None:
    ok = SkillContent(kind="skill", skill_md=_SKILL_MD, files={"..foo/x.md": "y"}, scope=_WS)
    assert "..foo/x.md" in ok.files


def test_skill_paths_case_insensitive_duplicates_rejected() -> None:
    with pytest.raises(ValidationError):
        SkillContent(kind="skill", skill_md=_SKILL_MD, files={"a.md": "1", "A.md": "2"}, scope=_WS)


def test_skill_size_limits() -> None:
    big = "x" * (256 * 1024 + 1)
    with pytest.raises(ValidationError):
        SkillContent(kind="skill", skill_md=big, scope=_WS)
    with pytest.raises(ValidationError):
        InstructionsContent(kind="instructions", target="agents_md", body=big, scope=_WS)
    with pytest.raises(ValidationError):
        SpecContent(kind="spec", markdown=big, scope=ArtifactScope(projects=["p"]))
    # skill_md も合計に含まれる: 4 x 256KiB のファイル + skill_md で 1MiB 超
    chunk = "x" * (256 * 1024)
    files = {f"f{i}.md": chunk for i in range(4)}
    with pytest.raises(ValidationError):
        SkillContent(kind="skill", skill_md=_SKILL_MD, files=files, scope=_WS)


@pytest.mark.parametrize(
    "text",
    [
        "x <!-- speclino:begin id=1 --> y",
        "x <!-- speclino:end --> y",
        "<!--speclino:begin",
        "<!--\n  SpecLino : END -->",
        "<!--  speclino:Begin",
    ],
)
def test_markers_rejected_everywhere(text: str) -> None:
    with pytest.raises(ValidationError):
        SkillContent(kind="skill", skill_md=text, scope=_WS)
    with pytest.raises(ValidationError):
        SkillContent(kind="skill", skill_md=_SKILL_MD, files={"a.md": text}, scope=_WS)
    with pytest.raises(ValidationError):
        InstructionsContent(kind="instructions", target="claude_md", body=text, scope=_WS)


def _rev(kind: str, content: object) -> ArtifactRevision:
    return ArtifactRevision.model_validate(
        {
            "artifact_id": "a1",
            "workspace": "w",
            "kind": kind,
            "name": "n",
            "revision": 1,
            "status": "draft",
            "content": content,
            "content_hash": "sha256:" + "0" * 64,
            "author": "u",
            "created_at": datetime.now(UTC),
        }
    )


def test_revision_kind_must_match_content() -> None:
    spec = {"kind": "spec", "markdown": "# x", "scope": {"projects": ["p"]}}
    assert _rev("spec", spec).content.kind == "spec"
    with pytest.raises(ValidationError):
        _rev("skill", spec)


def test_spec_content_valid() -> None:
    sc = SpecContent(kind="spec", markdown="# x", scope=ArtifactScope(projects=["p1"]))
    assert sc.compiled is None and sc.legacy_ir == []


def test_lock_expiry_must_follow_issue() -> None:
    now = datetime.now(UTC)
    with pytest.raises(ValidationError):
        KnowledgeLock(
            workspace="w",
            github_repo_id=1,
            bundle_generation=1,
            members=[],
            files={},
            issued_at=now,
            expires_at=now,
            signature="s",
        )


def test_instructions_target_is_optional_and_old_data_still_loads() -> None:
    new = InstructionsContent(kind="instructions", body="b", scope=_WS)
    assert new.target is None
    old = InstructionsContent.model_validate(
        {"kind": "instructions", "target": "claude_md", "body": "b", "scope": {"workspace": True}}
    )
    assert old.target == "claude_md"
