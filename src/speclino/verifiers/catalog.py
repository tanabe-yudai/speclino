"""規約の検査器の一覧（#114）。実行する登録（`policy_registry`）と承認時の検証（`KNOWN_VERIFIERS`）の
唯一の出所。

検査器を足すときはここに 1 行足すだけでよい。承認で受け付ける (ID, 版) は登録した検査器の属性から
作るので、「承認できるのに実行できない」「実行できるのに承認できない」検査器は生じない。
"""

from __future__ import annotations

from collections.abc import Mapping

from speclino.domain.finding import Finding
from speclino.verifiers.api_conventions import ApiConventionsVerifier
from speclino.verifiers.api_error_format import ApiErrorFormatVerifier
from speclino.verifiers.base import Verifier
from speclino.verifiers.ci_required import CiRequiredVerifier
from speclino.verifiers.error_message_format import ErrorMessageFormatVerifier
from speclino.verifiers.lint_preset import LintPresetVerifier
from speclino.verifiers.llm_checklist import LlmChecklistVerifier
from speclino.verifiers.make_targets import MakeTargetsVerifier
from speclino.verifiers.naming_identifiers import NamingIdentifiersVerifier
from speclino.verifiers.registry import VerifierRegistry
from speclino.verifiers.spec_format import SpecFormatVerifier
from speclino.verifiers.spec_tests import SpecTestsVerifier


def _verifiers(llm_findings: Mapping[str, list[Finding]] | None) -> list[Verifier]:
    return [
        MakeTargetsVerifier(),
        SpecFormatVerifier(),
        SpecTestsVerifier(),
        CiRequiredVerifier(),
        ApiErrorFormatVerifier(),
        ApiConventionsVerifier(),
        NamingIdentifiersVerifier(),
        ErrorMessageFormatVerifier(),
        LintPresetVerifier(),
        # S70: LLM 支援は worker が先に評価した結果を返すだけ（ここでは LLM を呼ばない）
        LlmChecklistVerifier(llm_findings),
    ]


def policy_registry(llm_findings: Mapping[str, list[Finding]] | None = None) -> VerifierRegistry:
    """run で規約を実行する登録。"""
    registry = VerifierRegistry()
    for verifier in _verifiers(llm_findings):
        registry.register(verifier)
    return registry


# 承認・画面で受け付ける検査器 ID → 版（実行の登録と同じ一覧から作る）
KNOWN_VERIFIERS: dict[str, str] = {v.id: v.version for v in _verifiers(None)}


__all__ = ["KNOWN_VERIFIERS", "policy_registry"]
