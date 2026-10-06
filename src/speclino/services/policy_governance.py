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
from speclino.services.check_pipeline import policy_package_hash
from speclino.verifiers.catalog import KNOWN_VERIFIERS

SEED_ACTOR = "seed"


# LLM 支援の規約で「広すぎる」とみなすパターン（リポジトリ全体に当たる）
_BROAD_PATTERNS = frozenset({"*", "**", "**/*", "**/*.py", "*.py", "./**", "./**/*"})


def llm_scope_warnings(package: PolicyPackage) -> list[str]:
    """LLM 支援の規約（`llm.checklist`）の送り先の範囲の警告（#114）。承認は拒否しない。

    秘密情報の検出は完全ではないので、LLM に送るのは送ってよいと確かめたパスだけにする。
    `params.paths` が無い（`applies_to.paths` をそのまま送る）・リポジトリ全体に当たるパターンを
    含む場合に警告する。
    """
    from speclino.domain.policy import LLM_CHECKLIST_VERIFIER_ID

    warnings: list[str] = []
    for rule in package.rules:
        if rule.verifier is None or rule.verifier.id != LLM_CHECKLIST_VERIFIER_ID:
            continue
        raw = rule.verifier.params.get("paths") if isinstance(rule.verifier.params, dict) else None
        paths = [p for p in raw if isinstance(p, str)] if isinstance(raw, list) else []
        if not paths:
            warnings.append(
                f"{rule.id}: LLM に送るパス（params.paths）が無い。applies_to.paths に一致する"
                "ファイルをすべて送る。送ってよいパスだけを指定する"
            )
        effective = paths or list(rule.applies_to.paths)
        broad = sorted({p for p in effective if p.strip() in _BROAD_PATTERNS})
        if broad:
            warnings.append(
                f"{rule.id}: LLM に送るパスが広すぎる（{', '.join(broad)}）。秘密情報の検出は"
                "完全ではないので、送ってよいディレクトリに絞る"
            )
    return warnings


class InvalidPackage(ValueError):
    """下書きにできない規約（重複したルール ID・未登録の検証器）。"""


class DraftExists(ValueError):
    """下書きが既にある（同時に 1 つ）。保存は save_draft で同じ下書きを更新する。"""


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
        if (
            r.verifier is not None
            and r.verifier.id == "llm.checklist"
            and r.verifier.params.get("unit_kind") == "string_literal"
            and not r.verifier.params.get("call_names")
        ):
            # #114 裁定 37: LLM に送る文字列は、指定した呼出しに渡されたリテラルだけに限る
            raise InvalidPackage(
                f"{r.id}: unit_kind=string_literal の LLM 支援の規約には params.call_names が要る"
                "（例: OrderError、HTTPException、api_error）"
            )
        if r.verifier is not None and r.verifier.version != KNOWN_VERIFIERS[r.verifier.id]:
            raise InvalidPackage(
                f"{r.id}: 検証器 {r.verifier.id} の版 {r.verifier.version} は使えない"
                f"（{KNOWN_VERIFIERS[r.verifier.id]}）"
            )


async def save_draft(
    session: AsyncSession,
    *,
    team: str,
    package: PolicyPackage,
    author: str,
    note: str,
    expected_revision: int | None,
    expected_hash: str | None,
) -> PolicyRevision:
    """下書きを保存する（S43）。無ければ作り、あれば同じ下書きを更新する（下書きは同時に 1 つ）。

    expected_* は保存する人が前提にした下書き（無ければ両方 None）。今の下書きと違えば
    DraftConflict（古い画面から保存して他の人の変更を黙って上書きしない）。
    チーム行のロックを先に取るので、同時の保存・承認・seed と直列化される（create_draft と同じ）。
    """
    _validate(package)
    await TeamRepository(session).ensure_and_lock(team)
    revisions = PolicyRevisionRepository(session)
    current = await revisions.current_draft(team)
    if current is None:
        seen = expected_revision is None and expected_hash is None
    else:
        seen = current.revision == expected_revision and draft_hash(current) == expected_hash
    if not seen:
        raise DraftConflict(f"{team} の下書きが変わった。読み直してから保存する")
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
    current = await PolicyRevisionRepository(session).current_draft(team)
    if approver not in set(await teams.list_approvers(team)):
        # 監査の対象は今の下書き（送られた番号は使わない: 任意の番号で監査を埋められないように）
        target_revision = current.revision if current is not None else 0
        await _record_denial(session, audit_factory, team, target_revision, approver)
        raise PermissionError(f"{approver} は {team} の承認者ではない")
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
    if await PolicyRevisionRepository(session).current_draft(team) is not None:
        raise DraftExists(
            f"{team} には既に下書きがある。保存は save_draft（同じ下書きを更新）で行う"
        )
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

    # 作成者だけでなく、下書きを更新した人も「自分で書いた」に数える
    editors = await AuditRepository(session).actors_for(f"{team}@{revision}", "policy.draft")
    approved = await PolicyRevisionRepository(session).approve(
        team=team, revision=revision, approver=approver
    )
    self_approved = approver == approved.author or approver in editors
    # S62: 規約が変わったら同じトランザクションで知識の bundle を作り直す（run が固定する単位）。
    # knowledge_registry はこのモジュールの DraftConflict を使うので、循環を避けて関数内で import
    from speclino.services.knowledge_registry import build_bundle

    await build_bundle(session, team)
    await AuditRepository(session).record(
        actor=approver,
        action="policy.approve",
        target=f"{team}@{revision}",
        decision="approved",
        detail={
            "author": approved.author,
            "self_approved": self_approved,
            # LLM 支援の規約の送り先の範囲の警告（#114。拒否はしない）
            **(
                {"warnings": warnings} if (warnings := llm_scope_warnings(approved.package)) else {}
            ),
        },
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


__all__ = [
    "KNOWN_VERIFIERS",
    "SEED_ACTOR",
    "DraftConflict",
    "DraftExists",
    "InvalidPackage",
    "approve",
    "approve_draft",
    "create_draft",
    "draft_hash",
    "llm_scope_warnings",
    "save_draft",
    "seed_from_package",
]


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
