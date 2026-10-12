"""チームの知識（skill / instructions / spec）の改訂・承認と bundle（S62）。

design/2026-10-05-team-knowledge-registry.md 3 章。承認の流れは `policy_governance.py` と同じ:

- 改訂操作はすべてワークスペース行のロック（`TeamRepository.ensure_and_lock`）で直列化する
- 承認できるのは owner / approver（`TeamRepository.list_approvers`）。拒否は監査に残す
  （`audit_factory` があれば独立トランザクションで。呼び出し側の rollback で消さない）
- 承認者が見た改訂（番号と content_hash）でなければ `DraftConflict`（規約と同じ例外）
- 作者本人（と下書きを直した人）の承認は認め、`self_approved` を改訂と監査に残す
- 承認・規約の有効化のたびに、同じトランザクションで新しい bundle を作る（`build_bundle`）。
  競合があれば `BundleConflict` で承認ごと拒否する（呼び出し側が rollback する）
- 取り下げ（`retire`）は active な改訂を superseded にして bundle から外す。行は消さない
  （過去の bundle は不変で、その改訂を指したまま再現できる）。新しい改訂を承認すれば戻る

呼び出し側は `async with session_factory() as s, s.begin(): ...` で 1 トランザクションに包む。
"""

from __future__ import annotations

import json
import re
import uuid
from collections import defaultdict
from collections.abc import Callable
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from speclino.conformance.expected import obligation_levels
from speclino.db.models import Project
from speclino.db.repositories.artifacts import ArtifactRepository
from speclino.db.repositories.audit import AuditRepository
from speclino.db.repositories.bundles import BundleRepository
from speclino.db.repositories.policy_revisions import PolicyRevisionRepository
from speclino.db.repositories.teams import TeamRepository
from speclino.domain.domain_spec import DomainSpec
from speclino.domain.knowledge import (
    ArtifactContent,
    ArtifactKind,
    ArtifactRevision,
    ArtifactScope,
    Bundle,
    BundleMember,
    ResolvedKnowledge,
    SkillContent,
    SpecContent,
)
from speclino.domain.knowledge_chat import NoteSource
from speclino.domain.knowledge_view import ArtifactSummary
from speclino.domain.spec_ir import SpecIR
from speclino.domain.uml import ParseResult, SpecDiagnostic
from speclino.services import bundle_signing
from speclino.services.check_pipeline import policy_package_hash
from speclino.services.policy_governance import DraftConflict, note_hash
from speclino.services.workspaces import WorkspaceNotFound, record_denial
from speclino.spec.loader import content_hash as _sha256
from speclino.spec.uml import compile_spec

_DRAFT = "knowledge.draft"
_APPROVE = "knowledge.approve"
_RETIRE = "knowledge.retire"
_FRONTMATTER = re.compile(r"\A---\r?\n(.*?)\r?\n---", re.DOTALL)
_FM_NAME = re.compile(r"^name:\s*(.+?)\s*$", re.MULTILINE)


SpecCompiler = Callable[[str, DomainSpec | None, list[SpecIR]], ParseResult]
"""spec の本文（Markdown・domain・旧形式の IR）から IR を作る（S63 の `compile_spec`）。"""


def unavailable_compiler(
    markdown: str, domain: DomainSpec | None, legacy: list[SpecIR]
) -> ParseResult:
    """parser の無い compiler。常にエラーにして spec の承認を拒否する（S65 より前の既定）。"""
    diag = SpecDiagnostic(
        code="E_PARSER_UNAVAILABLE", severity="error", message="仕様の変換器がまだ無い"
    )
    return ParseResult(compiled=None, diagnostics=[diag])


def uml_compiler(markdown: str, domain: DomainSpec | None, legacy: list[SpecIR]) -> ParseResult:
    """S63 の決定的な parser（`compile_spec`）を `SpecCompiler` の形にしたもの（承認の既定）。"""
    return compile_spec(markdown, domain=domain, legacy_ir=legacy)


class ArtifactNotFound(LookupError):
    """artifact が無い、または別のワークスペースのもの（呼び出し側は 404）。"""


class ArtifactExists(ValueError):
    """同じワークスペース・kind・名前の artifact が既にある。"""


