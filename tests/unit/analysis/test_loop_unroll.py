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


# ------------------------------------------------------- codex フルレビュー (#50) の指摘


def test_elements_are_evaluated_before_the_loop_not_after_body_mutation(tmp_path: Path) -> None:
    """Python は反復の全要素をループ開始前に評価する。要素式を退避せず
    ``x=e1;*body;x=e2;*body`` と書き換えると、body が変数を書き換えた後に e2 を評価してしまい
    回数がずれる。ここでは実際の Python は 2 要素とも「ループ開始前の x（0）」に束縛されるので
    x==1 は一度も成立せず、inventory.restore は 0 回しか呼ばれない
    （``if x == 1`` は展開後にそのまま if として解析されるので、この経路自体は 2 通りの分岐
    （if の then/else）×2 回のイテレーションに枝分かれするが、x==1 が真になる枝はどれも
    ガードが充足不能。退避が無いと 2 回目の要素評価が body の ``x = 1`` 更新後になり、
    充足可能な枝で誤って 1 回・VERIFIED_IN_SCOPE になっていた）。
    """
    mutant = CORRECT_SERVICE.replace(
        "    inventory.restore(order.items)\n",
        "    x = 0\n"
        "    for x in (0, x):\n"
        "        if x == 1:\n"
        "            inventory.restore(order.items)\n"
        "        x = 1\n",
    )
    code = analyze(tmp_path, mutant)
    assert all(p.precision == "exact_in_model" for p in code.paths), [
        (p.path_id, [u.detail for u in p.unknown_causes]) for p in code.paths
    ]
    assert verdicts(code)["R-CANCEL-04"] == ("VIOLATED", "missing_effect")


def test_side_effecting_element_call_is_recorded_once_even_if_body_ends_the_path(
    tmp_path: Path,
) -> None:
    """要素式の呼出しは退避の段階で（ループより前に）1 回だけ記録される。body が最初の
    繰り返しで即座に経路を終えても、後続要素の呼出しイベントが取りこぼされない。
    """
    mutant = CORRECT_SERVICE.replace(
        "    inventory.restore(order.items)\n",
        "    for x in (0, inventory.restore(order.items)):\n"
        "        raise HTTPException(status_code=500)\n",
    )
    code = analyze(tmp_path, mutant)
    guarded = code.paths[2]
    assert [e.operation_or_target for e in guarded.events].count("inventory_restore_command") == 1


def test_module_level_range_shadow_prevents_unroll(tmp_path: Path) -> None:
    """対象モジュールの最上位で range を再定義していたら、組込みと確定できないので展開しない。"""
    mutant = (
        CORRECT_SERVICE.replace(
            "    inventory.restore(order.items)\n",
            "    for _ in range(1):\n        inventory.restore(order.items)\n",
        )
        + "\n\ndef range(n):\n    return ()\n"
    )
    code = analyze(tmp_path, mutant)
    success = code.paths[2]
    assert success.precision == "incomplete"
    assert success.unknown_causes[0].code == "unsupported_syntax"
    assert "組込みと確定できない" in success.unknown_causes[0].detail
    assert verdicts(code)["R-CANCEL-04"][0] == "UNKNOWN"


# --------------------------------------------- codex 再レビュー (#50) の指摘（2 回目）


def test_range_shadow_inside_nested_if_prevents_unroll(tmp_path: Path) -> None:
    """モジュール直下の単純な文だけでなく、ネストした if の中の再束縛も見つける
    （if True: の中の range = ... は最上位の文だけを見ていると見落とす）。
    """
    mutant = (
        CORRECT_SERVICE.replace(
            "    inventory.restore(order.items)\n",
            "    for _ in range(1):\n        inventory.restore(order.items)\n",
        )
        + "\nif True:\n    range = lambda n: ()\n"
    )
    code = analyze(tmp_path, mutant)
    success = code.paths[2]
    assert success.precision == "incomplete"
    assert "組込みと確定できない" in success.unknown_causes[0].detail
    assert verdicts(code)["R-CANCEL-04"][0] == "UNKNOWN"


