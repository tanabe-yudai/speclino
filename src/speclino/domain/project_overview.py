"""プロジェクト画面（S45）の応答の型。組み立ては `services/project_overview.py`。

design/2026-09-30-ui-redesign.md 5.2 節。判定は検査器の findings をそのまま使い、ここでは作らない。
"""

from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import Field

from speclino.domain.common import IRModel
from speclino.domain.finding import Verdict, VerdictCounts
from speclino.domain.policy import PolicyLevel
from speclino.domain.run import ExecState, GateStatus

ApprovalState = Literal["approved", "unapproved", "stale"]
PolicyOrigin = Literal["team", "project"]
OverviewStatus = Literal["ok", "not_checked", "unavailable"]


class RunView(IRModel):
    id: str
    pr_number: int | None
    head_sha: str
    exec_state: ExecState
    gate_status: GateStatus | None
    created_at: datetime | None


class RequirementView(IRModel):
    id: str
    behavior_id: str
    title: str | None
    spec_path: str | None
    line_start: int | None
    line_end: int | None
    approval: ApprovalState
    tests_declared: int = Field(ge=0)
    verdict: Verdict | None = Field(description="findings の最も重い判定。None は未解析")
    finding_ids: list[str] = Field(default_factory=list)
    removed: bool = Field(default=False, description="head の Spec IR から消えた要求")


class PolicyView(IRModel):
    rule_id: str
    origin: PolicyOrigin
    title: str | None
    level: PolicyLevel | None = Field(
        description="分からなければ None（プロジェクト固有で成立したもの）"
    )
    has_verifier: bool | None
    status: Verdict | None = Field(description="None はこの検査で結果が無い")
    counts: VerdictCounts
    waived: int = Field(
        default=0, ge=0, description="例外（exceptions.yaml）で免除された finding の数"
    )


class ProjectSummary(IRModel):
    id: str
    repo_full_name: str
    default_branch: str
    status: OverviewStatus = Field(description="latest_run の成果物を読めたか（overview と同じ）")
    latest_run: RunView | None = Field(
        description="最新の完了した検査（completed / awaiting_answer）"
    )
    counts: VerdictCounts | None = Field(description="latest_run の要求ごとの判定の件数")
    unanalyzed: int | None = Field(
        default=None,
        description="finding の無い要求と抽出しきれなかった要求の数。読めなければ None",
    )


class ProjectOverview(IRModel):
    status: OverviewStatus
    project: ProjectSummary
    run: RunView | None = None
    counts: VerdictCounts | None = None
    unanalyzed: int = Field(
        default=0, description="finding の無い要求と、抽出しきれなかった要求の数"
    )
    unextracted: list[str] = Field(
        default_factory=list, description="Spec IR の unextracted（抽出しきれなかった要求 ID）"
    )
    unapproved: int = Field(default=0, description="承認済みでない要求（未承認と承認切れ）")
    policy_team: str | None = None
    policy_revision: int | None = None
    requirements: list[RequirementView] = Field(default_factory=list)
    policies: list[PolicyView] = Field(default_factory=list)
    runs: list[RunView] = Field(
        default_factory=list, description="その project の run（新しい順、上限 20）"
    )
