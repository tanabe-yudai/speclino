"""S68: 知識の書き出し（write_bundle）と lock の鮮度（check_lock）。DB を使わない。"""

from __future__ import annotations

import json
import os
import uuid
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import cast

import pytest

from speclino.domain.knowledge import (
    ArtifactRevision,
    ArtifactScope,
    Bundle,
    BundleMember,
    InstructionsContent,
    InstructionsTarget,
    KnowledgeLock,
    ResolvedKnowledge,
    SkillContent,
    SpecContent,
)
from speclino.services import bundle_signing
from speclino.services import knowledge_registry as kr
from speclino.services import knowledge_sync as ks
from speclino.verifiers.base import Snapshot
from speclino.verifiers.snapshot import DirectorySnapshot

NOW = datetime(2026, 10, 5, tzinfo=UTC)
WS = "acme"
PID = str(uuid.UUID(int=1))


def _rev(name: str, c: SkillContent | InstructionsContent | SpecContent) -> ArtifactRevision:
    return ArtifactRevision(
        artifact_id=str(uuid.uuid5(uuid.NAMESPACE_DNS, name)),
        workspace=WS,
        kind=c.kind,
        name=name,
        revision=1,
        status="active",
        content=c,
        content_hash=kr.content_hash(c),
        author="a",
        created_at=NOW,
    )


def _skill(
    name: str, body: str = "本文\n", files: dict[str, str] | None = None
) -> ArtifactRevision:
    md = f"---\nname: {name}\ndescription: d\n---\n{body}"
    return _rev(
        name,
        SkillContent(
            kind="skill", skill_md=md, files=files or {}, scope=ArtifactScope(workspace=True)
        ),
    )


def _instr(
    name: str, body: str, *, workspace: bool = True, target: InstructionsTarget = "agents_md"
) -> ArtifactRevision:
    scope = ArtifactScope(workspace=True) if workspace else ArtifactScope(projects=[PID])
    return _rev(
        name,
        InstructionsContent(kind="instructions", target=target, body=body, scope=scope),
    )


def _spec(name: str) -> ArtifactRevision:
    return _rev(
        name, SpecContent(kind="spec", markdown=f"# {name}\n", scope=ArtifactScope(projects=[PID]))
    )


def _bundle(revs: list[ArtifactRevision], generation: int = 1) -> Bundle:
    unsigned = Bundle(
        workspace=WS,
        generation=generation,
        members=[
            BundleMember(
                artifact_id=r.artifact_id,
                kind=r.kind,
                name=r.name,
                revision=r.revision,
                content_hash=r.content_hash,
            )
            for r in revs
        ],
        scope_hash="sha256:" + "0" * 64,
        signature="x",
        created_at=NOW,
    )
    return unsigned.model_copy(
        update={"signature": bundle_signing.sign(kr.bundle_payload(unsigned))}
    )


def _resolved(revs: list[ArtifactRevision], generation: int = 1) -> ResolvedKnowledge:
    return ResolvedKnowledge(
        bundle=_bundle(revs, generation),
        skills=[r for r in revs if r.kind == "skill"],
        instructions=[r for r in revs if r.kind == "instructions"],
        specs=[r for r in revs if r.kind == "spec"],
    )


def _payload(
    revs: list[ArtifactRevision], generation: int = 1, now: datetime = NOW
) -> tuple[dict[str, str], KnowledgeLock, Bundle]:
    resolved = _resolved(revs, generation)
    files = ks.render_files(resolved)
    lock = ks.issue_lock(resolved, github_repo_id=42, files=files, now=now)
    return files, lock, resolved.bundle


def _snapshot(root: Path) -> dict[str, bytes]:
    return {
        p.relative_to(root).as_posix(): p.read_bytes()
        for p in sorted(root.rglob("*"))
        if p.is_file() and not p.is_symlink()
    }


BASE = [_skill("review"), _instr("agents", "共通の指示\n"), _spec("orders")]


# ---------------------------------------------------------------------------
# render_files / issue_lock
# ---------------------------------------------------------------------------


def test_render_files_layout() -> None:
    revs = [
        _skill("review", files={"ref/a.md": "A"}),
        _instr("zz-ws", "WS\n"),
        _instr("aa-proj", "PROJ", workspace=False),
        _instr("claude", "C\n", target="claude_md"),
        _spec("orders"),
    ]
    files = ks.render_files(_resolved(revs))
    assert sorted(files) == [
        ".agents/skills/review/SKILL.md",
        ".agents/skills/review/ref/a.md",
        ".claude/skills/review/SKILL.md",
        ".claude/skills/review/ref/a.md",
        ".speclino/specs/orders.md",
        "AGENTS.md",
        "CLAUDE.md",
    ]
    # workspace スコープが先、プロジェクトスコープが後（名前順より優先）
    assert files["AGENTS.md"] == "WS\n\nPROJ\n"