class InvalidArtifact(ValueError):
    """kind と内容が合わない、スコープのプロジェクトがワークスペースに属さない、など。"""


class SpecNotCompilable(ValueError):
    """spec の本文をサーバで変換したら診断にエラーが残った（承認できない）。"""


class BundleConflict(ValueError):
    """bundle を作れない（スコープの重なる競合、スコープのプロジェクトの所属が変わった）。"""


class NotActive(ValueError):
    """取り下げる有効な改訂が無い（未承認、または取り下げ済み）。"""


class BundleNotFound(LookupError):
    """指定の世代の bundle が無い。"""


def canonical_json(obj: Any) -> bytes:
    """署名・ハッシュの対象の正規 JSON。"""
    return json.dumps(obj, sort_keys=True, separators=(",", ":")).encode()


def content_hash(content: ArtifactContent) -> str:
    """改訂の内容のハッシュ（承認者が見た内容の照合と bundle のメンバーに使う）。"""
    return _sha256(canonical_json(content.model_dump(mode="json")))


def bundle_payload(bundle: Bundle) -> bytes:
    """bundle の署名の対象（`signature` 以外のすべて。`created_at` も含む）。

    後から足した `key_id` は値があるときだけ含める（欄の無い古い bundle の署名を保つ）。
    """
    data = bundle.model_dump(mode="json", exclude={"signature"})
    if data.get("key_id") is None:
        data.pop("key_id", None)
    return canonical_json(data)


def verify_bundle(bundle: Bundle) -> bool:
    return bundle_signing.verify(bundle_payload(bundle), bundle.signature, bundle.key_id)


def _reject_client_derived(content: ArtifactContent) -> None:
    """下書きに入れてよいのは人が書く入力だけ。IR（`compiled`）は承認時にサーバが本文から作る。

    クライアントの IR を受け取ると、人が読んだ本文と違う IR を承認させられる（黙って捨てずに拒否）。
    """
    if isinstance(content, SpecContent) and content.compiled is not None:
        raise InvalidArtifact("compiled はサーバが承認時に作る。下書きに含めない")


def _target(workspace: str, artifact_id: str, revision: int) -> str:
    return f"{workspace}/{artifact_id}@{revision}"


def _parse_id(artifact_id: str) -> uuid.UUID:
    try:
        return uuid.UUID(artifact_id)
    except ValueError as e:
        raise ArtifactNotFound(artifact_id) from e


async def _scope_problems(
    session: AsyncSession, workspace: str, revisions: list[tuple[str, ArtifactScope]]
) -> list[str]:
    """スコープに挙げたプロジェクトが workspace に属しているか。(名前, scope) ごとの問題を返す。"""
    wanted = {p: _canonical_project_id(p) for _, scope in revisions for p in scope.projects}
    ids = [u for u in wanted.values() if u is not None]
    teams: dict[uuid.UUID, str | None] = {}
    if ids:
        stmt = select(Project.id, Project.team).where(Project.id.in_(ids))
        teams = {pid: team for pid, team in (await session.execute(stmt)).all()}
    problems = []
    for name, scope in revisions:
        for p in scope.projects:
            pid = wanted[p]
            if pid is None or teams.get(pid) != workspace:
                problems.append(f"{name}: プロジェクト {p} はワークスペース {workspace} に属さない")
    return problems


def _canonical_project_id(p: str) -> uuid.UUID | None:
    """正規形（小文字・ハイフン区切り）の UUID 文字列だけを受け付ける。

    `uuid.UUID` は大文字・ハイフン無し・`urn:uuid:`・波括弧も読むので、同じプロジェクトを
    別の綴りでスコープに書けてしまい、スコープの重なりの判定（文字列比較）をすり抜ける。
    """
    try:
        u = uuid.UUID(p)
    except ValueError:
        return None
    return u if str(u) == p else None


def _normalized_projects(scope: ArtifactScope) -> set[str]:
    """比較用のプロジェクト ID（防御的に正規化する。読めない ID はそのまま）。"""
    out = set()
    for p in scope.projects:
        try:
            out.add(str(uuid.UUID(p)))
        except ValueError:
            out.add(p)
    return out


async def _lock_workspace(session: AsyncSession, workspace: str) -> TeamRepository:
    teams = TeamRepository(session)
    if await teams.get(workspace) is None:
        raise WorkspaceNotFound(workspace)
    await teams.ensure_and_lock(workspace)
    return teams


