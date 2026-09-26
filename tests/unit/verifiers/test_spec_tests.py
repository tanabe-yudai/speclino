"""SPEC-TEST-001 (spec_tests) の検証器テスト（S49）。

Spec IR の rules[].tests（要求 → テスト）と @pytest.mark.spec（テスト → 要求）が
双方向に一致しているかを検査する。S38 で「マーカーはあるが tests は空」という状態が
実際に起きたため、その再発検出を軸にケースを組む。
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from speclino.domain.finding import Finding
from speclino.domain.policy import PolicyExceptions
from speclino.verifiers.registry import VerifierRegistry
from speclino.verifiers.snapshot import DirectorySnapshot
from speclino.verifiers.spec_tests import SpecTestsVerifier

from .conftest import MakePackage, MakeRule, WriteFiles

PARAMS: dict[str, Any] = {
    "spec_ir_paths": ["specs/ir/*.spec-ir.json"],
    "test_paths": ["tests/**/test_*.py"],
    "marker": "spec",
}


def _run(tmp_path: Path, make_rule: MakeRule, params: dict[str, Any] = PARAMS) -> list[Finding]:
    rule = make_rule(
        id="SPEC-TEST-001",
        verifier_id="spec_tests",
        paths=["specs/ir/*.spec-ir.json", "tests/**/test_*.py"],
        params=params,
        on_missing_input="unknown",
        level="advisory",
    )
    snapshot = DirectorySnapshot(root=tmp_path)
    return SpecTestsVerifier().run(rule, snapshot)


def _spec_ir(rules: list[dict[str, Any]]) -> str:
    return json.dumps({"rules": rules})


# ---------------------------------------------------------------- (a) 実在しないテスト ID


def test_declared_test_with_missing_function_is_violated(
    tmp_path: Path, make_rule: MakeRule, write_files: WriteFiles
) -> None:
    write_files(
        tmp_path,
        {
            "specs/ir/demo.spec-ir.json": _spec_ir(
                [{"id": "R-GHOST", "tests": ["tests/test_demo.py::test_ghost"]}]
            ),
            "tests/test_demo.py": "def test_something_else() -> None:\n    pass\n",
        },
    )
    findings = _run(tmp_path, make_rule)
    assert len(findings) == 1
    f = findings[0]
    assert f.verdict == "VIOLATED"
    assert f.category == "missing_effect"
    assert "test_ghost" in (f.actual or "")
    assert f.next_steps
    Finding.model_validate(f.model_dump())


def test_declared_test_with_missing_file_is_violated(
    tmp_path: Path, make_rule: MakeRule, write_files: WriteFiles
) -> None:
    write_files(
        tmp_path,
        {
            "specs/ir/demo.spec-ir.json": _spec_ir(
                [{"id": "R-GHOST2", "tests": ["tests/test_missing_file.py::test_x"]}]
            ),
        },
    )
    findings = _run(tmp_path, make_rule)
    assert len(findings) == 1
    assert findings[0].verdict == "VIOLATED"
    assert findings[0].category == "missing_effect"
    assert "存在しません" in (findings[0].actual or "")


# ---------------------------------------------------------------- (b) マーカーはあるが tests 未宣言


def test_marker_without_declared_test_is_violated(
    tmp_path: Path, make_rule: MakeRule, write_files: WriteFiles
) -> None:
    write_files(
        tmp_path,
        {
            "specs/ir/demo.spec-ir.json": _spec_ir([{"id": "R-UNDECLARED", "tests": []}]),
            "tests/test_demo.py": (
                "import pytest\n\n\n"
                '@pytest.mark.spec("R-UNDECLARED")\n'
                "def test_something() -> None:\n    pass\n"
            ),
        },
    )
    findings = _run(tmp_path, make_rule)
    assert len(findings) == 1
    f = findings[0]
    assert f.verdict == "VIOLATED"
    assert f.category == "missing_effect"
    assert "含まれていません" in (f.actual or "")
    assert f.next_steps


# ---------------------------------------------------------------- (c) Spec IR に無い要求ID


def test_marker_for_unknown_requirement_is_violated(
    tmp_path: Path, make_rule: MakeRule, write_files: WriteFiles
) -> None:
    write_files(
        tmp_path,
        {
            "specs/ir/demo.spec-ir.json": _spec_ir([]),
            "tests/test_demo.py": (
                "import pytest\n\n\n"
                '@pytest.mark.spec("R-GHOST-ID")\n'
                "def test_something() -> None:\n    pass\n"
            ),
        },
    )
    findings = _run(tmp_path, make_rule)
    assert len(findings) == 1
    f = findings[0]
    assert f.verdict == "VIOLATED"
    assert f.category == "missing_effect"
    assert "Spec IR に存在しません" in (f.actual or "")


# ---------------------------------------------------------------- (d) 両方整合 → VERIFIED


def test_consistent_declaration_and_marker_is_verified(
    tmp_path: Path, make_rule: MakeRule, write_files: WriteFiles
) -> None:
    write_files(
        tmp_path,
        {
            "specs/ir/demo.spec-ir.json": _spec_ir(
                [{"id": "R-OK", "tests": ["tests/test_demo.py::test_ok"]}]
            ),
            "tests/test_demo.py": (
                'import pytest\n\n\n@pytest.mark.spec("R-OK")\ndef test_ok() -> None:\n    pass\n'
            ),
        },
    )
    findings = _run(tmp_path, make_rule)
    assert len(findings) == 2
    assert {f.verdict for f in findings} == {"VERIFIED_IN_SCOPE"}
    assert {f.category for f in findings} == {"conformant"}
    for f in findings:
        Finding.model_validate(f.model_dump())


# ---------------------------------------------------------------- (e) 複数マーカー


def test_multiple_markers_on_one_test_are_each_checked(
    tmp_path: Path, make_rule: MakeRule, write_files: WriteFiles
) -> None:
    write_files(
        tmp_path,
        {
            "specs/ir/demo.spec-ir.json": _spec_ir(
                [
                    {"id": "R-MULTI-A", "tests": ["tests/test_demo.py::test_multi"]},
                    {"id": "R-MULTI-B", "tests": ["tests/test_demo.py::test_multi"]},
                ]
            ),
            "tests/test_demo.py": (
                "import pytest\n\n\n"
                '@pytest.mark.spec("R-MULTI-A")\n'
                '@pytest.mark.spec("R-MULTI-B")\n'
                "def test_multi() -> None:\n    pass\n"
            ),
        },
    )
    findings = _run(tmp_path, make_rule)
    # direction1（各 rule.tests）2件 + direction2（マーカー2件）2件 = 4件
    assert len(findings) == 4
    assert {f.verdict for f in findings} == {"VERIFIED_IN_SCOPE"}
    marker_req_ids = {f.requirement_id.split(":")[1] for f in findings}
    assert marker_req_ids == {"R-MULTI-A", "R-MULTI-B"}


# ---------------------------------------------------------------- (f) async def のテスト


def test_async_def_test_is_recognized(
    tmp_path: Path, make_rule: MakeRule, write_files: WriteFiles
) -> None:
    write_files(
        tmp_path,
        {
            "specs/ir/demo.spec-ir.json": _spec_ir(
                [{"id": "R-ASYNC", "tests": ["tests/test_demo.py::test_async_ok"]}]
            ),
            "tests/test_demo.py": "async def test_async_ok() -> None:\n    pass\n",
        },
    )
    findings = _run(tmp_path, make_rule)
    assert len(findings) == 1
    assert findings[0].verdict == "VERIFIED_IN_SCOPE"


def test_async_marker_without_declared_test_is_violated(
    tmp_path: Path, make_rule: MakeRule, write_files: WriteFiles
) -> None:
    write_files(
        tmp_path,
        {
            "specs/ir/demo.spec-ir.json": _spec_ir([{"id": "R-ASYNC2", "tests": []}]),
            "tests/test_demo.py": (
                "import pytest\n\n\n"
                '@pytest.mark.spec("R-ASYNC2")\n'
                "async def test_async_marked() -> None:\n    pass\n"
            ),
        },
    )
    findings = _run(tmp_path, make_rule)
    assert len(findings) == 1
    assert findings[0].verdict == "VIOLATED"


# ---------------------------------------------------------------- (g) 対象なしで on_missing_input


def test_on_missing_input_is_handled_by_registry(
    tmp_path: Path,
    make_rule: MakeRule,
    make_package: MakePackage,
    empty_exceptions: PolicyExceptions,
) -> None:
    rule = make_rule(
        id="SPEC-TEST-001",
        verifier_id="spec_tests",
        paths=["nowhere/*.spec-ir.json", "notests/**/test_*.py"],
        params=PARAMS,
        on_missing_input="unknown",
        level="advisory",
    )
    registry = VerifierRegistry()
    registry.register(SpecTestsVerifier())
    findings = registry.run_package(
        make_package(rule), empty_exceptions, DirectorySnapshot(root=tmp_path)
    )
    assert len(findings) == 1
    assert findings[0].verdict == "UNKNOWN"
    assert findings[0].category == "unsupported_analysis"


def test_on_missing_input_violation_mode(
    tmp_path: Path,
    make_rule: MakeRule,
    make_package: MakePackage,
    empty_exceptions: PolicyExceptions,
) -> None:
    rule = make_rule(
        id="SPEC-TEST-001",
        verifier_id="spec_tests",
        paths=["nowhere/*.spec-ir.json", "notests/**/test_*.py"],
        params=PARAMS,
        on_missing_input="violation",
        level="advisory",
    )
    registry = VerifierRegistry()
    registry.register(SpecTestsVerifier())
    findings = registry.run_package(
        make_package(rule), empty_exceptions, DirectorySnapshot(root=tmp_path)
    )
    assert len(findings) == 1
    assert findings[0].verdict == "VIOLATED"
    assert findings[0].category == "missing_effect"


# ---------------------------------------------------------------- (h) 決定性


def test_findings_are_deterministic(
    tmp_path: Path, make_rule: MakeRule, write_files: WriteFiles
) -> None:
    write_files(
        tmp_path,
        {
            "specs/ir/demo.spec-ir.json": _spec_ir(
                [
                    {"id": "R-OK", "tests": ["tests/test_demo.py::test_ok"]},
                    {"id": "R-GHOST", "tests": ["tests/test_demo.py::test_ghost"]},
                ]
            ),
            "tests/test_demo.py": (
                "import pytest\n\n\n"
                '@pytest.mark.spec("R-OK")\n'
                "def test_ok() -> None:\n    pass\n\n\n"
                '@pytest.mark.spec("R-STRAY")\n'
                "def test_stray() -> None:\n    pass\n"
            ),
        },
    )
    first = _run(tmp_path, make_rule)
    second = _run(tmp_path, make_rule)
    assert [f.id for f in first] == [f.id for f in second]
    assert [f.model_dump() for f in first] == [f.model_dump() for f in second]


# ---------------------------------------------------------------- その他: 複数ファイル・不正 JSON


def test_multiple_spec_ir_files_are_all_read(
    tmp_path: Path, make_rule: MakeRule, write_files: WriteFiles
) -> None:
    write_files(
        tmp_path,
        {
            "specs/ir/a.spec-ir.json": _spec_ir(
                [{"id": "R-A", "tests": ["tests/test_demo.py::test_a"]}]
            ),
            "specs/ir/b.spec-ir.json": _spec_ir(
                [{"id": "R-B", "tests": ["tests/test_demo.py::test_b"]}]
            ),
            "tests/test_demo.py": (
                "def test_a() -> None:\n    pass\n\n\ndef test_b() -> None:\n    pass\n"
            ),
        },
    )
    findings = _run(tmp_path, make_rule)
    assert len(findings) == 2
    assert {f.verdict for f in findings} == {"VERIFIED_IN_SCOPE"}


def test_malformed_spec_ir_json_is_skipped_not_crashed(
    tmp_path: Path, make_rule: MakeRule, write_files: WriteFiles
) -> None:
    write_files(tmp_path, {"specs/ir/broken.spec-ir.json": "{not valid json"})
    findings = _run(tmp_path, make_rule)
    assert findings == []
