"""規約パッケージの実行: 検証器の登録と `applies_to` / `on_missing_input` の解決。

例外（PolicyException）の適用はまだ行わない（S30 で実装）。`exceptions` はシグネチャに
残すが、ここでは参照しない。
"""

from __future__ import annotations

from speclino.domain.finding import Finding
from speclino.domain.policy import PolicyExceptions, PolicyPackage, PolicyRule
from speclino.verifiers._findings import missing_input_outcome
from speclino.verifiers.base import Snapshot, Verifier


def _has_any_match(paths: list[str], snapshot: Snapshot) -> bool:
    return any(snapshot.glob(pattern) for pattern in paths)


def _missing_input_finding(rule: PolicyRule, snapshot: Snapshot) -> Finding | None:
    outcome = missing_input_outcome(rule)
    if outcome is None:
        return None
    verdict, category = outcome
    patterns = ", ".join(rule.applies_to.paths) or "(未指定)"
    return Finding(
        id=f"policy:{rule.id}:missing_input",
        requirement_id=rule.id,
        behavior_id=f"policy:{rule.verifier.id if rule.verifier else rule.id}",
        verdict=verdict,
        category=category,
        severity="high" if rule.level == "mandatory" else "medium",
        message=f"{rule.id}: applies_to のパターン（{patterns}）に一致する対象がありません",
        actual="対象なし",
        claim_scope="persisted_state",
    )


class VerifierRegistry:
    """検証器 ID → Verifier 実装の登録・実行。"""

    def __init__(self) -> None:
        self._verifiers: dict[str, Verifier] = {}

    def register(self, verifier: Verifier) -> None:
        self._verifiers[verifier.id] = verifier

    def get(self, verifier_id: str) -> Verifier | None:
        return self._verifiers.get(verifier_id)

    def run_package(
        self,
        package: PolicyPackage,
        exceptions: PolicyExceptions,
        snapshot: Snapshot,
    ) -> list[Finding]:
        del exceptions  # S30 で適用する。現時点では未使用（型シグネチャのみ確定）。
        findings: list[Finding] = []
        for rule in package.rules:
            findings.extend(self._run_rule(rule, snapshot))
        return findings

    def _run_rule(self, rule: PolicyRule, snapshot: Snapshot) -> list[Finding]:
        if rule.verifier is None:
            # 機械検証されていない助言ルール（S42）。判定を作らない。
            # 「人が見て判断する」を Finding にすると、根拠の無い verdict を足すことになる。
            # UI は改訂のデータからこのルールを「未検証」として表示する。
            return []
        if not _has_any_match(rule.applies_to.paths, snapshot):
            missing = _missing_input_finding(rule, snapshot)
            return [missing] if missing is not None else []

        verifier = self._verifiers.get(rule.verifier.id)
        if verifier is None:
            raise LookupError(f"未登録の検証器: {rule.verifier.id}")
        return verifier.run(rule, snapshot)