async def create_draft(
    session: AsyncSession,
    *,
    workspace: str,
    kind: ArtifactKind,
    name: str,
    content: ArtifactContent,
    author: str,
    note: str = "",
    note_source: NoteSource = "manual",
) -> ArtifactRevision:
    """新しい artifact と、その改訂 1（draft）を作る。同じ名前があれば `ArtifactExists`。

    `note` は改訂の説明（S73）。`note_source` は本人の申告（会話の要約から入れたか）で、監査に
    残すだけ（サーバは由来を証明しない）。
    """
    if content.kind != kind:
        raise InvalidArtifact(f"kind {kind} と内容の kind {content.kind} が違う")
    _reject_client_derived(content)
    await _lock_workspace(session, workspace)
    problems = await _scope_problems(session, workspace, [(name, content.scope)])
    if problems:
        raise InvalidArtifact("; ".join(problems))
    repo = ArtifactRepository(session)
    if await repo.find(workspace, kind, name) is not None:
        raise ArtifactExists(f"{workspace} に {kind} {name} は既にある")
    artifact = await repo.create(workspace=workspace, kind=kind, name=name)
    revision = await repo.add_draft(
        artifact_id=artifact.id,
        revision=1,
        content=content.model_dump(mode="json"),
        content_hash=content_hash(content),
        author=author,
        note=note,
        note_source=note_source,
    )
    await AuditRepository(session).record(
        author,
        _DRAFT,
        _target(workspace, revision.artifact_id, 1),
        "created",
        {
            "kind": kind,
            "name": name,
            "content_hash": revision.content_hash,
            "note_source": note_source,
        },
    )
    return revision


async def update_draft(
    session: AsyncSession,
    *,
    artifact_id: str,
    expected_revision: int,
    expected_hash: str,
    content: ArtifactContent,
    author: str,
    note: str = "",
    note_source: NoteSource = "manual",
) -> ArtifactRevision:
    """下書きを保存する。下書きがあれば置き換え、無ければ最新の改訂の次の番号で作る。

    `note`（改訂の説明、S73）は内容と同じ照合の中で一緒に置き換える。

    `expected_revision` / `expected_hash` は保存する人が前提にした改訂の番号と content_hash
    （下書きがあればその下書き、無ければ今の最新＝active）。違えば `DraftConflict`（古い画面から
    他の人の変更を後勝ちで上書きしない）。
    """
    aid = _parse_id(artifact_id)
    repo = ArtifactRepository(session)
    artifact = await repo.get(aid)
    if artifact is None:
        raise ArtifactNotFound(artifact_id)
    if content.kind != artifact.kind:
        raise InvalidArtifact(f"kind {artifact.kind} の artifact に {content.kind} は保存できない")
    _reject_client_derived(content)
    await _lock_workspace(session, artifact.workspace)
    problems = await _scope_problems(session, artifact.workspace, [(artifact.name, content.scope)])
    if problems:
        raise InvalidArtifact("; ".join(problems))
    data, digest = content.model_dump(mode="json"), content_hash(content)
    draft = await repo.current_draft(aid)
    if draft is not None:
        if draft.revision != expected_revision or draft.content_hash != expected_hash:
            raise DraftConflict(f"{artifact_id} の下書きが変わった。読み直してから保存する")
        saved = await repo.update_draft(
            artifact_id=aid,
            revision=draft.revision,
            content=data,
            content_hash=digest,
            note=note,
            note_source=note_source,
        )
        decision = "updated"
    else:
        latest = await repo.latest(aid)
        if (
            latest is None
            or latest.revision != expected_revision
            or latest.content_hash != expected_hash
        ):
            raise DraftConflict(f"{artifact_id} の改訂が変わった。読み直してから保存する")
        saved = await repo.add_draft(
            artifact_id=aid,
            revision=latest.revision + 1,
            content=data,
            content_hash=digest,
            author=author,
            note=note,
            note_source=note_source,
        )
        decision = "created"
    if saved is None:
        raise DraftConflict(f"{artifact_id} の下書きが承認された。読み直してから保存する")
    await AuditRepository(session).record(
        author,
        _DRAFT,
        _target(artifact.workspace, artifact_id, saved.revision),
        decision,
        {
            "kind": artifact.kind,
            "name": artifact.name,
            "content_hash": saved.content_hash,
            "note_source": note_source,
        },
    )
    return saved


