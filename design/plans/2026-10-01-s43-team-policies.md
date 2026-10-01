# S43 チーム規約の画面 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** ワークスペースのチーム規約を画面で見て、下書きで変え、owner / approver の承認で有効にできるようにする（Issue #53）。

**Architecture:** 書き込みの判断は既存の `services/policy_governance.py`（チーム行のロックによる排他・承認者の検証・拒否の監査）を通す。足りない「下書きは同時に 1 つ（あれば更新）」「検証器の存在確認」「監査と改訂の一覧」を services と repositories に足し、`api/routes/policies.py` は呼ぶだけ。直近の成否は S45 の `services/project_overview.py` の組み立て（`_latest`）を各 project に使って規約ごとに集計する。画面は `web/src/services/policyDraft.ts` の純粋関数（ルールの編集の適用・差分）を vitest で試し、部品は `web/src/components/policies/`。

**Tech Stack:** Python 3.12 / FastAPI / SQLAlchemy async / pydantic、React 19 / Tailwind v4 / shadcn/ui、pytest（integration は実 PostgreSQL）、vitest。

**Spec:** `design/2026-09-30-ui-redesign.md` 6 章（4 章のルート `/w/:slug/policies`、8 章のテスト）。Issue #53 の完了条件。設計の背景: `design/2026-09-26-team-policies-and-dx.md`。

## Global Constraints

- 書き込みは承認フロー経由だけ: 下書きの作成・更新はメンバーなら誰でも、承認（有効にする）は owner / approver だけ。作成者本人の承認も認め、監査に残す
- 下書きは同時に 1 つ。あるときに保存すれば同じ下書きを更新する
- 承認者でない人の承認は拒否し（HTTP 403、`code: "not_an_approver"`）、拒否も監査に残す（呼び出し側の rollback で消えないよう `audit_factory` を渡す）
- メンバーでない人・存在しないワークスペースは 404（`workspace_not_found`）。存在を漏らさない
- 規約の中身は `domain/policy.py` の `PolicyPackage` / `PolicyRule` で検証する。検証器 ID は登録済みのもの（`services/check_pipeline.py` の `_policy_registry`）だけ
- 承認すると新しい有効な改訂になり、以後の検査の manifest にその番号が出る（既存の `resolve_team_policy` がそのまま使う）。過去の改訂と過去の判定は変えない
- 直近の成否は推測で埋めない: 結果の無い規約は「結果なし」、成果物を読めない project は数えない
- 型は `domain/`・`web/src/types/`、ロジックは `services/`、API と画面に置かない。`conformance/`・`analysis/` は変えない
- 画面に「デモ」と出さない。個人の環境の値を書かない。スマホ幅（390px）で横スクロールなし、ライト / ダーク
- 本番の操作（デプロイ・admin Job の `policy seed`・規約の承認・検査の起動）はユーザーの確認を取ってから行う

## Review Focus

1. 承認者でない人が承認 API を直接呼ぶ → 403、改訂は変わらず、拒否が監査に残る（画面でボタンを隠すだけにしない）
2. 2 人がほぼ同時に下書きを保存する → 下書きが 2 つにならない（同じ下書きの更新になる）
3. 下書きの承認の直前に別の人が下書きを更新した → 承認するのは承認者が見た内容。見た改訂番号と内容のハッシュの両方が今の下書きと一致しなければ 409 で拒否する（`expected_revision`・`expected_hash`。下書きの更新は同じ番号のまま中身を変えるので、番号だけでは見分けられない）
4. チーム規約がまだ無いワークスペース（本番の初期状態）→ 画面が壊れず「まだありません」と出て、最初の下書きを空から作れる
5. 編集で不正な値（重複したルール ID・コロンを含む ID・未登録の検証器・空の題）→ 422 で理由を返し、下書きは変わらない

---

## 設計上の判断（計画の時点で決めたこと）

- **下書きは upsert**: `PUT /api/workspaces/{slug}/policies/draft` は、下書きが無ければ作り、あれば同じ改訂を更新する（「同時に 1 つ」を API の形で保証する）。更新も `policy.draft`（`decision: "updated"`）として監査に残す
- **承認は見た改訂番号と内容のハッシュつき**: `POST /policies/draft/approve` は `{"expected_revision": n, "expected_hash": "sha256:…"}` を取る。ハッシュは `services/check_pipeline.py` の `policy_package_hash`（manifest の `team_policy_hash` と同じ計算）。今の下書きと違えば 409。見ていない内容を承認させない
- **承認者の検証が先**: 承認者でない人は、下書きの有無や番号に関係なく常に 403 で、拒否を監査に残す（番号の確認より前に検証する）
- **下書きは同時に 1 つをサービス全体で守る**: 既存の `policy_governance.create_draft` も、チーム行のロックの中で下書きがあれば `DraftExists` で拒否する（CLI など他の経路も含めて 2 つ目を作らせない）。リポジトリ層の `create_draft` は変えない
- **監査の一覧はワークスペースで完全に絞る**: target が `<slug>@<数字>` に完全に一致するものだけ（slug は `@` を含みうるので前方一致にしない）
- **下書きの破棄は作らない**: 仕様に無い。誤った下書きは編集で戻すか、承認せずに置く（YAGNI）
- **本番の初期化**: `policy seed` の取り込み元 `.speclino/policy/speclino-core.yaml` がコンテナに無いので、`Dockerfile` で `.speclino/policy/` をイメージに入れる。admin Job で `policy,seed,--team,<slug>` を実行できるようにする
- **⌘K に「規約」を足す**（段 1 で段 4 に回した分）。上部のバーにも「規約」を出す

---

### Task 1: 下書きの upsert・承認の番号確認・一覧（services / repositories）

**Files:**
- Modify: `src/speclino/db/repositories/policy_revisions.py`（`current_draft`・`update_draft`・`list_summaries` を追加）
- Modify: `src/speclino/db/repositories/audit.py`（`list_for_team` を追加）
- Create: `src/speclino/domain/policy_view.py`（画面用の型）
- Modify: `src/speclino/services/policy_governance.py`（`save_draft`・`approve_draft` を追加）
- Test: `tests/integration/services/test_policy_governance_ui.py`

**Interfaces:**
- Produces（domain）: `RevisionSummary(revision: int, status: PolicyRevisionStatus, author: str, approver: str | None, note: str, rule_count: int, created_at: datetime, approved_at: datetime | None)`、`AuditEntry(ts: datetime, actor: str, action: str, target: str, decision: str, detail: dict[str, Any])`
- Produces（repositories）: `PolicyRevisionRepository.current_draft(team) -> PolicyRevision | None`、`update_draft(team, revision, package, note) -> PolicyRevision`、`list_summaries(team, limit) -> list[RevisionSummary]`、`AuditRepository.list_for_team(team: str, actions: tuple[str, ...], limit: int) -> list[AuditEntry]`
- Produces（services）: `KNOWN_VERIFIERS: dict[str, str]`（ID → 版）、`class DraftConflict(Exception)`、`class DraftExists(ValueError)`、`class InvalidPackage(ValueError)`、`draft_hash(revision: PolicyRevision) -> str`、`save_draft(session, *, team, package, author, note) -> PolicyRevision`、`approve_draft(session, *, team, expected_revision, expected_hash, approver, audit_factory) -> PolicyRevision`

- [ ] **Step 1: domain の型を書く**（`src/speclino/domain/policy_view.py`、ロジックなし）

```python
"""チーム規約の画面（S43）の応答の型。組み立ては services/policy_governance.py と routes。"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from speclino.domain.common import IRModel
from speclino.domain.policy import PolicyRevisionStatus


class RevisionSummary(IRModel):
    revision: int
    status: PolicyRevisionStatus
    author: str
    approver: str | None
    note: str
    rule_count: int
    created_at: datetime
    approved_at: datetime | None


class AuditEntry(IRModel):
    ts: datetime
    actor: str
    action: str
    target: str
    decision: str
    detail: dict[str, Any]
```

