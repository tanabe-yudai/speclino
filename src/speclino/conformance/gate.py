"""gate（ハッカソン計画「判定モデル」）。Findings と fail_on 方針からゲート結果を決める。

verdict は正直に 4 値を保ち、ゲートはフェイルクローズ: mandatory ルールに fail_on の verdict が
1 つでも残れば失敗。実行状態（exec_state）とは分離する。
"""

from __future__ import annotations

from dataclasses import dataclass, field

from speclino.domain.finding import Finding, Findings, Verdict
from speclino.domain.run import GateStatus
from speclino.domain.spec_ir import RuleLevel

DEFAULT_FAIL_ON: tuple[Verdict, ...] = ("VIOLATED", "UNKNOWN", "REVIEW")


@dataclass(frozen=True)
class GatePolicy:
    fail_on: tuple[Verdict, ...] = DEFAULT_FAIL_ON
    mode: str = "blocking"  # advisory のときは結果を残すが失敗にしない（CLI 既定）


DEFAULT_POLICY = GatePolicy()


@dataclass(frozen=True)
class GateResult:
    status: GateStatus
    blocking: list[Finding] = field(default_factory=list)  # 失敗の原因になった Finding
    advisory: list[Finding] = field(default_factory=list)  # 助言ルールの非成立
    waived: list[Finding] = field(default_factory=list)  # 有効な例外で免除された Finding
    reason: str = ""


def aggregate_gate(
    findings: Findings,
    levels: dict[str, RuleLevel],
    policy: GatePolicy = DEFAULT_POLICY,
    waived_ids: frozenset[str] = frozenset(),
) -> GateResult:
    """levels は requirement_id → mandatory|advisory。無いものは mandatory とみなす（安全側）。"""
    if not findings.findings:
        return GateResult("fail", reason="必須の検証集合が空。空の検証集合を通過として扱わない")
    blocking: list[Finding] = []
    advisory: list[Finding] = []
    waived: list[Finding] = []
    has_mandatory = False
    for f in findings.findings:
        level = levels.get(f.requirement_id, "mandatory")
        if level == "mandatory":
            has_mandatory = True
        if f.verdict not in policy.fail_on:
            continue
        if f.id in waived_ids and f.verdict == "VIOLATED":
            waived.append(f)
        elif level == "mandatory":
            blocking.append(f)
        else:
            advisory.append(f)
    if not has_mandatory:
        return GateResult("fail", advisory=advisory, reason="必須ルールが 1 つも無い")
    if blocking:
        if policy.mode == "advisory":
            return GateResult(
                "fail",
                blocking=blocking,
                advisory=advisory,
                waived=waived,
                reason=f"advisory モード: 必須ルール {len(blocking)} 件が非成立（警告のみ）",
            )
        counts = {v: sum(1 for f in blocking if f.verdict == v) for v in policy.fail_on}
        desc = ", ".join(f"{v} {n}" for v, n in counts.items() if n)
        return GateResult(
            "fail",
            blocking=blocking,
            advisory=advisory,
            waived=waived,
            reason=f"必須ルールが非成立: {desc}",
        )
    status: GateStatus = "pass_with_exceptions" if waived else "pass"
    return GateResult(
        status,
        advisory=advisory,
        waived=waived,
        reason="必須ルールはすべて成立" + ("（例外による免除あり）" if waived else ""),
    )
