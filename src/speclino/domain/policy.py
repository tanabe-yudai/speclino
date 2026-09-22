"""規約パッケージ（PoC 3章 / 7章、`design/product-spec-summary.md` 規約パッケージ節）。

型定義のみ。検証ロジックは `speclino/verifiers/` に置く。
"""

from __future__ import annotations

from datetime import date
from typing import Any, Literal

from pydantic import Field

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
    id: str = Field(min_length=1, description="安定した規約 ID（例 MAKE-001）")
    version: str = Field(min_length=1)
    level: PolicyLevel
    title: str = Field(min_length=1)
    text: str = Field(min_length=1)
    applies_to: PolicyApplicability
    source: PolicySource | None = None
    verifier: PolicyVerifierRef
    on_missing_input: OnMissingInput = Field(
        description="applies_to が対象を1つも見つけられないときの扱い"
    )
    exceptions_allowed: bool = True


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