- [ ] **Step 2: 失敗するテストを書く**（`tests/integration/services/test_policy_governance_ui.py`。`engine` は同じディレクトリの conftest の fixture）

```python
"""S43: 下書きの upsert・承認の番号確認・拒否の監査・一覧（services/policy_governance.py）。"""

from __future__ import annotations

import os
import uuid

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine, async_sessionmaker

from speclino.db.repositories.audit import AuditRepository
from speclino.db.repositories.policy_revisions import PolicyRevisionRepository
from speclino.domain.policy import PolicyApplicability, PolicyPackage, PolicyRule
from speclino.services import policy_governance as gov

pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(os.environ.get("SPECLINO_DATABASE_URL") is None, reason="DB 未設定"),
]


def _rule(rid: str = "DOC-001", level: str = "advisory", verifier: str | None = None) -> PolicyRule:
    """verifier は検証器 ID（登録済みなら版を引き、未登録なら版 "1"）。"""
    return PolicyRule.model_validate(
        {
            "id": rid,
            "version": "1.0.0",
            "level": level,
            "title": f"{rid} の題",
            "text": "本文",
            "applies_to": PolicyApplicability(paths=["docs/**"]).model_dump(),
            "verifier": {"id": verifier, "version": gov.KNOWN_VERIFIERS.get(verifier, "1")}
            if verifier
            else None,
            "on_missing_input": "unknown",
        }
    )


def _pkg(*rules: PolicyRule) -> PolicyPackage:
    return PolicyPackage(id="team", version="1", status="active", owner="o", rules=list(rules))


async def _workspace(engine: AsyncEngine) -> str:
    slug = f"t-pol-{uuid.uuid4().hex[:8]}"
    async with engine.begin() as c:
        await c.execute(text("INSERT INTO teams (team, approvers) VALUES (:t, '[]')"), {"t": slug})
        for uid, role in (("owner1", "owner"), ("member1", "member")):
            await c.execute(
                text("INSERT INTO workspace_members (team, uid, role) VALUES (:t, :u, :r)"),
                {"t": slug, "u": uid, "r": role},
            )
    return slug


async def test_save_draft_creates_then_updates_the_same_draft(engine: AsyncEngine) -> None:
    slug = await _workspace(engine)
    f = async_sessionmaker(engine, expire_on_commit=False)
    async with f() as s, s.begin():
        first = await gov.save_draft(
            s, team=slug, package=_pkg(_rule()), author="member1", note="追加"
        )
    async with f() as s, s.begin():
        second = await gov.save_draft(
            s,
            team=slug,
            package=_pkg(_rule(level="mandatory", verifier="spec_tests")),
            author="owner1",
            note="必須に",
        )
    assert second.revision == first.revision and second.status == "draft"
    assert second.package.rules[0].level == "mandatory"
    async with f() as s:
        drafts = [
            r for r in await PolicyRevisionRepository(s).list_revisions(slug) if r.status == "draft"
        ]
        assert len(drafts) == 1
        audit = await AuditRepository(s).list_for_team(slug, ("policy.draft",), 10)
        assert [a.decision for a in audit] == ["updated", "created"]


async def test_invalid_verifier_and_duplicate_ids_are_rejected(engine: AsyncEngine) -> None:
    slug = await _workspace(engine)
    f = async_sessionmaker(engine, expire_on_commit=False)
    for pkg in (_pkg(_rule(verifier="nope")), _pkg(_rule(), _rule())):
        with pytest.raises(gov.InvalidPackage):
            async with f() as s, s.begin():
                await gov.save_draft(s, team=slug, package=pkg, author="member1", note="")


async def test_non_approver_is_denied_and_the_denial_is_audited(engine: AsyncEngine) -> None:
    slug = await _workspace(engine)
    f = async_sessionmaker(engine, expire_on_commit=False)
    async with f() as s, s.begin():
        draft = await gov.save_draft(s, team=slug, package=_pkg(_rule()), author="member1", note="")
    with pytest.raises(PermissionError):
        async with f() as s, s.begin():
            await gov.approve_draft(
                s,
                team=slug,
                expected_revision=draft.revision + 9,  # 番号が違っても、承認者でなければ 403 が先
                expected_hash="sha256:stale",
                approver="member1",
                audit_factory=f,
            )
    async with f() as s:
        assert (await PolicyRevisionRepository(s).current_draft(slug)) is not None
        denied = await AuditRepository(s).list_for_team(slug, ("policy.approve",), 10)
        assert [a.decision for a in denied] == ["denied"]


async def test_approve_checks_the_revision_the_approver_saw(engine: AsyncEngine) -> None:
    slug = await _workspace(engine)
    f = async_sessionmaker(engine, expire_on_commit=False)
    async with f() as s, s.begin():
        draft = await gov.save_draft(s, team=slug, package=_pkg(_rule()), author="member1", note="")
    with pytest.raises(gov.DraftConflict):
        async with f() as s, s.begin():
            await gov.approve_draft(
                s,
                team=slug,
                expected_revision=draft.revision + 1,
                expected_hash=gov.draft_hash(draft),
                approver="owner1",
                audit_factory=f,
            )
    # 承認者が見た後で別の人が同じ下書きの中身を変えた → 見たハッシュとずれるので拒否
    seen = gov.draft_hash(draft)
    async with f() as s, s.begin():
        changed = await gov.save_draft(
            s, team=slug, package=_pkg(_rule(), _rule("DOC-002")), author="member1", note="追加"
        )
    with pytest.raises(gov.DraftConflict):
        async with f() as s, s.begin():
            await gov.approve_draft(
                s,
                team=slug,
                expected_revision=draft.revision,
                expected_hash=seen,
                approver="owner1",
                audit_factory=f,
            )
    async with f() as s, s.begin():
        active = await gov.approve_draft(
            s,
            team=slug,
            expected_revision=changed.revision,
            expected_hash=gov.draft_hash(changed),
            approver="owner1",
            audit_factory=f,
        )
    assert active.status == "active"
    async with f() as s:
        assert await PolicyRevisionRepository(s).current_draft(slug) is None
        [summary] = await PolicyRevisionRepository(s).list_summaries(slug, 20)
        assert (summary.status, summary.rule_count, summary.approver) == ("active", 2, "owner1")
        assert summary.approved_at is not None


async def test_author_can_approve_their_own_draft(engine: AsyncEngine) -> None:
    slug = await _workspace(engine)
    f = async_sessionmaker(engine, expire_on_commit=False)
    async with f() as s, s.begin():
        draft = await gov.save_draft(s, team=slug, package=_pkg(_rule()), author="owner1", note="")
        active = await gov.approve_draft(
            s,
            team=slug,
            expected_revision=draft.revision,
            expected_hash=gov.draft_hash(draft),
            approver="owner1",
            audit_factory=f,
        )
    assert active.author == active.approver == "owner1"
    async with f() as s:
        [approved] = await AuditRepository(s).list_for_team(slug, ("policy.approve",), 10)
        assert approved.detail["self_approved"] is True
```

同じファイルに足す（下書きは同時に 1 つ、監査はワークスペースで完全に絞る）:

```python
async def test_create_draft_refuses_a_second_draft(engine: AsyncEngine) -> None:
    slug = await _workspace(engine)
    f = async_sessionmaker(engine, expire_on_commit=False)
    async with f() as s, s.begin():
        await gov.create_draft(s, team=slug, package=_pkg(_rule()), author="member1")
    with pytest.raises(gov.DraftExists):
        async with f() as s, s.begin():
            await gov.create_draft(s, team=slug, package=_pkg(_rule()), author="owner1")


async def test_audit_does_not_leak_a_workspace_whose_slug_shares_the_prefix(
    engine: AsyncEngine,
) -> None:
    slug = await _workspace(engine)
    f = async_sessionmaker(engine, expire_on_commit=False)
    async with f() as s, s.begin():
        await AuditRepository(s).record(
            actor="x",
            action="policy.draft",
            target=f"{slug}@other@1",
            decision="created",
            detail={},
        )
        await gov.save_draft(s, team=slug, package=_pkg(_rule()), author="member1", note="")
    async with f() as s:
        rows = await AuditRepository(s).list_for_team(slug, ("policy.draft",), 10)
        assert [r.target for r in rows] == [f"{slug}@1"]
```

