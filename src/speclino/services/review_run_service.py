"""ReviewRunService: Webhook / API から run を作り、worker が実行して Check を更新する。

worker の手順（決定的オーケストレータ）:
  0 DB で実行権を原子的に取得（再配信は無視）
  1 head / base のスナップショットを取得し固定
  2 base の Spec IR と head の差分を照合して承認状態を付け替える（S18）
  3 run_check（validator → 解析 → 検査 → 規約）
  4 未解決があればエージェントが証拠を集め、再評価（S23）
  5 gate（フェイルクローズ）
  6 鮮度確認（AC-011）: superseded / 最新でなければ公開しない
  7 reporter → Check Run 更新、成果物保存、run_events / findings を DB に保存
"""

from __future__ import annotations

import json
import tempfile
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from uuid import UUID

from google.adk.models.base_llm import BaseLlm
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from speclino.adapters.github.checks_mapping import to_check_state
from speclino.agent.app import DEFAULT_MODEL
from speclino.agent.permissions import AgentPermissions
from speclino.agent.tools import InvestigationContext
from speclino.analysis.limits import DEFAULT_LIMITS
from speclino.conformance.engine import EngineInputError
from speclino.conformance.gate import DEFAULT_FAIL_ON, GatePolicy, GateResult, aggregate_gate
from speclino.db.repositories import (
    ApprovalRepository,
    AuditRepository,
    FindingRepository,
    ProjectRepository,
    QuestionRepository,
    ReviewRunRepository,
    RunEventRepository,
)
from speclino.domain.code_ir import AnalysisLimits
from speclino.domain.finding import Finding, Findings
from speclino.domain.policy import PolicyRevision
from speclino.domain.run import ExecState, GateStatus
from speclino.domain.semantic_diff import SemanticDiff
from speclino.domain.spec_ir import ApprovalRecord, SpecIR
from speclino.ports.blob_store import BlobStore
from speclino.ports.code_host import CodeHost
from speclino.ports.sandbox import Sandbox
from speclino.ports.task_queue import TaskQueue
from speclino.reporter.check_report import (
    COMMENT_MARKER,
    check_annotations,
    render_check,
    render_pr_comment,
)
from speclino.services.check_pipeline import (
    CheckInputError,
    CheckRequest,
    CheckResult,
    rediff,
    run_check,
    write_result,
)
from speclino.services.cost_guard import CostGuard
from speclino.services.investigation import InvestigationResult, investigate
from speclino.services.obligation_diff import effective_spec
from speclino.services.policy_governance import team_of
from speclino.services.run_freshness import should_publish
from speclino.spec import load_spec_ir
from speclino.telemetry import configure_tracing, span

CHECK_NAME = "SpecLino / gate"


@dataclass
class ReviewRunConfig:
    limits: AnalysisLimits = DEFAULT_LIMITS
    fail_on: tuple[str, ...] = DEFAULT_FAIL_ON
    permissions: AgentPermissions = field(default_factory=AgentPermissions)
    model: str | BaseLlm = DEFAULT_MODEL
    details_url_base: str | None = None  # 例 https://speclino.example/runs
    agent_enabled: bool = True
    sandbox: Sandbox | None = None  # test_run（無ければツールを出さない）
    # S42: run の実行前に active なチーム規約改訂を取る。DB アクセスはこの service に
    # 直書きせず、呼び出し可能なものとして注入する（実体は api/deps.py が DB から渡す）。
    # None なら従来どおりチーム規約を適用しない（.speclino/policy/ だけを実行する）
    resolve_team_policy: Callable[[str], Awaitable[PolicyRevision | None]] | None = None


