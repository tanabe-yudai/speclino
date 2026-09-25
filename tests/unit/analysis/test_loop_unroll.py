"""for の構文拡張（S41b）: 定数回の反復を純粋な AST 書き換えで展開する。

解析器に新しい意味論を足さない（展開後は既存の if / Assign / Call 処理がそのまま扱う）ことを、
定数タプル・range(定数)・break がある場合・上限超過・ネスト・orelse の各パターンで確認する。
"""

from __future__ import annotations

import time
from pathlib import Path

from speclino.analysis.path_analyzer import UNROLL_MAX_ITERATIONS
from tests.unit.analysis.test_path_analyzer import CORRECT_SERVICE, analyze, verdicts


def test_tuple_literal_is_unrolled_and_stays_conformant(tmp_path: Path) -> None:
    """要素数 1 の定数タプルの for は 1 回の呼出しに展開され、経路は exact_in_model のまま。"""
    mutant = CORRECT_SERVICE.replace(
        "    inventory.restore(order.items)\n",
        "    for item in (order.items,):\n        inventory.restore(item)\n",
    )
    code = analyze(tmp_path, mutant)
    success = code.paths[2]
    assert success.precision == "exact_in_model"
    assert [e.operation_or_target for e in success.events] == [
        "order.status",
        "inventory_restore_command",
        "audit_record_command",
    ]
    assert success.events[1].arguments["items"].model_dump() == {
        "op": "ref",
        "path": "before.order.items",
    }
    assert verdicts(code)["R-CANCEL-04"] == ("VERIFIED_IN_SCOPE", "conformant")


def test_range_constant_is_unrolled_twice_and_violates_cardinality(tmp_path: Path) -> None:
    """range(定数) の for は要素数ぶん呼出しを複製する。2 回呼べば cardinality_mismatch。"""
    mutant = CORRECT_SERVICE.replace(
        "    inventory.restore(order.items)\n",
        "    for _ in range(2):\n        inventory.restore(order.items)\n",
    )
    code = analyze(tmp_path, mutant)
    success = code.paths[2]
    assert success.precision == "exact_in_model"
    assert [e.operation_or_target for e in success.events] == [
        "order.status",
        "inventory_restore_command",
        "inventory_restore_command",
        "audit_record_command",
    ]
    assert verdicts(code)["R-CANCEL-04"] == ("VIOLATED", "cardinality_mismatch")


def test_range_with_start_and_stop_is_unrolled(tmp_path: Path) -> None:
    """range(a, b) の 2 引数形も対応する。range(1, 2) は 1 回だけ呼ぶ。"""
    mutant = CORRECT_SERVICE.replace(
        "    inventory.restore(order.items)\n",
        "    for _ in range(1, 2):\n        inventory.restore(order.items)\n",
    )
    code = analyze(tmp_path, mutant)
    success = code.paths[2]
    assert success.precision == "exact_in_model"
    assert verdicts(code)["R-CANCEL-04"] == ("VERIFIED_IN_SCOPE", "conformant")


def test_break_prevents_unroll_and_notes_reason(tmp_path: Path) -> None:
    """本体に break があれば展開せず、従来どおり構造解析による不完全な経路として扱う。"""
    mutant = CORRECT_SERVICE.replace(
        "    inventory.restore(order.items)\n",
        "    for item in (order.items,):\n"
        "        if item is None:\n"
        "            break\n"
        "        inventory.restore(item)\n",
    )
    code = analyze(tmp_path, mutant)
    success = code.paths[2]
    assert success.precision == "incomplete"
    [cause] = success.unknown_causes
    assert cause.code == "unsupported_syntax"
    assert "break" in cause.detail
    assert verdicts(code)["R-CANCEL-04"] == ("UNKNOWN", "unsupported_analysis")


def test_exceeding_unroll_limit_prevents_unroll(tmp_path: Path) -> None:
    """要素数が展開上限を超える場合は展開せず、理由を UnknownCause.detail に残す。"""
    too_many = UNROLL_MAX_ITERATIONS + 1
    elements = ", ".join(["0"] * too_many)
    mutant = CORRECT_SERVICE.replace(
        "    audit.record(user.id, AuditAction.CANCEL_ORDER)\n",
        f"    for _ in ({elements},):\n        audit.record(user.id, AuditAction.CANCEL_ORDER)\n",
    )
    code = analyze(tmp_path, mutant)
    success = code.paths[2]
    assert success.precision == "incomplete"
    [cause] = success.unknown_causes
    assert cause.code == "unsupported_syntax"
    assert "上限" in cause.detail
    v = verdicts(code)
    assert v["R-CANCEL-05"][0] == "UNKNOWN"
    # 展開できない領域は在庫復元・状態更新に触れないので、それらは判定できたまま
    assert v["R-CANCEL-04"] == ("VERIFIED_IN_SCOPE", "conformant")


