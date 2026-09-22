"""SPEC-FMT-001: 仕様 Markdown の要求 ID と必須節を検証する（AC-002）。

要求 ID は front matter の `id` フィールドまたは `### <ID>` 見出しのどちらかで宣言できる
（`id_pattern` はどちらにも同じ正規表現を適用する）。必須節は見出し文字列の前方一致で判定する
（params の `required_sections` に完全な見出し文字列、例 `## 前提`、を指定する）。

ファイルごとに Finding 1 件。requirement_id は `<rule.id>:<path>`。
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any

import yaml

from speclino.domain.common import SourceRef
from speclino.domain.finding import Category, Finding, Verdict
from speclino.domain.policy import PolicyRule
from speclino.verifiers.base import Snapshot

VERIFIER_ID = "spec.required_sections"
VERIFIER_VERSION = "1.0.0"

_FRONT_MATTER_RE = re.compile(r"\A---[ \t]*\r?\n(.*?\r?\n)---[ \t]*\r?\n?", re.DOTALL)
_HEADING_ID_RE = re.compile(r"^###\s+(\S+)")


@dataclass
class _FoundId:
    value: str
    line_no: int | None  # front matter 由来は None


def _parse_front_matter(text: str) -> dict[str, Any]:
    """先頭の YAML front matter を辞書として返す。無い・壊れている場合は空辞書。"""
    m = _FRONT_MATTER_RE.match(text)
    if not m:
        return {}
    try:
        data = yaml.safe_load(m.group(1))
    except yaml.YAMLError:
        return {}
    return data if isinstance(data, dict) else {}


def _find_ids(
    text: str, id_pattern: re.Pattern[str], front_matter: dict[str, Any]
) -> list[_FoundId]:
    found: list[_FoundId] = []
    fm_id = front_matter.get("id")
    if isinstance(fm_id, str) and id_pattern.match(fm_id):
        found.append(_FoundId(value=fm_id, line_no=None))
    for i, line in enumerate(text.split("\n"), start=1):
        m = _HEADING_ID_RE.match(line.strip())
        if m and id_pattern.match(m.group(1)):
            found.append(_FoundId(value=m.group(1), line_no=i))
    return found


def _missing_sections(text: str, required_sections: list[str]) -> list[str]:
    lines = [line.rstrip() for line in text.split("\n")]
    return [
        section
        for section in required_sections
        if not any(line.startswith(section) for line in lines)
    ]


def _duplicate_ids(found: list[_FoundId]) -> list[str]:
    counts: dict[str, int] = {}
    for f in found:
        counts[f.value] = counts.get(f.value, 0) + 1
    return sorted(value for value, count in counts.items() if count > 1)


class SpecFormatVerifier:
    id = VERIFIER_ID
    version = VERIFIER_VERSION

    def run(self, rule: PolicyRule, snapshot: Snapshot) -> list[Finding]:
        params = rule.verifier.params
        id_pattern = re.compile(params["id_pattern"])
        required_sections: list[str] = list(params.get("required_sections", []))

        paths: list[str] = []
        for pattern in rule.applies_to.paths:
            for p in snapshot.glob(pattern):
                if p not in paths:
                    paths.append(p)
        paths.sort()

        findings: list[Finding] = []
        for path in paths:
            findings.append(self._check_file(rule, snapshot, path, id_pattern, required_sections))
        return findings

    def _check_file(
        self,
        rule: PolicyRule,
        snapshot: Snapshot,
        path: str,
        id_pattern: re.Pattern[str],
        required_sections: list[str],
    ) -> Finding:
        text = snapshot.read_text(path) or ""
        front_matter = _parse_front_matter(text)
        found_ids = _find_ids(text, id_pattern, front_matter)
        missing = _missing_sections(text, required_sections)
        dupes = _duplicate_ids(found_ids)

        verdict: Verdict
        category: Category
        if not found_ids:
            verdict, category = "VIOLATED", "missing_effect"
            message = f"{path}: 要求 ID 見出し（パターン {id_pattern.pattern}）が1つもありません"
            actual = "id なし"
        elif missing:
            verdict, category = "VIOLATED", "missing_effect"
            message = f"{path}: 必須節が欠落しています: {', '.join(missing)}"
            actual = f"欠落節: {', '.join(missing)}"
        elif dupes:
            verdict, category = "VIOLATED", "constraint_mismatch"
            message = f"{path}: 要求 ID が重複しています: {', '.join(dupes)}"
            actual = f"重複 ID: {', '.join(dupes)}"
        else:
            verdict, category = "VERIFIED_IN_SCOPE", "conformant"
            message = f"{path}: 要求 ID・必須節ともに適合しています"
            actual = f"id: {', '.join(f.value for f in found_ids)}"

        code_refs = [
            SourceRef(snapshot_id=snapshot.snapshot_id, path=path, line_start=f.line_no)
            for f in found_ids
            if f.line_no is not None
        ]

        return Finding(
            id=f"policy:{rule.id}:{path}",
            requirement_id=f"{rule.id}:{path}",
            behavior_id=f"policy:{rule.verifier.id}",
            verdict=verdict,
            category=category,
            severity="high" if rule.level == "mandatory" else "medium",
            message=message,
            expected=(
                "要求 ID 見出しが1つ以上あり、必須節"
                f"（{', '.join(required_sections)}）が揃い、ID が重複しないこと"
            ),
            actual=actual,
            claim_scope="persisted_state",
            code_refs=code_refs,
        )
