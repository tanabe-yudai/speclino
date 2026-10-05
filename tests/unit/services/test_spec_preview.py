"""仕様のプレビュー（S66）。S63 の compile_spec / render を呼ぶだけで、判断を足さない。"""

from __future__ import annotations

from speclino.services.spec_preview import preview
from speclino.spec.uml import compile_spec, render
from tests.unit.spec.uml.samples import FULL, block


def test_valid_spec_has_compiled_and_canonical() -> None:
    result = preview(FULL, domain=None, legacy_ir=[])
    assert result.parse_result.compiled is not None
    assert not [d for d in result.parse_result.diagnostics if d.severity == "error"]
    expected = compile_spec(FULL, domain=None, legacy_ir=[]).compiled
    assert expected is not None
    assert result.canonical == render(expected)


def test_error_has_no_compiled_and_located_diagnostics() -> None:
    broken = FULL + "\n" + block("%%@ id: bad", "sequenceDiagram", "    par [p1]", "    end")
    result = preview(broken, domain=None, legacy_ir=[])
    assert result.parse_result.compiled is None
    assert result.canonical is None
    errors = [d for d in result.parse_result.diagnostics if d.severity == "error"]
    assert errors
    located = [d for d in errors if d.span is not None]
    assert located, "エラーは場所（行・列）を持つ"
    assert all(d.span is not None and d.span.line >= 1 and d.span.column >= 1 for d in located)


def test_preview_is_deterministic() -> None:
    a = preview(FULL, domain=None, legacy_ir=[])
    b = preview(FULL, domain=None, legacy_ir=[])
    assert a.model_dump() == b.model_dump()
