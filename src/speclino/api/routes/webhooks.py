"""GitHub Webhook（web ロール）。署名検証 → delivery_id 重複排除 → run 作成 or コマンド処理。

Check の作成・更新は鍵を持つ worker が行う。ここでは DB への記録とキュー投入だけ。
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, Request

from speclino.adapters.github.webhook import (
    extract_comment,
    extract_pr,
    extract_rerun,
    parse_event,
    verify_signature,
)
from speclino.api.deps import AppState, get_state
from speclino.api.errors import api_error, error_responses
from speclino.db.repositories import (
    ApprovalRepository,
    AuditRepository,
    ProjectRepository,
    QuestionRepository,
    ReviewRunRepository,
    WebhookDeliveryRepository,
)
from speclino.services.approvals import authorize_command, parse_commands

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
        if not await WebhookDeliveryRepository(session).mark_if_new(
            event.delivery_id, event.event, event.action
        ):
            return {"status": "duplicate", "delivery_id": event.delivery_id}

    if event.event == "pull_request" and (pr := extract_pr(event.payload)) is not None:
        run_id = await state.service.create_run(
            repo_full_name=pr.repo_full_name,
            head_sha=pr.head_sha,
            base_sha=pr.base_sha,
            pr_number=pr.pr_number,
            trigger=f"pull_request.{event.action}",
            installation_id=pr.installation_id,
        )
        return {"status": "queued", "run_id": str(run_id), "fork": pr.is_fork}

    if event.event in ("check_run", "check_suite") and (rr := extract_rerun(event.payload)):
        for pr_number in rr.pr_numbers or [None]:  # type: ignore[list-item]
            base = await _latest_base(state, rr.repo_full_name, pr_number)
            run_id = await state.service.create_run(
                repo_full_name=rr.repo_full_name,
                head_sha=rr.head_sha,
                base_sha=base,
                pr_number=pr_number,
                trigger="rerequested",
                installation_id=rr.installation_id,
            )
        return {"status": "queued", "run_id": str(run_id)}

    if event.event == "issue_comment" and (cm := extract_comment(event.payload)) is not None:
        return await _handle_comment(state, cm)

    return {"status": "ignored", "event": event.event, "action": event.action}


async def _latest_base(state: AppState, repo: str, pr_number: int | None) -> str | None:
    async with state.session_factory() as session:
        project = await ProjectRepository(session).get_by_repo(repo)
        if project is None:
            return None
        latest = await ReviewRunRepository(session).latest_run_for_pr(project.id, pr_number)
        return latest.base_sha if latest else None


async def _handle_comment(state: AppState, cm: Any) -> dict[str, Any]:
    commands = parse_commands(cm.body)
    if not commands or not cm.is_pull_request:
        return {"status": "ignored", "reason": "コマンドなし"}
    results: list[dict[str, Any]] = []
    async with state.session_factory() as session, session.begin():
        project = await ProjectRepository(session).get_by_repo(cm.repo_full_name)
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
            if cmd.kind == "answer":
                outcome = await _record_answer(session, cmd.args, cm, project, latest)
                results.append({"kind": cmd.kind, **outcome})
                continue
            results.append({"kind": cmd.kind, "accepted": True})
    # 承認・回答・再実行のあとは最新 head で再検証する（回答は親 run を持つ子 run として）
    if latest is not None and any(r["accepted"] for r in results):
        answered = [r for r in results if r["kind"] == "answer" and r["accepted"]]
        run_id = await state.service.create_run(
            repo_full_name=cm.repo_full_name,
            head_sha=latest.head_sha,
            base_sha=latest.base_sha,
            pr_number=cm.issue_number,
            trigger="comment",
            installation_id=cm.installation_id,
            parent_run_id=answered[0]["parent_run_id"] if answered else None,
        )
        return {"status": "queued", "run_id": str(run_id), "commands": results}
    return {"status": "handled", "commands": results}


async def _record_answer(
    session: Any, args: tuple[str, ...], cm: Any, project: Any, latest: Any
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
