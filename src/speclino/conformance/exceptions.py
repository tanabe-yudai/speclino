"""例外（免除）の適用（FR 003 / AC-009）。

例外は違反を適合へ書き換えず、免除として別記する。有効期限を過ぎた例外、必須項目を欠く例外は
無効（読込み時に除外）。対象は規約 ID（または仕様の要求 ID）と、任意でパスの glob。

チーム規約のルールが `exceptions_allowed=False` を宣言しているときは、対象・期限が一致する
例外があっても免除しない（S42: プロジェクト側の .speclino/exceptions.yaml でチーム規約の
mandatory ルールを回避できてしまう穴を塞ぐ）。呼び出し側が `policy_rules`（規約 ID →
PolicyRule）を渡さない、またはそのルールが渡された辞書に無い（仕様の要求など、そもそも
PolicyRule ではないもの）場合は、従来どおり免除できる。
"""

from __future__ import annotations

import fnmatch
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import date

from speclino.domain.finding import Finding, Findings
from speclino.domain.policy import PolicyException, PolicyExceptions, PolicyRule
from speclino.verifiers.exceptions import effective_exceptions


@dataclass(frozen=True)
class Waiver:
    finding_id: str
    requirement_id: str
    exception_id: str
    approver: str
    expires_on: date
    reason: str


@dataclass(frozen=True)
class RejectedWaiver:
    """対象・期限は一致したが、ルールが免除を許さないため適用されなかった例外。

    黙って無視すると「免除を書いたのに効いていない」ことに誰も気付けない。呼び出し側
    （check_pipeline）はこれを Finding.next_steps 等に残し、免除が拒否された事実を明示する。
    """

    finding_id: str
    requirement_id: str
    exception_id: str
    rule: str


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


def _rule_allows_exception(rule_id: str, policy_rules: Mapping[str, PolicyRule] | None) -> bool:
    """`rule_id` の PolicyRule が免除を許すか。渡された辞書に無いルール（仕様の要求など、
    そもそも PolicyRule ではないもの）は従来どおり免除可とする。
    """
    if policy_rules is None:
        return True
    rule = policy_rules.get(rule_id)
    return True if rule is None else rule.exceptions_allowed


def waivers_for(
    findings: Findings,
    exceptions: PolicyExceptions,
    today: date,
    policy_rules: Mapping[str, PolicyRule] | None = None,
) -> list[Waiver]:
    """VIOLATED の Finding に適用できる有効な例外を列挙する（UNKNOWN / REVIEW は免除しない）。

    `policy_rules`（規約 ID → PolicyRule。チーム規約とプロジェクト固有の規約を合わせたもの）を
    渡すと、`exceptions_allowed=False` のルールは対象・期限が一致していても免除しない。
    """
    active = effective_exceptions(exceptions, today)
    out: list[Waiver] = []
    for f in findings.findings:
        if f.verdict != "VIOLATED":
            continue
        for exc in active:
            if not _matches(exc, f):
                continue
            if not _rule_allows_exception(exc.rule, policy_rules):
                continue
            out.append(
                Waiver(f.id, f.requirement_id, exc.id, exc.approver, exc.expires_on, exc.reason)
            )
            break
    return out


def rejected_exceptions_for(
    findings: Findings,
    exceptions: PolicyExceptions,
    today: date,
    policy_rules: Mapping[str, PolicyRule] | None = None,
) -> list[RejectedWaiver]:
    """対象・期限は一致したが、ルールが免除を禁止する（`exceptions_allowed=False`）ため
    適用されなかった例外を列挙する。`policy_rules` を渡さないときは常に空（従来どおり
    禁止の判定自体を行わない）。
    """
    if policy_rules is None:
        return []
    active = effective_exceptions(exceptions, today)
    out: list[RejectedWaiver] = []
    for f in findings.findings:
        if f.verdict != "VIOLATED":
            continue
        for exc in active:
            if _matches(exc, f) and not _rule_allows_exception(exc.rule, policy_rules):
                out.append(RejectedWaiver(f.id, f.requirement_id, exc.id, exc.rule))
                break
    return out


def waived_ids(
    findings: Findings,
    exceptions: PolicyExceptions,
    today: date,
    policy_rules: Mapping[str, PolicyRule] | None = None,
) -> frozenset[str]:
    return frozenset(w.finding_id for w in waivers_for(findings, exceptions, today, policy_rules))


__all__ = [
    "RejectedWaiver",
    "Waiver",
    "rejected_exceptions_for",
    "waived_ids",
    "waivers_for",
]