- [ ] **Step 3: 失敗を確かめる**

Run: `export SPECLINO_DATABASE_URL=postgresql+asyncpg://speclino:speclino@localhost:5432/speclino && make db-up && make db-migrate && UV_NO_SYNC=1 uv run pytest tests/integration/services/test_policy_governance_ui.py -q`
Expected: FAIL（`gov.KNOWN_VERIFIERS` が無い）

- [ ] **Step 4: repositories に足す**

`PolicyRevisionRepository`（`policy_revisions.py`）に:

```python
async def current_draft(self, team: str) -> PolicyRevision | None:
    """team の下書き（S43: 同時に 1 つ。複数あれば新しいもの）。"""
    stmt = (
        select(PolicyRevisionRecord)
        .where(PolicyRevisionRecord.team == team, PolicyRevisionRecord.status == "draft")
        .order_by(PolicyRevisionRecord.revision.desc())
        .limit(1)
    )
    row = (await self._session.execute(stmt)).scalar_one_or_none()
    return _to_domain(row) if row is not None else None


async def update_draft(
    self, *, team: str, revision: int, package: PolicyPackage, note: str
) -> PolicyRevision:
    """下書きの中身を置き換える。draft でなくなっていれば ValueError（承認と競合した）。"""
    stmt = (
        update(PolicyRevisionRecord)
        .where(
            PolicyRevisionRecord.team == team,
            PolicyRevisionRecord.revision == revision,
            PolicyRevisionRecord.status == "draft",
        )
        .values(package=package.model_dump(mode="json"), note=note)
        .returning(PolicyRevisionRecord)
    )
    row = (await self._session.execute(stmt)).scalar_one_or_none()
    if row is None:
        raise ValueError(f"{team}@{revision} は下書きではない")
    return _to_domain(row)


async def list_summaries(self, team: str, limit: int) -> list[RevisionSummary]:
    stmt = (
        select(PolicyRevisionRecord)
        .where(PolicyRevisionRecord.team == team)
        .order_by(PolicyRevisionRecord.revision.desc())
        .limit(limit)
    )
    return [
        RevisionSummary(
            revision=r.revision,
            status=r.status,
            author=r.author,
            approver=r.approver,
            note=r.note,
            rule_count=len(r.package.get("rules", [])),
            created_at=r.created_at,
            approved_at=r.approved_at,
        )
        for r in (await self._session.execute(stmt)).scalars()
    ]
```

（`update` と `RevisionSummary` の import を足す。`update` は同ファイルの `approve` が既に使っていれば既存の import を使う）

`AuditRepository`（`audit.py`。`func` を sqlalchemy から import）に:

```python
async def list_for_team(self, team: str, actions: tuple[str, ...], limit: int) -> list[AuditEntry]:
    """target が `<team>@<改訂番号>` に完全に一致し、action が actions のどれかの監査（新しい順）。

    slug は `@` を含みうるので前方一致だけでは別のワークスペース（`<team>@x@1`）を拾う。
    残りが数字だけであることも確かめる。
    """
    rest = func.substr(AuditEvent.target, len(team) + 2)
    stmt = (
        select(AuditEvent)
        .where(
            AuditEvent.target.startswith(f"{team}@", autoescape=True),
            rest.op("~")("^[0-9]+$"),
            AuditEvent.action.in_(actions),
        )
        .order_by(AuditEvent.ts.desc())
        .limit(limit)
    )
    return [
        AuditEntry(
            ts=e.ts,
            actor=e.actor,
            action=e.action,
            target=e.target,
            decision=e.decision,
            detail=e.detail,
        )
        for e in (await self._session.execute(stmt)).scalars()
    ]
```

- [ ] **Step 5: services に足す**（`policy_governance.py`。既存の関数は変えない）

```python
from speclino.verifiers.api_error_format import VERIFIER_ID as _API_ID, VERIFIER_VERSION as _API_V
from speclino.verifiers.ci_required import VERIFIER_ID as _CI_ID, VERIFIER_VERSION as _CI_V
from speclino.verifiers.make_targets import VERIFIER_ID as _MAKE_ID, VERIFIER_VERSION as _MAKE_V
from speclino.verifiers.spec_format import VERIFIER_ID as _SPEC_ID, VERIFIER_VERSION as _SPEC_V
from speclino.verifiers.spec_tests import VERIFIER_ID as _TESTS_ID, VERIFIER_VERSION as _TESTS_V

# 画面から選べる検証器（services/check_pipeline.py の _policy_registry と同じ集合）
KNOWN_VERIFIERS: dict[str, str] = {
    _MAKE_ID: _MAKE_V,
    _SPEC_ID: _SPEC_V,
    _TESTS_ID: _TESTS_V,
    _CI_ID: _CI_V,
    _API_ID: _API_V,
}


class InvalidPackage(ValueError):
    """下書きにできない規約（重複したルール ID・未登録の検証器）。"""


class DraftConflict(Exception):
    """承認しようとした下書きが、承認者の見た改訂ではない（別の人が先に更新・承認した）。"""


def _validate(package: PolicyPackage) -> None:
    ids = [r.id for r in package.rules]
    dup = sorted({i for i in ids if ids.count(i) > 1})
    if dup:
        raise InvalidPackage(f"ルール ID が重複している: {', '.join(dup)}")
    for r in package.rules:
        if r.verifier is not None and r.verifier.id not in KNOWN_VERIFIERS:
            raise InvalidPackage(f"{r.id}: 未登録の検証器 {r.verifier.id}")


async def save_draft(
    session: AsyncSession, *, team: str, package: PolicyPackage, author: str, note: str
) -> PolicyRevision:
    """下書きを保存する（S43）。無ければ作り、あれば同じ下書きを更新する（下書きは同時に 1 つ）。

    チーム行のロックを先に取るので、同時の保存・承認・seed と直列化される（create_draft と同じ）。
    """
    _validate(package)
    await TeamRepository(session).ensure_and_lock(team)
    revisions = PolicyRevisionRepository(session)
    current = await revisions.current_draft(team)
    if current is None:
        return await create_draft(session, team=team, package=package, author=author, note=note)
    updated = await revisions.update_draft(
        team=team, revision=current.revision, package=package, note=note
    )
    await AuditRepository(session).record(
        actor=author,
        action="policy.draft",
        target=f"{team}@{current.revision}",
        decision="updated",
        detail={"note": note, "rule_count": len(package.rules)},
    )
    return updated


def draft_hash(revision: PolicyRevision) -> str:
    """承認者が見た下書きの内容のハッシュ（manifest の team_policy_hash と同じ計算）。"""
    return policy_package_hash(revision.package)


async def approve_draft(
    session: AsyncSession,
    *,
    team: str,
    expected_revision: int,
    expected_hash: str,
    approver: str,
    audit_factory: async_sessionmaker[AsyncSession] | None,
) -> PolicyRevision:
    """承認者が見た下書き（番号と内容のハッシュ）を承認する（S43）。

    1. 承認者でなければ、下書きの有無や番号に関係なく拒否し、監査に残す（PermissionError）
    2. 今の下書きが見たもの（番号・ハッシュ）でなければ DraftConflict
    3. 承認は既存の `approve`（承認者の検証をもう一度通るが、結果は同じ）
    作成者本人の承認は認め、監査の detail に self_approved を残す（`_approve`）。
    """
    teams = TeamRepository(session)
    await teams.ensure_and_lock(team)
    if approver not in set(await teams.list_approvers(team)):
        await _record_denial(session, audit_factory, team, expected_revision, approver)
        raise PermissionError(f"{approver} は {team} の承認者ではない")
    current = await PolicyRevisionRepository(session).current_draft(team)
    if (
        current is None
        or current.revision != expected_revision
        or draft_hash(current) != expected_hash
    ):
        raise DraftConflict(f"{team}@{expected_revision} は承認者が見た下書きではない")
    return await approve(
        session,
        team=team,
        revision=expected_revision,
        approver=approver,
        audit_factory=audit_factory,
    )
```

