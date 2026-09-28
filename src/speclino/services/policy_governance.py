"""チーム規約の改訂（下書き作成・承認）と監査記録をまとめる（S42、統治の穴修正）。

`db/repositories/policy_revisions.py` は DB 操作だけを担い、監査も承認者検証も書かない。
何を監査に残すか・誰が承認できるかはビジネスロジックなので CLAUDE.md の規約
（ビジネスロジックは services/ に集約する）に従いここへ置く。呼び出し側（CLI / 将来の API）は
`async with session_factory() as session, session.begin(): ...` で1トランザクションに
包んで呼ぶ（`ReviewRunService` と同じ流儀）。`approve` が例外を投げた場合、それまでに
実行した UPDATE（`PolicyRevisionRepository.approve` 内の一時的な superseded 化など）を
取り消すのは呼び出し側の rollback の責任（このモジュール自身はトランザクション境界を
開かない）。
"""

from __future__ import annotations

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from speclino.db.repositories.audit import AuditRepository
from speclino.db.repositories.policy_revisions import PolicyRevisionRepository
from speclino.db.repositories.teams import TeamRepository
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
    """draft 改訂を作り、`audit_events` に `policy.draft` を残す。

    統治の穴修正 #4: `seed_from_package` とここ（および `approve`）の間にも排他が要る。
    `seed_from_package` は「チーム行をロック → 改訂が0件か確認 → 挿入」を1トランザクション
    で行うが、この関数がチーム行をロックしなければ、seed が確認を終えた直後に別の
    トランザクションでここが割り込んで draft 作成→承認まで完了させてしまい、seed が
    （古い「0件」を前提のまま）resume してその改訂を superseded にできてしまう
    （承認者検証をバイパスする seed が、承認を経た改訂を上書きする）。そのため、この関数も
    `TeamRepository.ensure_and_lock` でチーム行をロックしてから進む（すべての改訂操作を
    同じ行ロックで直列化する）。ロックの取得順序は `seed_from_package` と揃えてチーム行→
    改訂行に統一し、デッドロックを避ける。
    """
    await TeamRepository(session).ensure_and_lock(team)
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
    audit_factory: async_sessionmaker[AsyncSession] | None = None,
) -> PolicyRevision:
    """draft 改訂を承認し、`audit_events` に `policy.approve` を残す。

    承認者はワークスペースのメンバーのうち role が owner / approver の uid で検証する
    （統治の穴修正 #1、S53 で `teams.approvers` から移した）。
    許可集合に無い ID からの承認は `PermissionError` で拒否し、拒否も
    `audit_events`（`decision="denied"`）に残す（`agent/gateway.py` の
    ToolGateway が「権限判定 → 拒否なら監査して denied」とする流儀に揃える）。
    承認者が1人も登録されていないチーム（`teams` に行が無い場合を含む）は
    誰も承認できない（安全側のデフォルト）。`seed_from_package` はこの検証を
    バイパスする内部専用の経路を使う（下記 `_approve` 参照）。
    """
    return await _approve(
        session,
        team=team,
        revision=revision,
        approver=approver,
        enforce_approvers=True,
        audit_factory=audit_factory,
    )


async def _approve(
    session: AsyncSession,
    *,
    team: str,
    revision: int,
    approver: str,
    enforce_approvers: bool,
    audit_factory: async_sessionmaker[AsyncSession] | None = None,
) -> PolicyRevision:
    """`approve` の実装本体。`enforce_approvers=False` は `seed_from_package` 専用
    （人の承認ではなく既存ファイルの移行のため、チームの承認者集合の検証を通さない）。
    公開 API からは `enforce_approvers=False` を選べない（`approve` は常に True で呼ぶ）。

    統治の穴修正 #4: `create_draft` と同じ理由で、ここも `seed_from_package` と排他する
    必要がある。承認者検証・改訂行の更新より前にチーム行をロックする
    （ロックの取得順序をチーム行→改訂行に統一し、デッドロックを避ける）。
    """
    await TeamRepository(session).ensure_and_lock(team)
    if enforce_approvers:
        approvers = set(await TeamRepository(session).list_approvers(team))
        if approver not in approvers:
            await _record_denial(session, audit_factory, team, revision, approver)
            raise PermissionError(f"{approver} は {team} の承認者ではない")

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
    `policy.approve` の両方が残る。

    統治の穴修正 #2: team に既に改訂が1件でもあれば拒否する（seed は初期化専用。
    変更は下書き作成→承認で行う）。判定（改訂が無いことの確認）と挿入（下書き作成→承認）
    を同一トランザクションで行い、間に別の seed が入らないようにするため、まず
    `TeamRepository.ensure_and_lock` でチームの行ロックを取ってから改訂の有無を確認する
    （行ロックを取った時点でこのトランザクションが commit/rollback するまで、同じ team に
    対する他の `ensure_and_lock` はブロックされる）。

    統治の穴修正 #4: 上記の排他は元々 seed 同士（2つの `seed_from_package`）にしか
    効いていなかった。`create_draft` / `approve`（`_approve`）が今ではここと同じ
    `ensure_and_lock` を呼ぶため、通常の下書き作成→承認との間もこの行ロックで直列化
    される。これにより、この関数が「改訂0件」を確認してから内部で `create_draft` /
    `_approve` を呼び終えて commit するまでの間、他のトランザクションが同じ team に
    対して draft 作成や承認を割り込ませることはできない（詳細は `create_draft` の
    docstring 参照）。
    """
    teams = TeamRepository(session)
    await teams.ensure_and_lock(team)
    if await PolicyRevisionRepository(session).exists_for_team(team):
        raise ValueError(
            f"{team} には既に改訂がある。seed は初期化専用: 変更は下書き作成→承認で行う"
        )

    draft = await create_draft(
        session,
        team=team,
        package=package,
        author=SEED_ACTOR,
        note="既存の規約パッケージ（.speclino/policy）の取り込み",
    )
    return await _approve(
        session,
        team=team,
        revision=draft.revision,
        approver=SEED_ACTOR,
        enforce_approvers=False,
    )


__all__ = ["SEED_ACTOR", "approve", "create_draft", "seed_from_package"]


async def _record_denial(
    session: AsyncSession,
    audit_factory: async_sessionmaker[AsyncSession] | None,
    team: str,
    revision: int,
    approver: str,
) -> None:
    """承認の拒否を監査に残す。**呼び出し側の rollback で消えないようにする**。

    拒否は例外で返すので、呼び出し側が rollback するのが自然。同じセッションに書くと
    「誰が承認を試みて拒否されたか」という監査の本体が一緒に消えてしまう（監査として
    成立しない）。`audit_factory` があれば独立したトランザクションで確定させる。

    渡されないときは同じセッションに best-effort で書く（テストなど、呼び出し側が
    commit する構成のため）。本番の経路（CLI / API）は必ず factory を渡すこと。
    """
    record = {
        "actor": approver,
        "action": "policy.approve",
        "target": f"{team}@{revision}",
        "decision": "denied",
        "detail": {"reason": "not_an_approver"},
    }
    if audit_factory is None:
        await AuditRepository(session).record(**record)  # type: ignore[arg-type]
        return
    async with audit_factory() as audit_session, audit_session.begin():
        await AuditRepository(audit_session).record(**record)  # type: ignore[arg-type]
