"""api.conventions の検証器テスト（S69）。"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

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
    "from fastapi import FastAPI\n"
    "app = FastAPI()\n\n"
    "@app.get('/v1/user-accounts/{account_id}', response_model=dict)\n"
    "def read(account_id: str): ...\n\n"
    "@app.post('/v1/user-accounts', response_model=dict)\n"
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


def test_version_prefix_only_judged_for_fastapi_apps(
    tmp_path: Path, make_rule: MakeRule, write_files: WriteFiles
) -> None:
    params = {**BASE, "openapi_path": None}
    router_src = (
        "router = APIRouter(prefix='/v1')\n"
        "@router.get('/things', response_model=dict)\n"
        "def a(): ...\n"
    )
    findings = _run(tmp_path, make_rule, write_files, {"api/x.py": router_src}, params)
    assert findings[0].verdict == "UNKNOWN"
    app_src = "app = FastAPI()\n@app.get('/things', response_model=dict)\ndef a(): ...\n"
    findings = _run(tmp_path, make_rule, write_files, {"api/x.py": app_src}, params)
    assert findings[0].verdict == "VIOLATED"
    assert "/v1 で始まっていません" in (findings[0].actual or "")
    # 接頭辞を求めない設定なら、APIRouter でも他の検査は確定する
    findings = _run(
        tmp_path,
        make_rule,
        write_files,
        {"api/x.py": router_src},
        {**params, "version_prefix": None},
    )
    assert findings[0].verdict == "VERIFIED_IN_SCOPE"


def test_violation_wins_over_unknown(
    tmp_path: Path, make_rule: MakeRule, write_files: WriteFiles
) -> None:
    src = "@router.get(P)\ndef a(): ...\n@router.get('/v1/Bad')\ndef b(): ...\n"
    findings = _run(
        tmp_path, make_rule, write_files, {"api/x.py": src}, {**BASE, "openapi_path": None}
    )
    assert findings[0].verdict == "VIOLATED"
    assert "判定不能" in (findings[0].actual or "")


def test_unparsable_inputs_are_unknown(
    tmp_path: Path, make_rule: MakeRule, write_files: WriteFiles
) -> None:
    findings = _run(
        tmp_path,
        make_rule,
        write_files,
        {"openapi.json": "{not json", "api/bad.py": "def (:\n"},
    )
    assert [f.verdict for f in findings] == ["UNKNOWN", "UNKNOWN"]
    assert all(f.category == "unsupported_analysis" for f in findings)


def test_openapi_without_paths_is_unknown(
    tmp_path: Path, make_rule: MakeRule, write_files: WriteFiles
) -> None:
    for body in ('{"openapi": "3.1.0"}', '{"paths": []}'):
        findings = _run(
            tmp_path,
            make_rule,
            write_files,
            {"openapi.json": body},
            {**BASE, "api_glob": None},
        )
        assert findings[0].verdict == "UNKNOWN"
        assert findings[0].category == "unsupported_analysis"


def test_missing_inputs_follow_on_missing_input(
    tmp_path: Path, make_rule: MakeRule, write_files: WriteFiles
) -> None:
    for on_missing, expected in (
        ("violation", ["VIOLATED"]),
        ("unknown", ["UNKNOWN"]),
        ("skipped", []),
    ):
        rule = make_rule(
            id="API-CONV-001",
            verifier_id="api.conventions",
            paths=["openapi.json"],
            params=BASE,
            on_missing_input=on_missing,
        )
        findings = ApiConventionsVerifier().run(rule, DirectorySnapshot(root=tmp_path))
        assert [f.verdict for f in findings] == expected  # openapi も api_glob も無い
    # api_glob だけの設定で対象 0 件
    rule = make_rule(
        id="API-CONV-001",
        verifier_id="api.conventions",
        paths=["api/**/*.py"],
        params={**BASE, "openapi_path": None},
        on_missing_input="unknown",
    )
    findings = ApiConventionsVerifier().run(rule, DirectorySnapshot(root=tmp_path))
    assert [f.verdict for f in findings] == ["UNKNOWN"]


def test_invalid_params_are_unknown_not_raised(
    tmp_path: Path, make_rule: MakeRule, write_files: WriteFiles
) -> None:
    for params in (
        {"path_case": "kebab"},  # 入力なし
        {**BASE, "path_case": "camel"},
        {"openapi_path": "openapi.json"},  # 検査項目なし
        {**BASE, "allowed_methods": ["fetch"]},
    ):
        findings = _run(tmp_path, make_rule, write_files, {}, params)
        assert [f.verdict for f in findings] == ["UNKNOWN"]
        assert findings[0].requirement_id == "API-CONV-001"
        assert "パラメーターが不正" in findings[0].message