`policy_package_hash` は `from speclino.services.check_pipeline import policy_package_hash` で使う（`check_pipeline` は `policy_governance` を import していないので循環しない。計画時に確認済み）。

既存の `create_draft`（サービス）の先頭のロックの直後に、下書きがあれば拒否する段落を足す:

```python
    if await PolicyRevisionRepository(session).current_draft(team) is not None:
        raise DraftExists(f"{team} には既に下書きがある。保存は save_draft（同じ下書きを更新）で行う")
```

`class DraftExists(ValueError)` を `InvalidPackage` の隣に置く。`save_draft` は下書きがあるとき `create_draft` を呼ばないので影響しない。既存のテストで同じチームに `policy_governance.create_draft` を 2 回呼ぶものがあれば、間に承認を挟む形に直す（リポジトリ層の `create_draft` を直接呼ぶテストは影響しない）。

作成者本人の承認を監査に残すため、既存の `_approve` の監査の `detail={"author": approved.author}` を `detail={"author": approved.author, "self_approved": approved.author == approver}` に変える（`test_author_can_approve_their_own_draft` が見る）。

`__all__` に `KNOWN_VERIFIERS`・`DraftConflict`・`DraftExists`・`InvalidPackage`・`draft_hash`・`save_draft`・`approve_draft` を足す。

- [ ] **Step 6: 通ることを確かめる**

Run: `UV_NO_SYNC=1 uv run pytest tests/integration/services/test_policy_governance_ui.py tests/integration/db/test_policy_revisions_repository.py -q && make lint typecheck`
Expected: PASS（新しい 7 件と既存の改訂のテスト）。既存の `policy.approve` の監査を見るテストが `detail` の完全一致で落ちたら、そのテストの期待値に `self_approved` を足す（意味は変わらない）

- [ ] **Step 7: コミット**

```bash
git add src/speclino/domain/policy_view.py src/speclino/db/repositories/policy_revisions.py src/speclino/db/repositories/audit.py src/speclino/services/policy_governance.py tests/integration
git commit -m "feat(api): チーム規約の下書きの upsert・承認の番号確認・一覧 (#53)"
```

---

### Task 2: 規約の API と直近の成否、本番の初期化の準備

**Files:**
- Create: `src/speclino/services/team_policies.py`（画面用の読み出しと成否の集計）
- Create: `src/speclino/api/routes/policies.py`
- Modify: `src/speclino/api/app.py`（`include_router(policies.router)`）
- Modify: `src/speclino/services/project_overview.py`（`_latest` を `latest_assembled` として公開する。中身は変えない）
- Modify: `Dockerfile`（`.speclino/policy/` をイメージに入れる）
- Modify: `openapi.json`（`make openapi`）
- Test: `tests/unit/services/test_team_policies.py`、`tests/integration/services/test_policies_api.py`

**Interfaces:**
- Consumes: Task 1 の `save_draft`・`approve_draft`・`KNOWN_VERIFIERS`・`InvalidPackage`・`DraftConflict`・`current_draft`・`list_summaries`・`list_for_team`、S45 の `PolicyView`・`worst_verdict`
- Produces: `aggregate_results(per_project: list[list[PolicyView]]) -> dict[str, RuleResult]`（`RuleResult(status: Verdict | None, projects: int, failing_projects: int)` を `domain/policy_view.py` に足す）、`load_policies(session, blobs, slug, uid) -> PoliciesView`（`PoliciesView(active: PolicyRevision | None, draft: PolicyRevision | None, draft_hash: str | None, can_approve: bool, verifiers: dict[str, str], results: dict[str, RuleResult])` を `domain/policy_view.py` に足す）。HTTP: `GET /api/workspaces/{slug}/policies`、`PUT .../policies/draft`（body `{"package": PolicyPackage, "note": str}`）、`POST .../policies/draft/approve`（body `{"expected_revision": int, "expected_hash": str}`）、`GET .../policies/revisions`、`GET .../policies/audit`

- [ ] **Step 1: 失敗するテストを書く**

`tests/unit/services/test_team_policies.py`:

```python
"""S43: ワークスペースの各 project の規約の結果を規約ごとに集計する。"""

from __future__ import annotations

from speclino.domain.finding import VerdictCounts
from speclino.domain.project_overview import PolicyView
from speclino.services.team_policies import aggregate_results


def _p(rule: str, status: str | None) -> PolicyView:
    return PolicyView(
        rule_id=rule,
        origin="team",
        title=None,
        level=None,
        has_verifier=None,
        status=status,  # type: ignore[arg-type]
        counts=VerdictCounts(),
    )


def test_worst_status_and_project_counts() -> None:
    results = aggregate_results(
        [[_p("A", "VERIFIED_IN_SCOPE"), _p("B", None)], [_p("A", "VIOLATED")]]
    )
    assert results["A"].status == "VIOLATED"
    assert (results["A"].projects, results["A"].failing_projects) == (2, 1)
    assert results["B"].status is None and results["B"].projects == 0


def test_no_projects_means_no_results() -> None:
    assert aggregate_results([]) == {}
```

`tests/integration/services/test_policies_api.py`（`tests/integration/services/test_projects_api.py` の `ctx` fixture と同じ作り。メンバーは `{"owner1": "owner", "member1": "member"}`）:

```python
async def test_non_member_gets_404(ctx: Ctx) -> None:
    c, slug, *_ = ctx
    pkg = {"id": "team", "version": "1", "status": "active", "owner": "o", "rules": [RULE]}
    bodies = {
        "/draft": {"package": pkg, "note": ""},
        "/draft/approve": {"expected_revision": 1, "expected_hash": "sha256:x"},
    }
    for method, path in (
        ("GET", ""),
        ("GET", "/revisions"),
        ("GET", "/audit"),
        ("PUT", "/draft"),
        ("POST", "/draft/approve"),
    ):
        res = await c.request(
            method,
            f"/api/workspaces/{slug}/policies{path}",
            json=bodies.get(path),
            headers={"X-Dev-User": "stranger"},
        )
        assert res.status_code == 404, (method, path, res.text)


async def test_empty_workspace_has_no_policies(ctx: Ctx) -> None:
    c, slug, *_ = ctx
    body = (await c.get(f"/api/workspaces/{slug}/policies", headers=MEMBER)).json()
    assert body["active"] is None and body["draft"] is None and body["can_approve"] is False
    assert "spec_tests" in body["verifiers"]


async def test_member_drafts_owner_approves(ctx: Ctx) -> None:
    c, slug, *_ = ctx
    pkg = {"id": "team", "version": "1", "status": "active", "owner": "o", "rules": [RULE]}
    saved = await c.put(
        f"/api/workspaces/{slug}/policies/draft",
        json={"package": pkg, "note": "追加"},
        headers=MEMBER,
    )
    assert saved.status_code == 200, saved.text
    rev = saved.json()["revision"]
    h = (await c.get(f"/api/workspaces/{slug}/policies", headers=MEMBER)).json()["draft_hash"]
    denied = await c.post(
        f"/api/workspaces/{slug}/policies/draft/approve",
        json={"expected_revision": rev, "expected_hash": h},
        headers=MEMBER,
    )
    assert denied.status_code == 403 and denied.json()["error"]["code"] == "not_an_approver"
    stale = await c.post(
        f"/api/workspaces/{slug}/policies/draft/approve",
        json={"expected_revision": rev, "expected_hash": "sha256:stale"},
        headers=OWNER,
    )
    assert stale.status_code == 409
    ok = await c.post(
        f"/api/workspaces/{slug}/policies/draft/approve",
        json={"expected_revision": rev, "expected_hash": h},
        headers=OWNER,
    )
    assert ok.status_code == 200 and ok.json()["status"] == "active"
    body = (await c.get(f"/api/workspaces/{slug}/policies", headers=OWNER)).json()
    assert (
        body["active"]["revision"] == rev and body["draft"] is None and body["can_approve"] is True
    )
    audit = (await c.get(f"/api/workspaces/{slug}/policies/audit", headers=MEMBER)).json()
    assert [(a["action"], a["decision"]) for a in audit][:3] == [
        ("policy.approve", "approved"),
        ("policy.approve", "denied"),
        ("policy.draft", "created"),
    ]
    [summary] = (await c.get(f"/api/workspaces/{slug}/policies/revisions", headers=MEMBER)).json()
    assert summary["status"] == "active" and summary["rule_count"] == 1


async def test_invalid_package_is_422(ctx: Ctx) -> None:
    c, slug, *_ = ctx
    bad_id = {**RULE, "id": "A:B"}
    unknown = {**RULE, "verifier": {"id": "nope", "version": "1"}}
    for rule in (bad_id, unknown):
        pkg = {"id": "team", "version": "1", "status": "active", "owner": "o", "rules": [rule]}
        res = await c.put(
            f"/api/workspaces/{slug}/policies/draft",
            json={"package": pkg, "note": ""},
            headers=MEMBER,
        )
        assert res.status_code == 422, res.text
    body = (await c.get(f"/api/workspaces/{slug}/policies", headers=MEMBER)).json()
    assert body["draft"] is None


async def test_results_come_from_the_latest_runs(ctx: Ctx) -> None:
    c, slug, repo, base, head, *_ = ctx
    await c.post(
        "/api/review-runs",
        json={"repo_full_name": repo, "head_sha": head, "base_sha": base},
        headers=MEMBER,
    )
    body = (await c.get(f"/api/workspaces/{slug}/policies", headers=MEMBER)).json()
    assert body["results"], "題材のリポジトリの .speclino/policy の規約の結果がある"
    assert all(r["projects"] >= 1 for r in body["results"].values() if r["status"] is not None)
```

