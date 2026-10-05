import pytest
from pydantic import ValidationError

from speclino.domain.knowledge import (
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
        expires_at=b.created_at,
        signature="sig",
    )
    assert KnowledgeLock.model_validate_json(lock.model_dump_json()) == lock
