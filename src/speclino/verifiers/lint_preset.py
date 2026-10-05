"""LINT: リンターのプリセットが有効で、CI で実行されているかを検証する（S69、
`design/2026-10-05-team-knowledge-registry.md` 6.1）。

設定ファイルと CI 定義を **読むだけ** で、リンターも CI も実行しない。

設定（``tool`` / ``config_path`` / ``required_rules``）:

- ``ruff``: ``pyproject.toml``（``[tool.ruff.lint]``）または ``ruff.toml`` / ``.ruff.toml``
  （``[lint]``）を TOML として読む。有効な規則は ``select`` + ``extend-select``（旧形式のトップ
  レベルも可）。``select`` が無いときは ruff の既定（``E4`` ``E7`` ``E9`` ``F``）。必須の規則コード
  は、``select`` のいずれかがその接頭辞であれば満たす（``E`` は ``E501`` を含むが、``F`` は
  ``FBT001`` を含まない。直後が数字のときだけ接頭辞。``PL`` は ``PLR`` 等を含む特例。
  ``ALL`` は全部）。
  ``ignore`` にも同様に接頭辞で当たる場合は、**より長い（具体的な）接頭辞が勝ち、同じ長さなら
  ignore が勝つ**。``per-file-ignores`` は見ない。
- ``eslint``: 旧形式の設定（JSON の ``.eslintrc.json`` / ``.eslintrc``、または YAML）の
  ``rules`` を読み、値が ``off`` / ``0``（配列なら先頭）でないものを有効とする。必須の規則は
  名前の完全一致。``overrides`` は見ない。フラット設定（``eslint.config.js`` など JS / TS）は
  静的に評価できないため UNKNOWN（unsupported_analysis）。

CI（``ci_glob`` と ``ci_command_pattern`` を両方指定したとき）: ``ci_glob`` に一致するワークフロー
YAML のどこかの ``run:`` ステップが ``ci_command_pattern``（``search``、タイムアウト付き）に
一致すること。``continue-on-error: true`` のステップは失敗が伝播しないため数えない。``if:`` が
付いたステップ（またはジョブ）だけが一致する場合は実行条件を静的に判定できないので UNKNOWN。
YAML が解析できないファイルがあり、他に一致が無い場合、照合がタイムアウトした場合も UNKNOWN。

継承: ruff の ``extend``、ESLint の ``extends`` がある設定では、必須の規則が設定内で明示的に
有効でなければ UNKNOWN（継承元は解析しない）。明示的に無効（ignore / off）なら VIOLATED。
``ruff`` の ``select`` 省略時の既定は、継承が無い場合だけ仮定する。

設定ファイル・CI ファイルが無い場合は ``rule.on_missing_input`` に従う（``violation`` は VIOLATED、
``unknown`` は UNKNOWN、``skipped`` は Finding を出さない）。

Finding は設定ファイルで 1 件（requirement_id ``<rule.id>:<config_path>``）、CI で 1 件
（``<rule.id>:<ci_glob>``）。
"""

from __future__ import annotations

import json
import tomllib
from dataclasses import dataclass
from typing import Any

import yaml

from speclino.domain.finding import Finding
from speclino.domain.policy import PolicyRule
from speclino.domain.policy_params import LintPresetParams
from speclino.verifiers._findings import (
    invalid_params_finding,
    make_finding,
    missing_input_finding,
    parse_params,
    unknown_finding,
)
from speclino.verifiers._safe_regex import RegexMatcher, RegexTimeout, compile_param
from speclino.verifiers.base import Snapshot

VERIFIER_ID = "lint.preset"
VERIFIER_VERSION = "1.0.0"

_RUFF_DEFAULT_SELECT = ["E4", "E7", "E9", "F"]
_ESLINT_FLAT_SUFFIXES = (".js", ".cjs", ".mjs", ".ts", ".cts", ".mts")


class _Unparsable(Exception):
    pass


def _string_list(value: Any) -> list[str]:
    if isinstance(value, list):
        return [v for v in value if isinstance(v, str)]
    return []


def _ruff_lint_table(doc: dict[str, Any], config_path: str) -> dict[str, Any]:
    if config_path.endswith("pyproject.toml"):
        tool = doc.get("tool")
        ruff = tool.get("ruff") if isinstance(tool, dict) else None
    else:
        ruff = doc
    if not isinstance(ruff, dict):
        return {}
    lint = ruff.get("lint")
    merged: dict[str, Any] = dict(lint) if isinstance(lint, dict) else {}
    for key in ("select", "extend-select", "ignore", "extend-ignore"):  # 旧形式のトップレベル
        if key in ruff and key not in merged:
            merged[key] = ruff[key]
    return merged