定数: `MEMBER = {"X-Dev-User": "member1"}`、`OWNER = {"X-Dev-User": "owner1"}`、`RULE = {"id": "DOC-001", "version": "1.0.0", "level": "advisory", "title": "設計判断を残す", "text": "本文", "applies_to": {"paths": ["docs/**"]}, "verifier": None, "on_missing_input": "unknown"}`。

- [ ] **Step 2: 失敗を確かめる**

Run: `UV_NO_SYNC=1 uv run pytest tests/unit/services/test_team_policies.py tests/integration/services/test_policies_api.py -q`
Expected: FAIL（`speclino.services.team_policies` が無い、ルートが無い）

- [ ] **Step 3: 型と集計と読み出しを書く**

`domain/policy_view.py` に足す:

```python
from speclino.domain.finding import Verdict
from speclino.domain.policy import PolicyRevision


class RuleResult(IRModel):
    status: Verdict | None
    projects: int
    failing_projects: int


class PoliciesView(IRModel):
    active: PolicyRevision | None
    draft: PolicyRevision | None
    draft_hash: str | None
    can_approve: bool
    verifiers: dict[str, str]
    results: dict[str, RuleResult]
```

`services/project_overview.py`: `_latest` の名前を `latest_assembled` に変え（呼び出し 2 か所も）、`__all__` が無ければ足さない（関数名だけ公開）。

`services/team_policies.py`:

```python
"""チーム規約の画面（S43）の読み出し。書き込みの判断は services/policy_governance.py。"""

from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from speclino.db.models import Project
from speclino.db.repositories.policy_revisions import PolicyRevisionRepository
from speclino.db.repositories.teams import TeamRepository
from speclino.domain.policy_view import PoliciesView, RuleResult
from speclino.domain.project_overview import PolicyView
from speclino.ports.blob_store import BlobStore
from speclino.services.policy_governance import KNOWN_VERIFIERS, draft_hash
from speclino.services.project_overview import latest_assembled, worst_verdict
from speclino.services.workspaces import require_member


def aggregate_results(per_project: list[list[PolicyView]]) -> dict[str, RuleResult]:
    """各 project の最新の検査の規約の結果を、規約ごとに集計する（結果の無い project は数えない）。"""
    grouped: dict[str, list[PolicyView]] = {}
    for views in per_project:
        for v in views:
            grouped.setdefault(v.rule_id, []).append(v)
    out: dict[str, RuleResult] = {}
    for rule_id, views in grouped.items():
        decided = [v for v in views if v.status is not None]
        out[rule_id] = RuleResult(
            status=worst_verdict(v.status for v in decided if v.status is not None),
            projects=len(decided),
            failing_projects=sum(1 for v in decided if v.status != "VERIFIED_IN_SCOPE"),
        )
    return out


async def load_policies(
    session: AsyncSession, blobs: BlobStore, slug: str, uid: str
) -> PoliciesView:
    await require_member(session, slug, uid)
    revisions = PolicyRevisionRepository(session)
    projects = (await session.execute(select(Project).where(Project.team == slug))).scalars()
    per_project: list[list[PolicyView]] = []
    for p in projects:
        _, _, assembled = await latest_assembled(session, blobs, p)
        if assembled is not None:
            per_project.append(assembled.policies)
    draft = await revisions.current_draft(slug)
    return PoliciesView(
        active=await revisions.active_revision(slug),
        draft=draft,
        draft_hash=draft_hash(draft) if draft is not None else None,
        can_approve=uid in await TeamRepository(session).list_approvers(slug),
        verifiers=dict(KNOWN_VERIFIERS),
        results=aggregate_results(per_project),
    )
```

- [ ] **Step 4: ルートを書く**（`api/routes/policies.py`）

```python
"""チーム規約の画面（S43）の API。判断は services/policy_governance.py と team_policies.py。

メンバー以外は 404。下書きの保存はメンバー、承認は owner / approver（それ以外は 403 で、拒否は監査に残る）。
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field

from speclino.api.auth import require_user
from speclino.api.deps import AppState, get_state
from speclino.api.errors import api_error, error_responses
from speclino.db.repositories.audit import AuditRepository
from speclino.db.repositories.policy_revisions import PolicyRevisionRepository
from speclino.domain.policy import PolicyPackage
from speclino.domain.workspace import AuthUser
from speclino.services import policy_governance as gov
from speclino.services.team_policies import load_policies
from speclino.services.workspaces import WorkspaceNotFound, require_member

router = APIRouter(
    prefix="/api/workspaces/{slug}/policies", tags=["policies"], responses=error_responses(401, 404)
)

HISTORY_LIMIT = 50


class DraftRequest(BaseModel):
    package: PolicyPackage
    note: str = Field(default="", max_length=500)


class ApproveRequest(BaseModel):
    expected_revision: int = Field(ge=1)
    expected_hash: str = Field(min_length=1, max_length=200)


def _not_found() -> Exception:
    return api_error(404, "workspace_not_found", "ワークスペースが無い")


@router.get("")
async def get_policies(
    slug: str, state: AppState = Depends(get_state), user: AuthUser = Depends(require_user)
) -> dict[str, Any]:
    try:
        async with state.session_factory() as s:
            view = await load_policies(s, state.blobs, slug, user.uid)
    except WorkspaceNotFound as e:
        raise _not_found() from e
    return view.model_dump(mode="json")


@router.put("/draft", responses=error_responses(422))
async def put_draft(
    slug: str,
    body: DraftRequest,
    state: AppState = Depends(get_state),
    user: AuthUser = Depends(require_user),
) -> dict[str, Any]:
    try:
        async with state.session_factory() as s, s.begin():
            await require_member(s, slug, user.uid)
            draft = await gov.save_draft(
                s, team=slug, package=body.package, author=user.uid, note=body.note
            )
    except WorkspaceNotFound as e:
        raise _not_found() from e
    except gov.InvalidPackage as e:
        raise api_error(422, "invalid_policy", str(e)) from e
    return draft.model_dump(mode="json")


@router.post("/draft/approve", responses=error_responses(403, 409))
async def approve_draft(
    slug: str,
    body: ApproveRequest,
    state: AppState = Depends(get_state),
    user: AuthUser = Depends(require_user),
) -> dict[str, Any]:
    try:
        async with state.session_factory() as s, s.begin():
            await require_member(s, slug, user.uid)
            active = await gov.approve_draft(
                s,
                team=slug,
                expected_revision=body.expected_revision,
                expected_hash=body.expected_hash,
                approver=user.uid,
                audit_factory=state.session_factory,
            )
    except WorkspaceNotFound as e:
        raise _not_found() from e
    except PermissionError as e:
        raise api_error(403, "not_an_approver", "承認できるのは owner / approver だけ") from e
    except gov.DraftConflict as e:
        raise api_error(409, "draft_changed", "下書きが変わった。読み直してから承認する") from e
    return active.model_dump(mode="json")


@router.get("/revisions")
async def list_revisions(
    slug: str, state: AppState = Depends(get_state), user: AuthUser = Depends(require_user)
) -> list[dict[str, Any]]:
    try:
        async with state.session_factory() as s:
            await require_member(s, slug, user.uid)
            rows = await PolicyRevisionRepository(s).list_summaries(slug, HISTORY_LIMIT)
    except WorkspaceNotFound as e:
        raise _not_found() from e
    return [r.model_dump(mode="json") for r in rows]


@router.get("/audit")
async def list_audit(
    slug: str, state: AppState = Depends(get_state), user: AuthUser = Depends(require_user)
) -> list[dict[str, Any]]:
    try:
        async with state.session_factory() as s:
            await require_member(s, slug, user.uid)
            rows = await AuditRepository(s).list_for_team(
                slug, ("policy.draft", "policy.approve"), HISTORY_LIMIT
            )
    except WorkspaceNotFound as e:
        raise _not_found() from e
    return [r.model_dump(mode="json") for r in rows]
```

