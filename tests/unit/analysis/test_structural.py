"""構造解析（S29）: 対応外の領域が触れない観測は、不完全な経路でも判定できる。"""

from __future__ import annotations

from pathlib import Path

from tests.unit.analysis.test_path_analyzer import CORRECT_SERVICE, analyze, verdicts

LOOP_RESTORE = CORRECT_SERVICE.replace(
    "    inventory.restore(order.items)\n",
    "    for item in order.items:\n        inventory.restore(item)\n",
)


def test_loop_touching_only_restore_leaves_other_rules_decidable(tmp_path: Path) -> None:
    """Golden 18: ループは在庫復元だけを呼ぶので、状態・監査・応答の要求は判定できる。"""
    code = analyze(tmp_path, LOOP_RESTORE)
    success = next(p for p in code.paths if p.outcome.status == 204)
    assert success.precision == "incomplete"
    [cause] = success.unknown_causes
    assert cause.code == "unsupported_syntax"
    assert cause.may_affect == ["call:inventory_restore_command"]
    # ループより後の監査呼出しと return も観測されている
    assert [e.operation_or_target for e in success.events] == [
        "order.status",
        "audit_record_command",
    ]
    v = verdicts(code)
    assert v["R-CANCEL-04"] == ("UNKNOWN", "unsupported_analysis")
    for rid in ("R-CANCEL-03", "R-CANCEL-05", "R-CANCEL-06"):
        assert v[rid] == ("VERIFIED_IN_SCOPE", "conformant"), (rid, v[rid])


def test_loop_with_return_inside_ends_the_path(tmp_path: Path) -> None:
    """領域内に return / raise があれば終了結果が領域内で決まり得るので従来どおり打ち切る。"""
    src = CORRECT_SERVICE.replace(
        "    inventory.restore(order.items)\n",
        "    for item in order.items:\n"
        "        if item is None:\n"
        "            return Response(status_code=500)\n"
        "        inventory.restore(item)\n",
    )
    code = analyze(tmp_path, src)
    success = next(p for p in code.paths if p.outcome.kind == "unresolved")
    assert success.unknown_causes[0].may_affect is None
    v = verdicts(code)
    assert all(v[r][0] == "UNKNOWN" for r in ("R-CANCEL-03", "R-CANCEL-04", "R-CANCEL-05"))


def test_loop_writing_bound_state_ends_the_path(tmp_path: Path) -> None:
    """領域が束縛変数を書くと以後の値が追えないので打ち切る（健全側）。"""
    src = CORRECT_SERVICE.replace(
        "    inventory.restore(order.items)\n",
        "    for item in order.items:\n        order.status = OrderStatus.SHIPPED\n",
    )
    code = analyze(tmp_path, src)
    assert any(p.outcome.kind == "unresolved" for p in code.paths)
    assert verdicts(code)["R-CANCEL-03"][0] == "UNKNOWN"


def test_loop_with_unresolved_call_ends_the_path(tmp_path: Path) -> None:
    src = CORRECT_SERVICE.replace(
        "    inventory.restore(order.items)\n",
        "    for item in order.items:\n        item.notify()\n",
    )
    code = analyze(tmp_path, src)
    assert any(p.outcome.kind == "unresolved" for p in code.paths)


def test_loop_calling_helper_that_calls_operation_is_traced(tmp_path: Path) -> None:
    """領域内の自リポジトリ関数は深さ上限まで辿り、その中の操作呼出しを観測に含める。"""
    src = (
        CORRECT_SERVICE.replace(
            "    inventory.restore(order.items)\n",
            "    for item in order.items:\n        _restore_one(item)\n",
        )
        + "\n\ndef _restore_one(item):\n    inventory.restore(item)\n"
    )
    code = analyze(tmp_path, src)
    success = next(p for p in code.paths if p.outcome.status == 204)
    assert success.unknown_causes[0].may_affect == ["call:inventory_restore_command"]
    v = verdicts(code)
    assert v["R-CANCEL-04"][0] == "UNKNOWN" and v["R-CANCEL-05"][0] == "VERIFIED_IN_SCOPE"


def test_locals_assigned_in_region_become_unresolved(tmp_path: Path) -> None:
    """領域内で代入されたローカルを後で条件に使うと、その時点で打ち切る。"""
    src = CORRECT_SERVICE.replace(
        "    inventory.restore(order.items)\n",
        "    flag = True\n"
        "    for item in order.items:\n"
        "        flag = False\n"
        "    if flag:\n"
        "        inventory.restore(order.items)\n",
    )
    code = analyze(tmp_path, src)
    success = [p for p in code.paths if p.guard is not None and p.outcome.kind == "unresolved"]
    assert success, [(p.path_id, p.outcome.kind) for p in code.paths]
