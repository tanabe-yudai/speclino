"""可観測性（S71、設計 9 章）の表示の型。組み立ては `services/observability.py`。

画面はこの 1 つの集計を表示するだけで、数え直さない。測っていないものは None（「未計測」）で、
0 や 100% にしない。
"""

from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import Field

from speclino.domain.common import IRModel
from speclino.domain.finding import VerdictCounts
from speclino.domain.policy import PolicyLevel
from speclino.domain.run import GateStatus

# UNKNOWN の理由の区分（Finding に構造化された欄が無いので、決まった言い回しから分類する）
UnknownReason = Literal[
    "unbound", "inexact_path", "analysis_limit", "missing_finding", "lock", "other"
]
# 規約の検査の種類: 決定的な検査器 / LLM 支援（llm.checklist）/ 検査器なし（助言のみ）
RuleTier = Literal["deterministic", "llm_assisted", "unverified"]


class Ratio(IRModel):
    """分子 / 分母。分母が 0 のときは作らない（呼び出し側が None = 未計測にする）。"""

    numerator: int = Field(ge=0)
    denominator: int = Field(ge=1)
    rate: float = Field(ge=0, le=1)


class UnknownBreakdown(IRModel):
    """UNKNOWN の Finding の理由ごとの件数。"""

    unbound: int = Field(default=0, ge=0, description="図・要求の要素に対応するコードが無い")
    inexact_path: int = Field(default=0, ge=0, description="経路が不完全・条件付きで確定できない")
    analysis_limit: int = Field(default=0, ge=0, description="ソルバー・経路数・模擬の上限")
    missing_finding: int = Field(default=0, ge=0, description="出るべき判定が出ていない（5.2）")
    lock: int = Field(default=0, ge=0, description="lock.json が無い・合わない")
    other: int = Field(default=0, ge=0)


class RunSummary(IRModel):
    run_id: str
    pr_number: int | None
    head_sha: str
    created_at: datetime
    gate_status: GateStatus | None
    counts: VerdictCounts = Field(description="run の Finding の verdict ごとの件数")
    bundle_generation: int | None = Field(description="run が固定した bundle。None は bundle 外")
    generation_lag: int | None = Field(
        description="今の bundle の世代 − run の世代。どちらかが無ければ None"
    )


class ProjectHealth(IRModel):
    project_id: str
    repo_full_name: str
    formalization: Ratio | None = Field(
        description="要求の表のうち図・IR で形式化された行の割合。None は未計測"
    )
    binding: Ratio | None = Field(
        description="仕様の義務のうち対応なしの UNKNOWN が無いものの割合。None は未計測"
    )
    unknown_breakdown: UnknownBreakdown | None = Field(description="latest_run の UNKNOWN の内訳")
    latest_run: RunSummary | None = Field(description="最新の完了した run（無ければ None）")


class FetchHealth(IRModel):
    """トークン（無ければメンバー）× プロジェクトごとの最後の取得。"""

    channel: str = Field(description="sync / mcp など")
    token_id: str | None
    token_name: str | None
    revoked: bool = Field(description="トークンが失効済み（遅れに数えない）")
    uid: str | None
    member_email: str | None
    project_id: str | None
    repo_full_name: str | None
    last_fetched_at: datetime | None = Field(description="None は期間内に取得が無い")
    last_generation: int | None
    behind_by: int | None = Field(
        description="今の世代 − 最後に取得した世代。None は未計測か失効済み"
    )


class TrendRun(IRModel):
    run_id: str
    repo_full_name: str
    created_at: datetime


class RulePoint(IRModel):
    violated: int = Field(default=0, ge=0)
    review: int = Field(default=0, ge=0)
    unknown: int = Field(default=0, ge=0)
    verified: int = Field(default=0, ge=0)


class RuleTrend(IRModel):
    rule_id: str
    title: str | None = Field(description="今の規約の改訂に無いルールは None")
    level: PolicyLevel | None
    tier: RuleTier | None
    points: list[RulePoint | None] = Field(
        description="WorkspaceHealth.trend_runs と同じ順（古い順）。None はその run に結果が無い"
    )


class LlmUsageStats(IRModel):
    """LLM 支援の記録（S70 の表）から取る値。供給元が無ければ渡さない。"""

    cache_hit_rate: float | None = Field(default=None, ge=0, le=1)
    attestations: int | None = Field(default=None, ge=0)
    self_attested: int | None = Field(default=None, ge=0)
    attestations_unverified: int | None = Field(default=None, ge=0)


class LlmStability(IRModel):
    issue: int = Field(ge=0)
    no_issue: int = Field(ge=0)
    split: int = Field(ge=0, description="多数決で割れた項目の数")
    cache_hit_rate: float | None = Field(description="None は未計測")
    attestations: int | None = Field(description="人の確定の件数。None は未計測")
    self_attested: int | None = Field(
        default=None, description="確定のうち PR の作成者が自分で確定した件数。None は未計測"
    )
    attestations_unverified: int | None = Field(
        default=None,
        description="確定のうち自己確定かどうかを照合できなかった件数。None は未計測",
    )


class WorkspaceHealth(IRModel):
    workspace: str
    generated_at: datetime
    current_generation: int | None = Field(description="今の bundle の世代。None は bundle 無し")
    fetch_window_days: int
    projects: list[ProjectHealth] = Field(default_factory=list)
    fetches: list[FetchHealth] = Field(default_factory=list)
    trend_runs: list[TrendRun] = Field(default_factory=list, description="規約の推移の run")
    rules: list[RuleTrend] = Field(default_factory=list)
    llm: LlmStability | None = Field(default=None, description="None は未計測")
