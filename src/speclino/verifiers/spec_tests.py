"""SPEC-TEST-001: `@pytest.mark.spec` マーカーと Spec IR の `tests` の対応を検査する（S49）。

同じ対応関係（要求 ↔ テスト）が2か所で二重管理になっている:

- Spec IR の ``rules[].tests``（要求 → テスト ID の宣言）
- テスト側の ``@pytest.mark.spec("R-...")``（テスト → 要求のマーカー）

S38 で「マーカーは付いているが Spec IR の ``tests`` は空」という状態が実際に起き、
``test_run`` が ``no_declared_tests`` で何もしなかった。この検査器はそのずれを2方向から
機械的に検出する:

1. **Spec IR → 実体**: 各 rule の ``tests`` に列挙された各テスト ID（``path::name`` 形式）に
   ついて、``path`` がスナップショットに存在し、その中に ``def name``（または
   ``async def name``）があるかを見る。無ければ VIOLATED（``missing_effect``）。
   このチェックは ``test_paths`` の glob には依存しない（宣言された ID が指す実際のパスを
   直接読む）。
2. **マーカー → 宣言**: ``test_paths`` に一致するテストファイルを ``ast.parse`` で読み、
   ``@pytest.mark.<marker>("...")`` / ``@mark.<marker>("...")`` が付いた各テストについて、
   その要求 ID が ``spec_ir_paths`` の Spec IR に存在し、かつ当該 rule の ``tests`` に
   このテスト ID が含まれているかを見る。要求 ID が Spec IR に無ければ VIOLATED（別メッセージ）、
   あればテスト ID が ``tests`` に無いことも VIOLATED（どちらも ``missing_effect``）。

Spec IR は ``json.loads`` で読む（``speclino.spec.load_spec_ir`` は使わない。検査器は
スナップショットからテキストを読むだけで、ファイルパスの実在を前提にしない）。テストは
``ast.parse`` で読み、対象リポジトリのコードを import・実行しない（PoC 16 章）。

Finding は2方向・宣言（rule, test_id）ごと・マーカー（path, name, requirement_id）ごとに
1件。同じ入力からは同じ順序・同じ ``id`` になる（決定的）。
"""

from __future__ import annotations

import ast
import json
from dataclasses import dataclass
from typing import Any

from speclino.domain.common import SourceRef
from speclino.domain.finding import Category, Finding, Severity, Verdict
from speclino.domain.policy import PolicyRule
from speclino.verifiers.base import Snapshot

VERIFIER_ID = "spec_tests"
VERIFIER_VERSION = "1.0.0"

_DEFAULT_MARKER = "spec"


@dataclass(frozen=True)
class _TestId:
    path: str
    name: str

    @property
    def test_id(self) -> str:
        return f"{self.path}::{self.name}"


@dataclass(frozen=True)
class _Marker:
    """1つのテスト関数に付いた ``@pytest.mark.<marker>("...")`` 1件。"""

    test: _TestId
    requirement_id: str
    line_no: int


@dataclass(frozen=True)
class _SpecRule:
    spec_path: str
    rule_id: str
    tests: tuple[str, ...]


def _glob_sorted(snapshot: Snapshot, patterns: list[str]) -> list[str]:
    paths: list[str] = []
    for pattern in patterns:
        for p in snapshot.glob(pattern):
            if p not in paths:
                paths.append(p)
    paths.sort()
    return paths


def _load_spec_rules(snapshot: Snapshot, spec_files: list[str]) -> list[_SpecRule]:
    """spec_files（json.loads で読む）から (path, rule.id, rule.tests) を集める。

    壊れた JSON・想定外の形（トップレベルが dict でない、rules が list でない等）は
    黙って読み飛ばす。この検査器の役目は tests の対応検査であり、Spec IR 自体の形式検査
    ではない。
    """
    spec_rules: list[_SpecRule] = []
    for path in spec_files:
        text = snapshot.read_text(path) or ""
        try:
            data: Any = json.loads(text)
        except json.JSONDecodeError:
            continue
        if not isinstance(data, dict):
            continue
        rules = data.get("rules")
        if not isinstance(rules, list):
            continue
        for r in rules:
            if not isinstance(r, dict):
                continue
            rule_id = r.get("id")
            if not isinstance(rule_id, str):
                continue
            tests_value = r.get("tests", [])
            tests = (
                tuple(t for t in tests_value if isinstance(t, str))
                if isinstance(tests_value, list)
                else ()
            )
            spec_rules.append(_SpecRule(spec_path=path, rule_id=rule_id, tests=tests))
    return spec_rules


