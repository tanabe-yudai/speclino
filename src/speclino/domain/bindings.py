"""業務語彙とコードの対応付け `.speclino/bindings.yaml` と外部操作の契約（PoC 8）。"""

from __future__ import annotations

import re
from typing import Literal

from pydantic import Field, field_validator

from speclino.domain.common import SCHEMA_VERSION, ClaimScope, IRModel

# 変数の対応先: "user.is_admin"（入口引数の属性）または "pure_call:pkg.mod.Cls.method"（契約済みの
# 純粋な呼出しの結果を名前付き変数として扱う拡張。SpecLino 自己検証に必要）
_VAR_SOURCE_RE = re.compile(r"^(pure_call:)?[A-Za-z_]\w*(\.[A-Za-z_]\w*)*$")
_ARG_BINDING_RE = re.compile(r"^(positional:\d+|keyword:[A-Za-z_]\w*)$")


class BehaviorBinding(IRModel):
    entrypoint: str = Field(min_length=1, description="完全修飾シンボル")
    variables: dict[str, str] = Field(
        default_factory=dict,
        description="ドメイン変数名 → 実装上の参照（例 actor.id: user.id）",
    )

    @field_validator("variables")
    @classmethod
    def _check_sources(cls, v: dict[str, str]) -> dict[str, str]:
        for k, src in v.items():
            if not _VAR_SOURCE_RE.match(src):
                raise ValueError(f"variables[{k}] の対応先が不正: {src!r}")
        return v


class OperationBinding(IRModel):
    symbol: str = Field(min_length=1, description="完全修飾シンボル")
    claim_scope: ClaimScope
    argument_bindings: dict[str, str] = Field(
        default_factory=dict, description="引数名 → positional:N | keyword:name"
    )
    summary_ref: str | None = Field(default=None, description="contracts/ の契約 ID")

    @field_validator("argument_bindings")
    @classmethod
    def _check_args(cls, v: dict[str, str]) -> dict[str, str]:
        for k, b in v.items():
            if not _ARG_BINDING_RE.match(b):
                raise ValueError(f"argument_bindings[{k}] が不正: {b!r}")
        return v


class Bindings(IRModel):
    schema_version: Literal["0.1"] = SCHEMA_VERSION
    behaviors: dict[str, BehaviorBinding] = Field(default_factory=dict)
    operations: dict[str, OperationBinding] = Field(default_factory=dict)
    known_pure: list[str] = Field(
        default_factory=list,
        description="副作用が無く結果を検査に使わない外部呼出し（例 time.monotonic）",
    )

    def operation_by_symbol(self, symbol: str) -> tuple[str, OperationBinding] | None:
        for op_id, ob in self.operations.items():
            if ob.symbol == symbol:
                return op_id, ob
        return None


class OperationContract(IRModel):
    """外部操作の承認済み要約（summary_ref の参照先）。未知の外部関数を副作用なしと仮定しない。"""

    id: str = Field(min_length=1)
    symbol: str = Field(min_length=1)
    assumes_success: bool = Field(description="正常終了すると仮定するか")
    returns: str | None = Field(default=None, description="戻り値の型名。None は戻り値を使わない")
    pure: bool = Field(default=False, description="状態を変更せず、同じ入力に同じ結果を返す")
    may_modify: list[str] = Field(default_factory=list, description="変更し得るドメイン変数")
    raises: list[str] = Field(default_factory=list)
    models_internal_calls: bool = Field(
        default=False, description="内部呼出しまでモデル化しているか"
    )
    approved_by: str | None = None
    notes: str | None = None
