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