def _ruff_matches(entry: str, code: str) -> bool:
    """ruff の選択子 ``entry`` が規則コード ``code`` を含むか。

    完全一致、または ``code`` が ``entry`` で始まり直後が数字のときだけ一致する（``F`` は
    ``FBT001`` を含まない。別のリンターの接頭辞だから）。例外として ``PL`` は ``PLC`` ``PLE``
    ``PLR`` ``PLW`` を含む（Pylint の子リンター）。
    """
    if entry == "ALL" or entry == code:
        return True
    if not code.startswith(entry) or len(code) == len(entry):
        return False
    following = code[len(entry)]
    return following.isdigit() or (entry == "PL" and following in "CERW")


def _ruff_check(text: str, config_path: str, required: list[str]) -> tuple[list[str], list[str]]:
    """(無効な必須規則, 継承のため判定できない必須規則) を返す。"""
    try:
        doc = tomllib.loads(text)
    except tomllib.TOMLDecodeError as e:
        raise _Unparsable(f"TOML として解析できません: {e}") from e
    lint = _ruff_lint_table(doc, config_path)
    inherits = _ruff_inherits(doc, config_path)
    if "select" in lint:
        base_select = _string_list(lint["select"])
    else:
        # 継承があれば親が select を定めているかもしれないので、既定を仮定しない
        base_select = [] if inherits else list(_RUFF_DEFAULT_SELECT)
    select = base_select + _string_list(lint.get("extend-select"))
    ignore = _string_list(lint.get("ignore")) + _string_list(lint.get("extend-ignore"))

    def specificity(entries: list[str], code: str) -> int:
        # ALL の具体性は 0（select でも ignore でも）。具体的な指定が勝ち、同じなら ignore が勝つ
        scores = [0 if e == "ALL" else len(e) for e in entries if _ruff_matches(e, code)]
        return max(scores) if scores else -1

    missing: list[str] = []
    undetermined: list[str] = []
    for code in required:
        selected = specificity(select, code)
        ignored = specificity(ignore, code)
        if selected >= 0 and ignored < selected:
            continue
        if ignored >= 0:
            missing.append(code)  # 明示的に無効
        elif inherits:
            undetermined.append(code)  # 継承元で有効かもしれない
        else:
            missing.append(code)
    return missing, undetermined


def _ruff_inherits(doc: dict[str, Any], config_path: str) -> bool:
    if config_path.endswith("pyproject.toml"):
        tool = doc.get("tool")
        ruff = tool.get("ruff") if isinstance(tool, dict) else None
    else:
        ruff = doc
    return isinstance(ruff, dict) and "extend" in ruff


def _eslint_enabled(value: Any) -> bool:
    level = value[0] if isinstance(value, list) and value else value
    return level not in ("off", 0)


def _eslint_check(text: str, config_path: str, required: list[str]) -> tuple[list[str], list[str]]:
    """(無効な必須規則, 継承のため判定できない必須規則) を返す。"""
    try:
        doc = yaml.safe_load(text) if config_path.endswith((".yml", ".yaml")) else json.loads(text)
    except (json.JSONDecodeError, yaml.YAMLError) as e:
        raise _Unparsable(f"設定として解析できません: {e}") from e
    if not isinstance(doc, dict):
        raise _Unparsable("設定の形式が不正です（トップレベルがオブジェクトでない）")
    rules = doc.get("rules")
    rules = rules if isinstance(rules, dict) else {}
    inherits = "extends" in doc
    missing: list[str] = []
    undetermined: list[str] = []
    for code in required:
        if code in rules:
            if not _eslint_enabled(rules[code]):
                missing.append(code)  # 明示的に off
        elif inherits:
            undetermined.append(code)
        else:
            missing.append(code)
    return missing, undetermined


@dataclass
class _CiMatch:
    solid: bool = False
    conditional: bool = False
    unparsable: int = 0
    timed_out: bool = False


def _collect_ci_matches(doc: Any, pattern: Any, matcher: RegexMatcher, result: _CiMatch) -> None:
    jobs = doc.get("jobs") if isinstance(doc, dict) else None
    if not isinstance(jobs, dict):
        return
    for job in jobs.values():
        if not isinstance(job, dict):
            continue
        steps = job.get("steps")
        for step in steps if isinstance(steps, list) else []:
            if not isinstance(step, dict) or not isinstance(step.get("run"), str):
                continue
            try:
                if not matcher.search(pattern, step["run"]):
                    continue
            except RegexTimeout:
                result.timed_out = True
                continue
            if step.get("continue-on-error") is True or job.get("continue-on-error") is True:
                continue
            if "if" in step or "if" in job:
                result.conditional = True
            else:
                result.solid = True


