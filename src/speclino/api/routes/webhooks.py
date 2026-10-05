"""GitHub Webhook（web ロール）。署名検証 → delivery_id 重複排除 → run 作成 or コマンド処理。

Check の作成・更新は鍵を持つ worker が行う。ここでは DB への記録とキュー投入だけ。

重複排除の記録と、その配信で行う DB の書き込み（project の解決・承認・回答・run の作成）は
1 つのトランザクションにする（S53）。処理が途中で失敗すれば記録も残らず、再配信で処理し直せる。
キュー投入は commit のあとで行い、失敗したら記録を消して 500 を返す（再配信で同じ PR の run を
作り直しても、`supersede_open_runs` が古い run を superseded にするので二重に公開されない）。

project はペイロードの `repository.id` で決める。名前は改名後に別リポジトリへ再利用されうるため、
名前で引くと別リポジトリの承認者・質問・run に触れてしまう。
"""

from __future__ import annotations

import uuid
from typing import Any

from fastapi import APIRouter, Depends, Request
from sqlalchemy.ext.asyncio import AsyncSession

from speclino.adapters.github.types import IssueCommentRef, WebhookEvent
from speclino.adapters.github.webhook import (
    extract_comment,
    extract_pr,
    extract_rerun,
    parse_event,
    verify_signature,
)
from speclino.api.deps import AppState, get_state
from speclino.api.errors import api_error, error_responses
from speclino.db.models import Project
from speclino.db.repositories import (
    ApprovalRepository,
    AuditRepository,
    ProjectRepository,
    QuestionRepository,
    ReviewRunRepository,
    WebhookDeliveryRepository,
)
from speclino.services.approvals import attest_disposition, authorize_command, parse_commands
from speclino.services.attestations import attest, same_github_user
from speclino.services.workspaces import resolve_github_project

router = APIRouter(prefix="/webhooks", tags=["webhooks"])


@router.post("/github", responses=error_responses(401))
async def github_webhook(request: Request, state: AppState = Depends(get_state)) -> dict[str, Any]:
    body = await request.body()
    secret = state.settings.github_webhook_secret
    must_verify = state.settings.env != "local" or bool(secret)
    if must_verify and (
        not secret or not verify_signature(secret, body, request.headers.get("X-Hub-Signature-256"))
    ):
        raise api_error(401, "invalid_signature", "署名が不正")
    event = parse_event(dict(request.headers), body)
    async with state.session_factory() as session, session.begin():
        deliveries = WebhookDeliveryRepository(session)
        if not await deliveries.mark_if_new(event.delivery_id, event.event, event.action):
            return {"status": "duplicate", "delivery_id": event.delivery_id}
        result, queued = await _dispatch(state, session, event)
    try:
        for run_id in queued:
            await state.queue.enqueue(run_id)
    except Exception:
        async with state.session_factory() as session, session.begin():
            await WebhookDeliveryRepository(session).forget(event.delivery_id)
        raise
    return result


async def _dispatch(
    state: AppState, session: AsyncSession, event: WebhookEvent
) -> tuple[dict[str, Any], list[uuid.UUID]]:
    """イベントごとの DB の書き込み。戻り値は応答と、commit 後に enqueue する run。"""
    if event.event == "pull_request" and (pr := extract_pr(event.payload)) is not None:
        project = await resolve_github_project(
            session,
            github_repo_id=pr.repo_id,
            repo_full_name=pr.repo_full_name,
            installation_id=pr.installation_id,
            actor="webhook",
        )
        run_id = await state.service.create_run_in(
            session,
            project=project,
            head_sha=pr.head_sha,
            base_sha=pr.base_sha,
            pr_number=pr.pr_number,
            trigger=f"pull_request.{event.action}",
            pr_author_id=pr.author_id,
        )
        return {"status": "queued", "run_id": str(run_id), "fork": pr.is_fork}, [run_id]

    if event.event in ("check_run", "check_suite") and (rr := extract_rerun(event.payload)):
        project = await resolve_github_project(
            session,
            github_repo_id=rr.repo_id,
            repo_full_name=rr.repo_full_name,
            installation_id=rr.installation_id,
            actor="webhook",
        )
        runs = ReviewRunRepository(session)
        queued: list[uuid.UUID] = []
        for pr_number in rr.pr_numbers or [None]:  # type: ignore[list-item]
            latest = await runs.latest_run_for_pr(project.id, pr_number)
            queued.append(
                await state.service.create_run_in(
                    session,
                    project=project,
                    head_sha=rr.head_sha,
                    base_sha=latest.base_sha if latest else None,
                    pr_number=pr_number,
                    trigger="rerequested",
                )
            )
        return {"status": "queued", "run_id": str(queued[-1])}, queued

    if event.event == "issue_comment" and (cm := extract_comment(event.payload)) is not None:
        return await _handle_comment(state, session, cm)

    return {"status": "ignored", "event": event.event, "action": event.action}, []


