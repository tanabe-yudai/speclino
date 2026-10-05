"""ERRMSG: エラーメッセージの書き方を検証する（S69、
`design/2026-10-05-team-knowledge-registry.md` 6.1）。

対象は ``rule.applies_to.paths`` に一致する Python ソース。AST で読むだけで、import しない。
``call_names``（既定 ``api_error`` / ``HTTPException``）の呼び出しの、メッセージ引数を検査する。

メッセージ引数は **キーワード** で読む: ``message_arg``（既定 ``message``）、無ければ ``detail``。
どちらのキーワードも無い呼び出し（位置引数だけなど）は位置が分からないので UNKNOWN 扱い。

params:

- ``pattern``: メッセージが fullmatch すべき正規表現（タイムアウト付き。タイムアウトや 1000 文字
  超の文字列はその呼び出しを判定不能にする）。
- ``require_code``: 呼び出しに ``code=`` キーワードがあること（``**kwargs`` があれば判定不能）。
- ``forbid_trailing_period``: 文末が ``.`` / ``。`` でないこと。
- ``language``: ``ja`` は日本語（ひらがな・カタカナ・漢字）を 1 文字以上含む、``en`` は ASCII のみ。

メッセージの形ごとの扱い:

- 文字列リテラル: すべての検査を行う。
- 埋め込み式の無い f 文字列: 文字列リテラルと同じ。
- 埋め込み式のある f 文字列: 定数部分だけで検査する。``pattern`` は判定不能。文末が埋め込み式なら
  文末は判定不能。``ja`` は定数部分に日本語があれば確定、無ければ判定不能（埋め込み式が日本語かも
  しれない）。``en`` は定数部分に ASCII 以外があれば違反。
- それ以外（変数・呼び出し・連結）: その呼び出しは判定不能。

確定した違反があれば VIOLATED を優先し、無ければ判定不能が 1 つでもあれば UNKNOWN。
Finding はファイルごとに 1 件。requirement_id は ``<rule.id>:<path>``。
"""

from __future__ import annotations

import ast
import re

import regex

from speclino.domain.finding import Finding
from speclino.domain.policy import PolicyRule
from speclino.domain.policy_params import ErrorMessageFormatParams
from speclino.verifiers._findings import (
    invalid_params_finding,
    make_finding,
    missing_input_finding,
    parse_params,
    unknown_finding,
)
from speclino.verifiers._safe_regex import RegexMatcher, RegexTimeout, compile_param
from speclino.verifiers.base import Snapshot

VERIFIER_ID = "error_message.format"
VERIFIER_VERSION = "1.0.0"

_JAPANESE_RE = re.compile("[぀-ヿ一-鿿]")
_TRAILING_PERIODS = (".", "。")


def _call_name(func: ast.expr) -> str | None:
    if isinstance(func, ast.Name):
        return func.id
    if isinstance(func, ast.Attribute):
        return func.attr
    return None


class ErrorMessageFormatVerifier:
    id = VERIFIER_ID
    version = VERIFIER_VERSION

    def run(self, rule: PolicyRule, snapshot: Snapshot) -> list[Finding]:
        try:
            params = parse_params(ErrorMessageFormatParams, rule)
        except ValueError as e:
            return [invalid_params_finding(rule, e)]
        pattern = compile_param(params.pattern) if params.pattern else None
        paths = sorted({p for g in rule.applies_to.paths for p in snapshot.glob(g)})
        py_paths = [p for p in paths if p.endswith(".py")]
        if not py_paths:
            missing = missing_input_finding(
                rule, snapshot, ", ".join(rule.applies_to.paths) or rule.id, "対象の Python ソース"
            )
            return [missing] if missing is not None else []
        return [self._check_file(rule, snapshot, p, params, pattern) for p in py_paths]

    def _check_file(
        self,
        rule: PolicyRule,
        snapshot: Snapshot,
        path: str,
        params: ErrorMessageFormatParams,
        pattern: regex.Pattern[str] | None,
    ) -> Finding:
        try:
            tree = ast.parse(snapshot.read_text(path) or "", filename=path)
        except SyntaxError:
            return unknown_finding(
                rule, snapshot, path, f"{path}: 構文解析に失敗しました", "解析エラー"
            )

        matcher = RegexMatcher()
        violations: list[str] = []
        unknowns: list[str] = []
        lines: list[int] = []
        names = set(params.call_names)

        for node in ast.walk(tree):
            if not (isinstance(node, ast.Call) and _call_name(node.func) in names):
                continue
            label = f"行 {node.lineno} {_call_name(node.func)}(...)"
            bad = _check_call(node, label, params, pattern, matcher, unknowns)
            if bad:
                violations.extend(bad)
                lines.append(node.lineno)

        expected = "エラーメッセージがテンプレート・コード・文末・言語の規約に合うこと"
        if violations:
            return make_finding(
                rule,
                snapshot,
                path,
                "VIOLATED",
                "constraint_mismatch",
                f"{path}: エラーメッセージの規約違反が {len(violations)} 件あります",
                expected=expected,
                actual="\n".join([*violations, *(f"（判定不能: {u}）" for u in unknowns)]),
                lines=sorted(set(lines)),
            )
        if unknowns:
            return make_finding(
                rule,
                snapshot,
                path,
                "UNKNOWN",
                "unsupported_analysis",
                f"{path}: 静的に判定できないエラーメッセージがあります",
                expected=expected,
                actual="\n".join(unknowns),
            )
        return make_finding(
            rule,
            snapshot,
            path,
            "VERIFIED_IN_SCOPE",
            "conformant",
            f"{path}: エラーメッセージはすべて規約に合っています",
            expected=expected,
            actual="違反なし",
        )


