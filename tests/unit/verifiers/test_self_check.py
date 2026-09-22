"""このリポジトリ自身の .speclino/policy/speclino-core.yaml を実データとして検証する。

Issue #11 の完了条件: 「このリポジトリ自身の Makefile と specs/ が適合する」。
"""

from __future__ import annotations

import yaml

from speclino.domain.policy import PolicyExceptions, PolicyPackage
from speclino.verifiers.make_targets import MakeTargetsVerifier
from speclino.verifiers.registry import VerifierRegistry
from speclino.verifiers.snapshot import DirectorySnapshot
from speclino.verifiers.spec_format import SpecFormatVerifier

from .conftest import REPO_ROOT


def _load_package() -> PolicyPackage:
    raw = yaml.safe_load((REPO_ROOT / ".speclino" / "policy" / "speclino-core.yaml").read_text())
    return PolicyPackage.model_validate(raw)


def test_speclino_core_package_is_valid() -> None:
    package = _load_package()
    assert package.id == "speclino-core"
    assert {rule.id for rule in package.rules} == {"MAKE-001", "SPEC-FMT-001"}


def test_this_repository_conforms_to_speclino_core() -> None:
    package = _load_package()
    registry = VerifierRegistry()
    registry.register(MakeTargetsVerifier())
    registry.register(SpecFormatVerifier())
    snapshot = DirectorySnapshot(root=REPO_ROOT)
    exceptions = PolicyExceptions(schema_version="0.1", exceptions=[])

    findings = registry.run_package(package, exceptions, snapshot)

    by_requirement = {f.requirement_id: f for f in findings}
    assert by_requirement["MAKE-001"].verdict == "VERIFIED_IN_SCOPE", by_requirement[
        "MAKE-001"
    ].message
    spec_finding = by_requirement["SPEC-FMT-001:specs/_template.md"]
    assert spec_finding.verdict == "VERIFIED_IN_SCOPE", spec_finding.message
    assert all(f.verdict != "VIOLATED" for f in findings)
