"""API-CONV: API のパス・メソッド・response_model の規約を検証する（S69、
`design/2026-10-05-team-knowledge-registry.md` 6.1）。

対象アプリは import しない。OpenAPI は JSON として、``api_glob`` の Python ソースは AST として
構文的に読むだけで判定する。入力は ``openapi_path`` と ``api_glob`` の一方または両方。

検査（すべて params で有効化。未指定は検査しない）:

- ``path_case`` (``kebab`` | ``snake``): パスの静的セグメントがそのケースであること。
  ``{id}`` のようなパスパラメーターは対象外。
- ``require_plural_collections``: コレクション（末尾が ``{param}`` でない）ルートの最後の静的
  セグメントが ``s`` で終わること。**単純な経験則**であり、不規則な複数形（``people``）や
  ``/health`` のような単数の資源は違反になる（そうしたものは免除で扱う）。版のセグメント
  （``v1``）は数えない。
- ``version_prefix``: パスが ``/v1`` のような接頭辞で始まること。
- ``allowed_methods``: 使ってよい HTTP メソッド（大文字小文字は区別しない）。
- ``require_response_model``: ルートのデコレーターに ``response_model=`` があること
  （AST のみ。OpenAPI には適用しない）。

デコレーターは ``@<router|app>.<get|post|...>("/path", ...)`` の形だけを読む。受け手の名前が
``app`` / ``api`` / ``router`` で終わるものに限る（``@cache.get("k")`` のような無関係な呼び出しを
ルートと誤認しないため）。

UNKNOWN になるもの: パスが文字列リテラルでないデコレーター、``prefix=`` を渡している
ファイル（ルーターの接頭辞を静的に合成できないため ``version_prefix`` と ``/`` ルートの
複数形は判定できない）、OpenAPI が無い・解析できない・構文エラー。確定できる違反があれば
VIOLATED を優先する。

Finding は OpenAPI で 1 件、``api_glob`` の各ファイルで 1 件。requirement_id は
``<rule.id>:<path>``。
"""

from __future__ import annotations

import ast
import json
import re
from dataclasses import dataclass, field
from typing import Any

from speclino.domain.finding import Category, Finding, Verdict
from speclino.domain.policy import PolicyRule
from speclino.domain.policy_params import ApiConventionsParams
from speclino.verifiers._findings import make_finding, parse_params, unknown_finding
from speclino.verifiers.base import Snapshot

VERIFIER_ID = "api.conventions"
VERIFIER_VERSION = "1.0.0"

_HTTP_METHODS = {"get", "put", "post", "delete", "options", "head", "patch", "trace"}
_CASE_RE = {
    "kebab": re.compile(r"[a-z0-9]+(-[a-z0-9]+)*"),
    "snake": re.compile(r"[a-z0-9]+(_[a-z0-9]+)*"),
}
_VERSION_SEGMENT_RE = re.compile(r"v\d+")
_RECEIVER_RE = re.compile(r"(app|api|router)$", re.IGNORECASE)


@dataclass
class _Outcome:
    violations: list[str] = field(default_factory=list)
    unknowns: list[str] = field(default_factory=list)
    lines: list[int] = field(default_factory=list)


def _check_route(
    outcome: _Outcome,
    params: ApiConventionsParams,
    label: str,
    path: str,
    method: str,
    line: int | None,
    *,
    check_prefix: bool,
) -> None:
    def violate(detail: str) -> None:
        outcome.violations.append(f"{label}: {detail}")
        if line is not None:
            outcome.lines.append(line)

    segments = [s for s in path.split("/") if s]
    static = [s for s in segments if not (s.startswith("{") and s.endswith("}"))]

    if params.allowed_methods is not None and method.lower() not in {
        m.lower() for m in params.allowed_methods
    }:
        violate(f"メソッド {method.upper()} は許可されていません")
    if params.path_case is not None:
        for seg in static:
            if not _CASE_RE[params.path_case].fullmatch(seg):
                violate(f"セグメント '{seg}' が {params.path_case} ケースでありません")
    if params.version_prefix is not None and check_prefix:
        prefix = params.version_prefix.rstrip("/")
        if path != prefix and not path.startswith(prefix + "/"):
            violate(f"パスが接頭辞 {params.version_prefix} で始まっていません")
    if params.require_plural_collections:
        collection = bool(segments) and segments[-1] in static
        if collection:
            names = [s for s in static if not _VERSION_SEGMENT_RE.fullmatch(s)]
            if names and not names[-1].endswith("s"):
                violate(f"コレクション '{names[-1]}' が複数形（s で終わる）でありません")
        elif not segments:
            outcome.unknowns.append(f"{label}: ルート '/' は複数形を判定できません")


def _check_openapi(spec: dict[str, Any], params: ApiConventionsParams) -> _Outcome:
    outcome = _Outcome()
    paths = spec.get("paths")
    if not isinstance(paths, dict):
        return outcome
    for path in sorted(paths):
        operations = paths[path]
        if not isinstance(operations, dict):
            continue
        for method in sorted(m for m in operations if m in _HTTP_METHODS):
            _check_route(
                outcome, params, f"{method.upper()} {path}", path, method, None, check_prefix=True
            )
    return outcome