def _check_call(
    call: ast.Call,
    label: str,
    params: ErrorMessageFormatParams,
    pattern: regex.Pattern[str] | None,
    matcher: RegexMatcher,
    unknowns: list[str],
) -> list[str]:
    bad: list[str] = []
    keywords = {k.arg: k.value for k in call.keywords if k.arg is not None}
    if params.require_code and "code" not in keywords:
        if any(k.arg is None for k in call.keywords):
            unknowns.append(f"{label}: **kwargs があり code= を判定できません")
        else:
            bad.append(f"{label}: code= がありません")

    message = keywords.get(params.message_arg, keywords.get("detail"))
    if message is None:
        unknowns.append(f"{label}: メッセージをキーワード引数で渡していないため判定できません")
        return bad

    try:
        if isinstance(message, ast.Constant) and isinstance(message.value, str):
            bad.extend(_check_text(label, message.value, params, pattern, matcher))
        elif isinstance(message, ast.JoinedStr):
            _check_joined(message, label, params, pattern, matcher, bad, unknowns)
        else:
            unknowns.append(f"{label}: メッセージが文字列リテラルでないため判定できません")
    except RegexTimeout as e:
        unknowns.append(f"{label}: {e}")
    return bad


def _check_text(
    label: str,
    text: str,
    params: ErrorMessageFormatParams,
    pattern: regex.Pattern[str] | None,
    matcher: RegexMatcher,
) -> list[str]:
    bad: list[str] = []
    if pattern is not None and not matcher.fullmatch(pattern, text):
        bad.append(f"{label}: '{text}' が {pattern.pattern} に合いません")
    if params.forbid_trailing_period and text.endswith(_TRAILING_PERIODS):
        bad.append(f"{label}: '{text}' の文末に句点・ピリオドがあります")
    if params.language == "ja" and not _JAPANESE_RE.search(text):
        bad.append(f"{label}: '{text}' に日本語が含まれていません")
    if params.language == "en" and not text.isascii():
        bad.append(f"{label}: '{text}' に ASCII 以外の文字が含まれています")
    return bad


def _check_joined(
    message: ast.JoinedStr,
    label: str,
    params: ErrorMessageFormatParams,
    pattern: regex.Pattern[str] | None,
    matcher: RegexMatcher,
    bad: list[str],
    unknowns: list[str],
) -> None:
    constants = [
        v.value for v in message.values if isinstance(v, ast.Constant) and isinstance(v.value, str)
    ]
    text = "".join(constants)
    if not any(isinstance(v, ast.FormattedValue) for v in message.values):
        # 埋め込み式の無い f 文字列は文字列リテラルと同じ
        bad.extend(_check_text(label, text, params, pattern, matcher))
        return
    if pattern is not None:
        unknowns.append(f"{label}: 埋め込み式があるため pattern を判定できません")
    if params.forbid_trailing_period:
        last = message.values[-1]
        if isinstance(last, ast.Constant) and isinstance(last.value, str):
            if last.value.endswith(_TRAILING_PERIODS):
                bad.append(f"{label}: メッセージの文末に句点・ピリオドがあります")
        else:
            unknowns.append(f"{label}: 文末が埋め込み式のため文末を判定できません")
    if params.language == "ja" and not _JAPANESE_RE.search(text):
        # 定数部分に日本語があれば確定。無ければ埋め込み式が日本語かもしれず判定できない
        unknowns.append(f"{label}: 定数部分に日本語が無く、埋め込み式の内容を判定できません")
    if params.language == "en" and not text.isascii():
        bad.append(f"{label}: メッセージの定数部分に ASCII 以外の文字が含まれています")
