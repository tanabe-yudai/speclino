"""API-ERR-001: API のエラー応答形式を検証する（AC-003 / AC-005 相当、S36）。

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

(a') 追加（1.1.0）: ``success_envelope_schema`` を指定すると、全 operation の 2xx で
    ``application/json`` の content を持つ応答が
    ``#/components/schemas/<success_envelope_schema>`` を ``$ref`` する
    （または ``allOf`` の先頭要素が参照する）こと。違反は (a) と同じ openapi の Finding に
    合流させる（requirement_id が衝突しないよう別 Finding は作らない）。指定が無ければ従来どおり。

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
from speclino.domain.policy_params import ApiErrorFormatParams
from speclino.verifiers._findings import invalid_params_finding, parse_params
from speclino.verifiers.base import Snapshot

VERIFIER_ID = "api.error_format"
VERIFIER_VERSION = "1.1.0"

_ERROR_STATUS_RE = re.compile(r"^[45]\d{2}$")
_SUCCESS_STATUS_RE = re.compile(r"^2\d{2}$")
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


def _envelope_ref_ok(response: Any, expected_ref: str) -> bool:
    if _schema_ref(response) == expected_ref:
        return True
    try:
        schema = response["content"]["application/json"]["schema"]
        first = schema["allOf"][0]
    except (KeyError, IndexError, TypeError):
        return False
    return isinstance(first, dict) and first.get("$ref") == expected_ref


def _check_openapi_success_envelope(spec: dict[str, Any], envelope_schema: str) -> list[_RefIssue]:
    expected_ref = f"#/components/schemas/{envelope_schema}"
    issues: list[_RefIssue] = []
    paths = spec.get("paths")
    if not isinstance(paths, dict):
        return issues
    for path in sorted(paths):
        operations = paths[path]
        if not isinstance(operations, dict):
            continue
        for method in sorted(operations):
            operation = operations[method]
            if method not in _HTTP_METHODS or not isinstance(operation, dict):
                continue
            responses = operation.get("responses")
            if not isinstance(responses, dict):
                continue
            for status in sorted(str(s) for s in responses):
                if not _SUCCESS_STATUS_RE.match(status):
                    continue
                response = responses[status]
                content = response.get("content") if isinstance(response, dict) else None
                if not isinstance(content, dict) or "application/json" not in content:
                    continue  # 204 など本文なしは対象外
                if not _envelope_ref_ok(response, expected_ref):
                    issues.append(
                        _RefIssue(
                            f"{method.upper()} {path} {status}: "
                            f"成功時の包み方が {expected_ref} でない"
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
        try:
            params = parse_params(ApiErrorFormatParams, rule)
        except ValueError as e:
            return [invalid_params_finding(rule, e)]

        findings = [
            self._check_openapi(
                rule,
                snapshot,
                params.openapi_path,
                params.error_schema,
                params.success_envelope_schema,
            )
        ]
        for path in snapshot.glob(params.api_glob):
            findings.append(self._check_file(rule, snapshot, path, params.helper_symbol))
        return findings

    def _severity(self, rule: PolicyRule) -> Severity:
        return "high" if rule.level == "mandatory" else "medium"

    def _check_openapi(
        self,
        rule: PolicyRule,
        snapshot: Snapshot,
        openapi_path: str,
        error_schema: str,
        envelope_schema: str | None = None,
    ) -> Finding:
        requirement_id = f"{rule.id}:{openapi_path}"
        severity = self._severity(rule)
        text = snapshot.read_text(openapi_path)
        if text is None:
            return Finding(
                id=f"policy:{requirement_id}",
                requirement_id=requirement_id,
                behavior_id=f"policy:{rule.verifier_ref.id}",
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
                behavior_id=f"policy:{rule.verifier_ref.id}",
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
                behavior_id=f"policy:{rule.verifier_ref.id}",
                verdict="UNKNOWN",
                category="unsupported_analysis",
                severity=severity,
                message=f"{openapi_path} の形式が不正です（トップレベルがオブジェクトでない）",
                actual="形式不正",
                claim_scope="persisted_state",
            )

        if not isinstance(spec.get("paths"), dict):
            return Finding(
                id=f"policy:{requirement_id}",
                requirement_id=requirement_id,
                behavior_id=f"policy:{rule.verifier_ref.id}",
                verdict="UNKNOWN",
                category="unsupported_analysis",
                severity=severity,
                message=f"{openapi_path} の形式が不正です（paths が不正）",
                actual="形式不正",
                claim_scope="persisted_state",
            )

        expected_ref = f"#/components/schemas/{error_schema}"
        expected = f"4xx/5xx の responses が {expected_ref} を参照すること"
        issues = _check_openapi_error_refs(spec, error_schema)
        if envelope_schema is not None:
            envelope_ref = f"#/components/schemas/{envelope_schema}"
            expected += f"、2xx の application/json 応答が {envelope_ref} で包まれること"
            issues += _check_openapi_success_envelope(spec, envelope_schema)
        if issues:
            return Finding(
                id=f"policy:{requirement_id}",
                requirement_id=requirement_id,
                behavior_id=f"policy:{rule.verifier_ref.id}",
                verdict="VIOLATED",
                category="constraint_mismatch",
                severity=severity,
                message=(
                    f"{openapi_path}: 4xx/5xx の responses が "
                    f"{expected_ref} を参照していない operation があります"
                    if envelope_schema is None
                    else f"{openapi_path}: エラー応答の参照または成功時の包み方が規約に合わない "
                    "operation があります"
                ),
                expected=expected,
                actual="\n".join(i.detail for i in issues),
                claim_scope="persisted_state",
            )
        return Finding(
            id=f"policy:{requirement_id}",
            requirement_id=requirement_id,
            behavior_id=f"policy:{rule.verifier_ref.id}",
            verdict="VERIFIED_IN_SCOPE",
            category="conformant",
            severity=severity,
            message=(
                f"{openapi_path}: 4xx/5xx の responses はすべて {error_schema} を参照しています"
                if envelope_schema is None
                else f"{openapi_path}: エラー応答の参照と成功時の包み方がすべて規約に合っています"
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
                behavior_id=f"policy:{rule.verifier_ref.id}",
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
                behavior_id=f"policy:{rule.verifier_ref.id}",
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
            behavior_id=f"policy:{rule.verifier_ref.id}",
            verdict="VERIFIED_IN_SCOPE",
            category="conformant",
            severity=severity,
            message=f"{path}: raise HTTPException(...) の直書きはありません",
            expected=expected,
            actual="違反なし",
            claim_scope="persisted_state",
        )
