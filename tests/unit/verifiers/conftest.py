"""tests/unit/verifiers 共通のフィクスチャ。"""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path
from typing import Any

import pytest

from speclino.domain.policy import (
    PolicyApplicability,
    PolicyExceptions,
    PolicyPackage,
    PolicyRule,
    PolicyVerifierRef,
)

REPO_ROOT = Path(__file__).resolve().parents[3]

MakeRule = Callable[..., PolicyRule]
MakePackage = Callable[[PolicyRule], PolicyPackage]
WriteFiles = Callable[[Path, dict[str, str]], None]


@pytest.fixture
def make_rule() -> MakeRule:
    def _make_rule(
        *,
        id: str,
        verifier_id: str,
        paths: list[str],
        params: dict[str, Any],
        on_missing_input: str = "violation",
        level: str = "mandatory",
    ) -> PolicyRule:
        return PolicyRule(
            id=id,
            version="1.0.0",
            level=level,  # type: ignore[arg-type]
            title=f"title-{id}",
            text=f"text-{id}",
            applies_to=PolicyApplicability(paths=paths, languages=[]),
            verifier=PolicyVerifierRef(id=verifier_id, version="1.0.0", params=params),
            on_missing_input=on_missing_input,  # type: ignore[arg-type]
            exceptions_allowed=True,
        )

    return _make_rule


@pytest.fixture
def empty_exceptions() -> PolicyExceptions:
    return PolicyExceptions(schema_version="0.1", exceptions=[])


@pytest.fixture
def make_package() -> MakePackage:
    def _make_package(rule: PolicyRule) -> PolicyPackage:
        return PolicyPackage(
            schema_version="0.1",
            id="pkg",
            version="1.0.0",
            status="active",
            owner="x",
            rules=[rule],
        )

    return _make_package


@pytest.fixture
def write_files() -> WriteFiles:
    def _write_files(root: Path, files: dict[str, str]) -> None:
        for rel_path, content in files.items():
            target = root / rel_path
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(content, encoding="utf-8")

    return _write_files