async def approve(
    session: AsyncSession,
    *,
    artifact_id: str,
    expected_revision: int,
    expected_hash: str,
    approver: str,
    audit_factory: async_sessionmaker[AsyncSession] | None = None,
    compiler: SpecCompiler = uml_compiler,
    expected_note_hash: str | None = None,
) -> Bundle:
    """承認者が見た下書き（番号と content_hash）を承認し、新しい bundle を返す。

    ワークスペース行のロック → 承認者の確認 → 見た内容の照合 → active 切替 → bundle の作成
    （競合の検査）→ 監査を、呼び出し側の 1 トランザクションで行う。例外のときは呼び出し側が
    rollback する（拒否の監査は `audit_factory` で別に確定させる）。

    spec は保存済みの本文から `compiler`（既定は S63 の parser）で IR を作り、それを入れた内容で
    active にする。
    承認者が照合する `expected_hash` は下書き（IR なし）の hash で、active 行の hash は IR 込みで
    計算し直す（監査に両方残す）。変換にエラーがあれば `SpecNotCompilable`。
    """
    aid = _parse_id(artifact_id)
    repo = ArtifactRepository(session)
    artifact = await repo.get(aid)
    if artifact is None:
        raise ArtifactNotFound(artifact_id)
    workspace = artifact.workspace
    teams = await _lock_workspace(session, workspace)
    draft = await repo.current_draft(aid)
    if approver not in set(await teams.list_approvers(workspace)):
        # 監査の対象は今の下書き（送られた番号で監査を埋められないように）
        await record_denial(
            session,
            audit_factory,
            actor=approver,
            action=_APPROVE,
            target=_target(workspace, artifact_id, draft.revision if draft else 0),
            detail={"reason": "not_an_approver"},
        )
        raise PermissionError(f"{approver} は {workspace} の承認者ではない")
    if (
        draft is None
        or draft.revision != expected_revision
        or draft.content_hash != expected_hash
        # S73: 説明（note）は content_hash に入らないので、見た説明のハッシュも照合する（任意）
        or (expected_note_hash is not None and note_hash(draft.note) != expected_note_hash)
    ):
        raise DraftConflict(f"{artifact_id}@{expected_revision} は承認者が見た下書きではない")
    final = _compile(artifact.name, draft.content, compiler)
    final_hash = content_hash(final)
    target = _target(workspace, artifact_id, draft.revision)
    editors = await AuditRepository(session).actors_for(target, _DRAFT)
    self_approved = approver == draft.author or approver in editors
    approved = await repo.approve(
        artifact_id=aid,
        revision=draft.revision,
        approver=approver,
        self_approved=self_approved,
        content=final.model_dump(mode="json"),
        content_hash=final_hash,
    )
    if approved is None:
        raise DraftConflict(f"{artifact_id}@{expected_revision} は並行する承認で状態が変わった")
    bundle = await build_bundle(session, workspace)
    await AuditRepository(session).record(
        approver,
        _APPROVE,
        target,
        "approved",
        {
            "author": draft.author,
            "self_approved": self_approved,
            "draft_hash": draft.content_hash,
            "content_hash": final_hash,
            "bundle_generation": bundle.generation,
        },
    )
    return bundle


