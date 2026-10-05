"""仕様エディタのプレビューの応答（S66）。組み立ては services/spec_preview.py。"""

from __future__ import annotations

from pydantic import Field

from speclino.domain.common import IRModel
from speclino.domain.uml import ParseResult


class SpecPreview(IRModel):
    parse_result: ParseResult
    canonical: str | None = Field(
        default=None, description="IR から作った正規形の Markdown（compiled があるときだけ）"
    )


__all__ = ["SpecPreview"]
