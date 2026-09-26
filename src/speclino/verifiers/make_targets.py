"""MAKE-001: 標準ターゲット（setup/test/lint 等）の存在と実処理を検証する（AC-004）。

Makefile は構文的に読むだけで、実行しない（`make` を呼ばない）。判定は以下の順に行う。

1. ターゲットが未定義 → VIOLATED（missing_effect）
2. ターゲットは定義されているが、レシピが空または trivial のみ → VIOLATED（missing_effect）。
   「存在」と「内容」を区別するため、未定義と別のメッセージにする。
3. 自身のレシピが無く、前提（prerequisite）を持つ場合は 1 段だけ辿り、依存先が実処理の
   レシピを持てば VERIFIED_IN_SCOPE とする。
4. include / $(eval / パターンルール(`%:`) / $(shell が対象ターゲットの判定に関わる場合は
   UNKNOWN（unsupported_analysis）。
5. 上記のいずれでもなければ VERIFIED_IN_SCOPE（conformant）。

ルールごとに Finding は 1 件。複数ターゲットの結果は message / actual にまとめ、
最悪の verdict（VIOLATED > UNKNOWN > VERIFIED_IN_SCOPE）を Finding 全体の verdict に採用する。
"""

from __future__ import annotations

import fnmatch
import re
from dataclasses import dataclass, field

from speclino.domain.common import SourceRef
from speclino.domain.finding import Category, Finding, Verdict
from speclino.domain.policy import PolicyRule
from speclino.verifiers.base import Snapshot

VERIFIER_ID = "makefile.required_targets"
VERIFIER_VERSION = "1.0.0"

_DEFAULT_TARGETS = ["setup", "test", "lint"]
_DEFAULT_TRIVIAL = ["true", ":", "@true", "echo *"]

_INCLUDE_RE = re.compile(r"^\s*(-|s)?include\b")
_ASSIGNMENT_RE = re.compile(r"^\s*[A-Za-z_][A-Za-z0-9_]*\s*(:=|::=|\+=|\?=|!=|=)(\s|$)")
_VERDICT_RANK: dict[Verdict, int] = {"VIOLATED": 2, "UNKNOWN": 1, "VERIFIED_IN_SCOPE": 0}
_VERDICT_CATEGORY: dict[Verdict, Category] = {
    "VIOLATED": "missing_effect",
    "UNKNOWN": "unsupported_analysis",
    "VERIFIED_IN_SCOPE": "conformant",
}


@dataclass
class _TargetDef:
    name: str
    prereqs: list[str] = field(default_factory=list)
    recipe_lines: list[str] = field(default_factory=list)
    line_no: int = 0
    is_pattern: bool = False


@dataclass
class _ParsedMakefile:
    targets: dict[str, _TargetDef]
    has_include: bool
    has_eval: bool
    has_shell: bool
    has_pattern_rule: bool

    @property
    def affects_lookup(self) -> bool:
        """未発見のターゲットが include/eval/パターンルール/shell で定義され得るか。"""
        return self.has_include or self.has_eval or self.has_shell or self.has_pattern_rule


@dataclass
class _TargetResult:
    verdict: Verdict
    detail: str
    line_no: int | None = None


def _split_logical_lines(text: str) -> list[tuple[int, str]]:
    """行継続 (`\\`) を結合した論理行を (開始行番号, 内容) の一覧で返す。"""
    raw_lines = text.split("\n")
    logical: list[tuple[int, str]] = []
    i = 0
    while i < len(raw_lines):
        start = i + 1
        parts = [raw_lines[i]]
        while parts[-1].endswith("\\") and i + 1 < len(raw_lines):
            parts[-1] = parts[-1][:-1]
            i += 1
            parts.append(raw_lines[i])
        logical.append((start, "".join(parts)))
        i += 1
    return logical


def _find_rule_colon(line: str) -> int | None:
    """代入 (`:=`) と区別して、ルール定義のコロン位置を返す。"""
    idx = line.find(":")
    if idx == -1:
        return None
    if line[idx : idx + 2] == ":=":
        return None
    return idx


def _parse_makefile(text: str) -> _ParsedMakefile:
    targets: dict[str, _TargetDef] = {}
    has_include = False
    has_eval = "$(eval" in text
    has_shell = "$(shell" in text
    has_pattern_rule = False

    current: list[_TargetDef] | None = None
    for line_no, line in _split_logical_lines(text):
        if line.startswith("\t"):
            if current is not None:
                recipe_line = line[1:]
                for target_def in current:
                    target_def.recipe_lines.append(recipe_line)
            continue

        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            current = None
            continue

        if _INCLUDE_RE.match(line):
            has_include = True
            current = None
            continue

        if _ASSIGNMENT_RE.match(line):
            # 代入行 (VAR = / VAR := / VAR ?= / VAR += / VAR != value) はルール定義ではない。
            current = None
            continue

        idx = _find_rule_colon(line)
        if idx is None:
            current = None
            continue

        targets_part = line[:idx].strip()
        rest = line[idx + 1 :]
        if not targets_part:
            current = None
            continue

        inline_recipe: str | None = None
        if ";" in rest:
            prereqs_part, inline_recipe = rest.split(";", 1)
            inline_recipe = inline_recipe.strip()
        else:
            prereqs_part = rest
        prereqs = [p for p in prereqs_part.split() if p != "|"]

        names = targets_part.split()
        current = []
        for name in names:
            is_pattern = "%" in name
            if is_pattern:
                has_pattern_rule = True
            td = targets.get(name)
            if td is None:
                td = _TargetDef(name=name, line_no=line_no, is_pattern=is_pattern)
                targets[name] = td
            for p in prereqs:
                if p not in td.prereqs:
                    td.prereqs.append(p)
            if inline_recipe:
                td.recipe_lines.append(inline_recipe)
            current.append(td)

    return _ParsedMakefile(
        targets=targets,
        has_include=has_include,
        has_eval=has_eval,
        has_shell=has_shell,
        has_pattern_rule=has_pattern_rule,
    )


