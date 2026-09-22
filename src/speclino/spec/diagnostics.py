"""spec_validator の出力型（PoC 6.1 の Diagnostic）。

`speclino.domain` に置かないのは、これが IR そのものの語彙ではなく、spec_validator が
形式検証（JSON Schema／pydantic）の後段で行う意味検証（参照解決・型整合・矛盾・承認ハッシュ）
固有の出力契約だから。ロジックは持たない。
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from speclino.domain.spec_ir import SpecIR

Severity = Literal["error", "warning"]


class Diagnostic(BaseModel):
    """spec_validator が検出した1件の問題。"""

    model_config = ConfigDict(frozen=True, extra="forbid")

    code: str = Field(min_length=1, description="例: E_REF_UNDEFINED, W_UNAPPROVED")
    severity: Severity
    rule_id: str | None = Field(default=None, description="単一の Rule に紐づかない場合は None")
    path: str | None = Field(
        default=None, description="問題箇所（例: applicability, assertion.expr）"
    )
    message: str = Field(min_length=1)


class ValidationReport(BaseModel):
    """`validate_spec_ir` の結果。error が1件も無ければ `ok`。"""

    model_config = ConfigDict(frozen=True, extra="forbid")

    spec: SpecIR
    diagnostics: list[Diagnostic] = Field(default_factory=list)

    @property
    def errors(self) -> list[Diagnostic]:
        return [d for d in self.diagnostics if d.severity == "error"]

    @property
    def warnings(self) -> list[Diagnostic]:
        return [d for d in self.diagnostics if d.severity == "warning"]

    @property
    def ok(self) -> bool:
        return not self.errors


__all__ = ["Diagnostic", "Severity", "ValidationReport"]
