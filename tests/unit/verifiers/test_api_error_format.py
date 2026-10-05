"""API-ERR-001 (api.error_format) の検証器テスト（S36）。"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from speclino.domain.finding import Finding
from speclino.verifiers.api_error_format import ApiErrorFormatVerifier
from speclino.verifiers.snapshot import DirectorySnapshot

from .conftest import REPO_ROOT, MakeRule, WriteFiles

PARAMS = {
    "openapi_path": "openapi.json",
    "error_schema": "ErrorResponse",
    "api_glob": "src/speclino/api/**/*.py",
    "helper_symbol": "api_error",
}

VALID_OPENAPI: dict[str, Any] = {
    "openapi": "3.1.0",
    "paths": {
        "/things": {
            "get": {
                "responses": {
                    "200": {"content": {"application/json": {"schema": {"type": "object"}}}},
                    "404": {
                        "content": {
                            "application/json": {
                                "schema": {"$ref": "#/components/schemas/ErrorResponse"}
                            }
                        }
                    },
                    "500": {
                        "content": {
                            "application/json": {
                                "schema": {"$ref": "#/components/schemas/ErrorResponse"}
                            }
                        }
                    },
                }
            }
        }
    },
    "components": {"schemas": {"ErrorResponse": {"type": "object"}}},
}

HELPER_FILE = (
    "from speclino.api.errors import api_error\n\n"
    "def handler():\n"
    "    raise api_error(404, 'not_found', 'missing')\n"
)

DIRECT_RAISE_FILE = (
    "from fastapi import HTTPException\n\n"
    "def handler():\n"
    "    raise HTTPException(status_code=404, detail='missing')\n"
)


def _run(
    tmp_path: Path,
    make_rule: MakeRule,
    write_files: WriteFiles,
    files: dict[str, str],
    openapi: dict[str, Any] | None,
    params: dict[str, Any] = PARAMS,
) -> list[Finding]:
    write_files(tmp_path, files)
    if openapi is not None:
        (tmp_path / "openapi.json").write_text(
            json.dumps(openapi, ensure_ascii=False, indent=2), encoding="utf-8"
        )
    rule = make_rule(
        id="API-ERR-001",
        verifier_id="api.error_format",
        paths=["openapi.json", "src/speclino/api/**/*.py"],
        params=params,
        on_missing_input="unknown",
        level="advisory",
    )
    snapshot = DirectorySnapshot(root=tmp_path)
    return ApiErrorFormatVerifier().run(rule, snapshot)


def test_valid_openapi_and_helper_usage_is_verified(
    tmp_path: Path, make_rule: MakeRule, write_files: WriteFiles
) -> None:
    findings = _run(
        tmp_path,
        make_rule,
        write_files,
        {"src/speclino/api/routes/good.py": HELPER_FILE},
        VALID_OPENAPI,
    )
    assert len(findings) == 2
    openapi_finding, file_finding = findings
    assert openapi_finding.requirement_id == "API-ERR-001:openapi.json"
    assert openapi_finding.verdict == "VERIFIED_IN_SCOPE"
    assert openapi_finding.category == "conformant"
    assert file_finding.requirement_id == "API-ERR-001:src/speclino/api/routes/good.py"
    assert file_finding.verdict == "VERIFIED_IN_SCOPE"
    assert file_finding.category == "conformant"
    for f in findings:
        Finding.model_validate(f.model_dump())


def test_missing_openapi_is_unknown(
    tmp_path: Path, make_rule: MakeRule, write_files: WriteFiles
) -> None:
    findings = _run(
        tmp_path,
        make_rule,
        write_files,
        {"src/speclino/api/routes/good.py": HELPER_FILE},
        None,
    )
    openapi_finding = findings[0]
    assert openapi_finding.verdict == "UNKNOWN"
    assert openapi_finding.category == "unsupported_analysis"


def test_openapi_missing_error_response_ref_is_violated(
    tmp_path: Path, make_rule: MakeRule, write_files: WriteFiles
) -> None:
    bad_openapi = json.loads(json.dumps(VALID_OPENAPI))
    bad_openapi["paths"]["/things"]["get"]["responses"]["404"]["content"]["application/json"][
        "schema"
    ] = {"type": "object"}
    findings = _run(
        tmp_path,
        make_rule,
        write_files,
        {"src/speclino/api/routes/good.py": HELPER_FILE},
        bad_openapi,
    )
    openapi_finding = findings[0]
    assert openapi_finding.verdict == "VIOLATED"
    assert openapi_finding.category == "constraint_mismatch"
    assert "404" in (openapi_finding.actual or "")


def test_direct_raise_http_exception_is_violated(
    tmp_path: Path, make_rule: MakeRule, write_files: WriteFiles
) -> None:
    findings = _run(
        tmp_path,
        make_rule,
        write_files,
        {"src/speclino/api/routes/bad.py": DIRECT_RAISE_FILE},
        VALID_OPENAPI,
    )
    file_finding = findings[1]
    assert file_finding.verdict == "VIOLATED"
    assert file_finding.category == "forbidden_effect"
    assert len(file_finding.code_refs) == 1
    assert file_finding.code_refs[0].path == "src/speclino/api/routes/bad.py"
    assert file_finding.code_refs[0].line_start == 4


def test_helper_raise_is_not_flagged(
    tmp_path: Path, make_rule: MakeRule, write_files: WriteFiles
) -> None:
    findings = _run(
        tmp_path,
        make_rule,
        write_files,
        {"src/speclino/api/routes/good.py": HELPER_FILE},
        VALID_OPENAPI,
    )
    file_finding = findings[1]
    assert file_finding.verdict == "VERIFIED_IN_SCOPE"
    assert file_finding.code_refs == []


def test_finding_count_matches_files_plus_one(
    tmp_path: Path, make_rule: MakeRule, write_files: WriteFiles
) -> None:
    findings = _run(
        tmp_path,
        make_rule,
        write_files,
        {
            "src/speclino/api/routes/a.py": HELPER_FILE,
            "src/speclino/api/routes/b.py": DIRECT_RAISE_FILE,
            "src/speclino/api/app.py": "def create_app():\n    pass\n",
        },
        VALID_OPENAPI,
    )
    assert len(findings) == 4


def test_finding_id_is_deterministic(
    tmp_path: Path, make_rule: MakeRule, write_files: WriteFiles
) -> None:
    files = {"src/speclino/api/routes/good.py": HELPER_FILE}
    first = _run(tmp_path, make_rule, write_files, files, VALID_OPENAPI)
    second = _run(tmp_path, make_rule, write_files, files, VALID_OPENAPI)
    assert [f.id for f in first] == [f.id for f in second]


def test_repository_self_check(make_rule: MakeRule) -> None:
    """このリポジトリ自身が API-ERR-001 を満たす（openapi.json は `make openapi` で再生成する）。"""
    rule = make_rule(
        id="API-ERR-001",
        verifier_id="api.error_format",
        paths=["openapi.json", "src/speclino/api/**/*.py"],
        params=PARAMS,
        on_missing_input="unknown",
        level="advisory",
    )
    snapshot = DirectorySnapshot(root=REPO_ROOT)
    findings = ApiErrorFormatVerifier().run(rule, snapshot)
    violated = [(f.requirement_id, f.message) for f in findings if f.verdict != "VERIFIED_IN_SCOPE"]
    assert violated == []
    assert {f.requirement_id for f in findings} >= {
        "API-ERR-001:openapi.json",
        "API-ERR-001:src/speclino/api/auth.py",
        "API-ERR-001:src/speclino/api/routes/review_runs.py",
    }


ENVELOPE_PARAMS = {**PARAMS, "success_envelope_schema": "Envelope"}
ENVELOPE_REF = "#/components/schemas/Envelope"


def _openapi_with_success(schema: dict[str, Any] | None, status: str = "200") -> dict[str, Any]:
    spec = json.loads(json.dumps(VALID_OPENAPI))
    response: dict[str, Any] = {"description": "ok"}
    if schema is not None:
        response["content"] = {"application/json": {"schema": schema}}
    spec["paths"]["/things"]["get"]["responses"][status] = response
    return spec


def test_envelope_ref_is_verified(
    tmp_path: Path, make_rule: MakeRule, write_files: WriteFiles
) -> None:
    findings = _run(
        tmp_path,
        make_rule,
        write_files,
        {},
        _openapi_with_success({"$ref": ENVELOPE_REF}),
        ENVELOPE_PARAMS,
    )
    assert findings[0].verdict == "VERIFIED_IN_SCOPE"


def test_envelope_allof_first_ref_is_verified(
    tmp_path: Path, make_rule: MakeRule, write_files: WriteFiles
) -> None:
    schema = {"allOf": [{"$ref": ENVELOPE_REF}, {"properties": {"data": {"type": "object"}}}]}
    findings = _run(
        tmp_path, make_rule, write_files, {}, _openapi_with_success(schema), ENVELOPE_PARAMS
    )
    assert findings[0].verdict == "VERIFIED_IN_SCOPE"


def test_envelope_missing_is_violated(
    tmp_path: Path, make_rule: MakeRule, write_files: WriteFiles
) -> None:
    findings = _run(
        tmp_path,
        make_rule,
        write_files,
        {},
        _openapi_with_success({"type": "object"}),
        ENVELOPE_PARAMS,
    )
    assert findings[0].verdict == "VIOLATED"
    assert findings[0].category == "constraint_mismatch"
    assert "GET /things 200" in (findings[0].actual or "")


def test_envelope_ignored_without_param_and_204_exempt(
    tmp_path: Path, make_rule: MakeRule, write_files: WriteFiles
) -> None:
    findings = _run(tmp_path, make_rule, write_files, {}, _openapi_with_success({"type": "object"}))
    assert findings[0].verdict == "VERIFIED_IN_SCOPE"
    findings = _run(
        tmp_path, make_rule, write_files, {}, _openapi_with_success(None, "204"), ENVELOPE_PARAMS
    )
    assert findings[0].verdict == "VIOLATED"  # 200 の既存応答は包まれていない
    spec = _openapi_with_success(None, "204")
    del spec["paths"]["/things"]["get"]["responses"]["200"]
    findings = _run(tmp_path, make_rule, write_files, {}, spec, ENVELOPE_PARAMS)
    assert findings[0].verdict == "VERIFIED_IN_SCOPE"


def test_unknown_param_is_unknown_finding_not_raised(
    tmp_path: Path, make_rule: MakeRule, write_files: WriteFiles
) -> None:
    findings = _run(tmp_path, make_rule, write_files, {}, VALID_OPENAPI, {**PARAMS, "typo": 1})
    assert len(findings) == 1
    assert findings[0].verdict == "UNKNOWN"
    assert findings[0].requirement_id == "API-ERR-001"
    assert "パラメーターが不正" in findings[0].message


def test_openapi_without_paths_is_unknown(
    tmp_path: Path, make_rule: MakeRule, write_files: WriteFiles
) -> None:
    findings = _run(tmp_path, make_rule, write_files, {}, {"openapi": "3.1.0"})
    assert findings[0].verdict == "UNKNOWN"
    assert findings[0].category == "unsupported_analysis"