async def _handle_comment(
    state: AppState, session: AsyncSession, cm: IssueCommentRef
) -> tuple[dict[str, Any], list[uuid.UUID]]:
    commands = parse_commands(cm.body)
    if not commands or not cm.is_pull_request:
        return {"status": "ignored", "reason": "コマンドなし"}, []
    results: list[dict[str, Any]] = []
    # ID で引く（名前で引くと、旧名を再利用した別リポジトリから旧 project の承認者・質問に
    # 触れられる）。コメントからは project を作らない: 見つからなければ承認者はいない。
    project = await ProjectRepository(session).get_by_github_id(cm.repo_id)
    # 承認者は project ごとの設定だけ。全体共通の承認者は、どのリポジトリでも承認できて
    # しまうため持たない（S52）。
    approvers = set(project.settings.get("approvers", [])) if project is not None else set()
    audit = AuditRepository(session)
    latest = (
        await ReviewRunRepository(session).latest_run_for_pr(project.id, cm.issue_number)
        if project
        else None
    )
    for cmd in commands:
        decision = authorize_command(cmd, cm.author_login, approvers)
        await audit.record(
            cm.author_login,
            f"command.{cmd.kind}",
            f"{cm.repo_full_name}#{cm.issue_number}",
            "accepted" if decision.accepted else "rejected",
            {"args": list(cmd.args), "reason": decision.reason, "url": cm.comment_url},
        )
        if not decision.accepted:
            results.append({"kind": cmd.kind, "accepted": False, "reason": decision.reason})
            continue
        if cmd.kind == "approve-spec" and project is not None:
            await ApprovalRepository(session).add(
                project_id=project.id,
                kind="approve_spec",
                target=cmd.args[0],
                content_hash=None,
                approver=cm.author_login,
                comment_url=cm.comment_url,
            )
        if cmd.kind == "attest":
            outcome = await _record_attestation(session, cmd.args, cm, project)
            results.append({"kind": cmd.kind, **outcome})
            continue
        if cmd.kind == "answer":
            outcome = await _record_answer(session, cmd.args, cm, project, latest)
            results.append({"kind": cmd.kind, **outcome})
            continue
        results.append({"kind": cmd.kind, "accepted": True})
    # 承認・回答・再実行のあとは最新 head で再検証する（回答は親 run を持つ子 run として）
    if project is not None and latest is not None and any(r["accepted"] for r in results):
        answered = [r for r in results if r["kind"] == "answer" and r["accepted"]]
        run_id = await state.service.create_run_in(
            session,
            project=project,
            head_sha=latest.head_sha,
            base_sha=latest.base_sha,
            pr_number=cm.issue_number,
            trigger="comment",
            parent_run_id=answered[0]["parent_run_id"] if answered else None,
        )
        return {"status": "queued", "run_id": str(run_id), "commands": results}, [run_id]
    return {"status": "handled", "commands": results}, []


async def _record_attestation(
    session: AsyncSession, args: tuple[str, ...], cm: IssueCommentRef, project: Project | None
) -> dict[str, Any]:
    """`/speclino attest <指紋> <sha256:hash> [ack]` を確定として記録する（S70）。

    承認者の照合は `authorize_command` で済んでいる。確定はワークスペースに属するので、
    ワークスペースに登録されていないリポジトリでは記録しない。actor は `github:<login>`。
    """
    if project is None or project.team is None:
        return {"accepted": False, "reason": "ワークスペースに登録されていないリポジトリ"}
    saved = await attest(
        session,
        fingerprint=args[0],
        content_hash=args[1],
        disposition=attest_disposition(args),
        actor=f"github:{cm.author_login}",
        workspace=project.team,
        via=f"pr_comment:{cm.comment_url}",
        # PR の作成者が自分で確定した（GitHub の ID で照合。拒否はせず印を残す。照合不可は None）
        self_attested=same_github_user(cm.author_id, cm.issue_author_id),
    )
    return {
        "accepted": True,
        "disposition": saved.disposition,
        "self_attested": saved.self_attested,
    }


async def _record_answer(
    session: AsyncSession,
    args: tuple[str, ...],
    cm: IssueCommentRef,
    project: Project | None,
    latest: Any,
) -> dict[str, Any]:
    """`/speclino answer Q-<id> <選択肢>`。head が変わっていれば stale（FR 015）。

    質問はこのコメントが付いた PR のもの（同じ project・同じ PR 番号の run の質問）に限る。
    短縮 ID は全体で引けるため、照合しないと別リポジトリの質問に回答でき、その run を親に
    した子 run で回答を取り込ませられる（S52）。見つからない場合と同じ応答にして、他の
    リポジトリの質問の有無を漏らさない。
    """
    qid, option = args[0].removeprefix("Q-"), args[1]
    qrepo = QuestionRepository(session)
    q = await qrepo.find_by_short_id(qid)
    run = await ReviewRunRepository(session).get(q.run_id) if q is not None else None
    if (
        q is None
        or run is None
        or project is None
        or run.project_id != project.id
        or run.pr_number != cm.issue_number
    ):
        return {"accepted": False, "reason": f"未回答の質問 Q-{qid} が無い"}
    options = q.options.get("options") or []
    if option not in options:
        return {"accepted": False, "reason": f"選択肢は {options} のいずれか"}
    if latest is not None and latest.head_sha != q.head_sha:
        await qrepo.set_answer(
            q.id, {"option": option, "by": cm.author_login, "stale": True}, "stale"
        )
        return {"accepted": False, "reason": "質問後に head が変わったため再確認が必要（stale）"}
    await qrepo.set_answer(
        q.id, {"option": option, "by": cm.author_login, "url": cm.comment_url}, "answered"
    )
    return {"accepted": True, "parent_run_id": q.run_id}
