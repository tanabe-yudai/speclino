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
  8 仕様 PR bot（S46）: 仕様 Markdown が変わった PR に Spec IR の候補をコメント
    （判定・Check Run には影響しない。`services/spec_pr_bot.py`）
"""

from __future__ import annotations

import json
import tempfile
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field, replace
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
from speclino.db.models import Project
from speclino.db.repositories import (
    ApprovalRepository,
    AuditRepository,
    FindingRepository,
    ProjectRepository,
    QuestionRepository,
    ReviewRunRepository,
    RunEventRepository,
)
from speclino.db.repositories.assessments import SessionFactoryAssessmentStore
from speclino.domain.code_ir import AnalysisLimits
from speclino.domain.finding import Finding, Findings
from speclino.domain.knowledge import ResolvedKnowledge
from speclino.domain.policy import PolicyRevision
from speclino.domain.run import ExecState, GateStatus
from speclino.domain.semantic_diff import SemanticDiff
from speclino.domain.spec_ir import ApprovalRecord, SpecIR
from speclino.ports.blob_store import BlobStore
from speclino.ports.code_host import CodeHost
from speclino.ports.sandbox import Sandbox
from speclino.ports.task_queue import TaskQueue
from speclino.ports.text_llm import TextLlm
from speclino.reporter.check_report import (
    COMMENT_MARKER,
    check_annotations,
    render_check,
    render_pr_comment,
)
from speclino.services.attestations import list_attestations, self_attested_ids
from speclino.services.check_pipeline import (
    CheckInputError,
    CheckRequest,
    CheckResult,
    llm_assisted_rules,
    rediff,
    run_check,
    write_result,
)
from speclino.services.cost_guard import CostGuard
from speclino.services.investigation import InvestigationResult, investigate
from speclino.services.knowledge_inputs import KnowledgeResolver
from speclino.services.llm_policies import (
    DEFAULT_MAX_CALLS_PER_RUN,
    AssessmentStore,
    CallBudget,
    assess,
    checklist_params,
    unknown_finding,
)
from speclino.services.llm_units import extract_units
from speclino.services.obligation_diff import UNKNOWN_SPEC_HASH, effective_spec
from speclino.services.project_settings import parse_stored
from speclino.services.replay_service import DETERMINISTIC_FINDINGS
from speclino.services.run_freshness import should_publish
from speclino.services.spec_pr_bot import (
    SPEC_BOT_EVENT,
    SPEC_BOT_MARKER,
    SpecBotInput,
    collect_spec_changes,
    generate_candidates,
    render_no_changes_comment,
    render_spec_bot_comment,
)
from speclino.services.workspaces import repository_mismatch
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
    # S46: 仕様 PR bot が候補を作る LLM。None なら bot を動かさない
    spec_llm: TextLlm | None = None
    # S65: ワークスペースに登録されたプロジェクトの run は、開始時に bundle を 1 つ固定する
    # （(workspace, project_id) → ResolvedKnowledge。実体は make_knowledge_resolver）。
    # workspace と project は DB の project 行から取る（リクエストの値は使わない）。None なら従来の
    # Git の入力（仕様は specs/、チーム規約は resolve_team_policy）
    resolve_knowledge: KnowledgeResolver | None = None
    # S70: LLM 支援の規約（tier=llm_assisted）を評価する LLM。None なら評価せず、そのルールは
    # UNKNOWN（ゲートは通らない）。評価の記録は DB（llm_assessments）に残し、同じキーは呼ばない
    policy_llm: TextLlm | None = None
    policy_llm_timeout_s: float = 60.0
    policy_llm_max_calls: int = DEFAULT_MAX_CALLS_PER_RUN


@dataclass
class ReviewRunService:
    session_factory: async_sessionmaker[AsyncSession]
    code_host: CodeHost
    queue: TaskQueue
    blobs: BlobStore
    config: ReviewRunConfig = field(default_factory=ReviewRunConfig)
    cost_guard: CostGuard | None = None

    async def _trusted_side(
        self,
        repo: str,
        default_branch: str,
        base_sha: str | None,
        base_root: Path | None,
        tmpdir: Path,
    ) -> tuple[str | None, Path | None]:
        """bundle の run の信頼側: code_host から取ったデフォルトブランチの先端（S65）。

        リクエスト・webhook・前の run から来た base_sha は信頼側に使わない（PR の作者が選べる）。
        先端が base_sha と同じときだけ、取得済みの base のスナップショットを使い回す。取れなければ
        (None, None) で、run は対応・契約・免除・規約を読まずに REVIEW になる（フェイルクローズ）。
        """
        try:
            sha = await self.code_host.branch_head(repo, default_branch)
            if sha is None:
                return None, None
            if base_root is not None and sha == base_sha:
                return sha, base_root
            return sha, await self.code_host.fetch_snapshot(repo, sha, tmpdir / "trusted")
        except Exception:  # noqa: BLE001 - 取れなければ信頼側なし（REVIEW）にする
            return None, None

    # ------------------------------------------------------------ create
    async def create_run(
        self,
        *,
        project_id: UUID,
        head_sha: str,
        base_sha: str | None,
        pr_number: int | None,
        trigger: str,
        parent_run_id: UUID | None = None,
    ) -> UUID:
        """既存 project の run を作って enqueue する（API・MCP・デモ再実行用）。

        project は ID で受け取る（S53）。呼び出し側が所属を確かめた project と別の行に run を
        作らないよう、名前で引き直さない。project を作るのは、リポジトリ ID を持つ webhook
        （`ProjectRepository.resolve_for_webhook`）だけ。
        """
        async with self.session_factory() as session, session.begin():
            project = await ProjectRepository(session).get(project_id)
            if project is None:
                raise ValueError(f"project {project_id} が無い")
            run_id = await self.create_run_in(
                session,
                project=project,
                head_sha=head_sha,
                base_sha=base_sha,
                pr_number=pr_number,
                trigger=trigger,
                parent_run_id=parent_run_id,
            )
        await self.queue.enqueue(run_id)
        return run_id

    async def create_run_in(
        self,
        session: AsyncSession,
        *,
        project: Project,
        head_sha: str,
        base_sha: str | None,
        pr_number: int | None,
        trigger: str,
        parent_run_id: UUID | None = None,
        pr_author_id: int | None = None,
    ) -> UUID:
        """呼び出し側のトランザクションの中で run を queued で作る。enqueue はしない。

        `pr_author_id`（PR の作成者の GitHub ユーザー ID。S70 の自己確定の照合）が無ければ、
        同じ PR の前の run から引き継ぐ（コメント・再実行・確定の後の再検査）。

        呼び出し側は commit のあとで `queue.enqueue(run_id)` する（webhook は重複排除の記録と
        run の作成を 1 つのトランザクションにするため、これを直接使う。S53）。
        同じ PR の未完了 run は superseded にする。parent_run_id を持つ子 run は、親（とその
        祖先）の回答済み質問を引き継いで再開する。親は同じ project の run に限る（別リポジトリの
        回答を取り込ませない。S52）。
        """
        runs = ReviewRunRepository(session)
        if parent_run_id is not None:
            parent = await runs.get(parent_run_id)
            if parent is None or parent.project_id != project.id:
                raise ValueError(
                    f"親 run {parent_run_id} は {project.repo_full_name} の run ではない"
                )
        if pr_author_id is None and pr_number is not None:
            previous = await runs.latest_run_for_pr(project.id, pr_number)
            pr_author_id = previous.pr_author_id if previous is not None else None
        run = await runs.create(
            project_id=project.id,
            pr_number=pr_number,
            head_sha=head_sha,
            base_sha=base_sha,
            trigger=trigger,
            parent_run_id=parent_run_id,
            pr_author_id=pr_author_id,
        )
        superseded = await runs.supersede_open_runs(project.id, pr_number, run.id)
        await RunEventRepository(session).append(
            run.id,
            "state",
            {"exec_state": "queued", "trigger": trigger, "superseded": superseded},
        )
        return run.id

    # ------------------------------------------------------------ execute
    async def execute(self, run_id: UUID) -> None:
        configure_tracing()
        with span("speclino.review_run", run_id=str(run_id)):
            await self._execute(run_id)

    async def _fail_before_start(self, run_id: UUID, reason: str) -> None:
        """スナップショットの取得より前に run を失敗で終える。Check は作らない（公開しない）。"""
        async with self.session_factory() as session, session.begin():
            await ReviewRunRepository(session).set_result(run_id, "failed", None, None, None)
            await RunEventRepository(session).append(
                run_id, "state", {"exec_state": "failed", "error": reason, "publish": "skip"}
            )
            await AuditRepository(session).record(
                "worker",
                "run.repository_mismatch",
                str(run_id),
                "denied",
                {"reason": reason},
                run_id,
            )

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
            project_id = project.id
            github_repo_id = project.github_repo_id
            default_branch = project.default_branch
            # チーム規約は登録先ワークスペースのもの。未登録（None）なら当てない（S53）
            team = project.team
            # S47: project の設定は証拠集めを止める方向にだけ効く（サーバ全体の設定と AND）。
            # 保存値が型に合わなければ止めた側（parse_stored の LOCKED_DOWN）
            project_settings = parse_stored(project.settings).settings
            approvals = await ApprovalRepository(session).list_for_project(
                project.id, "approve_spec"
            )
            server_approvals = [
                ApprovalRecord(
                    id=f"pr-comment:{a.id}",
                    approver=a.approver,
                    approved_at=a.created_at,
                    source="pr_comment",
                    spec_content_hash=a.content_hash or UNKNOWN_SPEC_HASH,
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
            # S70: 人の確定は run の開始時に読む（確定の後は再検査の run が作られる）
            attestations = await list_attestations(session, team) if team is not None else []

        mismatch = await repository_mismatch(self.code_host, repo_full_name, github_repo_id)
        if mismatch is not None:
            await self._fail_before_start(run_id, mismatch)
            return

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
        spec_bot_input: SpecBotInput | None = None
        spec_bot_error: str | None = None
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
                # 仕様 PR bot の入力（変更された仕様 Markdown）はスナップショットがある間に
                # 集めておき、LLM の呼出しは Check の公開後に行う（判定を待たせない）
                if self.config.spec_llm is not None and pr_number is not None:
                    try:
                        spec_bot_input = collect_spec_changes(base_root, head_root)
                    except Exception as e:  # noqa: BLE001 - bot の失敗で run を落とさない
                        spec_bot_error = f"{type(e).__name__}: {e}"[:500]
                # S65: bundle を固定する（この run の中では再解決しない）。仕様の正本は bundle に
                # あるので、Git の仕様の承認の付け替え（_reconcile_specs）は行わない
                knowledge: ResolvedKnowledge | None = None
                trusted_root: Path | None = None
                trusted_sha: str | None = None
                if self.config.resolve_knowledge is not None and team is not None:
                    knowledge = await self.config.resolve_knowledge(team, str(project_id))
                    trusted_sha, trusted_root = await self._trusted_side(
                        repo_full_name, default_branch, base_sha, base_root, tmpdir
                    )
                overrides: dict[str, SpecIR] | None = None
                extra: list[Finding] = []
                team_policy: PolicyRevision | None = None
                if knowledge is None:
                    overrides, extra = _reconcile_specs(base_root, head_root, server_approvals)
                    if self.config.resolve_team_policy is not None and team is not None:
                        team_policy = await self.config.resolve_team_policy(team)
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
                    knowledge=knowledge,
                    github_repo_id=github_repo_id,
                    trusted_root=trusted_root,
                    trusted_sha=trusted_sha,
                    attestations=tuple(attestations),
                )
                llm_findings, llm_keys = await self._assess_llm_policies(
                    req, str(run_id), team if team is not None else f"project:{project_id}"
                )
                req = replace(req, llm_findings=llm_findings, llm_assessment_keys=tuple(llm_keys))
                with span("speclino.check", head=head_sha):
                    result = run_check(req)
                findings = result.findings
                gate = result.gate
                semantic = result.semantic_diff
                # 未解決があればエージェントが証拠を集める（判定はエンジン）
                agent_enabled = self.config.agent_enabled and project_settings.agent_enabled
                permissions = replace(
                    self.config.permissions,
                    test_run_enabled=self.config.permissions.test_run_enabled
                    and project_settings.test_run_enabled,
                )
                if agent_enabled and result.domain and result.bindings:
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
                        sandbox=self.config.sandbox if permissions.test_run_enabled else None,
                    )
                    cost_ok = (True, "")
                    if self.cost_guard is not None:
                        cost_ok = await self.cost_guard.can_start(datetime.now(UTC).date())
                    # エージェントの失敗で run 全体を落とさない。証拠が増えないだけで、
                    # 未解決は未解決のまま残りゲートが失敗する（design の「最悪でも
                    # 証拠不足 → 未検証 → ゲート失敗」）。LLM の 429/5xx や
                    # ネットワーク障害はここで吸収する。
                    try:
                        inv = await investigate(ctx, permissions, self.config.model, cost_ok)
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
                    waived_finding_ids=result.waived_finding_ids,
                    cleared_finding_ids=result.cleared_finding_ids,
                    waivers=result.waivers,
                )
                paths = write_result(final, out_dir)
                # エージェントの調査前の決定的な Findings（replay はこれと比べる。S65）
                deterministic = out_dir / DETERMINISTIC_FINDINGS
                deterministic.write_text(result.findings.model_dump_json(indent=2))
                paths[DETERMINISTIC_FINDINGS] = deterministic
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
                        # 阻止しない REVIEW（LLM 支援の指摘なし・人の確定）。画面の件数と理由の正本
                        "cleared_ids": sorted(f.id for f in gate.cleared),
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

        # 仕様 PR bot（S46）。古い run は出さない（新しい run が出す）。失敗しても run の
        # 結果・Check は変えず、イベントに残すだけ
        if fresh.publish and pr_number is not None and self.config.spec_llm is not None:
            try:
                if spec_bot_error is not None:
                    raise RuntimeError(spec_bot_error)
                if spec_bot_input is not None:
                    await self._run_spec_bot(
                        run_id, project_id, repo_full_name, pr_number, head_sha, spec_bot_input
                    )
            except Exception as e:  # noqa: BLE001 - bot の失敗は判定に影響させない
                async with self.session_factory() as session, session.begin():
                    await RunEventRepository(session).append(
                        run_id,
                        SPEC_BOT_EVENT,
                        {"status": "error", "error": f"{type(e).__name__}: {e}"[:500]},
                    )

    def _assessment_store(self, workspace: str) -> AssessmentStore:
        return SessionFactoryAssessmentStore(self.session_factory, workspace)

    async def _assess_llm_policies(
        self, req: CheckRequest, run_id: str, workspace: str
    ) -> tuple[dict[str, list[Finding]] | None, list[str]]:
        """LLM 支援の規約を run_check の前に評価する（S70）。LLM の設定が無ければ None。

        単位は head のスナップショットから AST で取り出す（対象のコードは import しない）。
        ルールのパラメーターの誤り・取り出しの失敗はそのルールの UNKNOWN にして run を続ける。
        評価の記録はワークスペースごと（`workspace`。未登録のプロジェクトは `project:<id>`）に
        区切る。呼出しの上限は 1 run で全ルールが共有する。
        """
        rules = llm_assisted_rules(req)
        llm = self.config.policy_llm
        if not rules or llm is None:
            return None, []
        store = self._assessment_store(workspace)
        calls = CallBudget(self.config.policy_llm_max_calls)
        out: dict[str, list[Finding]] = {}
        keys: list[str] = []
        for target in rules:
            rule = target.rule
            try:
                params = checklist_params(rule)
                units = extract_units(req.repo_root, params, rule.applies_to.paths)
                found, used = await assess(
                    rule,
                    units.units,
                    llm=llm,
                    store=store,
                    policy_revision=target.policy_revision,
                    cost_guard=self.cost_guard,
                    run_id=run_id,
                    timeout_s=self.config.policy_llm_timeout_s,
                    unassessable=units.problems,
                    origin=target.origin,
                    call_budget=calls,
                )
            except Exception as e:  # noqa: BLE001 - そのルールの UNKNOWN にして run を続ける
                found, used = (
                    [
                        unknown_finding(
                            rule,
                            rule.id,
                            f"{type(e).__name__}: {e}"[:300],
                            suffix="error",
                            path=None,
                        )
                    ],
                    [],
                )
            out[rule.id] = found
            keys.extend(used)
        return out, keys

    async def _run_spec_bot(
        self,
        run_id: UUID,
        project_id: UUID,
        repo_full_name: str,
        pr_number: int,
        head_sha: str,
        inp: SpecBotInput,
    ) -> None:
        """変更された仕様の候補をコメントする。状態は run_events（type=spec_bot）だけに持つ。

        同じ PR の直前の記録と入力ハッシュが同じなら、コメントは最新なので Gemini を呼ばない
        （承認や回答による再実行のたびに費用をかけない）。仕様の変更が無くなった head では、
        以前に出したコメントだけを「変更なし」に更新する（無ければ何も出さない）。
        """
        assert self.config.spec_llm is not None
        record = self._record_spec_bot
        async with self.session_factory() as session:
            last = await RunEventRepository(session).latest_for_pr(
                project_id, pr_number, SPEC_BOT_EVENT
            )
        last_status = last.payload.get("status") if last else None
        payload: dict[str, Any]
        if not inp.targets:
            if last_status in (None, "no_changes"):
                return
            if not await self._still_latest(run_id, project_id, pr_number):
                await record(run_id, {"status": "stale", "input_hash": inp.input_hash})
                return
            await self.code_host.upsert_comment(
                repo_full_name, pr_number, SPEC_BOT_MARKER, render_no_changes_comment(head_sha)
            )
            payload = {"status": "no_changes", "input_hash": inp.input_hash}
        elif (
            last is not None
            and last_status in ("posted", "unchanged")
            and last.payload.get("input_hash") == inp.input_hash
        ):
            payload = {"status": "unchanged", "input_hash": inp.input_hash}
        else:
            outcomes = await generate_candidates(
                inp, self.config.spec_llm, self.cost_guard, str(run_id), datetime.now(UTC).date()
            )
            # 生成の間に同じ PR の新しい run ができていたら投稿しない（新しい run が出す。
            # 古い head の候補で上書きしない）。鮮度は投稿の直前に取り直す
            if not await self._still_latest(run_id, project_id, pr_number):
                await record(run_id, {"status": "stale", "input_hash": inp.input_hash})
                return
            await self.code_host.upsert_comment(
                repo_full_name,
                pr_number,
                SPEC_BOT_MARKER,
                render_spec_bot_comment(outcomes, head_sha),
            )
            failed = [o for o in outcomes if o.result is None]
            payload = {
                # 失敗があれば次の run で作り直す（"posted" だけが再利用の対象）
                "status": "failed" if failed else "posted",
                "input_hash": inp.input_hash,
                "targets": [o.target.path for o in outcomes],
                "errors": [o.error for o in failed],
            }
        await record(run_id, payload)

    async def _still_latest(self, run_id: UUID, project_id: UUID, pr_number: int) -> bool:
        async with self.session_factory() as session:
            latest = await ReviewRunRepository(session).latest_run_for_pr(project_id, pr_number)
        return latest is None or latest.id == run_id

    async def _record_spec_bot(self, run_id: UUID, payload: dict[str, Any]) -> None:
        async with self.session_factory() as session, session.begin():
            await RunEventRepository(session).append(run_id, SPEC_BOT_EVENT, payload)


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
    gate = aggregate_gate(
        findings,
        result.gate_levels,
        GatePolicy(fail_on=tuple(fail_on), mode="blocking"),  # type: ignore[arg-type]
        result.waived_finding_ids,
        result.cleared_finding_ids,
    )
    # 自己確定の印は今の Findings（エージェントの後）から数え直す
    attestations = result.manifest.attestations
    return replace(
        gate,
        self_attested_ids=self_attested_ids(findings, attestations, True),
        self_attest_unverified_ids=self_attested_ids(findings, attestations, None),
    )


def _model_name(model: str | BaseLlm) -> str:
    return model if isinstance(model, str) else model.model


__all__ = ["CHECK_NAME", "ReviewRunConfig", "ReviewRunService"]