class LintPresetVerifier:
    id = VERIFIER_ID
    version = VERIFIER_VERSION

    def run(self, rule: PolicyRule, snapshot: Snapshot) -> list[Finding]:
        try:
            params = parse_params(LintPresetParams, rule)
        except ValueError as e:
            return [invalid_params_finding(rule, e)]
        ci_pattern = compile_param(params.ci_command_pattern) if params.ci_command_pattern else None
        findings: list[Finding] = []
        config = self._check_config(rule, snapshot, params)
        if config is not None:
            findings.append(config)
        if params.ci_glob is not None and ci_pattern is not None:
            ci = self._check_ci(rule, snapshot, params.ci_glob, ci_pattern)
            if ci is not None:
                findings.append(ci)
        return findings

    def _check_config(
        self, rule: PolicyRule, snapshot: Snapshot, params: LintPresetParams
    ) -> Finding | None:
        path = params.config_path
        text = snapshot.read_text(path)
        if text is None:
            return missing_input_finding(rule, snapshot, path, "リンターの設定ファイル")
        if params.tool == "eslint" and path.endswith(_ESLINT_FLAT_SUFFIXES):
            return unknown_finding(
                rule,
                snapshot,
                path,
                f"{path}: JS / TS の ESLint 設定は静的に評価できません",
                "未対応の設定形式",
            )
        try:
            missing, undetermined = (
                _ruff_check(text, path, params.required_rules)
                if params.tool == "ruff"
                else _eslint_check(text, path, params.required_rules)
            )
        except _Unparsable as e:
            return unknown_finding(rule, snapshot, path, f"{path}: {e}", "解析エラー")
        required = ", ".join(params.required_rules) or "(なし)"
        expected = f"{params.tool} の必須規則が有効であること: {required}"
        if missing:
            return make_finding(
                rule,
                snapshot,
                path,
                "VIOLATED",
                "missing_effect",
                f"{path}: 必須の規則が有効になっていません: {', '.join(missing)}",
                expected=expected,
                actual=f"無効または未設定: {', '.join(missing)}"
                + (f"\n（判定不能: {', '.join(undetermined)}）" if undetermined else ""),
            )
        if undetermined:
            return unknown_finding(
                rule,
                snapshot,
                path,
                f"{path}: 継承（extend / extends）元を解析しないため判定できない規則があります",
                f"継承元で有効かもしれない規則: {', '.join(undetermined)}",
            )
        return make_finding(
            rule,
            snapshot,
            path,
            "VERIFIED_IN_SCOPE",
            "conformant",
            f"{path}: 必須の規則はすべて有効です",
            expected=expected,
            actual="違反なし",
        )

    def _check_ci(
        self, rule: PolicyRule, snapshot: Snapshot, ci_glob: str, pattern: Any
    ) -> Finding | None:
        files = snapshot.glob(ci_glob)
        if not files:
            return missing_input_finding(rule, snapshot, ci_glob, "CI の定義")
        result = _CiMatch()
        matcher = RegexMatcher()
        for file in files:
            try:
                doc = yaml.safe_load(snapshot.read_text(file) or "")
            except yaml.YAMLError:
                result.unparsable += 1
                continue
            _collect_ci_matches(doc, pattern, matcher, result)
        expected = f"CI のいずれかの run ステップが {pattern.pattern} に一致すること"
        if result.solid:
            return make_finding(
                rule,
                snapshot,
                ci_glob,
                "VERIFIED_IN_SCOPE",
                "conformant",
                f"{ci_glob}: リンターを実行する CI ステップがあります",
                expected=expected,
                actual="違反なし",
            )
        if result.conditional or result.unparsable or result.timed_out:
            reason = (
                "正規表現の照合がタイムアウトしたステップがあります"
                if result.timed_out
                else "一致するステップに if: が付いており実行条件を判定できません"
                if result.conditional
                else f"解析できない YAML が {result.unparsable} 件あり、他に一致がありません"
            )
            return unknown_finding(rule, snapshot, ci_glob, f"{ci_glob}: {reason}", reason)
        return make_finding(
            rule,
            snapshot,
            ci_glob,
            "VIOLATED",
            "missing_effect",
            f"{ci_glob}: リンターを実行する CI ステップが見つかりません",
            expected=expected,
            actual="一致する run ステップなし",
        )
