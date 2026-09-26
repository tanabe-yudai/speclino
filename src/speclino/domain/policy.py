"""規約パッケージ（PoC 3章 / 7章、`design/product-spec-summary.md` 規約パッケージ節）。

型定義のみ。検証ロジックは `speclino/verifiers/` に置く。
"""

from __future__ import annotations

from datetime import date
from typing import Any, Literal

from pydantic import Field, model_validator

from speclino.domain.common import SCHEMA_VERSION, IRModel

PolicyLevel = Literal["mandatory", "advisory"]
OnMissingInput = Literal["violation", "unknown", "skipped"]


class PolicyApplicability(IRModel):
    """ルールの適用範囲。paths は glob パターンの一覧。"""

    paths: list[str] = Field(default_factory=list, description="glob パターン（例 specs/*.md）")
    languages: list[str] = Field(default_factory=list)


class PolicySource(IRModel):
    """ルールの根拠となる正本文書の参照。"""

    doc: str = Field(min_length=1, description="正本文書のパスまたは URL")
    anchor: str | None = Field(default=None, description="文書内の節を指す任意の参照")


class PolicyVerifierRef(IRModel):
    """ルールが使う検証器と、その実行パラメータ。"""

    id: str = Field(min_length=1, description="VerifierRegistry に登録した検証器 ID")
    version: str = Field(min_length=1)
    params: dict[str, Any] = Field(default_factory=dict)


class PolicyRule(IRModel):
    id: str = Field(
        min_length=1,
        # コロンを禁じる: 規約 Finding の requirement_id は `<rule>:<path>` の 2 段で、
        # 例外の照合（conformance/exceptions.py の _rule_of / _path_of）が最初のコロンで
        # 分解する。ID 自身がコロンを含むと切り詰められ、「規約が見つからない＝免除可」に
        # 化けて exceptions_allowed=False を回避できてしまう（codex レビューで実測）。
        pattern=r"^[^:]+$",
        description="安定した規約 ID（例 MAKE-001）。コロンは使えない",
    )
    version: str = Field(min_length=1)
    level: PolicyLevel
    title: str = Field(min_length=1)
    text: str = Field(min_length=1)
    applies_to: PolicyApplicability
    source: PolicySource | None = None
    verifier: PolicyVerifierRef | None = Field(
        default=None,
        description=(
            "検証器の参照。None は「機械検証されていない（助言のみ）」を表す（S42）。"
            "散文の制約をまず advisory として登録し、検証器を書けたものから mandatory に上げる"
        ),
    )
    on_missing_input: OnMissingInput = Field(
        description="applies_to が対象を1つも見つけられないときの扱い"
    )
    exceptions_allowed: bool = True

    @property
    def verifier_ref(self) -> PolicyVerifierRef:
        """検証器つきのルールとしての参照。

        検証器の無いルール（助言のみ）は `VerifierRegistry._run_rule` が先に弾くので、
        検査器の中でここに来ることはない。不変条件を各検査器に散らさず 1 か所で保証する。
        """
        if self.verifier is None:
            raise ValueError(f"{self.id}: 検証器の無いルールを検査器に渡した")
        return self.verifier

    @model_validator(mode="after")
    def _mandatory_needs_a_verifier(self) -> PolicyRule:
        """必須ルールは機械検証できることを要求する。

        検証器の無い規約を mandatory にするとゲートを落とす根拠が人の判断になり、
        「判定は決定的な検査器だけが行う」という前提が崩れる。
        """
        if self.level == "mandatory" and self.verifier is None:
            raise ValueError(
                f"{self.id}: mandatory なルールには verifier が必要（助言なら advisory に）"
            )
        return self


class PolicyPackage(IRModel):
    schema_version: Literal["0.1"] = SCHEMA_VERSION
    id: str = Field(min_length=1)
    version: str = Field(min_length=1)
    status: Literal["active", "draft"]
    owner: str = Field(min_length=1)
    rules: list[PolicyRule] = Field(default_factory=list)


class PolicyExceptionTarget(IRModel):
    paths: list[str] = Field(min_length=1, description="glob パターン")


class PolicyException(IRModel):
    """規約の免除。全項目必須（AC-009: 期限切れは無効）。"""

    id: str = Field(min_length=1)
    rule: str = Field(min_length=1, description="対象の PolicyRule.id")
    target: PolicyExceptionTarget
    reason: str = Field(min_length=1)
    approver: str = Field(min_length=1)
    expires_on: date
    alternative: str = Field(min_length=1, description="代替策")


class PolicyExceptions(IRModel):
    schema_version: Literal["0.1"] = SCHEMA_VERSION
    exceptions: list[PolicyException] = Field(default_factory=list)


# --------------------------------------------------------------- チーム規約の改訂（S42）
# 規約はプロジェクト固有ではなくチーム固有なので、正本を対象リポジトリの Git ではなく
# SpecLino が持つ（design/2026-09-26-team-policies-and-dx.md）。Git に置く代わりに
# バージョン・レビュー・監査・再現性の 4 つをこちら側で満たす。

PolicyRevisionStatus = Literal["draft", "active", "superseded"]


class PolicyRevision(IRModel):
    """チーム規約の 1 改訂。不変・連番で、承認されるまで有効にならない。

    `package` が規約の中身（既存の PolicyPackage をそのまま使う）。改訂は上書きせず、
    新しい番号で積む。run-manifest には適用した `revision` を記録し、`replay` は
    その改訂で再評価する（UI で規約を変えても過去の判定は変わらない）。
    """

    team: str = Field(min_length=1, description="チームの識別子（当面は GitHub の owner）")
    revision: int = Field(ge=1, description="チーム内で連番。改訂は不変")
    status: PolicyRevisionStatus
    package: PolicyPackage
    author: str = Field(min_length=1)
    approver: str | None = Field(default=None, description="承認者。draft のうちは None")
    note: str = Field(default="", description="この改訂で何を変えたか")


__all__ = [
    "OnMissingInput",
    "PolicyApplicability",
    "PolicyException",
    "PolicyExceptionTarget",
    "PolicyExceptions",
    "PolicyLevel",
    "PolicyPackage",
    "PolicyRevision",
    "PolicyRevisionStatus",
    "PolicyRule",
    "PolicySource",
    "PolicyVerifierRef",
]