def test_range_shadow_via_tuple_unpacking_prevents_unroll(tmp_path: Path) -> None:
    """タプル分解の代入先に range が含まれていても再束縛とみなす
    （target.id だけを見ていると range, other = ... を見落とす）。
    """
    mutant = (
        CORRECT_SERVICE.replace(
            "    inventory.restore(order.items)\n",
            "    for _ in range(1):\n        inventory.restore(order.items)\n",
        )
        + "\nrange, other = (lambda n: ()), 0\n"
    )
    code = analyze(tmp_path, mutant)
    success = code.paths[2]
    assert success.precision == "incomplete"
    assert "組込みと確定できない" in success.unknown_causes[0].detail


def test_temp_variable_cannot_collide_with_user_local(tmp_path: Path) -> None:
    """退避用の一時変数は Python の識別子として書けない名前にするため、ユーザーコードの
    変数（たまたま以前の実装が使っていた命名パターンと同じ名前でも）と衝突しない。
    衝突すると退避代入がユーザー変数を上書きし、後続で読んだ値が変わってしまう。
    """
    mutant = CORRECT_SERVICE.replace(
        "    inventory.restore(order.items)\n",
        "    _speclino_unroll_0_0 = AuditAction.CANCEL_ORDER\n"
        "    for item in (order.items,):\n"
        "        inventory.restore(item)\n",
    ).replace(
        "    audit.record(user.id, AuditAction.CANCEL_ORDER)\n",
        "    audit.record(user.id, _speclino_unroll_0_0)\n",
    )
    code = analyze(tmp_path, mutant)
    success = code.paths[2]
    assert success.precision == "exact_in_model"
    audit_event = next(e for e in success.events if e.operation == "audit_record_command")
    assert audit_event.arguments["action"].model_dump() == {
        "op": "const",
        "type": "AuditAction",
        "value": "CANCEL_ORDER",
    }


def test_element_call_result_used_in_condition_does_not_crash(tmp_path: Path) -> None:
    """展開後、要素が呼出しの結果をループ変数経由で if の条件に使うと、以前は
    Unresolved.code（"call_result"、UnknownCause.code に無い値）がそのまま _unknown に渡り、
    pydantic の検証で例外になって解析全体が失敗していた。例外にならず、その経路を
    UNKNOWN として打ち切ることを確認する。
    """
    mutant = CORRECT_SERVICE.replace(
        "    inventory.restore(order.items)\n",
        "    for x in (inventory.restore(order.items),):\n        if x:\n            pass\n",
    )
    code = analyze(tmp_path, mutant)  # 例外にならないこと自体がこのテストの主目的
    assert verdicts(code)["R-CANCEL-04"][0] == "UNKNOWN"


def test_call_result_used_in_condition_without_loop_does_not_crash(tmp_path: Path) -> None:
    """ループを経ない ``x = call(); if x: ...`` でも同じ問題が起きる（展開とは独立の、
    既存の _assign の穴）。例外にならず UNKNOWN として経路を打ち切ることを確認する。
    """
    mutant = CORRECT_SERVICE.replace(
        "    inventory.restore(order.items)\n",
        "    x = inventory.restore(order.items)\n    if x:\n        pass\n",
    )
    code = analyze(tmp_path, mutant)  # 例外にならないこと自体がこのテストの主目的
    assert verdicts(code)["R-CANCEL-04"][0] == "UNKNOWN"


# --------------------------------------------- codex 再レビュー (#50) の指摘（3 回目）


def test_call_inside_compare_is_not_mistaken_for_analyzed_call_result(tmp_path: Path) -> None:
    """比較式の中の呼出しは _call を通らない（イベントが記録されない）。以前は前回の修正
    （Unresolved.code を "unresolved_call" に統一）が退行を生んでいた: _return が code
    だけを見て「_assign が解析済みの呼出し結果に付けた印」と誤認し、比較式の中の
    未解析の呼出し（実行時は 2 回目の restore）を無かったことにして経路を「完全」扱い
    していた。analyzed_call フラグで区別するようになったので、この経路は UNKNOWN に
    なる（少なくとも誤った VERIFIED にはならない）。
    """
    mutant = (
        CORRECT_SERVICE.replace(
            "    inventory.restore(order.items)\n",
            "    inventory.restore(order.items)\n    extra_restore(order)\n",
        )
        + "\n\ndef extra_restore(order):\n    return inventory.restore(order.items) == 0\n"
    )
    code = analyze(tmp_path, mutant)
    v = verdicts(code)
    assert v["R-CANCEL-04"][0] == "UNKNOWN", v


