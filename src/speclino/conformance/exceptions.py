"""例外（免除）の適用（FR 003 / AC-009）。

例外は違反を適合へ書き換えず、免除として別記する。有効期限を過ぎた例外、必須項目を欠く例外は
無効（読込み時に除外）。対象は規約 ID（または仕様の要求 ID）と、任意でパスの glob。
"""

from __future__ import annotations

import fnmatch
from dataclasses import dataclass
from datetime import date

from speclino.domain.finding import Finding, Findings
from speclino.domain.policy import PolicyException, PolicyExceptions
from speclino.verifiers.exceptions import effective_exceptions


@dataclass(frozen=True)
class Waiver:
    finding_id: str
    requirement_id: str
    exception_id: str
    approver: str
    expires_on: date
    reason: str


def _rule_of(requirement_id: str) -> str:
    # 規約 Finding は "<rule>:<path>"、仕様 Finding は要求 ID そのもの
    return requirement_id.split(":", 1)[0]


def _path_of(requirement_id: str, finding: Finding) -> str | None:
    if ":" in requirement_id:
        return requirement_id.split(":", 1)[1]
    for ref in finding.code_refs:
        if ref.path:
            return ref.path
    return None


def _matches(exc: PolicyException, finding: Finding) -> bool:
    if exc.rule != _rule_of(finding.requirement_id):
        return False
    path = _path_of(finding.requirement_id, finding)
    if path is None:
        # 位置を持たない Finding（削除された要求など）は "**" だけが一致する
        return "**" in exc.target.paths
    return any(fnmatch.fnmatch(path, pat) for pat in exc.target.paths)


def waivers_for(findings: Findings, exceptions: PolicyExceptions, today: date) -> list[Waiver]:
    """VIOLATED の Finding に適用できる有効な例外を列挙する（UNKNOWN / REVIEW は免除しない）。"""
    active = effective_exceptions(exceptions, today)
    out: list[Waiver] = []
    for f in findings.findings:
        if f.verdict != "VIOLATED":
            continue
        for exc in active:
            if _matches(exc, f):
                out.append(
                    Waiver(f.id, f.requirement_id, exc.id, exc.approver, exc.expires_on, exc.reason)
                )
                break
    return out


def waived_ids(findings: Findings, exceptions: PolicyExceptions, today: date) -> frozenset[str]:
    return frozenset(w.finding_id for w in waivers_for(findings, exceptions, today))


__all__ = ["Waiver", "waived_ids", "waivers_for"]
