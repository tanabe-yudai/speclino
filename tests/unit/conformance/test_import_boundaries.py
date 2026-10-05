"""conformance / analysis / spec.uml は LLM・adapters・agent に依存しない（構成規約）。"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

SRC = Path(__file__).resolve().parents[3] / "src" / "speclino"
FORBIDDEN_PREFIXES = (
    "google",
    "speclino.agent",
    "speclino.adapters",
    "speclino.api",
    "speclino.db",
    "speclino.spec.spec_extractor",  # LLM の抽出器
)


def _imports(path: Path) -> set[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"))
    names: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            names.update(a.name for a in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            names.add(node.module)
    return names


@pytest.mark.parametrize("package", ["conformance", "analysis", "domain", "spec/uml"])
def test_pure_packages_do_not_import_llm_or_adapters(package: str) -> None:
    for py in (SRC / package).rglob("*.py"):
        bad = {n for n in _imports(py) if n.startswith(FORBIDDEN_PREFIXES)}
        assert not bad, f"{py.relative_to(SRC)} が禁止 import を含む: {sorted(bad)}"


# S70: LLM 支援の規約は services/llm_policies.py だけが LLM を呼ぶ。判定の層（検査器を含む）と
# 単位の取り出し（services/llm_units.py）は LLM のポート・評価のサービスを import しない
LLM_PREFIXES = (
    "google",
    "speclino.ports.text_llm",
    "speclino.services.llm_policies",
    "speclino.adapters.llm",
)


@pytest.mark.parametrize("package", ["conformance", "analysis", "spec/uml", "verifiers"])
def test_judging_layers_do_not_call_llm(package: str) -> None:
    for py in (SRC / package).rglob("*.py"):
        bad = {n for n in _imports(py) if n.startswith(LLM_PREFIXES)}
        assert not bad, f"{py.relative_to(SRC)} が LLM を import している: {sorted(bad)}"


def test_unit_extraction_does_not_call_llm() -> None:
    bad = {n for n in _imports(SRC / "services" / "llm_units.py") if n.startswith(LLM_PREFIXES)}
    assert not bad, sorted(bad)