def test_analyzed_call_flag_is_trusted_only_for_bare_name_return(tmp_path: Path) -> None:
    """analyzed_call の印は `return x`（解析済みの呼出し結果をそのまま返す）でだけ信用する。

    `return x == g()` のような複合式では、to_expr が左辺の Unresolved をそのまま返して印が
    伝播することがあり、右辺の g() が解析されないまま経路が「完全」扱いになっていた
    （codex 軽量レビュー指摘。実行時は restore が 2 回なのに 1 回として VERIFIED）。
    複合式は未解析の部分式を含み得るので UNKNOWN に落とす。`return x` は従来どおり通る。
    """
    compound = (
        CORRECT_SERVICE.replace(
            "    inventory.restore(order.items)\n", "    extra_restore(order)\n"
        )
        + "\n\ndef extra_restore(order):\n"
        "    x = inventory.restore(order.items)\n"
        "    return x == inventory.restore(order.items)\n"
    )
    (tmp_path / "compound").mkdir()
    assert verdicts(analyze(tmp_path / "compound", compound))["R-CANCEL-04"][0] == "UNKNOWN"

    bare = (
        CORRECT_SERVICE.replace(
            "    inventory.restore(order.items)\n", "    extra_restore(order)\n"
        )
        + "\n\ndef extra_restore(order):\n    x = inventory.restore(order.items)\n    return x\n"
    )
    (tmp_path / "bare").mkdir()
    assert verdicts(analyze(tmp_path / "bare", bare))["R-CANCEL-04"] == (
        "VERIFIED_IN_SCOPE",
        "conformant",
    )


def test_range_shadow_via_match_capture_prevents_unroll(tmp_path: Path) -> None:
    """match のキャプチャ名（MatchAs.name）による再束縛も見つける。キャプチャ名は
    Name(ctx=Store) にならず MatchAs.name という文字列フィールドに入るので、Name ノードだけを
    見ていると素通りする。
    """
    mutant = (
        CORRECT_SERVICE.replace(
            "    inventory.restore(order.items)\n",
            "    for _ in range(1):\n        inventory.restore(order.items)\n",
        )
        + "\nmatch (lambda n: ()):\n    case range:\n        pass\n"
    )
    code = analyze(tmp_path, mutant)
    success = code.paths[2]
    assert success.precision == "incomplete"
    assert "組込みと確定できない" in success.unknown_causes[0].detail


def test_skipped_loop_target_becomes_unresolved(tmp_path: Path) -> None:
    """展開を諦めて読み飛ばした for のターゲットは未解決化される。読み飛ばす前の
    ループ変数の値が残ったままだと、その後の条件式が実行時とずれた値で判定されてしまう
    （読み飛ばした 2 つ目の for は本来 x を上書きするが、展開できないので実際の値は
    分からないはず）。読み飛ばし後に x を参照した経路が UNKNOWN になることを確認する。
    """
    too_many = ", ".join(["0"] * (UNROLL_MAX_ITERATIONS + 1))
    mutant = CORRECT_SERVICE.replace(
        "    inventory.restore(order.items)\n",
        "    for x in (1,):\n"
        "        pass\n"
        f"    for x in ({too_many}):\n"
        "        pass\n"
        "    if x == 1:\n"
        "        inventory.restore(order.items)\n",
    )
    code = analyze(tmp_path, mutant)
    success = code.paths[2]
    assert success.precision == "incomplete"
    assert verdicts(code)["R-CANCEL-04"][0] == "UNKNOWN"