@dataclass
class ReviewRunService:
    session_factory: async_sessionmaker[AsyncSession]
    code_host: CodeHost
    queue: TaskQueue
    blobs: BlobStore
    config: ReviewRunConfig = field(default_factory=ReviewRunConfig)
    cost_guard: CostGuard | None = None

    # ------------------------------------------------------------ create
    async def create_run(
        self,
        *,
        repo_full_name: str,
        head_sha: str,
        base_sha: str | None,
        pr_number: int | None,
        trigger: str,
        installation_id: int | None = None,
        default_branch: str = "main",
        settings: dict[str, Any] | None = None,
        parent_run_id: UUID | None = None,
    ) -> UUID:
        """run を queued で作り、同じ PR の未完了 run を superseded にして enqueue する。

        parent_run_id を持つ子 run は、親（とその祖先）の回答済み質問を引き継いで再開する。
        """
        async with self.session_factory() as session, session.begin():
            projects = ProjectRepository(session)
            project = await projects.upsert(
                repo_full_name=repo_full_name,
                installation_id=installation_id,
                default_branch=default_branch,
                settings=settings or {},
            )
            runs = ReviewRunRepository(session)
            run = await runs.create(
                project_id=project.id,
                pr_number=pr_number,
                head_sha=head_sha,
                base_sha=base_sha,
                trigger=trigger,
                parent_run_id=parent_run_id,
            )
            superseded = await runs.supersede_open_runs(project.id, pr_number, run.id)
            await RunEventRepository(session).append(
                run.id,
                "state",
                {"exec_state": "queued", "trigger": trigger, "superseded": superseded},
            )
            run_id = run.id
        await self.queue.enqueue(run_id)
        return run_id

    # ------------------------------------------------------------ execute
    async def execute(self, run_id: UUID) -> None:
        configure_tracing()
        with span("speclino.review_run", run_id=str(run_id)):
            await self._execute(run_id)

    async def _execute(self, run_id: UUID) -> None:
        async with self.session_factory() as session, session.begin():
            runs = ReviewRunRepository(session)
            if not await runs.try_acquire(run_id):
                return  # 再配信または他プロセスが実行中
            run = await runs.get(run_id)
            assert run is not None
            project = await ProjectRepository(session).get(run.project_id)
            assert project is not None
            repo_full_name = project.repo_full_name
            approvals = await ApprovalRepository(session).list_for_project(
                project.id, "approve_spec"
            )
            server_approvals = [
                ApprovalRecord(
                    id=f"pr-comment:{a.id}",
                    approver=a.approver,
                    approved_at=a.created_at,
                    source="pr_comment",
                    spec_content_hash=a.content_hash or "sha256:unknown",
                    ir_content_hash=a.target,
                    evidence_url=a.comment_url,
                )
                for a in approvals
            ]
            events = RunEventRepository(session)
            await events.append(run_id, "state", {"exec_state": "running"})
            head_sha, base_sha, pr_number = run.head_sha, run.base_sha, run.pr_number
            answers = await _collect_answers(session, run.parent_run_id)
            bindings_patch = {
                a["option"]: a["symbol"]
                for a in answers
                if a.get("kind") == "binding" and a.get("option") not in (None, "none")
            }
            for a in answers:
                await events.append(run_id, "human_answer", a)

        details_url = (
            f"{self.config.details_url_base}/{run_id}" if self.config.details_url_base else None
        )
        check_run_id = await self.code_host.create_check(
            repo_full_name, head_sha, CHECK_NAME, details_url, str(run_id)
        )
        async with self.session_factory() as session, session.begin():
            await ReviewRunRepository(session).set_check_run_id(run_id, check_run_id)
        await self.code_host.update_check(
            repo_full_name,
            check_run_id,
            "in_progress",
            None,
            "検証中",
            "スナップショットを固定して解析しています",
            "",
            [],
        )

        exec_state: ExecState = "completed"
        questions: list[dict[str, Any]] = []
        gate: GateResult | None = None
        result: CheckResult | None = None
        findings: Findings | None = None
        semantic: SemanticDiff | None = None
        agent_events: list[dict[str, Any]] = []
        error: str | None = None
        artifacts_uri: str | None = None
        try:
            with tempfile.TemporaryDirectory(prefix="speclino-run-") as tmp:
                tmpdir = Path(tmp)
                head_root = await self.code_host.fetch_snapshot(
                    repo_full_name, head_sha, tmpdir / "head"
                )
                base_root = (
                    await self.code_host.fetch_snapshot(repo_full_name, base_sha, tmpdir / "base")
                    if base_sha
                    else None
                )
                overrides, extra = _reconcile_specs(base_root, head_root, server_approvals)
                team_policy: PolicyRevision | None = None
                if self.config.resolve_team_policy is not None:
                    team_policy = await self.config.resolve_team_policy(team_of(repo_full_name))
                req = CheckRequest(
                    repo_root=head_root,
                    head_sha=head_sha,
                    base_sha=base_sha,
                    limits=self.config.limits,
                    fail_on=tuple(self.config.fail_on),  # type: ignore[arg-type]
                    mode="blocking",
                    run_id=str(run_id),
                    spec_overrides=overrides,
                    extra_findings=tuple(extra),
                    bindings_patch=bindings_patch or None,
                    base_root=base_root,
                    team_policy=team_policy,
                )
                with span("speclino.check", head=head_sha):
                    result = run_check(req)
                findings = result.findings
                gate = result.gate
                semantic = result.semantic_diff
                # 未解決があればエージェントが証拠を集める（判定はエンジン）
                if self.config.agent_enabled and result.domain and result.bindings:
                    ctx = InvestigationContext(
                        repo_root=head_root,
                        domain=result.domain,
                        bindings=result.bindings,
                        contracts=result.contracts,
                        specs={s.behavior_id: s for s in result.spec_irs},
                        findings=findings,
                        code_irs={c.behavior_id: c for c in result.code_irs},
                        limits=self.config.limits,
                        base_root=base_root,
                        sandbox=self.config.sandbox,
                    )
                    cost_ok = (True, "")
                    if self.cost_guard is not None:
                        cost_ok = await self.cost_guard.can_start(datetime.now(UTC).date())
                    # エージェントの失敗で run 全体を落とさない。証拠が増えないだけで、
                    # 未解決は未解決のまま残りゲートが失敗する（design の「最悪でも
                    # 証拠不足 → 未検証 → ゲート失敗」）。LLM の 429/5xx や
                    # ネットワーク障害はここで吸収する。
                    try:
                        inv = await investigate(
                            ctx, self.config.permissions, self.config.model, cost_ok
                        )
                    except Exception as e:  # noqa: BLE001 - 証拠収集の失敗は判定に影響させない
                        inv = InvestigationResult(
                            started=False,
                            findings=findings,
                            reason=f"エージェントを実行できなかった: {type(e).__name__}: {e}",
                            events=[
                                {
                                    "type": "agent.error",
                                    "payload": {"error": f"{type(e).__name__}: {e}"[:500]},
                                }
                            ],
                        )
                    agent_events = inv.events
                    questions = inv.questions
                    if inv.started:
                        findings = _merge_non_behavior_findings(result.findings, ctx.findings)
                        # 証拠で verdict が変わり得るので、同じ base で意味差分を取り直す
                        semantic, findings = rediff(result, findings)
                        gate = _regate(findings, result, self.config.fail_on)
                        if inv.log is not None and self.cost_guard is not None:
                            await self.cost_guard.record(
                                datetime.now(UTC).date(),
                                str(run_id),
                                _model_name(self.config.model),
                                inv.log.input_tokens,
                                inv.log.output_tokens,
                            )
                out_dir = tmpdir / "out"
                out_dir.mkdir()
                final = CheckResult(
                    manifest=result.manifest,
                    spec_irs=result.spec_irs,
                    code_irs=result.code_irs,
                    findings=findings,
                    gate=gate,
                    validation=result.validation,
                    policy_findings=result.policy_findings,
                    semantic_diff=semantic,
                )
                paths = write_result(final, out_dir)
                (out_dir / "agent-events.json").write_text(
                    json.dumps(agent_events, ensure_ascii=False, indent=2, default=str)
                )
                paths["agent-events.json"] = out_dir / "agent-events.json"
                for name, path in paths.items():
                    uri = await self.blobs.put(f"runs/{run_id}/{name}", path.read_bytes())
                    artifacts_uri = uri.rsplit("/", 1)[0]
        except (CheckInputError, EngineInputError) as e:
            exec_state, error = "failed", f"入力不正: {e}"
        except Exception as e:  # noqa: BLE001 - 実行障害は適合ではなく失敗として記録する
            exec_state, error = "failed", f"実行障害: {type(e).__name__}: {e}"

        # 鮮度確認と公開
        async with self.session_factory() as session, session.begin():
            runs = ReviewRunRepository(session)
            current = await runs.get(run_id)
            assert current is not None
            latest = await runs.latest_run_for_pr(current.project_id, pr_number)
            fresh = should_publish(run_id, current.exec_state, latest.id if latest else None)
            events = RunEventRepository(session)
            for ev in agent_events:
                await events.append(run_id, ev["type"], ev["payload"])
            if findings is not None:
                await FindingRepository(session).replace_for_run(run_id, findings.findings)
            question_ids: list[str] = []
            if questions and exec_state == "completed":
                qrepo = QuestionRepository(session)
                for q in questions:
                    row = await qrepo.add(
                        run_id=run_id,
                        requirement_id=q.get("requirement_id"),
                        head_sha=head_sha,
                        text=q["question"],
                        options={k: v for k, v in q.items() if k != "question"},
                    )
                    question_ids.append(str(row.id))
                exec_state = "awaiting_answer"
            gate_status: GateStatus | None = gate.status if gate else None
            final_state: ExecState = (
                "superseded" if current.exec_state == "superseded" else exec_state
            )
            stored_manifest = result.manifest.model_dump(mode="json") if result else None
            if stored_manifest is not None:
                # UI 用の要約（ゲート理由・免除・エージェント予算）を manifest に同梱する
                stored_manifest["gate"] = (
                    {
                        "status": gate.status,
                        "reason": gate.reason,
                        "blocking_ids": [f.id for f in gate.blocking],
                        "advisory_ids": [f.id for f in gate.advisory],
                        "waived_ids": [f.id for f in gate.waived],
                    }
                    if gate
                    else None
                )
                agent_end = next((e for e in agent_events if e["type"] == "agent.end"), None)
                stored_manifest["agent"] = agent_end["payload"] if agent_end else None
                stored_manifest["questions"] = question_ids
                diff = semantic
                stored_manifest["semantic_diff"] = (
                    {
                        "base_available": diff.base_available,
                        "base_error": diff.base_error,
                        "counts": diff.counts.model_dump(mode="json"),
                    }
                    if diff
                    else None
                )
            await runs.set_result(
                run_id,
                final_state,
                gate_status if exec_state in ("completed", "awaiting_answer") else None,
                stored_manifest,
                artifacts_uri,
            )
            await events.append(
                run_id,
                "state",
                {
                    "exec_state": final_state,
                    "gate": gate_status,
                    "error": error,
                    "publish": fresh.decision,
                },
            )
            if not fresh.publish:
                await AuditRepository(session).record(
                    "worker",
                    "publish.skip",
                    str(run_id),
                    "skipped",
                    {"reason": fresh.reason},
                    run_id,
                )

        # Check Run の更新（公開してよいときだけ現在の結果として反映）
        if (
            fresh.publish
            and findings is not None
            and gate is not None
            and exec_state == "completed"
        ):
            status, conclusion = to_check_state(gate.status, "completed")
            text = render_check(findings, gate, semantic)
            await self.code_host.update_check(
                repo_full_name,
                check_run_id,
                status,
                conclusion,
                text.title,
                text.summary,
                text.text,
                check_annotations(findings),
            )
            # 会話タブでも判定と反例が読めるようにする。push のたびに増やさないよう、
            # 目印（COMMENT_MARKER）つきの既存コメントを更新する。
            if pr_number is not None:
                await self.code_host.upsert_comment(
                    repo_full_name,
                    pr_number,
                    COMMENT_MARKER,
                    render_pr_comment(findings, gate, details_url, semantic),
                )
        elif fresh.publish and exec_state == "awaiting_answer" and findings is not None:
            body = _question_comment(questions, question_ids)
            if pr_number is not None:
                await self.code_host.post_comment(repo_full_name, pr_number, body)
            status, conclusion = to_check_state(None, "awaiting_answer")
            await self.code_host.update_check(
                repo_full_name,
                check_run_id,
                status,
                conclusion,
                f"回答待ち（質問 {len(questions)} 件）",
                body[:1500],
                render_check(findings, gate).text if gate else "",
                check_annotations(findings),
            )
        elif fresh.publish:
            status, conclusion = to_check_state(None, exec_state)
            await self.code_host.update_check(
                repo_full_name,
                check_run_id,
                status,
                conclusion,
                "検証を完了できなかった",
                error or "実行障害",
                "",
                [],
            )
        else:
            await self.code_host.update_check(
                repo_full_name,
                check_run_id,
                "completed",
                "cancelled",
                "古い実行",
                fresh.reason,
                "",
                [],
            )