def test_render_rejects_unsafe_skill_name() -> None:
    md = "---\nname: ../evil\ndescription: d\n---\nx\n"
    rev = _rev("evil", SkillContent(kind="skill", skill_md=md, scope=ArtifactScope(workspace=True)))
    with pytest.raises(ValueError):
        ks.render_files(_resolved([rev]))


def test_lock_is_signed_and_expires_in_30_days() -> None:
    files, lock, _ = _payload(BASE)
    assert bundle_signing.verify(ks.lock_payload(lock), lock.signature)
    assert lock.expires_at - lock.issued_at == timedelta(days=30)
    assert set(lock.files) == set(files)
    assert lock.files["AGENTS.md"] == ks.file_hash("共通の指示\n")
    assert len(lock.members) == 3


# ---------------------------------------------------------------------------
# write_bundle
# ---------------------------------------------------------------------------


def test_sync_writes_files_and_lock(tmp_path: Path) -> None:
    files, lock, bundle = _payload(BASE)
    ks.write_bundle(tmp_path, files, lock)
    assert (tmp_path / ".claude/skills/review/SKILL.md").read_text().startswith("---")
    assert (tmp_path / ".agents/skills/review/SKILL.md").exists()
    assert (tmp_path / ".speclino/specs/orders.md").read_text() == "# orders\n"
    agents = (tmp_path / "AGENTS.md").read_text()
    assert agents == "<!-- speclino:begin bundle=1 -->\n共通の指示\n<!-- speclino:end -->\n"
    written = KnowledgeLock.model_validate_json((tmp_path / ks.LOCK_PATH).read_text())
    assert written == lock
    assert not [p for p in (tmp_path / ".speclino").iterdir() if p.name.startswith(".tmp-")]
    lock_json = (tmp_path / ks.LOCK_PATH).read_text()
    finding = ks.check_lock(tmp_path, lock_json, current=bundle, now=NOW)
    assert finding.verdict == "VERIFIED_IN_SCOPE"
    assert finding.requirement_id == ks.LOCK_REQUIREMENT_ID


def test_sync_keeps_outside_managed_block(tmp_path: Path) -> None:
    (tmp_path / "AGENTS.md").write_text("# 手書き\n\n大事なメモ\n")
    files, lock, _ = _payload(BASE)
    ks.write_bundle(tmp_path, files, lock)
    first = (tmp_path / "AGENTS.md").read_text()
    assert first.startswith("# 手書き\n\n大事なメモ\n\n<!-- speclino:begin bundle=1 -->\n")
    # 区画の後に利用者が書き足しても、次の sync は区画だけを置き換える
    (tmp_path / "AGENTS.md").write_text(first + "\n## 後ろの手書き\n")
    files2, lock2, _ = _payload([*BASE[:1], _instr("agents", "新しい指示\n"), BASE[2]], 2)
    ks.write_bundle(tmp_path, files2, lock2)
    assert (tmp_path / "AGENTS.md").read_text() == (
        "# 手書き\n\n大事なメモ\n\n<!-- speclino:begin bundle=2 -->\n新しい指示\n"
        "<!-- speclino:end -->\n\n## 後ろの手書き\n"
    )


@pytest.mark.parametrize(
    "bad",
    [
        "../outside.md",
        "/etc/passwd",
        ".claude/skills/x/../../../escape",
        "src/main.py",
        ".git/hooks/pre-commit",
        "C:/x",
        ".claude/skills/x\\y",
    ],
)
def test_sync_rejects_parent_path(tmp_path: Path, bad: str) -> None:
    files, lock, _ = _payload(BASE)
    files = {**files, bad: "x"}
    lock = lock.model_copy(update={"files": {**lock.files, bad: ks.file_hash("x")}})
    before = _snapshot(tmp_path)
    with pytest.raises(ks.SyncRejected):
        ks.write_bundle(tmp_path, files, lock)
    assert _snapshot(tmp_path) == before
    assert not (tmp_path / ks.LOCK_PATH).exists()