def _decorator_marker_arg(node: ast.expr, marker: str) -> str | None:
    """decorator が ``pytest.mark.<marker>("...")`` / ``mark.<marker>("...")`` なら引数を返す。"""
    if not isinstance(node, ast.Call):
        return None
    func = node.func
    if not (isinstance(func, ast.Attribute) and func.attr == marker):
        return None
    value = func.value
    is_pytest_mark = (
        isinstance(value, ast.Attribute)
        and value.attr == "mark"
        and isinstance(value.value, ast.Name)
        and value.value.id == "pytest"
    )
    is_bare_mark = isinstance(value, ast.Name) and value.id == "mark"
    if not (is_pytest_mark or is_bare_mark):
        return None
    if len(node.args) != 1 or not isinstance(node.args[0], ast.Constant):
        return None
    arg = node.args[0].value
    return arg if isinstance(arg, str) else None


def _parse_test_file(path: str, text: str, marker: str) -> tuple[dict[str, int], list[_Marker]]:
    """(関数名 → 定義行, マーカー一覧) を返す。構文エラー時は両方空。

    ``ast.walk`` で全ての関数定義（同期・非同期、ネストも含む）を集める。テストクラス
    （``class Test...``）はこのリポジトリに存在しないため対応しない。
    """
    try:
        tree = ast.parse(text, filename=path)
    except SyntaxError:
        return {}, []
    names: dict[str, int] = {}
    markers: list[_Marker] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef):
            continue
        names[node.name] = node.lineno
        for dec in node.decorator_list:
            req_id = _decorator_marker_arg(dec, marker)
            if req_id is not None:
                markers.append(
                    _Marker(
                        test=_TestId(path=path, name=node.name),
                        requirement_id=req_id,
                        line_no=dec.lineno,
                    )
                )
    return names, markers


_ParsedFile = tuple[dict[str, int], list[_Marker]]


def _parsed_for(
    snapshot: Snapshot, cache: dict[str, _ParsedFile | None], path: str, marker: str
) -> _ParsedFile | None:
    """path の (関数名 → 定義行, マーカー一覧)。存在しなければ None（結果をキャッシュする）。

    宣言されたテスト ID が指すパス（direction 1）と test_paths に一致するパス
    （direction 2）は重なることが多い（宣言した ID は通常 test_paths 配下を指す）。
    両方向で同じキャッシュを共有し、同じパスの read_text・ast.parse は高々1回にする。
    """
    if path not in cache:
        text = snapshot.read_text(path)
        cache[path] = None if text is None else _parse_test_file(path, text, marker)
    return cache[path]