async def retire(
    session: AsyncSession,
    *,
    artifact_id: str,
    expected_revision: int,
    expected_hash: str,
    actor: str,
    reason: str = "",
    audit_factory: async_sessionmaker[AsyncSession] | None = None,
) -> tuple[ArtifactRevision, Bundle]:
    """有効な改訂（見た番号と content_hash）を取り下げ、それを外した新しい bundle を返す。

    承認と同じく owner / approver だけができ、ワークスペース行のロックの下で 1 トランザクションで
    行う。有効な改訂が無ければ `NotActive`、見た改訂と違えば `DraftConflict`。下書きは残す
    （承認すれば戻る）。
    """
    aid = _parse_id(artifact_id)
    repo = ArtifactRepository(session)
    artifact = await repo.get(aid)
    if artifact is None:
        raise ArtifactNotFound(artifact_id)
    workspace = artifact.workspace
    teams = await _lock_workspace(session, workspace)
    active = await repo.current_active(aid)
    if actor not in set(await teams.list_approvers(workspace)):
        await record_denial(
            session,
            audit_factory,
            actor=actor,
            action=_RETIRE,
            target=_target(workspace, artifact_id, active.revision if active else 0),
            detail={"reason": "not_an_approver"},
        )
        raise PermissionError(f"{actor} は {workspace} の承認者ではない")
    if active is None:
        raise NotActive(f"{artifact_id} に有効な改訂が無い")
    if active.revision != expected_revision or active.content_hash != expected_hash:
        raise DraftConflict(f"{artifact_id}@{expected_revision} は今の有効な改訂ではない")
    retired = await repo.retire(artifact_id=aid, revision=active.revision)
    if retired is None:
        raise DraftConflict(f"{artifact_id}@{expected_revision} は並行する操作で状態が変わった")
    bundle = await build_bundle(session, workspace)
    await AuditRepository(session).record(
        actor,
        _RETIRE,
        _target(workspace, artifact_id, retired.revision),
        "retired",
        {
            "kind": artifact.kind,
            "name": artifact.name,
            "content_hash": retired.content_hash,
            "reason": reason,
            "bundle_generation": bundle.generation,
        },
    )
    return retired, bundle


def _compile(name: str, content: ArtifactContent, compiler: SpecCompiler) -> ArtifactContent:
    """active にする内容。spec は本文から IR を作って入れる（下書きの値は使わない）。"""
    if not isinstance(content, SpecContent):
        return content
    result = compiler(content.markdown, content.domain, list(content.legacy_ir))
    errors = [d for d in result.diagnostics if d.severity == "error"]
    if errors or result.compiled is None:
        detail = ", ".join(f"{d.code}: {d.message}" for d in errors) or "変換結果が無い"
        raise SpecNotCompilable(f"{name}: 仕様の変換にエラーがある（{detail}）")
    data = content.model_dump(mode="json")
    data["compiled"] = result.compiled.model_dump(mode="json")
    return SpecContent.model_validate(data)


def skill_dir_name(rev: ArtifactRevision) -> str:
    """書き出し先のスキル名: SKILL.md の frontmatter の `name`、無ければ artifact 名。"""
    assert isinstance(rev.content, SkillContent)
    fm = _FRONTMATTER.match(rev.content.skill_md)
    found = _FM_NAME.search(fm.group(1)) if fm else None
    return found.group(1).strip("'\"") if found else rev.name


def _requirement_ids(rev: ArtifactRevision) -> set[str]:
    """承認時にサーバが作った IR（active 行の `compiled`）の要求 ID だけを使う。"""
    assert isinstance(rev.content, SpecContent)
    compiled = rev.content.compiled
    if compiled is None:
        return set()
    return {r.id for r in compiled.requirements} | {
        r.id for ir in compiled.legacy for r in ir.rules
    }


def _overlaps(a: ArtifactScope, b: ArtifactScope) -> bool:
    return a.workspace or b.workspace or bool(_normalized_projects(a) & _normalized_projects(b))


def _conflicts(actives: list[ArtifactRevision]) -> list[str]:
    """同じスコープに当たる同名のスキル・同じ要求 ID の仕様。

    指示書は競合させない。書き出し先は 1 つのファイルで、ワークスペース全体 → プロジェクト、
    それぞれ名前順に連結する（`knowledge_sync.render_files`）。
    """
    groups: dict[str, list[ArtifactRevision]] = defaultdict(list)
    for rev in actives:
        c = rev.content
        if c.kind == "instructions":
            continue
        if c.kind == "skill":
            groups[f"スキル名 {skill_dir_name(rev).casefold()}"].append(rev)
        else:
            for rid in _requirement_ids(rev):
                groups[f"要求 ID {rid}"].append(rev)
    problems = []
    for label, revs in sorted(groups.items()):
        for i, a in enumerate(revs):
            for b in revs[i + 1 :]:
                if _overlaps(a.content.scope, b.content.scope):
                    problems.append(f"{label} が {a.name} と {b.name} でスコープが重なる")
    return problems