def _receiver_name(node: ast.expr) -> str | None:
    if isinstance(node, ast.Name):
        return node.id
    if isinstance(node, ast.Attribute):
        return node.attr
    return None


def _check_source(tree: ast.Module, params: ApiConventionsParams) -> _Outcome:
    outcome = _Outcome()
    # ルーターの prefix= は静的に合成しない。ある場合は版の接頭辞を判定できない。
    has_prefix = any(
        isinstance(n, ast.Call) and any(k.arg == "prefix" for k in n.keywords)
        for n in ast.walk(tree)
    )
    for node in ast.walk(tree):
        if not isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef):
            continue
        for deco in node.decorator_list:
            if not (
                isinstance(deco, ast.Call)
                and isinstance(deco.func, ast.Attribute)
                and deco.func.attr in _HTTP_METHODS
            ):
                continue
            receiver = _receiver_name(deco.func.value)
            if receiver is None or not _RECEIVER_RE.search(receiver):
                continue
            method = deco.func.attr
            label = f"@{receiver}.{method} (行 {deco.lineno})"
            path_node: ast.expr | None = deco.args[0] if deco.args else None
            if path_node is None:
                path_node = next((k.value for k in deco.keywords if k.arg == "path"), None)
            if isinstance(path_node, ast.Constant) and isinstance(path_node.value, str):
                _check_route(
                    outcome,
                    params,
                    label,
                    path_node.value,
                    method,
                    deco.lineno,
                    check_prefix=not has_prefix,
                )
                if has_prefix and params.version_prefix is not None:
                    outcome.unknowns.append(
                        f"{label}: ルーターの prefix= があり、版の接頭辞を判定できません"
                    )
            else:
                outcome.unknowns.append(f"{label}: パスが文字列リテラルでないため判定できません")
            if params.require_response_model and not any(
                k.arg == "response_model" for k in deco.keywords
            ):
                outcome.violations.append(f"{label}: response_model がありません")
                outcome.lines.append(deco.lineno)
    return outcome


_EXPECTED = "API の規約（パスのケース・複数形・版の接頭辞、メソッド、response_model）を満たすこと"


def _finding(
    rule: PolicyRule, snapshot: Snapshot, path: str, outcome: _Outcome, scope: str
) -> Finding:
    if outcome.violations:
        verdict: Verdict = "VIOLATED"
        category: Category = "constraint_mismatch"
        detail = outcome.violations + [f"（判定不能: {u}）" for u in outcome.unknowns]
        message = f"{path}: {scope}に規約違反が {len(outcome.violations)} 件あります"
    elif outcome.unknowns:
        verdict, category = "UNKNOWN", "unsupported_analysis"
        detail = outcome.unknowns
        message = f"{path}: {scope}の一部を静的に判定できません"
    else:
        return make_finding(
            rule,
            snapshot,
            path,
            "VERIFIED_IN_SCOPE",
            "conformant",
            f"{path}: {scope}は規約に合っています",
            expected=_EXPECTED,
            actual="違反なし",
        )
    return make_finding(
        rule,
        snapshot,
        path,
        verdict,
        category,
        message,
        expected=_EXPECTED,
        actual="\n".join(detail),
        lines=sorted(set(outcome.lines)),
    )


class ApiConventionsVerifier:
    id = VERIFIER_ID
    version = VERIFIER_VERSION

    def run(self, rule: PolicyRule, snapshot: Snapshot) -> list[Finding]:
        params = parse_params(ApiConventionsParams, rule)
        findings: list[Finding] = []
        if params.openapi_path is not None:
            findings.append(self._check_openapi(rule, snapshot, params.openapi_path, params))
        if params.api_glob is not None:
            for path in snapshot.glob(params.api_glob):
                findings.append(self._check_file(rule, snapshot, path, params))
        return findings

    def _check_openapi(
        self, rule: PolicyRule, snapshot: Snapshot, path: str, params: ApiConventionsParams
    ) -> Finding:
        text = snapshot.read_text(path)
        if text is None:
            return unknown_finding(
                rule, snapshot, path, f"{path} が無いため確認できません", "ファイルなし"
            )
        try:
            spec = json.loads(text)
        except json.JSONDecodeError as e:
            return unknown_finding(
                rule, snapshot, path, f"{path} を JSON として解析できません: {e}", "解析エラー"
            )
        if not isinstance(spec, dict):
            return unknown_finding(
                rule,
                snapshot,
                path,
                f"{path} の形式が不正です（トップレベルがオブジェクトでない）",
                "形式不正",
            )
        return _finding(rule, snapshot, path, _check_openapi(spec, params), "OpenAPI")

    def _check_file(
        self, rule: PolicyRule, snapshot: Snapshot, path: str, params: ApiConventionsParams
    ) -> Finding:
        try:
            tree = ast.parse(snapshot.read_text(path) or "", filename=path)
        except SyntaxError:
            return unknown_finding(
                rule, snapshot, path, f"{path}: 構文解析に失敗しました", "解析エラー"
            )
        return _finding(rule, snapshot, path, _check_source(tree, params), "ルート定義")
