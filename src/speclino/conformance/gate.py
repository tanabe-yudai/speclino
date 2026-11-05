"""gate（ハッカソン計画「判定モデル」）。Findings と fail_on 方針からゲート結果を決める。

verdict は正直に 4 値を保ち、ゲートはフェイルクローズ: mandatory ルールに fail_on の verdict が
1 つでも残れば失敗。実行状態（exec_state）とは分離する。

仕分け（`triage`: ループ）と決定（`decide_gate`: 分岐だけ）を分けている。`decide_gate` は
SpecLino 自身の解析対象（R-GATE-01〜03）なので対応構文だけで書く。
"""

from __future__ import annotations

from dataclasses import dataclass, field, replace

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
    # 阻止しない REVIEW（S70: 人が attested で確定した指摘）。verdict は REVIEW のまま
    cleared: list[Finding] = field(default_factory=list)
    # cleared のうち PR の作成者が自分で確定したもの（S70。拒否はせず印を残す）
    self_attested_ids: list[str] = field(default_factory=list)
    # cleared のうち作成者との照合ができなかった確定によるもの（黙って「自己確定でない」にしない）
    self_attest_unverified_ids: list[str] = field(default_factory=list)


@dataclass(frozen=True)
class Triage:
    """Findings を level と fail_on で仕分けた結果。`decide_gate` はこれだけを見る。"""

    has_findings: bool
    has_mandatory: bool
    blocking: list[Finding]
    advisory: list[Finding]
    waived: list[Finding]
    cleared: list[Finding] = field(default_factory=list)

    @property
    def has_blocking(self) -> bool:
        return bool(self.blocking)

    @property
    def has_waived(self) -> bool:
        return bool(self.waived)


def triage(
    findings: Findings,
    levels: dict[str, RuleLevel],
    policy: GatePolicy = DEFAULT_POLICY,
    waived_ids: frozenset[str] = frozenset(),
    cleared_ids: frozenset[str] = frozenset(),
) -> Triage:
    """levels は requirement_id → mandatory|advisory。無いものは mandatory とみなす（安全側）。

    `cleared_ids` は阻止しない REVIEW の ID（S70）。REVIEW 以外の verdict には効かない
    （UNKNOWN・VIOLATED を人の確定で通すことはできない）。
    """
    blocking: list[Finding] = []
    advisory: list[Finding] = []
    waived: list[Finding] = []
    cleared: list[Finding] = []
    has_mandatory = False
    for f in findings.findings:
        level = levels.get(f.requirement_id, "mandatory")
        if level == "mandatory":
            has_mandatory = True
        if f.verdict not in policy.fail_on:
            continue
        if f.id in waived_ids and f.verdict == "VIOLATED":
            waived.append(f)
        elif f.id in cleared_ids and f.verdict == "REVIEW":
            cleared.append(f)
        elif level == "mandatory":
            blocking.append(f)
        else:
            advisory.append(f)
    return Triage(bool(findings.findings), has_mandatory, blocking, advisory, waived, cleared)


def decide_gate(t: Triage, policy: GatePolicy) -> GateResult:
    """R-GATE-01: 検証集合が空か必須ルールが無ければ通過を返さない。R-GATE-02: 必須ルールの
    非成立が残れば失敗。R-GATE-03: それ以外は通過（免除があれば例外付き通過）。"""
    if not t.has_findings:
        return _fail(t, "必須の検証集合が空。空の検証集合を通過として扱わない")
    if not t.has_mandatory:
        # 助言ルールしか出ていない結果まで落とすのは厳しすぎるので通す
        return _pass(t)
    if t.has_blocking:
        return _fail(t, _blocking_reason(t, policy))
    if t.has_waived:
        return _pass_with_exceptions(t)
    return _pass(t)


def aggregate_gate(
    findings: Findings,
    levels: dict[str, RuleLevel],
    policy: GatePolicy = DEFAULT_POLICY,
    waived_ids: frozenset[str] = frozenset(),
    cleared_ids: frozenset[str] = frozenset(),
) -> GateResult:
    t = triage(findings, levels, policy, waived_ids, cleared_ids)
    return replace(decide_gate(t, policy), cleared=t.cleared)


# ------------------------------------------------------------------ outcomes
# 決定の各出口。bindings.yaml で操作（gate_fail / gate_pass / gate_pass_waived）に束縛する


def _fail(t: Triage, reason: str) -> GateResult:
    return GateResult("fail", t.blocking, t.advisory, t.waived, reason)


def _pass(t: Triage) -> GateResult:
    return GateResult("pass", [], t.advisory, t.waived, "必須ルールはすべて成立")


def _pass_with_exceptions(t: Triage) -> GateResult:
    return GateResult(
        "pass_with_exceptions",
        [],
        t.advisory,
        t.waived,
        "必須ルールはすべて成立（例外による免除あり）",
    )


def _blocking_reason(t: Triage, policy: GatePolicy) -> str:
    if policy.mode == "advisory":
        return f"advisory モード: 必須ルール {len(t.blocking)} 件が非成立（警告のみ）"
    counts = {v: sum(1 for f in t.blocking if f.verdict == v) for v in policy.fail_on}
    desc = ", ".join(f"{v} {n}" for v, n in counts.items() if n)
    return f"必須ルールが非成立: {desc}"


__all__ = [
    "DEFAULT_FAIL_ON",
    "DEFAULT_POLICY",
    "GatePolicy",
    "GateResult",
    "Triage",
    "aggregate_gate",
    "decide_gate",
    "triage",
]