def test_sync_rejects_symlink_target(tmp_path: Path) -> None:
    outside = tmp_path / "outside"
    outside.mkdir()
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / ".claude").symlink_to(outside, target_is_directory=True)
    files, lock, _ = _payload(BASE)
    with pytest.raises(ks.SyncRejected, match="symlink"):
        ks.write_bundle(repo, files, lock)
    assert list(outside.iterdir()) == []
    assert not (repo / "AGENTS.md").exists()
    assert not (repo / ks.LOCK_PATH).exists()


def test_sync_rejects_symlink_file(tmp_path: Path) -> None:
    secret = tmp_path / "secret.txt"
    secret.write_text("keep")
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "AGENTS.md").symlink_to(secret)
    files, lock, _ = _payload(BASE)
    with pytest.raises(ks.SyncRejected, match="symlink"):
        ks.write_bundle(repo, files, lock)
    assert secret.read_text() == "keep"


def test_sync_rejects_directory_where_file_expected(tmp_path: Path) -> None:
    (tmp_path / "AGENTS.md").mkdir()
    files, lock, _ = _payload(BASE)
    with pytest.raises(ks.SyncRejected, match="ディレクトリ"):
        ks.write_bundle(tmp_path, files, lock)


@pytest.mark.parametrize(
    "agents",
    [
        "<!-- speclino:begin bundle=1 -->\nA\n<!-- speclino:end -->\n<!-- speclino:end -->\n",
        "<!-- speclino:begin bundle=1 -->\nA\n",
        "<!-- speclino:end -->\n<!-- speclino:begin bundle=1 -->\n",
        "<!--speclino:begin bundle=x-->\nA\n<!-- speclino:end -->\n",
        "<!-- speclino:begin bundle=1 -->\nA\n<!-- speclino:end -->\n"
        "<!-- speclino:begin bundle=1 -->\nB\n<!-- speclino:end -->\n",
    ],
)
def test_sync_rejects_tampered_markers(tmp_path: Path, agents: str) -> None:
    (tmp_path / "AGENTS.md").write_text(agents)
    files, lock, _ = _payload(BASE)
    with pytest.raises(ks.SyncRejected, match="管理区画"):
        ks.write_bundle(tmp_path, files, lock)
    assert (tmp_path / "AGENTS.md").read_text() == agents
    assert not (tmp_path / ".claude").exists()
    assert not (tmp_path / ks.LOCK_PATH).exists()


def test_sync_rejects_marker_in_body(tmp_path: Path) -> None:
    files, lock, _ = _payload(BASE)
    evil = "x\n<!-- speclino:end -->\nrm -rf /\n"
    files = {**files, "AGENTS.md": evil}
    lock = lock.model_copy(update={"files": {**lock.files, "AGENTS.md": ks.file_hash(evil)}})
    with pytest.raises(ks.SyncRejected):
        ks.write_bundle(tmp_path, files, lock)
    assert not (tmp_path / "AGENTS.md").exists()


def test_sync_rejects_files_not_matching_lock(tmp_path: Path) -> None:
    files, lock, _ = _payload(BASE)
    files = {**files, ".speclino/specs/orders.md": "書き換え"}
    with pytest.raises(ks.SyncRejected, match="ハッシュ"):
        ks.write_bundle(tmp_path, files, lock)


