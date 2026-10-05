"""このリポジトリ自身の .speclino/policy/speclino-core.yaml を実データとして検証する。

Issue #11 の完了条件: 「このリポジトリ自身の Makefile と specs/ が適合する」。
"""

from __future__ import annotations

import pytest
import yaml

from speclino.domain.policy import PolicyExceptions, PolicyPackage
from speclino.verifiers.api_error_format import ApiErrorFormatVerifier
from speclino.verifiers.ci_required import CiRequiredVerifier
from speclino.verifiers.make_targets import MakeTargetsVerifier
from speclino.verifiers.registry import VerifierRegistry
from speclino.verifiers.snapshot import DirectorySnapshot
from speclino.verifiers.spec_format import SpecFormatVerifier
from speclino.verifiers.spec_tests import SpecTestsVerifier

from .conftest import REPO_ROOT


def _load_package() -> PolicyPackage:
    raw = yaml.safe_load((REPO_ROOT / ".speclino" / "policy" / "speclino-core.yaml").read_text())
    return PolicyPackage.model_validate(raw)


def test_speclino_core_package_is_valid() -> None:
    package = _load_package()
    assert package.id == "speclino-core"
    assert {rule.id for rule in package.rules} == {
        "MAKE-001",
        "SPEC-FMT-001",
        "SPEC-TEST-001",
        "CI-001",
        "API-ERR-001",
        "ARCH-001",
        "ARCH-002",
        "ARCH-003",
        "ARCH-004",
        "VERDICT-001",
    }


@pytest.mark.selfcheck
def test_this_repository_conforms_to_speclino_core() -> None:
    package = _load_package()
    registry = VerifierRegistry()
    registry.register(MakeTargetsVerifier())
    registry.register(SpecFormatVerifier())
    registry.register(CiRequiredVerifier())
    registry.register(ApiErrorFormatVerifier())
    registry.register(SpecTestsVerifier())
    snapshot = DirectorySnapshot(root=REPO_ROOT)
    exceptions = PolicyExceptions(schema_version="0.1", exceptions=[])

    findings = registry.run_package(package, exceptions, snapshot)

    by_requirement = {f.requirement_id: f for f in findings}
    assert by_requirement["MAKE-001"].verdict == "VERIFIED_IN_SCOPE", by_requirement[
        "MAKE-001"
    ].message
    spec_finding = by_requirement["SPEC-FMT-001:specs/_template.md"]
    assert spec_finding.verdict == "VERIFIED_IN_SCOPE", spec_finding.message
    ci_finding = by_requirement["CI-001:.github/workflows/ci.yml"]
    assert ci_finding.verdict == "VERIFIED_IN_SCOPE", ci_finding.message

    # API-ERR-001 / SPEC-TEST-001 は advisory。前者は既存ルートの HTTPException 直書きが
    # 未置換、後者は R-RUN 系のマーカー（tests/unit/services/test_run_freshness.py）が
    # Spec IR の tests に未宣言のまま残っているため、このリポジトリ自身は現状 VIOLATED を
    # 許容する（design/product-spec-summary.md: 規約違反を自分に対して検出するデモ、S49）。
    # mandatory ルールにだけ「VIOLATED が無いこと」を要求する。
    mandatory_ids = {rule.id for rule in package.rules if rule.level == "mandatory"}
    mandatory_findings = [f for f in findings if f.requirement_id.split(":", 1)[0] in mandatory_ids]
    violated = [f.requirement_id for f in mandatory_findings if f.verdict == "VIOLATED"]
    assert violated == []
