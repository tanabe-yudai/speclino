"""NAMING: Python の識別子の命名規則を検証する（S69）。

対象は ``rule.applies_to.paths`` に一致する Python ソース。AST で読むだけで、import しない。

params（すべて任意。指定したものだけ検査する）:

- ``function`` / ``class`` / ``constant`` / ``module``: 名前が fullmatch すべき正規表現。
  ``function`` は ``def`` / ``async def``（メソッドを含む）、``class`` は ``class``、
  ``constant`` はモジュール直下の代入で名前が ALL_CAPS のもの、
  ``module`` はファイル名（拡張子なし）。
- ``forbidden_words``: 識別子に含めてはいけない語。識別子を ``_`` と camelCase の境で語に分け、
  語が（大文字小文字を無視して）一致したら違反。部分文字列では判定しない（``database`` は
  ``data`` に当たらない）。

先頭の ``_``（private）は外してから正規表現に当てる。``__init__`` のようなダンダー名と
``__init__.py`` は対象外。

Finding はファイルごとに 1 件。構文解析できなければ UNKNOWN。requirement_id は
``<rule.id>:<path>``。
"""

from __future__ import annotations

import ast
import re

import regex

from speclino.domain.finding import Finding
from speclino.domain.policy import PolicyRule
from speclino.domain.policy_params import NamingIdentifiersParams
from speclino.verifiers._findings import (
    invalid_params_finding,
    make_finding,
    missing_input_finding,
    parse_params,
    unknown_finding,
)
from speclino.verifiers._safe_regex import RegexMatcher, RegexTimeout, compile_param
from speclino.verifiers.base import Snapshot

VERIFIER_ID = "naming.identifiers"
VERIFIER_VERSION = "1.0.0"

_CONSTANT_RE = re.compile(r"_*[A-Z][A-Z0-9_]*")
_WORD_RE = re.compile(r"[A-Z]+(?![a-z])|[A-Z]?[a-z]+|[0-9]+")


def _is_dunder(name: str) -> bool:
    return len(name) > 4 and name.startswith("__") and name.endswith("__")


def _words(name: str) -> set[str]:
    return {w.lower() for w in _WORD_RE.findall(name)}


class NamingIdentifiersVerifier:
    id = VERIFIER_ID
    version = VERIFIER_VERSION

    def run(self, rule: PolicyRule, snapshot: Snapshot) -> list[Finding]:
        try:
            params = parse_params(NamingIdentifiersParams, rule)
        except ValueError as e:
            return [invalid_params_finding(rule, e)]
        patterns = {
            kind: compile_param(pattern)
            for kind, pattern in (
                ("function", params.function),
                ("class", params.class_),
                ("constant", params.constant),
                ("module", params.module),
            )
            if pattern is not None
        }
        forbidden = {w.lower() for w in params.forbidden_words}
        paths = sorted({p for g in rule.applies_to.paths for p in snapshot.glob(g)})
        py_paths = [p for p in paths if p.endswith(".py")]
        if not py_paths:
            missing = missing_input_finding(
                rule, snapshot, ", ".join(rule.applies_to.paths) or rule.id, "対象の Python ソース"
            )
            return [missing] if missing is not None else []
        return [self._check_file(rule, snapshot, p, patterns, forbidden) for p in py_paths]

    def _check_file(
        self,
        rule: PolicyRule,
        snapshot: Snapshot,
        path: str,
        patterns: dict[str, regex.Pattern[str]],
        forbidden: set[str],
    ) -> Finding:
        try:
            tree = ast.parse(snapshot.read_text(path) or "", filename=path)
        except SyntaxError:
            return unknown_finding(
                rule, snapshot, path, f"{path}: 構文解析に失敗しました", "解析エラー"
            )

        matcher = RegexMatcher()
        details: list[str] = []
        timeouts: list[str] = []
        lines: list[int] = []

        def check(kind: str, name: str, line: int | None) -> None:
            if _is_dunder(name):
                return
            problems: list[str] = []
            pattern = patterns.get(kind)
            try:
                if pattern is not None and not matcher.fullmatch(pattern, name.lstrip("_")):
                    problems.append(f"{kind} '{name}' が {pattern.pattern} に合いません")
            except RegexTimeout as e:
                timeouts.append(f"{kind} '{name[:50]}': {e}")
            hit = sorted(_words(name) & forbidden)
            if hit:
                problems.append(f"{kind} '{name}' に禁止語 {', '.join(hit)} を含みます")
            for problem in problems:
                details.append(problem if line is None else f"行 {line}: {problem}")
                if line is not None:
                    lines.append(line)

        stem = path.rsplit("/", 1)[-1].removesuffix(".py")
        if stem != "__init__":
            check("module", stem, None)
        for node in ast.walk(tree):
            if isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef):
                check("function", node.name, node.lineno)
            elif isinstance(node, ast.ClassDef):
                check("class", node.name, node.lineno)
        for stmt in tree.body:
            targets = (
                stmt.targets
                if isinstance(stmt, ast.Assign)
                else [stmt.target]
                if isinstance(stmt, ast.AnnAssign)
                else []
            )
            for target in targets:
                if isinstance(target, ast.Name) and _CONSTANT_RE.fullmatch(target.id):
                    check("constant", target.id, stmt.lineno)

        expected = "識別子が命名規則（正規表現・禁止語）に合うこと"
        if details:
            return make_finding(
                rule,
                snapshot,
                path,
                "VIOLATED",
                "constraint_mismatch",
                f"{path}: 命名規則に合わない識別子が {len(details)} 件あります",
                expected=expected,
                actual="\n".join([*details, *(f"（判定不能: {u}）" for u in timeouts)]),
                lines=sorted(set(lines)),
            )
        if timeouts:
            return unknown_finding(
                rule,
                snapshot,
                path,
                f"{path}: 正規表現の照合がタイムアウトした識別子があります",
                "\n".join(timeouts),
            )
        return make_finding(
            rule,
            snapshot,
            path,
            "VERIFIED_IN_SCOPE",
            "conformant",
            f"{path}: 識別子はすべて命名規則に合っています",
            expected=expected,
            actual="違反なし",
        )
