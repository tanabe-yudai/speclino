"""API-ERR-001: API のエラー応答形式を検証する（`design/product-spec-summary.md` 規約パッケージ節、
AC-003 / AC-005 相当、S36）。

対象アプリは import しない。openapi.json は JSON として、`api_glob` に一致する Python ソースは
AST として構文的に読むだけで判定する。

(a) ``openapi_path``（既定 openapi.json）の全 operation について、4xx/5xx の responses が
    ``#/components/schemas/<error_schema>`` を参照していること
    （``content.application/json.schema.$ref``）。参照していない operation が1つでもあれば
    VIOLATED（constraint_mismatch）。openapi.json が無ければ UNKNOWN（unsupported_analysis）。
    Finding は openapi 全体で1件。
(b) ``api_glob`` に一致する各ファイルで ``raise HTTPException(...)`` を直接書いていないこと。
    AST で ``ast.Raise`` の ``exc`` が ``Call`` かつ関数名（``ast.Name.id`` または
    ``ast.Attribute.attr``）が ``HTTPException`` の箇所を探す。``<helper_symbol>(...)`` の
    raise（既定 ``api_error``）は許可する。違反があれば VIOLATED（forbidden_effect、code_refs に
    path と行）。ファイルごとに Finding 1件。

requirement_id は ``<rule.id>:<path>``（(a) は openapi_path、(b) は各ファイルパス）。
"""

from __future__ import annotations

import ast
import json
import re
from dataclasses import dataclass
from typing import Any

from speclino.domain.common import SourceRef
from speclino.domain.finding import Finding, Severity
from speclino.domain.policy import PolicyRule
from speclino.verifiers.base import Snapshot

VERIFIER_ID = "api.error_format"
VERIFIER_VERSION = "1.0.0"

_DEFAULT_ERROR_SCHEMA = "ErrorResponse"
_DEFAULT_HELPER_SYMBOL = "api_error"
_ERROR_STATUS_RE = re.compile(r"^[45]\d{2}$")
_HTTP_METHODS = {"get", "put", "post", "delete", "options", "head", "patch", "trace"}


@dataclass
class _RefIssue:
    detail: str


def _schema_ref(response: Any) -> str | None:
    if not isinstance(response, dict):
        return None
    content = response.get("content")
    if not isinstance(content, dict):
        return None
    media = content.get("application/json")
    if not isinstance(media, dict):
        return None
    schema = media.get("schema")
    if not isinstance(schema, dict):
        return None
    ref = schema.get("$ref")
    return ref if isinstance(ref, str) else None


def _check_openapi_error_refs(spec: dict[str, Any], error_schema: str) -> list[_RefIssue]:
    expected_ref = f"#/components/schemas/{error_schema}"
    issues: list[_RefIssue] = []
    paths = spec.get("paths")
    if not isinstance(paths, dict):
        return issues
    for path in sorted(paths):
        operations = paths[path]
        if not isinstance(operations, dict):
            continue
        for method in sorted(operations):
            if method not in _HTTP_METHODS:
                continue
            operation = operations[method]
            if not isinstance(operation, dict):
                continue
            responses = operation.get("responses")
            if not isinstance(responses, dict):
                continue
            for status in sorted(str(s) for s in responses):
                if not _ERROR_STATUS_RE.match(status):
                    continue
                ref = _schema_ref(responses[status])
                if ref != expected_ref:
                    issues.append(
                        _RefIssue(
                            f"{method.upper()} {path} {status}: "
                            f"{ref or '(スキーマ参照なし)'}（期待値 {expected_ref}）"
                        )
                    )
    return issues


def _direct_http_exception_raises(text: str, path: str) -> list[int] | None:
    """None は構文解析に失敗したことを示す。"""
    try:
        tree = ast.parse(text, filename=path)
    except SyntaxError:
        return None
    lines: list[int] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Raise) or not isinstance(node.exc, ast.Call):
            continue
        func = node.exc.func
        name: str | None = None
        if isinstance(func, ast.Name):
            name = func.id
        elif isinstance(func, ast.Attribute):
            name = func.attr
        if name == "HTTPException":
            lines.append(node.lineno)
    return lines