def _is_trivial_line(line: str, trivial_patterns: list[str]) -> bool:
    return any(fnmatch.fnmatchcase(line, pat) for pat in trivial_patterns)


def _all_trivial(lines: list[str], trivial_patterns: list[str]) -> bool:
    return all(_is_trivial_line(line, trivial_patterns) for line in lines)


def _resolve_target(
    name: str, parsed: _ParsedMakefile, trivial_patterns: list[str]
) -> _TargetResult:
    td = parsed.targets.get(name)
    if td is None or td.is_pattern:
        if parsed.affects_lookup:
            return _TargetResult(
                verdict="UNKNOWN",
                detail=(
                    f"'{name}' はこのファイル内に直接見つからず、"
                    "include/$(eval/パターンルール/$(shell の影響で判定できません"
                ),
            )
        return _TargetResult(verdict="VIOLATED", detail=f"'{name}' が未定義です")

    non_blank = [line for line in td.recipe_lines if line.strip()]
    if non_blank:
        if _all_trivial(non_blank, trivial_patterns):
            return _TargetResult(
                verdict="VIOLATED",
                detail=(
                    f"'{name}' は定義されていますが、レシピが trivial のみで実処理がありません"
                    "（存在と内容を区別）"
                ),
                line_no=td.line_no,
            )
        return _TargetResult(
            verdict="VERIFIED_IN_SCOPE",
            detail=f"'{name}' は実処理のレシピを持ちます",
            line_no=td.line_no,
        )

    # 自身のレシピが無い場合
    if td.prereqs:
        first_dep = td.prereqs[0]
        dep = parsed.targets.get(first_dep)
        if dep is None or dep.is_pattern:
            if parsed.affects_lookup:
                return _TargetResult(
                    verdict="UNKNOWN",
                    detail=(
                        f"'{name}' は自身のレシピが無く、依存先 '{first_dep}' も"
                        "include/$(eval/パターンルール/$(shell の影響で判定できません"
                    ),
                    line_no=td.line_no,
                )
            return _TargetResult(
                verdict="VIOLATED",
                detail=(
                    f"'{name}' は自身のレシピが無く、依存先 '{first_dep}' も未定義です"
                    "（存在と内容を区別）"
                ),
                line_no=td.line_no,
            )
        dep_non_blank = [line for line in dep.recipe_lines if line.strip()]
        if dep_non_blank and not _all_trivial(dep_non_blank, trivial_patterns):
            return _TargetResult(
                verdict="VERIFIED_IN_SCOPE",
                detail=(
                    f"'{name}' は自身のレシピが無いものの、依存先 '{first_dep}' が"
                    "実処理を持つため成立します"
                ),
                line_no=td.line_no,
            )
        return _TargetResult(
            verdict="VIOLATED",
            detail=(
                f"'{name}' は自身のレシピが無く、依存先 '{first_dep}' も実処理を持ちません"
                "（存在と内容を区別）"
            ),
            line_no=td.line_no,
        )

    return _TargetResult(
        verdict="VIOLATED",
        detail=f"'{name}' は定義されていますが、レシピが空です（存在と内容を区別）",
        line_no=td.line_no,
    )


def _worst(results: dict[str, _TargetResult]) -> Verdict:
    return max((r.verdict for r in results.values()), key=lambda v: _VERDICT_RANK[v])


class MakeTargetsVerifier:
    id = VERIFIER_ID
    version = VERIFIER_VERSION

    def run(self, rule: PolicyRule, snapshot: Snapshot) -> list[Finding]:
        params = rule.verifier_ref.params
        targets: list[str] = list(params.get("targets", _DEFAULT_TARGETS))
        trivial: list[str] = list(params.get("trivial", _DEFAULT_TRIVIAL))

        paths = [p for pattern in rule.applies_to.paths for p in snapshot.glob(pattern)]
        if not paths:
            # registry が on_missing_input を処理するので、ここには通常到達しない。
            return []
        # ルールごとに Finding 1 件なので、applies_to に複数一致しても最初の1件だけを見る
        # （speclino-core.yaml の MAKE-001 は Makefile 1 個のみを対象にする想定）。
        makefile_path = paths[0]
        text = snapshot.read_text(makefile_path) or ""
        parsed = _parse_makefile(text)

        results: dict[str, _TargetResult] = {
            name: _resolve_target(name, parsed, trivial) for name in targets
        }
        verdict = _worst(results)
        category = _VERDICT_CATEGORY[verdict]

        lines = [f"- {name}: {r.detail}" for name, r in results.items()]
        message = f"{rule.id} ({makefile_path}) の判定: {verdict}\n" + "\n".join(lines)
        actual = "\n".join(lines)
        expected = f"ターゲット {', '.join(targets)} が実処理のレシピを持つこと"

        code_refs = [
            SourceRef(snapshot_id=snapshot.snapshot_id, path=makefile_path, line_start=r.line_no)
            for r in results.values()
            if r.line_no is not None
        ]

        finding = Finding(
            id=f"policy:{rule.id}:{makefile_path}",
            requirement_id=rule.id,
            behavior_id=f"policy:{rule.verifier_ref.id}",
            verdict=verdict,
            category=category,
            severity="high" if rule.level == "mandatory" else "medium",
            message=message,
            expected=expected,
            actual=actual,
            claim_scope="command_invocation",
            code_refs=code_refs,
        )
        return [finding]
