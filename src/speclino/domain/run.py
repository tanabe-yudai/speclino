"""run-manifest（PoC 5.3 / 12.3）。入力ハッシュと版を固定し、再現可能にする。"""

from __future__ import annotations

from datetime import date, datetime
from typing import Annotated, Literal

from pydantic import Field, StringConstraints

from speclino.domain.assessment import Attestation
from speclino.domain.code_ir import AnalysisLimits, ReachabilityDigest
from speclino.domain.common import SCHEMA_VERSION, IRModel

# 外から受け取る Git の ref（SHA・ブランチ・タグ）。GitHub の URL パスに埋め込むので、どの
# セグメントも `.` で始まらない形に限る（`..` による別リポジトリへのパス移動を防ぐ。S54）
GIT_REF_PATTERN = r"^[A-Za-z0-9_-][A-Za-z0-9._-]*(/[A-Za-z0-9_-][A-Za-z0-9._-]*)*$"
GitRef = Annotated[str, StringConstraints(pattern=GIT_REF_PATTERN, max_length=255)]

GateStatus = Literal["pass", "pass_with_exceptions", "fail"]
ExecState = Literal[
    "queued", "running", "awaiting_answer", "completed", "failed", "cancelled", "superseded"
]


class RunInputs(IRModel):
    repository: str | None = None
    head_commit: str = Field(min_length=1)
    base_commit: str | None = None
    spec_content_hash: str | None = None
    spec_ir_hash: str | None = None
    bindings_hash: str | None = None
    domain_hash: str | None = None
    contracts_hash: str | None = None
    policy_hash: str | None = None
    team_policy_hash: str | None = Field(
        default=None,
        description=(
            "適用したチーム規約（PolicyRevision.package）だけから作ったハッシュ（S42）。"
            "プロジェクト固有の .speclino/policy/*.yaml を含む policy_hash とは別に持ち、"
            "replay がチーム規約の改訂だけを対象リポジトリ無しで再現・照合できるようにする"
        ),
    )
    code_ir_hash: str | None = Field(default=None, description="--code-ir 入力時のハッシュ")


class RunVersions(IRModel):
    speclino: str
    analyzer: str
    engine: str
    solver: str
    settings_hash: str | None = None


class BehaviorAnalysis(IRModel):
    """増分解析（S41c）の記録。再利用は「解析していない」ことなので必ず記録して隠さない。"""

    behavior_id: str = Field(min_length=1)
    reachability: ReachabilityDigest = Field(
        description="到達関数群のソースハッシュ（未解決呼出しも含め隠さず記録する）"
    )
    inputs_hash: str = Field(
        min_length=1, description="仕様・domain・bindings・contracts の内容ハッシュ"
    )
    reused_from_base: bool = Field(description="base の Code IR を再利用したか")
    reason: str = Field(min_length=1, description="再利用した／しなかった理由（人が読める説明）")


class OutOfScopeDiagram(IRModel):
    """ワークスペース全体の仕様のうち、要素がこのリポジトリに 1 つも無く義務を出さなかった図。"""

    spec: str = Field(min_length=1, description="仕様（artifact）の名前")
    diagram_id: str = Field(min_length=1)


class RunManifest(IRModel):
    schema_version: Literal["0.1"] = SCHEMA_VERSION
    run_id: str = Field(min_length=1)
    created_at: datetime
    inputs: RunInputs
    versions: RunVersions
    limits: AnalysisLimits
    fail_on: list[str] = Field(default_factory=list, description="ゲート失敗にする verdict")
    mode: Literal["advisory", "blocking"] = "advisory"
    analysis: list[BehaviorAnalysis] = Field(
        default_factory=list, description="behavior ごとの増分解析の記録（S41c）"
    )
    policy_team: str | None = Field(
        default=None, description="適用したチーム規約の team 識別子（S42）"
    )
    policy_revision: int | None = Field(
        default=None,
        description="適用したチーム規約の改訂番号（S42）。replay はこの改訂を解決して照合する",
    )
    bundle_generation: int | None = Field(default=None, description="run の開始時に固定した bundle")
    bundle_hash: str | None = None
    uml_parser_version: str | None = None
    expected_requirement_ids: list[str] = Field(
        default_factory=list, description="出るべき要求 ID（欠けたら UNKNOWN）"
    )
    out_of_scope_diagrams: list[OutOfScopeDiagram] = Field(
        default_factory=list,
        description="ワークスペース全体の仕様で対象外にした図（その図の義務は出るべき要求に入れない）",
    )
    llm_assessment_keys: list[str] = Field(default_factory=list)
    attestations: list[Attestation] = Field(
        default_factory=list,
        description=(
            "このゲートで効いた人の確定（S70）。verdict は書き換えず、ゲートの処置として残す"
        ),
    )
    trusted_commit: str | None = Field(
        default=None,
        description=(
            "bundle の run（S65）で対応・契約・免除・規約を読んだ信頼側のコミット（worker が取った"
            "デフォルトブランチの先端）。None は信頼側を確かめられなかった run"
        ),
    )
    source_roots: list[str] = Field(
        default_factory=list, description="解析した source_roots（空は既定。replay で使う）"
    )
    today: date | None = Field(default=None, description="例外の期限判定に使った日付")
    bindings_patch: dict[str, str] = Field(
        default_factory=dict, description="人の回答による run 限定の操作束縛（S32）"
    )