async def _collect_answers(
    session: AsyncSession, parent_run_id: UUID | None
) -> list[dict[str, Any]]:
    """親 run（とその祖先、最大 3 世代）の回答済み質問を集める。"""
    answers: list[dict[str, Any]] = []
    runs = ReviewRunRepository(session)
    qrepo = QuestionRepository(session)
    current = parent_run_id
    for _ in range(3):
        if current is None:
            break
        for q in await qrepo.list_for_run(current, "answered"):
            if q.answer:
                answers.append(
                    {
                        "question_id": str(q.id),
                        "kind": q.options.get("kind"),
                        "symbol": q.options.get("symbol"),
                        "requirement_id": q.requirement_id,
                        **q.answer,
                    }
                )
        parent = await runs.get(current)
        current = parent.parent_run_id if parent else None
    return answers


def _question_comment(questions: list[dict[str, Any]], ids: list[str]) -> str:
    lines = [
        "SpecLino からの確認事項です。承認者は次の形式で回答してください: "
        "`/speclino answer Q-<ID> <選択肢>`",
        "",
    ]
    for q, qid in zip(questions, ids, strict=True):
        short = qid[:8]
        lines.append(f"### Q-{short}（{q.get('requirement_id') or '-'} / {q['kind']}）")
        lines.append(q["question"])
        if q.get("symbol"):
            lines.append(f"- 対象シンボル: `{q['symbol']}`")
        lines.append("- 選択肢: " + " / ".join(f"`{o}`" for o in q["options"]))
        lines.append("")
    lines.append(
        "仕様の意味を変える回答はできません。必要なら specs/ と specs/ir を修正してください。"
    )
    return "\n".join(lines)


