"""api.conventions の検証器テスト（S69）。"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from speclino.domain.finding import Finding
from speclino.verifiers.api_conventions import ApiConventionsVerifier
from speclino.verifiers.snapshot import DirectorySnapshot

from .conftest import MakeRule, WriteFiles

BASE: dict[str, Any] = {
    "openapi_path": "openapi.json",
    "api_glob": "api/**/*.py",
    "path_case": "kebab",
    "require_plural_collections": True,
    "version_prefix": "/v1",
    "allowed_methods": ["get", "post"],
    "require_response_model": True,
}

GOOD_ROUTES = (
    "from fastapi import APIRouter\n"
    "router = APIRouter()\n\n"
    "@router.get('/v1/user-accounts/{account_id}', response_model=dict)\n"
    "def read(account_id: str): ...\n\n"
    "@router.post('/v1/user-accounts', response_model=dict)\n"
    "def create(): ...\n"
)


def _openapi(paths: dict[str, Any]) -> str:
    return json.dumps({"openapi": "3.1.0", "paths": paths})


def _run(
    tmp_path: Path,
    make_rule: MakeRule,
    write_files: WriteFiles,
    files: dict[str, str],
    params: dict[str, Any] | None = None,
) -> list[Finding]:
    write_files(tmp_path, files)
    rule = make_rule(
        id="API-CONV-001",
        verifier_id="api.conventions",
        paths=["openapi.json", "api/**/*.py"],
        params=BASE if params is None else params,
        on_missing_input="unknown",
    )
    findings = ApiConventionsVerifier().run(rule, DirectorySnapshot(root=tmp_path))
    for f in findings:
        Finding.model_validate(f.model_dump())
        assert f.verdict != "REVIEW"
    return findings


def test_conforming_openapi_and_decorators_are_verified(
    tmp_path: Path, make_rule: MakeRule, write_files: WriteFiles
) -> None:
    findings = _run(
        tmp_path,
        make_rule,
        write_files,
        {
            "openapi.json": _openapi(
                {"/v1/user-accounts/{id}": {"get": {}}, "/v1/orders": {"post": {}}}
            ),
            "api/routes.py": GOOD_ROUTES,
        },
    )
    assert [f.requirement_id for f in findings] == [
        "API-CONV-001:openapi.json",
        "API-CONV-001:api/routes.py",
    ]
    assert {f.verdict for f in findings} == {"VERIFIED_IN_SCOPE"}


def test_openapi_violations(tmp_path: Path, make_rule: MakeRule, write_files: WriteFiles) -> None:
    findings = _run(
        tmp_path,
        make_rule,
        write_files,
        {
            "openapi.json": _openapi(
                {
                    "/v1/userAccounts": {"get": {}},  # ケース違反
                    "/v1/order": {"post": {}},  # 単数
                    "/items": {"delete": {}},  # 接頭辞なし・メソッド
                }
            )
        },
        {**BASE, "api_glob": None},
    )
    assert len(findings) == 1
    f = findings[0]
    assert f.verdict == "VIOLATED"
    actual = f.actual or ""
    assert "userAccounts" in actual and "'order'" in actual
    assert "DELETE" in actual and "/v1 で始まっていません" in actual


def test_path_params_are_exempt_from_case(
    tmp_path: Path, make_rule: MakeRule, write_files: WriteFiles
) -> None:
    findings = _run(
        tmp_path,
        make_rule,
        write_files,
        {"openapi.json": _openapi({"/v1/things/{thingId}": {"get": {}}})},
        {"openapi_path": "openapi.json", "path_case": "snake", "version_prefix": "/v1"},
    )
    assert findings[0].verdict == "VERIFIED_IN_SCOPE"


def test_decorator_violations_with_lines(
    tmp_path: Path, make_rule: MakeRule, write_files: WriteFiles
) -> None:
    src = (
        "@app.get('/v1/thing')\n"
        "def a(): ...\n"
        "@app.put('/v1/things', response_model=dict)\n"
        "def b(): ...\n"
    )
    findings = _run(
        tmp_path, make_rule, write_files, {"api/x.py": src}, {**BASE, "openapi_path": None}
    )
    f = findings[0]
    assert f.verdict == "VIOLATED"
    assert f.category == "constraint_mismatch"
    assert "response_model がありません" in (f.actual or "")
    assert "PUT" in (f.actual or "")
    assert [r.line_start for r in f.code_refs] == [1, 3]


def test_unrelated_decorator_is_ignored(
    tmp_path: Path, make_rule: MakeRule, write_files: WriteFiles
) -> None:
    src = "@cache.get('Weird_Key')\ndef a(): ...\n"
    findings = _run(
        tmp_path, make_rule, write_files, {"api/x.py": src}, {**BASE, "openapi_path": None}
    )
    assert findings[0].verdict == "VERIFIED_IN_SCOPE"


def test_non_literal_path_is_unknown(
    tmp_path: Path, make_rule: MakeRule, write_files: WriteFiles
) -> None:
    src = "P = '/v1/things'\n@router.get(P, response_model=dict)\ndef a(): ...\n"
    findings = _run(
        tmp_path, make_rule, write_files, {"api/x.py": src}, {**BASE, "openapi_path": None}
    )
    assert findings[0].verdict == "UNKNOWN"
    assert findings[0].category == "unsupported_analysis"


def test_router_prefix_makes_version_prefix_unknown(
    tmp_path: Path, make_rule: MakeRule, write_files: WriteFiles
) -> None:
    src = (
        "router = APIRouter(prefix='/v1')\n"
        "@router.get('/things', response_model=dict)\n"
        "def a(): ...\n"
    )
    findings = _run(
        tmp_path, make_rule, write_files, {"api/x.py": src}, {**BASE, "openapi_path": None}
    )
    assert findings[0].verdict == "UNKNOWN"


def test_violation_wins_over_unknown(
    tmp_path: Path, make_rule: MakeRule, write_files: WriteFiles
) -> None:
    src = "@router.get(P)\ndef a(): ...\n@router.get('/v1/Bad')\ndef b(): ...\n"
    findings = _run(
        tmp_path, make_rule, write_files, {"api/x.py": src}, {**BASE, "openapi_path": None}
    )
    assert findings[0].verdict == "VIOLATED"
    assert "判定不能" in (findings[0].actual or "")


def test_missing_and_unparsable_inputs_are_unknown(
    tmp_path: Path, make_rule: MakeRule, write_files: WriteFiles
) -> None:
    findings = _run(tmp_path, make_rule, write_files, {})
    assert [f.verdict for f in findings] == ["UNKNOWN"]
    findings = _run(
        tmp_path,
        make_rule,
        write_files,
        {"openapi.json": "{not json", "api/bad.py": "def (:\n"},
    )
    assert [f.verdict for f in findings] == ["UNKNOWN", "UNKNOWN"]
    assert all(f.category == "unsupported_analysis" for f in findings)


def test_invalid_params_raise_value_error(
    tmp_path: Path, make_rule: MakeRule, write_files: WriteFiles
) -> None:
    with pytest.raises(ValueError):
        _run(tmp_path, make_rule, write_files, {}, {"path_case": "kebab"})  # 入力なし
    with pytest.raises(ValueError):
        _run(tmp_path, make_rule, write_files, {}, {**BASE, "path_case": "camel"})