FastAPI はボディの検証（422）をメンバー確認より先に行うので、`test_non_member_gets_404` は正しい形のボディを送る（不正なボディの非メンバーは 422 になるが、ワークスペースの存在は漏れない）。

`api/app.py`: `projects` の次に `policies` を import・`include_router`。

`Dockerfile`: `COPY src/ ./src/` の次に `COPY .speclino/policy/ ./.speclino/policy/` を足す（`WORKDIR /app` の下。`policy seed` の既定の取り込み元 `.speclino/policy/speclino-core.yaml` がイメージ内で見つかるように）。

- [ ] **Step 5: 通ることを確かめる**

Run: `UV_NO_SYNC=1 uv run pytest tests/unit/services/test_team_policies.py tests/integration/services/test_policies_api.py tests/integration/services/test_projects_api.py -q && make openapi && make check`
Expected: PASS。`make check` が通る（`openapi.json` を再生成済み）

- [ ] **Step 6: コミット**

```bash
git add src/speclino tests Dockerfile openapi.json
git commit -m "feat(api): チーム規約の API（下書き・承認・履歴・監査・直近の成否） (#53)"
```

---

### Task 3: 画面側の型・API・規則

**Files:**
- Create: `web/src/types/policy.ts`
- Create: `web/src/api/policies.ts`、`web/src/api/mock/policies.ts`
- Create: `web/src/services/policyDraft.ts`、Test: `web/src/services/policyDraft.test.ts`
- Modify: `web/src/services/paths.ts`（`policies` は既にある）、`web/src/services/commands.ts`（規約を足す）、Test: `web/src/services/commands.test.ts`

**Interfaces:**
- Produces: 型 `PolicyLevel`・`OnMissingInput`・`PolicyRule`・`PolicyPackage`・`PolicyRevision`・`RuleResult`・`PoliciesView`・`RevisionSummary`・`AuditEntry`・`RuleChange`。API `getPolicies(slug)`・`saveDraft(slug, pkg, note)`・`approveDraft(slug, expectedRevision, expectedHash)`・`listRevisions(slug)`・`listPolicyAudit(slug)`。規則 `baseForDraft(view: PoliciesView): PolicyPackage`、`upsertRule(pkg: PolicyPackage, rule: PolicyRule, originalId?: string): PolicyPackage`、`diffPackages(before: PolicyPackage | null, after: PolicyPackage): RuleChange[]`、`parsePaths(text: string): string[]`、`newRule(id: string): PolicyRule`

- [ ] **Step 1: 型を書く**（`web/src/types/policy.ts`。`src/speclino/domain/policy.py`・`policy_view.py` に対応）

```ts
import type { Verdict } from './finding'

export type PolicyLevel = 'mandatory' | 'advisory'
export type OnMissingInput = 'violation' | 'unknown' | 'skipped'

export interface PolicyRule {
  id: string
  version: string
  level: PolicyLevel
  title: string
  text: string
  applies_to: { paths: string[]; languages?: string[] }
  source?: { doc: string; anchor?: string | null } | null
  verifier: { id: string; version: string; params?: Record<string, unknown> } | null
  on_missing_input: OnMissingInput
  exceptions_allowed: boolean
}

export interface PolicyPackage {
  schema_version?: string
  id: string
  version: string
  status: 'active' | 'draft'
  owner: string
  rules: PolicyRule[]
}

export interface PolicyRevision {
  team: string
  revision: number
  status: 'draft' | 'active' | 'superseded'
  package: PolicyPackage
  author: string
  approver: string | null
  note: string
}

export interface RuleResult {
  status: Verdict | null
  projects: number
  failing_projects: number
}

export interface PoliciesView {
  active: PolicyRevision | null
  draft: PolicyRevision | null
  draft_hash: string | null
  can_approve: boolean
  verifiers: Record<string, string>
  results: Record<string, RuleResult>
}

export interface RevisionSummary {
  revision: number
  status: PolicyRevision['status']
  author: string
  approver: string | null
  note: string
  rule_count: number
  created_at: string
  approved_at: string | null
}

export interface AuditEntry {
  ts: string
  actor: string
  action: string
  target: string
  decision: string
  detail: Record<string, unknown>
}

/** 有効な改訂から下書きへの変化（1 ルール 1 件）。fields は変わった項目名。 */
export interface RuleChange {
  ruleId: string
  kind: 'added' | 'removed' | 'changed'
  fields: string[]
}
```

- [ ] **Step 2: 失敗するテストを書く**（`policyDraft.test.ts`）

```ts
import { describe, expect, it } from 'vitest'
import { baseForDraft, diffPackages, newRule, parsePaths, upsertRule } from './policyDraft'
import type { PoliciesView, PolicyPackage } from '@/types/policy'

const pkg = (rules = [newRule('DOC-001')]): PolicyPackage => ({ id: 'team', version: '1', status: 'active', owner: 'o', rules })
const view = (over: Partial<PoliciesView>): PoliciesView => ({
  active: null, draft: null, draft_hash: null, can_approve: false, verifiers: {}, results: {}, ...over,
})
const rev = (p: PolicyPackage, status: 'draft' | 'active') => ({ team: 't', revision: 1, status, package: p, author: 'a', approver: null, note: '' })

describe('policyDraft', () => {
  it('下書きがあれば下書き、無ければ有効な改訂、どちらも無ければ空から始める', () => {
    const d = pkg([newRule('D')])
    const a = pkg([newRule('A')])
    expect(baseForDraft(view({ draft: rev(d, 'draft'), active: rev(a, 'active') })).rules[0].id).toBe('D')
    expect(baseForDraft(view({ active: rev(a, 'active') })).rules[0].id).toBe('A')
    expect(baseForDraft(view({})).rules).toEqual([])
  })

  it('ルールの追加と置き換え（ID を変えた編集は元の位置で置き換える）', () => {
    const p = pkg([newRule('A'), newRule('B')])
    expect(upsertRule(p, newRule('C')).rules.map((r) => r.id)).toEqual(['A', 'B', 'C'])
    expect(upsertRule(p, { ...newRule('B2') }, 'B').rules.map((r) => r.id)).toEqual(['A', 'B2'])
    expect(upsertRule(p, { ...newRule('A'), level: 'mandatory' }).rules[0].level).toBe('mandatory')
  })

  it('差分: 追加・削除・変わった項目', () => {
    const before = pkg([newRule('A'), newRule('B')])
    const after = pkg([{ ...newRule('A'), level: 'mandatory', title: '新しい題' }, newRule('C')])
    expect(diffPackages(before, after)).toEqual([
      { ruleId: 'A', kind: 'changed', fields: ['level', 'title'] },
      { ruleId: 'C', kind: 'added', fields: [] },
      { ruleId: 'B', kind: 'removed', fields: [] },
    ])
    expect(diffPackages(null, after).map((c) => c.kind)).toEqual(['added', 'added'])
  })

  it('適用範囲は改行かカンマで区切り、空は捨てる', () => {
    expect(parsePaths('specs/*.md, docs/**\n\n  src/**  ')).toEqual(['specs/*.md', 'docs/**', 'src/**'])
  })

  it('新しいルールは助言・検証器なし・例外可', () => {
    expect(newRule('X')).toMatchObject({ id: 'X', level: 'advisory', verifier: null, exceptions_allowed: true, on_missing_input: 'unknown' })
  })
})
```

