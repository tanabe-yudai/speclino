"""S72: ショップ題材（examples/shop）。仕様が誤りなく変換でき、各変異が意図した判定を 1 つだけ出す。

DB は使わない（bundle は手で組む）。DB と worker を通す版は
tests/integration/services/test_shop_bundle.py。
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from examples.shop.materialize import DOMAIN, SPEC, materialize, mutation_names
from speclino.services.check_pipeline import run_check
from speclino.spec import load_domain
from speclino.spec.uml import compile_spec
from tests.unit.services.test_check_pipeline_bundle import knowledge, request, spec_revision

PLACE = ("place#OrderService.place_order", "OrderService.place_order")
CANCEL = ("cancel#OrderService.cancel_order", "OrderService.cancel_order")
STATUS_PLACE = ("status", "OrderService.place_order")
STATUS_SHIP = ("status", "OrderService.ship_order")
BINDINGS = ("KNOWLEDGE-BINDINGS-001", "knowledge:config")

# 変異 → main との違い（(要求 ID, 振る舞い) → 判定）。ここに無い Finding は main と同じ判定
EXPECTED_CHANGES: dict[str, dict[tuple[str, str], str]] = {
    "01-missing-charge": {PLACE: "VIOLATED"},
    "02-ship-from-pending": {STATUS_SHIP: "VIOLATED"},
    "03-refund-on-cancel": {CANCEL: "VIOLATED"},
    "04-notify-between": {},  # シーケンス図は open-world（図に無い呼出しを挟んでよい）
    # 動的呼出しは解析しきれない。同じ経路を使う 2 つの義務が UNKNOWN（偽の成立にしない）
    "05-dynamic-charge": {PLACE: "UNKNOWN", STATUS_PLACE: "UNKNOWN"},
    "06-rebind-charge": {BINDINGS: "REVIEW"},
    # LLM 支援の規約だけが見る違い（DB を使う版で REVIEW を確かめる）。決定的な検査は変わらない
    "07-terse-error": {},
}

MAIN_EXPECTED = {
    ("vocab#OrderService", "vocab"): "VERIFIED_IN_SCOPE",
    ("vocab#Inventory", "vocab"): "VERIFIED_IN_SCOPE",
    ("vocab#Payment", "vocab"): "VERIFIED_IN_SCOPE",
    ("vocab#Mailer", "vocab"): "VERIFIED_IN_SCOPE",
    PLACE: "VERIFIED_IN_SCOPE",
    CANCEL: "VERIFIED_IN_SCOPE",
    STATUS_PLACE: "VERIFIED_IN_SCOPE",
    STATUS_SHIP: "VERIFIED_IN_SCOPE",
    ("status", "OrderService.cancel_order"): "VERIFIED_IN_SCOPE",
}


def verdicts(findings: list[Any]) -> dict[tuple[str, str], str]:
    return {(f.requirement_id, f.behavior_id): f.verdict for f in findings}


def changes(
    main: dict[tuple[str, str], str], variant: dict[tuple[str, str], str]
) -> dict[tuple[str, str], str]:
    return {k: v for k, v in variant.items() if main.get(k) != v}


def test_spec_compiles_without_diagnostics() -> None:
    result = compile_spec(SPEC.read_text(), domain=load_domain(DOMAIN), legacy_ir=[])
    assert result.diagnostics == []
    assert result.compiled is not None
    ids = [r.id for r in result.compiled.requirements]
    assert ids == ["R-SHOP-01", "R-SHOP-02", "R-SHOP-03", "R-SHOP-04", "R-SHOP-05"]
    assert [r.id for r in result.compiled.requirements if not r.formalized_by] == ["R-SHOP-05"]


def test_every_mutation_is_listed() -> None:
    assert mutation_names() == sorted(EXPECTED_CHANGES)


@pytest.fixture(scope="module")
def shop(tmp_path_factory: pytest.TempPathFactory) -> tuple[Path, Any]:
    root = tmp_path_factory.mktemp("shop")
    rev = spec_revision("shop", SPEC.read_text(), domain=load_domain(DOMAIN))
    return materialize(root / "main"), knowledge(rev)


def _run(shop: tuple[Path, Any], root: Path) -> dict[tuple[str, str], str]:
    default, k = shop
    res = run_check(request(root, k, trusted_root=default, github_repo_id=None))
    return {
        key: v
        for key, v in verdicts(res.findings.findings).items()
        if key[0] != "KNOWLEDGE-LOCK-001"
    }


def test_main_conforms(shop: tuple[Path, Any]) -> None:
    assert _run(shop, shop[0]) == MAIN_EXPECTED


@pytest.mark.parametrize("mutation", sorted(EXPECTED_CHANGES))
def test_mutation_has_exactly_the_intended_verdict(
    shop: tuple[Path, Any], tmp_path: Path, mutation: str
) -> None:
    got = _run(shop, materialize(tmp_path / "repo", mutation))
    assert changes(MAIN_EXPECTED, got) == EXPECTED_CHANGES[mutation]


def test_missing_call_has_a_counterexample(shop: tuple[Path, Any], tmp_path: Path) -> None:
    default, k = shop
    root = materialize(tmp_path / "repo", "01-missing-charge")
    res = run_check(request(root, k, trusted_root=default, github_repo_id=None))
    (f,) = [x for x in res.findings.findings if x.requirement_id == PLACE[0]]
    assert f.counterexample is not None
    assert "Payment.charge" in f.message