class SpecTestsVerifier:
    id = VERIFIER_ID
    version = VERIFIER_VERSION

    def run(self, rule: PolicyRule, snapshot: Snapshot) -> list[Finding]:
        params = rule.verifier_ref.params
        spec_ir_paths: list[str] = list(params["spec_ir_paths"])
        test_paths: list[str] = list(params["test_paths"])
        marker: str = params.get("marker", _DEFAULT_MARKER)

        spec_files = _glob_sorted(snapshot, spec_ir_paths)
        test_files = _glob_sorted(snapshot, test_paths)

        spec_rules = _load_spec_rules(snapshot, spec_files)

        # (関数名 → 定義行, マーカー一覧) をパスごとにキャッシュする。direction 1 が指す
        # パスは test_paths の glob に一致するとは限らないため on-demand に読む。
        parsed_cache: dict[str, _ParsedFile | None] = {}

        findings: list[Finding] = []
        for spec_rule in spec_rules:
            for test_id in spec_rule.tests:
                findings.append(
                    self._check_declared_test(
                        rule, snapshot, parsed_cache, marker, spec_rule, test_id
                    )
                )

        # direction 2（マーカー → 宣言）用: test_paths に一致するファイルだけを走査する。
        all_markers: list[_Marker] = []
        for path in test_files:
            parsed = _parsed_for(snapshot, parsed_cache, path, marker)
            if parsed is not None:
                all_markers.extend(parsed[1])

        tests_by_rule_id: dict[str, set[str]] = {}
        for sr in spec_rules:
            tests_by_rule_id.setdefault(sr.rule_id, set()).update(sr.tests)

        for m in sorted(all_markers, key=lambda m: (m.test.path, m.test.name, m.requirement_id)):
            findings.append(self._check_marker(rule, snapshot, marker, tests_by_rule_id, m))

        return findings

    def _check_declared_test(
        self,
        rule: PolicyRule,
        snapshot: Snapshot,
        parsed_cache: dict[str, _ParsedFile | None],
        marker: str,
        spec_rule: _SpecRule,
        test_id: str,
    ) -> Finding:
        # 既存の規約 Finding と同じ "<rule>:<path>" の 2 段にする。3 段にすると
        # exceptions.yaml の照合（requirement_id の 2 段目を path として glob する）が
        # 効かず免除できない。どの要求・どのテストかは Finding.id と message が持つ。
        requirement_id = f"{rule.id}:{test_id.split('::', 1)[0]}"
        parts = test_id.split("::")
        verdict: Verdict
        category: Category
        code_refs: list[SourceRef] = []

        if len(parts) < 2 or not parts[0] or not parts[-1]:
            verdict, category = "VIOLATED", "missing_effect"
            actual = f"path::name 形式ではありません: {test_id}"
            next_steps = [
                f"Spec IR（{spec_rule.spec_path}）の {spec_rule.rule_id}.tests の"
                f" '{test_id}' を 'path/to/test.py::test_name' 形式に修正する"
            ]
        else:
            test_path, func_name = parts[0], parts[-1]
            parsed = _parsed_for(snapshot, parsed_cache, test_path, marker)
            names = parsed[0] if parsed is not None else None
            if names is None:
                verdict, category = "VIOLATED", "missing_effect"
                actual = f"ファイルが存在しません: {test_path}"
                next_steps = [
                    f"Spec IR（{spec_rule.spec_path}）の {spec_rule.rule_id}.tests から"
                    f" '{test_id}' を削除する、または {test_path} を追加する"
                ]
            elif func_name not in names:
                verdict, category = "VIOLATED", "missing_effect"
                actual = f"{test_path} に def {func_name}（または async def）がありません"
                next_steps = [
                    f"Spec IR（{spec_rule.spec_path}）の {spec_rule.rule_id}.tests から"
                    f" '{test_id}' を削除する、または {test_path} に {func_name} を実装する"
                ]
            else:
                verdict, category = "VERIFIED_IN_SCOPE", "conformant"
                actual = f"{test_id} は実在します"
                next_steps = []
                code_refs = [
                    SourceRef(
                        snapshot_id=snapshot.snapshot_id,
                        path=test_path,
                        line_start=names[func_name],
                    )
                ]

        message = f"{spec_rule.spec_path}: {spec_rule.rule_id}.tests の '{test_id}': {actual}"

        return Finding(
            id=f"policy:{rule.id}:declared:{spec_rule.rule_id}:{test_id}",
            requirement_id=requirement_id,
            behavior_id=f"policy:{rule.verifier_ref.id}",
            verdict=verdict,
            category=category,
            severity=self._severity(rule),
            message=message,
            expected="tests に宣言したテスト ID が実在するファイル::関数を指すこと",
            actual=actual,
            claim_scope="persisted_state",
            code_refs=code_refs,
            spec_refs=[
                SourceRef(
                    snapshot_id=snapshot.snapshot_id,
                    path=spec_rule.spec_path,
                    requirement_id=spec_rule.rule_id,
                )
            ],
            next_steps=next_steps,
        )

    def _check_marker(
        self,
        rule: PolicyRule,
        snapshot: Snapshot,
        marker: str,
        tests_by_rule_id: dict[str, set[str]],
        m: _Marker,
    ) -> Finding:
        requirement_id = f"{rule.id}:{m.test.path}"  # 上と同じ理由で 2 段
        marker_repr = f'@pytest.mark.{marker}("{m.requirement_id}")'
        verdict: Verdict
        category: Category
        next_steps: list[str]

        if m.requirement_id not in tests_by_rule_id:
            verdict, category = "VIOLATED", "missing_effect"
            actual = f"要求 ID {m.requirement_id} は Spec IR に存在しません"
            next_steps = [
                f"{m.requirement_id} を Spec IR に追加する、"
                f"または {m.test.test_id} のマーカーを正しい要求 ID に修正する／外す"
            ]
        elif m.test.test_id not in tests_by_rule_id[m.requirement_id]:
            verdict, category = "VIOLATED", "missing_effect"
            actual = f"{m.requirement_id}.tests に {m.test.test_id} が含まれていません"
            next_steps = [
                f"Spec IR の {m.requirement_id}.tests に {m.test.test_id} を追加する、"
                f"またはマーカー {marker_repr} を外す"
            ]
        else:
            verdict, category = "VERIFIED_IN_SCOPE", "conformant"
            actual = f"{m.test.test_id} は {m.requirement_id}.tests に宣言されています"
            next_steps = []

        message = f"{m.test.path}: {m.test.name} の {marker_repr}: {actual}"

        return Finding(
            id=f"policy:{rule.id}:marker:{m.requirement_id}:{m.test.test_id}",
            requirement_id=requirement_id,
            behavior_id=f"policy:{rule.verifier_ref.id}",
            verdict=verdict,
            category=category,
            severity=self._severity(rule),
            message=message,
            expected=(
                "マーカーの要求 ID が Spec IR に存在し、対応する rule.tests に"
                "このテスト ID が宣言されていること"
            ),
            actual=actual,
            claim_scope="persisted_state",
            code_refs=[
                SourceRef(snapshot_id=snapshot.snapshot_id, path=m.test.path, line_start=m.line_no)
            ],
            next_steps=next_steps,
        )

    def _severity(self, rule: PolicyRule) -> Severity:
        return "high" if rule.level == "mandatory" else "medium"