def _reconcile_specs(
    base_root: Path | None, head_root: Path, server_approvals: list[ApprovalRecord]
) -> tuple[dict[str, SpecIR], list[Finding]]:
    """base/head の Spec IR を差分照合し、承認状態を付け替えた Spec IR と削除 Finding を返す。"""
    overrides: dict[str, SpecIR] = {}
    extra: list[Finding] = []
    head_dir = head_root / "specs" / "ir"
    if not head_dir.is_dir():
        return overrides, extra
    base_specs: dict[str, SpecIR] = {}
    if base_root is not None and (base_root / "specs" / "ir").is_dir():
        for p in sorted((base_root / "specs" / "ir").glob("*.spec-ir.json")):
            s = load_spec_ir(p)
            base_specs[s.behavior_id] = s
    for p in sorted(head_dir.glob("*.spec-ir.json")):
        head_spec = load_spec_ir(p)
        base_spec = base_specs.get(head_spec.behavior_id)
        spec, _changes, removed = effective_spec(base_spec, head_spec, server_approvals)
        overrides[head_spec.behavior_id] = spec
        extra.extend(removed)
    return overrides, extra


def _merge_non_behavior_findings(original: Findings, updated: Findings) -> Findings:
    """エージェントが差し替えた behavior の Finding と、規約などそれ以外の Finding を合わせる。"""
    behavior_ids = {f.behavior_id for f in updated.findings}
    others = [f for f in original.findings if f.behavior_id not in behavior_ids]
    merged = [*updated.findings, *others]
    from speclino.domain.finding import VerdictCounts

    counts = VerdictCounts()
    for f in merged:
        counts = counts.model_copy(update={f.verdict: getattr(counts, f.verdict) + 1})
    return original.model_copy(update={"findings": merged, "counts": counts})


def _regate(findings: Findings, result: CheckResult, fail_on: tuple[str, ...]) -> GateResult:
    """エージェントの証拠で Findings が変わった後にゲートを取り直す。

    level と免除は `run_check` が決めたものをそのまま使う（`CheckResult.gate_levels` /
    `waived_finding_ids`）。ここで作り直すと 2 か所の計算がずれる: 以前は仕様側の level を
    先に入れて規約を `setdefault` していたため、PR で同じ ID の advisory な要求を足すだけで
    チーム規約の mandatory を advisory に落とせた（承認も不要）。免除も渡していなかったので、
    エージェントが動いた run では例外が効かなくなっていた（どちらも codex レビューで実測）。
    """
    return aggregate_gate(
        findings,
        result.gate_levels,
        GatePolicy(fail_on=tuple(fail_on), mode="blocking"),  # type: ignore[arg-type]
        result.waived_finding_ids,
    )


def _model_name(model: str | BaseLlm) -> str:
    return model if isinstance(model, str) else model.model


__all__ = ["CHECK_NAME", "ReviewRunConfig", "ReviewRunService"]