class ApiErrorFormatVerifier:
    id = VERIFIER_ID
    version = VERIFIER_VERSION

    def run(self, rule: PolicyRule, snapshot: Snapshot) -> list[Finding]:
        params = rule.verifier.params
        openapi_path: str = params.get("openapi_path", "openapi.json")
        error_schema: str = params.get("error_schema", _DEFAULT_ERROR_SCHEMA)
        api_glob: str = params.get("api_glob", "src/speclino/api/**/*.py")
        helper_symbol: str = params.get("helper_symbol", _DEFAULT_HELPER_SYMBOL)

        findings = [self._check_openapi(rule, snapshot, openapi_path, error_schema)]
        for path in snapshot.glob(api_glob):
            findings.append(self._check_file(rule, snapshot, path, helper_symbol))
        return findings

    def _severity(self, rule: PolicyRule) -> Severity:
        return "high" if rule.level == "mandatory" else "medium"

    def _check_openapi(
        self, rule: PolicyRule, snapshot: Snapshot, openapi_path: str, error_schema: str
    ) -> Finding:
        requirement_id = f"{rule.id}:{openapi_path}"
        severity = self._severity(rule)
        text = snapshot.read_text(openapi_path)
        if text is None:
            return Finding(
                id=f"policy:{requirement_id}",
                requirement_id=requirement_id,
                behavior_id=f"policy:{rule.verifier.id}",
                verdict="UNKNOWN",
                category="unsupported_analysis",
                severity=severity,
                message=f"{openapi_path} が無いため確認できません",
                actual="ファイルなし",
                claim_scope="persisted_state",
            )
        try:
            spec = json.loads(text)
        except json.JSONDecodeError as e:
            return Finding(
                id=f"policy:{requirement_id}",
                requirement_id=requirement_id,
                behavior_id=f"policy:{rule.verifier.id}",
                verdict="UNKNOWN",
                category="unsupported_analysis",
                severity=severity,
                message=f"{openapi_path} を JSON として解析できません: {e}",
                actual="解析エラー",
                claim_scope="persisted_state",
            )
        if not isinstance(spec, dict):
            return Finding(
                id=f"policy:{requirement_id}",
                requirement_id=requirement_id,
                behavior_id=f"policy:{rule.verifier.id}",
                verdict="UNKNOWN",
                category="unsupported_analysis",
                severity=severity,
                message=f"{openapi_path} の形式が不正です（トップレベルがオブジェクトでない）",
                actual="形式不正",
                claim_scope="persisted_state",
            )

        expected_ref = f"#/components/schemas/{error_schema}"
        expected = f"4xx/5xx の responses が {expected_ref} を参照すること"
        issues = _check_openapi_error_refs(spec, error_schema)
        if issues:
            return Finding(
                id=f"policy:{requirement_id}",
                requirement_id=requirement_id,
                behavior_id=f"policy:{rule.verifier.id}",
                verdict="VIOLATED",
                category="constraint_mismatch",
                severity=severity,
                message=(
                    f"{openapi_path}: 4xx/5xx の responses が "
                    f"{expected_ref} を参照していない operation があります"
                ),
                expected=expected,
                actual="\n".join(i.detail for i in issues),
                claim_scope="persisted_state",
            )
        return Finding(
            id=f"policy:{requirement_id}",
            requirement_id=requirement_id,
            behavior_id=f"policy:{rule.verifier.id}",
            verdict="VERIFIED_IN_SCOPE",
            category="conformant",
            severity=severity,
            message=(
                f"{openapi_path}: 4xx/5xx の responses はすべて {error_schema} を参照しています"
            ),
            expected=expected,
            actual="違反なし",
            claim_scope="persisted_state",
        )

    def _check_file(
        self, rule: PolicyRule, snapshot: Snapshot, path: str, helper_symbol: str
    ) -> Finding:
        requirement_id = f"{rule.id}:{path}"
        severity = self._severity(rule)
        text = snapshot.read_text(path) or ""
        lines = _direct_http_exception_raises(text, path)

        if lines is None:
            return Finding(
                id=f"policy:{requirement_id}",
                requirement_id=requirement_id,
                behavior_id=f"policy:{rule.verifier.id}",
                verdict="UNKNOWN",
                category="unsupported_analysis",
                severity=severity,
                message=f"{path}: 構文解析に失敗しました",
                actual="解析エラー",
                claim_scope="persisted_state",
            )

        expected = f"HTTPException は {helper_symbol}(...) ヘルパー経由で送出すること"
        if lines:
            code_refs = [
                SourceRef(snapshot_id=snapshot.snapshot_id, path=path, line_start=n) for n in lines
            ]
            return Finding(
                id=f"policy:{requirement_id}",
                requirement_id=requirement_id,
                behavior_id=f"policy:{rule.verifier.id}",
                verdict="VIOLATED",
                category="forbidden_effect",
                severity=severity,
                message=(
                    f"{path}: raise HTTPException(...) を直接書いています"
                    f"（{helper_symbol}(...) 経由にしてください）"
                ),
                expected=expected,
                actual=f"行: {', '.join(str(n) for n in lines)}",
                claim_scope="persisted_state",
                code_refs=code_refs,
            )
        return Finding(
            id=f"policy:{requirement_id}",
            requirement_id=requirement_id,
            behavior_id=f"policy:{rule.verifier.id}",
            verdict="VERIFIED_IN_SCOPE",
            category="conformant",
            severity=severity,
            message=f"{path}: raise HTTPException(...) の直書きはありません",
            expected=expected,
            actual="違反なし",
            claim_scope="persisted_state",
        )