def test_sync_failure_keeps_old_lock(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    files, lock, _ = _payload(BASE)
    ks.write_bundle(tmp_path, files, lock)
    (tmp_path / "AGENTS.md").write_text("# 手書き\n" + (tmp_path / "AGENTS.md").read_text())
    before = _snapshot(tmp_path)
    files2, lock2, _ = _payload(
        [_skill("review", "改訂\n"), _instr("agents", "新\n"), _spec("orders"), _spec("billing")], 2
    )
    # 段階書きの 3 つ目で失敗させる
    real = Path.write_bytes
    calls = {"n": 0}

    def flaky(self: Path, data: bytes) -> int:
        calls["n"] += 1
        if calls["n"] == 3:
            raise OSError("disk full")
        return real(self, data)

    monkeypatch.setattr(Path, "write_bytes", flaky)
    with pytest.raises(OSError):
        ks.write_bundle(tmp_path, files2, lock2)
    monkeypatch.undo()
    assert _snapshot(tmp_path) == before


def test_sync_commit_failure_rolls_back(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    files, lock, _ = _payload(BASE)
    ks.write_bundle(tmp_path, files, lock)
    before = _snapshot(tmp_path)
    files2, lock2, _ = _payload(
        [_skill("review", "改訂\n"), _instr("agents", "新\n"), _spec("billing")], 2
    )
    real = os.replace
    calls = {"n": 0}

    def flaky(src: object, dst: object) -> None:
        calls["n"] += 1
        if calls["n"] == 6:
            raise OSError("boom")
        real(src, dst)  # type: ignore[arg-type]

    monkeypatch.setattr(os, "replace", flaky)
    with pytest.raises(OSError, match="boom"):
        ks.write_bundle(tmp_path, files2, lock2)
    monkeypatch.undo()
    assert _snapshot(tmp_path) == before
    assert not (tmp_path / ".speclino/specs/billing.md").exists()


def test_sync_removes_only_previously_locked_skill_dirs(tmp_path: Path) -> None:
    files, lock, _ = _payload([_skill("old", files={"x.md": "X"}), *BASE])
    ks.write_bundle(tmp_path, files, lock)
    # 利用者が自分で置いたスキルと、同期済みのスキルに足したファイル
    (tmp_path / ".claude/skills/mine").mkdir()
    (tmp_path / ".claude/skills/mine/SKILL.md").write_text("mine")
    (tmp_path / ".agents/skills/old/notes.md").write_text("user")

    files2, lock2, _ = _payload(BASE, 2)
    ks.write_bundle(tmp_path, files2, lock2)
    assert not (tmp_path / ".claude/skills/old").exists()
    assert (tmp_path / ".claude/skills/mine/SKILL.md").read_text() == "mine"
    # 前の lock に無いファイルは消さない（ディレクトリも残る）
    assert (tmp_path / ".agents/skills/old/notes.md").read_text() == "user"
    assert not (tmp_path / ".agents/skills/old/SKILL.md").exists()
    assert (tmp_path / ".claude/skills/review/SKILL.md").exists()


def test_sync_ignores_unsafe_paths_in_previous_lock(tmp_path: Path) -> None:
    (tmp_path / "src").mkdir()
    (tmp_path / "src/app.py").write_text("code")
    files, lock, _ = _payload(BASE)
    ks.write_bundle(tmp_path, files, lock)
    forged = json.loads((tmp_path / ks.LOCK_PATH).read_text())
    forged["files"]["src/app.py"] = ks.file_hash("code")
    (tmp_path / ks.LOCK_PATH).write_text(json.dumps(forged))
    files2, lock2, _ = _payload(BASE, 2)
    ks.write_bundle(tmp_path, files2, lock2)
    assert (tmp_path / "src/app.py").read_text() == "code"


def test_sync_removes_managed_block_when_instructions_gone(tmp_path: Path) -> None:
    (tmp_path / "AGENTS.md").write_text("# 手書き\n")
    files, lock, _ = _payload(BASE)
    ks.write_bundle(tmp_path, files, lock)
    files2, lock2, _ = _payload([BASE[0], BASE[2]], 2)
    ks.write_bundle(tmp_path, files2, lock2)
    assert (tmp_path / "AGENTS.md").read_text() == "# 手書き\n\n"


# ---------------------------------------------------------------------------
# check_lock
# ---------------------------------------------------------------------------


def _synced(tmp_path: Path, revs: list[ArtifactRevision] = BASE) -> tuple[str, Bundle]:
    files, lock, bundle = _payload(revs)
    ks.write_bundle(tmp_path, files, lock)
    return (tmp_path / ks.LOCK_PATH).read_text(), bundle


def test_lock_missing_is_unknown(tmp_path: Path) -> None:
    _, bundle = _payload(BASE)[1:]
    f = ks.check_lock(tmp_path, None, current=bundle, now=NOW)
    assert f.verdict == "UNKNOWN" and f.severity == "high"


def test_lock_bad_signature_is_unknown(tmp_path: Path) -> None:
    lock_json, bundle = _synced(tmp_path)
    data = json.loads(lock_json)
    data["bundle_generation"] = 1
    data["signature"] = "hmac-sha256:" + "0" * 64
    f = ks.check_lock(tmp_path, json.dumps(data), current=bundle, now=NOW)
    assert f.verdict == "UNKNOWN" and "署名" in f.message
    garbage = ks.check_lock(tmp_path, "{not json", current=bundle, now=NOW)
    assert garbage.verdict == "UNKNOWN"


def test_lock_expired_is_unknown(tmp_path: Path) -> None:
    lock_json, bundle = _synced(tmp_path)
    f = ks.check_lock(tmp_path, lock_json, current=bundle, now=NOW + timedelta(days=31))
    assert f.verdict == "UNKNOWN" and "期限" in f.message


def test_lock_stale_is_review(tmp_path: Path) -> None:
    lock_json, _ = _synced(tmp_path)
    newer = [_skill("review", "改訂\n"), *BASE[1:]]
    current = _bundle(newer, generation=2)
    f = ks.check_lock(tmp_path, lock_json, current=current, now=NOW)
    assert f.verdict == "REVIEW" and f.category == "stale_approval"
    # このプロジェクトのメンバーが同じなら（別のプロジェクトだけが変わった）古くない
    lock = KnowledgeLock.model_validate_json(lock_json)
    same = ks.check_lock(
        tmp_path, lock_json, current=current, current_members=lock.members, now=NOW
    )
    assert same.verdict == "VERIFIED_IN_SCOPE"
    changed = ks.check_lock(
        tmp_path, lock_json, current=current, current_members=current.members, now=NOW
    )
    assert changed.verdict == "REVIEW"


def test_lock_tampered_file_is_unknown(tmp_path: Path) -> None:
    lock_json, bundle = _synced(tmp_path)
    (tmp_path / ".claude/skills/review/SKILL.md").write_text("改ざん")
    f = ks.check_lock(tmp_path, lock_json, current=bundle, now=NOW)
    assert f.verdict == "UNKNOWN"
    assert f.actual is not None and ".claude/skills/review/SKILL.md" in f.actual


def test_lock_tampered_managed_block_is_unknown(tmp_path: Path) -> None:
    lock_json, bundle = _synced(tmp_path)
    agents = tmp_path / "AGENTS.md"
    # 区画の外を書き換えても一致のまま
    agents.write_text("# 手書き\n" + agents.read_text())
    assert ks.check_lock(tmp_path, lock_json, current=bundle, now=NOW).verdict == (
        "VERIFIED_IN_SCOPE"
    )
    agents.write_text(agents.read_text().replace("共通の指示", "勝手な指示"))
    assert ks.check_lock(tmp_path, lock_json, current=bundle, now=NOW).verdict == "UNKNOWN"
    agents.write_text("# 区画を消した\n")
    assert ks.check_lock(tmp_path, lock_json, current=bundle, now=NOW).verdict == "UNKNOWN"


def test_lock_missing_file_is_unknown(tmp_path: Path) -> None:
    lock_json, bundle = _synced(tmp_path)
    (tmp_path / ".speclino/specs/orders.md").unlink()
    assert ks.check_lock(tmp_path, lock_json, current=bundle, now=NOW).verdict == "UNKNOWN"


def test_lock_other_workspace_or_repo_is_unknown(tmp_path: Path) -> None:
    lock_json, bundle = _synced(tmp_path)
    other = bundle.model_copy(update={"workspace": "other"})
    assert ks.check_lock(tmp_path, lock_json, current=other, now=NOW).verdict == "UNKNOWN"
    wrong_repo = ks.check_lock(tmp_path, lock_json, current=bundle, github_repo_id=7, now=NOW)
    assert wrong_repo.verdict == "UNKNOWN"


def test_lock_newer_than_current_is_unknown(tmp_path: Path) -> None:
    files, lock, _ = _payload(BASE, generation=3)
    ks.write_bundle(tmp_path, files, lock)
    current = _bundle(BASE, generation=2)
    lock_json = (tmp_path / ks.LOCK_PATH).read_text()
    assert ks.check_lock(tmp_path, lock_json, current=current, now=NOW).verdict == "UNKNOWN"


def test_check_lock_snapshot_reads_lock_from_snapshot(tmp_path: Path) -> None:
    _, bundle = _synced(tmp_path)
    snap = cast(Snapshot, DirectorySnapshot(tmp_path, snapshot_id="head"))
    f = ks.check_lock_snapshot(snap, current=bundle, now=NOW)
    assert f.verdict == "VERIFIED_IN_SCOPE"
    assert f.code_refs[0].snapshot_id == "head"


def test_lock_ignores_crlf_checkout(tmp_path: Path) -> None:
    lock_json, bundle = _synced(tmp_path)
    p = tmp_path / ".speclino/specs/orders.md"
    p.write_bytes(p.read_bytes().replace(b"\n", b"\r\n"))
    assert ks.check_lock(tmp_path, lock_json, current=bundle, now=NOW).verdict == (
        "VERIFIED_IN_SCOPE"
    )