`commands.test.ts` に（`/w/ws` の次に規約が並ぶ順）:

```ts
  it('ワークスペースごとに規約への候補がある', () => {
    const cmds = buildCommands({ me, projects: {} })
    expect(cmds.find((c) => c.id === 'policies:ws')).toMatchObject({ label: 'Example の規約', path: '/w/ws/policies' })
  })
```

（既存の「ホーム・各ワークスペースのプロジェクトと設定を並べる」の期待値の配列に `'/w/ws/policies'` を設定の前に足す）

- [ ] **Step 3: 失敗を確かめる**

Run: `cd web && npx vitest run src/services/policyDraft.test.ts src/services/commands.test.ts`
Expected: FAIL

- [ ] **Step 4: 実装する**

`policyDraft.ts`:

```ts
/** チーム規約の下書きの編集（S43、design 6 章）。画面の部品から使う純粋関数。 */

import type { PoliciesView, PolicyPackage, PolicyRule, RuleChange } from '@/types/policy'

const EMPTY: PolicyPackage = { id: 'team', version: '1', status: 'active', owner: 'team', rules: [] }

/** 編集の起点: 下書き → 有効な改訂 → 空。 */
export function baseForDraft(view: PoliciesView): PolicyPackage {
  return view.draft?.package ?? view.active?.package ?? EMPTY
}

/** ルールを追加する。originalId（編集前の ID）か同じ ID があればその位置で置き換える。 */
export function upsertRule(pkg: PolicyPackage, rule: PolicyRule, originalId?: string): PolicyPackage {
  const key = originalId ?? rule.id
  const i = pkg.rules.findIndex((r) => r.id === key)
  const rules = i < 0 ? [...pkg.rules, rule] : pkg.rules.map((r, j) => (j === i ? rule : r))
  return { ...pkg, rules }
}

const FIELDS: (keyof PolicyRule)[] = ['level', 'title', 'text', 'applies_to', 'verifier', 'on_missing_input', 'exceptions_allowed', 'version']

/** before（有効な改訂）から after（下書き）への変化。after の順、削除は最後。 */
export function diffPackages(before: PolicyPackage | null, after: PolicyPackage): RuleChange[] {
  const old = new Map((before?.rules ?? []).map((r) => [r.id, r]))
  const changes: RuleChange[] = []
  for (const r of after.rules) {
    const o = old.get(r.id)
    if (!o) {
      changes.push({ ruleId: r.id, kind: 'added', fields: [] })
      continue
    }
    const fields = FIELDS.filter((f) => JSON.stringify(o[f] ?? null) !== JSON.stringify(r[f] ?? null))
    if (fields.length > 0) changes.push({ ruleId: r.id, kind: 'changed', fields: fields as string[] })
  }
  const kept = new Set(after.rules.map((r) => r.id))
  for (const r of before?.rules ?? []) {
    if (!kept.has(r.id)) changes.push({ ruleId: r.id, kind: 'removed', fields: [] })
  }
  return changes
}

export function parsePaths(text: string): string[] {
  return text
    .split(/[\n,]/)
    .map((s) => s.trim())
    .filter((s) => s.length > 0)
}

export function newRule(id: string): PolicyRule {
  return {
    id,
    version: '1.0.0',
    level: 'advisory',
    title: '',
    text: '',
    applies_to: { paths: [] },
    verifier: null,
    on_missing_input: 'unknown',
    exceptions_allowed: true,
  }
}
```

`diffPackages` のテストの `fields` の順は `FIELDS` の順（`level` → `title`）。

`commands.ts`: 各ワークスペースの設定の候補の直前に

```ts
    commands.push({ id: `policies:${ws.slug}`, label: `${label} の規約`, group: 'ワークスペース', path: paths.policies(ws.slug) })
```

`api/mock/policies.ts`（`example-org` だけを使う。有効な改訂 3 にルール 3 つ、下書き 4 で 1 つを必須へ、結果、履歴 2、監査 3）と `api/policies.ts`（`api/projects.ts` と同じ書き方。一覧のキャッシュは使わない）:

```ts
/** チーム規約（S43）のエンドポイント関数。VITE_MOCK=1 のときは固定値。 */

import { apiFetch } from './client'
import { MOCK_POLICIES, MOCK_POLICY_AUDIT, MOCK_REVISIONS } from './mock/policies'
import type { AuditEntry, PoliciesView, PolicyPackage, PolicyRevision, RevisionSummary } from '../types/policy'

const USE_MOCK = import.meta.env.VITE_MOCK === '1'
const base = (slug: string) => `/api/workspaces/${encodeURIComponent(slug)}/policies`
const json = { 'Content-Type': 'application/json' }

export async function getPolicies(slug: string): Promise<PoliciesView> {
  if (USE_MOCK) return MOCK_POLICIES
  return apiFetch<PoliciesView>(base(slug))
}

export async function saveDraft(slug: string, pkg: PolicyPackage, note: string): Promise<PolicyRevision> {
  if (USE_MOCK) return { ...(MOCK_POLICIES.draft as PolicyRevision), package: pkg, note }
  return apiFetch<PolicyRevision>(`${base(slug)}/draft`, { method: 'PUT', headers: json, body: JSON.stringify({ package: pkg, note }) })
}

export async function approveDraft(
  slug: string,
  expectedRevision: number,
  expectedHash: string,
): Promise<PolicyRevision> {
  if (USE_MOCK) return { ...(MOCK_POLICIES.draft as PolicyRevision), status: 'active' }
  return apiFetch<PolicyRevision>(`${base(slug)}/draft/approve`, {
    method: 'POST', headers: json, body: JSON.stringify({ expected_revision: expectedRevision, expected_hash: expectedHash }),
  })
}

export async function listRevisions(slug: string): Promise<RevisionSummary[]> {
  if (USE_MOCK) return MOCK_REVISIONS
  return apiFetch<RevisionSummary[]>(`${base(slug)}/revisions`)
}

export async function listPolicyAudit(slug: string): Promise<AuditEntry[]> {
  if (USE_MOCK) return MOCK_POLICY_AUDIT
  return apiFetch<AuditEntry[]>(`${base(slug)}/audit`)
}
```

`mock/policies.ts` の中身: `MOCK_POLICIES: PoliciesView`（`active` = 改訂 3、ルール `SPEC-001`（必須・`spec.required_sections`）・`TEST-001`（助言・`spec_tests`）・`DOC-001`（助言・検証器なし）。`draft` = 改訂 4、`TEST-001` を必須にしたもの、作成者 `member-uid`。`can_approve: true`。`verifiers` は 5 つの ID と `'1.0.0'`。`results`: `SPEC-001` 適合 2/0、`TEST-001` 違反 2/1、`DOC-001` は無し）、`MOCK_REVISIONS`（4 draft・3 active）、`MOCK_POLICY_AUDIT`（`policy.draft created`・`policy.approve approved`・`policy.approve denied`）。

- [ ] **Step 5: 通ることを確かめる**

Run: `cd web && npm test && npx tsc -b && npx oxlint`
Expected: PASS

- [ ] **Step 6: コミット**