def test_nested_constant_loops_multiply_call_count(tmp_path: Path) -> None:
    """ネストした for も再帰的に展開され、呼出し回数は要素数の積になる。"""
    mutant = CORRECT_SERVICE.replace(
        "    audit.record(user.id, AuditAction.CANCEL_ORDER)\n",
        "    for _ in range(2):\n"
        "        for _ in range(2):\n"
        "            audit.record(user.id, AuditAction.CANCEL_ORDER)\n",
    )
    code = analyze(tmp_path, mutant)
    success = code.paths[2]
    assert success.precision == "exact_in_model"
    assert [e.operation_or_target for e in success.events].count("audit_record_command") == 4
    assert verdicts(code)["R-CANCEL-05"] == ("VIOLATED", "cardinality_mismatch")


def test_orelse_is_appended_after_the_unrolled_body(tmp_path: Path) -> None:
    """break が無い for の orelse は必ず実行されるので、展開後の末尾にそのまま付く。"""
    mutant = CORRECT_SERVICE.replace(
        "    inventory.restore(order.items)\n",
        "    for _ in range(1):\n        pass\n    else:\n        inventory.restore(order.items)\n",
    )
    code = analyze(tmp_path, mutant)
    success = code.paths[2]
    assert success.precision == "exact_in_model"
    assert [e.operation_or_target for e in success.events] == [
        "order.status",
        "inventory_restore_command",
        "audit_record_command",
    ]
    assert verdicts(code)["R-CANCEL-04"] == ("VERIFIED_IN_SCOPE", "conformant")


def test_tuple_unpacking_target_is_not_unrolled(tmp_path: Path) -> None:
    """target がタプル分解の for は対象外（従来どおり unsupported_syntax）。"""
    mutant = CORRECT_SERVICE.replace(
        "    inventory.restore(order.items)\n",
        "    for a, b in ((1, 2),):\n        inventory.restore(order.items)\n",
    )
    code = analyze(tmp_path, mutant)
    success = code.paths[2]
    assert success.precision == "incomplete"
    assert success.unknown_causes[0].code == "unsupported_syntax"
    assert "タプル分解" in success.unknown_causes[0].detail


def test_while_is_still_unsupported_with_reason(tmp_path: Path) -> None:
    """while は定数回反復として展開できないので、従来どおり対応外だが理由を detail に残す。"""
    mutant = CORRECT_SERVICE.replace(
        "    inventory.restore(order.items)\n",
        "    n = 1\n    while n > 0:\n        inventory.restore(order.items)\n        n -= 1\n",
    )
    code = analyze(tmp_path, mutant)
    success = code.paths[2]
    assert success.precision == "incomplete"
    assert success.unknown_causes[0].code == "unsupported_syntax"
    assert "展開できない" in success.unknown_causes[0].detail


def test_independent_branches_do_not_share_unroll_budget(tmp_path: Path) -> None:
    """展開予算は経路（Draft）ごとに独立して数える。if の別々の枝にある for は互いの予算を
    奪わないので、それぞれ単独では上限内の展開が両方とも成立する。
    """
    mutant = CORRECT_SERVICE.replace(
        "    audit.record(user.id, AuditAction.CANCEL_ORDER)\n",
        "    if user.is_admin:\n"
        "        for _ in range(5):\n"
        "            audit.record(user.id, AuditAction.CANCEL_ORDER)\n"
        "    else:\n"
        "        for _ in range(5):\n"
        "            audit.record(user.id, AuditAction.CANCEL_ORDER)\n",
    )
    code = analyze(tmp_path, mutant)
    success_paths = [p for p in code.paths if p.outcome.status == 204]
    assert len(success_paths) == 2
    assert all(p.precision == "exact_in_model" for p in success_paths), [
        (p.path_id, [u.detail for u in p.unknown_causes]) for p in success_paths
    ]
    assert all(
        [e.operation_or_target for e in p.events].count("audit_record_command") == 5
        for p in success_paths
    )


def test_range_with_huge_magnitude_is_rejected_without_materializing_it(tmp_path: Path) -> None:
    """range(定数) の要素数が展開上限を超えるなら、ast.Constant の列を作る前に断念する。

    要素を作ってから上限と比べていると、range(10**9) のような巨大な定数引数で解析器が
    ハングしかねない（S41b の /simplify レビューで指摘）。ここでは解析が数秒で終わることで、
    要素を実際には作っていないことを確認する。
    """
    mutant = CORRECT_SERVICE.replace(
        "    inventory.restore(order.items)\n",
        "    for _ in range(1_000_000_000):\n        inventory.restore(order.items)\n",
    )
    started = time.monotonic()
    code = analyze(tmp_path, mutant)
    assert time.monotonic() - started < 5.0
    success = code.paths[2]
    assert success.precision == "incomplete"
    assert "上限" in success.unknown_causes[0].detail
