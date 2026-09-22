"""ドメイン定義 `.speclino/domain.yaml`（PoC 5.1 の domain）。

状態の列挙値、各変数の型、入口の仮定、観測境界を宣言する。型は boolean / integer /
列挙型 / opaque ID（同一性だけ比較）の 4 種。
"""

from __future__ import annotations

import re
from typing import Literal

from pydantic import Field, field_validator, model_validator

from speclino.domain.common import SCHEMA_VERSION, IRModel
from speclino.domain.expr import BOOL_TYPE, INT_TYPE, Expr

_VAR_RE = re.compile(r"^[A-Za-z_]\w*(\.[A-Za-z_]\w*)*$")
_TYPE_RE = re.compile(r"^[A-Za-z_]\w*$")


class DomainSpec(IRModel):
    schema_version: Literal["0.1"] = SCHEMA_VERSION
    enums: dict[str, list[str]] = Field(default_factory=dict, description="列挙型名 → 値の一覧")
    opaque_types: list[str] = Field(default_factory=list, description="同一性だけ比較する ID 型")
    variables: dict[str, str] = Field(
        default_factory=dict, description="変数名（名前空間なし。例 order.status）→ 型名"
    )
    assumptions: list[Expr] = Field(
        default_factory=list, description="入口で成立すると仮定する条件（before 名前空間）"
    )
    observation_scope: list[str] = Field(
        default_factory=list, description="この解析が判定できる観測境界の説明"
    )
    out_of_scope: list[str] = Field(default_factory=list, description="対象外の説明")

    @field_validator("enums")
    @classmethod
    def _check_enums(cls, v: dict[str, list[str]]) -> dict[str, list[str]]:
        for name, values in v.items():
            if not _TYPE_RE.match(name):
                raise ValueError(f"列挙型名が不正: {name!r}")
            if not values or len(set(values)) != len(values):
                raise ValueError(f"列挙 {name} の値が空か重複している")
        return v

    @model_validator(mode="after")
    def _check_variables(self) -> DomainSpec:
        known = {BOOL_TYPE, INT_TYPE, *self.enums, *self.opaque_types}
        for var, ty in self.variables.items():
            if not _VAR_RE.match(var):
                raise ValueError(f"変数名が不正: {var!r}")
            if ty not in known:
                raise ValueError(f"変数 {var} の型 {ty!r} は未定義")
        return self

    def type_of(self, variable: str) -> str | None:
        return self.variables.get(variable)

    def is_enum(self, type_name: str) -> bool:
        return type_name in self.enums

    def is_opaque(self, type_name: str) -> bool:
        return type_name in self.opaque_types