```bash
git add web/src
git commit -m "feat(web): チーム規約の型・API・下書きの規則 (#53)"
```

---

### Task 4: チーム規約の画面

**Files:**
- Create: `web/src/components/policies/DraftBanner.tsx`、`RuleList.tsx`、`RuleEditor.tsx`、`RevisionHistory.tsx`、`AuditLog.tsx`
- Create: `web/src/pages/Policies.tsx`
- Modify: `web/src/App.tsx`（`/w/:slug/policies`）、`web/src/components/shell/TopBar.tsx`（「規約」を「設定」の前に）
- Modify: `web/src/services/labels.ts`（`ON_MISSING_INPUT_LABELS`・`AUDIT_LABELS`）

**Interfaces:**
- Consumes: Task 3 のすべて、既存の `Panel`・`ToneBadge`・`VerdictBadge`・`Button`・`Input`・`Label`・`Textarea`・`Dialog`・`ErrorState`・`LoadingState`・`toast`（sonner）・`useCurrentWorkspace`・`POLICY_LEVEL_LABELS`・`formatDateTime`
- Produces: 画面のみ

- [ ] **Step 1: 文言を足す**（`labels.ts`）

```ts
export const ON_MISSING_INPUT_LABELS: Record<OnMissingInput, string> = {
  violation: '対象が無ければ違反',
  unknown: '対象が無ければ未検証',
  skipped: '対象が無ければ評価しない',
}

/** 監査の「何を」（action と decision の組）。 */
export function auditLabel(action: string, decision: string): string {
  if (action === 'policy.draft') return decision === 'updated' ? '下書きを更新' : '下書きを作成'
  if (action === 'policy.approve') return decision === 'denied' ? '承認を拒否（承認者ではない）' : '承認して有効にした'
  return `${action} ${decision}`
}
```

（`OnMissingInput` を `types/policy` から import）

- [ ] **Step 2: 部品を書く**

- `DraftBanner({ view, onApproved })`: `view.draft` が無ければ何も出さない。あるとき: 「下書き 改訂 {n}」・作成者・`note`・変更の件数（`diffPackages(view.active?.package ?? null, view.draft.package).length`）・「差分を見る」（押すと各 `RuleChange` を「{ruleId}: 追加 / 削除 / {fields を日本語で}」と並べる。`level` は「必須 / 助言」の前後を `POLICY_LEVEL_LABELS` で出す）・`view.can_approve` のときだけ「承認して有効にする」（`Dialog` で「改訂 {n} を有効にします。以後の検査はこの改訂で判定します。」と確認 → `approveDraft(slug, view.draft.revision, view.draft_hash)`（画面に出している下書きの番号とハッシュ。承認者が見たものだけを承認する）。409 なら toast「下書きが変わりました。読み直してください」と再読込、403 なら toast「承認できるのは owner / approver だけです」、成功で toast「改訂 {n} を有効にしました」と `onApproved()`）
- `RuleList({ view, draftPkg, onEdit })`: 表示するルールは `draftPkg`（下書きの中身。無ければ有効な改訂）。各行: ID（`font-mono break-all`）・題・`POLICY_LEVEL_LABELS[level]`・検査器（`verifier ? verifier.id : '助言のみ（検査器なし）'`）・直近の成否（`view.results[id]` があれば `VerdictBadge` と「{failing}/{projects} プロジェクトで未達」、無ければ「結果なし」）・適用範囲（`applies_to.paths.join(', ')`、`break-all`）・例外の可否。下書きで変わった行は `diffPackages` の結果で「変更」「追加」の `ToneBadge tone="rev"` を付け、削除された規則は一覧の末尾に取り消し線で出す。行の「編集」で `onEdit(rule)`、一覧の下に「ルールを追加」（`onEdit(null)`）
- `RuleEditor({ rule, verifiers, onCancel, onSave })`: 題（`Input`）・本文（`Textarea`）・ID（追加のときだけ編集可）・レベル（必須 / 助言のラジオ）・適用範囲（`Textarea`、`parsePaths` で配列に）・例外の可否（チェックボックス）・対象が無いとき（`ON_MISSING_INPUT_LABELS` の選択）・検査器（「なし（助言のみ）」＋ `verifiers` の ID の選択。選んだら `{ id, version: verifiers[id] }`）。「下書きに保存」で `onSave(rule, originalId)`。題・ID が空、ID にコロン、必須なのに検査器が「なし」なら保存ボタンを無効にして理由を出す（必須のルールは検査器が要る: `domain/policy.py` の `_mandatory_needs_a_verifier`。最終的な検証はサーバの 422）
- `RevisionHistory({ items })`: 改訂番号・状態（有効 / 下書き / 置き換え済み）・作成者・承認者・時刻（`approved_at ?? created_at`）・ルール数・note
- `AuditLog({ items })`: 時刻・誰が（actor）・何を（`auditLabel`）・対象（target の `@` 以降を「改訂 n」）。拒否の行は `ToneBadge tone="bad"`

- [ ] **Step 3: ページを書く**（`pages/Policies.tsx`）

ふるまい:
- `useParams` の slug を `useCurrentWorkspace()` で照合し、所属に無ければ「このワークスペースは見つかりません」
- `getPolicies`・`listRevisions`・`listPolicyAudit` を並べて取り、取得中は `LoadingState`、失敗は `ErrorState`（再試行）
- `view.active` も `view.draft` も無ければ、上部に「チーム規約はまだありません。ルールを追加して下書きを作り、owner / approver が承認すると有効になります。」
- 編集: `RuleEditor` で保存 → `upsertRule(baseForDraft(view), rule, originalId)` を `saveDraft(slug, pkg, note)`（note は「{ID} を{追加 / 変更}」）。422 は `ErrorState` ではなく toast でサーバの理由を出し、編集を閉じない。成功で toast「下書きに保存しました」と 3 つを取り直す
- 配置: `lg:grid-cols-[minmax(0,1fr)_20rem]`。左に `DraftBanner`・`RuleList`（編集中はその位置に `RuleEditor`）、右に `RevisionHistory`・`AuditLog`（スマホでは下に縦に並ぶ）。すべての列に `min-w-0`

`App.tsx`: `<Route path="/w/:slug/policies" element={<Policies />} />` を設定の前に。`TopBar.tsx`: ワークスペースがあるとき「規約」（`paths.policies(slug)`）を「設定」の前に、同じ `navClass`。

- [ ] **Step 4: 確かめる**

Run: `cd web && npm test && npx tsc -b && npx oxlint && npm run build`
Expected: PASS

- [ ] **Step 5: コミット**

```bash
git add web/src
git commit -m "feat(web): チーム規約の画面（一覧・編集・下書き・承認・履歴・監査） (#53)"
```

---

### Task 5: 本番での確認（ユーザーの確認を取ってから行う）

完了条件「本番で 1 ルールを advisory → mandatory に上げ、次の run の manifest に新しい改訂番号が出る」。ここはコードの変更ではなく本番の操作なので、各段の前にユーザーに確認する。

- [ ] **Step 1**: PR・CI・マージ・デプロイ（`gh workflow run deploy.yml --ref main`）
- [ ] **Step 2**: admin Job でチーム規約を初期化: `gcloud run jobs execute speclino-admin --project=<project_id> --region=asia-northeast1 --wait --args=policy,seed,--team,<slug>`（`deploy.yml` と同じく `--project` を必ず付け、別の環境の同名 Job を実行しない。`<project_id>`・`<slug>` は会話の中で確認し、文書には書かない）
- [ ] **Step 3**: 画面で助言のルール 1 つ（例: `.speclino/policy/speclino-core.yaml` の advisory のもの）を必須にして下書きに保存 → owner が承認
- [ ] **Step 4**: 次の run（デモ PR の再実行か、新しい PR）の詳細で manifest の `policy_revision` が新しい番号であることを確かめる（`GET /api/review-runs/{id}` の `manifest.policy_revision`）
- [ ] **Step 5**: Issue #53 の成果に、改訂番号と run の ID を書いて完了にする
