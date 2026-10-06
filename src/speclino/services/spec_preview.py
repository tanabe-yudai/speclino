"""仕様エディタのプレビュー（S66、設計 4.3）。

S63 の `compile_spec` と `render` を呼ぶだけで、判断を足さない（決定的・LLM なし）。
大きさの上限は `compile_spec` が診断で返す。
"""

from __future__ import annotations

from speclino.domain.domain_spec import DomainSpec
from speclino.domain.spec_ir import SpecIR
from speclino.domain.spec_preview import SpecPreview
from speclino.spec.uml import compile_spec, render


def preview(markdown: str, *, domain: DomainSpec | None, legacy_ir: list[SpecIR]) -> SpecPreview:
    result = compile_spec(markdown, domain=domain, legacy_ir=legacy_ir)
    canonical = render(result.compiled) if result.compiled is not None else None
    return SpecPreview(parse_result=result, canonical=canonical)


__all__ = ["preview"]