async def build_bundle(session: AsyncSession, workspace: str) -> Bundle:
    """workspace の今の active な改訂と規約から、新しい世代の bundle を作って署名・保存する。

    承認（`approve`）と規約の有効化（`policy_governance._approve`）から、同じトランザクションの
    中で呼ぶ。ワークスペース行のロックを取る（呼び出し側が取っていても同じ行なので問題ない）。
    スコープのプロジェクトの所属と競合をここで確かめ、問題があれば `BundleConflict`。
    """
    await TeamRepository(session).ensure_and_lock(workspace)
    actives = await ArtifactRepository(session).active_in_workspace(workspace)
    problems = await _scope_problems(
        session, workspace, [(r.name, r.content.scope) for r in actives]
    )
    problems += _conflicts(actives)
    if problems:
        raise BundleConflict("; ".join(problems))
    policy = await PolicyRevisionRepository(session).active_revision(workspace)
    bundles = BundleRepository(session)
    unsigned = Bundle(
        workspace=workspace,
        generation=await bundles.next_generation(workspace),
        policy_revision=policy.revision if policy else None,
        policy_hash=policy_package_hash(policy.package) if policy else None,
        members=[
            BundleMember(
                artifact_id=r.artifact_id,
                kind=r.kind,
                name=r.name,
                revision=r.revision,
                content_hash=r.content_hash,
            )
            for r in actives
        ],
        scope_hash=_sha256(
            canonical_json(
                {r.artifact_id: r.content.scope.model_dump(mode="json") for r in actives}
            )
        ),
        key_id=bundle_signing.current_key_id(),
        signature="unsigned",
        created_at=datetime.now(UTC),
    )
    bundle = unsigned.model_copy(
        update={"signature": bundle_signing.sign(bundle_payload(unsigned))}
    )
    await bundles.add(bundle)
    return bundle


async def current_bundle(session: AsyncSession, workspace: str) -> Bundle | None:
    return await BundleRepository(session).latest(workspace)


async def ensure_bundle(session: AsyncSession, workspace: str) -> Bundle:
    """今の bundle。まだ無ければ（S62 より前からのワークスペース）ワークスペースのロックの下で作る。

    run の開始（S65）が使う。ロックを取ってから確かめ直すので、同時に呼ばれても 1 つだけ作る。
    """
    found = await current_bundle(session, workspace)
    if found is not None:
        return found
    await _lock_workspace(session, workspace)
    found = await current_bundle(session, workspace)
    return found if found is not None else await build_bundle(session, workspace)


async def get_bundle(session: AsyncSession, workspace: str, generation: int) -> Bundle:
    bundle = await BundleRepository(session).at(workspace, generation)
    if bundle is None:
        raise BundleNotFound(f"{workspace} の bundle {generation} は無い")
    return bundle


async def resolve_for_project(
    session: AsyncSession, bundle: Bundle, project_id: str
) -> ResolvedKnowledge:
    """bundle のうち project に当たる改訂と規約を返す（run はこれを開始時に 1 回だけ呼ぶ）。

    署名の合わない bundle・メンバーのハッシュが改訂と合わない bundle は `ValueError`。
    project が bundle のワークスペースに属さなければ `LookupError`。
    """
    if not verify_bundle(bundle):
        raise ValueError(f"{bundle.workspace} の bundle {bundle.generation} の署名が合わない")
    try:
        project = await session.get(Project, uuid.UUID(project_id))
    except ValueError:
        project = None
    if project is None or project.team != bundle.workspace:
        raise LookupError(f"プロジェクト {project_id} は {bundle.workspace} に属さない")
    repo = ArtifactRepository(session)
    by_kind: dict[str, list[ArtifactRevision]] = defaultdict(list)
    for m in bundle.members:
        rev = await repo.revision_at(uuid.UUID(m.artifact_id), m.revision)
        if rev is None or rev.content_hash != m.content_hash or rev.workspace != bundle.workspace:
            raise ValueError(f"bundle のメンバー {m.name}@{m.revision} が改訂と合わない")
        scope = rev.content.scope
        if scope.workspace or str(project.id) in _normalized_projects(scope):
            by_kind[rev.kind].append(rev)
    policy = None
    if bundle.policy_revision is not None:
        policy = await PolicyRevisionRepository(session).revision_at(
            bundle.workspace, bundle.policy_revision
        )
        if policy is None or policy_package_hash(policy.package) != bundle.policy_hash:
            raise ValueError(f"bundle の規約 {bundle.policy_revision} が改訂と合わない")
    return ResolvedKnowledge(
        bundle=bundle,
        skills=by_kind["skill"],
        instructions=by_kind["instructions"],
        specs=by_kind["spec"],
        policy=policy,
    )


