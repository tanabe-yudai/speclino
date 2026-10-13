"""承認者の照合（S61）。"""

from __future__ import annotations

from speclino.services.approvers import approver_ids_missing, is_approver


def test_matches_by_id_when_ids_are_stored() -> None:
    settings = {"approvers": ["octo-cat"], "approver_ids": [42]}
    assert is_approver(settings, user_id=42, login="octo-cat")
    # 改名しても ID が同じなら承認者
    assert is_approver(settings, user_id=42, login="new-name")


def test_rename_same_login_different_id_is_not_an_approver() -> None:
    """旧ログイン名を他人が取得しても、ID が違えば承認者ではない。"""
    settings = {"approvers": ["octo-cat"], "approver_ids": [42]}
    assert not is_approver(settings, user_id=7, login="octo-cat")


def test_without_user_id_is_not_an_approver_when_ids_are_stored() -> None:
    settings = {"approvers": ["octo-cat"], "approver_ids": [42]}
    assert not is_approver(settings, user_id=None, login="octo-cat")


def test_legacy_settings_without_ids_approve_no_one() -> None:
    """`approver_ids` の無い設定はログイン名で照合しない（乗っ取りを防ぐ。フェイルクローズ）。"""
    legacy = {"approvers": ["octo-cat"]}
    assert not is_approver(legacy, user_id=None, login="octo-cat")
    assert not is_approver(legacy, user_id=7, login="octo-cat")
    assert not is_approver({**legacy, "approver_ids": []}, user_id=7, login="octo-cat")


def test_approver_ids_missing_flags_incomplete_ids() -> None:
    """承認者がいるのに ID が無い・足りない設定は、保存し直しを促す（画面の警告）。"""
    assert approver_ids_missing({"approvers": ["octo-cat"]})
    assert approver_ids_missing({"approvers": ["octo-cat", "hubot"], "approver_ids": [42]})
    assert not approver_ids_missing({"approvers": ["octo-cat"], "approver_ids": [42]})
    assert not approver_ids_missing({})
    assert not approver_ids_missing({"approvers": []})


def test_empty_or_invalid_settings_approve_no_one() -> None:
    assert not is_approver({}, user_id=42, login="octo-cat")
    # 型に合わない保存値は安全側（承認者なし）
    assert not is_approver({"approvers": "octo-cat"}, user_id=None, login="octo-cat")
    assert not is_approver({"approvers": ["octo-cat"], "approver_ids": "x"}, user_id=1, login="x")
