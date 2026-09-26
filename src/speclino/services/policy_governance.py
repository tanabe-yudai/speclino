"""チーム規約の改訂（下書き作成・承認）と監査記録をまとめる（S42）。

`db/repositories/policy_revisions.py` は DB 操作だけを担い、監査を書かない。
何を監査に残すかはビジネスロジックなので CLAUDE.md の規約（ビジネスロジックは
services/ に集約する）に従いここへ置く。呼び出し側（CLI / 将来の API）は
`async with session_factory() as session, session.begin(): ...` で1トランザクションに
包んで呼ぶ（`ReviewRunService` と同じ流儀）。
"""

from __future__ import annotations

from sqlalchemy.ext.asyncio import AsyncSession

from speclino.db.repositories.audit import AuditRepository
from speclino.db.repositories.policy_revisions import PolicyRevisionRepository
from speclino.domain.policy import PolicyPackage, PolicyRevision

SEED_ACTOR = "seed"


async def create_draft(
    session: AsyncSession,
    *,
    team: str,
    package: PolicyPackage,
    author: str,
    note: str = "",
) -> PolicyRevision:
    """draft 改訂を作り、`audit_events` に `policy.draft` を残す。"""
    revision = await PolicyRevisionRepository(session).create_draft(
        team=team, package=package, author=author, note=note
    )
    await AuditRepository(session).record(
        actor=author,
        action="policy.draft",
        target=f"{team}@{revision.revision}",
        decision="created",
        detail={"note": note, "rule_count": len(package.rules)},
    )
    return revision


async def approve(
    session: AsyncSession,
    *,
    team: str,
    revision: int,
    approver: str,
) -> PolicyRevision:
    """draft 改訂を承認し、`audit_events` に `policy.approve` を残す。"""
    approved = await PolicyRevisionRepository(session).approve(
        team=team, revision=revision, approver=approver
    )
    await AuditRepository(session).record(
        actor=approver,
        action="policy.approve",
        target=f"{team}@{revision}",
        decision="approved",
        detail={"author": approved.author},
    )
    return approved


async def seed_from_package(
    session: AsyncSession, *, team: str, package: PolicyPackage
) -> PolicyRevision:
    """既存の規約パッケージ（`.speclino/policy/*.yaml`）を team の最初の改訂として取り込む。

    人の承認ではなく既存ファイルの移行なので author / approver は両方 "seed" にする。
    通常の下書き作成→承認と同じ経路を通すため、監査には `policy.draft` と
    `policy.approve` の両方が残る。team に既に改訂があれば `create_draft` が
    次の番号を振るだけで、revision=1 になる保証はチームが空であることに依る
    （呼び出し側の CLI は初回投入のためだけに使う想定）。
    """
    draft = await create_draft(
        session,
        team=team,
        package=package,
        author=SEED_ACTOR,
        note="既存の規約パッケージ（.speclino/policy）の取り込み",
    )
    return await approve(session, team=team, revision=draft.revision, approver=SEED_ACTOR)


__all__ = ["SEED_ACTOR", "approve", "create_draft", "seed_from_package"]