async def requirement_artifacts(
    session: AsyncSession, workspace: str, generation: int, project_id: str
) -> dict[str, str]:
    """bundle の仕様のうち project に当たる改訂の、要求 ID → artifact ID。

    プロジェクト画面の要求の行から仕様のエディタへ移るためだけに使う（判定には使わない）。
    bundle や改訂が引けなければ、その分は対応なし（空）にする。
    """
    bundle = await BundleRepository(session).at(workspace, generation)
    if bundle is None:
        return {}
    repo = ArtifactRepository(session)
    found: dict[str, str] = {}
    for m in bundle.members:
        if m.kind != "spec":
            continue
        rev = await repo.revision_at(uuid.UUID(m.artifact_id), m.revision)
        if rev is None or rev.workspace != workspace or not isinstance(rev.content, SpecContent):
            continue
        scope = rev.content.scope
        if not (scope.workspace or project_id in _normalized_projects(scope)):
            continue
        compiled = rev.content.compiled
        ids = _requirement_ids(rev) | (set(obligation_levels(compiled)) if compiled else set())
        for rid in sorted(ids):
            found.setdefault(rid, rev.artifact_id)
    return found


async def list_artifacts(session: AsyncSession, workspace: str) -> list[ArtifactSummary]:
    repo = ArtifactRepository(session)
    numbers = await repo.revision_numbers(workspace)
    scopes = await repo.revision_scopes(workspace)
    return [
        ArtifactSummary(
            artifact_id=str(a.id),
            workspace=a.workspace,
            kind=a.kind,
            name=a.name,
            active_revision=numbers.get(a.id, {}).get("active"),
            draft_revision=numbers.get(a.id, {}).get("draft"),
            retired_revision=(
                None
                if "active" in numbers.get(a.id, {})
                else numbers.get(a.id, {}).get("superseded")
            ),
            scope=scopes.get(a.id, {}).get("active") or scopes.get(a.id, {}).get("draft"),
            created_at=a.created_at,
        )
        for a in await repo.list_for_workspace(workspace)
    ]


async def require_artifact(session: AsyncSession, workspace: str, artifact_id: str) -> None:
    """artifact が workspace のものでなければ `ArtifactNotFound`（別のワークスペースのものも）。"""
    artifact = await ArtifactRepository(session).get(_parse_id(artifact_id))
    if artifact is None or artifact.workspace != workspace:
        raise ArtifactNotFound(artifact_id)


async def current_draft(session: AsyncSession, artifact_id: str) -> ArtifactRevision | None:
    return await ArtifactRepository(session).current_draft(_parse_id(artifact_id))


async def list_revisions(session: AsyncSession, artifact_id: str) -> list[ArtifactRevision]:
    """新しい順。"""
    return await ArtifactRepository(session).list_revisions(_parse_id(artifact_id))


async def revision_at(session: AsyncSession, artifact_id: str, revision: int) -> ArtifactRevision:
    found = await ArtifactRepository(session).revision_at(_parse_id(artifact_id), revision)
    if found is None:
        raise ArtifactNotFound(f"{artifact_id}@{revision}")
    return found


__all__ = [
    "ArtifactExists",
    "ArtifactNotFound",
    "BundleConflict",
    "BundleNotFound",
    "InvalidArtifact",
    "NotActive",
    "SpecCompiler",
    "SpecNotCompilable",
    "uml_compiler",
    "unavailable_compiler",
    "approve",
    "build_bundle",
    "bundle_payload",
    "canonical_json",
    "content_hash",
    "create_draft",
    "current_bundle",
    "current_draft",
    "ensure_bundle",
    "get_bundle",
    "list_artifacts",
    "list_revisions",
    "require_artifact",
    "resolve_for_project",
    "retire",
    "revision_at",
    "skill_dir_name",
    "update_draft",
    "verify_bundle",
]
